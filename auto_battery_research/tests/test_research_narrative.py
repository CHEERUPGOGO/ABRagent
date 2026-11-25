"""论证整合的覆盖、来源、审核、防陈旧与只读报告回归。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_research_support import sample, verdict
from auto_battery_research.util.research_records import collect_research
from auto_battery_research.util.research_support import apply_review as apply_record_review
from auto_battery_research.util import research_narrative as narrative


def fixture():
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    doc["research"] = collect_research({"answers": [row]}, doc, params)
    row = doc["research"]["answers"][0]
    apply_record_review(doc["research"], [verdict("answers", row, doc)], doc, params, doc["evidence"])
    rid = row["requirement_id"]
    raw = {"sections": [{"id": "s1", "title": "研究依据与验证边界", "paragraphs": [{
        "id": "p1", "text": "采用{{cathode.material}}探索结构稳定性改善，但协同作用尚需通过同条件对照验证。",
        "requirement_ids": [rid], "source_ids": [f"answers:{rid}:response"],
        "citations": ["p1", "p2"], "parameter_refs": ["cathode.material"], "limitation_ids": []}]}]}
    return doc, raw


def review(doc, draft, supported=True):
    status = "supported" if supported else "unsupported"
    return {"input_fingerprint": draft["input_fingerprint"], "narrative_fingerprint": draft["narrative_fingerprint"],
        "coverage": {"status": status, "reason": "逐段回应原课题且没有新增科研内容"},
        "paragraphs": [{"id": p["id"], "status": status, "reason": "忠实整合原句，条件与假设保留"}
                       for s in draft["sections"] for p in s["paragraphs"]],
        "limitations": [{"id": l["id"], "status": status, "reason": "具体争议在关联段落中明确保留"}
                        for l in narrative.source_context(doc)["limitations"]], "issues": []}


def approved_doc():
    doc, raw = fixture()
    draft = narrative.normalize(raw, doc)
    narrative.apply_review(draft, review(doc, draft), doc, doc["evidence"])
    doc["research_narrative"] = draft
    return doc


def test_integrated_body_replaces_pointwise_body_and_coverage_is_only_in_appendix():
    from auto_battery_research.util.research_reporting import render_research, render_review_appendix, render_summary
    doc = approved_doc()
    before = copy.deepcopy(doc)
    body = render_research(doc)
    assert "### 2.1 研究依据与验证边界" in body and "采用配方A" in body
    assert "课题回应1" not in body and "[p1]" in body and "[p2]" in body
    appendix = render_review_appendix(doc)
    assert "课题逐项回应档案" in appendix and "课题回应1" in appendix and "候选比较档案" in appendix
    summary = render_summary(doc)
    assert "第2.1节·研究依据与验证边界" in appendix
    assert "课题要求覆盖表" in appendix and "| 序号 |" not in summary
    assert all(title in summary for title in ("主要结论", "证据边界", "下一步"))
    assert doc == before


@pytest.mark.parametrize("mutation", ["source", "number", "requirement", "parameter", "self_approve", "duplicate"])
def test_narrative_rejects_missing_sources_unbound_values_and_self_approval(mutation):
    doc, raw = fixture()
    p = raw["sections"][0]["paragraphs"][0]
    if mutation == "source": p["source_ids"] = ["unknown"]
    elif mutation == "number": p["text"] += "保持率达到99%。"
    elif mutation == "requirement": p["requirement_ids"] = []
    elif mutation == "parameter": p["text"] = p["text"].replace("cathode.material", "cathode.unknown")
    elif mutation == "self_approve": raw["status"] = "approved"
    else: raw["sections"].append(copy.deepcopy(raw["sections"][0]))
    with pytest.raises(ValueError):
        narrative.normalize(raw, doc)


@pytest.mark.parametrize("change", ["text", "parameter", "source", "review", "requirement", "evidence"])
def test_changed_inputs_or_edited_narrative_never_inherit_approval(change):
    doc = approved_doc()
    if change == "text": doc["research_narrative"]["sections"][0]["paragraphs"][0]["text"] += "已经验证。"
    elif change == "parameter": doc["ledger_view"]["parameters"]["cathode.material"]["value"] = "配方B"
    elif change == "source": doc["final_answer"] += "新条件。"
    elif change == "review": doc["research_narrative"]["review"]["paragraphs"] = []
    elif change == "requirement": doc["requirements"]["source"] += "额外目标"
    else: doc["evidence"][0]["text"] += "条件已更新"
    assert narrative.current_narrative(doc) is None
    from auto_battery_research.util.research_reporting import render_research, render_review_appendix
    if change == "review":
        assert "本段待复核" in narrative.render(doc)
    else:
        assert narrative.display_narrative(doc) is None
    assert "**课题回应1" not in render_research(doc)
    assert "课题回应1" in render_review_appendix(doc)


def test_pending_scientific_limits_require_explicit_mapping_and_reviewer_acceptance():
    doc, raw = fixture()
    row = doc["research"]["answers"][0]
    row["support_review"]["fields"]["response"] = {"status": "needs_review", "reason": "跨体系推断不能作为本批实证"}
    preliminary = narrative.normalize(raw, doc)
    assert any("限制" in note for note in preliminary["coverage_notes"])
    # 关联漏填交给实际表述审核；缺少有效审核仍不获通过。
    narrative.apply_review(preliminary, {}, doc, doc["evidence"])
    assert preliminary["status"] == "rejected"
    p = raw["sections"][0]["paragraphs"][0]
    p["limitation_ids"] = list(p["source_ids"])
    draft = narrative.normalize(raw, doc)
    rejected = review(doc, draft)
    rejected["limitations"][0].update(status="unsupported", reason="正文未写明跨体系限制")
    narrative.apply_review(draft, rejected, doc, doc["evidence"])
    assert draft["status"] == "rejected"
    doc["research_narrative"] = draft
    rendered = narrative.render(doc)
    assert "本段待复核" in rendered and "正文未写明跨体系限制" in rendered
    assert "以下为原稿" in rendered


def test_explicit_limitation_id_also_binds_its_existing_source():
    doc, raw = fixture()
    row = doc["research"]["answers"][0]
    row["support_review"]["fields"]["reason"] = {"status": "needs_review", "reason": "跨体系适用性仍需验证"}
    key = f"answers:{row['requirement_id']}:reason"
    p = raw["sections"][0]["paragraphs"][0]
    p["limitation_ids"] = [key]
    draft = narrative.normalize(raw, doc)
    assert key in draft["sections"][0]["paragraphs"][0]["source_ids"]
    assert draft["status"] == "pending"


def two_paragraph_doc():
    doc, raw = fixture()
    first = raw["sections"][0]["paragraphs"][0]
    first["citations"] = ["p1"]
    second = copy.deepcopy(first)
    second.update(id="p2", text="材料候选的组成选择依据仍须独立核对，不应把待筛选范围当作已确认的最优范围。", citations=["p2"])
    raw["sections"][0]["paragraphs"].append(second)
    draft = narrative.normalize(raw, doc)
    return doc, draft


def test_partial_review_keeps_good_paragraph_and_marks_problem_without_changing_scientific_status():
    from auto_battery_research.util.research_reporting import render_research, render_summary, render_review_appendix
    doc, draft = two_paragraph_doc()
    result = review(doc, draft)
    result["paragraphs"][1].update(status="needs_review", reason="组成窗口的选择依据不足")
    result["coverage"].update(status="needs_review", reason="组成选择的科学边界仍待补充")
    result["issues"] = ["p2：未说明组成窗口的选择依据"]
    narrative.apply_review(draft, result, doc, doc["evidence"])
    doc["research_narrative"] = draft
    before = copy.deepcopy(doc)
    assert narrative.current_narrative(doc) is None and draft["status"] == "rejected"
    body = render_research(doc)
    assert "1段通过当前来源与表述审核，1段待复核" in body
    assert "\n\n采用配方A" in body
    assert "> 材料候选的组成选择依据" in body
    assert "**本段待复核" in body and "组成窗口的选择依据不足" in body
    assert "组成选择的科学边界仍待补充" in body and "课题回应1" not in body
    summary = render_summary(doc)
    assert "有1段待复核" in summary and "| 序号 |" not in summary
    assert "材料候选的组成选择依据" not in summary
    assert "课题要求覆盖表" in render_review_appendix(doc)
    assert doc == before


@pytest.mark.parametrize("missing", ["paragraph", "evidence", "duplicate"])
def test_incomplete_paragraph_audit_only_marks_affected_paragraph(missing):
    doc, draft = two_paragraph_doc()
    result = review(doc, draft)
    if missing == "paragraph": result["paragraphs"].pop()
    elif missing == "duplicate": result["paragraphs"].append(copy.deepcopy(result["paragraphs"][1]))
    narrative.apply_review(draft, result, doc, doc["evidence"])
    if missing == "evidence": draft["reviewed_evidence_ids"].remove("p2")
    doc["research_narrative"] = draft
    displayed = narrative.display_narrative(doc)
    assert displayed["paragraph_reviews"]["p1"] == []
    assert displayed["paragraph_reviews"]["p2"]
    assert "1段通过当前来源与表述审核，1段待复核" in narrative.render(doc)


def test_unscoped_issue_stays_visible_without_rejecting_supported_paragraphs():
    doc, draft = two_paragraph_doc()
    result = review(doc, draft)
    result["issues"] = ["章节标题存在过度概括，需核对其适用范围"]
    narrative.apply_review(draft, result, doc, doc["evidence"])
    doc["research_narrative"] = draft
    body = narrative.render(doc)
    assert "2段通过当前来源与表述审核，0段待复核" in body
    assert "整体待复核事项" in body and result["issues"][0] in body
    assert "本段待复核" not in body
    assert narrative.current_narrative(doc) is None


def test_changed_rejected_draft_cannot_inherit_partial_display():
    doc, draft = two_paragraph_doc()
    result = review(doc, draft, supported=False)
    narrative.apply_review(draft, result, doc, doc["evidence"])
    doc["research_narrative"] = draft
    assert narrative.display_narrative(doc)
    draft["sections"][0]["paragraphs"][0]["text"] += "额外的科学结论。"
    assert narrative.display_narrative(doc) is None


def test_missing_review_or_missing_evidence_never_passes():
    doc, raw = fixture()
    draft = narrative.normalize(raw, doc)
    narrative.apply_review(draft, review(doc, draft), doc, [])
    assert draft["status"] == "rejected"
    result = review(doc, draft); result["narrative_fingerprint"] = "wrong"
    narrative.apply_review(draft, result, doc, doc["evidence"])
    assert draft["status"] == "rejected"


def test_parameter_reference_uses_its_declared_source_without_inventing_paragraph_evidence():
    doc, raw = fixture()
    params = doc["ledger_view"]["parameters"]
    doc["final_answer"] += "\n采用半电池评价。"
    params["packaging.cell_type"] = {"value": "半电池", "unit": "", "source_quote": "采用半电池评价。"}
    doc["research"]["answers"][0]["parameter_refs"].append("packaging.cell_type")
    # 原句变化使研究审核失效，重新审核后整合的参数仍从明确来源绑定。
    row = doc["research"]["answers"][0]
    apply_record_review(doc["research"], [verdict("answers", row, doc)], doc, params, doc["evidence"])
    p = raw["sections"][0]["paragraphs"][0]
    p["text"] += "评价采用{{packaging.cell_type}}。"
    p["parameter_refs"].append("packaging.cell_type")
    draft = narrative.normalize(raw, doc)
    assert draft["status"] == "pending"
    narrative.apply_review(draft, review(doc, draft), doc, doc["evidence"])
    doc["research_narrative"] = draft
    params["packaging.cell_type"]["value"] = "全电池"
    assert narrative.current_narrative(doc) is None


def test_binding_errors_are_returned_together_for_one_bounded_repair():
    doc, raw = fixture()
    p = raw["sections"][0]["paragraphs"][0]
    p["text"] += "无来源数值99%。"
    other = copy.deepcopy(p)
    other.update(id="p2", text="该段提出来源未提供的123数值，必须拒绝。", parameter_refs=[])
    raw["sections"][0]["paragraphs"].append(other)
    with pytest.raises(ValueError) as exc:
        narrative.normalize(raw, doc)
    assert "99" in str(exc.value) and "123" in str(exc.value)


def test_fraction_range_endpoint_uses_display_amount_and_rejects_new_value():
    doc, raw = fixture()
    params = doc["ledger_view"]["parameters"]
    doc["final_answer"] += "\nLi过量3–5 wt%，属于工程候选。"
    params["process.li_excess_min"] = {"value": 0.03, "unit": "fraction", "quantity_kind": "mass_fraction",
        "denominator": "名义Li量", "source_quote": "Li过量3–5 wt%，属于工程候选。"}
    row = doc["research"]["answers"][0]
    row["parameter_refs"].append("process.li_excess_min")
    apply_record_review(doc["research"], [verdict("answers", row, doc)], doc, params, doc["evidence"])
    p = raw["sections"][0]["paragraphs"][0]
    p["text"] += "Li过量下限{{process.li_excess_min}}仅作工程候选。"
    p["parameter_refs"].append("process.li_excess_min")
    assert narrative.normalize(raw, doc)["status"] == "pending"
    params["process.li_excess_min"]["value"] = 0.09
    with pytest.raises(ValueError, match="来源支持"):
        narrative.normalize(raw, doc)


def test_integration_is_bounded_checkpointed_and_does_not_change_research():
    doc, raw = fixture()
    before = copy.deepcopy(doc)
    state, calls, checkpoints = {}, [], []
    def generate(*a, **kw):
        calls.append("generate")
        assert state["phase"] == "generating"
        return json.dumps(raw)
    def audit(**kw):
        calls.append("review")
        assert state["phase"] == "reviewing"
        return review(kw["doc"], kw["narrative"], supported=calls.count("review") == 2)
    client = SimpleNamespace(available=True, chat=generate)
    reviewer = SimpleNamespace(review_research_narrative=audit)
    narrative.integrate(doc, client, reviewer, state, lambda: checkpoints.append(copy.deepcopy(state)))
    assert calls == ["generate", "review", "generate", "review"]
    assert narrative.current_narrative(doc)
    for key in before: assert doc[key] == before[key]
    narrative.integrate(doc, client, reviewer, state)
    assert len(calls) == 4
    assert state["generation_calls"] == state["review_calls"] == 2


def test_interrupted_call_is_not_repeated_and_stale_state_cannot_reset_budget():
    doc, _ = fixture()
    client = SimpleNamespace(available=True, chat=lambda *a, **k: pytest.fail("不得重复调用"))
    reviewer = SimpleNamespace(review_research_narrative=lambda **kw: pytest.fail("不得重复审核"))
    state = {"input_fingerprint": narrative.input_fingerprint(doc), "generation_calls": 1, "phase": "generating"}
    narrative.integrate(doc, client, reviewer, state)
    assert state["phase"] == "interrupted" and state["generation_calls"] == 1
    narrative.integrate(doc, client, reviewer, state)
    assert state["generation_calls"] == 1
    doc["final_answer"] += "输入发生变化"
    narrative.integrate(doc, client, reviewer, state)
    assert doc["research_narrative"]["status"] == "stale" and state["generation_calls"] == 1


def test_existing_valid_proposal_still_needs_review_without_regeneration():
    doc, raw = fixture()
    state = {"phase": "proposal", "submitted_draft": raw}
    client = SimpleNamespace(available=True, chat=lambda *a, **k: pytest.fail("不得重生成已通过绑定的稿件"))
    reviewer = SimpleNamespace(review_research_narrative=lambda **kw: review(kw["doc"], kw["narrative"]))
    narrative.integrate(doc, client, reviewer, state)
    assert state.get("generation_calls", 0) == 0 and state["review_calls"] == 1
    assert narrative.current_narrative(doc)


def test_review_package_keeps_source_evidence_even_if_paragraph_omits_inline_citation():
    doc, raw = fixture()
    raw["sections"][0]["paragraphs"][0]["citations"] = []
    doc["evidence"] = [{"passage_id": f"noise{i}", "text": "无关参考"} for i in range(30)] + doc["evidence"]
    client = SimpleNamespace(available=True, chat=lambda *a, **k: json.dumps(raw))
    def audit(**kw):
        assert {"p1", "p2"} <= {e["passage_id"] for e in kw["evidence"]}
        return review(kw["doc"], kw["narrative"])
    narrative.integrate(doc, client, SimpleNamespace(review_research_narrative=audit))
    assert narrative.current_narrative(doc)


@pytest.mark.parametrize("fail_preview", [False, True])
@pytest.mark.parametrize("partial_review", [False, True])
def test_existing_task_integration_publishes_only_after_final_check(tmp_path, monkeypatch, fail_preview, partial_review):
    from auto_battery_research.tests.test_final_design_reporting import report_fixture
    from auto_battery_research.tests.test_experiment_design import setup_design
    from auto_battery_research.tests.test_research_contract import research_doc
    from auto_battery_research.util.research_recovery import integrate_research_narrative
    from auto_battery_research.util.research_experiments import fallback_links
    from auto_battery_research.util.experiment_design import plan_hash, validate
    mgr, _, _, _ = report_fixture(tmp_path)
    doc, experiment = setup_design(tmp_path)
    base = research_doc()
    doc.update(target=base["target"], requirements=base["requirements"], final_answer="采用NCM811作为探索性方案，仍需同条件对照验证。")
    rid = doc["requirements"]["items"][0]["id"]
    row = {"requirement_id": rid, "status": "answered", "verification": "unverified",
        "response": doc["final_answer"], "reason": "", "next_step": "", "citations": [], "parameter_refs": [],
        "source_quotes": [doc["final_answer"]], "field_support": {"response": [0]}}
    doc["research"] = collect_research({"answers": [row]}, doc, doc["ledger_view"]["parameters"])
    row = doc["research"]["answers"][0]
    apply_record_review(doc["research"], [verdict("answers", row, doc)], doc, doc["ledger_view"]["parameters"], [])
    fallback_links(experiment, doc)
    experiment["parameters"]["cathode.active_fraction"]["canonical_path"] = "cathode.active_fraction"
    experiment["plan_hash"] = plan_hash(experiment)
    experiment["bindings"] = validate(experiment, doc)
    doc["experiment_plan_hash"] = experiment["plan_hash"]
    mgr.target_goal = doc["target"]
    for name in ("pinn_input_spec.json", "pinn_simulation_result.json"):
        (tmp_path / name).unlink()
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "design_scheme.md").write_text(doc["final_answer"], encoding="utf-8")
    (tmp_path / "experiment_plan.json").write_text(json.dumps(experiment, ensure_ascii=False), encoding="utf-8")
    raw = {"sections": [{"id": "s1", "title": "研究依据与验证边界", "paragraphs": [{
        "id": "p1", "text": doc["final_answer"], "requirement_ids": [rid], "source_ids": [f"answers:{rid}:response"],
        "citations": [], "parameter_refs": [], "limitation_ids": []}]}]}
    client = SimpleNamespace(available=True, chat=lambda *a, **k: json.dumps(raw))
    reviewer = SimpleNamespace(review_research_narrative=lambda **kw: review(kw["doc"], kw["narrative"], supported=not partial_review))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    if fail_preview:
        from auto_battery_research.checkers.final_report_checker import FinalReportChecker
        monkeypatch.setattr(FinalReportChecker, "do_check", lambda self: (False, {"error": "模拟报告拒绝"}))
    result = integrate_research_narrative(mgr, client=client, reviewer=reviewer)
    if fail_preview:
        assert not result["success"]
        assert all((tmp_path / name).read_bytes() == content for name, content in before.items())
    else:
        assert result["success"], result
        saved = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
        if partial_review:
            assert narrative.current_narrative(saved) is None
            assert saved["research_narrative"]["status"] == "rejected"
            assert result["paragraphs_pending"] == 1
        else:
            assert narrative.current_narrative(saved)
        assert saved["research"] == doc["research"] and saved["scheme"] == doc["scheme"]
        body = (tmp_path / "final_research_report.md").read_text(encoding="utf-8")
        supplement = (tmp_path / "final_research_report_supplement.md").read_text(encoding="utf-8")
        assert "### 2.1 研究依据与验证边界" in body
        assert "当前课题要求覆盖" not in body and "当前课题要求覆盖" in supplement
        if partial_review:
            from auto_battery_research.checkers.final_report_checker import FinalReportChecker
            assert "本段待复核，以下为原稿" in body
            (tmp_path / "final_research_report.md").write_text(body.replace("本段待复核，以下为原稿", "本段为已确认结论"), encoding="utf-8")
            checker = FinalReportChecker()
            checker.on_init(mgr, {"id": 6}, mgr.config)
            passed, diagnostic = checker.do_check()
            assert not passed and diagnostic["error_code"] == "REPORT_FINAL_INPUT_MISMATCH"


def test_normal_reviewer_role_handles_narrative_and_only_returns_verdict():
    from auto_battery_research.rag import ReviewerAgent
    doc, raw = fixture()
    draft = narrative.normalize(raw, doc)
    received = []
    def chat(system, user, **kw):
        received.append((system, json.loads(user), kw))
        return json.dumps(review(doc, draft))
    reviewer = ReviewerAgent(SimpleNamespace(available=True, chat=chat))
    result = reviewer.review_research_narrative(doc=doc, narrative=draft, evidence=doc["evidence"])
    assert result["narrative_fingerprint"] == draft["narrative_fingerprint"]
    assert received[0][2]["trace_tag"] == "research_narrative_review"
    assert received[0][1]["prompt"] == doc["requirements"]["source"]
