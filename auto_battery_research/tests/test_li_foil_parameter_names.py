"""锂箔厚度规范身份贯通Writer、几何恢复与报告，保留物理边界与用户锁定。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.parameter_names import INSTRUCTIONS, checked_name, effective_parameters
from auto_battery_research.util.numeric_consistency import canonical, audit
from auto_battery_research.util.physical_parameters import fixed_geometry
from auto_battery_research.util.parameter_presentation import parameter_view, render_parameter_appendix
from auto_battery_research.util.report_narrative import render_design, missing_suggestions
from auto_battery_research.simulation.geometry_contract import geometry_from_declared_parameters, validate_geometry_contract

ALIAS = "anode.li_foil_thickness_um"
CANONICAL = "anode.L_um"


def field(value, unit="", **extra):
    return dict({"value": value, "unit": unit, "basis": "engineering_proposal", "source": "原始方案声明"}, **extra)


def parameters():
    return {"cathode.material": field("LRMO"), "anode.material": field("li_metal"),
            "electrolyte.formulation": field("1 mol/L LiPF6/EC+DMC"),
            "cathode.L_um": field(18.75, "um"), "cathode.porosity": field(.3),
            ALIAS: field(500, "um", meaning="锂金属负极箔厚度")}


def scheme():
    return {"cathode": "LRMO", "anode": "li_metal", "electrolyte": "1 mol/L LiPF6/EC+DMC"}


def test_prompts_explicitly_define_lithium_foil_canonical_thickness():
    from auto_battery_research.rag import WriterAgent
    from auto_battery_research.tests.test_design_structure import document
    data = dict(document(), version=2, parameters={})
    requests = []
    def chat(system, user, **kwargs):
        requests.append((system, user))
        return json.dumps(data, ensure_ascii=False)
    result = WriterAgent(SimpleNamespace(available=True, chat=chat)).run("设计锂金属负极方案", {},
        [{"passage_id": "p1", "source": "离线测试资料", "text": "方案仍需实验核验。"}])
    assert not result["fallback"], result
    assert "锂箔本体厚度" in requests[0][0] and CANONICAL in requests[0][0]
    assert "致密金属锂负极的锂箔厚度" in requests[0][1]
    assert 'canonical_path="anode.L_um"' in requests[0][1]
    assert "不套用干涂层质量组成" in INSTRUCTIONS


def test_original_writer_alias_keeps_path_value_source_and_registers_identity():
    from auto_battery_research.util.writer_protocol import produce
    from auto_battery_research.tests.test_design_structure import document
    data = dict(document(), version=2, parameters={ALIAS: parameters()[ALIAS]})
    data["sections"]["goals"]["body"] += "金属锂负极厚度{{param:" + ALIAS + "}}。"
    raw = json.dumps(data, ensure_ascii=False)
    state = {"raw_response": raw}
    current = {"anode.material": field("li_metal", locked=True)}
    result = produce(SimpleNamespace(available=False), "s", "u", current, state, lambda: None)
    assert not result["fallback"], result
    row = result["numeric_parameters"][ALIAS]
    assert row["canonical_path"] == CANONICAL and row["value"] == 500
    assert row["source"] == parameters()[ALIAS]["source"]
    assert "500 μm" in result["draft_answer"] and state["raw_response"] == raw
    assert result["numeric_parameters"]["anode.material"]["locked"] is True


def test_geometry_recovers_foil_and_uses_existing_dense_metal_rule_without_mutation():
    params = parameters()
    before = copy.deepcopy(params)
    candidate, geometry = geometry_from_declared_parameters(params, scheme(), [])
    assert candidate["anode"]["L_um"] == 500
    assert "porosity" not in candidate["anode"]  # 由既有致密锂箔构建规则处理，不伪造模型声明。
    assert geometry["anode"]["L_um"] == 500 and geometry["anode"]["porosity"] == 0
    assert geometry["anode"]["R_s_um"] is None and geometry["anode"]["D_s_m2s"] is None
    assert ALIAS in geometry["parameter_sources"][CANONICAL]["declaration"]
    assert not validate_geometry_contract(geometry, scheme(), [])
    assert params == before


def test_projection_retains_alias_source_and_report_labels_foil_not_coating():
    from auto_battery_research.util.scheme_ledger import project_scheme_parameters
    params = parameters()
    _, geometry = geometry_from_declared_parameters(params, scheme(), [])
    projected = project_scheme_parameters(dict(scheme(), geometry=geometry), params)
    assert projected[CANONICAL]["value"] == 500
    assert projected[CANONICAL]["projection_source_path"] == ALIAS
    view = parameter_view({"parameters": projected})
    foil = [row for row in view if row["canonical_key"] == CANONICAL]
    assert len(foil) == 1 and set(foil[0]["source_fields"]) == {ALIAS, CANONICAL}
    body = render_design({"parameters": projected})
    assert "**负极配置。** 锂箔厚度为**500 μm**" in body
    appendix = render_parameter_appendix({"parameters": projected})
    assert appendix.count("锂箔厚度：500 μm") == 1
    assert "负极涂层" not in body + appendix


def test_raw_current_ledger_without_canonical_field_displays_existing_foil():
    params = parameters()
    body = render_design({"parameters": params})
    assert "**负极配置。** 锂箔厚度为**500 μm**" in body
    assert "具体极片几何和面载量尚未完整声明" not in body
    params[CANONICAL] = field(None, "um")
    assert effective_parameters(params)[CANONICAL]["value"] == 500
    assert CANONICAL not in missing_suggestions({"parameters": params})


@pytest.mark.parametrize("metadata", [{"group_id": "control"}, {"scope": "candidate"},
                                      {"purpose": "敏感性测试"}, {"conditions": {"温度": 60}},
                                      {"basis": "literature"}, {"quantity_role": "lower_bound"}])
def test_scoped_foil_cannot_fill_main_thickness(metadata):
    params = parameters()
    params[ALIAS].update(metadata)
    params[CANONICAL] = field(None, "um")
    assert CANONICAL not in fixed_geometry(params)
    assert effective_parameters(params)[CANONICAL]["value"] is None
    with pytest.raises(ValueError, match="anode.L_um"):
        geometry_from_declared_parameters(params, scheme(), [])


@pytest.mark.parametrize("value,unit", [(0, "um"), (-1, "um"), (float("inf"), "um"), (True, "um"), (500, "kg")])
def test_invalid_foil_values_and_units_are_rejected(value, unit):
    params = parameters()
    params[ALIAS].update(value=value, unit=unit)
    with pytest.raises(ValueError):
        geometry_from_declared_parameters(params, scheme(), [])
    assert audit(params)["issues"]


def test_conflicting_canonical_thickness_and_locks_remain_protected(tmp_path):
    from auto_battery_research.util.scheme_ledger import ensure, update
    params = parameters()
    params[CANONICAL] = field(50, "um")
    with pytest.raises(ValueError, match="同一工况"):
        fixed_geometry(params)
    assert any(i["code"] == "DECLARATION_CONFLICT" for i in audit(params)["issues"])
    ledger = ensure(tmp_path)
    ledger = update(tmp_path, {CANONICAL: field(None, "um")}, ledger["revision"], actor="user")
    with pytest.raises(ValueError, match="锁定"):
        update(tmp_path, {ALIAS: parameters()[ALIAS]}, ledger["revision"])


def test_dense_rule_does_not_override_locked_null_porosity():
    params = parameters()
    params["anode.porosity"] = field(None, locked=True)
    with pytest.raises(ValueError):
        geometry_from_declared_parameters(params, scheme(), [])


def test_explicit_porosity_is_preserved_instead_of_replaced_by_dense_default():
    params = parameters()
    params["anode.porosity"] = field(.3, locked=True)
    _, geo = geometry_from_declared_parameters(params, scheme(), [])
    assert geo["anode"]["porosity"] == .3
    assert geo["parameter_sources"]["anode.porosity"]["basis"] != "dense_metal_foil"


def test_locked_null_foil_alias_cannot_be_filled_by_canonical_value():
    params = parameters()
    params[ALIAS].update(value=None, locked=True)
    params[CANONICAL] = field(500, "um")
    with pytest.raises(ValueError, match="锁定为空"):
        geometry_from_declared_parameters(params, scheme(), [])


def test_lithium_alias_does_not_supply_silicon_or_collector_geometry():
    params = parameters()
    params["anode.material"]["value"] = "si_base"
    params[CANONICAL] = field(None, "um")
    assert effective_parameters(params)[CANONICAL]["value"] is None
    with pytest.raises(ValueError, match="负极材料不一致"):
        fixed_geometry(params)
    assert any(i["code"] == "PARAMETER_MATERIAL_CONFLICT" for i in audit(params)["issues"])
    assert canonical("current_collector.al_foil_thickness_um") == "current_collector.al_foil_thickness_um"
    assert canonical("anode.protective_layer_thickness_um") == "anode.protective_layer_thickness_um"
    with pytest.raises(ValueError, match="身份冲突"):
        checked_name(ALIAS, field(500, "um", canonical_path="cathode.L_um"))


@pytest.mark.parametrize("meaning", ["含保护层的锂箔总厚度", "锂箔含铜箔的总厚度", "锂负极复合结构总厚度"])
def test_foil_alias_does_not_hide_composite_thickness(meaning):
    with pytest.raises(ValueError, match="本体厚度"):
        checked_name(ALIAS, field(500, "um", meaning=meaning))


def test_foil_description_can_explicitly_exclude_collector_and_protection():
    row = checked_name(ALIAS, field(500, "um", meaning="锂箔本体厚度，不包括集流体，不含保护层"))
    assert row["canonical_path"] == CANONICAL and row["value"] == 500


def test_silicon_porosity_still_requires_explicit_value():
    params = parameters()
    params.pop(ALIAS)
    params["anode.material"]["value"] = "si_base"
    params[CANONICAL] = field(70, "um")
    with pytest.raises(ValueError, match="anode.porosity"):
        geometry_from_declared_parameters(params, dict(scheme(), anode="si_base"), [])
