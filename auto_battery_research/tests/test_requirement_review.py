"""用户误读证据与科学审核边界：错误分类不得获得固定字段阻断权限。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_research_contract import research_doc, requirement
from auto_battery_research.util.research_contract import normalize_requirements, finalize_research
from auto_battery_research.util.requirement_review import normalize_review, blocking_fields


def bound_doc():
    goal = "正极采用Li1.2Ni0.2Mn0.6O2。"
    req = normalize_requirements({"items": [requirement("正极采用Li1.2Ni0.2Mn0.6O2", "constraint",
        binding={"path": "cathode.material", "value": "Li1.2Ni0.2Mn0.6O2", "unit": ""})]}, goal)
    return {"target": goal, "requirements": req,
            "final_answer": "正极采用Li1.2Ni0.2Mn0.6O2；由Li2MnO3与LiNi0.5Mn0.5O2复合表示，机制尚待验证。",
            "parameters": {"cathode.material": {"value": "LRMO", "unit": "", "original_value": "Li1.2Ni0.2Mn0.6O2"}},
            "scheme": {"cathode": "LRMO"}}


def verdict(doc, evidence=None):
    return {"issues": ["科学解释需要核验"], "requirement_review": {
        "source_checked": True, "missing_quotes": [], "unanswered_ids": [],
        "misread_ids": [doc["requirements"]["items"][0]["id"]], "misread_evidence": evidence or []}}


def proof(doc, **changes):
    return dict(requirement_id=doc["requirements"]["items"][0]["id"], scope="user_binding",
                user_quote="正极采用Li1.2Ni0.2Mn0.6O2", observed_quote=doc["final_answer"],
                path="cathode.material", expected_value="Li1.2Ni0.2Mn0.6O2", expected_unit="",
                actual_value=doc["parameters"]["cathode.material"]["value"], actual_unit="",
                reason="待核查基体解释", **changes)


def test_unsubstantiated_misread_is_pending_not_a_field_block():
    doc = bound_doc(); result = verdict(doc)
    review = normalize_review(result, doc)
    assert review["misread_ids"] == [] and review["blocked_fields"] == []
    assert review["reported_misread_ids"] and review["misread_diagnostics"]
    assert result["issues"][0] == "科学解释需要核验"
    doc["reviewer_output"] = result
    assert blocking_fields(doc) == []


def test_correct_binding_with_disputed_mechanism_cannot_block():
    doc = bound_doc(); result = verdict(doc, [proof(doc)])
    review = normalize_review(result, doc)
    assert review["blocked_fields"] == [] and review["misread_ids"] == []
    assert "一致" in review["misread_diagnostics"][0]["reason"]
    # 改错expected_value或scope也不能把科学争议变成用户字段误读。
    bad = proof(doc); bad.update(expected_value="LiNiO2")
    assert not normalize_review(verdict(doc, [bad]), doc)["blocked_fields"]


def test_actual_wrong_material_has_only_its_own_field_blocked():
    doc = bound_doc()
    doc["final_answer"] = "正极采用LiNiO2；电解液沿用既定条件。"
    doc["parameters"]["cathode.material"] = {"value": "LiNiO2", "unit": ""}
    doc["scheme"]["cathode"] = "LiNiO2"
    review = normalize_review(verdict(doc, [proof(doc)]), doc)
    assert review["blocked_fields"] == ["cathode.material"]
    assert review["misread_ids"] == [doc["requirements"]["items"][0]["id"]]


def test_same_material_family_does_not_hide_formula_change():
    doc = bound_doc()
    doc["parameters"]["cathode.material"] = {"value": "LRMO", "unit": "", "original_value": "Li1.2Ni0.3Mn0.5O2"}
    doc["final_answer"] = "正极采用Li1.2Ni0.3Mn0.5O2。"
    assert normalize_review(verdict(doc, [proof(doc)]), doc)["blocked_fields"] == ["cathode.material"]


def test_reordered_formula_is_same_binding_not_misread():
    doc = bound_doc()
    doc["parameters"]["cathode.material"]["original_value"] = "Li1.2Mn0.6Ni0.2O2"
    doc["final_answer"] = "正极采用Li1.2Mn0.6Ni0.2O2，作用机制待验证。"
    assert not normalize_review(verdict(doc, [proof(doc)]), doc)["blocked_fields"]


@pytest.mark.parametrize("change", [
    {"user_quote": "正文不存在的用户要求"}, {"observed_quote": "不存在的错误解释"},
    {"actual_value": "LiCoO2"}, {"actual_unit": "V"}, {"path": "electrolyte.material"},
    {"scope": "scientific_explanation"}, {"expected_unit": "kg"},
])
def test_bad_evidence_is_not_trusted(change):
    doc = bound_doc(); doc["final_answer"] = "正极采用LiNiO2。"
    doc["parameters"]["cathode.material"] = {"value": "LiNiO2", "unit": ""}
    doc["scheme"]["cathode"] = "LiNiO2"
    record = proof(doc); record.update(change)
    result = normalize_review(verdict(doc, [record]), doc)
    assert not result["blocked_fields"] and result["misread_diagnostics"]


def test_repeated_or_stale_evidence_loses_blocking_permission():
    doc = bound_doc(); doc["final_answer"] = "正极采用LiNiO2。"
    doc["parameters"]["cathode.material"] = {"value": "LiNiO2", "unit": ""}
    doc["scheme"]["cathode"] = "LiNiO2"
    record = proof(doc)
    assert not normalize_review(verdict(doc, [record, record]), doc)["blocked_fields"]
    result = verdict(doc, [record]); normalize_review(result, doc)
    doc["reviewer_output"] = result
    doc["final_answer"] = "正极采用Li1.2Ni0.2Mn0.6O2。"
    doc["parameters"]["cathode.material"]["value"] = "Li1.2Ni0.2Mn0.6O2"
    doc["scheme"]["cathode"] = "Li1.2Ni0.2Mn0.6O2"
    assert blocking_fields(doc) == []


def test_interpretation_misread_has_no_parameter_permission():
    doc = research_doc(); record = {"requirement_id": doc["requirements"]["items"][0]["id"],
        "scope": "requirement_interpretation", "user_quote": doc["target"], "observed_quote": doc["final_answer"],
        "reason": "用户要求比较，回答把任务误当成已定案"}
    review = normalize_review(verdict(doc, [record]), doc)
    assert review["misread_ids"] and not review["blocked_fields"]


def test_false_misread_does_not_erase_answer_at_finalize():
    doc = research_doc(); doc["reviewer_output"] = verdict(doc)
    original = copy.deepcopy(doc["research"]["answers"][0])
    finalize_research(doc)
    assert doc["research"]["answers"][0] == original
    assert doc["research"]["coverage_reviewed"] is False


def test_reviewer_boundary_prompt_and_protocol_validation():
    from auto_battery_research.rag import ReviewerAgent
    doc = bound_doc(); received = []
    def chat(system, user, **kw):
        received.append((system, user))
        return json.dumps(dict(verdict(doc), confidence="medium", error_type="writing"))
    reviewer = ReviewerAgent(SimpleNamespace(available=True, chat=chat))
    reviewer.requirements = doc["requirements"]; reviewer.research_draft = doc
    result = reviewer.run(question=doc["target"], draft_answer=doc["final_answer"],
        evidence=[{"passage_id": "p1", "source": "离线材料证据", "text": "结构解释仍需验证"}], scheme=doc["scheme"])
    assert "科学审核分开" in received[0][1] and "misread_evidence" in received[0][1]
    assert not result["requirement_review"]["blocked_fields"]
    assert result["confidence"] == "low"


def test_numeric_binding_is_not_confused_with_scientific_formula_numbers():
    goal = "充电上限4.8 V"
    req = normalize_requirements({"items": [requirement(goal, "constraint",
        binding={"path": "condition.v_max", "value": 4.8, "unit": "V"})]}, goal)
    doc = {"target": goal, "requirements": req, "final_answer": "充电上限采用4.6 V。",
           "scheme": {}, "parameters": {"condition.v_max": {"value": 4.6, "unit": "V"}}}
    record = {"requirement_id": req["items"][0]["id"], "scope": "user_binding", "user_quote": goal,
        "observed_quote": doc["final_answer"], "reason": "把用户4.8 V误写成4.6 V", "path": "condition.v_max",
        "expected_value": 4.8, "expected_unit": "V", "actual_value": 4.6, "actual_unit": "V"}
    assert normalize_review(verdict(doc, [record]), doc)["blocked_fields"] == ["condition.v_max"]
    record["actual_value"] = 4
    assert not normalize_review(verdict(doc, [record]), doc)["blocked_fields"]


def test_commit_revalidates_evidence_and_does_not_trust_blocked_fields(tmp_path):
    from auto_battery_research.util.scheme_ledger import commit_design, load, ensure
    ensure(tmp_path, "离线误读审核测试")
    doc = bound_doc(); doc["final_answer"] = "正极采用LiNiO2。"
    doc["parameters"]["cathode.material"] = {"value": "LiNiO2", "unit": ""}
    doc["scheme"]["cathode"] = "LiNiO2"
    doc["reviewer_output"] = verdict(doc, [proof(doc)])
    with pytest.raises(ValueError, match="cathode.material"):
        commit_design(tmp_path, doc)
    assert "cathode.material" not in load(tmp_path)["parameters"]
    doc["reviewer_output"] = verdict(doc)
    doc["reviewer_output"]["requirement_review"]["blocked_fields"] = ["cathode.material"]
    doc["scheme"]["cathode"] = "LRMO"
    commit_design(tmp_path, doc)
    assert load(tmp_path)["parameters"]["cathode.material"]["value"] == "LRMO"


def test_diagnostic_restore_preserves_unaffected_research_and_original_text():
    from auto_battery_research.tests.test_partial_recovery import two_answer_doc
    from auto_battery_research.util.report_readiness import restore_diagnostic
    from auto_battery_research.util.research_records import collect_research
    doc = two_answer_doc()
    params = doc["ledger_view"]["parameters"]
    doc["research"] = collect_research(doc["research"], doc, params)
    doc["report_readiness"] = {"category": "CONDITIONAL", "issues": []}
    ledger = dict(schema_version=1, revision=0, parameters=params, candidates=[], history=[], derived={}, issues=[])
    original_text = doc["final_answer"]
    restore_diagnostic(doc, ledger, "cathode.material误读证据待修复")
    assert original_text in doc["final_answer"]
    assert all(a["status"] == "answered" for a in doc["research"]["answers"])
    assert doc["research"]["candidates"] and doc["research"]["arguments"]
    assert doc["rejected_designs"][0]["reason"] == "cathode.material误读证据待修复"


def test_stage4_adapter_retains_answers_for_scientific_misclassification(tmp_path, monkeypatch):
    from auto_battery_research.tests.test_stage4_golden import (
        _install_scripted_llm, _build_seeded_pipeline, _run_adapter, GOLDEN_WRITER_DRAFT,
    )
    _install_scripted_llm(monkeypatch, GOLDEN_WRITER_DRAFT)
    pipeline = _build_seeded_pipeline(tmp_path)
    initial = tmp_path / "initial"
    assert _run_adapter(pipeline, initial)["success"]
    raw = json.loads((initial / "rag_result.json").read_text(encoding="utf-8"))
    raw["research_draft"]["maintenance_calls"] = 2
    req = raw["plan"]["requirements"]
    # 本例为无binding的研究目标：伪误读也不应清空已经生成的回答。
    raw["reviewer_output"]["requirement_review"]["misread_ids"] = [req["items"][0]["id"]]
    raw["reviewer_output"]["issues"].append("研究解释有争议，但实际用户要求已经回答")
    monkeypatch.setattr(pipeline, "run", lambda *a, **kw: raw)
    monkeypatch.setattr(pipeline.writer_llm, "chat", lambda *a, **kw: pytest.fail("不得追加模型请求"))
    output = tmp_path / "replayed"
    assert _run_adapter(pipeline, output)["success"]
    saved = json.loads((output / "design_scheme.json").read_text(encoding="utf-8"))
    assert saved["research"]["answers"][0]["status"] == "answered"
    assert saved["reviewer_output"]["requirement_review"]["misread_ids"] == []
    assert "研究解释有争议" in saved["reviewer_output"]["issues"][0]
    assert saved["review_status"] == "REVIEWED_WITH_ISSUES"


def test_fixed_binding_scientific_dispute_survives_adapter(tmp_path, monkeypatch):
    from auto_battery_research.tests.test_stage4_golden import (
        _install_scripted_llm, _build_seeded_pipeline, _run_adapter, GOLDEN_GOAL, GOLDEN_WRITER_DRAFT,
    )
    _install_scripted_llm(monkeypatch, GOLDEN_WRITER_DRAFT)
    pipeline = _build_seeded_pipeline(tmp_path)
    initial = tmp_path / "initial"
    assert _run_adapter(pipeline, initial)["success"]
    raw = json.loads((initial / "rag_result.json").read_text(encoding="utf-8"))
    req = raw["plan"]["requirements"]
    item = req["items"][0]
    quote = "LiFSI"  # 脚本化既有正文的电解液盐，绑定到同一个当前值。
    goal = GOLDEN_GOAL + "，采用LiFSI"
    req["source"] = goal
    item.update(quote=quote, start=goal.index(quote), end=goal.index(quote)+len(quote),
                kind="constraint", binding={"path": "electrolyte.salt", "value": "LiFSI", "unit": ""})
    cache = raw["research_draft"]
    cache["requirements"] = copy.deepcopy(req)
    cache["maintenance_calls"] = 2
    key = item["id"]
    rv = raw["reviewer_output"]
    rv["requirement_review"] = {"source_checked": True, "missing_quotes": [], "unanswered_ids": [], "misread_ids": [key],
        "misread_evidence": [{"requirement_id": key, "scope": "user_binding", "user_quote": quote,
            "observed_quote": "LiFSI", "path": "electrolyte.salt", "expected_value": "LiFSI", "expected_unit": "",
            "actual_value": "LiFSI", "actual_unit": "", "reason": "盐正确，但界面机理需要核验"}]}
    rv["issues"].append("盐正确，但界面机理需要核验")
    monkeypatch.setattr(pipeline, "run", lambda *a, **kw: raw)
    monkeypatch.setattr(pipeline.writer_llm, "chat", lambda *a, **kw: pytest.fail("不能追加请求"))
    task = tmp_path / "replayed"
    result = _run_adapter(pipeline, task, goal)
    assert result["success"], result
    saved = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    assert saved["ledger_view"]["parameters"]["electrolyte.salt"]["value"] == "LiFSI"
    assert saved["research"]["answers"][0]["status"] == "answered"
    assert not saved["reviewer_output"]["requirement_review"]["blocked_fields"]
