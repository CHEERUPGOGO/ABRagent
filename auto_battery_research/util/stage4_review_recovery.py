"""恢复已有课题的正文审核；固定当前参数，在副本中完成补丁和报告验收。"""
import copy
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from .scheme_ledger import atomic_write, digest, require_current, task_lock

PUBLISH_FILES = ("design_scheme.json", "design_scheme.md", "stage4_repair_checkpoint.json", "rag_result.json",
                 "experiment_plan.json", "experiment_report.json", "stage5_execution.json", "final_research_report.md", "numeric_consistency.json")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def resume_stage4_review(mgr, writer_client=None, reviewer=None, *, restore_titles=False):
    from .adaptive_retrieval import cache_resume_matches
    from .design_repair import pending_structure_repair, review_design, review_verdict
    from .research_recovery import _research_client
    from auto_battery_research.rag import RAGPipeline, RelationEngine, ReviewerAgent

    task = Path(mgr.get_task_output_dir())
    original = _read(task / "design_scheme.json")
    require_current(task, original)
    if restore_titles and original.get("design_structure"):
        from .design_structure import structure_issues
        errors = structure_issues(original["final_answer"], original["design_structure"])
        return {"success": not errors, "changed": False, "error": "；".join(errors),
                "reason": "当前设计已使用程序章节契约"}
    checkpoint = task / "stage4_repair_checkpoint.json"
    if not checkpoint.exists():
        return {"success": False, "error": "缺少原审核检查点，无法恢复既有预算"}
    prior = _read(checkpoint)
    if not cache_resume_matches(prior, original, original.get("provenance") or {}):
        return {"success": False, "error": "检查点与当前正文、参数或证据不一致，不能恢复旧审核"}
    title_result = None
    if restore_titles:
        from .design_title_recovery import restore_titles as recover_titles
        try:
            title_result = recover_titles(prior)
            if not original["final_answer"].startswith(prior["draft"]):
                raise ValueError("原报告正文与检查点不是精确前缀，拒绝替换")
            restored_answer = title_result[0] + original["final_answer"][len(prior["draft"]):]
            for key, row in original["ledger_view"]["parameters"].items():
                quote = row.get("source_quote")
                if quote and quote in original["final_answer"] and quote not in restored_answer:
                    raise ValueError("恢复标题会使当前参数来源失效：" + key)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
    input_hash = digest({"document": original, "checkpoint": prior, **({"restore_titles": True} if restore_titles else {})})
    root = task / ("stage4_title_history" if restore_titles else "stage4_review_history")
    history = None
    for folder in sorted(root.glob("*/recovery.json"), reverse=True):
        metadata = _read(folder)
        if metadata.get("input_hash") == input_hash and metadata.get("status") != "PUBLISHED":
            history = folder.parent
            break
    if history is None:
        if not restore_titles and not pending_structure_repair(prior):
            return {"success": False, "error": "当前检查点没有可定位的格式修复，或审核/独立格式预算已耗尽"}
        history = root / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        history.mkdir(parents=True)
        stage = history / "preview"
        stage.mkdir()
        originals = history / "originals"
        originals.mkdir()
        for file in task.iterdir():
            if file.is_file():
                shutil.copy2(file, stage / file.name)
                shutil.copy2(file, originals / file.name)
        if (task / "design_revisions").exists():
            shutil.copytree(task / "design_revisions", stage / "design_revisions")
        manifest = {name: hashlib.sha256((task / name).read_bytes()).hexdigest() if (task / name).exists() else None
                    for name in (*PUBLISH_FILES, "scheme_ledger.json", ".stage_state.json", "stage_journals.json", "stage_journals.md")}
        atomic_write(history / "recovery.json", json.dumps({"input_hash": input_hash, "status": "REVIEWING", "original_files": manifest}))
    stage = history / "preview"
    seed = _read(stage / "stage4_repair_checkpoint.json")
    if restore_titles and not seed.get("title_recovery"):
        text, contract, entry = title_result
        seed["title_recovery"] = {"prior_review": copy.deepcopy(seed.get("reviewer_output")),
                                  "prior_pending_review": seed.pop("pending_review", None)}
        seed.update(draft=text, design_structure=contract)
        seed.setdefault("repair_audit", []).append(entry)
        seed.pop("reviewed_hash", None)
        seed.pop("research_reviewed_signature", None)
        seed["reviewer_output"] = dict(seed.get("reviewer_output") or {}, error_type="reviewer", confidence="low",
            review_status="STALE_TEXT", issues=list((seed.get("reviewer_output") or {}).get("issues", []))
            + ["标题已按归档原稿恢复，当前正文尚未完成重新审核；历史科学意见继续保留。"])
        _save(stage / "stage4_repair_checkpoint.json", seed)
    writer_client = None if restore_titles else (writer_client if writer_client is not None else _research_client(mgr, "writer"))
    engine = RelationEngine()
    reviewer = reviewer if reviewer is not None else ReviewerAgent(_research_client(mgr, "reviewer"), relation_engine=engine)
    if not restore_titles and (writer_client is None or not getattr(writer_client, "available", False)):
        return {"success": False, "error": "恢复正文修复需要可用的模型", "history": str(history)}
    # 既有 Stage 5 和研报引用当前参数；恢复正文只修论证和格式，参数始终冻结。
    parameters = copy.deepcopy(original["ledger_view"]["parameters"])
    frozen = {key: dict(row, locked=True) for key, row in parameters.items()}
    seed.update(parameters=frozen, scheme=copy.deepcopy(original["scheme"]))
    if original.get("requirements") and not seed.get("recovery_input_initialized"):
        seed["research_draft"] = copy.deepcopy(original)
    seed["recovery_input_initialized"] = True
    seed.setdefault("maintenance_calls", prior.get("maintenance_calls", 0))
    retrieval = seed.get("retrieval_state") or {}
    pipeline = SimpleNamespace(writer=SimpleNamespace(llm=writer_client), reviewer=reviewer,
        scheme_context=original["ledger_view"], report_first=True,
        repair_checkpoint_path=stage / "stage4_repair_checkpoint.json",
        _extract_energy_claim=RAGPipeline._extract_energy_claim,
        retrieval_options={"supplement_rounds": retrieval.get("supplement_rounds", 0),
                           "rebuild_rounds": retrieval.get("rebuild_rounds", 0)})
    if restore_titles:
        text, verdict, evidence, fields, audit = _review_restored_titles(pipeline, seed, original["target"], stage)
    else:
        text, verdict, evidence, fields, audit = review_design(pipeline, seed, original["target"])
    if fields != frozen:
        raise ValueError("恢复审核不能改动当前方案参数")
    completed, passed = review_verdict(text, verdict, original.get("requirements"))
    if not completed and not restore_titles:
        metadata = _read(history / "recovery.json")
        _save(history / "recovery.json", dict(metadata, status="REVIEW_FAILED"))
        return {"success": False, "error": "结构修复或学术审核尚未完成，检查点预算已保存在恢复目录",
                "history": str(history), "review": verdict}
    doc = copy.deepcopy(original)
    suffix = original["final_answer"][len(prior["draft"]):] if restore_titles else RAGPipeline._build_reference_section(evidence)
    if restore_titles and not original["final_answer"].startswith(prior["draft"]):
        raise ValueError("原报告正文与检查点不是精确前缀，拒绝替换")
    doc.update(final_answer=text + suffix, reviewer_output=verdict,
               review_completed=completed, review_passed=passed, confidence=verdict.get("confidence", "low"),
               repair_audit=audit, retrieval_state=copy.deepcopy(seed["retrieval_state"]))
    if restore_titles:
        doc["design_structure"] = copy.deepcopy(seed["design_structure"])
    if doc.get("requirements"):
        doc["research"] = copy.deepcopy(seed.get("research_draft", {}).get("research") or doc.get("research"))
        doc["research_narrative"] = copy.deepcopy(seed.get("research_draft", {}).get("research_narrative"))
    doc["rule_checks"] = engine.check_scheme(doc["scheme"], claimed_energy=RAGPipeline._extract_energy_claim(text), answer_text=text)
    from .design_sections import BOLD_ERROR, FORMAT_ERROR
    obsolete = {BOLD_ERROR, FORMAT_ERROR, "正文结构不完整，尚未完成审核", "未完成有效学术审核，须人工复核；确定性检查不能替代学术审核。"}
    warnings = [issue for issue in (original.get("report_readiness") or {}).get("issues", []) if issue not in obsolete]
    if restore_titles and completed:
        # 旧审核完整保存在恢复档案；本轮全文复核结果成为当前意见，不保留旧定位队列。
        old_issues = set((original.get("reviewer_output") or {}).get("issues", []))
        warnings = [issue for issue in warnings if issue not in old_issues]
    from .report_readiness import prepare
    status_doc = copy.deepcopy(doc)
    prepare(status_doc, original["ledger_view"], warnings)
    doc["report_readiness"] = status_doc["report_readiness"]
    doc["review_status"] = status_doc["review_status"]
    doc["report_readiness"]["physics_status"] = original.get("report_readiness", {}).get("physics_status", "NOT_EVALUATED")
    if doc.get("requirements"):
        from .research_contract import finalize_research
        finalize_research(doc)
    for key, row in parameters.items():
        quote = row.get("source_quote")
        if quote and quote in original["final_answer"] and quote not in doc["final_answer"]:
            raise ValueError("恢复补丁使当前参数来源失效：" + key)
    if any(doc[key] != original[key] for key in ("scheme", "ledger_view", "ledger_hash", "scheme_parameters", "evidence")):
        raise ValueError("恢复正文审核改变了既有设计或证据")
    seed.update(parameters=parameters, scheme=copy.deepcopy(doc["scheme"]))
    if doc.get("requirements"):
        seed["research_draft"] = copy.deepcopy(doc)
    _save(stage / "stage4_repair_checkpoint.json", seed)
    _save(stage / "design_scheme.json", doc)
    atomic_write(stage / "design_scheme.md", doc["final_answer"])
    raw = _read(stage / "rag_result.json") if (stage / "rag_result.json").exists() else {}
    raw.update(final_answer=doc["final_answer"], reviewer_output=verdict, review_completed=completed,
               review_passed=passed, repair_audit=audit, repair_parameters=parameters, scheme=doc["scheme"],
               rule_checks=doc["rule_checks"], retrieval_state=doc["retrieval_state"], research_draft=doc)
    if restore_titles:
        raw["design_structure"] = copy.deepcopy(seed["design_structure"])
    _save(stage / "rag_result.json", raw)
    _sync_experiment(stage, doc)
    _save(stage / "design_scheme.json", doc)
    return _publish(mgr, task, stage, history, input_hash, original, prior, doc)


