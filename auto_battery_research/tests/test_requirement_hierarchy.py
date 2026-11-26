"""层级不重复计数、固定条件锁定及连接词恢复的行为回归。"""
import copy

import pytest

from auto_battery_research.tests.test_research_contract import requirement
from auto_battery_research.util.research_contract import attach_requirements, bindings, normalize_requirements
from auto_battery_research.util.requirement_structure import ordered_items


def raw_contract(goal, rows):
    return {"split_version": 2, "hierarchy_version": 1, "task_type": "experiment", "scheme_required": False,
            "task": {"description": "研究材料", "source_quotes": ["研究材料"]},
            "groups": [{"id": "science", "title": "循环稳定性", "parent_id": None, "source_quotes": ["研究材料"]},
                       {"id": "fixed", "title": "固定条件", "parent_id": None, "source_quotes": ["研究材料"]}],
            "items": rows}


def test_group_is_not_an_extra_answer_and_voltage_window_is_one_item(tmp_path):
    from auto_battery_research.util import scheme_ledger
    from auto_battery_research.util.research_experiments import required_tests
    goal = "研究材料；评价容量保持与电压衰减；窗口2.0–4.8 V；1 C＝250 mA/g。"
    rows = [dict(requirement("评价容量保持与电压衰减"), id="retention", description="评价容量保持", group_id="science"),
            dict(requirement("评价容量保持与电压衰减"), id="fade", description="评价电压衰减", group_id="science"),
            dict(requirement("窗口2.0–4.8 V", "constraint"), id="window", group_id="fixed", verification_needed=False,
                 bindings=[{"path": "condition.v_min", "value": 2, "unit": "V"},
                           {"path": "condition.v_max", "value": 4.8, "unit": "V"}]),
            dict(requirement("1 C＝250 mA/g", "constraint"), id="rate", group_id="fixed", verification_needed=False,
                 quantity={"value": 250, "unit": "mA/g", "operator": "="},
                 bindings=[{"path": "condition.C_rate_reference_current", "value": 250, "unit": "mA/g"}])]
    req = normalize_requirements(raw_contract(goal, rows), goal)
    assert len(req["items"]) == 4 and len(required_tests({"requirements": req})) == 2
    doc = {"target": goal, "requirements": req, "scheme": {}, "scheme_parameters": {}}
    scheme_ledger.commit_design(tmp_path, doc)
    assert len(bindings(req)) == 3
    for path, field in bindings(req).items():
        saved = doc["ledger_view"]["parameters"][path]
        assert saved["locked"] and saved["basis"] == "user_requirement"
        with pytest.raises(ValueError, match="锁定"):
            scheme_ledger.update(tmp_path, {path: dict(field, value=field["value"] + 1)}, doc["ledger_revision"])
    with pytest.raises(ValueError, match="别名"):
        scheme_ledger.update(tmp_path, {"condition.voltage_window_upper": {"value": 4.9, "unit": "V", "basis": "engineering_proposal", "source": "试算"}}, doc["ledger_revision"])


def test_binding_value_cannot_borrow_formula_or_wrong_unit():
    goal = "研究材料；Li1.2Ni0.2Mn0.6O2；上限4.8 mA。"
    row = dict(requirement("Li1.2Ni0.2Mn0.6O2", "constraint"), id="r", group_id="fixed", verification_needed=False,
               bindings=[{"path": "condition.v_max", "value": 1.2, "unit": "V"}])
    with pytest.raises(ValueError, match="固定值"):
        normalize_requirements(raw_contract(goal, [row]), goal)
    row.update(quote="上限4.8 mA", bindings=[{"path": "condition.v_max", "value": 4.8, "unit": "V"}])
    with pytest.raises(ValueError, match="固定值"):
        normalize_requirements(raw_contract(goal, [row]), goal)


