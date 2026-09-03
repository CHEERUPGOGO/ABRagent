"""AutoBatteryResearch Agent (ABRAgent) — 全生命周期化学电池自主研究智能体.

包含：
- 6 阶段全流程工作流（文献解析 -> 向量库 -> 材料电芯挖掘 -> 多智能体RAG -> PINN物理仿真[可Skip] -> 综合研报）
- 确定性 Python 门禁检查器 (Checkers) + LLM 语义审查
- Stage 5 (PINN) 灵活跳过/激活机制
- 全局自主科研主控智能体 (ABRAgent, 单一执行内核) + 标准 MCP Server 协议支持
"""

from __future__ import annotations

__version__ = "1.0.0"
__author__ = "Auto-Battery-Research Team"

# PEP 562 惰性导出: 包 __init__ 保持秒级完成 —— MCP / CLI 轻量入口
# (abr-cli --mcp / --status / --web) 不再连带拉起 langchain、RAG 引擎、
# numpy 等重依赖链 (曾使冷启动达 30s+ 触发 MCP 客户端握手超时)。
# 重对象在首次属性访问时才 import, 并缓存回包命名空间 (后续访问零开销)。
# name -> (模块路径, 模块内符号名); 符号名为 None 表示导出模块本身。
_LAZY_EXPORTS = {
    "ABRAgent": ("auto_battery_research.agent", "ABRAgent"),
    "StageManager": ("auto_battery_research.workflow.stage_manager", "StageManager"),
    "cli_main": ("auto_battery_research.cli", "main"),
    "rag": ("auto_battery_research.rag", None),
    "simulation": ("auto_battery_research.simulation", None),
    "mining": ("auto_battery_research.mining", None),
    "pipeline": ("auto_battery_research.pipeline", None),
}

__all__ = [
    "ABRAgent",
    "StageManager",
    "cli_main",
    "rag",
    "simulation",
    "mining",
    "pipeline",
    "__version__",
]


def __getattr__(name: str):
    """首次访问时惰性加载子模块/符号 (PEP 562), 并缓存到包命名空间."""
    spec = _LAZY_EXPORTS.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    module_path, attr_name = spec
    module = importlib.import_module(module_path)
    value = module if attr_name is None else getattr(module, attr_name)
    globals()[name] = value  # 缓存: 后续访问不再经过 __getattr__
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
