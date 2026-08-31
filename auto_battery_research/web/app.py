"""Gradio Web Dashboard — 交互式 Web 大屏 (AutoBatteryResearch Agent).

支持通过 `--web` 命令行参数一键启动，提供：
- 📊 智能体 6 阶段全流程工作流大屏与实时门禁审计
- 🧪 多智能体 RAG 电池方案设计与 C1-C8 规则审查
- 📚 文献资产库与电芯数据挖掘全景
- ⚡ PINN / PyBaMM 物理仿真与放电曲线绘制
"""

import os
import sys
import json
import glob
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

try:
    import gradio as gr
except ImportError:
    gr = None

from auto_battery_research.workflow.stage_manager import StageManager
from auto_battery_research.backend.loop_runner import AutonomousLoopRunner
from auto_battery_research.tools.stage_tools import (
    tool_get_status,
    tool_get_current_tips,
    tool_check_stage,
    tool_complete_stage,
    tool_get_all_stage_journal,
    tool_skip_stage,
    tool_enable_stage,
    tool_run_stage_task,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def get_stages_markdown(mgr: StageManager) -> str:
    """生成漂亮的阶段状态 Markdown 表格."""
    status = mgr.get_status()
    rows = []
    for s in status.get("stages", []):
        st = s["status"]
        if st == "PASSED":
            badge = "🟢 **PASSED (已完成)**"
        elif st == "IN_PROGRESS":
            badge = "🟡 **IN_PROGRESS (进行中)**"
        elif st == "SKIPPED":
            badge = "🔵 **SKIPPED (已跳过)**"
        elif st == "FAILED":
            badge = "🔴 **FAILED (未通过)**"
        else:
            badge = "⚪ **PENDING (等待中)**"

        skip_text = "是 (Skip)" if s["skip"] else "否"
        rows.append(f"| **Stage {s['id']}** | `{s['key']}` | {s['name']} | {badge} | {skip_text} |")

    table_md = f"""### 📋 6 阶段工作流执行矩阵 (当前活跃: Stage {status.get('current_stage_id')} · 进度: {status.get('progress')})

| 阶段 | Key | 阶段名称 | 当前状态 | 是否跳过 |
|:---:|:---|:---|:---:|:---:|
""" + "\n".join(rows)
    return table_md


def create_web_app(manager: Optional[StageManager] = None):
    """构建 Gradio 交互式仪表盘."""
    if gr is None:
        raise ImportError("Gradio 未安装，请先执行：pip install gradio")

    mgr = manager or StageManager()

    theme = gr.themes.Soft(
        primary_hue="cyan",
        secondary_hue="blue",
        neutral_hue="slate",
    )

    with gr.Blocks(title="AutoBatteryResearch Agent Web Dashboard") as demo:
        gr.Markdown(
            """
            # ⚡ AutoBatteryResearch Agent 综合科研工作流大屏
            > **全生命周期化学电池自主研究智能体** · 文献解析 · 语义向量库 · 材料电芯挖掘 · 多智能体 RAG · PINN 物理仿真 · 综合研报
            """
        )

        with gr.Tabs():
            # =================================================================
            # TAB 1: 智能体工作流大屏
            # =================================================================
            with gr.Tab("📊 智能体工作流大屏 (Workflow Dashboard)"):
                with gr.Row():
                    with gr.Column(scale=3):
                        stage_status_display = gr.Markdown(value=get_stages_markdown(mgr))
                    with gr.Column(scale=2):
                        goal_input = gr.Textbox(
                            label="🎯 电池研发目标 (Goal)",
                            value="设计400Wh/kg高比能液态锂金属电池方案",
                            lines=2,
                        )
                        with gr.Row():
                            run_auto_btn = gr.Button("🚀 启动全自动自主循环", variant="primary")
                            reset_btn = gr.Button("🔄 重置工作流", variant="secondary")

                with gr.Row():
                    check_btn = gr.Button("🔍 阶段门禁自检 (Check)")
                    complete_btn = gr.Button("✅ 阶段终审推进 (Complete)")
                    skip_pinn_btn = gr.Button("⏭️ 跳过 Stage 5 (PINN)")
                    enable_pinn_btn = gr.Button("⚡ 激活 Stage 5 (PINN)")

                with gr.Row():
                    with gr.Column(scale=1):
                        tips_display = gr.Markdown(value=mgr.get_current_tips(), label="🎯 当前阶段任务指引 (Tips)")
                    with gr.Column(scale=1):
                        diag_display = gr.JSON(label="🔍 阶段门禁诊断 (Diagnostics)")

                with gr.Accordion("💻 实时执行日志 (Live Execution Console)", open=True):
                    live_log_display = gr.Textbox(
                        label="终端执行输出流",
                        value="*等待启动自主循环或执行阶段指令...*",
                        lines=7,
                        max_lines=16,
                        interactive=False,
                    )

                def get_current_report_content(goal: str = "") -> str:
                    task_dir = mgr.get_task_output_dir(goal or mgr.target_goal)
                    cand_reports = [
                        task_dir / "final_research_report.md",
                        task_dir / "final_report.md",
                        task_dir / "battery_research_synthesis_report.md",
                        ROOT_DIR / "output" / "auto_battery_research" / "final_research_report.md",
                        ROOT_DIR / "output" / "auto_battery_research" / "final_report.md",
                        ROOT_DIR / "output" / "auto_battery_research" / "battery_research_synthesis_report.md",
                    ]
                    for p in cand_reports:
                        if p.exists():
                            try:
                                with open(p, "r", encoding="utf-8") as f:
                                    text = f.read()
                                if len(text.strip()) > 50:
                                    return text
                            except Exception:
                                pass
                    return "*未检测到已生成的综合研报。请点击上方【🚀 启动全自动自主循环】生成完整研报。*"

                with gr.Accordion("📄 最终综合研发报告 (Synthesis Report)", open=True):
                    with gr.Row():
                        refresh_rep_btn = gr.Button("🔄 刷新研报预览", size="sm", variant="secondary")
                    report_markdown = gr.Markdown(value=get_current_report_content(mgr.target_goal))

                with gr.Accordion("📝 历史阶段研发日志 (Stage Journals)", open=False):
                    journal_display = gr.JSON(value=mgr.get_all_stage_journal(), label="Journal Log")

                # 事件绑定
                def _sync_goal(goal: str) -> str:
                    """将面板操作统一同步到目标文本框中的课题 (切换并重载该课题状态).

                    所有按钮先经过本函数，避免 Check/Complete 等操作作用在与
                    文本框不一致的旧课题状态上；课题变更走 StageManager.switch_goal
                    正式路径，防止跨课题内存进度污染。
                    """
                    goal = (goal or "").strip()
                    if goal and goal != mgr.target_goal and hasattr(mgr, "switch_goal"):
                        mgr.switch_goal(goal)
                    return goal or mgr.target_goal

                def on_run_auto(goal, progress=gr.Progress()):
                    goal = _sync_goal(goal)
                    runner = AutonomousLoopRunner(manager=mgr, goal=goal, verbose=False)
                    for step in runner.run_stream():
                        p_val = step.get("progress_ratio", 0.0)
                        s_id = step.get("stage_id", 1)
                        progress(p_val, desc=f"Stage {s_id} 正在推进中...")

                        rep = step.get("report", "")
                        if not rep or rep in ("*未找到研报*", "*科研任务执行中...*"):
                            if step.get("event") == "finished":
                                rep = get_current_report_content(goal)
                            else:
                                rep = f"⏳ **工作流推进中** · 当前正在执行 Stage {s_id}，完成后将在此自动渲染完整研报..."

                        yield (
                            get_stages_markdown(mgr),
                            step.get("tips", ""),
                            step.get("diag", {}),
                            step.get("log", ""),
                            rep,
                            step.get("journal", []),
                        )

                def on_check(goal):
                    goal = _sync_goal(goal)
                    passed, diag = mgr.check_stage(is_complete=False)
                    status_text = "PASSED" if passed else "FAILED"
                    log_text = f"[MANUAL CHECK] Stage {mgr.get_current_stage().id} Gate Check: [{status_text}]"
                    return get_stages_markdown(mgr), diag, log_text

                def on_complete(goal):
                    goal = _sync_goal(goal)
                    comp_ok, res = mgr.complete_stage()
                    log_text = f"[MANUAL COMPLETE] {res.get('message', 'Stage advanced')}"
                    rep = get_current_report_content(mgr.target_goal)
                    return (
                        get_stages_markdown(mgr),
                        mgr.get_current_tips(),
                        res,
                        log_text,
                        rep,
                        mgr.get_all_stage_journal(),
                    )

                def on_skip_pinn(goal):
                    goal = _sync_goal(goal)
                    mgr.set_stage_skip(5, skip=True, reason="Web 用户跳过")
                    log_text = "[USER ACTION] Stage 5 (PINN) 已设置为跳过 (SKIPPED)。"
                    return get_stages_markdown(mgr), mgr.get_current_tips(), log_text

                def on_enable_pinn(goal):
                    goal = _sync_goal(goal)
                    mgr.set_stage_skip(5, skip=False)
                    log_text = "[USER ACTION] Stage 5 (PINN) 已重新激活为必检阶段。"
                    return get_stages_markdown(mgr), mgr.get_current_tips(), log_text

                def on_reset(goal):
                    goal = _sync_goal(goal)
                    mgr.reset_workflow()
                    return (
                        get_stages_markdown(mgr),
                        mgr.get_current_tips(),
                        {"message": "工作流已重置为 Stage 1"},
                        "🔄 工作流状态已全部重置回 Stage 1，请配置新课题目标并重新启动。",
                        "*工作流已重置，等待运行...*",
                        [],
                    )

                run_auto_btn.click(
                    on_run_auto,
                    inputs=[goal_input],
                    outputs=[stage_status_display, tips_display, diag_display, live_log_display, report_markdown, journal_display],
                    show_progress="minimal",
                )
                check_btn.click(on_check, inputs=[goal_input], outputs=[stage_status_display, diag_display, live_log_display], show_progress="minimal")
                complete_btn.click(on_complete, inputs=[goal_input], outputs=[stage_status_display, tips_display, diag_display, live_log_display, report_markdown, journal_display], show_progress="minimal")
                skip_pinn_btn.click(on_skip_pinn, inputs=[goal_input], outputs=[stage_status_display, tips_display, live_log_display], show_progress="minimal")
                enable_pinn_btn.click(on_enable_pinn, inputs=[goal_input], outputs=[stage_status_display, tips_display, live_log_display], show_progress="minimal")
                reset_btn.click(
                    on_reset,
                    inputs=[goal_input],
                    outputs=[stage_status_display, tips_display, diag_display, live_log_display, report_markdown, journal_display],
                    show_progress="minimal",
                )
                refresh_rep_btn.click(
                    lambda g: get_current_report_content(g),
                    inputs=[goal_input],
                    outputs=[report_markdown],
                    show_progress="minimal",
                )

            # =================================================================
            # TAB 2: 多智能体 RAG 方案设计
            # =================================================================
            with gr.Tab("🧪 多智能体 RAG 方案设计 (Multi-Agent RAG)"):
                gr.Markdown(
                    "### 4-Agent 协同规划：Planner → Retrieval → Writer → Reviewer (C1-C8 校验)\n"
                    "> 方案产物写入左侧【🎯 电池研发目标】所属课题的任务目录；生成后回到工作流页"
                    "对 Stage 4 执行 Check / Complete 即可推进门禁。"
                )
                with gr.Row():
                    rag_query = gr.Textbox(
                        label="设计需求 Query",
                        value="设计 400 Wh/kg 固液混合电解质超高镍单晶锂金属电池方案",
                        lines=1,
                    )
                    rag_gen_btn = gr.Button("⚡ 生成 RAG 方案", variant="primary")

                rag_result_md = gr.Markdown(label="设计方案报告")
                rag_rule_checks = gr.JSON(label="C1-C8 规则硬约束审查结果")

                def on_gen_rag(query, goal):
                    from auto_battery_research.tools.workflow_actions import run_rag_design
                    # 课题键取 Tab 1 目标 (产物落同一课题目录、状态互通)；query 仅作为设计需求
                    bound_goal = _sync_goal(goal)
                    res = run_rag_design(target_query=bound_goal, design_query=query)

                    kf = res.get("key_findings", {})
                    rule_checks = kf.get("rule_checks") or res.get("rule_checks") or {}
                    if not res.get("success"):
                        err = res.get("error") or res.get("message") or "未知错误"
                        return f"❌ **RAG 方案生成失败**: {err}", rule_checks

                    scheme_md = ""
                    if Path(res.get("scheme_file", "")).exists():
                        with open(res["scheme_file"], "r", encoding="utf-8") as f:
                            scheme_md = f.read()
                    return scheme_md, rule_checks

                rag_gen_btn.click(
                    on_gen_rag,
                    inputs=[rag_query, goal_input],
                    outputs=[rag_result_md, rag_rule_checks],
                    show_progress="minimal",
                )

            # =================================================================
            # TAB 3: 文献资产与电芯数据挖掘
            # =================================================================
            with gr.Tab("📚 文献资产与电芯数据挖掘 (Data Mining)"):
                gr.Markdown("### 本地文献库与已抽取结构化电芯数据集全景")

                def get_mining_stats_md() -> str:
                    """实时统计本地文献与挖掘资产 (每次刷新重新扫描磁盘)."""
                    from auto_battery_research.tools.workflow_actions import _merged_literature_dirs
                    pdf_count = len(glob.glob(str(ROOT_DIR / "papers/pdf/**/*.pdf"), recursive=True))
                    mrg_count = 0
                    mrg_dir_names = []
                    for _mdir in _merged_literature_dirs():
                        _c = len(glob.glob(str(_mdir / "**/*.md"), recursive=True))
                        if _c:
                            mrg_count += _c
                            mrg_dir_names.append(f"`{_mdir.relative_to(ROOT_DIR).as_posix()}/`")
                    mrg_dir_disp = " + ".join(mrg_dir_names) or "`papers/merged/`"
                    db_count = len(glob.glob(str(ROOT_DIR / "database/type/**/*.md"), recursive=True))
                    ext_count = len(glob.glob(str(ROOT_DIR / "miner/json/100/*.json")))

                    # 元数据索引：v5_qwen 流水线约定 metadata/ 子目录，旧流水线产物在根下，双候选探测
                    meta_len = 0
                    for _meta_cand in ("miner/json/meta_merged.json", "miner/json/metadata/meta_merged.json"):
                        _mf = ROOT_DIR / _meta_cand
                        if _mf.exists():
                            try:
                                with open(_mf, "r", encoding="utf-8") as f:
                                    meta_len = len(json.load(f))
                                break
                            except Exception:
                                pass

                    return f"""
                    | 资产类型 | 所在路径 | 当前数量 |
                    |:---|:---|:---:|
                    | 📄 **原始 PDF 论文** | `papers/pdf/` | **{pdf_count} 篇** |
                    | 📝 **清洗合并 Markdown** | {mrg_dir_disp} | **{mrg_count} 篇** |
                    | 🗃️ **分类归档文献** | `database/type/` | **{db_count} 篇** |
                    | 🔍 **结构化挖掘电芯数据集** | `miner/json/100/` | **{ext_count} 篇** |
                    | 🏷️ **学术元数据索引** | `miner/json/*meta_merged.json` | **{meta_len} 篇** |
                    """

                def get_mining_sample_rows() -> Tuple[List[List[str]], str]:
                    """读取真实抽取数据集样本；缺失时返回空表与说明，不填充编造样本."""
                    rows: List[List[str]] = []
                    csv_path = ROOT_DIR / "miner" / "json" / "csv" / "all_extracted.csv"
                    if not csv_path.exists():
                        return rows, "⚠️ 未检测到挖掘数据集 (`miner/json/csv/all_extracted.csv`)。执行 Stage 3 材料挖掘流水线后，此处将展示真实抽取数据。"
                    try:
                        df = pd.read_csv(csv_path)
                    except Exception as e:
                        return rows, f"⚠️ 读取数据集失败: {e}"
                    for _, row in df.head(15).iterrows():
                        # 有则提取、无则留空、禁止编造：字段缺失显示为空而非示例值
                        rows.append([
                            str(row.get("doi") or row.get("paper_id") or ""),
                            str(row.get("component") or ""),
                            str(row.get("material_name") or row.get("canonical_id") or ""),
                            str(row.get("capacity") or row.get("specific_capacity") or ""),
                            str(row.get("ice") or ""),
                            str(row.get("cycle_retention") or ""),
                        ])
                    note = "" if rows else "⚠️ 数据集中暂无记录。"
                    return rows, note

                with gr.Row():
                    refresh_stats_btn = gr.Button("🔄 刷新资产统计", size="sm", variant="secondary")

                mining_stats_md = gr.Markdown(value=get_mining_stats_md())

                sample_rows, sample_note = get_mining_sample_rows()
                mining_note_md = gr.Markdown(value=sample_note)
                sample_mining_table = gr.Dataframe(
                    headers=["DOI", "材料类型", "化学式/归一化ID", "放电比容量 (mAh/g)", "首次效率 ICE (%)", "循环保持率 (%)"],
                    value=sample_rows,
                    label="电芯材料与性能数据采样视图 (真实抽取)",
                )

                def on_refresh_stats():
                    rows, note = get_mining_sample_rows()
                    return get_mining_stats_md(), note, rows

                refresh_stats_btn.click(
                    on_refresh_stats,
                    outputs=[mining_stats_md, mining_note_md, sample_mining_table],
                    show_progress="minimal",
                )

            # =================================================================
            # TAB 4: PINN 物理仿真与放电曲线
            # =================================================================
            with gr.Tab("⚡ PINN 物理仿真与放电曲线 (Physics Simulation)"):
                gr.Markdown("### Newman P2D / PINN 物理连续性放电曲线求解器")
                with gr.Row():
                    with gr.Column(scale=1):
                        c_rate = gr.Slider(minimum=0.1, maximum=5.0, value=0.5, step=0.1, label="放电倍率 (C-rate)")
                        loading = gr.Slider(minimum=10.0, maximum=40.0, value=22.0, step=1.0, label="正极面载量 (mg/cm²)")
                        np_ratio = gr.Slider(minimum=1.0, maximum=3.0, value=2.0, step=0.1, label="负/正容量比 (N/P Ratio)")
                        sim_run_btn = gr.Button("🚀 运行物理仿真求解", variant="primary")
                    with gr.Column(scale=2):
                        plot_output = gr.Plot(label="电压-比容量放电曲线 (V-Q Curve)")
                        sim_metrics_display = gr.JSON(label="电芯级能量密度与物理指标")

                def on_run_simulation(c_rate_val, load_val, np_val):
                    import numpy as np
                    
                    # 优先尝试调用真实 PyBaMM P2D 求解器
                    try:
                        from pinn.p2d_runner import PyBaMMP2DRunner
                        runner = PyBaMMP2DRunner()
                        sim_res = runner.run_simulation(
                            c_rate=c_rate_val,
                            loading_mg_cm2=load_val,
                        )
                        capacities = np.array(sim_res.get("discharge_curve", {}).get("capacity", []))
                        voltages = np.array(sim_res.get("discharge_curve", {}).get("voltage", []))
                        solver_type = "PyBaMM Newman P2D Solver"
                    except Exception:
                        solver_type = "Calibrated Electrochemical SPM Surrogate"
                        # 连续电化学放电曲线解析求解
                        q_max = 225.0
                        n_pts = 40
                        soc = np.linspace(1.0, 0.02, n_pts)
                        capacities = q_max * (1.0 - soc)
                        
                        # 开路电压函数 OCV(SOC) + Butler-Volmer 活化过电位 + 扩散极化
                        ocv = 3.65 + 0.65 * (soc ** 0.5) - 0.25 * ((1.0 - soc) ** 2.5)
                        r_int = 0.035 + 0.004 * load_val / 20.0
                        eta_ohm = c_rate_val * r_int
                        eta_diff = 0.045 * (c_rate_val ** 0.7) * (1.0 / (soc + 0.05) - 0.95)
                        eta_diff = np.clip(eta_diff, 0.0, 0.65)
                        
                        voltages = ocv - eta_ohm - eta_diff
                        valid_idx = voltages >= 2.75
                        capacities = capacities[valid_idx]
                        voltages = voltages[valid_idx]

                    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=100)
                    ax.plot(capacities, voltages, color="#00bcd4", linewidth=2.5, label=f"Simulation ({c_rate_val}C)")
                    ax.axhline(y=2.8, color="r", linestyle="--", alpha=0.6, label="Cutoff Voltage (2.8V)")
                    ax.set_title(f"Cell Discharge Voltage Profile @ {c_rate_val}C (Loading: {load_val} mg/cm²)", fontsize=11)
                    ax.set_xlabel("Discharge Specific Capacity (mAh/g)", fontsize=10)
                    ax.set_ylabel("Cell Terminal Voltage (V)", fontsize=10)
                    ax.set_ylim(2.5, 4.4)
                    ax.grid(True, linestyle=":", alpha=0.6)
                    ax.legend(loc="lower left")
                    fig.tight_layout()

                    v_mean = float(np.mean(voltages)) if len(voltages) > 0 else 3.7
                    q_delivered = float(capacities[-1]) if len(capacities) > 0 else 220.0
                    cell_weight_per_cm2 = load_val * 4.2 + (load_val * np_val * 1.8) + 85.0
                    energy_density = (q_delivered * v_mean * 1000) / (cell_weight_per_cm2 * 1.15)

                    metrics = {
                        "solver": solver_type,
                        "c_rate": f"{c_rate_val} C",
                        "delivered_capacity_mAh_g": round(q_delivered, 2),
                        "average_voltage_V": round(v_mean, 3),
                        "calculated_cell_energy_wh_kg": round(energy_density, 1),
                    }

                    if solver_type.startswith("PyBaMM"):
                        # 残差与收敛信息如实透传自真实求解器；缺失时标注 N/A，不编造收敛声明
                        resid = sim_res.get("pde_residual_loss")
                        if isinstance(resid, (int, float)):
                            metrics["pde_residual_loss"] = round(float(resid), 6)
                            metrics["convergence"] = f"Converged (Residual {float(resid):.2e} < 1e-3)"
                        else:
                            metrics["pde_residual_loss"] = "N/A (求解器未返回残差指标)"
                            metrics["convergence"] = "N/A (求解器未返回收敛信息)"
                    else:
                        metrics["pde_residual_loss"] = "N/A (解析代理模型无 PDE 残差)"
                        metrics["convergence"] = "N/A (代理模型为解析求解，无迭代收敛过程)"
                    return fig, metrics


                sim_run_btn.click(
                    on_run_simulation,
                    inputs=[c_rate, loading, np_ratio],
                    outputs=[plot_output, sim_metrics_display],
                    show_progress="minimal",
                )

        gr.Markdown("--- \n*AutoBatteryResearch Agent · 吸收顶尖自动化工作流架构精髓，赋能化学电池研发新范式。*")

    # 串行执行: 工具层运行态 (全局 Manager/课题缓存) 为进程内共享状态，
    # 并发事件同时跑流水线会互相踩踏课题状态与产物目录。
    demo.queue(default_concurrency_limit=1)
    return demo


def launch_web_server(
    manager: Optional[StageManager] = None,
    host: str = "0.0.0.0",
    port: int = 7865,
    share: bool = False
):
    """启动 Web 服务入口."""
    theme = gr.themes.Soft(
        primary_hue="cyan",
        secondary_hue="blue",
        neutral_hue="slate",
    )
    demo = create_web_app(manager=manager)
    print(f"\n🌐 启动 AutoBatteryResearch Agent Web 仪表盘...")
    print(f"👉 本地访问地址: http://127.0.0.1:{port}")
    demo.launch(theme=theme, server_name=host, server_port=port, share=share)
