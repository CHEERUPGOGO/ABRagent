"""科研限制由模型表达，程序保留值、引用、原始状态及审核边界。"""
import copy
import json

import pytest

from auto_battery_research.tests.test_experiment_design import recipe_payload
from auto_battery_research.tests.test_experiment_generation import Client, checkpoint, design
from auto_battery_research.util import experiment_design as recipes
from auto_battery_research.util import experiment_review as review
from auto_battery_research.util.experiment_consistency import stamp, validate_snapshot
from auto_battery_research.util.experiment_values import separate_pending
from auto_battery_research.util.scheme_ledger import digest


FIELD = "testing.oxygen_release_limit_pct"


def example(tmp_path, state="planned", explicit=True):
    doc = design(tmp_path, requirements=True)
    doc["requirements"]["items"][0].update(description="候选组合排行", verification_needed=True)
    payload = recipe_payload()
    payload["parameters"]["cathode.active_fraction"]["canonical_path"] = "cathode.active_fraction"
    payload["parameters"][FIELD] = {
        "value": None, "unit": "%", "basis": "engineering_proposal", "source": "当前缺少预实验结果",
        "meaning": "氧释放下降门槛", "role": "testing", "expected_type": "number",
        "reason": "须先取得基线波动数据", "acquisition_method": "用同批母体的重复测试确定判据",
    }
    reference = "{{pending:" + FIELD + "}}" if explicit else "氧释放下降门槛"
    payload["requirement_tests"] = [{
        "requirement_id": "r1", "status": state, "method": "比较同批候选的结构与氧释放表现，形成暂定排序",
        "conditions": reference + "尚未确定，可先采集基线数据",
        "baseline": "同批未掺杂母体", "criterion": "确定门槛后再判断改善是否合格",
        "reason": "现阶段可进行初步比较，最终排行须等待判据和实验数据",
        "next_step": "先测母体波动，确认判据后再确认最终排名",
    }, {
        "requirement_id": "r2", "status": "planned", "method": "用XRD比较结构",
        "conditions": "相同扫描设置", "baseline": "同批母体", "criterion": "比较物相组成",
        "reason": "先取得可重复的结构数据", "next_step": "核对批次一致性",
    }]
    payload["sections"]["testing"].append("先测母体波动；" + reference + "取得后再用于最终排行。")
    return doc, payload


@pytest.mark.parametrize("state", ["planned", "gap", "not_applicable"])
@pytest.mark.parametrize("explicit", [True, False])
def test_model_states_and_limitations_survive_generation_and_display(tmp_path, state, explicit):
    doc, payload = example(tmp_path, state, explicit)
    if state == "not_applicable":
        payload["requirement_tests"][0].update(method="仅保留文献比较", conditions="本轮不开展最终排序",
            reason="本轮仅采集基线，最终排序不适用于本轮阶段", next_step="后续有数据再安排排序")
    before = copy.deepcopy(payload["requirement_tests"])
    client = Client(payload)
    record = recipes.generate(tmp_path, doc, client)
    assert record["requirement_tests"] == before
    assert record["pending_parameters"][FIELD]["value"] is None
    assert FIELD not in record["parameters"] and FIELD not in record["bindings"]
    assert "pending_dependencies" not in record and not record["pending_operations"]
    assert not record.get("generation_pending")
    assert [tag for tag, _ in client.calls] == ["experiment_design"]
    assert review.status(record, doc)[0] == "LEGACY"
    assert recipes.delivery_status(record, doc=doc)[0] == "CONDITIONAL"
    body = recipes.render(record, doc)
    assert before[0]["method"] in body and before[0]["reason"] in body and before[0]["next_step"] in body
    assert "各目标的方法与待解决事项" in body
    assert "方法安排不代表条件齐全或验证完成" in body
    assert "待确认参数及获取方法" in body and "用同批母体的重复测试确定判据" in body
    assert "相关操作须在补齐条件后执行" not in body and "{{" not in body


