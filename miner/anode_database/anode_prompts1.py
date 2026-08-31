# -*- coding: utf-8 -*-
"""负极 Prompt — 材料+性能+条件"""
# ==================== Agent 1: 材料本征属性 ====================

PROMPT_ANODE_MATERIAL_INCLUDE = """You are a lithium battery anode material expert. Determine if the paragraph contains quantitative anode material intrinsic property data (NOT electrochemical performance like capacity, efficiency, overpotential, etc.).

Context: battery system={battery_system_context}, material_id={material_id}
Property names from: {explanation}
Rules: Find ALL matching properties with numerical values or clear categorical values (e.g., space group). For comparison studies, extract for EACH material separately. If none, return [].

Paragraph: The lattice parameters are a=2.87 Å, c=14.2 Å, space group R-3m.
List:```JSON
["Lattice_Parameters", "Space_Group"]
```

Paragraph: {paragraph}
List:"""

PROMPT_ANODE_MATERIAL_EXTRACT = """Extract quantitative values for {prop}.
Context: battery system={battery_system_context}, material_id={material_id}
Format: {structured_data}
Rules: Extract only explicit data. Use the exact JSON schema provided. source_text must be verbatim from paragraph.
Reference: {information}
{example}
Paragraph: {paragraph}
JSON:"""

# ==================== Agent 2: 电化学性能 ====================
PROMPT_ANODE_PERFORMANCE_INCLUDE = """You are a lithium battery electrochemical testing expert. Determine if the paragraph contains quantitative anode performance data (capacity, ICE, retention, rate, energy density, voltage, impedance, overpotential, cycle life, etc.).

Context: battery system={battery_system_context}, material_id={material_id}, condition_id={condition_id}
Property names from: {explanation}
Rules: Find ALL matching properties with numerical values or clear categorical values (e.g., space group). For comparison studies, extract for EACH material separately. If none, return [].

Paragraph: The initial coulombic efficiency is 85%, and the capacity retention after 100 cycles is 92%.
List:```JSON
["Initial_Coulombic_Efficiency", "Capacity_Retention_at_Nth_Cycle"]
```

Paragraph: {paragraph}
List:"""

PROMPT_ANODE_PERFORMANCE_EXTRACT = """Extract quantitative values for {prop}.
Context: battery system={battery_system_context}, material_id={material_id}, condition_id={condition_id}
Format: {structured_data}
Rules: Extract only explicit data. Use the exact JSON schema provided. source_text must be verbatim from paragraph.
Reference: {information}
{example}
Paragraph: {paragraph}
JSON:"""

# ==================== Agent 3: 制备与测试条件 ====================

