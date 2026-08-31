"""Phase 0-3 的 prompt 模板 (v2: 全电池 + 本征/性能分离)"""

# Phase 1: 条件提取（串行，只抽条件）

PHASE1_CONDITION_EXTRACT = """You are a lithium battery testing and synthesis condition analyst.

## Task
Extract ALL distinct test/synthesis/characterization conditions described in this paragraph.
Each condition represents one unique experimental setup.

## Scenario Classification
Classify each condition into exactly one scenario:
- "synthesis": material/preparation parameters (precursor ratio, calcination temperature, coating process, etc.)
- "half_cell_test": half-cell electrochemical testing (against Li metal)
- "full_cell_test": full-cell testing (two active electrodes)
- "symmetric_cell_test": symmetric cell testing
- "material_characterization": structural/physical characterization (XRD, SEM, TEM, XPS, BET, TGA, etc.)
- "theoretical_calculation": DFT, MD simulation, machine learning, etc.

## Known Conditions (from previous paragraphs in this paper)
Reuse condition_id from known conditions when the condition is identical.
If the paragraph describes a NEW condition that does not match any known condition, create a new clean condition object.

{known_conditions}

## Output Format
Return ONLY a JSON object:

{{
  "conditions": [
    {{
      "condition_id": "C001",
      "scenario": "half_cell_test",
      "condition": {{
        "temperature": {{"value": 25, "unit": "C"}},
        "c_rate": {{"value": 0.5, "unit": "C"}},
        "current_density": {{"value": null, "unit": "mA/g"}},
        "voltage_range": {{"min": 2.8, "max": 4.3, "unit": "V"}},
        "electrolyte": "1 M LiPF6 in EC/DEC (1:1 v/v)",
        "electrode_config": "half_cell",
        "mass_loading": {{"value": null, "unit": "mg/cm2"}},
        "cycle_number": 0,
        "test_method": "galvanostatic",
        "separator": "",
        "counter_electrode": ""
      }},
      "material_id": "",
      "source_text": ""
    }}
  ]
}}

Rules:
- temperature: always extract if mentioned; default unit "C"
- c_rate: extract as numeric with "C" unit
- current_density: extract as numeric with "mA/g" or "mA/cm2"
- voltage_range: extract min/max values (not just string)
- electrolyte: full description including salt, concentration, solvents
- electrode_config: "half_cell", "full_cell", "symmetric_cell"
- separator: mention if described (e.g. "Celgard 2325")
- counter_electrode: mention if described (e.g. "Li metal", "NCM811")
- Omit fields not mentioned; Null means not mentioned

## Paragraph
{content}
"""

# Phase 2: 属性提取（并行，带已知条件）

PHASE2_PROPERTY_EXTRACT = """You are a lithium battery expert analyzing a section of a research paper.

## Task
Extract ALL quantitative property values from this paragraph.
Each value MUST be linked to:
1. Its condition (by condition_id from the known conditions list)
2. Which battery component it belongs to (cathode / anode / electrolyte / separator / full_cell)
3. Which material it describes

## Property Categories
Identify which category each property belongs to:
- "electrochemical_performance": capacity, retention, coulombic efficiency, energy density, overpotential, impedance, cycle life, etc.
- "physicochemical_property": ionic/electronic conductivity, diffusion coefficient, viscosity, density, thermal stability, contact angle, etc.
- "material_property": lattice parameters, band gap, particle size, porosity, BET, XRD peaks, space group, etc.
- "synthesis_parameter": calcination temp, precursor ratio, coating thickness, drying temp, etc.

## Known Conditions (extracted from this paper)
{known_conditions}

## Output Format
Return ONLY a JSON object with two arrays:

{{
  "conditioned_properties": [
    {{
      "condition_id": "C001",
      "component": "cathode",
      "material_id": "NCM811",
      "property_type": "electrochemical_performance",
      "property_name": "Discharge_Specific_Capacity",
      "value": {{"value": 200, "unit": "mAh/g"}},
      "source_text": ""
    }}
  ],
  "intrinsic_properties": [
    {{
      "component": "cathode",
      "material_id": "NCM811",
      "property_type": "material_property",
      "property_name": "Lattice_Parameters",
      "value": {{"value": {{"a": 2.86}}, "unit": "AA"}},
      "source_text": ""
    }}
  ]
}}

Rules:
- conditioned_properties: properties that depend on test conditions (capacity, impedance, conductivity, overpotential, etc.) MUST have condition_id
- intrinsic_properties: material's own properties that don't depend on test setup (crystal structure, band gap, particle morphology, etc.) NO condition_id
- component: "cathode", "anode", "electrolyte", "separator", "full_cell"
- If a property's condition_id is uncertain, leave it empty Phase 3 will match it later.
- Extract ALL numeric properties, not just the first one.
- CRITICAL: property_name MUST be exactly one of the labels listed in "Available Labels" below. Do NOT invent new names or synonyms. Use the exact label name from the list.
- For condition labels (Mass_Loading, Electrode_Thickness, etc.): also use the exact label name.

## Available Labels
{label_fields}

## Paragraph
{content}
"""