@pytest.mark.parametrize("symbol", ["=", "＝"])
@pytest.mark.parametrize("operator", [None, "＝"])
def test_rate_definition_fills_binding_and_normalizes_numeric_string(symbol, operator):
    goal = f"研究材料；1 C{symbol}250 mA/g的倍率定义。"
    row = dict(requirement(f"1 C{symbol}250 mA/g的倍率定义", "constraint"), id="rate", group_id="fixed",
               quantity={"value": 250, "unit": "mA/g", "operator": operator},
               binding={"path": "condition.C_rate_reference_current", "value": "250", "unit": "mA/g"})
    result = normalize_requirements(raw_contract(goal, [row]), goal)
    fixed = bindings(result)["condition.C_rate_reference_current"]
    assert fixed["value"] == 250 and type(fixed["value"]) is float and fixed["locked"]
    assert result["items"][0]["quantity"]["operator"] == "="
    row.pop("binding")
    result = normalize_requirements(raw_contract(goal, [row]), goal)
    assert bindings(result)["condition.C_rate_reference_current"]["value"] == 250


@pytest.mark.parametrize("quote", ["按1 C测试，电流250 mA/g", "例如1 C=250 mA/g", "不采用1 C=250 mA/g", "0.1 C=250 mA/g", "1 C=250 mAh/g"])
def test_non_definition_or_excluded_rate_is_not_bound(quote):
    from auto_battery_research.util.requirement_structure import normalize_rate_definition
    row = dict(requirement(quote, "constraint"), id="rate")
    assert "condition.C_rate_reference_current" not in {b["path"] for b in normalize_rate_definition(row).get("bindings", [])}


def test_failed_rate_keeps_pending_binding_and_other_requirements():
    from auto_battery_research.util.requirement_recovery import recover_requirements
    from auto_battery_research.util.research_contract import document_errors, gap_answer
    goal = "研究材料；1 C=250 mA/g。"
    row = dict(requirement("1 C=250 mA/g", "constraint"), id="rate", group_id="fixed",
               quantity={"value": 999, "unit": "mA/g", "operator": "="})
    result, _ = recover_requirements(raw_contract(goal, [row]), goal)
    item = next(i for i in result["items"] if i["kind"] == "constraint")
    assert item["binding_status"] == "pending" and item["binding_reason"]
    doc = dict(target=goal, requirements=result, research={"answers": [gap_answer(item)]}, ledger_view={"parameters": {}})
    assert "固定条件缺少字段绑定或待绑定说明" not in document_errors(doc)


def test_fixed_condition_without_binding_is_disclosed_and_partial():
    goal = "研究材料；使用陌生设备QX-7。"
    row = dict(requirement("使用陌生设备QX-7", "constraint"), id="r", group_id="fixed", verification_needed=False)
    raw = raw_contract(goal, [row])
    with pytest.raises(ValueError, match="固定条件"):
        normalize_requirements(raw, goal)
    row.update(binding_status="pending", binding_reason="设备规范字段尚待确认")
    result = normalize_requirements(raw, goal)
    assert result["status"] == "partial" and "待绑定" in result["diagnostics"][0]


def test_connection_word_does_not_create_duplicate_but_negation_is_retained():
    goal = "研究材料，并设计对照实验；禁止含氟。"
    row = dict(requirement("设计对照实验", "deliverable"), id="r", group_id="science")
    raw = raw_contract(goal, [row])
    parsed = attach_requirements({"requirements": raw}, goal)["requirements"]
    assert any("禁止含氟" in r["quote"] for r in parsed["items"])
    assert not any(r["quote"] == "并" for r in parsed["items"])
    raw["task"]["source_quotes"].append("禁止含氟")
    result = normalize_requirements(raw, goal)
    assert len(result["items"]) == 1


