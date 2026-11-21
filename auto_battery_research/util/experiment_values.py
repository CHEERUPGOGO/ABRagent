"""实验参数值与缺项契约：共用类型检查、缺项登记、原文归档及只读显示。"""
from __future__ import annotations

import copy
import json
import math
import re
import unicodedata

from .scheme_ledger import digest

VERSION = 2
PENDING_TOKEN = re.compile(r"\{\{pending:([A-Za-z0-9_.-]+)\}\}")
VALUE_TOKEN = re.compile(r"\{\{([A-Za-z0-9_.-]+)\}\}")
UNRESOLVED = re.compile(r"^(?:待确定|待确认|待定|未确定|未明确|未知|未提供|未给出|缺失|暂无|不详|TBD|TBC|unknown|pending|none|null|n/a)(?=$|[\s（(:：，,。;；])", re.I)
NUMERIC_UNITS = {"h", "hr", "min", "s", "ms", "rpm", "Hz", "kHz", "MHz", "mV/s", "V/s", "K", "°C", "℃",
                 "K/min", "°C/min", "C", "V", "mV", "A", "mA", "mA/g", "A/g", "mA/cm2", "mol/L", "M", "mol/kg",
                 "wt%", "vol%", "%", "ppm", "g", "mg", "kg", "mL", "uL", "L", "mL/min", "um", "nm", "mm", "cm",
                 "m", "cm2", "m2", "g/cm3", "mg/cm2", "mAh/g", "mAh/cm2", "Wh/kg", "uL/cm2", "m2/s", "Pa", "MPa",
                 "cycles", "cycle", "cells", "cell", "count", "圈", "只", "个"}
PENDING_KEYS = {"status", "value", "expected_type", "unit", "meaning", "reason", "acquisition_method", "record", "record_hash"}
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def numeric_field(key, row):
    from .scheme_ledger import canonical_unit
    from .experiment_design import parameter_validation_key
    path = parameter_validation_key(row.get("canonical_path") or key)
    unit = unicodedata.normalize("NFKC", str(row.get("unit") or "")).replace("μ", "u").replace("µ", "u").replace("^", "")
    return (canonical_unit(path) is not None or unit in NUMERIC_UNITS
            or bool(re.search(r"(?:_time_(?:h|min|s)|_speed_rpm|_scan_rate_(?:mV|V)_s|_cycles|\.replicates)$", path)))


def expected_type(key, row):
    explicit = row.get("value_type") or row.get("expected_type")
    if explicit in ("number", "range", "sequence", "text"):
        return explicit
    return "number" if numeric_field(key, row) else "text"


def validate_value(key, row):
    """开放字段按明确类型与量纲验收，缺项文字、空值和字符串数值均不得充当数值。"""
    if not isinstance(row, dict):
        raise ValueError("实验参数须为对象")
    if "ref" in row:
        return  # 引用的权限和当前值另由既有方案表核对，不能在此重写它。
    value = row.get("value")
    kind = row.get("value_type")
    if kind is not None and kind not in ("number", "text", "range", "sequence"):
        raise ValueError("参数value_type只允许number/text/range/sequence")
    if value is None or isinstance(value, str) and (not value.strip() or UNRESOLVED.search(value.strip())):
        raise ValueError("参数值尚未确定，须登记到pending_parameters并保留获取方法")
    numeric = numeric_field(key, row)
    if kind == "text" and numeric:
        raise ValueError("数值量纲不能声明为文字类型")
    if type(value) in (int, float):
        if not math.isfinite(value) or kind not in (None, "number"):
            raise ValueError("数值须有限且与声明类型一致")
        return
    if kind in ("range", "sequence"):
        if kind == "range":
            if not isinstance(value, dict) or set(value) != {"min", "max"}:
                raise ValueError("范围须为含min/max的数值对象")
            values = [value["min"], value["max"]]
        else:
            if not isinstance(value, list) or not value or len(value) > 100:
                raise ValueError("序列须为非空有限数值数组，最多100项")
            values = value
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError("范围或序列中的值须为有限数值")
        if kind == "range" and values[0] > values[1]:
            raise ValueError("范围下限不得高于上限")
        if row.get("range_order", "ascending") not in ("ascending", "descending"):
            raise ValueError("范围顺序只能为ascending或descending")
        return
    if not isinstance(value, str) or numeric or kind == "number":
        raise ValueError("数值参数须为数值；范围和序列须使用明确结构")


