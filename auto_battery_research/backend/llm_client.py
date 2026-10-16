"""LLMClient — 统一大语言模型客户端 (AutoBatteryResearch Agent).

基于通用 OpenAI 兼容接口，支持各类前沿商业与开源大模型（GPT、DeepSeek、Qwen、MiniMax、Claude 等），
支持真实大模型推理、系统提示词与 Few-Shot 样本注入。
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional

L = logging.getLogger("AutoBatteryResearch.LLMClient")


class LLMClient:
    """多后端 LLM 客户端包装器."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        llm_cfg = self.config.get("llm", {})
        openai_cfg = self.config.get("openai", {})

        self.backend = llm_cfg.get("backend", "openai")
        
        # 优先从 config 读取 (已由 ABRConfigLoader 解析 $(VAR: default))
        self.api_key = (
            llm_cfg.get("api_key")
            or openai_cfg.get("openai_api_key")
            or os.getenv("OPENAI_API_KEY", "")
            or os.getenv("DEEPSEEK_API_KEY", "")
        )
        self.base_url = (
            llm_cfg.get("base_url")
            or openai_cfg.get("openai_api_base")
            or os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
        )
        self.model = (
            llm_cfg.get("writer_model")
            or llm_cfg.get("model")
            or openai_cfg.get("model_name")
            or os.getenv("OPENAI_MODEL", "gpt-4o")
        )
        self.temperature = llm_cfg.get("temperature", 0.1)

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """调用大模型生成文本（无伪造规则兜底，真实调用并捕获异常）."""
        import time as _time
        try:
            from auto_battery_research.util import llm_trace as _trace_mod
        except Exception:
            _trace_mod = None
        _t0 = _time.monotonic() if (_trace_mod and _trace_mod.llm_trace_enabled()) else None

        if not self.api_key:
            raise ValueError(f"未配置 LLM API Key (OPENAI_API_KEY / DEEPSEEK_API_KEY 为空)")

        from openai import OpenAI
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        def _record(text: Any = None, resp: Any = None, error: Any = None) -> None:
            if _t0 is None or _trace_mod is None:
                return
            try:
                usage = None
                sdk_usage = getattr(resp, "usage", None) if resp is not None else None
                if sdk_usage is not None and getattr(sdk_usage, "total_tokens", None) is not None:
                    usage = {"input_tokens": getattr(sdk_usage, "prompt_tokens", None),
                             "output_tokens": getattr(sdk_usage, "completion_tokens", None),
                             "total_tokens": getattr(sdk_usage, "total_tokens", None)}
                _trace_mod.record_llm_call(
                    "backend.llm_client.generate",
                    backend=self.backend, model=self.model,
                    duration_ms=(_time.monotonic() - _t0) * 1000.0,
                    input={"messages": messages},
                    output={"status": "ok" if error is None else "error", "text": text},
                    usage=usage, error=error)
            except Exception:
                pass

        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
            )
            text = resp.choices[0].message.content or ""
            _record(text=text, resp=resp)
            return text
        except Exception as e:
            _record(error=e)
            raise
