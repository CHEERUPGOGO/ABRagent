"""Stage 5 参数提取单元测试 (PINN 真实模型接入前的占位阶段).

覆盖:
1. 完整方案 (NCM811/li_metal/lhce) → CellSpec 物理量从缺省表提取, 锂金属负极
   物理字段为 null (li_metal_boundary 模型);
2. 缺省表未覆盖材料 (LFP) → 物理量字段保持 null, 仅 candidates.json 材料级参数可提取;
3. Stage 4 方案缺失 → 降级生成 (scheme 为空), 不抛异常;
4. pinn_trigger.enabled 开关占位判定;
5. PINNPhysicsChecker 对 pinn_input_spec.json 的结构校验 (缺失/损坏/完整);
6. skip 快速通道语义: _generate_pinn_input_spec 直接以 (goal, mgr) 调用同样落盘。

全部测试在临时工作区运行，不触碰真实仓库产物。
"""

import json
from pathlib import Path

from auto_battery_research.workflow.stage_manager import StageManager
from auto_battery_research.tools.workflow_actions import (
    _generate_pinn_input_spec,
    run_pinn_simulation,
)
from auto_battery_research.checkers.pinn_physics_checker import PINNPhysicsChecker


SCHEME_NCM811_LI_METAL = {
    "schema_version": "1.1",
    "target": "设计400Wh/kg高比能电池",
    "scheme": {
        "cathode": "NCM811",
        "anode": "li_metal",
        "electrolyte": "lhce",
        "additives": ["FEC"],
        "target_energy_wh_kg": 400.0,
    },
    "evidence": [{"passage_id": "ev_001", "source": "10.1038/s41467", "text": "证据"}],
}

SCHEME_LFP_GRAPHITE = {
    "schema_version": "1.1",
    "target": "磷酸铁锂常规体系",
    "scheme": {
        "cathode": "LFP",
        "anode": "graphite",
        "electrolyte": "carbonate_ec",
        "target_energy_wh_kg": 180.0,
    },
    "evidence": [],
}


def _make_mgr(tmp_path: Path, goal: str, pinn_trigger: dict = None) -> StageManager:
    mgr = StageManager(target_goal=goal, workspace_root=str(tmp_path))
    if pinn_trigger is not None:
        mgr.config["pinn_trigger"] = pinn_trigger
    return mgr


def _write_scheme(tmp_path: Path, mgr: StageManager, goal: str, scheme_payload: dict) -> None:
    task_dir = mgr.get_task_output_dir(goal)
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "design_scheme.json").write_text(
        json.dumps(scheme_payload, ensure_ascii=False), encoding="utf-8"
    )


def test_extraction_full_scheme_ncm811_li_metal(tmp_path):
    """缺省表内材料: 物理量从 DEFAULT 表提取; 锂金属负极物理字段为 null."""
    goal = "Stage5参数提取_完整方案测试"
    mgr = _make_mgr(tmp_path, goal)
    _write_scheme(tmp_path, mgr, goal, SCHEME_NCM811_LI_METAL)

    res = _generate_pinn_input_spec(goal, mgr=mgr)
    assert res["success"] is True, res.get("error")
    assert res["key_findings"]["scheme_found"] is True
    assert res["key_findings"]["status"] == "PINN_NOT_TRIGGERED"

    spec_file = Path(res["spec_file"])
    assert spec_file.exists()
    payload = json.loads(spec_file.read_text(encoding="utf-8"))
    assert payload["kind"] == "pinn_input_spec"
    assert payload["trigger"]["triggered"] is False

    cell_spec = payload["cell_spec"]
    assert cell_spec["cathode"]["material"]["c_max"] == 49000.0
    assert cell_spec["cathode"]["material"]["D_s"] == 1e-14
    assert cell_spec["cathode"]["material"]["R_p"] == 5e-6
    # NCM811 无 U_ocp 缺省 → 留 null
    assert cell_spec["cathode"]["material"]["U_ocp"] is None
    # 锂金属负极: li_metal_boundary 模型, 物理字段全 null
    assert cell_spec["anode"]["material"]["model"] == "li_metal_boundary"
    assert cell_spec["anode"]["material"]["D_s"] is None
    assert cell_spec["anode"]["material"]["c_max"] is None
    # lhce 电解液: c_e0 = 2500 mol/m³
    assert cell_spec["electrolyte"]["name"] == "lhce"
    assert cell_spec["electrolyte"]["c_e0"] == 2500.0
    # 设计目标与测试条件
    assert cell_spec["design"]["target_energy_density"] == 400.0
    assert cell_spec["condition"]["c_rate"] == 0.5
    assert cell_spec["condition"]["temperature_C"] == 25.0
    # NCM811 在 MATERIAL_PROFILES 中有电压窗口
    assert cell_spec["condition"]["voltage_min"] is not None

    summary = payload["extraction_summary"]
    assert summary["cathode"]["in_default_table"] is True
    assert "c_max" in summary["cathode"]["fields_filled"]
    assert "U_ocp" in summary["cathode"]["fields_missing"]
    assert summary["anode"]["in_default_table"] is True
    assert "D_s" in summary["anode"]["fields_missing"]


