"""实验推荐的交付边界：执行缺项阻断，已披露的后续验证单列。"""
from __future__ import annotations

from .experiment_values import PENDING_TOKEN, VALUE_TOKEN, pending_rows


def validation_gap_errors(test, record, doc):
    """验证范围必须显式声明并引用本版推荐，不能用验证标签隐藏未交付项。"""
    kind = test.get("gap_kind")
    if kind is None:
        return ["推荐引用缺少验证范围声明"] if test.get("recommendation_refs") else []
    if test.get("status") != "gap" or kind not in ("delivery", "validation"):
        return ["缺口范围须为gap状态下的delivery或validation"]
    if kind == "delivery":
        return []
    requirements = (doc or {}).get("requirements") or {}
    requirement = next((r for r in requirements.get("items", []) if r.get("id") == test.get("requirement_id")), {})
    if (requirements.get("status") not in ("parsed", "partial") or requirement.get("status") not in (None, "clear")
            or requirement.get("kind") != "objective"
            or requirement.get("verification_needed") is not True):
        return ["后续验证须对应已解析且明确需要验证的研究目标"]
    if any(not isinstance(test.get(k), str) or not test[k].strip() for k in
           ("method", "conditions", "baseline", "criterion", "reason", "next_step")):
        return ["后续验证须保留推荐评估方法、条件、基线、判据、边界及下一步"]
    refs = test.get("recommendation_refs")
    if (not isinstance(refs, list) or not refs or any(not isinstance(p, str) for p in refs)
            or len(refs) != len(set(refs))):
        return ["后续验证须关联本版已给出的推荐参数"]
    current = ((doc or {}).get("ledger_view") or {}).get("parameters", {})
    used = {ref for text in (test["method"], test["conditions"], test["criterion"]) for ref in VALUE_TOKEN.findall(text)}
    for key in refs:
        row = record.get("parameters", {}).get(key) or {}
        declared = current.get(row.get("ref")) if "ref" in row else row
        if (key not in used or not isinstance(declared, dict) or declared.get("value") is None
                or declared.get("scope") in ("control", "alternative", "candidate", "literature")
                or declared.get("group_id") or row.get("scope") == "control"
                or declared.get("basis") in ("literature", "budget_assumption")):
            return ["后续验证引用的当前主推荐缺失、未采用或不属于主组：" + key]
    return []


def _unused_intrinsic_parameters(record, doc):
    """仅明确留空且不被操作/判据使用的扩散系数可作非阻断物性说明。"""
    if doc is None:
        return set()
    from .experiment_consistency import parameter_pending_fields
    conflicts = set(parameter_pending_fields(doc))
    current = (doc.get("ledger_view") or {}).get("parameters", {})
    active_text = [text for section, paragraphs in record.get("sections", {}).items() if section != "gaps"
                   for text in paragraphs if isinstance(text, str)]
    active_text += [value for row in record.get("requirement_tests", []) for value in row.values() if isinstance(value, str)]
    active_text += [text for text in record.get("extensions", []) if isinstance(text, str)]
    if isinstance(record.get("route"), str):
        active_text.append(record["route"])
    referenced = {key for text in active_text for key in PENDING_TOKEN.findall(text) + VALUE_TOKEN.findall(text)}
    referenced |= {row.get("ref") for row in record.get("parameters", {}).values()
                   if isinstance(row, dict) and isinstance(row.get("ref"), str)}
    eligible = set()
    for key, missing in pending_rows(record).items():
        if key not in ("cathode.D_s_m2s", "anode.D_s_m2s") or key in referenced or key in conflicts:
            continue
        original = missing.get("record") or {}
        declared = current.get(key) or {}
        if (original.get("value") is None and not original.get("locked") and missing.get("expected_type") == "number"
                and missing.get("unit") == "m2/s" and not declared.get("locked")
                and declared.get("value") is None and original.get("canonical_path", key) == key):
            eligible.add(key)
    return eligible


def assessment(record, doc=None):
    """只读分类，不改缺项、推荐值、目标gap状态或科学审核结论。"""
    doc = (doc or {}).get("_report_record", {}).get("design", doc)
    informational = _unused_intrinsic_parameters(record, doc)
    pending = record.get("pending_parameters") or {}
    blocking_sections = []
    for location, row in (record.get("pending_sections") or {}).items():
        source = row.get("text") if isinstance(row, dict) else None
        section, _, index = location.partition(":")
        paragraphs = record.get("sections", {}).get(section, [])
        current = paragraphs[int(index)] if index.isdecimal() and int(index) < len(paragraphs) else None
        refs = set(PENDING_TOKEN.findall(source) + VALUE_TOKEN.findall(source)) if isinstance(source, str) else set()
        expected = VALUE_TOKEN.sub(lambda m: "{{pending:" + m[1] + "}}" if m[1] in informational else m[0], source) if source else None
        if not (section == "gaps" and refs and refs <= informational and expected == current):
            blocking_sections.append(location)
    notes = [{"kind": "material_property", "field": key,
              "reason": f"{pending[key]['meaning']}尚待测量；本轮操作和判据未使用该值，保留为空，不影响实验推荐交付。",
              "next_step": pending[key]["acquisition_method"]} for key in sorted(informational)]
    blocking_goals, errors = [], []
    for row in record.get("requirement_tests", []):
        if not isinstance(row, dict):
            errors.append("目标记录格式非法")
            continue
        issues = validation_gap_errors(row, record, doc)
        errors += issues
        if row.get("status") != "gap":
            continue
        if row.get("gap_kind") == "validation" and not issues:
            notes.append({"kind": "experimental_validation", "requirement_id": row["requirement_id"],
                          "recommendation_refs": list(row["recommendation_refs"]),
                          "reason": "当前推荐已给出，最终效果或最优性仍待实验验证：" + row["reason"],
                          "next_step": row["next_step"]})
        else:
            blocking_goals.append(row.get("requirement_id"))
    return {"blocking_parameters": sorted(set(pending) - informational),
            "informational_parameters": sorted(informational), "blocking_sections": blocking_sections,
            "blocking_goals": blocking_goals, "validation_notes": notes, "errors": errors,
            "unexplained_partial": record.get("repair_status") == "PARTIAL" and (not informational
                or bool(record.get("repair_error")) or not all(record.get("sections", {}).get(s)
                    for s in ("groups", "fabrication", "assembly", "testing", "criteria")))}


def blocking_pending_fields(record, doc):
    from .experiment_consistency import parameter_pending_fields
    return sorted(set(parameter_pending_fields(doc)) | set(assessment(record, doc)["blocking_parameters"]))
