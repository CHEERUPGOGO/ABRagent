"""Stage 5 几何反馈修订回路单元测试.

覆盖:
1. 叙事提取: 颗粒粒径 R_s (μm) 与固相扩散系数 D_s 的正则提取 (科学计数法
   1e-14 / 2.5×10⁻¹⁴ / 10^-14、cm²/s 换算、区间取中值、逐组件归属);
2. geometry_revision.apply_geometry_revision: 合法修订 (provenance=stage5_revision +
   geometry_revisions 留痕)、越界拒绝 (不落盘)、未知字段拒绝、预算耗尽;
3. revise_geometry 端到端: design_scheme.json/md 同步更新 (声明表重渲染 + 修订记录),
   md 就地更新幂等;
4. 达标判定块: 课题文本目标能量提取、容差阈值 (400 Wh/kg, ±5% → 380) 与
   run_pinn_simulation 返回的 key_findings.target;
5. 逐轮留档: history 索引 + result/spec 旁车件; NO_MATCH 时历史一并清除;
6. 收尾择优: 未达标且最优轮 ≠ 最后一轮 → 回滚最优轮 (result/spec/几何/PNG 一致,
   恢复件通过 PINNPhysicsChecker 门禁); 已达标/最优即最后/无数值目标 → 不动作。

全部离线运行 (无 LLM/网络/TF)。
"""

import json
from pathlib import Path

import pytest

from src.lmllm.RAG.rag_pipeline import RAGPipeline
from pinn.input_spec import build_cell_spec, complete_geometry_block
from pinn.spm_runner import match_pinn_system
from auto_battery_research.workflow.stage_manager import StageManager
from auto_battery_research.tools.workflow_actions import (
    _clear_pinn_history,
    _load_pinn_history,
    extract_energy_target_wh_kg,
    get_max_geometry_revisions,
    get_target_energy_tolerance_pct,
    revise_geometry,
    run_pinn_simulation,
    select_best_stage5_result,
    _update_design_scheme_md,
)
from auto_battery_research.tools.geometry_revision import (
    apply_geometry_revision,
    collect_geometry_violations,
    count_geometry_revisions,
    render_revision_log_markdown,
    resolve_input_ranges,
)
from auto_battery_research.util.hashing import canonical_hash, stage5_scheme_projection
from auto_battery_research.checkers.pinn_physics_checker import PINNPhysicsChecker


SYSTEM_ID = "GrSi_NMC811"

# 位于 registry input_ranges 内的两套几何 (G1 能量高 / G2 能量低的占位语义, 由测试自定)
GEOMETRY_G1 = {
    "schema_version": "1.1",
    "cathode": {"L_um": 70.0, "porosity": 0.31, "R_s_um": 5.34, "D_s_m2s": 1e-14},
    "anode": {"L_um": 110.0, "porosity": 0.56, "R_s_um": 8.21, "D_s_m2s": 4e-14},
    "provenance": {
        "cathode.L_um": "stage4_recipe", "cathode.porosity": "stage4_recipe",
        "cathode.R_s_um": "stage4_recipe", "cathode.D_s_m2s": "stage4_recipe",
        "anode.L_um": "stage4_recipe", "anode.porosity": "stage4_recipe",
        "anode.R_s_um": "stage4_recipe", "anode.D_s_m2s": "stage4_recipe",
    },
    "condition": {"v_min": 2.8, "c_rate": 0.5},
}


def _geometry_copy() -> dict:
    return json.loads(json.dumps(GEOMETRY_G1))


def _make_mgr(tmp_path: Path, goal: str) -> StageManager:
    return StageManager(target_goal=goal, workspace_root=str(tmp_path))


def _write_design_scheme(tmp_path: Path, mgr: StageManager, goal: str, scheme: dict, md_text: str = None) -> Path:
    task_dir = mgr.get_task_output_dir(goal)
    task_dir.mkdir(parents=True, exist_ok=True)
    scheme_file = task_dir / "design_scheme.json"
    scheme_file.write_text(
        json.dumps({"schema_version": "1.1", "target": goal, "scheme": scheme, "evidence": []},
                   ensure_ascii=False),
        encoding="utf-8")
    if md_text is not None:
        (task_dir / "design_scheme.md").write_text(md_text, encoding="utf-8")
    return scheme_file


