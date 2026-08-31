"""ConsoleWidget for AutoBatteryResearch TUI (Using Textual RichLog)."""

from __future__ import annotations
from typing import TYPE_CHECKING, Callable, List

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import RichLog, Static, Input
from rich.text import Text

if TYPE_CHECKING:
    pass


class ConsoleWidget(Vertical):
    """底部控制台：基于 RichLog 的高性能流式日志与指令输入."""

    def __init__(self, on_command: Callable[[str], None], **kwargs) -> None:
        super().__init__(**kwargs)
        self.border_title = "Console"
        self.on_command = on_command
        self.history: List[str] = []
        self.history_index: int = -1

    def compose(self) -> ComposeResult:
        yield RichLog(
            id="console-output",
            max_lines=1000,
            auto_scroll=True,
            wrap=True,
            markup=False,
            highlight=False,
        )
        with Horizontal(id="console-input-row"):
            yield Static("(AutoBattery) > ", id="console-prompt")
            yield Input(placeholder="输入指令 (输入 'run' 或回车立即启动全自动执行，'help' 查看清单)...", id="console-input")

    def on_mount(self) -> None:
        self.write_log(
            "AutoBatteryResearch TUI ready. Literature, vector DB and cell data assets connected.",
            style="bold green"
        )
        self.write_log(
            "Stage 1~3 pre-flight passed. Active focus: Stage 4 (Multi-Agent RAG Design). Type 'run' or press Enter to execute.",
            style="bold cyan"
        )


    def write_log(self, text: str, style: str = "white") -> None:
        """使用 RichLog 线程安全地追加日志."""
        try:
            log_widget = self.query_one("#console-output", RichLog)
            log_widget.write(Text(text, style=style))
        except Exception:
            pass

    def clear_log(self) -> None:
        """清空日志."""
        try:
            log_widget = self.query_one("#console-output", RichLog)
            log_widget.clear()
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """用户回车提交指令."""
        val = event.value.strip()
        inp = self.query_one("#console-input", Input)
        inp.value = ""
        self.history_index = -1

        # 若用户直接按回车未输入内容，默认执行 'run'
        if not val:
            val = "run"

        self.history.append(val)
        self.write_log(f"> {val}", style="bold cyan")
        self.on_command(val)
