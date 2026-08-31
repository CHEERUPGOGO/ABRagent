# -*- coding: utf-8 -*-
"""电解质 Prompt — 属性+性能+条件（增强版，对应 format1 的三 Agent 分组）"""

# ==================== Agent 1: 材料本征属性（19 标签） ====================

PROMPT_ELECTROLYTE_MATERIAL_INCLUDE = """You are a lithium battery electrolyte expert. Determine if the paragraph contains quantitative electrolyte material intrinsic property data (NOT electrochemical performance metrics like conductivity, resistance, capacity, etc.).

Context: battery system={battery_system_context}, material_id={material_id}
Property names from: {explanation}
Rules: Find ALL matching properties with numerical values or clear categorical values (e.g., molecule name). For comparison studies, extract for EACH electrolyte formulation separately. If none, return [].

Paragraph: DFT calculations at the PBE0/def2-TZVP level gave a Li\u207a-EC binding energy of -2.68 eV and a Li\u207a-DMC binding energy of -2.15 eV.
List:```JSON
["Li_Solvent_Binding_Energy"]
```

Paragraph: {paragraph}
List:"""


PROMPT_ELECTROLYTE_MATERIAL_EXTRACT = """Extract quantitative values for {prop}.
Context: battery system={battery_system_context}, material_id={material_id}
Format: {structured_data}
Rules: Extract only explicit data. Use the exact JSON schema provided. source_text must be verbatim from paragraph.
Reference: {information}
{example}
Paragraph: {paragraph}
JSON:"""


# ==================== Agent 2: 电化学性能（32 标签） ====================

PROMPT_ELECTROLYTE_PERFORMANCE_INCLUDE = """You are a lithium battery electrolyte performance analyst. Determine if the paragraph contains quantitative electrolyte performance data (conductivity, resistance, stability window, capacity retention, impedance, overpotential, gas evolution, etc.).

Context: battery system={battery_system_context}, material_id={material_id}, condition_id={condition_id}
Property names from: {explanation}
Rules: Find ALL matching metrics with numerical values. For comparison studies, extract for EACH electrolyte formulation separately. If none, return [].

Paragraph: EIS measurements showed the ionic conductivity of 1M LiPF6 in EC/DMC (1:1) at 25 \u00b0C is 10.2 mS/cm with a charge transfer resistance of 38.2 \u03a9.
List:```JSON
["Ionic_Conductivity", "Charge_Transfer_Resistance"]
```

Paragraph: {paragraph}
List:"""


PROMPT_ELECTROLYTE_PERFORMANCE_EXTRACT = """Extract quantitative values for {prop}.
Context: battery system={battery_system_context}, material_id={material_id}, condition_id={condition_id}
Format: {structured_data}
Rules: Extract only explicit data. Use the exact JSON schema provided. Include condition_id where applicable. source_text must be verbatim from paragraph.
Reference: {information}
{example}
Paragraph: {paragraph}
JSON:"""


# ==================== Agent 3: 制备与测试条件（6 标签） ====================

