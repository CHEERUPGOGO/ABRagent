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


def test_extract_design_anchors_and_report_rendering():
    """第 3 节设计锚点提取应准确归一化下标并传递到 Stage 6 渲染与实验方案 prompt。"""
    from lmllm.RAG.rag_pipeline import RAGPipeline
    from auto_battery_research.util.report_contract import render_selected_design
    from auto_battery_research.util.report_recipe_llm import _assemble_user_prompt
    from auto_battery_research.util.report_analysis import render_materials

    question = (
        "双位点共掺杂改进无钴富锂 Li1.2Mn0.6Ni0.2O2 正极，重点评估 Zn–Na、Cu–Na 组合，"
        "匹配金属锂负极和 1 mol/L LiPF₆/EC+DMC。"
    )
    sec3_excerpt = (
        "### 最优组合的位点归属与掺量\n"
        "**最优组合：Zn–Na 共掺，名义式 Li₁.₁₇Na₀.₀₃Mn₀.₆Ni₀.₁₈Zn₀.₀₂O₂（总掺量以总阳离子计 2.5 mol%，远低于 5 mol% 上限）**\n"
        "### 合成方案\n"
        "**路线 A：碳酸盐共沉淀 + 固相锂化（推荐，易放大）**\n"
        "- 主烧：**925 °C 保温 12 h**\n"
        "### 纽扣电池测试方案\n"
        "活性物质:导电炭（Super P）:PVDF = **92:4:4**（wt），涂于 **15 μm Al 箔**，冲片 φ12 mm。\n"
        "- 隔膜：**Celgard 2400**；注液量 **60 μL**\n"
    )
    anchors = RAGPipeline._extract_design_anchors(question, sec3_excerpt)
    assert anchors["primary_formula"] == "Li1.17Na0.03Mn0.6Ni0.18Zn0.02O2"
    assert anchors["doping_upper_limit_mol_pct"] == pytest.approx(5.0)
    assert anchors["precursor_type"] == "carbonate_coprecipitation"
    assert anchors["calcination_temp_c"] == pytest.approx(925.0)
    assert anchors["calcination_hours"] == pytest.approx(12.0)
    assert anchors["al_foil_um"] == pytest.approx(15.0)
    assert anchors["separator"] == "Celgard 2400"
    assert anchors["cathode_diameter_mm"] == pytest.approx(12.0)
    assert anchors["cathode_area_cm2"] == pytest.approx(1.13)
    assert anchors["electrolyte_volume_uL"] == "60"
    assert anchors["slurry_ratio"] == "92:4:4"

    scheme = {
        "cathode": "LRMO",
        "anode": "li_metal",
        "electrolyte": "carbonate_ec",
        "user_locked_components": ["cathode", "anode", "electrolyte"],
        "design_anchors": anchors,
        "geometry": {
            "schema_version": "1.1",
            "cathode": {"L_um": 20.0, "porosity": 0.32, "R_s_um": None, "D_s_m2s": None},
            "anode": {"L_um": 20.0, "porosity": 0.0, "R_s_um": None, "D_s_m2s": None},
            "condition": {"v_min": 2.0, "v_max": 4.8, "c_rate": 1.0},
        },
    }
    doc = {"scheme": scheme, "evidence": [{"passage_id": "p1", "source": "paper"}],
           "final_answer": "**正极**\n- LRMO 双位点共掺杂 [p1]。\n\n**电解液**\n- 1 mol/L LiPF6/EC+DMC [p1]。"}
    ctx = report_context(doc, {"status": "SKIPPED"}, {})
    selected_md = render_selected_design(ctx)
    recipe_md = render_recipe(ctx)
    user_prompt = _assemble_user_prompt(question, sec3_excerpt, ctx, doc, {"status": "SKIPPED"}, {})
    materials_md = render_materials(doc)

    assert "0.534 g/cm³" in selected_md and "Li1.17Na0.03Mn0.6Ni0.18Zn0.02O2" in selected_md
    assert "0.534 g/cm³" in recipe_md and "Celgard 2400" in recipe_md
    assert "锁定配方与工艺锚点" in user_prompt and "carbonate_coprecipitation" in user_prompt
    assert "常规碳酸酯电解液（carbonate_ec）" in materials_md and "无钴富锂锰基正极（lrmo）" in materials_md


def test_validate_plan_text_catches_cross_section_contradictions():
    """Stage 6 方案验收应放行完全一致的方案，并精确拦截 real report 中的 5 类跨节矛盾。"""
    from auto_battery_research.util.report_recipe_llm import validate_plan_text

    anchors = {
        "primary_formula": "Li1.17Na0.03Mn0.6Ni0.18Zn0.02O2",
        "doping_upper_limit_mol_pct": 5.0,
        "precursor_type": "carbonate_coprecipitation",
        "calcination_temp_c": 925.0,
        "calcination_hours": 12.0,
        "al_foil_um": 15.0,
        "separator": "Celgard 2400",
        "cathode_diameter_mm": 12.0,
    }
    doc = {
        "scheme": {
            "cathode": "LRMO",
            "anode": "li_metal",
            "electrolyte": "carbonate_ec",
            "user_locked_components": ["cathode", "anode", "electrolyte"],
            "design_anchors": anchors,
            "geometry": {
                "cathode": {"L_um": 20.0, "porosity": 0.32},
                "anode": {"L_um": 20.0, "porosity": 0.0},
            },
        },
        "evidence": [{"passage_id": "p1", "source": "paper", "text": "LRMO"}],
        "reviewer_output": {"issues": []},
    }
    ctx = report_context(doc, {"status": "SKIPPED"}, {})

    consistent_plan = (
        "### 5.1 实验目标与分组\n"
        "正极采用 LRMO（Li₁.₁₇Na₀.₀₃Mn₀.₆Ni₀.₁₈Zn₀.₀₂O₂），总掺量 ≤ 5 mol%，匹配 20 μm Li 箔负极。\n"
        "主配方：1 mol/L LiPF₆/EC+DMC；LHCE 仅作界面机理对照 [p1]。\n"
        "| 排行 | 推荐组合 | 名义配方示例 |\n"
        "|---|---|---|\n"
        "| 1 | Zn–Na 共掺 | Li₁.₁₇Na₀.₀₃Mn₀.₆Ni₀.₁₈Zn₀.₀₂O₂ |\n"
        "### 5.2 材料制备与极片加工\n"
        "前驱体优先采用碳酸盐共沉淀，金属盐与 Na₂CO₃ 经双通道蠕动泵并流滴加 [p1]。\n"
        "主烧 925 °C 保温 12 h；水洗后须用 ICP-OES/MS 核查 Na 流失。\n"
        "极片厚度 20 μm，涂于 15 μm 电池级 Al 箔，正极冲片 φ12 mm。\n"
        "### 5.3 电芯组装与化成\n"
        "CR2032 扣电采用 Celgard 2400 隔膜，常规注液量 100–120 μL（60 μL 仅作贫液对照组）。\n"
        "金属锂箔真密度 0.534 g/cm³，正极半电池属锂过量体系，平行设 Li||Li 与 Li||Cu 对照。\n"
        "### 5.4 性能测试与模型对照\n" + "测试矩阵" * 40
    )
    assert validate_plan_text(consistent_plan, doc, ctx, target=None, tolerance=0.05) == []

    # 1. 电解液主次反转 (carbonate_ec 锁定却写 LHCE 为主推荐)
    bad_elec = consistent_plan.replace(
        "主配方：1 mol/L LiPF₆/EC+DMC；LHCE 仅作界面机理对照",
        "主推荐电解液：3.0 M LiFSI in EC/DMC + HFE/TTE (LHCE)；1 mol/L LiPF₆ 仅作对照",
    )
    assert any("carbonate_ec" in e for e in validate_plan_text(bad_elec, doc, ctx, None, 0.05))

    # 反向锁定 lhce 却写 LiPF6 为主配方
    doc_lhce = copy.deepcopy(doc)
    doc_lhce["scheme"]["electrolyte"] = "lhce"
    ctx_lhce = report_context(doc_lhce, {"status": "SKIPPED"}, {})
    bad_lhce_rev = consistent_plan.replace(
        "主配方：1 mol/L LiPF₆/EC+DMC；LHCE 仅作界面机理对照",
        "主推荐：1 mol/L LiPF₆/EC+DMC，LHCE 仅作对照",
    )
    assert any("(lhce)矛盾" in e for e in validate_plan_text(bad_lhce_rev, doc_lhce, ctx_lhce, None, 0.05))

    # 2. 金属锂非物理压实密度 2.8 g/cm³
    bad_li_comp = consistent_plan + "\n负极金属锂箔压实密度 2.8 g/cm³。\n"
    assert any("真密度 0.534" in e for e in validate_plan_text(bad_li_comp, doc, ctx, None, 0.05))

    # 3a. 排行第 1 名义化学式与第 3 节不一致
    bad_formula = consistent_plan.replace("Li₁.₁₇Na₀.₀₃Mn₀.₆Ni₀.₁₈Zn₀.₀₂O₂", "Li₁.₁₈₈Na₀.₀₁₂Mn₀.₆Ni₀.₁₈₄Zn₀.₀₁₆O₂")
    assert any("名义化学式" in e for e in validate_plan_text(bad_formula, doc, ctx, None, 0.05))

    # 3b. 总掺杂量上限不一致 (<= 3.5 mol% vs 5 mol%)
    bad_cap = consistent_plan.replace("总掺量 ≤ 5 mol%", "总掺量 ≤ 3.5 mol%")
    assert any("总掺杂量上限" in e for e in validate_plan_text(bad_cap, doc, ctx, None, 0.05))

    # 4a. 前驱体路线不一致 (氢氧化物 vs 碳酸盐共沉淀)
    bad_precursor = consistent_plan.replace(
        "前驱体优先采用碳酸盐共沉淀",
        "前驱体优先采用氢氧化物共沉淀 Mn0.6Ni0.184Zn0.016(OH)2",
    )
    assert any("合成路线" in e for e in validate_plan_text(bad_precursor, doc, ctx, None, 0.05))

    # 4b. 主烧温度不一致 (850–900 °C vs 925 °C)
    bad_temp = consistent_plan.replace("主烧 925 °C 保温 12 h", "烧结 850–900 °C 保温 10–12 h")
    assert any("烧结温度" in e for e in validate_plan_text(bad_temp, doc, ctx, None, 0.05))

    # 5a. Al 箔厚度不一致 (20 μm vs 15 μm)
    bad_al = consistent_plan.replace("15 μm 电池级 Al 箔", "20 μm 电池级 Al 箔")
    assert any("Al 箔厚度" in e for e in validate_plan_text(bad_al, doc, ctx, None, 0.05))

    # 5b. 隔膜型号不一致 (Celgard 2325 vs Celgard 2400)
    bad_sep = consistent_plan.replace("Celgard 2400", "Celgard 2325")
    assert any("隔膜型号" in e for e in validate_plan_text(bad_sep, doc, ctx, None, 0.05))

    # 5c. 正极冲片直径不一致 (16.6 mm vs 12 mm)
    bad_diam = consistent_plan.replace("正极冲片 φ12 mm", "正极冲片 φ16.6 mm")
    assert any("正极尺寸" in e for e in validate_plan_text(bad_diam, doc, ctx, None, 0.05))


