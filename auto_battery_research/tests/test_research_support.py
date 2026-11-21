"""多段摘要：可定位来源、逐字段引用、审核指纹和局部恢复回归。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_research_contract import research_doc
from auto_battery_research.util.research_records import collect_research, validate_record
from auto_battery_research.util.research_support import (
    normalize, apply_review, render_field, supported, fingerprint, refresh_for_review,
)


def sample():
    doc = research_doc()
    quotes = ["方案采用配方A。", "该路线提高结构稳定性 [p1]。", "协同效果尚未经过本批实验验证，需与未改性样品比较 [p2]。"]
    doc["final_answer"] = "\n\n".join(quotes)
    doc["evidence"] = [{"passage_id": "p1", "text": "结构稳定性改善", "source": "文献甲"},
                       {"passage_id": "p2", "text": "需要同条件对照", "source": "文献乙"}]
    row = {"requirement_id": doc["requirements"]["items"][0]["id"], "status": "answered",
           "verification": "unverified", "response": "采用{{cathode.material}}探索结构稳定性改善，协同效果仍需验证。",
           "reason": "协同结论尚未有本批实证。", "next_step": "开展同条件的未改性对照实验。",
           "source_quotes": quotes, "field_support": {"response": [0, 1, 2], "reason": [2], "next_step": [2]},
           "citations": ["p1", "p2"], "parameter_refs": ["cathode.material"]}
    return doc, row


def verdict(collection, row, doc, status="supported"):
    from auto_battery_research.util.research_support import FIELDS
    return {"collection": collection, "id": row.get("requirement_id") or row["id"],
            "fingerprint": fingerprint(row, doc, doc["ledger_view"]["parameters"]),
            "fields": {f: {"status": status, "reason": "忠实综合所绑定原文，未增加事实"}
                       for f in FIELDS[collection] if row.get(f)}}


@pytest.mark.parametrize("stale", [False, True])
def test_missing_review_completion_is_bounded_and_never_overwrites_existing_verdict(stale):
    from auto_battery_research.util.research_recovery import complete_missing_support_review
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    row = normalize(row, "answers", doc, params)
    doc["research"] = dict(answers=[row], candidates=[], arguments=[])
    first = verdict("answers", row, doc, status="unsupported")
    first["fields"] = {"response": first["fields"]["response"]}
    original = dict(research_support_review=[first], issues=[])
    apply_review(doc["research"], [first], doc, params, doc["evidence"])
    calls = []
    def review(**kwargs):
        calls.append(kwargs)
        v = verdict("answers", row, doc)
        if stale:
            v["fingerprint"] = "stale"
        return dict(research_support_review=[v], issues=[])
    merged, count = complete_missing_support_review(SimpleNamespace(run=review), doc, original, doc["evidence"])
    assert count == 1 and len(calls) == 1 and "reason/next_step" in calls[0]["history_context"]
    apply_review(doc["research"], merged["research_support_review"], doc, params, doc["evidence"])
    assert row["support_review"]["fields"]["response"]["status"] == "unsupported"
    assert supported(row, "reason", doc, params) is (not stale)
    assert supported(row, "next_step", doc, params) is (not stale)
    if not stale:
        assert complete_missing_support_review(SimpleNamespace(run=review), doc, merged, doc["evidence"])[1] == 0


def test_analysis_summary_accepts_multiple_exact_quotes_but_waits_for_review():
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    validate_record(row, "answers", doc, params)
    row = normalize(row, "answers", doc, params)
    assert len(row["source_locations"]) == 3
    assert "分析摘要待核验" in render_field(row, "response", doc)
    assert not supported(row, "response", doc, params)
    research = {"answers": [row]}
    apply_review(research, [verdict("answers", row, doc)], doc, params, doc["evidence"])
    assert render_field(row, "response", doc) == "采用配方A探索结构稳定性改善，协同效果仍需验证。"


@pytest.mark.parametrize("change", [
    lambda r: r["source_quotes"].append("不存在的原文"),
    lambda r: r.update(response="保持率达到99%"),
    lambda r: r["field_support"].update(response=[99]),
    lambda r: r.update(response="该机制已经验证 [unknown]"),
    lambda r: r.update(response="采用{{unknown.material}}"),
    lambda r: r["field_support"].update(response=[1]),
])
def test_fabricated_sources_values_or_bad_field_binding_fail(change):
    doc, row = sample()
    change(row)
    with pytest.raises(ValueError):
        validate_record(row, "answers", doc, doc["ledger_view"]["parameters"])


def test_correct_number_but_wrong_conditions_or_claim_strength_can_be_rejected_by_reviewer():
    doc, row = sample()
    row["response"] = "结构改善已经证明抑制氧释放。"
    row["parameter_refs"] = []
    row = normalize(row, "answers", doc, doc["ledger_view"]["parameters"])
    review = verdict("answers", row, doc)
    review["fields"]["response"] = {"status": "unsupported", "reason": "结构稳定性不能直接证明抑氧"}
    apply_review({"answers": [row]}, [review], doc, doc["ledger_view"]["parameters"], doc["evidence"])
    assert "已经证明" not in render_field(row, "response", doc)
    assert "归档原文" in render_field(row, "response", doc)


@pytest.mark.parametrize("what", ["summary", "source", "evidence", "parameter"])
def test_review_expires_after_input_mutation(what):
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    row = normalize(row, "answers", doc, params)
    apply_review({"answers": [row]}, [verdict("answers", row, doc)], doc, params, doc["evidence"])
    if what == "summary": row["response"] += "新结论"
    elif what == "source": doc["final_answer"] += "补充原文"
    elif what == "evidence": doc["evidence"][0]["text"] += "修正"
    else: params["cathode.material"]["value"] = "配方B"
    assert not supported(row, "response", doc, params)


def test_absent_review_evidence_and_missing_verdict_do_not_pass():
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    row = normalize(row, "answers", doc, params)
    apply_review({"answers": [row]}, [verdict("answers", row, doc)], doc, params, [])
    assert not supported(row, "response", doc, params)
    assert not supported(row, "next_step", doc, params)
    apply_review({"answers": [row]}, [], doc, params, doc["evidence"])
    assert not supported(row, "response", doc, params)


def test_malformed_support_verdict_stays_pending():
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    row = normalize(row, "answers", doc, params)
    review = verdict("answers", row, doc)
    review["fields"]["response"]["status"] = ["supported"]
    apply_review({"answers": [row]}, [review], doc, params, doc["evidence"])
    assert not supported(row, "response", doc, params)


def test_existing_reviewer_prompt_and_finish_review_share_the_same_summary():
    from auto_battery_research.rag import ReviewerAgent
    from auto_battery_research.util.research_support import finish_review
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    doc["research"] = collect_research({"answers": [row]}, doc, params)
    doc["raw_archive_sentinel"] = "整份历史档案不应重复发送"
    received = []
    def chat(system, prompt, **kw):
        received.append(prompt)
        summary = doc["research"]["answers"][0]
        return json.dumps({"confidence": "medium", "issues": [],
            "requirement_review": {"source_checked": True, "missing_quotes": [], "misread_ids": [], "unanswered_ids": []},
            "research_support_review": [verdict("answers", summary, doc)]})
    reviewer = ReviewerAgent(SimpleNamespace(available=True, chat=chat))
    reviewer.requirements = doc["requirements"]
    pipeline = SimpleNamespace(writer=SimpleNamespace(llm=None), reviewer=reviewer)
    seed = {"research_draft": doc, "maintenance_calls": 2}
    refreshed = refresh_for_review(pipeline, seed, doc["final_answer"], params, doc["evidence"])
    review = reviewer.run(question=doc["target"], draft_answer=doc["final_answer"], evidence=doc["evidence"])
    finish_review(pipeline, seed, review, params, doc["evidence"], doc["evidence"])
    summary = refreshed["research"]["answers"][0]
    assert summary["support_fingerprint"] in received[0]
    assert "research_support_review" in received[0]
    assert "raw_archive_sentinel" not in received[0]
    assert supported(summary, "response", refreshed, params)
    assert len(received) == 1 and seed["maintenance_calls"] == 2


def test_service_failure_keeps_existing_research_and_records_the_cause():
    from auto_battery_research.util.scheme_ledger import collect_parameters
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    doc["research"] = collect_research({"answers": [row]}, doc, params)
    def chat(*a, **kw):
        raise RuntimeError("模拟服务超时")
    collect_parameters(doc, SimpleNamespace(available=True, chat=chat))
    assert doc["research"]["answers"][0]["response"] == row["response"]
    assert doc["parameter_diagnostics"][0][0]["reason"] == "模拟服务超时"
    assert doc["maintenance_calls"] == 1


def test_research_only_drops_unauthorized_parameters_but_keeps_valid_records():
    from auto_battery_research.util.scheme_ledger import collect_parameters
    doc, row = sample()
    doc["research_only"] = True
    original = copy.deepcopy(doc["ledger_view"])
    doc["scheme_parameters"] = {"cathode.material": {"value": "原配方"}}
    existing = copy.deepcopy(doc["scheme_parameters"])
    received = []
    def chat(system, user, **kw):
        received.append(system)
        return json.dumps({"parameters": {"cathode.material": {"value": "越权配方"}},
                           "research": {"answers": [row]}})
    collect_parameters(doc, SimpleNamespace(available=True, chat=chat))
    assert doc["research"]["answers"][0]["response"] == row["response"]
    assert doc["ledger_view"] == original and doc["scheme_parameters"] == existing
    assert "仅输出JSON" in received[0] and "完整沿用current_parameters" in received[0]
    assert doc["parameter_diagnostics"][0][0]["path"] == "$scope"


def test_model_cannot_self_approve_and_valid_prior_review_survives_partial_retry():
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    row = normalize(row, "answers", doc, params)
    apply_review({"answers": [row]}, [verdict("answers", row, doc)], doc, params, doc["evidence"])
    raw = {"answers": [row], "candidates": [], "arguments": [], "selected_candidate_id": None}
    first = collect_research(raw, doc, params)
    assert "support_review" not in first["answers"][0]
    apply_review(first, [verdict("answers", first["answers"][0], doc)], doc, params, doc["evidence"])
    next_result = collect_research({}, doc, params, first)
    assert supported(next_result["answers"][0], "response", doc, params)


def test_research_only_repair_response_does_not_require_parameters():
    from auto_battery_research.util.scheme_ledger import collect_parameters
    doc, row = sample()
    bad = copy.deepcopy(row); bad["response"] = "不存在99%的成绩"
    calls = []
    def chat(system, user, **kw):
        calls.append(json.loads(user))
        return json.dumps({"parameters": {}, "research": {"answers": [bad]}} if len(calls) == 1
                          else {"research": {"answers": [row]}})
    doc.pop("research")
    collect_parameters(doc, SimpleNamespace(available=True, chat=chat))
    assert len(calls) == 2
    assert doc["research"]["answers"][0]["response"] == row["response"]
    assert not doc["research"]["record_errors"]


def test_rejected_answer_keeps_only_locatable_sources_without_presenting_failed_summary():
    from auto_battery_research.util.research_contract import finalize_research
    doc, row = sample()
    row["response"] = "捏造的99%结果"
    row["source_quotes"].append("伪造原句")
    doc["research"] = collect_research({"answers": [row]}, doc, doc["ledger_view"]["parameters"])
    doc["reviewer_output"] = {"requirement_review": {"source_checked": True, "missing_quotes": [],
        "misread_ids": [], "unanswered_ids": [row["requirement_id"]]}}
    finalize_research(doc)
    body = render_field(doc["research"]["answers"][0], "response", doc)
    assert "研究回答待核验" in body and "方案采用配方A" in body
    assert "捏造的99%" not in body and "伪造原句" not in body
    doc["final_answer"] = "正文已替换"
    assert render_field(doc["research"]["answers"][0], "response", doc) == "待补充"


def test_refresh_after_review_is_bounded_and_cannot_inherit_old_acceptance():
    doc, row = sample(); params = doc["ledger_view"]["parameters"]
    doc["research"] = collect_research({"answers": [row]}, doc, params)
    apply_review(doc["research"], [verdict("answers", doc["research"]["answers"][0], doc)], doc, params, doc["evidence"])
    pipeline = SimpleNamespace(writer=SimpleNamespace(llm=None), reviewer=SimpleNamespace())
    seed = {"research_draft": doc, "maintenance_calls": 2}
    changed = doc["final_answer"] + "\n新的适用条件"
    refreshed = refresh_for_review(pipeline, seed, changed, params, doc["evidence"])
    assert seed["maintenance_calls"] == 2
    assert not supported(refreshed["research"]["answers"][0], "response", refreshed, params)


def test_empty_candidate_list_keeps_section_22():
    from auto_battery_research.util.research_reporting import render_research
    doc, _ = sample()
    doc["research"]["candidates"] = []
    doc["research"]["selected_candidate_id"] = None
    body = render_research(doc)
    assert "### 2.2 候选比较与取舍" in body and "当前没有通过提取校验" in body


def choice_doc():
    from auto_battery_research.util.research_records import identity_bindings
    doc = research_doc()
    params = doc["ledger_view"]["parameters"]
    params["cathode.nominal_formula"] = {"value": "LiMnO2", "unit": "", "quantity_kind": "text"}
    params["anode.material"] = {"value": "金属锂", "unit": ""}
    quotes = ["当前组成 LiMnO2。", "结构线索来自另一体系，仍需确认适用性 [na]。", "需要同条件对照，尚未证明最优。"]
    doc["final_answer"] = "\n\n".join(quotes)
    doc["evidence"] = [{"passage_id": "na", "text": "另一体系的结构线索"}]
    candidate = {"id": "main", "name": "LiMnO2", "rank": None, "scope": "candidate",
        "summary": "当前探索组成 LiMnO2。", "rationale": "已证明所有体系性能最优。", "risks": "仍需同条件对照。",
        "source_quotes": quotes, "field_support": {"summary": [0], "rationale": [1], "risks": [2]},
        "citations": ["na"], "parameter_refs": ["cathode.nominal_formula"]}
    candidate = normalize(candidate, "candidates", doc, params)
    doc["research"].update(candidates=[candidate], selected_candidate_id=None, bound_fields=identity_bindings(params))
    review = verdict("candidates", candidate, doc)
    review["fields"]["rationale"] = {"status": "needs_review", "reason": "跨体系适用性待核验"}
    apply_review(doc["research"], [review], doc, params, doc["evidence"])
    return doc


def test_section24_keeps_current_composition_and_field_review_without_restoring_selection():
    from auto_battery_research.util.research_reporting import render_research
    doc = choice_doc()
    before = copy.deepcopy(doc)
    body = render_research(doc).split("### 2.4", 1)[1].split("### 2.5", 1)[0]
    assert "当前方案" in body and "**LiMnO2**" in body and "选材考虑" in body
    assert "结构线索来自另一体系" in body and "[na]" in body and "跨体系适用性待核验" in body
    assert "已证明所有体系性能最优" not in body and "尚无通过结构化校验" not in body
    assert "同条件对照" in body and "取舍与验证边界" in body
    assert doc == before and doc["research"]["selected_candidate_id"] is None


def test_sample_id_can_link_current_formula_only_through_exact_summary_sources():
    from auto_battery_research.util.research_reporting import render_research
    from auto_battery_research.util.research_support import normalize
    doc = choice_doc()
    candidate = doc["research"]["candidates"][0]
    doc["final_answer"] += "\n\n| C31 | 当前主推 |"
    candidate.update(name="C31", summary="C31名义组成为{{cathode.nominal_formula}}。")
    candidate["source_quotes"].append("| C31 | 当前主推 |")
    candidate["field_support"]["summary"] = [0, 3]
    candidate = normalize(candidate, "candidates", doc, doc["ledger_view"]["parameters"])
    doc["research"]["candidates"] = [candidate]
    review = verdict("candidates", candidate, doc)
    review["fields"]["rationale"] = dict(status="needs_review", reason="跨体系适用性待核验")
    apply_review(doc["research"], [review], doc, doc["ledger_view"]["parameters"], doc["evidence"])
    before = copy.deepcopy(doc)
    choice = render_research(doc).split("### 2.4", 1)[1]
    assert "结构线索来自另一体系" in choice and "跨体系适用性待核验" in choice
    assert doc == before and doc["research"]["selected_candidate_id"] is None
    candidate["name"] = "C3"
    assert "尚未归档可明确对应当前组成的选择理由" in render_research(doc).split("### 2.4", 1)[1]
    candidate["name"] = "C31"
    candidate["source_quotes"][0] = "当前组成LiNiO2。"
    assert "尚未归档可明确对应当前组成的选择理由" in render_research(doc).split("### 2.4", 1)[1]


@pytest.mark.parametrize("change", ["different_formula", "stale_binding", "ambiguous", "bad_source"])
def test_section24_does_not_attach_stale_ambiguous_or_unlocatable_candidate_reasons(change):
    from auto_battery_research.util.research_reporting import render_research
    doc = choice_doc()
    if change == "different_formula":
        doc["ledger_view"]["parameters"]["cathode.nominal_formula"]["value"] = "LiNiO2"
    elif change == "stale_binding":
        doc["research"]["bound_fields"]["cathode.nominal_formula"]["value"] = "旧组成"
    elif change == "ambiguous":
        other = copy.deepcopy(doc["research"]["candidates"][0]); other["id"] = "other"
        doc["research"]["candidates"].append(other)
    else:
        doc["final_answer"] = "原始正文已替换"
    body = render_research(doc).split("### 2.4", 1)[1].split("### 2.5", 1)[0]
    assert "当前方案" in body and "尚未归档可明确对应当前组成的选择理由" in body
    assert "结构线索来自另一体系" not in body and "[na]" not in body


@pytest.mark.parametrize("fail_preview", [False, True])
def test_recovery_previews_report_and_preserves_scientific_inputs(tmp_path, monkeypatch, fail_preview):
    from auto_battery_research.tests.test_final_design_reporting import report_fixture
    from auto_battery_research.tests.test_experiment_design import setup_design
    from auto_battery_research.util.research_recovery import repair_research_records
    mgr, _, _, _ = report_fixture(tmp_path)
    doc, experiment = setup_design(tmp_path)
    research = research_doc()
    doc.update(target=research["target"], requirements=research["requirements"])
    doc["final_answer"] = "候选甲为当前探索方案，采用NCM811。循环结论仍需同条件测试。"
    from auto_battery_research.util.research_experiments import fallback_links
    fallback_links(experiment, doc)
    experiment["parameters"]["cathode.active_fraction"]["canonical_path"] = "cathode.active_fraction"
    from auto_battery_research.util.experiment_design import plan_hash, validate
    experiment["plan_hash"] = plan_hash(experiment)
    experiment["bindings"] = validate(experiment, doc)
    doc["experiment_plan_hash"] = experiment["plan_hash"]
    mgr.target_goal = doc["target"]
    for n in ("pinn_input_spec.json", "pinn_simulation_result.json"):
        (tmp_path / n).unlink()
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "design_scheme.md").write_text(doc["final_answer"], encoding="utf-8")
    (tmp_path / "experiment_plan.json").write_text(json.dumps(experiment, ensure_ascii=False), encoding="utf-8")
    quotes = doc["final_answer"].split("。")
    candidate = {"id": "a", "name": "候选甲", "rank": None, "scope": "candidate", "summary": "探索性候选，采用NCM811。",
        "rationale": "仍需同条件测试。", "risks": "尚无本批循环结论。", "source_quotes": [quotes[0] + "。", quotes[1] + "。"],
        "field_support": {"summary": [0], "rationale": [1], "risks": [1]}, "citations": [], "parameter_refs": []}
    client = SimpleNamespace(available=True, chat=lambda *a, **k: json.dumps({"research": {"candidates": [candidate]}}))
    def review(**kwargs):
        doc_for_review = reviewer.research_draft
        c = doc_for_review["research"]["candidates"][0]
        return {"confidence": "medium", "issues": [], "requirement_review": {"source_checked": True, "missing_quotes": [], "misread_ids": [], "unanswered_ids": []},
                "research_support_review": [verdict("candidates", c, doc_for_review)]}
    reviewer = SimpleNamespace(run=review)
    import threading
    mgr.stages[0].runtime_lock = threading.RLock()
    before = copy.deepcopy(doc["scheme"])
    original_files = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    if fail_preview:
        from auto_battery_research.checkers.final_report_checker import FinalReportChecker
        monkeypatch.setattr(FinalReportChecker, "do_check", lambda self: (False, {"error": "模拟终审拒绝"}))
    result = repair_research_records(mgr, client=client, reviewer=reviewer)
    if fail_preview:
        assert not result["success"]
        assert all((tmp_path / name).read_bytes() == content for name, content in original_files.items())
        return
    assert result["success"], result
    saved = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    assert saved["scheme"] == before and saved["ledger_view"] == doc["ledger_view"]
    assert "### 2.2 候选比较与取舍" in (tmp_path / "final_research_report.md").read_text(encoding="utf-8")
