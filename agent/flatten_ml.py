"""Flatten: 将归组后的结构展开为 ML-ready CSV（双表）"""

import ast
import json
import csv
import logging
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger("FlattenML")

# 属性级条件参数（属性 value dict 里除 value/unit 外的语义字段）→ CSV 列名
# 容量保持率必须带圈数、倍率性能必须带倍率，否则数值无意义。
PROP_COND_FIELD_MAP = {
    "cycle_number": "cycle_number",
    "cycle_range": "cycle_range",
    "scan_rate": "scan_rate",
    "c_rate": "prop_c_rate",
    "rate": "prop_rate",
    "voltage_range": "prop_voltage_range",
    "temperature": "prop_temperature",
    "current_density": "prop_current_density",
}


def _extract_val(item: Any) -> Any:
    if item is None:
        return None
    if isinstance(item, dict):
        return item.get("value", item)
    return item


def _extract_unit(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("unit", ""))
    return ""


def _normalize_property_value(pv: Any) -> tuple:
    """归一化属性 value 形态，返回 (value, unit)。

    处理 LLM 输出的三种不稳定形态：
    - 形态A: {"CIP": 40.7, "AGG": 0, "unit": "%"}          unit 混在 value 里
    - 形态B: {"value": {"CIP": 40.7, "AGG": 0}, "unit": "%"}  value 再套一层
    - 字符串: "{'CIP': 40.7, 'AGG': 0}"                    Python 风格 dict 字符串
    统一输出: value 为 dict/标量（不含 unit 键），unit 单独返回。
    """
    unit = ""
    extra = {}
    if isinstance(pv, dict):
        unit = str(pv.get("unit", ""))
        for src, dst in PROP_COND_FIELD_MAP.items():
            if src in pv:
                extra[dst] = pv[src]
        inner = pv.get("value", pv)
    else:
        inner = pv
    # 字符串形态：安全解析回 dict
    if isinstance(inner, str):
        s = inner.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                parsed = ast.literal_eval(s)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                inner = parsed
                if "unit" in parsed and not unit:
                    unit = str(parsed.get("unit", ""))
    # dict 形态：抽 unit 键，保证展平时不产生 _unit 行
    if isinstance(inner, dict):
        if not unit:
            unit = str(inner.get("unit", ""))
        inner = {k: v for k, v in inner.items() if k != "unit"}
    return inner, unit, extra


FLATTEN_CONDITIONED_HEADERS = [
    "doi", "material_id", "material_name",
    "canonical_condition_id", "scenario",
    "temperature", "temp_unit",
    "c_rate", "c_rate_unit",
    "current_density", "current_density_unit",
    "voltage_min", "voltage_max",
    "electrolyte", "electrode_config",
    "mass_loading", "mass_loading_unit",
    "test_method", "separator", "counter_electrode",
    "component", "property_type", "property_name",
    "cycle_number", "cycle_range", "scan_rate",
    "prop_c_rate", "prop_rate", "prop_voltage_range",
    "prop_temperature", "prop_current_density",
    "value", "unit",
]

FLATTEN_INTRINSIC_HEADERS = [
    "doi", "material_id", "material_name",
    "component", "property_type", "property_name",
    "value", "unit",
]