def test_recovery_keeps_valid_groups_when_uncovered_requirement_is_pending():
    from auto_battery_research.util.requirement_recovery import recover_requirements
    from auto_battery_research.util.requirement_structure import normalize_groups
    goal = "研究材料；评价循环；禁止含氟。"
    row = dict(requirement("评价循环"), id="cycle", group_id="science")
    result, _ = recover_requirements(raw_contract(goal, [row]), goal)
    assert {g["id"] for g in result["groups"]} >= {"science", "fixed"}
    assert result["status"] == "partial"
    assert next(r for r in result["items"] if r["quote"] == "评价循环")["group_id"] == "science"
    missing = next(r for r in result["items"] if r["quote"] == "禁止含氟")
    assert missing["group_id"].startswith("recovery_pending") and missing["status"] == "ambiguous"
    assert normalize_groups(result, goal, result["items"]) == result["groups"]


@pytest.mark.parametrize("fault", ["missing", "cycle", "source", "duplicate"])
def test_invalid_group_recovery_has_no_dangling_references(fault):
    from auto_battery_research.util.requirement_recovery import recover_requirements
    from auto_battery_research.util.requirement_structure import normalize_groups
    goal = "研究材料；评价循环。"
    raw = raw_contract(goal, [dict(requirement("评价循环"), id="cycle", group_id="science")])
    if fault == "missing": raw["groups"][0]["parent_id"] = "absent"
    elif fault == "cycle": raw["groups"][0]["parent_id"] = "science"
    elif fault == "source": raw["groups"][0]["source_quotes"] = ["编造来源"]
    else: raw["groups"].append(copy.deepcopy(raw["groups"][0]))
    result, _ = recover_requirements(raw, goal)
    assert "fixed" in {g["id"] for g in result["groups"]}
    assert result["status"] == "partial" and result["diagnostics"]
    assert all(r.get("group_id") in {g["id"] for g in result["groups"]} for r in result["items"])
    assert normalize_groups(result, goal, result["items"]) == result["groups"]


def test_task_source_patch_expands_coverage_without_rewriting_accepted_items():
    from auto_battery_research.util.requirement_recovery import recover_requirements, merge_requirement_patch
    goal = "研究材料；以提高循环为目标。"
    row = dict(requirement("提高循环"), id="cycle", group_id="science")
    raw = raw_contract(goal, [row])
    _, accepted = recover_requirements(raw, goal)
    patch = {"task": {"description": "不允许重写已给定总任务", "source_quotes": ["以提高循环为目标"]},
             "items": [dict(row, description="不允许重写通过项")]}
    merged = merge_requirement_patch(raw, patch, accepted, goal)
    assert merged["items"] == [row] and merged["task"]["description"] == raw["task"]["description"]
    assert merged["task"]["source_quotes"] == ["研究材料", "以提高循环为目标"]
    result, _ = recover_requirements(merged, goal)
    assert result["status"] == "parsed" and len(result["items"]) == 1
    assert {g["id"] for g in result["groups"]} == {"science", "fixed"}
    patch["task"]["source_quotes"] = ["不存在的原文"]
    assert merge_requirement_patch(raw, patch, accepted, goal)["task"] == raw["task"]


def test_isolated_goal_connectors_are_not_requirements_but_negation_is_preserved():
    from auto_battery_research.util.requirement_recovery import recover_requirements
    goal = "研究材料；以提高循环为目标；不含氟。"
    row = dict(requirement("提高循环"), id="cycle", group_id="science")
    result, _ = recover_requirements(raw_contract(goal, [row]), goal)
    assert not any(r["quote"] in {"以", "为目标"} for r in result["items"])
    assert any(r["quote"] == "不含氟" for r in result["items"])


