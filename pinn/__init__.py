# -*- coding: utf-8 -*-
"""pinn — 物理验证层（P2D 模型 + PINN + 数据契约）

本目录承载「高比能液态锂电池设计」方案里的物理验证层：
  - cell_spec_schema.py   阶段 A：电芯方案参数字典（Cell Spec Dict）
                          三方对齐契约（P2D 输入 / miner JSON / 数据库字段）
  - （阶段 B）P2D 骨架 + 积分后处理（PyBaMM）
  - （阶段 C）miner 子集验证闭环
  - （阶段 D）PINN 化 + 接入 RAG 管线

从 cell_spec_schema 导入：
    from pinn.cell_spec_schema import CellSpec, candidates_scheme_to_cell_spec
"""

from .cell_spec_schema import (  # noqa: F401
    CellSpec,
    MaterialSpec,
    ElectrodeSpec,
    ElectrolyteSpec,
    SeparatorSpec,
    CellDesignSpec,
    TestCondition,
    PerformanceAnchor,
    Provenance,
    candidates_scheme_to_cell_spec,
    miner_records_to_cell_spec,
    fill_missing,
    to_pybamm_dict,
    validate,
    estimate_material_energy,
    estimate_cell_energy,
    estimate_scheme_energy,
)

try:
    from .p2d_runner import PyBaMMP2DRunner
except Exception:
    PyBaMMP2DRunner = None

