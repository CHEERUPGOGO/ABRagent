"""AutoBatteryResearch RAG 统一门面模块 (Unified RAG Facade).

对外暴露多智能体方案设计引擎、热力学规则引擎 (RelationEngine C1-C8)、
能量密度估算模型及统一大模型客户端。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
# sys.path 自举：仓库态 lmllm 位于 src/ 下，需将 src 加入 sys.path；
# wheel 安装态 lmllm 即已安装包，以上插入为不存在的死路径、无副作用。
for _extra in (str(ROOT_DIR), str(ROOT_DIR / "src")):
    if _extra not in sys.path:
        sys.path.insert(0, _extra)

# 从旧实现位置再导出 (lmllm 单一命名空间：仓库态解析到 src/lmllm，安装态解析到
# wheel 内 lmllm 包；chromadb 等重依赖在源模块内延迟加载)
from lmllm.RAG import RAGPipeline
from lmllm.RAG.relation_engine import RelationEngine, RULES_VERSION
from lmllm.RAG.llm_client import LLMClient, strip_think_blocks
from lmllm.RAG.energy_model import estimate_scheme_energy, check_energy_claim

__all__ = [
    "RAGPipeline",
    "RelationEngine",
    "RULES_VERSION",
    "LLMClient",
    "strip_think_blocks",
    "estimate_scheme_energy",
    "check_energy_claim",
]
