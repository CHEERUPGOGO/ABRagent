# -*- coding: utf-8 -*-
"""clean_extracted.py — 提取后数据清洗（数值异常值过滤 + 单位归一化 + 质量报告）

定位：miner/agent 挖掘流水线的**末端数据治理层**。
现有 pipeline 只有文本清洗（agent/clean_agent.py）和结构清洗
（miner/extraction_core/postprocess.py），缺少**数值异常值过滤**——
本模块补上这一环。

功能：
  1. 单位归一化：同属性多单位（mS/cm vs S/cm、mV vs V、nm vs Å vs μm…）统一到基准单位
  2. 物理量级校验：比容量 / 保持率 / 扩散系数 / 电导率 / 电压 / 载量 … 合理范围检查
  3. 质量报告：规则命中统计 + 被拒条目样例（可审计）
  4. 两种模式：flag（默认，标注不删） / drop（删除异常条目）

用法：
  # 批量清洗存量 extracted JSON（输出到新目录）
  python -m miner.data_quality.clean_extracted miner/json/100 --out miner/json_clean/
  # 清洗扁平化 CSV
  python -m miner.data_quality.clean_extracted --csv agent/output/rag_clean/_all_conditioned_data.csv --out agent/output/rag_clean_clean/
  # 单文件（flag 模式，stdout 打印报告）
  python -m miner.data_quality.clean_extracted miner/json/100/xxx_extracted.json

接入流水线末端（增量）：
  from miner.data_quality.clean_extracted import clean_extracted_file
  data, report = clean_extracted_file(json_path, drop=False)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ══════════════════════════════════════════════════════════════
# 1. 属性校验规则表
#    (正则, 基准单位, min, max, 说明, 适用组件)
#    component: None=通用 / "cathode" / "anode"（不匹配则跳过该规则）
#    数值先换算到基准单位，再检查范围。无法识别单位 → 跳过（不误杀）。
# ══════════════════════════════════════════════════════════════

RULES: List[Tuple[str, str, float, float, str, Optional[str]]] = [
    # ── 比容量类（mAh/g；1 Ah/kg ≡ 1 mAh/g；负极合金可达数千）──
    (r"Discharge_Specific_Capacity|Rate_Capability|First_Lithiation|"
     r"Specific_Capacity|Charge_Specific_Capacity|Reversible_Capacity",
     "mAh/g", 0, 600, "比容量(正极)", "cathode"),
    (r"Discharge_Specific_Capacity|Rate_Capability|First_Lithiation|"
     r"Specific_Capacity|Charge_Specific_Capacity|Reversible_Capacity",
     "mAh/g", 0, 5000, "比容量(负极合金)", "anode"),
    (r"Discharge_Specific_Capacity|Rate_Capability|First_Lithiation|"
     r"Specific_Capacity|Charge_Specific_Capacity|Reversible_Capacity",
     "mAh/g", 0, 5000, "比容量", None),
    (r"Theoretical_Specific_Capacity", "mAh/g", 0, 5000, "理论比容量", None),
    # ── 能量密度 ──
    (r"Energy_Density|Gravimetric_Energy|Volumetric_Energy", "Wh/kg", 0, 2000, "能量密度", None),
    # ── 保持率 / 效率（%）──
    (r"Capacity_Retention|Retention", "%", 0, 100, "容量保持率", None),
    (r"Coulombic_Efficiency|Initial_Coulombic", "%", 0, 120, "库仑效率/首效", None),
    (r"Rate_Recovery", "%", 0, 120, "倍率恢复", None),
    (r"Pseudocapacitive_Contribution", "%", 0, 100, "赝电容贡献", None),
    (r"Unit_Cell_Volume_Change", "%", 0, 100, "晶胞体积变化", None),
    # ── 面容量 ──
    (r"Areal_Capacity|Deposition_Capacity", "mAh/cm2", 0, 100, "面容量", None),
    # ── 电压类（V；mV ÷1000；窗口/起始电位允许负还原侧）──
    (r"Stability_Window|Onset_Potential|Potential_of_Zero", "V", -3, 6, "电化学窗口/电位", None),
    (r"Voltage_Hysteresis|Overpotential|Phase_Transition_Voltage|Open_Circuit_Voltage|"
     r"Nominal_Discharge_Voltage|Voltage|Potential|Operating_Voltage|"
     r"Charge_Plateau|Discharge_Plateau", "V", 0, 6, "电压/过电位", None),
    # ── 电导率（基准 mS/cm；固态电解质可低至 1e-7 S/cm = 1e-4 mS/cm）──
    (r"Ionic_Conductivity|Conductivity", "mS/cm", 1e-4, 500, "离子电导率", None),
    # ── 扩散系数（基准 cm²/s；m²/s ×1e4）──
    (r"Diffusion_Coefficient", "cm2/s", 1e-12, 1e-8, "扩散系数", None),
    # ── 阻抗（Ω；mΩ ÷1000，kΩ ×1000）──
    (r"Resistance|Impedance", "ohm", 0, 10000, "阻抗", None),
    # ── 规格参数 ──
    (r"Mass_Loading", "mg/cm2", 0.1, 50, "面载量", None),
    (r"Coating_Thickness", "um", 1, 500, "涂层厚度", None),
    (r"Electrode_Thickness", "um", 1, 500, "电极厚度", None),
    (r"Porosity", "frac", 0, 1, "孔隙率", None),
    (r"N_P_Ratio|NP_Ratio", "frac", 0, 10, "N/P 比", None),
    (r"Active_Material_Mass_Fraction", "frac", 0, 1, "活性物质质量分数", None),
    (r"Compacted_Density", "g/cm3", 0, 10, "压实密度", None),
    # ── 粒径 / 膜厚 / 层间距（基准 nm；μm ×1000，Å ×0.1）──
    (r"Particle_Size|Crystallite_Size|Interlayer_Spacing|SEI_Thickness|CEI_Thickness|"
     r"Surface_.*Layer", "nm", 0.01, 1e5, "粒径/膜厚/层间距", None),
    (r"Secondary_Particle_Size", "nm", 10, 1e5, "二次粒径", None),
    # ── 循环寿命 ──
    (r"Cycle_Life|Cycle_Number|cycle_number", "cycles", 0, 100000, "循环寿命", None),
    # ── 交换电流密度 ──
    (r"Exchange_Current_Density", "mA/cm2", 1e-6, 1e4, "交换电流密度", None),
    # ── 温度（热失控/自热可达数百°C）──
    (r"Thermal_Runaway|Maximum.*Temperature|Self_Heating", "C", 0, 800, "热失控/自热温度", None),
    (r"Temperature|thermal.*Temperature|Onset_Temperature", "C", -50, 200, "温度", None),
    # ── 倍率 / 电流 ──
    (r"c_rate|C_rate|Rate", "C", 0, 1000, "倍率", None),
    (r"current_density", "mA/g", 0, 1e6, "电流密度", None),
    # ── 能量类（eV；结合能/能级可为负）──
    (r"Band_Gap|Migration_Barrier|Binding_Energy|Formation_Energy|"
     r"HOMO_LUMO|Activation_Energy|Vacancy_Formation", "eV", -30, 30, "能量(eV)", None),
    (r"Viscosity", "mPa.s", 1e-3, 1e5, "粘度", None),
]

# ══════════════════════════════════════════════════════════════
# 2. 单位 → 基准单位换算
#    (识别串, 基准单位, 系数)  value_base = value × 系数
#    K → C 特殊（减 273.15）；% → 分数特殊（÷100）
# ══════════════════════════════════════════════════════════════

UNIT_CONV: Dict[str, Tuple[str, float]] = {
    # 比容量
    "mah/g": ("mAh/g", 1.0), "ah/kg": ("mAh/g", 1.0),
    "mah/kg": ("mAh/g", 1.0),
    # 能量密度
    "wh/kg": ("Wh/kg", 1.0), "wh/l": ("Wh/L", 1.0),
    # 保持率/效率
    "%": ("%", 1.0), "ratio": ("%", 100.0),
    # 面容量
    "mah/cm2": ("mAh/cm2", 1.0), "mah/cm^2": ("mAh/cm2", 1.0),
    "ah/m2": ("mAh/cm2", 0.1),
    # 电压
    "v": ("V", 1.0), "mv": ("V", 0.001),
    "v vs. li/li+": ("V", 1.0), "v vs. li/li⁺": ("V", 1.0),
    "v vs li/li+": ("V", 1.0),
    # 电导率
    "ms/cm": ("mS/cm", 1.0), "s/cm": ("mS/cm", 1000.0),
    "s/m": ("mS/cm", 10.0),
    # 扩散
    "cm2/s": ("cm2/s", 1.0), "cm²/s": ("cm2/s", 1.0),
    "m2/s": ("cm2/s", 1e4), "m²/s": ("cm2/s", 1e4),
    # 阻抗
    "ω": ("ohm", 1.0), "ohm": ("ohm", 1.0), "ω cm2": ("ohm", 1.0),
    "ohm cm2": ("ohm", 1.0), "kω": ("ohm", 1000.0),
    "kohm": ("ohm", 1000.0), "mω": ("ohm", 1e-3),
    # 面载量
    "mg/cm2": ("mg/cm2", 1.0), "mg/cm^2": ("mg/cm2", 1.0),
    "kg/m2": ("mg/cm2", 100.0),
    # 长度
    "nm": ("nm", 1.0), "μm": ("nm", 1000.0), "um": ("nm", 1000.0),
    "å": ("nm", 0.1), "a": ("nm", 0.1), "mm": ("nm", 1e6),
    # 温度
    "°c": ("C", 1.0), "c": ("C", 1.0),
    # 密度
    "g/cm3": ("g/cm3", 1.0), "g/cm³": ("g/cm3", 1.0),
    # 电流
    "ma/g": ("mA/g", 1.0), "ma/cm2": ("mA/cm2", 1.0), "a/m2": ("mA/cm2", 0.1),
    # 能量
    "ev": ("eV", 1.0), "kj/mol": ("eV", 0.010364),  # 1 kJ/mol ≈ 0.010364 eV
    "mev": ("eV", 0.001),
    # 粘度
    "mpa·s": ("mPa.s", 1.0), "mpa.s": ("mPa.s", 1.0), "cp": ("mPa.s", 1.0),
    # 时间
    "h": ("h", 1.0), "hr": ("h", 1.0), "hours": ("h", 1.0),
    "s": ("h", 1 / 3600.0), "min": ("h", 1 / 60.0),
    "cycles": ("cycles", 1.0), "cycle": ("cycles", 1.0),
    # 分数
    "mole fraction": ("frac", 1.0), "mol fraction": ("frac", 1.0),
    "weight ratio": ("frac", 1.0), "wt%": ("frac", 0.01),
    # 空单位 → 原样
    "": ("", 1.0),
}


def _norm_unit(unit: str) -> str:
    return (unit or "").strip().lower().replace(" ", "")


def to_base_value(value: float, unit: str, base_unit: str) -> Tuple[Optional[float], Optional[str]]:
    """数值换算到基准单位。返回 (value_base, warning)。无法识别单位返回 (None, reason)。"""
    u = _norm_unit(unit)
    if u in UNIT_CONV:
        conv_base, factor = UNIT_CONV[u]
        if conv_base != base_unit:
            return None, f"单位 {unit} 不属于本属性基准单位 {base_unit}"
        return value * factor, None
    # 特殊：K → C
    if u in ("k", "kelvin") and base_unit == "C":
        return value - 273.15, None
    if u in ("%",) and base_unit == "frac":
        return value / 100.0, None
    if u == "":
        return value, None  # 无单位按数值本身
    return None, f"未知单位 '{unit}'，跳过校验"


_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
_SCI_RE = re.compile(r"([-+]?\d*\.?\d+)\s*[×x*]\s*10\s*\^\s*\{?([-+]?\d+)\}?")
_POW_RE = re.compile(r"10\s*\^\s*\{?([-+]?\d+)\}?")


def _extract_number(value: Any) -> Optional[float]:
    """从 value（数字/字符串/dict）提取第一个数值。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.replace(",", "").strip()
        # 科学计数法文本："5.1 × 10^{-7}" / "10^{-13}" / "3×10^5"
        m = _SCI_RE.search(s)
        if m:
            return float(m.group(1)) * 10 ** int(m.group(2))
        m = _POW_RE.search(s)
        if m:
            return 10 ** int(m.group(1))
        m = _NUM_RE.search(s)
        return float(m.group()) if m else None
    if isinstance(value, dict):
        # 取第一个数值子值（如 {"D50": 0.2}）
        for v in value.values():
            r = _extract_number(v)
            if r is not None:
                return r
    return None