# ══════════════════ 1. 叙事提取: R_s / D_s ══════════════════

def test_parse_sci_number_variants():
    """科学计数法归一: e 记法 / ×10⁻¹⁴ / 裸 10^-14 / 纯小数."""
    assert RAGPipeline._parse_sci_number("1e-14") == 1e-14
    assert RAGPipeline._parse_sci_number("2.5×10⁻¹⁴") == 2.5e-14
    assert RAGPipeline._parse_sci_number("10^-14") == 1e-14
    assert RAGPipeline._parse_sci_number("2.5 * 10^-14") == 2.5e-14
    assert RAGPipeline._parse_sci_number("0.5") == 0.5
    assert RAGPipeline._parse_sci_number("abc") is None


def test_extract_geometry_rs_ds_per_component():
    """粒径/扩散系数按组件归属; D_s 支持区间取中值与 cm²/s 换算."""
    text = (
        "## 2. 推荐组合\n"
        "| 正极 | NCM811 | 极片厚度 85 μm，孔隙率 25%，颗粒粒径 0.5 μm，固相扩散系数 2.5×10⁻¹⁴ m²/s |\n"
        "| 负极 | 石墨/硅 | 极片厚度 120 μm，孔隙率 30%，D50 8 μm，扩散系数约为 1e-14~4e-14 m²/s |\n"
    )
    geo = RAGPipeline._extract_geometry(text)
    assert geo["cathode"]["R_s_um"] == 0.5
    assert geo["cathode"]["D_s_m2s"] == pytest.approx(2.5e-14)
    assert geo["anode"]["R_s_um"] == 8.0
    assert geo["anode"]["D_s_m2s"] == pytest.approx(2.5e-14)


def test_extract_geometry_ds_cm2s_conversion():
    """cm²/s 单位 ×1e-4 换算为 m²/s."""
    geo = RAGPipeline._extract_geometry("正极扩散系数 3.2×10⁻¹⁰cm²/s")
    assert geo["cathode"]["D_s_m2s"] == pytest.approx(3.2e-14)


def test_extract_geometry_rs_aliases():
    """颗粒半径/一次颗粒别名同样归入 R_s_um; 缺失字段不写入."""
    geo = RAGPipeline._extract_geometry("正极颗粒半径 4 μm；负极一次颗粒 10 μm")
    assert geo["cathode"]["R_s_um"] == 4.0
    assert geo["anode"]["R_s_um"] == 10.0
    assert "D_s_m2s" not in geo["cathode"]


def test_extracted_rs_ds_flow_through_normalization():
    """叙事提取的 R_s/D_s 经归一化后 provenance=stage4_recipe (硬字段语义)."""
    geo = RAGPipeline._extract_geometry(
        "正极颗粒粒径 5.34 μm，固相扩散系数 1e-14 m²/s；负极颗粒粒径 8.21 μm，扩散系数 4e-14 m²/s")
    block = complete_geometry_block(
        {"cathode": geo.get("cathode", {}), "anode": geo.get("anode", {})},
        "NCM811", "si_base")
    assert block is not None
    assert block["cathode"]["R_s_um"] == pytest.approx(5.34)
    assert block["provenance"]["cathode.R_s_um"] == "stage4_recipe"
    assert block["provenance"]["anode.D_s_m2s"] == "stage4_recipe"


# ══════════════════ 2. apply_geometry_revision ══════════════════

def _scheme_with_geometry() -> dict:
    return {
        "cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce",
        "geometry": _geometry_copy(),
    }


