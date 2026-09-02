"""统一 LLM 客户端 — 支持 OpenAI/MiniMax API、DeepSeek API 和 Ollama 多后端

提供统一的 chat() 接口、可用性检测、规则问题拆解与结构化 JSON 解析。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .config import (
    OPENAI_API_KEY, OPENAI_API_BASE, OPENAI_MODEL,
    DEEPSEEK_API_KEY, DEEPSEEK_API_BASE,
    OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT,
    LLM_TEMPERATURE, LLM_MAX_TOKENS,
    create_openai_llm, create_deepseek_llm,
)

# 推理模型 (DeepSeek-R1 / MiniMax-M2 / Qwen3-thinking 等) 会在正文前输出 <think>...</think> 思考块
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_think_blocks(text: Optional[str]) -> str:
    """剥离 LLM 返回中的 <think>...</think> 思考块，仅保留正式正文.

    未闭合的 <think> (流式截断常见) 从开标签起全部视为思考丢弃；
    正常文本不含该标签时原样返回 (零开销快速路径)。
    """
    if not text or "<think>" not in text.lower():
        return text or ""
    stripped = _THINK_BLOCK_RE.sub("", text)
    idx = stripped.lower().find("<think>")
    if idx != -1:
        stripped = stripped[:idx]
    return stripped.strip()


class LLMClient:
    """统一的 LLM 客户端, 自动选择后端.

    优先级: OpenAI/MiniMax API > DeepSeek API > Ollama 本地 > 规则回退
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        backend: str = "auto",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ):
        self.backend = backend  # "auto" | "openai" | "deepseek" | "ollama" | "rule"
        self.temperature = temperature if temperature is not None else LLM_TEMPERATURE
        self.max_tokens = max_tokens if max_tokens is not None else LLM_MAX_TOKENS

        # 1. OpenAI / MiniMax API (显式传入优先，其次配置表/环境变量)
        self._openai_llm = None
        self._model_name = model_name or OPENAI_MODEL
        effective_key = api_key or OPENAI_API_KEY
        effective_base = api_base or OPENAI_API_BASE
        if effective_key:
            try:
                self._openai_llm = create_openai_llm(
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    model_name=self._model_name,
                    api_key=effective_key,
                    api_base=effective_base,
                )
            except Exception:
                self._openai_llm = None


        # 2. DeepSeek API
        self._deepseek_llm = None
        if DEEPSEEK_API_KEY:
            try:
                self._deepseek_llm = create_deepseek_llm(
                    "classification",
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    model_name=model_name,
                )
            except Exception:
                self._deepseek_llm = None

        # 3. Ollama
        self.ollama_base_url = OLLAMA_BASE_URL.rstrip("/")
        self.ollama_model = model_name or OLLAMA_MODEL
        self.ollama_timeout = OLLAMA_TIMEOUT

    @property
    def openai_ready(self) -> bool:
        return self._openai_llm is not None

    @property
    def deepseek_ready(self) -> bool:
        return self._deepseek_llm is not None

    @property
    def ollama_ready(self) -> bool:
        try:
            req = urllib.request.Request(
                f"{self.ollama_base_url}/api/tags", method="GET"
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            models = [m.get("name", "") for m in data.get("models", [])]
            return self.ollama_model in models
        except Exception:
            return False

    @property
    def available(self) -> bool:
        """是否有至少一种后端可用"""
        if self.backend == "openai":
            return self.openai_ready
        if self.backend == "deepseek":
            return self.deepseek_ready
        if self.backend == "ollama":
            return self.ollama_ready
        # auto
        return self.openai_ready or self.deepseek_ready or self.ollama_ready

    def status_text(self) -> str:
        parts = []
        if self.openai_ready:
            parts.append(f"OpenAI/MiniMax ({self._model_name or OPENAI_MODEL}): 就绪")
        else:
            parts.append("OpenAI/MiniMax: 未配置")
        if self.deepseek_ready:
            parts.append("DeepSeek API: 就绪")
        if self.ollama_ready:
            parts.append(f"Ollama ({self.ollama_model}): 就绪")
        return " | ".join(parts)

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
    ) -> str:
        """统一的 chat 接口, 自动选择可用后端."""
        temp = temperature if temperature is not None else self.temperature

        # 1. 尝试 OpenAI / MiniMax
        if self.backend in ("auto", "openai") and self._openai_llm is not None:
            try:
                from langchain_core.messages import SystemMessage, HumanMessage
                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ]
                llm = self._openai_llm
                if hasattr(llm, "temperature"):
                    llm.temperature = temp
                resp = llm.invoke(messages)
                return strip_think_blocks(resp.content)
            except Exception as e:
                if self.backend == "openai":
                    raise RuntimeError(f"OpenAI/MiniMax API 调用失败: {e}")

        # 2. 尝试 DeepSeek
        if self.backend in ("auto", "deepseek") and self._deepseek_llm is not None:
            try:
                from langchain_core.messages import SystemMessage, HumanMessage
                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ]
                llm = self._deepseek_llm
                if hasattr(llm, "temperature"):
                    llm.temperature = temp
                resp = llm.invoke(messages)
                return strip_think_blocks(resp.content)
            except Exception as e:
                if self.backend == "deepseek":
                    raise RuntimeError(f"DeepSeek API 调用失败: {e}")

        # 3. 尝试 Ollama
        if self.backend in ("auto", "ollama") and self.ollama_ready:
            try:
                return strip_think_blocks(self._ollama_chat(system_prompt, user_prompt, temp))
            except Exception as e:
                if self.backend == "ollama":
                    raise RuntimeError(f"Ollama 调用失败: {e}")

        raise RuntimeError(
            "没有可用的 LLM 后端. 请设置 OPENAI_API_KEY / DEEPSEEK_API_KEY 或启动 Ollama 服务."
        )

    def _ollama_chat(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        payload = {
            "model": self.ollama_model,
            "stream": False,
            "options": {"temperature": temperature},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.ollama_base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.ollama_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            return (data.get("message", {}) or {}).get("content", "").strip()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Ollama HTTP {e.code}: {detail}")


def rule_decompose_question(question: str) -> Dict[str, Any]:
    """当 LLM 不可用时的纯规则问题拆解."""
    q = question.strip()
    focus_comp = None
    if any(k in q for k in ["正极", "NCM", "LFP", "高镍", "LRMO", "富锂"]):
        focus_comp = "cathode"
    elif any(k in q for k in ["负极", "金属锂", "石墨", "硅", "硬碳"]):
        focus_comp = "anode"
    elif any(k in q for k in ["电解液", "溶剂", "添加剂", "LiFSI", "LHCE"]):
        focus_comp = "electrolyte"

    return {
        "question_type": "screening" if any(k in q for k in ["对比", "筛选", "vs", "哪种", "区别"]) else "numeric",
        "focus_component": focus_comp,
        "focus_labels": ["电化学性能", "材料属性与表征"],
        "retrieval_queries": [q],
        "answer_outline": ["目标与路线", "推荐材料组合", "预期指标", "可行性依据", "风险评估"],
        "needs_reasoning": True,
        "fallback": True,
    }


def safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出中安全解析 JSON."""
    text = text.strip()

    # 1) 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2) 提取 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3) 用 JSONDecoder 从每个 { 位置尝试提取合法 JSON 对象
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass

    return None


def rule_conservative_answer(
    question: str,
    evidence: List[Dict[str, Any]],
    plan: Optional[Dict[str, Any]] = None,
) -> str:
    """当 LLM 不可用时的纯规则保守回答."""
    if not evidence:
        return (
            "【知识库暂无直接证据】\n\n"
            f"关于「{question}」,当前文献库中未检索到强相关段落.\n"
            "建议扩大检索范围或补充相关文献."
        )

    lines = [f"基于知识库检索到的 {len(evidence)} 条相关段落,整理如下信息:\n"]
    for i, p in enumerate(evidence[:5], 1):
        source = p.get("source_paper", p.get("source", "未知来源"))
        text = p.get("text", "")[:300]
        lines.append(f"**证据 {i}** (来源: {source}):\n> {text}\n")

    lines.append("\n*注: 当前回答基于检索段落直接提取,建议结合具体实验条件进一步验证.*")
    return "\n".join(lines)
