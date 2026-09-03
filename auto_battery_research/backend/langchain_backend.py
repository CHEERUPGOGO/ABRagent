"""ABRLangChainBackend — 基于 LangChain / LangGraph 的智能体运行时引擎 (AutoBatteryResearch Agent).

职责：
1. 统一管理大语言模型连接（OpenAI API / Qwen / DeepSeek / MiniMax / Ollama）
2. 构造 LangGraph create_agent 与 MemorySaver 状态持久化
3. 挂载 TrimAndSummaryMiddleware 实现上下文窗口与 Token 统计
4. 支持流式 Token 接收与 TUI/CLI 回调广播
"""

from __future__ import annotations

import os
import sys
import time
import logging
from typing import Dict, Any, List, Optional, Callable
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage, BaseMessage
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI

# langgraph 为可选依赖：缺失时退化为原生 Tool Calling 模型，无递归上限异常可捕获
try:
    from langgraph.errors import GraphRecursionError
    _RECURSION_EXCS = (GraphRecursionError,)
except Exception:
    _RECURSION_EXCS = ()

L = logging.getLogger("AutoBatteryResearch.LangChainBackend")


class TokenStreamHandler(BaseCallbackHandler):
    """流式 Token 接收与回调转发处理器."""

    def __init__(self, on_token: Optional[Callable[[str], None]] = None):
        self.on_token = on_token
        self.token_count = 0

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        self.token_count += 1
        if self.on_token:
            try:
                self.on_token(token)
            except Exception:
                pass


class MessageStatistic:
    """消息流与 Token 统计器."""

    def __init__(self):
        self.total_tokens = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.msg_count_ai = 0
        self.msg_count_tool = 0
        self.msg_count_system = 0
        self.msg_count_human = 0

    def update(self, msg: BaseMessage) -> None:
        if isinstance(msg, AIMessage):
            self.msg_count_ai += 1
        elif isinstance(msg, ToolMessage):
            self.msg_count_tool += 1
        elif isinstance(msg, SystemMessage):
            self.msg_count_system += 1
        elif isinstance(msg, HumanMessage):
            self.msg_count_human += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "msg_count_ai": self.msg_count_ai,
            "msg_count_tool": self.msg_count_tool,
            "msg_count_human": self.msg_count_human,
            "total_messages": self.msg_count_ai + self.msg_count_tool + self.msg_count_human,
        }


