"""材料标识显式迁移：副本验收、原文保留、失败回滚，不运行RAG/PINN/实验模型。"""
import copy
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from .material_ids import VERSION, FIELDS, canonical_document, canonical_parameters, canonical_id, canonical_experiment, checkpoint_identity
from .scheme_ledger import atomic_write, digest, load, require_current, task_lock, calculate, _save


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))


def _preview_manager(mgr, path):
    stages = [SimpleNamespace(**{key: getattr(stage, key, None) for key in (
        "id", "key", "name", "description", "status", "skip", "allow_skip", "skip_reason")}) for stage in mgr.stages]
    return SimpleNamespace(config=mgr.config, root_dir=mgr.root_dir, target_goal=mgr.target_goal,
        stages=stages, is_legacy_task=getattr(mgr, "is_legacy_task", False), get_task_output_dir=lambda *a, **k: path,
        get_all_stage_journal=mgr.get_all_stage_journal, get_stage_by_id=lambda i: next((s for s in stages if s.id == i), None))


def _proposal(original, ledger):
    from auto_battery_research.simulation.geometry_contract import synchronize_material_baselines, geometry_hash, input_diagnostic
    doc = canonical_document(original)
    data = copy.deepcopy(ledger)
    data["parameters"] = canonical_parameters(data["parameters"])
    data["material_id_version"] = VERSION
    changes = []
    for path in sorted(FIELDS):
        old = ledger["parameters"].get(path) or {}
        new = data["parameters"].get(path) or {}
        if old.get("value") != new.get("value"):
            changes.append({"path": path, "before": old.get("value"), "after": new.get("value"), "kind": "material_id"})
    geo = doc.get("scheme", {}).get("geometry")
    if not isinstance(geo, dict) or geo.get("contract_version") != "2.0":
        raise ValueError("已有任务缺少结构化几何，材料迁移不能从正文补造参数")
    synced = synchronize_material_baselines(geo, doc["scheme"])
    for comp in ("cathode", "anode"):
        for field in ("L_um", "porosity", "R_s_um", "D_s_m2s"):
            path = comp + "." + field
            before, after = (geo.get(comp) or {}).get(field), synced[comp].get(field)
            if before != after:
                if before is not None:
                    raise ValueError("迁移不得修改已有几何值：" + path)
                current = data["parameters"].get(path) or {}
                if current.get("locked") or current.get("value") is not None:
                    raise ValueError("模型基准与已锁定或非空方案参数冲突：" + path)
                row = {"value": after, "unit": "um" if field == "R_s_um" else "m2/s",
                       "basis": "model_reference", "source": "匹配PINN体系的训练参数基准（材料标识迁移）",
                       "meaning": "模型基准，不是本批材料的实测值", "conditions": {}, "locked": False}
                data["parameters"][path] = row
                if path in doc.get("scheme_parameters", {}):
                    doc["scheme_parameters"][path] = copy.deepcopy(row)
                changes.append({"path": path, "before": before, "after": after, "kind": "model_baseline"})
    geometry_changed = synced != geo
    doc["scheme"]["geometry"] = synced
    doc["scheme"]["geometry_hash"] = geometry_hash(synced)
    if geometry_changed:
        doc["scheme"]["design_revision"] = doc["scheme"].get("design_revision", 0) + 1
    diagnostic = input_diagnostic(doc["scheme"], doc.get("evidence") or [])
    touched = doc != original or data != ledger
    if touched:
        data["revision"] += 1
        data["derived"], data["issues"] = calculate(data)
        data["history"].append({"revision": data["revision"], "actor": "agent", "reason": "材料规范标识与几何模型基准同步",
                                "changed": sorted({c["path"] for c in changes})})
    doc.update(ledger_view=copy.deepcopy(data), ledger_hash=digest(data), ledger_revision=data["revision"], material_id_version=VERSION)
    return doc, data, changes, diagnostic, touched


