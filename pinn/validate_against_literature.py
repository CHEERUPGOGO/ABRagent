# -*- coding: utf-8 -*-
"""validate_against_literature.py — 阶段 C+：文献点 vs 多材料 P2D 预测 验证闭环

流程：
  1. 读 agent/output/rag_clean/_all_conditioned_data.csv（miner/agent 扁平化结果）
  2. 过滤验证锚点（严格 = half_cell + 0.1C + 2.8-4.3V；宽松 = 比容量点）
  3. 锚点材料名 → P2D profile（NCM811 / Ni96 / LRMO / LNMO）
  4. 每组跑一次 P2D（缓存），逐点对比偏差
  5. 产出报告（JSON）

判定阈值（相对偏差 |lit - pred| / lit）：
  <=5%  excellent | <=10% ok | <=20% warning | >20% mismatch

用法：
  /home/ls/anaconda3/envs/py3134_conda/bin/python pinn/validate_against_literature.py
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pinn import candidates_scheme_to_cell_spec, fill_missing
from pinn.p2d_runner import run_discharge, MATERIAL_PROFILES

DATA_CSV = Path(__file__).resolve().parent.parent / "agent/output/rag_clean/_all_conditioned_data.csv"
OUT_DIR = Path(__file__).resolve().parent / "output"
OUT_DIR.mkdir(exist_ok=True)

TOL = {"excellent": 0.05, "ok": 0.10, "warning": 0.20}

# 锚点材料名 → P2D profile（部分匹配）
PROFILE_KEYWORDS = [
    ("Ni96", ("ni96", "ni-96", "ni 96", "ni96")),
    ("LRMO", ("lrmo", "li-rich", "li1.2", "li-rich mn", "lirich")),
    ("LNMO", ("lnmo", "spinelle", "spinel")),
    ("NCM811", ("ncm", "nmc", "811")),
]


def material_to_profile(name: str) -> str:
    """锚点材料名 → P2D profile 材料 id。未知默认 NCM811（NCM 系近似）。"""
    n = (name or "").lower()
    for profile, kws in PROFILE_KEYWORDS:
        if any(k in n for k in kws):
            return profile
    return "NCM811"


def _is_modified(name: str) -> bool:
    """判断材料是否为改性变体（P-/GP-/doped 前缀等）。"""
    if not name:
        return False
    for p in ("P-", "GP-", "doped", "modified"):
        if name.startswith(p) or p in name.lower():
            return True
    return False


def load_anchors(csv_path: Path) -> Tuple[List[Dict], List[Dict]]:
    """读 CSV，返回 (严格锚点, 宽松锚点)。"""
    strict: List[Dict] = []
    loose: List[Dict] = []
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for r in rows:
        if "Discharge_Specific_Capacity" not in r["property_name"]:
            continue
        try:
            val = float(r["value"])
        except (TypeError, ValueError):
            continue
        anchor = {
            "doi": r["doi"],
            "material": r["material_name"],
            "profile": material_to_profile(r["material_name"]),
            "value_mAh_g": val,
            "c_rate": r["c_rate"],
            "voltage": f"{r['voltage_min']}-{r['voltage_max']}",
            "loading_mg_cm2": r["mass_loading"],
            "scenario": r["scenario"],
        }
        is_strict = (
            r["scenario"] == "half_cell_test"
            and r["c_rate"] in ("0.1", "0.1C")
            and r["voltage_min"] == "2.8"
            and r["voltage_max"] == "4.3"
        )
        (strict if is_strict else loose).append(anchor)

    return strict, loose


def run_p2d_profile(profile: str, cache: Dict[str, Dict]) -> Dict:
    """跑指定 profile 的 P2D，结果缓存。失败时返回 fallback 标记。"""
    if profile in cache:
        return cache[profile]
    scheme = {"cathode": profile, "anode": "li_metal", "electrolyte": "lhce",
              "target_energy": 400}
    spec = candidates_scheme_to_cell_spec(scheme, scheme_id=f"{profile}|li_metal|lhce")
    fill_missing(spec)
    result = run_discharge(spec, c_rate=0.1)
    scalar = result["scalar"]
    cache[profile] = {
        "profile": profile,
        "pred_mAh_g": scalar.get("specific_capacity_mAh_g"),
        "fallback": scalar.get("fallback", False),
        "reason": scalar.get("reason", ""),
        "0th_energy": result.get("0th_order_energy_Wh_kg"),
        "note": MATERIAL_PROFILES.get(profile, {}).get("note", ""),
    }
    return cache[profile]


def _verdict(dev: float) -> str:
    if dev <= TOL["excellent"]:
        return "excellent"
    if dev <= TOL["ok"]:
        return "ok"
    if dev <= TOL["warning"]:
        return "warning"
    return "mismatch"


def compare_group(pred_mah_g: Optional[float], anchors: List[Dict]) -> List[Dict]:
    """组内逐点对比。pred 为 None（fallback）时标注 no_pred。"""
    out = []
    for a in anchors:
        lit = a["value_mAh_g"]
        if pred_mah_g is None:
            out.append({**a, "pred_mAh_g": None, "abs_dev_pct": None,
                        "signed_dev_pct": None, "verdict": "no_pred"})
            continue
        dev = abs(lit - pred_mah_g) / lit if lit else float("inf")
        signed = (lit - pred_mah_g) / lit * 100 if lit else 0.0
        out.append({
            **a,
            "pred_mAh_g": round(pred_mah_g, 1),
            "abs_dev_pct": round(dev * 100, 2),
            "signed_dev_pct": round(signed, 2),
            "verdict": _verdict(dev),
            "is_modified": _is_modified(a["material"]),
        })
    return out


def main() -> None:
    strict, loose = load_anchors(DATA_CSV)
    all_anchors = strict + loose
    print(f"严格锚点: {len(strict)} | 宽松锚点: {len(loose)} | 合计: {len(all_anchors)}")

    profiles_needed = sorted({a["profile"] for a in all_anchors})
    print("需要跑的 profile:", profiles_needed)

    cache: Dict[str, Dict] = {}
    report = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "condition": "0.1C, 2.8-4.3V vs Li/Li+（严格锚点）；宽松锚点为缺条件比容量点",
        "thresholds": TOL,
        "profiles": {},
        "points": [],
    }

    for profile in profiles_needed:
        p2d = run_p2d_profile(profile, cache)
        anchors = [a for a in all_anchors if a["profile"] == profile]
        cmp = compare_group(p2d["pred_mAh_g"], anchors)
        report["profiles"][profile] = {
            "pred_mAh_g": p2d["pred_mAh_g"],
            "fallback": p2d["fallback"],
            "reason": p2d["reason"][:200] if p2d["reason"] else "",
            "note": p2d["note"],
            "n_anchors": len(cmp),
            "points": cmp,
        }
        report["points"].extend(cmp)

    # 落盘
    json_path = OUT_DIR / "validation_report_multi.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    # 控制台摘要
    print("\n" + "=" * 68)
    for profile in profiles_needed:
        p2d = report["profiles"][profile]
        pred = p2d["pred_mAh_g"]
        line = (f"[{profile}] P2D 预测: "
                f"{pred if pred is not None else 'fallback(0阶)'} mAh/g")
        if p2d["note"]:
            line += f"  | {p2d['note']}"
        print(line)
        for c in p2d["points"]:
            tag = "改性" if c.get("is_modified") else "基准"
            if c["verdict"] == "no_pred":
                print(f"    {c['material'] or '(未标注)':12s} 文献={c['value_mAh_g']:7.2f} "
                      f"[无 P2D 预测]")
            else:
                print(f"    {c['material'] or '(未标注)':12s} 文献={c['value_mAh_g']:7.2f} "
                      f"偏差={c['signed_dev_pct']:+6.2f}% [{c['verdict']}] {tag}")
    print(f"\n报告已保存: {json_path}")
    print("=" * 68)


if __name__ == "__main__":
    main()