def test_render_materials_dedup_risk_exclusion_and_anchors():
    """第 2 节应从 design_anchors 解析隔膜/集流体、排除第 5 节风险条目、避免跨小节重复摘录，并保留已填充文献题名."""
    from auto_battery_research.util.report_analysis import render_materials, render_sources

    doc = {
        "scheme": {
            "cathode": "NCM811",
            "anode": "li_metal",
            "electrolyte": "carbonate_ec",
            "design_anchors": {
                "separator": "Celgard 2400",
                "al_foil_um": "15-16",
            },
        },
        "evidence": [
            {
                "passage_id": "p1",
                "source": "/home/ls/xiaoyue/LLM2/LMLLM/database/type/cathode/10.1021_test1.md",
                "metadata": {"source_paper": "10.1021/test1", "title": ""},
            },
            {
                "passage_id": "p2",
                "source": "database/type/cathode/10.1021_test1.md",
                "title": "High-Voltage Cathode Stabilization",
                "metadata": {"source_paper": "10.1021/test1", "title": "High-Voltage Cathode Stabilization"},
            },
            {
                "passage_id": "p3",
                "source": "/old/10.1016_anode.md",
                "title": "Lithium Metal Interface Design",
                "metadata": {"source_paper": "10.1016/anode"},
            },
        ],
        "final_answer": (
            "## 2. 推荐材料组合\n"
            "NCM811 正极采用单晶高镍路线提升高压循环稳定性 [p1]。负极采用 20 μm 金属锂箔，在常规碳酸酯电解液中需抑制枝晶生长 [p3]。\n\n"
            "## 5. 风险与数据缺口\n"
            "1. **掺杂偏析**：晶界偏析对金属锂负极长期界面影响尚不明确 [p3]。\n"
            "2. **电解液副反应**：高压分解产物迁移至负极表面诱发副反应 [p3]。\n\n"
            "1. **半电池固有偏差**：过量锂负极掩盖全电池循环衰减 [p3]。\n"
        ),
    }

    sources_md = render_sources(doc)
    assert "归并为 2 个来源" in sources_md
    assert "High-Voltage Cathode Stabilization" in sources_md
    assert "/home/ls/xiaoyue" not in sources_md
    assert "database/type/cathode/10.1021_test1.md" in sources_md
    assert "/old/10.1016_anode.md" in sources_md

    materials_md = render_materials(doc)
    assert "| 隔膜 | Celgard 2400 | 体系已选定" in materials_md
    assert "| 集流体 | Al 箔 (15–16 μm) | 体系已选定" in materials_md
    assert "采用 Celgard 2400 材料体系" in materials_md
    assert "采用 Al 箔 (15–16 μm) 材料体系" in materials_md

    sec_cathode = materials_md.split("### 2.1 正极")[1].split("### 2.2 负极")[0]
    sec_anode = materials_md.split("### 2.2 负极")[1].split("### 2.3 电解液与添加剂")[0]
    sec_electrolyte = materials_md.split("### 2.3 电解液与添加剂")[1].split("### 2.4 隔膜")[0]

    # 风险/数据缺口条目不能泄漏进 2.2 负极
    for risk_kw in ("掺杂偏析", "电解液副反应", "半电池固有偏差"):
        assert risk_kw not in sec_anode
        assert risk_kw not in materials_md

    # 跨小节不重复粘贴整段原文：2.2 仅提取负极相关句，2.3 不重复 2.2 已引用的同一子句
    assert "NCM811 正极采用单晶高镍路线提升高压循环稳定性" in sec_cathode
    assert "NCM811 正极采用单晶高镍路线" not in sec_anode
    assert "负极采用 20 μm 金属锂箔" in sec_anode
    assert "负极采用 20 μm 金属锂箔" not in sec_electrolyte


def test_publish_report_audit_and_journal_sync_updates_stage6_table(tmp_path):
    """验证单条 report_fixture 兼容、多阶段自动复用展示、Stage 6 编研时不标 FAILED 及完成后双表同步更新."""
    from auto_battery_research.util.reports import publish_report_audit
    from auto_battery_research.workflow.stage_manager import StageManager

    # 1. 保留单条 report_fixture 存根行为 (SimpleNamespace 无 name/expected_outputs)
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    mgr_stub, _, _, _ = report_fixture(stub_dir)
    assert run_synthesis_report(target_query=mgr_stub.target_goal, stage_manager=mgr_stub)["success"]
    stub_report = (stub_dir / "final_research_report.md").read_text(encoding="utf-8")
    assert "| Stage 5 |" in stub_report
    assert "| Stage 1 |" not in stub_report

    # 2. 多阶段真实 StageManager：Stage 1-2 自动预检 PASSED (无 journal)，Stage 3-5 有 journal，Stage 6 曾 FAILED
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    goal = "测试350Wh/kg电池"
    mgr = StageManager(workspace_root=str(ws_dir), target_goal=goal, skip_pinn=False)
    task_dir = mgr.get_task_output_dir()

    s = design(1.12)
    spec = {"cell_spec": build_cell_spec(s, {}), "scheme": stage5_scheme_projection(s)}
    spec["cell_spec_hash"] = canonical_hash(spec["cell_spec"])
    result = run_pinn_discharge(spec["cell_spec"], scheme=s)
    result["pinn"].update(spec_hash=spec["cell_spec_hash"], scheme_hash=canonical_hash(spec["scheme"]), run_id="test_run")
    doc = {"target": goal, "scheme": s, "evidence": []}
    for filename, obj in (("design_scheme.json", doc), ("pinn_input_spec.json", spec), ("pinn_simulation_result.json", result)):
        (task_dir / filename).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    (task_dir / "design_scheme.md").write_text("测试方案背景", encoding="utf-8")

    # 模拟 Stage 1-2 自动复用到 PASSED (不写 journal)，Stage 3-5 写入 journal，Stage 6 上轮曾 FAILED
    for i in range(5):
        mgr.stages[i].status = "PASSED"
    mgr.stages[5].status = "FAILED"
    mgr.current_stage_idx = 5

    for sid in (3, 4, 5):
        mgr.set_stage_journal(sid, notes=f"Stage {sid} 已完成", key_findings={"duration_seconds": float(sid)})
    # 模拟重试前 Stage 6 已有旧 journal
    mgr.set_stage_journal(6, notes="首轮生成待终审", key_findings={"duration_seconds": 2.0})

    out = run_synthesis_report(target_query=goal, stage_manager=mgr)
    assert out["success"]
    report_path = task_dir / "final_research_report.md"
    pre_audit_text = report_path.read_text(encoding="utf-8")
    table_section_pre = pre_audit_text.split("### 阶段执行履历")[1]
    assert "Stage 6" in table_section_pre
    assert "IN_PROGRESS (待终审)" in table_section_pre
    assert "状态 FAILED" not in table_section_pre
    assert "| Stage 1 |" in table_section_pre and "既有基础资产预检通过" in table_section_pre
    assert "| Stage 2 |" in table_section_pre

    # 3. Stage 6 记录正式日志并 complete_stage(6) -> 双文件同步为 PASSED + 真实耗时 + 交付物
    mgr.set_stage_journal(6, notes="Stage 6 终审通过", key_findings={"duration_seconds": 14.5})
    ok, comp_res = mgr.complete_stage(6)
    assert ok, comp_res

    journals_md = (task_dir / "stage_journals.md").read_text(encoding="utf-8")
    assert "| Stage 1 |" in journals_md and "复用通过" in journals_md
    assert "| Stage 2 |" in journals_md and "复用通过" in journals_md
    assert "| Stage 6 |" in journals_md and "14.5s" in journals_md

    post_audit_text = report_path.read_text(encoding="utf-8")
    table_section_post = post_audit_text.split("### 阶段执行履历")[1]
    assert "IN_PROGRESS (待终审)" not in table_section_post
    assert "| Stage 6 | 综合研发报告生成 | 状态 PASSED；耗时 14.5s；详细记录见 stage_journals.json | `final_research_report.md, stage_journals.json` |" in table_section_post

    # 4. complete_stage(6) 后再次 set_stage_journal(6, ...) 同样立即刷新底部履历表；非契约报告与缺失 json 安全兜底
    mgr.set_stage_journal(6, notes="补充终审耗时", key_findings={"duration_seconds": 19.2})
    refreshed_text = report_path.read_text(encoding="utf-8")
    assert "耗时 19.2s" in refreshed_text.split("### 阶段执行履历")[1]

    # 缺失 stage_journals.json 时 publish_report_audit 不抛异常
    (task_dir / "stage_journals.json").unlink()
    publish_report_audit(report_path, mgr.stages)
    assert "尚无阶段日志" in report_path.read_text(encoding="utf-8")

    # 非契约 markdown 文件时 set_stage_journal 安全忽略而不报错
    report_path.write_text("# 非契约普通报告\n正文", encoding="utf-8")
    res_non_contract = mgr.set_stage_journal(6, notes="非契约兼容测试", key_findings={"duration_seconds": 20.0})
    assert res_non_contract["status"] == "success"


