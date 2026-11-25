"""v3表达、量值与科学边界的离线回归。"""
import copy
import json

import pytest

from auto_battery_research.util.stage4_document import (compile_document, decode_document, candidate_parameters, SECTION_IDS)
from auto_battery_research.util.stage4_consistency import authorize, apply_patch, request, parse_consistency, DIMENSIONS
from auto_battery_research.util.stage4_research_projection import preserve_support, field_fingerprint
from auto_battery_research.util.research_support import apply_review, supported


@pytest.fixture
def example():
    content = {"version": 3, "sections": {key: {"blocks": [{"type": "paragraph", "id": key + "_p", "text": "保留完整科研条件、适用边界和验证方法。"}]} for key in SECTION_IDS},
        "research_links": {"answers": [{"requirement_id": "r1", "status": "answered", "verification": "unverified",
            "response": [{"leaf_id": "goals_p"}]}], "candidates": [], "arguments": []}}
    requirements = {"items": [{"id": "r1", "kind": "goal", "description": "设计目标", "quote": "设计目标"}]}
    evidence = [{"passage_id": "p1", "source": "种子文献", "text": "原文条件与科学边界。"}]
    parameters = {"cathode.L_um": {"value": 80, "unit": "um", "basis": "engineering_proposal", "source": "工程声明"}}
    return content, parameters, requirements, evidence


