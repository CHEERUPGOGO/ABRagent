# -*- coding: utf-8 -*-
"""正极材料 Agent v2 — 修复：只使用49个材料属性标签，排除11个性能标签"""
import re, logging
from typing import Any
from langchain_classic.chains.llm import LLMChain
from langchain_core.prompts import PromptTemplate
from langchain_core.callbacks.manager import CallbackManagerForChainRun
from miner.extraction_core.base_agent_v2 import BaseAgentV2
from miner.extraction_core.errors import LangchainError
from miner.cathode_database.cathode_formatter import CathodeFormatter
from miner.cathode_database.cathode_prompts import PROMPT_MATERIAL_INCLUDE, PROMPT_MATERIAL_EXTRACT

log = logging.getLogger("CathodeAgentV2")

class CathodeAgentV2(BaseAgentV2):
    """正极材料 Agent v2 — 只从49个材料属性标签中识别"""
    formatter: Any = CathodeFormatter
    prompt_include: str = PROMPT_MATERIAL_INCLUDE
    prompt_extract: str = PROMPT_MATERIAL_EXTRACT
    base_name: str = "cathode-material-v2"

    def _call(self, inputs, run_manager=None):
        _rm = run_manager or CallbackManagerForChainRun.get_noop_manager()
        _rm.get_child()
        content = str(inputs.get(self.input_key, ""))
        mid = inputs.get("material_id", "")
        ctx = inputs.get("battery_system_context", "")
        cid = inputs.get("condition_id", "")
        doi = inputs.get("doi", "")

        base = {"content": content, "material_id": mid,
                "battery_system_context": ctx, "property_types": [],
                "extracted_info": {}, "doi": doi}

        fmt = self.formatter
        # [修复1] 只传材料标签（49个），不传性能标签（11个）
        expl = "\n".join(f"- {k}: {v}" for k, v in fmt.material_explanation.items())

        prompt_text = self.prompt_include.format(
            battery_system_context=ctx, material_id=mid,
            condition_id=cid, explanation=expl, paragraph=content)
        try:
            inc = self.include_chain.llm.invoke(prompt_text)
            inc = inc.content if hasattr(inc, 'content') else str(inc)
        except Exception as e:
            raise LangchainError(chain_name="Include", original_error=e)
        if self.token_checker:
            self.token_checker.record(f"{self.base_name}-include", prompt_text, inc, "include")

        ptypes = self._parse_include(inc)
        # [修复2] 后置过滤：只保留确属材料标签的属性
        material_keys = set(fmt.material_keys())
        ptypes = [p for p in ptypes if p in material_keys]
        if not ptypes:
            return {"output": base}

        st = info = ex = ps = ""
        for p in ptypes:
            try:
                st += f"- {fmt.material_structured_data[p]}\n"
                info += f"- {fmt.material_information[p]}\n"
                ex += f"- {fmt.material_example_text[p]}\n"
                ps += f"{p}, "
            except KeyError:
                pass
        if not ps:
            return {"output": {**base, "property_types": ptypes}}

        ext_prompt = self.prompt_extract.format(
            battery_system_context=ctx, material_id=mid,
            condition_id=cid, prop=ps, structured_data=st,
            information=info, example=ex, paragraph=content)
        try:
            ext = self.extract_chain.llm.invoke(ext_prompt)
            ext = ext.content if hasattr(ext, 'content') else str(ext)
        except Exception as e:
            raise LangchainError(chain_name="Extract", original_error=e)
        if self.token_checker:
            self.token_checker.record(f"{self.base_name}-extract", ext_prompt, ext, "extract")

        return {"output": {**base, "property_types": ptypes,
                           "extracted_info": self._parse_extract(ext)}}

    @classmethod
    def from_llm(cls, include_llm, extract_llm, token_checker=None,
                 prompt_include=None, prompt_extract=None, **kwargs):
        pi = prompt_include or cls.model_fields['prompt_include'].default
        pe = prompt_extract or cls.model_fields['prompt_extract'].default
        inc = LLMChain(llm=include_llm, prompt=PromptTemplate(
            template=pi, input_variables=[
                "battery_system_context", "material_id", "condition_id",
                "explanation", "paragraph"]))
        ext = LLMChain(llm=extract_llm, prompt=PromptTemplate(
            template=pe, input_variables=[
                "battery_system_context", "material_id", "condition_id",
                "prop", "structured_data", "information", "example", "paragraph"]))
        inst = cls(include_chain=inc, extract_chain=ext, **kwargs)
        inst.token_checker = token_checker
        return inst
