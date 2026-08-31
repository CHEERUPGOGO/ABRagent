# -*- coding: utf-8 -*-
"""正极 Prompt — 材料+性能+条件（增强版，含内嵌示例和CONDITION_EXAMPLE_TEXT）"""

# ==================== Agent 1: 材料本征属性 ====================

PROMPT_CATHODE_MATERIAL_INCLUDE = """You are a lithium battery cathode material expert. Determine if the paragraph contains quantitative cathode material intrinsic property data (NOT electrochemical performance like capacity, efficiency, voltage, etc.).

Context: battery system={battery_system_context}, material_id={material_id}
Property names from: {explanation}
Rules: Find ALL matching properties with numerical values or clear categorical values (e.g., space group). For comparison studies, extract for EACH material separately. If none, return [].

Paragraph: The lattice parameters of LiNi0.8Co0.1Mn0.1O2 were refined to a=b=2.872 \u00c5, c=14.215 \u00c5, space group R-3m.
List:```JSON
["Lattice_Parameters", "Crystal_Space_Group"]
```

Paragraph: {paragraph}
List:"""


PROMPT_CATHODE_MATERIAL_EXTRACT = """Extract quantitative values for {prop}.
Context: battery system={battery_system_context}, material_id={material_id}
Format: {structured_data}
Rules: Extract only explicit data. Use the exact JSON schema provided. source_text must be verbatim from paragraph.
Reference: {information}
{example}
Paragraph: {paragraph}
JSON:"""


# ==================== Agent 2: 电化学性能 ====================

PROMPT_CATHODE_PERFORMANCE_INCLUDE = """You are a lithium battery electrochemical testing expert. Determine if the paragraph contains quantitative cathode performance data (capacity, ICE, retention, rate, energy density, voltage, impedance, cycle life, etc.).

Context: battery system={battery_system_context}, material_id={material_id}, condition_id={condition_id}
Property names from: {explanation}
Rules: Find ALL matching metrics with numerical values. For comparison studies, extract for EACH material separately. If none, return [].

Paragraph: The NCM811 cathode delivered an initial discharge capacity of 198.5 mAh/g at 0.1C with an initial Coulombic efficiency of 86%.
List:```JSON
["Discharge_Specific_Capacity_Initial", "Initial_Coulombic_Efficiency"]
```

Paragraph: {paragraph}
List:"""


PROMPT_CATHODE_PERFORMANCE_EXTRACT = """Extract quantitative values for {prop}.
Context: battery system={battery_system_context}, material_id={material_id}, condition_id={condition_id}
Format: {structured_data}
Rules: Extract only explicit data. Use the exact JSON schema provided. Include condition_id in output. source_text must be verbatim from paragraph.
Reference: {information}
{example}
Paragraph: {paragraph}
JSON:"""


# ==================== Agent 3: 制备与测试条件 ====================

CONDITION_EXAMPLE_TEXT = """
Example 1 (NCM811 half-cell, standard cycling):
Paragraph: The NCM811 cathode was tested in a CR2032 half-cell with Li metal counter electrode, 1M LiPF6 in EC/DMC (1:1) electrolyte, Celgard 2400 separator, Al foil current collector, assembled in an Ar glovebox. The active material mass loading was 3.2 mg/cm\u00b2 with 2% carbon black and 3% PVDF binder. The cell was cycled at 0.5C between 2.8-4.3 V vs. Li/Li\u207a at 25 \u00b0C. The electrode porosity was 35% and the coating thickness was 65 \u03bcm.
Output:
[
  {{
    "condition_id": "C001",
    "material_id": "sample_001",
    "electrochemical_test_conditions": {{
      "temperature_C": 25,
      "c_rate": "0.5C",
      "current_density_mA_g": 0.0,
      "current_density_mA_cm2": 0.0,
      "voltage_min_V": 2.8,
      "voltage_max_V": 4.3,
      "reference_electrode": "Li/Li+",
      "test_method": "galvanostatic",
      "cycle_number": 0,
      "cycle_range": [],
      "scan_rate_mV_s": 0.0,
      "soc_percent": 0.0,
      "charge_discharge_mode": "",
      "pulse_time_min": 0,
      "source_text": "The cell was cycled at 0.5C between 2.8-4.3 V vs. Li/Li\u207a at 25 \u00b0C."
    }},
    "cell_assembly_conditions": {{
      "electrolyte_composition": "1M LiPF6 in EC/DMC (1:1)",
      "separator_type": "Celgard 2400",
      "mass_loading_mg_cm2": 3.2,
      "current_collector": "Al foil",
      "cell_type": "CR2032",
      "assembly_environment": "Ar glovebox",
      "li_counter_reference": "Li metal"
    }},
    "electrode_fabrication_params": {{
      "Electrode_Porosity": [
        {{
          "value": 35.0,
          "unit": "%",
          "method": "",
          "source_text": "The electrode porosity was 35%"
        }}
      ],
      "Coating_Thickness": [
        {{
          "value": 65,
          "unit": "nm",
          "method": "",
          "source_text": "the coating thickness was 65 \u03bcm"
        }}
      ]
    }}
  }}
]

Example 2 (LFP full-cell with rate test):
Paragraph: The LFP || graphite pouch cell (1 Ah) with an N/P ratio of 1.15 was tested at multiple rates between 2.5-3.8 V at 30 \u00b0C. Electrolyte: 1.2M LiPF6 in EC/EMC (3:7) with 2% VC. The electrode compaction density was 2.3 g/cm\u00b3.
Output:
[
  {{
    "condition_id": "C003",
    "material_id": "sample_002",
    "electrochemical_test_conditions": {{
      "temperature_C": 30,
      "c_rate": "",
      "current_density_mA_g": 0.0,
      "current_density_mA_cm2": 0.0,
      "voltage_min_V": 2.5,
      "voltage_max_V": 3.8,
      "reference_electrode": "",
      "test_method": "galvanostatic",
      "cycle_number": 0,
      "cycle_range": [],
      "scan_rate_mV_s": 0.0,
      "soc_percent": 0.0,
      "charge_discharge_mode": "",
      "pulse_time_min": 0,
      "source_text": "The LFP || graphite pouch cell was tested between 2.5-3.8 V at 30 \u00b0C."
    }},
    "cell_assembly_conditions": {{
      "electrolyte_composition": "1.2M LiPF6 in EC/EMC (3:7) with 2% VC",
      "separator_type": "",
      "mass_loading_mg_cm2": 0.0,
      "current_collector": "",
      "cell_type": "pouch cell",
      "assembly_environment": "",
      "li_counter_reference": ""
    }},
    "electrode_fabrication_params": {{
      "Compacted_Density": [
        {{
          "value": 2.3,
          "unit": "g/cm3",
          "source_text": "The electrode compaction density was 2.3 g/cm\u00b3"
        }}
      ]
    }}
  }}
]
"""


