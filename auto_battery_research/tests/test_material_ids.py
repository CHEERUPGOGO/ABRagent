"""材料标识统一和显式迁移：锁定、来源、模型基准与失败回滚。"""
import copy
import json

import pytest

from auto_battery_research.util.material_ids import canonical_id, canonical_field, source_matches
from auto_battery_research.util.scheme_ledger import ensure, update, load, commit_design, digest
from auto_battery_research.simulation.geometry_contract import build_geometry_contract, validate_geometry_contract, synchronize_material_baselines


def field(value, unit=""):
    return {"value": value, "unit": unit, "basis": "engineering_proposal", "source": "测试设计"}


def test_known_material_aliases_share_one_id_and_unknown_stays_unchanged():
    assert canonical_id("硅碳") == canonical_id("Si-C") == "si_base"
    assert canonical_id("NCM 811") == "NCM811"
    assert canonical_id("未知材料XQ-27") == "未知材料XQ-27"
    row = canonical_field("anode.material", dict(field("硅碳"), source_quote="负极采用硅碳。", locked=True))
    assert row["value"] == "si_base" and row["original_value"] == "硅碳"
    assert row["locked"] and row["source_quote"] == "负极采用硅碳。"
    assert canonical_field("anode.material", row) == row


def test_same_material_name_update_preserves_locked_and_dependent_fields(tmp_path):
    data = ensure(tmp_path)
    data = update(tmp_path, {"anode.material": field("硅碳"), "anode.L_um": field(82, "um")}, data["revision"], actor="user")
    old = copy.deepcopy(data)
    data = update(tmp_path, {"anode.material": field("Si-C")}, data["revision"])
    assert data == old
    assert data["parameters"]["anode.material"]["value"] == "si_base"
    assert data["parameters"]["anode.L_um"]["value"] == 82
    with pytest.raises(ValueError, match="锁定"):
        update(tmp_path, {"anode.material": field("li_metal")}, data["revision"])


def test_alias_in_requirement_quote_normalizes_but_quote_and_identity_stay():
    from auto_battery_research.tests.test_research_contract import requirement
    from auto_battery_research.util.research_contract import normalize_requirements, bindings
    raw = {"items": [requirement("硅碳负极", "constraint", binding={"path": "anode.material", "value": "硅碳", "unit": ""})]}
    doc = normalize_requirements(raw, "硅碳负极")
    item = doc["items"][0]
    assert item["quote"] == "硅碳负极" and item["binding"]["value"] == "si_base"
    again = normalize_requirements(doc, "硅碳负极")
    assert again["items"][0]["id"] == item["id"]
    assert bindings(doc)["anode.material"]["original_value"] == "硅碳"
    assert source_matches("anode.material", "si_base", "负极采用硅碳。")
    assert not source_matches("anode.material", "si_base", "负极采用金属锂。")


def test_geometry_before_material_match_uses_canonical_id_and_model_baseline():
    scheme = {"cathode": "NCM 811", "anode": "硅碳"}
    candidate = {"cathode": {"L_um": 78, "porosity": .28}, "anode": {"L_um": 82, "porosity": .36}}
    geometry = build_geometry_contract(candidate, scheme, [])
    assert geometry["material_ids"] == {"cathode": "NCM811", "anode": "si_base"}
    assert geometry["cathode"]["D_s_m2s"] == 1e-14 and geometry["anode"]["D_s_m2s"] == 4e-14
    assert geometry["parameter_sources"]["anode.D_s_m2s"]["system_id"] == "GrSi_NMC811"
    assert not validate_geometry_contract(geometry, {"cathode": "NCM811", "anode": "si_base"}, [])
    altered = copy.deepcopy(geometry); altered["parameter_sources"]["anode.D_s_m2s"]["params_hash"] = "过期基准"
    assert any("基准版本" in e for e in validate_geometry_contract(altered, {"cathode": "NCM811", "anode": "si_base"}, []))


