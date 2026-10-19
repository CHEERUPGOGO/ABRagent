"""最终设计/实际输入/报告一致性回归，使用真实 numpy PINN，全部离线。"""

import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.simulation import build_cell_spec, run_pinn_discharge
from auto_battery_research.simulation.geometry_contract import build_geometry_contract, geometry_hash
from auto_battery_research.util.hashing import canonical_hash, stage5_scheme_projection
from auto_battery_research.util.report_contract import report_context, render_energy, render_recipe
from auto_battery_research.tools.workflow_actions import run_synthesis_report
from auto_battery_research.checkers.final_report_checker import FinalReportChecker


def design(np_ratio=None):
    s = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "high_concentration", "design_revision": 2}
    c = {"cathode": {"L_um": 170., "porosity": .15, "R_s_um": 2.7, "D_s_m2s": None},
         "anode": {"L_um": 145., "porosity": .18, "R_s_um": 4.11, "D_s_m2s": None},
         "condition": {"v_min": 2.5, "v_max": 4.2, "c_rate": .5}}
    if np_ratio is not None:
        c["n_p_ratio"] = np_ratio
    s["geometry"] = build_geometry_contract(c, s, [])
    s["geometry_hash"] = geometry_hash(s["geometry"])
    return s


def test_missing_np_keeps_both_declared_thicknesses():
    s = design()
    spec = build_cell_spec(s, {})
    assert s["geometry"]["n_p_ratio"] is None
    assert spec["anode"]["N_P_ratio"] is None
    result = run_pinn_discharge(spec, scheme=s, c_rate=.5)
    assert result["status"] == "CONVERGED"
    assert result["pinn"]["design"]["ne"]["L"] == pytest.approx(145e-6)
    assert result["pinn"]["design"]["pe"]["L"] == pytest.approx(170e-6)
    assert not any("N/P" in w for w in result.get("warnings", []))


def test_llm_cannot_introduce_unspecified_np():
    from auto_battery_research.simulation.geometry_contract import constrain_generated_np
    candidate = {"n_p_ratio": 1.12}
    assert constrain_generated_np(candidate, "设计350Wh/kg电池")["n_p_ratio"] is None
    assert constrain_generated_np(candidate, "设计电池，N/P=1.08")["n_p_ratio"] == 1.08
    assert candidate["n_p_ratio"] == 1.12


def test_explicit_np_in_design_request_is_preserved():
    from auto_battery_research.simulation.geometry_contract import constrain_generated_np
    assert constrain_generated_np({"n_p_ratio": 1.12}, "输出方案，N/P 比为 1.08")['n_p_ratio'] == 1.08


def test_explicit_np_still_controls_effective_geometry_and_energy_scope():
    s = design(1.12)
    result = run_pinn_discharge(build_cell_spec(s, {}), scheme=s)
    assert result["status"] == "CONVERGED"
    assert result["pinn"]["design"]["ne"]["L"] == pytest.approx(215.92744142011836e-6)
    accounting = result["energy_accounting"]
    assert accounting["cell_energy_ratio"] == .42
    assert result["energy_wh_kg"] == pytest.approx(result["material_energy_Wh_kg"] * .42)


def report_fixture(tmp_path):
    s = design(1.12)
    spec = {"cell_spec": build_cell_spec(s, {}), "scheme": stage5_scheme_projection(s)}
    spec["cell_spec_hash"] = canonical_hash(spec["cell_spec"])
    result = run_pinn_discharge(spec["cell_spec"], scheme=s)
    result["pinn"].update(spec_hash=spec["cell_spec_hash"], scheme_hash=canonical_hash(spec["scheme"]), run_id="test_run")
    doc = {"target": "测试350Wh/kg电池", "scheme": s, "evidence": []}
    for filename, obj in (("design_scheme.json", doc), ("pinn_input_spec.json", spec), ("pinn_simulation_result.json", result)):
        (tmp_path / filename).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "design_scheme.md").write_text("旧几何：78/65μm；4.4V；体系物理上限；但**电芯级", encoding="utf-8")
    (tmp_path / "stage_journals.json").write_text('[{"stage_id": 5, "notes": "详细发现"}]', encoding="utf-8")
    stages = [SimpleNamespace(id=i, status="PASSED", skip=False, skip_reason="", allow_skip=False) for i in range(1, 7)]
    mgr = SimpleNamespace(config={"paths": {"output_dir": str(tmp_path)}}, root_dir=tmp_path,
                          target_goal=doc["target"], stages=stages, is_legacy_task=False,
                          get_task_output_dir=lambda *a, **kw: tmp_path, get_stage_by_id=lambda i: stages[i-1],
                          get_all_stage_journal=lambda: [{"stage_id": 5, "notes": "多行\n发现", "duration_seconds": 10}])
    return mgr, doc, spec, result