def validate_scalar_fields(key, row):
    from .scheme_ledger import validate_field
    from .experiment_design import parameter_validation_key
    validate_value(key, row)
    value, kind = row.get("value"), row.get("value_type")
    values = list(value.values()) if kind == "range" else value if kind == "sequence" else [value]
    for item in values:
        validate_field(parameter_validation_key(row.get("canonical_path") or key), dict(row, value=item))


def validate_control_changes(changes, current=None):
    """对照变化与普通参数共用值检查；不替换主组值，也不猜测缺项。"""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("对照必须明确列出变化及目的")
    for key, row in changes.items():
        if (not isinstance(key, str) or not isinstance(row, dict)
                or not {"value", "unit", "reason"} <= row.keys()
                or set(row) - {"value", "unit", "reason", "original_value"}):
            raise ValueError("对照变化仅允许值、单位和原因，不接受权限字段或其他声明")
        if not isinstance(row["unit"], str) or not isinstance(row["reason"], str) or not row["reason"].strip():
            raise ValueError("对照单位与变化原因须为明确文本")
        if current is not None:
            if key not in current:
                raise ValueError("对照引用未知的主方案字段：" + key)
            declared = current[key]
            if row["unit"] != declared.get("unit", ""):
                raise ValueError("对照单位须与主方案规范单位一致：" + key)
            if declared.get("locked") and declared.get("value") is None:
                raise ValueError("对照不能补造用户锁定的未知值：" + key)
        # 分组中的单项变化仍为标量；范围/序列需拆成明确的对照记录。
        if type(row["value"]) not in (int, float, str):
            raise ValueError("对照值须为明确标量，未知项不得进入活动组别")
        if key.endswith("D_s_m2s") or "diffusion" in key.lower():
            raise ValueError("对照不得通过新物性值绕过物性证据通道")
        validate_scalar_fields(key, {"value": row["value"], "unit": row["unit"],
                                     "basis": "engineering_proposal", "source": row["reason"]})


def control_value_issues(record, current=None):
    """不依赖requirements存在与否，对全部活动对照给出精确错误路径。"""
    issues = []
    groups = record.get("groups", [])
    if not isinstance(groups, list):
        return [{"path": ["groups"], "reason": "实验分组须为数组"}]
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            issues.append({"path": ["groups", index], "reason": "实验组须为对象"})
            continue
        if group.get("scope") != "control":
            continue
        try:
            validate_control_changes(group.get("changes"), current)
        except (ValueError, TypeError) as exc:
            issues.append({"path": ["groups", index], "reason": str(exc)})
    return issues


def make_pending(key, row, reason=None, *, explicit=False):
    """保存原文而不猜测数值；获取方法仅沿用明确字段或旧字符串中的显式说明。"""
    original = copy.deepcopy(row)
    method = row.get("acquisition_method")
    if not isinstance(method, str) or not method.strip():
        value = row.get("value")
        match = re.search(r"获取方法\s*[:：]\s*(.*)", value) if isinstance(value, str) else None
        method = match[1].rstrip("）)").strip() if match else "先核对原始来源或开展针对性预实验，明确该条件后登记。"
    kind = expected_type(key, row)
    if numeric_field(key, row) and kind == "text":
        kind = "number"
    entry = {"status": "unresolved", "value": None, "expected_type": kind, "unit": row.get("unit", ""),
             "meaning": row.get("meaning") or key, "reason": reason or row.get("reason") or "原参数未给出明确值",
             "acquisition_method": method, "record": original, "record_hash": digest(original)}
    if explicit and (row.get("value") is not None or not isinstance(row.get("reason"), str) or not row["reason"].strip()
                     or not isinstance(row.get("acquisition_method"), str) or not row["acquisition_method"].strip()):
        raise ValueError("缺项提交须value=null并明确原因与获取方法")
    return entry