def test_material_alias_stays_canonical_through_collection_merge_commit_and_resume(tmp_path):
    from types import SimpleNamespace
    from auto_battery_research.util.scheme_ledger import collect_parameters
    from auto_battery_research.util.design_repair import scheme_with_fields
    from auto_battery_research.util.material_ids import canonical_document
    doc = {"target": "测试硅碳材料", "scheme": {"anode": "硅碳"}, "final_answer": "负极采用硅碳。", "evidence": []}
    client = SimpleNamespace(available=True, chat=lambda *a, **kw: json.dumps({"parameters": {
        "anode.material": dict(field("硅碳"), source_quote=doc["final_answer"], meaning="负极材料")}, "candidates": []}))
    collect_parameters(doc, client)
    assert doc["scheme_parameters"]["anode.material"]["value"] == "si_base"
    doc["scheme"] = scheme_with_fields(doc["scheme"], doc["scheme_parameters"])
    commit_design(tmp_path, doc)
    assert load(tmp_path)["parameters"]["anode.material"]["value"] == "si_base"
    assert canonical_document(doc)["scheme"]["anode"] == "si_base"
    assert doc["final_answer"] == "负极采用硅碳。"


def test_material_change_cannot_keep_geometry_for_previous_material(tmp_path):
    scheme = {"cathode": "NCM811", "anode": "si_base"}
    geometry = build_geometry_contract({"cathode": {"L_um":70,"porosity":.31}, "anode":{"L_um":110,"porosity":.56}}, scheme, [])
    doc = {"scheme": dict(scheme, geometry=geometry)}
    commit_design(tmp_path,doc)
    revision = load(tmp_path)["revision"]
    doc["scheme"]["anode"] = "li_metal"
    with pytest.raises(ValueError,match="材料身份"):
        commit_design(tmp_path,doc)
    assert load(tmp_path)["revision"] == revision


def test_baseline_migration_does_not_fill_optional_conditions_or_replace_explicit_geometry():
    scheme = {"cathode":"NCM811", "anode":"si_base"}
    geometry = build_geometry_contract({"cathode":{"L_um":70,"porosity":.31}, "anode":{"L_um":110,"porosity":.56}},scheme,[])
    geometry["condition"] = {"v_min":None,"v_max":None,"c_rate":None}
    geometry["anode"]["R_s_um"] = 4
    result = synchronize_material_baselines(geometry,scheme)
    assert result["condition"] == geometry["condition"] and result["n_p_ratio"] is None
    assert result["anode"]["R_s_um"] == 4
    assert result["cathode"]["L_um"] == 70 and result["anode"]["porosity"] == .56


def migration_fixture(tmp_path):
    from auto_battery_research.tests.test_final_design_reporting import report_fixture
    from auto_battery_research.tests.test_experiment_design import setup_design
    from auto_battery_research.simulation.geometry_contract import geometry_hash
    from auto_battery_research.util.experiment_design import plan_hash, validate
    from auto_battery_research.util.experiment_consistency import stamp
    mgr, _, _, _ = report_fixture(tmp_path)
    doc, experiment = setup_design(tmp_path)
    scheme = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce"}
    candidate = {"cathode": {"L_um": 78, "porosity": .28, "R_s_um": 3}, "anode": {"L_um": 82, "porosity": .36, "R_s_um": 6}}
    candidate["condition"] = {key.partition(".")[2]: value for key, value in experiment.get("fixed_fields", {}).items()
                              if key.partition(".")[0] == "condition"}
    geo = build_geometry_contract(candidate, scheme, [])
    geo["anode"]["R_s_um"] = 4
    for side in ("cathode", "anode"):
        geo[side]["D_s_m2s"] = None
        geo["parameter_sources"][side + ".D_s_m2s"] = {"origin": "missing", "basis": "unresolved", "material_id": scheme[side]}
        geo["provenance"][side + ".D_s_m2s"] = "unmatched"
    doc["scheme"] = dict(scheme, geometry=geo, geometry_hash=geometry_hash(geo), design_revision=0)
    commit_design(tmp_path, doc)
    doc.update(delivery_contract=1,report_readiness={"category":"CONDITIONAL","issues":[],"physics_status":"FAILED","review_status":"UNREVIEWED"})
    mgr.target_goal = doc["target"]
    for name in ("pinn_input_spec.json", "pinn_simulation_result.json"):
        (tmp_path / name).unlink(missing_ok=True)
    experiment["fixed_fields"] = {k:v for k,v in experiment.get("fixed_fields",{}).items() if not k.startswith("anode.")}
    stamp(experiment,doc)
    experiment["plan_hash"] = plan_hash(experiment)
    experiment["bindings"] = validate(experiment,doc)
    doc["experiment_plan_hash"] = experiment["plan_hash"]
    for name,obj in (("design_scheme.json",doc),("experiment_plan.json",experiment)):
        (tmp_path / name).write_text(json.dumps(obj,ensure_ascii=False),encoding="utf8")
    (tmp_path / "design_scheme.md").write_text(doc["final_answer"],encoding="utf8")
    return mgr,doc,experiment


