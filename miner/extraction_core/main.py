# -*- coding: utf-8 -*-
"""正极材料数据库 — 独立/协同 Agent 提取。--agent material|perf|condition|all"""

import os, sys, json, re, logging
from pathlib import Path
from typing import Dict, List, Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from miner.config import create_llm
from miner.cleaning.structured_clean import structured_clean
from miner.extraction_core import (MaterialAgent, PerformanceAgent, ConditionAgent)
from miner.extraction_core.pricing import TokenChecker
from miner.extraction_core.errors import LangchainError

logger = logging.getLogger("ExtractionCore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")


def detect_battery_systems(text: str) -> List[Dict]:
    systems = []; sid = 0
    for m in re.finditer(r'(?:a\s+)?(\w[\w\d.]+\s*(?:cathode|正极|positive))\s*(?:was\s+tested\s+)?in\s+(?:a\s+)?half[\s-]?cell\s+(?:with|against|vs\.?)\s+(Li(?:\s*metal)?|Na(?:\s*metal)?)', text, re.I):
        sid += 1; systems.append({"system_id": f"BS{sid:03d}", "type": "half-cell", "material": m.group(1).strip(), "counter": m.group(2).strip()})
    for m in re.finditer(r'(?:a\s+)?full[\s-]?cell\s+(?:with|using|of)\s+(\w[\w\d.]*)\s*\|\|\s*(\w[\w\d.]*)', text, re.I):
        sid += 1; systems.append({"system_id": f"BS{sid:03d}", "type": "full-cell", "material": m.group(1).strip(), "counter": m.group(2).strip()})
    for m in re.finditer(r'(commercial|bare|pristine|conventional|unmodified|standard)\s+(\w[\w\d.]*)', text, re.I):
        sid += 1; systems.append({"system_id": f"BS{sid:03d}", "type": "comparison", "material": f"{m.group(1)} {m.group(2)}".strip(), "counter": ""})
    if not systems:
        tm = re.search(r'#\s+(.+)', text); t = tm.group(1) if tm else ""
        systems.append({"system_id": "BS001", "type": "unknown", "material": t[:80], "counter": ""})
    return systems


def _safe_invoke(agent, inputs):
    try: return agent.invoke(inputs).get("output", {})
    except: return {}


