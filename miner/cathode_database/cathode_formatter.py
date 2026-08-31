# -*- coding: utf-8 -*-
"""
正极 Formatter — 从 format1/ 加载数据，分组：材料属性(36) + 电化学性能(18) + 条件(8)
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

explain_data = _load("ce", "cathode_explanation.py")
info_data = _load("ci", "cathode_information.py")
example_data = _load("cx", "cathode_example_text.py")
st_data = _load("cs", "cathode_structured_data.py")

# ==================== 标签分组（来自 format1/cathode_structured_data.py 的 agent 分段） ====================

CATHODE_MATERIAL_LABELS: Set[str] = {
    "Lattice_Parameters", "Crystal_Space_Group",
    "Lithium_Ion_Diffusion_Activation_Energy", "Li_Ion_Migration_Barrier",
    "Electronic_Band_Gap", "Theoretical_Specific_Capacity",
    "Formation_Energy", "Volume_Change_Ratio", "a_c_axis_expansion",
    "Oxygen_Vacancy_Concentration", "Oxygen_Vacancy_Formation_Energy",
    "Transition_Metal_Migration_Energy_Barrier", "Interlayer_Spacing_of_TM_Layers",
    "Density_of_States_at_Fermi_Level", "Bader_Charge",
    "Chemical_Composition_Mole_Fractions", "Element_Valence_State",
    "Jahn_Teller_Active_Ion_Content", "devtE", "VEd",
    "Average_Electron_Affinity", "Average_Deviation_of_Ionic_Radius",
    "Average_Ionization_Energy", "Configurational_Entropy",
    "Valence_Electron_Count", "d_Electron_Configuration_Type",
    "Li_Ni_mixing_ratio", "Metal_Oxygen_Bond_Energy",
    "Primary_Particle_Size_Distribution", "Secondary_Particle_Size_Distribution",
    "Electrode_Pore_Size_Distribution", "Surface_Spinel_Layer_Thickness",
    "XPS_ROCO2Li_Peak", "XPS_C_O_Peak", "XPS_NiF2_Peak", "LixPOyFz",
    "Adhesion_Strength",
    "Mesoscopic_Porosity",
}

CATHODE_PERFORMANCE_LABELS: Set[str] = {
    "Electronic_Conductivity_Bulk",
    "Initial_Coulombic_Efficiency", "Discharge_Specific_Capacity_Initial",
    "Rate_Performance", "Capacity_Retention_Ratio", "Rate_Capability_Profile",
    "Nominal_Discharge_Voltage", "Average_Discharge_Voltage",
    "Charge_Discharge_Voltage_Gap", "Ion_Diffusion_Coefficient",
    "Gravimetric_Energy_Density", "Volumetric_Energy_Density",
    "Charge_Transfer_Resistance", "SEI_Resistance", "Self_Discharge_Rate",
    "Thermal_Runaway_Onset_Temperature", "Phase_Transition_Voltage",
    "Transition_Metal_Dissolution_Amount", "O2_CO2_Evolution",
}

CATHODE_CONDITION_LABELS: Set[str] = {
    "Active_Material_Mass_Fraction", "Electrode_Thickness", "Mass_Loading",
    "Compacted_Density", "Conductive_Additive_Binder_Ratio",
    "Electronic_Conductivity_Electrode", "Peel_Strength", "Electrode_Porosity",
}

assert len(CATHODE_MATERIAL_LABELS) == 38
assert len(CATHODE_PERFORMANCE_LABELS) == 19
assert len(CATHODE_CONDITION_LABELS) == 8
assert CATHODE_MATERIAL_LABELS.isdisjoint(CATHODE_PERFORMANCE_LABELS)
assert CATHODE_MATERIAL_LABELS.isdisjoint(CATHODE_CONDITION_LABELS)
assert CATHODE_PERFORMANCE_LABELS.isdisjoint(CATHODE_CONDITION_LABELS)


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


class CathodeFormatter:
    material_structured_data = LabelGroupView(_s, CATHODE_MATERIAL_LABELS)
    material_explanation = LabelGroupView(_e, CATHODE_MATERIAL_LABELS)
    material_information = LabelGroupView(_i, CATHODE_MATERIAL_LABELS)
    material_example_text = LabelGroupView(_x, CATHODE_MATERIAL_LABELS)

    perf_structured_data = LabelGroupView(_s, CATHODE_PERFORMANCE_LABELS)
    perf_explanation = LabelGroupView(_e, CATHODE_PERFORMANCE_LABELS)
    perf_information = LabelGroupView(_i, CATHODE_PERFORMANCE_LABELS)
    perf_example_text = LabelGroupView(_x, CATHODE_PERFORMANCE_LABELS)

    # BaseAgent 兼容别名（指向完整标签集）
    explanation = _e
    structured_data = _s
    information = _i
    example_text = _x

    @classmethod
    def material_keys(cls) -> Iterable[str]:
        return list(CATHODE_MATERIAL_LABELS)

    @classmethod
    def performance_keys(cls) -> Iterable[str]:
        return list(CATHODE_PERFORMANCE_LABELS)

    @classmethod
    def condition_keys(cls) -> Iterable[str]:
        return list(CATHODE_CONDITION_LABELS)
