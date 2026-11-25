"""正文引用补丁必须保值、保归属、可审计；错误全集和恢复预算不能丢失。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_writer_protocol import example
from auto_battery_research.util.numeric_bindings import SourceBindingError, declarations, source_parameters
from auto_battery_research.util.design_structure import render_design
from auto_battery_research.util.scheme_ledger import digest
from auto_battery_research.util.writer_protocol import diagnostics, parse, produce
from auto_battery_research.util.writer_references import apply_references, collect_errors


def row(value, unit="", **kwargs):
    return dict(value=value, unit=unit, basis="engineering_proposal", source="工程声明", meaning="工程取值", **kwargs)


def draft():
    data = example()
    data["parameters"] = {
        "cathode.doping.basis": row("总阳离子", quantity_kind="text"),
        "cathode.doping.na": row(.015, quantity_kind="site_fraction", denominator="总阳离子"),
        "condition.cycle_number": row(100, "cycles"),
    }
    data["sections"]["goals"]["body"] = "本方案以总阳离子为唯一计量分母，主组Na掺量1.5 mol%，主判定循环100圈。"
    return data


def patch(data):
    body = data["sections"]["goals"]["body"]
    return {"base_hash": digest(data), "parameters": [], "references": [
        dict(field=field, body_path=["sections", "goals", "body"], quote=body, value_quote=value)
        for field, value in [("cathode.doping.basis", "总阳离子"), ("cathode.doping.na", "1.5 mol%"),
                             ("condition.cycle_number", "100圈")]]}


def client_for(data, proposed=None, reject=False):
    calls, seen = [], []
    def chat(system, user, **kw):
        tag = kw["trace_tag"]
        calls.append(tag)
        payload = json.loads(user) if tag != "writer" else None
        seen.append(payload)
        if tag == "writer":
            return json.dumps(data, ensure_ascii=False)
        if tag == "writer_protocol_repair":
            assert client.single_attempt
            return json.dumps(proposed or patch(data), ensure_ascii=False)
        assert tag == "writer_reference_verify"
        assert client.single_attempt
        return json.dumps({"input_hash": payload["input_hash"], "bindings": [
            {"index": b["index"], "supported": not reject, "reason": "同一主组、分母和测试用途" if not reject else "原文是文献结果，不属于主方案"}
            for b in payload["bindings"]]})
    client = SimpleNamespace(available=True, chat=chat)
    return client, calls, seen


def test_all_unbound_fields_are_reported_together_without_mutation():
    data = draft()
    text, _ = render_design(data)
    params = declarations(data, {})
    before = copy.deepcopy(params)
    with pytest.raises(SourceBindingError) as caught:
        source_parameters(text, params)
    assert params == before
    assert {i["path"][1] for i in caught.value.issues} == set(data["parameters"])
    issues = diagnostics(data, {}, caught.value)
    assert len(issues) == 3 and all(i["code"] == "UNBOUND_PARAMETER" for i in issues)


def test_legacy_noop_patch_only_refreshes_errors_without_new_calls():
    data = draft()
    state = {"generation_attempted": True, "raw_response": json.dumps(data), "repair_attempts": 1,
             "repair_raw_response": json.dumps({"base_hash": digest(data), "parameters": []})}
    client = SimpleNamespace(available=True, chat=lambda *a, **k: pytest.fail("不能重置历史预算"))
    result = produce(client, "s", "u", {}, state, lambda: None)
    assert result["fallback"] and len(result["diagnostics"]) == 3
    assert all(key in result["error"] for key in data["parameters"])
    assert state["repair_attempts"] == 1 and not state.get("reference_review")


def test_metadata_and_body_references_can_be_repaired_in_one_transaction():
    data = example()
    data["parameters"] = {"synthesis.temp": row(900, "℃")}
    data["sections"]["goals"]["body"] = "主路线处理温度900 ℃，需验证。"
    proposed = {"base_hash": digest(data), "parameters": [{"path": "synthesis.temp", "new_path": "process.temp"}],
        "references": [{"field": "process.temp", "body_path": ["sections", "goals", "body"],
                        "quote": data["sections"]["goals"]["body"], "value_quote": "900 ℃"}]}
    client, _, _ = client_for(data, proposed)
    result = produce(client, "s", "u", {}, {}, lambda: None)
    assert not result["fallback"]
    assert result["structured_sections"]["parameters"] == {"process.temp": data["parameters"]["synthesis.temp"]}


def test_table_subsection_references_keep_other_candidate_values():
    data = example()
    data["parameters"] = {"cathode.doping.zn": row(.005, quantity_kind="site_fraction", denominator="总阳离子")}
    body = "以总阳离子为分母。\n|组别|掺量|\n|---|---|\n|主组Zn|0.5 mol%|\n|候选Cu|0.5 mol%|"
    data["sections"]["recommendations"]["subsections"][1]["body"] = body
    changes = [dict(field="cathode.doping.zn", body_path=["sections", "recommendations", "subsections", 1, "body"],
                    quote="|主组Zn|0.5 mol%|", value_quote="0.5 mol%")]
    changed, _ = apply_references(data, changes, {})
    after = changed["sections"]["recommendations"]["subsections"][1]["body"]
    assert "|候选Cu|0.5 mol%|" in after and after.count("{{param:cathode.doping.zn}}") == 1


def test_errors_in_multiple_parameters_and_body_references_all_survive():
    data = draft()
    data["parameters"].update({"bad.temperature": row(900, "℃"), "process.bad": row("100", quantity_kind="text")})
    data["sections"]["metrics"]["body"] += "未登记{{param:cathode.unknown}}与{{param:condition.unknown}}。"
    issues = collect_errors(data, {})
    assert len([e for e in issues if e["code"] == "PARAMETER"]) == 2
    assert len([e for e in issues if e["code"] == "REFERENCE"]) == 2
    assert len([e for e in issues if e["code"] == "UNBOUND_PARAMETER"]) == 5


@pytest.mark.parametrize("kind", [["site_fraction"], {"kind": "site_fraction"}, "invented_kind"])
def test_malformed_quantity_kind_is_reported_without_crashing_diagnostics(kind):
    data = draft()
    data["parameters"]["cathode.doping.na"]["quantity_kind"] = kind
    state = {"generation_attempted": True, "raw_response": json.dumps(data), "repair_attempts": 1}
    result = produce(SimpleNamespace(available=False), "s", "u", {}, state, lambda: None)
    assert result["fallback"]
    assert any(e["code"] == "PARAMETER" and "quantity_kind" in e["reason"] for e in result["diagnostics"])
    assert len([e for e in result["diagnostics"] if e["code"] == "UNBOUND_PARAMETER"]) == 3


def test_successful_reference_patch_preserves_source_and_replays_offline(tmp_path):
    data = draft()
    original = copy.deepcopy(data)
    client, calls, seen = client_for(data)
    state, saved = {}, []
    def save():
        saved.append(copy.deepcopy(state))
    result = produce(client, "system", "user", {}, state, save)
    assert not result["fallback"]
    assert calls == ["writer", "writer_protocol_repair", "writer_reference_verify"]
    assert len(seen[1]["errors"]) == 3
    assert data == original and result["structured_sections"]["parameters"] == original["parameters"]
    assert state["repair_attempts"] == 1 and state["reference_review"]["status"] == "ACCEPTED"
    assert any(s.get("reference_review", {}).get("attempted") and "raw_response" not in s["reference_review"] for s in saved)
    assert state["diagnostics"] == []
    assert not hasattr(client, "single_attempt")
    restart = json.loads(json.dumps(state))
    offline = SimpleNamespace(available=False, chat=lambda *a, **k: pytest.fail("恢复不能重新请求模型"))
    again = produce(offline, "system", "user", {}, restart, lambda: None)
    assert again["draft_answer"] == result["draft_answer"]
    assert "{{param:" not in again["draft_answer"] and "1.5%" in again["draft_answer"]


@pytest.mark.parametrize("reject", [False, True])
def test_text_binding_uses_complete_site_context_and_independent_review(reject):
    data = example()
    data["parameters"] = {"cathode.doping.A_site": row("Na 占 Li 位（A 位）", quantity_kind="text"),
                          "cathode.doping.B_site": row("Zn 或 Cu 占 Ni 位（B 位）", quantity_kind="text")}
    body = "设计路线：A 位为 Li 位、B 位为 Ni 位；A 位引入 Na+，B 位引入 Zn2+ 或 Cu2+。"
    data["sections"]["goals"]["body"] = body
    proposed = {"base_hash": digest(data), "parameters": [], "references": [
        dict(field=field, body_path=["sections", "goals", "body"], quote=body, value_quote=quote)
        for field, quote in [("cathode.doping.A_site", "A 位引入 Na+"),
                             ("cathode.doping.B_site", "B 位引入 Zn2+ 或 Cu2+")]]}
    client, calls, seen = client_for(data, proposed, reject=reject)
    state = {}
    original = copy.deepcopy(data)
    result = produce(client, "s", "u", {}, state, lambda: None)
    assert calls == ["writer", "writer_protocol_repair", "writer_reference_verify"]
    payload = seen[-1]
    assert all(b["equivalence_mode"] == "semantic_text" and b["body"] == body for b in payload["bindings"])
    assert payload["before"]["sections"]["goals"]["body"] == body
    assert data == original
    if reject:
        assert result["fallback"] and state["reference_review"]["status"] == "REJECTED"
        assert not state.get("accepted_patch")
    else:
        assert not result["fallback"] and state["reference_review"]["status"] == "ACCEPTED"
        assert result["structured_sections"]["parameters"] == original["parameters"]
        assert "Na 占 Li 位（A 位）" in result["draft_answer"]
    before = list(calls)
    repeated = produce(client, "s", "u", {}, state, lambda: None)
    assert repeated["fallback"] == reject and calls == before


def test_saved_unit_and_site_patch_reuses_legacy_hash_and_keeps_request_budget():
    from auto_battery_research.util.writer_protocol import normalize
    data = example()
    data["parameters"] = {"cathode.L_um": row(20, "μm"),
                          "cathode.doping.A_site": row("Na 占 Li 位（A 位）", quantity_kind="text")}
    body = "A 位为 Li 位，A 位引入 Na+；厚度{{param:cathode.L_um}}。"
    data["sections"]["goals"]["body"] = body
    proposed = {"base_hash": digest(normalize(data, normalize_units=False)),
        "parameters": [{"path": "cathode.L_um", "metadata": {"unit": "um"}}],
        "references": [dict(field="cathode.doping.A_site", body_path=["sections", "goals", "body"],
                            quote=body, value_quote="A 位引入 Na+")]}
    state = {"raw_response": json.dumps(data, ensure_ascii=False), "generation_attempted": True,
             "repair_attempts": 1, "repair_raw_response": json.dumps(proposed)}
    client, calls, _ = client_for(data, proposed)
    result = produce(client, "s", "u", {}, state, lambda: None)
    assert not result["fallback"], result
    assert calls == ["writer_reference_verify"] and state["repair_attempts"] == 1
    assert state["accepted_patch"] == proposed and json.loads(state["raw_response"]) == data
    assert result["numeric_parameters"]["cathode.L_um"]["unit"] == "um"
    assert not produce(SimpleNamespace(available=False), "s", "u", {}, state, lambda: None)["fallback"]


def test_incomplete_or_changed_text_relation_cannot_publish_without_review():
    data = example()
    data["parameters"] = {"cathode.doping.A_site": row("Na 占 Li 位（A 位）", quantity_kind="text")}
    body = "Na 仅为对照，不确定是否占 Li 位。"
    data["sections"]["goals"]["body"] = body
    proposed = {"base_hash": digest(data), "parameters": [], "references": [
        dict(field="cathode.doping.A_site", body_path=["sections", "goals", "body"], quote=body, value_quote="Na")]}
    client, calls, _ = client_for(data, proposed, reject=True)
    state = {}
    result = produce(client, "s", "u", {}, state, lambda: None)
    assert result["fallback"] and state["reference_review"]["status"] == "REJECTED"
    assert "writer_reference_verify" in calls and not state.get("accepted_patch")


@pytest.mark.parametrize("attack", ["value", "unit", "ambiguous", "title", "source", "code", "duplicate", "whole_body", "unknown", "locked", "overlap", "stale"])
def test_illegal_patch_never_changes_values_or_scientific_prose(attack):
    data = draft()
    proposal = patch(data)
    current = {}
    if attack == "value":
        data["parameters"]["cathode.doping.na"]["value"] = .02
    elif attack == "unit":
        data["parameters"]["condition.cycle_number"]["unit"] = "h"
    elif attack == "ambiguous":
        data["sections"]["goals"]["body"] *= 2
    elif attack == "title":
        proposal["references"][0]["body_path"] = ["sections", "goals", "title"]
    elif attack == "source":
        proposal["references"][0]["body_path"] = ["parameters", "cathode.doping.basis", "source"]
    elif attack == "code":
        data["sections"]["goals"]["body"] = "```text\n" + data["sections"]["goals"]["body"] + "\n```"
    elif attack == "duplicate":
        proposal["references"].append(copy.deepcopy(proposal["references"][0]))
    elif attack == "whole_body":
        proposal["references"][0]["replacement"] = "任意新正文"
    elif attack == "unknown":
        proposal["references"][0]["field"] = "cathode.unknown"
    elif attack == "locked":
        current = {"cathode.doping.basis": dict(data["parameters"]["cathode.doping.basis"], locked=True)}
    elif attack == "overlap":
        proposal["references"][1]["value_quote"] = "总阳离子"
    elif attack == "stale":
        proposal["base_hash"] = "stale"
    client, calls, _ = client_for(data, proposal)
    before = copy.deepcopy(data)
    result = produce(client, "s", "u", current, {}, lambda: None)
    assert result["fallback"] and data == before
    assert "writer_reference_verify" not in calls


@pytest.mark.parametrize("quote", ["Ni0.015O2", "0.015–0.02", "0.015 mmol", "0.015%"])
def test_numeric_substring_or_wrong_scale_is_rejected(quote):
    data = draft()
    data["sections"]["goals"]["body"] = "总阳离子；主组Na：" + quote + "。"
    change = dict(field="cathode.doping.na", body_path=["sections", "goals", "body"],
                  quote=data["sections"]["goals"]["body"], value_quote="0.015")
    with pytest.raises(ValueError):
        apply_references(data, [change], {})


def test_semantic_rejection_does_not_bind_literature_or_other_sample():
    data = draft()
    data["sections"]["goals"]["body"] = "文献对照以总阳离子为分母，Na掺量1.5 mol%，循环100圈。[p1]"
    client, calls, _ = client_for(data, reject=True)
    state = {}
    result = produce(client, "s", "u", {}, state, lambda: None)
    assert result["fallback"] and "文献结果" in result["error"]
    before = list(calls)
    produce(client, "s", "u", {}, state, lambda: None)
    assert calls == before and not state.get("accepted_patch")


def test_partial_patch_repairs_only_remaining_fields_and_replays_offline():
    data = draft()
    client, calls, _ = client_for(data)
    original = client.chat
    requests = []
    def chat(system, user, **kwargs):
        if kwargs["trace_tag"] != "writer_protocol_repair":
            return original(system, user, **kwargs)
        calls.append(kwargs["trace_tag"])
        request = json.loads(user)
        requests.append(request)
        proposed = patch(request["document"])
        remaining = {e["path"][1] for e in request["errors"] if e["code"] == "UNBOUND_PARAMETER"}
        proposed["references"] = [r for r in proposed["references"] if r["field"] in remaining]
        if len(requests) == 1:
            proposed["references"] = proposed["references"][:1]
        return json.dumps(proposed, ensure_ascii=False)
    client.chat = chat
    state = {}
    result = produce(client, "s", "u", {}, state, lambda: None)
    assert not result["fallback"], result
    assert state["repair_attempts"] == 2 and len(state["repair_history"]) == 2
    assert {i["path"][1] for i in requests[1]["errors"]} == {"cathode.doping.na", "condition.cycle_number"}
    assert "{{param:cathode.doping.basis}}" in requests[1]["document"]["sections"]["goals"]["body"]
    assert calls.count("writer_reference_verify") == 2
    before = list(calls)
    restored = json.loads(json.dumps(state))
    again = produce(SimpleNamespace(available=False), "s", "u", {}, restored, lambda: None)
    assert again["draft_answer"] == result["draft_answer"]
    assert calls == before


@pytest.mark.parametrize("kind,unit,denominator", [("site_fraction", "wt%", "总阳离子"),
    ("site_fraction", "mol%", ""), ("mass_fraction", "mol%", "总质量"),
    ("formula_coefficient", "%", "每化学式"), ("site_fraction", "mol%", "TM位点")])
def test_fraction_denominator_and_unit_cannot_be_invented(kind, unit, denominator):
    data = draft()
    data["parameters"]["cathode.doping.na"].update(quantity_kind=kind, denominator=denominator)
    data["sections"]["goals"]["body"] = "本方案以总阳离子为分母；主组Na掺量1.5 " + unit + "。"
    change = dict(field="cathode.doping.na", body_path=["sections", "goals", "body"],
                  quote=data["sections"]["goals"]["body"], value_quote="1.5 " + unit)
    with pytest.raises(ValueError):
        apply_references(data, [change], {})


@pytest.mark.parametrize("fragment", ["`100圈`", "[100圈]", "$100圈$", "<!-- 100圈 -->", "{{param:100圈}}"])
def test_nonprose_ranges_are_not_replacement_targets(fragment):
    data = draft()
    body = "工程示例" + fragment + "，尚待验证。"
    data["sections"]["goals"]["body"] = body
    change = dict(field="condition.cycle_number", body_path=["sections", "goals", "body"], quote=body, value_quote="100圈")
    with pytest.raises(ValueError):
        apply_references(data, [change], {})


def test_new_null_filled_field_and_declared_unit_conversion_are_supported():
    data = example()
    data["parameters"] = {"cathode.L_um": row(70, "um")}
    body = "主组正极干涂层厚度0.07 mm，需称重复核。"
    data["sections"]["goals"]["body"] = body
    current = {"cathode.L_um": row(None, "um", locked=False)}
    changes = [dict(field="cathode.L_um", body_path=["sections", "goals", "body"], quote=body, value_quote="0.07 mm")]
    changed, records = apply_references(data, changes, current)
    assert records[0]["parameter"]["value"] == 70
    assert "{{param:cathode.L_um}}" in changed["sections"]["goals"]["body"]
    assert data["sections"]["goals"]["body"] == body


def test_review_response_saved_before_interruption_is_reused_without_call():
    data = draft()
    client, calls, _ = client_for(data)
    state = {}
    def save():
        if state.get("reference_review", {}).get("raw_response"):
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        produce(client, "s", "u", {}, state, save)
    assert state["reference_review"]["raw_response"] and not state.get("accepted_patch")
    offline = SimpleNamespace(available=False, chat=lambda *a, **k: pytest.fail("已有复核响应不能重发"))
    assert not produce(offline, "s", "u", {}, state, lambda: None)["fallback"]


def test_accepted_patch_rejects_changed_current_parameters_and_review_hash():
    data = draft()
    client, calls, _ = client_for(data)
    state = {}
    assert not produce(client, "s", "u", {}, state, lambda: None)["fallback"]
    before = list(calls)
    assert produce(client, "s", "u", {"condition.c_rate": row(1, "C")}, state, lambda: None)["fallback"]
    state["reference_review"]["input_hash"] = "stale"
    result = produce(client, "s", "u", {}, state, lambda: None)
    assert result["fallback"] and "基准已变化" in result["error"]
    assert calls == before


def test_reference_review_interrupted_request_is_not_replayed():
    data = draft()
    client, calls, _ = client_for(data)
    original_chat = client.chat
    def chat(*a, **kw):
        if kw["trace_tag"] == "writer_reference_verify":
            calls.append(kw["trace_tag"])
            raise RuntimeError("连接中断")
        return original_chat(*a, **kw)
    client.chat = chat
    state = {}
    assert produce(client, "s", "u", {}, state, lambda: None)["fallback"]
    assert produce(client, "s", "u", {}, state, lambda: None)["fallback"]
    assert calls.count("writer_reference_verify") == 1


@pytest.mark.parametrize("attack", ["omitted", "duplicate", "string_bool", "wrong_hash", "no_reason"])
def test_incomplete_semantic_verdict_cannot_approve(attack):
    data = draft()
    client, _, _ = client_for(data)
    original_chat = client.chat
    def chat(*a, **kw):
        reply = original_chat(*a, **kw)
        if kw["trace_tag"] != "writer_reference_verify":
            return reply
        obj = json.loads(reply)
        if attack == "omitted": obj["bindings"].pop()
        if attack == "duplicate": obj["bindings"][-1] = obj["bindings"][0]
        if attack == "string_bool": obj["bindings"][0]["supported"] = "true"
        if attack == "wrong_hash": obj["input_hash"] = "stale"
        if attack == "no_reason": obj["bindings"][0]["reason"] = ""
        return json.dumps(obj)
    client.chat = chat
    assert produce(client, "s", "u", {}, {}, lambda: None)["fallback"]