def test_extract_design_anchors_range_and_at_pct_generalization():
    """验证 _extract_design_anchors 跨体系泛化提取 at% 范围、Al 箔范围、冲 φ12 mm 与注液量，并与渲染/校验兼容."""
    from lmllm.RAG.rag_pipeline import RAGPipeline
    from auto_battery_research.util.report_contract import render_selected_design
    from auto_battery_research.util.report_recipe_llm import _assemble_user_prompt, _validate_design_anchors

    question = "高电压尖晶石 LNMO (LiNi0.5Mn1.5O4) 掺杂改性，匹配石墨负极与碳酸酯电解液。"
    sec3_excerpt = (
        "### 掺杂与制备参数\n"
        "- 最优配方：LiNi0.48Mg0.02Mn1.5O4（总掺量 1–5 at%，主选 2.0 at%）\n"
        "- 溶胶-凝胶法主烧 750 °C 保温 10 h\n"
        "- 极片涂于 15–16 μm 铝箔，冲 φ12 mm，Celgard 2325 隔膜，注液 100–120 μL\n"
    )
    anchors = RAGPipeline._extract_design_anchors(question, sec3_excerpt)
    assert anchors["primary_formula"] == "LiNi0.48Mg0.02Mn1.5O4"
    assert anchors["doping_upper_limit_mol_pct"] == pytest.approx(5.0)
    assert anchors["precursor_type"] == "sol_gel"
    assert anchors["calcination_temp_c"] == pytest.approx(750.0)
    assert anchors["calcination_hours"] == pytest.approx(10.0)
    assert anchors["al_foil_um"] == "15-16"
    assert anchors["separator"] == "Celgard 2325"
    assert anchors["cathode_diameter_mm"] == pytest.approx(12.0)
    assert anchors["cathode_area_cm2"] == pytest.approx(1.13)
    assert anchors["electrolyte_volume_uL"] == "100–120"

    # 同时验证 float 与 range 字符串的 render_selected_design、_assemble_user_prompt 和 _validate_design_anchors
    for al_val, expected_al_render in ((15.0, "15 μm"), ("15-16", "15–16 μm")):
        cur_anchors = {**anchors, "al_foil_um": al_val}
        scheme = {
            "cathode": "LNMO",
            "anode": "graphite",
            "electrolyte": "carbonate_ec",
            "design_anchors": cur_anchors,
            "geometry": {
                "schema_version": "1.1",
                "cathode": {"L_um": 60.0, "porosity": 0.30, "R_s_um": 4.0, "D_s_m2s": None},
                "anode": {"L_um": 70.0, "porosity": 0.35, "R_s_um": 8.0, "D_s_m2s": None},
                "condition": {"v_min": 3.5, "v_max": 4.9, "c_rate": 0.5},
            },
        }
        doc = {"scheme": scheme, "evidence": [{"passage_id": "p1", "source": "paper"}]}
        ctx = report_context(doc, {"status": "SKIPPED"}, {})
        rendered = render_selected_design(ctx)
        prompt = _assemble_user_prompt(question, sec3_excerpt, ctx, doc, {"status": "SKIPPED"}, {})
        assert f"正极集流体铝箔厚度：{expected_al_render}" in rendered
        assert f"al_foil_um: {expected_al_render}" in prompt
        assert "正极极片尺寸：φ12 mm（面积 1.13 cm²）" in rendered
        assert "扣电设计注液量：100–120 μL" in rendered

        good_plan = (
            "正极 LiNi0.48Mg0.02Mn1.5O4，总掺量 ≤ 5 at%，主烧 750 °C 保温 10 h，"
            f"涂于 {expected_al_render} Al 箔，正极冲 φ12 mm，采用 Celgard 2325 隔膜，注液 100–120 μL。"
        )
        assert _validate_design_anchors(good_plan, cur_anchors) == []

        bad_cap_plan = good_plan.replace("总掺量 ≤ 5 at%", "总掺量 ≤ 3 at%")
        assert any("总掺杂量上限" in e for e in _validate_design_anchors(bad_cap_plan, cur_anchors))

        bad_al_plan = good_plan.replace(f"{expected_al_render} Al 箔", "22 μm Al 箔")
        assert any("Al 箔厚度" in e for e in _validate_design_anchors(bad_al_plan, cur_anchors))

        bad_formula_plan = good_plan.replace("LiNi0.48Mg0.02Mn1.5O4", "LiNi0.47Zr0.03Mn1.5O4")
        assert any("最优名义化学式" in e for e in _validate_design_anchors(bad_formula_plan, cur_anchors))

    # 验证 LFP 体系中 Fe 不被误判为掺杂元素，且简单无机锂盐 Li2CO3 不抢占 primary_formula
    lfp_anchors = RAGPipeline._extract_design_anchors(
        "LiFePO4 橄榄石正极 Mg 掺杂改性",
        "前驱体含 Li2CO3 烧结助剂；未改性 LiFePO4 倍率受限，推荐 LiFe0.98Mg0.02PO4。",
    )
    assert lfp_anchors["primary_formula"] == "LiFe0.98Mg0.02PO4"
    assert _validate_design_anchors("采用 LiFe0.98Mg0.02PO4 正极，辅以 Li2CO3 补锂。", lfp_anchors) == []
    assert any(
        "最优名义化学式" in e
        for e in _validate_design_anchors("改用 LiFe0.97Ti0.03PO4 替代原方案。", lfp_anchors)
    )

    # 验证带圆括号团簇化学式 Li1.164Na0.036(Mn0.600Ni0.184Zn0.016)O2 能够被 primary_formula 与 _FORMULA_RE 正确捕获
    from auto_battery_research.util.report_recipe_llm import _FORMULA_RE

    paren_formula = "Li1.164Na0.036(Mn0.600Ni0.184Zn0.016)O2"
    paren_anchors = RAGPipeline._extract_design_anchors(
        "双位点共掺杂改进无钴富锂 Li1.2Mn0.6Ni0.2O2 正极",
        f"基体未掺杂对照组 Li1.2Mn0.6Ni0.2O2；优选双位点共掺杂配方 {paren_formula}。",
    )
    assert paren_anchors["primary_formula"] == paren_formula
    assert paren_formula in _FORMULA_RE.findall(f"推荐制备 {paren_formula} 电极。")
    assert _validate_design_anchors(f"主选化学式为 {paren_formula}。", paren_anchors) == []
    assert any(
        "最优名义化学式" in e
        for e in _validate_design_anchors("改用 Li1.15Na0.05(Mn0.60Ni0.18Al0.02)O2。", paren_anchors)
    )


def test_valence_normalization_and_formula_stoichiometry():
    """Fix 1: 化学价态上标剥离与计量等价比较应精准识别分裂价态写法并保留单位与 +FEC 拼接。"""
    from auto_battery_research.util.report_recipe_llm import (
        _extract_formulas,
        _normalize_formula,
        _same_formula,
        _strip_valence_superscripts,
        _validate_design_anchors,
    )

    sample = "主推荐 Li₁.₂₀Mn⁴⁺₀.₆₀Ni²⁺₀.₁₆Ni³⁺₀.₀₄O₂，固相扩散系数 10⁻¹⁴ m²/s（10^-14 cm²/s），比容量 250 mAh·g⁻¹，匹配 Li1.2Mn0.6Ni0.2O2+FEC。"
    stripped = _strip_valence_superscripts(sample)
    assert "10⁻¹⁴ m²/s" in stripped
    assert "10^-14 cm²/s" in stripped
    assert "mAh·g⁻¹" in stripped
    assert "O2+FEC" in stripped

    assert _normalize_formula("Li₁.₂₀Mn⁴⁺₀.₆₀Ni²⁺₀.₁₆Ni³⁺₀.₀₄O₂") == "Li1.2Mn0.6Ni0.16Ni0.04O2"
    extracted = _extract_formulas(sample)
    assert "Li1.2Mn0.6Ni0.16Ni0.04O2" in extracted
    assert "Li1.2Mn0.6Ni0.2O2" in extracted
    assert _same_formula("Li₁.₂₀Mn⁴⁺₀.₆₀Ni²⁺₀.₁₆Ni³⁺₀.₀₄O₂", "Li1.2Mn0.6Ni0.2O2")
    assert _same_formula("Li1.2Mn^{4+}0.6Ni^{2+}0.16Ni^{3+}0.04O2", "Li1.2Mn0.6Ni0.2O2")
    assert _same_formula("LiFe²⁺₀.₉₈Mg²⁺₀.₀₂PO₄", "LiFe0.98Mg0.02PO4")

    anchors = {"primary_formula": "Li1.2Mn0.6Ni0.2O2"}
    plan_valence = (
        "| 排名 | 化学式 |\n|---|---|\n"
        "| 1 | Li₁.₂₀Mn⁴⁺₀.₆₀Ni²⁺₀.₁₆Ni³⁺₀.₀₄O₂ |\n"
        "主正极采用 Li1.2Mn^{4+}0.6Ni(2+)0.16Ni{3+}0.04O2。"
    )
    assert _validate_design_anchors(plan_valence, anchors) == []
    assert any(
        "名义化学式" in e
        for e in _validate_design_anchors("改用 Li₁.₁₈Na₀.₀₂Mn⁴⁺₀.₆₀Ni²⁺₀.₁₈Mg²⁺₀.₀₂O₂ 作为主正极。", anchors)
    )

    # 验证 G1/G2 角色倒置：锚点 Zn–Na 被降格到 G2 探索组，而 G1 / primary_formula= / 唯一主推荐组 写成了 Mg–Na
    zn_na_anchors = {"primary_formula": "Li1.17Na0.03Mn0.6Ni0.18Zn0.02O2"}
    inverted_g1_g2_cases = [
        (
            "| 组别 | 体系 | 名义化学式 |\n|---|---|---|\n"
            "| G1 | Mg–Na（主推荐） | Li₁.₁₅Na₀.₀₃Mg₀.₀₂[Mn₀.₅₉Mg₀.₀₁Ni₀.₂₀]O₂ |\n"
            "| G2 | Zn–Na（探索组） | Li₁.₁₇Na₀.₀₃Mn₀.₆Ni₀.₁₈Zn₀.₀₂O₂ |"
        ),
        (
            "最终锁定锚点 primary_formula = Li₁.₁₅Na₀.₀₃Mg₀.₀₂[Mn₀.₅₉Mg₀.₀₁Ni₀.₂₀]O₂；"
            "Zn–Na 配方 Li₁.₁₇Na₀.₀₃Mn₀.₆Ni₀.₁₈Zn₀.₀₂O₂ 仅作备选对照。"
        ),
        (
            "以该 Mg–Na 式（Li₁.₁₅Na₀.₀₃Mg₀.₀₂[Mn₀.₅₉Mg₀.₀₁Ni₀.₂₀]O₂）为唯一主推荐组，"
            "Zn–Na（Li₁.₁₇Na₀.₀₃Mn₀.₆Ni₀.₁₈Zn₀.₀₂O₂）列为 G2 探索组。"
        ),
    ]
    for bad_case in inverted_g1_g2_cases:
        errs = _validate_design_anchors(bad_case, zn_na_anchors)
        assert any("实验方案最优名义化学式与第 3 节选定设计不一致" in e for e in errs), bad_case

    # 正向 G0(基体) + G1(主推荐=Zn–Na) + G2(对照=Mg–Na) 应放行
    valid_g0_g1_g2 = (
        "| 组别 | 体系 | 名义化学式 |\n|---|---|---|\n"
        "| G0 | 未掺杂基体对照 | Li₁.₂Mn₀.₆Ni₀.₂O₂ |\n"
        "| G1 | Zn–Na（主推荐） | Li₁.₁₇Na₀.₀₃Mn₀.₆Ni₀.₁₈Zn₀.₀₂O₂ |\n"
        "| G2 | Mg–Na（探索组） | Li₁.₁₅Na₀.₀₃Mg₀.₀₂[Mn₀.₅₉Mg₀.₀₁Ni₀.₂₀]O₂ |\n"
        "最终锁定锚点 primary_formula = Li₁.₁₇Na₀.₀₃Mn₀.₆Ni₀.₁₈Zn₀.₀₂O₂。"
    )
    assert _validate_design_anchors(valid_g0_g1_g2, zn_na_anchors) == []


