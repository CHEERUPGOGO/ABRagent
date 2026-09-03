"""Unit tests for Stage-specific Tool Partitioning and Default Argument Resolution."""

import json
import pytest
from auto_battery_research.agent import ABRAgent, STAGE_ALLOWED_DOMAIN_TOOLS, COMMON_STAGE_TOOLS
from auto_battery_research.tools.domain_tools import (
    SynthesizeResearchReportArgs,
    SynthesizeResearchReportTool,
    RunPhysicsSimulationArgs,
    RunPhysicsSimulationTool,
    ExtractAndAssembleCellsArgs,
    ExtractAndAssembleCellsTool,
)
from auto_battery_research.tools.workflow_actions import run_rag_design, run_synthesis_report
from auto_battery_research.tools.stage_tools import get_stage_manager

OFFLINE_CONFIG = {"openai": {"openai_api_key": "dummy_key"}, "llm": {"api_key": "dummy_key"}}


def test_stage_tools_partitioning():
    """验证阶段专属工具裁剪机制：Stage 4 绝对不能看到 Stage 6 的研报工具或 Stage 1 的文献工具."""
    agent = ABRAgent(goal="设计500wh/kg高比能液态锂金属电池方案", config=OFFLINE_CONFIG, verbose=False, enable_file_log=False)
    
    # 1. 全局工具箱完整性保留 (向前兼容)
    assert len(agent.all_tools) == 15

    # 2. Stage 4 专属工具检查 (物理隔离)
    s4_tools = agent.backend.get_stage_tools(stage_id=4)
    s4_tool_names = [t.name for t in s4_tools]
    
    assert "RunRAGDesign" in s4_tool_names
    assert "Check" in s4_tool_names
    assert "Complete" in s4_tool_names
    assert "CurrentTips" in s4_tool_names
    assert "Status" in s4_tool_names
    assert "SetStageJournal" in s4_tool_names
    
    # 核心验证：严禁 Stage 6 工具抢跑
    assert "SynthesizeResearchReport" not in s4_tool_names
    assert "InspectLiteratureAssets" not in s4_tool_names
    assert "ExtractAndAssembleCells" not in s4_tool_names
    assert "RunPhysicsSimulation" not in s4_tool_names

    # 3. Stage 1 专属工具检查
    s1_tools = agent.backend.get_stage_tools(stage_id=1)
    s1_tool_names = [t.name for t in s1_tools]
    assert "InspectLiteratureAssets" in s1_tool_names
    assert "IngestLiteraturePapers" in s1_tool_names
    assert "RunRAGDesign" not in s1_tool_names
    assert "SynthesizeResearchReport" not in s1_tool_names


def test_tool_arguments_no_hardcoded_400wh(monkeypatch):
    """验证所有工具默认参数不再硬编码 400Wh/kg，并且动态绑定当前活跃课题."""
    mgr = get_stage_manager()
    mgr.target_goal = "设计500wh/kg高比能液态锂金属电池方案"

    # 1. SynthesizeResearchReport
    args = SynthesizeResearchReportArgs()
    assert args.target_goal == ""

    tool_rep = SynthesizeResearchReportTool()
    monkeypatch.setattr("auto_battery_research.tools.domain_tools.generate_synthesis_report", lambda target_query: {"goal": target_query})
    res_rep = json.loads(tool_rep._run())
    assert res_rep["goal"] == "设计500wh/kg高比能液态锂金属电池方案"

    # 2. RunPhysicsSimulation
    args_pinn = RunPhysicsSimulationArgs()
    assert args_pinn.target_goal == ""

    tool_pinn = RunPhysicsSimulationTool()
    monkeypatch.setattr("auto_battery_research.tools.domain_tools.run_pinn_simulation", lambda target_query, current_rate: {"goal": target_query, "rate": current_rate})
    res_pinn = json.loads(tool_pinn._run())
    assert res_pinn["goal"] == "设计500wh/kg高比能液态锂金属电池方案"
    assert res_pinn["rate"] == "0.2C"

    # 3. ExtractAndAssembleCells
    tool_mine = ExtractAndAssembleCellsTool()
    monkeypatch.setattr("auto_battery_research.tools.domain_tools.run_data_mining", lambda max_files, target_query: {"goal": target_query})
    res_mine = json.loads(tool_mine._run())
    assert res_mine["goal"] == "设计500wh/kg高比能液态锂金属电池方案"