def _review_restored_titles(pipeline, seed, question, stage):
    """标题恢复不生成正文或检索；最多使用原预算中的一次完整审核，调用前持久化。"""
    from .adaptive_retrieval import init_state, select_context, options
    from .research_support import refresh_for_review, finish_review
    init_state(seed)
    text, params, evidence = seed["draft"], seed["parameters"], seed["evidence"]
    pipeline.reviewer.requirements = (seed.get("plan") or {}).get("requirements")
    refresh_for_review(pipeline, seed, text, params, evidence, repair_records=False)
    verdict = seed["reviewer_output"]
    if (not seed.get("title_review_attempted") and seed.get("review_rounds", 0) < 6
            and seed.get("review_service_failures", 0) < 2):
        seed["title_review_attempted"] = True
        seed["review_rounds"] = seed.get("review_rounds", 0) + 1
        package = select_context(seed, purpose="final_review", text=text,
            issues=verdict.get("repair_issues", []), config=options(pipeline))
        _save(stage / "stage4_repair_checkpoint.json", seed)
        try:
            verdict = pipeline.reviewer.run(question=question, draft_answer=text, evidence=package,
                scheme=seed.get("scheme"), claimed_energy=pipeline._extract_energy_claim(text),
                history_context="本轮仅恢复了程序误改的章节标题，正文、参数和文献不变。请完整复核当前稿件，"
                "旧意见仅供核对，不沿用已经失效的章节定位。历史意见：\n"
                + json.dumps(seed["title_recovery"]["prior_review"].get("issues", []), ensure_ascii=False))
            if verdict.get("error_type") != "reviewer":
                finish_review(pipeline, seed, verdict, params, evidence, package)
                seed["reviewed_hash"] = digest(text)
        except Exception as exc:
            verdict = dict(seed["reviewer_output"], error_type="reviewer", issues=seed["reviewer_output"]["issues"]
                           + ["标题恢复后的审核失败：" + str(exc)])
        if verdict.get("error_type") == "reviewer":
            seed["review_service_failures"] = seed.get("review_service_failures", 0) + 1
            verdict["issues"] = list(dict.fromkeys(seed["reviewer_output"].get("issues", []) + verdict.get("issues", [])))
    seed["reviewer_output"] = verdict
    seed.pop("pending_review", None)
    _save(stage / "stage4_repair_checkpoint.json", seed)
    return text, verdict, evidence, params, seed["repair_audit"]


