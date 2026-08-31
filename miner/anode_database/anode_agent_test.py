#!/usr/bin/env python3
"""负极 agent 完整管线：Condition → Material → Performance 三agent联动"""
import os, sys, json, re, logging, importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from collections.abc import Mapping

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from miner.config import create_llm
from miner.cleaning.structured_clean import structured_clean
from miner.extraction_core.pricing import TokenChecker
from miner.extraction_core.base_agent_v2 import BaseAgentV2
from miner.extraction_core.errors import LangchainError
from miner.anode_database.anode_formatter import AnodeFormatter
from langchain_classic.chains.base import Chain
from langchain_classic.chains.llm import LLMChain
from langchain_core.prompts import PromptTemplate
from langchain_core.callbacks.manager import CallbackManagerForChainRun

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("AnodePipeline")

def _load_mod(fp):
    s = importlib.util.spec_from_file_location(Path(fp).stem, fp)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

_pm = _load_mod(str(_PROJECT_ROOT/"miner"/"anode_database"/"anode_prompts1.py"))
P_MAT_INC=_pm.PROMPT_ANODE_MATERIAL_INCLUDE; P_MAT_EXT=_pm.PROMPT_ANODE_MATERIAL_EXTRACT
P_PERF_INC=_pm.PROMPT_ANODE_PERFORMANCE_INCLUDE; P_PERF_EXT=_pm.PROMPT_ANODE_PERFORMANCE_EXTRACT
P_COND_INC=_pm.PROMPT_ANODE_CONDITION_INCLUDE; P_COND_EXT=_pm.PROMPT_ANODE_CONDITION_EXTRACT; P_COND_EX=_pm.CONDITION_EXAMPLE_TEXT


class AnodeMaterialAgent1(BaseAgentV2):
    formatter:Any=None; prompt_include:str=P_MAT_INC; prompt_extract:str=P_MAT_EXT; base_name:str="anode-material"
    def _call(self,inputs,rm=None):
        s=self.formatter; self.formatter=type('MV',(),{'explanation':s.material_explanation,'structured_data':s.material_structured_data,'information':s.material_information,'example_text':s.material_example_text})()
        try: return super()._call(inputs,rm)
        finally: self.formatter=s

class AnodePerformanceAgent1(BaseAgentV2):
    formatter:Any=None; prompt_include:str=P_PERF_INC; prompt_extract:str=P_PERF_EXT; base_name:str="anode-perf"
    def _call(self,inputs,rm=None):
        s=self.formatter; self.formatter=type('PV',(),{'explanation':s.perf_explanation,'structured_data':s.perf_structured_data,'information':s.perf_information,'example_text':s.perf_example_text})()
        try: return super()._call(inputs,rm)
        finally: self.formatter=s

# 模块级计数器 + 线程锁
_cond_counter: int = 0
_cond_lock = __import__('threading').Lock()

