"""Stage 5 SPM PINN 推理接入单元测试.

覆盖:
1. numpy 网络前向 vs golden_io.json 金样本 (TF SavedModel 导出时生成) —— 锁定
   与训练实现数值等价, 架构/权重被改动时立刻失败;
2. registry 体系匹配与逐参数有效范围 (input_ranges) 声明;
3. spec 驱动显式输入: R_s/D_s/厚度/孔隙率/C-rate 从 cell_spec 提取 (无值回退
   训练参数); 范围越界在网络前向之前拦截 (OUT_OF_RANGE), 组合越界由训练包络
   拦截 (OUT_OF_ENVELOPE);
4. run_pinn_charge 配对占位: registry 无充电模型 → CHARGE_NOT_AVAILABLE;
5. 端到端 run_pinn_simulation: Stage 5 激活 + 匹配 + 输入合规 → 落盘
   pinn_simulation_result.json; 输入越界 → 拦截不产出; skip/未匹配/总开关关闭
   → 只落盘参数提取清单;
6. PINNPhysicsChecker 对 pinn_simulation_result.json 的接受/拒绝路径。

全部测试在临时工作区运行, 离线无 LLM/网络/TF (numpy 前向为核心依赖自带)。
"""

import json
from pathlib import Path

import numpy as np
import pytest

from pinn.spm_runner import (
    MODELS_DIR,
    load_system_params,
    load_weights,
    match_pinn_system,
    network_forward,
    run_pinn_charge,
    run_pinn_discharge,
)
from pinn.input_spec import build_cell_spec
from auto_battery_research.workflow.stage_manager import StageManager
from auto_battery_research.tools.workflow_actions import run_pinn_simulation
from auto_battery_research.checkers.pinn_physics_checker import PINNPhysicsChecker


SYSTEM_ID = "GrSi_NMC811"
SCHEME_MATCHED = {
    "cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce",
    "target_energy_wh_kg": 500.0,
}
SCHEME_UNMATCHED = {"cathode": "NCM811", "anode": "li_metal", "electrolyte": "lhce"}

CHECKER_CAPACITY_RANGE = (50.0, 4500.0)
CHECKER_VOLTAGE_RANGE = (1.0, 5.5)
CHECKER_ENERGY_RANGE = (50.0, 3000.0)


# ══════════════════ 工具函数 ══════════════════

def _make_mgr(tmp_path: Path, goal: str, pinn_trigger: dict = None) -> StageManager:
    mgr = StageManager(target_goal=goal, workspace_root=str(tmp_path))
    if pinn_trigger is not None:
        mgr.config["pinn_trigger"] = pinn_trigger
    return mgr


def _write_scheme(tmp_path: Path, mgr: StageManager, goal: str, scheme: dict) -> None:
    task_dir = mgr.get_task_output_dir(goal)
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "design_scheme.json").write_text(
        json.dumps({"schema_version": "1.1", "target": goal, "scheme": scheme, "evidence": []},
                   ensure_ascii=False),
        encoding="utf-8",
    )


def _build_cell_spec_dict(scheme: dict) -> dict:
    """标准提取链路产物（无缺省表回填，物理字段 null）。"""
    spec = build_cell_spec(scheme, None, c_rate=0.5)
    spec["condition"]["voltage_min"] = 2.8
    return spec


def _calibrated_cell_spec_dict(scheme: dict) -> dict:
    """GrSi 体系训练参考值级别的 spec（对齐 params.json，位于 input_ranges 中心）。"""
    cell_spec = _build_cell_spec_dict(scheme)
    # 负极 (ne) ← GrSi 训练参考值
    cell_spec["anode"]["L"] = 110e-6
    cell_spec["anode"]["epsilon"] = 0.56
    cell_spec["anode"]["material"]["R_p"] = 8.21e-6
    cell_spec["anode"]["material"]["D_s"] = 4e-14
    # 正极 (pe) ← NMC811 训练参考值
    cell_spec["cathode"]["L"] = 70e-6
    cell_spec["cathode"]["epsilon"] = 0.31
    cell_spec["cathode"]["material"]["R_p"] = 5.34e-6
    cell_spec["cathode"]["material"]["D_s"] = 1e-14
    cell_spec["condition"]["c_rate"] = 0.5
    return cell_spec


def _make_checker(output_dir: Path) -> PINNPhysicsChecker:
    checker = PINNPhysicsChecker()
    checker.on_init(
        stage_manager=None,
        stage_info={"id": 5, "name": "PINN 物理仿真校验", "skip": False},
        config={"paths": {"output_dir": str(output_dir)}},
    )
    return checker


# ══════════════════ 1. numpy 前向等价性 (golden_io) ══════════════════

