# -*- coding: utf-8 -*-
"""UnifiedExtractionAgent — 一次 LLM 调用抽取 Condition / Material / Performance

基于 v4 版本的 prompt 模式，合并 condition/material/performance 三类抽取
为一次 LLM 调用。保留了 v4 的：
  - 组件角色设定（cathode/anode/electrolyte 专家）
  - 少样本示例（one-shot example）
  - structured_data schema 引用
  - source_text verbatim 要求
  - 条件提取的详细嵌套 schema

输出经 postprocess.py 后处理为规范结构。
"""

import json, re, logging
from typing import Any, Dict, List, Optional, Type

from langchain_classic.chains.base import Chain
from langchain_core.callbacks.manager import CallbackManagerForChainRun

from miner.extraction_core.postprocess import (
    normalize_conditions,
    normalize_embedded_conditions,
    remove_nulls,
    normalize_label_buckets,
)
from miner.extraction_core.errors import LangchainError
from miner.extraction_core.utils import fix_json_escape

logger = logging.getLogger("UnifiedExtractionAgent")

# 模块级调用计数器（避免 pydantic 类属性包装问题）
_unified_total_calls: int = 0


def _build_focus_instruction(focus_tasks: str) -> str:
    """将 focus_tasks 转换为 prompt 中的聚焦指令。

    当 focus_tasks 为 "all" 时返回空字符串，不改变现有 LLM 行为。
    否则生成聚焦指令，引导 LLM 优先提取对应类别。
    """
    focus_map = {
        "condition": (
            "\n## Focus\nThis paragraph contains TEST CONDITIONS (electrolyte, voltage, "
            "C-rate, temperature, cell config, etc.). Prioritize extracting condition_params "
            "and conditions."
        ),
        "material": (
            "\n## Focus\nThis paragraph contains MATERIAL PROPERTIES (synthesis, structure, "
            "morphology, composition, etc.). Prioritize extracting property_types and "
            "extracted_info."
        ),
        "performance": (
            "\n## Focus\nThis paragraph contains ELECTROCHEMICAL PERFORMANCE (capacity, "
            "retention, rate, impedance, conductivity, etc.). Prioritize extracting "
            "performance_types and performance_info. Link to condition_id when available."
        ),
    }

    focus_lower = focus_tasks.strip().lower()
    if not focus_tasks or focus_lower == "all":
        return "\n".join(focus_map.values())

    instructions = []
    for key, instruction in focus_map.items():
        if key in focus_lower:
            instructions.append(instruction)
    return "\n".join(instructions) if instructions else ""


# ── Prompt 模板（基于 v4 的 include + extract 模式合并） ──

