#!/usr/bin/env python3
"""提取结果 JSON -> CSV"""
import json, csv, glob, os
from pathlib import Path

EXTRACTED_DIR = "/home/ls/xiaoyue/LLM2/LMLLM/miner/json"
OUTPUT_DIR = "/home/ls/xiaoyue/LLM2/LMLLM/miner/json/csv"

def _match_material(text, materials):
    names = []
    for m in materials:
        sn = m.get("short_name","")
        fn = m.get("name","")
        if sn: names.append((sn, len(sn)))
        if fn: names.append((fn, len(fn)))
    names.sort(key=lambda x: -x[1])
    matched = []
    for name, _ in names:
        if name in text and name not in matched:
            matched.append(name)
    return ", ".join(matched[:4]) if matched else ""

def fmt_cond(c):
    parts = []
    if isinstance(c.get('temperature'), dict): t = c['temperature']; parts.append(f"{t.get('value','')}°{t.get('unit','')}")
    if isinstance(c.get('c_rate_or_current'), dict): r = c['c_rate_or_current']; parts.append(f"{r.get('value','')} {r.get('unit','')}")
    elec = c.get('electrolyte', '')[:50]
    if elec: parts.append(elec)
    meth = c.get('test_method', '')[:40]
    if meth: parts.append(meth)
    conf = c.get('electrode_config', '')[:30]
    if conf: parts.append(conf)
    return " | ".join(parts)

os.makedirs(OUTPUT_DIR, exist_ok=True)
for fp in sorted(glob.glob(os.path.join(EXTRACTED_DIR, "*_extracted.json"))):
    data = json.load(open(fp))
    meta = data.get("meta", {})
    materials = data.get("materials", [])
    items = data.get("items", [])
    doi = data.get("doi", "")
    title = meta.get("meta_title", doi)[:80]
    cond_map = {}
    for item in items:
        for c in (item.get("conditions") or []):
            cond_map[c.get("condition_id","")] = fmt_cond(c)
    rows = []
    for item in items:
        info = {}
        info.update(item.get("extracted_info", {}))
        info.update(item.get("performance_info", {}))
        if not info: continue
        para = item.get("paragraph","")[:200]
        for pn, pv in info.items():
            vals = pv if isinstance(pv, list) else [pv]
            for v in vals:
                if isinstance(v, dict):
                    val = v.get("value",""); unit = v.get("unit",""); src = v.get("source_text","")[:100]
                    cid = v.get("condition_id",""); cond_d = cond_map.get(cid,"")
                    mat = _match_material(cond_d, materials) if (cid and cid in cond_map) else _match_material(para, materials)
                    rows.append([pn, f"{val} {unit}".strip(), mat, cond_d, src, para, doi, title])
                else:
                    rows.append([pn, str(v), _match_material(para, materials), "", "", para, doi, title])
    if not rows: continue
    out = os.path.join(OUTPUT_DIR, os.path.basename(fp).replace("_extracted.json","_extracted.csv"))
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["属性","数值","关联材料","条件","原文片段","段落来源","DOI","标题"])
        w.writerows(rows)
    print(f"{os.path.basename(out)} -> {len(rows)} 行")
print(f"\n保存至 {OUTPUT_DIR}")
