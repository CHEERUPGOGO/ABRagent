"""规范命名、空占位消解及主方案展示共用取值，保持原始档案与物理边界。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.parameter_names import checked_name, effective_parameters
from auto_battery_research.util.numeric_bindings import declarations, display, render, binding_errors
from auto_battery_research.util.numeric_consistency import audit
from auto_battery_research.util.parameter_presentation import parameter_view, render_parameter_appendix
from auto_battery_research.util.report_narrative import missing_suggestions, render_design

TOTAL = "cathode.loading_mg_cm2"
ALIAS = "cathode.total_loading_mg_cm2"
ACTIVE = "cathode.active_loading_mg_cm2"


def row(value, unit="mg/cm2", **extra):
    return dict({"value": value, "unit": unit, "basis": "engineering_proposal",
                 "source": "当前方案明确声明", "meaning": "干涂层总面载量", "locked": False}, **extra)


def sample():
    return {TOTAL: row(None, meaning=TOTAL), ALIAS: row(5.625, source="4.5 / 0.8 = 5.625"),
            ACTIVE: row(4.5, meaning="单面活性物质面载量")}


def test_writer_gets_short_field_table_even_without_existing_loading():
    from auto_battery_research.rag import WriterAgent
    from auto_battery_research.tests.test_design_structure import document
    data = dict(document(), version=2, parameters={})
    calls = []
    def chat(system, user, **kwargs):
        calls.append(user)
        return json.dumps(data, ensure_ascii=False)
    agent = WriterAgent(SimpleNamespace(available=True, chat=chat))
    agent.current_parameters = {}
    result = agent.run("设计材料方案", {}, [{"passage_id": "p1", "source": "离线证据", "text": "性能需实验验证。"}])
    assert not result["fallback"], result
    assert TOTAL in calls[0] and ACTIVE in calls[0]
    assert "不是必填清单" in calls[0] and "canonical_path" in calls[0]
    assert not result["numeric_parameters"]  # 字段表不自动新增必填值或空字段。


@pytest.mark.parametrize("reference", [ALIAS, TOTAL])
def test_writer_accepts_known_alias_with_binding_and_original_response_preserved(reference):
    from auto_battery_research.tests.test_design_structure import document
    from auto_battery_research.util.writer_protocol import produce
    field = row(5.625)
    field.pop("locked")
    data = dict(document(), version=2, parameters={ALIAS: field})
    data["sections"]["goals"]["body"] += "总面载量{{param:" + reference + "}}，仍需实测。"
    raw = json.dumps(data, ensure_ascii=False)
    state = {"raw_response": raw}
    current = {TOTAL: row(None)}
    before = copy.deepcopy(current)
    result = produce(SimpleNamespace(available=False), "system", "user", current, state, lambda: None)
    assert not result["fallback"], result
    actual = result["numeric_parameters"][ALIAS]
    assert actual["canonical_path"] == TOTAL and actual["value"] == 5.625
    assert actual["source"] == field["source"] and actual["source_quote"] in result["draft_answer"]
    assert state["raw_response"] == raw and current == before
    assert result["numeric_parameters"][TOTAL]["value"] is None
    assert "5.625" in display("param", TOTAL, result["numeric_parameters"])


def test_v3_uses_same_name_validation_and_preserves_original_path():
    from auto_battery_research.util.stage4_document import candidate_parameters
    field = row(5.625)
    field.pop("locked")
    params, issues = candidate_parameters({ALIAS: field}, {TOTAL: row(None)})
    assert not issues and params[ALIAS]["canonical_path"] == TOTAL
    assert params[ALIAS]["reference_origin"] == "writer_v3"
    assert TOTAL in params and params[TOTAL]["value"] is None


def test_v3_reference_tracks_effective_source_for_later_review_invalidation():
    from auto_battery_research.util.stage4_document import compile_document, SECTION_IDS
    content = {"version": 3, "sections": {key: {"blocks": [{"type": "paragraph", "id": key + "_p",
               "text": "当前声明仍需实验验证。"}]} for key in SECTION_IDS},
               "research_links": {"answers": [], "candidates": [], "arguments": []}}
    content["sections"]["goals"]["blocks"][0]["text"] += "总面载量{{param:" + TOTAL + "}}。"
    params = {TOTAL: row(None), ALIAS: row(5.625, reference_origin="writer_v3")}
    result = compile_document(content, params, {"items": []}, [])
    assert result.valid, result.diagnostics
    assert result.dependencies["goals_p"]["parameters"] == sorted([TOTAL, ALIAS])
    assert "5.625" in result.markdown
    changed = copy.deepcopy(params)
    changed[ALIAS]["value"] = 6
    after = compile_document(content, changed, {"items": []}, [])
    assert after.valid and after.render_fingerprint != result.render_fingerprint


@pytest.mark.parametrize("path,target", [(ALIAS, ACTIVE), (TOTAL, ACTIVE), (ACTIVE, TOTAL),
                                          (TOTAL, "anode.loading_mg_cm2"), ("cathode.D_s_m2s", "anode.D_s_m2s")])
def test_known_name_cannot_be_remapped_to_another_quantity(path, target):
    with pytest.raises(ValueError, match="身份冲突"):
        checked_name(path, row(5.625, canonical_path=target))
    assert any(i["code"] == "PARAMETER_NAME_CONFLICT" for i in audit({path: row(5.625, canonical_path=target)})["issues"])


def test_report_references_appendix_and_gaps_use_same_effective_value_without_writes():
    params = sample()
    before = copy.deepcopy(params)
    effective = effective_parameters(params)
    assert effective[TOTAL]["value"] == 5.625 and effective[TOTAL]["resolved_from"] == ALIAS
    assert TOTAL not in missing_suggestions({"parameters": params})
    body = render("总面载量{{param:" + TOTAL + "}}；活性载量{{param:" + ACTIVE + "}}。", params)
    assert "5.625" in body and "4.5" in body and "未确定" not in body
    assert not binding_errors(body, params)
    design = render_design({"parameters": params})
    assert "5.625" in design and "4.5" in design and "正极待补数据" not in design
    appendix = render_parameter_appendix({"parameters": params})
    assert appendix.count("总干涂层面载量：5.625") == 1 and "未确定" not in appendix
    merged = next(r for r in parameter_view({"parameters": params}) if r["canonical_key"] == TOTAL)
    assert set(merged["source_fields"]) == {TOTAL, ALIAS}
    assert {s["value"] for s in merged["sources"]} == {None, 5.625}
    assert params == before


@pytest.mark.parametrize("scope", [
    {"group_id": "control"}, {"scope": "candidate"}, {"purpose": "窄窗对照"},
    {"conditions": {"涂布面数": "双面"}}, {"conditions": {"质量范围": "含集流体"}},
    {"quantity_role": "upper_bound"}, {"quantity_role": "target"},
    {"basis": "literature"}, {"basis": "literature_measurement"},
])
def test_other_samples_conditions_roles_and_literature_do_not_fill_main(scope):
    params = {TOTAL: row(None), ALIAS: row(5.625, **scope)}
    assert effective_parameters(params)[TOTAL]["value"] is None
    assert TOTAL in missing_suggestions({"parameters": params})
    assert len(parameter_view({"parameters": params})) == 2


def test_active_loading_and_unknown_names_do_not_fill_total_without_mapping():
    params = {TOTAL: row(None), ACTIVE: row(4.5), "cathode.unseen_budget": row(5.625)}
    assert effective_parameters(params)[TOTAL]["value"] is None
    params["cathode.unseen_budget"]["canonical_path"] = TOTAL
    assert effective_parameters(params)[TOTAL]["value"] == 5.625


@pytest.mark.parametrize("meaning", ["含集流体的极片厚度", "湿膜厚度", "双面总厚"])
def test_ambiguous_electrode_thickness_keeps_existing_scope_guard(meaning):
    path = "cathode.electrode_thickness"
    params = {"cathode.L_um": row(None, "um"), path: row(70, "um", meaning=meaning)}
    assert effective_parameters(params)["cathode.L_um"]["value"] is None
    rows = parameter_view({"parameters": params})
    assert {r["canonical_key"] for r in rows} == {"cathode.L_um", path}
    with pytest.raises(ValueError, match="物理口径冲突"):
        checked_name(path, dict(params[path], canonical_path="cathode.L_um"))


def test_conflicting_alias_values_remain_visible_and_do_not_resolve_null():
    params = sample()
    params["cathode.total_loading"] = row(6)
    assert effective_parameters(params)[TOTAL]["value"] is None
    assert any(i["code"] == "DECLARATION_CONFLICT" for i in audit(params)["issues"])
    assert TOTAL in missing_suggestions({"parameters": params})
    values = {r["value"] for r in parameter_view({"parameters": params}) if r["canonical_key"] == TOTAL}
    assert values == {None, 5.625, 6}


def test_equal_values_with_convertible_units_merge_sources():
    params = {TOTAL: row(5.625), ALIAS: row(.005625, "g/cm2", source="另一条等值来源")}
    rows = parameter_view({"parameters": params})
    assert len(rows) == 1 and len(rows[0]["source_fields"]) == 2
    assert not audit(params)["issues"]


@pytest.mark.parametrize("meaning", ["单面活性物质面载量", "含集流体的总面载量", "湿涂层总面载量"])
def test_total_name_with_conflicting_meaning_is_rejected(meaning):
    params = {TOTAL: row(None), ALIAS: row(5.625, meaning=meaning)}
    assert effective_parameters(params)[TOTAL]["value"] is None
    with pytest.raises(ValueError, match="冲突|不能混用"):
        checked_name(ALIAS, params[ALIAS])


@pytest.mark.parametrize("meaning", ["干涂层总面载量，不含集流体，包含活性物和粘结剂",
                                    "干涂层总面载量，不包含集流体", "干涂层总面载量，并非湿膜质量",
                                    "干涂层总面载量，不是湿涂层质量，不包括铝箔"])
def test_dry_coating_description_and_condition_refs_keep_correct_values(meaning):
    from auto_battery_research.util.parameter_presentation import render_conditions
    params = sample()
    params[ALIAS]["meaning"] = meaning
    assert effective_parameters(params)[TOTAL]["value"] == 5.625
    assert "5.625" in render_conditions([{"name": "总面载量", "ref": TOTAL}], parameters=params)


@pytest.mark.parametrize("value,unit", [(5.625, "kg"), (float("nan"), "mg/cm2"), (-1, "mg/cm2"), (True, "mg/cm2")])
def test_invalid_values_cannot_hide_missing_parameter(value, unit):
    params = {TOTAL: row(None), ALIAS: row(value, unit)}
    assert effective_parameters(params)[TOTAL]["value"] is None
    assert audit(params)["issues"]


def test_locked_null_cannot_be_filled_or_bypassed_through_new_alias(tmp_path):
    from auto_battery_research.util import scheme_ledger
    params = {TOTAL: row(None, locked=True), ALIAS: row(5.625)}
    assert effective_parameters(params)[TOTAL]["value"] is None
    proposal = row(5.625)
    proposal.pop("locked")
    with pytest.raises(ValueError, match="同义"):
        declarations({"parameters": {ALIAS: proposal}}, {TOTAL: params[TOTAL]})
    scheme_ledger.ensure(tmp_path)
    ledger = scheme_ledger.update(tmp_path, {TOTAL: row(None)}, 0, actor="user")
    with pytest.raises(ValueError, match="锁定"):
        scheme_ledger.update(tmp_path, {ALIAS: row(5.625)}, ledger["revision"])
    assert scheme_ledger.load(tmp_path)["revision"] == ledger["revision"]


@pytest.mark.parametrize("scope", [{"group_id": "control"}, {"purpose": "窄窗对照"}, {"quantity_role": "target"}])
def test_main_lock_does_not_reject_separate_sample_or_role(tmp_path, scope):
    from auto_battery_research.util import scheme_ledger
    scheme_ledger.ensure(tmp_path)
    ledger = scheme_ledger.update(tmp_path, {TOTAL: row(5.625)}, 0, actor="user")
    ledger = scheme_ledger.update(tmp_path, {"cathode.other_loading": row(4, canonical_path=TOTAL, **scope)}, ledger["revision"])
    assert ledger["parameters"][TOTAL]["value"] == 5.625
    assert ledger["parameters"][TOTAL]["locked"]
    assert ledger["parameters"]["cathode.other_loading"]["value"] == 4
    assert not ledger["issues"]


def test_physical_projection_adopts_total_alias_but_never_calculates_new_values():
    from auto_battery_research.util.physical_parameters import fixed_geometry, record_geometry_sources
    from auto_battery_research.util.scheme_ledger import project_scheme_parameters
    params = sample()
    fixed = fixed_geometry(params)
    assert fixed[TOTAL]["value"] == 5.625 and "cathode.L_um" not in fixed
    geometry = {"cathode": {}, "anode": {}}
    record_geometry_sources(geometry, fixed)
    projected = project_scheme_parameters({"geometry": geometry}, params)
    assert projected[TOTAL]["value"] == 5.625 and projected[TOTAL]["projection_source_path"] == ALIAS
    assert params[TOTAL]["value"] is None
    assert audit(projected)["derived"] == {}
