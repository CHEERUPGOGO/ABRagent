"""独立验收粒度、数量原文及历史迁移回归；模拟语义响应不冒充真实模型准确率。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.research_contract import normalize_requirements, attach_requirements, document_errors
from auto_battery_research.util.requirement_migration import reparse_requirements
from auto_battery_research.tests.test_research_contract import requirement
from auto_battery_research.tests.test_final_design_reporting import report_fixture
from auto_battery_research.util.scheme_ledger import commit_design, atomic_write, digest


def new_raw(goal, rows, task_quotes):
    return {"split_version": 2, "hierarchy_version": 1, "task_type": "design", "scheme_required": True,
            "groups": [{"id": "g1", "title": "研究要求", "parent_id": None, "source_quotes": task_quotes}],
            "task": {"description": "按原始目标开展研究", "source_quotes": task_quotes},
            "items": [dict(row, group_id="g1") for row in rows]}


@pytest.mark.parametrize("goal,quote,value,unit,operator", [
    ("设计300Wh_kg高比能电池", "300Wh_kg高比能", 300, "Wh/kg", None),
    ("目标350 Wh/kg", "350 Wh/kg", 350, "Wh/kg", None),
    ("保持率显著>74%", "保持率显著>74%", 74, "%", ">"),
    ("厚度不超过5 mm", "厚度不超过5 mm", 5, "mm", "<="),
    ("加工温度≤80 °C", "加工温度≤80 °C", 80, "°C", "<="),
    ("产率约80%", "产率约80%", 80, "%", "approx"),
])
def test_original_numeric_targets_and_optional_relation(goal, quote, value, unit, operator):
    raw = {"items": [requirement(quote, quantity={"value": value, "unit": unit, "operator": operator})]}
    qty = normalize_requirements(raw, goal)["items"][0]["quantity"]
    assert qty["value"] == value and qty["operator"] == operator
    assert qty["original_unit"]


@pytest.mark.parametrize("goal,quote,qty", [
    ("目标300 Wh/kg", "目标300 Wh/kg", {"value": 300, "unit": "Wh/kg", "operator": ">="}),
    ("保持率>74%", "保持率>74%", {"value": 74, "unit": "%", "operator": None}),
    ("电流300 mA，质量按Wh/kg说明", "电流300 mA，质量按Wh/kg说明", {"value": 300, "unit": "Wh/kg", "operator": None}),
    ("目标300 Wh/kg", "目标300 Wh/kg", {"value": 3000, "unit": "Wh/kg", "operator": None}),
    ("电流300 mAh/g", "电流300 mAh/g", {"value": 300, "unit": "mA", "operator": None}),
])
def test_numeric_fabrication_and_split_quantity_are_rejected(goal, quote, qty):
    with pytest.raises(ValueError):
        normalize_requirements({"items": [requirement(quote, quantity=qty)]}, goal)


def test_task_and_sources_cover_without_creating_action_item():
    goal = "设计300Wh_kg高比能硅碳负极NCM811锂离子电池"
    rows = [dict(requirement("300Wh_kg高比能", quantity={"value": 300, "unit": "Wh/kg", "operator": None}), id="energy"),
            dict(requirement("硅碳负极", "constraint", binding={"path": "anode.material", "value": "si_base", "unit": None}), id="anode"),
            dict(requirement("NCM811", "constraint", binding={"path": "cathode.material", "value": "NCM811", "unit": None}), id="cathode")]
    result = normalize_requirements(new_raw(goal, rows, ["设计", "锂离子电池"]), goal)
    assert len(result["items"]) == 3 and result["task"]["source_quotes"] == ["设计", "锂离子电池"]


def test_generic_task_alone_does_not_require_fake_independent_item():
    goal = "设计电池方案"
    result = normalize_requirements(new_raw(goal, [], [goal]), goal)
    assert result["items"] == [] and result["task"]
    assert not document_errors({"target": goal, "requirements": result, "research": {"answers": []}})


def test_uncovered_clause_is_not_hidden_by_task_recovery():
    goal = "设计电池；保持率>74%；禁止含氟。"
    raw = new_raw(goal, [requirement("保持率>74%", quantity={"value": 74, "unit": "%", "operator": ">"})], ["设计电池"])
    result = attach_requirements({"requirements": raw}, goal)["requirements"]
    assert result["status"] == "partial"
    assert any("禁止含氟" in i["quote"] for i in result["items"])
    assert not any(i["quote"] == "设计电池" for i in result["items"])


def test_same_goal_additional_wording_and_distinct_conditions():
    goal = "研究材料XQ-27；0.1 C保持率80%；1 C保持率80%；降低成本，成本优先降低；给出排行、合成路线、测试方案。"
    quotes = ["0.1 C保持率80%", "1 C保持率80%", "降低成本", "排行", "合成路线", "测试方案"]
    rows = [dict(requirement(q, "deliverable" if n >= 3 else "objective"), id=f"r{n}") for n, q in enumerate(quotes)]
    for row in rows:
        row["start"] = goal.index(row["quote"])
    rows[1]["start"] = goal.index("；1 C") + 1
    rows[2]["source_quotes"] = ["成本优先降低"]
    result = normalize_requirements(new_raw(goal, rows, ["研究材料XQ-27", "给出"]), goal)
    assert len(result["items"]) == 6
    assert result["items"][2]["source_spans"][0]["quote"] == "成本优先降低"


def migration_fixture(tmp_path):
    mgr, doc, _, _ = report_fixture(tmp_path)
    goal = "设计电池；降低成本；成本优先降低；采用NCM811"
    rows = [dict(requirement("设计电池", "deliverable"), id="task", verification_needed=False),
            dict(requirement("降低成本", "preference"), id="cost1"),
            dict(requirement("成本优先降低", "preference"), id="cost2"),
            dict(requirement("采用NCM811", "constraint", binding={"path": "cathode.material", "value": "NCM811", "unit": ""}), id="material")]
    old = normalize_requirements({"items": rows}, goal)
    doc.update(target=goal, requirements=old, delivery_contract=1,
               report_readiness={"category": "CONDITIONAL", "issues": [], "physics_status": "PASSED", "review_status": "REVIEWED"})
    doc["research"] = {"version": 1, "status": "partial", "answers": [], "candidates": [], "arguments": [], "diagnostics": [], "selected_candidate_id": None}
    from auto_battery_research.util.research_contract import gap_answer, bindings
    doc["research"]["answers"] = [gap_answer(i, "已有缺口") for i in old["items"]]
    doc["scheme_parameters"] = bindings(old)
    mgr.target_goal = goal
    commit_design(tmp_path, doc)
    from auto_battery_research.util.experiment_design import engineering_fallback
    recipe = engineering_fallback(tmp_path, doc, "test", "测试副本保留明确缺口")
    doc.update(experiment_contract=2, stage5_contract=2, experiment_plan_hash=recipe["plan_hash"])
    atomic_write(tmp_path / "design_scheme.json", json.dumps(doc, ensure_ascii=False))
    mgr.stages[4].status = "FALLBACK"
    raw = new_raw(goal, [dict(requirement("降低成本", "preference", source_quotes=["成本优先降低"]), id="cost"),
                         dict(requirement("采用NCM811", "constraint", binding={"path": "cathode.material", "value": "NCM811", "unit": ""}), id="material")], ["设计电池"])
    proposal = {"requirements": raw, "mapping": [
        {"old_id": old["items"][0]["id"], "new_id": None, "relation": "task", "reason": "泛泛总任务"},
        {"old_id": old["items"][1]["id"], "new_id": "cost", "relation": "merged", "reason": "同义偏好"},
        {"old_id": old["items"][2]["id"], "new_id": "cost", "relation": "merged", "reason": "同义偏好"},
        {"old_id": old["items"][3]["id"], "new_id": "material", "relation": "equivalent", "reason": "约束相同"}]}
    llm = SimpleNamespace(available=True, chat=lambda *a, **k: json.dumps(proposal))
    review = SimpleNamespace(available=True, chat=lambda *a, **k: '{"approved":true,"issues":[]}')
    return mgr, doc, proposal, llm, review


def test_migration_dry_run_is_non_mutating(tmp_path):
    mgr, doc, _, llm, review = migration_fixture(tmp_path)
    initial = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    result = reparse_requirements(mgr, dry_run=True, client=llm, reviewer=review)
    assert result["success"] and result["before"] == 4 and result["after"] == 2
    assert initial == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert not (tmp_path / "requirement_migration_history").exists()


def test_migration_updates_associations_without_changing_physics(tmp_path):
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    mgr, doc, _, llm, review = migration_fixture(tmp_path)
    physics = (tmp_path / "pinn_simulation_result.json").read_bytes()
    result = reparse_requirements(mgr, client=llm, reviewer=review)
    assert result["success"], result
    saved = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    assert len(saved["requirements"]["items"]) == 2
    assert saved["scheme"] == doc["scheme"] and saved.get("final_answer") == doc.get("final_answer")
    assert (tmp_path / "pinn_simulation_result.json").read_bytes() == physics
    assert saved["ledger_view"]["parameters"]["cathode.material"]["locked"]
    assert not document_errors(saved)
    checker = FinalReportChecker(); checker.on_init(mgr, {"id": 6}, mgr.config)
    assert checker.do_check()[0]


def test_mapping_cannot_drop_lock_or_hide_requirement(tmp_path):
    mgr, doc, proposal, _, review = migration_fixture(tmp_path)
    proposal["mapping"].pop()
    llm = SimpleNamespace(available=True, chat=lambda *a, **k: json.dumps(proposal))
    original = (tmp_path / "design_scheme.json").read_bytes()
    assert not reparse_requirements(mgr, client=llm, reviewer=review)["success"]
    assert (tmp_path / "design_scheme.json").read_bytes() == original


def test_independent_review_rejection_keeps_original(tmp_path):
    mgr, doc, _, llm, _ = migration_fixture(tmp_path)
    review = SimpleNamespace(available=True, chat=lambda *a, **k: '{"approved":false,"issues":["遗漏独立目标"]}')
    original = (tmp_path / "design_scheme.json").read_bytes()
    assert not reparse_requirements(mgr, client=llm, reviewer=review)["success"]
    assert (tmp_path / "design_scheme.json").read_bytes() == original


def test_preview_failure_does_not_replace_task_files(tmp_path, monkeypatch):
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    mgr, _, _, llm, review = migration_fixture(tmp_path)
    initial = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    monkeypatch.setattr(FinalReportChecker, "do_check", lambda *a, **k: (False, {"error": "终审拒绝"}))
    assert not reparse_requirements(mgr, client=llm, reviewer=review)["success"]
    assert all((tmp_path / name).read_bytes() == data for name, data in initial.items())


def test_cli_requirement_preview_never_constructs_manager(tmp_path, monkeypatch):
    import sys
    from auto_battery_research import cli
    from auto_battery_research.tools import stage_tools
    from auto_battery_research.util import requirement_migration
    mgr, doc, _, llm, review = migration_fixture(tmp_path)
    initial = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    monkeypatch.setattr(requirement_migration, "preview_requirements", lambda *a: reparse_requirements(mgr, dry_run=True, client=llm, reviewer=review))
    monkeypatch.setattr(stage_tools, "get_stage_manager_for_goal", lambda *a: pytest.fail("预览不构造管理器"))
    monkeypatch.setattr(sys, "argv", ["abr-cli", "--goal", doc["target"], "--reparse-requirements", "--dry-run"])
    cli.main()
    assert all((tmp_path / name).read_bytes() == data for name, data in initial.items())


def test_commit_rejection_rolls_back_every_published_file(tmp_path, monkeypatch):
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    mgr, _, _, llm, review = migration_fixture(tmp_path)
    initial = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    checks = []
    def check(*a, **k):
        checks.append(1)
        return len(checks) == 1, {"error": "提交后拒绝"}
    monkeypatch.setattr(FinalReportChecker, "do_check", check)
    assert not reparse_requirements(mgr, client=llm, reviewer=review)["success"] and len(checks) == 2
    assert all((tmp_path / name).read_bytes() == data for name, data in initial.items())


def test_migration_preserves_attempt_budget_and_scientific_rounds(tmp_path):
    mgr, doc, _, llm, review = migration_fixture(tmp_path)
    from auto_battery_research.util import stage5_attempts
    stage5_attempts.reserve(mgr, "energy_optimization", doc["scheme"]["geometry_hash"])
    before = (tmp_path / stage5_attempts.STATE_FILE).read_bytes()
    result = reparse_requirements(mgr, client=llm, reviewer=review)
    assert result["success"], result
    assert (tmp_path / stage5_attempts.STATE_FILE).read_bytes() == before
    assert stage5_attempts.budget(mgr)["used"] == 1