def test_exact_duplicate_preserves_sources_and_query_links():
    goal = "研究材料；设计对照实验；并设计对照实验。"
    a = dict(requirement("设计对照实验", "deliverable"), id="a", start=5, description="设计对照实验", group_id="science")
    b = dict(a, id="b", quote="并设计对照实验", start=12)
    raw = raw_contract(goal, [a, b])
    raw["query_links"] = [{"query_index": 0, "requirement_ids": ["a", "b"]}]
    result = attach_requirements({"requirements": raw, "retrieval_queries": ["对照实验"]}, goal)["requirements"]
    assert len(result["items"]) == 1 and result["status"] == "parsed"
    assert result["items"][0]["source_quotes"] == ["并设计对照实验"]
    assert result["query_links"][0]["requirement_ids"] == [result["items"][0]["id"]]


def test_equal_model_descriptions_do_not_merge_different_original_conditions():
    goal = "研究材料；0.1 C容量保持；1 C容量保持。"
    rows = [dict(requirement("0.1 C容量保持"), id="a", description="容量保持", group_id="science"),
            dict(requirement("1 C容量保持"), id="b", description="容量保持", group_id="science")]
    result = attach_requirements({"requirements": raw_contract(goal, rows)}, goal)["requirements"]
    assert len(result["items"]) == 2


def test_hierarchy_order_shared_by_coverage_and_detail():
    from auto_battery_research.util.research_reporting import render_summary, render_review_appendix
    goal = "研究材料；容量保持；电压衰减；固定电压4.8 V。"
    rows = [dict(requirement("固定电压4.8 V", "constraint"), id="voltage", group_id="fixed", verification_needed=False,
                 binding={"path": "condition.v_max", "value": 4.8, "unit": "V"}),
            dict(requirement("容量保持"), id="retention", group_id="science"),
            dict(requirement("电压衰减"), id="fade", group_id="science")]
    req = normalize_requirements(raw_contract(goal, rows), goal)
    assert [r["quote"] for r in ordered_items(req)] == ["容量保持", "电压衰减", "固定电压4.8 V"]
    doc = {"requirements": req, "research": {}, "final_answer": "", "ledger_view": {"parameters": {}}}
    summary, detail = render_summary(doc), render_review_appendix(doc)
    assert detail.index("容量保持") < detail.index("固定电压4.8 V")
    assert "#### 循环稳定性" in detail and "课题回应3" in detail
    assert "所属目标 / 类别" in detail and "所属目标 / 类别" not in summary
    bad = copy.deepcopy(req)
    bad["groups"][0]["parent_id"] = "science"
    with pytest.raises(ValueError, match="循环"):
        normalize_requirements(bad, goal)


def test_strengthening_only_promotes_matching_values_and_never_weakens_lock():
    from auto_battery_research.util.requirement_migration import strengthen_bindings
    from auto_battery_research.util.scheme_ledger import digest
    req = {"items": [{"id": "r", "quote": "窗口4.8 V", "description": "窗口", "conditions": [],
                       "bindings": [{"path": "condition.v_max", "value": 4.8, "unit": "V"}]}]}
    ledger = {"revision": 1, "history": [], "parameters": {"condition.v_max": {"value": 4.8, "unit": "V", "locked": False}}}
    before = digest(ledger)
    strengthened, changes = strengthen_bindings(ledger, req)
    assert digest(ledger) == before and changes == ["condition.v_max"]
    assert strengthened["parameters"]["condition.v_max"]["locked"]
    req["items"][0]["bindings"][0]["value"] = 4.7
    with pytest.raises(ValueError, match="冲突"):
        strengthen_bindings(strengthened, req)


