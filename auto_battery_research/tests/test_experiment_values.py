"""实验缺项值隔离、量值类型、缺项展示、恢复预算及真实五条响应回归。"""
import copy
import json
from pathlib import Path

import pytest

from auto_battery_research.tests.test_experiment_generation import Client, design, checkpoint
from auto_battery_research.tests.test_experiment_design import recipe_payload, setup_design
from auto_battery_research.util import experiment_design as recipes
from auto_battery_research.util.experiment_values import (validate_value, validate_scalar_fields, separate_pending,
    pending_rows, substitute_pending, normalize_legacy_collection)
from auto_battery_research.util.experiment_consistency import stamp
from auto_battery_research.util.experiment_review import status as review_status


def proposal(value, unit="h", **extra):
    return {"value": value, "unit": unit, "basis": "engineering_proposal", "source": "原稿未给出该条件",
            "meaning": "待核对操作条件", "role": "processing", **extra}


REAL_GAPS = [
    ("process.precursor_washing_liquid", "", "待确定（获取方法：以去离子水洗涤至滤液电导率稳定，并用ICP-OES核查Na残留后固定）"),
    ("process.precursor_drying_time_h", "h", "待确定（获取方法：以前驱体在105 °C下达到恒重所需时间确定）"),
    ("process.ball_milling_time_h", "h", "待确定（获取方法：以混合均匀性预实验和D50稳定时间确定）"),
    ("process.ball_milling_speed_rpm", "rpm", "待确定（获取方法：按设备说明书与混合均匀性预实验确定，记录球料比）"),
    ("condition.cv_scan_rate_mV_s", "mV/s", "待确定（获取方法：以不同扫描速率预扫，选峰分离且重复性满足要求的速率后固定）"),
]


@pytest.mark.parametrize("key,unit,value", REAL_GAPS)
def test_real_gap_values_fail_entry_and_final_validation(tmp_path, key, unit, value):
    from auto_battery_research.util.experiment_slots import validate_proposal
    doc, old = setup_design(tmp_path)
    row = proposal(value, unit)
    with pytest.raises(ValueError, match="待定|未确定|pending"):
        validate_proposal({"field": key, **row}, doc, old["groups"], old["parameters"])
    old["parameters"][key] = row
    old["plan_hash"] = recipes.plan_hash(old)
    with pytest.raises(ValueError):
        recipes.validate(old, doc, delivery=True)
    assert recipes.delivery_status(old, doc=doc)[0] == "CONDITIONAL"


@pytest.mark.parametrize("value", ["待确定", "未知", "TBD", "unknown", None, "12", "6–12 h", "N₂", True, float("nan")])
def test_numeric_fields_reject_non_numeric_and_unknown(value):
    with pytest.raises(ValueError):
        validate_value("process.drying_time_h", proposal(value))


@pytest.mark.parametrize("value", ["N₂", "随炉冷却", "去离子水", "未掺杂母体", "无水乙醇", "Na（占据 Li 位）"])
def test_explicit_text_values_are_kept(value):
    validate_value("process.medium", proposal(value, ""))


@pytest.mark.parametrize("value,kind", [({"min": 2, "max": 12}, "range"), ([.1, .5, 1, 2], "sequence")])
def test_range_and_sequence_validation_and_display(value, kind):
    from auto_battery_research.util.parameter_presentation import value_text
    row = proposal(value, value_type=kind)
    validate_scalar_fields("process.drying_time_h", row)
    assert "h" in value_text("process.drying_time_h", row)


@pytest.mark.parametrize("value,kind", [({"min": 12, "max": 2}, "range"), ({"min": 1, "max": "待定"}, "range"),
    ([], "sequence"), ([1, float("inf")], "sequence"), ([True, 2], "sequence"), ({"min": 0, "max": 1, "extra": 2}, "range")])
def test_invalid_collections_fail(value, kind):
    with pytest.raises(ValueError):
        validate_value("process.time_h", proposal(value, value_type=kind))


