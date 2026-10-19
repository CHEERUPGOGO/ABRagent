"""LLM 全量轨迹记录 (opt-in) — AutoBatteryResearch Agent.

开启后 (runtime_options.llm_trace / 环境变量 LLM_TRACE / abr-cli --trace)，系统内
每一次 LLM 调用的完整输入、输出、token 用量、耗时，以及输入的产生过程
(prompt_assembly 事件、ReAct 工具轨迹、RAG 各智能体角色) 都按行追加进课题目录
的 `llm_trace/trace_<会话时间戳>.jsonl`；默认关闭时仅一次布尔判断即返回，零 IO。

设计约束:
- 记录失败绝不影响主流程 (record 全程 try/except 静默)；
- 不记录任何 api_key (事件 schema 本身不含密钥；meta 的 config 摘要由调用方
  传入脱敏后的字段)；
- 进程级单例: 同一课题目录重复 configure 追加同一会话文件，换课题开新文件；
  无课题场景 (TUI 问答 / MCP consult / 子进程挖矿) 落缺省 `log/llm_trace/`。
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from langchain_core.callbacks import BaseCallbackHandler
except Exception:  # langchain 不可用时退化为普通类 (仅影响 legacy 工厂回调, 不影响主记录器)
    class BaseCallbackHandler:  # type: ignore[no-redef]
        pass

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

TRACE_ENV_VAR = "LLM_TRACE"
_TRACE_TRUTHY = ("1", "true", "yes", "on")

# 事件类型
EVENT_LLM_CALL = "llm_call"
EVENT_PROMPT_ASSEMBLY = "prompt_assembly"


def _env_enabled() -> bool:
    return os.getenv(TRACE_ENV_VAR, "").strip().lower() in _TRACE_TRUTHY


def serialize_message(m: Any) -> Any:
    """把 langchain BaseMessage / dict / str 序列化为可 JSON 化的 dict (字段保真)."""
    if m is None or isinstance(m, (str, int, float, bool)):
        return m
    if isinstance(m, dict):
        return m
    data: Dict[str, Any] = {"type": m.__class__.__name__}
    content = getattr(m, "content", None)
    if content is not None:
        data["content"] = content
    role = getattr(m, "type", None)
    if role:
        data["role"] = role
    ak = getattr(m, "additional_kwargs", None)
    if isinstance(ak, dict) and ak:
        keep = {k: ak[k] for k in ("tool_calls", "function_call", "reasoning_content") if k in ak}
        if keep:
            data["additional_kwargs"] = keep
    tool_calls = getattr(m, "tool_calls", None)
    if tool_calls:
        data["tool_calls"] = tool_calls
    for attr in ("name", "tool_call_id", "status"):
        v = getattr(m, attr, None)
        if v is not None:
            data[attr] = v
    rm = getattr(m, "response_metadata", None)
    if isinstance(rm, dict) and rm:
        data["response_metadata"] = rm
    um = getattr(m, "usage_metadata", None)
    if isinstance(um, dict) and um:
        data["usage_metadata"] = um
    return data


def serialize_messages(msgs: Any) -> List[Any]:
    """批量序列化消息列表 (非列表输入原样包裹)。"""
    if msgs is None:
        return []
    if not isinstance(msgs, (list, tuple)):
        msgs = [msgs]
    return [serialize_message(m) for m in msgs]


def extract_usage(obj: Any) -> Optional[Dict[str, int]]:
    """从 langchain AIMessage (usage_metadata/response_metadata) 或 openai SDK
    response (resp.usage) 提取归一化 token 用量；无则 None。"""
    try:
        um = getattr(obj, "usage_metadata", None)
        if isinstance(um, dict) and um.get("total_tokens") is not None:
            return {
                "input_tokens": um.get("input_tokens"),
                "output_tokens": um.get("output_tokens"),
                "total_tokens": um.get("total_tokens"),
            }
        rm = getattr(obj, "response_metadata", None)
        tu = rm.get("token_usage") if isinstance(rm, dict) else None
        if not isinstance(tu, dict):
            tu = getattr(getattr(obj, "usage", None), "__dict__", None)
        if isinstance(tu, dict) and tu.get("total_tokens") is not None:
            return {
                "input_tokens": tu.get("prompt_tokens", tu.get("input_tokens")),
                "output_tokens": tu.get("completion_tokens", tu.get("output_tokens")),
                "total_tokens": tu.get("total_tokens"),
            }
    except Exception:
        pass
    return None


class LLMTraceRecorder:
    """进程级轨迹记录单例 (线程安全; 关闭时零开销)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._enabled = False
        self._trace_dir: Optional[Path] = None
        self._session_file: Optional[Path] = None
        self._seq = 0
        self._meta: Dict[str, Any] = {}
        self._meta_written = False

    # ── 开关 ──
    def configure(
        self,
        enabled: Optional[bool] = None,
        trace_dir: Optional[Any] = None,
        goal: Optional[str] = None,
        source: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """配置记录器。

        enabled=None 时回落环境变量 LLM_TRACE (供 MCP/TUI/子进程无配置场景)。
        trace_dir 变更 (切换课题) 会开启新会话文件；同目录重复 configure 追加同一会话。
        meta: 调用方传入的脱敏 config 摘要 (如模型名/base_url)，仅写入 trace_meta.json。
        """
        with self._lock:
            if enabled is None:
                enabled = _env_enabled()
            self._enabled = bool(enabled)
            if goal is not None:
                self._meta["goal"] = goal
            if source is not None:
                self._meta["source"] = source
            if isinstance(meta, dict):
                self._meta.update(meta)
            if not self._enabled:
                return False  # 关闭时不触碰磁盘
            if trace_dir is not None:
                new_dir = Path(trace_dir)
                if str(new_dir) != str(self._trace_dir or ""):
                    self._trace_dir = new_dir
                    self._session_file = None  # 新课题 → 新会话文件
                    self._seq = 0
                    self._meta_written = False
            elif self._trace_dir is None:
                self._trace_dir = ROOT_DIR / "log" / "llm_trace"
            return True

    def is_enabled(self) -> bool:
        return self._enabled

    # ── 记录 ──
    def record(self, event: Dict[str, Any]) -> Optional[Path]:
        """追加一条事件；任何失败静默返回 None (绝不影响主流程)。

        会话 meta (goal/source/模型摘要) 随每条事件携带, 保证单行自洽可独立溯源。
        """
        if not self._enabled:
            return None
        try:
            with self._lock:
                if self._trace_dir is None:
                    self._trace_dir = ROOT_DIR / "log" / "llm_trace"
                if self._session_file is None:
                    self._trace_dir.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    self._session_file = self._trace_dir / f"trace_{stamp}.jsonl"
                self._seq += 1
                payload = {
                    "seq": self._seq,
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    **self._meta,
                    **event,
                }
                with open(self._session_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                if not self._meta_written:
                    self._write_meta_locked()
                return self._session_file
        except Exception:
            return None

    def _write_meta_locked(self) -> None:
        try:
            meta = {
                "enabled_at": datetime.now().isoformat(timespec="seconds"),
                "session_file": self._session_file.name if self._session_file else None,
                "pid": os.getpid(),
                **self._meta,
            }
            self._trace_dir.mkdir(parents=True, exist_ok=True)
            (self._trace_dir / "trace_meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            self._meta_written = True
        except Exception:
            self._meta_written = True  # 写失败不再重试, 不阻塞记录

    def record_llm_call(
        self,
        call_site: str,
        input: Optional[Dict[str, Any]] = None,
        output: Optional[Dict[str, Any]] = None,
        usage: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[float] = None,
        error: Optional[Any] = None,
        backend: Optional[str] = None,
        model: Optional[str] = None,
        **meta: Any,
    ) -> Optional[Path]:
        """记录一次 LLM 调用 (llm_call 事件)。"""
        return self.record({
            "event": EVENT_LLM_CALL,
            "call_site": call_site,
            **meta,
            "backend": backend,
            "model": model,
            "duration_ms": round(duration_ms) if isinstance(duration_ms, (int, float)) else None,
            "input": input,
            "output": output,
            "usage": usage,
            "error": (f"{type(error).__name__}: {error}" if isinstance(error, Exception) else error) or None,
        })

    def record_event(self, event: str, call_site: str, **fields: Any) -> Optional[Path]:
        """记录一条过程事件 (如 prompt_assembly)。"""
        return self.record({"event": event, "call_site": call_site, **fields})


_RECORDER = LLMTraceRecorder()


def get_llm_trace_recorder() -> LLMTraceRecorder:
    return _RECORDER


def configure_llm_trace(**kwargs: Any) -> bool:
    return _RECORDER.configure(**kwargs)


def llm_trace_enabled() -> bool:
    return _RECORDER.is_enabled()


def record_llm_call(call_site: str, **kwargs: Any) -> Optional[Path]:
    return _RECORDER.record_llm_call(call_site, **kwargs)


def record_trace_event(event: str, call_site: str, **fields: Any) -> Optional[Path]:
    return _RECORDER.record_event(event, call_site, **fields)


def trace_callback_list(call_site: str) -> List[Any]:
    """给 legacy ChatOpenAI 工厂用的回调注入: tracing 关闭时返回空列表。"""
    if not _RECORDER.is_enabled():
        return []
    try:
        return [LLMTraceCallbackHandler(call_site=call_site)]
    except Exception:
        return []


class LLMTraceCallbackHandler(BaseCallbackHandler):
    """langchain 回调处理器 —— 覆盖直接使用 ChatOpenAI 的 legacy 挖矿链路.

    on_chat_model_start 记录输入消息, on_llm_end 记录输出与 token 用量,
    on_llm_error 记录错误; 生命周期键为 run_id (并发安全)。
    """

    def __init__(self, call_site: str = "langchain.ChatOpenAI") -> None:
        super().__init__()
        self.call_site = call_site
        self._t0: Dict[str, float] = {}

    def _elapsed(self, run_id: Any) -> Optional[float]:
        t0 = self._t0.pop(str(run_id), None)
        return (time.monotonic() - t0) * 1000.0 if t0 is not None else None

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        try:
            if run_id is not None:
                self._t0[str(run_id)] = time.monotonic()
            flat: List[Any] = []
            for group in (messages or []):
                flat.extend(serialize_messages(group))
            _RECORDER.record({
                "event": EVENT_LLM_CALL,
                "call_site": self.call_site,
                "phase": "start",
                "input": {"messages": flat},
            })
        except Exception:
            pass

    def on_llm_start(self, serialized: Any, prompts: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        try:
            if run_id is not None:
                self._t0[str(run_id)] = time.monotonic()
            _RECORDER.record({
                "event": EVENT_LLM_CALL,
                "call_site": self.call_site,
                "phase": "start",
                "input": {"prompts": list(prompts or [])},
            })
        except Exception:
            pass

    def on_llm_end(self, response: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        try:
            elapsed_ms = self._elapsed(run_id)
            gens: List[Any] = []
            usage: Optional[Dict[str, Any]] = None
            for gen_list in (getattr(response, "generations", None) or []):
                for gen in gen_list:
                    msg = getattr(gen, "message", None)
                    if msg is not None:
                        gens.append(serialize_message(msg))
                        if usage is None:
                            usage = extract_usage(msg)
                    elif getattr(gen, "text", None) is not None:
                        gens.append({"type": "generation", "text": gen.text})
            if usage is None:
                llm_output = getattr(response, "llm_output", None)
                if isinstance(llm_output, dict):
                    usage = extract_usage(type("U", (), {"response_metadata": {"token_usage": llm_output.get("token_usage")}})())
            _RECORDER.record({
                "event": EVENT_LLM_CALL,
                "call_site": self.call_site,
                "phase": "end",
                "duration_ms": round(elapsed_ms) if elapsed_ms is not None else None,
                "output": {"messages": gens},
                "usage": usage,
            })
        except Exception:
            pass

    def on_llm_error(self, error: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        try:
            _RECORDER.record({
                "event": EVENT_LLM_CALL,
                "call_site": self.call_site,
                "phase": "error",
                "duration_ms": self._elapsed(run_id),
                "error": f"{type(error).__name__}: {error}",
            })
        except Exception:
            pass