def pending_rows(record):
    return {k: v for k, v in (record.get("pending_parameters") or {}).items()
            if isinstance(v, dict) and v.get("status") == "unresolved"}


def normalize_legacy_collection(key, row):
    """只恢复显式数组或明确范围；斜杠和裸逗号不能推断序列。"""
    value, unit = row.get("value"), row.get("unit")
    if not isinstance(value, str) or row.get("value_type") not in (None, "sequence") or not numeric_field(key, row) or not unit:
        return row
    # 外层数组是明确的序列声明；1/2可能是分数，1,000可能是千位分隔，均保持原文。
    text = value.strip()
    tail = re.search(r"\s*" + re.escape(unit) + r"$", text)
    core = text[:tail.start()].strip() if tail else text
    if core.startswith("[") and core.endswith("]"):
        try:
            values = json.loads(core)
            result = dict(row, value=values, value_type="sequence")
            validate_value(key, result)
            return result
        except (ValueError, TypeError):
            return row
    if row.get("value_type"):
        return row
    match = re.fullmatch(r"\s*(" + NUMBER + r")\s*([A-Za-zμµ°℃/%]*)\s*[–—~～至]\s*(" + NUMBER + r")\s*([A-Za-zμµ°℃/%]*)\s*", text)
    if not match:
        return row
    lo, first_unit, hi, last_unit = match.groups()
    first_unit, last_unit = first_unit or last_unit or unit, last_unit or first_unit or unit
    # 仅列明可证明等价的频率前缀换算；其他单位必须与声明逐字相同。
    frequency = {"Hz": 1., "kHz": 1000., "MHz": 1000000., "mHz": .001}
    def convert(number, source):
        if source == unit:
            return float(number)
        if source in frequency and unit in frequency:
            return float(number) * frequency[source] / frequency[unit]
        raise ValueError("范围单位未列明等价换算")
    try:
        start, stop = convert(lo, first_unit), convert(hi, last_unit)
        result = dict(row, value={"min": min(start, stop), "max": max(start, stop)}, value_type="range",
                      range_order="descending" if start > stop else "ascending")
        validate_value(key, result)
        return result
    except ValueError:
        return row


def validate_pending(record):
    raw_pending = record.get("pending_parameters", {})
    if not isinstance(raw_pending, dict):
        raise ValueError("pending_parameters必须为对象")
    for key, row in raw_pending.items():
        if not isinstance(row, dict) or row.get("status") not in (None, "unresolved"):
            raise ValueError("待定参数状态非法")
    for key, row in pending_rows(record).items():
        if (set(row) != PENDING_KEYS or row.get("value") is not None
                or row.get("expected_type") not in ("number", "range", "sequence", "text")
                or any(not isinstance(row.get(k), str) or not row[k].strip() for k in ("meaning", "reason", "acquisition_method"))
                or not isinstance(row.get("unit"), str) or not isinstance(row.get("record"), dict)
                or row.get("record_hash") != digest(row["record"])):
            raise ValueError("待定参数结构或原文指纹非法：" + key)
        if key in record.get("parameters", {}):
            raise ValueError("同一参数不能同时作为有效值与待定项：" + key)
    pending = pending_rows(record)
    for text in iter_text(record):
        refs = set(PENDING_TOKEN.findall(text))
        if refs - pending.keys():
            raise ValueError("正文引用未登记的待定参数")
        clean = VALUE_TOKEN.sub("", PENDING_TOKEN.sub("", text))
        if "{{pending:" in clean:
            raise ValueError("待定参数引用格式非法")
        if set(VALUE_TOKEN.findall(text)) & pending.keys():
            raise ValueError("待定参数不能使用有效值引用")


def iter_text(record):
    for paragraphs in (record.get("sections") or {}).values():
        yield from (p for p in paragraphs if isinstance(p, str))
    for test in record.get("requirement_tests", []):
        yield from (v for v in test.values() if isinstance(v, str))
    yield from (v for v in record.get("extensions", []) if isinstance(v, str))
    if isinstance(record.get("route"), str):
        yield record["route"]