class AnodeConditionAgent(Chain):
    include_chain:LLMChain; extract_chain:LLMChain; input_key:str="content"; output_key:str="output"
    token_checker:Optional[TokenChecker]=None
    @classmethod
    def _counter_next(cls) -> str:
        global _cond_counter
        with _cond_lock: _cond_counter+=1
        return f"C{_cond_counter:03d}"
    @property
    def input_keys(self): return [self.input_key]
    @property
    def output_keys(self): return [self.output_key]
    @classmethod
    def reset_counter(cls):
        import sys; mod = sys.modules[__name__]; mod._cond_counter = 0
    def _call(self,inputs,rm=None):
        rm=rm or CallbackManagerForChainRun.get_noop_manager()
        ct=str(inputs.get(self.input_key,"")); mid=inputs.get("material_id",""); ctx=inputs.get("battery_system_context",""); known=inputs.get("known_conditions",[])
        base={"content":ct,"material_id":mid,"has_condition":False,"extracted_conditions":[]}
        ip=P_COND_INC.format(battery_system_context=ctx,material_id=mid,paragraph=ct)
        try:
            io=self.include_chain.llm.invoke(ip); it=io.content if hasattr(io,'content') else str(io)
        except Exception as e: raise LangchainError(chain_name="CondInclude",original_error=e)
        if self.token_checker: self.token_checker.record("cond-inc",ip,it,"include")
        if not it.strip().lower().startswith("yes"): return {"output":base}
        ks=json.dumps([{"condition_id":c.get("condition_id",""),"electrochemical_test_conditions":c.get("electrochemical_test_conditions",{}),"cell_assembly_conditions":c.get("cell_assembly_conditions",{})} for c in known[-3:]],ensure_ascii=False)
        ep=P_COND_EXT.format(battery_system_context=ctx,material_id=mid,known_conditions_summary=ks,condition_example_text=P_COND_EX,paragraph=ct)
        try:
            eo=self.extract_chain.llm.invoke(ep); et=eo.content if hasattr(eo,'content') else str(eo)
        except Exception as e: raise LangchainError(chain_name="CondExtract",original_error=e)
        if self.token_checker: self.token_checker.record("cond-ext",ep,et,"extract")
        ec=et.replace("```JSON","").replace("```json","").replace("```","").strip()
        parsed=[]
        if re.search(r"[Ii] do not know",ec): pass
        else:
            try: parsed=json.loads(ec)
            except:
                m=re.search(r"(\[.*\])",ec,re.DOTALL)
                if m:
                    try: parsed=json.loads(m.group(1))
                    except: pass
        if not isinstance(parsed,list): parsed=[parsed] if isinstance(parsed,dict) else []
        final=[]
        for c in parsed:
            if not isinstance(c,dict): continue
            matched=False
            for kc in known:
                e1=c.get("electrochemical_test_conditions",{})or{}; e2=kc.get("electrochemical_test_conditions",{})or{}
                if any(str(e1.get(k,""))and str(e2.get(k,""))and str(e1[k])==str(e2[k]) for k in ["temperature_C","c_rate","current_density_mA_g","current_density_mA_cm2","voltage_min_V","voltage_max_V"]):
                    c["condition_id"]=kc["condition_id"]; matched=True; break
            if not matched: c["condition_id"]=self._counter_next()
            if not c.get("material_id"): c["material_id"]=mid
            final.append(c)
        return {"output":{**base,"has_condition":True,"extracted_conditions":final}}
    @classmethod
    def from_llm(cls,il,el,tc=None):
        inc=LLMChain(llm=il,prompt=PromptTemplate(template=P_COND_INC,input_variables=["battery_system_context","material_id","paragraph"]))
        ext=LLMChain(llm=el,prompt=PromptTemplate(template=P_COND_EXT,input_variables=["battery_system_context","material_id","known_conditions_summary","condition_example_text","paragraph"]))
        return cls(include_chain=inc,extract_chain=ext,token_checker=tc)

def discover_materials(full_text: str, file_stem: str, inc_llm):
    """使用 MaterialDiscoveryAgent 识别论文中的负极材料"""
    from miner.extraction_core.material_discovery import MaterialDiscoveryAgent
    agent = MaterialDiscoveryAgent(inc_llm)
    raw = full_text[:50000] if len(full_text) > 50000 else full_text
    mats = agent.discover(raw, component="anode", file_stem=file_stem)
    if not mats:
        tm = re.search(r'#\s+(.+)', raw or "")
        name = tm.group(1).strip()[:80] if tm else "anode material"
        mats = [{"name": name, "short_name": file_stem, "formula": "", "role": "novel", "description": "", "material_id": file_stem}]
    return mats

def si(a,i):
    try: return a.invoke(i).get("output",{})
    except Exception as e: logger.warning(f"fail: {e}"); return {}

def process_one_material(m, i, paras, doi, global_cond, global_cond_items, primary_cid):
    """处理单个材料（无 condition，用全局条件）"""
    mid = m.get("material_id", f"M{i+1:03d}")
    ctx = f"{m['role']}: {m['name']}" + (f" ({m.get('formula','')})" if m.get('formula') else "")

    il = create_llm("classification"); el = create_llm("extraction")
    tc = TokenChecker(getattr(il,'model_name',''), getattr(el,'model_name',''))
    mat = AnodeMaterialAgent1.from_llm(il, el, tc); mat.formatter = AnodeFormatter()
    perf = AnodePerformanceAgent1.from_llm(il, el, tc); perf.formatter = AnodeFormatter()

    # Material
    mi = []
    for p in paras:
        o = si(mat, {"content":p,"material_id":mid,"battery_system_context":ctx,"doi":doi})
        mi.append({"paragraph":p[:200],"include_result":{"property_types":o.get("property_types",[])},"extract_result":{"extracted_info":o.get("extracted_info",{})}})

    # Performance（用全局 primary_cid）
    pi = []
    for p in paras:
        o = si(perf, {"content":p,"material_id":mid,"battery_system_context":ctx,"condition_id":primary_cid,"doi":doi})
        if o.get("property_types") or o.get("extracted_info"):
            pi.append({"paragraph":p[:200],"include_result":{"property_types":o.get("property_types",[])},"extract_result":{"extracted_info":o.get("extracted_info",{})}})

    logger.info(f"[{mid}] {ctx} done: mat={len([x for x in mi if x['include_result']['property_types']])} perf={len(pi)}")
    return {"material_id":mid,"material_name":m["name"],"material_context":ctx,
            "material_items":mi,"performance_items":pi,"condition_id_used":primary_cid,
            "condition_items":global_cond_items,"all_conditions":global_cond}

