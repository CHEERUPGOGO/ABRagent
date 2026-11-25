"""最终报告的统一当前记录：锁内取样、版本校验、只读投影与发布前复核。"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .scheme_ledger import atomic_write, digest, task_lock

VERSION = 1
FILENAME = "report_record.json"
INPUTS = ("design_scheme.json", "scheme_ledger.json", "experiment_plan.json", "stage5_execution.json",
          "pinn_simulation_result.json", "pinn_input_spec.json", "design_scheme.md", "stage_journals.json")


def _read_inputs(task):
    return {name: (task / name).read_bytes() if (task / name).exists() else None for name in INPUTS}


def hashes(inputs):
    result = {name: hashlib.sha256(value).hexdigest() if value is not None else None for name, value in inputs.items()}
    if inputs.get("stage_journals.json"):
        journals = json.loads(inputs["stage_journals.json"])
        if isinstance(journals, list):
            # Stage 6完成日志是本报告的输出，不能再当作科学输入使自身过期。
            result["stage_journals.json"] = digest([j for j in journals if str(j.get("stage_id")) != "6"])
    return result


def _json(inputs, name, default=None):
    value = inputs.get(name)
    return json.loads(value) if value is not None else copy.deepcopy(default if default is not None else {})


def capture(task_dir, expected_doc=None, result=None, spec=None):
    """最新版指当前被选定且引用一致的版本，不按文件修改时间挑选科学内容。"""
    from .scheme_ledger import require_current
    from .experiment_design import require_current as require_experiment
    task = Path(task_dir)
    with task_lock(task):
        inputs = _read_inputs(task)
        if inputs["design_scheme.json"] is None:
            return None
        doc = _json(inputs, "design_scheme.json")
        if expected_doc is not None:
            expected = {k: v for k, v in expected_doc.items() if k != "_report_record"}
            if doc != expected:
                raise ValueError("报告取样前设计记录已变化，请重新生成")
        require_current(task, doc)
        recipe = _json(inputs, "experiment_plan.json") if doc.get("experiment_contract") else {}
        if doc.get("experiment_contract"):
            require_experiment(task, doc)
        execution = _json(inputs, "stage5_execution.json")
        if recipe and execution.get("experiment", {}).get("plan_hash") not in (None, recipe.get("plan_hash")):
            raise ValueError("阶段执行状态与当前实验计划不属于同一版本")
        physics_hash = execution.get("physics", {}).get("input_hash")
        if physics_hash and physics_hash != digest(doc.get("scheme")):
            raise ValueError("物理执行状态与当前主方案不属于同一版本")
        for name, selected in (("pinn_simulation_result.json", result), ("pinn_input_spec.json", spec)):
            if selected and _json(inputs, name) != selected:
                raise ValueError("报告仿真输入已变化：" + name)
        record = {"version": VERSION, "input_hashes": hashes(inputs), "design": doc,
                  "experiment": recipe, "execution": execution, "result": copy.deepcopy(result or {}),
                  "spec": copy.deepcopy(spec or {}),
                  "scheme_markdown": inputs["design_scheme.md"].decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n") if inputs["design_scheme.md"] else "",
                  "journals": _json(inputs, "stage_journals.json", [])}
        record["view"] = project(record)
        if hashes(_read_inputs(task)) != record["input_hashes"]:
            raise ValueError("报告取样期间来源发生变化，拒绝混合版本")
        record["record_id"] = digest(record)
        return record


def project(record):
    """当前回答只对应本轮论证和实验，旧提案/审稿文本保留为历史资料。"""
    from .research_narrative import display_narrative
    from .research_contract import document_errors
    from .report_readiness import unresolved_issues
    from .experiment_design import delivery_status
    from .research_reporting import fixed_condition_response
    doc, recipe = record["design"], record["experiment"]
    narrative = display_narrative(doc)
    paragraph_map = {}
    if narrative:
        for n, section in enumerate(narrative["sections"], 1):
            for paragraph in section["paragraphs"]:
                row = {"id": paragraph["id"], "location": f"第2.{n}节·{section['title']}",
                       "text": paragraph["text"], "citations": paragraph["citations"],
                       "issues": narrative["paragraph_reviews"][paragraph["id"]]}
                for rid in paragraph["requirement_ids"]:
                    paragraph_map.setdefault(rid, []).append(copy.deepcopy(row))
    tests = {row["requirement_id"]: copy.deepcopy(row) for row in recipe.get("requirement_tests", [])}
    coverage = []
    for item in (doc.get("requirements") or {}).get("items", []):
        if item.get("kind") == "background":
            continue
        rid = item["id"]
        coverage.append({"requirement_id": rid, "requirement": copy.deepcopy(item),
                         "fixed_response": fixed_condition_response(item, doc),
                         "paragraphs": paragraph_map.get(rid, []), "experiment": tests.get(rid)})
    active = []
    rules = (doc.get("rule_checks") or {}).get("rule_checks", {})
    for violation in rules.get("violations", []):
        if isinstance(violation, dict):
            active.append("当前相容性规则" + str(violation.get("rule_id", "")) + "：" + str(violation.get("reason", "待核验")))
    for issue in document_errors(doc) if doc.get("requirements") else []:
        active.append("当前记录校验：" + issue)
    if doc.get("numeric_contract"):
        from .numeric_consistency import document_audit, messages
        active += messages(document_audit(doc))
    active += [str(s) for s in (doc.get("requirements") or {}).get("diagnostics", [])]
    active += [str(s) for s in (doc.get("research") or {}).get("diagnostics", [])]
    if narrative and narrative["status"] != "approved":
        active += ["当前论证：" + s for s in narrative.get("diagnostics", [])]
    elif doc.get("research_narrative") and not narrative:
        active.append("当前论证审核已过期，原稿仅作历史资料，需基于当前输入复核。")
    if not doc.get("review_completed"):
        active.append("尚未完成有效科学审核，当前记录不代表科学结论通过。")
        active += [str(s) for s in (doc.get("reviewer_output") or {}).get("issues", [])]
    elif not doc.get("review_passed"):
        active.append("原科学审核仍有保留；当前论证表述通过不提升方案的科学验证状态，历史意见及适用范围见归档。")
    physics = record["execution"].get("physics", {})
    if physics and physics.get("status") in ("FAILED", "FALLBACK"):
        active.append("当前物理验证状态：" + str(physics.get("diagnostic") or physics.get("reason") or physics["status"]))
    if recipe:
        status, reasons = delivery_status(recipe, doc=doc)
        active += ["当前实验限制：" + r for r in reasons]
    else:
        status = (doc.get("report_readiness") or {}).get("experiment_status")
    historical = list(dict.fromkeys(unresolved_issues(doc)
                       + [str(s) for s in (doc.get("research_consistency_review") or {}).get("issues", [])]))
    if not doc.get("requirements") or not narrative:
        # 没有新的有效论证承接时，原科学问题仍是当前保留项，不能因改版而隐去。
        active = list(dict.fromkeys(active + unresolved_issues(doc)))
    return {"coverage": coverage, "active_issues": list(dict.fromkeys(active)),
            "historical_issues": historical, "narrative": narrative, "experiment_status": status,
            "physics_status": physics.get("status") or (doc.get("report_readiness") or {}).get("physics_status")}


def attach(record):
    doc = copy.deepcopy(record["design"])
    doc["_report_record"] = record
    return doc


def marker(record):
    return "<!-- abr-report-record:" + record["record_id"] + " -->"


def validate_saved(task_dir, record):
    if not isinstance(record, dict) or record.get("version") != VERSION:
        raise ValueError("统一报告记录缺失或版本不支持")
    if record.get("record_id") != digest({k: v for k, v in record.items() if k != "record_id"}):
        raise ValueError("统一报告记录内容指纹不匹配")
    if hashes(_read_inputs(Path(task_dir))) != record.get("input_hashes"):
        raise ValueError("报告记录已过期：主方案、实验或执行记录已改变，须重新生成全篇")


def for_check(task_dir, doc, result, spec, content, supplement=None):
    """旧报告兼容读取；带新标记的报告必须与当前快照及其来源一致。"""
    if "<!-- abr-report-record:" not in content:
        return doc
    task = Path(task_dir)
    record = json.loads((task / FILENAME).read_text(encoding="utf-8"))
    validate_saved(task, record)
    if marker(record) not in content or record["design"] != doc or record["result"] != (result or {}) or record["spec"] != (spec or {}):
        raise ValueError("报告各部分没有使用同一当前记录")
    if project(record) != record["view"]:
        raise ValueError("报告当前记录投影与最新渲染规则不一致")
    from .report_consistency import check
    errors = check(content, record, supplement)
    if errors:
        raise ValueError("；".join(errors))
    return attach(record)


def publish(task_dir, record, report_text, supplement_text=None):
    """发布前重新比较全部输入，失败回滚记录与报告，拒绝多版本混合。"""
    task = Path(task_dir)
    from .report_consistency import check
    errors = check(report_text, record, supplement_text)
    if errors:
        raise ValueError("报告全篇一致性检查失败：" + "；".join(errors))
    files = {FILENAME: json.dumps(record, ensure_ascii=False, indent=2), "final_research_report.md": report_text}
    if supplement_text is not None:
        from .report_bundle import SUPPLEMENT_FILENAME
        files[SUPPLEMENT_FILENAME] = supplement_text
    with task_lock(task):
        validate_saved(task, record)
        previous = {n: (task / n).read_bytes() if (task / n).exists() else None for n in files}
        try:
            for name, text in files.items():
                atomic_write(task / name, text)
            validate_saved(task, record)
        except Exception:
            for name, value in previous.items():
                if value is None:
                    (task / name).unlink(missing_ok=True)
                else:
                    (task / name).write_bytes(value)
            raise