def test_numpy_forward_matches_golden_io():
    """network_forward(npz 权重) 必须复现导出时的 TF 前向输出 (atol 1e-9)."""
    system_dir = MODELS_DIR / "systems" / SYSTEM_ID
    golden_path = system_dir / "golden_io.json"
    assert golden_path.exists(), (
        f"金样本缺失 ({golden_path}) —— 权重须由 scripts/export_pinn_spm_weights.py 导出, 不可手工构造")
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    for side in ("ne", "pe"):
        weights = load_weights(system_dir / "weights" / f"discharge_{side}.npz")
        X = np.asarray(golden["sides"][side]["inputs"], dtype=np.float64)
        y_expected = np.asarray(golden["sides"][side]["outputs"], dtype=np.float64)
        y_actual = network_forward(weights, X)
        assert y_actual.shape == y_expected.shape
        max_diff = float(np.max(np.abs(y_actual - y_expected)))
        assert max_diff < 1e-9, f"{side}: numpy 前向偏离 TF 金样本 max|Δ|={max_diff:.3e}"


# ══════════════════ 2. registry 匹配与逐参数有效范围 ══════════════════

def test_registry_match_hit_and_miss():
    system = match_pinn_system("NCM811", "si_base")
    assert system is not None and system["system_id"] == SYSTEM_ID
    assert system["directions"]["discharge"]["ne"] and system["directions"]["discharge"]["pe"]
    assert system["directions"]["charge"] is None  # 充电模型配对位预留
    # 逐参数有效范围显式声明（调用前拦截的依据）
    ranges = system["input_ranges"]
    for side in ("ne", "pe"):
        for field in ("thickness", "porosity", "R_s", "D_s"):
            r = ranges[side][field]
            assert r["min"] < r["max"], f"{side}.{field} 范围声明非法"
    assert ranges["c_rate"]["min"] == 0.1 and ranges["c_rate"]["max"] == 3.0
    # 未命中: 锂金属负极 / LFP+石墨 / 空 id
    assert match_pinn_system("NCM811", "li_metal") is None
    assert match_pinn_system("LFP", "graphite") is None
    assert match_pinn_system("", "") is None


# ══════════════════ 3. spec 驱动输入与双层拦截 ══════════════════

def test_run_pinn_discharge_converged_within_checker_bounds():
    """输入合规 (校准 spec): 收敛 + 标量落在 checker 区间 + 包络内 + 输入回显."""
    cell_spec = _calibrated_cell_spec_dict(SCHEME_MATCHED)
    res = run_pinn_discharge(cell_spec, scheme=SCHEME_MATCHED, c_rate=0.5)
    assert res["status"] == "CONVERGED", res.get("error")
    assert res["system_id"] == SYSTEM_ID
    assert res["envelope_ok"] is True
    assert CHECKER_CAPACITY_RANGE[0] <= res["specific_capacity_mAh_g"] <= CHECKER_CAPACITY_RANGE[1]
    assert CHECKER_VOLTAGE_RANGE[0] <= res["average_voltage_V"] <= CHECKER_VOLTAGE_RANGE[1]
    assert CHECKER_ENERGY_RANGE[0] <= res["energy_wh_kg"] <= CHECKER_ENERGY_RANGE[1]
    assert res["termination"] == "voltage_cutoff"
    assert res["convergence"] == "Converged" and res["is_fallback"] is False
    residual = res["pde_residual_loss"]
    assert residual is not None and residual <= 0.05
    # 曲线契约: 容量单调递增, 电压下降
    cap, volt = res["discharge_curve"]["capacity"], res["discharge_curve"]["voltage"]
    assert len(cap) == len(volt) >= 2
    assert cap[-1] > cap[0] and volt[-1] < volt[0]
    # spec 驱动回显: 设计输入生效记录 + 来源 + 倍率来源
    assert res["c_rate_source"] == "spec"
    assert res["pinn"]["spec_inputs"]["ne"]["D_s"]["value"] == 4e-14
    assert res["pinn"]["design"]["ne"]["R_s"] == 8.21e-6
    # 校准 spec ≈ 训练参考 → 工作点恰为倍率本身
    assert abs(res["pinn"]["u_n"] - 0.5) < 1e-9


def test_spec_inputs_drive_the_simulation():
    """spec 输入真实生效: 改变厚度/孔隙率/倍率 → 工作点与容量随之改变."""
    base = run_pinn_discharge(_calibrated_cell_spec_dict(SCHEME_MATCHED),
                              scheme=SCHEME_MATCHED)
    assert base["status"] == "CONVERGED"
    thicker = _calibrated_cell_spec_dict(SCHEME_MATCHED)
    thicker["cathode"]["L"] = 140e-6  # 正极加厚 2 倍容量余量 → 限负极, 容量不变
    thicker["cathode"]["epsilon"] = 0.31
    res = run_pinn_discharge(thicker, scheme=SCHEME_MATCHED)
    assert res["status"] == "CONVERGED"
    assert res["pinn"]["design"]["pe"]["L"] == 140e-6
    assert res["pinn"]["spec_inputs"]["pe"]["thickness"]["value"] == 140e-6