def test_actual_frequency_and_rate_sequence_restore_without_guessing():
    frequency = proposal("100 kHz–10 mHz", "Hz")
    converted = normalize_legacy_collection("condition.eis_frequency_range", frequency)
    assert converted["value"] == {"min": .01, "max": 100000.} and converted["range_order"] == "descending"
    sequence = normalize_legacy_collection("condition.rate_test_c_rates", proposal("[0.1, 0.2, 0.5, 1, 2, 5] C", "C"))
    assert sequence["value"] == [.1, .2, .5, 1, 2, 5]
    ambiguous = proposal("时间6小时或等到恒重", "h")
    assert normalize_legacy_collection("process.time_h", ambiguous) == ambiguous


def test_separation_preserves_original_and_displays_missing_values(tmp_path):
    doc, original = setup_design(tmp_path)
    for key, unit, value in REAL_GAPS:
        original["parameters"][key] = proposal(value, unit, meaning=key)
    original["sections"]["fabrication"].append("先按{{process.precursor_drying_time_h}} h干燥，再核对结果。")
    original["sections"]["testing"].append("扫描速率{{condition.cv_scan_rate_mV_s}} mV/s须确认后再测量。")
    before = copy.deepcopy(original)
    updated, keys = separate_pending(original)
    assert original == before and len(keys) == 5
    assert len(pending_rows(updated)) == 5 and not set(keys) & updated["parameters"].keys()
    assert updated["sections"]["assembly"] == original["sections"]["assembly"]
    assert updated["pending_parameters"][keys[0]]["record"] == original["parameters"][keys[0]]
    assert updated["pending_sections"]["fabrication:1"]["text"] == original["sections"]["fabrication"][1]
    stamp(updated, doc)
    updated["plan_hash"] = recipes.plan_hash(updated)
    updated["bindings"] = recipes.validate(updated, doc, delivery=True)
    body = recipes.render(updated, doc)
    assert "待确认参数及获取方法" in body and "待确定（" not in body and "{{pending:" not in body
    assert "相关操作须在补齐条件后执行" not in body and not updated["pending_operations"]
    assert "获取方法" in body and "恒重" in body
    assert review_status(updated, doc)[0] == "LEGACY"
    assert recipes.delivery_status(updated, doc=doc)[0] == "CONDITIONAL"
    again, changed = separate_pending(updated)
    assert not changed and again == updated


def test_explicit_missing_submission_gets_pending_state_without_repeated_retry(tmp_path):
    doc = design(tmp_path)
    payload = recipe_payload()
    payload["parameters"]["process.drying_time_h"] = proposal(None, expected_type="number", reason="无明确时间", acquisition_method="按恒重试验确定")
    payload["sections"]["fabrication"].append("先确认{{pending:process.drying_time_h}}再执行干燥。")
    client = Client(payload)
    record = recipes.generate(tmp_path, doc, client)
    assert "process.drying_time_h" not in record["parameters"]
    assert record["pending_parameters"]["process.drying_time_h"]["value"] is None
    assert "pending_dependencies" not in record and not record["pending_operations"]
    assert [tag for tag, _ in client.calls] == ["experiment_design"]
    assert record["repair_status"] == "PARTIAL"


def test_changed_pending_value_or_reference_fails_closed(tmp_path):
    doc, record = setup_design(tmp_path)
    record["parameters"]["process.time_h"] = proposal("待确定")
    record["sections"]["fabrication"].append("等待{{process.time_h}}后实施。")
    record, _ = separate_pending(record)
    stamp(record, doc)
    for mutation in (lambda v: v["pending_parameters"]["process.time_h"].update(value=10),
                     lambda v: v["pending_parameters"]["process.time_h"].update(status="ready"),
                     lambda v: v["sections"]["fabrication"].append("使用{{pending:process.unknown}}。"),
                     lambda v: v["sections"]["fabrication"].append("使用{{process.time_h}}。")):
        changed = copy.deepcopy(record)
        mutation(changed)
        changed["plan_hash"] = recipes.plan_hash(changed)
        with pytest.raises(ValueError):
            recipes.validate(changed, doc, delivery=True)




