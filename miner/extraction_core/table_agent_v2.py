# -*- coding: utf-8 -*-
"""表格数据提取 Agent v2 — 组件感知、标签过滤、JSON 解析增强

与 v1 的区别：
- 接受 Formatter 类，用组件特定标签集验证和过滤提取结果
- 增强 JSON 解析 fallback（同 PerformanceAgentV2 模式）
- Prompt 包含组件标签参考信息，引导 LLM 输出符合标签体系的数据
- 后置过滤：只保留属于 material_keys / performance_keys 的标签
"""

import json, re, logging
from typing import Any, Dict, List, Optional

from langchain_core.language_models.base import BaseLanguageModel
from langchain_classic.chains.base import Chain
from langchain_classic.chains.llm import LLMChain
from langchain_core.prompts import PromptTemplate
from langchain_core.callbacks.manager import CallbackManagerForChainRun

from miner.extraction_core.errors import StructuredFormatError, LangchainError
from miner.extraction_core.utils import fix_json_escape

log = logging.getLogger("TableAgentV2")

TABLE_IS_RELEVANT_PROMPT_V2 = PromptTemplate(
    input_variables=["content"],
    template="""先读表标题和表头，再判断表格是否包含可提取的电池数据。

表格：{content}

返回 JSON：{{"relevant": true/false, "reason": "简短理由"}}
relevant=true（满足任一）：
- 表头含容量/电压/效率/循环/倍率/能量密度/离子电导率
- 表头含组分/结构参数/比表面积/粒径等材料属性
- 表标题含"performance""electrochemical""comparison"
relevant=false：纯文本/参考文献/图片路径/推广信息。仅返回 JSON。"""
)


def _build_table_extract_prompt(component: str, formatter: Any) -> str:
    """根据组件和 Formatter 动态构建提取 prompt（带标签参考）。"""
    mat_labels = list(formatter.material_explanation.keys())
    perf_labels = list(formatter.perf_explanation.keys())
    mat_expl = "\n".join(f"  - {k}: {v[:60]}" for k, v in formatter.material_explanation.items())
    perf_expl = "\n".join(f"  - {k}: {v[:60]}" for k, v in formatter.perf_explanation.items())

    return f"""你是一个电池材料数据提取专家。请分析表格并提取数据。

表格内容：
{{content}}
上下文：DOI={{doi}} | 组件={component} | 材料={{material_id}}
当前目标材料：{{material_context}}

═══ 规则 A：对比汇总表（多篇文献对比表）═══
- 只提取本工作(this work)的行，排除其他文献数据
- 如果无法确定哪行是"this work" — 返回空数据，不提取任何内容
- is_comparison_table = true

═══ 规则 B：本工作专用表（仅描述自己的实验数据）═══
- 只提取与"当前目标材料: {{material_context}}"相关的行
- 如果表格包含多种材料（如 NMC、LTLC、VGCF、PTFE 等），只提取目标材料的行
- 对于明显不属于目标材料的行（如导电剂 VGCF、粘结剂 PTFE），全部排除
- 如果整表没有目标材料的数据，返回空
- is_comparison_table = false

═══ 组件 {component} 的可用标签（请尽量使用这些标签名）═══

材料属性标签（property_types / extracted_info）：
{mat_expl}

电化学性能标签（performance_types / performance_info）：
{perf_expl}

═══ 输出格式（严格 JSON）：═══
{{{{
  "is_comparison_table": true/false,
  "property_types": ["属性1"],
  "extracted_info": {{{{
    "属性1": [
      {{{{ "value": "数值1", "unit": "单位", "condition": "条件1" }}}},
      {{{{ "value": "数值2", "unit": "单位", "condition": "条件2" }}}}
    ]
  }}}},
  "performance_types": ["指标1"],
  "performance_info": {{{{
    "指标1": [
      {{{{ "value": "数值1", "unit": "单位", "condition": "条件1" }}}},
      {{{{ "value": "数值2", "unit": "单位", "condition": "条件2" }}}}
    ]
  }}}}
}}}}

- property/extracted: 材料属性（使用上方材料属性标签）
- performance: 电化学性能（使用上方电化学性能标签）
- 请尽量使用给出的标签名，如果找不到完全匹配的标签，可以使用近义词
- 有多个实验条件时，目标材料的数据全部提取为多条
- 非目标材料的行全部跳过
- 无法确定目标材料时返回空。仅返回 JSON。"""


