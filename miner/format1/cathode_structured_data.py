# -*- coding: utf-8 -*-
"""
正极材料结构化输出
每个标签定义标准化的结构化数据输出 schema。
"""

# ==================== Agent 1: 材料本征属性 ====================

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

Crystal_Space_Group = """
"Crystal_Space_Group": [
    {
        "value": "",
        "unit": "",
        "method": "",
        "source_text": ""
    }
]
"""

Lithium_Ion_Diffusion_Activation_Energy = """
"Lithium_Ion_Diffusion_Activation_Energy": [
    {
        "value": null,
        "unit": "eV",
        "method": "",
        "path": "",
        "soc": "",
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

Electronic_Band_Gap = """
"Electronic_Band_Gap": [
    {
        "value": null,
        "unit": "eV",
        "type": "",
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

Formation_Energy = """
"Formation_Energy": [
    {
        "value": null,
        "unit": "eV/f.u.",
        "method": "",
        "source_text": ""
    }
]
"""

Volume_Change_Ratio = """
"Volume_Change_Ratio": [
    {
        "value": null,
        "unit": "%",
        "from_state": "",
        "to_state": "",
        "source_text": ""
    }
]
"""

a_c_axis_expansion = """
"a_c_axis_expansion": [
    {
        "value": {
            "delta_a": null,
            "delta_c": null,
            "delta_V": null
        },
        "unit": "Å / Å / %",
        "from_state": "",
        "to_state": "",
        "source_text": ""
    }
]
"""

Oxygen_Vacancy_Concentration = """
"Oxygen_Vacancy_Concentration": [
    {
        "value": null,
        "unit": "mole fraction",
        "method": "",
        "source_text": ""
    }
]
"""

Oxygen_Vacancy_Formation_Energy = """
"Oxygen_Vacancy_Formation_Energy": [
    {
        "value": null,
        "unit": "eV",
        "method": "",
        "source_text": ""
    }
]
"""

Transition_Metal_Migration_Energy_Barrier = """
"Transition_Metal_Migration_Energy_Barrier": [
    {
        "value": null,
        "unit": "eV",
        "element": "",
        "path": "",
        "method": "",
        "source_text": ""
    }
]
"""

Interlayer_Spacing_of_TM_Layers = """
"Interlayer_Spacing_of_TM_Layers": [
    {
        "value": null,
        "unit": "Å",
        "plane": "",
        "state": "",
        "source_text": ""
    }
]
"""

Density_of_States_at_Fermi_Level = """
"Density_of_States_at_Fermi_Level": [
    {
        "value": null,
        "unit": "states/eV/cell",
        "method": "",
        "source_text": ""
    }
]
"""

Bader_Charge = """
"Bader_Charge": [
    {
        "value": {},
        "unit": "e",
        "method": "",
        "source_text": ""
    }
]
"""

Chemical_Composition_Mole_Fractions = """
"Chemical_Composition_Mole_Fractions": [
    {
        "value": {},
        "unit": "mole fraction",
        "method": "",
        "source_text": ""
    }
]
"""

Element_Valence_State = """
"Element_Valence_State": [
    {
        "value": {},
        "unit": "oxidation state",
        "state": "",
        "method": "",
        "source_text": ""
    }
]
"""

Jahn_Teller_Active_Ion_Content = """
"Jahn_Teller_Active_Ion_Content": [
    {
        "value": null,
        "unit": "mole fraction",
        "derived": true,
        "source_text": ""
    }
]
"""

devtE = """
"devtE": [
    {
        "value": null,
        "unit": "dimensionless",
        "derived": true,
        "method": "formula_based",
        "source_text": ""
    }
]
"""

VEd = """
"VEd": [
    {
        "value": null,
        "unit": "dimensionless",
        "derived": true,
        "method": "formula_based",
        "source_text": ""
    }
]
"""

Average_Electron_Affinity = """
"Average_Electron_Affinity": [
    {
        "value": null,
        "unit": "eV",
        "derived": true,
        "method": "elemental_database",
        "source_text": ""
    }
]
"""

Average_Deviation_of_Ionic_Radius = """
"Average_Deviation_of_Ionic_Radius": [
    {
        "value": null,
        "unit": "pm",
        "derived": true,
        "method": "formula_based",
        "source_text": ""
    }
]
"""

Average_Ionization_Energy = """
"Average_Ionization_Energy": [
    {
        "value": null,
        "unit": "eV",
        "derived": true,
        "method": "elemental_database",
        "source_text": ""
    }
]
"""

Configurational_Entropy = """
"Configurational_Entropy": [
    {
        "value": null,
        "unit": "J/(mol·K)",
        "derived": true,
        "method": "formula_based",
        "source_text": ""
    }
]
"""

Valence_Electron_Count = """
"Valence_Electron_Count": [
    {
        "value": {},
        "unit": "dimensionless",
        "derived": true,
        "source_text": ""
    }
]
"""

d_Electron_Configuration_Type = """
"d_Electron_Configuration_Type": [
    {
        "value": "",
        "unit": "",
        "derived": true,
        "source_text": ""
    }
]
"""

Li_Ni_mixing_ratio = """
"Li_Ni_mixing_ratio": [
    {
        "value": null,
        "unit": "%",
        "method": "",
        "state": "",
        "source_text": ""
    }
]
"""

Metal_Oxygen_Bond_Energy = """
"Metal_Oxygen_Bond_Energy": [
    {
        "value": null,
        "unit": "eV",
        "bond_type": "",
        "method": "",
        "source_text": ""
    }
]
"""

Primary_Particle_Size_Distribution = """
"Primary_Particle_Size_Distribution": [
    {
        "value": {
            "D10": null,
            "D50": null,
            "D90": null
        },
        "unit": "nm",
        "method": "",
        "source_text": ""
    }
]
"""

Secondary_Particle_Size_Distribution = """
"Secondary_Particle_Size_Distribution": [
    {
        "value": {
            "D10": null,
            "D50": null,
            "D90": null
        },
        "unit": "μm",
        "method": "",
        "source_text": ""
    }
]
"""

Electrode_Pore_Size_Distribution = """
"Electrode_Pore_Size_Distribution": [
    {
        "value": {},
        "unit": "vol%",
        "method": "",
        "source_text": ""
    }
]
"""

Surface_Spinel_Layer_Thickness = """
"Surface_Spinel_Layer_Thickness": [
    {
        "value": null,
        "unit": "nm",
        "state": "",
        "method": "",
        "source_text": ""
    }
]
"""

XPS_ROCO2Li_Peak = """
"XPS_ROCO2Li_Peak": [
    {
        "value": null,
        "unit": "at%",
        "state": "",
        "source_text": ""
    }
]
"""

XPS_C_O_Peak = """
"XPS_C_O_Peak": [
    {
        "value": null,
        "unit": "at%",
        "state": "",
        "source_text": ""
    }
]
"""

XPS_NiF2_Peak = """
"XPS_NiF2_Peak": [
    {
        "value": null,
        "unit": "at%",
        "state": "",
        "source_text": ""
    }
]
"""

LixPOyFz = """
"LixPOyFz": [
    {
        "value": null,
        "unit": "at%",
        "state": "",
        "source_text": ""
    }
]
"""


# ==================== Agent 2: 电化学性能 ====================

Electronic_Conductivity_Bulk = """
"Electronic_Conductivity_Bulk": [
    {
        "value": null,
        "unit": "S/cm",
        "method": "",
        "source_text": ""
    }
]
"""

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

Discharge_Specific_Capacity_Initial = """
"Discharge_Specific_Capacity_Initial": [
    {
        "value": null,
        "unit": "mAh/g",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Rate_Performance = """
"Rate_Performance": [
    {
        "value": null,
        "unit": "ratio",
        "condition_id": "",
        "high_rate": "",
        "low_rate": "",
        "source_text": ""
    }
]
"""

Capacity_Retention_Ratio = """
"Capacity_Retention_Ratio": [
    {
        "value": null,
        "unit": "%",
        "condition_id": "",
        "cycle_number": 0,
        "source_text": ""
    }
]
"""

Rate_Capability_Profile = """
"Rate_Capability_Profile": [
    {
        "value": [],
        "unit": "mAh/g",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Nominal_Discharge_Voltage = """
"Nominal_Discharge_Voltage": [
    {
        "value": null,
        "unit": "V",
        "condition_id": "",
        "reference": "",
        "source_text": ""
    }
]
"""

Average_Discharge_Voltage = """
"Average_Discharge_Voltage": [
    {
        "value": null,
        "unit": "V",
        "condition_id": "",
        "reference": "",
        "source_text": ""
    }
]
"""

Charge_Discharge_Voltage_Gap = """
"Charge_Discharge_Voltage_Gap": [
    {
        "value": null,
        "unit": "V",
        "condition_id": "",
        "soc": null,
        "source_text": ""
    }
]
"""

Ion_Diffusion_Coefficient = """
"Ion_Diffusion_Coefficient": [
    {
        "value": null,
        "unit": "cm2/s",
        "condition_id": "",
        "method": "",
        "voltage_V": null,
        "source_text": ""
    }
]
"""

Gravimetric_Energy_Density = """
"Gravimetric_Energy_Density": [
    {
        "value": null,
        "unit": "Wh/kg",
        "condition_id": "",
        "basis": "",
        "source_text": ""
    }
]
"""

Volumetric_Energy_Density = """
"Volumetric_Energy_Density": [
    {
        "value": null,
        "unit": "Wh/L",
        "condition_id": "",
        "basis": "",
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

Self_Discharge_Rate = """
"Self_Discharge_Rate": [
    {
        "value": null,
        "unit": "%/month",
        "condition_id": "",
        "soc_percent": null,
        "source_text": ""
    }
]
"""

Thermal_Runaway_Onset_Temperature = """
"Thermal_Runaway_Onset_Temperature": [
    {
        "value": null,
        "unit": "°C",
        "method": "",
        "soc_percent": null,
        "source_text": ""
    }
]
"""

Phase_Transition_Voltage = """
"Phase_Transition_Voltage": [
    {
        "value": null,
        "unit": "V",
        "transition_name": "",
        "condition_id": "",
        "source_text": ""
    }
]
"""

Transition_Metal_Dissolution_Amount = """
"Transition_Metal_Dissolution_Amount": [
    {
        "value": null,
        "unit": "ppm",
        "element": "",
        "condition_id": "",
        "method": "",
        "source_text": ""
    }
]
"""

O2_CO2_Evolution = """
"O2_CO2_Evolution": [
    {
        "value": {"O2": null, "CO2": null},
        "unit": "μmol/g",
        "method": "",
        "condition_id": "",
        "source_text": ""
    }
]
"""


# ==================== Agent 3: 制备与测试条件 ====================

Active_Material_Mass_Fraction = """
"Active_Material_Mass_Fraction": [
    {
        "value": null,
        "unit": "%",
        "source_text": ""
    }
]
"""

Electrode_Thickness = """
"Electrode_Thickness": [
    {
        "value": null,
        "unit": "μm",
        "source_text": ""
    }
]
"""

Mass_Loading = """
"Mass_Loading": [
    {
        "value": null,
        "unit": "mg/cm2",
        "source_text": ""
    }
]
"""

Compacted_Density = """
"Compacted_Density": [
    {
        "value": null,
        "unit": "g/cm3",
        "source_text": ""
    }
]
"""

Conductive_Additive_Binder_Ratio = """
"Conductive_Additive_Binder_Ratio": [
    {
        "value": {"conductive": null, "binder": null},
        "unit": "wt%",
        "source_text": ""
    }
]
"""

Electronic_Conductivity_Electrode = """
"Electronic_Conductivity_Electrode": [
    {
        "value": null,
        "unit": "S/cm",
        "method": "",
        "source_text": ""
    }
]
"""

Peel_Strength = """
"Peel_Strength": [
    {
        "value": null,
        "unit": "N/m",
        "angle": "",
        "source_text": ""
    }
]
"""

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