def _checkpoint_updates(task, doc, data):
    updates = {}
    for name in ("stage4_repair_checkpoint.json", "rag_result.json", "stage4_retrieval_checkpoint.json"):
        if not (task / name).exists():
            continue
        old = _read(task / name)
        current = canonical_document(old)
        if name != "stage4_retrieval_checkpoint.json":
            current["scheme"] = copy.deepcopy(doc["scheme"])
            for path, field in data["parameters"].items():
                if path.endswith("D_s_m2s") and path in current.get("parameters", {}) and current["parameters"][path].get("value") is None:
                    current["parameters"][path] = copy.deepcopy(field)
            if current.get("research_draft"):
                current["research_draft"].update(scheme=copy.deepcopy(doc["scheme"]), ledger_view=copy.deepcopy(data))
        if name != "rag_result.json":
            current["checkpoint_identity"] = checkpoint_identity(doc["target"], data, doc.get("provenance") or {})
        if current != old:
            updates[name] = current
    return updates


def _sync_experiment(record, doc):
    from .experiment_design import validate, plan_hash, input_fingerprint
    from .experiment_consistency import stamp
    result = canonical_experiment(record)
    result["base_ledger_hash"] = doc["ledger_hash"]
    stamp(result, doc)
    recipe_doc = copy.deepcopy(doc)
    recipe_doc["evidence"] = doc.get("experiment_evidence") or doc.get("evidence") or []
    recipe_doc["physics_context"] = {"status": doc.get("report_readiness", {}).get("physics_status", "NOT_EVALUATED"), "result": {}}
    result["input_fingerprint"] = input_fingerprint(recipe_doc, doc.get("final_answer", "")[:12000])
    result["plan_hash"] = plan_hash(result)
    result["bindings"] = validate(result, doc)
    return result


def preview_material_migration(root, goal):
    """CLI预览仅定位既有任务，不创建管理器、目录、状态或配置快照。"""
    tasks = Path(root) / "output" / "tasks"
    matches = []
    for task in tasks.iterdir() if tasks.exists() else []:
        if not task.is_dir() or not (task / "design_scheme.json").exists():
            continue
        try:
            if _read(task / "design_scheme.json").get("target") == goal:
                matches.append(task)
        except (ValueError, OSError):
            continue
    if len(matches) != 1:
        return {"success": False, "error": "未找到唯一的既有课题目录，请使用已保存的完整课题名称"}
    return migrate_material_ids(SimpleNamespace(get_task_output_dir=lambda: matches[0]), dry_run=True)