def separate_pending(record):
    """隔离未知或非法值并规范引用；目标状态、方法与限制由模型和科学审核负责。"""
    result = copy.deepcopy(record)
    changed = []
    for key, row in list(result.get("parameters", {}).items()):
        if "ref" in row:
            continue
        if row.get("status") == "unresolved" and set(row) == PENDING_KEYS:
            result.setdefault("pending_parameters", {})[key] = copy.deepcopy(row)
            result["parameters"].pop(key)
            result.get("bindings", {}).pop(key, None)
            changed.append(key)
            continue
        normalized = normalize_legacy_collection(key, row)
        if normalized != row:
            event = {"field": key, "original": copy.deepcopy(row), "original_hash": digest(row), "normalized": normalized}
            if event not in result.setdefault("parameter_value_history", []):
                result["parameter_value_history"].append(event)
            result["parameters"][key] = row = normalized
        try:
            validate_value(key, row)
        except ValueError as exc:
            entry = make_pending(key, row, str(exc))
            result.setdefault("pending_parameters", {})[key] = entry
            result["parameters"].pop(key)
            result.get("bindings", {}).pop(key, None)
            changed.append(key)
    pending = pending_rows(result)
    if not pending:
        return result, changed
    def replace(text):
        return VALUE_TOKEN.sub(lambda m: "{{pending:" + m[1] + "}}" if m[1] in pending else m[0], text)
    for section, paragraphs in result.get("sections", {}).items():
        for index, text in enumerate(paragraphs):
            updated = replace(text)
            if updated != text:
                result.setdefault("pending_sections", {}).setdefault(f"{section}:{index}", {"text": text, "reason": "相关参数尚未确定；保留原步骤"})
                paragraphs[index] = updated
    if isinstance(result.get("route"), str):
        updated = replace(result["route"])
        if updated != result["route"]:
            result.setdefault("pending_sections", {}).setdefault("route", {"text": result["route"], "reason": "路线引用的操作参数尚未确定"})
            result["route"] = updated
    for index, test in enumerate(result.get("requirement_tests", [])):
        updated = {k: replace(v) if isinstance(v, str) else v for k, v in test.items()}
        if test != updated:
            if test not in result.setdefault("pending_tests", []):
                result["pending_tests"].append(copy.deepcopy(test))
            result["requirement_tests"][index] = updated
    result["parameter_value_contract"] = VERSION
    # 旧版依赖扫描结果不再参与生成或验收，不据此恢复或提升旧目标状态。
    result.pop("pending_dependencies", None)
    result["repair_status"] = "PARTIAL"
    if changed and result.get("scientific_review"):
        old = result["scientific_review"]
        if old not in result.setdefault("scientific_review_history", []):
            result["scientific_review_history"].append(copy.deepcopy(old))
        # 原判定完整保留；内容指纹变化由既有审核status()确定为STALE，不补授结论。
    return result, changed


def substitute_pending(text, record):
    """待定引用只用于显示，不进入数值bindings或计算；单位保留于缺口说明。"""
    rows = pending_rows(record)
    pattern = re.compile(PENDING_TOKEN.pattern + r"(?:(?P<space>\s*)(?P<unit>mV/s|V/s|rpm|Hz|kHz|h|min|s|K|℃|°C|mol/L|ppm|%)(?![A-Za-z_]))?")
    def replace(match):
        row = rows[match[1]]
        tail = ""
        if match["unit"] and match["unit"] != row.get("unit"):
            tail = (match["space"] or "") + match["unit"]
        return "【" + row["meaning"] + "未确定，见待确认项】" + tail
    return pattern.sub(replace, text)


def render_pending(record):
    rows = pending_rows(record)
    if not rows:
        return ""
    lines = ["**待确认参数及获取方法**", ""]
    for row in rows.values():
        unit = "；记录单位：" + row["unit"] if row["unit"] else ""
        reason = row["reason"].replace("pending_parameters", "待确认项")
        lines.append(f"- {row['meaning']}：未确定{unit}。原因：{reason}。获取方法：{row['acquisition_method']}")
    return "\n".join(lines)
