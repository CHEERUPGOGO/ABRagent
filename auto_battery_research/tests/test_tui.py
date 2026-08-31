"""Test Textual TUI for AutoBatteryResearch."""

import pytest

# 若环境未安装 Textual，自动优雅跳过而不阻断全局测试收集
textual = pytest.importorskip("textual", reason="Textual is required for TUI testing")

from auto_battery_research.workflow.stage_manager import StageManager
from auto_battery_research.tui.app import BatteryAgentTUI


@pytest.mark.asyncio
async def test_battery_agent_tui_headless():
    """测试 TUI 无头启动与基础渲染."""
    mgr = StageManager(skip_pinn=True)
    app = BatteryAgentTUI(manager=mgr)
    async with app.run_test() as pilot:
        # 等待挂载完成
        await pilot.pause()
        assert app.is_mounted
        
        # 测试控制台指令
        app.handle_command("status")
        await pilot.pause()
        
        app.handle_command("detail")
        await pilot.pause()
        
        app.handle_command("tips")
        await pilot.pause()
        
        app.handle_command("check")
        await pilot.pause()