def test_all_synthesis_routes_calcination_ranges_and_section2_control_demotion():
    """Fix 2: 全部 4 类合成路线、煅烧区间、第 3 节渲染与第 2 节对照路线降级标记应跨化学体系通用。"""
    from auto_battery_research.util.report_analysis import render_materials
    from auto_battery_research.util.report_contract import render_selected_design
    from auto_battery_research.util.report_recipe_llm import _validate_design_anchors

    anchors_ss = {
        "precursor_type": "solid_state",
        "calcination_temp_c": 925.0,
        "calcination_temp_range_c": "900-950",
        "calcination_hours": 11.0,
        "calcination_hours_range": "10-12",
    }
    doc = {
        "scheme": {
            "cathode": "LRMO",
            "anode": "li_metal",
            "electrolyte": "carbonate_ec",
            "user_locked_components": ["cathode", "anode", "electrolyte"],
            "design_anchors": anchors_ss,
            "geometry": {"cathode": {"L_um": 60.0, "porosity": 0.35}, "anode": {"L_um": 20.0, "porosity": 0.0}},
        },
        "evidence": [{"passage_id": "p1", "source": "paper"}],
        "final_answer": (
            "**合成路线 B：溶胶-凝胶法（对照）**\n"
            "采用柠檬酸溶胶-凝胶法 750 °C 煅烧对比 LRMO [p1]。\n\n"
            "**合成路线 A：固相法**\n"
            "采用球磨固相法 900–950 °C 保温 10–12 h 合成 LRMO [p1]。"
        ),
    }
    rendered = render_selected_design(report_context(doc, {"status": "SKIPPED"}, {}))
    assert "合成路线：固相法" in rendered
    assert "前驱体沉淀路线" not in rendered
    assert "主烧/烧结条件：900–950 °C，保温 10–12 h" in rendered

    materials = render_materials(doc)
    sec21 = materials.split("### 2.1 正极")[1].split("### 2.2")[0]
    assert "合成路线 A：固相法" in sec21.split("**采用条件**")[0]
    assert "对照/备选路线摘录（非当前锁定主路线" in sec21
    assert "与第 3 节锚点对照提示" in sec21

    # 校验 solid_state / sol_gel / hydroxide_coprecipitation 的主次反转拦截与区间煅烧校验
    assert _validate_design_anchors(
        "主路线（路线 A）：固相法，主烧 925 °C 保温 12 h；溶胶-凝胶法（路线 B）仅作对照。",
        anchors_ss,
    ) == []
    assert any("固相法" in e for e in _validate_design_anchors("优先采用溶胶-凝胶法为主路线。", anchors_ss))
    assert any("固相法" in e for e in _validate_design_anchors("路线 B（溶胶-凝胶法）作为主推路线。", anchors_ss))
    assert any("烧结温度" in e for e in _validate_design_anchors("主烧 780 °C 保温 12 h。", anchors_ss))
    assert any("保温时间" in e for e in _validate_design_anchors("主烧 925 °C 保温 24 h。", anchors_ss))
    assert any("溶胶-凝胶法" in e for e in _validate_design_anchors("优先采用固相法制备 LNMO。", {"precursor_type": "sol_gel"}))
    assert any("氢氧化物共沉淀" in e for e in _validate_design_anchors("优先采用碳酸盐共沉淀制备 NCM811。", {"precursor_type": "hydroxide_coprecipitation"}))


def test_additive_lock_consistency_across_section3_prompt_and_validation():
    """Fix 3: 第 3 节已选定添加剂 (如 FEC) 与提示词、第 5 节校验不得出现“FEC 未锁定”自相矛盾。"""
    from auto_battery_research.util.report_contract import render_selected_design
    from auto_battery_research.util.report_recipe_llm import _assemble_user_prompt, validate_plan_text

    doc = {
        "scheme": {
            "cathode": "LRMO",
            "anode": "li_metal",
            "electrolyte": "carbonate_ec",
            "additives": ["FEC"],
            "user_locked_components": ["cathode", "anode", "electrolyte"],
            "geometry": {"cathode": {"L_um": 60.0, "porosity": 0.35}, "anode": {"L_um": 20.0, "porosity": 0.0}},
        },
        "evidence": [{"passage_id": "p1", "source": "paper", "text": "LRMO+FEC"}],
        "reviewer_output": {"issues": []},
    }
    ctx = report_context(doc, {"status": "SKIPPED"}, {})
    rendered = render_selected_design(ctx)
    assert "| 添加剂 | FEC | 候选界面添加剂（不预加入 G0/G1 基础电解液 E0；纳入第 5 节 G7 开展 0 vs 1/2 wt% 单因素及组合筛选） |" in rendered

    prompt = _assemble_user_prompt("LRMO+FEC", "", ctx, doc, {"status": "SKIPPED"}, {})
    assert "candidate_screening" in prompt and "G0–G6" in prompt and "G7" in prompt

    base_plan = (
        "### 5.1 目标\n正极 LRMO 匹配金属锂 li_metal 与 carbonate_ec [p1]。\n"
        "G0–G6 采用无添加剂基础电解液 E0；FEC 作为候选界面添加剂纳入 G7 与第 5.3 节筛选（0 vs 1 vs 2 wt%）。\n"
        "### 5.2 制备\n正极厚度 60 μm，金属锂负极厚度 20 μm。\n### 5.3 组装\nFEC 0/1/2 wt% 单因素梯度筛选。\n### 5.4 测试\n"
        + "实验验证路线。" * 35
    )
    assert validate_plan_text(base_plan, doc, ctx, None, 0.05) == []
    bad_plan = base_plan.replace(
        "G0–G6 采用无添加剂基础电解液 E0；FEC 作为候选界面添加剂纳入 G7 与第 5.3 节筛选（0 vs 1 vs 2 wt%）。",
        "FEC 未锁定：不预设固定值。",
    )
    assert any("FEC" in e and "未锁定" in e for e in validate_plan_text(bad_plan, doc, ctx, None, 0.05))

    # 当 additive_status == "locked" 时，Section 3 与提示词保持已锁定引入口径
    locked_doc = copy.deepcopy(doc)
    locked_doc["scheme"]["design_anchors"] = {"additive_status": "locked"}
    locked_ctx = report_context(locked_doc, {"status": "SKIPPED"}, {})
    assert "| 添加剂 | FEC | 已锁定引入（剂量水平与化成匹配待第 5 节实验筛选） |" in render_selected_design(locked_ctx)
    locked_prompt = _assemble_user_prompt("LRMO+FEC", "", locked_ctx, locked_doc, {"status": "SKIPPED"}, {})
    assert "严禁写成“FEC 未锁定”" in locked_prompt


