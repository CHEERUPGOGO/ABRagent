# -*- coding: utf-8 -*-
"""测试条件提取 Agent v2 — 修复：强化 prompt 要求完整提取电解液/温度等字段"""
import json, re, logging
from typing import Any, Dict, List, Optional
from langchain_core.language_models.base import BaseLanguageModel
from langchain_classic.chains.base import Chain
from langchain_classic.chains.llm import LLMChain
from langchain_core.prompts import PromptTemplate
from langchain_core.callbacks.manager import CallbackManagerForChainRun
from miner.extraction_core.errors import StructuredFormatError, LangchainError
from miner.extraction_core.pricing import TokenChecker
from miner.extraction_core.utils import fix_json_escape

log = logging.getLogger("ConditionAgentV2")

# [修复] 强化 extract prompt — 明确要求提取电解液完整配方、温度、倍率等
PROMPT_CONDITION_INCLUDE_V2 = """You are an electrochemical testing condition analyst. Identify whether the paragraph describes test conditions for the target material.

Context: battery={battery_system_context}, material={material_id}

Parameter clues -- include if the text mentions:
- "temperature": C, K, degrees, room temp, 25 C, elevated temperature
- "c_rate": C-rate, e.g. 0.1C, 1C, 2C, 0.5C
- "current_density": current density, e.g. mA/g, mA/cm2, A/g, A/cm2
- "voltage_range": voltage window (2.5-4.3 V), cutoff voltage, charged to X V, vs. Li/Li+
- "electrolyte": salt name (LiPF6, LiTFSI), solvent (EC, DEC, DMC, FEC), concentration (1M, 0.5M)
- "cycle_number": after X cycles, nth cycle, cycled for X times, cycle life
- "electrode_config": half-cell, full-cell, symmetric cell, Li||NCM, configuration type
- "reference_electrode": vs. Li/Li+, vs. Na/Na+, reference, counter electrode
- "test_method": GITT, EIS, CV, galvanostatic, cycling, rate test, potentiostatic
- "soc_state": SOC%, state of charge, depth of discharge, DOD
- "mass_loading": mg/cm2, loading, areal loading, mass of active material
- "aging_condition": storage, calendar aging, rest, shelf life, stored at

Return ONLY a JSON array of matching parameter names. If none, return [].

Paragraph: The cell was cycled at 1C between 2.8-4.3 V at 25 C.
List: ["temperature","c_rate","voltage_range"]

Paragraph: T=25 C, 0.5C, 2.5-4.2V, 1M LiPF6.
List: ["temperature","c_rate","voltage_range","electrolyte"]

Paragraph: {paragraph}
List:"""

PROMPT_CONDITION_EXTRACT_V2 = """Extract detailed test conditions from the paragraph.

IMPORTANT: You MUST extract ALL available fields mentioned in the paragraph, especially:
- electrolyte: Include the full formula (salt + solvent + additives, with concentrations)
- temperature: Always extract if mentioned
- c_rate: Always extract if mentioned (e.g. 0.1C, 1C, 2C)
- current_density: Always extract if mentioned (e.g. mA/g, mA/cm2)
- voltage_range: Always extract if mentioned

Context:
- Battery system: {battery_system_context}
- Material ID: {material_id}
- Known conditions (for reuse): {known_conditions_summary}

Output schema:
{{
    "condition_id": "",  # Reuse from known_conditions if identical, or generate "Cxxx"
    "material_id": "",   # The target material this condition applies to
    "battery_system_context": "",
    "battery_configuration": "",  # "half-cell", "full-cell", "symmetric", or "unknown"
    "temperature": {{"value": 0.0, "unit": "C"}},
    "c_rate": {{"value": "", "unit": "C"}},
    "current_density": {{"value": 0.0, "unit": "mA/cm2"}},
    "voltage_range": {{"min": 0.0, "max": 0.0, "unit": "V", "reference": ""}},
    "electrolyte": "",  # Full description: e.g. "1 M LiPF6 in EC/DEC (1:1 v/v) with 2% FEC"
    "cycle_number": 0,
    "mass_loading": {{"value": 0.0, "unit": "mg/cm2"}},
    "electrode_config": "",
    "reference_electrode": "",
    "test_method": "",
    "soc_state": "",
    "aging_condition": "",
    "source_text": ""
}}

Rules:
- For each DISTINCT set of test conditions, create a separate JSON object.
- Classify battery_configuration as:
    - "half-cell": electrode vs. Li/Na metal (e.g., "NCM811 || Li metal", "half-cell vs. Li/Li+")
    - "full-cell": two different electrode materials paired (e.g., "NCM811 || graphite")
    - "symmetric": same material on both sides (e.g., "Li || Li symmetric cell")
    - "unknown": cannot determine from the text
- If a condition matches one in "known_conditions", reuse that condition_id.
- Generate new condition_id as "C001", "C002"... for unique conditions.
- Omit fields not mentioned in the paragraph entirely.
- "source_text" must be EXACT text from paragraph.
- Do NOT leave known fields empty — if a mentioned field has a value, include it.

If uncertain, reply with "I do not know".

Paragraph: {paragraph}
JSON:"""


