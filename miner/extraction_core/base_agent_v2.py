# -*- coding: utf-8 -*-
"""BaseAgent v2 — 修复 _parse_include 标签名清洗"""
import json, re, logging
from typing import Any
from langchain_classic.chains.base import Chain
from langchain_classic.chains.llm import LLMChain
from langchain_core.prompts import PromptTemplate
from langchain_core.callbacks.manager import CallbackManagerForChainRun
from miner.extraction_core.errors import LangchainError
from miner.extraction_core.utils import fix_json_escape

log = logging.getLogger("BaseAgentV2")

def _clean_label(raw: str) -> str:
    """提取纯标签名: 'Li_Nucleation_Overpotential (Cu): 73 mV' → 'Li_Nucleation_Overpotential'"""
    s = re.sub(r'\s*\(.*?\)\s*', '', raw)  # 去掉 (Cu)
    s = re.sub(r'\s*[:：].*', '', s)        # 去掉 : 73 mV
    s = re.sub(r'\s+[\d.%~/mAWhgV°Cμ²³⁻¹⁺≈±×]+.*$', '', s)
    return s.strip()

class BaseAgentV2(Chain):
    include_chain: LLMChain
    extract_chain: LLMChain
    input_key: str = "content"
    output_key: str = "output"
    token_checker: Any = None
    formatter: Any = None
    prompt_include: str = ""
    prompt_extract: str = ""
    base_name: str = "agent-v2"

    @property
    def input_keys(self): return [self.input_key]
    @property
    def output_keys(self): return [self.output_key]

    def _parse_include(self, o: str):
        o = o.replace("```JSON","").replace("```json","").replace("```","").strip()
        if re.search(r"[Ii] do not know", o): return []
        import ast
        try:
            if o.startswith("["):
                parsed = ast.literal_eval(o)
                if isinstance(parsed, list):
                    result = [r for r in parsed if isinstance(r, str)]
                    if result: return list(set(_clean_label(r) for r in result))
                    for key in ("property_name","property","name","label"):
                        r2 = [r.get(key) for r in parsed if isinstance(r,dict) and r.get(key)]
                        if r2: return list(set(_clean_label(r) for r in r2))
            m = re.search(r"\[.*\]", o, re.DOTALL)
            if m:
                parsed = ast.literal_eval(m.group(0))
                if isinstance(parsed, list):
                    result = [r for r in parsed if isinstance(r, str)]
                    if result: return list(set(_clean_label(r) for r in result))
                    for key in ("property_name","property","name","label"):
                        r2 = [r.get(key) for r in parsed if isinstance(r,dict) and r.get(key)]
                        if r2: return list(set(_clean_label(r) for r in r2))
        except: pass
        # fallback: 自然语言扫描已知标签
        if self.formatter and hasattr(self.formatter, 'explanation'):
            all_labels = list(self.formatter.explanation.keys())
            found = [l for l in all_labels if re.search(rf'[\*\-\s]{{0,3}}{re.escape(l)}\s*[:：]', o)]
            if found: return found
        return []

    def _parse_extract(self, o):
        o = o.replace("```JSON","").replace("```json","").replace("```","").strip()
        if re.search(r"[Ii] do not know", o): return {}
        o = fix_json_escape(o)
        try: return json.loads(o)
        except: pass
        for pat in [r'^[Oo]kay.*?\{', r'^[Hh]ere.*?\{']:
            if re.search(pat, o, re.DOTALL):
                m = re.search(r"(\{.*\})", o, re.DOTALL)
                if m:
                    try: return json.loads(m.group(1))
                    except: pass
                break
        try:
            m = re.search(r"(\{(?:[^{}]|(?R))*\})", o, re.DOTALL)
            if m: return json.loads(m.group(1))
        except: pass
        try:
            m = re.search(r"(\[.*\])", o, re.DOTALL)
            if m:
                r = json.loads(m.group(1))
                if isinstance(r, list) and r:
                    return r[0] if isinstance(r[0], dict) else r
        except: pass
        lines = o.strip().split("\n")
        data = {}
        for line in lines:
            line = line.strip().rstrip(",")
            m = re.match(r'^[\*\-\s]*([A-Za-z_]+(?:\s[A-Za-z_]+)*)\s*[:：]\s*([\d.~≈＞<≥≤±×]+)\s*([\w/°%μg⁻¹²³μ³⁺A-Za-z·\^-]+)', line)
            if m:
                label = m.group(1).strip()
                try: val = float(m.group(2).replace("~","").replace("≈",""))
                except ValueError: val = m.group(2).strip()
                unit = m.group(3).strip()
                data.setdefault(label, []).append({"value": val, "unit": unit})
                continue
            m2 = re.match(r'^[\*\-\s]*([A-Za-z_]+(?:\s[A-Za-z_]+)*)\s*[:：]\s*(.+?)(?:\s*\((.+?)\))?\s*$', line)
            if m2:
                label = m2.group(1).strip()
                rest = m2.group(2).strip()
                data.setdefault(label, []).append({"value": rest, "source_text": rest})
        if data:
            return data
        return {}

    def _call(self, inputs, run_manager=None):
        _rm = run_manager or CallbackManagerForChainRun.get_noop_manager()
        _rm.get_child()
        content = str(inputs.get(self.input_key, ""))
        mid = inputs.get("material_id", "")
        ctx = inputs.get("battery_system_context", "")
        cid = inputs.get("condition_id", "")
        doi = inputs.get("doi", "")
        base = {"content":content,"material_id":mid,"battery_system_context":ctx,
                "property_types":[],"extracted_info":{},"doi":doi}
        fmt = self.formatter
        expl = "\n".join(f"- {k}: {v}" for k,v in fmt.explanation.items())
        pt = self.prompt_include.format(battery_system_context=ctx,material_id=mid,condition_id=cid,explanation=expl,paragraph=content)
        try:
            inc = self.include_chain.llm.invoke(pt)
            inc = inc.content if hasattr(inc,'content') else str(inc)
        except Exception as e: raise LangchainError(chain_name="Include",original_error=e)
        if self.token_checker: self.token_checker.record(f"{self.base_name}-include",pt,inc,"include")
        ptypes = self._parse_include(inc)
        if not ptypes: return {"output":base}
        st=info=ex=ps=""
        for p in ptypes:
            ok=False
            try: st+=f"- {fmt.structured_data[p]}\n"; ok=True
            except KeyError: pass
            try: info+=f"- {fmt.information[p]}\n"
            except KeyError: pass
            try: ex+=f"- {fmt.example_text[p]}\n"
            except KeyError: pass
            if ok: ps+=f"{p}, "
        if not ps: return {"output":{**base,"property_types":ptypes}}
        ep = self.prompt_extract.format(battery_system_context=ctx,material_id=mid,condition_id=cid,prop=ps,structured_data=st,information=info,example=ex,paragraph=content)
        try:
            ext = self.extract_chain.llm.invoke(ep)
            ext = ext.content if hasattr(ext,'content') else str(ext)
        except Exception as e: raise LangchainError(chain_name="Extract",original_error=e)
        if self.token_checker: self.token_checker.record(f"{self.base_name}-extract",ep,ext,"extract")
        return {"output":{**base,"property_types":ptypes,"extracted_info":self._parse_extract(ext)}}

    @classmethod
    def from_llm(cls, include_llm, extract_llm, token_checker=None, prompt_include=None, prompt_extract=None, **kwargs):
        pi = prompt_include
        pe = prompt_extract
        if not pi and 'prompt_include' in cls.model_fields: pi = cls.model_fields['prompt_include'].default
        if not pe and 'prompt_extract' in cls.model_fields: pe = cls.model_fields['prompt_extract'].default
        inc = LLMChain(llm=include_llm, prompt=PromptTemplate(template=pi, input_variables=["battery_system_context","material_id","condition_id","explanation","paragraph"]))
        ext = LLMChain(llm=extract_llm, prompt=PromptTemplate(template=pe, input_variables=["battery_system_context","material_id","condition_id","prop","structured_data","information","example","paragraph"]))
        inst = cls(include_chain=inc, extract_chain=ext, **kwargs)
        inst.token_checker = token_checker
        return inst