def test_apply_revision_ok_path():
    """合法修订: 值更新 + provenance=stage5_revision + 修订留痕 + 预算计数."""
    scheme = _scheme_with_geometry()
    res = apply_geometry_revision(
        scheme, {"cathode": {"L_um": 85.0, "porosity": 0.28}}, reason="能量缺口 30 Wh/kg")
    assert res["ok"] is True
    assert scheme["geometry"]["cathode"]["L_um"] == 85.0
    assert scheme["geometry"]["cathode"]["porosity"] == 0.28
    assert scheme["geometry"]["provenance"]["cathode.L_um"] == "stage5_revision"
    # 未修订字段 provenance 原样携带 (幂等)
    assert scheme["geometry"]["provenance"]["anode.L_um"] == "stage4_recipe"
    assert count_geometry_revisions(scheme) == 1
    entry = scheme["geometry_revisions"][0]
    assert entry["kind"] == "revision" and entry["round"] == 1
    assert {c["field"] for c in entry["changes"]} == {"cathode.L_um", "cathode.porosity"}


def test_apply_revision_out_of_range_rejected():
    """越界修订 (pe R_s 上限 10.68 μm) → 拒绝且零副作用."""
    scheme = _scheme_with_geometry()
    res = apply_geometry_revision(scheme, {"cathode": {"R_s_um": 50.0}}, reason="test")
    assert res["ok"] is False and res["error_code"] == "GEOMETRY_VALUE_ILLEGAL"
    assert any("cathode.R_s_um" in s for s in res["illegal"])
    assert res["legal_ranges"]  # 附合法范围提示
    assert scheme["geometry"]["cathode"]["R_s_um"] == 5.34  # 原值未动
    assert count_geometry_revisions(scheme) == 0


def test_apply_revision_unknown_field_rejected():
    """非可修订字段 (c_max / 未知组件) → 拒绝."""
    scheme = _scheme_with_geometry()
    res = apply_geometry_revision(scheme, {"cathode": {"c_max": 1.0}})
    assert res["ok"] is False and res["error_code"] == "REVISION_FIELD_UNKNOWN"
    res = apply_geometry_revision(scheme, {"separator": {"L_um": 1.0}})
    assert res["ok"] is False and res["error_code"] == "REVISION_FIELD_UNKNOWN"


def test_apply_revision_budget_exhausted():
    """修订预算耗尽 → REVISION_BUDGET_EXHAUSTED (择优回滚条目不占预算)."""
    scheme = _scheme_with_geometry()
    res1 = apply_geometry_revision(scheme, {"cathode": {"L_um": 82.0}}, max_revisions=1)
    assert res1["ok"] is True
    scheme["geometry_revisions"].append({"kind": "selection", "round": 99})  # 不应计入预算
    res2 = apply_geometry_revision(scheme, {"cathode": {"L_um": 84.0}}, max_revisions=1)
    assert res2["ok"] is False and res2["error_code"] == "REVISION_BUDGET_EXHAUSTED"


def test_apply_revision_missing_block():
    """无 v1.1 geometry 块 → GEOMETRY_BLOCK_MISSING."""
    res = apply_geometry_revision({"cathode": "NCM811"}, {"cathode": {"L_um": 80.0}})
    assert res["ok"] is False and res["error_code"] == "GEOMETRY_BLOCK_MISSING"


def test_resolve_input_ranges_and_collect_violations():
    """范围解析 + 越界收集 (checker/修订共用同一逻辑)."""
    ranges = resolve_input_ranges("NCM811", "si_base")
    assert ranges.get("pe", {}).get("R_s")
    geo = _geometry_copy()
    assert collect_geometry_violations(geo, ranges) == []
    geo["anode"]["D_s_m2s"] = 1e-5  # 越出 ne D_s 上限 1.6e-13
    violations = collect_geometry_violations(geo, ranges)
    assert any("anode.D_s_m2s" in v for v in violations)


# ══════════════════ 3. revise_geometry 端到端 (json/md 落盘) ══════════════════

_RAG_STYLE_MD = (
    "# 设计方案\n\n## 1. 目标与设计路线\n设计400Wh/kg方案\n\n---\n\n"
    "## 2. 推荐组合\n正极 NCM811 + 负极 si_base\n"
)