def test_value_separation_changes_references_but_not_goal_judgment(tmp_path):
    doc, payload = example(tmp_path)
    original = recipes.generate(tmp_path, doc, Client(payload))
    original["parameters"][FIELD] = dict(payload["parameters"][FIELD], value="待确定")
    original.pop("pending_parameters")
    row = original["requirement_tests"][0]
    row["conditions"] = "{{" + FIELD + "}}待确定，仍可先做基线实验"
    old_test = copy.deepcopy(row)
    updated, changed = separate_pending(original)
    assert changed == [FIELD] and original["requirement_tests"][0] == old_test
    assert updated["requirement_tests"][0] == dict(old_test, conditions="{{pending:" + FIELD + "}}待确定，仍可先做基线实验")
    assert old_test in updated["pending_tests"]
    assert review.status(updated, doc)[0] == "LEGACY"


@pytest.mark.parametrize("reference", ["{{pending:testing.missing}}", "{{" + FIELD + "}}"])
def test_unknown_and_active_references_to_missing_values_still_fail(tmp_path, reference):
    doc, payload = example(tmp_path)
    record = recipes.generate(tmp_path, doc, Client(payload))
    record["requirement_tests"][0]["conditions"] = reference
    record["plan_hash"] = recipes.plan_hash(record)
    with pytest.raises(ValueError, match="待定参数"):
        recipes.validate(record, doc, delivery=True)






def test_cached_legacy_gaps_and_review_are_not_rewritten_or_upgraded(tmp_path):
    doc, payload = example(tmp_path, state="gap")
    client = Client(payload)
    record = recipes.generate(tmp_path, doc, client)
    record["parameter_value_contract"] = 1
    record["pending_dependencies"] = {"test:r1": [FIELD], "testing:1": [FIELD]}
    record["pending_operations"] = {"testing:1": [FIELD]}
    # 模拟旧科学否决，指纹仍绑定同一内容；兼容读取不得补授通过。
    record["generation_contract"] = 1
    record["scientific_review"] = {"status": "UNREVIEWED", "subject_hash": digest(review.subject(record, doc)),
                                   "checks": [], "issues": [], "reason": "旧审核未完成"}
    record["plan_hash"] = recipes.plan_hash(record)
    path = tmp_path / recipes.FILENAME
    path.write_text(json.dumps(record), encoding="utf-8")
    raw = path.read_bytes()
    loaded = recipes.generate(tmp_path, doc, client)
    assert loaded == record and path.read_bytes() == raw
    assert len(client.calls) == 1
    assert review.status(loaded, doc)[0] == "UNREVIEWED"
    assert recipes.delivery_status(loaded, doc=doc)[0] == "CONDITIONAL"
    assert loaded["requirement_tests"][0]["status"] == "gap"


def test_legacy_missing_value_compatibility_cannot_hide_actual_parameter_conflict(tmp_path):
    doc, payload = example(tmp_path)
    record = recipes.generate(tmp_path, doc, Client(payload))
    doc["scheme"]["electrolyte"] = "carbonate_ec"
    stamp(record, doc)
    conflicts = copy.deepcopy(record["pending_operations"])
    assert conflicts
    record["parameter_value_contract"] = 1
    record["pending_operations"].setdefault("testing:1", []).append(FIELD)
    validate_snapshot(record, doc)
    record["pending_operations"] = {"testing:1": [FIELD]}
    with pytest.raises(ValueError, match="一致性"):
        validate_snapshot(record, doc)


def test_experiment_review_notice_does_not_become_a_new_input_conflict(tmp_path):
    doc, payload = example(tmp_path)
    record = recipes.generate(tmp_path, doc, Client(payload))
    original_fingerprint = recipes.input_fingerprint(doc)
    original_snapshot = copy.deepcopy(record["consistency"])
    doc["report_readiness"] = {"category": "CONDITIONAL", "issues": [
        "实验交付限制：正文可能存在冲突：cathode.L_um需核对，process.route_main需说明对照范围"]}
    assert recipes.input_fingerprint(doc) == original_fingerprint
    validate_snapshot(record, doc)
    assert record["consistency"] == original_snapshot
    assert "条件待确认。" not in recipes.render(record, doc)
    # 独立Stage 4意见仍保护；不能因忽略实验输出汇总而隐藏输入冲突。
    doc["reviewer_output"] = {"issues": ["cathode.L_um存在声明冲突，尚待复核"]}
    with pytest.raises(ValueError, match="一致性"):
        validate_snapshot(record, doc)
