"""结构化几何跨阶段传递与证据门禁回归（完全离线）。"""
import copy
import pytest

from auto_battery_research.simulation.geometry_contract import (
    build_geometry_contract, geometry_hash, validate_diffusion_evidence, validate_geometry_contract,
)
from auto_battery_research.tools.geometry_revision import apply_geometry_revision
from pinn.input_spec import build_cell_spec


def candidate():
    return {"cathode": {"L_um": 72., "porosity": .28, "R_s_um": 4., "D_s_m2s": None},
            "anode": {"L_um": 95., "porosity": .5, "R_s_um": 6., "D_s_m2s": None}}


def scheme():
    s = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce"}
    s["geometry"] = build_geometry_contract(candidate(), s, [])
    s["design_revision"] = 0
    return s


def test_baseline_is_not_fabricated_design():
    s = scheme()
    g = s["geometry"]
    assert g["provenance"]["cathode.D_s_m2s"] == "params_baseline"
    assert g["parameter_sources"]["cathode.D_s_m2s"]["origin"] == "model_baseline"
    spec = build_cell_spec(s, {})
    assert spec["geometry_hash"] == geometry_hash(g)
    assert spec["cathode"]["material"]["R_p"] == pytest.approx(4e-6)


@pytest.mark.parametrize("value", [True, "72", float("nan"), float("inf")])
def test_strict_numbers(value):
    c = candidate()
    c["cathode"]["L_um"] = value
    with pytest.raises(ValueError):
        build_geometry_contract(c, {"cathode": "NCM811", "anode": "si_base"}, [])


def test_revision_identity_and_diffusion_rejection_are_atomic():
    s = scheme()
    old = copy.deepcopy(s)
    assert apply_geometry_revision(s, {"cathode": {"R_s_um": 4.5}})["error_code"] == "GEOMETRY_REVISION_STALE"
    assert s == old
    identity = {"base_revision": 0, "base_geometry_hash": geometry_hash(s["geometry"])}
    assert apply_geometry_revision(s, {"cathode": {"R_s_um": 4.5, "D_s_m2s": 2e-14}}, **identity)["error_code"] == "DIFFUSION_EVIDENCE_INVALID"
    assert s == old
    assert apply_geometry_revision(s, {"cathode": {"R_s_um": 4.5}}, **identity)["ok"]
    assert s["design_revision"] == 1
    assert s["geometry"]["parameter_sources"]["cathode.R_s_um"]["basis"] == "engineering_proposal"
    assert not apply_geometry_revision(s, {"anode": {"L_um": 100.}}, **identity)["ok"]


def test_diffusion_evidence_conversion_and_forgery():
    text = "NCM811 constant solid-phase diffusion coefficient: 2e-10 cm2/s at 25°C, identified by fitting."
    ev = [{"passage_id": "p1", "text": text}]
    claim = {"passage_id": "p1", "quote": text, "material_id": "NCM811",
             "parameter": "solid_diffusion_coefficient", "value": 2e-10, "unit": "cm2/s",
             "temperature_K": 298.15, "applicability": "constant_full_soc", "method": "fitting"}
    assert validate_diffusion_evidence(claim, 2e-14, "NCM811", ev)["basis"] == "evidence_verified"
    s = scheme()
    assert apply_geometry_revision(s, {"cathode": {"D_s_m2s": 2e-14}},
        base_revision=0, base_geometry_hash=geometry_hash(s["geometry"]),
        evidence_claims={"cathode.D_s_m2s": claim}, evidence=ev)["ok"]
    with pytest.raises(ValueError):
        validate_diffusion_evidence(claim, 4e-14, "NCM811", ev)
    with pytest.raises(ValueError):
        validate_diffusion_evidence(claim, 2e-14, "si_base", ev)
    with pytest.raises(ValueError):
        validate_diffusion_evidence(claim, 2e-14, "NCM811", [])


def test_baseline_tag_cannot_hide_changed_diffusion():
    s = scheme()
    assert validate_geometry_contract(s["geometry"], s, []) == []
    s["geometry"]["cathode"]["D_s_m2s"] *= 2
    assert any("基准" in e for e in validate_geometry_contract(s["geometry"], s, []))


def test_initial_geometry_rejects_unverified_diffusion():
    c = candidate()
    c["cathode"]["D_s_m2s"] = 2e-14
    with pytest.raises(ValueError, match="证据"):
        build_geometry_contract(c, {"cathode": "NCM811", "anode": "si_base"}, [])
