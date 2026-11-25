"""需求分组档案零模型恢复：仅删连接词、保护科研内容与预算、拒绝并发覆盖。"""
import copy
import json
from pathlib import Path

import pytest

from auto_battery_research.tests.test_research_contract import requirement
from auto_battery_research.tests.test_requirement_hierarchy import raw_contract
from auto_battery_research.util import scheme_ledger
from auto_battery_research.util.research_contract import normalize_requirements, fallback_requirements, fallback_research, document_errors
from auto_battery_research.util.requirement_archive_repair import repair_archived_groups, sync_contract


def setup(task):
    goal = "研究材料；以提高循环为目标。"
    raw = raw_contract(goal, [dict(requirement("提高循环"), id="cycle", group_id="science")])
    old = normalize_requirements(copy.deepcopy(raw), goal)
    old["groups"] = []
    for word in ("以", "为目标"):
        row = fallback_requirements(word)["items"][0]
        row.update(start=goal.index(word), end=goal.index(word) + len(word))
        old["items"].append(row)
    ledger = scheme_ledger.ensure(task, goal)
    doc = {"target": goal, "requirements": old, "plan": {"requirements": copy.deepcopy(old)}, "scheme": {},
           "final_answer": "科研正文与既有科学限制保留。", "evidence": [{"passage_id": "p1", "text": "原文"}],
           "ledger_view": ledger, "ledger_hash": scheme_ledger.digest(ledger), "delivery_contract": 1,
           "review_completed": True, "review_passed": False, "review_status": "REVIEWED_WITH_ISSUES", "confidence": "low",
           "report_readiness": {"category": "DIAGNOSTIC", "issues": ["科学证据不足"], "retryable": False},
           "research": fallback_research(old, "科研证据不足")}
    seed = {"draft": doc["final_answer"], "parameters": ledger["parameters"], "plan": doc["plan"],
            "review_rounds": 5, "repair_rounds": 3, "maintenance_calls": 2, "research_draft": doc}
    for name, value in [("design_scheme.json", doc), ("rag_result.json", doc),
                        ("stage4_repair_checkpoint.json", seed), ("stage4_retrieval_checkpoint.json", {"plan": doc["plan"]})]:
        (task / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    (task / "design_scheme.md").write_text(doc["final_answer"], encoding="utf-8")
    (task / ".stage_state.json").write_text('{"stage":4,"status":"FAILED"}', encoding="utf-8")
    trace = task / "llm_trace"
    trace.mkdir()
    events = [{"goal": goal, "trace_tag": "planner", "output": {"status": "ok", "text": json.dumps({"requirements": raw})}},
              {"goal": goal, "trace_tag": "requirements_repair", "output": {"status": "ok", "text": json.dumps({
                  "task": {"description": "恢复原文覆盖", "source_quotes": ["以提高循环为目标"]}, "groups": raw["groups"]})}}]
    (trace / "trace_test.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8")
    return doc, seed


def test_archive_repair_keeps_science_and_passes_real_gate(tmp_path):
    doc, seed = setup(tmp_path)
    initial = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    result = repair_archived_groups(tmp_path)
    assert result["success"] and result["stage4_check_pass"] and not result["workflow_advanced"]
    repaired = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    assert not document_errors(repaired)
    assert repaired["requirements"]["groups"] and len(repaired["requirements"]["items"]) == 1
    for key in ("final_answer", "scheme", "ledger_view", "confidence", "review_completed", "review_passed", "report_readiness"):
        assert repaired[key] == doc[key]
    checkpoint = json.loads((tmp_path / "stage4_repair_checkpoint.json").read_text(encoding="utf-8"))
    assert [checkpoint[k] for k in ("review_rounds", "repair_rounds", "maintenance_calls")] == [5, 3, 2]
    assert (tmp_path / ".stage_state.json").read_bytes() == initial[".stage_state.json"]
    backup = Path(result["history"]) / "originals"
    assert all((backup / name).read_bytes() == data for name, data in initial.items())
    assert repair_archived_groups(tmp_path)["changed"] is False


def test_archive_repair_refuses_concurrent_update(tmp_path, monkeypatch):
    from auto_battery_research.util import report_readiness
    setup(tmp_path)
    before = (tmp_path / "design_scheme.json").read_bytes()
    real_check = report_readiness.check
    def check(path, doc):
        (tmp_path / ".stage_state.json").write_text("并发更新", encoding="utf-8")
        return real_check(path, doc)
    monkeypatch.setattr(report_readiness, "check", check)
    with pytest.raises(ValueError, match="任务已变更"):
        repair_archived_groups(tmp_path)
    assert (tmp_path / "design_scheme.json").read_bytes() == before


def test_sync_contract_only_removes_active_connector_records():
    old, new = {"source": "课题"}, {"source": "课题", "groups": [{"id": "science"}]}
    value = {"requirements": old, "research": {"answers": [{"requirement_id": "real"}, {"requirement_id": "connector"}]},
             "reviewer_output": {"issues": ["原科学意见"], "research_support_review": [
                 {"collection": "answers", "id": "real"}, {"collection": "answers", "id": "connector"}]},
             "repair_audit": [{"requirements": old, "requirement_ids": ["connector"]}]}
    result = sync_contract(value, old, new, {"connector"})
    assert result["research"]["answers"] == [{"requirement_id": "real"}]
    assert result["reviewer_output"]["issues"] == ["原科学意见"]
    assert result["repair_audit"] == value["repair_audit"] and value["requirements"] == old
