"""结构修复后必须继续学术审核，原文、参数和持久化预算均受保护。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.design_repair import (
    _repair_bold_marker, pending_structure_repair, review_design, review_verdict,
)
from auto_battery_research.util.design_sections import (
    BOLD_ERROR, FENCE_ERROR, FORMAT_ERROR, REQUIRED_SECTIONS, bold_repair_issue,
    classify_design_output, design_output_issues,
)
from auto_battery_research.util.scheme_ledger import digest


def draft():
    return "\n\n".join("## " + name + "\n\n" + "已有证据与适用条件须独立核验。" * 10
                        for name, _ in REQUIRED_SECTIONS)


def pipeline(tmp_path, review, llm=None):
    return SimpleNamespace(
        reviewer=SimpleNamespace(run=review, llm=llm), writer=SimpleNamespace(llm=llm),
        _extract_energy_claim=lambda text: None, repair_checkpoint_path=tmp_path / "checkpoint.json",
    )


def test_table_marker_repair_continues_to_scientific_review_and_persists(tmp_path):
    text = draft() + "\n\n| 电解液 | **1 mol/L LiPF6/EC+DMC** | 界面机理对照**\n\n后续风险声明。"
    calls = []

    def review(**kwargs):
        calls.append(kwargs["draft_answer"])
        assert not design_output_issues(kwargs["draft_answer"])
        return {"issues": [], "confidence": "high"}

    seed = {"draft": text, "scheme": {}, "evidence": [], "repair_rounds": 1, "review_rounds": 1,
            "repair_audit": [{"status": "SCOPE_UNRESOLVED", "issues": []}],
            "reviewer_output": {"review_status": "STRUCTURE_INVALID", "error_type": "structure"}}
    expected = text.replace("机理对照**", "机理对照")
    engine = pipeline(tmp_path, review)
    result, verdict, _, _, audit = review_design(engine, seed, "目标")
    assert calls == [expected] and result == expected
    assert review_verdict(result, verdict) == (True, True)
    assert audit[-1]["kind"] == "markdown_marker" and audit[-1]["after"] == digest(expected)
    saved = json.loads(engine.repair_checkpoint_path.read_text(encoding="utf-8"))
    assert (saved["repair_rounds"], saved["review_rounds"], saved["format_repair_rounds"]) == (1, 2, 1)
    assert saved["reviewed_hash"] == digest(expected)
    assert not pending_structure_repair(saved)


def test_format_repair_keeps_scientific_rejection_and_original_parameters(tmp_path):
    text = draft() + "\n\n| 正极厚度 | 18.75 μm | 工程声明**\n\n后续说明。"
    params = {"cathode.L_um": {"value": 18.75, "unit": "um", "locked": True}}
    seed = {"draft": text, "scheme": {}, "evidence": [], "parameters": copy.deepcopy(params)}

    def review(**kwargs):
        return {"issues": ["材料尚未得到同体系证据支持"], "repair_issues": [], "confidence": "low"}

    result, verdict, _, fields, audit = review_design(pipeline(tmp_path, review), seed, "目标")
    assert fields == params and result == text.replace("工程声明**", "工程声明")
    assert review_verdict(result, verdict) == (True, False)
    assert audit[-1]["status"] == "SCOPE_UNRESOLVED"
    assert seed["repair_rounds"] == 0 and seed["format_repair_rounds"] == 1


def test_ambiguous_or_truncated_structure_does_not_consume_repair_budget(tmp_path):
    text = draft() + "\n\n但**后续解释尚未写完"
    seed = {"draft": text, "scheme": {}, "evidence": []}
    review_design(pipeline(tmp_path, lambda **kwargs: pytest.fail("结构未完成不能学术审核")), seed, "目标")
    assert seed.get("repair_rounds", 0) == 0
    assert seed["draft"] == text and BOLD_ERROR in seed["reviewer_output"]["issues"]


def test_markers_inside_code_are_not_markdown_errors():
    text = draft() + "\n\n```python\nx = '**'\n```\n\n行内代码 `**`。"
    assert not design_output_issues(text)
    assert bold_repair_issue(text) is None
    assert FENCE_ERROR in design_output_issues(draft() + "\n\n~~~python\nx = 1")


def test_format_patch_preserves_all_tokens_and_citations():
    text = draft() + "\n\n| 电解液 | **1 mol/L LiPF6/EC+DMC** [p1] | 只作对照**\n\n后续说明。"
    params = {"electrolyte.formulation": {"value": "1 mol/L LiPF6/EC+DMC", "locked": True}}
    fixed, fields, audit = _repair_bold_marker(text, params, bold_repair_issue(text))
    assert fixed.replace("**", "") == text.replace("**", "") and fields == params
    assert len(text) - len(fixed) == 2 and audit["patch"]["base_hash"] == digest(text)


def test_local_format_classification_is_distinct_from_truncation_and_missing_sections():
    local = draft() + "\n\n| 字段 | 已声明值**\n\n后续完整说明。"
    classified = classify_design_output(local)
    assert not classified["blocking_issues"] and len(classified["format_issues"]) == 1
    assert design_output_issues(local) == [FORMAT_ERROR]
    missing = local.replace("## 风险与数据缺口", "## 其他说明")
    assert any("缺少五段式章节" in e for e in classify_design_output(missing)["blocking_issues"])
    for text in (draft() + "\n\n但**解释未写完", draft() + "\n\n```python\nx = 1",
                 draft() + "\n\n说明**还没写完\n\n后续段落。"):
        assert classify_design_output(text)["blocking_issues"]
        assert not classify_design_output(text)["format_issues"]


def test_local_prose_format_patch_only_inserts_closing_marker(tmp_path):
    text = draft() + "\n\n**容量改善仍需同条件验证 [p1]。\n\n独立后续段落。"
    params = {"cathode.L_um": {"value": 60, "unit": "um", "locked": True}}
    issue = classify_design_output(text)["format_issues"][0]
    assert "insert_offset" in issue
    fixed, fields, entry = _repair_bold_marker(text, params, issue)
    assert fixed.replace("**", "") == text.replace("**", "") and fields == params
    assert len(fixed) - len(text) == 2 and not design_output_issues(fixed)
    assert entry["status"] == "APPLIED"


@pytest.mark.parametrize("error", ["truncated", "fence", "missing"])
def test_content_integrity_failures_remain_blocking_scientific_review(tmp_path, error):
    text = draft()
    if error == "truncated":
        text += "\n\n因此**该结论尚未"
    elif error == "fence":
        text += "\n\n~~~python\nx = 1"
    else:
        text = text.replace("## 风险与数据缺口", "## 其他说明")
    seed = {"draft": text, "scheme": {}, "evidence": [], "repair_rounds": 3}
    _, verdict, _, _, _ = review_design(
        pipeline(tmp_path, lambda **kw: pytest.fail("截断或缺章不能跳过结构门禁")), seed, "目标")
    assert not review_verdict(text, verdict)[0]


def test_format_budget_exhaustion_does_not_block_scientific_review(tmp_path):
    text = draft() + "\n\n| 字段 | 已声明值**\n\n完整后续段落。"
    calls = []
    def review(**kwargs):
        calls.append(kwargs["draft_answer"])
        return {"issues": ["机理推断缺少同体系证据"], "confidence": "low", "error_type": "writing"}
    seed = {"draft": text, "scheme": {}, "evidence": [], "format_repair_rounds": 3}
    result, verdict, _, _, audit = review_design(pipeline(tmp_path, review), seed, "目标")
    assert calls == [text] and result == text
    assert "机理推断缺少同体系证据" in verdict["issues"] and verdict["format_issues"]
    assert review_verdict(result, verdict) == (True, False)
    assert seed["format_repair_rounds"] == 3 and seed.get("repair_rounds", 0) == 0
    assert not any(e.get("kind") == "markdown_marker" for e in audit)


def test_actual_reviewer_receives_independent_format_diagnostic():
    from auto_battery_research.rag import ReviewerAgent
    prompts = []
    def chat(system, user, **kwargs):
        prompts.append(user)
        return json.dumps({"issues": ["机理证据不足"], "confidence": "low", "error_type": "writing"})
    reviewer = ReviewerAgent(SimpleNamespace(available=True, chat=chat))
    text = draft() + "\n\n| 字段 | 已声明值**\n\n完整后续段落。"
    verdict = reviewer.run(question="核对机理", evidence=[{
        "passage_id": "p1", "source": "本地种子", "text": "机理尚未在本体系验证。"}], draft_answer=text)
    assert len(prompts) == 1 and "【独立格式诊断】" in prompts[0]
    assert "继续完整核对全部科学问题" in prompts[0]
    assert "机理证据不足" in verdict["issues"]


def test_failed_format_attempt_is_persisted_and_not_replayed(tmp_path, monkeypatch):
    import auto_battery_research.util.design_repair as repair_module
    text = draft() + "\n\n| 字段 | 已声明值**\n\n完整后续段落。"
    attempted = []
    def reject(text, parameters, issue):
        attempted.append(1)
        return text, parameters, {"status": "REJECTED", "reason": "无法安全提交标记"}
    monkeypatch.setattr(repair_module, "_repair_bold_marker", reject)
    def review(**kwargs):
        return {"issues": [], "confidence": "high"}
    seed = {"draft": text, "scheme": {}, "evidence": []}
    engine = pipeline(tmp_path, review)
    _, verdict, _, _, _ = review_design(engine, seed, "目标")
    assert review_verdict(text, verdict) == (True, False)
    saved = json.loads(engine.repair_checkpoint_path.read_text(encoding="utf-8"))
    review_design(engine, saved, "目标")
    assert attempted == [1] and saved["format_repair_rounds"] == 1
    assert saved.get("repair_rounds", 0) == 0


def test_three_scientific_patch_rounds_remain_available_after_format_repair(tmp_path):
    from auto_battery_research.tests.test_repair_interfaces import Script
    text = draft() + "\n\n| 字段 | 已声明值**\n\n容量必然提升。"
    script = Script({"容量必然提升": "容量改善仍需验证"}, failed=["issue-0"])
    def review(**kwargs):
        return {"issues": ["结论缺少证据"], "confidence": "low", "repair_issues": [
            {"quote": "容量必然提升", "reason": "结论缺少证据", "suggestion": "降低强度"}]}
    seed = {"draft": text, "scheme": {}, "evidence": []}
    _, verdict, _, _, audit = review_design(pipeline(tmp_path, review, script), seed, "目标")
    assert seed["repair_rounds"] == 3 and seed["format_repair_rounds"] == 1
    assert sum(tag == "writer_patch" for tag, _ in script.calls) == 3
    assert [e["status"] for e in audit] == ["APPLIED", "REJECTED", "REJECTED", "REJECTED"]
    assert verdict["issues"]


def test_local_format_recovery_is_independent_of_exhausted_scientific_budget():
    seed = {"draft": draft() + "\n\n| 字段 | 对照**\n\n完整后续段落。",
            "reviewer_output": {"review_status": "STRUCTURE_INVALID"}, "repair_rounds": 3}
    assert pending_structure_repair(seed)
    seed["format_attempted_hashes"] = [digest({"text": seed["draft"], "parameters": {}})]
    assert not pending_structure_repair(seed)


def test_missing_section_gets_content_patch_before_scientific_review(tmp_path):
    text = draft().replace("## 风险与数据缺口", "## 其他说明")
    tags, calls = [], []
    def chat(system, user, **kwargs):
        tags.append(kwargs["trace_tag"])
        prompt = json.loads(user)
        return json.dumps({"base_hash": prompt["permission"]["base_hash"], "append_sections": [{
            "name": "风险与数据缺口", "text": "证据仍需同体系验证，氧释放与循环保持率必须通过独立对照确认，不得用机理假设替代实验结论。"}]})
    def review(**kwargs):
        assert not classify_design_output(kwargs["draft_answer"])["blocking_issues"]
        calls.append(kwargs["draft_answer"])
        return {"issues": [], "confidence": "high"}
    seed = {"draft": text, "scheme": {}, "evidence": []}
    result, verdict, _, _, _ = review_design(pipeline(tmp_path, review, SimpleNamespace(chat=chat)), seed, "目标")
    assert tags == ["writer_patch"] and calls == [result]
    assert seed["repair_rounds"] == 1 and seed["format_repair_rounds"] == 0
    assert review_verdict(result, verdict) == (True, True)


@pytest.mark.parametrize("counter,limit", [("format_repair_rounds", 3), ("review_rounds", 6)])
def test_structure_recovery_respects_existing_budget(counter, limit):
    seed = {"draft": draft() + "\n\n| 字段 | 对照**\n\n完整后续段落。",
            "reviewer_output": {"review_status": "STRUCTURE_INVALID"}, counter: limit}
    assert not pending_structure_repair(seed)


def test_restored_structure_review_can_issue_and_commit_scientific_patch(tmp_path):
    from auto_battery_research.tests.test_repair_interfaces import Script
    text = draft() + "\n\n| 电解液 | **1 mol/L LiPF6/EC+DMC** | 只作对照**\n\n容量必然提升。"
    calls = []
    script = Script({"容量必然提升": "容量改善仍需同体系验证"})

    def review(**kwargs):
        calls.append(kwargs["draft_answer"])
        assert not design_output_issues(kwargs["draft_answer"])
        return {"issues": ["容量结论缺乏证据"], "confidence": "low", "repair_issues": [
            {"quote": "容量必然提升", "reason": "容量结论缺乏证据", "suggestion": "改为待验证假设"}]}

    seed = {"draft": text, "scheme": {}, "evidence": [], "review_rounds": 1, "repair_rounds": 1,
            "reviewer_output": {"review_status": "STRUCTURE_INVALID", "error_type": "structure"},
            "repair_audit": [{"status": "SCOPE_UNRESOLVED", "issues": []}]}
    result, verdict, _, _, audit = review_design(pipeline(tmp_path, review, script), seed, "目标")
    assert len(calls) == 1 and "容量改善仍需同体系验证" in result
    assert [tag for tag, _ in script.calls] == ["writer_patch", "repair_verify"]
    assert [entry["status"] for entry in audit] == ["SCOPE_UNRESOLVED", "APPLIED", "APPLIED"]
    assert seed["repair_rounds"] == 2 and seed["format_repair_rounds"] == 1 and not verdict["issues"]
    saved = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert saved["draft"] == result and saved["reviewed_hash"] == digest(result)


def test_format_repaired_with_exhausted_content_budget_still_gets_final_review(tmp_path):
    text = draft() + "\n\n| 字段 | 已声明值**\n\n完整后续说明。"
    calls = []

    def review(**kwargs):
        calls.append(kwargs["draft_answer"])
        return {"issues": [], "confidence": "high"}

    seed = {"draft": text, "scheme": {}, "evidence": [], "repair_rounds": 3}
    result, verdict, _, _, _ = review_design(pipeline(tmp_path, review), seed, "目标")
    assert calls == [result] and seed["repair_rounds"] == 3 and seed["format_repair_rounds"] == 1
    assert review_verdict(result, verdict) == (True, True)


@pytest.mark.parametrize("concurrent_write", [False, True])
@pytest.mark.parametrize("restore_titles", [False, True])
def test_existing_report_review_is_published_only_after_checks(tmp_path, monkeypatch, concurrent_write, restore_titles):
    from auto_battery_research.tests.test_experiment_design import setup_design
    from auto_battery_research.tools import workflow_actions
    from auto_battery_research.util.adaptive_retrieval import init_state
    from auto_battery_research.util.stage4_review_recovery import resume_stage4_review
    doc, recipe = setup_design(tmp_path)
    doc.update(final_answer=draft() + "\n\n| 参数 | 已声明值**\n\n后续风险说明。",
               delivery_contract=1, review_completed=False, review_passed=False,
               reviewer_output={"error_type": "structure", "review_status": "STRUCTURE_INVALID", "issues": [BOLD_ERROR]},
               report_readiness={"category": "CONDITIONAL", "issues": [BOLD_ERROR],
                                 "review_status": "UNREVIEWED", "physics_status": "NOT_APPLICABLE"})
    seed = {"draft": doc["final_answer"], "scheme": copy.deepcopy(doc["scheme"]), "evidence": [],
            "parameters": copy.deepcopy(doc["ledger_view"]["parameters"]), "review_rounds": 1, "repair_rounds": 1,
            "reviewer_output": doc["reviewer_output"]}
    if restore_titles:
        from auto_battery_research.tests.test_design_title_recovery import legacy_seed
        legacy = legacy_seed()
        seed.update({k: legacy[k] for k in ("draft", "writer_output", "repair_audit", "repair_rounds", "review_rounds", "pending_review")})
        doc["final_answer"] = seed["draft"]
    init_state(seed)
    doc["retrieval_state"] = copy.deepcopy(seed["retrieval_state"])
    for name, data in (("stage4_repair_checkpoint.json", seed), ("design_scheme.json", doc),
                       ("stage5_execution.json", {"physics": {"status": "NOT_APPLICABLE", "input_hash": digest(doc["scheme"])},
                                                   "experiment": {"status": "PASSED", "plan_hash": recipe["plan_hash"]}}),
                       ("stage_journals.json", [{"stage_id": 4, "notes": "初稿审核"}, {"stage_id": 5, "notes": "参数已核对"}])):
        (tmp_path / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "design_scheme.md").write_text(doc["final_answer"], encoding="utf-8")
    stages = [SimpleNamespace(id=i, key=str(i), name="阶段" + str(i), status="FALLBACK" if i == 4 else "PASSED",
                              skip=False, allow_skip=False, duration_seconds=1) for i in range(1, 7)]
    manager = SimpleNamespace(config={"paths": {"output_dir": str(tmp_path)}}, root_dir=tmp_path,
        target_goal=doc["target"], stages=stages, is_legacy_task=False, state_file_path=tmp_path / ".stage_state.json",
        get_task_output_dir=lambda *a: tmp_path, get_stage_by_id=lambda i: stages[i - 1],
        get_all_stage_journal=lambda: json.loads((tmp_path / "stage_journals.json").read_text(encoding="utf-8")),
        _save_state=lambda: None, set_stage_journal=lambda *a, **kw: None)
    monkeypatch.setattr(workflow_actions, "_match_pinn_system_for_scheme", lambda s: {"status": "NO_MATCH", "matched": False})
    writer = SimpleNamespace(available=True, chat=lambda *a, **kw: pytest.fail("格式修补不可重写科研正文"))
    calls = []

    def reviewer(**kwargs):
        calls.append(kwargs)
        if concurrent_write:
            (tmp_path / "final_research_report.md").write_text("并发任务生成的报告", encoding="utf-8")
        return {"issues": [], "confidence": "high"}

    outcome = resume_stage4_review(manager, writer_client=writer, reviewer=SimpleNamespace(run=reviewer, llm=writer), restore_titles=restore_titles)
    if concurrent_write:
        assert not outcome["success"] and "final_research_report.md" in outcome["error"]
        assert json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8")) == doc
        assert (tmp_path / "final_research_report.md").read_text(encoding="utf-8") == "并发任务生成的报告"
        return
    assert outcome["success"], outcome
    fixed = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    assert fixed["review_completed"] and BOLD_ERROR not in fixed["report_readiness"]["issues"]
    assert fixed["scheme"] == doc["scheme"] and fixed["ledger_hash"] == doc["ledger_hash"]
    assert len(calls) == 1 and "已声明值**" not in fixed["final_answer"]
    if restore_titles:
        assert "### 5.2 数据缺口与获取路径" in fixed["final_answer"]
        saved = json.loads((tmp_path / "stage4_repair_checkpoint.json").read_text(encoding="utf-8"))
        assert saved["repair_rounds"] == 3 and saved["review_rounds"] == 6 and "pending_review" not in saved
        again = resume_stage4_review(manager, reviewer=SimpleNamespace(run=reviewer), restore_titles=True)
        assert again["success"] and again["changed"] is False and len(calls) == 1


def test_stage4_cache_and_gate_resume_pending_structure_without_rebuilding(tmp_path):
    from auto_battery_research.tests.test_experiment_design import setup_design
    from auto_battery_research.tools.rag_adapter import AbrRagAdapter
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
    from auto_battery_research.util.adaptive_retrieval import init_state
    doc, _ = setup_design(tmp_path)
    (tmp_path / "experiment_plan.json").unlink()
    doc.pop("experiment_contract")
    doc.pop("experiment_plan_hash")
    doc.update(final_answer=draft() + "\n\n| 字段 | 明确的原文**\n\n后续说明。",
               delivery_contract=1, report_readiness={"category": "CONDITIONAL", "issues": [BOLD_ERROR]},
               reviewer_output={"review_status": "STRUCTURE_INVALID", "error_type": "structure"})
    seed = {"draft": doc["final_answer"], "scheme": doc["scheme"], "parameters": doc["ledger_view"]["parameters"],
            "evidence": [], "reviewer_output": doc["reviewer_output"]}
    init_state(seed)
    doc["retrieval_state"] = seed["retrieval_state"]
    for name, data in (("design_scheme.json", doc), ("stage4_repair_checkpoint.json", seed)):
        (tmp_path / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "design_scheme.md").write_text(doc["final_answer"], encoding="utf-8")
    manager = SimpleNamespace(root_dir=tmp_path, target_goal=doc["target"], is_legacy_task=False,
                              get_task_output_dir=lambda *a: tmp_path)
    checker = RAGDesignChecker()
    checker.on_init(manager, {"id": 4}, {"paths": {"output_dir": str(tmp_path)}})
    ok, diagnostic = checker.do_check()
    assert not ok and diagnostic["error_code"] == "STAGE4_STRUCTURE_REPAIR_PENDING"

    class ResumeReached(Exception):
        pass

    engine = SimpleNamespace()

    def resume(query):
        assert engine.repair_resume["draft"] == seed["draft"]
        raise ResumeReached()

    engine.run = resume
    adapter = AbrRagAdapter(config={"paths": {}, "runtime_options": {"report_first": True}}, pipeline=engine)
    adapter._build_provenance = lambda: {}
    with pytest.raises(ResumeReached):
        adapter.run_rag_design(doc["target"], tmp_path)