UNIFIED_EXTRACT_PROMPT_TEMPLATE = """You are a lithium battery {component} expert. Extract ALL of the following from the paragraph in a single JSON:
1) Test/synthesis conditions (condition_params + conditions)
2) Material intrinsic properties (property_types + extracted_info)
3) Electrochemical performance data (performance_types + performance_info)

## Context
- Battery system: {battery_system_context}
- Material ID: {material_id}
- DOI: {doi}
- Known conditions (reuse condition_id if identical): {known_conditions}
{focus_instruction}
## 1) Condition Parameters
If the paragraph describes test conditions, extract using this schema.
Parameter clues -- include if text mentions:
- temperature: C, K, degrees, 25 C, elevated temperature
- c_rate: C-rate, e.g. 0.1C, 1C, 0.5C
- current_density: mA/g, mA/cm2, A/g
- voltage_range: voltage window, cutoff, vs. Li/Li+, vs. Na/Na+
- electrolyte: salt (LiPF6, LiTFSI), solvent (EC, DEC, DMC, FEC), concentration (1M)
- cycle_number: after X cycles, nth cycle, cycled for X times
- mass_loading: mg/cm2, areal loading, active material loading
- electrode_config: half-cell, full-cell, symmetric cell
- reference_electrode: vs. Li/Li+, vs. Na/Na+
- test_method: GITT, EIS, CV, galvanostatic, cycling, rate test

Available condition labels for {component}:
{condition_fields}

## 2) Material Property Labels (intrinsic properties, NOT electrochemical performance)
Identify which of these properties have quantitative values in the paragraph.
For each identified property, provide the value following the defined format.

Available material property labels for {component}:
{material_fields}

{material_structured_data}

## 3) Electrochemical Performance Labels
Identify which performance metrics have quantitative values in the paragraph.
For each identified metric, provide the value following the defined format.
Include condition_id in the output to link the performance to its test condition.

Available performance labels for {component}:
{performance_fields}

{performance_structured_data}

## Table Data Mapping
If the paragraph contains TABLE DATA BLOCK, map table headers to the standard labels above.
Link numerical values in the table to the corresponding material or performance labels.

## Output JSON Format
Return ONLY valid JSON matching this schema:

{{
  "condition_params": [],        // List of detected condition parameter names
  "conditions": [                 // Condition objects — one per distinct test condition
    {{
      "condition_id": "C001",
      "temperature": {{"value": 25, "unit": "C"}},
      "c_rate": {{"value": "", "unit": "C"}},
      "current_density": {{"value": 0.0, "unit": "mA/cm2"}},
      "voltage_range": {{"min": 0.0, "max": 0.0, "unit": "V", "reference": ""}},
      "electrolyte": "",
      "cycle_number": 0,
      "mass_loading": {{"value": 0.0, "unit": "mg/cm2"}},
      "electrode_config": "",
      "reference_electrode": "",
      "test_method": "",
      "source_text": "..."
    }}
  ],
  "property_types": [],          // Material property label names found
  "extracted_info": {{}},         // Map: label_name -> value(s) following structured_data schema
  "performance_types": [],        // Performance label names found
  "performance_info": {{}},        // Map: label_name -> {{"value": ..., "unit": ..., "condition_id": "..."}}
  "condition_id": ""              // condition_id linking to the primary condition used
}}

Rules:
- For each DISTINCT set of test conditions, create a separate condition object. Reuse condition_id from known_conditions if identical.
- source_text must be verbatim from paragraph.
- Omit fields not mentioned in the paragraph entirely.
- If uncertain, return empty arrays/objects — "I do not know" is not valid JSON.

## Paragraph
{content}
"""