class ContextTrimmer:
    """轻量级上下文窗口管理与修剪器."""

    def __init__(self, max_keep_msgs: int = 40, tail_keep_msgs: int = 15):
        self.max_keep_msgs = max_keep_msgs
        self.tail_keep_msgs = tail_keep_msgs

    def trim(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        if len(messages) <= self.max_keep_msgs:
            return messages
        # 保留首条 SystemMessage 与尾部消息
        sys_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        tail_msgs = messages[-self.tail_keep_msgs:]
        kept = sys_msgs[:1] + tail_msgs
        # 修剪孤儿 ToolMessage：其对应的 AIMessage(tool_calls) 被裁掉后，
        # 残留的 ToolMessage 会被 OpenAI 兼容接口拒绝 (tool message must follow tool_calls)
        valid_tool_ids = set()
        for m in kept:
            if isinstance(m, AIMessage):
                for tc in (getattr(m, "tool_calls", None) or []):
                    tid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                    if tid:
                        valid_tool_ids.add(tid)
        return [m for m in kept if not isinstance(m, ToolMessage) or m.tool_call_id in valid_tool_ids]


class ABRLangChainBackend:
    """AutoBatteryResearch LangChain / LangGraph 后端引擎."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        on_token: Optional[Callable[[str], None]] = None,
        streaming: bool = False,
    ):
        self.config = config or {}
        self.streaming = streaming
        self.stream_handler = TokenStreamHandler(on_token=on_token)
        self.statistic = MessageStatistic()
        self.trimmer = ContextTrimmer()
        
        self.model = self._init_model()
        self.agent = None
        self.tools = []

        # ReAct 递归深度上限：每轮工具调用约消耗 2 个 super-step (model 节点 + tools 节点)，
        # 过小的上限会让智能体在完成 "Tips -> Inspect -> 执行 -> Check -> Complete" 链路前被掐断。
        # 默认 25 支持完整多轮工具编排，可经 llm.recursion_limit / runtime_options.recursion_limit 覆盖。
        llm_cfg = self.config.get("llm") or {}
        rt_cfg = self.config.get("runtime_options") or {}
        self.recursion_limit = int(
            llm_cfg.get("recursion_limit") or rt_cfg.get("recursion_limit") or 25
        )

    def _init_model(self) -> ChatOpenAI:
        """初始化底座大模型."""
        llm_cfg = self.config.get("llm", {})
        openai_cfg = self.config.get("openai", {})

        cfg_key = openai_cfg.get("openai_api_key") or llm_cfg.get("api_key")
        if cfg_key in ("dummy_key", "none", "None", ""):
            api_key = cfg_key or "dummy_key"
        else:
            api_key = os.getenv("OPENAI_API_KEY") or cfg_key or "dummy_key"

        api_base = (
            openai_cfg.get("openai_api_base")
            or llm_cfg.get("base_url")
            or os.getenv("OPENAI_API_BASE", "https://api.minimaxi.com/v1")
        )
        model_name = (
            llm_cfg.get("model")
            or openai_cfg.get("model_name")
            or os.getenv("OPENAI_MODEL", "MiniMax-M2.7-highspeed")
        )

        callbacks = [self.stream_handler] if self.streaming else []

        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=api_base,
            temperature=float(llm_cfg.get("temperature", 0.1)),
            streaming=self.streaming,
            callbacks=callbacks,
            request_timeout=float(llm_cfg.get("request_timeout") or 120.0),
            max_retries=2,
        )

    def bind_tools(self, tools: List[Any]) -> None:
        """绑定全量领域工具与阶段门禁工具."""
        self.tools = tools
        # 优先使用 langchain>=1.0 的 create_agent —— langgraph.prebuilt.create_react_agent
        # 已于 LangGraph V1.0 弃用 (V2.0 移除)；旧依赖栈或缺少 langgraph 时逐级回退
        self.agent = None
        try:
            from langchain.agents import create_agent
            from langgraph.checkpoint.memory import MemorySaver
            self.agent = create_agent(
                model=self.model,
                tools=self.tools,
                checkpointer=MemorySaver(),
            )
        except Exception as e1:
            try:
                from langgraph.prebuilt import create_react_agent
                from langgraph.checkpoint.memory import MemorySaver
                self.agent = create_react_agent(
                    model=self.model,
                    tools=self.tools,
                    checkpointer=MemorySaver(),
                )
                L.warning("已回退到 langgraph.prebuilt.create_react_agent (langchain.agents.create_agent 不可用: %s)", e1)
            except Exception:
                # 若环境既无 langchain.agents 也无 langgraph，则使用原生 Tool Calling 模型
                self.agent = self.model.bind_tools(self.tools)

    def invoke(self, messages: List[BaseMessage], thread_id: str = "default") -> Any:
        """执行单次或多轮 ReAct 推理.

        thread_id 需在多次重试间保持稳定，MemorySaver 的线程记忆才能累积
        (上一轮已执行的工具调用与观测对下一轮反思可见)。
        """
        trimmed_msgs = self.trimmer.trim(messages)
        for m in trimmed_msgs:
            self.statistic.update(m)

        # 离线单测模式直接返回兜底消息
        raw_key = getattr(self.model, "openai_api_key", None) or getattr(self.model, "api_key", None)
        if raw_key is not None and hasattr(raw_key, "get_secret_value"):
            model_key = raw_key.get_secret_value()
        else:
            model_key = raw_key
        if model_key is None or str(model_key).strip() in ("dummy_key", "none", "None", ""):
            return AIMessage(content="[Offline Mode] 确定性离线模式运行中。")

        if hasattr(self.agent, "invoke"):
            # LangGraph agent 调用
            config = {"configurable": {"thread_id": thread_id}, "recursion_limit": self.recursion_limit}
            try:
                return self.agent.invoke({"messages": trimmed_msgs}, config=config)
            except _RECURSION_EXCS:
                # 递归深度达上限：从 checkpointer 回收已完成的工具调用轨迹，
                # 调用方仍能解析已执行动作，而非整体作废
                L.warning("ReAct 循环达到递归上限 (recursion_limit=%s)，回收部分执行轨迹", self.recursion_limit)
                try:
                    state = self.agent.get_state(config)
                    if state is not None and state.values:
                        return {"messages": state.values.get("messages", [])}
                except Exception:
                    pass
                raise
            except TypeError:
                return self.agent.invoke(trimmed_msgs)
        else:
            return self.model.invoke(trimmed_msgs)
