#!/usr/bin/env python3
"""将负极三个 agent 结果合并为"以材料为中心"的最终格式"""
import json, os
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BASE = _PROJECT_ROOT / "miner" / "json"
STEM = "10.1039_d1ta07306k"

with open(BASE / f"{STEM}_anode_material.json") as f: mat_data = json.load(f)
with open(BASE / f"{STEM}_anode_perf.json") as f: perf_data = json.load(f)
with open(BASE / f"{STEM}_anode_cond.json") as f: cond_data = json.load(f)

materials = []
for i, (ms, ps, cs) in enumerate(zip(mat_data["results"], perf_data["results"], cond_data["results"])):
    mid = ms["material_id"]
    cid_used = ps.get("condition_id_used", "")

    # 本征属性聚合
    intrinsic = {}
    for item in ms["material_items"]:
        ei = item["extract_result"].get("extracted_info", {})
        if not isinstance(ei, dict): continue
        for prop, values in ei.items():
            if prop in ("value","unit","source_text"): continue
            if isinstance(values, list) and len(values)>0:
                intrinsic.setdefault(prop, [])
                intrinsic[prop].extend(values)
    for k in intrinsic:
        seen=set(); uniq=[]
        for v in intrinsic[k]:
            key=json.dumps(v,sort_keys=True,ensure_ascii=False)
            if key not in seen: seen.add(key); uniq.append(v)
        intrinsic[k]=uniq

    # 条件映射
    all_conds = cs.get("all_conditions", [])
    cond_map = {c["condition_id"]:c for c in all_conds if c.get("condition_id")}

    # 性能按 condition_id 分组
    perf_by_cond = {}
    for item in ps["performance_items"]:
        ei = item["extract_result"].get("extracted_info", {})
        if not isinstance(ei, dict): continue
        cid = cid_used
        for vals in ei.values():
            if isinstance(vals,list):
                for v in vals:
                    if isinstance(v,dict) and "condition_id" in v:
                        cid = v["condition_id"]
        perf_by_cond.setdefault(cid, {})
        for metric, values in ei.items():
            if not isinstance(values,list) or len(values)==0: continue
            cleaned = [{k:vv for k,vv in v.items() if k!="condition_id"} if isinstance(v,dict) else v for v in values]
            perf_by_cond[cid].setdefault(metric, []); perf_by_cond[cid][metric].extend(cleaned)

    perf_with_cond = []
    for cid, metrics in perf_by_cond.items():
        c = cond_map.get(cid, {})
        perf_with_cond.append({
            "condition_id": cid,
            "electrochemical_test_conditions": c.get("electrochemical_test_conditions",{}),
            "cell_assembly_conditions": c.get("cell_assembly_conditions",{}),
            "electrode_fabrication_params": c.get("electrode_fabrication_params",{}),
            "performance_metrics": metrics,
        })

    materials.append({
        "material_name": ms["battery_system_context"],
        "material_id": mid,
        "intrinsic_properties": intrinsic,
        "electrochemical_performance": perf_with_cond,
    })

result = {"doi":mat_data["doi"],"title":mat_data["title"],"source_file":mat_data["file"],"materials":materials}
out_path = BASE / f"{STEM}_anode_consolidated.json"
with open(out_path,"w",encoding="utf-8") as f:
    json.dump(result,f,ensure_ascii=False,indent=2)

print(f"✅ -> {out_path}")
for mat in materials:
    print(f"\n材料: {mat['material_name']}")
    print(f"  本征: {list(mat['intrinsic_properties'].keys())}")
    for ep in mat['electrochemical_performance']:
        tc=ep['electrochemical_test_conditions']
        ac=ep['cell_assembly_conditions']
        print(f"  {ep['condition_id']}: {tc.get('cell_config','?')} | {tc.get('current_density_mA_cm2','')}mA/cm² | 电解液={ac.get('electrolyte_composition','')[:40]}")
        for m,v in ep['performance_metrics'].items():
            for xv in v:
                print(f"    {m}: {xv.get('value','?')} {xv.get('unit','')}")
