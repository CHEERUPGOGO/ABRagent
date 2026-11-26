"""主方案执行条件的只读继承、工况组合与局部一致性核对。"""
from __future__ import annotations

import copy
import math
import re

from .scheme_ledger import digest
from .numeric_consistency import canonical, normalized_number, unit

VERSION = 1
PROFILE = re.compile(r"\{\{profile:([\w-]+)\}\}")
REF = re.compile(r"\{\{([\w.-]+)\}\}")
DOMAINS = {"cathode", "anode", "electrolyte", "separator", "current_collector", "packaging", "process", "condition"}
PROFILE_FIELDS = {
    "main_cycle": ("常规循环", ("condition.c_rate", "condition.voltage_window", "condition.v_min", "condition.v_max", "condition.cycles", "condition.temperature_C", "condition.temperature_K", "condition.C_rate_reference_current")),
    "high_voltage": ("高压应力", ("condition.high_voltage_c_rate", "condition.high_voltage_window", "condition.high_voltage_v_min", "condition.high_voltage_v_max", "condition.high_voltage_cycles", "condition.high_voltage_temperature_C", "condition.high_voltage_target_retention")),
    "formation": ("化成", ("condition.formation_c_rate", "condition.formation_voltage_window", "condition.formation_cycles", "condition.formation_temperature_C")),
}
INSTRUCTIONS = '''
execution_conditions是当前主方案执行条件，优先于原设计文字、旧审核与文献建议。
字段的值、单位、工况、主组/备选身份均须继承；不得把已声明值称为未定案，待实测核验不等于设计未声明。
inheritance由程序维护并在对应实验节显示，未在正文逐项重复的主方案字段也整体继承。
工况可用{{profile:main_cycle}}、{{profile:high_voltage}}、{{profile:formation}}整套引用，只有execution_conditions.profiles存在的ID可用。
每套工况只含主方案明确声明的值，不从其它工况补默认值。若列两套分别注明用途；不可将高压窗口与常规圈数拼接。
主方案备选路线不自动成为首轮主路线。主方案没有的操作条件可以作为工程建议，但不得重新声明已有配方和数值。
全部文字、groups、requirement_tests、extensions及gaps遵守同一主方案；无关格式差异不算冲突。
'''


def equivalent(path, proposed, current):
    """明确同义与同量纲等值可通过；条件和分母不靠数值相同推断。"""
    from .research_contract import field_equal
    from .parameter_presentation import semantic_conditions
    if proposed.get("conditions") and semantic_conditions(proposed["conditions"]) != semantic_conditions(current.get("conditions") or {}):
        return False
    if proposed.get("denominator") and current.get("denominator") and proposed["denominator"] != current["denominator"]:
        return False
    a, b = proposed.get("value"), current.get("value")
    try:
        av, au = normalized_number(path, proposed)
        bv, bu = normalized_number(path, current)
        if au == bu:
            return math.isclose(av, bv, rel_tol=1e-8, abs_tol=1e-12)
        # 常用工艺温度及持续时间不改变原始方案单位。
        transforms = {"℃": (1, 273.15, "K"), "°C": (1, 273.15, "K"), "K": (1, 0, "K"),
                      "min": (60, 0, "s"), "h": (3600, 0, "s"), "s": (1, 0, "s")}
        left, right = transforms.get(au), transforms.get(bu)
        if left and right and left[2] == right[2]:
            return math.isclose(av*left[0]+left[1], bv*right[0]+right[1], rel_tol=1e-8)
    except (ValueError, TypeError):
        pass
    return unit(proposed.get("unit")) == unit(current.get("unit")) and field_equal(path, a, b)


def section_for(path, row):
    domain, _, field = path.partition(".")
    if domain not in DOMAINS or row.get("value") is None or row.get("basis") == "literature":
        return None
    if row.get("scope") in ("control", "alternative", "candidate") or row.get("group_id"):
        return None
    if field in ("R_s_um", "D_s_m2s", "capacity_mAh_g", "n_p_ratio"):
        return None  # 物性及仿真专用量不强制成为实验操作。
    if domain == "process":
        description = str(row.get("meaning", ""))
        if any(w in description for w in ("备选", "对照路线", "对照/")) and not any(w in description for w in ("主推", "首选")):
            return None
        return "fabrication"
    if domain == "condition":
        return "criteria" if any(w in field for w in ("target", "retention")) else "testing"
    if domain in ("electrolyte", "separator", "packaging", "anode"):
        return "assembly"
    return "fabrication"


