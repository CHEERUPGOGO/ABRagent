"""Writer自报锁定不授予权限；已有用户锁定仍保护，原声明和恢复预算留档。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_design_structure import document
from auto_battery_research.util.writer_protocol import produce


def example(value=25, locked=True):
    data = dict(document(), version=2, parameters={"cathode.L_um": {
        "value": value, "unit": "um", "basis": "user_requirement", "source": "明确用户原句",
        "meaning": "单面干涂层厚度", "locked": locked}})
    data["sections"]["goals"]["body"] += "厚度{{param:cathode.L_um}}。"
    return data


@pytest.mark.parametrize("claim", [True, False])
def test_self_claimed_lock_is_ignored_and_raw_response_preserved(claim):
    data = example(locked=claim)
    raw = json.dumps(data, ensure_ascii=False)
    state = {"raw_response": raw, "repair_attempts": 1, "repair_history": [{"authority_version": 3, "error": "旧空补丁未能处理"}]}
    before = copy.deepcopy(data)
    result = produce(SimpleNamespace(available=False), "system", "user", {}, state, lambda: None)
    assert not result["fallback"], result
    assert result["numeric_parameters"]["cathode.L_um"]["locked"] is False
    assert result["numeric_parameters"]["cathode.L_um"]["value"] == 25
    assert state["raw_response"] == raw and json.loads(raw) == before
    assert state["ignored_writer_lock_claims"]["fields"] == [{"path": "cathode.L_um", "requested_locked": claim}]
    assert state["repair_attempts"] == 1 and len(state["repair_history"]) == 1


@pytest.mark.parametrize("claim", [True, False])
def test_same_value_does_not_unlock_current_user_record(claim):
    current = {"cathode.L_um": {"value": 25, "unit": "um", "locked": True, "source": "已有用户锁定"}}
    before = copy.deepcopy(current)
    state = {"raw_response": json.dumps(example(locked=claim))}
    result = produce(SimpleNamespace(available=False), "system", "user", current, state, lambda: None)
    assert not result["fallback"] and result["numeric_parameters"]["cathode.L_um"] == before["cathode.L_um"]
    assert current == before


@pytest.mark.parametrize("value", [None, 70])
@pytest.mark.parametrize("claim", [True, False])
def test_self_claim_cannot_override_locked_value_or_null(value, claim):
    current = {"cathode.L_um": {"value": value, "unit": "um", "locked": True}}
    result = produce(SimpleNamespace(available=False), "system", "user", current,
                     {"raw_response": json.dumps(example(locked=claim))}, lambda: None)
    assert result["fallback"] and "已有方案字段" in result["error"]
    assert current["cathode.L_um"]["value"] == value and current["cathode.L_um"]["locked"]


@pytest.mark.parametrize("extra", [{"locked": "true"}, {"approved": True}, {"review_status": "PASSED"}, {"actor": "user"}])
def test_other_permission_metadata_is_still_rejected(extra):
    data = example()
    data["parameters"]["cathode.L_um"].update(extra)
    result = produce(SimpleNamespace(available=False), "system", "user", {},
                     {"raw_response": json.dumps(data)}, lambda: None)
    assert result["fallback"] and "权限元数据" in result["error"]
