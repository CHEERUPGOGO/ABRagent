

from __future__ import annotations
import os
import sys
import json
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import HelpPanel

from auto_battery_research.workflow.stage_manager import StageManager
from auto_battery_research.tools.stage_tools import (
    set_stage_manager,
    tool_get_status,
    tool_get_detail,
    tool_get_current_tips,
    tool_check_stage,
    tool_complete_stage,
    tool_get_all_stage_journal,
    tool_skip_stage,
    tool_enable_stage,
    tool_run_stage_task,
)
from .widgets import (
    TaskPanel,
    MessagesPanel,
    ConsoleWidget,
    StatusBar,
    VerticalSplitter,
    HorizontalSplitter,
)
from .screens import ThemePickerScreen

CSS_PATH = "styles/default.tcss"


def now_str() -> str:
    """生成对标的时间戳前缀."""
    return datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f")[:-3] + " INFO]"


def err_str() -> str:
    """生成错误时间戳前缀."""
    return datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f")[:-3] + " ERROR]"


# 推理流日志行解析: 标准格式 "[YYYY-MM-DD HH:MM:SS.mmm LEVEL] text", 仅提取模型 ReAct 推理链相关级别
import re as _re

_REASON_LINE_PATTERN = _re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+\s+(THINK|TOOL|OBS|ERROR)\]\s*(.*)$")
_REASON_SUCCESS_PATTERN = _re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+\s+INFO\]\s*Success:\s*(.*)$")
_REASON_LEVEL_STYLE = {"THINK": "yellow", "TOOL": "bold cyan", "OBS": "green", "ERROR": "bold red"}


def _parse_reasoning_log_line(text: str):
    """从 logger 格式化行中提取推理流条目, 返回 (展示行, 样式); 非推理行返回 None.

    提取范围: THINK(AgentThought 思维链) / TOOL(工具调用) / OBS(观测结果) /
    ERROR(推理出错) / Success(阶段成果), 其余 INFO 噪音不上右侧推理面板.
    """
    m = _REASON_LINE_PATTERN.match(str(text).strip())
    if m:
        level, body = m.group(1), m.group(2).strip()
        icon = {"THINK": "💭", "TOOL": "🔧", "OBS": "👁", "ERROR": "❌"}.get(level, "·")
        return f"{icon} {body}", _REASON_LEVEL_STYLE.get(level, "white")
    m = _REASON_SUCCESS_PATTERN.match(str(text).strip())
    if m:
        return f"✅ {m.group(1).strip()}", "bold green"
    return None


