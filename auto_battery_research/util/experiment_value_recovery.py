"""实验参数缺项显式迁移：副本验收、保留模型预算和原文、失败回滚。"""
from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime
from pathlib import Path

from .scheme_ledger import atomic_write, digest, require_current, task_lock
from .experiment_values import separate_pending, pending_rows, normalize_legacy_collection


def write(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def repair_experiment_values(mgr):
    from . import experiment_design as recipes
    from .experiment_consistency import stamp, parameter_pending_fields
    from .experiment_review import status as science_status
    CHECKPOINT = "experiment_generation_checkpoint.json"
    from .material_migration import _preview_manager
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    from auto_battery_research.checkers.stage5_checker import Stage5Checker
    task = Path(mgr.get_task_output_dir())
    initial = {p.name: p.read_bytes() for p in task.iterdir() if p.is_file()}
    original = json.loads(initial["experiment_plan.json"])
    doc = json.loads(initial["design_scheme.json"])
    require_current(task, doc)
    record, changed = separate_pending(original)
    if record == original:
        recipes.validate(record, doc, delivery=True)
        return {"success": True, "changed": False, "model_calls": 0}
    history = task / "experiment_value_recovery_history" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    preview = history / "preview"
    preview.mkdir(parents=True)
    for name, data in initial.items():
        (history / name).write_bytes(data)
        (preview / name).write_bytes(data)
    for directory in ("scheme_history", "design_revisions"):
        if (task / directory).exists():
            shutil.copytree(task / directory, preview / directory)
    summary = {"changed": True, "fields": changed, "model_calls": 0, "history": str(history),
               "normalized_collections": [e["field"] for e in record.get("parameter_value_history", [])
                                          if e not in original.get("parameter_value_history", [])]}
    try:
        from .experiment_titles import normalize_step_titles
        normalize_step_titles(record)
        stamp(record, doc)
        record["plan_hash"] = recipes.plan_hash(record)
        record["bindings"] = recipes.validate(record, doc, delivery=True)
        write(preview / "experiment_plan.json", record)
        cp = json.loads(initial[CHECKPOINT]) if CHECKPOINT in initial else None
        if cp is not None:
            before_batches = copy.deepcopy(cp.get("batches"))
            accepted_history = {}
            for slot, accepted in cp.get("accepted", {}).items():
                if not slot.startswith("parameter:"):
                    continue
                key, row = accepted["field"], accepted["row"]
                if key in pending_rows(record):
                    accepted_history[slot] = copy.deepcopy(accepted)
                    accepted["row"] = copy.deepcopy(record["pending_parameters"][key])
                else:
                    normalized = normalize_legacy_collection(key, row)
                    if normalized != row:
                        accepted_history[slot] = copy.deepcopy(accepted)
                        accepted["row"] = normalized
            cp.setdefault("value_migration_history", []).append({"fields": changed, "accepted_before": accepted_history,
                "original_checkpoint_hash": digest(json.loads(initial[CHECKPOINT])), "history": str(history.relative_to(task))})
            for key in ("assembled", "result"):
                if isinstance(cp.get(key), dict):
                    normalized, _ = separate_pending(cp[key])
                    normalize_step_titles(normalized)
                    stamp(normalized, doc)
                    normalized["plan_hash"] = recipes.plan_hash(normalized)
                    normalized["bindings"] = recipes.validate(normalized, doc, delivery=True)
                    cp[key] = normalized
            cp["review_invalidated_reason"] = "实验参数缺项迁移改变输入，原审核保留、预算不重置"
            assert cp.get("batches") == before_batches
            write(preview / CHECKPOINT, cp)
        if "experiment_draft.json" in initial:
            draft = json.loads(initial["experiment_draft.json"])
            if isinstance(draft.get("record"), dict):
                draft.setdefault("audit", {})["parameter_value_migration"] = {"fields": changed, "model_calls": 0}
                draft["record"] = copy.deepcopy(record)
                write(preview / "experiment_draft.json", draft)
        doc["experiment_plan_hash"] = record["plan_hash"]
        fields = sorted(set(parameter_pending_fields(doc)) | set(record.get("pending_parameters") or {}))
        outcome, reasons = recipes.delivery_status(record, fields, doc=doc)
        readiness = doc.get("report_readiness")
        if readiness:
            readiness["experiment_status"] = outcome
            readiness["issues"] = [i for i in readiness.get("issues", []) if not str(i).startswith("实验交付限制：")]
            readiness["issues"].append("实验交付限制：" + "；".join(reasons))
            if readiness.get("category") == "COMPLETE":
                readiness["category"] = "CONDITIONAL"
        write(preview / "design_scheme.json", doc)
        execution = json.loads(initial.get("stage5_execution.json", b"{}"))
        execution.setdefault("experiment", {}).update(status=outcome, reasons=reasons, plan_hash=record["plan_hash"],
            pending_fields=fields, origin=record.get("origin"))
        write(preview / "stage5_execution.json", execution)
        write(preview / "experiment_plan_failure.json", {"resolved": False, "repair_status": "PARTIAL", "warnings": reasons})
        manager = _preview_manager(mgr, preview)
        rendered = run_synthesis_report(target_query=mgr.target_goal, stage_manager=manager)
        if not rendered.get("success"):
            raise ValueError("缺项迁移副本报告生成失败：" + str(rendered))
        checks = {}
        for stage, checker_type in ((5, Stage5Checker), (6, FinalReportChecker)):
            checker = checker_type()
            checker.on_init(manager, {"id": stage}, mgr.config)
            passed, diagnostic = checker.do_check()
            checks[str(stage)] = {"passed": passed, "diagnostic": diagnostic}
            if not passed:
                raise ValueError(f"缺项迁移副本Stage {stage}验收失败：{diagnostic}")
        names = ("design_scheme.json", "experiment_plan.json", "experiment_draft.json", CHECKPOINT,
                 "stage5_execution.json", "experiment_plan_failure.json", "experiment_report.json", "final_research_report.md")
        with task_lock(task):
            if any(not (task / name).exists() or (task / name).read_bytes() != data for name, data in initial.items()):
                raise ValueError("缺项迁移期间任务已变化，拒绝覆盖")
            written = []
            try:
                for name in names:
                    if (preview / name).exists():
                        content = (preview / name).read_text(encoding="utf-8")
                        if name == "final_research_report.md":
                            content = content.replace(str(preview), str(task))
                        atomic_write(task / name, content)
                        written.append(name)
                checker = FinalReportChecker()
                checker.on_init(mgr, {"id": 6}, mgr.config)
                passed, diagnostic = checker.do_check()
                if not passed:
                    raise ValueError("缺项迁移提交后终审失败：" + str(diagnostic))
            except Exception:
                for name in written:
                    if name in initial:
                        (task / name).write_bytes(initial[name])
                    else:
                        (task / name).unlink(missing_ok=True)
                raise
        summary.update(success=True, checks=checks, scientific_review=science_status(record, doc),
                       plan_hash=record["plan_hash"], pending_parameters=sorted(pending_rows(record)))
        write(history / "audit.json", summary)
        return summary
    except Exception as exc:
        summary.update(success=False, error=str(exc))
        write(history / "failure.json", summary)
        return summary
