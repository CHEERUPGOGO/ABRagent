"""显式历史计算诊断兼容；当前设计主流程不再调用这些派生公式。"""
import copy
from functools import partial

import pytest

from auto_battery_research.util.numeric_consistency import audit
from auto_battery_research.util.numeric_bindings import render
from auto_battery_research.util.scheme_ledger import calculate

audit = partial(audit, legacy_calculations=True)
render = partial(render, legacy_calculations=True)
calculate = partial(calculate, legacy_calculations=True)


def row(value, unit="", **meta):
    return dict({"value": value, "unit": unit, "basis": "engineering_proposal", "source": "当前同工况设计声明"}, **meta)


def example(comp="cathode"):
    return {comp + ".active_loading_mg_cm2": row(4.5, "mg/cm2"), comp + ".active_fraction": row(.8),
            comp + ".L_um": row(25, "um"), comp + ".porosity": row(.44),
            comp + ".electrode_area": row(1.13, "cm2"), comp + ".capacity_mAh_g": row(200, "mAh/g")}


@pytest.mark.parametrize("comp", ["cathode", "anode"])
def test_active_loading_supplies_total_density_and_capacity_without_writeback(comp):
    params = example(comp)
    original = copy.deepcopy(params)
    d = audit(params)["derived"]
    assert d[comp + ".calculated_loading_mg_cm2"]["value"] == pytest.approx(5.625)
    assert d[comp + ".calculated_compaction_g_cm3"]["value"] == pytest.approx(2.25)
    assert d[comp + ".areal_capacity_mAh_cm2"]["value"] == pytest.approx(.9)
    assert d[comp + ".calculated_active_loading_mg_cm2"]["status"] == "DECLARED"
    assert d[comp + ".calculated_loading_mg_cm2"]["paths"] == [comp + ".active_loading_mg_cm2", comp + ".active_fraction"]
    assert params == original and comp + ".compaction_g_cm3" not in params
    if comp == "cathode":
        text = render("总面载量{{derived:cathode.calculated_loading_mg_cm2}}；"
                      "压实密度{{derived:cathode.calculated_compaction_g_cm3}}。", params)
        assert "5.625" in text and "2.25" in text and "未完成" not in text
        ledger, _ = calculate({"parameters": params})
        assert ledger["cathode.calculated_loading_mg_cm2"]["value"] == 5.625
        assert ledger["cathode.areal_capacity_mAh_cm2"]["value"] == .9
        assert ledger["cathode.calculated_compaction_g_cm3"]["routes"]


def test_all_available_loading_paths_are_checked():
    params = example()
    params["cathode.compaction_g_cm3"] = row(2.25, "g/cm3")
    result = audit(params)
    loading = result["derived"]["cathode.calculated_loading_mg_cm2"]
    assert len(loading["routes"]) == 2
    assert {r["value"] for r in loading["routes"]} == {5.625}
    assert not result["issues"]
    params["cathode.compaction_g_cm3"]["value"] = 3
    result = audit(params)
    loading = result["derived"]["cathode.calculated_loading_mg_cm2"]
    assert loading["status"] == "CONFLICT"
    assert {r["value"] for r in loading["routes"]} == {5.625, 7.5}
    assert result["derived"]["cathode.areal_capacity_mAh_cm2"]["value"] is None
    assert any(i["code"] == "CALCULATION_CONFLICT" for i in result["issues"])


def test_inverse_loading_thickness_and_pores_feed_ep_with_explicit_units():
    params = {"cathode.loading_mg_cm2": row(.005625, "g/cm2"),
              "cathode.compaction_g_cm3": row(2250, "kg/m3"),
              "cathode.porosity": row(.4), "cathode.electrode_area": row(100, "mm2"),
              "electrolyte.volume": row(.05, "mL")}
    d = audit(params)["derived"]
    assert d["cathode.calculated_thickness_um"]["value"] == pytest.approx(25)
    assert d["cathode.pore_volume_uL_cm2"]["value"] == pytest.approx(1)
    assert d["cathode.calculated_pore_volume_uL"]["value"] == pytest.approx(1)
    assert d["condition.calculated_electrolyte_to_cathode_pore_ratio"]["value"] == pytest.approx(50)


def test_declared_total_pores_and_area_supply_ep_without_thickness():
    params = {"cathode.pore_volume": row(2, "uL"), "cathode.electrode_area": row(2, "cm2"),
              "electrolyte.volume": row(60, "uL")}
    d = audit(params)["derived"]
    assert d["cathode.pore_volume_uL_cm2"]["value"] == 1
    assert d["cathode.calculated_pore_volume_uL"]["status"] == "DECLARED"
    assert d["condition.calculated_electrolyte_to_cathode_pore_ratio"]["value"] == 30


def test_measured_areal_capacity_and_specific_capacity_supply_loading():
    params = {"cathode.areal_capacity_mAh_cm2": row(.9, "mAh/cm2"),
              "cathode.capacity_mAh_g": row(200, "mAh/g"), "cathode.active_fraction": row(.8)}
    d = audit(params)["derived"]
    assert d["cathode.calculated_active_loading_mg_cm2"]["value"] == pytest.approx(4.5)
    assert d["cathode.calculated_loading_mg_cm2"]["value"] == pytest.approx(5.625)
    assert d["cathode.areal_capacity_mAh_cm2"]["status"] == "DECLARED"


@pytest.mark.parametrize("extra", [{"group_id": "G2"}, {"purpose": "应力测试"},
                                 {"conditions": {"temperature_C": 40}}, {"basis": "literature"}])
def test_alternative_formula_cannot_mix_sample_or_conditions(extra):
    params = {"cathode.active_loading_mg_cm2": row(4.5, "mg/cm2"), "cathode.active_fraction": row(.8, **extra)}
    assert audit(params)["derived"]["cathode.calculated_loading_mg_cm2"]["value"] is None


def test_missing_capacity_or_zero_denominator_cannot_create_values():
    params = {"cathode.active_loading_mg_cm2": row(4.5, "mg/cm2"), "cathode.active_fraction": row(0),
              "condition.C_rate_reference_current": row(250, "mA/g")}
    d = audit(params)["derived"]
    assert d["cathode.calculated_loading_mg_cm2"]["value"] is None
    assert d["cathode.areal_capacity_mAh_cm2"]["value"] is None
    assert d["cathode.calculated_compaction_g_cm3"]["value"] is None


def test_ambiguous_declared_value_is_not_bypassed_by_an_alternate_path():
    params = example()
    params["cathode.loading_mg_cm2"] = row(5.625, "mg/cm2")
    params["cathode.other_loading"] = row(8, "mg/cm2", canonical_path="cathode.loading_mg_cm2")
    d = audit(params)["derived"]
    assert d["cathode.calculated_loading_mg_cm2"]["status"] == "INCOMPLETE"
    density = d["cathode.calculated_compaction_g_cm3"]
    assert density["value"] == pytest.approx(2.25)
    assert "cathode.loading_mg_cm2" not in density["paths"]
    assert "cathode.other_loading" not in density["paths"]