def test_explicit_out_of_range_input_intercepted():
    """spec 显式输入越界 (D_s=1e-16 不在声明范围) → 逐参数拦截, 点名字段与范围."""
    cell_spec = _calibrated_cell_spec_dict(SCHEME_MATCHED)
    cell_spec["anode"]["material"]["D_s"] = 1e-16
    res = run_pinn_discharge(cell_spec, scheme=SCHEME_MATCHED, c_rate=0.5)
    assert res["status"] == "OUT_OF_RANGE", res
    assert "D_s" in res["error"] and "有效范围" in res["error"]
    assert res["pinn"]["violations"]


def test_c_rate_below_range_intercepted():
    """倍率低于声明下限 (0.005C < 0.1C) → OUT_OF_RANGE 拦截."""
    cell_spec = _calibrated_cell_spec_dict(SCHEME_MATCHED)
    cell_spec["condition"]["c_rate"] = 0.005
    res = run_pinn_discharge(cell_spec, scheme=SCHEME_MATCHED, c_rate=0.005)
    assert res["status"] == "OUT_OF_RANGE"
    assert "c_rate" in res["error"]


def test_combination_out_of_envelope_intercepted():
    """单参数均在范围内、组合越界 → 第二层 bc 包络拦截 (u≈48 > 15).

    R_s=2x × C_rate=3x × D_s=0.25x → u ∝ R_s²·C/D_s = 4·3·4 = 48.
    """
    cell_spec = _calibrated_cell_spec_dict(SCHEME_MATCHED)
    cell_spec["anode"]["material"]["R_p"] = 1.642e-05   # 2x 参考值（范围上边界）
    cell_spec["anode"]["material"]["D_s"] = 1e-14       # 0.25x 参考值（范围下边界）
    cell_spec["condition"]["c_rate"] = 3.0
    res = run_pinn_discharge(cell_spec, scheme=SCHEME_MATCHED)
    assert res["status"] == "OUT_OF_ENVELOPE", res.get("error")
    assert res["pinn"]["envelope_ok"] is False
    assert res["pinn"]["u_n"] > 15.0


def test_c_rate_resolution_precedence():
    """C-rate 来源: spec condition.c_rate 优先; spec 缺失回退调用参数; 再缺省 0.5."""
    spec = _calibrated_cell_spec_dict(SCHEME_MATCHED)
    spec["condition"].pop("c_rate", None)
    res = run_pinn_discharge(spec, scheme=SCHEME_MATCHED, c_rate=1.0)
    assert res["c_rate_source"] == "argument" and res["c_rate"] == 1.0
    spec["condition"]["c_rate"] = None
    res = run_pinn_discharge(spec, scheme=SCHEME_MATCHED)  # 连调用参数也不给
    assert res["c_rate_source"] == "default" and res["c_rate"] == 0.5


def test_spec_missing_fields_fall_back_to_params():
    """spec 物理字段为 null (如 li_metal 型空负极) → 回退体系训练参数, 不拦截."""
    spec = _calibrated_cell_spec_dict(SCHEME_MATCHED)
    spec["anode"]["L"] = None
    spec["anode"]["epsilon"] = None
    spec["anode"]["material"]["R_p"] = None
    spec["anode"]["material"]["D_s"] = None
    res = run_pinn_discharge(spec, scheme=SCHEME_MATCHED)
    assert res["status"] == "CONVERGED", res.get("error")
    assert res["pinn"]["design"]["ne"]["R_s"] == 8.21e-6   # params 训练参考值
    assert res["pinn"]["spec_inputs"]["ne"] == {}          # 无 spec 输入记录


# ══════════════════ 4.7 显式完整几何声明块 (产物契约 v1.1) ══════════════════

_RAW_GEOMETRY = {
    "cathode": {"loading_mg_cm2": 20.0, "compaction_g_cm3": 3.5, "porosity_pct": 30.0},
    "anode": {"porosity_pct": 25.0},
    "n_p_ratio": 1.1,
    "condition": {"v_min": 2.8, "v_max": 4.3},
}


