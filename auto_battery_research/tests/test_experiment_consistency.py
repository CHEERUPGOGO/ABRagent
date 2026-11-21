"""P0：主方案预检、待定操作、对照作用域与分组/测试局部恢复。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_experiment_design import setup_design, recipe_payload, slot_response
from auto_battery_research.tests.test_partial_recovery import two_answer_doc
from auto_battery_research.util import experiment_design as recipes
from auto_battery_research.util.experiment_consistency import preflight, stamp
from auto_battery_research.util.experiment_repair import repair_record
from auto_battery_research.util.research_experiments import fallback_links, recover_links, diagnose_links


def test_additive_not_mentioned_is_not_a_prohibition(tmp_path):
    doc, _ = setup_design(tmp_path)
    doc["target"] = "采用LiPF6/EC+DMC"
    doc["scheme"]["additives"] = ["FEC"]
    doc["ledger_view"]["parameters"]["electrolyte.additives"] = {"value": ["FEC"], "unit": ""}
    assert preflight(doc)["status"] == "READY"
    doc["reviewer_output"] = {"issues": ["electrolyte.additives与用户要求可能冲突，须核对是否禁止添加剂。"]}
    value = preflight(doc)
    assert value["pending_fields"] == ["electrolyte.additives"]
    assert value["issues"][0]["source"] == "review"
    assert doc["scheme"]["additives"] == ["FEC"]


def test_generation_receives_preflight_and_only_affected_methods_are_pending(tmp_path):
    doc, _ = setup_design(tmp_path)
    doc["ledger_view"]["parameters"]["electrolyte.additives"] = {"value": ["FEC"], "unit": ""}
    doc["reviewer_output"] = {"issues": ["electrolyte.additives存在声明冲突。"]}
    payload = recipe_payload()
    payload["parameters"]["electrolyte.material"] = {"ref": "electrolyte.material"}
    payload["sections"]["assembly"] = ["电解液采用{{electrolyte.material}}，添加剂先核对后再装配。"]
    calls = []
    def chat(system, user, **kwargs):
        calls.append(json.loads(user))
        return slot_response(payload, user, kwargs["trace_tag"])
    record = recipes.generate(tmp_path, doc, SimpleNamespace(available=True, chat=chat), "新实验")
    assert len(calls) == 1 and calls[0]["consistency"]["pending_fields"] == ["electrolyte.additives"]
    assert record["pending_operations"] == {"assembly:0": ["electrolyte.additives"]}
    assert record["sections"]["fabrication"] == payload["sections"]["fabrication"]
    body = recipes.render(record, doc)
    assert "条件待确认" in body and "NCM811" in body


def test_removed_pending_marker_or_new_conflict_cannot_pass_gate(tmp_path):
    doc, record = setup_design(tmp_path)
    doc["ledger_view"]["parameters"]["electrolyte.additives"] = {"value": ["FEC"], "unit": ""}
    doc["reviewer_output"] = {"issues": ["electrolyte.additives存在声明冲突。"]}
    with pytest.raises(ValueError, match="一致性核对"):
        recipes.validate(record, doc)
    stamp(record, doc)
    record["plan_hash"] = recipes.plan_hash(record)
    assert recipes.validate(record, doc)
    record["pending_operations"] = {}
    record["plan_hash"] = recipes.plan_hash(record)
    with pytest.raises(ValueError, match="待定操作"):
        recipes.validate(record, doc)


def test_new_literal_conflict_rejected_and_local_fallback_keeps_other_sections(tmp_path):
    doc, record = setup_design(tmp_path)
    record["sections"]["fabrication"] = ["正极厚度{{cathode.L_um}}=99，之后涂布。"]
    record["plan_hash"] = recipes.plan_hash(record)
    with pytest.raises(ValueError, match="引用值"):
        recipes.validate(record, doc)
    unaffected = copy.deepcopy(record["sections"]["testing"])
    fixed = repair_record(record, doc, None, tmp_path, "局部修复")
    assert "尚未定案" in fixed["sections"]["fabrication"][0]
    assert fixed["sections"]["testing"] == unaffected
    assert fixed["parameters"]["cathode.L_um"] == {"ref": "cathode.L_um"}


def test_identity_parameter_cannot_be_added_by_experiment_namespace(tmp_path):
    doc, record = setup_design(tmp_path)
    record["parameters"]["fabrication.new_additive"] = {"value": "FEC", "unit": "", "basis": "engineering_proposal",
        "role": "recipe", "canonical_path": "electrolyte.additives"}
    record["plan_hash"] = recipes.plan_hash(record)
    with pytest.raises(ValueError, match="主方案定案"):
        recipes.validate(record, doc)
    record["parameters"].pop("fabrication.new_additive")
    record["parameters"]["cathode.L_um"] = {"ref": "anode.material"}
    record["plan_hash"] = recipes.plan_hash(record)
    with pytest.raises(ValueError, match="另一项"):
        recipes.validate(record, doc)


def test_control_parameter_must_bind_group_and_cannot_enter_main(tmp_path):
    doc, record = setup_design(tmp_path)
    record["groups"] = [{"id": "G0", "scope": "control", "changes": {"cathode.L_um": {"value": 40, "unit": "um", "reason": "厚度对照"}}}]
    record["parameters"]["fabrication.control_thickness"] = {"value": 40, "unit": "um", "basis": "engineering_proposal",
        "role": "processing", "scope": "control", "group_id": "G0", "canonical_path": "cathode.L_um"}
    record["sections"]["fabrication"] = ["G0对照使用{{fabrication.control_thickness}}厚度。"]
    record["plan_hash"] = recipes.plan_hash(record)
    assert recipes.validate(record, doc)
    record["sections"]["fabrication"] = ["主组使用{{fabrication.control_thickness}}厚度。"]
    record["plan_hash"] = recipes.plan_hash(record)
    with pytest.raises(ValueError, match="对照参数"):
        recipes.validate(record, doc)
    record["parameters"]["fabrication.control_thickness"]["group_id"] = "unknown"
    record["plan_hash"] = recipes.plan_hash(record)
    with pytest.raises(ValueError, match="group_id"):
        recipes.validate(record, doc)


def test_bad_group_and_missing_test_do_not_erase_valid_records():
    doc = two_answer_doc()
    record = {"parameters": {}}
    fallback_links(record, doc)
    good = {"id": "G0", "name": "配方对照", "scope": "control", "parameter_refs": [],
            "changes": {"cathode.material": {"value": "对照材料", "unit": "", "reason": "比较配方"}}}
    record["groups"] += [copy.deepcopy(good), {"id": "G2", "scope": "control", "changes": {}}]
    good_test = dict(record["requirement_tests"][0], status="planned", method="同批循环", conditions="固定条件",
                     baseline="基准样品", criterion="按用户目标判断", next_step="复测")
    record["requirement_tests"] = [copy.deepcopy(good_test)]
    recover_links(record, doc)
    assert good in record["groups"] and len(record["groups"]) == 2
    assert record["pending_groups"][0]["record"]["id"] == "G2"
    assert record["requirement_tests"][0] == good_test and record["requirement_tests"][1]["status"] == "gap"
    assert not diagnose_links(record, doc)


def test_unknown_verification_cannot_be_dismissed_as_not_applicable():
    from auto_battery_research.util.research_contract import fallback_requirements
    doc = {"requirements": fallback_requirements("提高保持率")}
    record = {"parameters": {}}
    fallback_links(record, doc)
    record["requirement_tests"][0]["status"] = "not_applicable"
    assert any("未知验证责任" in e["reason"] for e in diagnose_links(record, doc))


def test_stage5_review_concerns_do_not_block_consistent_experiment_parameters(tmp_path, monkeypatch):
    from auto_battery_research.tests.test_stage5_execution import manager
    from auto_battery_research.tests.test_experiment_design import scripted_client
    from auto_battery_research.util.stage5_execution import finalize_stage5
    from auto_battery_research.checkers.pinn_physics_checker import PINNPhysicsChecker
    from auto_battery_research.checkers.stage5_checker import Stage5Checker
    doc, _ = setup_design(tmp_path)
    doc["delivery_contract"] = 1
    doc["report_readiness"] = {"category": "CONDITIONAL", "issues": ["electrolyte.material存在声明冲突。"]}
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(PINNPhysicsChecker, "do_check", lambda *a, **k: (True, {}))
    mgr = manager(tmp_path)
    outcome = finalize_stage5(mgr, design_only=True, client=scripted_client())
    assert outcome["success"] and outcome["key_findings"]["experiment"]["status"] == "PASSED"
    checker = Stage5Checker()
    checker.on_init(mgr, {"id": 5}, mgr.config)
    passed, diagnostic = checker.do_check()
    assert passed and not diagnostic.get("observed", {}).get("is_fallback", False)
    saved = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    saved.pop("delivery_contract")
    (tmp_path / "design_scheme.json").write_text(json.dumps(saved), encoding="utf-8")
    assert checker.do_check()[0]
