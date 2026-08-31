# -*- coding: utf-8 -*-
"""
电解质结构化输出
每个标签定义标准化的结构化数据输出 schema。
"""

# ==================== Agent 1: 材料本征属性 ====================

Li_Solvent_Binding_Energy = """
"Li_Solvent_Binding_Energy": [
    {
        "value": null,
        "unit": "eV",
        "solvent": "",
        "method": "",
        "source_text": ""
    }
]
"""

Li_Anion_Binding_Energy = """
"Li_Anion_Binding_Energy": [
    {
        "value": null,
        "unit": "eV",
        "anion": "",
        "method": "",
        "source_text": ""
    }
]
"""

TM_Solvent_Binding_Energy = """
"TM_Solvent_Binding_Energy": [
    {
        "value": null,
        "unit": "eV",
        "metal_ion": "",
        "solvent": "",
        "method": "",
        "source_text": ""
    }
]
"""

Li_Anion_Coordination_Number_MD = """
"Li_Anion_Coordination_Number_MD": [
    {
        "value": null,
        "unit": "",
        "simulation_conditions": "",
        "source_text": ""
    }
]
"""

Li_Solvent_Coordination_Number_MD = """
"Li_Solvent_Coordination_Number_MD": [
    {
        "value": null,
        "unit": "",
        "simulation_conditions": "",
        "source_text": ""
    }
]
"""

Molecule_Formation_Energy = """
"Molecule_Formation_Energy": [
    {
        "value": null,
        "unit": "eV/atom",
        "molecule": "",
        "method": "",
        "source_text": ""
    }
]
"""

CIP_AGG_Fraction = """
"CIP_AGG_Fraction": [
    {
        "value": {"CIP": null, "AGG": null},
        "unit": "%",
        "conditions": "",
        "source_text": ""
    }
]
"""

Solvent_van_der_Waals_Volume = """
"Solvent_van_der_Waals_Volume": [
    {
        "value": null,
        "unit": "cm³/mol",
        "solvent": "",
        "method": "",
        "source_text": ""
    }
]
"""

Mixing_Entropy = """
"Mixing_Entropy": [
    {
        "value": null,
        "unit": "J mol⁻¹ K⁻¹",
        "derived": true,
        "source_text": ""
    }
]
"""

Dipole_Moment = """
"Dipole_Moment": [
    {
        "value": null,
        "unit": "D",
        "molecule": "",
        "method": "",
        "source_text": ""
    }
]
"""

Dielectric_Constant = """
"Dielectric_Constant": [
    {
        "value": null,
        "unit": "",
        "temperature_C": null,
        "source_text": ""
    }
]
"""

Fluorination_Degree = """
"Fluorination_Degree": [
    {
        "value": 0,
        "unit": "F atoms",
        "molecule": "",
        "source_text": ""
    }
]
"""

Number_of_Fluorine_Substituents = """
"Number_of_Fluorine_Substituents": [
    {
        "value": 0,
        "unit": "",
        "molecule": "",
        "source_text": ""
    }
]
"""

HOMO_LUMO_Energy = """
"HOMO_LUMO_Energy": [
    {
        "value": {"HOMO": null, "LUMO": null, "gap": null},
        "unit": "eV",
        "molecule": "",
        "method": "",
        "source_text": ""
    }
]
"""

Melting_Point = """
"Melting_Point": [
    {
        "value": null,
        "unit": "°C",
        "source_text": ""
    }
]
"""

Boiling_Point = """
"Boiling_Point": [
    {
        "value": null,
        "unit": "°C",
        "source_text": ""
    }
]
"""

Flash_Point = """
"Flash_Point": [
    {
        "value": null,
        "unit": "°C",
        "source_text": ""
    }
]
"""

Viscosity = """
"Viscosity": [
    {
        "value": null,
        "unit": "mPa·s",
        "temperature_C": null,
        "source_text": ""
    }
]
"""

Density = """
"Density": [
    {
        "value": null,
        "unit": "g/cm³",
        "temperature_C": null,
        "source_text": ""
    }
]
"""


# ==================== Agent 2: 电化学性能 ====================