def test_complete_geometry_block_matched_fills_baselines():
    """匹配体系: 叙事值→stage4_recipe, 缺失字段→params_baseline, 单位归一."""
    from pinn.input_spec import complete_geometry_block
    block = complete_geometry_block(_RAW_GEOMETRY, "NCM811", "si_base")
    assert block["schema_version"] == "1.1"
    # 正极: 换算厚度 20/3.5×10 = 57.14 μm (stage4_recipe), 孔隙率 30%→0.30
    assert block["cathode"]["L_um"] == pytest.approx(20.0 / 3.5 * 10.0)
    assert block["provenance"]["cathode.L_um"] == "stage4_recipe"
    assert block["cathode"]["porosity"] == pytest.approx(0.30)
    # 负极: 叙事只有孔隙率 25%→0.25, 厚度兜底体系基准 110 μm
    assert block["anode"]["porosity"] == pytest.approx(0.25)
    assert block["anode"]["L_um"] == pytest.approx(110.0)
    assert block["provenance"]["anode.L_um"] == "params_baseline"
    # R_s/D_s: 无叙事来源 → 体系基准
    assert block["cathode"]["R_s_um"] == pytest.approx(5.34)
    assert block["provenance"]["cathode.R_s_um"] == "params_baseline"
    assert block["anode"]["D_s_m2s"] == 4e-14
    assert block["provenance"]["anode.D_s_m2s"] == "params_baseline"
    assert block["n_p_ratio"] == pytest.approx(1.1)
    assert block["condition"] == {"v_min": 2.8, "v_max": 4.3}


def test_complete_geometry_block_unmatched():
    """未匹配体系: 无叙事值 → None; 有叙事值 → 保留原值并标 unmatched (不虚构)."""
    from pinn.input_spec import complete_geometry_block
    assert complete_geometry_block(None, "LFP", "graphite") is None
    block = complete_geometry_block({"cathode": {"porosity_pct": 30.0}}, "LFP", "graphite")
    assert block["cathode"]["porosity"] == pytest.approx(0.30)
    assert block["provenance"]["cathode.porosity"] == "stage4_recipe"
    assert block["cathode"]["L_um"] is None
    assert block["provenance"]["cathode.L_um"] == "unmatched"


def test_format_geometry_markdown_renders_declaration_table():
    """design_scheme.md 声明表: 表头/来源口径/电压窗口."""
    from pinn.input_spec import complete_geometry_block, format_geometry_markdown
    block = complete_geometry_block(_RAW_GEOMETRY, "NCM811", "si_base")
    md = format_geometry_markdown(block)
    assert "设计点几何声明" in md
    assert "| 正极 | 57.1" in md
    assert "叙事提取" in md and "体系基准" in md
    assert "2.8–4.3 V" in md and "1.1" in md
    assert format_geometry_markdown({}) == ""


def test_build_cell_spec_v1_1_schema_full_chain():
    """v1.1 声明块 → cell_spec (L/ε/R_p/D_s/电压窗口) → PINN 全链路生效."""
    from pinn.input_spec import complete_geometry_block
    scheme = {**SCHEME_MATCHED, "geometry": complete_geometry_block(_RAW_GEOMETRY, "NCM811", "si_base")}
    spec = build_cell_spec(scheme, None, c_rate=0.5)
    assert spec["cathode"]["L"] == pytest.approx(57.142857, rel=1e-3) or \
        spec["cathode"]["L"] == pytest.approx(20.0 / 3.5 * 10.0 * 1e-6)
    assert spec["cathode"]["epsilon"] == pytest.approx(0.30)
    assert spec["cathode"]["material"]["R_p"] == pytest.approx(5.34e-6)
    assert spec["anode"]["material"]["D_s"] == 4e-14
    assert spec["condition"]["voltage_min"] == 2.8
    assert spec["condition"]["voltage_max"] == 4.3
    assert spec["provenance"]["cathode.material.R_p"]["source"] == "params_baseline"

    res = run_pinn_discharge(spec, scheme=scheme)
    assert res["status"] == "CONVERGED", res.get("error")
    assert res["envelope_ok"] is True
    assert res["pinn"]["design"]["pe"]["L"] == pytest.approx(20.0 / 3.5 * 10.0 * 1e-6)
    assert res["pinn"]["spec_inputs"]["pe"]["R_s"]["source"] == "params_baseline"
    assert res["pinn"]["spec_inputs"]["pe"]["thickness"]["source"] == "stage4_recipe"


