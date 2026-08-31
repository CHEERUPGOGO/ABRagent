"""TaskPanel widget for AutoBatteryResearch TUI."""

from __future__ import annotations
from typing import TYPE_CHECKING
import os
import time
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static
from rich.text import Text

from auto_battery_research.util.logger import get_session_tool_counts

if TYPE_CHECKING:
    from auto_battery_research.workflow.stage_manager import StageManager


def format_duration(seconds: float) -> str:
    s = int(seconds)
    mins = s // 60
    secs = s % 60
    return f"{mins:02d}m {secs:02d}s"


class TaskPanel(VerticalScroll):
    """左侧面板：展示使命目标、6阶段动态秒级计时、工具计数与产物列表."""

    def __init__(self, manager: StageManager, **kwargs) -> None:
        super().__init__(**kwargs)
        self.manager = manager
        self.border_title = "Mission"

    def compose(self) -> ComposeResult:
        yield Static(id="mission-title")
        yield Static(id="task-list")
        yield Static("Changed Files", classes="section-title")
        yield Static(id="changed-files")
        yield Static("Tools Call", classes="section-title")
        yield Static(id="tool-status")
        yield Static("Status", classes="section-title")
        yield Static(id="status-summary")

    def on_mount(self) -> None:
        self.update_content()
        self.set_interval(1.0, self.update_content)

    def update_content(self) -> None:
        """每秒动态刷新面板内容、阶段耗时与产物变化."""
        status = self.manager.get_status()
        goal = getattr(self.manager, "target_goal", "化学电池全生命周期自主科研与智能设计")
        
        # 1. 顶部标题
        title_text = Text(f"{goal}\n", style="bold cyan")
        self.query_one("#mission-title", Static).update(title_text)

        # 2. 格式化 6 阶段列表 (秒级动态计时)
        rendered_tasks = Text()
        stages = status.get("stages", [])
        curr_id = status.get("current_stage_id", 1)

        for idx, s in enumerate(stages):
            sid = s["id"]
            key = s["key"]
            name = s["name"]
            st = s["status"]
            skip = s["skip"]
            dur = s.get("duration_seconds", 0.0)
            fails = s.get("fail_count", 0)
            time_str = format_duration(dur)

            if skip:
                line_str = f"{idx:<2} {sid}-{key}-{name} ({fails} fails, {time_str}) (skipped)\n"
                rendered_tasks.append(line_str, style="dim blue")
            elif st == "PASSED":
                line_str = f"{idx:<2} {sid}-{key}-{name} ({fails} fails, {time_str})\n"
                rendered_tasks.append(line_str, style="green")
            elif st == "IN_PROGRESS":
                line_str = f"{idx:<2} {sid}-{key}-{name} ({fails} fails, {time_str}) [Running]\n"
                rendered_tasks.append(line_str, style="bold yellow")
            elif sid == curr_id:
                line_str = f"{idx:<2} {sid}-{key}-{name} ({fails} fails, {time_str}) [Active]\n"
                rendered_tasks.append(line_str, style="bold cyan")
            elif st == "FAILED":
                line_str = f"{idx:<2} {sid}-{key}-{name} ({fails} fails, {time_str}) [Failed]\n"
                rendered_tasks.append(line_str, style="bold red")
            else:
                line_str = f"{idx:<2} {sid}-{key}-{name} ({fails} fails, {time_str})\n"
                rendered_tasks.append(line_str, style="dim white")

        self.query_one("#task-list", Static).update(rendered_tasks)

        # 3. Changed Files: 动态扫描当前课题目录与全局产物目录中最新更新的交付物
        task_dir = self.manager.get_task_output_dir()
        legacy_dir = self.manager.root_dir / "output" / "auto_battery_research"
        
        seen_files = set()
        file_list = []
        for d in (task_dir, legacy_dir):
            if d.exists():
                for p in d.glob("**/*"):
                    if p.is_file() and p.name not in seen_files:
                        seen_files.add(p.name)
                        try:
                            mtime = os.path.getmtime(p)
                            file_list.append((mtime, p.name))
                        except Exception:
                            pass
        file_list.sort(reverse=True)
        file_list = file_list[:4]

        file_text = Text()
        if file_list:
            for mtime, fn in file_list:
                t_str = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")
                file_text.append(f"{t_str}: {fn}\n", style="green")
        else:
            file_text.append("none\n", style="dim")
        self.query_one("#changed-files", Static).update(file_text)

        # 4. Tools Call: 动态展示当前会话各工具精确调用次数
        tool_counts = get_session_tool_counts()
        tools_str = " ".join([f"{k}({v})" for k, v in tool_counts.items()])
        tools_text = Text(f"{tools_str}\n", style="yellow")
        self.query_one("#tool-status", Static).update(tools_text)

        # 5. 状态概览
        summary_text = Text()
        summary_text.append(f"Current Stage: Stage {curr_id} ({status.get('current_stage_name')})\n", style="cyan")
        summary_text.append(f"Progress: {status.get('progress')}\n", style="bold green")
        self.query_one("#status-summary", Static).update(summary_text)
