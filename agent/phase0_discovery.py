"""Phase 0: 全篇材料发现 + 初始条件提取（合并，一次 LLM 调用）"""
import json, re, logging
from typing import List, Dict, Tuple
L = logging.getLogger("Phase0")
PROMPT = """You are a lithium battery expert. Analyze the full paper text and extract:
1) ALL battery materials studied
2) ALL test/synthesis conditions mentioned

## Material Extraction Rules for {component_type}
- For electrolyte: Identify the electrolyte formulations used in battery assembly (e.g., "E-PFPN", "1M LiPF6 in EC/DEC"). Do NOT list individual solvents, salts, or additives as separate materials. One formula = one material.
- For cathode/anode: Identify the electrode active materials (e.g., "NCM811", "graphite", "Li metal"). Do NOT list current collectors, separator, or packaging.
- Focus on materials that are studied or compared in the paper, not every chemical mentioned.

## Base / Modification Fields (for electrode materials)
Each material may carry optional structured fields:
- "base_material": the underlying pristine material (e.g., "NCM811", "graphite", "Si"). Omit if the material is unmodified.
- "mods": dict of modifications, keys among:
  "coating" (包覆, e.g. ["Al2O3"]), "dopants" (掺杂, e.g. ["Nd","Fe"]),
  "composite" (复合, e.g. ["graphene"]), "morphology" (形貌, e.g. ["nanoparticle","single-crystal"]),
  "treatment" (预处理, e.g. ["prelithiated"]).
  Omit if unmodified.

## Output Format
Return ONLY a JSON object:
{{
  "materials": [
    {{"name":"NCM811","formula":"LiNi0.8Co0.1Mn0.1O2","role":"novel","short_name":"NCM811"}},
    {{"name":"Al2O3-coated NCM811","formula":"LiNi0.8Co0.1Mn0.1O2","role":"novel","short_name":"NCM811-Al2O3","base_material":"NCM811","mods":{{"coating":["Al2O3"]}}}},
    {{"name":"Nd-doped NCM955","formula":"LiNi0.9Co0.05Mn0.05O2","role":"novel","short_name":"NCM955-Nd","base_material":"NCM955","mods":{{"dopants":["Nd"]}}}},
    {{"name":"E-PFPN (1M LiTFSI in DME + 20% HFE + 5% PFPN)","formula":{{"salt":{{"name":"LiTFSI","concentration":"1M"}},"solvents":[{{"name":"DME","role":"main"}}],"diluents":[{{"name":"HFE","ratio":"20%"}}],"additives":[{{"name":"PFPN","ratio":"5%"}}]}},"role":"novel","short_name":"E-PFPN"}}
  ],
  "conditions": [
    {{
      "condition_id": "C001",
      "scenario": "half_cell_test",
      "condition": {{
        "current_density": {{"value": 0.5, "unit": "mA/cm2"}},
        "deposition_capacity": {{"value": 1.0, "unit": "mAh/cm2"}},
        "voltage_range": {{"min": 0, "max": 1, "unit": "V"}},
        "electrolyte": "1 M LiTFSI in DOL/DME",
        "separator": "Celgard 2325",
        "test_method": "galvanostatic Li plating/stripping",
        "counter_electrode": "Li"
      }},
      "material_id": "",
      "source_text": ""
    }}
  ]
}}

Scenario options: synthesis / half_cell_test / full_cell_test / symmetric_cell_test / material_characterization / theoretical_calculation

🔹 Condition fields depend on scenario — use these as guide:
- synthesis: temperature, atmosphere, duration, test_method, source_text
- half_cell_test: current_density, deposition_capacity, voltage_range, electrolyte, separator, counter_electrode, precycles, test_method, source_text
- symmetric_cell_test: current_density, deposition_capacity, cycle_number, separator, test_method, source_text
- full_cell_test: c_rate, current_density, voltage_range, electrolyte, mass_loading, NP_ratio, EC_ratio, separator, cycle_number, test_method, source_text
- material_characterization: instrument, test_parameter, temperature, test_method, source_text
- theoretical_calculation: method, software, test_method, source_text

Paper text:
{text}"""

