"""StatusBar widget for AutoBatteryResearch TUI."""

import time
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static


class StatusBar(Horizontal):
    """底部单行状态提示栏."""

    def __init__(
        self,
        start_time: float = 0,
        target_goal: str = "",
        model_name: str = "DeepSeek-V3",
        **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.start_time = start_time or time.time()
        self.target_goal = target_goal or "400Wh/kg 锂金属电池设计"
        self.model_name = model_name or "DeepSeek-V3"

    def compose(self) -> ComposeResult:
        yield Static(id="status-left")
        yield Static("F1 for shortcuts", id="status-hint")

    def on_mount(self) -> None:
        self.update_bar()
        self.set_interval(1.0, self.update_bar)

    def update_bar(self) -> None:
        elapsed = int(time.time() - self.start_time)
        hrs = elapsed // 3600
        mins = (elapsed % 3600) // 60
        secs = elapsed % 60
        time_str = f"{hrs:02d}:{mins:02d}:{secs:02d}"

        text = (
            f"Run Time: {time_str} │ "
            f"Target: {self.target_goal} │ "
            f"Model: {self.model_name} │ "
            f"Backend: LangChain │ "
            f"Mode: Autonomous"
        )
        try:
            self.query_one("#status-left", Static).update(text)
        except Exception:
            pass

    def set_goal(self, new_goal: str) -> None:
        self.target_goal = new_goal
        self.update_bar()