CONDITION_EXAMPLE_TEXT = """
Example 1 (Formulation + ionic conductivity measurement):
Paragraph: The electrolyte consisting of 1M LiPF6 in EC/EMC (3:7 vol%) with 2 wt% vinylene carbonate (VC) as additive was prepared in an Ar-filled glovebox (H2O < 0.1 ppm, O2 < 0.1 ppm) by mixing in a glass vial for 30 min. The water content was measured to be 8.5 ppm by Karl Fischer titration. EIS measurements of the electrolyte in a symmetric SS||SS cell at 25 \u00b0C gave an ionic conductivity of 9.8 mS/cm.
Output:
[
  {{
    "condition_id": "C001",
    "material_id": "sample_001",
    "electrochemical_test_conditions": {{
      "temperature_C": 25,
      "c_rate": "",
      "current_density_mA_g": 0.0,
      "current_density_mA_cm2": 0.0,
      "voltage_min_V": 0.0,
      "voltage_max_V": 0.0,
      "reference_electrode": "",
      "test_method": "EIS",
      "cycle_number": 0,
      "cycle_range": [],
      "scan_rate_mV_s": 0.0,
      "soc_percent": 0.0,
      "charge_discharge_mode": "",
      "pulse_time_min": 0,
      "source_text": "EIS measurements of the electrolyte in a symmetric SS||SS cell at 25 \u00b0C gave an ionic conductivity of 9.8 mS/cm"
    }},
    "cell_assembly_conditions": {{
      "electrolyte_composition": "1M LiPF6 in EC/EMC (3:7 vol%) with 2 wt% VC",
      "separator_type": "",
      "mass_loading_mg_cm2": 0.0,
      "current_collector": "SS||SS",
      "cell_type": "",
      "assembly_environment": "Ar-filled glovebox (H2O < 0.1 ppm, O2 < 0.1 ppm)",
      "li_counter_reference": ""
    }},
    "electrode_fabrication_params": {{ }}
  }}
]

Example 2 (Electrochemical stability window + SEI characterization):
Paragraph: LSV was performed using a Li || Pt cell from OCP to 6.0 V vs. Li/Li\u207a at 1.0 mV/s and 25 \u00b0C in 1.2M LiPF6 EC/DEC (1:1). The anodic stability onset was 4.8 V. After cycling, XPS of the Cu electrode showed the SEI composition: 45 at% LiF, 30 at% Li2CO3, and the SEI thickness by TEM was 12.5 nm. The cell was assembled in a dry room (dew point -50 \u00b0C).
Output:
[
  {{
    "condition_id": "C002",
    "material_id": "sample_002",
    "electrochemical_test_conditions": {{
      "temperature_C": 25,
      "c_rate": "",
      "current_density_mA_g": 0.0,
      "current_density_mA_cm2": 0.0,
      "voltage_min_V": 0.0,
      "voltage_max_V": 6.0,
      "reference_electrode": "Li/Li+",
      "test_method": "LSV",
      "cycle_number": 0,
      "cycle_range": [],
      "scan_rate_mV_s": 1.0,
      "soc_percent": 0.0,
      "charge_discharge_mode": "",
      "pulse_time_min": 0,
      "source_text": "LSV was performed using a Li || Pt cell from OCP to 6.0 V vs. Li/Li\u207a at 1.0 mV/s and 25 \u00b0C"
    }},
    "cell_assembly_conditions": {{
      "electrolyte_composition": "1.2M LiPF6 EC/DEC (1:1)",
      "separator_type": "",
      "mass_loading_mg_cm2": 0.0,
      "current_collector": "Pt",
      "cell_type": "",
      "assembly_environment": "dry room (dew point -50 \u00b0C)",
      "li_counter_reference": "Li"
    }},
    "electrode_fabrication_params": {{ }}
  }}
]
"""


PROMPT_ELECTROLYTE_CONDITION_INCLUDE = """You are an electrolyte formulation and testing condition analyst. Determine if the paragraph contains any information about electrolyte preparation or test conditions. This includes: formulation parameters (salt type, concentration, solvent composition, additives), preparation conditions (water content, mixing process, glovebox environment), or electrochemical test conditions (temperature, voltage window, test method, cell configuration).

If the paragraph describes any of these, return "yes". Otherwise, return "no". Do not extract values here; only judge existence.
Context: battery system={battery_system_context}, material_id={material_id}

Paragraph: 1M LiPF6 in EC/DMC (1:1 vol%) was prepared in an Ar glovebox with H2O < 0.1 ppm.
Answer: yes

Paragraph: {paragraph}
Answer:"""


PROMPT_ELECTROLYTE_CONDITION_EXTRACT = """Extract detailed formulation and test conditions for electrolyte. Output is a list of condition objects.

Context: battery system={battery_system_context}, material_id={material_id}

For preparation/formulation parameters (salt type, concentration, solvent composition, additives, water content, mixing process), these can be placed in the cell_assembly_conditions section. For electrochemical test conditions, use the electrochemical_test_conditions section.

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
    "electrode_fabrication_params": {{ }}
  }}
]


Rules:

**General rules:**
- Create a separate condition object for each distinct set of parameters.
- condition_id: Unique ID (e.g., C001). Reuse from known_conditions if identical; otherwise generate new.
- material_id: Must match the material_id provided in the context.
- source_text: Verbatim sentence or clause from which the conditions were extracted.
- Omit any field that is not explicitly mentioned (do not output zero/placeholder values for missing fields).

**Formulation & preparation parameters extraction rules:**

- electrolyte_composition: Full description including lithium salt, concentration, solvent ratios, and additives. Preserve exact wording (e.g., "1M LiPF6 in EC/EMC (3:7 vol%) with 2 wt% VC").
- assembly_environment: Glovebox conditions, dry room, or other preparation environment.
- current_collector: For electrolyte-only measurements, the working electrode (e.g., "SS", "Pt", "Au").

**Electrochemical test conditions extraction rules:**

- cell_config: "symmetric" for SS||SS or blocking electrode cells; "half-cell" for Li || working electrode cells.
- temperature_C: Extract in \u00b0C. If given in K, subtract 273.15.
- test_method: "EIS", "LSV", "CV", "galvanostatic", "chronoamperometry".
- scan_rate_mV_s: For LSV/CV, scan rate in mV/s.
- reference_electrode: For half-cell measurements (e.g., "Li/Li\u207a").
- voltage_min_V, voltage_max_V: For LSV or CV.

**Electrode fabrication parameters:**
- For electrolyte measurements, this is typically empty {{ }} unless electrode fabrication is described.

Examples:
{condition_example_text}
If uncertain, reply with "I do not know".
Paragraph: {paragraph}
JSON:"""