def test_revise_geometry_updates_json_and_md(tmp_path):
    """端到端: json scheme.geometry/geometry_revisions 更新, md 声明表重渲染 + 修订记录."""
    goal = "几何修订端到端测试400Wh/kg"
    mgr = _make_mgr(tmp_path, goal)
    scheme = _scheme_with_geometry()
    scheme_file = _write_design_scheme(
        tmp_path, mgr, goal, scheme,
        md_text=_RAG_STYLE_MD + "\n\n---\n\n**设计点几何声明**（Stage 4 显式给出，Stage 5 PINN 仿真输入）：\n\n| 旧表 |\n|---|\n| 旧 |\n")
    old_json = scheme_file.read_text(encoding="utf-8")

    res = revise_geometry(
        revisions={"cathode": {"L_um": 88.0}},
        reason="提升能量",
        target_query=goal, stage_manager=mgr)
    assert res["success"] is True, res.get("error")
    assert res["key_findings"]["revisions_used"] == 1

    doc = json.loads(scheme_file.read_text(encoding="utf-8"))
    assert doc["scheme"]["geometry"]["cathode"]["L_um"] == 88.0
    assert doc["scheme"]["geometry"]["provenance"]["cathode.L_um"] == "stage5_revision"
    assert doc["scheme"]["geometry_revisions"][0]["kind"] == "revision"

    md_text = (mgr.get_task_output_dir(goal) / "design_scheme.md").read_text(encoding="utf-8")
    assert "**设计点几何声明**" in md_text and "88" in md_text
    assert "**几何修订记录**" in md_text and "提升能量" in md_text
    assert "旧表" not in md_text  # 旧声明表被重渲染替换
    assert md_text.count("**设计点几何声明**") == 1

    # 幂等: 同内容重渲染不漂移
    from pinn.input_spec import format_geometry_markdown
    _update_design_scheme_md(
        mgr.get_task_output_dir(goal) / "design_scheme.md",
        format_geometry_markdown(doc["scheme"]["geometry"]),
        render_revision_log_markdown(doc["scheme"]["geometry_revisions"]))
    assert (mgr.get_task_output_dir(goal) / "design_scheme.md").read_text(encoding="utf-8") == md_text


def test_revise_geometry_budget_from_runtime_options(tmp_path):
    """预算取 runtime_options.max_geometry_revisions (此处 1) → 第二轮拒绝."""
    goal = "几何修订预算测试"
    mgr = _make_mgr(tmp_path, goal)
    mgr.config["runtime_options"] = {"max_geometry_revisions": 1}
    _write_design_scheme(tmp_path, mgr, goal, _scheme_with_geometry())

    res1 = revise_geometry(revisions={"cathode": {"L_um": 82.0}},
                           target_query=goal, stage_manager=mgr)
    assert res1["success"] is True
    res2 = revise_geometry(revisions={"cathode": {"L_um": 84.0}},
                           target_query=goal, stage_manager=mgr)
    assert res2["success"] is False and res2["error_code"] == "REVISION_BUDGET_EXHAUSTED"


def test_revise_geometry_missing_scheme(tmp_path):
    """无 design_scheme.json → DESIGN_SCHEME_MISSING, 不落盘."""
    goal = "几何修订缺方案测试"
    mgr = _make_mgr(tmp_path, goal)
    res = revise_geometry(revisions={"cathode": {"L_um": 80.0}},
                          target_query=goal, stage_manager=mgr)
    assert res["success"] is False and res["error_code"] == "DESIGN_SCHEME_MISSING"


def test_default_budget_and_tolerance():
    """缺省预算 3 轮 / 容差 5%; 非法配置回退默认."""
    assert get_max_geometry_revisions(None) == 3
    assert get_target_energy_tolerance_pct(None) == 5.0


# ══════════════════ 4. 达标判定块 (目标提取 + 容差) ══════════════════