PROMPT_CATHODE_CONDITION_INCLUDE = """You are an electrochemical testing condition analyst. Determine if the paragraph contains any information about test conditions for cathode measurements. Conditions include: electrochemical test conditions (temperature, C-rate, voltage window, test method, cycle number, scan rate, SOC, etc.), cell assembly conditions (electrolyte composition, separator, mass loading, current collector, cell type, assembly environment, counter electrode), or electrode fabrication parameters (porosity, coating thickness, compaction density, coating composition, etc.).

If the paragraph describes any of these, return "yes". Otherwise, return "no". Do not extract values here; only judge existence.
Context: battery system={battery_system_context}, material_id={material_id}

Paragraph: The NCM811 cathode was cycled at 0.1C between 2.8-4.3 V at 25 \u00b0C.
Answer: yes

Paragraph: {paragraph}
Answer:"""


PROMPT_CATHODE_CONDITION_EXTRACT = """Extract detailed fabrication and test conditions for cathode. Output is a list of condition objects.

Context: battery system={battery_system_context}, material_id={material_id}

For electrode fabrication parameters (porosity, coating thickness, compaction density, coating composition, mass loading), adopt the tag-list format. Each parameter serves as a key within the electrode_fabrication_params object. Its corresponding value is a list of objects containing the following fields: value, unit, method (if available), and source_text.

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
- Create a separate condition object for each distinct set of test parameters.
- condition_id: Unique ID (e.g., C001). Reuse from known_conditions if identical; otherwise generate new.
- material_id: Must match the material_id provided in the context.
- source_text: Verbatim sentence or clause from which the conditions were extracted.
- Omit any field that is not explicitly mentioned (do not output zero/placeholder values for missing fields).

**Electrochemical test conditions extraction rules:**

- cell_config: Must be one of "half-cell", "full-cell", or "symmetric". Infer from context.
- temperature_C: Extract in \u00b0C. If given in K, subtract 273.15.
- c_rate: String like "0.1C", "1C". If current density is given in mA/g or mA/cm\u00b2, leave "c_rate" empty and use "current_density_mA_g" or "current_density_mA_cm2" instead.
- current_density_mA_g: Numerical value in mA/g (based on active material mass).
- current_density_mA_cm2: Numerical value in mA/cm\u00b2 (geometric area).
- voltage_min_V, voltage_max_V: Lower and upper cut-off voltages in V vs. reference electrode.
- reference_electrode: For half-cells, use "Li/Li\u207a" (or "Na/Na\u207a" for sodium). For full-cells, leave empty.
- test_method: One of "galvanostatic", "GITT", "EIS", "CV", "PITT".
- cycle_number: Integer number of cycles for long-term cycling.
- cycle_range: List of two integers [start, end] for stable cycling region.
- scan_rate_mV_s: For CV, scan rate in mV/s. Convert from V/s if needed (1 V/s = 1000 mV/s).
- soc_percent: State of charge (0\u2013100).
- charge_discharge_mode: "CC" (constant current), "CV" (constant voltage), "CCCV".
- pulse_time_min: For GITT, duration of current pulse in minutes.

**Cell assembly conditions extraction rules:**

- electrolyte_composition: Raw string, e.g., "1M LiPF6 in EC/DMC (1:1)".
- separator_type: e.g., "Celgard 2400", "glass fiber".
- mass_loading_mg_cm2: Active material loading in mg/cm\u00b2. If given in g/cm\u00b2, multiply by 1000.
- current_collector: e.g., "Al foil" (cathode), "Cu foil" (anode).
- cell_type: "CR2032", "pouch cell", "Swagelok", "18650", etc.
- assembly_environment: e.g., "Ar glovebox, H2O<0.1 ppm".
- li_counter_reference: For half-cells: "Li metal".

**Electrode fabrication parameters (tag-list format):**

- Only include parameters that are explicitly mentioned.
- Use the exact key names from the schema.
- Each value is a list of objects: {{"value": float, "unit": str, "method": str, "source_text": str}}.
- If a parameter is not mentioned, do NOT include its key in "electrode_fabrication_params".


Examples:
{condition_example_text}
If uncertain, reply with "I do not know".
Paragraph: {paragraph}
JSON:"""
