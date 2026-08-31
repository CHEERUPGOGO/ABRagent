# -*- coding: utf-8 -*-
"""电化学性能提取 Agent v2 — 修复：增加 key:value 行解析 fallback，解决 extract JSON 解析失败"""
import json, re, logging
from typing import Any, Dict, List, Optional
from langchain_core.language_models.base import BaseLanguageModel
from langchain_classic.chains.base import Chain
from langchain_classic.chains.llm import LLMChain
from langchain_core.prompts import PromptTemplate
from langchain_core.callbacks.manager import CallbackManagerForChainRun
from miner.cathode_database.cathode_formatter import CathodeFormatter
from miner.cathode_database.cathode_prompts import PROMPT_PERFORMANCE_INCLUDE, PROMPT_PERFORMANCE_EXTRACT
from miner.extraction_core.errors import StructuredFormatError, LangchainError
from miner.extraction_core.pricing import TokenChecker
from miner.extraction_core.utils import fix_json_escape

log = logging.getLogger("PerformanceAgentV2")

class PerformanceAgentV2(Chain):
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
        rm.on_text(f"\n[PerfV2] ", verbose=self.verbose)
        rm.on_text(text, verbose=self.verbose, color="yellow")

    def _parse_include(self, output: str) -> List[str]:
        output = output.replace("```JSON", "").replace("```json", "").replace("```", "").strip()
        if re.search(r"[Ii] do not know", output): return []
        try:
            import ast
            if output.startswith("[") and output.endswith("]"):
                return list(set(r for r in ast.literal_eval(output) if isinstance(r, str)))
            m = re.search(r"\[.*\]", output, re.DOTALL)
            if m: return list(set(r for r in ast.literal_eval(m.group(0)) if isinstance(r, str)))
        except: pass
        # [修复] BaseAgent 风格 fallback：扫描 LLM 自然语言输出中的已知标签
        all_labels = list(CathodeFormatter.perf_explanation.keys())
        found = []
        for label in all_labels:
            if re.search(rf'[\*\-\s]{{0,3}}{re.escape(label)}\s*[:：]', output):
                found.append(label)
        if found:
            return found
        return []

    def _parse_extract(self, output: str, prop_name: str = "") -> Any:
        """v2: 增加 key:value 行解析 fallback"""
        output = output.replace("```JSON", "").replace("```json", "").replace("```", "").strip()
        if re.search(r"[Ii] do not know", output): return {}

        # 尝试标准 JSON 解析
        output = fix_json_escape(output)
        try: return json.loads(output)
        except json.JSONDecodeError: pass

        # 尝试从 Okay/Here 前缀后提取 JSON
        for pat in [r'^[Oo]kay[,\.]?.*?\{', r'^[Hh]ere[\'s]?.*?\{']:
            if re.search(pat, output, re.DOTALL):
                m = re.search(r"(\{.*\})", output, re.DOTALL)
                if m:
                    try: return json.loads(m.group(1))
                    except: pass
                break

        # 尝试递归匹配 `{...}`
        try:
            m = re.search(r"(\{(?:[^{}]|(?R))*\})", output, re.DOTALL)
            if m: return json.loads(m.group(1))
        except: pass

        # [新增] 尝试 JSON 数组 `[{...}, {...}]`
        try:
            m = re.search(r"(\[.*\])", output, re.DOTALL)
            if m: return json.loads(m.group(1))
        except: pass

        # [新增] key: value 行解析 fallback
        lines = output.strip().split("\n")
        data = {}
        for line in lines:
            line = line.strip().rstrip(",")
            # 匹配 "Label: value unit, condition..." 或 "Label: value unit"
            m = re.match(r'^[\*\-\s]*([A-Za-z_]+(?:\s[A-Za-z_]+)*)\s*[:：]\s*([\d.~≈＞<≥≤±×]+)\s*([\w/°%μg⁻¹²³μ³⁺A-Za-z·\^-]+)', line)
            if m:
                label = m.group(1).strip()
                try:
                    val = float(m.group(2).replace("~","").replace("≈",""))
                except ValueError:
                    val = m.group(2).strip()
                unit = m.group(3).strip()
                data.setdefault(label, []).append({"value": val, "unit": unit})
                continue
            # 更宽松的匹配: "label: value"
            m2 = re.match(r'^[\*\-\s]*([A-Za-z_]+(?:\s[A-Za-z_]+)*)\s*[:：]\s*(.+?)(?:\s*\((.+?)\))?\s*$', line)
            if m2:
                label = m2.group(1).strip()
                rest = m2.group(2).strip()
                data.setdefault(label, []).append({"value": rest, "source_text": rest})

        if data:
            return data

        raise StructuredFormatError(raw_output=output, property_name=prop_name)

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
        condition_id = inputs.get("condition_id", "")
        doi = inputs.get("doi", "")

        base = {"content": content, "material_id": material_id,
                "battery_system_context": battery_system_context,
                "condition_id": condition_id, "performance_types": [],
                "extracted_info": {}, "doi": doi}

        explanation_text = "\n".join(f"- {k}: {v}" for k, v in CathodeFormatter.perf_explanation.items())
        inc_prompt = PROMPT_PERFORMANCE_INCLUDE.format(
            battery_system_context=battery_system_context, material_id=material_id,
            condition_id=condition_id, explanation=explanation_text, paragraph=content)
        if self.token_checker: self.token_checker.check_include(inc_prompt)
        try:
            inc_out = self.include_chain.llm.invoke(inc_prompt)
            inc_out = inc_out.content if hasattr(inc_out, 'content') else str(inc_out)
        except Exception as e:
            raise LangchainError(chain_name="PerfInclude", original_error=e)

        perf_types = self._parse_include(inc_out)
        self._write_log(f"识别性能: {perf_types}", _rm)
        if not perf_types:
            return {"output": base}

        st_str, info_str, ex_str, prop_str = "", "", "", ""
        for p in perf_types:
            try:
                st_str += f"- {CathodeFormatter.perf_structured_data[p]}\n"
                info_str += f"- {CathodeFormatter.perf_information[p]}\n"
                ex_str += f"- {CathodeFormatter.perf_example_text[p]}\n"
                prop_str += f"{p}, "
            except KeyError:
                self._write_log(f"无格式: {p}", _rm)
        if not prop_str:
            return {"output": {**base, "performance_types": perf_types}}

        ext_prompt = PROMPT_PERFORMANCE_EXTRACT.format(
            battery_system_context=battery_system_context, material_id=material_id,
            condition_id=condition_id, prop=prop_str, structured_data=st_str,
            information=info_str, example=ex_str, paragraph=content)
        if self.token_checker: self.token_checker.check_extract(ext_prompt)
        try:
            ext_out = self.extract_chain.llm.invoke(ext_prompt)
            ext_out = ext_out.content if hasattr(ext_out, 'content') else str(ext_out)
        except Exception as e:
            raise LangchainError(chain_name="PerfExtract", original_error=e)

        try:
            extracted = self._parse_extract(ext_out)
        except StructuredFormatError:
            log.warning("JSON 解析失败，返回空")
            extracted = {}

        return {"output": {**base, "performance_types": perf_types, "extracted_info": extracted}}

    @classmethod
    def from_llm(cls, include_llm: BaseLanguageModel, extract_llm: BaseLanguageModel,
                 token_checker: TokenChecker = None, **kwargs) -> "PerformanceAgentV2":
        inc = LLMChain(llm=include_llm, prompt=PromptTemplate(
            template=PROMPT_PERFORMANCE_INCLUDE, input_variables=[
                "battery_system_context", "material_id", "condition_id",
                "explanation", "paragraph"]))
        ext = LLMChain(llm=extract_llm, prompt=PromptTemplate(
            template=PROMPT_PERFORMANCE_EXTRACT, input_variables=[
                "battery_system_context", "material_id", "condition_id",
                "prop", "structured_data", "information", "example", "paragraph"]))
        instance = cls(include_chain=inc, extract_chain=ext, **kwargs)
        instance.token_checker = token_checker
        return instance