class TableAgentV2(Chain):
    include_chain: LLMChain
    extract_chain: LLMChain
    input_key: str = "content"
    output_key: str = "output"
    component: str = ""
    formatter: Any = None

    @property
    def input_keys(self) -> List[str]:
        return [self.input_key]

    @property
    def output_keys(self) -> List[str]:
        return [self.output_key]

    def _parse_extract(self, output: str) -> Dict:
        """多级 JSON 解析 fallback，同 PerformanceAgentV2 模式。"""
        output = output.replace("```JSON", "").replace("```json", "").replace("```", "").strip()
        if re.search(r"[Ii] do not know", output, re.IGNORECASE):
            return {}

        output = fix_json_escape(output)
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            pass

        for pat in [r'^[Oo]kay[,\.]?.*?\{', r'^[Hh]ere[\'s]?.*?\{']:
            if re.search(pat, output, re.DOTALL):
                m = re.search(r"(\{.*\})", output, re.DOTALL)
                if m:
                    try:
                        return json.loads(m.group(1))
                    except json.JSONDecodeError:
                        pass
                break

        try:
            m = re.search(r"(\{(?:[^{}]|(?R))*\})", output, re.DOTALL)
            if m:
                return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

        try:
            m = re.search(r"(\[.*\])", output, re.DOTALL)
            if m:
                parsed = json.loads(m.group(1))
                if isinstance(parsed, list) and parsed:
                    return parsed[0] if isinstance(parsed[0], dict) else {}
        except json.JSONDecodeError:
            pass

        log.warning(f"JSON parse failed: {output[:200]}")
        raise StructuredFormatError(raw_output=output, message="表格 JSON 解析失败")

    def _validate_labels(self, parsed: Dict) -> Dict:
        """后置过滤：只保留属于组件标签体系的 property/performance 标签。"""
        if not self.formatter:
            return parsed

        mat_keys = set(self.formatter.material_keys())
        perf_keys = set(self.formatter.performance_keys())

        pts = parsed.get("property_types", [])
        if isinstance(pts, list):
            validated_pts = [p for p in pts if p in mat_keys]
            parsed["property_types"] = validated_pts
            ei = parsed.get("extracted_info", {})
            if isinstance(ei, dict):
                parsed["extracted_info"] = {k: v for k, v in ei.items() if k in mat_keys}

        pts2 = parsed.get("performance_types", [])
        if isinstance(pts2, list):
            validated_pts2 = [p for p in pts2 if p in perf_keys]
            parsed["performance_types"] = validated_pts2
            pi = parsed.get("performance_info", {})
            if isinstance(pi, dict):
                parsed["performance_info"] = {k: v for k, v in pi.items() if k in perf_keys}

        return parsed

    def _call(self, inputs: Dict[str, Any], run_manager=None) -> Dict[str, Any]:
        content = inputs.get(self.input_key, "")
        material_id = inputs.get("material_id", "")
        doi = inputs.get("doi", "")
        material_context = inputs.get("material_context", "")

        if not content or not content.strip():
            return {"output": {"error": "empty_table"}}

        inc_result = self.include_chain.invoke(
            {"content": content[:2000]},
            config={"callbacks": run_manager.get_child() if run_manager else None}
        )
        inc_text = inc_result.get("text", "").strip()
        try:
            inc_parsed = json.loads(inc_text)
            if not inc_parsed.get("relevant", False):
                return {"output": {"skipped": True, "reason": inc_parsed.get("reason", "not_relevant")}}
        except json.JSONDecodeError:
            if "false" in inc_text.lower() or "no" in inc_text.lower():
                return {"output": {"skipped": True, "reason": "not_relevant"}}

        ext_inputs = {
            "content": content[:4000],
            "material_id": material_id,
            "doi": doi,
            "material_context": material_context,
        }
        ext_result = self.extract_chain.invoke(
            ext_inputs,
            config={"callbacks": run_manager.get_child() if run_manager else None}
        )
        ext_text = ext_result.get("text", "").strip()

        try:
            parsed = self._parse_extract(ext_text)
        except StructuredFormatError:
            log.warning(f"JSON 解析失败: {ext_text[:200]}")
            return {"output": {"error": "json_parse_failed", "raw": ext_text[:500]}}

        parsed = self._validate_labels(parsed)

        is_comp = parsed.get("is_comparison_table", False)
        has_data = bool(parsed.get("property_types") or parsed.get("extracted_info") or
                        parsed.get("performance_types") or parsed.get("performance_info"))
        if is_comp and not has_data:
            return {"output": {"skipped": True, "reason": "comparison_table_no_this_work"}}

        return {"output": parsed}

    @classmethod
    def from_llm(
        cls,
        include_llm: BaseLanguageModel,
        extract_llm: BaseLanguageModel,
        component: str = "",
        formatter: Any = None,
    ) -> "TableAgentV2":
        if formatter and component:
            extract_template = _build_table_extract_prompt(component, formatter)
        else:
            # 降级：使用通用表格提取 prompt（不含组件标签参考）
            extract_template = """你是一个电池材料数据提取专家。请分析表格并提取数据。

表格内容：
{content}
上下文：DOI={doi} | 材料={material_id}
当前目标材料：{material_context}

═══ 规则 A：对比汇总表（多篇文献对比表）═══
- 只提取本工作(this work)的行，排除其他文献数据
- 如果无法确定哪行是"this work"，返回空数据
- is_comparison_table = true

═══ 规则 B：本工作专用表 ═══
- 只提取与"当前目标材料: {material_context}"相关的行
- 非目标材料的行全部排除
- is_comparison_table = false

═══ 输出格式（严格 JSON）═══
{{{{
  "is_comparison_table": true/false,
  "property_types": [],
  "extracted_info": {{{{
    "属性1": [{{{{"value":"","unit":"","condition":""}}}}]
  }}}},
  "performance_types": [],
  "performance_info": {{{{
    "指标1": [{{{{"value":"","unit":"","condition":""}}}}]
  }}}}
}}}}

无法确定目标材料时返回空。仅返回 JSON。"""

        return cls(
            include_chain=LLMChain(llm=include_llm, prompt=TABLE_IS_RELEVANT_PROMPT_V2),
            extract_chain=LLMChain(llm=extract_llm, prompt=PromptTemplate(
                template=extract_template,
                input_variables=["content", "material_id", "doi", "material_context"],
            )),
            component=component,
            formatter=formatter,
        )
