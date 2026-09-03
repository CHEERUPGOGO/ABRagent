"""AutoBatteryResearch RAG 统一门面模块 (Unified RAG Facade).

对外暴露多智能体方案设计引擎、热力学规则引擎 (RelationEngine C1-C8)、
能量密度估算模型及统一大模型客户端。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# 从旧实现位置再导出 (上方 sys.path 自举保证 src.* 可导入; chromadb 等重依赖在源模块内延迟加载)
from src.lmllm.RAG import RAGPipeline
from src.lmllm.RAG.relation_engine import RelationEngine, RULES_VERSION
from src.lmllm.RAG.llm_client import LLMClient, strip_think_blocks
from src.lmllm.RAG.energy_model import estimate_scheme_energy, check_energy_claim

__all__ = [
    "RAGPipeline",
    "RelationEngine",
    "RULES_VERSION",
    "LLMClient",
    "strip_think_blocks",
    "estimate_scheme_energy",
    "check_energy_claim",
]