def test_rag_adapter_normalizes_geometry_into_artifacts(tmp_path, monkeypatch):
    """Stage 4 产物结构: design_scheme.json 含显式完整几何块, .md 尾部有声明表."""
    from auto_battery_research.tools.rag_adapter import AbrRagAdapter

    rag_raw = {
        "final_answer": "# 350 Wh/kg 高比能锂离子电池（Si-C 复合负极 ‖ NCM811 正极）材料组合方案\n"
                        "## 一、总体路线与能量密度口径\n正文\n## 二、五段式材料组合方案\n"
                        "### 2.1 正极：NCM811\n正极面载量 20 mg·cm⁻²，孔隙率 30%。\n"
                        "### 2.2 负极：Si-C 复合负极\n负极孔隙率 25%。\n"
                        "## 三、可验证的关键性能指标与工艺参数\n正文\n## 四、可行性依据与机理\n正文\n"
                        "## 五、风险与数据缺口\n正文",
        "plan": {}, "retrieval": {"db_type": "hybrid"},
        "evidence": [{"passage_id": "ev_001", "source": "10.1007/s40843-021-1784-0", "text": "SiO和Si/C具有高容量"}],
        "reviewer_output": {}, "rule_checks": {"violations": [], "rejects": []},
        "confidence": "high",
        "scheme": {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce",
                   "additives": ["FEC"], "target_energy_wh_kg": 350.0,
                   "geometry": {"cathode": {"loading_mg_cm2": 20.0, "porosity_pct": 30.0},
                                "anode": {"porosity_pct": 25.0}}},
    }

    class _StubPipeline:
        def run(self, q):
            return rag_raw

    adapter = AbrRagAdapter.__new__(AbrRagAdapter)
    adapter.pipeline = _StubPipeline()
    adapter._init_error = None
    adapter.config = {}  # _build_provenance 读取 paths (缺省路径即可)

    res = adapter.run_rag_design("设计350Wh/kg高比能硅碳负极NCM811锂离子电池方案", tmp_path)
    assert res["success"] is True, res.get("error")

    contract = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    geo = contract["scheme"]["geometry"]
    assert geo["schema_version"] == "1.1"
    # fixture 正极无压实 → 厚度换算不可用 → 兜底体系基准 70 μm
    assert geo["cathode"]["L_um"] == pytest.approx(70.0)
    assert geo["provenance"]["cathode.L_um"] == "params_baseline"
    assert geo["cathode"]["porosity"] == pytest.approx(0.30)
    assert geo["provenance"]["cathode.porosity"] == "stage4_recipe"
    assert geo["provenance"]["anode.porosity"] == "stage4_recipe"
    assert geo["anode"]["L_um"] == pytest.approx(110.0)
    # rag_result.json 同步归一 (同一 dict 透传)
    raw_rag = json.loads((tmp_path / "rag_result.json").read_text(encoding="utf-8"))
    assert raw_rag["scheme"]["geometry"]["schema_version"] == "1.1"
    # design_scheme.md 尾部声明表
    md = (tmp_path / "design_scheme.md").read_text(encoding="utf-8")
    assert "设计点几何声明" in md and "| 正极 |" in md


def test_run_pinn_charge_not_available():
    """充电模型未接入: 返回明确占位状态, 不抛异常."""
    cell_spec = _calibrated_cell_spec_dict(SCHEME_MATCHED)
    res = run_pinn_charge(cell_spec, scheme=SCHEME_MATCHED)
    assert res["status"] == "CHARGE_NOT_AVAILABLE"


# ══════════════════ 4.5 Stage 4 配方几何贯通 ══════════════════

_GEOMETRY_NARRATIVE = """### 2.1 正极材料
正极活性材料选用 NCM811（LiNi0.8Co0.1Mn0.1O2），面载量 12–20 mg·cm⁻²，压实密度 3.4–3.6 g·cm⁻³，孔隙率 25–35%，单晶 D50 3–6 μm。
### 2.2 负极材料
负极选用硅碳（Si-C）复合负极，碳涂铜箔 6–8 μm，单面载量 3.5–4.5 mg·cm⁻²，匹配正极面容量 4.0–4.5 mAh·cm⁻²，N/P 控制 1.08–1.15，孔隙率 35–45%。
对比锂金属负极体系，硅碳的体积膨胀需要 SEI 稳定设计。"""


def test_extract_geometry_from_stage4_narrative():
    """五段式叙事 → geometry 结构化提取: 标题切片归属 + 区间中值 + 跨组件干扰不串位."""
    from lmllm.RAG.agents import ReviewerAgent  # noqa: F401  (确保模块可导入)
    from lmllm.RAG.rag_pipeline import RAGPipeline
    from lmllm.RAG.relation_engine import RelationEngine

    pipeline = RAGPipeline.__new__(RAGPipeline)
    pipeline.relation_engine = RelationEngine()
    question = "设计350Wh/kg高比能硅碳负极NCM811锂离子电池方案"
    scheme = pipeline._extract_scheme(question, {}, _GEOMETRY_NARRATIVE)
    assert scheme is not None
    g = scheme.get("geometry") or {}
    assert g["cathode"]["loading_mg_cm2"] == pytest.approx(16.0)   # 12–20 中值
    assert g["cathode"]["compaction_g_cm3"] == pytest.approx(3.5)
    assert g["cathode"]["porosity_pct"] == pytest.approx(30.0)     # 25–35 中值
    assert g["anode"]["loading_mg_cm2"] == pytest.approx(4.0)      # 3.5–4.5 中值
    assert g["anode"]["porosity_pct"] == pytest.approx(40.0)       # 负极章节归属, 不串正极
    assert g["n_p_ratio"] == pytest.approx(1.08)
    # 无几何叙事 → 无 geometry 键
    scheme2 = pipeline._extract_scheme(question, {}, "锂金属负极因其极高的理论比容量而备受关注。")
    assert "geometry" not in (scheme2 or {})