def test_electrolyte_volume_label_and_physical_ep_pore_volume_check():
    """Fix 4: 扣电设计注液量标签 (<80 μL 说明) 与 E/P ≥ 3.0 孔体积误导拦截及第 2 节物理核对。"""
    from auto_battery_research.util.report_analysis import render_materials
    from auto_battery_research.util.report_contract import compute_cathode_pore_metrics, render_selected_design
    from auto_battery_research.util.report_recipe_llm import _assemble_user_prompt, validate_plan_text

    doc = {
        "scheme": {
            "cathode": "LRMO",
            "anode": "li_metal",
            "electrolyte": "carbonate_ec",
            "design_anchors": {
                "cathode_diameter_mm": 12.0,
                "cathode_area_cm2": 1.13,
                "electrolyte_volume_uL": "50",
            },
            "geometry": {"cathode": {"L_um": 60.0, "porosity": 0.35}, "anode": {"L_um": 20.0, "porosity": 0.0}},
        },
        "evidence": [{"passage_id": "p1", "source": "paper", "text": "electrolyte"}],
        "final_answer": "**电解液**\n- 采用常规碳酸酯注液 50 μL 以贴近孔体积匹配 [p1]。",
        "reviewer_output": {"issues": []},
    }
    ctx = report_context(doc, {"status": "SKIPPED"}, {})
    rendered = render_selected_design(ctx)
    assert "扣电设计注液量：50 μL（扣电过量/泛液测试基准，E/P ≈ 21.1×；相较 100–120 μL 常规值属减量富液，非贫液/低液量）" in rendered

    metrics = compute_cathode_pore_metrics(doc["scheme"])
    assert metrics is not None
    assert metrics["v_pore_uL"] == pytest.approx(2.373)
    assert metrics["ratio_min"] == pytest.approx(50.0 / 2.373)

    prompt = _assemble_user_prompt("LRMO", "", ctx, doc, {"status": "SKIPPED"}, {})
    assert "2.37 μL" in prompt and "E/P ≥ 3.0" in prompt and "严禁使用“低液量测试基准”" in prompt

    materials = render_materials(doc)
    assert "注液量物理核对警示" in materials and "2.37 μL" in materials

    valid_plan = (
        "### 5.1 目标\nLRMO 与 20 μm 金属锂负极、1 mol/L LiPF6 电解液 [p1]。\n"
        "### 5.2 制备\n正极厚度 60 μm，正极冲片 φ12 mm。\n"
        "### 5.3 组装\n扣电设计注液量 50 μL（扣电过量/泛液测试基准，理论孔体积约 2.37 μL，E/P ≈ 21.1×，属减量富液，非贫液/低液量；常规参考 100–120 μL）。\n"
        "### 5.4 测试\n" + "测试矩阵" * 45
    )
    assert validate_plan_text(valid_plan, doc, ctx, None, 0.05) == []

    bad_claim_plan = valid_plan.replace("扣电过量/泛液测试基准，理论孔体积约 2.37 μL，E/P ≈ 21.1×，属减量富液，非贫液/低液量；常规参考 100–120 μL", "贴近孔体积匹配")
    assert any("孔体积" in e for e in validate_plan_text(bad_claim_plan, doc, ctx, None, 0.05))

    bad_lean_label = valid_plan.replace("扣电过量/泛液测试基准", "低液量测试基准")
    assert any("低液量测试基准" in e for e in validate_plan_text(bad_lean_label, doc, ctx, None, 0.05))

    # 当真正在低 E/P (< 3.0) 条件 (如 5 μL / 2.373 μL = 2.11×) 时，应展示低液量基准且不拦截贴近孔体积表述
    lean_doc = copy.deepcopy(doc)
    lean_doc["scheme"]["design_anchors"]["electrolyte_volume_uL"] = "5"
    lean_rendered = render_selected_design(report_context(lean_doc, {"status": "SKIPPED"}, {}))
    assert "扣电设计注液量：5 μL（低液量测试基准，E/P ≈ 2.1×；常规参考 100–120 μL）" in lean_rendered
    assert validate_plan_text(bad_claim_plan, lean_doc, report_context(lean_doc, {"status": "SKIPPED"}, {}), None, 0.05) == []


def test_single_1c_basis_current_math_and_phantom_xref_evidence_tier():
    """Fix 5 & Fix 7: 唯一 1 C 基准与电流核算、禁止幻象交叉引用与 α=0.5 降级数值升格校验。"""
    from auto_battery_research.util.report_analysis import render_materials
    from auto_battery_research.util.report_recipe_llm import validate_plan_text

    doc = {
        "scheme": {
            "cathode": "LRMO",
            "anode": "li_metal",
            "electrolyte": "carbonate_ec",
            "geometry": {"cathode": {"L_um": 60.0, "porosity": 0.35}, "anode": {"L_um": 20.0, "porosity": 0.0}},
        },
        "evidence": [{"passage_id": "p1", "source": "paper", "text": "LRMO rate"}],
        "final_answer": "**正极**\n- 参见完整方案第三节排行表，α=0.5 外推扩散提升 1.4 倍 [p1]。",
        "reviewer_output": {"issues": []},
    }
    ctx = report_context(doc, {"status": "SKIPPED"}, {})
    materials = render_materials(doc)
    assert "证据等级核对提示" in materials
    assert "交叉引用核对提示" in materials

    good_plan = (
        "### 5.1 目标\nLRMO 与金属锂 li_metal 负极，基准组与共掺杂组统一锁定名义基准 1 C = 250 mA·g⁻¹ [p1]。\n"
        "### 5.2 制备\n正极厚度 60 μm，负极厚度 20 μm，单片活性物质质量 10.0 mg。\n"
        "### 5.3 组装\n扣电组装。\n"
        "### 5.4 测试\n1 C 对应绝对电流 2.50 mA，0.2 C 电流 0.50 mA；1 C 实测容量参考 195 mAh·g⁻¹。"
        "详情见 [设计方案原文](design_scheme.md)；α=0.5 近似外推仅作定性趋势参考，不作为实测预期。\n"
        + "倍率测试与模型比对。" * 30
    )
    assert validate_plan_text(good_plan, doc, ctx, None, 0.05) == []

    # 1. 混用 1 C = 250 mA/g 与 1 C = 200 mA/g
    bad_dual_1c = good_plan + "\n### 5.6 补充\n改性样设定 1 C = 200 mA·g⁻¹。\n"
    assert any("1 C 基准" in e for e in validate_plan_text(bad_dual_1c, doc, ctx, None, 0.05))

    # 2. 混用名义 1 C 与动态实测容量定义 1 C
    bad_dyn_1c = good_plan + "\n### 5.6 补充\n后续测试以本批母体实测容量定义 1 C。\n"
    assert any("实测容量动态定义 1 C" in e for e in validate_plan_text(bad_dyn_1c, doc, ctx, None, 0.05))

    # 3. 绝对电流 m_active * 1C 计算错误 (10 mg * 250 mA/g 应为 2.5 mA 而非 5.0 mA)
    bad_i_math = good_plan.replace("1 C 对应绝对电流 2.50 mA", "1 C 对应绝对电流 5.00 mA")
    assert any("绝对电流" in e for e in validate_plan_text(bad_i_math, doc, ctx, None, 0.05))

    # 4. 幻象内部章节表引用
    bad_xref = good_plan.replace("[设计方案原文](design_scheme.md)", "完整方案第三节排行表")
    assert any("内部章节引用" in e for e in validate_plan_text(bad_xref, doc, ctx, None, 0.05))

    # 5. 正文把 α=0.5 外推估算升格为无保留定量预期
    bad_tier = good_plan.replace(
        "α=0.5 近似外推仅作定性趋势参考，不作为实测预期",
        "依据 α=0.5 估算可实现 95% 循环保持率",
    )
    assert any("α=0.5" in e for e in validate_plan_text(bad_tier, doc, ctx, None, 0.05))


def test_compliant_llm_negation_and_disclaimer_clauses_not_false_positive():
    """真实 LLM 合规否定/换算备注表述（隔膜厚度、不宣称孔体积、换算备注 1 C、不得表述为 FEC 未锁定）不得误报."""
    from auto_battery_research.util.report_recipe_llm import validate_plan_text

    doc = {
        "scheme": {
            "cathode": "LRMO",
            "anode": "li_metal",
            "electrolyte": "carbonate_ec",
            "additives": ["FEC"],
            "user_locked_components": ["cathode", "anode", "electrolyte"],
            "design_anchors": {
                "primary_formula": "Li1.17Na0.03Mn0.6Ni0.18Zn0.02O2",
                "precursor_type": "solid_state",
                "separator": "Celgard 2500",
                "cathode_diameter_mm": 12.0,
                "cathode_area_cm2": 1.13,
                "electrolyte_volume_uL": "50",
            },
            "geometry": {"cathode": {"L_um": 60.0, "porosity": 0.35}, "anode": {"L_um": 20.0, "porosity": 0.0}},
        },
        "evidence": [
            {"passage_id": "p1", "source": "paper", "text": "LRMO"},
            {"passage_id": "0dc46c604ef1", "source": "paper2", "text": "200 mA/g"},
        ],
        "reviewer_output": {"issues": []},
    }
    ctx = report_context(doc, {"status": "SKIPPED"}, {})
    compliant_llm_plan = (
        "### 5.1 目标\n"
        "正极采用 LRMO（Li₁.₁₇Na₀.₀₃Mn₀.₆Ni₀.₁₈Zn₀.₀₂O₂），匹配 20 μm 金属锂负极与 1 mol/L LiPF6/EC+DMC [p1]。\n"
        "FEC 种类已选定引入，不得表述为“FEC 未锁定”，剂量与化成匹配待实验优化。\n"
        "### 5.2 制备\n"
        "主路线（路线 A）：固相法；**对照路线（仅用于 D50/均匀性对比，不改变主路线地位）**：溶胶-凝胶法对比。\n"
        "正极厚度 60 μm，冲片 φ12 mm。\n"
        "### 5.3 组装\n"
        "采用隔膜 **Celgard 2500**（厚度 25 μm），扣电设计注液量 50 μL（扣电过量/泛液测试基准，E/P ≈ 21.1×，不宣称贴近/接近/匹配孔体积，亦不构成“贴近/匹配孔体积”；"
        "原文“以 50 μL 贴近孔体积匹配”与 12.8×/21.1× 自相矛盾，作废；严禁误称“低液量测试基准”）。\n"
        "### 5.4 测试\n"
        "全节统一锁定名义 1 C = 250 mA·g⁻¹；本方案不改为“以本批母体实测容量定义 1 C”。\n"
        "另有 1 C = 200 mA·g⁻¹ 口径 [0dc46c604ef1]，仅作换算备注，不用于本方案。\n"
        + "倍率测试与失效分析。" * 25
    )
    assert validate_plan_text(compliant_llm_plan, doc, ctx, None, 0.05) == []

    # 无显式 "LRMO" 缩写，仅以分裂价态主推荐化学式 / 补零母体式表述时也应识别并放行
    formula_only_plan = compliant_llm_plan.replace(
        "LRMO（Li₁.₁₇Na₀.₀₃Mn₀.₆Ni₀.₁₈Zn₀.₀₂O₂）",
        "Li₁.₁₇Na₀.₀₃Mn₀.₆Ni²⁺₀.₁₄Ni³⁺₀.₀₄Zn₀.₀₂O₂（基体对比 Li₁.₂₀Mn₀.₆₀Ni₀.₂₀O₂）",
    )
    assert validate_plan_text(formula_only_plan, doc, ctx, None, 0.05) == []

    # 反例：换用完全无关正极体系仍必须拦截
    wrong_cathode_plan = compliant_llm_plan.replace(
        "LRMO（Li₁.₁₇Na₀.₀₃Mn₀.₆Ni₀.₁₈Zn₀.₀₂O₂）",
        "LiFePO4 橄榄石正极",
    )
    assert any("正极材料" in e for e in validate_plan_text(wrong_cathode_plan, doc, ctx, None, 0.05))