def process_document(file_path, agent_mode, material_agent=None, perf_agent=None, condition_agent=None):
    logger.info(f"[{agent_mode}] {file_path}")
    try:
        doc = structured_clean(file_path, min_text_len=200, mode="extract")
        if doc is None: return {"file": file_path, "error": "text_too_short", "agent": agent_mode}
    except Exception as e:
        return {"file": file_path, "error": str(e), "agent": agent_mode}

    meta = doc.meta
    title = doc.meta.get("title") or ""
    doi = doc.meta.get("doi") or ""
    full_text = doc.clean_text
    battery_systems = detect_battery_systems(full_text)
    paragraphs = [p.strip() for p in doc.texts if len(p.strip()) > 100]

    if agent_mode != "all": ConditionAgent.reset_counter()

    all_results = []; global_conds = []; mc = 0
    for bs in battery_systems:
        mc += 1; mid = f"M{mc:03d}"
        ctx = f"{bs['type']}: {bs['material']}" + (f" || {bs['counter']}" if bs['counter'] else "")
        logger.info(f"  {bs['system_id']}: M={mid}")

        sys_res = {"battery_system_id": bs["system_id"], "battery_system_context": ctx, "material_id": mid}

        if agent_mode == "condition":
            items = []
            for p in paragraphs:
                out = _safe_invoke(condition_agent, {"content": p, "material_id": mid, "battery_system_context": ctx, "doi": doi, "known_conditions": global_conds})
                conds = out.get("extracted_conditions", [])
                cparams = out.get("condition_params", [])
                global_conds.extend(conds)
                items.append({"paragraph": p[:200], "include_result": {"condition_params": cparams}, "extract_result": {"conditions": conds}})
            sys_res["condition_items"] = items
        elif agent_mode == "material":
            items = []
            for p in paragraphs:
                out = _safe_invoke(material_agent, {"content": p, "material_id": mid, "battery_system_context": ctx, "doi": doi})
                ptypes = out.get("property_types", [])
                pinfo = out.get("extracted_info", {})
                items.append({"paragraph": p[:200], "include_result": {"property_types": ptypes}, "extract_result": {"extracted_info": pinfo}})
            sys_res["material_items"] = items
        elif agent_mode == "perf":
            ConditionAgent.reset_counter()
            cond_items = []; lconds = []
            for p in paragraphs:
                out = _safe_invoke(condition_agent, {"content": p, "material_id": mid, "battery_system_context": ctx, "doi": doi, "known_conditions": lconds})
                cs = out.get("extracted_conditions", []); lconds.extend(cs)
                if cs: cond_items.append({"paragraph": p[:200], "conditions": cs})
            cids = [c.get("condition_id","") for ci in cond_items for c in ci.get("conditions",[]) if c.get("condition_id")]
            primary_cid = cids[0] if cids else ""
            items = []
            for p in paragraphs:
                out = _safe_invoke(perf_agent, {"content": p, "material_id": mid, "battery_system_context": ctx, "condition_id": primary_cid, "doi": doi})
                if out.get("performance_types"): items.append({"paragraph": p[:200], "performance_types": out["performance_types"], "extracted_info": out.get("extracted_info",{})})
            sys_res["condition_id_used"] = primary_cid
            sys_res["performance_items"] = items
        else:  # all
            items = []
            for p in paragraphs:
                co = _safe_invoke(condition_agent, {"content": p, "material_id": mid, "battery_system_context": ctx, "doi": doi, "known_conditions": global_conds})
                conds = co.get("extracted_conditions", []); global_conds.extend(conds)
                cids = [c.get("condition_id","") for c in conds if c.get("condition_id")]
                pcid = cids[0] if cids else ""
                mo = _safe_invoke(material_agent, {"content": p, "material_id": mid, "battery_system_context": ctx, "doi": doi})
                po = _safe_invoke(perf_agent, {"content": p, "material_id": mid, "battery_system_context": ctx, "condition_id": pcid, "doi": doi})
                items.append({"paragraph": p[:200],
                              "condition": {"include": co.get("condition_params",[]), "extract": conds},
                              "material": {"include": {"property_types": mo.get("property_types",[])}, "extract": mo.get("extracted_info",{})},
                              "performance": {"include": {"performance_types": po.get("performance_types",[])}, "extract": po.get("extracted_info",{}), "condition_id": pcid}})
            sys_res["items"] = items
        all_results.append(sys_res)

    return {"file": file_path, "doi": doi, "title": title, "agent_mode": agent_mode,
            "battery_systems": battery_systems, "results": all_results}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("-i", "--input", default="papers/merged")
    p.add_argument("-o", "--output-dir", default="miner/json")
    p.add_argument("--agent", default="all", choices=["material","perf","condition","all"])
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--model-fast", default="classification")
    p.add_argument("--model-pro", default="extraction")
    args = p.parse_args()

    idir = args.input if os.path.isabs(args.input) else str(_PROJECT_ROOT / args.input)
    odir = args.output_dir if os.path.isabs(args.output_dir) else str(_PROJECT_ROOT / args.output_dir)
    os.makedirs(odir, exist_ok=True)

    inc_llm = create_llm(args.model_fast); ext_llm = create_llm(args.model_pro)
    tc = TokenChecker(getattr(inc_llm,'model_name',''), getattr(ext_llm,'model_name',''))

    mat_agt = MaterialAgent.from_llm(inc_llm, ext_llm, tc) if args.agent in ("material","all") else None
    prf_agt = PerformanceAgent.from_llm(inc_llm, ext_llm, tc) if args.agent in ("perf","all") else None
    cnd_agt = ConditionAgent.from_llm(inc_llm, ext_llm, tc) if args.agent in ("condition","perf","all") else None

    md_files = []
    for root, dirs, files in os.walk(idir):
        for f in files:
            if f.lower().endswith(".md"): md_files.append(os.path.join(root, f))
    md_files.sort()
    if args.limit > 0: md_files = md_files[:args.limit]
    logger.info(f"{len(md_files)} files | agent={args.agent}")

    all_docs = []
    for i, mdf in enumerate(md_files, 1):
        logger.info(f"[{i}/{len(md_files)}] {os.path.basename(mdf)}")
        try:
            doc = process_document(mdf, args.agent, mat_agt, prf_agt, cnd_agt)
            all_docs.append(doc)
            base = os.path.splitext(os.path.basename(mdf))[0]
            with open(os.path.join(odir, f"{base}_{args.agent}.json"), "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
        except LangchainError as e:
            logger.error(f"LC err: {e}"); all_docs.append({"file": mdf, "error": str(e)})
        except Exception as e:
            logger.error(f"Err: {e}"); all_docs.append({"file": mdf, "error": str(e)})

    summary = os.path.join(odir, f"_summary_{args.agent}.json")
    with open(summary, "w", encoding="utf-8") as f:
        json.dump({"agent_mode": args.agent, "n_files": len(all_docs), "token_summary": tc.summary(), "results": all_docs}, f, ensure_ascii=False, indent=2)
    ok = sum(1 for d in all_docs if "error" not in d)
    print(f"\n✅ [{args.agent}] {ok}/{len(md_files)} → {odir}")