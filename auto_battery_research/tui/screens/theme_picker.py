"""ThemePicker screen for AutoBatteryResearch TUI."""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.containers import Vertical


class ThemePickerScreen(ModalScreen[str]):
    """主题切换对话框."""

    THEMES = [
        "textual-dark",
        "textual-light",
        "dracula",
        "tokyo-night",
        "monokai",
        "nord",
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="theme-dialog"):
            yield Static("🎨 请选择终端控制台主题 (按 Enter 确认, Esc 取消):")
            yield OptionList(*self.THEMES, id="theme-list")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        selected_theme = self.THEMES[event.option_index]
        self.dismiss(selected_theme)

    def key_escape(self) -> None:
        self.dismiss(None)
