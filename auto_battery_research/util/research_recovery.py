"""旧课题研究记录恢复；固定科研方案，在任务副本中生成并验收报告后再替换。"""
import copy
import json
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from .scheme_ledger import atomic_write, digest, require_current


def complete_missing_support_review(reviewer, doc, verdict, package):
    """仅补一次漏返字段审核；已审核字段不覆盖，不以缺审核视作通过。"""
    from .research_support import missing_support_fields
    params = doc["ledger_view"]["parameters"]
    missing = missing_support_fields(doc["research"], doc, params)
    if not missing:
        return verdict, 0
    reviewer.research_draft = doc
    supplement = reviewer.run(question=doc["target"], draft_answer=doc["final_answer"], evidence=package,
        scheme=doc["scheme"], claimed_energy=None, history_context=(
            "本轮只补齐漏返的逐字段支持性审核，不改正文、参数、已审核状态或研究记录。"
            "answers.reason/next_step必须分别返回状态，不能只返回response。无法支持标needs_review或unsupported。"
            "以下每项都须在research_support_review中返回，按collection/id/fingerprint合并fields：\n"
            + json.dumps(missing, ensure_ascii=False)))
    result = copy.deepcopy(verdict)
    existing = {(r.get("collection"), r.get("id"), r.get("fingerprint")): r
                for r in result.get("research_support_review", [])}
    allowed = {(m["collection"], m["id"], m["fingerprint"], m["field"]) for m in missing}
    for row in supplement.get("research_support_review", []):
        key = (row.get("collection"), row.get("id"), row.get("fingerprint"))
        accepted = {f: v for f, v in (row.get("fields") or {}).items() if (*key, f) in allowed}
        if accepted:
            existing.setdefault(key, dict(collection=key[0], id=key[1], fingerprint=key[2], fields={}))
            existing[key].setdefault("fields", {}).update(accepted)
    result["research_support_review"] = list(existing.values())
    result["issues"] = list(dict.fromkeys(result.get("issues", []) + supplement.get("issues", [])))
    return result, 1


def _research_client(mgr, role):
    """研究恢复沿用角色模型和输出预算，不依赖实验生成开关。"""
    import os
    from .report_recipe_llm import _first_valid
    from auto_battery_research.rag import LLMClient
    cfg = mgr.config or {}
    llm = cfg.get("llm") or {}
    endpoint = cfg.get("openai") or {}
    if not llm and not endpoint:
        return None
    key = _first_valid(os.getenv("OPENAI_API_KEY"), llm.get("api_key"), endpoint.get("openai_api_key"))
    if not key:
        return None
    base = _first_valid(os.getenv("OPENAI_API_BASE"), llm.get("base_url"), endpoint.get("openai_api_base"))
    model = _first_valid(llm.get(role + "_model"), os.getenv("OPENAI_MODEL"), llm.get("model"), endpoint.get("model_name"))
    budget = int(os.getenv("LLM_MAX_TOKENS") or llm.get("max_tokens") or 16384)
    return LLMClient(model_name=model or None, backend="openai", api_key=key, api_base=base or None,
                     temperature=0.1, max_tokens=budget, output_max_tokens=llm.get("output_max_tokens"))


