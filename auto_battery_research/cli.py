#!/usr/bin/env python3
"""AutoBatteryResearch CLI — 命令行交互与工作流驱动入口."""

import sys
import argparse
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

from auto_battery_research.agent import ABRAgent
from auto_battery_research.workflow.stage_manager import StageManager
from auto_battery_research.backend.loop_runner import AutonomousLoopRunner
from auto_battery_research.tools.stage_tools import (
    set_stage_manager,
    tool_get_status,
    tool_get_current_tips,
    tool_check_stage,
    tool_complete_stage,
    tool_get_all_stage_journal,
    tool_skip_stage,
    tool_enable_stage,
)
from auto_battery_research.tools.mcp_server import start_stdio_server


def print_report_in_terminal(manager: StageManager, goal: str = None):
    """在终端格式化高亮渲染综合研报."""
    resolved_goal = goal or manager.target_goal
    task_dir = manager.get_task_output_dir(resolved_goal)
    cand_reports = [
        task_dir / "final_research_report.md",
        task_dir / "final_report.md",
        task_dir / "battery_research_synthesis_report.md",
    ]
    # 全局 legacy 报告仅对历史存量课题回退展示；新课题绝不把全局旧研报冒充本课题产物
    if manager.is_legacy_goal(resolved_goal):
        cand_reports.extend([
            Path("output/auto_battery_research/final_research_report.md"),
            Path("output/auto_battery_research/final_report.md"),
            Path("output/auto_battery_research/battery_research_synthesis_report.md"),
        ])
    found_rf = next((p for p in cand_reports if p.exists()), None)
    if not found_rf:
        print("\n⚠️ 未找到已生成的综合研发报告。请先执行：python auto_battery_research_cli.py --run 生成研报。\n")
        return

    with open(found_rf, "r", encoding="utf-8") as f:
        content = f.read()

    try:
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.panel import Panel
        console = Console()
        console.print("\n")
        console.print(Panel(f"[bold cyan]AutoBatteryResearch Agent 综合研发研报[/bold cyan]\n[dim]报告路径: {found_rf.resolve()}[/dim]", border_style="cyan"))
        console.print(Markdown(content))
        console.print("\n")
    except Exception:
        print(f"\n==================== 综合研发报告 ({found_rf}) ====================")
        print(content)
        print("====================================================================\n")


def print_status_table(status: dict):
    """格式化打印阶段状态表格."""
    print("\n" + "="*80)
    print(f"[AutoBatteryResearch Agent] 任务: {status.get('mission_name')}")
    print(f"当前活跃阶段: Stage {status.get('current_stage_id')} ({status.get('current_stage_name')}) | 状态: {status.get('current_stage_status')}")
    print(f"整体完成进度: {status.get('progress')}")
    print("="*80)
    print(f"{'ID':<4} {'Key':<28} {'名称':<22} {'状态':<14} {'是否跳过':<8}")
    print("-"*80)
    for s in status.get("stages", []):
        st = s["status"]
        status_tag = f"[{st}]"
        skip_str = "是 (Skip)" if s["skip"] else "否"
        print(f"{s['id']:<4} {s['key']:<28} {s['name']:<22} {status_tag:<14} {skip_str:<8}")
    print("="*80 + "\n")


