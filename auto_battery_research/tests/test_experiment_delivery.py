"""推荐交付与实验验证分开，执行缺项及物理失败仍受门禁保护。"""
import copy
import json

import pytest

from auto_battery_research.tests.test_experiment_design import recipe_payload
from auto_battery_research.tests.test_experiment_generation import Client, design
from auto_battery_research.tests.test_stage5_execution import manager
from auto_battery_research.util import experiment_design as recipes
from auto_battery_research.util.experiment_delivery import assessment, blocking_pending_fields, validation_gap_errors
from auto_battery_research.util.experiment_values import make_pending

pytestmark = pytest.mark.unit
DS = "cathode.D_s_m2s"


def setup(tmp_path):
    doc = design(tmp_path)
    doc["requirements"] = {"scheme_required": True, "status": "parsed", "items": [
        {"id": "r1", "kind": "objective", "quote": "推荐极片厚度并确认最优性", "description": "推荐厚度并验证",
         "verification_needed": True, "status": "clear"}]}
    payload = recipe_payload()
    payload["parameters"]["cathode.active_fraction"]["canonical_path"] = "cathode.active_fraction"
    payload["parameters"][DS] = {"value": None, "unit": "m2/s", "basis": "engineering_proposal",
        "source": "未测量", "meaning": "本征扩散系数", "role": "transport",
        "reason": "本轮操作不使用该物性", "acquisition_method": "后续独立测量"}
    payload["sections"]["gaps"].append("本征物性{{" + DS + "}}保持待测，本轮操作不使用。")
    payload["requirement_tests"] = [{"requirement_id": "r1", "status": "gap", "gap_kind": "validation",
        "recommendation_refs": ["cathode.L_um"], "method": "先评估推荐厚度，再比较候选",
        "conditions": "采用{{cathode.L_um}}", "baseline": "同批对照", "criterion": "按实测表现比较",
        "reason": "当前推荐已给出，最优性须后续实验确认", "next_step": "首轮完成后比较不同厚度"}]
    record = recipes.generate(tmp_path, doc, Client(payload))
    doc.update(experiment_contract=2, stage5_contract=2, experiment_plan_hash=record["plan_hash"], delivery_contract=1,
               review_completed=True, review_passed=False,
               report_readiness={"category": "CONDITIONAL", "issues": [], "review_status": "REVIEWED"})
    (tmp_path/"design_scheme.json").write_text(json.dumps(doc), encoding="utf-8")
    return doc, record


def test_unused_property_and_recommendation_validation_do_not_downgrade(tmp_path):
    doc, record = setup(tmp_path)
    before = copy.deepcopy(record)
    details = assessment(record, doc)
    assert details["blocking_parameters"] == details["blocking_sections"] == details["blocking_goals"] == []
    assert len(details["validation_notes"]) == 2
    assert blocking_pending_fields(record, doc) == []
    assert recipes.delivery_status(record, [DS], doc=doc) == ("PASSED", [])
    assert record == before and record["requirement_tests"][0]["status"] == "gap"
    assert record["pending_parameters"][DS]["value"] is None and not doc["review_passed"]
    text = recipes.render(record, doc)
    assert "本轮实验推荐已交付" in text and "后续独立测量" in text
    assert "附条件实验方案" not in text


@pytest.mark.parametrize("section", ["fabrication", "assembly", "testing", "criteria"])
def test_unknown_property_used_by_current_operations_remains_blocking(tmp_path, section):
    doc, record = setup(tmp_path)
    record["sections"][section].append("按{{pending:" + DS + "}}计算本轮操作参数。")
    assert DS in assessment(record, doc)["blocking_parameters"]
    assert recipes.delivery_status(record, doc=doc)[0] == "CONDITIONAL"


def test_intrinsic_note_cannot_hide_missing_operation_or_actual_conflict(tmp_path):
    doc, record = setup(tmp_path)
    record["pending_parameters"]["process.drying_time_h"] = make_pending("process.drying_time_h",
        {"value": None, "unit": "h", "meaning": "干燥时间", "reason": "尚未给定", "acquisition_method": "先明确工艺"})
    assert blocking_pending_fields(record, doc) == ["process.drying_time_h"]
    assert recipes.delivery_status(record, doc=doc)[0] == "CONDITIONAL"
    doc["scheme"]["electrolyte"] = "carbonate_ec"
    assert "electrolyte.material" in blocking_pending_fields(record, doc)
    record["pending_parameters"].pop("process.drying_time_h")
    assert recipes.delivery_status(record, doc=doc)[0] == "CONDITIONAL"