# Phase 1+2 合并: 逐段提取条件+属性

PHASE12_UNIFIED = """You are a lithium battery expert analyzing one paragraph of a research paper.

## Task
Extract from this paragraph:
1. Any test/synthesis conditions mentioned
2. Any quantitative property values (material properties, electrochemical performance, etc.)

Each property MUST be linked to a condition_id from the Known Conditions list below.

## Known Conditions (reuse these condition_id values if matching)
{known_conditions}

## Property Categories
- "electrochemical_performance": capacity, retention, coulombic efficiency, energy density, etc.
- "physicochemical_property": conductivity, diffusion coefficient, viscosity, density, etc.
- "material_property": lattice parameters, band gap, particle size, porosity, etc.
- "synthesis_parameter": calcination temp, precursor ratio, coating thickness, etc.

## Output Format
Return ONLY a JSON object with three arrays:

{{
  "new_conditions": [
    {{
      "condition_id": "C001",
      "scenario": "synthesis",
      "condition": {{
        "temperature": {{"value": 25, "unit": "C"}},
        "c_rate": {{"value": 0.5, "unit": "C"}},
        "current_density": {{"value": null, "unit": "mA/g"}},
        "voltage_range": {{"min": null, "max": null, "unit": "V"}},
        "electrolyte": "",
        "electrode_config": "half_cell",
        "mass_loading": {{"value": null, "unit": "mg/cm2"}},
        "cycle_number": 0,
        "test_method": "",
        "separator": "",
        "counter_electrode": ""
      }},
      "material_id": "",
      "source_text": ""
    }}
  ],
  "conditioned_properties": [
    {{
      "condition_id": "C001",
      "component": "cathode",
      "material_id": "NCM811",
      "property_type": "electrochemical_performance",
      "property_name": "Discharge_Specific_Capacity",
      "value": {{"value": 200, "unit": "mAh/g"}},
      "source_text": ""
    }}
  ],
  "intrinsic_properties": [
    {{
      "component": "cathode",
      "material_id": "NCM811",
      "property_type": "material_property",
      "property_name": "Lattice_Parameters",
      "value": {{"value": {{"a": 2.86}}, "unit": "AA"}},
      "source_text": ""
    }}
  ]
}}

Rules:
- new_conditions: only conditions NOT already in Known Conditions. If it matches, omit it.
- conditioned_properties: properties that depend on test conditions MUST have condition_id. But only link if the text explicitly states or clearly implies the property was measured under that condition. Do NOT assume just because they appear in the same paragraph.
- intrinsic_properties: material's own properties (crystal structure, band gap, particle size, morphology, etc.) do NOT depend on test setup. NO condition_id.
- CRITICAL: property_name MUST be exactly one of the labels in "Available Labels" below. Do NOT invent new names.
- component: "cathode", "anode", "electrolyte", "separator", "full_cell"

## Available Labels
{label_fields}

## Paragraph
{content}
"""