class UnifiedExtractionAgent(Chain):
    """统一抽取 Agent — 一次 LLM 抽取 condition / material / performance"""

    llm: Any = None
    formatter_class: Type = None
    component: str = ""
    token_checker: Any = None
    input_key: str = "content"
    output_key: str = "output"

    @property
    def input_keys(self):
        return [self.input_key, "material_id", "battery_system_context",
                "doi", "known_conditions", "focus_tasks"]

    @property
    def output_keys(self):
        return [self.output_key]

    @classmethod
    def reset_counter(cls):
        global _unified_total_calls
        _unified_total_calls = 0

    @classmethod
    def call_count(cls) -> int:
        return _unified_total_calls

    def _build_prompt_kwargs(self, inputs: dict) -> dict:
        """构造 prompt 填充字段 — 基于 v4 formatter 的数据"""
        formatter = self.formatter_class()

        # ── condition fields ──
        condition_keys = []
        if hasattr(formatter, "condition_keys"):
            for k in formatter.condition_keys():
                try:
                    expl = getattr(formatter, "explanation", {}).get(k, "")
                    condition_keys.append(f"- {k}: {expl[:200]}")
                except Exception:
                    condition_keys.append(f"- {k}")

        # ── material fields + structured_data ──
        material_labels = []
        material_struct_lines = []
        for k in formatter.material_keys():
            try:
                expl = formatter.material_explanation.get(k, "")
                material_labels.append(f"- {k}: {expl[:200]}")
            except Exception:
                material_labels.append(f"- {k}")
            try:
                sd = formatter.material_structured_data.get(k, "")
                if sd:
                    material_struct_lines.append(f"  {k}: {sd[:300]}")
            except Exception:
                pass

        material_struct_block = (
            "Material property format (structured_data):\n" + "\n".join(material_struct_lines)
            if material_struct_lines else ""
        )

        # ── performance fields + structured_data ──
        perf_labels = []
        perf_struct_lines = []
        for k in formatter.performance_keys():
            try:
                expl = formatter.perf_explanation.get(k, "")
                perf_labels.append(f"- {k}: {expl[:200]}")
            except Exception:
                perf_labels.append(f"- {k}")
            try:
                sd = formatter.perf_structured_data.get(k, "")
                if sd:
                    perf_struct_lines.append(f"  {k}: {sd[:300]}")
            except Exception:
                pass

        perf_struct_block = (
            "Performance format (structured_data):\n" + "\n".join(perf_struct_lines)
            if perf_struct_lines else ""
        )

        return {
            "component": self.component,
            "material_id": inputs.get("material_id", ""),
            "battery_system_context": inputs.get("battery_system_context", ""),
            "doi": inputs.get("doi", ""),
            "known_conditions": json.dumps(inputs.get("known_conditions", []), ensure_ascii=False),
            "focus_tasks": inputs.get("focus_tasks", "all"),
            "focus_instruction": _build_focus_instruction(inputs.get("focus_tasks", "all")),
            "condition_fields": "\n".join(condition_keys) if condition_keys
                else "- temperature, c_rate, current_density, voltage_range, electrolyte, cycle_number, mass_loading, electrode_config, test_method",
            "material_fields": "\n".join(material_labels) if material_labels
                else "（no predefined material property labels for this component）",
            "material_structured_data": material_struct_block,
            "performance_fields": "\n".join(perf_labels) if perf_labels
                else "（no predefined performance labels for this component）",
            "performance_structured_data": perf_struct_block,
            "content": inputs.get("content", ""),
        }

    def _parse_output(self, raw: str) -> dict:
        """解析 LLM 输出 JSON"""
        raw = raw.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
        if re.search(r"[Ii] do not know", raw):
            return {"condition_params": [], "conditions": [], "property_types": [],
                    "extracted_info": {}, "performance_types": [], "performance_info": {}}

        raw = fix_json_escape(raw)

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"(\{.*\})", raw, re.DOTALL)
            if m:
                try:
                    result = json.loads(m.group(1))
                except json.JSONDecodeError:
                    logger.warning(f"JSON parse failed, raw[:200]: {raw[:200]}")
                    return {"condition_params": [], "conditions": [], "property_types": [],
                            "extracted_info": {}, "performance_types": [], "performance_info": {}}
            else:
                logger.warning(f"No JSON block found, raw[:200]: {raw[:200]}")
                return {"condition_params": [], "conditions": [], "property_types": [],
                        "extracted_info": {}, "performance_types": [], "performance_info": {}}

        result.setdefault("condition_params", [])
        result.setdefault("conditions", [])
        result.setdefault("property_types", [])
        result.setdefault("extracted_info", {})
        result.setdefault("performance_types", [])
        result.setdefault("performance_info", {})
        result.setdefault("condition_id", "")

        return result

    def _call(self, inputs: dict, run_manager: Optional[CallbackManagerForChainRun] = None) -> dict:
        global _unified_total_calls
        _unified_total_calls += 1
        _rm = run_manager or CallbackManagerForChainRun.get_noop_manager()

        prompt_kwargs = self._build_prompt_kwargs(inputs)
        prompt = UNIFIED_EXTRACT_PROMPT_TEMPLATE.format(**prompt_kwargs)

        try:
            response = self.llm.invoke(prompt)
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            raise LangchainError(chain_name="UnifiedExtractionAgent", original_error=e)

        if self.token_checker:
            self.token_checker.record(
                f"unified-{self.component}-extract", prompt, raw, "extract"
            )

        parsed = self._parse_output(raw)

        # 后处理
        parsed["conditions"] = normalize_conditions(parsed.get("conditions", []))

        formatter = self.formatter_class()
        try:
            pt, ei, perf_t, perf_i = normalize_label_buckets(
                formatter,
                parsed.get("property_types", []),
                parsed.get("extracted_info", {}),
                parsed.get("performance_types", []),
                parsed.get("performance_info", {}),
            )
            parsed["property_types"] = pt
            parsed["extracted_info"] = ei
            parsed["performance_types"] = perf_t
            parsed["performance_info"] = perf_i
        except Exception as e:
            logger.debug(f"Label bucket normalization skipped: {e}")

        parsed["performance_info"] = normalize_embedded_conditions(
            parsed.get("performance_info", {})
        )

        parsed["extracted_info"] = remove_nulls(parsed.get("extracted_info", {}))
        parsed["performance_info"] = remove_nulls(parsed.get("performance_info", {}))

        return {self.output_key: parsed}

    @classmethod
    def from_llm(
        cls,
        llm: Any,
        formatter_class: Type,
        component: str = "",
        token_checker: Any = None,
        **kwargs,
    ) -> "UnifiedExtractionAgent":
        """工厂方法：从 LLM 实例创建 Agent"""
        inst = cls(llm=llm, formatter_class=formatter_class, component=component, **kwargs)
        inst.token_checker = token_checker
        return inst
