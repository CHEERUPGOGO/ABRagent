"""AutoBatteryResearch Simulation 统一门面模块 (Unified Simulation Facade).

对外暴露 SPM PINN 推理运行器与输入 spec 构建契约。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pinn.spm_runner import run_pinn_discharge, run_pinn_charge, match_pinn_system, load_registry
from pinn.input_spec import build_cell_spec

__all__ = [
    "run_pinn_discharge",
    "run_pinn_charge",
    "match_pinn_system",
    "load_registry",
    "build_cell_spec",
]