def test_p0_p1_p2_contract_ranking_table_and_unanchored_hardware_rendering():
    """验证 P0-2, P0-5, P0-6, P0-7, P1-9, P2 合同渲染、表格抽取、规范拼写与保守性注释。"""
    from auto_battery_research.util.report_contract import render_recipe, render_selected_design

    doc = {
        "target": "双位点共掺杂改进无钴富锂正极以抑制氧释放并提升1 C保持率",
        "review_status": "PASSED",
        "scheme": {
            "cathode": "lrmo",
            "anode": "li_metal",
            "electrolyte": "carbonate_ec",
            "additives": ["FEC", "VC", "LIDFOB"],
            "user_locked_components": ["lrmo", "li_metal", "carbonate_ec"],
            "design_anchors": {
                "primary_formula": "Li1.2Mn0.56Ni0.20Zn0.025Mg0.015Na0.01O2",
                "doping_upper_limit_mol_pct": 3.0,
                "a_site_upper_limit_mol_pct": 2.0,
                "b_site_upper_limit_mol_pct": 3.0,
                "precursor_type": "solid_state",
                "calcination_temp_range_c": "900-950",
                "calcination_hours_range": "10-12",
                "cathode_diameter_mm": 12.0,
                "cathode_area_cm2": 1.13,
                "electrolyte_volume_uL": "50-80",
                "slurry_ratio": "8:1:1",
            },
            "geometry": {"cathode": {"L_um": 30.0, "porosity": 0.32}, "anode": {"L_um": 50.0, "porosity": 0.0}},
        },
        "final_answer": (
            "## 二、推荐方案\n"
            "| 排序 | A/B 元素组合 | 名义化学式 | 综合评分 | 预计 1 C 保持率 |\n"
            "|---|---|---|---|---|\n"
            "| 1 | Na + Zn/Mg | Li1.2Mn0.56Ni0.20Zn0.025Mg0.015Na0.01O2 | 92 | 82–90% |\n"
            "| 2 | Na + Cu/Mg | Li1.2Mn0.56Ni0.20Cu0.015Mg0.015Na0.01O2 | 86 | 80–86% |\n"
            "附注：LIDFOB 可作高压成膜筛选添加剂。\n"
        ),
        "reviewer_output": {"issues": ["缺高载量验证"]},
    }
    ctx = report_context(doc, {"status": "SKIPPED"}, {})
    rendered = render_selected_design(ctx)
    assert "LIDFOB" not in rendered and "LiDFOB" in rendered
    assert "主推候选名义化学式（Rank #1，待实验验证）" in rendered
    assert "候选设计配方与工艺规格锚点（待实验闭环验证）" in rendered
    assert "唯一最优" not in rendered
    assert "候选界面添加剂（不预加入 G0/G1 基础电解液 E0；纳入第 5 节 G7 开展 0 vs 1/2 wt% 单因素及组合筛选）" in rendered
    assert "隔膜与集流体规格：未设设计锚点（由第 5 节实验室入库台账固定同批次规格，非设计锚点）" in rendered
    assert "A 位掺杂量上限（总阳离子基准）：≤ 2 mol%" in rendered
    assert "B 位掺杂量上限（总阳离子基准）：≤ 3 mol%" in rendered
    assert "扣电过量/泛液测试基准" in rendered and "减量富液，非贫液/低液量" in rendered
    assert "**候选掺杂组合正式排行与综合对比（源自方案设计，待第 5 节实验闭环验证）**" in rendered
    assert "| 1 | Na + Zn/Mg | Li1.2Mn0.56Ni0.20Zn0.025Mg0.015Na0.01O2 |" in rendered
    assert "TM redox" in rendered and "保守" in rendered

    recipe_text = render_recipe(ctx)
    assert "注液量、静置时间" not in recipe_text
    assert "扣电设计注液量" in recipe_text


def test_p1_11_p2_render_materials_splitting_and_contract_fallbacks():
    """验证 P1-11, P2, P0-6: 单换行 **(7) 标题** 拆段、去孤立序号、主推候选优先与 2.2/2.3 契约摘要回退。"""
    from auto_battery_research.util.report_analysis import render_materials

    doc = {
        "target": "无钴富锂改性",
        "scheme": {
            "cathode": "lrmo",
            "anode": "graphite",
            "electrolyte": "carbonate_ec",
            "additives": ["FEC", "VC", "LIDFOB"],
            "user_locked_components": ["lrmo", "graphite", "carbonate_ec"],
            "design_anchors": {
                "primary_formula": "Li1.2Mn0.56Ni0.20Zn0.025Mg0.015Na0.01O2",
                "additive_status": "candidate_screening",
            },
            "geometry": {"cathode": {"L_um": 40.0, "porosity": 0.30}, "anode": {"L_um": 55.0, "porosity": 0.25}},
        },
        "final_answer": (
            "## 三、选型依据\n"
            "**(1) 晶格稳定机制**：Na/Zn/Mg 共掺杂 Li₁.₂Mn₀.₅₆Ni₀.₂₀Zn₀.₀₂₅Mg₀.₀₁₅Na₀.₀₁O₂ 可抑氧释放 [p1]。\n"
            "**(7) 合成可行性**：固相法分段煅烧可获得纯相层状结构 [p2]。\n"
            "**(8) 测试可行性**：扣式半电池按统一倍率评估保持率 [p3]。"
        ),
        "evidence": [
            {"passage_id": "p1", "source": "paper_mech", "text": "co-doping"},
            {"passage_id": "p2", "source": "paper_syn", "text": "solid state"},
            {"passage_id": "p3", "source": "paper_test", "text": "coin cell"},
        ],
    }
    materials = render_materials(doc)
    assert "**(7)" not in materials and "**(8)" not in materials
    assert "**晶格稳定机制**" in materials and "**合成可行性**" in materials
    assert materials.index("**晶格稳定机制**") < materials.index("**合成可行性**")
    assert "LIDFOB" not in materials and "FEC, VC, LiDFOB" in materials
    assert "G0–G6" in materials and "G7" in materials
    assert "不补造未引用文献依据" in materials
    assert "graphite" in materials


