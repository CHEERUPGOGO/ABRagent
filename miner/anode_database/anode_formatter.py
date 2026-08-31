# -*- coding: utf-8 -*-
"""
负极 Formatter — 从 format1/ 加载数据，分组：材料属性(14) + 电化学性能(34) + 制备条件(8)
"""

import os, inspect, importlib.util
from typing import Dict, Iterable, Set
from collections.abc import Mapping

_format1_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "format1")

def _load(name, fn):
    fp = os.path.join(_format1_dir, fn)
    spec = importlib.util.spec_from_file_location(name, fp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

explain = _load("ae", "andode_explanation.py")
info = _load("ai", "anode_information.py")
example = _load("ax", "anode_example_text.py")
st_data = _load("as", "anode_structured_data.py")

# ==================== 标签分组（与 format1/anode_*.py 中的标签名保持一致） ====================

ANODE_MATERIAL_LABELS: Set[str] = {
    "Elemental_Composition", "Chemical_Formula", "Crystal_System",
    "Space_Group", "Lattice_Parameters", "Crystallite_Size",
    "Interlayer_Spacing", "Unit_Cell_Volume_Change", "Band_Gap",
    "Li_Ion_Migration_Barrier", "Theoretical_Specific_Capacity",
    "LiF_Content_in_SEI_XPS", "SEI_Chemical_Composition_XPS", "Exchange_Current_Density",
    "Adhesion_Strength",
    "Mesoscopic_Porosity",
}

ANODE_PERFORMANCE_LABELS: Set[str] = {
    "Initial_Coulombic_Efficiency", "First_Lithiation_Capacity",
    "Reversible_Capacity_First_Cycle",
    "Pseudocapacitive_Contribution_Ratio",
    "Rate_Capability_at_Given_C_rate", "Energy_Density_Full_Cell",
    "Critical_Current_Density_for_Li_Dendrite_Onset",
    "Volumetric_Capacity", "Areal_Capacity",
    "Average_Operating_Voltage_vs_Li_Li",
    "Cycle_Life_80_Percent_Capacity_Retention",
    "Rate_Recovery_Percent",
    "Symmetric_Cell_Cycling_Stability",
    "Average_Coulombic_Efficiency_Stable_Cycling",
    "Capacity_Retention_at_Nth_Cycle",
    "Open_Circuit_Voltage",
    "Surface_Diffusion_Controlled_Contribution",
    "Irreversible_Capacity_Loss_First_Cycle",
    "Ionic_Conductivity_of_SEI",
    "SEI_Resistance", "Charge_Transfer_Resistance",
    "Chemical_Diffusion_Coefficient_of_Li_GITT",
    "Li_Dendrite_Nucleation_Overpotential", "Li_Dendrite_Growth_Rate",
    "Activation_Energy_for_Li_Transport_through_SEI",
    "Activation_Energy_for_Li_Desolvation",
    "Li_Nucleation_Overpotential", "Plateau_Overpotential",
}

ANODE_CONDITION_LABELS: Set[str] = {
    "Electrode_Porosity", "Coating_Thickness", "Electrode_Compaction_Density",
    "Artificial_SEI_Thickness", "Youngs_Modulus", "Tensile_Strength",
    "Elongation_at_Break", "Electrolyte_Wettability_Contact_Angle",
}

assert len(ANODE_MATERIAL_LABELS) == 16
assert len(ANODE_PERFORMANCE_LABELS) == 28
assert len(ANODE_CONDITION_LABELS) == 8
assert ANODE_MATERIAL_LABELS.isdisjoint(ANODE_PERFORMANCE_LABELS)
assert ANODE_MATERIAL_LABELS.isdisjoint(ANODE_CONDITION_LABELS)
assert ANODE_PERFORMANCE_LABELS.isdisjoint(ANODE_CONDITION_LABELS)


class BaseFormatter(Mapping):
    def __init__(self, mod):
        self.data = {k: v for k, v in vars(mod).items() if isinstance(v, str) and not k.startswith("_")}
    def __getitem__(self, k): return self.data[k].strip()
    def __iter__(self): return iter(self.data)
    def __len__(self): return len(self.data)


class LabelGroupView(Mapping):
    def __init__(self, full, labels):
        self._data = {k: v for k, v in full.data.items() if k in labels}
    def __getitem__(self, k): return self._data[k].strip()
    def __iter__(self): return iter(self._data)
    def __len__(self): return len(self._data)
    @property
    def data(self): return self._data


_e = BaseFormatter(explain)
_i = BaseFormatter(info)
_x = BaseFormatter(example)
_s = BaseFormatter(st_data)


class AnodeFormatter:
    material_explanation = LabelGroupView(_e, ANODE_MATERIAL_LABELS)
    material_information = LabelGroupView(_i, ANODE_MATERIAL_LABELS)
    material_example_text = LabelGroupView(_x, ANODE_MATERIAL_LABELS)
    material_structured_data = LabelGroupView(_s, ANODE_MATERIAL_LABELS)

    perf_explanation = LabelGroupView(_e, ANODE_PERFORMANCE_LABELS)
    perf_information = LabelGroupView(_i, ANODE_PERFORMANCE_LABELS)
    perf_example_text = LabelGroupView(_x, ANODE_PERFORMANCE_LABELS)
    perf_structured_data = LabelGroupView(_s, ANODE_PERFORMANCE_LABELS)

    # BaseAgent 兼容别名（指向完整标签集）
    explanation = _e
    structured_data = _s
    information = _i
    example_text = _x

    @classmethod
    def material_keys(cls) -> Iterable[str]:
        return list(ANODE_MATERIAL_LABELS)

    @classmethod
    def performance_keys(cls) -> Iterable[str]:
        return list(ANODE_PERFORMANCE_LABELS)

    @classmethod
    def condition_keys(cls) -> Iterable[str]:
        return list(ANODE_CONDITION_LABELS)