def integrate_research_narrative(mgr, client=None, reviewer=None, proposal=None, proposal_feedback=None):
    """旧课题仅补整合及独立审核，副本验收后提交，不重新提取研究记录或运行Stage 5。"""
    from .research_narrative import integrate, display_narrative
    from auto_battery_research.rag import ReviewerAgent
    task = mgr.get_task_output_dir()
    original = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    require_current(task, original)
    if not original.get("requirements") or not original.get("research"):
        return {"success": False, "error": "论证整合需要已有课题要求与研究记录"}
    client = client if client is not None else _research_client(mgr, "writer")
    reviewer = reviewer if reviewer is not None else ReviewerAgent(_research_client(mgr, "reviewer"))
    if client is None or not getattr(client, "available", False):
        return {"success": False, "error": "论证整合需要可用模型，原报告未更改"}
    history = task / "narrative_integration_history" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stage = history / "preview"
    stage.mkdir(parents=True)
    for source in task.iterdir():
        if source.is_file():
            shutil.copy2(source, stage / source.name)
    for name in ("design_scheme.json", "experiment_plan.json", "experiment_report.json", "report_record.json", "final_research_report.md", "final_research_report_supplement.md"):
        if (task / name).exists():
            shutil.copy2(task / name, history / name)
    doc, state = copy.deepcopy(original), {}
    if proposal is not None:
        # 显式维护可复用前次未通过的稿件作为修订输入；仍须完整校验及独立审核。
        from .research_narrative import normalize
        state["submitted_draft"] = copy.deepcopy(proposal)
        state["phase"] = "proposal"
        try:
            normalize(proposal, doc)
            state["repair_feedback"] = ["复用既有稿件，保留原观点并压缩重复；再次审核后方可展示。"]
        except (ValueError, TypeError, KeyError) as exc:
            state["repair_feedback"] = [str(exc)]
        if proposal_feedback:
            state["repair_feedback"] = list(proposal_feedback) + state.get("repair_feedback", [])
            state["phase"] = "revision_requested"
    def save():
        atomic_write(history / "integration_checkpoint.json", json.dumps({"state": state,
            "research_narrative": doc.get("research_narrative")}, ensure_ascii=False, indent=2))
    integrate(doc, client, reviewer, state=state, save=save)
    displayed = display_narrative(doc)
    if not displayed:
        return {"success": False, "error": "整合稿未通过来源或Reviewer审核，原报告已保留",
                "history": str(history), "diagnostics": (doc.get("research_narrative") or {}).get("diagnostics", [])}
    for key in ("research", "requirements", "reviewer_output"):
        if doc.get(key) != original.get(key):
            raise ValueError("论证整合不得修改已有科研记录或审核：" + key)
    atomic_write(stage / "design_scheme.json", json.dumps(doc, ensure_ascii=False, indent=2))
    result = _publish_recovery(mgr, history, original, doc, doc["research_narrative"].get("review", {}),
                              review_calls=state.get("review_calls", 0))
    result["narrative_sections"] = len(doc["research_narrative"]["sections"])
    result["generation_calls"] = state.get("generation_calls", 0)
    result["narrative_status"] = displayed["status"]
    result["paragraphs_pending"] = sum(bool(r) for r in displayed["paragraph_reviews"].values())
    return result