def test_report_uses_final_design_and_rejects_tampered_recipe(tmp_path):
    mgr, doc, spec, result = report_fixture(tmp_path)
    out = run_synthesis_report(target_query=mgr.target_goal, stage_manager=mgr)
    assert out["success"]
    path = tmp_path / "final_research_report.md"
    text = path.read_text(encoding="utf-8")
    assert "旧几何" not in text and "4.4V" not in text and "96:2:2" not in text
    assert "215.927" in text and "170" in text
    assert "未达标" in text and "固定折算系数 0.42" in text and "不代表物理上限" in text
    assert "2.5–4.2 V" in text
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    ok, diag = checker.do_check(is_complete=True)
    assert ok, diag
    path.write_text(text.replace("已声明电压窗口：2.5–4.2 V", "已声明电压窗口：2.5–4.4 V"), encoding="utf-8")
    ok, diag = checker.do_check(is_complete=True)
    assert not ok and diag["error_code"] == "REPORT_FINAL_INPUT_MISMATCH"


def test_recipe_missing_values_stay_unknown():
    context = report_context({"scheme": {"cathode": "NCM811"}}, None)
    text = render_recipe(context)
    assert "4.4" not in text and "96:2:2" not in text and "未提供" in text
    assert "不能判定达标" in render_energy(context, 350, 5)


def test_six_section_report_requires_appendix(tmp_path):
    mgr, _, _, _ = report_fixture(tmp_path)
    assert run_synthesis_report(target_query=mgr.target_goal, stage_manager=mgr)["success"]
    path = tmp_path / "final_research_report.md"
    text = path.read_text(encoding="utf-8")
    assert "## 2. 材料选择与文献依据" in text
    assert "## 6. 参考来源附录" in text
    path.write_text(text.replace("## 6. 参考来源附录", "附录"), encoding="utf-8")
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    assert checker.do_check()[0] is False


def test_sanitized_goal_retains_numeric_target():
    from auto_battery_research.tools.workflow_actions import extract_energy_target_wh_kg
    assert extract_energy_target_wh_kg("设计350Wh_kg高比能电池") == 350


def _plan_fixture():
    from auto_battery_research.util.report_contract import report_context
    doc = {"scheme": {"cathode": "NCM811", "anode": "si_base",
                      "geometry": {"cathode": {"L_um": 170.}, "anode": {"L_um": 145.}}},
           "evidence": [{"passage_id": "p1", "source": "paper", "text": "制备"}],
           "reviewer_output": {"issues": []}}
    result = {"status": "CONVERGED", "energy_wh_kg": 242.0, "c_rate": 0.5}
    context = report_context(doc, result, {})
    return doc, context, result, {}


def test_experiment_plan_validation_flags_fabrication_and_bad_citation():
    from auto_battery_research.util.report_recipe_llm import validate_plan_text
    doc, context, result, spec = _plan_fixture()
    good = "### 5.1 目标\nNCM811 正极与 si_base 负极对照 [p1]。\n### 5.2 制备\n厚度 170 μm 验证。\n### 5.3 组装\n### 5.4 测试\n" + "验证路线" * 60
    assert validate_plan_text(good, doc, context, 350, 5) == []
    assert any("未在本次归档证据中定位" in e for e in validate_plan_text(good + "[ghost]", doc, context, 350, 5))
    assert any("已达成" in e or "达标" in e for e in validate_plan_text(
        good + "本方案已经通过实验验证并已达成 350 Wh/kg。", doc, context, 350, 5))
    assert any("厚度表述" in e for e in validate_plan_text(good.replace("170 μm", "99 μm"), doc, context, 350, 5))


def test_llm_plan_used_and_recorded(tmp_path, monkeypatch):
    import auto_battery_research.util.report_recipe_llm as plan_mod
    mgr, doc, spec, result = report_fixture(tmp_path)
    text = ("### 5.1 目标与分组\nNCM811 正极与 si_base 负极对照 [p1]。\n### 5.2 材料制备\n"
            "以 170 μm 与 215.927 μm 作为极片验收点。\n### 5.3 组装与化成\n待测。\n### 5.4 性能测试\n" + "对照实验" * 60)
    doc["evidence"] = [{"passage_id": "p1", "source": "paper", "text": "制备"}]
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "design_scheme.md").write_text("完整方案正文", encoding="utf-8")
    monkeypatch.setattr(plan_mod, "_llm_client", lambda mgr: type("C", (), {
        "available": True, "_model_name": "stub",
        "chat": lambda self, system, user, **kw: text})())
    assert run_synthesis_report(target_query=mgr.target_goal, stage_manager=mgr)["success"]
    report = (tmp_path / "final_research_report.md").read_text(encoding="utf-8")
    assert "### 5.1 目标与分组" in report
    record = json.loads((tmp_path / "experiment_plan.json").read_text(encoding="utf-8"))
    assert record["citations"] == ["p1"] and "170 μm" in record["text"]
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    assert checker.do_check(is_complete=True)[0] is True