PROMPT_FULL = """You are a lithium battery expert. Analyze the full paper text and extract EVERYTHING:
1) ALL battery materials studied
2) ALL test/synthesis conditions
3) ALL quantitative property values (material properties, electrochemical performance, etc.)

## Output Format
Return ONLY a JSON object:

{{
  "materials": [{{"name":"NCM811","formula":"LiNi0.8Co0.1Mn0.1O2","role":"novel","short_name":"NCM811"}}],
  "conditions": [
    {{
      "condition_id": "C001",
      "scenario": "synthesis",
      "condition": {{
        "temperature": {{"value": 800, "unit": "C"}},
        "c_rate": {{"value": null, "unit": "C"}},
        "current_density": {{"value": null, "unit": "mA/g"}},
        "voltage_range": {{"min": null, "max": null, "unit": "V"}},
        "electrolyte": "",
        "electrode_config": "",
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
- conditioned_properties: only link condition_id if the text explicitly states the property was measured under that condition
- intrinsic_properties: properties not depending on test setup (crystal structure, band gap, particle size, etc.)
- CRITICAL: property_name MUST be exactly one of the labels in "Standard Labels" below. Do NOT invent new names.

## Standard Labels
{labels}

## Paper text
{text}"""

def _get_explanation(component: str) -> str:
    """获取轻量 explanation（仅标签名+简短说明），用于 Include 阶段"""
    try:
        if component == "cathode":
            from miner.cathode_database.cathode_formatter import CathodeFormatter as F
        elif component == "anode":
            from miner.anode_database.anode_formatter import AnodeFormatter as F
        elif component == "electrolyte":
            from miner.electrolyte_database.electrolyte_formatter import ElectrolyteFormatter as F
        else:
            return "(no labels)"
        inst = F()
        lines = []
        for k in inst.material_keys():
            e = getattr(inst, "material_explanation", {}).get(k, "")
            lines.append(f"  [material_property] {k}: {str(e)[:80]}")
        for k in inst.performance_keys():
            e = getattr(inst, "perf_explanation", {}).get(k, "")
            lines.append(f"  [electrochemical_performance] {k}: {str(e)[:80]}")
        try:
            for k in inst.condition_keys():
                lines.append(f"  [synthesis_parameter] {k}")
        except AttributeError:
            pass
        return "\n".join(lines)
    except Exception:
        return "(no labels)"

def _get_label_details(component: str, matched_labels: list = None) -> str:
    """获取命中标签的完整定义（schema+info+example），用于 Extract 阶段
       如果 matched_labels 为 None，返回全部标签（全量模式）
    """
    try:
        if component == "cathode":
            from miner.cathode_database.cathode_formatter import CathodeFormatter as F
        elif component == "anode":
            from miner.anode_database.anode_formatter import AnodeFormatter as F
        elif component == "electrolyte":
            from miner.electrolyte_database.electrolyte_formatter import ElectrolyteFormatter as F
        else:
            return "(no labels)"
        inst = F()
        def _collect(keys_func, typ, expl_attr, sd_attr, info_attr, ex_attr):
            lines = []
            for k in keys_func():
                if matched_labels is not None and k not in matched_labels:
                    continue
                e = getattr(inst, expl_attr, {}).get(k, "")
                sd = getattr(inst, sd_attr, {}).get(k, "")
                info = getattr(inst, info_attr, {}).get(k, "")
                ex = getattr(inst, ex_attr, {}).get(k, "")
                lines.append(f"[{typ}] {k}")
                if e: lines.append(f"  desc: {str(e)[:120]}")
                if sd: lines.append(f"  schema: {str(sd)[:250]}")
                if info: lines.append(f"  info: {str(info)[:150]}")
                if ex: lines.append(f"  example: {str(ex)[:180]}")
            return lines
        lines = []
        lines.extend(_collect(inst.material_keys, "material_property", "material_explanation", "material_structured_data", "material_information", "material_example_text"))
        lines.extend(_collect(inst.performance_keys, "electrochemical_performance", "perf_explanation", "perf_structured_data", "perf_information", "perf_example_text"))
        try:
            for k in inst.condition_keys():
                if matched_labels is None or k in matched_labels:
                    lines.append(f"[synthesis_parameter] {k}")
        except (AttributeError, StopIteration):
            pass
        return "\n".join(lines)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"(no labels: {e})"