def repair_research_records(mgr, client=None, reviewer=None):
    from .scheme_ledger import collect_parameters, research_parameters
    from .research_records import collect_research
    from .research_contract import finalize_research
    from .research_support import apply_review, review_pending
    from .experiment_design import load as load_experiment, plan_hash, validate as validate_experiment
    from .research_experiments import identity_fields
    from auto_battery_research.rag import ReviewerAgent

    task = mgr.get_task_output_dir()
    original = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    require_current(task, original)
    if not original.get("requirements") or not original.get("ledger_view"):
        return {"success": False, "error": "研究记录恢复需要已有需求契约与方案表；不能从旧报告猜配方"}
    supplied_client = client is not None
    client = client if supplied_client else _research_client(mgr, "writer")
    if client is None or not getattr(client, "available", False):
        return {"success": False, "error": "研究记录恢复需要可用模型，未更改原报告"}
    if reviewer is None:
        # 仍使用同一Reviewer接口；不运行Planner、检索、Writer或实验生成器。
        reviewer = ReviewerAgent(client if supplied_client else _research_client(mgr, "reviewer"))
    initial = {name: (task / name).read_bytes() for name in (
        "design_scheme.json", "experiment_plan.json", "experiment_report.json", "report_record.json", "final_research_report.md", "final_research_report_supplement.md") if (task / name).exists()}
    history = task / "research_repair_history" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    history.mkdir(parents=True)
    for name, data in initial.items():
        (history / name).write_bytes(data)
    stage = history / "preview"
    stage.mkdir()
    for source in task.iterdir():
        if source.is_file():
            shutil.copy2(source, stage / source.name)
    doc = copy.deepcopy(original)
    doc["research_only"] = True
    # 固定正文和参数，最多两次维护调用，直接走同一研究记录局部验收。
    collect_parameters(doc, client)
    doc.pop("research_only", None)
    # 局部重试可能越权重报已通过项；重验实际保留记录，拒绝提交的原因留维护审计。
    doc["research"] = collect_research({}, doc, research_parameters(doc), previous=doc.get("research"))
    atomic_write(history / "research_draft.json", json.dumps(doc, ensure_ascii=False, indent=2))
    research = doc.get("research") or {}
    valid_answers = [a for a in research.get("answers", []) if a.get("source_quote") or a.get("source_quotes")]
    if not valid_answers and not research.get("candidates") and not research.get("arguments"):
        atomic_write(history / "failure.json", json.dumps({"error": "未恢复有效研究记录",
            "maintenance_calls": doc.get("maintenance_calls", 0), "parameter_diagnostics": doc.get("parameter_diagnostics"),
            "research": doc.get("research")}, ensure_ascii=False, indent=2))
        return {"success": False, "error": "未恢复有效研究记录，原报告已保留", "history": str(history)}
    reviewer.requirements = doc["requirements"]
    reviewer.research_draft = doc
    from .adaptive_retrieval import evidence_package
    from .research_support import cited_evidence_ids
    package, selection = evidence_package(doc.get("evidence", []), text=doc["final_answer"],
        preferred_ids=cited_evidence_ids(doc), requirement_items=doc["requirements"]["items"])
    doc["research_review_evidence"] = selection
    verdict = reviewer.run(question=doc["target"], draft_answer=doc["final_answer"], evidence=package,
                          scheme=doc["scheme"], history_context="", claimed_energy=None)
    from .requirement_review import normalize_review
    normalize_review(verdict, doc)
    params = doc["ledger_view"]["parameters"]
    apply_review(doc["research"], verdict.get("research_support_review"), doc, params, package)
    verdict, supplemental_calls = complete_missing_support_review(reviewer, doc, verdict, package)
    apply_review(doc["research"], verdict.get("research_support_review"), doc, params, package)
    # 原文科学争议不被“摘要忠实”审核消除；仅替换本次逐项回应覆盖记录。
    old_review = copy.deepcopy(doc.get("reviewer_output") or {})
    old_review["issues"] = list(dict.fromkeys(old_review.get("issues", []) + verdict.get("issues", [])))
    for key in ("requirement_review", "research_support_review"):
        if key in verdict:
            old_review[key] = verdict[key]
    doc["reviewer_output"] = old_review
    state = doc.setdefault("report_readiness", {})
    state.setdefault("issues", [])
    state.setdefault("category", "CONDITIONAL")
    pending = review_pending(doc["research"], doc, params)
    if pending and state["category"] == "COMPLETE":
        state["category"] = "CONDITIONAL"
    old_state_issues = copy.deepcopy(state["issues"])
    finalize_research(doc)
    # 科学争议继续留档，旧“结构化回答未生成”诊断放入历史，避免恢复后仍显示相互矛盾状态。
    if not doc["research"].get("record_errors"):
        stale = [s for s in state["issues"] if "结构化研究回答两项" in s and "待补充" in s]
        state["issues"] = [s for s in state["issues"] if s not in stale]
        old_review["issues"] = [s for s in old_review.get("issues", []) if s not in stale]
    if doc["scheme"] != original["scheme"] or doc["ledger_view"] != original["ledger_view"]:
        raise ValueError("研究记录恢复不得改变主方案或方案表")
    experiment = load_experiment(stage)
    if experiment is not None:
        for group in experiment.get("groups", []):
            if group.get("scope") == "main":
                group.update(candidate_id=doc["research"].get("selected_candidate_id"), parameter_refs=identity_fields(doc))
        # 仅同步主组身份指向，保留配方、操作和全部测试条件。
        experiment["plan_hash"] = plan_hash(experiment)
        experiment["bindings"] = validate_experiment(experiment, doc)
        doc["experiment_plan_hash"] = experiment["plan_hash"]
        atomic_write(stage / "experiment_plan.json", json.dumps(experiment, ensure_ascii=False, indent=2))
    atomic_write(stage / "design_scheme.json", json.dumps(doc, ensure_ascii=False, indent=2))
    return _publish_recovery(mgr, history, original, doc, verdict, old_state_issues, review_calls=1 + supplemental_calls)