class BatteryAgentTUI(App[None]):
    """AutoBatteryResearch Agent 终端全屏控制台 ."""

    CSS_PATH = CSS_PATH
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+c", "quit", "退出", show=False, priority=True),
        Binding("ctrl+t", "choose_theme", "换肤", show=False),
        Binding("f1", "toggle_help", "帮助", show=False),
    ]

    def __init__(self, manager: Optional[StageManager] = None, initial_goal: str = "化学电池全生命周期自主科研与智能设计") -> None:

        super().__init__()
        self.manager = manager or StageManager(target_goal=initial_goal)
        self.manager.target_goal = initial_goal
        set_stage_manager(self.manager)
        self.start_time = time.time()
        self.is_running_task: bool = False
        
        # 获取单模型名称
        llm_cfg = self.manager.config.get("llm", {})
        self.model_name = llm_cfg.get("writer_model") or llm_cfg.get("model") or "DeepSeek-V3"

    def on_mount(self) -> None:
        """挂载时绑定全局日志流."""
        from auto_battery_research.util.logger import set_console_sink
        set_console_sink(self._log_sink_callback)

    def _ui_post(self, fn, *args) -> None:
        """线程安全 UI 投递: 工作线程走 call_from_thread; 若本就在事件循环线程
        (如 handle_command 内同步执行 checker 触发日志), 直接调用避免静默丢行."""
        try:
            self.call_from_thread(fn, *args)
        except Exception:
            try:
                fn(*args)
            except Exception:
                pass

    def _log_sink_callback(self, text: str, style: str = "white") -> None:
        # 日志双路分发: 底部 Console 全量留痕 + 右侧 Messages 面板推理流 (仅 THINK/TOOL/OBS/ERROR/Success)
        try:
            console_widget = self.query_one("#console-container", ConsoleWidget)
        except Exception:
            return
        self._ui_post(console_widget.write_log, text, style)
        entry = _parse_reasoning_log_line(text)
        if entry:
            self._post_reasoning(entry[0], entry[1])

    def _post_reasoning(self, text: str, style: str = "white") -> None:
        """把模型推理流/阶段事件行投递到右侧 Messages 面板实时推理视图 (线程安全).

        推理视图未激活时 (非 run 状态) 自动忽略, 不会劫持阶段指引展示."""
        try:
            messages_panel = self.query_one("#messages-panel", MessagesPanel)
        except Exception:
            return
        self._ui_post(messages_panel.append_reasoning, text, style)

    def compose(self) -> ComposeResult:
        with Vertical(id="app-container"):
            with Horizontal(id="main-container"):
                yield TaskPanel(self.manager, id="task-panel")
                yield VerticalSplitter(classes="splitter vertical")
                with Vertical(id="right-container"):
                    yield MessagesPanel(self.manager, id="messages-panel")
            yield HorizontalSplitter(classes="splitter horizontal")
            yield ConsoleWidget(self.handle_command, id="console-container")
            yield StatusBar(
                start_time=self.start_time,
                target_goal=self.manager.target_goal,
                model_name=self.model_name,
                id="status-bar"
            )

    def action_choose_theme(self) -> None:
        """换肤对话框 (Ctrl+T)."""
        def apply_theme(theme_name: Optional[str]) -> None:
            if theme_name:
                self.theme = theme_name
                self.query_one("#console-container", ConsoleWidget).write_log(
                    f"{now_str()} Applied theme: {theme_name}", style="green"
                )


        self.push_screen(ThemePickerScreen(), apply_theme)

    def action_toggle_help(self) -> None:
        """显示帮助."""
        self.show_help()

    def handle_command(self, cmd_line: str) -> None:
        """解析并执行控制台指令."""
        parts = cmd_line.split()
        if not parts:
            return

        action = parts[0].lower()
        args = parts[1:]
        console_widget = self.query_one("#console-container", ConsoleWidget)
        messages_panel = self.query_one("#messages-panel", MessagesPanel)
        task_panel = self.query_one("#task-panel", TaskPanel)
        status_bar = self.query_one("#status-bar", StatusBar)

        if action in ("exit", "quit", "q"):
            console_widget.write_log(f"{now_str()} AutoBatteryResearch Agent is exited.", style="yellow")
            self.exit()

        elif action in ("clear", "cls"):
            console_widget.clear_log()

        elif action in ("help", "?"):
            self.show_help()

        elif action in ("goal", "set_goal", "target"):
            if not args:
                console_widget.write_log(f"{now_str()} 当前研发目标: '{self.manager.target_goal}' (用法: goal <新研究目标>)", style="cyan")
            else:
                new_goal = " ".join(args)
                switch_fn = getattr(self.manager, "switch_goal", None)
                if callable(switch_fn):
                    switch_fn(new_goal)
                else:
                    self.manager.target_goal = new_goal
                status_bar.set_goal(new_goal)
                task_panel.update_content()
                messages_panel.update_content(force=True)
                console_widget.write_log(f"{now_str()} Research target updated: '{new_goal}'", style="bold green")

        elif action in ("status", "st"):
            status_info = self.manager.get_status()
            task_panel.update_content()
            console_widget.write_log(
                f"{now_str()} Status: Active Stage {status_info['current_stage_id']} ({status_info['current_stage_name']}) │ Progress: {status_info['progress']}",
                style="green",
            )

        elif action in ("detail", "dt"):
            detail_info = self.manager.get_detail()
            console_widget.write_log(f"{now_str()} Mission details loaded to Messages panel.", style="cyan")
            formatted_json = json.dumps(detail_info, ensure_ascii=False, indent=2)
            messages_panel.show_text("Mission & Stages Detail", f"```json\n{formatted_json}\n```")

        elif action in ("tips", "t"):
            messages_panel.update_content(force=True)
            console_widget.write_log(f"{now_str()} Stage {self.manager.get_current_stage().id} guidelines refreshed.", style="green")

        elif action in ("check", "c"):
            sid = int(args[0]) if args and args[0].isdigit() else None
            res = tool_check_stage(stage_id=sid)
            passed = res.get("passed", False)
            diag = res.get("diagnostic", {})
            messages_panel.show_diagnostic(diag)
            task_panel.update_content()
            style_name = "green" if passed else "bold red"
            console_widget.write_log(
                f"{now_str()} Check Stage {sid or self.manager.get_current_stage().id}: {'[PASSED]' if passed else '[FAILED]'}",
                style=style_name,
            )

        elif action in ("complete", "cmp", "next"):
            sid = int(args[0]) if args and args[0].isdigit() else None
            res = tool_complete_stage(stage_id=sid)
            task_panel.update_content()
            messages_panel.update_content(force=True)
            if res.get("complete"):
                console_widget.write_log(f"{now_str()} {res.get('message', 'Completed successfully')}", style="bold green")
            else:
                console_widget.write_log(f"{err_str()} Complete failed: {res.get('message')}", style="bold red")
                if "failure_summary" in res and res["failure_summary"]:
                    messages_panel.show_diagnostic(res["failure_summary"])

        elif action in ("run", "r", "loop"):
            if self.is_running_task:
                console_widget.write_log(f"{now_str()} Warning: Task loop is already running.", style="yellow")
                return

            # 解析 --log / --log-file / --no-log 参数 (兼容 -log 与 --log)
            # 文件日志默认开启 (审计友好)；--no-log 显式关闭
            enable_log = True
            custom_log_file = None
            clean_args = []
            i = 0
            while i < len(args):
                arg = args[i]
                if arg in ("--log", "-log"):
                    enable_log = True
                elif arg in ("--no-log", "-no-log"):
                    enable_log = False
                elif arg.startswith(("--log-file=", "-log-file=")):
                    enable_log = True
                    custom_log_file = arg.split("=", 1)[1]
                elif arg in ("--log-file", "-log-file") and i + 1 < len(args):
                    enable_log = True
                    custom_log_file = args[i + 1]
                    i += 1
                else:
                    clean_args.append(arg)
                i += 1

            if clean_args:
                goal = " ".join(clean_args)
                # 课题目标变更走正式切换路径 (重载新课题状态)，禁止直接赋值造成跨课题进度污染
                switch_fn = getattr(self.manager, "switch_goal", None)
                if callable(switch_fn):
                    switch_fn(goal)
                else:
                    self.manager.target_goal = goal
                status_bar.set_goal(goal)
                task_panel.update_content()
            else:
                goal = self.manager.target_goal

            log_info_str = f" [Log enabled: {custom_log_file or 'log/<goal>.log'}]" if enable_log else ""
            console_widget.write_log(f"{now_str()} Starting ABRAgent for goal: '{goal}'{log_info_str}", style="bold cyan")
            self.is_running_task = True
            # 右侧面板切入实时推理视图: 展示模型接收任务后的 ReAct 推理流程 (思维链/工具调用/观测)
            messages_panel.begin_reasoning(f"{goal[:40]}{'…' if len(goal) > 40 else ''}")

            def _on_stage_update(stage_id: int, status: str, duration: float):
                self.call_from_thread(task_panel.update_content)
                self.call_from_thread(messages_panel.update_content, True)

            def _run_thread():
                try:
                    from auto_battery_research.agent import ABRAgent
                    agent = ABRAgent(
                        manager=self.manager,
                        goal=goal,
                        verbose=False,
                        enable_file_log=enable_log,
                        log_file=custom_log_file,
                        on_stage_update=_on_stage_update
                    )
                    
                    t_start = time.time()
                    for event in agent.run_stream(goal=goal):
                        ev_type = event.get("event")
                        ev_sid = event.get("stage_id")
                        if ev_type == "stage_start":
                            self.call_from_thread(console_widget.write_log, f"{now_str()} >>> [Stage {ev_sid}] ABRAgent 正在自主规划与调度领域工具...", "yellow")
                            self._post_reasoning(f"▶ [Stage {ev_sid}] 模型接收任务, 开始自主规划与工具调度…", "bold yellow")
                        elif ev_type == "stage_checked":
                            self.call_from_thread(console_widget.write_log, f"{now_str()} [CHECK] Stage {ev_sid} 确定性质量门禁验证 PASSED.", "green")
                            self._post_reasoning(f"✅ [Stage {ev_sid}] 门禁自检通过", "green")
                        elif ev_type == "stage_failed_attempt":
                            fail_sum = event.get("diag", {}).get("failure_summary", {})
                            self.call_from_thread(console_widget.write_log, f"{now_str()} [FAIL] Stage {ev_sid} 门禁未通过: {fail_sum.get('error', '未达标')} -> 自愈修复中...", "red")
                            self._post_reasoning(f"⚠ [Stage {ev_sid}] 门禁未通过 ({fail_sum.get('error_code', 'FAIL')}), 自愈重试中…", "bold red")
                        elif ev_type == "stage_completed":
                            self.call_from_thread(console_widget.write_log, f"{now_str()} [COMPLETE] Stage {ev_sid} 门禁终审通过，进入下一阶段。", "bold green")
                            self._post_reasoning(f"🏁 [Stage {ev_sid}] 终审通过, 进入下一阶段", "bold green")

                        self.call_from_thread(task_panel.update_content)
                        self.call_from_thread(messages_panel.update_content, True)

                    elapsed = time.time() - t_start
                    all_done = self.manager.is_all_completed()
                    self.is_running_task = False

                    self.call_from_thread(task_panel.update_content)
                    self.call_from_thread(messages_panel.update_content, True)

                    task_dir = self.manager.get_task_output_dir(goal)
                    report_file = task_dir / "final_research_report.md"
                    scheme_file = task_dir / "design_scheme.md"
                    
                    # 优先确保当前课题的最新综合研报已生成
                    if not report_file.exists():
                        try:
                            from auto_battery_research.tools.workflow_actions import run_synthesis_report
                            run_synthesis_report(target_query=goal, stage_manager=self.manager)
                        except Exception:
                            pass
                    
                    self._post_reasoning("🏁 课题运行结束, 推理流程收束", "bold cyan")
                    if report_file.exists():
                        with open(report_file, "r", encoding="utf-8") as f:
                            rep_content = f.read()
                        self.call_from_thread(messages_panel.show_text, "Synthesis Research Report", rep_content)
                    elif scheme_file.exists():
                        with open(scheme_file, "r", encoding="utf-8") as f:
                            rep_content = f.read()
                        self.call_from_thread(messages_panel.show_text, "Design Scheme (Stage 4)", rep_content)
                    else:
                        # 无研报/方案可展示时交还面板给阶段指引 (推理留痕仍在底部 Console)
                        self.call_from_thread(messages_panel.end_reasoning)

                    success_msg = f"{now_str()} ABRAgent Loop finished! All stages completed: success={all_done} (time: {elapsed:.1f}s)"
                    self.call_from_thread(console_widget.write_log, success_msg, "bold green")
                    if report_file.exists():
                        self.call_from_thread(
                            console_widget.write_log,
                            f"{now_str()} 📄 最终综合研报已渲染至右侧 Messages 面板 (输入 report 可随时回看, 'web' 可在大屏浏览器中查看)",
                            "bold magenta",
                        )
                    elif scheme_file.exists():
                        self.call_from_thread(
                            console_widget.write_log,
                            f"{now_str()} 📝 Stage 4 设计方案已渲染至右侧面板 (综合研报待后续运行生成, 输入 report 重试)",
                            "bold magenta",
                        )
                except Exception as e:
                    self.is_running_task = False
                    self.call_from_thread(console_widget.write_log, f"{now_str()} [ERROR] ABRAgent 运行异常: {e}", "bold red")
                    self.call_from_thread(messages_panel.end_reasoning)
                    import traceback
                    traceback.print_exc()

            threading.Thread(target=_run_thread, daemon=True).start()

        elif action in ("skip", "s"):
            if not args or not args[0].isdigit():
                console_widget.write_log(f"{now_str()} Usage: skip <stage_id>, e.g. skip 5", style="yellow")
            else:
                sid = int(args[0])
                tool_skip_stage(sid, reason="User skipped in TUI")
                task_panel.update_content()
                console_widget.write_log(f"{now_str()} Stage {sid} marked as SKIPPED.", style="cyan")

        elif action in ("enable", "e"):
            if not args or not args[0].isdigit():
                console_widget.write_log(f"{now_str()} Usage: enable <stage_id>, e.g. enable 5", style="yellow")
            else:
                sid = int(args[0])
                tool_enable_stage(sid)
                task_panel.update_content()
                console_widget.write_log(f"{now_str()} Stage {sid} re-enabled as required.", style="green")

        elif action in ("task", "exec"):
            sid = int(args[0]) if args and args[0].isdigit() else None
            res = tool_run_stage_task(stage_id=sid, target_query=self.manager.target_goal)
            console_widget.write_log(f"{now_str()} Task execution: {res.get('message', 'done')}", style="cyan")
            task_panel.update_content()

        elif action in ("journal", "j", "log"):
            journals = tool_get_all_stage_journal()
            formatted = json.dumps(journals, ensure_ascii=False, indent=2)
            messages_panel.show_text("Stage Journals", f"```json\n{formatted}\n```")
            console_widget.write_log(f"{now_str()} Stage journals loaded into Messages panel.", style="green")

        elif action in ("report", "rep"):
            current_goal = self.manager.target_goal
            task_dir = self.manager.get_task_output_dir(current_goal)
            report_file = task_dir / "final_research_report.md"
            scheme_file = task_dir / "design_scheme.md"
            
            if not report_file.exists():
                try:
                    from auto_battery_research.tools.workflow_actions import run_synthesis_report
                    run_synthesis_report(target_query=current_goal, stage_manager=self.manager)
                except Exception:
                    pass

            if report_file.exists():
                with open(report_file, "r", encoding="utf-8") as f:
                    rep_text = f.read()
                messages_panel.show_text("Synthesis Research Report", rep_text)
                console_widget.write_log(f"{now_str()} Synthesis report for '{current_goal}' rendered into Messages panel.", style="green")
            elif scheme_file.exists():
                with open(scheme_file, "r", encoding="utf-8") as f:
                    scheme_text = f.read()
                messages_panel.show_text("Design Scheme (Stage 4)", scheme_text)
                console_widget.write_log(f"{now_str()} Design scheme for '{current_goal}' rendered into Messages panel.", style="green")
            else:
                console_widget.write_log(f"{now_str()} Report not generated yet for '{current_goal}'. Type 'run' to execute workflow.", style="yellow")


        elif action in ("web", "ui"):
            self._launch_web_monitor(console_widget)

        elif action in ("reset", "restart"):
            self.manager.reset_workflow()
            task_panel.update_content()
            messages_panel.update_content(force=True)
            console_widget.write_log(f"{now_str()} Workflow state reset to Stage 1.", style="bold green")

        else:
            # 智能自然语言问答与动态课题交互
            query = cmd_line.strip()
            console_widget.write_log(f"{now_str()} Received query: '{query}'", style="bold cyan")
            console_widget.write_log(f"{now_str()} Dispatched to LLM Reasoning Agent (Model: {self.model_name})...", style="yellow")

            def _chat_thread():
                from auto_battery_research.backend.llm_client import LLMClient
                from auto_battery_research.util.logger import log_tool_call, log_observation

                log_tool_call("ConversationalAgent", f"query='{query}'")
                client = LLMClient(self.manager.config)


                sys_prompt = (
                    "你是一名顶尖电化学储能与电池材料专家科研智能体。请结合前沿电化学知识，"
                    "以严谨、专业、清晰的语言准确回答用户的学术或工程问题。"
                    "回答需条理分明，使用标准化学分子式（如 LiNi0.8Co0.1Mn0.1O2、LiFSI）与适度数据支撑。"
                )
                try:
                    ans = client.generate(query, system_prompt=sys_prompt)
                    log_observation(f"Answer generated ({len(ans)} chars)")

                    formatted_ans = f"# 专家智能体问答响应\n\n> **问题**: {query}\n> **模型**: {client.model}\n\n---\n\n{ans}"
                    self.call_from_thread(messages_panel.show_text, f"Q&A: {query[:20]}...", formatted_ans)
                    self.call_from_thread(console_widget.write_log, f"{now_str()} Answer rendered in Messages panel.", "bold green")
                except Exception as e:
                    self.call_from_thread(console_widget.write_log, f"{err_str()} Query failed: {e}", "bold red")

            threading.Thread(target=_chat_thread, daemon=True).start()


    def _launch_web_monitor(self, console_widget) -> None:
        """以独立子进程启动 (或复用已在运行的) Web 监控大屏.

        ⚠️ 绝不能在 TUI 进程内的后台线程里跑 uvicorn: Textual 运行时接管了
        stdout/stderr, 后台线程的 print 与 uvicorn 日志会与终端渲染争抢输出,
        轻则花屏、重则死锁 (表现为界面无法输入)。独立子进程彻底隔离,
        且服务生命周期与 TUI 解耦 —— TUI 退出后大屏仍可访问。
        """
        import subprocess
        from auto_battery_research.web.server import probe_monitor_health, _port_free

        port = 7865
        if probe_monitor_health("127.0.0.1", port):
            console_widget.write_log(
                f"{now_str()} Web 监控大屏已在运行, 直接打开: http://127.0.0.1:{port} (只读, 与 TUI 实时联动)",
                style="bold green",
            )
            return

        if not _port_free("127.0.0.1", port):
            console_widget.write_log(
                f"{now_str()} 端口 {port} 被其他程序占用, 自动尝试 {port + 1}~{port + 10} ...", style="yellow"
            )
            for cand in range(port + 1, port + 11):
                if _port_free("127.0.0.1", cand):
                    port = cand
                    break
            else:
                console_widget.write_log(
                    f"{err_str()} 端口 {port}~{port + 10} 均被占用, Web 大屏启动失败 (可用 --port 指定其他端口)。", style="bold red"
                )
                return

        # Windows 下无新窗口、独立进程组 (Ctrl+C 不波及); 输出全部丢弃, 不污染 TUI 终端
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [sys.executable, "-m", "auto_battery_research.cli", "--web", "--port", str(port)],
            cwd=str(Path(__file__).resolve().parents[2]),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        console_widget.write_log(f"{now_str()} 正在启动 Web 监控大屏 (独立进程): http://127.0.0.1:{port} ...", style="bold cyan")

        def _wait_ready():
            for _ in range(20):
                if probe_monitor_health("127.0.0.1", port):
                    self.call_from_thread(
                        console_widget.write_log,
                        f"{now_str()} ✅ Web 监控大屏已就绪: http://127.0.0.1:{port} (浏览器打开, 5 秒轮询联动本 TUI)",
                        "bold green",
                    )
                    return
                time.sleep(1)
            self.call_from_thread(
                console_widget.write_log,
                f"{err_str()} Web 服务 20s 内未就绪; 请在另一终端运行 `abr-cli --web` 查看具体报错。",
                "bold red",
            )

        threading.Thread(target=_wait_ready, daemon=True).start()

    def show_help(self) -> None:
        """展示帮助面板."""
        help_md = """# AutoBatteryResearch TUI Command Reference

| Command | Alias | Description |
|:---|:---:|:---|
| `goal <target>` | `set_goal` | Set active battery research target (e.g. `goal 450Wh/kg SC-NCM90`) |
| `run [target]` | `r`, `loop` | Launch full autonomous closed-loop execution |
| `status` | `st` | Refresh 6-stage execution matrix |
| `detail` | `dt` | Show full mission metadata and configuration breakdown |
| `tips` | `t` | Refresh current stage task guidelines & requirements |
| `check [id]` | `c` | Run deterministic gate self-check (Diagnostic only) |
| `complete [id]` | `cmp` | Approve gate pass and advance to next stage |
| `skip <id>` | `s` | Skip specific stage (e.g. `skip 5` for PINN) |
| `enable <id>` | `e` | Re-enable skipped stage (e.g. `enable 5`) |
| `task [id]` | `exec` | Run underlying data mining or RAG pipeline |
| `journal` | `j` | View historical stage journals and deliverables |
| `report` | `rep` | Render final battery synthesis report |
| `web` | `ui` | Launch read-only Web Monitor (FastAPI) |
| `reset` | | Reset workflow state to Stage 1 |
| `clear` | `cls` | Clear console output |
| `help` | `?` | Show this command reference (or press F1) |
| `exit` | `q` | Exit TUI console (or press Ctrl+C) |

> Theme switcher: Press **Ctrl+T** to open theme picker (Dark / Dracula / Tokyo-Night / Nord / Light).
"""

        self.query_one("#messages-panel", MessagesPanel).show_text("Command Reference & Help", help_md)
        self.query_one("#console-container", ConsoleWidget).write_log(f"{now_str()} Help reference loaded into Messages panel.", style="cyan")


def launch_tui(manager: Optional[StageManager] = None, initial_goal: str = "化学电池全生命周期自主科研与智能设计"):
    """启动工业级 Textual TUI 应用."""
    app = BatteryAgentTUI(manager=manager, initial_goal=initial_goal)
    app.run()

