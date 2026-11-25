"""歧义旧值不猜转、对照值全路径共用校验的离线回归。"""
import copy
import json

import pytest

from auto_battery_research.tests.test_experiment_generation import Client, design, checkpoint
from auto_battery_research.tests.test_experiment_design import recipe_payload
from auto_battery_research.util import experiment_design as recipes
from auto_battery_research.util.experiment_values import normalize_legacy_collection, separate_pending, control_value_issues
from auto_battery_research.util.experiment_slots import validate_control
from auto_battery_research.util.experiment_repair import diagnose, repair_record
from auto_battery_research.util.experiment_consistency import stamp


@pytest.mark.parametrize("value,unit", [
    ("1/2 h", "h"), ("1 / 2 h", "h"), ("1,000 rpm", "rpm"), ("1,000,000 rpm", "rpm"),
    ("1,5 h", "h"), ("1, 2 h", "h"), ("0.1/0.2/0.5/1/2/5 C", "C"),
    ("1、2 h", "h"), ("[1,000] rpm", "rpm"), ("[1/2, 2] h", "h"),
])
def test_ambiguous_separators_never_change_value_or_unit(value, unit):
    key = "process.operation"
    row = {"value": value, "unit": unit, "basis": "engineering_proposal", "source": "旧原稿", "meaning": "操作条件", "role": "processing"}
    original = copy.deepcopy(row)
    assert normalize_legacy_collection(key, row) == original
    assert row == original
    record, fields = separate_pending({"parameters": {key: row}})
    assert fields == [key] and not record["parameters"]
    assert record["pending_parameters"][key]["record"] == original
    assert not record.get("parameter_value_history")


@pytest.mark.parametrize("text,extra,expected", [
    ("[0.1, 0.5, 1, 2] C", {}, [.1, .5, 1, 2]),
    ("[0.1, 0.5, 1, 2] C", {"value_type": "sequence"}, [.1, .5, 1, 2]),
    ("[0.1, 0.5, 1, 2]", {}, [.1, .5, 1, 2]),
])
def test_explicit_json_sequence_keeps_values_and_archives_original(text, extra, expected):
    key = "condition.rate_test"
    original = {"value": text, "unit": "C", "basis": "engineering_proposal", "source": "旧声明", **extra}
    record, fields = separate_pending({"parameters": {key: original}})
    assert fields == [] and record["parameters"][key]["value"] == expected
    assert record["parameters"][key]["unit"] == "C"
    assert record["parameter_value_history"][0]["original"] == original
    assert separate_pending(record) == (record, [])


@pytest.mark.parametrize("extra", [{}, {"value_type": "sequence"}])
def test_sequence_type_alone_does_not_authorize_guessing_separator(extra):
    row = {"value": "1,000 rpm", "unit": "rpm", **extra}
    assert normalize_legacy_collection("process.speed", row) == row


def control_doc(tmp_path, requirements=False):
    doc = design(tmp_path, requirements=requirements)
    doc["ledger_view"]["parameters"]["process.precalcination_time_h"] = {"value": 5, "unit": "h", "basis": "engineering_proposal", "source": "已定条件"}
    return doc


def control(value=2, **extra):
    return {"name": "预烧时间对照", "changes": {"process.precalcination_time_h": {"value": value, "unit": "h", "reason": "比较时间条件", **extra}}}


@pytest.mark.parametrize("value", ["待确定", "未知", "", "TBD", "2", "1/2 h", "1,000 h", None, True, -1])
def test_bad_control_values_fail_entry_and_delivery_even_without_requirements(tmp_path, value):
    doc = control_doc(tmp_path)
    with pytest.raises(ValueError):
        validate_control(control(value), doc, 1)
    record = recipes.generate(tmp_path, doc, Client())
    record["groups"].append({"id": "G2", "scope": "control", "parameter_refs": [], **control(value)})
    record["plan_hash"] = recipes.plan_hash(record)
    for delivery in (False, True):
        with pytest.raises(ValueError, match="对照"):
            recipes.validate(record, doc, delivery=delivery)
    assert recipes.delivery_status(record, doc=doc)[0] == "CONDITIONAL"
    assert recipes.delivery_status(record)[0] == "CONDITIONAL"


@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_nonfinite_control_values_fail_before_storage(tmp_path, value):
    with pytest.raises(ValueError):
        validate_control(control(value), control_doc(tmp_path), 1)


@pytest.mark.parametrize("extra", [{"unit": "min"}, {"locked": False}, {"ref": "process.precalcination_time_h"}])
def test_control_units_and_metadata_cannot_bypass_shared_validation(tmp_path, extra):
    doc = control_doc(tmp_path)
    with pytest.raises(ValueError):
        validate_control(control(2, **extra), doc, 1)
    result = control_value_issues({"groups": [{"scope": "control", **control(2, **extra)}]}, recipes.effective_data(doc)["parameters"])
    assert result and result[0]["path"] == ["groups", 0]


def test_valid_text_zero_and_numeric_control_values_remain_valid(tmp_path):
    doc = control_doc(tmp_path)
    doc["ledger_view"]["parameters"].update({
        "cathode.doping.site_A_element": {"value": "Na", "unit": ""},
        "cathode.doping.x_A": {"value": .01, "unit": ""}})
    value = control(2)
    value["changes"].update({"cathode.doping.site_A_element": {"value": "无（未掺A位）", "unit": "", "reason": "单变量对照"},
                             "cathode.doping.x_A": {"value": 0, "unit": "", "reason": "无掺杂基线"}})
    row = validate_control(value, doc, 1)
    assert row["changes"] == value["changes"]
    assert not control_value_issues({"groups": [row]}, recipes.effective_data(doc)["parameters"])


