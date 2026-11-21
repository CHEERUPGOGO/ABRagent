"""参数的只读语义视图：条件独立解释、重复声明归并及完整来源附录。"""
from __future__ import annotations

import copy
import json
import re
import unicodedata

CONDITION_NAMES = {
    "anode": "负极", "electrolyte": "电解液", "voltage_window": "电压窗口",
    "denominator": "计量基准", "basis": "计量口径", "site": "占位", "duration": "持续时间",
    "time_h": "持续时间", "cycles": "循环圈数", "c_rate": "倍率", "then_c_rate": "后续倍率",
    "temperature": "温度", "temperature_C": "温度", "temperature_K": "温度",
    "atmosphere": "气氛", "solvent": "溶剂", "mass_ratio": "质量比", "precursor": "前驱体",
    "purpose": "用途", "stage": "适用阶段", "control": "核验方法", "excess": "过量说明",
    "doping": "改性状态", "coating": "涂布条件", "electrode": "电极配置",
    "electrode_system": "实验体系", "combination": "筛选矩阵", "x": "化学式变量x", "y": "化学式变量y",
}
CONDITION_UNITS = {"time_h": "h", "temperature_C": "℃", "temperature_K": "K", "cycles": "圈",
                   "c_rate": "C", "then_c_rate": "C"}
FLAGS = {"vacuum": ("真空", "非真空"), "co_free": ("无钴", "未声明无钴"), "cobalt_free": ("无钴", "未声明无钴"),
         "formula_unit": ("按每化学式单位记录", "未采用每化学式单位口径"),
         "upper_bound": ("上限", "非上限")}
# 仅列明确同义字段；不同步骤的工艺条件不能因数值相同被合并。
ALIASES = {
    "cathode.parent_formula": "cathode.base_formula", "cathode.mother_formula": "cathode.base_formula",
    "current_collector.cathode_material": "current_collector.cathode", "current_collector.anode_material": "current_collector.anode",
    "packaging.type": "packaging.cell_format", "packaging.cell_type": "packaging.cell_format",
    "condition.voltage_min": "condition.v_min", "condition.voltage_max": "condition.v_max",
    "condition.voltage_window_lower": "condition.v_min", "condition.voltage_window_upper": "condition.v_max",
    "electrolyte.conc_M": "electrolyte.concentration", "electrolyte.concentration_M": "electrolyte.concentration",
}
PSEUDO_UNITS = {"formula", "formula_unit", "element", "material", "salt", "solvent", "solvent_system", "chemical",
                "route", "method", "media", "atmosphere", "cell_type", "cell type", "species", "methods", "cooling_profile", "1", "N/A"}
QUANTITY_KINDS = {"physical", "site_fraction", "mass_fraction", "volume_fraction", "formula_coefficient", "ratio", "count", "text", "other"}
INSTRUCTIONS = '''
参数语义必须明确：每项提供中文meaning，可选display_label（简短中文名称）、quantity_kind
(physical/site_fraction/mass_fraction/volume_fraction/formula_coefficient/ratio/count/text/other)、denominator（比例分母）。
conditions优先使用 [{"name":"中文条件名","value":明确值,"unit":"真实单位或空串"}]，只记录原文支持的适用条件。
不要把元数据标志、其他字段重复数值或整套配方塞进conditions；已声明条件可用ref引用规范字段。
unit只放单位，禁止fraction of Li sites、formula_unit、cell type等说明性伪单位；公式和材料unit为空。
化学式变量x/y是每化学式单位系数时用formula_coefficient，不得写成位点分数。位点分数必须明确分母，
质量/体积分数与化学式系数分开记录；原文口径含混时放candidates并说明需确认，不自行换算百分比。
压实后单面干涂层厚度使用cathode.L_um或anode.L_um；电压窗口使用condition.v_min/v_max。主循环、窄窗对照、化成和首圈测氧须分别注明用途。专属工况使用独立路径、canonical_path及conditions中明确的用途。
字段名尽量复用current_parameters及常见规范字段；上下限用同一字段族，不重复母体/封装/工况声明。
'''