@pytest.mark.parametrize("change", [
    {"gap_kind": "delivery"}, {"gap_kind": "unverified"}, {"recommendation_refs": []},
    {"recommendation_refs": ["missing.field"]}, {"method": ""}, {"gap_kind": None},
])
def test_missing_or_invalid_recommendation_scope_still_downgrades(tmp_path, change):
    doc, record = setup(tmp_path)
    record["requirement_tests"][0].update(change)
    assert recipes.delivery_status(record, doc=doc)[0] == "CONDITIONAL"


@pytest.mark.parametrize("change", [{"verification_needed": None}, {"kind": "deliverable"}])
def test_unknown_validation_responsibility_or_missing_deliverable_not_exempt(tmp_path, change):
    doc, record = setup(tmp_path)
    doc["requirements"]["items"][0].update(change)
    assert validation_gap_errors(record["requirement_tests"][0], record, doc)
    assert assessment(record, doc)["blocking_goals"] == ["r1"]


def test_unrelated_unparsed_constraint_does_not_invalidate_clear_objective(tmp_path):
    doc, record = setup(tmp_path)
    doc["requirements"]["status"] = "partial"
    assert not validation_gap_errors(record["requirement_tests"][0], record, doc)
    assert assessment(record, doc)["blocking_goals"] == []
    doc["requirements"]["items"][0]["status"] = "ambiguous"
    assert validation_gap_errors(record["requirement_tests"][0], record, doc)


def test_pending_metadata_and_physical_parameter_value_remain_validated(tmp_path):
    doc, record = setup(tmp_path)
    record["pending_parameters"][DS]["record_hash"] = "corrupted"
    assert recipes.delivery_status(record, doc=doc)[0] == "CONDITIONAL"
    record["plan_hash"] = recipes.plan_hash(record)
    with pytest.raises(ValueError, match="指纹"):
        recipes.validate(record, doc, delivery=True)


@pytest.mark.parametrize("change", ["locked", "invalid_original", "removed_step", "repair_error"])
def test_validation_note_does_not_exempt_other_partial_failures(tmp_path, change):
    from auto_battery_research.util.scheme_ledger import digest
    doc, record = setup(tmp_path)
    if change == "locked":
        doc["ledger_view"]["parameters"][DS] = {"value": None, "unit": "m2/s", "locked": True}
    elif change == "invalid_original":
        row = record["pending_parameters"][DS]
        row["record"]["value"] = "未知字符串"
        row["record_hash"] = digest(row["record"])
    elif change == "removed_step":
        record["sections"]["assembly"] = []
    else:
        record["repair_error"] = "局部修复仍失败"
    assert recipes.delivery_status(record, doc=doc)[0] == "CONDITIONAL"


@pytest.mark.parametrize("exemption", [True, False])
def test_stage5_passes_validation_notes_but_preserves_physics_failures(tmp_path, monkeypatch, exemption):
    from auto_battery_research.checkers.stage5_checker import Stage5Checker
    from auto_battery_research.checkers.pinn_physics_checker import PINNPhysicsChecker
    from auto_battery_research.tools import workflow_actions as actions
    doc, record = setup(tmp_path)
    monkeypatch.setattr(actions, "_match_pinn_system_for_scheme", lambda scheme:
                        {"status": "NO_MATCH", "matched": False} if exemption else {"matched": True})
    monkeypatch.setattr(PINNPhysicsChecker, "do_check", lambda *a, **kw: (False, {"error": "必需仿真输入缺失"}))
    checker = Stage5Checker()
    checker.on_init(manager(tmp_path), {"id": 5}, {})
    passed, diag = checker.do_check()
    assert passed
    assert diag["observed"]["experiment_status"] == "PASSED"
    assert diag["observed"]["is_fallback"] is (not exemption)
    assert len(diag["observed"]["validation_notes"]) == 2


def test_stage5_executor_and_checker_use_same_delivery_boundary(tmp_path, monkeypatch):
    from auto_battery_research.util.stage5_execution import finalize_stage5
    from auto_battery_research.checkers.stage5_checker import Stage5Checker
    from auto_battery_research.tools import workflow_actions as actions
    doc, record = setup(tmp_path)
    monkeypatch.setattr(actions, "_match_pinn_system_for_scheme", lambda scheme: {"status": "NO_MATCH", "matched": False})
    monkeypatch.setattr(actions, "run_pinn_simulation", lambda **kw: pytest.fail("无匹配模型不运行仿真"))
    monkeypatch.setattr(recipes, "generate", lambda *a: record)
    mgr = manager(tmp_path)
    result = finalize_stage5(mgr, client=Client())
    assert result["success"]
    experiment = result["key_findings"]["experiment"]
    assert experiment["status"] == "PASSED" and experiment["pending_fields"] == []
    checker = Stage5Checker()
    checker.on_init(mgr, {"id": 5}, {})
    ok, diag = checker.do_check()
    assert ok and not diag["observed"]["is_fallback"]
    assert diag["observed"]["validation_notes"] == experiment["validation_notes"]
