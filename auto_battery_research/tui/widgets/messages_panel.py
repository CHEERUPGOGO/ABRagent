

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

    # 阶段状态 → 展示标签 (动态渲染, 替换曾经的硬编码静态表格)
    _STATUS_LABELS = {
        "PASSED": "✅ Completed",
        "IN_PROGRESS": "🔄 Active",
        "SKIPPED": "⏭️ Skipped",
        "FAILED": "❌ Failed",
        "PENDING": "⚪ Pending",
    }

    def __init__(self, manager: StageManager, **kwargs) -> None:
        super().__init__(**kwargs)
        self.manager = manager
        self.border_title = "Messages"
        self._last_stage_id: Optional[int] = None
        self._showing_custom_text: bool = False

    def compose(self) -> ComposeResult:
        yield Markdown(id="guide-markdown")
        yield Static(id="diagnostic-box")

    def on_mount(self) -> None:
        self.update_content()
        self.set_interval(1.0, self.update_content)

    def _render_stage_table(self) -> str:
        """由 StageManager 真实状态渲染 6 阶段进度表 (有则提取、无则留空)."""
        rows = ["| Stage | Description | Status |", "|:---:|:---|:---:|"]
        try:
            status = self.manager.get_status()
            stages = status.get("stages", [])
        except Exception:
            stages = []
        for s in stages:
            st = s.get("status", "PENDING")
            if s.get("skip") and st not in ("PASSED", "FAILED"):
                st = "SKIPPED"
            label = self._STATUS_LABELS.get(st, st)
            rows.append(f"| {s.get('id')} | {s.get('name', s.get('key', ''))} | [{label}] |")
        return "\n".join(rows) + "\n\n"

    def update_content(self, force: bool = False) -> None:
        """刷新指南与交付物列表."""
        curr = self.manager.get_current_stage()
        # 如果正在展示研报/自定义内容，且非强制刷新且阶段未发生变化，则保持展示
        if self._showing_custom_text and not force and curr.id == self._last_stage_id:
            return

        if force or curr.id != self._last_stage_id:
            self._showing_custom_text = False
            self._last_stage_id = curr.id
            self.border_title = "Messages"
            tips_md = curr.get_tips()

            # 顶部表格与状态摘要 (动态渲染真实阶段状态)
            stages_table = self._render_stage_table()
            full_md = f"### Stage {curr.id}: {curr.name}\n\n" + stages_table + "### Key Deliverables & Guidelines\n" + tips_md
            try:
                self.query_one("#guide-markdown", Markdown).update(full_md)
            except Exception:
                pass

    def show_text(self, title: str, content: str) -> None:
        """展示自定义 Markdown 内容 (如最新综合研报或方案)."""
        try:
            self._showing_custom_text = True
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