def test_p0_p1_p2_plan_prompt_math_and_validators_across_chemistries():
    """验证 P0-1..P0-8, P1-10, P2 在 LRMO 与非 LRMO 体系下的预计算提示与确定性拦截器。"""
    from auto_battery_research.util.report_recipe_llm import _assemble_user_prompt, validate_plan_text

    issues = [f"审稿保留意见{i}" for i in range(1, 5)]
    doc = {
        "target": "NCM811/Si-C 或无钴双掺改性",
        "review_status": "PASSED",
        "scheme": {
            "cathode": "lrmo",
            "anode": "li_metal",
            "electrolyte": "carbonate_ec",
            "additives": ["FEC", "VC", "LiDFOB"],
            "user_locked_components": ["lrmo", "li_metal", "carbonate_ec"],
            "design_anchors": {
                "primary_formula": "Li1.2Mn0.56Ni0.20Zn0.025Mg0.015Na0.01O2",
                "doping_upper_limit_mol_pct": 3.0,
                "a_site_upper_limit_mol_pct": 1.5,
                "b_site_upper_limit_mol_pct": 2.5,
                "additive_status": "candidate_screening",
                "precursor_type": "solid_state",
                "calcination_temp_range_c": "900-950",
                "calcination_hours_range": "10-12",
                "cathode_diameter_mm": 12.0,
                "cathode_area_cm2": 1.13,
                "electrolyte_volume_uL": "50-80",
                "slurry_ratio": "8:1:1",
            },
            "geometry": {
                "cathode": {"L_um": 30.0, "porosity": 0.32, "loading_mg_cm2": 4.5},
                "anode": {"L_um": 50.0, "porosity": 0.0},
            },
        },
        "final_answer": "候选 G1 Li1.2Mn0.56Ni0.20Zn0.025Mg0.015Na0.01O2 与对照 G5 Cu 0.5 mol% 共掺体系。",
        "evidence": [{"passage_id": "p1", "source": "paper", "text": "doping"}],
        "reviewer_output": {"issues": issues},
    }
    ctx = report_context(doc, {"status": "SKIPPED"}, {})
    prompt = _assemble_user_prompt(doc["target"], "", ctx, doc, {"status": "SKIPPED"}, {})
    assert "5.625 mg/cm²" in prompt
    assert "1.875 g/cm³" in prompt
    assert "2.76 g/cm³" in prompt
    assert "半电池事后容量比估算（非全电池 N/P 设计输入）" in prompt
    assert "Cu" in prompt and "Zn" in prompt and "Mg" in prompt and "Na" in prompt
    assert "separator（隔膜型号）" in prompt and "al_foil_um（正极铝箔厚度）" in prompt

    # 当 geometry.cathode.loading_mg_cm2 为空而仅 scheme_md 给出“面载量 **4.5 mg/cm²**”时也应精准提取并预计算
    doc_md_only = copy.deepcopy(doc)
    doc_md_only["scheme"]["geometry"]["cathode"].pop("loading_mg_cm2", None)
    prompt_md = _assemble_user_prompt(
        doc_md_only["target"],
        "**电极与电解液**：8:1:1，面载量 **4.5 mg/cm²**（待实测）",
        report_context(doc_md_only, {"status": "SKIPPED"}, {}),
        doc_md_only,
        {"status": "SKIPPED"},
        {},
    )
    assert "5.625 mg/cm²" in prompt_md and "1.875 g/cm³" in prompt_md and "半电池事后容量比估算" in prompt_md

    good_plan = (
        "### 5.1 实验目标与分组\n"
        "主推候选名义化学式为 Li₁.₂Mn₀.₅₆Ni₀.₂₀Zn₀.₀₂₅Mg₀.₀₁₅Na₀.₀₁O₂（Rank #1，待实验验证；不得使用“唯一最优”）[p1]。\n"
        "掺量统一以总阳离子为基准：总掺杂量上限 ≤ 3.0 mol%（A 位 ≤ 1.5 mol%、B 位 ≤ 2.5 mol%）。\n"
        "| 组别 | 配方 | 电解液 |\n|---|---|---|\n"
        "| G0 | 母体 LRMO (Li1.2Mn0.6Ni0.2O2) | E0 (1 mol/L LiPF6/EC+DMC，不含 FEC/VC/LiDFOB) |\n"
        "| G1 | Li1.2Mn0.56Ni0.20Zn0.025Mg0.015Na0.01O2 (总掺量 2.49 mol%，A位 0.50 mol%，B位 1.99 mol%) | E0 (不含添加剂) |\n"
        "| G5 | Cu 对照组 Li1.2Mn0.56Ni0.20Zn0.020Cu0.010Na0.010O2 | E0 |\n"
        "| G6 | 梯度边界组 1.0 mol% + 1.8 mol% = 2.8 mol% | E0 |\n"
        "| G7 | G1 + 候选界面添加剂 FEC / VC / LiDFOB 筛选 | E0 + FEC/VC/LiDFOB |\n"
        "5 项联立验收判据：\n"
        "① 1 C 循环保持率目标 ≥ 80.0%，最低放行线 ≥ 78.0%（78.0%–79.9% 有条件进复配）；\n"
        "② 首周比容量 ≥ 95% G0 且 ICE ≥ G0；\n"
        "③ 首周不可逆氧释放目标 < 5.0 mAh·g⁻¹；\n"
        "④ 平均工作电压衰减 ≤ 2.0 mV/cycle；\n"
        "⑤ XRD Rietveld 晶格畸变与杂相上限达标。\n"
        "### 5.2 材料制备与极片加工\n"
        "合成路线采用固相法，925 °C 保温 11 h。正极活性面载量 4.5 mg/cm²，浆料 8:1:1，总面载量 5.625 mg/cm²，"
        "极片厚度 30 μm，压实密度 1.875 g/cm³，金属锂负极厚度 50 μm（半电池事后容量比估算约 9.1 倍，非全电池 N/P 设计输入）。\n"
        "### 5.3 电芯组装与化成\n"
        "隔膜与正极 Al 箔未设设计锚点（由实验室入库台账固定同批次规格）；正极 φ12 mm。\n"
        "扣电设计注液量 50–80 μL，属扣电过量/泛液测试基准（减量富液，非贫液/低液量）。\n"
        "DOE 筛选矩阵全覆盖 FEC、VC、LiDFOB（0/1/2 wt% 单因素及优胜二元组合 × 化成协议 A/B）。\n"
        "### 5.4 性能测试与模型对照\n"
        "1 C = 250 mA·g⁻¹。ICP-OES 与负极 XPS 全覆盖 Mn、Ni、Zn、Mg、Na 与对照元素 Cu 溶解及锂片表面沉积检测。\n"
        "### 5.5 结果判定与下一轮迭代\n"
        "5 项联立判据与 5.1 完全一致：\n"
        "① 1 C 循环保持率目标 ≥ 80.0%，最低放行线 ≥ 78.0%（78.0%–79.9% 有条件进复配）；\n"
        "② 首周比容量 ≥ 95% G0 且 ICE ≥ G0；\n"
        "③ 首周不可逆氧释放目标 < 5.0 mAh·g⁻¹；\n"
        "④ 平均工作电压衰减 ≤ 2.0 mV/cycle；\n"
        "⑤ XRD Rietveld 晶格畸变与杂相上限达标。\n"
        "#### 审稿保留意见逐条闭环状态表\n"
        "| 序号 | 审稿意见要点 | 闭环状态 | 对应节次 |\n|---|---|---|---|\n"
        "| 1 | 保留意见1 | 待实验闭环 | 5.1 |\n"
        "| 2 | 保留意见2 | 待实验闭环 | 5.2 |\n"
        "| 3 | 保留意见3 | 待实验闭环 | 5.3 |\n"
        "| 4 | 保留意见4 | 待实验闭环 | 5.4 |\n"
    )
    assert validate_plan_text(good_plan, doc, ctx, None, 0.05) == []

    # 逐项反例拦截
    assert any("唯一最优" in e for e in validate_plan_text(good_plan.replace("Rank #1，待实验验证", "唯一最优"), doc, ctx, None, 0.05))
    assert any("LiDFOB" in e for e in validate_plan_text(good_plan.replace("LiDFOB", "LIDFOB"), doc, ctx, None, 0.05))
    assert any("G0/G1" in e for e in validate_plan_text(good_plan.replace("不含添加剂", "含 1 wt% FEC"), doc, ctx, None, 0.05))
    assert any("遗漏候选添加剂" in e for e in validate_plan_text(good_plan.replace("LiDFOB", "DTD").replace("FEC/VC/DTD", "FEC/VC"), doc, ctx, None, 0.05))
    assert any("隔膜" in e for e in validate_plan_text(good_plan.replace("未设设计锚点（由实验室入库台账固定同批次规格）", "与锚点一致"), doc, ctx, None, 0.05))
    assert any("总面载量" in e for e in validate_plan_text(good_plan.replace("总面载量 5.625 mg/cm²", "总面载量 5.06 mg/cm²"), doc, ctx, None, 0.05))
    assert any("压实密度" in e for e in validate_plan_text(good_plan.replace("压实密度 1.875 g/cm³", "压实密度 1.69 g/cm³"), doc, ctx, None, 0.05))
    assert any("超出总掺杂上限" in e for e in validate_plan_text(good_plan.replace("1.0 mol% + 1.8 mol% = 2.8 mol%", "2.5 mol% + 1.5 mol% = 4.0 mol%"), doc, ctx, None, 0.05))
    assert any("自相矛盾" in e for e in validate_plan_text(good_plan.replace("总掺杂量上限 ≤ 3.0 mol%（A 位 ≤ 1.5 mol%、B 位 ≤ 2.5 mol%）", "总掺杂量控制在 3.0 mol% 上限内（x_A ≤ 2.0 mol%）"), doc, ctx, None, 0.05))
    assert any("遗漏了第 5.1 节" in e for e in validate_plan_text(good_plan.replace("与对照元素 Cu 溶解", "溶解"), doc, ctx, None, 0.05))
    assert any("闭环状态表" in e for e in validate_plan_text(good_plan.replace("| 4 | 保留意见4 | 待实验闭环 | 5.4 |\n", ""), doc, ctx, None, 0.05))
    assert any("判据条数" in e for e in validate_plan_text(good_plan.replace("④ 平均工作电压衰减 ≤ 2.0 mV/cycle；\n⑤ XRD Rietveld 晶格畸变与杂相上限达标。", "", 1), doc, ctx, None, 0.05))

    # 通用非 LRMO 体系（NCM811 + 石墨 + 碳酸盐共沉淀）应完全通用放行
    ncm_doc = {
        "target": "高镍 NCM811/石墨体系",
        "review_status": "APPROVED",
        "scheme": {
            "cathode": "NCM811",
            "anode": "graphite",
            "electrolyte": "carbonate_ec",
            "additives": ["FEC"],
            "design_anchors": {
                "primary_formula": "LiNi0.8Mn0.1Co0.1Al0.01O2",
                "doping_upper_limit_mol_pct": 2.0,
                "precursor_type": "carbonate_coprecipitation",
                "additive_status": "locked",
            },
            "geometry": {"cathode": {"L_um": 70.0, "porosity": 0.25}, "anode": {"L_um": 85.0, "porosity": 0.28}},
        },
        "evidence": [{"passage_id": "n1", "source": "ncm_paper", "text": "NCM811"}],
        "reviewer_output": {"issues": []},
    }
    ncm_ctx = report_context(ncm_doc, {"status": "SKIPPED"}, {})
    ncm_plan = (
        "### 5.1 目标\nNCM811（LiNi0.8Mn0.1Co0.1Al0.01O2）匹配 graphite 与 carbonate_ec [n1]。总掺杂量上限 ≤ 2.0 mol%。\n"
        "FEC 种类已选定引入，剂量水平（0 vs 1 wt%）待优化。\n"
        "### 5.2 制备\n采用碳酸盐共沉淀路线；正极厚度 70 μm，负极厚度 85 μm。\n"
        "### 5.3 组装\n常规扣电与全电池验证，隔膜未设设计锚点。\n"
        "### 5.4 测试\n1 C = 200 mA·g⁻¹，ICP-OES 覆盖 Ni、Co、Mn、Al 溶解监测。" + "性能与循环测试。" * 25
    )
    assert validate_plan_text(ncm_plan, ncm_doc, ncm_ctx, None, 0.05) == []


