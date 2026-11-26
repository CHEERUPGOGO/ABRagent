"""主方案字段语义修复：活性与总载量分离、别名投影、半电池口径与参数保护。"""
import copy

import pytest

from auto_battery_research.util.design_projection_repair import repair_parameters, rebind_tree
from auto_battery_research.util.material_ids import assemble_components
from auto_battery_research.util.design_presentation import render_design
from auto_battery_research.util.physical_parameters import fixed_geometry


def parameters():
    def row(value, unit="", meaning="工程声明", **extra):
        return {**dict(value=value, unit=unit, meaning=meaning, source="原设计", basis="engineering_proposal", locked=False), **extra}
    return {"revision": 2, "goal": "材料实验", "history": [], "parameters": {
        "cathode.doping.nominal_formula": row("Li1.18Na0.02Mn0.6Ni0.18Zn0.02O2", quantity_kind="text"),
        "cathode.base_formula": row("Li1.2Mn0.6Ni0.2O2", locked=True),
        "anode.material": row("li_metal", locked=True), "electrolyte.formulation": row("1 mol/L LiPF6/EC+DMC", locked=True),
        "cathode.loading_mg_cm2": row(4.5, "mg/cm2", "活性物质面载量；总载量须按质量占比换算"),
        "cathode.active_fraction": row(.8), "cathode.L_um": row(25, "um"), "cathode.porosity": row(.32),
        "cathode.compaction_g_cm3": row(None, "g/cm3"), "cathode.compaction_density_g_cm3": row(2.25, "g/cm3"),
        "separator.type": row("Celgard 2400", quantity_kind="text"),
        "condition.voltage_window": row("2.0–4.6 V", quantity_kind="text"),
        "condition.high_voltage_window": row("2.0–4.8 V", quantity_kind="text"),
        "condition.v_min": row(None, "V"), "condition.v_max": row(None, "V"), "condition.c_rate": row(1, "C"),
        "condition.C_rate_reference_current": row(250, "mA/g"), "condition.cycles": row(200, "cycle"),
        "electrolyte.ec_dmc_ratio": row("EC:DMC=1:1", quantity_kind="text")}}


def test_projection_repairs_declared_values_and_renders_complete_half_cell():
    data = parameters()
    before = copy.deepcopy(data)
    repaired, mapping = repair_parameters(data, "半电池不做 N/P 校准。")
    assert data == before
    p = repaired["parameters"]
    assert p["cathode.active_loading_mg_cm2"]["value"] == 4.5
    assert p["cathode.loading_mg_cm2"]["value"] == 5.625
    assert p["cathode.compaction_g_cm3"]["value"] == 2.25
    assert p["condition.v_min"]["value"] == 2 and p["condition.v_max"]["value"] == 4.6
    assert p["condition.high_voltage_v_max"]["value"] == 4.8
    assert fixed_geometry(p)["condition.v_max"]["value"] == 4.6
    assert not repaired["issues"]
    scheme = assemble_components({}, p)
    assert scheme["cathode"] == p["cathode.doping.nominal_formula"]["value"]
    assert scheme["separator"] == "Celgard 2400"
    body = render_design(repaired)
    assert "正极的具体规格尚未确定" not in body
    assert "活性物质面载量为**4.5 mg/cm²**" in body
    assert "5.625" in body and "2.25" in body
    assert "N/P不作为本轮" in body and "容量匹配尚未闭合" not in body
    assert "1 C换算基准" in body and "工程设定" in body
    assert "材料为**Celgard 2400**" in body
    assert p["cathode.base_formula"] == before["parameters"]["cathode.base_formula"]
    assert "cathode.D_s_m2s" not in p


@pytest.mark.parametrize("fault", ["locked", "ambiguous", "density", "voltage", "half_cell"])
def test_projection_never_overwrites_conflicting_or_undeclared_inputs(fault):
    data = parameters()
    text = "半电池不做 N/P 校准。"
    if fault == "locked": data["parameters"]["cathode.loading_mg_cm2"]["locked"] = True
    elif fault == "ambiguous": data["parameters"]["cathode.loading_mg_cm2"]["meaning"] = "涂层面载量"
    elif fault == "density": data["parameters"]["cathode.compaction_density_g_cm3"]["value"] = 2.8
    elif fault == "voltage": data["parameters"]["condition.v_max"]["value"] = 4.9
    else: text = "全电池容量匹配。"
    original = copy.deepcopy(data)
    with pytest.raises(ValueError): repair_parameters(data, text)
    assert data == original


def test_rebind_updates_live_references_and_preserves_raw_audit():
    value = {"parameters": {"cathode.loading_mg_cm2": {"value": 4.5}},
             "text": "活性载量{{cathode.loading_mg_cm2}}，{{param:cathode.loading_mg_cm2}}。",
             "raw_response": "{{cathode.loading_mg_cm2}}", "repair_audit": [{"field": "cathode.loading_mg_cm2"}]}
    result = rebind_tree(value, {"cathode.loading_mg_cm2": "cathode.active_loading_mg_cm2"})
    assert "cathode.active_loading_mg_cm2" in result["parameters"]
    assert result["text"].count("cathode.active_loading_mg_cm2") == 2
    assert result["raw_response"] == value["raw_response"] and result["repair_audit"] == value["repair_audit"]
