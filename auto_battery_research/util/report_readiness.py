"""报告交付状态与科研验证状态分开维护。"""
import copy
import json
import math

from .scheme_ledger import digest, projection_errors


def prepare(doc, ledger, warnings, candidate=None):
    doc["delivery_contract"] = 1
    scheme = doc.setdefault("scheme", {})
    params = ledger.get("parameters", {})
    target = params.get("requirements.target_energy_wh_kg", {}).get("value")
    if target is None:
        scheme.pop("target_energy_wh_kg", None)
    else:
        scheme["target_energy_wh_kg"] = target
    for comp in ("cathode", "anode", "electrolyte"):
        fixed = params.get(comp + ".material") or {}
        if fixed.get("value") is not None:
            scheme[comp] = fixed["value"]
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
    issues = list(dict.fromkeys(str(w) for w in warnings))
    if not doc.get("evidence"):
        issues.append("未归档有效文献证据，不能把机理假设或工程建议作为文献实证。")
    issues += [str(w) for w in (doc.get("reviewer_output") or {}).get("issues", [])]
    if not doc.get("review_completed"):
        issues.append("未完成有效学术审核，须人工复核；确定性检查不能替代学术审核。")
    category = "CONDITIONAL" if issues else "COMPLETE"
    if not all(scheme.get(c) for c in ("cathode", "anode", "electrolyte")):
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
               "FALLBACK": "附条件交付，物理验证未通过或未完成", "FAILED": "物理验证未通过",
               "SKIPPED": "已跳过物理仿真"}
    lines = ["**交付状态：** " + names.get(state.get("category"), "有条件候选报告") + "。交付不代表性能达标或实验验证通过。",
             "**验证进度：** " + review.get(state.get("review_status"), "尚未完成审核") + "；" + physics.get(state.get("physics_status"), "尚未完成物理验证") + "。"]
    if unresolved_issues(doc):
        lines.append(f"**关键限制：** 尚有 {len(unresolved_issues(doc))} 项待复核事项，完整意见集中列于第 2.6 节。")
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
    scheme = {c: params[c + ".material"]["value"] for c in ("cathode", "anode", "electrolyte", "separator", "current_collector") if c + ".material" in params}
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
    doc["final_answer"] = "本轮候选与当前方案存在未解决冲突，未将该候选作为推荐。原始文献与失败设计保留在RAG档案；须先核对明确材料与参数，再开展定量性能验证。"
