"""AutoBatteryResearch Mining 统一门面模块 (Unified Material Mining & Assembly Facade).

对外暴露 Tok2000 材料微观表征挖掘流水线、材料与标签归一化器及电芯组装器。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.pipeline_tok2000 import run as run_tok2000
from agent.material_norm import MaterialNormalizer
from agent.label_norm import LabelNormalizer
from agent.cell_assembler import assemble_cells

__all__ = [
    "run_tok2000",
    "MaterialNormalizer",
    "LabelNormalizer",
    "assemble_cells",
]