def check_property(label: str, value: Any, unit: str,
                   component: str = "") -> Tuple[bool, Optional[str], Optional[float]]:
    """单条属性校验。返回 (ok, reason, value_base)。
    规则未命中（属性不在规则表）→ (True, None, None) 视为通过。
    """
    num = _extract_number(value)
    if num is None:
        return True, None, None  # 非数值（化学式、文本）跳过
    for pat, base_unit, lo, hi, desc, comp in RULES:
        if comp is not None and comp != component:
            continue
        if not re.search(pat, label):
            continue
        v_base, warn = to_base_value(num, unit, base_unit)
        if warn:
            return True, warn, None  # 单位无法换算 → 不误杀，记录 warning
        if v_base is None:
            return True, None, None
        if not (lo <= v_base <= hi):
            return False, (f"{desc} '{label}' = {value}{unit} → {v_base:.6g}{base_unit} "
                           f"超出合理范围 [{lo}, {hi}]"), v_base
        return True, None, v_base
    return True, None, None


# ══════════════════════════════════════════════════════════════
# 3. 清洗入口
# ══════════════════════════════════════════════════════════════

def _clean_dict_entries(entries: Any, label: str, issues: List[Dict],
                        stats: Dict[str, int], component: str = "") -> Any:
    """递归清洗 performance_info/extracted_info 的条目列表。"""
    if not isinstance(entries, list):
        return entries
    cleaned = []
    for e in entries:
        if not isinstance(e, dict):
            cleaned.append(e)
            continue
        e = dict(e)
        if "value" in e:
            ok, reason, _ = check_property(label, e.get("value"), e.get("unit", ""), component)
            if not ok:
                stats["dropped"] += 1
                issues.append({"label": label, "value": e.get("value"),
                               "unit": e.get("unit", ""), "reason": reason,
                               "source_text": str(e.get("source_text", ""))[:120]})
                continue  # drop 模式：直接丢弃异常条目
        cleaned.append(e)
    return cleaned


