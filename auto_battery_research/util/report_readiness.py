"""报告交付状态与科研验证状态分开维护。"""
import copy
import json
import math

from .scheme_ledger import digest, projection_errors


def prepare(doc, ledger, warnings, candidate=None):
    from .material_ids import canonical_scheme, assemble_components
    doc["delivery_contract"] = 1
    scheme = doc.setdefault("scheme", {})
    params = {**ledger.get("parameters", {}), **(doc.get("scheme_parameters") or {})}
    target = params.get("requirements.target_energy_wh_kg", {}).get("value")
    if target is None:
        scheme.pop("target_energy_wh_kg", None)
    else:
        scheme["target_energy_wh_kg"] = target
    scheme = assemble_components(scheme, params, strict=False)
    geo = copy.deepcopy(scheme.get("geometry") or candidate or {})
    clean = {"schema_version": "1.1", "contract_version": "report-1", "condition": {}}
    for comp in ("cathode", "anode", "condition"):
        clean.setdefault(comp, {})
        for key, value in (geo.get(comp) or {}).items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                continue
            if value < 0 or key == "porosity" and value > 1:
                continue
            clean[comp][key] = value
    # 方案表已经接受的值优先；不为满足仿真要求虚填物性。
    for path, row in params.items():
        comp, _, key = path.partition(".")
        if comp in clean and isinstance(clean[comp], dict) and key in ("L_um", "porosity", "R_s_um", "D_s_m2s", "v_min", "v_max", "c_rate"):
            clean[comp][key] = row.get("value")
    if "geometry" not in scheme or (scheme.get("geometry") or {}).get("contract_version") != "2.0":
        scheme["geometry"] = clean
        scheme.pop("geometry_hash", None)
    doc["scheme"] = canonical_scheme(scheme)
    issues = list(dict.fromkeys(str(w) for w in warnings))
    issues.extend(scheme.get("component_identity_errors") or [])
    if not doc.get("evidence"):
        issues.append("未归档有效文献证据，不能把机理假设或工程建议作为文献实证。")
    issues += [str(w) for w in (doc.get("reviewer_output") or {}).get("issues", [])]
    if doc.get("numeric_contract"):
        from .numeric_consistency import audit, messages
        from .numeric_bindings import binding_errors
        issues.extend(messages(audit(params, doc.get("final_answer", ""))))
        issues.extend(binding_errors(doc.get("final_answer", ""), params))
    if not doc.get("review_completed"):
        issues.append("未完成有效学术审核，须人工复核；确定性检查不能替代学术审核。")
    category = "CONDITIONAL" if issues else "COMPLETE"
    from .research_contract import requires_scheme
    if requires_scheme(doc) and not all(scheme.get(c) for c in ("cathode", "anode", "electrolyte")):
        category = "DIAGNOSTIC"
    doc["report_readiness"] = {"category": category, "issues": issues,
        "review_status": "REVIEWED" if doc.get("review_completed") else "UNREVIEWED",
        "physics_status": "NOT_EVALUATED", "retryable": False}
    doc["review_status"] = "APPROVED" if category == "COMPLETE" else "REVIEWED_WITH_ISSUES"


def notice(doc):
    state = doc.get("report_readiness") or {}
    if not state:
        return ""
    names = {"COMPLETE": "研究候选报告", "CONDITIONAL": "有条件候选报告", "DIAGNOSTIC": "问题诊断报告"}
    review = {"REVIEWED": "已完成审核", "UNREVIEWED": "尚未完成审核"}
    physics = {"PASSED": "阶段门禁通过，具体仿真与达标结论见第 4 节", "NOT_EVALUATED": "尚未评估",
               "NOT_APPLICABLE": "当前体系无匹配模型，不影响其他研究目标的实验验证",
               "FALLBACK": "附条件交付，物理验证未通过或未完成", "FAILED": "物理验证未通过",
               "SKIPPED": "已跳过物理仿真"}
    lines = ["**交付状态：** " + names.get(state.get("category"), "有条件候选报告") + "。交付不代表性能达标或实验验证通过。",
             "**验证进度：** " + review.get(state.get("review_status"), "尚未完成审核") + "；" + physics.get(state.get("physics_status"), "尚未完成物理验证") + "。"]
    scheme = doc.get("scheme") or {}
    for comp, match in (doc.get("rule_checks", {}).get("material_matches") or {}).items():
        parent = (scheme.get("rule_parents") or {}).get(comp)
        if (comp in ("cathode", "anode") and parent and match.get("basis") == "parent" and match.get("matched")
                and match.get("parent_declaration") == parent and match.get("declared_value") == scheme.get(comp)):
            name = "正极" if comp == "cathode" else "负极"
            lines.append(f"**规则匹配口径：** {name}按声明母体 {match['rule_material_id']} 匹配材料类别与相容性规则，"
                         "完整掺杂配方保留，掺杂位点、性能和协同效应仍需独立验证；母体匹配不赋予PINN模型适用性。")
        composition = match.get("composition") or {}
        if (comp == "electrolyte" and match.get("basis") == "composition" and match.get("matched")
                and composition.get("complete") and composition.get("raw") == scheme.get(comp)
                and match.get("declared_value") == scheme.get(comp)):
            lines.append("**电解液规则口径：** 按完整组成识别为碳酸酯体系，浓度、溶剂比例与添加成分保留原声明；"
                         "分类成功不代表该配方的相容性、性能或PINN适用性已验证。"
                         + ("溶剂比例未声明，保持待确认。" if composition.get("solvent_ratio") is None else ""))
    if unresolved_issues(doc):
        location = "6节附录" if doc.get("requirements") else "2.6 节"
        lines.append(f"**关键限制：** 尚有 {len(unresolved_issues(doc))} 项待复核事项，完整意见集中列于第 {location}。")
        lines.append("存在未解决相容性或硬约束的配置不能作为已认可的实验推荐；应先验证相应对策。")
    return "\n\n".join(lines)


