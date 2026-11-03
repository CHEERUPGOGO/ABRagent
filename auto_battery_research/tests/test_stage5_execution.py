"""两子步骤、轻量检索及宽松实验推荐的离线回归。"""
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util import experiment_design as recipes
from auto_battery_research.util.stage5_execution import finalize_stage5
from auto_battery_research.tests.test_experiment_design import setup_design, scripted_client, recipe_payload


def manager(tmp_path, skip=False):
    return SimpleNamespace(root_dir=tmp_path, config={"paths": {}}, target_goal="实验方案测试",
        get_task_output_dir=lambda: tmp_path, get_stage_by_id=lambda i: SimpleNamespace(skip=skip))


def test_overlap_is_strict_but_budget_is_allowed(tmp_path):
    doc, recipe = setup_design(tmp_path)
    recipe["parameters"]["cathode.design_capacity_mAh_g"] = {
        "value": 190, "unit": "mAh/g", "basis": "budget_assumption", "source": "预算假设"}
    recipe["plan_hash"] = recipes.plan_hash(recipe)
    recipes.validate(recipe, doc)
    recipe["parameters"]["fabrication.cathode_thickness_um"] = {
        "value": 80, "unit": "um", "basis": "engineering_proposal", "source": "工程推荐"}
    recipe["plan_hash"] = recipes.plan_hash(recipe)
    with pytest.raises(ValueError, match="冲突"):
        recipes.validate(recipe, doc)


@pytest.mark.parametrize("physical_ok", [True, False])
def test_experiment_runs_with_or_without_valid_physics(tmp_path, monkeypatch, physical_ok):
    from auto_battery_research.checkers.pinn_physics_checker import PINNPhysicsChecker
    from auto_battery_research.tools import workflow_actions as actions
    doc, recipe = setup_design(tmp_path)
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc), encoding="utf-8")
    calls = []
    monkeypatch.setattr(PINNPhysicsChecker, "do_check", lambda *a, **k: (physical_ok, {}))
    monkeypatch.setattr(actions, "run_pinn_simulation", lambda *a, **k: calls.append("physics"))
    monkeypatch.setattr(actions, "select_best_stage5_result", lambda *a, **k: {})
    outcome = finalize_stage5(manager(tmp_path), client=scripted_client())
    assert outcome["key_findings"]["experiment"]["status"] == "PASSED"
    assert outcome["success"] is physical_ok
    assert (tmp_path / "experiment_evidence.json").exists()
    again = finalize_stage5(manager(tmp_path), client=None)
    assert calls.count("physics") <= 1
    assert again["key_findings"]["experiment"]["status"] == "PASSED"


def test_check_does_not_generate(tmp_path, monkeypatch):
    from auto_battery_research.checkers.stage5_checker import Stage5Checker
    from auto_battery_research.checkers.pinn_physics_checker import PINNPhysicsChecker
    monkeypatch.setattr(PINNPhysicsChecker, "do_check", lambda *a, **k: (True, {}))
    doc, _ = setup_design(tmp_path)
    doc["stage5_contract"] = 2
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc), encoding="utf-8")
    (tmp_path / "experiment_plan.json").unlink()
    checker = Stage5Checker()
    checker.on_init(manager(tmp_path, True), {"id": 5}, {})
    passed, diagnostic = checker.do_check()
    assert not passed and diagnostic["error_code"] == "STAGE5_EXPERIMENT_MISSING"


def test_physics_exception_does_not_skip_experiment(tmp_path, monkeypatch):
    from auto_battery_research.checkers.pinn_physics_checker import PINNPhysicsChecker
    from auto_battery_research.tools import workflow_actions as actions
    doc, _ = setup_design(tmp_path)
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(PINNPhysicsChecker, "do_check", lambda *a, **k: (False, {}))
    def fail(*args, **kwargs):
        raise RuntimeError("物理执行异常")
    monkeypatch.setattr(actions, "run_pinn_simulation", fail)
    result = finalize_stage5(manager(tmp_path), client=scripted_client())
    assert result["key_findings"]["physics"]["status"] == "FAILED"
    assert result["key_findings"]["experiment"]["status"] == "PASSED"
    assert not result["success"]
