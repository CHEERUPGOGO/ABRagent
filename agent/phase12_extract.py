"""Phase12: Include + Extract 两步串行"""
import json, re, logging
from typing import List, Dict
from agent.config import MIN_PARAGRAPH_LEN
from agent.prompts import PHASE12_INCLUDE, PHASE12_EXTRACT, TABLE_EXTRACT
from agent.phase0_discovery import _get_explanation, _get_label_details
L = logging.getLogger("Phase12")
_MF = ["temperature","c_rate","current_density","electrode_config","test_method"]

def _id(a,b):
    for f in _MF:
        va = (a.get("condition") or {}).get(f,{}) if isinstance(a.get("condition"),dict) else {}
        vb = (b.get("condition") or {}).get(f,{}) if isinstance(b.get("condition"),dict) else {}
        if isinstance(va,dict) and isinstance(vb,dict):
            av,bv = va.get("value"), vb.get("value")
            if av is not None and bv is not None and av != bv: return False
        elif va != vb and va and vb: return False
    ea = ((a.get("condition") or {}).get("electrolyte","") or "").strip().lower()
    eb = ((b.get("condition") or {}).get("electrolyte","") or "").strip().lower()
    return not (ea and eb and ea != eb)

def _merge(a,b):
    m = json.loads(json.dumps(a))
    ca,cb = m.get("condition",{}), b.get("condition",{})
    if isinstance(ca,dict) and isinstance(cb,dict):
        for k in cb:
            if k not in ca or ca[k] is None or ca[k]=="":
                v = cb[k]
                if v is not None and v != "" and v != 0: ca[k] = json.loads(json.dumps(v))
    return m

def _fmt(c):
    if not c: return "No known conditions yet."
    return "\n".join(f"  {x.get('condition_id','?')} ({x.get('scenario','?')}): {json.dumps(x.get('condition',{}),ensure_ascii=False)[:300]}" for x in c)

