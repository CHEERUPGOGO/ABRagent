"""统一方案表的离线回归：锁定、派生计算、版本及报告。"""
import copy
import json

import pytest

from auto_battery_research.util import scheme_ledger as ledger


def field(value, unit=""):
    return {"value": value, "unit": unit, "basis": "engineering_proposal", "source": "测试设计"}


def test_lock_revision_and_unlock(tmp_path):
    data = ledger.ensure(tmp_path, "设计300Wh/kg电池")
    assert data["parameters"]["requirements.target_energy_wh_kg"]["locked"]
    with pytest.raises(ValueError, match="锁定"):
        ledger.update(tmp_path, {"requirements.target_energy_wh_kg": field(400, "Wh/kg")}, 0)
    data = ledger.update(tmp_path, {"cathode.material": field("NCM811")}, 0, actor="user")
    before = (tmp_path / ledger.FILENAME).read_bytes()
    with pytest.raises(ValueError, match="过期"):
        ledger.update(tmp_path, {}, 0)
    assert (tmp_path / ledger.FILENAME).read_bytes() == before
    data = ledger.update(tmp_path, {}, data["revision"], actor="user", unlock=["cathode.material"])
    assert not data["parameters"]["cathode.material"]["locked"]
    assert (tmp_path / "scheme_history" / f"revision_{data['revision']}.json").exists()


def test_pores_capacity_and_np_recompute(tmp_path):
    ledger.ensure(tmp_path)
    changes = {"cathode.L_um": field(60, "um"), "cathode.porosity": field(.28),
               "cathode.loading_mg_cm2": field(20.5, "mg/cm2"), "cathode.active_fraction": field(.98),
               "cathode.capacity_mAh_g": field(200, "mAh/g"), "anode.L_um": field(45, "um"),
               "anode.porosity": field(.45), "anode.porosity_basis": field("before_loading"),
               "anode.li_filling_fraction": field(.6), "anode.li_density_g_cm3": field(.534, "g/cm3"),
               "anode.li_capacity_mAh_g": field(3860, "mAh/g")}
    data = ledger.update(tmp_path, changes, 0)
    assert data["derived"]["cathode.pore_volume_uL_cm2"]["value"] == pytest.approx(1.68)
    assert data["derived"]["anode.pore_volume_uL_cm2"]["value"] == pytest.approx(2.025)
    assert data["derived"]["anode.n_p_ratio"]["value"] == pytest.approx(.6233, abs=.001)
    data = ledger.update(tmp_path, {"cathode.active_fraction": field(.96)}, data["revision"])
    assert data["derived"]["cathode.areal_capacity_mAh_cm2"]["value"] == pytest.approx(3.936)


def test_missing_dependencies_and_bad_units(tmp_path):
    data = ledger.ensure(tmp_path)
    assert data["derived"]["cathode.areal_capacity_mAh_cm2"]["value"] is None
    for value in (field(60, "mm"), field("60", "um"), field(float("nan"), "um")):
        with pytest.raises(ValueError):
            ledger.update(tmp_path, {"cathode.L_um": value}, 0)
    assert ledger.load(tmp_path)["revision"] == 0


def test_projection_stale_and_material_change(tmp_path):
    ledger.ensure(tmp_path)
    doc = {"scheme": {"cathode": "NCM811", "anode": "li_metal", "electrolyte": "lhce",
                      "geometry": {"cathode": {"L_um": 60}}}}
    ledger.commit_design(tmp_path, doc)
    assert not ledger.projection_errors(tmp_path, doc)
    old = copy.deepcopy(doc)
    doc["scheme"]["geometry"]["cathode"]["L_um"] = 70
    ledger.commit_design(tmp_path, doc, reason="几何修订")
    assert ledger.projection_errors(tmp_path, old)
    data = ledger.load(tmp_path)
    data = ledger.update(tmp_path, {"cathode.material": field("LFP")}, data["revision"])
    assert "cathode.L_um" not in data["parameters"]


def test_legacy_import_ignores_narrative(tmp_path):
    (tmp_path / "design_scheme.json").write_text(json.dumps({"scheme": {"cathode": "NCM811"},
        "final_answer": "厚度999 um"}), encoding="utf-8")
    data = ledger.ensure(tmp_path)
    assert data["parameters"]["cathode.material"]["value"] == "NCM811"
    assert "cathode.L_um" not in data["parameters"]


def test_report_uses_ledger_and_no_fake_simulation(tmp_path):
    from auto_battery_research.util.report_analysis import render_materials
    from auto_battery_research.util.report_contract import report_context, render_recipe
    ledger.ensure(tmp_path)
    doc = {"scheme": {"cathode": "NCM811"}, "final_answer": "旧参数999 um"}
    ledger.commit_design(tmp_path, doc)
    assert "999" not in render_materials(doc)
    text = render_recipe(report_context(doc, None))
    assert "未完成有效放电仿真" in text
    assert "999" not in text