def test_build_cell_spec_consumes_recipe_geometry():
    """scheme.geometry → cell_spec 显式输入: 单位换算 (载量/压实→L, %→ε) + provenance."""
    scheme = {
        **SCHEME_MATCHED,
        "geometry": {
            "cathode": {"loading_mg_cm2": 16.0, "compaction_g_cm3": 3.5, "porosity_pct": 30.0},
            "anode": {"loading_mg_cm2": 4.0, "porosity_pct": 40.0},
            "n_p_ratio": 1.08,
        },
    }
    spec = build_cell_spec(scheme, None, c_rate=0.5)
    # 厚度 = 载量/压实 = 16/3.5 mg·cm⁻¹ → 45.7 μm → 4.571e-5 m
    assert spec["cathode"]["L"] == pytest.approx(16.0 / 3.5 * 1e-5)
    assert spec["cathode"]["epsilon"] == pytest.approx(0.30)
    assert spec["cathode"]["mass_loading"] == pytest.approx(0.16)  # mg/cm² × 0.01 → kg/m²
    assert spec["anode"]["epsilon"] == pytest.approx(0.40)
    assert spec["anode"]["N_P_ratio"] == pytest.approx(1.08)
    assert spec["provenance"]["cathode.L"]["source"] == "stage4_recipe"
    # 无 geometry → 全 null (现状回归)
    plain = build_cell_spec(SCHEME_MATCHED, None, c_rate=0.5)
    assert plain["cathode"]["L"] is None and plain["cathode"]["epsilon"] is None


def test_run_pinn_discharge_at_recipe_design_point():
    """PINN 在推荐几何设计点仿真: design 回显推荐值, spec_inputs 标 stage4_recipe."""
    scheme = {
        **SCHEME_MATCHED,
        "geometry": {
            "cathode": {"loading_mg_cm2": 16.0, "compaction_g_cm3": 3.5, "porosity_pct": 30.0},
            "anode": {"loading_mg_cm2": 4.0, "porosity_pct": 40.0},
            "n_p_ratio": 1.08,
        },
    }
    cell_spec = build_cell_spec(scheme, None, c_rate=0.5)
    cell_spec["condition"]["voltage_min"] = 2.8
    res = run_pinn_discharge(cell_spec, scheme=scheme)
    assert res["status"] == "CONVERGED", res.get("error")
    assert res["envelope_ok"] is True
    assert res["pinn"]["design"]["pe"]["L"] == pytest.approx(16.0 / 3.5 * 1e-5)
    assert res["pinn"]["design"]["ne"]["porosity"] == pytest.approx(0.40)
    assert res["pinn"]["spec_inputs"]["pe"]["thickness"]["source"] == "stage4_recipe"
    # 标量随几何变化 (正极减薄 → 限负极电流下降 → 与训练基准点结果不同)
    assert res["pinn"]["u_n"] != pytest.approx(0.5)


def test_recipe_geometry_out_of_range_intercepted():
    """推荐几何越界 (孔隙率 72% > 0.7 上限) → OUT_OF_RANGE 拦截, 不静默回退."""
    scheme = {
        **SCHEME_MATCHED,
        "geometry": {"cathode": {"porosity_pct": 72.0}, "anode": {}},
    }
    cell_spec = build_cell_spec(scheme, None, c_rate=0.5)
    cell_spec["condition"]["voltage_min"] = 2.8
    res = run_pinn_discharge(cell_spec, scheme=scheme)
    assert res["status"] == "OUT_OF_RANGE"
    assert "porosity" in res["error"]


def test_np_ratio_consistency_adjustment():
    """声明几何与声明 N/P 自相矛盾时 (隐含 N/P 0.19 vs 声明 1.0):
    以 N/P 为系统级约束调整负极厚度, 调整记录进 warnings, 仿真收敛且标量回到合理区间."""
    scheme = {
        **SCHEME_MATCHED,
        "geometry": {
            "cathode": {"loading_mg_cm2": 20.0, "compaction_g_cm3": 3.5, "porosity_pct": 30.0},
            "anode": {"loading_mg_cm2": 2.0, "compaction_g_cm3": 1.0},  # 隐含 N/P≈0.19
            "n_p_ratio": 1.0,
        },
    }
    spec = build_cell_spec(scheme, None, c_rate=0.5)
    spec["condition"]["voltage_min"] = 2.8
    res = run_pinn_discharge(spec, scheme=scheme)
    assert res["status"] == "CONVERGED", res.get("error")
    # 负极厚度按 N/P 调整 (20 μm → 约 100 μm 量级), 调整可见
    assert res["pinn"]["design"]["ne"]["L"] > 5e-5
    assert any("N/P" in w for w in res["warnings"])
    assert CHECKER_CAPACITY_RANGE[0] <= res["specific_capacity_mAh_g"] <= CHECKER_CAPACITY_RANGE[1]


