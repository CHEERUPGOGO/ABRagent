"""研究来源接口保持科研文本与状态，只修精确记录的来源并独立复核。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_research_support import sample, verdict
from auto_battery_research.util.design_repair import authorize, repair
from auto_battery_research.util.repair_interfaces import build_interfaces, execute
from auto_battery_research.util.research_records import collect_research
from auto_battery_research.util.research_support import apply_review, supported
from auto_battery_research.util.research_source_metadata import route_issues, prepare, permissions


def stale_case():
    doc, row = sample()
    original = copy.deepcopy(row)
    row["source_quotes"][1] = "旧版本的结构论证已经被改写 [p1]。"
    params = doc["ledger_view"]["parameters"]
    doc["research"] = collect_research({"answers": [row]}, doc, params)
    assert doc["research"]["record_errors"]
    return doc, params, original


class SourceClient:
    def __init__(self, fixed, *, approved=True, mutate=None, body=None):
        self.fixed, self.approved, self.mutate, self.body = fixed, approved, mutate, body
        self.calls = []

    def chat(self, system, user, **kw):
        data = json.loads(user)
        self.calls.append((kw["trace_tag"], data))
        if kw["trace_tag"] == "writer_patch":
            submissions = []
            for iid, item in data["interface_contract"]["interfaces"].items():
                if item.get("kind") == "research_source":
                    row = {"interface_id": iid, "source_quotes": self.fixed["source_quotes"], "field_support": self.fixed["field_support"]}
                    if self.mutate:
                        self.mutate(row)
                else:
                    row = {"interface_id": iid, "replacement": self.body or item["original"]}
                submissions.append(row)
            return json.dumps({"base_hash": data["permission"]["base_hash"], "submissions": submissions})
        return json.dumps({"verdicts": [{"group_id": gid, "issue_id": key, "resolved": self.approved, "reason": "逐字段核对新来源与原摘要"}
            for gid, item in data["transactions"].items() for key in item["issue_ids"]], "new_issues": []})


def test_source_only_issue_routes_to_record_instead_of_expanding_body_permission():
    doc, params, fixed = stale_case()
    key = fixed["requirement_id"]
    old = {"id": "i", "quote": "方案采用配方A。", "reason": f"answers[{key}] 的source_quote失效", "suggestion": "更新来源", "parameter_paths": []}
    routed = route_issues([old], doc, doc["final_answer"], params)
    assert len(routed) == 1 and routed[0]["source_only"]
    permit = authorize(doc["final_answer"], routed, params, doc)
    assert permit["grants"]["i"]["blocks"] == permit["grants"]["i"]["fields"] == []
    contract = build_interfaces(doc["final_answer"], params, routed, permit)
    assert len(contract["interfaces"]) == 1
    target = next(iter(contract["interfaces"].values()))
    assert target["kind"] == "research_source" and target["research_source"]["id"] == key


@pytest.mark.parametrize("approved", [True, False])
def test_source_metadata_repair_preserves_record_content_and_waits_for_scientific_review(approved):
    doc, params, fixed = stale_case()
    before = copy.deepcopy(doc)
    client = SourceClient(fixed, approved=approved)
    text, values, audit = repair(SimpleNamespace(llm=client), doc["final_answer"], {"issues": ["来源待修复"]}, doc["evidence"], params,
                                 verifier=client, research_doc=doc)
    assert text == doc["final_answer"] and values == params and doc == before
    assert [tag for tag, _ in client.calls] == ["writer_patch", "repair_verify"]
    if approved:
        current = audit["_research_draft"]
        row = current["research"]["answers"][0]
        assert audit["status"] == "APPLIED" and not current["research"]["record_errors"]
        for name in ("response", "reason", "next_step", "verification", "status", "parameter_refs", "citations"):
            assert row[name] == fixed[name]
        assert row["source_quotes"] == fixed["source_quotes"]
        assert not supported(row, "response", current, params)
        assert audit["research_source_updates"][0]["before"]["source_quotes"] != row["source_quotes"]
        checked = client.calls[-1][1]
        assert checked["combined_preview"]["research"]["answers"][0]["source_quotes"] == fixed["source_quotes"]
    else:
        assert audit["status"] == "REJECTED" and audit["research_source_updates"] == []


@pytest.mark.parametrize("attack", ["response", "status", "verification", "parameters", "support_review", "nonexistent", "unknown_cite", "bad_binding", "wrong_id"])
def test_metadata_interface_rejects_fabrication_and_scientific_or_identity_changes(attack):
    doc, params, fixed = stale_case()
    def mutate(row):
        if attack in ("response", "status", "verification", "parameters", "support_review"):
            row[attack] = "越权"
        elif attack == "nonexistent": row["source_quotes"] = ["正文不存在的来源"]
        elif attack == "unknown_cite": row["source_quotes"] = ["新文献[unknown]。"]
        elif attack == "bad_binding": row["field_support"] = {"response": [99]}
        else: row["interface_id"] = "another-record"
    client = SourceClient(fixed, mutate=mutate)
    text, values, audit = repair(SimpleNamespace(llm=client), doc["final_answer"], {"issues": ["来源待修复"]}, doc["evidence"], params,
                                 verifier=client, research_doc=doc)
    assert audit["status"] == "REJECTED" and text == doc["final_answer"] and values == params
    assert [tag for tag, _ in client.calls] == ["writer_patch"]


def test_old_record_hash_cannot_authorize_a_changed_summary():
    doc, params, fixed = stale_case()
    issue = route_issues([], doc, doc["final_answer"], params)[0]
    grants = permissions(issue, doc)
    token = next(iter(grants))
    doc["research"]["record_errors"][0]["record"]["response"] += "新主张"
    with pytest.raises(ValueError, match="过期"):
        prepare(doc, doc["final_answer"], doc["final_answer"], params,
                [{"permission": token, "source_quotes": fixed["source_quotes"], "field_support": fixed["field_support"]}], grants)


def test_source_repair_preserves_unrelated_invalid_record_status():
    doc, params, fixed = stale_case()
    doc["research"]["record_errors"][0]["record"]["verification"] = "experiment"
    client = SourceClient(fixed)
    _, _, audit = repair(SimpleNamespace(llm=client), doc["final_answer"], {"issues": ["来源待修复"]}, doc["evidence"], params,
                          verifier=client, research_doc=doc)
    assert audit["status"] == "APPLIED"
    current = audit["_research_draft"]["research"]
    assert current["answers"][0]["status"] == "gap"
    assert current["record_errors"][0]["record"]["verification"] == "experiment"
    assert current["record_errors"][0]["record"]["source_quotes"] == fixed["source_quotes"]
    assert "不得宣称" in current["record_errors"][0]["reason"]


def test_deferred_source_problem_is_not_resubmitted_with_independent_body_patch():
    doc, params, fixed = stale_case()
    key = fixed["requirement_id"]
    text = doc["final_answer"] + "\n\n独立问题原句。"
    doc["final_answer"] = text
    issue = {"quote": "独立问题原句。", "reason": "补充条件", "suggestion": "明确范围", "parameter_paths": []}
    client = SourceClient(fixed, body="独立问题仅限当前范围。")
    result, _, audit = repair(SimpleNamespace(llm=client), text,
        {"issues": ["补充条件"], "repair_issues": [issue], "deferred_research_sources": [("answers", key)]},
        doc["evidence"], params, verifier=client, research_doc=doc)
    assert audit["status"] == "APPLIED" and result.endswith("独立问题仅限当前范围。")
    assert all(i.get("kind") != "research_source" for i in audit["interfaces"]["interfaces"].values())
    assert audit["research_source_updates"] == []


def test_body_edit_updates_research_sources_but_invalidates_old_support():
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    doc["research"] = collect_research({"answers": [row]}, doc, params)
    accepted = doc["research"]["answers"][0]
    apply_review(doc["research"], [verdict("answers", accepted, doc)], doc, params, doc["evidence"])
    assert supported(accepted, "response", doc, params)
    new_text = doc["final_answer"].replace("该路线提高结构稳定性 [p1]。", "该路线有望提高结构稳定性，须验证适用性 [p1]。")
    result, updates = prepare(doc, doc["final_answer"], new_text, params)
    updated = result["research"]["answers"][0]
    assert updated["response"] == accepted["response"]
    assert updated["source_quotes"][1] in new_text and "有望" in updated["source_quotes"][1]
    assert not supported(updated, "response", result, params)
    assert updates[0]["before"]["support_review"] == accepted["support_review"]


def test_reviewer_research_scheme_uses_current_parameters_instead_of_old_component_cache():
    from auto_battery_research.util.research_support import refresh_for_review, review_payload
    from auto_battery_research.util.material_ids import assemble_components
    doc, params, _ = stale_case()
    original = copy.deepcopy(doc)
    params = {"cathode.nominal_formula": {"value": "Li1.18Na0.02Mn0.60Ni0.18Zn0.02O2", "unit": "",
              "basis": "engineering_proposal", "source": "已接受配方修订"}}
    doc["scheme"] = assemble_components({}, {"cathode.nominal_formula": dict(params["cathode.nominal_formula"], value="Li1.18Na0.02Mn0.58Ni0.20Zn0.02O2")})
    old_scheme = copy.deepcopy(doc["scheme"])
    doc["scheme_parameters"] = {"cathode.nominal_formula": dict(params["cathode.nominal_formula"], source_quote="旧来源")}
    params["cathode.nominal_formula"]["source_quote"] = "当前来源"
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(), writer=SimpleNamespace(llm=None))
    seed = {"research_draft": doc, "scheme": assemble_components(old_scheme, params), "maintenance_calls": 2}
    refreshed = refresh_for_review(pipeline, seed, doc["final_answer"], params, doc["evidence"], repair_records=False)
    assert refreshed["scheme"] == seed["scheme"]
    assert review_payload(refreshed)["scheme"]["component_identities"]["cathode"]["source_values"] == {
        "cathode.nominal_formula": params["cathode.nominal_formula"]["value"]}
    assert doc["scheme"] == old_scheme
    assert refreshed["scheme_parameters"]["cathode.nominal_formula"]["source_quote"] == "当前来源"
    candidate, _ = prepare(doc, doc["final_answer"], doc["final_answer"], params)
    assert candidate["scheme"] == seed["scheme"]
    assert candidate["scheme_parameters"] == refreshed["scheme_parameters"]


def test_body_and_source_patch_depending_on_new_sentence_form_one_transaction():
    doc, params, fixed = stale_case()
    original = fixed["source_quotes"][1]
    replacement = "该路线有望提高结构稳定性 [p1]。"
    fixed["source_quotes"][1] = replacement
    issue = {"id": "body", "quote": original, "reason": "降低结论强度", "suggestion": "保留条件", "parameter_paths": []}
    issues = route_issues([issue], doc, doc["final_answer"], params)
    permit = authorize(doc["final_answer"], issues, params, doc)
    client = SourceClient(fixed, body=replacement)
    text, _, audit = execute(SimpleNamespace(llm=client), client, doc["final_answer"], params, permit, issues, doc["evidence"], research_doc=doc)
    assert audit["status"] == "APPLIED" and replacement in text
    assert len(audit["interfaces"]["transactions"]) == 1
    assert audit["_research_draft"]["research"]["answers"][0]["source_quotes"][1] == replacement


def test_pipeline_persists_source_patch_rechecks_support_and_resumes_without_new_calls(tmp_path, monkeypatch):
    from auto_battery_research.util.design_repair import review_design
    from auto_battery_research.util import adaptive_retrieval, research_narrative
    from auto_battery_research.util.design_structure import render_design
    from auto_battery_research.tests.test_design_structure import document
    doc, params, fixed = stale_case()
    body, structure = render_design(document())
    text = body + "\n\n" + doc["final_answer"]
    doc["final_answer"] = text
    reviews = []
    reviewer = SimpleNamespace()
    def run(**kwargs):
        current = reviewer.research_draft
        reviews.append(copy.deepcopy(current["research"]))
        row = current["research"]["answers"][0]
        if current["research"]["record_errors"]:
            return {"issues": ["研究来源待修复"], "confidence": "low", "repair_issues": []}
        return {"issues": [], "confidence": "medium", "research_support_review": [verdict("answers", row, current)]}
    client = SourceClient(fixed)
    reviewer.run = run
    reviewer.llm = client
    pipeline = SimpleNamespace(reviewer=reviewer, writer=SimpleNamespace(llm=client),
        stage4_repair_options={"content_rounds": 1, "review_rounds": 3}, _extract_energy_claim=lambda _: None,
        repair_checkpoint_path=tmp_path / "checkpoint.json")
    seed = {"draft": text, "evidence": doc["evidence"], "parameters": params, "scheme": doc["scheme"],
        "plan": {"requirements": doc["requirements"]}, "research_draft": doc, "maintenance_calls": 2,
        "numeric_contract": 1, "design_structure": structure, "protocol_replayed": True}
    monkeypatch.setattr(adaptive_retrieval, "after_review", lambda *a: False)
    def context(seed, **kwargs):
        seed["retrieval_state"].setdefault("context_history", []).append({"purpose": kwargs.get("purpose")})
        return seed["evidence"]
    monkeypatch.setattr(adaptive_retrieval, "select_context", context)
    monkeypatch.setattr(research_narrative, "integrate", lambda *a, **kw: None)
    result, _, *_ = review_design(pipeline, seed, "研究材料及验证路径")
    assert result == text and seed["repair_rounds"] == 1 and seed["review_rounds"] == 2
    assert len(reviews) == 2 and len(client.calls) == 2
    saved = json.loads(pipeline.repair_checkpoint_path.read_text(encoding="utf-8"))
    assert saved["repair_audit"][0]["research_source_updates"]
    assert "_research_draft" not in saved["repair_audit"][0]
    assert not saved["research_draft"]["research"]["record_errors"]
    row = saved["research_draft"]["research"]["answers"][0]
    assert supported(row, "response", saved["research_draft"], saved["parameters"])
    review_design(pipeline, saved, "研究材料及验证路径")
    assert len(reviews) == 2 and len(client.calls) == 2