def unresolved_issues(doc):
    """审核和交付意见仅在一处完整呈现，原始问题不截断、不改写。"""
    groups = [(doc.get("report_readiness") or {}).get("issues", []),
              (doc.get("reviewer_output") or {}).get("issues", []),
              (doc.get("ledger_view") or {}).get("issues", [])]
    return list(dict.fromkeys(str(issue) for group in groups for issue in group))


def check(task_dir, doc):
    errors = projection_errors(task_dir, doc)
    if doc.get("numeric_contract"):
        from .numeric_consistency import document_audit, messages
        from .numeric_bindings import binding_errors
        numeric = messages(document_audit(doc)) + binding_errors(doc.get("final_answer", ""), doc.get("ledger_view", {}).get("parameters", {}))
        disclosed = unresolved_issues(doc)
        if numeric and (doc.get("report_readiness", {}).get("category") == "COMPLETE" or any(i not in disclosed for i in numeric)):
            errors.append("当前数值冲突未按附条件交付披露：" + "；".join(numeric))
    if doc.get("design_structure"):
        from .design_structure import structure_issues
        errors.extend(structure_issues(doc.get("final_answer", ""), doc["design_structure"]))
    if doc.get("requirements"):
        from .research_contract import document_errors
        research_errors = document_errors(doc)
        if doc.get("report_readiness", {}).get("category") == "DIAGNOSTIC":
            research_errors = [e for e in research_errors if "用户固定要求与主方案不一致" not in e]
        errors.extend(research_errors)
    from .scheme_ledger import load
    if not load(task_dir):
        errors.append("缺少当前统一方案表，无法确认参数一致性")
    if doc.get("report_readiness", {}).get("category") == "DIAGNOSTIC":
        from .scheme_ledger import load
        existing = (load(task_dir) or {}).get("issues", [])
        errors = [e for e in errors if e not in existing]
    if not isinstance(doc.get("report_readiness"), dict):
        errors.append("缺少交付状态")
    return errors


def restore_diagnostic(doc, ledger, reason):
    """冲突候选不写入方案，保留表中已接受的值并交付诊断。"""
    params = ledger.get("parameters", {})
    original_text = doc.get("final_answer", "")
    original_research = copy.deepcopy(doc.get("research"))
    doc.setdefault("rejected_designs", []).append({"reason": reason, "scheme": copy.deepcopy(doc.get("scheme")),
        "scheme_parameters": copy.deepcopy(doc.get("scheme_parameters")), "research": original_research})
    from .material_ids import assemble_components
    scheme = assemble_components({}, params, strict=False)
    geo = {"cathode": {}, "anode": {}, "condition": {}}
    for path, field in params.items():
        comp, _, name = path.partition(".")
        if comp in geo and name in ("L_um", "porosity", "R_s_um", "D_s_m2s", "loading_mg_cm2", "compaction_g_cm3", "v_min", "v_max", "c_rate"):
            geo[comp][name] = field["value"]
    scheme["geometry"] = geo
    if "electrolyte.additives" in params:
        scheme["additives"] = params["electrolyte.additives"]["value"]
    if "requirements.target_energy_wh_kg" in params:
        scheme["target_energy_wh_kg"] = params["requirements.target_energy_wh_kg"]["value"]
    doc.update(scheme=scheme, ledger_view=copy.deepcopy(ledger), ledger_hash=digest(ledger), ledger_revision=ledger["revision"])
    doc.pop("scheme_parameters", None)
    doc["report_readiness"].update(category="DIAGNOSTIC")
    doc["review_status"] = "REVIEWED_WITH_ISSUES"
    doc["report_readiness"]["issues"].append("一致性冲突，已保留方案表而未采用冲突候选：" + reason)
    doc["report_readiness"]["issues"].extend(ledger.get("issues", []))
    doc["final_answer"] = ("本轮候选与当前方案存在未解决冲突，未将该候选作为推荐；以下仅保留研究分析，参数以已接受方案表为准。\n\n" + original_text)
    if doc.get("requirements"):
        from .research_records import collect_research
        # 按当前已接受参数重验，只有受冲突/失效引用影响的记录降级。
        doc["research"] = collect_research({}, doc, params, previous=original_research)
        doc["research"]["diagnostics"].append("当前方案存在冲突：" + reason)