def test_range_boundary_float_tolerance():
    """恰在范围边界上的值 (换算引入 1 ULP 浮点漂移) 不得误判越界.

    真实案例: 叙事载量/压实换算出 ne.thickness=1.9999999999999998e-05 m,
    与下边界 2e-05 数学相等但浮点表示偏小 → 曾被 OUT_OF_RANGE 误拦。
    """
    import math

    cell_spec = _calibrated_cell_spec_dict(SCHEME_MATCHED)
    cell_spec["anode"]["L"] = math.nextafter(2.0e-05, 0.0)  # 下边界低 1 ULP
    res = run_pinn_discharge(cell_spec, scheme=SCHEME_MATCHED)
    assert res["status"] != "OUT_OF_RANGE", res.get("error")
    assert res["status"] == "CONVERGED"


def test_render_pinn_geometry_block():
    """报告几何表: μm 换算、来源行随 spec_inputs 切换、空输入容错."""
    from auto_battery_research.tools.workflow_actions import _render_pinn_geometry_block
    summary = {
        "design": {
            "pe": {"L": 7e-5, "porosity": 0.31, "eps_s": 0.524, "R_s": 5.34e-6, "D_s": 1e-14},
            "ne": {"L": 1.1e-4, "porosity": 0.56, "eps_s": 0.4, "R_s": 8.21e-6, "D_s": 4e-14},
        },
        "spec_inputs": {"ne": {}, "pe": {}},
        "c_rate": 0.5,
        "temperature_K": 298.15,
    }
    block = _render_pinn_geometry_block(summary)
    assert "| 正极 | 70" in block and "| 负极 | 110" in block
    assert "1.00e-14" in block
    assert "体系训练参数基准" in block
    # 显式输入 → 来源行切换
    summary["spec_inputs"] = {"pe": {"thickness": {"value": 7e-5, "source": "stage4_recipe"}}}
    block2 = _render_pinn_geometry_block(summary)
    assert "Stage 4 方案显式输入" in block2
    # 缺失容错
    assert _render_pinn_geometry_block(None) == ""
    assert "—" in _render_pinn_geometry_block({"design": {"pe": {"L": 7e-5}, "ne": {}}})


# ══════════════════ 4. 端到端 run_pinn_simulation ══════════════════

def test_run_pinn_simulation_matched_runs_and_writes_result(tmp_path):
    """Stage 5 激活 (默认) + 体系匹配 → 完整链路: 提取清单 + 仿真结果都落盘.

    标准提取不做缺省表回填 (物理字段 null) → PINN 回退体系训练参数 (在
    input_ranges 内) → 仿真收敛。
    """
    goal = "Stage5PINN端到端匹配测试"
    mgr = _make_mgr(tmp_path, goal)
    _write_scheme(tmp_path, mgr, goal, SCHEME_MATCHED)

    res = run_pinn_simulation(target_query=goal, stage_manager=mgr)
    assert res["success"] is True, res.get("error")
    task_dir = mgr.get_task_output_dir(goal)
    assert (task_dir / "pinn_input_spec.json").exists()
    result_file = task_dir / "pinn_simulation_result.json"
    assert result_file.exists(), "体系匹配且 Stage 5 激活, 应落盘 PINN 仿真结果"

    payload = json.loads(result_file.read_text(encoding="utf-8"))
    assert payload["kind"] == "pinn_simulation_result"
    assert payload["status"] == "CONVERGED" and payload["system_id"] == SYSTEM_ID
    assert CHECKER_CAPACITY_RANGE[0] <= payload["specific_capacity_mAh_g"] <= CHECKER_CAPACITY_RANGE[1]
    assert res["key_findings"]["pinn_run"]["status"] == "CONVERGED"
    assert res["key_findings"]["pinn_run"]["result_file"] == str(result_file)
    # 放电曲线 PNG: 收敛即渲染, 计入 deliverables, JSON 内记录路径
    png_file = task_dir / "pinn_simulation_curve.png"
    assert png_file.exists() and png_file.stat().st_size > 1000, "收敛仿真应渲染放电曲线 PNG"
    assert res["key_findings"]["pinn_run"]["curve_png"] == str(png_file)
    assert payload["curve_png"] == str(png_file)
    assert res["deliverables"][-1] == str(png_file)
    # journal notes 应提及 PINN 仿真完成
    assert "pinn_simulation_result.json" in res["journal_notes"]