def test_existing_artifact_cache_does_not_skip_value_validation(tmp_path):
    doc = design(tmp_path)
    client = Client()
    old = recipes.generate(tmp_path, doc, client)
    old["parameters"]["process.old_time_h"] = proposal("待确定")
    old["plan_hash"] = recipes.plan_hash(old)
    (tmp_path / recipes.FILENAME).write_text(json.dumps(old), encoding="utf-8")
    with pytest.raises(ValueError, match="未确定"):
        recipes.generate(tmp_path, doc, client)
    assert len(client.calls) == 1


def migration_fixture(tmp_path, monkeypatch):
    from auto_battery_research.tests.test_final_design_reporting import report_fixture
    from auto_battery_research.tools import workflow_actions
    mgr, _, _, _ = report_fixture(tmp_path)
    doc, record = setup_design(tmp_path)
    mgr.target_goal = doc["target"]
    monkeypatch.setattr(workflow_actions, "_match_pinn_system_for_scheme", lambda s: {"status": "NO_MATCH", "matched": False})
    for name in ("pinn_input_spec.json", "pinn_simulation_result.json"):
        (tmp_path / name).unlink(missing_ok=True)
    doc.update(delivery_contract=1, report_readiness={"category": "CONDITIONAL", "issues": [], "physics_status": "NOT_APPLICABLE", "review_status": "UNREVIEWED"})
    for key, unit, value in REAL_GAPS:
        record["parameters"][key] = proposal(value, unit)
    record["sections"]["fabrication"].append("干燥时间{{process.precursor_drying_time_h}} h先确认后实施。")
    record["plan_hash"] = recipes.plan_hash(record)
    doc["experiment_plan_hash"] = record["plan_hash"]
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc), encoding="utf-8")
    (tmp_path / "experiment_plan.json").write_text(json.dumps(record), encoding="utf-8")
    (tmp_path / "final_research_report.md").write_text("旧报告完整备份", encoding="utf-8")
    mgr.stages[3].status = mgr.stages[4].status = "FALLBACK"
    return mgr, doc, record


def test_migration_publishes_verified_report_without_changing_ledger_or_budgets(tmp_path, monkeypatch):
    from auto_battery_research.util.experiment_value_recovery import repair_experiment_values
    mgr, doc, old = migration_fixture(tmp_path, monkeypatch)
    ledger_before = (tmp_path / "scheme_ledger.json").read_bytes()
    batches = {"legacy_archived_calls": []}
    (tmp_path / "experiment_generation_checkpoint.json").write_text(json.dumps({"batches": batches}), encoding="utf-8")
    result = repair_experiment_values(mgr)
    assert result["success"], result
    assert len(result["fields"]) == 5 and result["model_calls"] == 0
    assert set(result["checks"]["5"]["diagnostic"]["observed"]["pending_fields"]) == set(result["fields"])
    assert (tmp_path / "scheme_ledger.json").read_bytes() == ledger_before
    assert checkpoint(tmp_path)["batches"] == batches
    new = recipes.load(tmp_path)
    assert len(pending_rows(new)) == 5 and new["sections"]["assembly"] == old["sections"]["assembly"]
    assert "待确认参数及获取方法" in (tmp_path / "final_research_report.md").read_text(encoding="utf-8")
    assert Path(result["history"], "final_research_report.md").read_text(encoding="utf-8") == "旧报告完整备份"
    assert repair_experiment_values(mgr) == {"success": True, "changed": False, "model_calls": 0}


@pytest.mark.parametrize("failure_at", ["preview", "publish"])
def test_migration_failure_keeps_all_original_files(tmp_path, monkeypatch, failure_at):
    from auto_battery_research.util.experiment_value_recovery import repair_experiment_values
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    mgr, _, _ = migration_fixture(tmp_path, monkeypatch)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    real = FinalReportChecker.do_check
    def check(self, *a, **kw):
        is_original = self.stage_manager.get_task_output_dir() == tmp_path
        if failure_at == "preview" or is_original:
            return False, {"error": "模拟终审失败"}
        return real(self, *a, **kw)
    monkeypatch.setattr(FinalReportChecker, "do_check", check)
    result = repair_experiment_values(mgr)
    assert not result["success"] and any(word in result["error"] for word in ("终审", "验收"))
    assert all((tmp_path / name).read_bytes() == content for name, content in before.items())