@pytest.mark.parametrize("requirements", [False, True])
def test_local_recovery_quarantines_only_bad_group_and_its_explicit_parameter(tmp_path, requirements):
    doc = control_doc(tmp_path, requirements)
    payload = recipe_payload()
    if requirements:
        payload["parameters"]["cathode.active_fraction"]["canonical_path"] = "cathode.active_fraction"
    record = recipes.generate(tmp_path, doc, Client(payload))
    good = validate_control(control(3), doc, 2)
    bad = {"id": "G2", "scope": "control", "parameter_refs": [], **control("待确定")}
    record["groups"] += [bad, good]
    record["parameters"]["process.control_time_h"] = {"value": 2, "unit": "h", "basis": "engineering_proposal", "source": "对照提案",
        "meaning": "对照预烧时间", "role": "processing", "scope": "control", "group_id": "G2", "canonical_path": "process.precalcination_time_h"}
    record["sections"]["fabrication"].append("G2预烧时间为{{process.control_time_h}}，其他条件不变。")
    before = copy.deepcopy(record)
    errors = diagnose(record, doc)
    assert record == before
    assert any(e["path"] == ["groups", 1] for e in errors)
    assert not any(e["path"][0] == "sections" and e["path"][1] == "testing" for e in errors)
    fixed = repair_record(record, doc, None, tmp_path, "offline-control")
    assert fixed["sections"]["testing"] == before["sections"]["testing"]
    assert fixed["sections"]["fabrication"][:-1] == before["sections"]["fabrication"][:-1]
    assert fixed["groups"] == [before["groups"][0], good]
    assert any(entry["record"] == bad for entry in fixed["pending_groups"])
    assert "process.control_time_h" not in fixed["parameters"]
    assert fixed["pending_parameters"]["process.control_time_h"]["record"] == before["parameters"]["process.control_time_h"]
    assert fixed["parameters"].get("process.precalcination_time_h") == before["parameters"].get("process.precalcination_time_h")
    recipes.validate(fixed, doc, delivery=True)


def test_cached_plan_does_not_bypass_control_validation(tmp_path):
    doc = control_doc(tmp_path)
    client = Client()
    record = recipes.generate(tmp_path, doc, client)
    record["groups"].append({"id": "G2", "scope": "control", "parameter_refs": [], **control("待确定")})
    record["plan_hash"] = recipes.plan_hash(record)
    (tmp_path / recipes.FILENAME).write_text(json.dumps(record), encoding="utf-8")
    before = (tmp_path / recipes.FILENAME).read_bytes()
    with pytest.raises(ValueError, match="对照"):
        recipes.generate(tmp_path, doc, client)
    assert len(client.calls) == 2 and (tmp_path / recipes.FILENAME).read_bytes() == before




def test_new_generation_retries_only_bad_control_and_retains_good_group(tmp_path):
    doc = control_doc(tmp_path)
    payload = recipe_payload()
    payload["groups"] = [{"id": "G1", "scope": "main", "name": "??", "changes": {}, "parameter_refs": []},
                         {"id": "G2", "scope": "control", "parameter_refs": [], **control("???")},
                         {"id": "G3", "scope": "control", "parameter_refs": [], **control(3)}]
    client = Client(payload)
    record = recipes.generate(tmp_path, doc, client)
    assert [tag for tag, _ in client.calls] == ["experiment_design", "experiment_repair", "experiment_alignment_review"]
    assert [g["id"] for g in record["groups"]] == ["G1", "G3"]
    assert record["pending_groups"][0]["record"]["id"] == "G2"
    assert record["sections"] == payload["sections"]
    assert recipes.delivery_status(record, doc=doc)[0] == "CONDITIONAL"


def test_real_stage5_checker_rejects_bad_control_before_delivery(tmp_path, monkeypatch):
    from auto_battery_research.tests.test_stage5_execution import manager
    from auto_battery_research.checkers.stage5_checker import Stage5Checker
    from auto_battery_research.tools import workflow_actions
    from auto_battery_research.util import scheme_ledger
    doc = control_doc(tmp_path)
    doc["scheme_parameters"] = {"process.precalcination_time_h": copy.deepcopy(doc["ledger_view"]["parameters"]["process.precalcination_time_h"])}
    scheme_ledger.commit_design(tmp_path, doc)
    record = recipes.generate(tmp_path, doc, Client())
    record["groups"].append({"id": "G2", "scope": "control", "parameter_refs": [], **control("待确定")})
    record["plan_hash"] = recipes.plan_hash(record)
    doc.update(experiment_contract=2, experiment_plan_hash=record["plan_hash"], delivery_contract=1)
    (tmp_path / "experiment_plan.json").write_text(json.dumps(record), encoding="utf-8")
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(workflow_actions, "_match_pinn_system_for_scheme", lambda s: {"status": "NO_MATCH", "matched": False})
    checker = Stage5Checker()
    checker.on_init(manager(tmp_path), {"id": 5}, {})
    passed, diagnostic = checker.do_check()
    assert not passed and "对照" in diagnostic["error"]
