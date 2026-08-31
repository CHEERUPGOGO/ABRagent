# -*- coding: utf-8 -*-
"""
负极材料结构化输出 
每个标签定义标准化的结构化数据输出 schema。
"""

# ==================== Agent 1: 材料本征属性 ====================

Elemental_Composition = """
"Elemental_Composition": [
    {
        "value": [],
        "unit": "",
        "method": "",
        "source_text": ""
    }
]
"""

Chemical_Formula = """
"Chemical_Formula": [
    {
        "value": "",
        "unit": "",
        "state": "",
        "source_text": ""
    }
]
"""

Crystal_System = """
"Crystal_System": [
    {
        "value": "",
        "unit": "",
        "source_text": ""
    }
]
"""

Space_Group = """
"Space_Group": [
    {
        "value": "",
        "unit": "",
        "source_text": ""
    }
]
"""

Lattice_Parameters = """
"Lattice_Parameters": [
    {
        "value": {
            "a": null,
            "b": null,
            "c": null,
            "alpha": null,
            "beta": null,
            "gamma": null
        },
        "unit": "Å / °",
        "crystal_system": "",
        "state": "",
        "method": "",
        "source_text": ""
    }
]
"""

Crystallite_Size = """
"Crystallite_Size": [
    {
        "value": null,
        "unit": "nm",
        "method": "",
        "source_text": ""
    }
]
"""

Interlayer_Spacing = """
"Interlayer_Spacing": [
    {
        "value": null,
        "unit": "Å",
        "plane": "",
        "state": "",
        "source_text": ""
    }
]
"""

Unit_Cell_Volume_Change = """
"Unit_Cell_Volume_Change": [
    {
        "value": null,
        "unit": "%",
        "from_state": "",
        "to_state": "",
        "source_text": ""
    }
]
"""

Band_Gap = """
"Band_Gap": [
    {
        "value": null,
        "unit": "eV",
        "type": "",
        "method": "",
        "source_text": ""
    }
]
"""

Li_Ion_Migration_Barrier = """
"Li_Ion_Migration_Barrier": [
    {
        "value": null,
        "unit": "eV",
        "path": "",
        "method": "",
        "source_text": ""
    }
]
"""

Theoretical_Specific_Capacity = """
"Theoretical_Specific_Capacity": [
    {
        "value": null,
        "unit": "mAh/g",
        "n_electrons": 0,
        "redox_reaction": "",
        "source_text": ""
    }
]
"""

LiF_Content_in_SEI_XPS = """
"LiF_Content_in_SEI_XPS": [
    {
        "value": null,
        "unit": "at%",
        "state": "",
        "spectrum": "",
        "source_text": ""
    }
]
"""

SEI_Chemical_Composition_XPS = """
"SEI_Chemical_Composition_XPS": [
    {
        "value": [],
        "unit": "",
        "state": "",
        "source_text": ""
    }
]
"""

Exchange_Current_Density = """
"Exchange_Current_Density": [
    {
        "value": null,
        "unit": "mA/cm2",
        "method": "",
        "temperature_C": null,
        "source_text": ""
    }
]
"""

# ==================== Agent 2: 电化学性能 ====================

Initial_Coulombic_Efficiency = """
"Initial_Coulombic_Efficiency": [
    {
        "value": null,
        "unit": "%",
        "condition_id": "",
        "source_text": ""
    }
]
"""

First_Lithiation_Capacity = """
"First_Lithiation_Capacity": [
    {
        "value": null,
        "unit": "mAh/g",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Reversible_Capacity_First_Cycle = """