def test_extraction_material_not_in_default_table(tmp_path):
    """缺省表外材料 (LFP): 物理量字段保持 null, 仅材料级 capacity 可由 candidates.json 提供."""
    goal = "Stage5参数提取_缺省表外材料测试"
    mgr = _make_mgr(tmp_path, goal)
    _write_scheme(tmp_path, mgr, goal, SCHEME_LFP_GRAPHITE)

    res = _generate_pinn_input_spec(goal, mgr=mgr)
    assert res["success"] is True

    payload = json.loads(Path(res["spec_file"]).read_text(encoding="utf-8"))
    cell_spec = payload["cell_spec"]
    # LFP 不在 DEFAULT_MATERIALS → D_s/c_max 等物理量无缺省, 保持 null
    assert cell_spec["cathode"]["material"]["D_s"] is None
    assert cell_spec["cathode"]["material"]["c_max"] is None
    # graphite 在缺省表内 → 物理量可提取
    assert cell_spec["anode"]["material"]["D_s"] == 3.9e-14
    # carbonate_ec 在缺省表内
    assert cell_spec["electrolyte"]["c_e0"] == 1000.0

    summary = payload["extraction_summary"]
    assert summary["cathode"]["in_default_table"] is False
    # candidates.json 可提供 LFP 的材料级参数 (formula/capacity/电压)
    assert "formula" in summary["cathode"]["fields_filled"]
    assert "theoretical_capacity" in summary["cathode"]["fields_filled"]
    # 物理量参数无缺省来源 → 全部留在 fields_missing
    for physics_field in ("D_s", "c_max", "R_p", "k_ref", "sigma"):
        assert physics_field in summary["cathode"]["fields_missing"]
        assert physics_field not in summary["cathode"]["fields_filled"]


def test_extraction_without_scheme_degrades(tmp_path):
    """Stage 4 方案缺失: 降级生成 (scheme 为空 + notes 标注), 不抛异常."""
    goal = "Stage5参数提取_无方案降级测试"
    mgr = _make_mgr(tmp_path, goal)

    res = _generate_pinn_input_spec(goal, mgr=mgr)
    assert res["success"] is True
    assert res["key_findings"]["scheme_found"] is False

    payload = json.loads(Path(res["spec_file"]).read_text(encoding="utf-8"))
    assert payload["scheme"]["cathode"] is None
    assert "design_scheme.json" in payload["notes"]


def test_trigger_enabled_switch(tmp_path):
    """pinn_trigger.enabled=true → triggered=True (占位开关语义, 具体条件待 PINN 模块定义)."""
    goal = "Stage5参数提取_触发开关测试"
    mgr = _make_mgr(tmp_path, goal, pinn_trigger={"enabled": True})
    _write_scheme(tmp_path, mgr, goal, SCHEME_NCM811_LI_METAL)

    res = _generate_pinn_input_spec(goal, mgr=mgr)
    assert res["key_findings"]["status"] == "PINN_TRIGGERED"

    payload = json.loads(Path(res["spec_file"]).read_text(encoding="utf-8"))
    assert payload["trigger"]["enabled"] is True
    assert payload["trigger"]["triggered"] is True
    assert any("开关" in r for r in payload["trigger"]["reasons"])


def test_run_pinn_simulation_wrapper(tmp_path):
    """run_pinn_simulation 对外契约: success/journal_notes/deliverables 结构不变."""
    goal = "Stage5参数提取_对外契约测试"
    mgr = _make_mgr(tmp_path, goal)
    _write_scheme(tmp_path, mgr, goal, SCHEME_NCM811_LI_METAL)

    res = run_pinn_simulation(target_query=goal, stage_manager=mgr)
    assert res["success"] is True
    assert res["journal_notes"]
    assert len(res["deliverables"]) == 1
    assert res["deliverables"][0].endswith("pinn_input_spec.json")
    # 不再产出仿真文件
    task_dir = mgr.get_task_output_dir(goal)
    assert not (task_dir / "simulation_result.json").exists()
    assert not (task_dir / "pinn_simulation_report.json").exists()


