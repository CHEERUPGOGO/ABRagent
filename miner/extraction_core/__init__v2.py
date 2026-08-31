# -*- coding: utf-8 -*-
"""提取核心 v2 — 导出新版流水线、Unified Agent、表格上下文、规则筛选、后处理

这是 __init__.py 的副本（v2 版本），新增了以下导出：
  - UnifiedExtractionAgent
  - extract_table_contexts
  - screen_extraction_unit / llm_include_fallback / ScreeningDecision
  - normalize_conditions / normalize_embedded_conditions / remove_nulls / normalize_label_buckets
"""

# 保留原有导出
from miner.extraction_core.pricing import TokenChecker, TokenStep

# MaterialDiscoveryAgent（来自旧版）
from miner.extraction_core.material_discovery import MaterialDiscoveryAgent, discover_materials

# === 统一抽取 Agent ===
from miner.extraction_core.unified_agent import UnifiedExtractionAgent

# === 表格上下文 ===
from miner.extraction_core.table_context import extract_table_contexts

# === 规则筛选 ===
from miner.extraction_core.rule_screening import (
    screen_extraction_unit,
    llm_include_fallback,
    ScreeningDecision,
)

# === 后处理 ===
from miner.extraction_core.postprocess import (
    normalize_conditions,
    normalize_embedded_conditions,
    remove_nulls,
    normalize_label_buckets,
)

__all__ = [
    # 原有
    "TokenChecker", "TokenStep",
    "MaterialDiscoveryAgent", "discover_materials",
    # 新加
    "UnifiedExtractionAgent",
    "extract_table_contexts",
    "screen_extraction_unit",
    "llm_include_fallback",
    "ScreeningDecision",
    "normalize_conditions",
    "normalize_embedded_conditions",
    "remove_nulls",
    "normalize_label_buckets",
]
