"""能量密度估算模型 — 高比能液态锂电池设计方案系统（阶段 0）

两级估算：
  1. 材料级比能量 = 平均电压(V) × 比容量(mAh/g)   → Wh/kg(活性物质)
  2. 电芯级比能量 = 材料级 × active_ratio（活性物质占比 + N/P + 电解液/隔膜/集流体折算）

active_ratio 默认值来自工程经验区间（0.35–0.5）。BetterBat Cell Database
（TUM，393 个商业电芯）不含正极活性物质质量，无法直接回归折算系数；
改用按化学体系统计电芯级能量密度经验区间（calibrate_active_ratios 产出），
供 check_energy_claim 做量级校验。校准结果落盘 data/calibrated/energy_ranges.json。

可靠性分层：本模块属于「规则层」的第一环，无 LLM 依赖。
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Dict, Optional

# 各负极体系的电芯级折算系数（工程经验初值，软包高比能电池）
DEFAULT_ACTIVE_RATIOS: Dict[str, float] = {
    "graphite": 0.50,   # 石墨负极工艺成熟，活性占比高
    "si_base": 0.45,    # 硅基需预留膨胀空间
    "li_metal": 0.50,   # 金属锂比容量极高(3860 mAh/g)，超薄锂箔软包电芯折算系数达 0.48-0.52
    "default": 0.48,
}

# 能量密度声称值与估算值的最大允许偏差（>此值判 energy_mismatch）
ENERGY_TOLERANCE = 0.35


CALIBRATED_DIR = Path(__file__).resolve().parent / "data" / "calibrated"


def estimate_material_energy(avg_voltage: float, capacity_mah_g: float) -> float:
    """材料级比能量 (Wh/kg 活性物质)。V × mAh/g = mWh/g = Wh/kg。"""
    return avg_voltage * capacity_mah_g   # V × mAh/g = mWh/g = Wh/kg


def estimate_cell_energy(
    avg_voltage: float,
    capacity_mah_g: float,
    active_ratio: float = 0.45,
) -> float:
    """电芯级比能量一级估算 (Wh/kg)。"""
    return estimate_material_energy(avg_voltage, capacity_mah_g) * active_ratio


def estimate_scheme_energy(
    scheme: Dict,
    candidates: Dict,
    active_ratios: Optional[Dict[str, float]] = None,
) -> Optional[float]:
    """给定材料组合方案，估算电芯级能量密度。

    Args:
        scheme: {"cathode": "NCM811", "anode": "li_metal", ...}
        candidates: candidates.json 内容（含 avg_voltage / capacity）
        active_ratios: 折算系数表，默认 DEFAULT_ACTIVE_RATIOS

    Returns:
        估算的电芯级能量密度 (Wh/kg)；材料不在候选表时返回 None。
    """
    ratios = active_ratios or DEFAULT_ACTIVE_RATIOS
    cathode_id = scheme.get("cathode")
    anode_id = scheme.get("anode")
    cathode = _find_material(candidates, "cathode", cathode_id)
    if not cathode or not cathode.get("capacity") or not cathode.get("avg_voltage"):
        return None
    ratio = ratios.get(anode_id or "", ratios["default"])
    return estimate_cell_energy(
        cathode["avg_voltage"], cathode["capacity"]["value"], ratio
    )


def check_energy_claim(claimed: float, estimated: float) -> str:
    """对比声称值与估算值，返回 ok / energy_mismatch。"""
    if estimated <= 0:
        return "energy_mismatch"
    err = abs(claimed - estimated) / estimated
    return "ok" if err <= ENERGY_TOLERANCE else "energy_mismatch"


def check_energy_in_range(claimed: float, chemistry: str,
                          ranges: Optional[Dict] = None) -> str:
    """声称值是否落在体系经验区间内（BetterBat 校准产物）。

    Returns: ok / below_range / above_range / unknown
    """
    if not ranges:
        try:
            ranges = load_calibrated_ranges()
        except Exception:
            return "unknown"
    r = ranges.get(chemistry)
    if not r:
        return "unknown"
    if claimed < r["min"]:
        return "below_range"
    if claimed > r["max"]:
        return "above_range"
    return "ok"


def calibrate_active_ratios(xlsx_path: str) -> Optional[Dict]:
    """用 BetterBat Cell Database 校准电芯级能量密度经验区间。

    BetterBat 只含电芯级规格（化学体系/grav_ED/电压/容量/重量），
    没有正极活性物质质量，因此不回归 material→cell 折算系数，
    而是按化学体系（默认石墨负极）统计 grav_ED 的 min/median/max，
    作为能量密度声称值的量级校验区间。

    Args:
        xlsx_path: CellDatabase_v6.xlsx 路径（data/raw/ 下）

    Returns:
        {"ranges": {chemistry: {"min","median","max","n"}}, "n_cells": N}
        并落盘 data/calibrated/energy_ranges.json。
        数据不可用时返回 None（调用方回退默认行为）。
    """
    try:
        import openpyxl  # type: ignore
    except ImportError:
        print("[energy_model] 未安装 openpyxl（pip install openpyxl），跳过校准")
        return None
    try:
        wb = openpyxl.load_workbook(xlsx_path, read_only=True)
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    except Exception as e:
        print(f"[energy_model] BetterBat 文件加载失败: {e}")
        return None
    if not rows:
        return None

    header = [str(h) if h else "" for h in rows[0]]
    def col_of(*keys):
        for i, h in enumerate(header):
            if any(k in h for k in keys):
                return i
        return None
    c_chem = col_of("Chemistry")
    c_ged = col_of("grav. Energy")
    if c_chem is None or c_ged is None:
        print("[energy_model] BetterBat 列名未识别，跳过校准")
        return None

    groups: Dict[str, list] = {}
    for row in rows[1:]:
        chem = str(row[c_chem]).strip() if row[c_chem] else ""
        ged = row[c_ged]
        if not chem or ged is None:
            continue
        try:
            v = float(ged)
        except (TypeError, ValueError):
            continue
        if v <= 0 or v > 2000:  # 异常值过滤
            continue
        groups.setdefault(chem, []).append(v)
    wb.close()

    ranges = {}
    n_cells = 0
    for chem, vals in sorted(groups.items()):
        n_cells += len(vals)
        ranges[chem] = {
            "min": round(min(vals), 1),
            "median": round(statistics.median(vals), 1),
            "max": round(max(vals), 1),
            "n": len(vals),
        }
    CALIBRATED_DIR.mkdir(parents=True, exist_ok=True)
    out = CALIBRATED_DIR / "energy_ranges.json"
    payload = {
        "source": "betterbat CellDatabase_v6",
        "note": "电芯级 grav. Energy Density (Wh/kg) 经验区间，默认石墨负极体系；"
                "min/max 为观测极值，median 为体系典型值。",
        "ranges": ranges,
        "n_cells": n_cells,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[energy_model] 校准完成: {n_cells} 电芯 → {out}")
    for chem, r in ranges.items():
        print(f"  {chem:12s} n={r['n']:3d}  min={r['min']:6.1f}  "
              f"median={r['median']:6.1f}  max={r['max']:6.1f} Wh/kg")
    return {"ranges": ranges, "n_cells": n_cells}


def load_calibrated_ranges() -> Dict:
    """加载校准区间（data/calibrated/energy_ranges.json）。"""
    p = CALIBRATED_DIR / "energy_ranges.json"
    return json.loads(p.read_text(encoding="utf-8")).get("ranges", {})


def _find_material(candidates: Dict, category: str, material_id: str) -> Optional[Dict]:
    for item in candidates.get(category, []):
        if item.get("id") == material_id:
            return item
    return None


def load_candidates(data_dir: Path) -> Dict:
    return json.loads((data_dir / "candidates.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    data_dir = Path(__file__).resolve().parent / "data"
    cands = load_candidates(data_dir)
    for m in cands["cathode"]:
        mat_e = estimate_material_energy(m["avg_voltage"], m["capacity"]["value"])
        print(f"{m['id']:8s} 材料级 {mat_e:7.1f} Wh/kg   "
              f"石墨体系电芯级 {estimate_cell_energy(m['avg_voltage'], m['capacity']['value'], 0.50):6.1f} Wh/kg")
    e = estimate_scheme_energy({"cathode": "LRMO", "anode": "li_metal"}, cands)
    print(f"LRMO + li_metal 方案电芯级估算: {e:.1f} Wh/kg")
    print("声称值校验: 450 vs 估算",
          check_energy_claim(450, estimate_scheme_energy(
              {"cathode": "LRMO", "anode": "li_metal"}, cands) or 0))
