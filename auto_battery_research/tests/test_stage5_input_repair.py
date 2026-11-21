"""越界输入修复与能量优化共用预算，真实 numpy 仿真及模拟 LLM，全部离线。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_final_design_reporting import report_fixture
from auto_battery_research.simulation.geometry_contract import geometry_hash
from auto_battery_research.util import stage5_attempts as attempts
from auto_battery_research.util.pinn_optimization import optimize
from auto_battery_research.util.pinn_inputs import diagnose
from auto_battery_research.util.scheme_ledger import atomic_write, commit_design, digest
from auto_battery_research.tools.workflow_actions import revise_geometry, run_pinn_simulation


def setup(tmp_path, changes=None, target=1000):
    mgr, doc, _, _ = report_fixture(tmp_path)
    doc["scheme"]["geometry"]["n_p_ratio"] = None
    doc["scheme"]["geometry"]["provenance"]["n_p_ratio"] = "unmatched"
    for key, value in (changes or {}).items():
        comp, field = key.split(".")
        doc["scheme"]["geometry"][comp][field] = value
    doc["scheme"]["geometry_hash"] = geometry_hash(doc["scheme"]["geometry"])
    doc["scheme"]["initial_geometry_hash"] = doc["scheme"]["geometry_hash"]
    doc["target"] = f"测试{target}Wh/kg电池" if target is not None else "比较电池设计"
    mgr.target_goal = doc["target"]
    mgr.config.update(pinn_trigger={"enabled": True}, runtime_options={"max_geometry_revisions": 3})
    commit_design(tmp_path, doc)
    atomic_write(tmp_path / "design_scheme.json", json.dumps(doc, ensure_ascii=False))
    for name in ("pinn_input_spec.json", "pinn_simulation_result.json"):
        (tmp_path / name).unlink()
    return mgr, doc


def client(*proposals):
    calls = []
    def chat(system, user, **kw):
        request = json.loads(user)
        calls.append((request, kw))
        value = proposals[min(len(calls)-1, len(proposals)-1)]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps({"revisions": value, "reason": "按输入范围修复工程候选"})
    return SimpleNamespace(available=True, chat=chat, calls=calls)


def test_repair_then_only_two_optimization_attempts(tmp_path):
    mgr, _ = setup(tmp_path, {"anode.R_s_um": 4.0})
    llm = client({"anode.R_s_um": 4.2}, {"cathode.L_um": 160.}, {"anode.L_um": 150.})
    state = optimize(mgr, llm)
    assert state["status"] == "BUDGET_EXHAUSTED" and state["attempts"] == 3
    assert [r[0]["mode"] for r in llm.calls] == ["input_repair", "energy_optimization", "energy_optimization"]
    assert [r["status"] for r in state["rounds"]] == ["SIMULATED"] * 3
    assert attempts.budget(mgr) == {"used": 3, "max": 3, "remaining": 0}
    assert run_pinn_simulation(stage_manager=mgr)["key_findings"]["revision_budget"]["used"] == 3
    optimize(mgr, llm)
    assert len(llm.calls) == 3


def test_multiple_fields_repaired_as_one_without_energy_target(tmp_path):
    mgr, _ = setup(tmp_path, {"anode.R_s_um": 4., "cathode.L_um": 500.}, target=None)
    llm = client({"anode.R_s_um": 4.2, "cathode.L_um": 170.})
    state = optimize(mgr, llm)
    assert state["status"] == "SIMULATION_VALID" and state["attempts"] == 1
    assert len(llm.calls) == 1
    assert llm.calls[0][0]["allowed_fields"] == ["anode.R_s_um", "cathode.L_um"]
    assert len(json.loads((tmp_path / "pinn_simulation_history.json").read_text(encoding="utf-8"))["rounds"]) == 1


@pytest.mark.parametrize("proposal", [{"anode.R_s_um": 1.}, {"anode.D_s_m2s": 1e-13}, "bad json", RuntimeError("服务失败")])
def test_failed_proposals_count_and_do_not_change_design(tmp_path, proposal):
    mgr, _ = setup(tmp_path, {"anode.R_s_um": 4.})
    original = (tmp_path / "design_scheme.json").read_bytes()
    ledger = (tmp_path / "scheme_ledger.json").read_bytes()
    llm = client(proposal)
    state = optimize(mgr, llm)
    assert state["attempts"] == 3 and state["status"] == "BUDGET_EXHAUSTED"
    assert len(llm.calls) == 3 and all(r["status"] == "REJECTED" for r in state["rounds"])
    assert (tmp_path / "design_scheme.json").read_bytes() == original
    assert (tmp_path / "scheme_ledger.json").read_bytes() == ledger
    assert not (tmp_path / "pinn_simulation_result.json").exists()
    optimize(mgr, llm)
    assert len(llm.calls) == 3


def test_failed_then_valid_repair_uses_two_attempts(tmp_path):
    mgr, _ = setup(tmp_path, {"anode.R_s_um": 4.}, target=None)
    llm = client({"anode.R_s_um": 1.}, {"anode.R_s_um": 4.2})
    state = optimize(mgr, llm)
    assert state["attempts"] == 2 and state["status"] == "SIMULATION_VALID"
    assert [r["status"] for r in state["rounds"]] == ["REJECTED", "SIMULATED"]
    assert len(json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))["scheme"]["geometry_revisions"]) == 1


def test_no_llm_preserves_remaining_budget(tmp_path):
    mgr, _ = setup(tmp_path, {"anode.R_s_um": 4.})
    assert optimize(mgr)["status"] == "LLM_UNAVAILABLE"
    assert attempts.budget(mgr)["used"] == 0
    assert optimize(mgr, client({"anode.R_s_um": 4.2}))["attempts"] == 3


@pytest.mark.parametrize("blocker", ["locked", "diffusion", "rate", "missing", "source", "hash", "nonfinite"])
def test_unrepairable_inputs_do_not_call_llm(tmp_path, blocker):
    mgr, doc = setup(tmp_path, {"anode.R_s_um": 4.})
    if blocker == "locked":
        ledger = json.loads((tmp_path / "scheme_ledger.json").read_text(encoding="utf-8"))
        ledger["parameters"]["anode.R_s_um"]["locked"] = True
        atomic_write(tmp_path / "scheme_ledger.json", json.dumps(ledger))
        doc.update(ledger_hash=digest(ledger), ledger_view=ledger)
    elif blocker == "diffusion":
        doc["scheme"]["geometry"]["anode"]["D_s_m2s"] = 1e-5
    elif blocker == "rate":
        doc["scheme"]["geometry"]["condition"]["c_rate"] = 100.
    elif blocker == "missing":
        doc["scheme"]["geometry"]["anode"]["L_um"] = None
    elif blocker == "source":
        doc["scheme"]["geometry"]["parameter_sources"]["anode.R_s_um"] = {"origin": "missing"}
    elif blocker == "nonfinite":
        doc["scheme"]["geometry"]["anode"]["R_s_um"] = float("nan")
    if blocker not in ("nonfinite", "hash"):
        doc["scheme"]["geometry_hash"] = geometry_hash(doc["scheme"]["geometry"])
    elif blocker == "hash":
        doc["scheme"]["geometry_hash"] = "wrong"
    # 非范围错误不需当前方案表一致，用无账本副本验证阻断本身。
    if blocker != "locked":
        (tmp_path / "scheme_ledger.json").unlink()
        for key in ("ledger_hash", "ledger_revision", "ledger_view"):
            doc.pop(key, None)
    atomic_write(tmp_path / "design_scheme.json", json.dumps(doc))
    llm = client({"anode.R_s_um": 4.2})
    assert optimize(mgr, llm)["status"] == "INPUT_BLOCKED"
    assert not llm.calls and attempts.budget(mgr)["used"] == 0


def test_direct_tool_and_optimizer_share_failed_attempt_budget(tmp_path):
    mgr, doc = setup(tmp_path, {"anode.R_s_um": 4.}, target=None)
    rejected = revise_geometry({"anode": {"R_s_um": 1.}}, stage_manager=mgr,
        base_revision=doc["scheme"]["design_revision"], base_geometry_hash=doc["scheme"]["geometry_hash"])
    assert not rejected["success"] and rejected["revision_budget"]["used"] == 1
    assert optimize(mgr, client({"anode.R_s_um": 4.2}))["attempts"] == 2


def test_tolerance_change_does_not_reset_spent_budget(tmp_path):
    mgr, doc = setup(tmp_path, {"anode.R_s_um": 4.})
    attempts.reserve(mgr, "input_repair", doc["scheme"]["geometry_hash"])
    mgr.config["runtime_options"]["target_energy_tolerance_pct"] = 10
    assert attempts.budget(mgr)["used"] == 1


def test_old_success_and_old_spec_cannot_skip_revised_geometry_simulation(tmp_path):
    mgr, doc = setup(tmp_path, target=100)
    assert run_pinn_simulation(stage_manager=mgr)["success"]
    revised = revise_geometry({"cathode": {"L_um": 160.}}, stage_manager=mgr,
        base_revision=doc["scheme"]["design_revision"], base_geometry_hash=doc["scheme"]["geometry_hash"])
    assert revised["success"]
    state = optimize(mgr)
    saved = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    spec = json.loads((tmp_path / "pinn_input_spec.json").read_text(encoding="utf-8"))
    assert state["status"] == "TARGET_MET" and state["attempts"] == 1
    assert spec["cell_spec"]["geometry_hash"] == saved["scheme"]["geometry_hash"]
    assert state["rounds"][0]["status"] == "SIMULATED"


def test_interrupted_request_preserves_one_used_opportunity(tmp_path):
    mgr, doc = setup(tmp_path, {"anode.R_s_um": 4.}, target=None)
    interrupted = attempts.reserve(mgr, "input_repair", doc["scheme"]["geometry_hash"])
    llm = client({"anode.R_s_um": 4.2})
    state = optimize(mgr, llm)
    assert state["attempts"] == 2 and len(llm.calls) == 1
    assert state["rounds"][0]["attempt_id"] == interrupted["attempt_id"]
    assert state["rounds"][0]["status"] == "INTERRUPTED"


def test_committed_candidate_resumes_simulation_without_llm(tmp_path):
    mgr, doc = setup(tmp_path, {"anode.R_s_um": 4.}, target=None)
    fixed = revise_geometry({"anode": {"R_s_um": 4.2}}, stage_manager=mgr,
        base_revision=doc["scheme"]["design_revision"], base_geometry_hash=doc["scheme"]["geometry_hash"])
    assert fixed["success"]
    # 模拟成功落盘后、账本提交标记前中断，靠修订中的 attempt_id 恢复身份。
    attempts.update(mgr, fixed["attempt_id"], status="PROPOSED")
    llm = client(RuntimeError("不应调用"))
    state = optimize(mgr, llm)
    assert state["attempts"] == 1 and state["status"] == "SIMULATION_VALID"
    assert state["rounds"][0]["status"] == "SIMULATED" and not llm.calls


def test_preflight_envelope_repair_without_loading_network(tmp_path, monkeypatch):
    mgr, doc = setup(tmp_path, target=None)
    # 单参数均合法，低倍率与小粒径使组合低于训练包络下限。
    doc["scheme"]["geometry"]["condition"]["c_rate"] = .1
    doc["scheme"]["geometry"]["anode"]["R_s_um"] = 4.105
    doc["scheme"]["geometry"]["cathode"]["R_s_um"] = 2.67
    doc["scheme"]["geometry_hash"] = geometry_hash(doc["scheme"]["geometry"])
    commit_design(tmp_path, doc)
    atomic_write(tmp_path / "design_scheme.json", json.dumps(doc))
    import pinn.spm_runner as runner
    forward = runner.network_forward
    monkeypatch.setattr(runner, "network_forward", lambda *a: pytest.fail("预检不得调用网络"))
    diagnostic = diagnose(doc)
    monkeypatch.setattr(runner, "network_forward", forward)
    if diagnostic["status"] != "OUT_OF_ENVELOPE":
        pytest.fail(f"预期组合越界，实际 {diagnostic}")
    llm = client({"anode.R_s_um": 8.21, "cathode.R_s_um": 5.34})
    state = optimize(mgr, llm)
    assert state["status"] == "SIMULATION_VALID" and state["attempts"] == 1


def test_finalize_stage5_repairs_prior_failed_input_and_reports_budget(tmp_path, monkeypatch):
    from auto_battery_research.util.stage5_execution import finalize_stage5
    from auto_battery_research.util import experiment_design as recipes
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    mgr, doc = setup(tmp_path, {"anode.R_s_um": 4.}, target=100)
    doc["delivery_contract"] = 1
    doc["report_readiness"] = {"category": "CONDITIONAL", "issues": ["物理输入仍待修复：旧半径", "独立学术审核待复核"], "physics_status": "FAILED", "review_status": "UNREVIEWED"}
    atomic_write(tmp_path / "design_scheme.json", json.dumps(doc))
    atomic_write(tmp_path / "stage5_execution.json", json.dumps({"physics": {"status": "FAILED", "input_hash": digest(doc["scheme"])}}))
    monkeypatch.setattr(recipes, "generate", lambda path, current, *args: recipes.engineering_fallback(path, current, "test", "离线验证"))
    llm = client({"anode.R_s_um": 4.2})
    outcome = finalize_stage5(mgr, client=llm)
    assert outcome["key_findings"]["physics"]["status"] == "PASSED"
    assert len(llm.calls) == 1
    saved = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    assert saved["pinn_attempt_summary"]["used"] == 1
    assert saved["physics_input_diagnostic"]["errors"] == []
    assert "独立学术审核待复核" in saved["report_readiness"]["issues"]
    assert any(i.startswith("实验交付限制：") for i in saved["report_readiness"]["issues"])
    assert outcome["key_findings"]["experiment"]["status"] == "FALLBACK"
    mgr.stages[4].status = "PASSED"
    out = run_synthesis_report(stage_manager=mgr)
    assert out["success"], out
    report = tmp_path / "final_research_report.md"
    text = report.read_text(encoding="utf-8")
    assert "已用 1/3 次，剩余 2 次" in text
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    assert checker.do_check()[0]
    report.write_text(text.replace("已用 1/3 次，剩余 2 次", "已用 0/3 次，剩余 3 次"), encoding="utf-8")
    assert not checker.do_check()[0]


def test_failed_finalized_input_does_not_restart_budget(tmp_path, monkeypatch):
    from auto_battery_research.util.stage5_execution import finalize_stage5
    from auto_battery_research.util import experiment_design as recipes
    mgr, _ = setup(tmp_path, {"anode.R_s_um": 4.})
    monkeypatch.setattr(recipes, "generate", lambda *args: {"plan_hash": "test", "parameters": {}})
    llm = client({"anode.R_s_um": 1.})
    result = finalize_stage5(mgr, client=llm)
    assert result["key_findings"]["physics"]["status"] == "FAILED" and len(llm.calls) == 3
    finalize_stage5(mgr, client=llm)
    assert len(llm.calls) == 3 and attempts.budget(mgr)["used"] == 3


def test_existing_task_input_copy_repairs_without_changing_original(tmp_path):
    task = next((Path(__file__).resolve().parents[2] / "output" / "tasks").glob("*_b08bf342"), None)
    if task is None:
        pytest.skip("本地历史课题可用时验证真实输入副本；CI使用其他独立回归")
    original = (task / "design_scheme.json").read_bytes()
    source = json.loads(original)
    mgr, doc = setup(tmp_path, target=None)
    doc["scheme"] = source["scheme"]
    doc["scheme"]["initial_geometry_hash"] = doc["scheme"]["geometry_hash"]
    # 只迁移当前结构化输入，不复制旧模型服务状态或重新运行 Stage 4。
    for name in ("scheme_ledger.json",):
        (tmp_path / name).unlink()
    for key in ("ledger_hash", "ledger_revision", "ledger_view"):
        doc.pop(key, None)
    commit_design(tmp_path, doc)
    atomic_write(tmp_path / "design_scheme.json", json.dumps(doc))
    initial_status = diagnose(doc)["status"]
    inherited = len([r for r in doc["scheme"].get("geometry_revisions", []) if r.get("kind") == "revision"])
    state = optimize(mgr, client({"anode.R_s_um": 4.2}))
    assert state["status"] == "SIMULATION_VALID" and state["attempts"] == inherited + (0 if initial_status == "VALID" else 1)
    saved = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    for comp in ("cathode", "anode"):
        assert saved["scheme"]["geometry"][comp]["D_s_m2s"] == source["scheme"]["geometry"][comp]["D_s_m2s"]
    assert (task / "design_scheme.json").read_bytes() == original


def test_rejected_training_envelope_candidate_is_not_committed(tmp_path):
    mgr, doc = setup(tmp_path, {"anode.R_s_um": 4.105, "cathode.R_s_um": 2.67}, target=None)
    doc["scheme"]["geometry"]["condition"]["c_rate"] = .1
    doc["scheme"]["geometry_hash"] = geometry_hash(doc["scheme"]["geometry"])
    commit_design(tmp_path, doc)
    atomic_write(tmp_path / "design_scheme.json", json.dumps(doc))
    assert diagnose(doc)["status"] == "OUT_OF_ENVELOPE"
    source = (tmp_path / "design_scheme.json").read_bytes()
    llm = client({"cathode.R_s_um": 2.7})
    state = optimize(mgr, llm)
    assert state["attempts"] == 3 and all(r["status"] == "REJECTED" for r in state["rounds"])
    assert (tmp_path / "design_scheme.json").read_bytes() == source


def test_legacy_attempts_inherit_larger_count_without_reset(tmp_path):
    mgr, doc = setup(tmp_path, {"anode.R_s_um": 4.}, target=None)
    old = {"identity": digest([mgr.target_goal, doc.get("generated_at"), doc["scheme"].get("initial_geometry_hash"), None]),
           "attempts": 2, "rounds": [{"status": "REJECTED", "error": "旧失败"}, {"status": "REJECTED", "error": "旧失败"}], "finished": True}
    atomic_write(tmp_path / attempts.STATE_FILE, json.dumps(old))
    state = optimize(mgr, client({"anode.R_s_um": 4.2}))
    assert state["attempts"] == 3 and state["status"] == "SIMULATION_VALID"
    assert state["rounds"][-1]["attempt"] == 3


def test_corrupt_attempt_ledger_cannot_create_fresh_budget(tmp_path):
    mgr, _ = setup(tmp_path, {"anode.R_s_um": 4.})
    atomic_write(tmp_path / attempts.STATE_FILE, "bad json")
    with pytest.raises(ValueError):
        attempts.budget(mgr)
    assert (tmp_path / attempts.STATE_FILE).read_text(encoding="utf-8") == "bad json"
