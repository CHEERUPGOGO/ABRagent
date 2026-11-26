"""按电极、物理量和工况核对方案声明，供几何生成、提交和报告共用。"""
from __future__ import annotations

import copy
import math
import unicodedata


UNITS = {"condition.v_min": "V", "condition.v_max": "V", "condition.c_rate": "C"}
ALIASES = {"condition.voltage.lower": "condition.v_min", "condition.voltage.upper": "condition.v_max",
           "condition.voltage_min": "condition.v_min", "condition.voltage_max": "condition.v_max",
           "condition.voltage_window_lower": "condition.v_min", "condition.voltage_window_upper": "condition.v_max"}
for electrode in ("cathode", "anode"):
    for field, unit in (("L_um", "um"), ("porosity", ""), ("R_s_um", "um"),
                        ("loading_mg_cm2", "mg/cm2"), ("compaction_g_cm3", "g/cm3")):
        UNITS[f"{electrode}.{field}"] = unit
    for alias, field in (("electrode_thickness", "L_um"), ("thickness_um", "L_um"),
                         ("total_loading", "loading_mg_cm2"), ("compaction_density", "compaction_g_cm3"),
                         ("compaction_density_g_cm3", "compaction_g_cm3")):
        ALIASES[f"{electrode}.{alias}"] = f"{electrode}.{field}"

from .parameter_names import ALIASES as NAME_ALIASES
ALIASES.update({alias: key for alias, key in NAME_ALIASES.items() if key in UNITS})

INSTRUCTIONS = "已定主组参数及来源见fixed_geometry。只为缺项提出候选。窄窗对照、化成和测氧等专属参数保留各自用途。"


def identity(path, row):
    """主声明默认归主组。带工况的开放字段需明确标注用途才可跨来源归并。"""
    key = row.get("canonical_path") or path
    key = ALIASES.get(key, key)
    if key not in UNITS:
        return None
    from .parameter_presentation import semantic_conditions

    conditions = row.get("conditions") or {}
    purpose = row.get("purpose")
    if isinstance(conditions, list):
        purpose_entries = [item for item in conditions if isinstance(item, dict) and item.get('name') in ('用途', 'purpose')]
        if len(purpose_entries) == 1 and not purpose:
            purpose = purpose_entries[0].get('value')
            conditions = [item for item in conditions if item is not purpose_entries[0]]
    elif isinstance(conditions, dict):
        conditions = dict(conditions)
        purpose = purpose or conditions.pop('purpose', None) or conditions.pop('用途', None)
    if not purpose or purpose in ("主循环", "主测试", "主方案", "主组", "main_cycle", "primary"):
        purpose = "main"
    if path.endswith(".electrode_thickness"):
        description = str(row.get("meaning", "")) + str(row.get("display_label", ""))
        description = description.replace("不含集流体", "").replace("不含箔", "")
        if any(word in description for word in ("湿膜", "含箔", "含集流体", "双面总厚")):
            return None
    sample = row.get("group_id") or row.get("scope") or "main"
    return key, str(sample), str(purpose), semantic_conditions(conditions)


def normalized_unit(unit):
    return unicodedata.normalize("NFKC", unit or "").replace("μ", "u").replace("^", "")


def physical_declarations(parameters):
    """返回主工况有效字段及全部冲突。保留来源，不用后写入的候选覆盖已有声明。"""
    from .parameter_names import foil_material_errors
    seen, errors = {}, foil_material_errors(parameters)
    for path, row in parameters.items():
        if not isinstance(row, dict) or row.get("value") is None:
            continue
        if row.get("quantity_role", "value") != "value" or row.get("basis") in ("literature", "literature_measurement"):
            continue
        from .parameter_names import checked_name
        try:
            checked_name(path, row)
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))
            continue
        group = identity(path, row)
        if not group:
            continue
        value = row['value']
        unit = normalized_unit(row.get('unit', UNITS[group[0]]))
        source = f"{path}={value} {unit}（{row.get('source') or '方案表'}）"
        if unit != UNITS[group[0]] or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            errors.append(f"物理量单位或数值待确认：{source}，规范单位 {UNITS[group[0]]}")
            continue
        previous = seen.get(group)
        if previous and not math.isclose(previous['value'], value, rel_tol=1e-9, abs_tol=1e-12):
            errors.append(f"同一工况的 {group[0]} 待确认：{previous['source']} 与 {source}")
        elif not previous or path == group[0]:
            seen[group] = {'value': value, 'unit': unit, 'source': source}
    fixed = {key: row for (key, sample, purpose, conditions), row in seen.items()
             if (sample, purpose, conditions) == ('main', 'main', '[]')}
    # 几何只继承明确声明；载量/密度/厚度的换算由模型提出并经Reviewer复核。
    return fixed, errors


def fixed_geometry(parameters):
    fixed, issues = physical_declarations(parameters)
    if issues:
        raise ValueError('；'.join(issues))
    return fixed


def fill_missing_candidate(candidate, fixed):
    if not isinstance(candidate, dict):
        raise ValueError('几何候选必须是对象')
    candidate = copy.deepcopy(candidate)
    for key, row in fixed.items():
        domain, field = key.split('.')
        if field not in ('loading_mg_cm2', 'compaction_g_cm3'):
            block = candidate.setdefault(domain, {})
            if not isinstance(block, dict):
                raise ValueError(f"{domain} 几何候选必须是对象")
            block[field] = row['value']
    return candidate


def record_geometry_sources(geometry, fixed):
    for key, row in fixed.items():
        source = geometry.setdefault("parameter_sources", {}).setdefault(key, {})
        source["declaration"] = row["source"]
    for comp in ('cathode', 'anode'):
        for field in ('loading_mg_cm2', 'compaction_g_cm3'):
            row = fixed.get(f'{comp}.{field}')
            if row:
                geometry[comp][field] = row['value']