def test_extract_energy_target():
    assert extract_energy_target_wh_kg("设计400Wh/kg高比能液态锂金属电池方案") == 400.0
    assert extract_energy_target_wh_kg("目标 350 Wh/kg 电池") == 350.0
    assert extract_energy_target_wh_kg("高比能电池方案") is None
    assert extract_energy_target_wh_kg("") is None


def test_run_pinn_simulation_returns_target_block(tmp_path):
    """e2e: CONVERGED 返回 key_findings.target (400 目标/±5% 容差/380 阈值) 与修订预算."""
    goal = "仿真达标判定块测试400Wh/kg"
    mgr = _make_mgr(tmp_path, goal)
    _write_design_scheme(tmp_path, mgr, goal, _scheme_with_geometry())

    res = run_pinn_simulation(target_query=goal, stage_manager=mgr)
    assert res["success"] is True, res.get("message")
    target = res["key_findings"].get("target")
    assert target, f"缺少达标判定块: {res['key_findings'].keys()}"
    assert target["energy_target_wh_kg"] == 400.0
    assert target["tolerance_pct"] == 5.0
    assert target["threshold_wh_kg"] == pytest.approx(380.0)
    assert isinstance(target["met"], bool)
    budget = res["key_findings"]["revision_budget"]
    assert budget == {"used": 0, "max": 3, "remaining": 3}
    # 仿真结果回显: 生效几何 + spec_inputs
    pinn_run = res["key_findings"]["pinn_run"]
    assert pinn_run["status"] == "CONVERGED"
    assert pinn_run["effective_geometry"] and pinn_run["spec_inputs"]
    assert pinn_run["round"] == 1


# ══════════════════ 5. 逐轮留档 ══════════════════

def test_history_recorded_and_cleared_on_no_match(tmp_path):
    """CONVERGED 轮落旁车 + 索引; 体系切到未匹配 → 历史与旁车一并清除."""
    goal = "逐轮留档测试400Wh/kg"
    mgr = _make_mgr(tmp_path, goal)
    _write_design_scheme(tmp_path, mgr, goal, _scheme_with_geometry())
    task_dir = mgr.get_task_output_dir(goal)

    res = run_pinn_simulation(target_query=goal, stage_manager=mgr)
    assert res["success"] is True
    assert (task_dir / "pinn_simulation_history.json").exists()
    assert (task_dir / "pinn_simulation_round_1.result.json").exists()
    assert (task_dir / "pinn_simulation_round_1.spec.json").exists()
    history = _load_pinn_history(task_dir)
    assert len(history["rounds"]) == 1
    assert history["rounds"][0]["status"] == "CONVERGED"
    assert history["rounds"][0]["geometry_snapshot"]["cathode"]["L_um"] == 70.0

    # 切换为未匹配体系 → NO_MATCH 清历史
    _write_design_scheme(tmp_path, mgr, goal, {"cathode": "NCM811", "anode": "li_metal", "electrolyte": "lhce"})
    res2 = run_pinn_simulation(target_query=goal, stage_manager=mgr)
    assert res2["key_findings"]["pinn_run"]["status"] == "NO_MATCH"
    assert not (task_dir / "pinn_simulation_history.json").exists()
    assert not (task_dir / "pinn_simulation_round_1.result.json").exists()
    assert res2["key_findings"]["pinn_run"]["history_cleared"]


# ══════════════════ 6. 收尾择优回滚 ══════════════════

