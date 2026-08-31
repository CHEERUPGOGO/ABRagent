# -*- coding: utf-8 -*-
"""按 DOI 合并挖掘结果 → merged_all.json + CSV（离线、幂等，不重新调 LLM）

用法:
  python agent/merge_tok_results.py -i agent/output/tok2000_mine/tok2000 -o agent/output/tok2000_mine

合并规则:
  - 扫描 {i}/{cathode,anode,electrolyte}/*_rag.json（单篇结果）
  - 同一 DOI 跨组件的记录合并成一篇：global_conditions 并集、materials 并集
    （同 material_id 的材料合并 conditions，属性最后统一过 phase3 去重+空值过滤）
  - 属性保留各自 component 标记（挖掘时已带 cathode/anode/electrolyte）
"""
import sys, json, argparse
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.flatten_ml import write_csv, write_json
from agent.phase3_merge_agent import _merge_duplicate_props


def _merge_material(dst, src):
    """合并同一材料：conditions 按 canonical_id 合并（properties 拼接），intrinsic 拼接。"""
    cond_map = {}
    for c in dst.get("conditions", []):
        cond_map[c.get("canonical_id") or c.get("condition_id", "")] = c
    for c in src.get("conditions", []):
        key = c.get("canonical_id") or c.get("condition_id", "")
        if key in cond_map:
            cond_map[key]["properties"].extend(c.get("properties", []))
        else:
            cond_map[key] = c
            dst["conditions"].append(c)
    dst["intrinsic_properties"].extend(src.get("intrinsic_properties", []))


def _flatten_for_pinn(rec: Dict) -> Dict:
    """归组结构 → PINN 期望的平铺结构（miner_records_to_cell_spec 输入）。

    phase3 归组后属性在 materials[].conditions[].properties 嵌套里，
    PINN 入口读顶层 conditioned_properties / intrinsic_properties / conditions，
    材料用 name/short_name。这里做一层摊平。
    """
    out = json.loads(json.dumps(rec))
    # 条件：global_conditions → conditions（PINN 读 conditions[0].condition/scenario）
    out["conditions"] = rec.get("global_conditions", [])
    # 属性平铺 + 材料映射（material_id 直接作为 name，PINN 用 DEFAULT_MATERIALS 匹配）
    cond_props: list = []
    intr_props: list = []
    mats: list = []
    for m in rec.get("materials", []):
        mid = m.get("material_id", "")
        mats.append({"name": mid, "material_id": mid})
        for c in m.get("conditions", []):
            for p in c.get("properties", []):
                cp = json.loads(json.dumps(p))
                cp.setdefault("material_id", mid)
                cp.setdefault("condition_id", c.get("canonical_id") or c.get("condition_id", ""))
                cond_props.append(cp)
        for p in m.get("intrinsic_properties", []):
            intr_props.append(p)
    out["materials"] = mats
    out["conditioned_properties"] = cond_props
    out["intrinsic_properties"] = intr_props
    return out


def main():
    ap = argparse.ArgumentParser(description="按 DOI 合并挖掘结果")
    ap.add_argument("-i", default="agent/output/tok2000_mine/tok2000", help="单篇 JSON 根目录")
    ap.add_argument("-o", default="agent/output/tok2000_mine", help="输出目录")
    args = ap.parse_args()
    root = Path(args.i)
    od = Path(args.o)
    od.mkdir(parents=True, exist_ok=True)

    by_doi = {}
    n_files = 0
    for comp in ["cathode", "anode", "electrolyte"]:
        d = root / comp
        if not d.exists():
            continue
        for fp in sorted(d.glob("*_rag.json")):
            n_files += 1
            data = json.loads(fp.read_text(encoding="utf-8"))
            for r in data:
                doi = r.get("doi") or fp.stem.replace("_rag", "")
                by_doi.setdefault(doi, []).append(r)

    merged = []
    n_cross = 0
    for doi, recs in by_doi.items():
        if len(recs) == 1:
            merged.append(recs[0])
            continue
        n_cross += 1
        base = json.loads(json.dumps(recs[0]))
        cond_ids = {c.get("condition_id") for c in base.get("global_conditions", [])}
        mat_map = {m.get("material_id"): m for m in base.get("materials", [])}
        for r in recs[1:]:
            for c in r.get("global_conditions", []):
                if c.get("condition_id") not in cond_ids:
                    base["global_conditions"].append(c)
                    cond_ids.add(c.get("condition_id"))
            for m in r.get("materials", []):
                mid = m.get("material_id")
                if mid in mat_map:
                    _merge_material(mat_map[mid], m)
                else:
                    mat_map[mid] = m
                    base["materials"].append(m)
        # 属性级去重 + 空值过滤（复用 phase3 逻辑）
        base = _merge_duplicate_props(base)
        merged.append(base)

    write_json(merged, od / "merged_all.json")
    write_csv(merged, od)
    # PINN 平铺结构（每 DOI 一条，顶层 conditioned_properties/intrinsic_properties/conditions）
    pinn_input = [_flatten_for_pinn(r) for r in merged]
    write_json(pinn_input, od / "pinn_input.json")
    print(f"单篇 JSON: {n_files} | 合并后记录: {len(merged)} | 跨组件合并 DOI: {n_cross}")
    print(f"输出: {od}/merged_all.json, {od}/pinn_input.json, "
          f"{od}/_all_conditioned_data.csv, {od}/_all_intrinsic_data.csv")
    return merged


if __name__ == "__main__":
    main()