def test_invalid_llm_plan_falls_back_to_deterministic(tmp_path, monkeypatch):
    import auto_battery_research.util.report_recipe_llm as plan_mod
    mgr, doc, spec, result = report_fixture(tmp_path)
    (tmp_path / "design_scheme.md").write_text("完整方案正文", encoding="utf-8")
    monkeypatch.setattr(plan_mod, "_llm_client", lambda mgr: type("C", (), {
        "available": True, "_model_name": "stub",
        "chat": lambda self, system, user, **kw: "### 5.1 目标\n已经通过实验验证达成 350 Wh/kg [ghost]。"})())
    assert run_synthesis_report(target_query=mgr.target_goal, stage_manager=mgr)["success"]
    report = (tmp_path / "final_research_report.md").read_text(encoding="utf-8")
    assert "<!-- abr-experiment-plan-fallback -->" in report and "abr-final-recipe:start" in report
    assert not (tmp_path / "experiment_plan.json").exists()
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    assert checker.do_check(is_complete=True)[0] is True


def test_stale_plan_input_is_rejected(tmp_path, monkeypatch):
    import auto_battery_research.util.report_recipe_llm as plan_mod
    mgr, doc, spec, result = report_fixture(tmp_path)
    doc["evidence"] = [{"passage_id": "p1", "source": "paper", "text": "制备"}]
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "design_scheme.md").write_text("完整方案正文", encoding="utf-8")
    text = ("### 5.1 目标\nNCM811 正极与 si_base 负极 [p1]\n### 5.2 制备\n厚度 170 与 215.927 μm\n### 5.3 组装\n### 5.4 测试\n" + "对照" * 60)
    monkeypatch.setattr(plan_mod, "_llm_client", lambda mgr: type("C", (), {
        "available": True, "_model_name": "stub", "chat": lambda self, s, u, **kw: text})())
    assert run_synthesis_report(target_query=mgr.target_goal, stage_manager=mgr)["success"]
    record = json.loads((tmp_path / "experiment_plan.json").read_text(encoding="utf-8"))
    record["fingerprint"] = "stale"
    (tmp_path / "experiment_plan.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    ok, diag = checker.do_check(is_complete=True)
    assert not ok and diag["error_code"] == "REPORT_FINAL_INPUT_MISMATCH"


def test_paper_dedup_and_material_excerpt_scope():
    from auto_battery_research.util.report_analysis import render_sources, render_materials
    doc = {"evidence": [
        {"passage_id": "p1", "source": "/old/10.1021_test.md", "metadata": {"source_paper": "10.1021/test", "title": "研究"}},
        {"passage_id": "p2", "source": "new/10.1021_test.md", "metadata": {"source_paper": "10.1021/test", "title": "研究"}},
    ], "final_answer": "正极单晶化为候选路线 [p1][p2]。\n\n冻结几何：正极厚度99 μm [p1]。\n\n负极虚构引用 [unknown]。"}
    sources = render_sources(doc)
    assert "归并为 1 个来源" in sources
    materials = render_materials(doc)
    assert "单晶化为候选路线" in materials
    assert "99 μm" not in materials and "[unknown]" not in materials
    assert "/old/" not in materials


def test_material_decisions_distinguish_selected_and_proposed():
    from auto_battery_research.util.report_analysis import render_materials
    doc = {"scheme": {"cathode": "NCM811", "anode": "si_base"},
           "evidence": [{"passage_id": "p1", "source": "paper"}],
           "final_answer": "**负极**\n- 优选碳包覆硅路线 [p1]。\n- 或 Si-Cu 一体化路线 [p1]。"}
    text = render_materials(doc)
    assert "采用 NCM811 材料体系" in text
    assert "硅基/硅碳负极（si_base）" in text
    assert "**备选路线**：Si-Cu" in text
    assert "尚未定案" in text


def test_component_heading_wins_over_cross_mention():
    """正极段含未定位引用时不能丢弃整段，也不能退回到含"正极"字样的隔膜段。"""
    from auto_battery_research.util.report_analysis import render_materials
    doc = {"scheme": {"cathode": "NCM811"},
           "evidence": [{"passage_id": "p1", "source": "paper"}],
           "final_answer": "**正极（主选 NCM811）**\n- NCM811 可逆容量约 200 mAh/g [p1]；抑氧依赖包覆 [ghost]。\n\n"
                           "**隔膜与集流体**\n- 隔膜 16 µm，匹配正极高压运行 [p1]。"}
    text = render_materials(doc)
    assert "NCM811 可逆容量约 200 mAh/g" in text
    assert "引用核对提示" in text and "[ghost]" in text
    assert "隔膜 16 µm" not in text.split("### 2.2")[0]


def test_removed_contract_marker_cannot_bypass_identity(tmp_path):
    mgr, _, _, _ = report_fixture(tmp_path)
    assert run_synthesis_report(target_query=mgr.target_goal, stage_manager=mgr)["success"]
    path = tmp_path / "final_research_report.md"
    path.write_text(path.read_text(encoding="utf-8").replace("<!-- abr-report-contract:2 -->", ""), encoding="utf-8")
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    ok, diag = checker.do_check(is_complete=True)
    assert not ok and diag["error_code"] == "REPORT_FINAL_INPUT_MISMATCH"