def _craft_converged_round(task_dir: Path, k: int, geometry: dict, energy: float) -> dict:
    """构造一轮真实可验收的 CONVERGED 留档 (result/spec 旁车 + 索引记录)."""
    scheme = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce", "geometry": geometry}
    scheme_hash = canonical_hash(stage5_scheme_projection(scheme))
    cell_spec = {"cathode": {"L": geometry["cathode"]["L_um"] * 1e-6}, "marker": f"round{k}"}
    spec_payload = {
        "cell_spec": cell_spec,
        "cell_spec_hash": canonical_hash(cell_spec),
        "trigger": {"enabled": True, "triggered": True},
    }
    result_payload = {
        "schema_version": "1.0", "kind": "pinn_simulation_result",
        "target": "test", "generated_at": "2026-01-01T00:00:00",
        "status": "CONVERGED", "system_id": SYSTEM_ID, "c_rate": 0.5,
        "specific_capacity_mAh_g": 200.0, "average_voltage_V": 3.5,
        "energy_wh_kg": energy, "calculated_cell_energy_wh_kg": energy,
        "termination": "voltage_cutoff", "envelope_ok": True,
        "discharge_curve": {"capacity": [0.0, 100.0, 200.0], "voltage": [3.9, 3.6, 3.0]},
        "pinn": {"spec_hash": spec_payload["cell_spec_hash"], "scheme_hash": scheme_hash,
                 "run_id": f"run{k:012d}"[:12], "model_version": SYSTEM_ID},
    }
    (task_dir / f"pinn_simulation_round_{k}.result.json").write_text(
        json.dumps(result_payload, ensure_ascii=False), encoding="utf-8")
    (task_dir / f"pinn_simulation_round_{k}.spec.json").write_text(
        json.dumps(spec_payload, ensure_ascii=False), encoding="utf-8")
    return {
        "round": k, "timestamp": "2026-01-01T00:00:00", "status": "CONVERGED",
        "system_id": SYSTEM_ID, "c_rate": 0.5, "energy_wh_kg": energy,
        "specific_capacity_mAh_g": 200.0, "average_voltage_V": 3.5,
        "termination": "voltage_cutoff",
        "spec_hash": spec_payload["cell_spec_hash"], "scheme_hash": scheme_hash,
        "geometry_snapshot": geometry,
        "result_file": f"pinn_simulation_round_{k}.result.json",
        "spec_file": f"pinn_simulation_round_{k}.spec.json",
    }


def _write_history(task_dir: Path, rounds: list) -> None:
    (task_dir / "pinn_simulation_history.json").write_text(
        json.dumps({"rounds": rounds}, ensure_ascii=False), encoding="utf-8")


