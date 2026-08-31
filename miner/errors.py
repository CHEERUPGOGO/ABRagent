# -*- coding: utf-8 -*-
"""锂电池正极材料数据库 — 自定义异常类"""

class StructuredFormatError(Exception):
    """LLM 返回的 JSON 无法解析或缺少必要字段"""
    def __init__(self, message="", raw_output="", property_name=""):
        self.message = message
        self.raw_output = (raw_output or "")[:500]
        self.property_name = property_name
        super().__init__(self._fmt())

    def _fmt(self):
        p = [f"[{self.property_name}]"] if self.property_name else []
        if self.message: p.append(self.message)
        if self.raw_output: p.append(f"raw: {self.raw_output}...")
        return " ".join(p) if p else "StructuredFormatError"


class LangchainError(Exception):
    """LLM 链执行失败（网络、超时、API 错误）"""
    def __init__(self, message="", chain_name="", original_error=None):
        self.message = message
        self.chain_name = chain_name
        self.original_error = original_error
        super().__init__(self._fmt())

    def _fmt(self):
        p = [f"[{self.chain_name}]"] if self.chain_name else []
        if self.message: p.append(self.message)
        if self.original_error: p.append(f"← {type(self.original_error).__name__}: {self.original_error}")
        return " ".join(p) if p else "LangchainError"


class TokenLimitError(Exception):
    """输入文本超过模型最大 token 限制"""
    def __init__(self, current_tokens=0, max_tokens=0, content_preview=""):
        self.current_tokens = current_tokens
        self.max_tokens = max_tokens
        self.content_preview = (content_preview or "")[:200]
        super().__init__(f"TokenLimitError: {current_tokens}/{max_tokens} tokens. "
                         f"Preview: {self.content_preview}...")