def normalized(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = f"{value:.12g}"
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold()


def unit(unit_value):
    value = str(unit_value or "").strip()
    if value in PSEUDO_UNITS:
        return ""
    return {"°C": "℃", "cycles": "圈", "cycle": "圈", "cells": "只", "cell": "只", "count": "个",
            "um": "μm", "uL": "μL", "mg/cm2": "mg/cm²", "uL/cm2": "μL/cm²", "g/cm3": "g/cm³",
            "m2/s": "m²/s", "mAh/cm2": "mAh/cm²", "mA·g⁻¹": "mA/g"}.get(value, value)


def ambiguous_ratio(row):
    raw_unit = str(row.get("unit", ""))
    conditions = row.get("conditions") or {}
    kind = row.get("quantity_kind")
    has_flag = isinstance(conditions, dict) and conditions.get("formula_unit") is True
    return bool(("fraction of" in raw_unit and (has_flag or not kind))
                or kind == "site_fraction" and has_flag
                or kind in ("site_fraction", "mass_fraction", "volume_fraction") and not row.get("denominator"))


def value_text(key, row):
    """旧的含混比例原值保留；百分比转换必须有明确口径。"""
    from .report_narrative import text
    value, raw_unit = row.get("value"), str(row.get("unit") or "")
    if value is None:
        return "未确定"
    if isinstance(value, bool):
        return "是" if value else "否"
    if row.get("value_type") in ("range", "sequence"):
        from .experiment_values import validate_value
        validate_value(key, row)
        scalar = {k: v for k, v in row.items() if k != "value_type"}
        values = [value["min"], value["max"]] if row["value_type"] == "range" else value
        if row["value_type"] == "range" and row.get("range_order") == "descending":
            values = list(reversed(values))
        joiner = " 至 " if row["value_type"] == "range" else "、"
        return joiner.join(value_text(key, dict(scalar, value=item)) for item in values)
    if ambiguous_ratio(row):
        return text(value) + "（计量口径待确认）"
    kind = row.get("quantity_kind")
    if isinstance(value, (int, float)):
        if kind in ("site_fraction", "mass_fraction", "volume_fraction") and raw_unit in ("", "1", "fraction"):
            return f"{value * 100:.6g}%"
        if kind == "formula_coefficient":
            return f"{value:.6g}（每化学式单位系数）"
        if raw_unit == "K":
            if any(t in key.lower() for t in ("delta", "difference", "rise", "increment", "温差", "温升")):
                return f"{value:.6g} ℃（温差）"
            return f"{value - 273.15:.6g} ℃"
        if raw_unit in ("K/min", "K/s", "K/h"):
            return f"{value:.6g} " + raw_unit.replace("K", "℃")
        if ("fraction" in key or key.split(".")[-1] == "porosity") and kind != "formula_coefficient" and raw_unit in ("", "1", "fraction"):
            return f"{value * 100:.6g}%"
    displayed_unit = unit(raw_unit)
    if "fraction of" in displayed_unit or "ratio (" in displayed_unit or len(displayed_unit.split()) > 2:
        displayed_unit = "（单位含义待确认）"
    result = text(value)
    return result + (" " + displayed_unit if displayed_unit and not result.rstrip().endswith(displayed_unit) else "")


def condition_entries(conditions):
    if isinstance(conditions, str):
        return [("适用条件", conditions, "", True)] if conditions.strip() else []
    entries = []
    if isinstance(conditions, dict):
        for key, value in conditions.items():
            if key in FLAGS and isinstance(value, bool):
                entries.append(("说明", FLAGS[key][0 if value else 1], "", True))
            elif key == "operator":
                entries.append(("比较符号", value, "", True))
            else:
                name = CONDITION_NAMES.get(key, key if re.search(r"[\u4e00-\u9fff]", key) else "未命名适用条件")
                known = key in CONDITION_NAMES or name == key
                if not known:
                    name += f"（{key}）"
                entries.append((name, value, CONDITION_UNITS.get(key, ""), known))
    elif isinstance(conditions, list):
        for item in conditions:
            if isinstance(item, dict):
                entries.append((item.get("name") or "未命名适用条件", item.get("value", "引用待核对"), item.get("unit", ""), bool(item.get("name"))))
    return entries


def render_conditions(conditions, *, complete=False, parameters=None):
    if isinstance(conditions, list) and parameters:
        conditions = copy.deepcopy(conditions)
        for item in conditions:
            if isinstance(item, dict) and item.get("ref") in parameters:
                linked = parameters[item["ref"]]
                item.update(value=linked.get("value"), unit=linked.get("unit", ""))
    parts, pending = [], 0
    for name, value, raw_unit, known in condition_entries(conditions):
        if not known:
            pending += 1
            if not complete:
                continue
        if name == "说明":
            parts.append(str(value))
        else:
            displayed = value_text("condition." + name, {"value": value, "unit": raw_unit})
            parts.append(displayed if name == "适用条件" else f"{name}：{displayed}")
    if pending and not complete:
        parts.append(f"另有{pending}项条件含义待确认，见参数附录")
    return "；".join(dict.fromkeys(parts))


def semantic_conditions(conditions):
    # 排序只用于判等；未知键保留原键，不能把不同未知条件合并。
    if isinstance(conditions, dict):
        entries = [("无钴标记" if k in ("co_free", "cobalt_free") else CONDITION_NAMES.get(k, k), v, CONDITION_UNITS.get(k, "")) for k, v in conditions.items()]
    else:
        entries = condition_entries(conditions)
    return json.dumps(sorted(entries, key=lambda e: str(e[0])), ensure_ascii=False, sort_keys=True, default=str)


def canonical_key(key, row=None):
    explicit = (row or {}).get("canonical_path")
    return explicit or ALIASES.get(key, key)


def parameter_view(data):
    """每条声明仅在值、单位、计量语义、条件均相同时归并，保存全部源键。"""
    from .physical_parameters import identity
    groups = {}
    for key, original in data.get("parameters", {}).items():
        row = copy.deepcopy(original)
        canonical = canonical_key(key, row)
        physical = identity(key, row)
        if physical:
            canonical = physical[0]
            row.setdefault("quantity_kind", "physical")
            row.setdefault("denominator", "")
            if row.get("scope") or row.get("group_id") or row.get("purpose"):
                items = condition_entries(row.get("conditions") or {})
                row["conditions"] = [{"name": name, "value": value, "unit": u} for name, value, u, *_ in items]
                row["conditions"] += [{"name": k, "value": v} for k, v in (("样品", physical[1]), ("用途", physical[2])) if v != "main"]
        signature = (canonical, normalized(row.get("value")), unit(row.get("unit")),
                     row.get("quantity_kind"), row.get("denominator"), semantic_conditions(row.get("conditions") or {}))
        if signature not in groups:
            row["source_fields"] = []
            row["sources"] = []
            row["canonical_key"] = canonical
            groups[signature] = row
        merged = groups[signature]
        merged["source_fields"].append(key)
        source = {k: original.get(k) for k in ("source", "source_quote", "passage_id", "basis", "locked")}
        if source not in merged["sources"]:
            merged["sources"].append(source)
    return list(groups.values())


def parameter_name(key, row):
    explicit = row.get("display_label")
    if isinstance(explicit, str) and re.search(r"[\u4e00-\u9fff]", explicit) and key not in explicit:
        return explicit.strip().rstrip("。")
    from .report_narrative import NAMES
    leaf = key.split(".")[-1]
    special = {"base_formula": "母体化学式", "parent_formula": "母体化学式", "mother_formula": "母体化学式",
               "cell_format": "封装形式", "type": "封装形式"}
    if special.get(leaf) or NAMES.get(leaf):
        return special.get(leaf) or NAMES[leaf]
    meaning = row.get("meaning")
    if isinstance(meaning, str) and re.search(r"[\u4e00-\u9fff]", meaning) and key not in meaning:
        return meaning.strip().rstrip("。")
    return "未命名参数（见参数附录）"


def row_description(row, *, complete=False, parameters=None):
    key = row["canonical_key"]
    name = parameter_name(key, row)
    content = name + "为" + value_text(key, row)
    if row.get("denominator"):
        content += "，计量基准：" + str(row["denominator"])
    scope = render_conditions(row.get("conditions"), complete=complete, parameters=parameters)
    if scope:
        content += "，适用条件：" + scope
    if any(s.get("basis") in ("literature", "literature_measurement") for s in row.get("sources", [])):
        content += "（含文献参考声明，须核对研究对象与条件）"
    return content


def combined_rows(rows):
    """上下限及中心点必须同字段族、同单位/口径/条件才组合；否则分别保留。"""
    used, result = set(), []
    for n, row in enumerate(rows):
        if n in used:
            continue
        key = row["canonical_key"]
        match = re.match(r"(.+?)(?:_min|_max)$", key)
        if not match:
            result.append(row)
            continue
        family = match[1]
        if family == "condition.v":
            # 截止电压上下限是一对工况，不是同一性能量的推荐区间。
            result.append(row)
            continue
        members = [(i, r) for i, r in enumerate(rows) if r["canonical_key"] in (family + "_min", family + "_max")
            and unit(r.get("unit")) == unit(row.get("unit")) and r.get("quantity_kind") == row.get("quantity_kind")
            and r.get("denominator") == row.get("denominator")
            and semantic_conditions(r.get("conditions") or {}) == semantic_conditions(row.get("conditions") or {})]
        by_key = {r["canonical_key"]: r for _, r in members}
        lo, hi = by_key.get(family + "_min"), by_key.get(family + "_max")
        if lo and hi and len(members) == 2 and isinstance(lo.get("value"), (int, float)) and isinstance(hi.get("value"), (int, float)) and lo["value"] <= hi["value"]:
            merged = copy.deepcopy(lo)
            merged["range_values"] = (lo["value"], hi["value"])
            merged["display_label"] = re.sub(r"(?:下限|上限)$", "窗口", parameter_name(key, row))
            merged["source_fields"] = [p for _, r in members for p in r["source_fields"]]
            merged["sources"] = [s for _, r in members for s in r["sources"]]
            center_key = family.rpartition(".")[0] + ".center"
            centers = [(i, r) for i, r in enumerate(rows) if r["canonical_key"] in (family + "_center", center_key)
                and unit(r.get("unit")) == unit(row.get("unit")) and r.get("quantity_kind") == row.get("quantity_kind")
                and r.get("denominator") == row.get("denominator")
                and semantic_conditions(r.get("conditions") or {}) == semantic_conditions(row.get("conditions") or {})]
            if len(centers) == 1 and isinstance(centers[0][1].get("value"), (int, float)) and lo["value"] <= centers[0][1]["value"] <= hi["value"]:
                i, center = centers[0]
                merged["center_value"] = center["value"]
                merged["source_fields"].extend(center["source_fields"])
                merged["sources"].extend(center["sources"])
                used.add(i)
                result = [r for r in result if r is not center]
            result.append(merged)
            used.update(i for i, _ in members)
        else:
            result.append(row)
    return result


def displayed_row(row, parameters=None):
    if "range_values" not in row:
        return row_description(row, parameters=parameters)
    lo, hi = row["range_values"]
    lower = dict(row, value=lo)
    upper = dict(row, value=hi)
    lower_text, upper_text = value_text(row["canonical_key"], lower), value_text(row["canonical_key"], upper)
    tail = upper_text[len(f"{hi:g}"):] if upper_text.startswith(f"{hi:g}") else ""
    if tail and lower_text.endswith(tail):
        lower_text = lower_text[:-len(tail)]
    text = parameter_name(row["canonical_key"], row) + "为" + lower_text + "–" + upper_text
    if "center_value" in row:
        text += "，中心点为" + value_text(row["canonical_key"], dict(row, value=row["center_value"]))
    if row.get("denominator"):
        text += "，计量基准：" + str(row["denominator"])
    scope = render_conditions(row.get("conditions"), parameters=parameters)
    return text + ("，适用条件：" + scope if scope else "")


def normalize_metadata(key, field, available=None):
    """仅用于新模型提取的局部校验，不修改既有任务或给比例猜测分母。"""
    row = copy.deepcopy(field)
    if row.get("canonical_path") is not None and (not isinstance(row["canonical_path"], str) or "." not in row["canonical_path"]):
        raise ValueError("canonical_path须为规范字段路径字符串")
    if not isinstance(row.get("meaning"), str) or not re.search(r"[\u4e00-\u9fff]", row["meaning"]):
        raise ValueError("参数须有明确中文meaning")
    kind = row.get("quantity_kind")
    if row.get("display_label") is not None and (not isinstance(row["display_label"], str) or not re.search(r"[\u4e00-\u9fff]", row["display_label"])):
        raise ValueError("display_label须为明确中文名称")
    if kind is not None and kind not in QUANTITY_KINDS:
        raise ValueError("quantity_kind非法")
    if row.get("denominator") is not None and not isinstance(row["denominator"], str):
        raise ValueError("denominator须为明确的计量基准文字")
    if "fraction of" in str(row.get("unit", "")) or len(str(row.get("unit", "")).split()) > 2:
        raise ValueError("说明和分母不得写入unit；须明确quantity_kind与denominator")
    if ambiguous_ratio(row):
        raise ValueError("比例分母与每化学式单位口径未明确，须放候选而非换算百分比")
    if row.get("unit") == "fraction" and kind not in ("site_fraction", "mass_fraction", "volume_fraction"):
        raise ValueError("fraction须明确比例类型与分母")
    if kind in ("site_fraction", "mass_fraction", "volume_fraction") and isinstance(row.get("value"), (int, float)):
        if row.get("unit", "") in ("", "1", "fraction") and not 0 <= row["value"] <= 1:
            raise ValueError("分数口径的比例必须在0–1范围内")
    conditions = row.get("conditions") or {}
    if not isinstance(conditions, (dict, list, str)):
        raise ValueError("conditions须为中文条件记录、兼容字典或文字")
    if isinstance(conditions, dict):
        unknown = [k for k in conditions if k not in CONDITION_NAMES and k not in FLAGS and k != "operator" and not re.search(r"[\u4e00-\u9fff]", k)]
        if unknown:
            raise ValueError("条件缺少中文含义：" + ", ".join(unknown))
    if isinstance(conditions, list):
        for item in conditions:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not re.search(r"[\u4e00-\u9fff]", item["name"]):
                raise ValueError("条件须有中文name")
            if item.get("ref"):
                if item["ref"] not in (available or {}):
                    raise ValueError("条件ref引用未知字段")
            elif "value" not in item:
                raise ValueError("条件缺少value")
            elif isinstance(item["value"], bool):
                raise ValueError("条件value不能是裸布尔标记，须改成中文说明")
    if kind == "formula_coefficient" and str(row.get("unit", "")) not in ("", "1"):
        raise ValueError("化学式系数的unit应为空，不能写成位点分数")
    if str(row.get("unit", "")) in PSEUDO_UNITS:
        row["unit"] = ""
    return row


def render_parameter_appendix(data):
    """附录涵盖每个原字段，归并后仍保留全部来源及原始存档入口。"""
    from .report_narrative import GROUPS
    def cell(value):
        return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")
    rows = parameter_view(data)
    body = ["### 完整参数与适用条件", f"共归档{len(data.get('parameters', {}))}条字段声明，按相同语义和条件归并为{len(rows)}项；归并只影响展示，不改方案表。",
            "含混计量口径保持原值；在确认分母或化学式系数前不用于百分比换算。",
            "原始字段、单位、来源原句与内部标记可在[方案表档案](scheme_ledger.json)核对。"]
    for group, group_name in GROUPS.items():
        entries = [r for r in rows if r["canonical_key"].startswith(group + ".")]
        if not entries:
            continue
        body += [f"#### {group_name}参数", "| 序号 | 参数与原值 | 适用条件 / 计量说明 | 来源与状态 |",
                 "|---|---|---|---|"]
        for n, row in enumerate(entries, 1):
            key = row["canonical_key"]
            value = value_text(key, row)
            conditions = render_conditions(row.get("conditions"), complete=True, parameters=data.get("parameters"))
            notes = []
            if row.get("denominator"):
                notes.append("计量基准：" + str(row["denominator"]))
            if ambiguous_ratio(row):
                notes.append("位点分数与每化学式单位口径须复核，原单位见档案")
            origins = []
            for source in row["sources"]:
                basis = {"engineering_proposal": "工程声明", "literature": "文献参考", "user_requirement": "用户要求",
                         "legacy_declaration": "历史声明", "model_reference": "模型基准，非本批实测"}.get(source.get("basis"), "依据待确认")
                pid = source.get("passage_id")
                origin = str(source.get("source") or "来源待补") + "（" + basis + ("，用户锁定" if source.get("locked") else "") + "）"
                if pid:
                    origin += f" [{pid}]"
                origins.append(origin)
            description = parameter_name(key, row) + "：" + value
            scope = "；".join(([conditions] if conditions else []) + notes)
            body.append(f"| {n} | {cell(description)} | {cell(scope or '未额外声明')} | {cell('；'.join(dict.fromkeys(origins)))} |")
    return "\n\n".join(body[:4]) + "\n\n" + "\n".join(body[4:])


def presentation_conflicts(data):
    """同一语义字段同一条件存在不同值/单位时明确报告，不做猜测合并。"""
    grouped, errors = {}, []
    for row in parameter_view(data):
        if row.get("value") is None:
            continue
        signature = (row["canonical_key"], row.get("quantity_kind"), row.get("denominator"), semantic_conditions(row.get("conditions") or {}))
        actual = (normalized(row["value"]), unit(row.get("unit")))
        previous = grouped.get(signature)
        if previous and previous != actual:
            errors.append("同一条件下参数声明冲突：" + parameter_name(row["canonical_key"], row))
        grouped[signature] = actual
    return list(dict.fromkeys(errors))
