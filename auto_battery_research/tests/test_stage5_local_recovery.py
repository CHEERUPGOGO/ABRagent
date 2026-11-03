"""Stage 5 自包含优化及实验局部修复，完全离线。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_experiment_design import recipe_payload, setup_design
from auto_battery_research.util.experiment_repair import diagnose, apply_local
from auto_battery_research.util import experiment_design as recipes
from auto_battery_research.util.scheme_ledger import digest


def test_bad_parameter_repaired_without_rewriting_valid_sections(tmp_path):
    doc, _ = setup_design(tmp_path)
    (tmp_path / "experiment_plan.json").unlink()
    original = recipe_payload()
    original["parameters"]["process.formation_c_rate"]["basis"] = "literature"
    calls = []
    def chat(system, user, **kw):
        calls.append(kw["trace_tag"])
        if kw["trace_tag"] == "experiment_design":
            return json.dumps(original)
        request = json.loads(user)
        fixed = copy.deepcopy(original["parameters"]["process.formation_c_rate"])
        fixed["basis"] = "engineering_proposal"
        return json.dumps({"base_hash": request["base_hash"], "changes": [{"path": ["parameters", "process.formation_c_rate"], "value": fixed}]})
    doc["physics_context"] = {"status": "PASSED"}
    result = recipes.generate(tmp_path, doc, SimpleNamespace(available=True, chat=chat))
    assert calls == ["experiment_design", "experiment_repair"]
    assert result["sections"] == original["sections"]
    assert result["parameters"]["process.drying_temperature_K"] == original["parameters"]["process.drying_temperature_K"]
    assert result["parameters"]["process.formation_c_rate"]["basis"] == "engineering_proposal"


def test_failed_local_repair_keeps_detailed_good_parts(tmp_path):
    doc, _ = setup_design(tmp_path)
    (tmp_path / "experiment_plan.json").unlink()
    original = recipe_payload()
    original["parameters"]["process.formation_c_rate"]["basis"] = "literature"
    def chat(*a, **kw):
        return json.dumps(original)  # 第二次错误地重写全文，应拒绝但不丢弃有效内容。
    result = recipes.generate(tmp_path, doc, SimpleNamespace(available=True, chat=chat))
    assert result["repair_status"] == "PARTIAL"
    assert result.get("origin") != "engineering_fallback"
    assert result["sections"] == original["sections"]
    assert "待确定" in result["parameters"]["process.formation_c_rate"]["value"]
    assert (tmp_path / "experiment_draft.json").exists()


def test_local_patch_rejects_valid_paragraph_overwrite(tmp_path):
    doc, _ = setup_design(tmp_path)
    record = recipe_payload()
    record.update(schema_version=3, generated_stage=5)
    record["parameters"]["process.formation_c_rate"]["basis"] = "literature"
    errors = diagnose(record, doc)
    patch = {"base_hash": digest(record), "changes": [{"path": ["sections", "groups", 0], "value": "擅改有效段落"}]}
    with pytest.raises(ValueError):
        apply_local(record, patch, errors)


def test_citation_objects_are_normalized_and_verified(tmp_path):
    doc, _ = setup_design(tmp_path)
    (tmp_path / "experiment_plan.json").unlink()
    doc["evidence"] = [{"passage_id": "p1", "text": "原文"}]
    payload = recipe_payload()
    payload["citations"] = [{"passage_id": "p1", "title": "原文"}]
    result = recipes.generate(tmp_path, doc, SimpleNamespace(available=True, chat=lambda *a, **k: json.dumps(payload)))
    assert result["citations"] == ["p1"]


@pytest.mark.parametrize("duplicate_fields,old_exhausted", [(False, False), (True, False), (True, True)])
def test_real_numpy_optimizer_runs_bounded_rounds_and_resumes(tmp_path, duplicate_fields, old_exhausted):
    from auto_battery_research.tests.test_final_design_reporting import report_fixture
    from auto_battery_research.util.pinn_optimization import optimize
    from auto_battery_research.util.scheme_ledger import commit_design, flatten_scheme
    from auto_battery_research.tools.workflow_actions import select_best_stage5_result
    mgr, doc, _, initial = report_fixture(tmp_path)
    if duplicate_fields:
        doc["scheme_parameters"] = {k: {"value": v, "unit": "um", "basis": "engineering_proposal", "source": "初始正文", "source_quote": "旧参数原句"}
            for k, v in flatten_scheme(doc["scheme"]).items() if k.endswith(("L_um", "R_s_um"))}
    commit_design(tmp_path, doc)
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc), encoding="utf-8")
    mgr.config["runtime_options"] = {"max_geometry_revisions": 3, "target_energy_tolerance_pct": 5}
    mgr.config["pinn_trigger"] = {"enabled": True}
    if old_exhausted:
        identity = digest([mgr.target_goal, doc.get("generated_at"), doc["scheme"].get("initial_geometry_hash"), 350 * .95])
        (tmp_path / "pinn_optimization_state.json").write_text(json.dumps({"identity": identity, "attempts": 3, "finished": True,
            "rounds": [{"status": "REJECTED", "error": "补充参数与几何契约冲突: cathode.L_um"}] * 3}), encoding="utf-8")
    state = optimize(mgr, client=None)
    assert state["status"] in ("TARGET_MET", "BUDGET_EXHAUSTED")
    assert 1 <= state["attempts"] <= 3
    history = json.loads((tmp_path / "pinn_simulation_history.json").read_text(encoding="utf-8"))
    assert len(history["rounds"]) >= 2
    saved = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    if duplicate_fields:
        for key, row in saved["scheme_parameters"].items():
            assert row["value"] == flatten_scheme(saved["scheme"])[key]
        assert saved.get("geometry_parameter_history")
    if old_exhausted:
        assert state["duplicate_geometry_recovery"]
        assert list((tmp_path / "pinn_optimization_history").glob("*.json"))
    attempts = state["attempts"]
    select_best_stage5_result(mgr)
    again = optimize(mgr, client=None)
    assert again["attempts"] == attempts


def test_unrelated_duplicate_conflicts_still_fail(tmp_path):
    from auto_battery_research.tests.test_final_design_reporting import design
    from auto_battery_research.util.scheme_ledger import align_geometry_parameters, commit_design
    doc = {"scheme": design(), "scheme_parameters": {"cathode.L_um": {"value": 1}}}
    align_geometry_parameters(doc, ["cathode.R_s_um"], "合法半径调整")
    with pytest.raises(ValueError, match="冲突"):
        commit_design(tmp_path, doc)


def test_target_already_met_does_not_revise(tmp_path):
    from auto_battery_research.tests.test_final_design_reporting import report_fixture
    from auto_battery_research.util.pinn_optimization import optimize
    mgr, _, _, _ = report_fixture(tmp_path)
    mgr.config["pinn_trigger"] = {"enabled": True}
    result_path = tmp_path / "pinn_simulation_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["energy_wh_kg"] = 400
    result_path.write_text(json.dumps(result), encoding="utf-8")
    assert optimize(mgr)["status"] == "TARGET_MET"
    assert not (tmp_path / "pinn_simulation_history.json").exists()
