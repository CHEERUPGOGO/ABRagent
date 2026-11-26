"""协议等价写法、三轮局部修复及跨轮检查点的离线回归。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_writer_protocol import example
from auto_battery_research.util import writer_protocol as protocol
from auto_battery_research.util.numeric_bindings import declarations
from auto_battery_research.util.experiment_values import validate_control_changes


def parameter(value, unit="", **extra):
    return dict(value=value, unit=unit, basis="engineering_proposal", source="工程建议，待验证", **extra)


def staged_document():
    data = example()
    data["parameters"] = {
        "synthesis.temperature": parameter(900, "℃"),
        "process.duration": parameter(6, "h", quantity_kind="duration"),
    }
    data["sections"]["goals"]["body"] += "温度{{param:synthesis.temperature}}，保温{{param:process.duration}}。"
    return data


def test_fraction_text_and_user_requirement_do_not_need_protocol_call():
    data = example()
    data["parameters"] = {
        "cathode.porosity": parameter(.3, quantity_kind="fraction", denominator="极片表观体积"),
        "process.route": parameter("高温固相法"),
        "condition.retention_user_floor": dict(parameter(.74, quantity_kind="ratio"),
                                               basis="user_requirement", quantity_role="lower_bound"),
    }
    data["sections"]["goals"]["body"] += (
        "孔隙率{{param:cathode.porosity}}；路线{{param:process.route}}；"
        "用户保持率下限{{param:condition.retention_user_floor}}，须实验核验。")
    raw = json.dumps(data, ensure_ascii=False)
    current = {"requirements.original_request": dict(parameter("1 C 保持率显著 >74%"), locked=True)}
    result = protocol.produce(SimpleNamespace(available=False), "s", "u", current,
                              {"raw_response": raw}, lambda: None)
    assert not result["fallback"], result
    params = result["numeric_parameters"]
    assert params["cathode.porosity"]["quantity_kind"] == "ratio"
    assert params["cathode.porosity"]["denominator"] == "极片表观体积"
    assert params["process.route"]["quantity_kind"] == "text"
    assert params["condition.retention_user_floor"]["basis"] == "user_requirement"
    assert params["condition.retention_user_floor"]["locked"] is False
    assert result["raw_response"] == raw


def test_repeated_current_value_preserves_current_source_and_lock():
    original = dict(parameter(70, "um"), locked=True, source="用户原始指定")
    proposed = {"parameters": {"cathode.L_um": parameter(70, "um")}}
    assert declarations(proposed, {"cathode.L_um": original})["cathode.L_um"] == original
    proposed["parameters"]["cathode.L_um"]["value"] = 71
    with pytest.raises(ValueError, match="已有方案字段"):
        declarations(proposed, {"cathode.L_um": original})


def test_same_number_cannot_relabel_current_sample_or_purpose():
    current = {"process.duration": parameter(6, "h", purpose="主路线")}
    data = {"parameters": {"process.duration": parameter(6, "h", purpose="备选路线")}}
    with pytest.raises(ValueError, match="已有方案字段"):
        declarations(data, current)


def test_three_protocol_rounds_keep_successful_changes_and_replay_offline():
    data = staged_document()
    raw = json.dumps(data, ensure_ascii=False)
    state, requests, saved = {"raw_response": raw}, [], []
    def chat(system, user, **kwargs):
        assert kwargs["trace_tag"] == "writer_protocol_repair"
        request = json.loads(user)
        requests.append(request)
        assert saved[-1]["repair_attempts"] == len(requests)
        if len(requests) == 1:
            return "协议响应JSON未闭合{"
        changes = [{"path": ["parameters", "synthesis.temperature"],
                    "new_path": ["process", "temperature"]}] if len(requests) == 2 else [
            {"path": ["parameters", "process.duration"], "metadata": {"quantity_kind": "physical"}}]
        return json.dumps({"base_hash": request["base_hash"], "parameters": changes})
    client = SimpleNamespace(available=True, chat=chat)
    result = protocol.produce(client, "s", "u", {}, state, lambda: saved.append(copy.deepcopy(state)))
    assert not result["fallback"], result
    assert state["repair_attempts"] == 3 and len(state["repair_history"]) == 3
    assert "JSON" in requests[1]["errors"][-1]["reason"] or requests[1]["errors"][-1]["code"] == "PROTOCOL"
    assert "process.temperature" in requests[2]["document"]["parameters"]
    assert result["numeric_parameters"]["process.temperature"]["value"] == 900
    assert result["numeric_parameters"]["process.duration"]["value"] == 6
    assert state["raw_response"] == raw and not hasattr(client, "single_attempt")
    restored = json.loads(json.dumps(state))
    again = protocol.produce(SimpleNamespace(available=False), "s", "u", {}, restored, lambda: None)
    assert again["draft_answer"] == result["draft_answer"] and restored["repair_attempts"] == 3


def test_three_failed_protocol_rounds_do_not_reset_on_resume():
    calls, state = [], {"raw_response": json.dumps(staged_document())}
    def chat(*args, **kwargs):
        calls.append(kwargs["trace_tag"])
        return ["非JSON响应", "[]", "null"][len(calls) - 1]
    client = SimpleNamespace(available=True, chat=chat)
    assert protocol.produce(client, "s", "u", {}, state, lambda: None, budget={"protocol_rounds": 3})["fallback"]
    assert len(calls) == 3 and state["repair_attempts"] == 3
    assert protocol.produce(client, "s", "u", {}, json.loads(json.dumps(state)), lambda: None,
                            budget={"protocol_rounds": 3})["fallback"]
    assert len(calls) == 3


def test_later_patch_cannot_roll_back_to_original_document_hash():
    data = staged_document()
    state, requests = {"raw_response": json.dumps(data)}, []
    def chat(system, user, **kwargs):
        request = json.loads(user)
        requests.append(request)
        if len(requests) == 1:
            return json.dumps({"base_hash": request["base_hash"], "parameters": [
                {"path": "synthesis.temperature", "new_path": "process.temperature"}]})
        if len(requests) == 3:
            assert "process.temperature" in request["document"]["parameters"]
            assert "synthesis.temperature" not in request["document"]["parameters"]
        return json.dumps({"base_hash": protocol.digest(data) if len(requests) == 2 else request["base_hash"],
            "parameters": [{"path": "process.duration", "metadata": {"quantity_kind": "physical"}}]})
    result = protocol.produce(SimpleNamespace(available=True, chat=chat), "s", "u", {}, state, lambda: None)
    assert not result["fallback"], result
    assert state["repair_attempts"] == 3
    assert "基准不一致" in state["repair_history"][1]["error"]


def test_interrupted_second_round_keeps_first_patch_without_resending():
    state, calls = {"raw_response": json.dumps(staged_document())}, []
    def chat(system, user, **kwargs):
        calls.append(kwargs["trace_tag"])
        if len(calls) == 2:
            raise RuntimeError("连接中断")
        request = json.loads(user)
        return json.dumps({"base_hash": request["base_hash"], "parameters": [
            {"path": "synthesis.temperature", "new_path": "process.temperature"}]})
    client = SimpleNamespace(available=True, chat=chat)
    assert protocol.produce(client, "s", "u", {}, state, lambda: None)["fallback"]
    assert state["repair_attempts"] == 2 and state["repair_history"][0]["accepted_patch"]
    restored = json.loads(json.dumps(state))
    assert protocol.produce(client, "s", "u", {}, restored, lambda: None)["fallback"]
    assert len(calls) == 2


@pytest.mark.parametrize("current_unit,change_unit", [(None, ""), ("", None), (None, None)])
def test_unitless_material_controls_accept_null_and_empty_string(current_unit, change_unit):
    current = {"cathode.base_formula": {"value": "Li1.2Mn0.6Ni0.2O2", "unit": current_unit}}
    changes = {"cathode.base_formula": {"value": "Li1.2Mn0.6Ni0.2O2", "unit": change_unit,
                                        "reason": "母体空白对照"}}
    before = copy.deepcopy(changes)
    validate_control_changes(changes, current)
    assert changes == before