Li_Desolvation_Activation_Energy = """
"Li_Desolvation_Activation_Energy": [
    {
        "value": null,
        "unit": "eV",
        "method": "",
        "electrode": "",
        "source_text": ""
    }
]
"""

Charge_Transfer_Resistance = """
"Charge_Transfer_Resistance": [
    {
        "value": null,
        "unit": "Ω",
        "condition_id": "",
        "cycle_number": 0,
        "temperature_C": null,
        "source_text": ""
    }
]
"""

SEI_Resistance = """
"SEI_Resistance": [
    {
        "value": null,
        "unit": "Ω",
        "condition_id": "",
        "cycle_number": 0,
        "source_text": ""
    }
]
"""

CEI_Resistance = """
"CEI_Resistance": [
    {
        "value": null,
        "unit": "Ω",
        "condition_id": "",
        "cycle_number": 0,
        "source_text": ""
    }
]
"""

Li_Transport_Activation_Energy_SEI = """
"Li_Transport_Activation_Energy_SEI": [
    {
        "value": null,
        "unit": "eV",
        "temperature_range": "",
        "source_text": ""
    }
]
"""

Li_Transport_Activation_Energy_CEI = """
"Li_Transport_Activation_Energy_CEI": [
    {
        "value": null,
        "unit": "eV",
        "temperature_range": "",
        "source_text": ""
    }
]
"""

SEI_Thickness = """
"SEI_Thickness": [
    {
        "value": null,
        "unit": "nm",
        "cycle_number": 0,
        "method": "",
        "source_text": ""
    }
]
"""

CEI_Thickness = """
"CEI_Thickness": [
    {
        "value": null,
        "unit": "nm",
        "cycle_number": 0,
        "method": "",
        "source_text": ""
    }
]
"""

LiF_Content_in_SEI_CEI = """
"LiF_Content_in_SEI_CEI": [
    {
        "value": null,
        "unit": "at%",
        "interphase": "",
        "cycle_number": 0,
        "source_text": ""
    }
]
"""

Inorganic_Organic_Ratio_SEI_CEI = """
"Inorganic_Organic_Ratio_SEI_CEI": [
    {
        "value": null,
        "unit": "",
        "interphase": "",
        "cycle_number": 0,
        "source_text": ""
    }
]
"""

Li2O_Content_in_SEI = """
"Li2O_Content_in_SEI": [
    {
        "value": null,
        "unit": "at%",
        "cycle_number": 0,
        "source_text": ""
    }
]
"""

S_N_Content_in_SEI = """
"S_N_Content_in_SEI": [
    {
        "value": {"S": null, "N": null},
        "unit": "at%",
        "cycle_number": 0,
        "source_text": ""
    }
]
"""

Transition_Metal_Deposition = """
"Transition_Metal_Deposition": [
    {
        "value": {},
        "unit": "at%",
        "interphase": "",
        "cycle_number": 0,
        "method": "",
        "source_text": ""
    }
]
"""

Interfacial_Crack_Density = """
"Interfacial_Crack_Density": [
    {
        "value": null,
        "unit": "µm⁻¹",
        "cycle_number": 0,
        "method": "",
        "source_text": ""
    }
]
"""

Interface_Roughness = """
"Interface_Roughness": [
    {
        "value": null,
        "unit": "nm",
        "condition": "",
        "source_text": ""
    }
]
"""

Contact_Angle = """
"Contact_Angle": [
    {
        "value": null,
        "unit": "°",
        "substrate": "",
        "source_text": ""
    }
]
"""

Ionic_Conductivity = """
"Ionic_Conductivity": [
    {
        "value": null,
        "unit": "mS/cm",
        "temperature_C": null,
        "method": "",
        "source_text": ""
    }
]
"""

Electrochemical_Stability_Window = """
"Electrochemical_Stability_Window": [
    {
        "value": {"min_V": null, "max_V": null},
        "unit": "V vs. Li/Li⁺",
        "working_electrode": "",
        "source_text": ""
    }
]
"""

Anodic_Stability_Onset_Potential = """
"Anodic_Stability_Onset_Potential": [
    {
        "value": null,
        "unit": "V vs. Li/Li⁺",
        "current_density_criterion": "",
        "source_text": ""
    }
]
"""