def test_live_llm_gate_decimals_c_codes_anchor_negation_and_g1_control_row():
    """回归覆盖真实 LLM 输出中的 4 类防误报场景：小数/C1-C5 判据计数、隔膜铝箔否定句、不使用“唯一最优”、G1 单掺对照行。"""
    from auto_battery_research.util.report_recipe_llm import validate_plan_text

    doc = {
        "target": "无钴富锂改性",
        "review_status": "PASSED",
        "scheme": {
            "cathode": "lrmo",
            "anode": "li_metal",
            "electrolyte": "carbonate_ec",
            "additives": ["FEC", "VC", "LiDFOB"],
            "user_locked_components": ["lrmo", "li_metal", "carbonate_ec"],
            "design_anchors": {
                "primary_formula": "Li1.17Na0.03Mn0.60Ni0.17Zn0.03O2",
                "doping_upper_limit_mol_pct": 5.0,
                "precursor_type": "carbonate_coprecipitation",
                "additive_status": "candidate_screening",
            },
            "geometry": {"cathode": {"L_um": 30.0, "porosity": 0.32}, "anode": {"L_um": 50.0, "porosity": 0.0}},
        },
        "evidence": [{"passage_id": "p1", "source": "paper", "text": "LRMO"}],
        "reviewer_output": {"issues": ["保留意见1", "保留意见2"]},
    }
    ctx = report_context(doc, {"status": "SKIPPED"}, {})
    live_style_plan = (
        "### 5.1 实验目标与分组\n"
        "主推候选（Rank #1，待实验验证）：Li1.17Na0.03Mn0.60Ni0.17Zn0.03O2；全文不使用“唯一最优”表述 [p1]。\n"
        "总掺杂量上限 ≤ 5.0 mol%（统一总阳离子基准）。\n"
        "| 组别 | A 位(mol%) | B 位(mol%) | 化学式 | 定位与电解液 |\n|---|---|---|---|---|\n"
        "| G0 | 0 | 0 | Li1.20Mn0.60Ni0.20O2 | 母体对照；E0（不含 FEC/VC/LiDFOB） |\n"
        "| G1 | 1.5 | 0 | Li1.17Na0.03Mn0.60Ni0.20O2 | A 位单掺对照；E0（无添加剂） |\n"
        "| G4 | 1.5 | 1.5 | Li1.17Na0.03Mn0.60Ni0.17Zn0.03O2 | Rank #1 主推候选；E0 |\n"
        "| G7 | 1.5 | 1.5 | Li1.17Na0.03Mn0.60Ni0.17Zn0.03O2 | E0 + FEC/VC/LiDFOB 筛选 |\n"
        "5 项联立判据：\n"
        "| 编号 | 判据 | 指标要求 |\n|---|---|---|\n"
        "| C1 | 1 C 保持率 | 目标 ≥ 80.0%，最低放行 ≥ 78.0%（78.0%–79.9% 有条件进复配） |\n"
        "| C2 | 首周比容量与 ICE | 2.0–4.8 V 首周比容量 ≥ 95% G0（差值 ≤ 5.0 个百分点） |\n"
        "| C3 | 不可逆氧释放 | < 5.0 mAh·g⁻¹ |\n"
        "| C4 | 平均电压衰减 | ≤ 2.0 mV/cycle |\n"
        "| C5 | XRD 晶格畸变与杂相 | 晶格畸变 < 1.5%，无明显杂相 |\n"
        "### 5.2 材料制备与极片加工\n"
        "碳酸盐共沉淀路线；正极厚度 30 μm，金属锂负极厚度 50 μm。\n"
        "### 5.3 电芯组装与化成\n"
        "| 核对项 | 处理方式 |\n|---|---|\n"
        "| 隔膜/铝箔未锚定 | 实验室台账固定同批次，不写“与锚点一致” |\n"
        "隔膜与铝箔均不得表述为“与锚点一致”；DOE 筛选 FEC、VC、LiDFOB（0/1/2 wt%）。\n"
        "### 5.4 性能测试与模型对照\n"
        "1 C = 250 mA·g⁻¹。ICP-OES 全覆盖 Mn、Ni、Na、Zn 元素溶解与迁移。\n"
        "### 5.5 结果判定与下一轮迭代\n"
        "5 项联立判据与 5.1 完全一致：\n"
        "① 1 C 保持率目标 ≥ 80.0%，最低放行 ≥ 78.0%（78.0%–79.9% 有条件进复配）；\n"
        "② 2.0–4.8 V 首周比容量 ≥ 95% G0（差值 ≤ 5.0 个百分点）且 ICE ≥ G0；\n"
        "③ 不可逆氧释放 < 5.0 mAh·g⁻¹；\n"
        "④ 平均电压衰减 ≤ 2.0 mV/cycle；\n"
        "⑤ XRD 晶格畸变 < 1.5% 且无杂相。\n"
        "#### 审稿保留意见逐条闭环状态表\n"
        "| 序号 | 审稿意见要点 | 闭环状态 | 对应节次 |\n|---|---|---|---|\n"
        "| 1 | 保留意见1 | 待实验闭环 | 5.1 |\n"
        "| 2 | 保留意见2 | 待实验闭环 | 5.2 |\n"
        "| 3 | 额外说明：1.2 保持率与 2.5 XRD 晶格参考 | 仅供补充记录 | 5.4 |\n"
    )
    assert validate_plan_text(live_style_plan, doc, ctx, None, 0.05) == []

    # 升序梯度 DOE 表（G0 基准 -> G1 低掺端定量证据 -> G2 低–中掺 -> G3 Rank #1 主推候选）应正常放行
    ascending_doe_plan = live_style_plan.replace(
        "| G0 | 0 | 0 | Li1.20Mn0.60Ni0.20O2 | 母体对照；E0（不含 FEC/VC/LiDFOB） |\n"
        "| G1 | 1.5 | 0 | Li1.17Na0.03Mn0.60Ni0.20O2 | A 位单掺对照；E0（无添加剂） |\n"
        "| G4 | 1.5 | 1.5 | Li1.17Na0.03Mn0.60Ni0.17Zn0.03O2 | Rank #1 主推候选；E0 |",
        "| G0 | 0 | 0 | Li1.20Mn0.60Ni0.20O2 | 同批基准；E0（不含 FEC/VC/LiDFOB） |\n"
        "| G1 | 0.5 | 0.75 | Li1.19Na0.01Mn0.60Ni0.19Zn0.01O2 | 低掺端定量证据；E0（无添加剂） |\n"
        "| G2 | 1.0 | 1.5 | Li1.18Na0.02Mn0.60Ni0.18Zn0.02O2 | 低–中掺；E0 |\n"
        "| **G3** | **1.5** | **2.25** | **Li1.17Na0.03Mn0.60Ni0.17Zn0.03O2（Rank #1）** | 主推候选验证；E0 |",
    )
    assert validate_plan_text(ascending_doe_plan, doc, ctx, None, 0.05) == []

    # 反例：若真实将 G1 设为非锚点的“Rank #1 主推候选”而把锚点降为 G4 对照，仍应拦截
    inverted_plan = live_style_plan.replace("A 位单掺对照；E0（无添加剂）", "Rank #1 主推候选；E0（无添加剂）").replace(
        "Rank #1 主推候选；E0", "G4 探索备选组；E0"
    )
    assert any("名义化学式" in e for e in validate_plan_text(inverted_plan, doc, ctx, None, 0.05))

    # P0-4 原文偏差说明防误报：引用 design_scheme.md 旧压实/载量并说明“与本核算不一致”时应放行，真实错写仍应拦截
    doc_with_loading = {
        **doc,
        "scheme": {
            **doc["scheme"],
            "design_anchors": {
                **doc["scheme"]["design_anchors"],
                "slurry_ratio": "8:1:1",
                "loading_mg_cm2": 4.5,
            },
        },
    }
    ctx_loading = report_context(doc_with_loading, {"status": "SKIPPED"}, {})
    disclaimed_plan = live_style_plan.replace(
        "碳酸盐共沉淀路线；正极厚度 30 μm，金属锂负极厚度 50 μm。",
        "碳酸盐共沉淀路线；正极厚度 30 μm（活性面载量 4.5 mg/cm²，总面载量 5.625 mg/cm²，几何压实密度 1.875 g/cm³），"
        "金属锂负极厚度 50 μm。\n"
        "说明：[设计方案原文](design_scheme.md) 中“总面载量 5.06 mg/cm²、几何压实 1.5 g/cm³、骨架 2.2 g/cm³”"
        "与本核算不一致，**以 5.625 mg/cm² 与 1.875 / 2.76 g/cm³ 为验证对象**。",
    )
    assert validate_plan_text(disclaimed_plan, doc_with_loading, ctx_loading, None, 0.05) == []

    # 反例：若实际采用错误压实密度且无否定/纠偏声明（即使出现无关“不一致”或引用 design_scheme.md），仍应拦截
    false_compaction_plan = live_style_plan.replace(
        "碳酸盐共沉淀路线；正极厚度 30 μm，金属锂负极厚度 50 μm。",
        "依据 design_scheme.md，极片正极活性载量 4.5 mg/cm²，正极厚度 30 μm，压实密度 1.5 g/cm³，仅电解液写法与前文不一致。",
    )
    assert any("压实密度" in e for e in validate_plan_text(false_compaction_plan, doc_with_loading, ctx_loading, None, 0.05))

    unrelated_mismatch_plan = live_style_plan.replace(
        "碳酸盐共沉淀路线；正极厚度 30 μm，金属锂负极厚度 50 μm。",
        "极片压实密度 1.69 g/cm³，与另一项文献值不一致。",
    )
    assert any("压实密度" in e for e in validate_plan_text(unrelated_mismatch_plan, doc_with_loading, ctx_loading, None, 0.05))