CONDITION_EXAMPLE_TEXT = """
Example 1 (half-cell, basic cycling):
Paragraph: The Si anode was tested in a half-cell with Li metal counter electrode, 1M LiPF6 in EC/DMC (1:1) electrolyte, Celgard 2400 separator, Cu foil current collector, in CR2032 coin cell assembled in an Ar glovebox (H2O<0.1 ppm). The active material mass loading was 1.2 mg/cm². The cell was cycled at 0.1C between 0.01-1.5 V vs. Li/Li⁺ at 25 °C. The electrode porosity was 35% and the compaction density was 1.5 g/cm³.
Output:
[
  {{
    "condition_id": "C001",
    "material_id": "sample_001",
    "electrochemical_test_conditions": {{
      "temperature_C": 25,
      "c_rate": "0.1C",
      "current_density_mA_g": 0.0,
      "current_density_mA_cm2": 0.0,
      "voltage_min_V": 0.01,
      "voltage_max_V": 1.5,
      "reference_electrode": "Li/Li+",
      "test_method": "galvanostatic",
      "cycle_number": 0,
      "cycle_range": [],
      "scan_rate_mV_s": 0.0,
      "soc_percent": 0.0,
      "charge_discharge_mode": "",
      "pulse_time_min": 0,
      "source_text": "The cell was cycled at 0.1C between 0.01-1.5 V vs. Li/Li⁺ at 25 °C."
    }},
    "cell_assembly_conditions": {{
      "electrolyte_composition": "1M LiPF6 in EC/DMC (1:1)",
      "separator_type": "Celgard 2400",
      "mass_loading_mg_cm2": 1.2,
      "current_collector": "Cu foil",
      "cell_type": "CR2032",
      "assembly_environment": "Ar glovebox, H2O<0.1 ppm",
      "li_counter_reference": "Li metal",
      "source_text": "The cell was assembled with 1.2 mg/cm² of active material, 1M LiPF6 in EC/DMC (1:1), Celgard 2400 separator, Cu foil current collector, Li metal counter reference, and assembled in an Ar glovebox with H2O<0.1 ppm."
    }},
    "electrode_fabrication_params": {{
      "Electrode_Porosity_BET": [
        {{
          "value": 35.0,
          "unit": "%",
          "method": "",
          "source_text": "The electrode porosity was 35%"
        }}
      ],
      "Electrode_Compaction_Density": [
        {{
          "value": 1.5,
          "unit": "g/cm3",
          "source_text": "the compaction density was 1.5 g/cm³"
        }}
      ]
    }}
  }}
]

Example 2 (full-cell with artificial SEI mechanical properties):
Paragraph: The Li||Li symmetric cell was tested with an artificial SEI layer of 2 μm thickness, Young's modulus 3.5 GPa, tensile strength 12 MPa, elongation at break 150%. The cell was cycled at 0.5 mA/cm² with 1 mAh/cm² at 25 °C. Electrolyte: 1M LiTFSI in DOL/DME (1:1) with 2% LiNO3. Coin cell CR2025.
Output:
[
  {{
    "condition_id": "C002",
    "material_id": "sample_002",
    "electrochemical_test_conditions": {{
      "temperature_C": 25,
      "c_rate": "",
      "current_density_mA_g": 0.0,
      "current_density_mA_cm2": 0.5,
      "voltage_min_V": 0.0,
      "voltage_max_V": 0.0,
      "reference_electrode": "",
      "test_method": "galvanostatic",
      "cycle_number": 0,
      "cycle_range": [],
      "scan_rate_mV_s": 0.0,
      "soc_percent": 0.0,
      "charge_discharge_mode": "",
      "pulse_time_min": 0,
      "source_text": "The cell was cycled at 0.5 mA/cm² with 1 mAh/cm² at 25 °C."
    }},
    "cell_assembly_conditions": {{
      "electrolyte_composition": "1M LiTFSI in DOL/DME (1:1) with 2% LiNO3",
      "separator_type": "",
      "mass_loading_mg_cm2": 0.0,
      "current_collector": "",
      "cell_type": "CR2025",
      "assembly_environment": "",
      "li_counter_reference": "Li",
      "source_text": "Coin cell CR2025."
    }},
    "electrode_fabrication_params": {{
      "Artificial_SEI_Thickness": [
        {{
          "value": 2,
          "unit": "μm",
          "source_text": "an artificial SEI layer of 2 μm thickness"
        }}
      ],
      "Youngs_Modulus": [
        {{
          "value": 3.5,
          "unit": "GPa",
          "source_text": "Young's modulus 3.5 GPa"
        }}
      ],
      "Tensile_Strength": [
        {{
          "value": 12,
          "unit": "MPa",
          "source_text": "tensile strength 12 MPa"
        }}
      ],
      "Elongation_at_Break": [
        {{
          "value": 150,
          "unit": "%",
          "source_text": "elongation at break 150%"
        }}
      ]
    }}
  }}
]
"""

