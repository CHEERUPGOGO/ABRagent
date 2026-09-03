

from __future__ import annotations
from collections import deque
from typing import TYPE_CHECKING, Optional

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static
from rich.text import Text

if TYPE_CHECKING:
    from auto_battery_research.workflow.stage_manager import StageManager

# 实时推理流滚动窗口行数上限 (超长自动淘汰最旧行, 保证长时间运行不卡)
REASONING_MAX_LINES = 400
# 单行截断长度 (工具入参 JSON 等长文本截断展示, 避免横向撑爆面板)
REASONING_LINE_MAX_CHARS = 200


class MessagesPanel(VerticalScroll, can_focus=True):
    """右侧面板：模型接收任务后的实时推理流程 (ReAct 链) + 阶段指引 + 研报渲染 ."""

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
        # 视图状态机: guide(阶段指引) | reasoning(实时推理流) | custom(研报等自定义内容)
        self._view_mode: str = "guide"
        self._reasoning_lines: deque = deque(maxlen=REASONING_MAX_LINES)

    def compose(self) -> ComposeResult:
        yield Markdown(id="guide-markdown")
        yield Static(id="reasoning-feed")
        yield Static(id="diagnostic-box")

    def on_mount(self) -> None:
        # 推理流默认隐藏, run 启动时由 begin_reasoning 接管显示
        try:
            self.query_one("#reasoning-feed", Static).display = False
        except Exception:
            pass
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
        # 实时推理流展示中: 不被周期刷新/阶段事件打断 (结束由 end_reasoning/show_text 切回)
        if self._view_mode == "reasoning":
            return
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
            self._view_mode = "custom"
            self._showing_custom_text = True
            self.border_title = f"Messages ── {title}"
            self.query_one("#reasoning-feed", Static).display = False
            self.query_one("#guide-markdown", Markdown).display = True
            self.query_one("#guide-markdown", Markdown).update(content)
        except Exception:
            pass

    # ---------- 实时推理流 (模型接收任务后的 ReAct 推理过程) ----------

    def begin_reasoning(self, title: str = "Agent 推理流程") -> None:
        """进入实时推理视图: 接管右侧面板, 流式展示模型思维链/工具调用/观测."""
        self._view_mode = "reasoning"
        self._showing_custom_text = False
        self._reasoning_lines.clear()
        self.border_title = f"Messages ── 🧠 {title} (实时)"
        try:
            self.query_one("#guide-markdown", Markdown).display = False
            self.query_one("#reasoning-feed", Static).display = True
        except Exception:
            pass
        self._render_reasoning()

    def append_reasoning(self, text: str, style: str = "white") -> None:
        """追加一条推理流程行 (贴近底部时自动跟随滚动, 手动上翻回看不打断)."""
        if self._view_mode != "reasoning":
            return
        line = " ".join(str(text).split())[:REASONING_LINE_MAX_CHARS]
        if not line:
            return
        try:
            follow = self.scroll_y >= (self.max_scroll or 0) - 3
        except Exception:
            follow = True
        self._reasoning_lines.append((line, style))
        self._render_reasoning()
        if follow:
            try:
                self.scroll_end(animate=False)
            except Exception:
                pass

    def end_reasoning(self) -> None:
        """退出推理视图: 交还面板给阶段指南/研报 (推理留痕仍在底部 Console)."""
        self._view_mode = "guide"
        self.border_title = "Messages"
        try:
            self.query_one("#reasoning-feed", Static).display = False
            self.query_one("#guide-markdown", Markdown).display = True
        except Exception:
            pass
        self._last_stage_id = None  # 强制下次刷新重渲染指南
        self.update_content(force=True)

    def _render_reasoning(self) -> None:
        feed = Text()
        if not self._reasoning_lines:
            feed.append("🧠 模型已接收任务, 等待推理流…\n", style="dim")
        for line, style in self._reasoning_lines:
            feed.append(f"{line}\n", style=style)
        try:
            self.query_one("#reasoning-feed", Static).update(feed)
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