def execution_conditions(doc):
    """只从当前已验收声明投影，不从旧科研段落补值或推断工况。"""
    from .experiment_design import effective_data
    params = effective_data(doc)["parameters"]
    inherited = {s: [] for s in ("fabrication", "assembly", "testing", "criteria")}
    alternatives = []
    for path, row in params.items():
        section = section_for(path, row)
        if section:
            inherited[section].append(path)
        elif path.startswith("process.") and row.get("value") is not None:
            alternatives.append(path)
    profiles = {}
    for pid, (label, keys) in PROFILE_FIELDS.items():
        refs = [k for k in keys if params.get(k, {}).get("value") is not None]
        if refs:
            profiles[pid] = {"label": label, "refs": refs}
    paths = set(alternatives) | {p for v in inherited.values() for p in v}
    fields = {p: {k: copy.deepcopy(params[p].get(k)) for k in
              ("value", "unit", "meaning", "basis", "conditions", "scope", "denominator", "quantity_kind", "locked")}
              for p in sorted(paths)}
    return {"version": VERSION, "sections": {s: sorted(v) for s, v in inherited.items()},
            "profiles": profiles, "alternatives": sorted(alternatives), "fields": fields}


def attach(record, doc):
    """程序提供继承引用，模型无需重复登记主方案全部字段。"""
    context = execution_conditions(doc)
    record["execution_contract"] = {"version": VERSION, "sections": context["sections"],
                                    "profiles": context["profiles"], "alternatives": context["alternatives"]}
    record.setdefault("parameters", {})
    for refs in context["sections"].values():
        for path in refs:
            row = record["parameters"].get(path)
            if row is None or ("ref" not in row and row.get("scope") != "control" and equivalent(path, row, context["fields"][path])):
                record["parameters"][path] = {"ref": path}
    return record


def synchronize(record, doc):
    """同步引用与可证明已被有效主组取代的旧登记失败，不改写科学正文。"""
    from .research_experiments import identity_fields
    before = copy.deepcopy(record)
    attach(record, doc)
    active = [g for g in record.get("groups", []) if g.get("scope") == "main"]
    if doc.get("requirements") and len(active) == 1 and not active[0].get("changes"):
        main = active[0]
        old_candidate = main.get("candidate_id")
        chosen = (doc.get("research") or {}).get("selected_candidate_id")
        main["parameter_refs"] = identity_fields(doc)
        main["candidate_id"] = chosen
        pending = []
        for row in record.get("pending_groups", []):
            old = row.get("record") or {}
            if (old.get("id") == main.get("id") and old.get("scope") == "main" and not old.get("changes")
                    and old.get("candidate_id") in (old_candidate, chosen)
                    and row.get("reason") == "实验主组必须完整引用当前唯一主方案，不能重定义组成"):
                record.setdefault("resolved_group_history", []).append(copy.deepcopy(row))
            else:
                pending.append(row)
        if "pending_groups" in record:
            record["pending_groups"] = pending
    return record != before


def expand_profiles(text, record):
    profiles = (record.get("execution_contract") or {}).get("profiles", {})
    def replace(match):
        profile = profiles.get(match[1])
        if not profile:
            raise ValueError("引用未声明的主方案工况：" + match[1])
        return profile["label"] + "（" + "、".join("{{"+p+"}}" for p in profile["refs"]) + "）"
    return PROFILE.sub(replace, text)


def text_entries(record):
    for section, paragraphs in record.get("sections", {}).items():
        if isinstance(paragraphs, list):
            for i, text in enumerate(paragraphs):
                if isinstance(text, str):
                    yield ["sections", section, i], text
    for i, row in enumerate(record.get("requirement_tests", [])):
        if isinstance(row, dict):
            for field, text in row.items():
                if isinstance(text, str) and field not in ("requirement_id", "status"):
                    yield ["requirement_tests", i, field], text
    for i, text in enumerate(record.get("extensions", [])):
        if isinstance(text, str):
            yield ["extensions", i], text