PROMPT_ANODE_CONDITION_INCLUDE = """You are an electrochemical testing condition analyst. Determine if the paragraph contains any information about test conditions for anode measurements. Conditions include: electrochemical test conditions (temperature, C-rate, voltage window, test method, cycle number, scan rate, SOC, etc.), cell assembly conditions (electrolyte composition, separator, mass loading, current collector, cell type, assembly environment, counter electrode), or electrode fabrication parameters (porosity, coating thickness, compaction density, artificial SEI thickness, mechanical properties, wettability).

If the paragraph describes any of these, return "yes". Otherwise, return "no". Do not extract values here; only judge existence.
Context: battery system={battery_system_context}, material_id={material_id}

Paragraph: The cell was cycled at 0.1C between 0.01-1.5 V at 25 °C. The electrolyte was 1M LiPF6 in EC/DMC.
Answer: yes

Paragraph: {paragraph}
Answer:"""

PROMPT_ANODE_CONDITION_EXTRACT = """Extract detailed fabrication and test conditions for anode. Output is a list of condition objects.

Context: battery system={battery_system_context}, material_id={material_id}

For electrode fabrication parameters (porosity, coating thickness, compaction density, artificial SEI thickness, mechanical properties, wettability), adopt the tag-list format. Each parameter serves as a key within the electrode_fabrication_params object. Its corresponding value is a list of objects containing the following fields: value, unit, method (if available), and source_text.

For electrochemical test conditions and cell assembly conditions, use nested objects with fixed field names (no list wrapping).


Output schema:
[
  {{
    "condition_id": "",           
    "material_id": "",            
    "electrochemical_test_conditions": {{ 
      "cell_config": "",
      "temperature_C": 0.0,
      "c_rate": "",
      "current_density_mA_g": 0.0,
      "current_density_mA_cm2": 0.0,
      "voltage_min_V": 0.0,
      "voltage_max_V": 0.0,
      "reference_electrode": "",
      "test_method": "",
      "cycle_number": 0,
      "cycle_range": [],
      "scan_rate_mV_s": 0.0,
      "soc_percent": 0.0,
      "charge_discharge_mode": "",
      "pulse_time_min": 0 
    }},
    "cell_assembly_conditions": {{
      "electrolyte_composition": "",
      "separator_type": "",
      "mass_loading_mg_cm2": 0.0,
      "current_collector": "",
      "cell_type": "",
      "assembly_environment": "",
      "li_counter_reference": ""
    }},
    "electrode_fabrication_params": {{ 
     // only keys mentioned in paragraph, each with list of {{value, unit, method, source_text}}
     }}
  }}
]


Rules:

**General rules:**
- Create a separate condition object for each distinct set of test parameters (different C-rate, different test method, different SOC, etc.).
- condition_id: Unique ID (e.g., C001). Reuse from known_conditions if identical; otherwise generate new (C001, C002...).
- material_id: Must match the material_id provided in the context (same as used in Agent 1).
- source_text: Verbatim sentence or clause from which the conditions were extracted.
- Omit any field that is not explicitly mentioned (do not output zero/placeholder values for missing fields).

**Electrochemical test conditions extraction rules:**

- cell_config: Must be one of "half-cell", "full-cell", or "symmetric". Infer from context:
    - "half-cell": Contains a lithium metal counter/reference electrode (e.g., "Li metal", "vs. Li/Li⁺") and no positive electrode material is mentioned as working electrode.
    - "full-cell": Both a cathode and an anode are specified (e.g., "NCM811||graphite", "full cell", "pouch cell" with cathode material).
    - "symmetric": Explicitly called "symmetric cell", or identical electrodes (e.g., "Li||Li", "LiₓSi||LiₓSi").
- temperature_C: Extract in °C. If given in K, subtract 273.15.
- c_rate: String like "0.1C", "1C". If current density is given in mA/g or mA/cm², leave "c_rate" empty and use "current_density_mA_g" or "current_density_mA_cm2" instead.
- current_density_mA_g: Numerical value in mA/g (based on active material mass). Do not include if C‑rate is used.
- current_density_mA_cm2: Numerical value in mA/cm² (geometric area). Prefer for Li metal or symmetric cells.
- voltage_min_V, voltage_max_V: Lower and upper cut‑off voltages in V. For half‑cells, values are vs. reference electrode (usually Li/Li⁺). For full‑cells, they are cell voltages. If only one value given (e.g., "up to 1.5 V"), set min=0.0 and extract max.
- reference_electrode: For half‑cells, use "Li/Li⁺" (or "Na/Na⁺" for sodium). For full‑cells or symmetric cells, leave empty string.
- test_method: One of "galvanostatic", "GITT", "EIS", "CV", "PITT". If method is mentioned (e.g., "cyclic voltammetry" → "CV", "galvanostatic charge/discharge" → "galvanostatic").
- cycle_number: Integer number of cycles for long‑term cycling (e.g., "500 cycles" → 500). For formation cycles (e.g., first 3 cycles), do not include unless explicitly stated as the number of cycles for a performance metric.
- cycle_range: List of two integers [start, end] for stable cycling region (e.g., "from cycle 2 to 200" → [2,200]).
- scan_rate_mV_s: For CV, scan rate in mV/s. Convert from V/s if needed (1 V/s = 1000 mV/s).
- soc_percent: State of charge (0–100). For GITT or EIS at a specific SOC (e.g., "50% SOC" → 50).
- charge_discharge_mode: "CC" (constant current), "CV" (constant voltage), "CCCV". If both CC and CV steps mentioned, use "CCCV". Omit if not specified.
- pulse_time_min: For GITT, duration of current pulse in minutes. Convert from seconds if needed (divide by 60).

**Cell assembly conditions extraction rules:**

- electrolyte_composition: Raw string, e.g., "1M LiPF6 in EC/DMC (1:1)", "1M LiTFSI in DOL/DME (1:1) with 2% LiNO3". Do not parse.
- separator_type: e.g., "Celgard 2400", "glass fiber", "PP/PE/PP". Omit if not mentioned.
- mass_loading_mg_cm2: Active material loading in mg/cm². If given in g/cm², multiply by 1000.
- current_collector: e.g., "Cu foil", "Al foil", "stainless steel". For full‑cells, can specify both ("Cu (anode), Al (cathode)").
- cell_type: "CR2032", "CR2025", "pouch cell", "Swagelok", "18650", etc.
- assembly_environment: e.g., "Ar glovebox, H2O<0.1 ppm", "dry room".
- li_counter_reference: For half‑cells: "Li metal". For symmetric cells: "Li". Omit for full‑cells.

**Electrode fabrication parameters (tag-list format):**

- Only include parameters that are explicitly mentioned with numerical values or clear presence.
- Use the exact key names (case‑sensitive): "Electrode_Porosity_BET", "Coating_Thickness", "Electrode_Compaction_Density", "Artificial_SEI_Thickness", "Youngs_Modulus", "Tensile_Strength", "Elongation_at_Break", "Electrolyte_Wettability_Contact_Angle".
- Each value is a list of objects: {{"value": float, "unit": str, "method": str, "source_text": str}}.
- Units and conversion:
    - Electrode_Porosity_BET: "%". Method can be "BET", "MIP", or "calculation".
    - Coating_Thickness: "nm" (convert μm to nm: ×1000). Method "TEM" or "SEM".
    - Electrode_Compaction_Density: "g/cm3".
    - Artificial_SEI_Thickness: "μm".
    - Youngs_Modulus: "GPa" (convert MPa to GPa: ÷1000).
    - Tensile_Strength: "MPa".
    - Elongation_at_Break: "%".
    - Electrolyte_Wettability_Contact_Angle: "°".
- If a parameter is not mentioned, do NOT include its key in "electrode_fabrication_params".


Examples:
{condition_example_text}
If uncertain, reply with "I do not know".
Paragraph: {paragraph}
JSON:"""