# Phase 1a: Include — 判断段落包含哪些标准标签

PHASE12_INCLUDE = """You are a lithium battery expert. Determine which standard property labels are present in this paragraph.

## Known Materials (use these material_id values)
{known_materials}

## Known Conditions (from this paper)
{known_conditions}

## Task
1. Identify any new test/synthesis conditions mentioned
2. Identify which standard property labels from the list below are present in this paragraph
   - A label is "present" if the paragraph contains a quantitative value for that property
   - property_type: "material_property" | "electrochemical_performance" | "physicochemical_property" | "synthesis_parameter"
   - material_id MUST be from Known Materials list. If the paragraph does not clearly state which material, leave material_id empty rather than guessing.

## Condition Schema (scenario-specific fields)
Base fields for ALL conditions: condition_id, scenario, electrolyte, test_method, source_text, material_id.

Extra fields per scenario (omit irrelevant fields):
- half_cell_test: current_density, voltage_range, deposition_capacity {{"value","unit":"mAh/cm2"}}, precycles (int), counter_electrode, separator
- symmetric_cell_test: current_density, deposition_capacity {{"value","unit":"mAh/cm2"}}, cycle_number, separator
- full_cell_test: c_rate, current_density, voltage_range, mass_loading {{"value","unit":"mg/cm2"}}, NP_ratio, EC_ratio {{"value","unit":"ul/mAh"}}, separator, cycle_number
- material_characterization: instrument, test_parameter, temperature, separator

Use these fields instead of the generic template. Do NOT fill fields irrelevant to the scenario.

## Standard Labels
{explanations}

## Output
Return ONLY a JSON object:
{{
  "new_conditions": [],
  "matched_labels": [
    {{"label": "Lattice_Parameters", "property_type": "material_property"}},
    {{"label": "Discharge_Specific_Capacity_Initial", "property_type": "electrochemical_performance"}}
  ]
}}

## Paragraph
{content}
"""

# Phase 1b: Extract — 只提取已确认标签的值

PHASE12_EXTRACT = """You are a lithium battery expert. Extract values for the confirmed property labels from this paragraph.

## Known Materials (use these material_id values)
If the paragraph does not explicitly name a material but the data clearly belongs to one of the known materials (e.g., the novel electrolyte studied in this paper), link it to that material.
{known_materials}

## Known Conditions (reuse condition_id)
{known_conditions}

## Condition Schema (new_conditions)
Base fields: condition_id, scenario, electrolyte, test_method, source_text, material_id.
Scenario-specific extras (same rules as Include step):
- half_cell_test: current_density, voltage_range, deposition_capacity, precycles, counter_electrode, separator
- symmetric_cell_test: current_density, deposition_capacity, cycle_number, separator
- full_cell_test: c_rate, current_density, voltage_range, mass_loading, NP_ratio, EC_ratio, separator, cycle_number
- material_characterization: instrument, test_parameter, temperature, separator

## Confirmed Labels (extract values for these only)
{label_details}

## Output
Return ONLY a JSON object:
{{
  "new_conditions": [],
  "conditioned_properties": [
    {{
      "condition_id": "C001",
      "component": "cathode",
      "material_id": "NCM811",
      "property_type": "electrochemical_performance",
      "property_name": "Discharge_Specific_Capacity",
      "value": {{"value": 200, "unit": "mAh/g"}},
      "source_text": ""
    }}
  ],
  "intrinsic_properties": [
    {{
      "component": "cathode",
      "material_id": "NCM811",
      "property_type": "material_property",
      "property_name": "Lattice_Parameters",
      "value": {{"value": {{"a": 2.86}}, "unit": "AA"}},
      "source_text": ""
    }}
  ]
}}

Rules:
- Only extract values for labels listed in Confirmed Labels. Skip others.
- conditioned_properties: Must link to condition_id if the property depends on test conditions.
- intrinsic_properties: No condition_id for material's own properties.
- material_id MUST be from Known Materials list. If the text does not clearly state which material, leave material_id empty rather than guessing.

## Paragraph
{content}
"""

