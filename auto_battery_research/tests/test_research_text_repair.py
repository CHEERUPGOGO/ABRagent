"""正文与关联研究摘要同步纠错：精确授权、独立支持复核和事务回滚。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_research_support import sample, verdict
from auto_battery_research.util.design_repair import authorize
from auto_battery_research.util.repair_interfaces import build_interfaces, execute
from auto_battery_research.util.research_records import collect_research
from auto_battery_research.util.research_source_metadata import prepare
from auto_battery_research.util.research_support import apply_review, supported


OLD = "按Li=1.26、TM=0.80计算，Li:TM=1.05 [p1]。"
NEW = OLD.replace("1.05", "1.575")


def case():
    doc, answer = sample()
    params = doc["ledger_view"]["parameters"]
    doc["final_answer"] += "\n\n" + OLD + "\n\n独立原句。"
    answer["source_quotes"].append(OLD)
    answer["response"] = "名义投料Li:TM=1.05，实际组成待测。"
    answer["field_support"]["response"] = [3]
    argument = {"id": "arg_calc", "title": "投料口径", "text": "名义Li:TM=1.05，不代表实测组成。",
        "basis": "inference", "requirement_ids": [answer["requirement_id"]], "parameter_refs": [],
        "source_quotes": [OLD], "field_support": {"text": [0]}, "citations": ["p1"]}
    candidate = {"id": "candidate_a", "name": "配方A", "scope": "candidate", "rank": None,
        "summary": "名义Li:TM=1.05，实际组成待测。", "rationale": "结构稳定性仍需验证。", "risks": "协同效果待验证。",
        "source_quotes": answer["source_quotes"], "field_support": {"summary": [3], "rationale": [1], "risks": [2]},
        "citations": ["p1", "p2"], "parameter_refs": ["cathode.material"]}
    doc["research"] = collect_research({"answers": [answer], "arguments": [argument], "candidates": [candidate]}, doc, params)
    assert not doc["research"]["record_errors"]
    for collection in ("answers", "arguments", "candidates"):
        row = doc["research"][collection][0]
        apply_review(doc["research"], [verdict(collection, row, doc)], doc, params, doc["evidence"])
    issues = [{"id": "calc", "quote": OLD, "reason": "比值计算错误", "suggestion": "修正除法及相关摘要", "parameter_paths": []}]
    return doc, params, issues


class Client:
    def __init__(self, replacements=None, mutate=None, verify=None):
        self.replacements = {OLD: NEW} if replacements is None else replacements
        self.mutate, self.verify, self.calls = mutate, verify, []

    def replace(self, value):
        for old, new in self.replacements.items():
            value = value.replace(old, new)
        return value

    def chat(self, system, user, **kwargs):
        data = json.loads(user)
        self.calls.append((kwargs["trace_tag"], data))
        if kwargs["trace_tag"] == "writer_patch":
            rows, seen = [], set()
            for iid, interface in data["interface_contract"]["interfaces"].items():
                row = {"interface_id": iid, "replacement": self.replace(interface["original"])}
                changes = []
                for token, grant in interface.get("research_text_permissions", {}).items():
                    fields = []
                    for field, rule in grant["fields"].items():
                        if (token, field) in seen:
                            continue
                        seen.add((token, field))
                        value = rule["old"].replace("1.05", "1.575").replace("提高结构稳定性", "有望提高结构稳定性")
                        if value != rule["old"]:
                            fields.append({"field": field, "old": rule["old"], "new": value,
                                           "source_quotes": [self.replace(q) for q in rule["source_quotes"]]})
                    if fields:
                        changes.append({"collection": grant["collection"], "id": grant["id"],
                                        "record_hash": grant["record_hash"], "fields": fields})
                if changes:
                    row["research_changes"] = changes
                rows.append(row)
            patch = {"base_hash": data["permission"]["base_hash"], "submissions": rows}
            if self.mutate:
                self.mutate(patch, data)
            return json.dumps(patch)
        result = {"verdicts": [{"group_id": gid, "issue_id": key, "resolved": True, "reason": "核对候选计算与工况"}
            for gid, transaction in data["transactions"].items() for key in transaction["issue_ids"]],
            "research_verdicts": [{"group_id": gid, **{k: field[k] for k in ("collection", "id", "field", "change_hash")},
                "supported": True, "reason": "与纠正后的原句及证据一致，未提升验证状态"}
                for gid, transaction in data["transactions"].items() for field in transaction["research_text_changes"]], "new_issues": []}
        if self.verify:
            self.verify(result)
        return json.dumps(result)


def run(doc, params, issues, client):
    permit = authorize(doc["final_answer"], issues, params, doc)
    permit["evidence_ids"] = [e["passage_id"] for e in doc["evidence"]]
    return execute(SimpleNamespace(llm=client), client, doc["final_answer"], params, permit, issues,
                   doc["evidence"], research_doc=doc)


def first_change(patch):
    return next(row["research_changes"][0] for row in patch["submissions"] if row.get("research_changes"))


def test_calculation_and_all_linked_collections_commit_together_without_scientific_promotion():
    doc, params, issues = case()
    before = copy.deepcopy(doc)
    client = Client()
    text, values, audit = run(doc, params, issues, client)
    assert doc == before and values == params
    assert audit["status"] == "APPLIED" and NEW in text and OLD not in text
    assert [tag for tag, _ in client.calls] == ["writer_patch", "repair_verify"]
    current = audit["_research_draft"]
    for collection, field in (("answers", "response"), ("arguments", "text"), ("candidates", "summary")):
        original, changed = before["research"][collection][0], current["research"][collection][0]
        assert "1.575" in changed[field] and not supported(changed, field, current, params)
        for key in ("status", "verification", "requirement_id", "requirement_ids", "id", "name", "rank", "scope", "citations", "parameter_refs", "reason", "next_step", "rationale", "risks"):
            assert changed.get(key) == original.get(key)
    assert len(audit["research_source_updates"]) == 3 and len(audit["transactions"]) == 1
    assert all(update["reason"] == "body_summary_correction" for update in audit["research_source_updates"])
    checked = client.calls[-1][1]
    assert len(next(iter(checked["transactions"].values()))["research_text_changes"]) == 3
    assert checked["combined_preview"]["research"]["arguments"][0]["text"] == current["research"]["arguments"][0]["text"]


@pytest.mark.parametrize("failure", ["unsupported", "missing", "wrong_hash", "duplicate", "new_issue", "malformed"])
def test_summary_review_failure_rolls_back_body_and_research(failure):
    doc, params, issues = case()
    def fail(result):
        if failure == "unsupported": result["research_verdicts"][0]["supported"] = False
        elif failure == "missing": result["research_verdicts"].pop()
        elif failure == "wrong_hash": result["research_verdicts"][0]["change_hash"] = "stale"
        elif failure == "duplicate": result["research_verdicts"].append(result["research_verdicts"][0])
        elif failure == "malformed": result["research_verdicts"] = {}
        else: result["new_issues"] = [{"group_id": result["verdicts"][0]["group_id"], "reason": "因果强度仍不匹配"}]
    text, values, audit = run(doc, params, issues, Client(verify=fail))
    assert text == doc["final_answer"] and values == params and audit["status"] == "REJECTED"
    assert audit["research_source_updates"] == []


@pytest.mark.parametrize("attack", ["status", "verification", "identity", "field", "old", "hash", "unknown_parameter", "remove_binding",
    "number", "source", "unchanged_source", "unknown_citation", "empty", "duplicate", "support_review", "record", "malformed_binding"])
def test_restricted_patch_rejects_unauthorized_or_unsupported_changes(attack):
    doc, params, issues = case()
    def mutate(patch, data):
        change = first_change(patch)
        field = change["fields"][0]
        if attack in ("status", "verification", "support_review"): change[attack] = "supported"
        elif attack == "identity": change["id"] = "unrelated"
        elif attack == "field": field["field"] = "reason"
        elif attack == "old": field["old"] += "过期"
        elif attack == "hash": change["record_hash"] = "stale"
        elif attack == "unknown_parameter": field["new"] += "{{condition.unknown}}"
        elif attack == "remove_binding": field["new"] += "{{cathode.material}}"
        elif attack == "malformed_binding": field["new"] += "{{param:cathode.material}}"
        elif attack == "number": field["new"] = "名义Li:TM=99.9，组成待测。"
        elif attack == "source": field["source_quotes"] = ["独立原句。"]
        elif attack == "unchanged_source": field["source_quotes"] = ["方案采用配方A。"]
        elif attack == "unknown_citation": field["new"] += "[unknown]"
        elif attack == "empty": field["new"] = ""
        elif attack == "duplicate": change["fields"].append(copy.deepcopy(field))
        else: change["fields"][0]["record"] = {"status": "answered"}
    client = Client(mutate=mutate)
    text, values, audit = run(doc, params, issues, client)
    assert text == doc["final_answer"] and values == params and audit["status"] == "REJECTED"
    assert [tag for tag, _ in client.calls] == ["writer_patch"]


def test_no_actual_source_change_cannot_authorize_summary_rewrite():
    doc, params, issues = case()
    client = Client(replacements={})
    text, _, audit = run(doc, params, issues, client)
    assert audit["status"] == "REJECTED" and text == doc["final_answer"]
    assert len(client.calls) == 1


def test_failed_summary_field_does_not_rollback_independent_body_transaction():
    doc, params, issues = case()
    issues.append({"id": "other", "quote": "独立原句。", "reason": "说明范围", "suggestion": "增加限制", "parameter_paths": []})
    def reject(result):
        result["research_verdicts"][0]["supported"] = False
    client = Client({OLD: NEW, "独立原句。": "独立说明仅限当前条件。"}, verify=reject)
    text, _, audit = run(doc, params, issues, client)
    assert OLD in text and NEW not in text and text.endswith("独立说明仅限当前条件。")
    assert audit["status"] == "APPLIED" and audit["resolved_issue_ids"] == ["other"]
    assert audit["research_source_updates"] == []


def test_permissions_do_not_include_unrelated_fields_source_only_or_compiled_v3_records():
    doc, params, issues = case()
    permit = authorize(doc["final_answer"], issues, params, doc)
    contract = build_interfaces(doc["final_answer"], params, issues, permit, doc)
    grants = next(iter(contract["interfaces"].values()))["research_text_permissions"]
    assert set(grants["answers:" + doc["research"]["answers"][0]["requirement_id"]]["fields"]) == {"response"}
    from auto_battery_research.util.research_text_repair import permissions
    assert permissions(doc, doc["final_answer"], {"kind": "research_source"}) == {}
    assert permissions(doc, doc["final_answer"], {"kind": "parameter_source"}) == {}
    for collection in ("answers", "arguments", "candidates"):
        doc["research"][collection][0]["projection_origin"] = "writer_content_v3"
    assert not permissions(doc, doc["final_answer"], next(iter(contract["interfaces"].values())))


def test_scientific_conclusion_can_be_downgraded_without_changing_status_or_identity():
    doc, answer = sample()
    params = doc["ledger_view"]["parameters"]
    answer["response"] = "采用{{cathode.material}}提高结构稳定性，协同效果待验证。"
    doc["research"] = collect_research({"answers": [answer]}, doc, params)
    old = "该路线提高结构稳定性 [p1]。"
    new = "该路线有望提高结构稳定性，尚需验证 [p1]。"
    issues = [{"id": "claim", "quote": old, "reason": "降低验证强度", "suggestion": "保留推断边界", "parameter_paths": []}]
    text, _, audit = run(doc, params, issues, Client({old: new}))
    assert audit["status"] == "APPLIED" and new in text
    fixed = audit["_research_draft"]["research"]["answers"][0]
    assert "有望" in fixed["response"] and "{{cathode.material}}" in fixed["response"]
    assert fixed["status"] == answer["status"] and fixed["verification"] == answer["verification"]


def test_existing_parameter_template_cannot_be_removed_during_claim_correction():
    doc, answer = sample()
    params = doc["ledger_view"]["parameters"]
    answer["response"] = "采用{{cathode.material}}提高结构稳定性，协同效果待验证。"
    doc["research"] = collect_research({"answers": [answer]}, doc, params)
    old = "该路线提高结构稳定性 [p1]。"
    new = "该路线有望提高结构稳定性 [p1]。"
    issues = [{"id": "claim", "quote": old, "reason": "结论待验证", "suggestion": "降低强度", "parameter_paths": []}]
    def remove(patch, data):
        field = first_change(patch)["fields"][0]
        field["new"] = field["new"].replace("{{cathode.material}}", "配方A")
    client = Client({old: new}, mutate=remove)
    text, _, audit = run(doc, params, issues, client)
    assert audit["status"] == "REJECTED" and text == doc["final_answer"] and len(client.calls) == 1


def test_single_source_legacy_record_can_be_corrected_with_same_explicit_binding():
    doc, params, issues = case()
    argument = doc["research"]["arguments"][0]
    argument["text"] = OLD
    argument["title"] = ""
    argument["source_quote"] = argument.pop("source_quotes")[0]
    for field in ("field_support", "source_locations", "support_fingerprint", "support_review"):
        argument.pop(field, None)
    client = Client()
    _, _, audit = run(doc, params, issues, client)
    assert audit["status"] == "APPLIED"
    row = audit["_research_draft"]["research"]["arguments"][0]
    assert row["text"] == NEW and row["source_quotes"] == [NEW]


def test_same_record_edits_from_two_interfaces_form_one_atomic_transaction():
    doc, params, issues = case()
    sentence = "该路线提高结构稳定性 [p1]。"
    issues.append({"id": "claim", "quote": sentence, "reason": "须降低强度", "suggestion": "区分推断与验证", "parameter_paths": []})
    def reject(result):
        result["research_verdicts"][0]["supported"] = False
    client = Client({OLD: NEW, sentence: "该路线有望提高结构稳定性 [p1]。"}, verify=reject)
    text, _, audit = run(doc, params, issues, client)
    assert text == doc["final_answer"] and audit["status"] == "REJECTED"
    assert len(audit["interfaces"]["transactions"]) == 1


def test_stale_or_invalid_whole_record_cannot_be_accepted_by_isolating_it():
    doc, params, issues = case()
    permit = authorize(doc["final_answer"], issues, params, doc)
    contract = build_interfaces(doc["final_answer"], params, issues, permit, doc)
    interface = next(iter(contract["interfaces"].values()))
    grants = interface["research_text_permissions"]
    grant = next(g for g in grants.values() if g["collection"] == "answers")
    rule = grant["fields"]["response"]
    patch = {"collection": "answers", "id": grant["id"], "record_hash": grant["record_hash"],
        "fields": [{"field": "response", "old": rule["old"], "new": rule["old"].replace("1.05", "1.575"), "source_quotes": [NEW]}]}
    doc["research"]["answers"][0]["verification"] = "experiment"
    with pytest.raises(ValueError, match="过期"):
        prepare(doc, doc["final_answer"], doc["final_answer"].replace(OLD, NEW), params, text_changes=[patch], text_grants=grants)
    # 即便重新签发记录哈希，错误验证状态也不能靠collect_research隔离后被认作成功。
    from auto_battery_research.util.scheme_ledger import digest
    grant["record_hash"] = patch["record_hash"] = digest(doc["research"]["answers"][0])
    with pytest.raises(ValueError, match="不得宣称"):
        prepare(doc, doc["final_answer"], doc["final_answer"].replace(OLD, NEW), params, text_changes=[patch], text_grants=grants)


def test_pipeline_saves_synchronized_research_reuses_budget_and_resumes_without_calls(tmp_path, monkeypatch):
    from auto_battery_research.util import adaptive_retrieval, research_narrative
    from auto_battery_research.util.design_repair import review_design
    from auto_battery_research.util.design_structure import render_design
    from auto_battery_research.tests.test_design_structure import document
    doc, params, issues = case()
    body, structure = render_design(document())
    doc["final_answer"] = body + "\n\n" + doc["final_answer"]
    reviews, client = [], Client()
    reviewer = SimpleNamespace(llm=client)
    def review(**kwargs):
        current = reviewer.research_draft
        reviews.append(copy.deepcopy(current))
        if OLD in current["final_answer"]:
            return {"issues": ["投料比计算错误及关联摘要不一致"], "confidence": "low", "repair_issues": issues}
        return {"issues": [], "confidence": "medium", "research_support_review": [verdict(c, r, current)
            for c in ("answers", "arguments", "candidates") for r in current["research"][c]]}
    reviewer.run = review
    pipeline = SimpleNamespace(reviewer=reviewer, writer=SimpleNamespace(llm=client),
        stage4_repair_options={"content_rounds": 2, "review_rounds": 5}, _extract_energy_claim=lambda _: None,
        repair_checkpoint_path=tmp_path / "checkpoint.json")
    seed = {"draft": doc["final_answer"], "evidence": doc["evidence"], "parameters": params, "scheme": doc["scheme"],
        "plan": {"requirements": doc["requirements"]}, "research_draft": doc, "maintenance_calls": 2,
        "repair_rounds": 1, "review_rounds": 1, "numeric_contract": 1, "design_structure": structure, "protocol_replayed": True}
    monkeypatch.setattr(adaptive_retrieval, "after_review", lambda *a: False)
    def context(seed, **kwargs):
        seed["retrieval_state"].setdefault("context_history", []).append({"purpose": kwargs.get("purpose")})
        return seed["evidence"]
    monkeypatch.setattr(adaptive_retrieval, "select_context", context)
    monkeypatch.setattr(research_narrative, "integrate", lambda *a, **kw: None)
    result, *_ = review_design(pipeline, seed, "研究材料及验证路径")
    assert NEW in result and OLD not in result
    assert seed["repair_rounds"] == 2 and seed["review_rounds"] == 3
    assert len(reviews) == 2 and len(client.calls) == 2
    saved = json.loads(pipeline.repair_checkpoint_path.read_text(encoding="utf-8"))
    assert saved["repair_audit"][0]["research_source_updates"][0]["changed_fields"]
    assert "_research_draft" not in saved["repair_audit"][0]
    row = saved["research_draft"]["research"]["answers"][0]
    assert "1.575" in row["response"] and supported(row, "response", saved["research_draft"], saved["parameters"])
    review_design(pipeline, saved, "研究材料及验证路径")
    assert len(reviews) == 2 and len(client.calls) == 2
    assert saved["repair_rounds"] == 2 and saved["review_rounds"] == 3
