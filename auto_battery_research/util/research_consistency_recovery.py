"""显式恢复研究来源和第二部分；独立审核、调用留档、整体验收后发布。"""
import copy
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from .scheme_ledger import atomic_write, digest, task_lock, require_current


def recover_research_report(task_dir, config):
    from .research_recovery import _research_client, complete_missing_support_review
    from .research_records import collect_research
    from .scheme_ledger import collect_parameters
    from .research_support import apply_review, cited_evidence_ids, review_pending
    from .research_narrative import integrate, display_narrative
    from .adaptive_retrieval import evidence_package
    from .design_projection_repair import rebind_tree
    from .numeric_bindings import refresh
    from .research_contract import document_errors
    from auto_battery_research.rag import ReviewerAgent
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
    from auto_battery_research.checkers.stage5_checker import Stage5Checker
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    task = Path(task_dir).resolve()
    original = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    require_current(task, original)
    identity = digest(original)
    root = task / "research_consistency_history"
    history = next((p.parent for p in sorted(root.glob("*/recovery.json"), reverse=True)
                    if json.loads(p.read_text(encoding="utf-8")).get("input_hash") == identity
                    and json.loads(p.read_text(encoding="utf-8")).get("status") != "PUBLISHED"), None)
    if history is None:
        history = root / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        (history / "preview").mkdir(parents=True)
        (history / "originals").mkdir()
        for path in task.iterdir():
            if path.is_file() and not path.name.endswith(".lock"):
                (history / "originals" / path.name).write_bytes(path.read_bytes())
                (history / "preview" / path.name).write_bytes(path.read_bytes())
        atomic_write(history / "recovery.json", json.dumps({"input_hash": identity, "status": "PREPARING"}))
    stage = history / "preview"
    calls = history / "calls"
    calls.mkdir(exist_ok=True)
    def cached_client(role):
        client = _research_client(SimpleNamespace(config=config), role)
        if client is None or not client.available:
            raise ValueError("研究恢复需要独立审核模型")
        invoke = client.chat
        def chat(system, user, **kw):
            path = calls / (digest({"role": role, "system": system, "user": user, "kwargs": kw}) + ".json")
            if path.exists():
                saved = json.loads(path.read_text(encoding="utf-8"))
                if "response" not in saved:
                    raise ValueError("该恢复调用已中断，保留预算与输入，不自动重发")
                return saved["response"]
            saved = {"role": role, "trace_tag": kw.get("trace_tag"), "system": system, "user": user, "attempted": True}
            atomic_write(path, json.dumps(saved, ensure_ascii=False, indent=2))
            client.single_attempt = True
            saved["response"] = invoke(system, user, **kw)
            atomic_write(path, json.dumps(saved, ensure_ascii=False, indent=2))
            return saved["response"]
        client.chat = chat
        return client
    writer, reviewer = cached_client("writer"), ReviewerAgent(cached_client("reviewer"))
    draft_path = history / "reviewed_records.json"
    if draft_path.exists():
        doc = json.loads(draft_path.read_text(encoding="utf-8"))
    else:
        doc = copy.deepcopy(original)
        research = copy.deepcopy(doc.get("research") or {})
        mapping = {}
        for path in sorted((task / "design_projection_repair_history").glob("*/audit.json")):
            audit = json.loads(path.read_text(encoding="utf-8"))
            if audit.get("stage4_5_6_checks_pass"):
                mapping.update(audit.get("field_mapping") or {})
        params = doc["ledger_view"]["parameters"]
        for error in research.get("record_errors", []):
            if isinstance(error.get("record"), dict):
                record = rebind_tree(error["record"], mapping)
                record["source_quotes"] = [refresh(q, params) for q in record.get("source_quotes", [])]
                error["record"] = record
        # 原参数和文献不改；迁移历史提供显式路径映射，旧审核需本轮重新验证。
        research["bound_fields"] = {}
        doc["research"] = collect_research({}, doc, params, previous=research)
        budget = doc.get("maintenance_calls", 0)
        if doc["research"].get("record_errors"):
            doc["research_only"] = True
            collect_parameters(doc, writer, max_attempts=2)
            doc.pop("research_only", None)
        doc["recovery_maintenance_calls"] = doc.get("maintenance_calls", 0)
        doc["maintenance_calls"] = budget
        doc["research"] = collect_research({}, doc, params, previous=doc.get("research"))
        reviewer.requirements = doc["requirements"]
        reviewer.research_draft = doc
        package, _ = evidence_package(doc["evidence"], preferred_ids=cited_evidence_ids(doc), requirement_items=doc["requirements"]["items"])
        verdict = reviewer.run(question=doc["target"], draft_answer=doc["final_answer"], evidence=package, scheme=doc["scheme"],
                               claimed_energy=None, history_context="本次按用户要求检查实质一致性。明确标注为工程建议、跨体系推断和待验证的陈述可通过表述审核；无明显矛盾不因措辞或风格拒绝。")
        apply_review(doc["research"], verdict.get("research_support_review"), doc, params, package)
        verdict, _ = complete_missing_support_review(reviewer, doc, verdict, package)
        apply_review(doc["research"], verdict.get("research_support_review"), doc, params, package)
        doc["research_consistency_review"] = verdict
        # 新审核只决定摘要及整合展示；此前科学争议仍在最终附录，不能隐式升级原方案通过状态。
        if document_errors(doc):
            raise ValueError("恢复后的研究记录结构失效：" + "；".join(document_errors(doc)))
        atomic_write(draft_path, json.dumps(doc, ensure_ascii=False, indent=2))
    state_path = history / "integration_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    doc.pop("research_narrative", None)
    def save():
        atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=2))
        atomic_write(stage / "design_scheme.json", json.dumps(doc, ensure_ascii=False, indent=2))
    integrate(doc, writer, reviewer, state, save)
    displayed = display_narrative(doc)
    if not displayed:
        return {"success": False, "history": str(history), "error": "整合仍需处理实质问题", "diagnostics": doc.get("research_narrative", {}).get("diagnostics")}
    for key in ("scheme", "ledger_view", "ledger_hash", "final_answer", "scheme_parameters", "evidence", "requirements"):
        if doc.get(key) != original.get(key):
            raise ValueError("论证恢复改变了科研输入：" + key)
    atomic_write(stage / "design_scheme.json", json.dumps(doc, ensure_ascii=False, indent=2))
    from .experiment_design import input_fingerprint, plan_hash, require_current as recipe_current
    recipe = json.loads((stage / "experiment_plan.json").read_text(encoding="utf-8"))
    recipe_doc = copy.deepcopy(doc)
    recipe_doc["evidence"] = doc.get("experiment_evidence") or doc["evidence"]
    recipe_doc["physics_context"] = doc.get("experiment_physics_context") or {}
    recipe["input_fingerprint"] = input_fingerprint(recipe_doc, doc["final_answer"][:12000])
    recipe["plan_hash"] = plan_hash(recipe)
    doc["experiment_plan_hash"] = recipe["plan_hash"]
    atomic_write(stage / "experiment_plan.json", json.dumps(recipe, ensure_ascii=False, indent=2))
    atomic_write(stage / "design_scheme.json", json.dumps(doc, ensure_ascii=False, indent=2))
    recipe_current(stage, doc)
    execution = json.loads((stage / "stage5_execution.json").read_text(encoding="utf-8"))
    execution["experiment"]["plan_hash"] = recipe["plan_hash"]
    atomic_write(stage / "stage5_execution.json", json.dumps(execution, ensure_ascii=False, indent=2))
    for name in ("rag_result.json", "stage4_repair_checkpoint.json"):
        value = json.loads((stage / name).read_text(encoding="utf-8"))
        value["research_draft"] = copy.deepcopy(doc)
        value["research_consistency_recovery"] = str(history.relative_to(task))
        atomic_write(stage / name, json.dumps(value, ensure_ascii=False, indent=2))
    stages = [SimpleNamespace(**s, allow_skip=False) for s in json.loads((stage / ".stage_state.json").read_text(encoding="utf-8"))["stages"]]
    mgr = SimpleNamespace(root_dir=Path(__file__).resolve().parents[2], config=config, target_goal=doc["target"], stages=stages,
        is_legacy_task=False, get_task_output_dir=lambda *a: stage, get_stage_by_id=lambda i:next(s for s in stages if s.id==i),
        get_all_stage_journal=lambda:json.loads((stage / "stage_journals.json").read_text(encoding="utf-8")))
    result = run_synthesis_report(doc["target"], stage_manager=mgr)
    if not result.get("success"):
        raise ValueError("报告重渲染失败：" + str(result))
    for i, cls in ((4,RAGDesignChecker),(5,Stage5Checker),(6,FinalReportChecker)):
        checker=cls();checker.on_init(mgr,{"id":i},config)
        passed, diagnostic=checker.do_check()
        if not passed:raise ValueError("恢复验收失败：" + str(diagnostic))
    names = ("design_scheme.json", "experiment_plan.json", "experiment_report.json", "stage5_execution.json", "stage4_repair_checkpoint.json", "rag_result.json", "report_record.json", "final_research_report.md", "final_research_report_supplement.md")
    with task_lock(task):
        for path in (history / "originals").iterdir():
            if (task / path.name).read_bytes() != path.read_bytes():
                raise ValueError("恢复期间任务已改变，拒绝覆盖")
        try:
            for name in names:
                content=(stage/name).read_text(encoding="utf-8")
                if name in ("final_research_report.md", "final_research_report_supplement.md"):content=content.replace(str(stage),str(task))
                atomic_write(task/name,content)
            mgr.get_task_output_dir=lambda *a:task
            checker=FinalReportChecker();checker.on_init(mgr,{"id":6},config)
            if not checker.do_check()[0]:raise ValueError("正式报告验收失败")
        except Exception:
            for name in names:
                original_path = history / "originals" / name
                if original_path.exists():
                    (task/name).write_bytes(original_path.read_bytes())
                else:
                    (task/name).unlink(missing_ok=True)
            raise
    summary={"success":True,"history":str(history),"narrative_status":displayed["status"],"sections":len(displayed["sections"]),
             "pending_paragraphs":sum(bool(v) for v in displayed["paragraph_reviews"].values()),
             "record_counts":{k:len(doc["research"][k]) for k in ("answers","candidates","arguments")},"model_calls":len(list(calls.glob('*.json'))),"input_hash":identity,"status":"PUBLISHED"}
    atomic_write(history/"recovery.json",json.dumps(summary,ensure_ascii=False,indent=2))
    return summary
