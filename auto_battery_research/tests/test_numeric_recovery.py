"""数值审计发布只读维护，完整验收后提交，并发覆盖拒绝。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.scheme_ledger import digest
from auto_battery_research.util.numeric_recovery import audit_existing_design


@pytest.mark.parametrize("concurrent", [False, True])
def test_legacy_numeric_audit_publishes_without_changing_science_or_budget(tmp_path, monkeypatch, concurrent):
    from auto_battery_research.tests.test_experiment_design import setup_design
    from auto_battery_research.tests.test_structure_review_recovery import draft
    from auto_battery_research.tools import workflow_actions
    from auto_battery_research.util.adaptive_retrieval import init_state
    doc, recipe = setup_design(tmp_path)
    doc.update(final_answer=draft(), delivery_contract=1, review_completed=True, review_passed=False,
               reviewer_output={"issues": ["科学论证仍需核验"], "confidence": "low"}, repair_audit=[],
               report_readiness={"category": "CONDITIONAL", "issues": ["科学论证仍需核验"], "review_status": "REVIEWED", "physics_status": "NOT_APPLICABLE"})
    seed = {"draft": doc["final_answer"], "scheme": copy.deepcopy(doc["scheme"]), "evidence": [],
            "parameters": copy.deepcopy(doc["ledger_view"]["parameters"]), "review_rounds": 6, "repair_rounds": 3, "maintenance_calls": 2,
            "reviewer_output": doc["reviewer_output"]}
    init_state(seed)
    doc["retrieval_state"] = copy.deepcopy(seed["retrieval_state"])
    execution = {"physics": {"status": "NOT_APPLICABLE", "input_hash": digest(doc["scheme"])},
                 "experiment": {"status": "PASSED", "plan_hash": recipe["plan_hash"]}}
    journal = [{"stage_id": 4, "notes": "保留设计与科学审核"}, {"stage_id": 5, "notes": "实验方案已核对"}]
    for name, data in (("design_scheme.json", doc), ("stage4_repair_checkpoint.json", seed), ("stage5_execution.json", execution), ("stage_journals.json", journal)):
        (tmp_path / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "design_scheme.md").write_text(doc["final_answer"], encoding="utf-8")
    stages = [SimpleNamespace(id=i, key=str(i), name="阶段" + str(i), status="FALLBACK" if i == 4 else "PASSED",
                              skip=False, allow_skip=False, duration_seconds=1) for i in range(1, 7)]
    mgr = SimpleNamespace(config={"paths": {"output_dir": str(tmp_path)}}, root_dir=tmp_path, target_goal=doc["target"], stages=stages,
        is_legacy_task=False, state_file_path=tmp_path / ".stage_state.json", get_task_output_dir=lambda *a: tmp_path,
        get_stage_by_id=lambda i: stages[i-1], get_all_stage_journal=lambda: journal, _save_state=lambda: None, set_stage_journal=lambda *a, **kw: None)
    monkeypatch.setattr(workflow_actions, "_match_pinn_system_for_scheme", lambda s: {"status": "NO_MATCH", "matched": False})
    real_render = workflow_actions.run_synthesis_report
    def render(*args, **kwargs):
        if concurrent:
            (tmp_path / "final_research_report.md").write_text("并发报告", encoding="utf-8")
        return real_render(*args, **kwargs)
    monkeypatch.setattr(workflow_actions, "run_synthesis_report", render)
    result = audit_existing_design(mgr)
    after = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    if concurrent:
        assert not result["success"] and "final_research_report.md" in result["error"]
        assert after == doc and not (tmp_path / "numeric_consistency.json").exists()
        return
    assert result["success"], result
    for key in ("scheme", "ledger_view", "evidence", "final_answer", "reviewer_output"):
        assert after[key] == doc[key]
    saved = json.loads((tmp_path / "stage4_repair_checkpoint.json").read_text(encoding="utf-8"))
    assert (saved["repair_rounds"], saved["review_rounds"], saved["maintenance_calls"]) == (3, 6, 2)
    assert "数值一致性核对" in (tmp_path / "final_research_report_supplement.md").read_text(encoding="utf-8")
    assert "数值一致性核对" not in (tmp_path / "final_research_report.md").read_text(encoding="utf-8")
    again = audit_existing_design(mgr)
    assert again["success"] and again["changed"] is False
