# -*- coding: utf-8 -*-
"""
电解质 Formatter — 从 format1/ 加载数据，分组：材料属性(19) + 电化学性能(32) + 条件(6)
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

explain_data = _load("ee", "electrolyte_explanation.py")
info_data = _load("ei", "electrolyte_information.py")
example_data = _load("ex", "electrolyte_example_text.py")
st_data = _load("es", "electrolyte_structured_data.py")

# ==================== 标签分组（来自 format1/electrolyte_structured_data.py 的 agent 分段） ====================

ELECTROLYTE_MATERIAL_LABELS: Set[str] = {
    "Li_Solvent_Binding_Energy", "Li_Anion_Binding_Energy", "TM_Solvent_Binding_Energy",
    "Li_Anion_Coordination_Number_MD", "Li_Solvent_Coordination_Number_MD",
    "Molecule_Formation_Energy", "CIP_AGG_Fraction",
    "Solvent_van_der_Waals_Volume", "Mixing_Entropy",
    "Dipole_Moment", "Dielectric_Constant",
    "Fluorination_Degree", "Number_of_Fluorine_Substituents",
    "HOMO_LUMO_Energy", "Melting_Point", "Boiling_Point", "Flash_Point",
    "Viscosity", "Density",
    "Thermal_Conductivity",
    "Thermal_Diffusivity",
    "Specific_Surface_Area",
}

ELECTROLYTE_PERFORMANCE_LABELS: Set[str] = {
    "Li_Desolvation_Activation_Energy", "Charge_Transfer_Resistance",
    "SEI_Resistance", "CEI_Resistance",
    "Li_Transport_Activation_Energy_SEI", "Li_Transport_Activation_Energy_CEI",
    "SEI_Thickness", "CEI_Thickness",
    "LiF_Content_in_SEI_CEI", "Inorganic_Organic_Ratio_SEI_CEI",
    "Li2O_Content_in_SEI", "S_N_Content_in_SEI",
    "Transition_Metal_Deposition", "Interfacial_Crack_Density",
    "Interface_Roughness", "Contact_Angle",
    "Ionic_Conductivity", "Electrochemical_Stability_Window",
    "Anodic_Stability_Onset_Potential", "Reduction_Onset_Potential",
    "Operating_Temperature_Range",
    "Capacity_Retention", "Coulombic_Efficiency", "Cycle_Life_80",
    "Energy_Density", "Maximum_Thermal_Runaway_Temperature",
    "Self_Heating_Onset_Temperature", "Gas_Evolution_Amount",
    "Voltage_Hysteresis", "Transition_Metal_Dissolution_Concentration",
    "Rate_Capability", "DCIR",
}

ELECTROLYTE_CONDITION_LABELS: Set[str] = {
    "Lithium_Salt_Type", "Salt_Concentration", "Solvent_Composition",
    "Additives", "Water_Content", "Mixing_Process",
}

assert len(ELECTROLYTE_MATERIAL_LABELS) == 22
assert len(ELECTROLYTE_PERFORMANCE_LABELS) == 32
assert len(ELECTROLYTE_CONDITION_LABELS) == 6
assert ELECTROLYTE_MATERIAL_LABELS.isdisjoint(ELECTROLYTE_PERFORMANCE_LABELS)
assert ELECTROLYTE_MATERIAL_LABELS.isdisjoint(ELECTROLYTE_CONDITION_LABELS)
assert ELECTROLYTE_PERFORMANCE_LABELS.isdisjoint(ELECTROLYTE_CONDITION_LABELS)

_all_labels = ELECTROLYTE_MATERIAL_LABELS | ELECTROLYTE_PERFORMANCE_LABELS | ELECTROLYTE_CONDITION_LABELS
assert len(_all_labels) == 60


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


_e = BaseFormatter(explain_data)
_i = BaseFormatter(info_data)
_x = BaseFormatter(example_data)
_s = BaseFormatter(st_data)


class ElectrolyteFormatter:
    material_explanation = LabelGroupView(_e, ELECTROLYTE_MATERIAL_LABELS)
    material_information = LabelGroupView(_i, ELECTROLYTE_MATERIAL_LABELS)
    material_example_text = LabelGroupView(_x, ELECTROLYTE_MATERIAL_LABELS)
    material_structured_data = LabelGroupView(_s, ELECTROLYTE_MATERIAL_LABELS)

    perf_explanation = LabelGroupView(_e, ELECTROLYTE_PERFORMANCE_LABELS)
    perf_information = LabelGroupView(_i, ELECTROLYTE_PERFORMANCE_LABELS)
    perf_example_text = LabelGroupView(_x, ELECTROLYTE_PERFORMANCE_LABELS)
    perf_structured_data = LabelGroupView(_s, ELECTROLYTE_PERFORMANCE_LABELS)

    # BaseAgent 兼容别名（指向完整标签集）
    explanation = _e
    structured_data = _s
    information = _i
    example_text = _x

    @classmethod
    def keys(cls):
        return sorted(_all_labels)

    @classmethod
    def material_keys(cls):
        return sorted(ELECTROLYTE_MATERIAL_LABELS)

    @classmethod
    def performance_keys(cls):
        return sorted(ELECTROLYTE_PERFORMANCE_LABELS)

    @classmethod
    def condition_keys(cls):
        return list(ELECTROLYTE_CONDITION_LABELS)