def flatten_to_rows(paper_result: Dict) -> (List[Dict], List[Dict]):
    """将一篇论文的归组结果展开为两张表

    Returns:
        (conditioned_rows, intrinsic_rows)
    """
    cond_rows = []
    intr_rows = []
    seen_cond = set()
    seen_intr = set()
    doi = paper_result.get("doi", "")

    for mat in paper_result.get("materials", []):
        mat_id = mat.get("material_id", "")
        mat_name = mat.get("material_name", mat_id)

        # 条件相关属性
        for cond_group in mat.get("conditions", []):
            cid = cond_group.get("canonical_id", "NO_CONDITION")
            scenario = cond_group.get("scenario", "")
            cond = cond_group.get("condition", {})

            keys = {
                "doi": doi, "material_id": mat_id, "material_name": mat_name,
                "canonical_condition_id": cid, "scenario": scenario,
                "temperature": _extract_val(cond.get("temperature")),
                "temp_unit": _extract_unit(cond.get("temperature")),
                "c_rate": _extract_val(cond.get("c_rate")),
                "c_rate_unit": _extract_unit(cond.get("c_rate")),
                "current_density": _extract_val(cond.get("current_density")),
                "current_density_unit": _extract_unit(cond.get("current_density")),
                "voltage_min": cond.get("voltage_range", {}).get("min") if isinstance(cond.get("voltage_range"), dict) else None,
                "voltage_max": cond.get("voltage_range", {}).get("max") if isinstance(cond.get("voltage_range"), dict) else None,
                "electrolyte": cond.get("electrolyte", ""),
                "electrode_config": cond.get("electrode_config", ""),
                "mass_loading": _extract_val(cond.get("mass_loading")),
                "mass_loading_unit": _extract_unit(cond.get("mass_loading")),
                "test_method": cond.get("test_method", ""),
                "separator": cond.get("separator", ""),
                "counter_electrode": cond.get("counter_electrode", ""),
            }

            for prop in cond_group.get("properties", []):
                component = prop.get("component", "")
                property_type = prop.get("property_type", "")
                property_name = prop.get("property_name", "")
                pv = prop.get("value", {})
                raw_val, unit, extra = _normalize_property_value(pv)
                if isinstance(raw_val, dict):
                    for sub_k, sub_v in raw_val.items():
                        if sub_k in ("unit",): continue
                        row = dict(keys)
                        row.update(extra)  # cycle_number / scan_rate 等属性级条件参数
                        row["component"] = component
                        row["property_type"] = property_type
                        row["property_name"] = f"{property_name}_{sub_k}"
                        if isinstance(sub_v, dict):
                            row["value"] = sub_v.get("value", sub_v)
                            row["unit"] = sub_v.get("unit", unit)
                        else:
                            row["value"] = sub_v
                            row["unit"] = unit
                        dedup_key = (doi, mat_id, cid, row["property_name"], str(row["value"]))
                        if dedup_key not in seen_cond:
                            seen_cond.add(dedup_key)
                            cond_rows.append(row)
                elif isinstance(raw_val, list) and raw_val and isinstance(raw_val[0], dict):
                    # 倍率性能等：value 是 [{rate, capacity, unit}, ...]，每个 rate 点一行
                    for item in raw_val:
                        row = dict(keys)
                        row.update(extra)
                        row["component"] = component
                        row["property_type"] = property_type
                        row["property_name"] = property_name
                        row["prop_c_rate"] = item.get("rate", "")
                        row["value"] = item.get("capacity", item.get("value", item))
                        row["unit"] = item.get("unit", unit)
                        dedup_key = (doi, mat_id, cid, property_name,
                                     str(row["prop_c_rate"]), str(row["value"]))
                        if dedup_key not in seen_cond:
                            seen_cond.add(dedup_key)
                            cond_rows.append(row)
                else:
                    row = dict(keys)
                    row.update(extra)  # cycle_number / scan_rate 等属性级条件参数
                    row["component"] = component
                    row["property_type"] = property_type
                    row["property_name"] = property_name
                    row["value"] = raw_val
                    row["unit"] = unit
                    dedup_key = (doi, mat_id, cid, property_name, str(raw_val))
                    if dedup_key not in seen_cond:
                        seen_cond.add(dedup_key)
                        cond_rows.append(row)

        # 本征属性
        for prop in mat.get("intrinsic_properties", []):
            pv = prop.get("value", {})
            raw_val, unit, _ = _normalize_property_value(pv)
            if isinstance(raw_val, dict):
                for sub_k, sub_v in raw_val.items():
                    if sub_k in ("unit",): continue
                    dedup_key = (doi, mat_id, f"{prop.get('property_name','')}_{sub_k}", str(sub_v))
                    if dedup_key not in seen_intr:
                        seen_intr.add(dedup_key)
                        intr_rows.append({
                            "doi": doi, "material_id": mat_id, "material_name": mat_name,
                            "component": prop.get("component", ""),
                            "property_type": prop.get("property_type", ""),
                            "property_name": f"{prop.get('property_name','')}_{sub_k}",
                            "value": sub_v, "unit": unit,
                        })
            else:
                dedup_key = (doi, mat_id, prop.get("property_name",""), str(raw_val))
                if dedup_key not in seen_intr:
                    seen_intr.add(dedup_key)
                    intr_rows.append({
                        "doi": doi, "material_id": mat_id, "material_name": mat_name,
                        "component": prop.get("component", ""),
                        "property_type": prop.get("property_type", ""),
                        "property_name": prop.get("property_name", ""),
                        "value": raw_val, "unit": unit,
                    })

    return cond_rows, intr_rows


def _write_csv_rows(rows: List[Dict], headers: List[str], output_path: Path, append: bool = False) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if append and output_path.exists():
        with open(output_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writerows(rows)
    else:
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)


def write_csv(all_results: List[Dict], output_dir: Path) -> None:
    """写入条件属性表 和 本征属性表"""
    all_cond = []
    all_intr = []
    for r in all_results:
        cond, intr = flatten_to_rows(r)
        all_cond.extend(cond)
        all_intr.extend(intr)

    if all_cond:
        _write_csv_rows(all_cond, FLATTEN_CONDITIONED_HEADERS,
                        output_dir / "_all_conditioned_data.csv")
        logger.info(f"条件属性表: {len(all_cond)} 行")
    if all_intr:
        _write_csv_rows(all_intr, FLATTEN_INTRINSIC_HEADERS,
                        output_dir / "_all_intrinsic_data.csv")
        logger.info(f"本征属性表: {len(all_intr)} 行")


def write_json(all_data: List[Dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    logger.info(f"JSON: {output_path}")
