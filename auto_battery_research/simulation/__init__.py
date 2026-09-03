"""AutoBatteryResearch Simulation 统一门面模块 (Unified Simulation Facade).

对外暴露 PyBaMM Newman P2D 物理求解器、文献锚点校验模型与 CellSpec 物理契约。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pinn.p2d_runner import PyBaMMP2DRunner, run_discharge, MATERIAL_PROFILES
from pinn.cell_spec_schema import CellSpec, candidates_scheme_to_cell_spec

__all__ = [
    "PyBaMMP2DRunner",
    "run_discharge",
    "MATERIAL_PROFILES",
    "CellSpec",
    "candidates_scheme_to_cell_spec",
]