def _selection_fixture(tmp_path: Path, goal: str, energies: tuple) -> StageManager:
    """择优夹具: 两轮留档 + 主产物停在最后一轮 + scheme 已再修订但未重跑仿真.

    - round k=1 (G1) / k=2 (G2) 各带真实 spec_hash/scheme_hash 旁车件;
    - 主 result/spec = 最后一轮 (round 2) 落盘;
    - design_scheme.json 几何 = G3 (round 2 之后的再修订, 未仿真) —— 主产物
      scheme_hash 失配, 门禁 PINN_SCHEME_STALE, 由择优回滚恢复一致。
    """
    mgr = _make_mgr(tmp_path, goal)
    g1, g2 = _geometry_copy(), _geometry_copy()
    g2["cathode"]["L_um"] = 90.0  # 最后一轮几何
    g3 = _geometry_copy()
    g3["cathode"]["L_um"] = 95.0  # 修订后未仿真的 scheme 几何
    _write_design_scheme(tmp_path, mgr, goal,
                         {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce", "geometry": g3})
    task_dir = mgr.get_task_output_dir(goal)
    rounds = [_craft_converged_round(task_dir, k + 1, g, e) for k, (g, e) in enumerate(zip((g1, g2), energies))]
    _write_history(task_dir, rounds)
    # 主产物 = 最后一轮 (round 2)
    (task_dir / "pinn_simulation_result.json").write_text(
        (task_dir / "pinn_simulation_round_2.result.json").read_text(encoding="utf-8"), encoding="utf-8")
    (task_dir / "pinn_input_spec.json").write_text(
        (task_dir / "pinn_simulation_round_2.spec.json").read_text(encoding="utf-8"), encoding="utf-8")
    return mgr


def test_select_best_rolls_back_to_higher_energy_round(tmp_path):
    """未达标 (两轮均 < 阈值 380) + 第 1 轮能量更高 → 回滚第 1 轮, 恢复件过门禁."""
    goal = "择优回滚测试400Wh/kg"
    mgr = _selection_fixture(tmp_path, goal, energies=(370.0, 350.0))
    task_dir = mgr.get_task_output_dir(goal)

    # 回滚前: 门禁应因 scheme_hash 失配拒绝最后一轮结果 (几何已被改)
    checker_before = PINNPhysicsChecker()
    checker_before.on_init(stage_manager=None, stage_info={"id": 5, "name": "PINN"},
                           config={"paths": {"output_dir": str(task_dir)}})
    passed_before, diag_before = checker_before.do_check()
    assert passed_before is False and diag_before["error_code"] == "PINN_SCHEME_STALE"

    out = select_best_stage5_result(mgr)
    assert out["action"] == "restored_best"
    assert out["best_round"] == 1 and out["best_energy_wh_kg"] == 370.0

    # result/spec 恢复为第 1 轮; PNG 由留存曲线重渲染
    result_payload = json.loads((task_dir / "pinn_simulation_result.json").read_text(encoding="utf-8"))
    assert result_payload["pinn"]["run_id"] == "run000000000001"[:12]
    spec_payload = json.loads((task_dir / "pinn_input_spec.json").read_text(encoding="utf-8"))
    assert spec_payload["cell_spec"]["marker"] == "round1"
    assert (task_dir / "pinn_simulation_curve.png").exists()

    # 几何回滚至第 1 轮快照 + 择优留痕
    doc = json.loads((task_dir / "design_scheme.json").read_text(encoding="utf-8"))
    assert doc["scheme"]["geometry"]["cathode"]["L_um"] == 70.0
    sel_entries = [e for e in doc["scheme"]["geometry_revisions"] if e["kind"] == "selection"]
    assert len(sel_entries) == 1 and "择优" in sel_entries[0]["reason"]
    md_text = (task_dir / "design_scheme.md").read_text(encoding="utf-8")
    assert "择优回滚" in md_text

    # 恢复件与恢复后几何一致 → 门禁通过
    checker_after = PINNPhysicsChecker()
    checker_after.on_init(stage_manager=None, stage_info={"id": 5, "name": "PINN"},
                          config={"paths": {"output_dir": str(task_dir)}})
    passed_after, diag_after = checker_after.do_check()
    assert passed_after is True, diag_after.get("error_msg")


def test_select_best_noop_when_target_met(tmp_path):
    """任一轮已达标 → 不回滚."""
    goal = "择优已达标测试400Wh/kg"
    mgr = _selection_fixture(tmp_path, goal, energies=(390.0, 350.0))
    # 第 1 轮能量提到阈值之上 (≥ 380)
    task_dir = mgr.get_task_output_dir(goal)
    rounds = _load_pinn_history(task_dir)["rounds"]
    rounds[0]["energy_wh_kg"] = 395.0
    _write_history(task_dir, rounds)
    out = select_best_stage5_result(mgr)
    assert out["action"] == "none" and out["reason"] == "target_met"


def test_selected_round_repairs_damaged_current_spec(tmp_path):
    mgr = _selection_fixture(tmp_path, "恢复损坏输入400Wh/kg", energies=(370., 350.))
    assert select_best_stage5_result(mgr)["action"] == "restored_best"
    path = mgr.get_task_output_dir() / "pinn_input_spec.json"
    path.write_text("{}", encoding="utf-8")
    assert select_best_stage5_result(mgr)["action"] == "restored_best"
    assert json.loads(path.read_text(encoding="utf-8"))["cell_spec"]["marker"] == "round1"


def test_missing_archive_spec_does_not_partially_restore(tmp_path):
    mgr = _selection_fixture(tmp_path, "缺失恢复件400Wh/kg", energies=(370., 350.))
    directory = mgr.get_task_output_dir()
    before = (directory / "design_scheme.json").read_bytes()
    (directory / "pinn_simulation_round_1.spec.json").unlink()
    assert select_best_stage5_result(mgr)["reason"] == "spec_sidecar_missing"
    assert (directory / "design_scheme.json").read_bytes() == before


def test_selection_cannot_restore_other_material_scheme(tmp_path):
    mgr = _selection_fixture(tmp_path, "隔离历史方案400Wh/kg", energies=(370., 350.))
    path = mgr.get_task_output_dir() / "design_scheme.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["scheme"]["electrolyte"] = "high_concentration"
    path.write_text(json.dumps(doc), encoding="utf-8")
    before = path.read_bytes()
    assert select_best_stage5_result(mgr)["reason"] == "archive_scheme_mismatch"
    assert path.read_bytes() == before


def test_select_best_noop_when_best_is_last(tmp_path):
    """最优轮即最后一轮 (两轮均未达标) → 不动作."""
    goal = "择优最优即最后测试400Wh/kg"
    mgr = _selection_fixture(tmp_path, goal, energies=(350.0, 370.0))
    out = select_best_stage5_result(mgr)
    assert out["action"] == "none" and out["reason"] == "best_is_last"


def test_select_best_noop_without_numeric_target(tmp_path):
    """课题无数值化能量目标 → 不动作 (定性判断归 LLM)."""
    goal = "择优无数值目标测试"
    mgr = _selection_fixture(tmp_path, goal, energies=(390.0, 350.0))
    out = select_best_stage5_result(mgr)
    assert out["action"] == "none" and out["reason"] == "no_numeric_target"


def test_select_best_noop_without_history(tmp_path):
    """无历史留档 → 静默跳过."""
    goal = "择优无历史测试400Wh/kg"
    mgr = _make_mgr(tmp_path, goal)
    assert select_best_stage5_result(mgr)["action"] == "none"


def test_clear_pinn_history_removes_sidecars(tmp_path):
    """_clear_pinn_history 清理索引 + 全部旁车件."""
    goal = "清历史测试"
    mgr = _make_mgr(tmp_path, goal)
    task_dir = mgr.get_task_output_dir(goal)
    task_dir.mkdir(parents=True, exist_ok=True)
    rounds = [_craft_converged_round(task_dir, 1, _geometry_copy(), 390.0)]
    _write_history(task_dir, rounds)
    removed = _clear_pinn_history(task_dir)
    assert "pinn_simulation_history.json" in removed
    assert "pinn_simulation_round_1.result.json" in removed
    assert not (task_dir / "pinn_simulation_history.json").exists()


def test_check_and_complete_select_same_round_once(tmp_path):
    """Check 已完成回滚后 Complete 不再次回滚、不重复追加选择日志。"""
    mgr = _selection_fixture(tmp_path, "一致收尾400Wh/kg", energies=(370., 350.))
    mgr.current_stage_idx = 4
    for stage in mgr.stages[:4]:
        stage.status = "PASSED"
    mgr.stages[4].status = "FAILED"
    ok, diagnostic = mgr.check_stage(5)
    assert ok, diagnostic
    task_dir = mgr.get_task_output_dir()
    before = (task_dir / "pinn_simulation_result.json").read_bytes()
    ok, result = mgr.complete_stage(5)
    assert ok, result
    assert (task_dir / "pinn_simulation_result.json").read_bytes() == before
    doc = json.loads((task_dir / "design_scheme.json").read_text(encoding="utf-8"))
    assert len([e for e in doc["scheme"]["geometry_revisions"] if e["kind"] == "selection"]) == 1


def test_completion_journal_preserves_research_notes(tmp_path):
    mgr = _make_mgr(tmp_path, "日志保留测试")
    mgr.set_stage_journal(5, notes="最佳候选370，目标未达成", deliverables=["result.json"], key_findings={"met": False})
    mgr.set_stage_journal(5, notes="阶段完成，耗时10秒", key_findings={"status": "PASSED"})
    entry = next(j for j in mgr.get_all_stage_journal() if j["stage_id"] == 5)
    assert "目标未达成" in entry["notes"] and "阶段完成" in entry["notes"]
    assert entry["key_findings"] == {"met": False, "status": "PASSED"}
    assert entry["deliverables"] == ["result.json"]
