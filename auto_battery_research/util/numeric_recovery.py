"""既有报告只补确定性数值审计；副本验收后发布，不选择冲突值或重置模型预算。"""
import copy
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from .numeric_consistency import document_audit, messages
from .numeric_bindings import binding_errors
from .scheme_ledger import digest, require_current
from .stage4_review_recovery import PUBLISH_FILES, _read, _save, _publish, _sync_experiment


def audit_existing_design(mgr):
    task = Path(mgr.get_task_output_dir())
    original = _read(task / "design_scheme.json")
    require_current(task, original)
    prior = _read(task / "stage4_repair_checkpoint.json")
    snapshot = document_audit(original)
    if original.get("numeric_consistency") == snapshot and (task / "numeric_consistency.json").exists():
        from auto_battery_research.checkers.final_report_checker import FinalReportChecker
        checker = FinalReportChecker()
        checker.on_init(mgr, {"id": 6}, mgr.config)
        valid, _ = checker.do_check(is_complete=True)
        if valid and _read(task / "numeric_consistency.json") == snapshot:
            return {"success": True, "changed": False, "numeric_status": snapshot["status"],
                    "report_file": str(task / "final_research_report.md")}
    history = task / "numeric_audit_history" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stage, originals = history / "preview", history / "originals"
    stage.mkdir(parents=True)
    originals.mkdir()
    for source in task.iterdir():
        if source.is_file():
            shutil.copy2(source, stage / source.name)
            shutil.copy2(source, originals / source.name)
    if (task / "design_revisions").exists():
        shutil.copytree(task / "design_revisions", stage / "design_revisions")
    manifest = {name: hashlib.sha256((task / name).read_bytes()).hexdigest() if (task / name).exists() else None
                for name in (*PUBLISH_FILES, "scheme_ledger.json", ".stage_state.json", "stage_journals.json", "stage_journals.md")}
    fingerprint = digest({"doc": original, "checkpoint": prior, "audit": snapshot})
    _save(history / "recovery.json", {"input_hash": fingerprint, "status": "AUDITING", "original_files": manifest,
                                    "journal_note": "已补充确定性数值一致性核对；未改科研正文、配方或审核预算，争议输入保留待确认。"})
    _save(stage / "numeric_consistency.json", snapshot)
    doc = copy.deepcopy(original)
    doc["numeric_contract"] = 1
    doc["numeric_consistency"] = snapshot
    diagnostics = messages(snapshot) + binding_errors(doc["final_answer"], doc["ledger_view"]["parameters"])
    doc["report_readiness"]["issues"] = list(dict.fromkeys(doc["report_readiness"].get("issues", []) + diagnostics))
    if diagnostics:
        doc["review_passed"] = False
        if doc["report_readiness"]["category"] == "COMPLETE":
            doc["report_readiness"]["category"] = "CONDITIONAL"
        doc["review_status"] = "REVIEWED_WITH_ISSUES"
    seed = copy.deepcopy(prior)
    seed.update(numeric_contract=1, numeric_consistency=snapshot)
    _save(stage / "stage4_repair_checkpoint.json", seed)
    _sync_experiment(stage, doc)
    _save(stage / "design_scheme.json", doc)
    raw = _read(stage / "rag_result.json") if (stage / "rag_result.json").exists() else {}
    raw.update(numeric_contract=1, numeric_consistency=snapshot)
    _save(stage / "rag_result.json", raw)
    result = _publish(mgr, task, stage, history, fingerprint, original, prior, doc)
    result.update(numeric_status=snapshot["status"], conflicts=len(snapshot["issues"]),
                  incomplete=sum(c["status"] == "INCOMPLETE" for c in snapshot["checks"]))
    return result