def test_pure_compiler_and_stable_duplicate_leaves(example):
    content, params, requirements, evidence = example
    prior = copy.deepcopy(example)
    compiled = compile_document(*example)
    assert compiled.valid, compiled.diagnostics
    assert compiled.markdown == compile_document(*example).markdown
    assert example == prior
    assert compiled.leaves["goals_p"]["start"] != compiled.leaves["risks_p"]["start"]
    row = compiled.research["answers"][0]
    assert row["source_locations"][0]["start"] == compiled.leaves["goals_p"]["start"]
    assert not supported(row, "response", {"requirements": requirements, "evidence": evidence}, params)


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}', '{"a":1}{"b":2}', '{"outer": {"version":3}'])
def test_complete_json_only(raw):
    if raw == '{"outer": {"version":3}':
        with pytest.raises(ValueError):
            decode_document(raw)
    else:
        with pytest.raises(ValueError):
            decode_document(raw)


def test_table_unicode_escape_and_coordinates(example):
    content, params, req, ev = example
    content["sections"]["goals"]["blocks"] = [{"type": "table", "id": "table_a", "columns": [{"id": "name", "name": "组成"}, {"id": "reason", "name": "依据"}],
        "rows": [{"id": "one", "cells": {"name": "LiNi₀.₈Mn₀.₁Co₀.₁O₂ | 中文\n第二行", "reason": "保留 α+β≠0 {{cite:p1}}"}}]}]
    content["research_links"]["answers"][0]["response"] = [{"leaf_id": "table_a__one__name"}]
    compiled = compile_document(*example)
    assert compiled.valid, compiled.diagnostics
    assert "LiNi₀.₈Mn₀.₁Co₀.₁O₂ \\| 中文<br>第二行" in compiled.markdown
    assert "α+β≠0 [p1]" in compiled.markdown
    row = compiled.research["answers"][0]
    source = row["source_locations"][0]
    assert compiled.markdown[source["start"]:source["end"]] == row["response"]


def test_all_reference_errors_and_unbound_proposals(example):
    content, params, req, ev = example
    params["cathode.L_um"]["reference_origin"] = "writer_v3"
    content["sections"]["goals"]["blocks"][0]["text"] = "未知 {{param:unknown}}、{{cite:missing}}、{{section:missing}}、{{candidate:missing}}。"
    compiled = compile_document(*example)
    assert sum(d["code"] == "REFERENCE_INVALID" for d in compiled.diagnostics) == 4
    assert any(d["code"] == "UNBOUND_PARAMETER" for d in compiled.diagnostics)


def test_missing_geometry_display_and_free_numbers_do_not_infer_capacity(example):
    content, params, req, ev = example
    content["sections"]["metrics"]["blocks"][0]["text"] = "扩散 {{param:cathode.D_s_m2s}}，1 C=250 mA/g是倍率基准；容量200 mAh/g来自其他条件。"
    compiled = compile_document(*example)
    assert compiled.valid, compiled.diagnostics
    assert "cathode.D_s_m2s缺少声明" in compiled.markdown
    assert "RATIO_CONFLICT" not in {d["code"] for d in compiled.diagnostics}


@pytest.mark.parametrize("value,unit", [(81, "um"), (80, "mm")])
def test_existing_and_locked_null_cannot_be_redeclared(value, unit):
    current = {"cathode.L_um": {"value": None if value is None else 80, "unit": "um", "locked": True}}
    view, issues = candidate_parameters({"cathode.L_um": {"value": value, "unit": unit, "basis": "engineering_proposal", "source": "模型"}}, current)
    assert issues
    assert view == current


def test_parameter_permission_metadata_rejected():
    for metadata in ({"locked": True}, {"basis": "model_reference"}):
        _, issues = candidate_parameters({"cathode.L_um": dict(value=80, unit="um", basis="engineering_proposal", source="工程", **metadata)}, {}) if "basis" not in metadata else candidate_parameters({"cathode.L_um": {"value": 80, "unit": "um", "source": "工程", **metadata}}, {})
        assert issues


@pytest.mark.parametrize("value", [None, 80])
def test_identical_redeclaration_preserves_existing_source_and_lock(value):
    current = {"cathode.L_um": {"value": value, "unit": "um", "basis": "user_requirement",
                                "source": "用户已声明", "locked": True}}
    view, issues = candidate_parameters({"cathode.L_um": {"value": value, "unit": "um",
        "basis": "engineering_proposal", "source": "模型重复输出"}}, current)
    assert not issues and view == current
    if value is None:
        _, issues = candidate_parameters({"cathode.L_um": {"value": 80, "unit": "um",
            "basis": "engineering_proposal", "source": "模型补值"}}, current)
        assert issues


def test_user_requirement_label_is_not_lock_or_review_permission():
    view, issues = candidate_parameters({"cathode.L_um": {"value": 80, "unit": "um",
        "basis": "user_requirement", "source": "用户原文要求80 um"}}, {})
    assert not issues
    assert view["cathode.L_um"]["basis"] == "user_requirement"
    assert view["cathode.L_um"]["locked"] is False
    assert view["cathode.L_um"]["reference_origin"] == "writer_v3"


def test_local_patch_keeps_ids_and_frozen_tokens(example):
    content, params, req, ev = example
    compiled = compile_document(*example)
    issue = {"leaf_id": "goals_p", "quote": "保留完整科研条件", "reason": "含义需澄清", "parameter_paths": []}
    permit = authorize(content, params, compiled, [issue], req)
    patch = {"base_hash": permit["base_hash"], "transactions": [{"interface_ids": [permit["interfaces"][0]["id"]],
        "replacements": [{"interface_id": permit["interfaces"][0]["id"], "text": "明确保留科研条件"}], "parameters": {}, "additions": []}]}
    changed, fields, log = apply_patch(content, params, compiled, permit, patch, req, ev)
    assert log[0]["status"] == "APPLIED"
    assert changed["sections"]["goals"]["blocks"][0]["id"] == "goals_p"
    assert changed["sections"]["risks"] == content["sections"]["risks"]
    assert fields == params
    patch["transactions"][0]["replacements"][0]["text"] = "新引用 {{cite:p1}}"
    changed, _, log = apply_patch(content, params, compiled, permit, patch, req, ev)
    assert changed == content
    assert log[0]["status"] == "REJECTED"


def test_field_support_dependency_change_only(example):
    content, params, req, ev = example
    content["sections"]["goals"]["blocks"][0]["text"] = "主声明厚度 {{param:cathode.L_um}}，尚待实验验证。"
    compiled = compile_document(*example)
    doc = {"requirements": req, "evidence": ev, "final_answer": compiled.markdown}
    row = compiled.research["answers"][0]
    apply_review(compiled.research, [{"collection": "answers", "id": "r1", "fingerprint": row["support_fingerprint"], "fields": {"response": {"status": "supported", "reason": "原条件有据"}}}], doc, params, ev)
    assert supported(row, "response", doc, params)
    old = copy.deepcopy(compiled.research)
    content["sections"]["risks"]["blocks"][0]["text"] = "无关段落增加风险披露，保留原验证条件。"
    fresh = compile_document(*example)
    preserve_support(old, fresh.research, doc, params)
    assert supported(fresh.research["answers"][0], "response", doc, params)
    params["cathode.L_um"]["value"] = 90
    fresh = compile_document(*example)
    preserve_support(old, fresh.research, doc, params)
    assert not supported(fresh.research["answers"][0], "response", doc, params)


def test_request_budget_persisted_before_interrupt():
    state, saved, calls = {}, [], []
    def invoke():
        calls.append(True)
        raise RuntimeError("请求中断")
    with pytest.raises(RuntimeError):
        request(state, "writer", "same", lambda: saved.append(copy.deepcopy(state)), invoke)
    assert saved[0]["requests"][0]["status"] == "requested"
    with pytest.raises(ValueError):
        request(state, "writer", "same", lambda: None, invoke)
    assert len(calls) == 1


def test_consistency_requires_every_dimension_and_cannot_erase_issues():
    raw = {"version": 1, "dimensions": {d: "passed" for d in DIMENSIONS}, "issues": []}
    assert parse_consistency(json.dumps(raw), "fp")["status"] == "passed"
    del raw["dimensions"][DIMENSIONS[0]]
    with pytest.raises(ValueError):
        parse_consistency(json.dumps(raw), "fp")


def test_diffusion_without_precise_material_evidence_rejected(example):
    content, params, req, ev = example
    params["cathode.material"] = {"value": "NCM811", "unit": "", "basis": "engineering_proposal", "source": "来源"}
    params["cathode.D_s_m2s"] = {"value": 2e-14, "unit": "m2/s", "basis": "engineering_proposal", "source": "未验证提案"}
    compiled = compile_document(*example)
    assert any(d["code"] == "DIFFUSION_EVIDENCE_INVALID" for d in compiled.diagnostics)


def test_stage5_diffusion_evidence_survives_parameter_projection(example):
    from auto_battery_research.simulation.geometry_contract import build_geometry_contract
    from auto_battery_research.util.scheme_ledger import align_geometry_parameters
    content, params, req, ev = example
    text = "NCM811 constant solid-phase diffusion coefficient: 2e-10 cm2/s at 25°C, identified by fitting."
    ev.append({"passage_id": "diffusion", "source": "原文", "text": text})
    claim = {"passage_id": "diffusion", "quote": text, "material_id": "NCM811", "parameter": "solid_diffusion_coefficient",
             "value": 2e-10, "unit": "cm2/s", "temperature_K": 298.15, "applicability": "constant_full_soc", "method": "fitting"}
    scheme = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce"}
    candidate = {"cathode": {"L_um": 70., "porosity": .31, "D_s_m2s": 2e-14}, "anode": {"L_um": 110., "porosity": .56},
                 "diffusion_evidence": {"cathode.D_s_m2s": claim}}
    scheme["geometry"] = build_geometry_contract(candidate, scheme, ev)
    params["cathode.material"] = {"value": "NCM811", "unit": "", "basis": "engineering_proposal", "source": "声明"}
    params["cathode.D_s_m2s"] = {"value": 1e-14, "unit": "m2/s", "basis": "model_reference", "source": "旧模型"}
    doc = {"writer_contract_version": 3, "scheme": scheme, "scheme_parameters": params}
    align_geometry_parameters(doc, ["cathode.D_s_m2s"], "已验证扩散修订")
    assert params["cathode.D_s_m2s"]["evidence"] == claim
    compiled = compile_document(content, params, req, ev)
    assert not any(d["code"] == "DIFFUSION_EVIDENCE_INVALID" for d in compiled.diagnostics), compiled.diagnostics


def test_list_multiline_chemical_punctuation_keeps_coordinates(example):
    content, params, req, ev = example
    content["sections"]["goals"]["blocks"] = [{"id": "goals_list", "type": "list", "ordered": True,
        "items": [{"id": "list_first", "text": "原条件 LiNi₀.₈O₂ | 保留\n第二句给出验证条件。"}]}]
    content["research_links"]["answers"][0]["response"] = [{"leaf_id": "list_first"}]
    compiled = compile_document(*example)
    assert compiled.valid, compiled.diagnostics
    row = compiled.research["answers"][0]
    loc = row["source_locations"][0]
    assert compiled.markdown[loc["start"]:loc["end"]] == row["response"]


def test_protocol_equivalent_reference_cannot_change_scope(example):
    from auto_battery_research.util.stage4_document import recover_references
    content, params, req, ev = example
    params["cathode.L_um"]["reference_origin"] = "writer_v3"
    content["sections"]["goals"]["blocks"][0]["text"] = "主组正极厚度80 μm，保持此条件验证。"
    compiled = compile_document(*example)
    data = dict(content, parameters={})
    changed, records = recover_references(data, [{"field": "cathode.L_um", "leaf_id": "goals_p", "quote": "正极厚度80 μm", "value_quote": "80 μm"}], params, compiled)
    assert "{{param:cathode.L_um}}" in changed["sections"]["goals"]["blocks"][0]["text"]
    assert records[0]["parameter"] == params["cathode.L_um"]
    with pytest.raises(ValueError):
        recover_references(data, [{"field": "cathode.L_um", "leaf_id": "goals_p", "quote": "正极厚度80 μm", "value_quote": "80"}], params, compiled)


def test_explicit_quote_follows_authorized_content_patch(example):
    content, params, req, ev = example
    content["research_links"]["answers"][0]["response"] = [{"leaf_id": "goals_p", "quote": "保留完整科研条件、适用边界和验证方法。"}]
    compiled = compile_document(*example)
    permit = authorize(content, params, compiled, [{"leaf_id": "goals_p", "quote": "保留完整科研条件", "parameter_paths": []}], req)
    interface = permit["interfaces"][0]["id"]
    patch = {"base_hash": permit["base_hash"], "transactions": [{"interface_ids": [interface],
        "replacements": [{"interface_id": interface, "text": "明确保留科研条件"}], "parameters": {}, "additions": []}]}
    changed, _, log = apply_patch(content, params, compiled, permit, patch, req, ev)
    assert log[0]["status"] == "APPLIED"
    assert changed["research_links"]["answers"][0]["response"][0]["quote"].startswith("明确保留科研条件")


def test_independent_transaction_partial_success(example):
    content, params, req, ev = example
    compiled = compile_document(*example)
    issues = [{"leaf_id": key, "quote": "保留完整科研条件", "parameter_paths": []} for key in ("goals_p", "risks_p")]
    permit = authorize(content, params, compiled, issues, req)
    a, b = [p["id"] for p in permit["interfaces"]]
    patch = {"base_hash": permit["base_hash"], "transactions": [
        {"interface_ids": [a], "replacements": [{"interface_id": a, "text": "明确保留科研条件"}], "parameters": {}, "additions": []},
        {"interface_ids": [b], "replacements": [{"interface_id": b, "text": "{{cite:forged}}"}], "parameters": {}, "additions": []}]}
    changed, _, log = apply_patch(content, params, compiled, permit, patch, req, ev)
    assert [r["status"] for r in log] == ["APPLIED", "REJECTED"]
    assert changed["sections"]["goals"]["blocks"][0]["text"].startswith("明确保留科研条件")
    assert changed["sections"]["risks"] == content["sections"]["risks"]
