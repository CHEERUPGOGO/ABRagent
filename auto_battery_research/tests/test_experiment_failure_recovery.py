"""空值边界、局部失败保留及工程模板的真实状态。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_experiment_design import setup_design, recipe_payload
from auto_battery_research.tests.test_stage5_execution import manager
from auto_battery_research.util import experiment_design as recipes
from auto_battery_research.util.research_experiments import validate_parameter
from auto_battery_research.util.experiment_repair import repair_record
from auto_battery_research.util.stage5_execution import finalize_stage5


def test_null_is_unset_but_locked_null_zero_and_real_values_are_protected():
    row = {"value": 17.5, "unit": "mg/cm2", "basis": "engineering_proposal", "source": "待验证工程值"}
    doc = {"requirements": {"scheme_required": True}, "ledger_view": {"parameters": {
        "cathode.loading_mg_cm2": {"value": None, "unit": "mg/cm2", "locked": False}}}}
    validate_parameter("cathode.loading_mg_cm2", row, doc)
    doc["ledger_view"]["parameters"]["cathode.loading_mg_cm2"]["locked"] = True
    with pytest.raises(ValueError, match="锁定"):
        validate_parameter("cathode.loading_mg_cm2", row, doc)
    for value in (0, 10):
        doc["ledger_view"]["parameters"]["cathode.loading_mg_cm2"].update(value=value, locked=False)
        with pytest.raises(ValueError, match="冲突"):
            validate_parameter("cathode.loading_mg_cm2", row, doc)


def test_invalid_ref_to_null_is_removed_with_only_dependent_steps(tmp_path):
    doc, record = setup_design(tmp_path)
    doc["ledger_view"]["parameters"]["cathode.loading_mg_cm2"] = {"value": None, "unit": "mg/cm2"}
    record["parameters"]["cathode.loading_mg_cm2"] = {"ref": "cathode.loading_mg_cm2"}
    record["sections"]["fabrication"].append("本步按{{cathode.loading_mg_cm2}}试制，确认干燥后称量。")
    unchanged = copy.deepcopy(record["sections"]["testing"])
    initial = copy.deepcopy(record)
    result = repair_record(record, doc, None, tmp_path, "input")
    assert result.get("origin") != "engineering_fallback" and result["repair_status"] == "PARTIAL"
    assert result["sections"]["testing"] == unchanged
    assert result["sections"]["fabrication"][:-1] == initial["sections"]["fabrication"][:-1]
    assert "cathode.loading_mg_cm2" not in result["parameters"]
    assert result["pending_parameters"]["cathode.loading_mg_cm2"]["record"] == {"ref": "cathode.loading_mg_cm2"}
    saved = json.loads((tmp_path / "experiment_draft.json").read_text(encoding="utf-8"))
    assert saved["original"] == initial and saved["record"] == result
    recipes.validate(result, doc)


def test_final_validation_error_is_archived_even_when_caller_must_fallback(tmp_path, monkeypatch):
    doc, record = setup_design(tmp_path)
    initial = copy.deepcopy(record)
    original = recipes.validate
    def validate(value, *a, **kw):
        if "repair_status" in value:
            raise ValueError("模拟提交失败")
        return original(value, *a, **kw)
    monkeypatch.setattr(recipes, "validate", validate)
    with pytest.raises(ValueError, match="模拟提交失败"):
        repair_record(record, doc, None, tmp_path, "input")
    saved = json.loads((tmp_path / "experiment_draft.json").read_text(encoding="utf-8"))
    assert saved["original"] == initial and saved["audit"]["final_error"] == "模拟提交失败"


def test_template_is_fallback_even_with_no_pending_fields_and_no_physics_model(tmp_path, monkeypatch):
    from auto_battery_research.tools import workflow_actions
    from auto_battery_research.checkers.stage5_checker import Stage5Checker
    doc, _ = setup_design(tmp_path)
    doc["delivery_contract"] = 1
    doc["report_readiness"] = {"category": "CONDITIONAL", "issues": [], "review_status": "REVIEWED"}
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(workflow_actions, "_match_pinn_system_for_scheme", lambda s: {"status": "NO_MATCH", "matched": False})
    monkeypatch.setattr(recipes, "generate", lambda task, current, *args: recipes.engineering_fallback(task, current, "test", "服务失败"))
    mgr = manager(tmp_path)
    result = finalize_stage5(mgr, client=SimpleNamespace(available=False))
    assert result["success"] and result["key_findings"]["experiment"]["status"] == "FALLBACK"
    checker = Stage5Checker()
    checker.on_init(mgr, {"id": 5}, {})
    passed, diagnostic = checker.do_check()
    assert passed and diagnostic["observed"]["is_fallback"] and not diagnostic["observed"]["experiment_ready"]
    current = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    assert current["report_readiness"]["experiment_status"] == "FALLBACK"
    assert "工程回退模板" in recipes.render(recipes.load(tmp_path), current)