def _publish_recovery(mgr, history, original, doc, verdict, original_issues=None, review_calls=1):
    """复用已完成的提取审核；预览或提交失败后无需再次请求模型。"""
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    from .research_support import review_pending
    task = mgr.get_task_output_dir()
    history = Path(history)
    stage = history / "preview"
    names = ["design_scheme.json", "experiment_plan.json", "experiment_report.json", "report_record.json", "final_research_report.md", "final_research_report_supplement.md"]
    initial = {name: (history / name).read_bytes() for name in names if (history / name).exists()}
    for key in ("scheme", "ledger_view", "final_answer", "scheme_parameters", "evidence"):
        if doc.get(key) != original.get(key):
            raise ValueError("研究恢复不能改写既有科研内容：" + key)
    require_current(stage, doc)
    # 阶段检查器持有管理器与线程锁，预览只需要不可变状态快照，不能复制运行时对象。
    stages = [SimpleNamespace(**{key: getattr(s, key, None) for key in (
        "id", "key", "name", "description", "status", "skip", "allow_skip")}) for s in mgr.stages]
    preview = SimpleNamespace(config=mgr.config, root_dir=mgr.root_dir, target_goal=mgr.target_goal,
        stages=stages, is_legacy_task=getattr(mgr, "is_legacy_task", False),
        get_task_output_dir=lambda *a, **k: stage, get_all_stage_journal=mgr.get_all_stage_journal,
        get_stage_by_id=lambda i: next((s for s in stages if s.id == i), None))
    result = run_synthesis_report(doc["target"], stage_manager=preview)
    checker = FinalReportChecker()
    checker.on_init(preview, {"id": 6}, mgr.config)
    passed, diagnostic = checker.do_check() if result.get("success") else (False, result)
    pending = review_pending(doc["research"], doc, doc["ledger_view"]["parameters"])
    audit = {"verdict": verdict, "diagnostic": diagnostic, "passed": passed,
             "maintenance_calls": doc.get("maintenance_calls", 0), "review_calls": review_calls,
             "pending_fields": len(pending), "original_issues": original_issues,
             "input_hash": digest(original), "output_hash": digest(doc)}
    atomic_write(history / "audit.json", json.dumps(audit, ensure_ascii=False, indent=2))
    if not passed:
        return {"success": False, "error": "恢复副本未通过报告终审，原报告已保留", "diagnostic": diagnostic, "history": str(history)}
    # 先确认真实任务在模型运行期间未被其他写入更新，再提交可回滚的文件集合。
    if any((task / name).read_bytes() != data for name, data in initial.items()):
        return {"success": False, "error": "恢复期间原课题产物已变化，拒绝覆盖", "history": str(history)}
    try:
        for name in names:
            if (stage / name).exists():
                content = (stage / name).read_text(encoding="utf-8")
                if name in ("final_research_report.md", "final_research_report_supplement.md"):
                    content = content.replace(str(stage), str(task))
                atomic_write(task / name, content)
        checker.on_init(mgr, {"id": 6}, mgr.config)
        valid, final_diag = checker.do_check()
        if not valid:
            raise ValueError(str(final_diag))
    except Exception:
        for name, data in initial.items():
            (task / name).write_bytes(data)
        for name in names:
            if name not in initial and (task / name).exists():
                (task / name).unlink()
        raise
    return {"success": True, "report_file": str(task / "final_research_report.md"), "history": str(history),
            "answers": len(doc["research"].get("answers", [])), "candidates": len(doc["research"].get("candidates", [])),
            "arguments": len(doc["research"].get("arguments", [])), "summary_fields_pending": len(pending)}
