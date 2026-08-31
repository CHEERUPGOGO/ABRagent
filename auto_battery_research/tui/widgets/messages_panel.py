

from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static
from rich.text import Text

if TYPE_CHECKING:
    from auto_battery_research.workflow.stage_manager import StageManager


class MessagesPanel(VerticalScroll, can_focus=True):
    """右上侧面板：展示阶段指引、交付物清单与实时诊断日志 ."""

    def __init__(self, manager: StageManager, **kwargs) -> None:
        super().__init__(**kwargs)
        self.manager = manager
        self.border_title = "Messages"
        self._last_stage_id: Optional[int] = None

    def compose(self) -> ComposeResult:
        yield Markdown(id="guide-markdown")
        yield Static(id="diagnostic-box")

    def on_mount(self) -> None:
        self.update_content()
        self.set_interval(1.0, self.update_content)

    def update_content(self, force: bool = False) -> None:
        """刷新指南与交付物列表."""
        curr = self.manager.get_current_stage()
        if force or curr.id != self._last_stage_id:
            self._last_stage_id = curr.id
            tips_md = curr.get_tips()
            
            # 顶部表格与状态摘要
            status = self.manager.get_status()
            stages_table = """| Stage | Description | Status |
|:---:|:---|:---:|
| 1-3 | Literature ingestion, Vector DB indexing & Cell mining | [Completed] |
| 4 | Multi-Agent RAG battery design scheme | [Active] |
| 5 | PINN & PyBaMM physics simulation | [Skipped] |
| 6 | Synthesis research report generation | [Pending] |

"""
            full_md = f"### Stage {curr.id}: {curr.name}\n\n" + stages_table + "### Key Deliverables & Guidelines\n" + tips_md
            try:
                self.query_one("#guide-markdown", Markdown).update(full_md)
            except Exception:
                pass

    def show_text(self, title: str, content: str) -> None:
        """展示自定义 Markdown 内容."""
        try:
            self.border_title = f"Messages ── {title}"
            self.query_one("#guide-markdown", Markdown).update(content)
        except Exception:
            pass

    def show_diagnostic(self, diag: dict) -> None:
        """更新诊断信息展示."""
        diag_text = Text()
        passed = diag.get("check_pass", False)
        if passed:
            diag_text.append("\n[2026-08-29 INFO] Checker Gate Passed! All electrochemical metrics valid.\n", style="bold green")
            diag_text.append(f"Next action: {diag.get('next_action', 'Run complete to advance')}\n", style="yellow")
        else:
            fail_sum = diag.get("failure_summary") or diag
            diag_text.append(f"\n[2026-08-29 ERROR] Check Gate Failed: {fail_sum.get('error_code', 'FAIL')}\n", style="bold red")
            diag_text.append(f"Reason: {fail_sum.get('error', 'Constraint mismatch')}\n", style="yellow")
            diag_text.append(f"Suggestion: {fail_sum.get('next_action', 'Please revise')}\n", style="bold cyan")
        
        try:
            self.query_one("#diagnostic-box", Static).update(diag_text)
        except Exception:
            pass
