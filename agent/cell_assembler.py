"""agent/cell_assembler.py — 电芯（cell）组装层

把 phase3 归组产物 + 归一化条件组装成"电芯"实体，电化学属性挂 cell：

    材料变体（工作电极） + 对电极 + 电解液配方 + 电池类型（scenario）
        → cell 实体
        → 电化学属性（capacity / CE / R_ct ...）挂 cell
    材料变体 → 本征属性（晶格参数 / 能带 ...）仍挂材料

对应知识图谱拓扑：
    材料变体节点 ──belongs_to──> cell 节点 ──measured_in──> 电化学属性
    本征属性直接挂材料变体节点

cell_id 基于组件组合（working + counter_electrode + electrolyte + scenario）哈希，
同一组件组合的不同测试协议共享 cell_id（properties 按条件组保留）。

用法：
    from agent.cell_assembler import assemble_cells
    cells = assemble_cells(merged["materials"], doi=doi)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _cell_id(working: str, cond: Dict, scenario: str) -> str:
    """基于组件组合的 cell id：组件变体 + scenario + 对电极 + 电解液 + 电压/电流窗口。"""
    key = "|".join([
        working or "",
        scenario or "",
        str(cond.get("counter_electrode_id") or cond.get("counter_electrode") or ""),
        str(cond.get("electrolyte_id") or cond.get("electrolyte") or ""),
        str(cond.get("voltage_range") or ""),
        str(cond.get("current_density") or ""),
    ])
    return "CELL_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def assemble_cells(materials: List[Dict], doi: str = "") -> List[Dict]:
    """phase3 materials -> cell 列表。

    cell 字段：
      cell_id / scenario / working_material(变体) / base_id / material_mods /
      counter_electrode / electrolyte / separator / condition / properties / doi
    本征属性不搬动（留在 materials 上）。
    """
    cells: List[Dict] = []
    for m in materials or []:
        if not isinstance(m, dict):
            continue
        mid = m.get("material_id", "")
        base = m.get("base_id", mid)
        mods = m.get("material_mods", {})
        for c in (m.get("conditions") or []):
            if not isinstance(c, dict):
                continue
            cond = c.get("condition") if isinstance(c.get("condition"), dict) else {}
            scenario = c.get("scenario", "")
            cell = {
                "cell_id": _cell_id(mid, cond, scenario),
                "scenario": scenario,
                "working_material": mid,
                "base_id": base,
                "material_mods": mods,
                "counter_electrode": cond.get("counter_electrode_id")
                                     or cond.get("counter_electrode", ""),
                "electrolyte": cond.get("electrolyte_id")
                               or cond.get("electrolyte", ""),
                "separator": cond.get("separator", ""),
                "condition": cond,
                "properties": c.get("properties") or [],
                "doi": doi,
            }
            cells.append(cell)
    return cells


def summarize_cells(cells: List[Dict]) -> Dict:
    """cell 汇总统计（调试/汇报用）。"""
    from collections import Counter
    return {
        "total_cells": len(cells),
        "by_scenario": dict(Counter(c.get("scenario", "") for c in cells)),
        "with_properties": sum(1 for c in cells if c.get("properties")),
        "working_materials": dict(Counter(c.get("working_material", "") for c in cells)),
    }


# ══════════════════════════════════════════════════════════════
# 自测（不依赖 API）：python agent/cell_assembler.py
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    materials = [
        {
            "material_id": "NCM811_Al2O3-coated",
            "base_id": "NCM811",
            "material_mods": {"coating": ["Al2O3"]},
            "intrinsic_properties": [
                {"property_name": "Lattice_Parameters", "value": {"a": 2.86}, "unit": "Å"},
            ],
            "conditions": [
                {
                    "canonical_id": "HASH_abc",
                    "scenario": "half_cell_test",
                    "condition": {
                        "electrolyte": "1M LiPF6 in EC/DEC",
                        "electrolyte_id": "LIPF6_DEC-EC",
                        "counter_electrode": "Li metal",
                        "counter_electrode_id": "li_metal",
                        "current_density": {"value": 0.5, "unit": "mA/cm2"},
                        "voltage_range": {"min": 2.8, "max": 4.3, "unit": "V"},
                    },
                    "properties": [
                        {"property_name": "Discharge_Specific_Capacity_Initial",
                         "value": {"value": 205, "unit": "mAh/g"}, "material_id": "NCM811_Al2O3-coated"},
                    ],
                },
                {
                    "canonical_id": "HASH_def",
                    "scenario": "half_cell_test",
                    "condition": {
                        "electrolyte": "1M LiPF6 in EC/DEC",
                        "electrolyte_id": "LIPF6_DEC-EC",
                        "counter_electrode": "Li metal",
                        "counter_electrode_id": "li_metal",
                        "current_density": {"value": 1.0, "unit": "mA/cm2"},
                    },
                    "properties": [
                        {"property_name": "Rate_Performance", "value": "data"},
                    ],
                },
            ],
        },
        {
            "material_id": "NCM811",
            "base_id": "NCM811",
            "material_mods": {},
            "conditions": [],
            "intrinsic_properties": [],
        },
    ]

    cells = assemble_cells(materials, doi="10.1016/j.test.2026.01")
    print(f"组装 cell 数: {len(cells)}（预期 2，无条件的材料不产出 cell）")
    fails = 0
    for c in cells:
        print(f"  {c['cell_id']} | {c['scenario']} | {c['working_material']} | "
              f"CE={c['counter_electrode']} | ELE={c['electrolyte']} | props={len(c['properties'])}")
        assert c["base_id"] == "NCM811"
        assert c["material_mods"].get("coating") == ["Al2O3"]
    # 同组件组合不同测试协议 → 不同 cell_id（协议区分）
    assert cells[0]["cell_id"] != cells[1]["cell_id"], "不同协议应生成不同 cell_id"
    # 相同组件组合相同协议 → 相同 cell_id
    c2 = assemble_cells([{"material_id": "NCM811", "base_id": "NCM811", "material_mods": {},
                          "conditions": [{"scenario": "half_cell_test", "condition": {
                              "electrolyte_id": "LIPF6_DEC-EC", "counter_electrode_id": "li_metal"}}]}])
    c3 = assemble_cells([{"material_id": "NCM811", "base_id": "NCM811", "material_mods": {},
                          "conditions": [{"scenario": "half_cell_test", "condition": {
                              "electrolyte_id": "LIPF6_DEC-EC", "counter_electrode_id": "li_metal"}}]}])
    assert c2[0]["cell_id"] == c3[0]["cell_id"], "相同配置应生成相同 cell_id"
    print(f"\n汇总: {summarize_cells(cells)}")
    print("全部通过" if not fails else f"{fails} 失败")