def test_checker_accepts_spec_file(tmp_path):
    """PINNPhysicsChecker: pinn_input_spec.json 结构校验 (缺失/损坏/缺块/完整)."""
    checker = PINNPhysicsChecker()
    checker.on_init(
        stage_manager=None,
        stage_info={"id": 5, "name": "参数提取", "skip": False},
        config={"paths": {"output_dir": str(tmp_path)}},
    )

    # 1. 产物缺失 → 失败
    passed, diag = checker.do_check()
    assert passed is False
    assert diag["error_code"] == "PINN_SIMULATION_RESULT_MISSING"

    # 2. JSON 损坏 → 失败
    spec_file = tmp_path / "pinn_input_spec.json"
    spec_file.write_text("{not valid json", encoding="utf-8")
    passed, diag = checker.do_check()
    assert passed is False
    assert diag["error_code"] == "PINN_INPUT_SPEC_CORRUPTED"

    # 3. 缺少 cell_spec 块 → 失败
    spec_file.write_text(json.dumps({"kind": "pinn_input_spec", "trigger": {}}), encoding="utf-8")
    passed, diag = checker.do_check()
    assert passed is False
    assert diag["error_code"] == "PINN_INPUT_SPEC_INCOMPLETE"

    # 4. 结构完整 → 通过
    spec_file.write_text(json.dumps({
        "kind": "pinn_input_spec",
        "trigger": {"enabled": False, "triggered": False, "reasons": ["占位"]},
        "scheme": {"cathode": "NCM811", "anode": "li_metal", "electrolyte": "lhce"},
        "cell_spec": {"cathode": {"material": {"c_max": 49000.0}}, "anode": {}, "electrolyte": {}},
        "extraction_summary": {
            "cathode": {"fields_filled": ["c_max", "D_s"], "fields_missing": ["U_ocp"]},
            "anode": {"fields_filled": [], "fields_missing": ["D_s"]},
        },
    }, ensure_ascii=False), encoding="utf-8")
    passed, diag = checker.do_check()
    assert passed is True
    assert diag["observed"]["pinn_triggered"] is False
    assert diag["observed"]["param_fields_filled"] == 2
    assert diag["observed"]["param_fields_missing"] == 2


def test_skip_channel_call_signature(tmp_path):
    """skip 快速通道语义: 直接以 (goal, mgr) 调用 (不传 task_dir) 同样落盘."""
    goal = "Stage5参数提取_skip通道测试"
    mgr = _make_mgr(tmp_path, goal)
    _write_scheme(tmp_path, mgr, goal, SCHEME_NCM811_LI_METAL)

    res = _generate_pinn_input_spec(goal, mgr=mgr)
    assert res["success"] is True
    assert res["deliverables"] == [str(mgr.get_task_output_dir(goal) / "pinn_input_spec.json")]
    assert Path(res["spec_file"]).exists()


def test_complete_stage_cascade_triggers_stage5_extraction(tmp_path):
    """LLM 调 Complete(stage_id=4) 时的级联跳过 (complete_stage 内部循环) 也必须执行参数提取.

    这是真实运行中 Stage 5 被绕过的实际路径: 级联标记 SKIPPED 不经过 agent 主循环
    skip 快速通道，需在此兜底执行提取并补记 Stage 5 journal。
    """
    goal = "Stage5级联跳过参数提取测试"
    mgr = _make_mgr(tmp_path, goal)
    _write_scheme(tmp_path, mgr, goal, SCHEME_NCM811_LI_METAL)

    # 手动置位: Stage 1-3 已完成, 当前活跃阶段为 Stage 4
    for s in mgr.stages[:3]:
        s.status = "PASSED"
    mgr.stages[3].status = "IN_PROGRESS"
    mgr.current_stage_idx = 3
    # 打桩 Stage 4 门禁直接放行
    mgr.check_stage = lambda stage_id=None, is_complete=False, **kw: (True, {})

    ok, res = mgr.complete_stage(4)
    assert ok is True, res.get("error")
    assert mgr.get_stage_by_id(5).status == "SKIPPED"

    # 级联跳过兜底: 参数提取落盘 + Stage 5 journal 补记
    spec_file = mgr.get_task_output_dir(goal) / "pinn_input_spec.json"
    assert spec_file.exists()
    payload = json.loads(spec_file.read_text(encoding="utf-8"))
    assert payload["cell_spec"]["cathode"]["material"]["c_max"] == 49000.0
    journals = mgr.get_all_stage_journal()
    s5_rows = [j for j in journals if j.get("stage_id") == 5]
    assert len(s5_rows) == 1
    assert s5_rows[0]["deliverables"] == [str(spec_file)]