def main():
    # 加载仓库根目录 .env (零依赖解析；不覆盖已导出的系统环境变量)
    from auto_battery_research.util.env_loader import load_env
    load_env()

    parser = argparse.ArgumentParser(
        description="AutoBatteryResearch Agent — 全生命周期化学电池研究自主智能体",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python auto_battery_research_cli.py --run                         # 启动 ABRAgent 全自动端到端自主研发大循环
  python auto_battery_research_cli.py --run --goal "研发500Wh/kg固态电池" # 指定电池设计课题
  python auto_battery_research_cli.py --status                      # 查看当前工作流与阶段状态
  python auto_battery_research_cli.py --tips                        # 查看当前阶段的任务 Tips 与要求
  python auto_battery_research_cli.py --check                       # 执行当前阶段门禁自检 (只诊断不推进)
  python auto_battery_research_cli.py --complete                    # 终审并通过当前阶段 (推进到下一阶段)
  python auto_battery_research_cli.py --report                      # 在终端中以高亮 Markdown 浏览最终研报
  python auto_battery_research_cli.py --run --with-pinn             # 全自动执行并激活 Stage 5 (PINN物理仿真)
  python auto_battery_research_cli.py --skip-stage 5                # 手动跳过 Stage 5
  python auto_battery_research_cli.py --enable-stage 5              # 重新激活 Stage 5
  python auto_battery_research_cli.py --journal                     # 查看历史阶段研发日志
  python auto_battery_research_cli.py --reset                       # 重置工作流状态至 Stage 1
  python auto_battery_research_cli.py --mcp                         # 启动 stdio MCP Server (供 IDE/Agent 接入)
        """,
    )

    parser.add_argument("--status", action="store_true", help="显示工作流状态矩阵与进度")
    parser.add_argument("--doctor", action="store_true", help="环境自检: LLM Key / Ollama / MinerU / 文献资产 / 可选依赖 一次查完")
    parser.add_argument("--detail", action="store_true", help="显示任务与所有阶段的深入明细信息")
    parser.add_argument("--tips", action="store_true", help="显示当前活跃 Stage 的任务指南与验收指标")

    parser.add_argument("--check", action="store_true", help="执行当前阶段确定性门禁自检 (只诊断不推进)")
    parser.add_argument("--check-stage", type=int, help="检查指定 Stage ID 的门禁")
    parser.add_argument("--complete", action="store_true", help="终审并通过当前阶段 (推进至下一阶段)")
    parser.add_argument("--complete-stage", type=int, help="终审并通过指定 Stage ID")
    parser.add_argument("--skip-stage", type=int, help="动态跳过指定 Stage ID (如 Stage 5)")
    parser.add_argument("--enable-stage", type=int, help="动态激活已跳过的 Stage ID")
    parser.add_argument("--with-pinn", action="store_true", help="激活 Stage 5 (PINN 物理仿真)")
    parser.add_argument("--skip-pinn", action="store_true", help="跳过 Stage 5 (PINN 物理仿真)")
    parser.add_argument("--run", action="store_true", help="启动 ABRAgent 进行全自动端到端自主执行")
    parser.add_argument("--goal", type=str, default="设计400Wh/kg高比能液态锂金属电池方案", help="指定电池设计研发目标")
    parser.add_argument("--journal", action="store_true", help="查看所有阶段的历史研发日志")
    parser.add_argument("--report", "-r", action="store_true", help="在终端中以高亮 Markdown 语法渲染并浏览最终综合研报")
    parser.add_argument("--log", action="store_true", help="启用运行日志落盘 (现已默认开启，保存至 log/<课题名称>.log)")
    parser.add_argument("--no-log", action="store_true", help="关闭运行日志落盘 (默认已开启，此开关供显式关闭)")
    parser.add_argument("--log-file", type=str, default=None, help="自定义日志落盘文件路径 (默认落入 log/ 目录下)")

    parser.add_argument("--reset", action="store_true", help="重置工作流状态至 Stage 1")
    parser.add_argument("--tui", action="store_true", help="启动 Rich 多面板终端交互控制台 (TUI)")
    parser.add_argument("--web", action="store_true", help="启动 FastAPI Web 监控大屏 (只读: 课题进度/研报/日志, 与 TUI/CLI 实时联动)")
    parser.add_argument("--web-gradio", action="store_true", help="启动旧版 Gradio 交互仪表盘 (后备入口, 兼容 Gradio 4/5/6)")
    parser.add_argument("--mcp", action="store_true", help="启动 stdio MCP Server 服务")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Web 监控大屏监听地址 (默认 127.0.0.1 安全本地回环)")
    parser.add_argument("--port", type=int, default=7865, help="Web 监控大屏端口 (默认 7865)")
    parser.add_argument("--share", action="store_true", help="创建公共分享链接 (仅旧版 Gradio 后备入口支持)")

    args = parser.parse_args()

    # 0. 环境自检 (无需 StageManager，直接输出体检报告后退出)
    if args.doctor:
        from auto_battery_research.util.doctor import print_doctor_report
        sys.exit(1 if print_doctor_report() > 0 else 0)

    # 1. MCP Server 模式
    if args.mcp:
        start_stdio_server()
        return

    # 2. FastAPI Web 监控大屏 (主 Web 入口): 纯读磁盘、零写入,
    #    故放在 StageManager 构造之前 —— 不触发 Checker 级联/状态落盘
    if args.web:
        from auto_battery_research.web.server import launch_fastapi_web_server
        launch_fastapi_web_server(host=args.host, port=args.port)
        return

    # 3. 初始化 StageManager
    skip_pinn_val = None
    if args.with_pinn:
        skip_pinn_val = False
    elif args.skip_pinn:
        skip_pinn_val = True

    mgr = StageManager(skip_pinn=skip_pinn_val, target_goal=args.goal)
    set_stage_manager(mgr)

    # 4. Gradio 旧版仪表盘 (后备入口, 需要 Manager 驱动交互操作)
    if args.web_gradio:
        from auto_battery_research.web.app import launch_web_server
        launch_web_server(manager=mgr, host=args.host, port=args.port, share=args.share)
        return

    # 4. TUI 模式
    if args.tui:
        from auto_battery_research.tui.app import launch_tui
        launch_tui(manager=mgr, initial_goal=args.goal)
        return

    # 5. 处理各 CLI 指令
    if args.reset and not args.run:
        mgr.reset_workflow(start_stage_id=1)
        print("🔄 工作流状态已重置为 Stage 1。")
        print_status_table(mgr.get_status())
        return

    if args.skip_stage:
        tool_skip_stage(args.skip_stage, reason="CLI 手动跳过")
        print(f"⏭️  Stage {args.skip_stage} 已设置为跳过 (SKIPPED)。")
        print_status_table(mgr.get_status())
        return

    if args.enable_stage:
        tool_enable_stage(args.enable_stage)
        print(f"✅ Stage {args.enable_stage} 已重新激活为必检阶段。")
        print_status_table(mgr.get_status())
        return

    if args.tips:
        print("\n" + mgr.get_current_tips() + "\n")
        return

    if args.check:
        res = tool_check_stage()
        print("\n" + "="*60)
        print(f"🔍 阶段门禁检查结果: {'[通过]' if res['passed'] else '[未通过]'}")
        print("="*60)
        import json
        print(json.dumps(res["diagnostic"], ensure_ascii=False, indent=2))
        return

    if args.check_stage:
        res = tool_check_stage(stage_id=args.check_stage)
        print("\n" + "="*60)
        print(f"🔍 Stage {args.check_stage} 门禁检查结果: {'[通过]' if res['passed'] else '[未通过]'}")
        print("="*60)
        import json
        print(json.dumps(res["diagnostic"], ensure_ascii=False, indent=2))
        return

    if args.complete:
        res = tool_complete_stage()
        print("\n" + res.get("message", "执行完成"))
        print_status_table(mgr.get_status())
        return

    if args.complete_stage:
        res = tool_complete_stage(stage_id=args.complete_stage)
        print("\n" + res.get("message", "执行完成"))
        print_status_table(mgr.get_status())
        return

    if args.journal:
        journals = tool_get_all_stage_journal()
        print("\n" + "="*60)
        print("📝 AutoBatteryResearch 全阶段研发日志记录")
        print("="*60)
        import json
        print(json.dumps(journals, ensure_ascii=False, indent=2))
        return

    if args.report:
        print_report_in_terminal(mgr, args.goal)
        return

    if args.run:
        # 文件日志默认开启 (审计友好)；--no-log 显式关闭；--log-file 隐含开启并指定路径
        enable_log = (not args.no_log) or bool(args.log_file)
        if args.reset:
            mgr.reset_workflow(start_stage_id=1)
        agent = ABRAgent(
            manager=mgr,
            goal=args.goal,
            skip_pinn=skip_pinn_val,
            enable_file_log=enable_log,
            log_file=args.log_file,
        )
        success = agent.run()
        print("\n" + "="*80)
        print(f"🎉 [ABRAgent] 全生命周期科研循环执行完成: {'全部闭环' if success else '部分完成'}")
        task_dir = mgr.get_task_output_dir(args.goal)
        report_p = task_dir / "final_research_report.md"
        if agent.enable_file_log and agent.log_file_path and agent.log_file_path.exists():
            print(f"📄 完整运行日志已保存至: {agent.log_file_path.resolve()}")
        if report_p.exists():
            print(f"👉 最终综合研报路径: {report_p.resolve()}")
        print("💡 您可以随时运行以下命令在终端高亮浏览完整研报：")
        print("   python auto_battery_research_cli.py --report")
        print("="*80 + "\n")
        return

    if args.status:
        print_status_table(mgr.get_status())
        return

    if args.detail:
        import json
        print("\n" + "="*80)
        print("📋 AutoBatteryResearch Mission 与 Stages 深入明细")
        print("="*80)
        print(json.dumps(mgr.get_detail(), ensure_ascii=False, indent=2))
        print("="*80 + "\n")
        return

    # 无任何参数时，默认启动交互式 TUI
    from auto_battery_research.tui.app import launch_tui
    launch_tui(manager=mgr, initial_goal=args.goal)


if __name__ == "__main__":
    main()