Reduction_Onset_Potential = """
"Reduction_Onset_Potential": [
    {
        "value": null,
        "unit": "V vs. Li/Li⁺",
        "current_density_criterion": "",
        "source_text": ""
    }
]
"""

Operating_Temperature_Range = """
"Operating_Temperature_Range": [
    {
        "value": {"min": null, "max": null},
        "unit": "°C",
        "criterion": "",
        "source_text": ""
    }
]
"""

Capacity_Retention = """
"Capacity_Retention": [
    {
        "value": null,
        "unit": "%",
        "cycle_number": 0,
        "condition_id": "",
        "source_text": ""
    }
]
"""

Coulombic_Efficiency = """
"Coulombic_Efficiency": [
    {
        "value": null,
        "unit": "%",
        "cycle_number": 0,
        "condition_id": "",
        "source_text": ""
    }
]
"""

Cycle_Life_80 = """
"Cycle_Life_80": [
    {
        "value": 0,
        "unit": "cycles",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Energy_Density = """
"Energy_Density": [
    {
        "value": {
            "gravimetric": {"value": null, "unit": "Wh/kg"},
            "volumetric": {"value": null, "unit": "Wh/L"}
        },
        "unit": "",
        "basis": "",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Maximum_Thermal_Runaway_Temperature = """
"Maximum_Thermal_Runaway_Temperature": [
    {
        "value": null,
        "unit": "°C",
        "soc_percent": 0,
        "source_text": ""
    }
]
"""

Self_Heating_Onset_Temperature = """
"Self_Heating_Onset_Temperature": [
    {
        "value": null,
        "unit": "°C",
        "soc_percent": 0,
        "source_text": ""
    }
]
"""

Gas_Evolution_Amount = """
"Gas_Evolution_Amount": [
    {
        "value": {},
        "unit": "nmol/mg",
        "condition_id": "",
        "method": "",
        "source_text": ""
    }
]
"""

Voltage_Hysteresis = """
"Voltage_Hysteresis": [
    {
        "value": null,
        "unit": "V",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Transition_Metal_Dissolution_Concentration = """
"Transition_Metal_Dissolution_Concentration": [
    {
        "value": {},
        "unit": "mg/L",
        "cycle_number": 0,
        "method": "",
        "source_text": ""
    }
]
"""

Rate_Capability = """
"Rate_Capability": [
    {
        "value": [
            {"rate": "0.2C", "capacity": 195, "unit": "mAh/g"},
            {"rate": "5C", "capacity": 112, "unit": "mAh/g"}
        ],
        "condition_id": "",
        "source_text": ""
    }
]
"""

DCIR = """
"DCIR": [
    {
        "value": null,
        "unit": "Ω",
        "pulse_duration_s": 0,
        "soc_percent": 0,
        "temperature_C": null,
        "source_text": ""
    }
]
"""


# ==================== Agent 3: 制备与测试条件 ====================
Lithium_Salt_Type = """
"Lithium_Salt_Type": [
    {
        "value": "",
        "unit": "",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Salt_Concentration = """
"Salt_Concentration": [
    {
        "value": null,
        "unit": "M",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Solvent_Composition = """
"Solvent_Composition": [
    {
        "value": "",
        "unit": "",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Additives = """
"Additives": [
    {
        "value": [],
        "unit": "wt%",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Water_Content = """
"Water_Content": [
    {
        "value": null,
        "unit": "ppm",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Mixing_Process = """
"Mixing_Process": [
    {
        "value": "",
        "unit": "",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Thermal_Conductivity = """
"Thermal_Conductivity": [
    {
        "value": null,
        "unit": "W/(m·K)",
        "temperature": "",
        "method": "",
        "source_text": ""
    }
]

"""

Thermal_Diffusivity = """
"Thermal_Diffusivity": [
    {
        "value": null,
        "unit": "mm²/s",
        "temperature": "",
        "method": "",
        "source_text": ""
    }
]

"""

Specific_Surface_Area = """
"Specific_Surface_Area": [
    {
        "value": null,
        "unit": "m²/g",
        "method": "",
        "source_text": ""
    }
]

"""