def test_fixed_window_reviewer_only_blocks_the_misread_endpoint():
    from auto_battery_research.util.requirement_review import normalize_review
    goal = "研究材料；窗口2.0–4.8 V。"
    row = dict(requirement("窗口2.0–4.8 V", "constraint"), id="window", group_id="fixed", verification_needed=False,
               bindings=[{"path": "condition.v_min", "value": 2, "unit": "V"},
                         {"path": "condition.v_max", "value": 4.8, "unit": "V"}])
    req = normalize_requirements(raw_contract(goal, [row]), goal)
    doc = {"requirements": req, "final_answer": "上限4.9 V", "ledger_view": {"parameters": {"condition.v_max": {"value": 4.9, "unit": "V"}}}}
    verdict = {"requirement_review": {"misread_ids": [req["items"][0]["id"]], "misread_evidence": [{
        "requirement_id": req["items"][0]["id"], "scope": "user_binding", "path": "condition.v_max",
        "user_quote": "窗口2.0–4.8 V", "observed_quote": "上限4.9 V", "expected_value": 4.8, "expected_unit": "V",
        "actual_value": 4.9, "actual_unit": "V", "reason": "用户上限4.8 V被改为4.9 V"}]}}
    assert normalize_review(verdict, doc)["blocked_fields"] == ["condition.v_max"]


def test_fixed_binding_metadata_cannot_remain_an_unlocked_proposal(tmp_path):
    from auto_battery_research.util.research_contract import document_errors, gap_answer
    from auto_battery_research.util.scheme_ledger import commit_design
    goal = "研究材料；窗口2.0–4.8 V。"
    row = dict(requirement("窗口2.0–4.8 V", "constraint"), id="window", group_id="fixed", verification_needed=False,
               bindings=[{"path": "condition.v_min", "value": 2, "unit": "V"}, {"path": "condition.v_max", "value": 4.8, "unit": "V"}])
    req = normalize_requirements(raw_contract(goal, [row]), goal)
    doc = {"target": goal, "requirements": req, "scheme": {}, "research": {"answers": [gap_answer(req["items"][0])]}}
    commit_design(tmp_path, doc)
    from auto_battery_research.util.research_reporting import render_review_appendix
    doc["final_answer"] = ""
    assert "方案已固定 / 条件一致" in render_review_appendix(doc)
    assert "主方案按用户指定固定" in render_review_appendix(doc)
    doc["ledger_view"]["parameters"]["condition.v_max"].update(locked=False, basis="engineering_proposal")
    assert "用户固定要求未作为用户条件锁定: condition.v_max" in document_errors(doc)
    assert "方案已固定 / 条件一致" not in render_review_appendix(doc)


def test_group_migration_retains_children_and_rejects_hidden_numeric_goal():
    from auto_battery_research.util.requirement_migration import validate_mapping
    goal = "研究材料；评价循环稳定性；容量保持；电压衰减。"
    raw = raw_contract(goal, [dict(requirement("容量保持"), id="a", group_id="science"),
                              dict(requirement("电压衰减"), id="b", group_id="science")])
    raw["groups"][0]["source_quotes"].append("评价循环稳定性")
    raw["items"][0]["source_quotes"] = ["评价循环稳定性"]
    new = normalize_requirements(raw, goal)
    source = dict(requirement("评价循环稳定性"), id="old")
    old = normalize_requirements({"items": [source]}, goal)
    mapping = [{"old_id": old["items"][0]["id"], "new_id": None, "group_id": "science", "relation": "group", "reason": "上层目标由容量及电压指标分别验收"}]
    result = validate_mapping(old, new, mapping, raw["items"])
    assert result[old["items"][0]["id"]]["group_id"] == "science"
    old["items"][0]["quantity"] = {"value": 80, "unit": "%"}
    with pytest.raises(ValueError, match="数量目标"):
        validate_mapping(old, new, mapping, raw["items"])


def test_migration_conditions_are_carried_to_mapped_children():
    from auto_battery_research.util.requirement_migration import preserve_mapped_conditions
    old = {"items": [{"id": "old", "conditions": ["1 C"]}]}
    raw = {"groups": [{"id": "cycling", "parent_id": None}], "items": [{"id": "retention", "group_id": "cycling", "conditions": []}]}
    result = preserve_mapped_conditions(old, raw, [{"old_id": "old", "new_id": None, "relation": "group", "group_id": "cycling"}])
    assert result["items"][0]["conditions"] == ["1 C"] and raw["items"][0]["conditions"] == []
