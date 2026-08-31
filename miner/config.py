"""
Miner 配置加载工具 — 统一管理 LLM API、路径、模块参数。

用法:
    from miner.config import load_config, create_llm

    cfg = load_config()
    llm = create_llm(model_type="classification")  # 或 "extraction"
"""

import os
import yaml
from pathlib import Path
from typing import Optional, Dict, Any

# 配置文件路径
_CONFIG_DIR = Path(__file__).resolve().parent
_CONFIG_PATH = _CONFIG_DIR / "config.yaml"
_PROJECT_ROOT = _CONFIG_DIR.parent  # 项目根目录 (LMLLM/)


# ==================== 配置加载 ====================

def load_config() -> Dict[str, Any]:
    """加载 miner/config.yaml，返回字典"""
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"[config] 未找到配置文件 {_CONFIG_PATH}，使用默认值")
        return {}
    except Exception as e:
        print(f"[config] 读取配置失败: {e}，使用默认值")
        return {}


# ==================== LLM 工厂 ====================

def create_llm(model_type: str = "classification") -> Any:
    """
    根据配置创建 DeepSeek ChatOpenAI 实例。

    Args:
        model_type: "classification" 或 "extraction"，
                    分别使用 fast/pro 模型

    Returns:
        ChatOpenAI 实例
    """
    cfg = load_config().get("llm", {})

    # API Key：优先从环境变量，其次从配置文件（不推荐）
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        api_key = cfg.get("api_key", "")
    if not api_key:
        raise ValueError(
            "未设置 DeepSeek API Key。请在以下任一位置配置:\n"
            "  1. 环境变量: export DEEPSEEK_API_KEY='your-key'\n"
            "  2. miner/config.yaml: llm.api_key 字段"
        )

    base_url = os.getenv("DEEPSEEK_API_BASE", cfg.get("base_url", "https://api.deepseek.com/v1"))

    # 根据任务类型选模型
    model_map = {
        "classification": cfg.get("classification_model", "deepseek-v4-flash"),
        "extraction": cfg.get("extraction_model", "deepseek-v4-pro"),
    }
    model = model_map.get(model_type, cfg.get("classification_model", "deepseek-v4-flash"))

    temperature = float(cfg.get("temperature", 0.0))
    max_tokens = int(cfg.get("max_tokens", 1024))
    timeout = int(cfg.get("request_timeout", 120))

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=timeout,
    )


# ==================== 路径工具 ====================

def get_path(key: str, default: Optional[str] = None) -> str:
    """获取配置文件中的路径（相对于项目根）"""
    cfg = load_config()
    path_val = cfg.get("paths", {}).get(key, default or key)
    return str(_PROJECT_ROOT / path_val)


def get_merged_root() -> str:
    return get_path("merged_root", "papers/merged")


def get_markdown_root() -> str:
    return get_path("markdown_root", "papers/markdown")


def get_output_dir(module: str = "meta_extraction") -> str:
    """
    获取模块的输出目录。

    Args:
        module: 模块名，对应配置中对应 section 的 output_dir
    """
    cfg = load_config()
    if module == "meta_extraction":
        default = "miner/json"
    elif module == "classification":
        default = "papers/classified"
    else:
        default = f"miner/{module}"
    out = cfg.get(module, {}).get("output_dir", default)
    # 如果路径不是绝对路径，相对于项目根
    if not os.path.isabs(out):
        out = str(_PROJECT_ROOT / out)
    return out
