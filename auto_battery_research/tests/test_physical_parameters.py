"""回归：双掺课题中的极片厚度、主测试上限和窄窗对照。"""
import pytest

from auto_battery_research.util import scheme_ledger as ledger
from auto_battery_research.util.physical_parameters import fill_missing_candidate, fixed_geometry
from auto_battery_research.util.parameter_presentation import parameter_view


def field(value, unit, **kwargs):
    return dict(value=value, unit=unit, basis="engineering_proposal", source="首轮方案", **kwargs)


@pytest.mark.parametrize("key,value,alias,other,unit", [
    ("cathode.L_um", 70, "cathode.electrode_thickness", 18.75, "um"),
    ("condition.v_max", 4.6, "condition.voltage.upper", 4.8, "V"),
])
def test_alias_conflict_does_not_commit(tmp_path, key, value, alias, other, unit):
    ledger.ensure(tmp_path)
    with pytest.raises(ValueError, match="同一工况"):
        ledger.update(tmp_path, {key: field(value, unit), alias: field(other, unit, quantity_kind="physical")}, 0)
    assert ledger.load(tmp_path)["revision"] == 0


def test_narrow_window_is_independent(tmp_path):
    ledger.ensure(tmp_path)
    params = {"condition.v_max": field(4.8, "V"), "condition.voltage.upper": field(4.8, "V"),
              "condition.control_window.upper": field(4.6, "V", canonical_path="condition.v_max", conditions={"purpose": "窄窗对照"})}
    data = ledger.update(tmp_path, params, 0)
    assert not data["issues"]
    assert fixed_geometry(params)["condition.v_max"]["value"] == 4.8
    assert len([r for r in parameter_view(data) if r['canonical_key'] == 'condition.v_max']) == 2


def test_geometry_inherits_only_declarations_and_does_not_calculate_budget():
    params = {"cathode.total_loading": field(5.625, "mg/cm2"), "cathode.compaction_density": field(3, "g/cm3"),
              "condition.voltage.upper": field(4.8, "V"), "cathode.porosity": field(.3, ""),
              "cathode.electrode_area": field(1.13, "cm2"), "electrolyte.volume": field(60, "uL")}
    focus = fixed_geometry(params)
    proposed = {"cathode": {"L_um": 70, "R_s_um": 3}, "condition": {"v_max": 4.6}}
    selected = fill_missing_candidate(proposed, focus)
    assert 'cathode.L_um' not in focus
    assert selected["cathode"] == {"L_um": 70, "R_s_um": 3, "porosity": .3}
    assert selected["condition"]["v_max"] == 4.8 and proposed["cathode"]["L_um"] == 70
    params["cathode.L_um"] = dict(field(70, "um"), source="模型明确提出的厚度，计算一致性待Reviewer核对")
    assert fixed_geometry(params)['cathode.L_um']['value'] == 70
    derived, errors = ledger.calculate({"parameters": params})
    assert not errors and derived == {}


def test_active_mass_or_collector_thickness_does_not_set_coating_geometry():
    params = {"cathode.active_material_loading": field(4.5, "mg/cm2"), "cathode.compaction_density": field(3, "g/cm3"),
              "cathode.electrode_thickness": field(70, "um", meaning="含集流体的极片厚度")}
    assert 'cathode.L_um' not in fixed_geometry(params)


def test_experiment_alias_ref_cannot_cross_windows():
    from auto_battery_research.util.experiment_consistency import parameter_issues
    rows = {"condition.v_max": field(4.8, "V"),
            "condition.voltage_max": field(4.6, "V", conditions={"purpose": "窄窗对照"}),
            "cathode.electrode_thickness": field(18.75, "um"), "cathode.L_um": field(18.75, "um")}
    doc = {"ledger_view": {"parameters": rows}}
    record = {"consistency": {}, "parameters": {"cathode.electrode_thickness": {"ref": "cathode.L_um"}}}
    assert parameter_issues(record, doc) == []
    record["parameters"]["condition.voltage_max"] = {"ref": "condition.v_max"}
    assert parameter_issues(record, doc)[0]["path"] == ["parameters", "condition.voltage_max"]