def _parse(raw):
    raw=raw.replace("```json","").replace("```JSON","").replace("```","").strip()
    m=re.search(r"(\{.*\})",raw,re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except: pass
    return {"new_conditions":[],"conditioned_properties":[],"intrinsic_properties":[],"matched_labels":[]}

def _upd(gc, nid, news):
    for nc in news:
        if not isinstance(nc,dict): continue
        nc.setdefault("condition",{}); ok=False
        for e in gc:
            if _id(e,nc): _merge(e,nc); ok=True; break
        if not ok: cid=f"C{nid:03d}"; nid+=1; nc["condition_id"]=cid; gc.append(nc)
    return nid

def run_include(llm, paragraphs, component="", ic=None):
    gc=list(ic) if ic else []; nid=len(gc)+1
    exp=_get_explanation(component); res=[]
    for idx,para in enumerate(paragraphs):
        if len(para.strip())<MIN_PARAGRAPH_LEN: res.append([]); continue
        p=PHASE12_INCLUDE.format(known_conditions=_fmt(gc),explanations=exp,content=para)
        try: r=_parse((resp.content if hasattr((resp:=llm.invoke(p)),"content") else str(resp)))
        except: r={}
        nid=_upd(gc,nid,r.get("new_conditions",[]))
        ml=r.get("matched_labels",[]); res.append(ml)
        if ml: L.info(f"  inc[{idx}]: {[m.get('label','?') for m in ml]}")
    L.info(f"Include: {len(gc)}条件")
    return gc, res

def run(llm, paragraphs, component="", fc=None, ic=None):
    """向后兼容 — 合并 include+extract（其他 pipeline 用）"""
    gc, inc_res = run_include(llm, paragraphs, component=component, ic=ic)
    ac, ai = run_extract(llm, paragraphs, inc_res, component=component, gc=gc)
    return {"global_conditions": gc, "conditioned_properties": ac, "intrinsic_properties": ai}

def _fmt_mats(materials):
    if not materials: return "(no materials)"
    return "\n".join(f"  {m.get('material_id','?')} ({m.get('name','?')}, {m.get('formula','')})" for m in materials)


def _extract_table_row(llm, para, component, materials):
    """表格行段落：跳过 include 标签匹配，直接用 TABLE_EXTRACT 提取属性。

    Returns:
        (props, new_materials)
    """
    mat_text = _fmt_mats(materials)
    labels = _get_label_details(component)  # 全量标签定义
    try:
        prompt = TABLE_EXTRACT.format(known_materials=mat_text, labels=labels)
    except Exception:
        prompt = TABLE_EXTRACT.format(known_materials="(no materials)", labels="(no labels)")
    prompt += "\n## Table Row\n" + para
    try:
        resp = llm.invoke(prompt)
        raw = resp.content if hasattr(resp, "content") else str(resp)
        data = _parse(raw)
    except Exception:
        return [], []
    props = [p for p in data.get("properties", []) if isinstance(p, dict)]
    new_mats = [m for m in data.get("new_materials", []) if isinstance(m, dict)]
    L.info(f"  [TABLE] {para.strip().splitlines()[0][:44]} -> {len(props)} 属性, {len(new_mats)} 新材料")
    return props, new_mats

def run_sequential(llm, paragraphs, component="", ic=None, materials=None):
    """段落内连做: include → 立即 extract → 更新条件 → 下一段"""
    gc=list(ic) if ic else []; nid=len(gc)+1
    exp=_get_explanation(component); ac,ai=[],[]
    mat_text=_fmt_mats(materials)
    for idx,para in enumerate(paragraphs):
        # 表格行段落（[TABLE ROW] 拆行产物）不受短段过滤，保证表格数据能进提取
        if len(para.strip())<MIN_PARAGRAPH_LEN and not para.strip().startswith("[TABLE ROW"):
            continue
        if para.strip().startswith("[TABLE ROW"):
            # 表格行：跳过 include 标签匹配，直通 TABLE_EXTRACT
            tprops, new_mats = _extract_table_row(llm, para, component, materials)
            for nm in new_mats:
                if not any(m.get("material_id") == nm.get("material_id") for m in materials):
                    materials.append(nm)
            for p in tprops:
                p.setdefault("source_type", "table")
                if p.get("property_type") == "electrochemical_performance":
                    ac.append(p)
                else:
                    ai.append(p)
            continue
        p_inc=PHASE12_INCLUDE.format(known_materials=mat_text, known_conditions=_fmt(gc), explanations=exp, content=para)
        try: r_inc=_parse((resp.content if hasattr((resp:=llm.invoke(p_inc)),"content") else str(resp)))
        except: r_inc={}
        nid=_upd(gc,nid,r_inc.get("new_conditions",[]))
        ml=r_inc.get("matched_labels",[])
        if not ml: continue
        L.info(f"  [{idx}] include: {[m.get('label','?') for m in ml]}")
        hl=set(m.get("label","") for m in ml if m.get("label"))
        det=_get_label_details(component,list(hl))
        p_ext=PHASE12_EXTRACT.format(known_materials=mat_text, known_conditions=_fmt(gc), label_details=det, content=para)
        try: r_ext=_parse((resp.content if hasattr((resp:=llm.invoke(p_ext)),"content") else str(resp)))
        except: r_ext={}
        nid=_upd(gc,nid,r_ext.get("new_conditions",[]))
        for _p in r_ext.get("conditioned_properties",[]):
            if isinstance(_p, dict):
                _p.setdefault("source_type", "text")
                ac.append(_p)
        for _p in r_ext.get("intrinsic_properties",[]):
            if isinstance(_p, dict):
                _p.setdefault("source_type", "text")
                ai.append(_p)
        n=len(r_ext.get("conditioned_properties",[]))+len(r_ext.get("intrinsic_properties",[]))
        if n: L.info(f"  [{idx}] extract: {n}")
    L.info(f"Sequential: {len(gc)}条件 {len(ac)}条件属性 {len(ai)}本征属性")
    return gc, ac, ai

def run_extract(llm, paragraphs, inc_results, component="", gc=None):
    nid=len(gc)+1 if gc else 1; ac,ai=[],[]
    for idx,para in enumerate(paragraphs):
        if len(para.strip())<MIN_PARAGRAPH_LEN or not inc_results or idx>=len(inc_results) or not inc_results[idx]: continue
        hl=set(m.get("label","") for m in inc_results[idx] if m.get("label"))
        if not hl: continue
        det=_get_label_details(component,list(hl))
        p=PHASE12_EXTRACT.format(known_conditions=_fmt(gc or []),label_details=det,content=para)
        try: r=_parse((resp.content if hasattr((resp:=llm.invoke(p)),"content") else str(resp)))
        except: r={}
        nid=_upd(gc,nid,r.get("new_conditions",[]))
        ac.extend(r.get("conditioned_properties",[])); ai.extend(r.get("intrinsic_properties",[]))
        n=len(r.get("conditioned_properties",[]))+len(r.get("intrinsic_properties",[]))
        if n: L.info(f"  ext[{idx}]: {n}")
    L.info(f"Extract: {len(ac)}条件属性 {len(ai)}本征属性")
    return ac, ai
