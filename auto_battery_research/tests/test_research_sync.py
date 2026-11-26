"""Stage 4最终正文、研究记录与Stage 6视图同步的离线回归。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_research_support import sample, verdict
from auto_battery_research.util.research_records import collect_research
from auto_battery_research.util.research_support import render_field, supported


def test_markdown_only_source_difference_binds_actual_text_and_still_needs_review():
    doc, row = sample()
    doc["final_answer"] = doc["final_answer"].replace("方案采用配方A。", "方案采用**配方A**。")
    params = doc["ledger_view"]["parameters"]
    research = collect_research({"answers": [row]}, doc, params)
    answer = research["answers"][0]
    assert answer["status"] == "answered"
    assert answer["source_quotes"][0] == "方案采用**配方A**。"
    for quote, span in zip(answer["source_quotes"], answer["source_locations"]):
        assert doc["final_answer"][span["start"]:span["end"]] == quote
    assert not supported(answer, "response", doc, params)


@pytest.mark.parametrize("changed", ["剂量**0.05**仍待验证。", "剂量**0.02**已被验证。"])
def test_source_format_matching_does_not_hide_changed_dose_or_qualification(changed):
    doc, row = sample()
    row.update(source_quotes=["剂量0.02仍待验证。"], field_support={"response": [0]},
               response="剂量0.02仍待验证。", reason="", next_step="", citations=[], parameter_refs=[])
    doc["final_answer"] = changed
    research = collect_research({"answers": [row]}, doc, doc["ledger_view"]["parameters"])
    assert research["answers"][0]["status"] == "gap"


def test_ambiguous_format_only_source_match_stays_pending():
    doc, row = sample()
    doc["final_answer"] = "方案采用**配方A**。\n方案采用**配方A**。\n" + doc["final_answer"].split("\n\n", 1)[1]
    research = collect_research({"answers": [row]}, doc, doc["ledger_view"]["parameters"])
    assert research["answers"][0]["status"] == "gap"


def test_reviewed_record_survives_adapter_cache_and_report_finalization():
    from auto_battery_research.util.research_support import apply_review
    from auto_battery_research.util.research_contract import finalize_research
    from auto_battery_research.util.scheme_ledger import restore_research_cache
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    doc["research"] = collect_research({"answers": [row]}, doc, params)
    apply_review(doc["research"], [verdict("answers", doc["research"]["answers"][0], doc)], doc, params, doc["evidence"])
    doc["reviewer_output"] = {"requirement_review": {"source_checked": True, "missing_quotes": [],
                            "misread_ids": [], "unanswered_ids": []}}
    cached = copy.deepcopy(doc)
    doc.pop("research")
    restore_research_cache(doc, cached)
    finalize_research(doc)
    answer = doc["research"]["answers"][0]
    assert supported(answer, "response", doc, params)
    assert "归档原文" not in render_field(answer, "response", doc)


def test_completed_review_archives_only_resolved_missing_review_diagnostic():
    from auto_battery_research.util.research_support import apply_review
    from auto_battery_research.util.research_contract import finalize_research
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    doc["research"] = collect_research({"answers": [row]}, doc, params)
    row = doc["research"]["answers"][0]
    missing = f"研究摘要待核验：answers[{row['requirement_id']}].reason：缺少匹配指纹的有效摘要支持性审核"
    scientific = f"研究摘要待核验：answers[{row['requirement_id']}].response：跨体系推断尚需实验验证"
    doc["reviewer_output"] = {"issues": [missing, scientific], "requirement_review": {
        "source_checked": True, "missing_quotes": [], "misread_ids": [], "unanswered_ids": []}}
    doc["report_readiness"] = {"category": "CONDITIONAL", "issues": [missing, scientific]}
    finalize_research(doc)
    assert doc["research"]["diagnostics"]
    apply_review(doc["research"], [verdict("answers", row, doc)], doc, params, doc["evidence"])
    finalize_research(doc)
    for key in ("reviewer_output", "report_readiness"):
        assert missing not in doc[key]["issues"]
        assert scientific in doc[key]["issues"]
    assert missing in doc["research_diagnostic_history"]
    assert not any(s.startswith("研究摘要支持性待核验：") for s in doc["research"]["diagnostics"])


def test_structured_citations_survive_json_array_brackets_and_context_limit():
    from auto_battery_research.util.adaptive_retrieval import evidence_package, select_context
    from auto_battery_research.util.research_support import cited_evidence_ids
    doc, row = sample()
    doc["research"] = collect_research({"answers": [row]}, doc, doc["ledger_view"]["parameters"])
    noise = [{"passage_id": f"unrelated{i}", "text": "无关证据"} for i in range(30)]
    doc["evidence"] = noise + doc["evidence"]
    context = json.dumps({"source_quotes": ["依据[p1]", "边界[p2]"]}, ensure_ascii=False)
    pack, _ = evidence_package(doc["evidence"], text=context, preferred_ids=cited_evidence_ids(doc), limit=2)
    assert {e["passage_id"] for e in pack} == {"p1", "p2"}
    seed = {"evidence": doc["evidence"], "research_draft": doc}
    pack = select_context(seed, purpose="final_review", text=context,
                          config={"context_passages": 2, "context_chars": 60000})
    assert {e["passage_id"] for e in pack} == {"p1", "p2"}


def test_revalidation_discards_rejected_retry_but_keeps_accepted_summary_and_review():
    from auto_battery_research.util.research_support import apply_review
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    accepted = collect_research({"answers": [row]}, doc, params)
    apply_review(accepted, [verdict("answers", accepted["answers"][0], doc)], doc, params, doc["evidence"])
    changed = dict(row, response="采用配方A开展另一项研究。", parameter_refs=[])
    attempted = collect_research({"answers": [changed]}, doc, params, previous=accepted)
    assert attempted["record_errors"] and attempted["answers"] == accepted["answers"]
    revalidated = collect_research({}, doc, params, previous=attempted)
    assert not revalidated["record_errors"]
    assert revalidated["answers"] == accepted["answers"]
    assert supported(revalidated["answers"][0], "response", doc, params)


def _pipeline(monkeypatch, tmp_path, *, omit_fields=False):
    import auto_battery_research.util.adaptive_retrieval as retrieval
    import auto_battery_research.util.design_sections as sections
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    doc["research"] = collect_research({"answers": [row]}, doc, params)
    calls = []
    reviewer = SimpleNamespace()
    def run(**kwargs):
        calls.append(kwargs)
        current = reviewer.research_draft
        answer = current["research"]["answers"][0]
        v = verdict("answers", answer, current)
        if omit_fields and len(calls) == 1:
            v["fields"] = {"response": {"status": "unsupported", "reason": "仍需科学复核"}}
        return {"issues": [], "confidence": "medium", "research_support_review": [v],
                "requirement_review": {"source_checked": True, "missing_quotes": [],
                                       "misread_ids": [], "unanswered_ids": []}}
    reviewer.run = run
    pipeline = SimpleNamespace(reviewer=reviewer, writer=SimpleNamespace(llm=None),
        stage4_repair_options={"content_rounds": 3},
        _extract_energy_claim=lambda text: None, repair_checkpoint_path=tmp_path / "checkpoint.json")
    monkeypatch.setattr(sections, "design_output_issues", lambda *a: [])
    monkeypatch.setattr(retrieval, "select_context", lambda seed, **kw: seed["evidence"])
    seed = {"draft": doc["final_answer"] + "\n新增适用边界，待实验验证。", "evidence": doc["evidence"],
            "parameters": params, "scheme": doc["scheme"], "plan": {"requirements": doc["requirements"]},
            "research_draft": doc, "maintenance_calls": 2, "repair_rounds": 3, "review_rounds": 3,
            "repair_audit": [{"status": "APPLIED"}, {"status": "REJECTED"}], "protocol_replayed": True}
    return pipeline, seed, calls


def test_successful_patch_then_rejected_patch_still_reviews_final_research(monkeypatch, tmp_path):
    from auto_battery_research.util.design_repair import review_design
    pipeline, seed, calls = _pipeline(monkeypatch, tmp_path)
    review_design(pipeline, seed, "目标")
    assert len(calls) == 1
    doc = seed["research_draft"]
    assert doc["final_answer"] == seed["draft"]
    assert supported(doc["research"]["answers"][0], "response", doc, seed["parameters"])
    restored = json.loads(pipeline.repair_checkpoint_path.read_text(encoding="utf-8"))
    review_design(pipeline, restored, "目标")
    assert len(calls) == 1  # 恢复已经复核的最终版本，不重复消耗预算。


def test_missing_final_review_fields_get_one_bounded_supplement(monkeypatch, tmp_path):
    from auto_battery_research.util.design_repair import review_design
    pipeline, seed, calls = _pipeline(monkeypatch, tmp_path, omit_fields=True)
    review_design(pipeline, seed, "目标")
    assert len(calls) == 2
    doc = seed["research_draft"]
    answer = doc["research"]["answers"][0]
    assert not supported(answer, "response", doc, seed["parameters"])
    assert supported(answer, "reason", doc, seed["parameters"])
    assert supported(answer, "next_step", doc, seed["parameters"])
    restored = json.loads(pipeline.repair_checkpoint_path.read_text(encoding="utf-8"))
    review_design(pipeline, restored, "目标")
    assert len(calls) == 2


def test_final_research_review_respects_exhausted_review_budget(monkeypatch, tmp_path):
    from auto_battery_research.util.design_repair import review_design
    pipeline, seed, calls = _pipeline(monkeypatch, tmp_path)
    seed["review_rounds"] = 6
    review_design(pipeline, seed, "目标")
    assert calls == []
    doc = seed["research_draft"]
    assert doc["final_answer"] == seed["draft"]
    assert not supported(doc["research"]["answers"][0], "response", doc, seed["parameters"])