def run(fp,od):
    fn=os.path.basename(fp);st=os.path.splitext(fn)[0];doi=st.replace("_","/")
    il=create_llm("classification");el=create_llm("extraction");tc=TokenChecker(getattr(il,'model_name',''),getattr(el,'model_name',''))
    doc=structured_clean(fp,200,"extract")
    if doc is None: logger.error("clean failed");return
    text=doc.clean_text
    paras=[p.strip() for p in doc.texts if len(p.strip())>100]
    materials = discover_materials(text, st, il)
    # Phase 0: 全局条件提取（只跑一次）
    logger.info("Phase 0: global conditions...")
    cnd0=AnodeConditionAgent.from_llm(il,el,tc); gc=[]; gci=[]
    for p in paras:
        o=si(cnd0,{"content":p,"material_id":"global","battery_system_context":"lithium metal anode","known_conditions":gc})
        cs=o.get("extracted_conditions",[]); gc.extend(cs)
        gci.append({"paragraph":p[:200],"has_condition":o.get("has_condition",False),"conditions":cs})
    pcid=next((c["condition_id"] for c in gc if c.get("condition_id")),"")
    logger.info(f"  conditions={len(gc)} primary_id={pcid}")
    logger.info(f"paras={len(paras)} materials={len(materials)}: {[m['short_name'] for m in materials]}")
    os.makedirs(od,exist_ok=True);title=doc.meta.get("title","") if doc.meta else ""

    # 材料间并行
    from concurrent.futures import ThreadPoolExecutor, as_completed
    am=[];ap=[];ac=[]
    with ThreadPoolExecutor(max_workers=min(6, len(materials))) as pool:
        futures = {pool.submit(process_one_material, m, i, paras, doi, gc, gci, pcid): i for i, m in enumerate(materials)}
        for f in as_completed(futures):
            r = f.result()
            am.append({"material_id":r["material_id"],"material_name":r["material_name"],"material_context":r["material_context"],"material_items":r["material_items"]})
            ap.append({"material_id":r["material_id"],"material_name":r["material_name"],"material_context":r["material_context"],"condition_id_used":r["condition_id_used"],"performance_items":r["performance_items"]})
            ac.append({"material_id":r["material_id"],"material_name":r["material_name"],"material_context":r["material_context"],"condition_items":r["condition_items"],"all_conditions":r["all_conditions"]})
    for mode,data,name in [("anode-material",am,"anode_material"),("anode-perf",ap,"anode_perf"),("anode-cond",ac,"anode_cond")]:
        j={"file":fp,"doi":doi,"title":title,"agent_mode":mode,"materials":materials,"results":data}
        with open(os.path.join(od,f"{st}_{name}.json"),"w",encoding="utf-8") as f: json.dump(j,f,ensure_ascii=False,indent=2)
        print(f"  -> {st}_{name}.json")
    cb=[]
    for i,m in enumerate(materials):
        mid=m.get("material_id", f"M{i+1:03d}")
        cb.append({"material_id":mid,"material_name":m["name"],"material_context":f"{m['role']}: {m['name']}",
            "n_cond_paras":sum(1 for c in ac[i]["condition_items"] if c["has_condition"]),
            "n_perf_paras":len(ap[i]["performance_items"]),
            "condition_id_used":ap[i]["condition_id_used"],"all_conditions":ac[i]["all_conditions"]})
    j={"file":fp,"doi":doi,"title":title,"agent_mode":"anode-pipeline","materials":materials,"systems":cb,"global_conditions":gc,"global_condition_id":pcid}
    with open(os.path.join(od,f"{st}_anode_pipeline.json"),"w",encoding="utf-8") as f: json.dump(j,f,ensure_ascii=False,indent=2)
    print(f"  -> {st}_anode_pipeline.json")
    print(f"\nToken: {tc.summary()}")
    print(f"\n✅ done -> {od}")

if __name__=="__main__":
    run(str(_PROJECT_ROOT/"papers"/"text_merged"/"mergemarkdown5"/"10.1039_d1ta07306k.md"),str(_PROJECT_ROOT/"miner"/"json"))