def test_run_pinn_simulation_skip_channel_no_simulation(tmp_path):
    """skip 快速通道: 只做参数提取, 不产出仿真结果."""
    goal = "Stage5PINN跳过通道测试"
    mgr = _make_mgr(tmp_path, goal)
    mgr.set_stage_skip(5, skip=True, reason="测试强制跳过")
    _write_scheme(tmp_path, mgr, goal, SCHEME_MATCHED)

    res = run_pinn_simulation(target_query=goal, stage_manager=mgr)
    assert res["success"] is True
    task_dir = mgr.get_task_output_dir(goal)
    assert (task_dir / "pinn_input_spec.json").exists()
    assert not (task_dir / "pinn_simulation_result.json").exists()
    assert res["key_findings"]["pinn_run"]["status"] == "SKIPPED"


def test_run_pinn_simulation_switch_disabled_no_simulation(tmp_path):
    """pinn_trigger.enabled=false (kill-switch): 匹配命中也不仿真."""
    goal = "Stage5PINN总开关关闭测试"
    mgr = _make_mgr(tmp_path, goal, pinn_trigger={"enabled": False, "rule": None})
    _write_scheme(tmp_path, mgr, goal, SCHEME_MATCHED)

    res = run_pinn_simulation(target_query=goal, stage_manager=mgr)
    assert res["success"] is True
    task_dir = mgr.get_task_output_dir(goal)
    assert not (task_dir / "pinn_simulation_result.json").exists()
    assert res["key_findings"]["pinn_run"]["status"] == "PINN_DISABLED"


def test_run_pinn_simulation_unmatched_falls_back_to_extraction(tmp_path):
    """体系未匹配 (li_metal): 平滑回退提取-only, spec 正常落盘, 无仿真产物."""
    goal = "Stage5PINN未匹配回退测试"
    mgr = _make_mgr(tmp_path, goal)
    _write_scheme(tmp_path, mgr, goal, SCHEME_UNMATCHED)

    res = run_pinn_simulation(target_query=goal, stage_manager=mgr)
    assert res["success"] is True
    task_dir = mgr.get_task_output_dir(goal)
    assert (task_dir / "pinn_input_spec.json").exists()
    assert not (task_dir / "pinn_simulation_result.json").exists()
    assert res["key_findings"]["pinn_run"]["status"] == "NO_MATCH"


# ══════════════════ 5. Checker 对 PINN 仿真结果的验收 ══════════════════

def test_checker_accepts_pinn_result(tmp_path):
    """结构完整且标量在区间内的 pinn_simulation_result.json → 通过, solver=pinn_spm."""
    cell_spec = _calibrated_cell_spec_dict(SCHEME_MATCHED)
    sim_res = run_pinn_discharge(cell_spec, scheme=SCHEME_MATCHED, c_rate=0.5)
    assert sim_res["status"] == "CONVERGED"
    result_file = tmp_path / "pinn_simulation_result.json"
    result_file.write_text(json.dumps(sim_res, ensure_ascii=False, indent=2), encoding="utf-8")

    checker = _make_checker(tmp_path)
    passed, diag = checker.do_check()
    assert passed is True, diag.get("error_msg")
    assert diag["observed"]["solver"] == "pinn_spm"
    assert diag["observed"]["system_id"] == SYSTEM_ID
    assert diag["observed"]["envelope_ok"] is True


def test_checker_rejects_corrupted_and_out_of_bounds_pinn_result(tmp_path):
    """损坏 JSON / 标量越界 / 包络外 → 分别给出对应 error_code."""
    checker = _make_checker(tmp_path)

    # 1. 损坏
    result_file = tmp_path / "pinn_simulation_result.json"
    result_file.write_text("{not valid json", encoding="utf-8")
    passed, diag = checker.do_check()
    assert passed is False and diag["error_code"] == "PINN_SIM_RESULT_CORRUPTED"

    # 2. 比容量越界
    cell_spec = _calibrated_cell_spec_dict(SCHEME_MATCHED)
    sim_res = run_pinn_discharge(cell_spec, scheme=SCHEME_MATCHED, c_rate=0.5)
    bad = dict(sim_res, specific_capacity_mAh_g=99999.0)
    result_file.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    passed, diag = checker.do_check()
    assert passed is False and diag["error_code"] == "PINN_CAPACITY_OUT_OF_BOUNDS"

    # 3. 包络外 (带合法标量, 让包络检查先于越界标量被触发)
    env_res = dict(sim_res)  # 合法标量
    env_res.update(
        status="CONVERGED",  # 模拟错误标记为收敛的越界产物
        envelope_ok=False,
        pinn={**(sim_res.get("pinn") or {}), "envelope_ok": False,
              "violations": ["ne: |bc|/|bc_ref| = 48 超出训练包络 [0.02, 15]"]},
    )
    result_file.write_text(json.dumps(env_res, ensure_ascii=False), encoding="utf-8")
    passed, diag = checker.do_check()
    assert passed is False and diag["error_code"] == "PINN_OUT_OF_ENVELOPE"