def migrate_material_ids(mgr, dry_run=False):
    task = mgr.get_task_output_dir()
    original = _read(task / "design_scheme.json")
    ledger = load(task)
    if not ledger:
        return {"success": False, "error": "材料迁移需要现有方案表"}
    try:
        require_current(task, original)
        doc, data, changes, diagnostic, touched = _proposal(original, ledger)
        checkpoint_updates = _checkpoint_updates(task, doc, data)
        touched = touched or bool(checkpoint_updates)
    except (ValueError, TypeError, KeyError) as exc:
        return {"success": False, "error": str(exc)}
    summary = {"success": True, "dry_run": dry_run, "changed": touched, "changes": changes,
               "physics": diagnostic, "checkpoint_updates": sorted(checkpoint_updates), "report_file": str(task / "final_research_report.md")}
    if dry_run or not touched:
        return summary
    initial = {p.name: p.read_bytes() for p in task.iterdir() if p.is_file()}
    if json.loads(initial["design_scheme.json"]) != original or json.loads(initial["scheme_ledger.json"]) != ledger:
        return {"success": False, "error": "准备迁移时任务版本发生变化，请重新读取后重试"}
    history = task / "material_migration_history" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    preview = history / "preview"
    preview.mkdir(parents=True)
    for name, content in initial.items():
        (history / name).write_bytes(content)
        (preview / name).write_bytes(content)
    for directory in ("design_revisions", "scheme_history"):
        if (task / directory).exists():
            import shutil
            shutil.copytree(task / directory, preview / directory)
    try:
        _save(preview, data)
        doc["physics_input_diagnostic"] = diagnostic
        state = doc.setdefault("report_readiness", {})
        if diagnostic.get("errors"):
            state["physics_status"] = "FAILED"
            if state.get("category") == "COMPLETE":
                state["category"] = "CONDITIONAL"
            state.setdefault("issues", []).append("物理输入仍待修复：" + "；".join(diagnostic["errors"]))
        # 无有效本轮推理结果时不能沿用旧物理已通过状态。
        elif state.get("physics_status") in ("PASSED", "CONVERGED"):
            state["physics_status"] = "NOT_EVALUATED"
        if doc.get("requirements"):
            from .research_contract import finalize_research
            from .research_support import review_pending
            finalize_research(doc)
            summary["summary_fields_pending"] = len(review_pending(doc.get("research") or {}, doc, data["parameters"]))
        _write(preview / "design_scheme.json", doc)
        if (preview / "experiment_plan.json").exists():
            experiment = _sync_experiment(_read(preview / "experiment_plan.json"), doc)
            doc["experiment_plan_hash"] = experiment["plan_hash"]
            _write(preview / "experiment_plan.json", experiment)
        for name, current in checkpoint_updates.items():
            _write(preview / name, current)
        # 旧仿真仍在备份中，但不能以旧身份进入当前报告或阻断新报告验收。
        stale_files = [name for name in ("pinn_input_spec.json", "pinn_simulation_result.json", "pinn_simulation_curve.png",
                                       "pinn_optimization_state.json", "pinn_simulation_history.json") if (preview / name).exists()]
        for name in stale_files:
            (preview / name).unlink()
        execution = _read(preview / "stage5_execution.json") if (preview / "stage5_execution.json").exists() else {}
        execution["physics"] = {"status": "FAILED" if diagnostic.get("errors") else "PENDING", "passed": False,
            "input_hash": digest(doc["scheme"]), "diagnostic": diagnostic, "migration": "规范材料标识与模型基准同步，未执行推理"}
        _write(preview / "stage5_execution.json", execution)
        doc["material_migration"] = {"version": VERSION, "changes": changes, "geometry_before": digest(original["scheme"]["geometry"]),
                                      "geometry_after": digest(doc["scheme"]["geometry"]), "history": str(history.relative_to(task))}
        _write(preview / "design_scheme.json", doc)
        from auto_battery_research.tools.workflow_actions import run_synthesis_report
        from auto_battery_research.checkers.final_report_checker import FinalReportChecker
        manager = _preview_manager(mgr, preview)
        result = run_synthesis_report(mgr.target_goal, stage_manager=manager)
        checker = FinalReportChecker()
        checker.on_init(manager, {"id": 6}, mgr.config)
        passed, report_diagnostic = checker.do_check() if result.get("success") else (False, result)
        if not passed:
            raise ValueError("迁移副本未通过报告验收：" + str(report_diagnostic))
        names = ("scheme_ledger.json", "scheme_table.md", "design_scheme.json", "experiment_plan.json", "experiment_report.json",
                 "final_research_report.md", "stage4_repair_checkpoint.json", "stage4_retrieval_checkpoint.json", "rag_result.json", "stage5_execution.json")
        snapshot = task / "scheme_history" / f"revision_{data['revision']}.json"
        snapshot_content = json.dumps(data, ensure_ascii=False, indent=2)
        if snapshot.exists() and _read(snapshot) != data:
            raise ValueError("方案版本历史已存在不同内容，迁移不能覆盖历史快照")
        with task_lock(task):
            if any(not (task / name).exists() or (task / name).read_bytes() != content for name, content in initial.items()):
                raise ValueError("迁移期间任务文件发生变化，拒绝覆盖")
            written, removed = [], []
            snapshot_created = False
            try:
                if not snapshot.exists():
                    atomic_write(snapshot, snapshot_content)
                    snapshot_created = True
                for name in names:
                    if (preview / name).exists():
                        content = (preview / name).read_text(encoding="utf-8")
                        if name == "final_research_report.md":
                            content = content.replace(str(preview), str(task))
                        atomic_write(task / name, content)
                        written.append(name)
                for name in stale_files:
                    (task / name).unlink(missing_ok=True)
                    removed.append(name)
                checker.on_init(mgr, {"id": 6}, mgr.config)
                if not checker.do_check()[0]:
                    raise ValueError("迁移提交后的报告验收失败")
            except Exception:
                for name in written:
                    if name in initial:
                        (task / name).write_bytes(initial[name])
                    else:
                        (task / name).unlink(missing_ok=True)
                for name in removed:
                    (task / name).write_bytes(initial[name])
                if snapshot_created:
                    snapshot.unlink(missing_ok=True)
                raise
        summary.update(history=str(history), changed=True)
        _write(history / "audit.json", dict(summary, passed=True, model_calls=0, report_diagnostic=report_diagnostic))
        return summary
    except (ValueError, TypeError, KeyError, OSError) as exc:
        _write(history / "failure.json", {"error": str(exc), "changes": changes, "physics": diagnostic})
        return {"success": False, "error": str(exc), "history": str(history)}
