"""AutoBatteryResearch Agent (ABRAgent) — 全生命周期化学电池自主研究智能体.

包含：
- 6 阶段全流程工作流（文献解析 -> 向量库 -> 材料电芯挖掘 -> 多智能体RAG -> PINN物理仿真[可Skip] -> 综合研报）
- 确定性 Python 门禁检查器 (Checkers) + LLM 语义审查
- Stage 5 (PINN) 灵活跳过/激活机制
- 全局自主科研主控智能体 (ABRAgent)
- 全自动化自主循环引擎 (AutonomousLoopRunner) + 标准 MCP Server 协议支持
"""

__version__ = "1.0.0"
__author__ = "Auto-Battery-Research Team"

from .agent import ABRAgent
from .workflow.stage_manager import StageManager
from .backend.loop_runner import AutonomousLoopRunner
from .cli import main as cli_main
from . import rag
from . import simulation
from . import mining
from . import pipeline

__all__ = [
    "ABRAgent",
    "StageManager",
    "AutonomousLoopRunner",
    "cli_main",
    "rag",
    "simulation",
    "mining",
    "pipeline",
    "__version__",
]