def test_migration_dry_run_and_publish_preserve_design_and_show_radius_block(tmp_path):
    from auto_battery_research.util.material_migration import migrate_material_ids
    mgr, doc, experiment = migration_fixture(tmp_path)
    before = {p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    result = migrate_material_ids(mgr,dry_run=True)
    assert result["success"] and result["physics"]["matched"]
    assert all((tmp_path / name).read_bytes() == raw for name,raw in before.items())
    result = migrate_material_ids(mgr)
    assert result["success"], result
    updated = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf8"))
    assert updated["scheme"]["geometry"]["anode"]["R_s_um"] == 4
    assert updated["scheme"]["geometry"]["anode"]["D_s_m2s"] == 4e-14
    assert updated["final_answer"] == doc["final_answer"]
    assert json.loads((tmp_path / "experiment_plan.json").read_text(encoding="utf8"))["sections"] == experiment["sections"]
    assert "已匹配 **GrSi_NMC811**" in (tmp_path / "final_research_report.md").read_text(encoding="utf8")
    second = migrate_material_ids(mgr)
    assert second["success"] and not second["changed"]


def test_migration_preview_failure_keeps_every_original_file(tmp_path,monkeypatch):
    from auto_battery_research.util.material_migration import migrate_material_ids
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    mgr,_,_ = migration_fixture(tmp_path)
    before = {p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    monkeypatch.setattr(FinalReportChecker,"do_check",lambda *a,**k:(False,{"error":"测试拒绝"}))
    result = migrate_material_ids(mgr)
    assert not result["success"]
    assert all((tmp_path / name).read_bytes() == raw for name,raw in before.items())


def test_migration_commit_failure_restores_files_and_stale_simulation(tmp_path, monkeypatch):
    from auto_battery_research.util.material_migration import migrate_material_ids
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    mgr,_,_ = migration_fixture(tmp_path)
    (tmp_path / "pinn_simulation_result.json").write_text('{"status":"CONVERGED","stale":true}',encoding="utf8")
    before = {p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    calls = []
    def check(*a,**kw):
        calls.append(1)
        return (len(calls)==1,{"error":"提交后验收拒绝"})
    monkeypatch.setattr(FinalReportChecker,"do_check",check)
    result = migrate_material_ids(mgr)
    assert not result["success"] and len(calls)==2
    assert all((tmp_path / name).read_bytes() == raw for name,raw in before.items())


def test_cli_material_preview_does_not_create_manager_or_write_task_files(tmp_path,monkeypatch):
    import sys
    from auto_battery_research import cli
    from auto_battery_research.tools import stage_tools
    from auto_battery_research.util import material_migration
    mgr,doc,_ = migration_fixture(tmp_path)
    before = {p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    def preview(root,goal):
        assert goal == doc["target"]
        return material_migration.migrate_material_ids(mgr,dry_run=True)
    monkeypatch.setattr(material_migration,"preview_material_migration",preview)
    monkeypatch.setattr(stage_tools,"get_stage_manager_for_goal",lambda *a,**k:pytest.fail("预览不能创建管理器"))
    monkeypatch.setattr(sys,"argv",["abr-cli","--goal",doc["target"],"--migrate-material-ids","--dry-run"])
    cli.main()
    assert all((tmp_path/name).read_bytes()==content for name,content in before.items())
