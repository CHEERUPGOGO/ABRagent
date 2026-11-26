"""PINN 正极母体匹配的完整输入与推理回归，全部离线。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.simulation import build_cell_spec, preflight_pinn_inputs, run_pinn_discharge, run_pinn_charge
from auto_battery_research.simulation.geometry_contract import build_geometry_contract, geometry_hash, validate_geometry_contract
from auto_battery_research.tools.geometry_revision import resolve_input_ranges
from auto_battery_research.tools.workflow_actions import _match_pinn_system_for_scheme, _generate_pinn_input_spec, _run_pinn_inference_if_triggered
from auto_battery_research.util.hashing import stage5_scheme_projection
from auto_battery_research.util.material_ids import assemble_components, pinn_material_ids
from auto_battery_research.util.stage5_execution import physics_exemption


def declared_parameters(parent="NCM811"):
    return {"cathode.nominal_formula": {"value": "少量掺杂的 NCM811", "scope": "main"},
            "cathode.base_formula": {"value": parent, "scope": "main"},
            "anode.material": {"value": "si_base"},
            "electrolyte.material": {"value": "carbonate_ec"}}


def doped_scheme():
    scheme = assemble_components({}, declared_parameters())
    scheme["geometry"] = build_geometry_contract(
        {"cathode": {"L_um": 70, "porosity": .31},
         "anode": {"L_um": 110, "porosity": .56}, "condition": {"c_rate": .5}}, scheme, [])
    scheme["geometry_hash"] = geometry_hash(scheme["geometry"])
    return scheme


def test_parent_matching_drives_baselines_ranges_and_real_inference():
    scheme = doped_scheme()
    original = copy.deepcopy(scheme)
    assert pinn_material_ids(scheme) == {"cathode": "NCM811", "anode": "si_base"}
    assert _match_pinn_system_for_scheme(scheme)["system_id"] == "GrSi_NMC811"
    assert validate_geometry_contract(scheme["geometry"], scheme, []) == []
    assert scheme["geometry"]["parameter_sources"]["cathode.D_s_m2s"]["origin"] == "model_baseline"
    assert resolve_input_ranges(scheme["cathode"], scheme["anode"], scheme=scheme) == resolve_input_ranges("NCM811", "si_base")
    spec = build_cell_spec(scheme, {})
    assert spec["cathode"]["material"]["name"] == scheme["cathode"]
    assert preflight_pinn_inputs(spec, scheme)["status"] == "VALID"
    result = run_pinn_discharge(spec, scheme)
    assert result["status"] == "CONVERGED"
    assert result["system_id"] == "GrSi_NMC811"
    assert run_pinn_charge(spec, scheme)["status"] == "CHARGE_NOT_AVAILABLE"
    # 不传原方案时，spec中记录的母体匹配口径仍驱动同一推理。
    assert run_pinn_discharge(spec)["energy_wh_kg"] == result["energy_wh_kg"]
    assert scheme == original


def test_workflow_trigger_and_inference_use_same_parent(tmp_path):
    scheme = doped_scheme()
    (tmp_path / "design_scheme.json").write_text(json.dumps({"scheme": scheme}), encoding="utf-8")
    mgr = SimpleNamespace(target_goal="母体匹配离线回归", config={"pinn_trigger": {"enabled": True}},
                          get_task_output_dir=lambda: tmp_path, get_stage_by_id=lambda _: SimpleNamespace(skip=False))
    extraction = _generate_pinn_input_spec(mgr.target_goal, mgr, tmp_path)
    assert extraction["success"]
    spec = json.loads((tmp_path / "pinn_input_spec.json").read_text("utf-8"))
    assert spec["trigger"]["triggered"]
    assert spec["scheme"]["cathode"] == scheme["cathode"]
    assert spec["cell_spec"]["pinn_material_ids"]["cathode"] == "NCM811"
    result = _run_pinn_inference_if_triggered(extraction, mgr, tmp_path, .5)
    assert result["status"] == "CONVERGED", result


def test_missing_full_identity_can_match_explicit_parent_without_mutating_scheme():
    parameters = declared_parameters("Li1.2Mn0.6Ni0.2O2")
    parameters.pop("cathode.nominal_formula")
    parameters["cathode.doping.rank1_formula"] = {"value": "Li1.17Na0.03Mn0.57Zn0.03Ni0.2O2"}
    parameters["anode.material"]["value"] = "li_metal"
    scheme = assemble_components({}, parameters)
    assert "cathode" not in scheme
    match = _match_pinn_system_for_scheme(scheme, parameters)
    assert match["status"] == "NO_MATCH"
    assert match["material_ids"] == {"cathode": "LRMO", "anode": "li_metal"}
    doc = {"scheme": scheme, "ledger_view": {"parameters": parameters}}
    assert "没有匹配" in physics_exemption(doc)
    assert "cathode" not in scheme


def test_parent_conflict_stale_source_and_wrong_anode_do_not_match():
    scheme = assemble_components({}, declared_parameters())
    assert not _match_pinn_system_for_scheme(dict(scheme, anode="li_metal"))["matched"]
    stale = copy.deepcopy(scheme)
    stale["cathode"] = "另一种掺杂材料"
    assert not _match_pinn_system_for_scheme(stale)["matched"]
    parameters = declared_parameters()
    parameters["cathode.parent_formula"] = {"value": "LFP"}
    with pytest.raises(ValueError, match="母体声明冲突"):
        pinn_material_ids(scheme, parameters)


def test_parent_alias_is_explicit_and_changes_result_identity():
    parameters = declared_parameters()
    parameters["cathode.open_parent"] = dict(parameters.pop("cathode.base_formula"), canonical_path="cathode.base_formula")
    scheme = assemble_components({}, parameters)
    assert _match_pinn_system_for_scheme(scheme)["matched"]
    changed = assemble_components(scheme, dict(parameters, **{"cathode.open_parent": {"value": "LFP", "canonical_path": "cathode.base_formula"}}))
    assert stage5_scheme_projection(scheme) != stage5_scheme_projection(changed)
    parameters["cathode.open_parent"]["scope"] = "candidate"
    assert not _match_pinn_system_for_scheme(assemble_components({}, parameters))["matched"]


def test_parent_matched_geometry_still_rejects_out_of_range_inputs():
    scheme = doped_scheme()
    scheme["geometry"]["cathode"]["R_s_um"] = 100
    result = preflight_pinn_inputs(build_cell_spec(scheme, {}), scheme)
    assert result["status"] == "OUT_OF_RANGE"