# Phase 3b: 孤儿属性匹配（LLM 仅做匹配，不做合并）

PHASE3_ORPHAN_MATCH = """You are a data matching agent for battery materials data.

## Task
For each "orphan" property (those with empty condition_id), find the best matching condition
from the known conditions list.

Matching rules:
1. Same material (material_id matches) -> highest priority
2. Same electrochemical scenario (half_cell/full_cell/symmetric) -> second priority
3. Numeric consistency (temperature within +/-5C, c_rate within +/-0.1C)
4. Text proximity (sentences mentioning the property are close to condition descriptions)

## Known Conditions
{known_conditions}

## Orphan Properties (need matching)
{orphan_properties}

## Output Format
Return ONLY a JSON array:
[
  {{
    "index": 0,
    "matched_condition_id": "C001",
    "confidence": "high",
    "reason": ""
  }}
]

For properties that cannot be matched to any condition, set matched_condition_id to null.
"""


# Phase 1c: 表格行提取 — 直接从 [TABLE ROW] 段落提取属性（跳过 include 标签匹配）

TABLE_EXTRACT = """You are a lithium battery expert. Extract property values from this TABLE ROW.

A table row is one line of a data table, formatted as:
[TABLE ROW: <material>] | Table: <caption>
<header1>: <value1> | <header2>: <value2> | ...

## Known Materials (reuse these material_id values when the row material is the SAME material)
{known_materials}

## Available Labels (property_name MUST be exactly one of these)
{labels}

## Task
1. Identify the material of this row: map the row material to a Known Material if it is the same material (different name / abbreviation / formula is OK). Otherwise define a new material_id and set "new_material": true.
2. For each data column: map the column header to the best matching standard label from Available Labels, and extract the numeric value and unit. Map headers like "Rct", "R_ct", "Rs", "阻抗" to the closest standard label.
3. Skip columns that are not properties (sample name, notes, remarks).
4. If the caption or row suggests a test condition (temperature, C-rate, voltage window), include it in "condition" for conditioned properties; otherwise leave condition empty.

## Output Format
Return ONLY a JSON object:
{{
  "new_materials": [
    {{"material_id": "NewMat_1", "name": "...", "formula": "", "new_material": true}}
  ],
  "properties": [
    {{
      "material_id": "...",
      "property_name": "...",
      "property_type": "material_property",
      "value": {{"value": ..., "unit": "..."}},
      "condition": {{"temperature": {{"value": null, "unit": "C"}}, "c_rate": null}},
      "source_type": "table",
      "raw_material": "...",
      "raw_header": "...",
      "source_text": "<copy the exact input table row text>"
    }}
  ]
}}
Rules:
- property_name MUST be exactly one of the Available Labels. Do NOT invent new names.
- material_id: reuse an existing Known Material id if the row material is the same material; otherwise define a new one and list it in new_materials with "new_material": true.
- property_type: "material_property" (intrinsic) or "electrochemical_performance" (condition-dependent, e.g. capacity, impedance, conductivity).
- source_type / raw_material / raw_header / source_text are REQUIRED for every property. raw_material is the material name as written in the row; raw_header is the original column header before mapping; source_text is the exact input table row text.
- Header mapping precision: map each distinct header to the most specific matching label, and DO NOT map multiple different headers to the same label unless they are literally the same physical quantity. Hints: "Rct"/"R_ct"/"RCT" (charge transfer) -> Charge_Transfer_Resistance; "Rsei"/"R_SEI"/"R_CEI" (surface film) -> SEI_Resistance (electrode surface passivation film resistance; R_CEI is the strict name for a cathode film and R_SEI for an anode film, both map to SEI_Resistance); "Rs"/"R_s"/"R_ohm" (ohmic/solution resistance) -> skip the column: it is the cell/system resistance, not a material property. Always keep the original header verbatim in raw_header.
"""