"Reversible_Capacity_First_Cycle": [
    {
        "value": null,
        "unit": "mAh/g",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Pseudocapacitive_Contribution_Ratio = """
"Pseudocapacitive_Contribution_Ratio": [
    {
        "value": null,
        "unit": "%",
        "condition_id": "",
        "scan_rate_mV_s": null,
        "source_text": ""
    }
]
"""

Rate_Capability_at_Given_C_rate = """
"Rate_Capability_at_Given_C_rate": [
    {
        "value": null,
        "unit": "mAh/g",
        "condition_id": "",
        "voltage_range": "",
        "source_text": ""
    }
]
"""

Energy_Density_Full_Cell = """
"Energy_Density_Full_Cell": [
    {
        "value": null,
        "unit": "Wh/kg",
        "condition_id": "",
        "basis": "",
        "source_text": ""
    }
]
"""

Critical_Current_Density_Dendrite = """
"Critical_Current_Density_Dendrite": [
    {
        "value": null,
        "unit": "mA/cm2",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Volumetric_Capacity = """
"Volumetric_Capacity": [
    {
        "value": null,
        "unit": "mAh/cm3",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Areal_Capacity = """
"Areal_Capacity": [
    {
        "value": null,
        "unit": "mAh/cm2",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Average_Operating_Voltage = """
"Average_Operating_Voltage": [
    {
        "value": null,
        "unit": "V",
        "condition_id": "",
        "reference": "",
        "source_text": ""
    }
]
"""

Cycle_Life_80_Retention = """
"Cycle_Life_80_Retention": [
    {
        "value": 0,
        "unit": "cycles",
        "condition_id": "",
        "retention_cutoff": 0,
        "source_text": ""
    }
]
"""

Rate_Recovery = """
"Rate_Recovery": [
    {
        "value": null,
        "unit": "%",
        "condition_id": "",
        "high_rate": "",
        "low_rate": "",
        "source_text": ""
    }
]
"""

Symmetric_Cell_Stability = """
"Symmetric_Cell_Stability": [
    {
        "value": {"time_h": null, "overpotential_mV": null},
        "unit": "h/mV",
        "condition_id": "",
        "current_density_mA_cm2": null,
        "areal_capacity_mAh_cm2": null,
        "source_text": ""
    }
]
"""

Average_Coulombic_Efficiency_Stable = """
"Average_Coulombic_Efficiency_Stable": [
    {
        "value": null,
        "unit": "%",
        "condition_id": "",
        "cycle_range": [],
        "source_text": ""
    }
]
"""

Capacity_Retention_at_Nth_Cycle = """
"Capacity_Retention_at_Nth_Cycle": [
    {
        "value": null,
        "unit": "%",
        "condition_id": "",
        "cycle_number": 0,
        "source_text": ""
    }
]
"""

Open_Circuit_Voltage = """
"Open_Circuit_Voltage": [
    {
        "value": null,
        "unit": "V",
        "condition_id": "",
        "soc_percent": null,
        "rest_time_h": null,
        "source_text": ""
    }
]
"""

Surface_Controlled_Contribution = """
"Surface_Controlled_Contribution": [
    {
        "value": null,
        "unit": "%",
        "condition_id": "",
        "scan_rate_mV_s": null,
        "source_text": ""
    }
]
"""

Irreversible_Capacity_Loss_First = """
"Irreversible_Capacity_Loss_First": [
    {
        "value": null,
        "unit": "mAh/g",
        "condition_id": "",
        "source_text": ""
    }
]
"""

SEI_Ionic_Conductivity = """
"SEI_Ionic_Conductivity": [
    {
        "value": null,
        "unit": "S/cm",
        "condition_id": "",
        "temperature_C": null,
        "method": "",
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
        "soc_percent": null,
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
        "soc_percent": null,
        "source_text": ""
    }
]
"""

Chemical_Diffusion_Coefficient_GITT = """
"Chemical_Diffusion_Coefficient_GITT": [
    {
        "value": null,
        "unit": "cm2/s",
        "condition_id": "",
        "voltage_V": null,
        "temperature_C": null,
        "source_text": ""
    }
]
"""

Li_Dendrite_Nucleation_Overpotential = """
"Li_Dendrite_Nucleation_Overpotential": [
    {
        "value": null,
        "unit": "mV",
        "condition_id": "",
        "current_density_mA_cm2": null,
        "substrate": "",
        "source_text": ""
    }
]
"""

Li_Dendrite_Growth_Rate = """
"Li_Dendrite_Growth_Rate": [
    {
        "value": null,
        "unit": "μm/min",
        "condition_id": "",
        "current_density_mA_cm2": null,
        "method": "",
        "source_text": ""
    }
]
"""

Activation_Energy_SEI_Transport = """
"Activation_Energy_SEI_Transport": [
    {
        "value": null,
        "unit": "kJ/mol",
        "condition_id": "",
        "temperature_range_C": "",
        "method": "",
        "source_text": ""
    }
]
"""

Activation_Energy_Desolvation = """
"Activation_Energy_Desolvation": [
    {
        "value": null,
        "unit": "kJ/mol",
        "condition_id": "",
        "temperature_range_C": "",
        "method": "",
        "source_text": ""
    }
]
"""

Li_Nucleation_Overpotential = """
"Li_Nucleation_Overpotential": [
    {
        "value": null,
        "unit": "mV",
        "condition_id": "",
        "current_density_mA_cm2": null,
        "source_text": ""
    }
]
"""

Plateau_Overpotential = """
"Plateau_Overpotential": [
    {
        "value": null,
        "unit": "mV",
        "condition_id": "",
        "current_density_mA_cm2": null,
        "capacity_mAh_cm2": null,
        "source_text": ""
    }
]
"""

# ==================== Agent 3: 电极制备参数 ====================
Electrode_Porosity = """
"Electrode_Porosity": [
    {
        "value": null,
        "unit": "%",
        "method": "",
        "source_text": ""
    }
]
"""

Coating_Thickness = """
"Coating_Thickness": [
    {
        "value": null,
        "unit": "nm",
        "method": "",
        "source_text": ""
    }
]
"""

Electrode_Compaction_Density = """
"Electrode_Compaction_Density": [
    {
        "value": null,
        "unit": "g/cm3",
        "source_text": ""
    }
]
"""

Artificial_SEI_Thickness = """
"Artificial_SEI_Thickness": [
    {
        "value": null,
        "unit": "μm",
        "source_text": ""
    }
]
"""

Youngs_Modulus = """
"Youngs_Modulus": [
    {
        "value": null,
        "unit": "GPa",
        "source_text": ""
    }
]
"""

Tensile_Strength = """
"Tensile_Strength": [
    {
        "value": null,
        "unit": "MPa",
        "source_text": ""
    }
]
"""

Elongation_at_Break = """
"Elongation_at_Break": [
    {
        "value": null,
        "unit": "%",
        "source_text": ""
    }
]
"""

Electrolyte_Wettability_Contact_Angle = """
"Electrolyte_Wettability_Contact_Angle": [
    {
        "value": null,
        "unit": "°",
        "source_text": ""
    }
]
"""

Adhesion_Strength = """
"Adhesion_Strength": [
    {
        "value": null,
        "unit": "N/m",
        "test_method": "",
        "source_text": ""
    }
]

"""

Mesoscopic_Porosity = """
"Mesoscopic_Porosity": [
    {
        "value": null,
        "unit": "%",
        "method": "",
        "source_text": ""
    }
]

"""