def test_mass_budget_follows_active_fraction(tmp_path):
    ledger.ensure(tmp_path)
    changes = {"cathode.loading_mg_cm2": field(20.5, "mg/cm2"), "cathode.active_fraction": field(.98),
        "cathode.capacity_mAh_g": field(200, "mAh/g"), "anode.composite_mass_mg_cm2": field(5.7, "mg/cm2"),
        "current_collector.cathode_mass_mg_cm2": field(3.24, "mg/cm2"), "current_collector.anode_mass_mg_cm2": field(4.03, "mg/cm2"),
        "separator.mass_mg_cm2": field(1, "mg/cm2"), "electrolyte.mass_mg_cm2": field(5, "mg/cm2"),
        "packaging.mass_mg_cm2": field(5, "mg/cm2"), "condition.average_discharge_voltage_V": field(3.7, "V")}
    data = ledger.update(tmp_path, changes, 0)
    initial = data["derived"]["packaging.cell_energy_wh_kg"]["value"]
    data = ledger.update(tmp_path, {"cathode.active_fraction": field(.92)}, data["revision"])
    assert data["derived"]["packaging.cell_energy_wh_kg"]["value"] < initial


def test_structured_report_extension_and_tampering(tmp_path, monkeypatch):
    from auto_battery_research.util import report_recipe_llm as plans
    from auto_battery_research.util.report_contract import report_context
    ledger.ensure(tmp_path)
    doc = {"scheme": {"cathode": "NCM811", "anode": "li_metal", "electrolyte": "lhce"}}
    ledger.commit_design(tmp_path, doc)
    class Client:
        def chat(self, *args, **kwargs):
            return json.dumps({"process.drying_temperature_K": dict(field(350, "K"), source="LLM候选")})
    monkeypatch.setattr(plans, "_llm_client", lambda mgr: Client())
    context = report_context(doc, None)
    plan = plans.generate_experiment_plan(None, tmp_path, doc, context, {}, {}, "", None, 5)
    assert "76.85 ℃" in plan["text"] and "未采用" in plan["text"]
    assert "process.drying_temperature_K" not in ledger.load(tmp_path)["parameters"]
    fp = plans.input_fingerprint(doc, context, {}, {}, "")
    text = f"{plans.PLAN_START}\n{plans.plan_fingerprint_marker(fp)}\n{plan['text']}\n{plans.PLAN_END}"
    assert not plans.validate_plan_record(tmp_path, doc, context, {}, {}, "", None, 5, text)
    record = plans.load_plan_record(tmp_path)
    record["text"] += "假冒当前参数"
    (tmp_path / plans.PLAN_FILENAME).write_text(json.dumps(record), encoding="utf-8")
    assert plans.validate_plan_record(tmp_path, doc, context, {}, {}, "", None, 5, text)


def test_complete_report_and_stale_gate(tmp_path, monkeypatch):
    from auto_battery_research.tests.test_final_design_reporting import report_fixture
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    from auto_battery_research.util import report_recipe_llm as plans
    mgr, doc, spec, result = report_fixture(tmp_path)
    monkeypatch.setattr(plans, "_llm_client", lambda mgr: None)
    ledger.ensure(tmp_path, mgr.target_goal)
    ledger.commit_design(tmp_path, doc)
    spec["ledger_hash"] = doc["ledger_hash"]
    from auto_battery_research.tests.test_experiment_design import scripted_client
    from auto_battery_research.util import experiment_design as recipes
    recipe = recipes.generate(tmp_path, doc, scripted_client())
    doc.update(experiment_contract=1, experiment_plan_hash=recipe["plan_hash"])
    for filename, obj in (("design_scheme.json", doc), ("pinn_input_spec.json", spec)):
        (tmp_path / filename).write_text(json.dumps(obj), encoding="utf-8")
    assert run_synthesis_report(target_query=mgr.target_goal, stage_manager=mgr)["success"]
    report_path = tmp_path / "final_research_report.md"
    report_text = report_path.read_text(encoding="utf-8")
    main_text = report_text.split("## 6.")[0]
    assert not any(line.lstrip().startswith("|") for line in main_text.splitlines())
    assert "```" not in main_text and "cathode.L_um" not in main_text
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    ok, diag = checker.do_check(is_complete=True)
    assert ok, diag
    assert "涂层厚度为**170 μm**" in report_text
    report_path.write_text(report_text.replace("涂层厚度为**170 μm**", "涂层厚度为**171 μm**"), encoding="utf-8")
    assert not checker.do_check(is_complete=True)[0]
    report_path.write_text(report_text, encoding="utf-8")
    data = ledger.load(tmp_path)
    ledger.update(tmp_path, {"electrolyte.salt": field("LiFSI")}, data["revision"], actor="user")
    ok, diag = checker.do_check(is_complete=True)
    assert not ok and diag["error_code"] == "REPORT_FINAL_INPUT_MISMATCH"
