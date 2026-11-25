"""正文来源同步、属性级权限及候选复核的离线回归。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.design_repair import authorize, apply_patch
from auto_battery_research.util.numeric_bindings import render
from auto_battery_research.util.repair_interfaces import execute
from auto_battery_research.util.source_metadata import rebase_quote


def field(value, unit="", **extra):
    return dict(value=value, unit=unit, basis="engineering_proposal", source="原设计依据", **extra)


def case():
    params = {"cathode.nominal_formula": field("Li1.18Na0.02Mn0.58Ni0.20Zn0.02O2"),
              "cathode.doping.x_A": field(.025), "cathode.doping.y_B": field(.025)}
    text = render("主推配方{{param:cathode.nominal_formula}}；A位{{param:cathode.doping.x_A}}，B位{{param:cathode.doping.y_B}}。该式待验证。", params)
    for row in params.values():
        row["source_quote"] = text
    issue = dict(id="formula", quote=text, reason="修正名义式", suggestion="同步原句与参数", parameter_paths=["cathode.nominal_formula"])
    return text, params, issue


def test_formula_and_prose_edit_refreshes_other_parameter_sources_atomically():
    text, params, issue = case()
    before = copy.deepcopy(params)
    replacement = text.replace("该式待验证", "该式电荷计量已更正，仍待实验验证")
    new_formula = "Li1.18Na0.02Mn0.60Ni0.18Zn0.02O2"
    new_quote = replacement.replace(params["cathode.nominal_formula"]["value"], new_formula)
    permit = authorize(text, [issue], params)
    updated_text, updated = apply_patch(text, params, permit, {"base_hash": permit["base_hash"],
        "edits": [{"issue_id": "formula", "block_id": "b0", "text": replacement}],
        "parameters": [{"issue_id": "formula", "path": "cathode.nominal_formula",
                        "field": field(new_formula, source_quote=new_quote)}]})
    assert params == before
    assert updated_text == new_quote
    for path in ("cathode.doping.x_A", "cathode.doping.y_B"):
        assert updated[path] == dict(params[path], source_quote=new_quote)


def test_unchanged_sentence_is_not_rebound_to_changed_neighbour():
    original = "首句原文。后句尚无结论。"
    assert rebase_quote("首句原文。", original, "首句原文。后句为工程假设。") == "首句原文。"
    assert rebase_quote("首句原文。", original * 2, "新稿") is None
    assert rebase_quote("句原文。后", original, "整段重写。") is None


@pytest.mark.parametrize("protection", ["locked", "literature", "user_requirement"])
def test_immutable_sources_are_preserved_and_never_receive_metadata_permission(protection):
    text, params, issue = case()
    if protection == "locked":
        params["cathode.doping.x_A"]["locked"] = True
    else:
        params["cathode.doping.x_A"]["basis"] = protection
    permit = authorize(text, [issue], params)
    assert "cathode.doping.x_A" not in permit["grants"]["formula"]["source_fields"]
    replacement = text.replace("该式待验证", "尚需实验验证")
    _, updated = apply_patch(text, params, permit, {"base_hash": permit["base_hash"],
        "edits": [{"issue_id": "formula", "block_id": "b0", "text": replacement}]})
    assert updated["cathode.doping.x_A"] == params["cathode.doping.x_A"]


def test_stale_source_gets_only_source_quote_permission_and_can_be_rebound():
    text, params, issue = case()
    params["cathode.doping.x_A"]["source_quote"] = "旧稿的失效来源"
    permit = authorize(text, [issue], params)
    grant = permit["grants"]["formula"]
    assert "cathode.doping.x_A" not in grant["fields"]
    assert grant["source_fields"]["cathode.doping.x_A"]["attributes"] == ["source_quote"]
    patch = {"base_hash": permit["base_hash"], "sources": [{"issue_id": "formula", "path": "cathode.doping.x_A", "source_quote": text}]}
    result, values = apply_patch(text, params, permit, patch)
    assert result == text and values["cathode.doping.x_A"] == dict(params["cathode.doping.x_A"], source_quote=text)
    assert params["cathode.doping.x_A"]["source_quote"] == "旧稿的失效来源"


@pytest.mark.parametrize("attack", ["value", "unit", "source", "basis", "conditions", "locked", "other_field", "missing_reference", "outside", "duplicate", "stale"])
def test_source_permission_cannot_edit_values_or_rebind_outside_its_target(attack):
    text, params, issue = case()
    extra = render("其他样品{{param:cathode.doping.x_A}}的独立说明。", params)
    whole = text + "\n\n" + extra
    permit = authorize(whole, [issue], params)
    patch = {"base_hash": permit["base_hash"], "sources": [{"issue_id": "formula", "path": "cathode.doping.x_A", "source_quote": text}]}
    change = patch["sources"][0]
    if attack in ("value", "unit", "source", "basis", "conditions", "locked"):
        change[attack] = "unauthorized"
    elif attack == "other_field": change["path"] = "anode.material"
    elif attack == "missing_reference": change["source_quote"] = "该式待验证。"
    elif attack == "outside": change["source_quote"] = extra
    elif attack == "duplicate": patch["sources"] *= 2
    else: permit["grants"]["formula"]["source_fields"]["cathode.doping.x_A"]["source_hash"] = "old"
    with pytest.raises(ValueError):
        apply_patch(whole, params, permit, patch)


def test_reviewer_receives_synchronized_candidate_parameters_and_denial_rolls_back():
    text, params, issue = case()
    replacement = text.replace("该式待验证", "该式仍待实验验证")
    for accept in (True, False):
        seen = []
        def chat(system, user, **kw):
            x = json.loads(user)
            seen.append(kw["trace_tag"])
            if kw["trace_tag"] == "writer_patch":
                return json.dumps({"base_hash": x["permission"]["base_hash"], "submissions": [
                    {"interface_id": iid, "replacement": replacement} for iid in x["interface_contract"]["interfaces"]]})
            assert x["original_parameters"] == params
            assert x["parameters"]["cathode.doping.x_A"]["source_quote"] == replacement
            assert x["combined_preview"]["parameters"] == x["parameters"]
            for candidate in x["transactions"].values():
                assert candidate["parameters"]["cathode.doping.x_A"]["source_quote"] == replacement
            return json.dumps({"verdicts": [{"group_id": gid, "issue_id": "formula", "resolved": accept, "reason": "已核对候选来源"}
                for gid in x["transactions"]], "new_issues": []})
        client = SimpleNamespace(chat=chat)
        result, values, audit = execute(SimpleNamespace(llm=client), client, text, params, authorize(text, [issue], params), [issue], [])
        assert seen == ["writer_patch", "repair_verify"]
        if accept:
            assert result == replacement and len(audit["source_updates"]) == 3
        else:
            assert result == text and values == params and audit["source_updates"] == []


def test_material_source_metadata_change_is_not_a_material_replacement():
    from auto_battery_research.util.design_repair import validate_repair_geometry
    old = {"cathode.material": field("NCM811", source_quote="旧来源")}
    new = copy.deepcopy(old)
    new["cathode.material"]["source_quote"] = "当前来源"
    validate_repair_geometry(new, old, {"scheme": {"cathode": "NCM811"}}, [])
    new["cathode.material"]["value"] = "LFP"
    with pytest.raises(ValueError, match="材料变更"):
        validate_repair_geometry(new, old, {"scheme": {"cathode": "NCM811"}}, [])


def test_stale_unrelated_source_with_removed_reference_is_preserved_for_repair():
    text, params, issue = case()
    stale = params["cathode.doping.x_A"]["source_quote"] + "旧引用<!-- abr-param:process.removed -->1<!-- /abr-value -->"
    params["cathode.doping.x_A"]["source_quote"] = stale
    permit = authorize(text, [issue], params)
    changed = text.replace("该式待验证", "该式须进一步验证")
    result, updated = apply_patch(text, params, permit, {"base_hash": permit["base_hash"],
        "edits": [{"issue_id": "formula", "block_id": "b0", "text": changed}]})
    assert result == changed and updated["cathode.doping.x_A"]["source_quote"] == stale


def test_parameter_source_interface_uses_identity_when_old_quote_is_absent():
    from auto_battery_research.util.design_repair import repair
    text, params, _ = case()
    params["cathode.doping.x_A"]["source_quote"] = "旧配方不存在于当前正文"
    issue = {"type": "source", "quote": "旧配方不存在于当前正文", "reason": "source_quote仍为旧配方",
             "suggestion": "同步来源", "parameter_paths": ["cathode.doping.x_A"]}
    calls = []
    def chat(system, user, **kw):
        x = json.loads(user)
        calls.append(kw["trace_tag"])
        if kw["trace_tag"] == "writer_patch":
            interfaces = x["interface_contract"]["interfaces"]
            assert len(interfaces) == 1
            iid, entry = next(iter(interfaces.items()))
            assert entry["kind"] == "parameter_source" and entry["path"] == "cathode.doping.x_A"
            assert entry["fields"] == [] and entry["block_id"] == ""
            return json.dumps({"base_hash": x["permission"]["base_hash"], "submissions": [{"interface_id": iid, "source_quote": text}]})
        return json.dumps({"verdicts": [{"group_id": gid, "issue_id": i, "resolved": True, "reason": "来源对应当前字段"}
            for gid, txn in x["transactions"].items() for i in txn["issue_ids"]], "new_issues": []})
    client = SimpleNamespace(chat=chat)
    current, fields, entry = repair(SimpleNamespace(llm=client), text, {"issues": [issue["reason"]], "repair_issues": [issue]}, [], params, verifier=client)
    assert current == text and fields["cathode.doping.x_A"] == dict(params["cathode.doping.x_A"], source_quote=text)
    assert entry["status"] == "APPLIED" and calls == ["writer_patch", "repair_verify"]


@pytest.mark.parametrize("attribute", ["replacement", "parameter_changes", "path", "value", "unit", "locked"])
def test_parameter_source_interface_rejects_other_attributes(attribute):
    from auto_battery_research.util.design_repair import repair
    text, params, _ = case()
    params["cathode.doping.x_A"]["source_quote"] = "旧来源"
    def chat(system, user, **kw):
        assert kw["trace_tag"] == "writer_patch"
        x = json.loads(user)
        iid = next(iter(x["interface_contract"]["interfaces"]))
        return json.dumps({"base_hash": x["permission"]["base_hash"], "submissions": [
            {"interface_id": iid, "source_quote": text, attribute: "越权内容"}]})
    client = SimpleNamespace(chat=chat)
    result, values, audit = repair(SimpleNamespace(llm=client), text, {"issues": ["来源失效"]}, [], params, verifier=client)
    assert audit["status"] == "REJECTED" and result == text and values == params
