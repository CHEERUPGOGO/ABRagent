"""研究提取契约、逐字段来源和缓存恢复的离线回归。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_research_support import sample, verdict
from auto_battery_research.tests.test_partial_recovery import two_answer_doc
from auto_battery_research.util.research_contract import COLLECTION_INSTRUCTIONS
from auto_battery_research.util.research_records import collect_research, validate_record
from auto_battery_research.util.research_support import (
    apply_review, is_summary, normalize_extracted_research, render_field, supported, refresh_for_review,
)
from auto_battery_research.util.scheme_ledger import collect_parameters, restore_research_cache


def test_new_extraction_schema_is_unambiguous_and_field_scoped():
    assert "response, source_quotes[], field_support{}" in COLLECTION_INSTRUCTIONS
    doc, row = sample()
    doc.pop("research")
    received = []
    def chat(system, user, **kw):
        received.append((system, json.loads(user)))
        return json.dumps({"parameters": {}, "research": {"answers": [row]}})
    collect_parameters(doc, SimpleNamespace(available=True, chat=chat))
    assert len(received) == 1
    assert '"parameters":{},"candidates":[],"research":' in received[0][0]
    assert doc["research"]["answers"][0]["status"] == "answered"
    assert not doc["research"]["record_errors"]
    assert not supported(doc["research"]["answers"][0], "response", doc, doc["ledger_view"]["parameters"])


def test_single_source_extraction_reason_is_pending_summary_not_false_gap():
    doc, row = sample()
    quote = doc["final_answer"].split("\n\n")[2]
    legacy = dict(row, response=quote, reason="缺少本批实证，不能认定协同已成立。",
                  next_step="使用未改性样品开展同条件比较。", source_quote=quote,
                  citations=["p2"], parameter_refs=[])
    legacy.pop("source_quotes"); legacy.pop("field_support")
    # 磁盘历史的单句契约保持严格；新模型返回同样结构时转换为需要Reviewer核验的摘要。
    with pytest.raises(ValueError, match="reason"):
        validate_record(legacy, "answers", doc, doc["ledger_view"]["parameters"])
    raw = normalize_extracted_research({"answers": [legacy]})
    result = collect_research(raw, doc, doc["ledger_view"]["parameters"])
    accepted = result["answers"][0]
    assert accepted["status"] == "answered" and not result["record_errors"]
    assert is_summary(accepted) and "support_review" not in accepted
    doc["research"] = result
    assert "缺少本批实证" not in render_field(accepted, "reason", doc)
    assert "归档原文" in render_field(accepted, "reason", doc)


def test_summary_parameter_support_can_span_separate_quotes():
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    # 引用值分别出现在两段依据中，不能要求任一整段含有另一段内容。
    params["cathode.nominal_formula"] = {"value": "LiMnO2", "unit": "", "quantity_kind": "text"}
    doc["final_answer"] += "\n名义式采用LiMnO2。"
    row["source_quotes"].append("名义式采用LiMnO2。")
    row["response"] = "采用{{cathode.material}}，名义式为{{cathode.nominal_formula}}。"
    row["parameter_refs"].append("cathode.nominal_formula")
    row["field_support"]["response"] = [0, 3]
    validate_record(row, "answers", doc, params)
    row["field_support"]["response"] = [0]
    with pytest.raises(ValueError, match="该字段"):
        validate_record(row, "answers", doc, params)


def test_generated_gap_does_not_overwrite_actual_rejection_on_revalidation():
    doc, row = sample()
    row["response"] = "本批已经达到99%保持率"
    first = collect_research({"answers": [row]}, doc, doc["ledger_view"]["parameters"])
    reason = first["record_errors"][0]["reason"]
    refreshed = collect_research({}, doc, doc["ledger_view"]["parameters"], previous=first)
    assert refreshed["record_errors"][0]["reason"] == reason
    assert refreshed["record_errors"][0]["record"]["response"] == row["response"]
    assert "没有的数值" in refreshed["answers"][0]["reason"]


def test_duplicate_submission_cannot_be_accepted_by_error_record_replay():
    doc, row = sample()
    result = collect_research({"answers": [row, copy.deepcopy(row)]}, doc, doc["ledger_view"]["parameters"])
    assert result["answers"][0]["status"] == "gap"
    assert result["record_errors"][0]["reason"] == "研究记录身份重复"


def test_reviewer_refresh_keeps_original_failed_record_after_text_change():
    doc, row = sample(); params = doc["ledger_view"]["parameters"]
    row["response"] = "本批已经达到99%保持率"
    doc["research"] = collect_research({"answers": [row]}, doc, params)
    original = copy.deepcopy(doc["research"]["record_errors"][0])
    pipeline = SimpleNamespace(writer=SimpleNamespace(llm=None), reviewer=SimpleNamespace())
    seed = {"research_draft": doc, "maintenance_calls": 2}
    refreshed = refresh_for_review(pipeline, seed, doc["final_answer"] + "\n无关段落修改。", params, doc["evidence"])
    assert refreshed["research"]["record_errors"][0] == original
    assert seed["maintenance_calls"] == 2


def cache_fixture():
    doc = two_answer_doc()
    params = doc["ledger_view"]["parameters"]
    doc["research"] = collect_research(doc["research"], doc, params)
    doc["maintenance_calls"] = 2
    doc["scheme_parameters"] = {"process.route": {"value": "湿化学", "unit": "", "basis": "engineering_proposal",
        "source": "设计", "source_quote": "采用配方A"}}
    doc["parameter_diagnostics"] = [[{"path": "old_field", "reason": "真实历史错误"}]]
    return doc


def test_changed_document_cache_keeps_valid_records_and_does_not_spend_budget():
    cached = cache_fixture()
    doc = copy.deepcopy(cached)
    doc.pop("research")
    doc["final_answer"] += "\n补充其他段落，不修改已绑定来源。"
    assert restore_research_cache(doc, cached)
    assert all(a["status"] == "answered" for a in doc["research"]["answers"])
    assert doc["research"]["candidates"] and doc["research"]["arguments"]
    assert doc["parameter_diagnostics"] == cached["parameter_diagnostics"]
    client = SimpleNamespace(available=True, chat=lambda *a, **k: pytest.fail("预算耗尽不可请求模型"))
    collect_parameters(doc, client, max_attempts=0)
    assert doc["maintenance_calls"] == 0  # 本次调用消费为0，适配器另行加已有两次预算。
    assert all(a["status"] == "answered" for a in doc["research"]["answers"])


def test_cache_drops_only_removed_sources_and_keeps_real_error_and_excerpt():
    cached = cache_fixture()
    doc = copy.deepcopy(cached)
    quote = doc["research"]["answers"][1]["source_quote"]
    doc["final_answer"] = doc["final_answer"].replace(quote, "该段已修订，仍需比较。")
    restore_research_cache(doc, cached)
    assert doc["research"]["answers"][0]["status"] == "answered"
    assert doc["research"]["answers"][1]["status"] == "gap"
    assert doc["research"]["record_errors"][0]["record"]["source_quote"] == quote
    assert doc["research"]["candidates"] and doc["research"]["arguments"]


def test_cache_rejects_changed_material_and_locked_parameter():
    cached = cache_fixture()
    cached["research"]["answers"][0]["parameter_refs"] = ["cathode.material"]
    doc = copy.deepcopy(cached)
    doc["ledger_view"]["parameters"]["cathode.material"]["value"] = "配方B"
    doc["ledger_view"]["parameters"]["process.route"] = {"value": "固相", "locked": True}
    restore_research_cache(doc, cached)
    assert doc["research"]["answers"][0]["status"] == "gap"
    assert doc["research"]["answers"][1]["status"] == "answered"
    assert "process.route" not in doc["scheme_parameters"]
    assert "锁定" in doc["parameter_diagnostics"][-1][0]["reason"]


def test_cache_rechecks_legacy_rejected_record_without_claiming_review_passed():
    doc, row = sample()
    legacy = dict(row, source_quote=row["source_quotes"][2], response=row["source_quotes"][2],
                  parameter_refs=[], citations=["p2"])
    legacy.pop("source_quotes"); legacy.pop("field_support")
    cached = copy.deepcopy(doc)
    cached["research"] = collect_research({"answers": [legacy]}, doc, doc["ledger_view"]["parameters"])
    assert cached["research"]["answers"][0]["status"] == "gap"
    cached["maintenance_calls"] = 2
    restore_research_cache(doc, cached)
    accepted = doc["research"]["answers"][0]
    assert accepted["status"] == "answered" and is_summary(accepted)
    assert not supported(accepted, "reason", doc, doc["ledger_view"]["parameters"])


def test_cache_recovers_original_record_from_audit_when_old_refresh_lost_it():
    doc, row = sample()
    legacy = dict(row, source_quote=row["source_quotes"][2], response=row["source_quotes"][2],
                  parameter_refs=[], citations=["p2"])
    legacy.pop("source_quotes"); legacy.pop("field_support")
    failed = collect_research({"answers": [legacy]}, doc, doc["ledger_view"]["parameters"])
    cached = copy.deepcopy(doc)
    cached["research"] = copy.deepcopy(failed)
    cached["research"]["record_errors"][0].update(record=failed["answers"][0], reason="研究记录缺少已审核正文中的source_quote")
    cached["parameter_diagnostics"] = [[{"path": "$research", "reason": "研究记录部分验收失败",
        "record_errors": failed["record_errors"]}]]
    cached["maintenance_calls"] = 2
    restore_research_cache(doc, cached)
    assert doc["research"]["answers"][0]["status"] == "answered"
    assert is_summary(doc["research"]["answers"][0])
    assert doc["parameter_diagnostics"] == cached["parameter_diagnostics"]
    assert doc["maintenance_calls"] == 2


def test_current_declared_parameter_is_not_overwritten_by_stale_cache():
    cached = cache_fixture()
    doc = copy.deepcopy(cached)
    doc["scheme_parameters"]["process.route"]["value"] = "新湿化学路线"
    restore_research_cache(doc, cached)
    assert doc["scheme_parameters"]["process.route"]["value"] == "新湿化学路线"
    assert "已声明" in doc["parameter_diagnostics"][-1][0]["reason"]


def test_old_rejection_audit_does_not_replace_later_valid_answer():
    cached = cache_fixture()
    old = copy.deepcopy(cached["research"]["answers"][0])
    old["response"] = "99%的虚构性能"
    cached["parameter_diagnostics"].append([{"path": "$research", "record_errors": [
        {"collection": "answers", "id": old["requirement_id"], "reason": "旧错误已修复", "record": old}]}])
    doc = copy.deepcopy(cached)
    restore_research_cache(doc, cached)
    assert all(a["status"] == "answered" for a in doc["research"]["answers"])
    assert not doc["research"]["record_errors"]


def test_zero_budget_preserves_specific_errors_and_adds_no_model_calls():
    doc, row = sample()
    row["response"] = "容量达到99%"
    doc["research"] = collect_research({"answers": [row]}, doc, doc["ledger_view"]["parameters"])
    original_error = copy.deepcopy(doc["research"]["record_errors"][0])
    collect_parameters(doc, SimpleNamespace(available=True, chat=lambda *a, **k: pytest.fail("不能增加调用")), max_attempts=0)
    assert doc["research"]["record_errors"][0] == original_error
    assert "没有的数值" in doc["research"]["answers"][0]["reason"]
    assert doc["maintenance_calls"] == 0


def test_cache_invalidates_summary_review_after_unrelated_text_change():
    doc, row = sample(); params = doc["ledger_view"]["parameters"]
    doc["research"] = collect_research({"answers": [row]}, doc, params)
    apply_review(doc["research"], [verdict("answers", doc["research"]["answers"][0], doc)], doc, params, doc["evidence"])
    cached = copy.deepcopy(doc)
    doc["final_answer"] += "\n新的适用条件。"
    restore_research_cache(doc, cached)
    assert doc["research"]["answers"][0]["status"] == "answered"
    assert not supported(doc["research"]["answers"][0], "response", doc, params)


def test_adapter_restores_changed_text_cache_after_two_maintenance_calls(tmp_path, monkeypatch):
    from auto_battery_research.tests.test_stage4_golden import (
        _install_scripted_llm, _build_seeded_pipeline, _run_adapter,
        GOLDEN_GOAL, GOLDEN_WRITER_DRAFT,
    )
    from auto_battery_research.util.research_reporting import render_summary, render_review_appendix
    _install_scripted_llm(monkeypatch, GOLDEN_WRITER_DRAFT)
    pipeline = _build_seeded_pipeline(tmp_path)
    initial_task = tmp_path / "initial_task"
    assert _run_adapter(pipeline, initial_task)["success"]
    first = json.loads((initial_task / "rag_result.json").read_text(encoding="utf-8"))
    cache = first["research_draft"]
    cache["maintenance_calls"] = 2
    # 模拟修复或追加章节后的正文，禁止再次请求提取模型。
    first["final_answer"] = "额外说明：本次材料设计仍需实验验证。\n\n" + first["final_answer"]
    cached_answer = cache["research"]["answers"][0]["response"]
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: first)
    monkeypatch.setattr(pipeline.writer_llm, "chat", lambda *a, **k: pytest.fail("缓存恢复不应追加模型调用"))
    task = tmp_path / "restored_task"
    result = _run_adapter(pipeline, task)
    assert result["success"], result
    saved = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    assert saved["maintenance_calls"] == 2
    assert saved["research"]["answers"][0]["status"] == "answered"
    assert saved["research"]["answers"][0]["response"] == cached_answer
    # 独立增补段落没有改动该回答依赖的来源，逐字段审核继续有效。
    assert supported(saved["research"]["answers"][0], "response", saved, saved["ledger_view"]["parameters"])
    assert "归档原文" not in render_summary(saved)
    assert "结构化研究回答尚未生成" not in render_review_appendix(saved)


def test_missing_research_response_is_reported_and_valid_cache_is_preserved():
    doc, row = sample()
    doc["research"] = collect_research({"answers": [row]}, doc, doc["ledger_view"]["parameters"])
    calls = []
    def chat(*a, **kw):
        calls.append(1)
        return '{"parameters":{}}'
    collect_parameters(doc, SimpleNamespace(available=True, chat=chat))
    assert len(calls) == 2
    assert doc["research"]["answers"][0]["status"] == "answered"
    assert any("缺少有效research" in e["reason"] for attempt in doc["parameter_diagnostics"] for e in attempt)


@pytest.mark.parametrize("change", [
    lambda r: r.update(response="达到99%保持率"),
    lambda r: r.update(citations=["unknown"]),
    lambda r: r.update(parameter_refs=["unknown.parameter"]),
    lambda r: r.update(source_quote="伪造来源"),
])
def test_compatibility_cannot_bypass_facts_sources_or_parameter_checks(change):
    doc, row = sample()
    legacy = dict(row, source_quote=row["source_quotes"][2], response=row["source_quotes"][2],
                  reason="仍需验证。", next_step="开展同条件比较。", parameter_refs=[], citations=["p2"])
    legacy.pop("source_quotes"); legacy.pop("field_support")
    change(legacy)
    result = collect_research(normalize_extracted_research({"answers": [legacy]}), doc, doc["ledger_view"]["parameters"])
    assert result["answers"][0]["status"] == "gap" and result["record_errors"]
def test_research_only_first_request_exposes_current_failures_and_keeps_strict_source_checks():
    from auto_battery_research.util.scheme_ledger import collect_parameters
    doc, _ = sample()
    doc["research_only"] = True
    doc["research"] = collect_research({}, doc, doc["ledger_view"]["parameters"])
    captured = []
    def chat(system, user, **kwargs):
        captured.append(json.loads(user))
        return json.dumps({"research": {"answers": [{
            "requirement_id": doc["requirements"]["items"][0]["id"],
            "status": "answered", "response": "没有依据的新结论", "verification": "unverified",
            "source_quotes": ["当前正文不存在的支持句"], "field_support": {"response": [0]},
            "citations": [], "parameter_refs": []}]}})
    before = copy.deepcopy(doc["ledger_view"])
    collect_parameters(doc, SimpleNamespace(available=True, chat=chat), max_attempts=1)
    assert captured[0]["record_errors"] and captured[0]["accepted_research"]
    assert doc["research"]["answers"][0]["status"] == "gap"
    assert doc["research"]["record_errors"] and doc["ledger_view"] == before

