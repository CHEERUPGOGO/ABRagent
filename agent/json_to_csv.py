"""JSON → CSV 转换工具（独立于 pipeline，可直接对已有 JSON 结果使用）"""

import json
import csv
import logging
import sys
from pathlib import Path
from typing import List, Dict, Any, Tuple

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("json2csv")


# ── 字段提取 ──

def _extract_val(item: Any) -> Any:
    if isinstance(item, dict):
        return item.get("value", item)
    return item


def _extract_unit(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("unit", ""))
    return ""


# ── CSV 表头 ──

CONDITIONED_HEADERS = [
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
    "value", "unit",
]

INTRINSIC_HEADERS = [
    "doi", "material_id", "material_name",
    "component", "property_type", "property_name",
    "value", "unit",
]


def flatten_paper(paper: Dict) -> Tuple[List[Dict], List[Dict]]:
    """将一篇论文的归组 JSON 展开为 (conditioned_rows, intrinsic_rows)。

    统一委托 flatten_ml.flatten_to_rows，保证 dict value 展平、
    归一化与去重行为与主 pipeline 完全一致（含 CIP/AGG 这类对象值）。
    """
    from agent.flatten_ml import flatten_to_rows
    return flatten_to_rows(paper)


def convert_json_to_csv(input_path: Path, output_dir: Path = None) -> None:
    """将单个 JSON 文件或目录下的所有 JSON 转换为 CSV"""
    if input_path.is_dir():
        json_files = sorted(input_path.rglob("*.json"))
    elif input_path.is_file():
        json_files = [input_path]
    else:
        logger.error(f"路径不存在: {input_path}")
        return

    all_cond = []
    all_intr = []

    for jf in json_files:
        try:
            with open(jf, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"  跳过 {jf.name}: {e}")
            continue

        if isinstance(data, dict):
            data = [data]

        for paper in data:
            cond, intr = flatten_paper(paper)
            all_cond.extend(cond)
            all_intr.extend(intr)

        logger.info(f"  {jf.name}: {len(cond)} 条件属性, {len(intr)} 本征属性")

    if not all_cond and not all_intr:
        logger.warning("未找到任何数据")
        return

    out_dir = output_dir or input_path if input_path.is_dir() else input_path.parent
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if all_cond:
        csv_path = out_dir / "_all_conditioned_data.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CONDITIONED_HEADERS)
            w.writeheader()
            w.writerows(all_cond)
        logger.info(f"\n条件属性: {len(all_cond)} 行 → {csv_path}")

    if all_intr:
        csv_path = out_dir / "_all_intrinsic_data.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=INTRINSIC_HEADERS)
            w.writeheader()
            w.writerows(all_intr)
        logger.info(f"本征属性: {len(all_intr)} 行 → {csv_path}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="JSON → CSV 转换（agent 输出 → ML-ready 数据表）")
    p.add_argument("input", help="输入 JSON 文件或目录")
    p.add_argument("-o", "--output", help="输出目录（默认同输入目录）")
    args = p.parse_args()
    convert_json_to_csv(Path(args.input), Path(args.output) if args.output else None)