def _get_labels(component: str) -> str:
    """获取当前组件的完整 format1 标签（含 explanation + schema + example + info）"""
    try:
        if component == "cathode":
            from miner.cathode_database.cathode_formatter import CathodeFormatter as F
        elif component == "anode":
            from miner.anode_database.anode_formatter import AnodeFormatter as F
        elif component == "electrolyte":
            from miner.electrolyte_database.electrolyte_formatter import ElectrolyteFormatter as F
        else:
            return "(no labels)"
        inst = F()
        lines = []
        def _add(typ, k, expl_attr, sd_attr, info_attr, ex_attr):
            e = getattr(inst, expl_attr, {}).get(k, "")
            sd = getattr(inst, sd_attr, {}).get(k, "")
            info = getattr(inst, info_attr, {}).get(k, "")
            ex = getattr(inst, ex_attr, {}).get(k, "")
            lines.append(f"  [{typ}] {k}")
            if e: lines.append(f"    desc: {str(e)[:150]}")
            if sd: lines.append(f"    schema: {str(sd)[:300]}")
            if info: lines.append(f"    info: {str(info)[:150]}")
            if ex: lines.append(f"    example: {str(ex)[:200]}")
        for k in inst.material_keys():
            _add("material_property", k, "material_explanation", "material_structured_data", "material_information", "material_example_text")
        for k in inst.performance_keys():
            _add("electrochemical_performance", k, "perf_explanation", "perf_structured_data", "perf_information", "perf_example_text")
        try:
            for k in inst.condition_keys():
                lines.append(f"  [synthesis_parameter] {k}")
        except AttributeError:
            pass
        return "\n".join(lines)
    except Exception:
        return "(no labels)"

def discover_all(llm, cleaned_text: str, component: str = "cathode", file_stem: str = "") -> Dict:
    """全篇一次调用: 材料+条件+属性（带标准标签）"""
    truncated = cleaned_text[:35000] if len(cleaned_text) > 35000 else cleaned_text
    labels = _get_labels(component)
    prompt = PROMPT_FULL.format(labels=labels, text=truncated)
    try:
        resp = llm.invoke(prompt)
        raw = resp.content if hasattr(resp, "content") else str(resp)
        raw = raw.replace("```json","").replace("```JSON","").replace("```","").strip()
        m = re.search(r"(\{.*\})", raw, re.DOTALL)
        if m:
            data = json.loads(m.group(1))
            mats = data.get("materials", [])
            for mat in mats:
                mat.setdefault("short_name", mat.get("name",""))
                mat.setdefault("material_id", mat.get("short_name","") or mat.get("name",""))
            L.info(f"  全篇: {len(mats)}材料, {len(data.get('conditions',[]))}条件, {len(data.get('conditioned_properties',[]))}条件属性, {len(data.get('intrinsic_properties',[]))}本征属性")
            return data
    except Exception as e:
        L.warning(f"全篇提取失败: {e}")
    return {"materials":[],"conditions":[],"conditioned_properties":[],"intrinsic_properties":[]}

def discover(llm, cleaned_text: str, component: str = "cathode", file_stem: str = "") -> Tuple[List[Dict], List[Dict]]:
    """全篇一次调用，返回 (materials, initial_conditions)"""
    truncated = cleaned_text[:50000] if len(cleaned_text) > 50000 else cleaned_text
    prompt = PROMPT.format(component_type=component, text=truncated)
    try:
        resp = llm.invoke(prompt)
        raw = resp.content if hasattr(resp, "content") else str(resp)
        raw = raw.replace("```json","").replace("```","").strip()
        m = re.search(r"(\{.*\})", raw, re.DOTALL)
        if m:
            data = json.loads(m.group(1))
            mats = data.get("materials", [])
            conds = data.get("conditions", [])
            for mat in mats:
                mat.setdefault("short_name", mat.get("name",""))
                mat.setdefault("material_id", mat.get("short_name","") or mat.get("name",""))
            L.info(f"  发现 {len(mats)} 种材料, {len(conds)} 个初始条件")
            return mats, conds
    except Exception as e:
        L.warning(f"Phase0 识别失败: {e}")
    return [{"name":f"{component} material","short_name":file_stem,"formula":"","role":"unknown","material_id":file_stem}], []
