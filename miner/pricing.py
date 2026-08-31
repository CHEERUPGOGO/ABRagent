# -*- coding: utf-8 -*-
"""
Token 计数 — DeepSeek 官网 Tokenizer
使用本地 deepseek_v3_tokenizer，不可用时降级为官网字符比例
"""

import logging, re, os
from typing import Optional, Dict

logger = logging.getLogger("TokenChecker")

MODEL_TOKEN_LIMITS = {
    "deepseek-v4-flash": 65536, "deepseek-v4-pro": 65536,
    "deepseek-chat": 65536, "default": 65536,
}

# DeepSeek 官网 tokenizer 路径
_TOKENIZER_DIR = "/home/ls/xiaoyue/LLM2/deepseek_v3_tokenizer/deepseek_v3_tokenizer"

_ZH = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]')

_tokenizer = None
_tokenizer_name = "DeepSeek 官网 tokenizer"
try:
    from tokenizers import Tokenizer
    _tokenizer = Tokenizer.from_file(os.path.join(_TOKENIZER_DIR, "tokenizer.json"))
    _tokenizer_name = "DeepSeek 官网 tokenizer (tokenizers.json)"
except ImportError:
    _tokenizer_name = "官网字符比例 (zh×0.6 + en×0.3) [tokenizers 未安装]"
except Exception as e:
    _tokenizer_name = f"官网字符比例 — 加载失败: {e}"


def estimate_tokens(text: str) -> int:
    if not text: return 0
    if _tokenizer is not None:
        try:
            r = _tokenizer.encode(text)
            return len(r.ids) if hasattr(r, "ids") else len(r)
        except Exception:
            pass
    zh = len(_ZH.findall(text))
    en = len(text) - zh
    return max(1, int(zh * 0.6 + en * 0.3))


def get_tokenizer_info() -> str:
    return _tokenizer_name


def get_model_limit(model_name: str = "") -> int:
    for key, limit in MODEL_TOKEN_LIMITS.items():
        if key in (model_name or ""): return limit
    return MODEL_TOKEN_LIMITS["default"]

def get_price(model_name: str, io_type: str) -> float:
    PRICES = {
        "deepseek-chat": {"input": 0.14, "output": 0.28},
        "deepseek-v4-flash": {"input": 0.14, "output": 0.28},
        "deepseek-v4-pro": {"input": 0.14, "output": 0.28},
        "deepseek-reasoner": {"input": 0.55, "output": 2.19},
        "default": {"input": 0.14, "output": 0.28},
    }
    for key, price in PRICES.items():
        if key in (model_name or ""):
            return price.get(io_type, 0.14)
    return PRICES["default"][io_type]


class TokenStep:
    """单次 LLM 调用的 token 统计"""
    def __init__(self, step_name, model_name="deepseek-chat"):
        self.step_name=step_name; self.model_name=model_name
        self.input_tokens=0; self.output_tokens=0; self.calls=0

    def record(self, prompt, response):
        self.input_tokens+=estimate_tokens(prompt)
        self.output_tokens+=estimate_tokens(response)
        self.calls+=1

    @property
    def total_tokens(self): return self.input_tokens+self.output_tokens

    @property
    def cost(self):
        ip=self.input_tokens*get_price(self.model_name,"input")/1_000_000
        op=self.output_tokens*get_price(self.model_name,"output")/1_000_000
        return ip+op

    def to_dict(self):
        return {"step":self.step_name,"model":self.model_name,
                "input_tokens":self.input_tokens,"output_tokens":self.output_tokens,
                "total_tokens":self.total_tokens,"calls":self.calls,
                "cost_usd":round(self.cost,6)}


class TokenChecker:
    def __init__(self, include_model="deepseek-chat", extract_model="deepseek-chat"):
        self.include_model = include_model
        self.extract_model = extract_model
        self.steps: Dict[str, TokenStep] = {}
        self.warnings: list = []

    def _get_step(self, step_name, model_name):
        if step_name not in self.steps:
            self.steps[step_name] = TokenStep(step_name, model_name)
        return self.steps[step_name]

    def record(self, step_name, prompt_text, response_text, stage="include"):
        """记录一次完整的 LLM 调用（input + output）"""
        model = self.include_model if stage == "include" else self.extract_model
        step = self._get_step(step_name, model)
        step.record(prompt_text, response_text)
        total = estimate_tokens(prompt_text)
        limit = get_model_limit(model)
        if total > limit:
            msg = f"[{step_name}] overflow: {total}>{limit}"
            logger.warning(msg); self.warnings.append(msg)

    # 保留旧接口兼容
    def check_include(self, prompt_text, content_length=0): pass
    def check_extract(self, prompt_text, content_length=0): pass

    @property
    def total_input(self):
        return sum(s.input_tokens for s in self.steps.values())

    @property
    def total_output(self):
        return sum(s.output_tokens for s in self.steps.values())

    @property
    def total_cost(self):
        return sum(s.cost for s in self.steps.values())

    def summary(self):
        return {
            "tokenizer": get_tokenizer_info(),
            "include_model": self.include_model, "extract_model": self.extract_model,
            "total_input_tokens": self.total_input,
            "total_output_tokens": self.total_output,
            "total_cost_usd": round(self.total_cost, 6),
            "steps": [s.to_dict() for s in self.steps.values()],
            "warnings": self.warnings,
        }


def update_token_checker(checker, model_name, stage="extract"):
    if stage == "include":
        checker.include_model = model_name
    else:
        checker.extract_model = model_name