def _save(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def _sync_experiment(stage, doc):
    from . import experiment_design as recipes
    from .experiment_consistency import stamp
    recipe = recipes.load(stage)
    if not recipe:
        return
    stamp(recipe, doc)
    recipe["bindings"] = recipes.validate(dict(recipe, plan_hash=recipes.plan_hash(recipe)), doc, delivery=True)
    recipe_doc = copy.deepcopy(doc)
    recipe_doc["evidence"] = doc.get("experiment_evidence") or doc["evidence"]
    execution = _read(stage / "stage5_execution.json") if (stage / "stage5_execution.json").exists() else {}
    recipe_doc["physics_context"] = {"status": execution.get("physics", {}).get("status"), "result": {}}
    result_path = stage / "pinn_simulation_result.json"
    if recipe_doc["physics_context"]["status"] == "PASSED" and result_path.exists():
        recipe_doc["physics_context"]["result"] = _read(result_path)
    recipe["input_fingerprint"] = recipes.input_fingerprint(recipe_doc, doc["final_answer"][:12000])
    recipe["plan_hash"] = recipes.plan_hash(recipe)
    doc["experiment_plan_hash"] = recipe["plan_hash"]
    _save(stage / "experiment_plan.json", recipe)
    if execution:
        execution["experiment"]["plan_hash"] = recipe["plan_hash"]
        _save(stage / "stage5_execution.json", execution)


def _publish(mgr, task, stage, history, input_hash, original, prior, doc):
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
    from auto_battery_research.checkers.stage5_checker import Stage5Checker
    from .reports import publish_report_audit
    stages = [SimpleNamespace(**{key: getattr(s, key, None) for key in (
        "id", "key", "name", "description", "status", "skip", "allow_skip", "duration_seconds")}) for s in mgr.stages]
    preview = SimpleNamespace(config=mgr.config, root_dir=mgr.root_dir, target_goal=mgr.target_goal,
        stages=stages, is_legacy_task=getattr(mgr, "is_legacy_task", False),
        get_task_output_dir=lambda *a, **kw: stage, get_all_stage_journal=mgr.get_all_stage_journal,
        get_stage_by_id=lambda i: next((s for s in stages if s.id == i), None))
    for number, checker_class in ((4, RAGDesignChecker), (5, Stage5Checker)):
        checker = checker_class()
        checker.on_init(preview, {"id": number}, mgr.config)
        passed, diagnostic = checker.do_check(is_complete=True)
        if not passed:
            return {"success": False, "error": "恢复副本未通过阶段验收", "diagnostic": diagnostic, "history": str(history)}
        preview.get_stage_by_id(number).status = "FALLBACK" if diagnostic.get("observed", {}).get("is_fallback") else "PASSED"
    result = run_synthesis_report(doc["target"], stage_manager=preview)
    checker = FinalReportChecker()
    checker.on_init(preview, {"id": 6}, mgr.config)
    passed, diagnostic = checker.do_check(is_complete=True) if result.get("success") else (False, result)
    _save(history / "audit.json", {"input_hash": input_hash, "passed": passed, "diagnostic": diagnostic,
        "review_completed": doc["review_completed"], "repair_audit": doc["repair_audit"]})
    if not passed:
        return {"success": False, "error": "恢复副本未通过研报终审", "history": str(history), "diagnostic": diagnostic}
    names = PUBLISH_FILES
    originals = {name: (task / name).read_bytes() for name in names if (task / name).exists()}
    stage_statuses = {s.id: s.status for s in mgr.stages}
    extra = [mgr.state_file_path, task / "stage_journals.json", task / "stage_journals.md"]
    snapshots = {path: path.read_bytes() for path in extra if path.exists()}
    with task_lock(task):
        metadata = _read(history / "recovery.json")
        for name, fingerprint in metadata.get("original_files", {}).items():
            actual = hashlib.sha256((task / name).read_bytes()).hexdigest() if (task / name).exists() else None
            if actual != fingerprint:
                return {"success": False, "error": "恢复期间当前课题文件已变化，拒绝覆盖：" + name, "history": str(history)}
        if (_read(task / "design_scheme.json") != original
                or _read(task / "stage4_repair_checkpoint.json") != prior):
            return {"success": False, "error": "恢复期间当前设计已变化，拒绝覆盖", "history": str(history)}
        try:
            archive = history / "originals"
            archive.mkdir(exist_ok=True)
            for name, content in originals.items():
                if not (archive / name).exists():
                    (archive / name).write_bytes(content)
            for name in names:
                if (stage / name).exists():
                    atomic_write(task / name, (stage / name).read_text(encoding="utf-8").replace(str(stage), str(task)))
            for number in (4, 5):
                mgr.get_stage_by_id(number).status = preview.get_stage_by_id(number).status
            mgr._save_state()
            mgr.set_stage_journal(4, notes=metadata.get("journal_note", "恢复原检查点，修复结构标记后完成学术审核；具体保留意见见报告。"),
                key_findings={"status": mgr.get_stage_by_id(4).status, "review_completed": doc["review_completed"]})
            publish_report_audit(task / "final_research_report.md", mgr.stages)
            checker.on_init(mgr, {"id": 6}, mgr.config)
            passed, diagnostic = checker.do_check(is_complete=True)
            if not passed:
                raise ValueError(str(diagnostic))
        except Exception:
            for name in names:
                if name in originals:
                    (task / name).write_bytes(originals[name])
                else:
                    (task / name).unlink(missing_ok=True)
            for path, content in snapshots.items():
                path.write_bytes(content)
            for s in mgr.stages:
                s.status = stage_statuses[s.id]
            raise
    _save(history / "recovery.json", dict(metadata, status="PUBLISHED"))
    return {"success": True, "review_completed": doc["review_completed"], "review_passed": doc["review_passed"],
            "stage4_status": mgr.get_stage_by_id(4).status, "history": str(history),
            "report_file": str(task / "final_research_report.md")}