def clean_extracted_file(path: Path, drop: bool = False,
                         ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """清洗单个 extracted JSON。返回 (data, report)。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    component = str(data.get("component", ""))
    stats = {"files": 1, "items_checked": 0, "dropped": 0, "unknown_unit": 0}
    issues: List[Dict] = []
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        stats["items_checked"] += 1
        for key in ("performance_info", "extracted_info"):
            info = item.get(key)
            if not isinstance(info, dict):
                continue
            for label, entries in list(info.items()):
                info[label] = _clean_dict_entries(entries, label, issues, stats, component)
        # 条件里的数值（temperature / voltage_range / c_rate 等）
        for cond in item.get("conditions", []):
            if not isinstance(cond, dict):
                continue
            for k, v in list(cond.items()):
                if k in ("temperature", "c_rate", "current_density") and \
                        isinstance(v, dict) and "value" in v:
                    ok, reason, _ = check_property(k, v["value"], v.get("unit", ""), component)
                    if not ok and drop:
                        stats["dropped"] += 1
                        issues.append({"label": k, "value": v["value"],
                                       "unit": v.get("unit", ""), "reason": reason,
                                       "source_text": ""})
    if drop:
        data["_quality"] = {"mode": "drop", "dropped": stats["dropped"]}
    else:
        data["_quality"] = {"mode": "flag", "issues": issues}
    report = {
        "file": str(path), "mode": "drop" if drop else "flag",
        **stats, "n_issues": len(issues),
        "issue_samples": issues[:20],
        "issue_rule_dist": _rule_dist(issues),
    }
    return data, report


def clean_csv_file(path: Path, drop: bool = False) -> Tuple[List[Dict], Dict[str, Any]]:
    """清洗扁平化 CSV（_all_conditioned_data.csv）。返回 (rows, report)。"""
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    stats = {"files": 1, "items_checked": len(rows), "dropped": 0}
    issues: List[Dict] = []
    kept: List[Dict] = []
    for r in rows:
        ok, reason, _ = check_property(r.get("property_name", ""),
                                       r.get("value", ""), r.get("unit", ""),
                                       r.get("component", ""))
        if not ok:
            stats["dropped"] += 1
            issues.append({"label": r.get("property_name"), "value": r.get("value"),
                           "unit": r.get("unit"), "reason": reason,
                           "source_text": r.get("material_name", "")})
            if drop:
                continue
        kept.append(r)
    report = {"file": str(path), "mode": "drop" if drop else "flag",
              **stats, "n_issues": len(issues),
              "issue_samples": issues[:20], "issue_rule_dist": _rule_dist(issues)}
    return kept, report


def _rule_dist(issues: List[Dict]) -> Dict[str, int]:
    dist: Dict[str, int] = {}
    for i in issues:
        reason = i.get("reason", "")
        key = reason.split("'")[0] if reason else "unknown"
        dist[key] = dist.get(key, 0) + 1
    return dist


def _print_report(report: Dict[str, Any]) -> None:
    print(f"文件: {report['file']}")
    print(f"模式: {report['mode']} | 条目: {report.get('items_checked', 0)} | "
          f"异常: {report.get('n_issues', 0)} | 删除: {report.get('dropped', 0)}")
    dist = report.get("issue_rule_dist", {})
    if dist:
        print("异常按规则分布:")
        for k, v in sorted(dist.items(), key=lambda x: -x[1]):
            print(f"  {k}: {v}")
    for s in report.get("issue_samples", [])[:8]:
        print(f"  ✗ {s.get('label')} = {s.get('value')}{s.get('unit')} — {s.get('reason')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="miner 提取后数值清洗")
    ap.add_argument("target", nargs="?", help="extracted JSON 文件或目录")
    ap.add_argument("--out", default=None, help="输出目录/文件（不指定则仅打印报告）")
    ap.add_argument("--drop", action="store_true", help="删除异常条目（默认 flag 标注）")
    ap.add_argument("--csv", action="store_true", help="目标为 CSV 文件")
    args = ap.parse_args()

    if args.csv:
        rows, report = clean_csv_file(Path(args.target), drop=args.drop)
        _print_report(report)
        if args.out:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True) if out.suffix == "" else None
            dest = out if out.suffix == ".csv" else out / Path(args.target).name
            with open(dest, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [])
                w.writeheader()
                w.writerows(rows)
            print(f"已保存: {dest}")
        return

    target = Path(args.target)
    files = [target] if target.is_file() else sorted(target.rglob("*_extracted.json"))
    if not files:
        print("未找到 extracted JSON 文件")
        return
    total = {"files": 0, "items_checked": 0, "dropped": 0, "n_issues": 0}
    out_dir = Path(args.out) if args.out else None
    for fp in files:
        data, report = clean_extracted_file(fp, drop=args.drop)
        for k in ("files", "items_checked", "dropped", "n_issues"):
            total[k] += report.get(k, 0)
        if out_dir:
            dest = out_dir / fp.relative_to(target if target.is_dir() else target.parent)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        if len(files) <= 10:
            _print_report(report)
    print(f"\n=== 汇总: {total['files']} 文件 | {total['items_checked']} 条目 | "
          f"{total['n_issues']} 异常 | 删除 {total['dropped']} ===")


if __name__ == "__main__":
    main()