class ConditionAgentV2(Chain):
    include_chain: LLMChain
    extract_chain: LLMChain
    input_key: str = "content"
    output_key: str = "output"
    token_checker: Optional[TokenChecker] = None

    @property
    def input_keys(self) -> List[str]: return [self.input_key]
    @property
    def output_keys(self) -> List[str]: return [self.output_key]

    def _write_log(self, text: str, rm):
        rm.on_text(f"\n[ConditionV2] ", verbose=self.verbose)
        rm.on_text(text, verbose=self.verbose, color="blue")

    @classmethod
    def reset_counter(cls):
        import sys
        mod = sys.modules[__name__]
        mod._cond_counter = 0

    @classmethod
    def _next_id(cls) -> str:
        import sys
        mod = sys.modules[__name__]
        mod._cond_counter += 1
        return f"C{mod._cond_counter:03d}"

    def _parse_include(self, output: str) -> List[str]:
        output = output.replace("```JSON", "").replace("```json", "").replace("```", "").strip()
        if re.search(r"[Ii] do not know", output): return []
        try:
            import ast
            if output.startswith("[") and output.endswith("]"):
                return [r for r in ast.literal_eval(output) if isinstance(r, str)]
            m = re.search(r"\[.*\]", output, re.DOTALL)
            if m: return [r for r in ast.literal_eval(m.group(0)) if isinstance(r, str)]
        except: pass
        return []

    def _parse_extract(self, output: str) -> Any:
        output = output.replace("```JSON", "").replace("```json", "").replace("```", "").strip()
        if re.search(r"[Ii] do not know", output): return {}
        output = fix_json_escape(output)
        try: return json.loads(output)
        except json.JSONDecodeError: pass
        for pat in [r'^[Oo]kay[,\.]?.*?\{', r'^[Hh]ere[\'s]?.*?\{']:
            if re.search(pat, output, re.DOTALL):
                m = re.search(r"(\{.*\})", output, re.DOTALL)
                if m:
                    try: return json.loads(m.group(1))
                    except: pass
                break
        try:
            m = re.search(r"(\{(?:[^{}]|(?R))*\})", output, re.DOTALL)
            if m: return json.loads(m.group(1))
        except: pass
        raise StructuredFormatError(raw_output=output, message="无法解析条件 JSON")

    def _llm_text(self, chain, prompt):
        r = chain.llm.invoke(prompt)
        return r.content if hasattr(r, "content") else str(r)

    def _call(self, inputs: Dict[str, Any],
              run_manager: Optional[CallbackManagerForChainRun] = None) -> Dict[str, Any]:
        _rm = run_manager or CallbackManagerForChainRun.get_noop_manager()
        cb = _rm.get_child()
        content = str(inputs.get(self.input_key, ""))
        material_id = inputs.get("material_id", "")
        battery_system_context = inputs.get("battery_system_context", "")
        doi = inputs.get("doi", "")
        known_conditions = inputs.get("known_conditions", [])

        base = {"content": content, "material_id": material_id,
                "battery_system_context": battery_system_context,
                "condition_params": [], "extracted_conditions": [], "doi": doi}

        try:
            inc_prompt = PROMPT_CONDITION_INCLUDE_V2.format(
                battery_system_context=battery_system_context, material_id=material_id, paragraph=content)
            inc_out = self.include_chain.llm.invoke(inc_prompt)
            inc_out_text = inc_out.content if hasattr(inc_out, 'content') else str(inc_out)
        except Exception as e:
            raise LangchainError(chain_name="ConditionInclude", original_error=e)
        if self.token_checker:
            self.token_checker.record("condition-include-v2", inc_prompt, inc_out_text, "include")

        cond_params = self._parse_include(inc_out_text)
        self._write_log(f"条件参数: {cond_params}", _rm)
        if not cond_params:
            return {"output": base}

        known_summary = json.dumps([{k: c.get(k) for k in ["condition_id", "temperature", "c_rate", "current_density", "voltage_range", "electrolyte"] if k in c} for c in known_conditions[-5:]], ensure_ascii=False)
        try:
            ext_prompt = PROMPT_CONDITION_EXTRACT_V2.format(
                battery_system_context=battery_system_context, material_id=material_id,
                known_conditions_summary=known_summary, paragraph=content)
            ext_out = self.extract_chain.llm.invoke(ext_prompt)
            ext_out_text = ext_out.content if hasattr(ext_out, 'content') else str(ext_out)
        except Exception as e:
            raise LangchainError(chain_name="ConditionExtract", original_error=e)
        if self.token_checker:
            self.token_checker.record("condition-extract-v2", ext_prompt, ext_out_text, "extract")

        try:
            extracted = self._parse_extract(ext_out_text)
        except StructuredFormatError:
            log.warning("条件 JSON 解析失败")
            extracted = {}

        if isinstance(extracted, dict) and extracted:
            conds = [extracted]
        elif isinstance(extracted, list):
            conds = extracted
        else:
            conds = []

        final = []
        for c in conds:
            if not isinstance(c, dict): continue
            matched = False
            for kc in known_conditions:
                if self._match(c, kc):
                    c["condition_id"] = kc.get("condition_id", "")
                    matched = True
                    break
            if not matched:
                c["condition_id"] = self._next_id()
            if not c.get("material_id") and material_id:
                c["material_id"] = material_id
            final.append(c)

        self._write_log(f"提取 {len(final)} 个条件: {[c.get('condition_id') for c in final]}", _rm)
        return {"output": {**base, "condition_params": cond_params, "extracted_conditions": final}}

    @staticmethod
    def _match(c1: Dict, c2: Dict) -> bool:
        keys = ["temperature", "c_rate", "current_density", "voltage_range", "electrolyte", "electrode_config"]
        for k in keys:
            v1, v2 = str(c1.get(k, "")), str(c2.get(k, ""))
            if v1 and v2 and v1 != v2:
                return False
        return any(str(c1.get(k, "")) and str(c2.get(k, "")) and str(c1[k]) == str(c2[k]) for k in keys)

    @classmethod
    def from_llm(cls, include_llm: BaseLanguageModel, extract_llm: BaseLanguageModel,
                 token_checker: TokenChecker = None, **kwargs) -> "ConditionAgentV2":
        inc = LLMChain(llm=include_llm, prompt=PromptTemplate(
            template=PROMPT_CONDITION_INCLUDE_V2, input_variables=[
                "battery_system_context", "material_id", "paragraph"]))
        ext = LLMChain(llm=extract_llm, prompt=PromptTemplate(
            template=PROMPT_CONDITION_EXTRACT_V2, input_variables=[
                "battery_system_context", "material_id", "known_conditions_summary", "paragraph"]))
        instance = cls(include_chain=inc, extract_chain=ext, **kwargs)
        instance.token_checker = token_checker
        return instance


# Module-level condition counter (avoids pydantic ModelPrivateAttr issue)
_cond_counter: int = 0
