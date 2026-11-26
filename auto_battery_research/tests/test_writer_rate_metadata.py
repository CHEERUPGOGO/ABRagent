"""Writer用途标签兼容与明确倍率空单位补全；真实冲突、锁定和原响应仍保留。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_writer_protocol import example
from auto_battery_research.util import writer_protocol as protocol
from auto_battery_research.util.numeric_bindings import normalize_declaration


def parameter(value=1, unit="", **extra):
    return dict(value=value, unit=unit, basis="engineering_proposal", source="原始工程建议", **extra)


def document(parameters):
    data = example()
    data["parameters"] = copy.deepcopy(parameters)
    data["sections"]["goals"]["body"] += "；".join("参数{{param:" + path + "}}" for path in parameters)
    return data


@pytest.mark.parametrize("role", ["target", "lower_bound", "upper_bound", "experiment_max"])
def test_role_in_kind_is_preserved_without_guessing_fraction_type(role):
    data = document({"cathode.doping.limit": parameter(.03, quantity_kind=role, denominator="总阳离子")})
    original = copy.deepcopy(data)
    normalized = protocol.normalize(data)
    row = normalized["parameters"]["cathode.doping.limit"]
    assert row["quantity_role"] == role and row["quantity_kind"] == "ratio"
    assert row["value"] == .03 and row["denominator"] == "总阳离子" and row["source"] == "原始工程建议"
    assert data == original and protocol.normalize(normalized) == normalized
    result = protocol.produce(SimpleNamespace(available=False), "s", "u", {},
                              {"raw_response": json.dumps(data)}, lambda: None)
    assert not result["fallback"], result
    assert result["numeric_parameters"]["cathode.doping.limit"]["quantity_role"] == role


@pytest.mark.parametrize("row,expected", [
    (parameter(.03, quantity_kind="upper_bound"), "other"),
    (parameter(4.8, "V", quantity_kind="upper_bound"), "physical"),
    (parameter("候选方案", quantity_kind="target"), "text"),
])
def test_role_only_uses_explicit_unit_denominator_or_text(row, expected):
    normalized = normalize_declaration(row)
    assert normalized["quantity_kind"] == expected
    assert normalized["quantity_role"] == row["quantity_kind"]
    assert "denominator" not in normalized


def test_conflicting_role_is_rejected_in_normalization_and_metadata_patch():
    data = document({"cathode.doping.limit": parameter(.03, quantity_kind="upper_bound", quantity_role="target")})
    with pytest.raises(ValueError, match="用途标签.*冲突"):
        protocol.normalize(data)
    with pytest.raises(ValueError, match="用途标签.*冲突"):
        protocol.metadata_patch(data, {"base_hash": protocol.digest(data), "parameters": [
            {"path": "cathode.doping.limit", "metadata": {"quantity_kind": "ratio"}}]})


@pytest.mark.parametrize("path", ["condition.c_rate", "condition.C_rate"])
@pytest.mark.parametrize("empty", ["", None])
def test_explicit_c_rate_with_empty_unit_is_completed_without_model_call(path, empty):
    data = document({path: parameter(.5, empty, meaning="本方案循环测试倍率")})
    raw = json.dumps(data, ensure_ascii=False)
    state = {"raw_response": raw}
    client = SimpleNamespace(available=True, chat=lambda *a, **k: pytest.fail("明确倍率不用模型补单位"))
    result = protocol.produce(client, "s", "u", {}, state, lambda: None)
    assert not result["fallback"], result
    row = result["numeric_parameters"][path]
    assert row["value"] == .5 and row["unit"] == "C" and row["source"] == "原始工程建议"
    assert state["raw_response"] == raw and not state.get("repair_attempts")
    assert "0.5 C" in result["draft_answer"]
    assert protocol.produce(client, "s", "u", {}, state, lambda: None)["draft_answer"] == result["draft_answer"]


@pytest.mark.parametrize("row", [
    parameter(1), parameter(1, meaning="工程参数"),
    parameter(1, meaning="不是倍率"), parameter(1, meaning="倍率待确认"),
    parameter(1, meaning="非倍率参数"), parameter(1, meaning="not a C-rate"),
    parameter(1, meaning="C-rate unknown"),
    parameter(250, meaning="1 C 倍率参考电流密度"),
    parameter(250, meaning="倍率对应的比容量"),
    parameter(25, meaning="倍率测试温度"),
    parameter(1, "mA/g", meaning="循环倍率"),
    parameter(1, "1", meaning="循环倍率"),
    parameter(None, meaning="循环倍率"), parameter(True, meaning="循环倍率"),
    parameter("1", meaning="循环倍率"), parameter(0, meaning="循环倍率"),
    parameter(-1, meaning="循环倍率"), parameter(1, meaning="循环倍率", locked=True),
])
def test_missing_or_conflicting_semantics_cannot_complete_c_rate_unit(row):
    data = document({"condition.c_rate": row})
    assert not protocol.can_complete_c_rate_unit("condition.c_rate", row)
    normalized = protocol.normalize(data)["parameters"]["condition.c_rate"]
    assert normalized.get("unit") == row.get("unit")
    if row.get("unit") != "C":
        with pytest.raises(ValueError):
            protocol.metadata_patch(data, {"base_hash": protocol.digest(data), "parameters": [
                {"path": "condition.c_rate", "metadata": {"unit": "C"}}]})


@pytest.mark.parametrize("path", ["condition.current_density", "condition.C_rate_reference_current", "process.rate"])
def test_other_quantity_paths_do_not_gain_c_units_from_word_rate(path):
    data = document({path: parameter(1, meaning="循环倍率")})
    assert protocol.normalize(data)["parameters"][path]["unit"] == ""
    with pytest.raises(ValueError):
        protocol.metadata_patch(data, {"base_hash": protocol.digest(data), "parameters": [
            {"path": path, "metadata": {"unit": "C"}}]})


def test_metadata_patch_preserves_misplaced_role_and_allows_only_controlled_unit():
    data = document({"cathode.doping.limit": parameter(.03, quantity_kind="upper_bound", denominator="总阳离子"),
                     "condition.c_rate": parameter(1, meaning="本方案循环倍率")})
    before = copy.deepcopy(data)
    result = protocol.metadata_patch(data, {"base_hash": protocol.digest(data), "parameters": [
        {"path": "cathode.doping.limit", "metadata": {"quantity_kind": "ratio"}},
        {"path": "condition.c_rate", "metadata": {"unit": "C"}}]})
    assert result["parameters"]["cathode.doping.limit"]["quantity_role"] == "upper_bound"
    assert result["parameters"]["condition.c_rate"]["unit"] == "C"
    assert result["sections"] == data["sections"] and data == before


def test_existing_locked_missing_or_conflicting_unit_is_not_overwritten_by_writer():
    data = document({"condition.c_rate": parameter(1, meaning="循环测试倍率")})
    for unit in ("", "mA/g"):
        current = {"condition.c_rate": parameter(1, unit, locked=True)}
        before = copy.deepcopy(current)
        result = protocol.produce(SimpleNamespace(available=False), "s", "u", current,
                                  {"raw_response": json.dumps(data)}, lambda: None)
        assert result["fallback"] and current == before


def test_valid_locked_c_rate_can_be_referenced_after_proposal_unit_completion():
    current = {"condition.c_rate": parameter(1, "C", locked=True, source_quote="用户给定1 C")}
    data = document({"condition.c_rate": parameter(1, meaning="循环测试倍率")})
    result = protocol.produce(SimpleNamespace(available=False), "s", "u", current,
                              {"raw_response": json.dumps(data)}, lambda: None)
    assert not result["fallback"], result
    assert result["numeric_parameters"]["condition.c_rate"] == current["condition.c_rate"]


def test_saved_failed_writer_recovers_offline_without_resetting_history_or_budget():
    data = document({"cathode.doping.x_A_max": parameter(.03, quantity_kind="upper_bound", denominator="总阳离子"),
                     "cathode.doping.y_B_max": parameter(.02, quantity_kind="upper_bound", denominator="总阳离子"),
                     "cathode.doping.total_max": parameter(.05, quantity_kind="upper_bound", denominator="总阳离子"),
                     "condition.c_rate": parameter(1, meaning="循环测试倍率")})
    state = {"raw_response": json.dumps(data, ensure_ascii=False), "status": "INVALID", "repair_attempts": 3,
             "repair_history": [{"repair_raw_response": "旧调用原响应", "error": "旧失败"} for _ in range(3)]}
    history = copy.deepcopy(state["repair_history"])
    result = protocol.produce(SimpleNamespace(available=True, chat=lambda *a, **k: pytest.fail("不能重发旧调用")),
                              "s", "u", {}, state, lambda: None)
    assert not result["fallback"] and state["status"] == "READY"
    assert state["repair_attempts"] == 3 and state["repair_history"] == history
    assert state["raw_response"] == json.dumps(data, ensure_ascii=False)
    assert result["structured_sections"]["sections"] == data["sections"]


def test_rate_unit_completion_does_not_convert_explicit_celsius_or_percent():
    for unit in ("℃", "°C", "%", "mAh/g"):
        data = document({"condition.c_rate": parameter(1, unit, meaning="循环倍率")})
        normalized = protocol.normalize(data)
        assert normalized["parameters"]["condition.c_rate"]["unit"] == unit
        result = protocol.produce(SimpleNamespace(available=False), "s", "u", {},
                                  {"raw_response": json.dumps(data)}, lambda: None)
        assert result["fallback"]