def issues(record, doc):
    """仅拦截可明确定位的事实冲突，开放文字的归属交独立一致性审核。"""
    from .experiment_design import effective_data, overlap_key
    params = effective_data(doc)["parameters"]
    errors = []
    def add(path, reason):
        row = {"path": path, "reason": reason}
        if row not in errors:
            errors.append(row)
    contract = record.get("execution_contract")
    if contract:
        expected = execution_conditions(doc)
        if contract != {k: expected[k] for k in ("version", "sections", "profiles", "alternatives")}:
            add(["execution_contract"], "主方案一致性核对已过期，须同步继承清单及工况")
        for section, refs in expected["sections"].items():
            for path in refs:
                row = record.get("parameters", {}).get(path, {})
                if row.get("ref") != path:
                    add(["parameters", path], "已有主方案条件须直接引用，不得指向另一项条件：" + path)
    for key, row in record.get("parameters", {}).items():
        if not isinstance(row, dict) or "ref" in row or row.get("scope") == "control":
            continue
        path = row.get("canonical_path") or overlap_key(key)
        matches = [p for p in params if canonical(p, params[p]) == canonical(path, row)]
        if len(matches) == 1 and params[matches[0]].get("value") is not None and not equivalent(matches[0], row, params[matches[0]]):
            add(["parameters", key], "实验参数与方案表冲突：" + matches[0])
    thresholds = {}
    for key, row in record.get("parameters", {}).items():
        rid = row.get("requirement_id") if isinstance(row, dict) else None
        if rid:
            item = next((i for i in (doc.get("requirements") or {}).get("items", []) if i.get("id") == rid), None)
            quantity = (item or {}).get("quantity") or {}
            if not item or not equivalent(key, row, quantity):
                add(["parameters", key], "用户目标阈值引用与原始要求不一致")
            else:
                thresholds[key] = item
    if not contract:
        return errors  # 旧记录沿用原验收；显式同步后才启用正文及组合核对。
    from .experiment_consistency import preflight
    pending = set(preflight(doc)["pending_fields"])
    for location, original in text_entries(record):
        try:
            text = expand_profiles(original, record)
        except ValueError as exc:
            add(location, str(exc))
            continue
        # 目标条件通常用分号分隔字段，整体看待；连续方法按句/分号保留各自工况。
        clauses = [text] if location[0] == "requirement_tests" and location[-1] == "conditions" else re.split(r"[。；;\n]", text)
        for clause in clauses:
            refs = {record.get("parameters", {}).get(r, {}).get("ref", r) for r in REF.findall(clause)}
            main = refs & {"condition.voltage_window", "condition.v_min", "condition.v_max", "condition.cycles"}
            high = refs & {"condition.high_voltage_window", "condition.high_voltage_v_min", "condition.high_voltage_v_max", "condition.high_voltage_cycles"}
            main_window = main & {"condition.voltage_window", "condition.v_min", "condition.v_max"}
            high_window = high & {"condition.high_voltage_window", "condition.high_voltage_v_min", "condition.high_voltage_v_max"}
            if (high_window and "condition.cycles" in main and not main_window
                    or main_window and "condition.high_voltage_cycles" in high and not high_window):
                add(location, "混用了常规循环和高压应力条件，应分别引用完整工况")
            remaining = re.sub(r"(?:不再|不能|不得|并非|不是|不应)[^，。；]{0,15}(?:未定案|尚未确定|尚未声明|未完成计算)", "", clause)
            if re.search(r"未定案|尚未确定|尚未声明|未完成计算|一致性待定|口径尚未", remaining):
                known = {r for r in refs if params.get(r, {}).get("value") is not None and r not in pending}
                if known:
                    add(location, "已声明的主方案条件被写为未定案：" + "、".join(sorted(known)))
            if location[:2] == ["sections", "criteria"] or location[0] == "requirement_tests" and location[-1] == "criterion":
                for key in REF.findall(clause):
                    item = thresholds.get(key)
                    if not item:
                        continue
                    if item.get("quantity", {}).get("operator") == ">" and not re.search(r">|高于|超过|大于", clause):
                        add(location, "判据遗漏用户目标的严格高于关系：" + item["description"])
                    if "显著" in item.get("description", "") and "显著" not in clause:
                        add(location, "判据遗漏用户目标的显著性限定：" + item["description"])
    return errors


def render_inheritance(record, doc, section):
    contract = record.get("execution_contract") or {}
    refs = contract.get("sections", {}).get(section, [])
    if not refs:
        return ""
    from .experiment_design import effective_data
    from .report_narrative import label, amount
    params = effective_data(doc)["parameters"]
    mentioned = set()
    for text in record.get("sections", {}).get(section, []):
        for ref in REF.findall(expand_profiles(text, record)):
            mentioned.add(record["parameters"].get(ref, {}).get("ref", ref))
    missing = [p for p in refs if p not in mentioned]
    lines = ["**沿用主方案。** 本节继承当前方案中适用的已定条件；对照变化仅按分组声明执行。"]
    if missing:
        lines.append("其余已定条件：" + "；".join(label(p, params[p]) + "为" + amount(p, params[p]) for p in missing) + "。")
    if section == "fabrication" and contract.get("alternatives"):
        lines.append("主方案另列的备选工艺作为后续选项，未纳入本轮主路线：" + "；".join(
            label(p, params[p]) + "：" + amount(p, params[p]) for p in contract["alternatives"]) + "。")
    return "\n\n".join(lines)
