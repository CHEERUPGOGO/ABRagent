"""MP 快照 × candidates.json 交叉比对 — 阶段 0 单源校验

MP summary 返回的是热力学性质（energy_above_hull / formation_energy / band_gap），
不是电化学容量/电压。因此单源只能验证「材料在 MP 中有稳定结构」，
不能验证「容量 200 mAh/g 这类电化学实验值」——后者需 CDX 双源。

评估规则：
  - 单元素体系（C/Si/Li）：看 min energy_above_hull（基态是否稳定）
  - 多元素插层体系（Li-Ni-Co-Mn-O 等）：看 median（体系典型相是否稳定）

产出 data/calibrated/mp_crosscheck.json：每个候选材料的 MP 稳定性结论。
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 候选材料 → 所属 MP chemsys（与 fetch_datasets.MP_QUERIES 对应）
CANDIDATE_CHEMSYS = {
    "NCM811": "Li-Ni-Co-Mn-O",
    "LRMO": "Li-Ni-Mn-O",      # Li1.2Ni0.2Mn0.6O2 属 Li-Ni-Mn-O
    "LNMO": "Li-Ni-Mn-O",      # LiNi0.5Mn1.5O4
    "graphite": "C",
    "si_base": "Si",
    "li_metal": "Li",
}

# 电解液候选没有 MP 对应结构（是溶剂分子/盐），单独标注
ELECTROLYTE_NOTE = "electrolyte 为溶剂/盐体系，无 MP 晶体结构，不参与结构稳定性校验"


def load_mp_snapshot() -> dict:
    raw = DATA_DIR / "raw"
    snaps = sorted(raw.glob("mp_snapshot_*.json"))
    if not snaps:
        raise FileNotFoundError("未找到 mp_snapshot，先跑 fetch_datasets.py mp")
    return json.loads(snaps[-1].read_text(encoding="utf-8"))


def stats(values: list) -> dict:
    if not values:
        return {"n": 0, "min": None, "median": None, "max": None}
    s = sorted(values)
    n = len(s)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return {"n": n, "min": round(s[0], 4), "median": round(med, 4),
            "max": round(s[-1], 4)}


def assess(chemsys: str, st: dict) -> str:
    """单元素看基态(min)，多元素插层看典型相(median)。"""
    if "-" not in chemsys:
        # 单元素：min 接近 0 → 存在稳定基态（石墨/晶体硅/金属锂）
        if st["min"] is not None and st["min"] < 0.01:
            return "存在稳定基态"
        return "无稳定基态，需人工复核"
    # 多元素插层：median < 0.2 eV/atom → 体系有稳定插层相
    if st["median"] is not None and st["median"] < 0.2:
        return "存在稳定插层相"
    return "体系记录少或偏亚稳，需人工复核"


def main():
    snap = load_mp_snapshot()
    cands = json.loads((DATA_DIR / "candidates.json").read_text(encoding="utf-8"))

    by_chemsys: dict = {}
    for rec in snap.get("records", []):
        chemsys = rec["chemsys"]
        ehulls = []
        for m in rec.get("response", {}).get("data", []):
            e = m.get("energy_above_hull")
            if e is not None:
                ehulls.append(float(e))
        by_chemsys[chemsys] = ehulls

    report = {"updated": snap["provenance"]["fetched_at"],
              "electrolyte_note": ELECTROLYTE_NOTE,
              "materials": {}}

    for cat in ("cathode", "anode", "electrolyte", "additive"):
        for m in cands.get(cat, []):
            cid = m["id"]
            if cid in CANDIDATE_CHEMSYS:
                cs = CANDIDATE_CHEMSYS[cid]
                st = stats(by_chemsys.get(cs, []))
                report["materials"][cid] = {
                    "chemsys": cs,
                    "n_records": st["n"],
                    "energy_above_hull": st,
                    "assessment": assess(cs, st),
                }
            else:
                report["materials"][cid] = {
                    "chemsys": None,
                    "note": ELECTROLYTE_NOTE if cat == "electrolyte" else "添加剂/无 MP 对应",
                }

    out_dir = DATA_DIR / "calibrated"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "mp_crosscheck.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"MP 交叉比对完成 → {out}\n")
    for cid, r in report["materials"].items():
        if r.get("chemsys"):
            st = r["energy_above_hull"]
            print(f"  {cid:10s} [{r['chemsys']:14s}] n={st['n']:3d}  "
                  f"min={st['min']} median={st['median']}  →  {r['assessment']}")
        else:
            print(f"  {cid:10s} →  {r.get('note', '')[:40]}")


if __name__ == "__main__":
    main()
