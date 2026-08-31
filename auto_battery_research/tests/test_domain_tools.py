"""Unit tests for Stage 1~6 domain tools and stage tools in AutoBatteryResearch."""

import json
import pytest
from pathlib import Path
from auto_battery_research.tools.domain_tools import (
    InspectLiteratureAssetsTool,
    InspectVectorDBTool,
    InspectCellEntitiesTool,
    RunRAGDesignTool,
    RunPhysicsSimulationTool,
    SynthesizeResearchReportTool,
    get_all_domain_tools,
)
from auto_battery_research.tools.stage_tools import (
    ToolCurrentTips,
    ToolStatus,
    ToolCheck,
    ToolComplete,
    ToolSetStageJournal,
    ToolGetAllStageJournal,
    get_all_stage_tools,
)
from auto_battery_research.workflow.stage_manager import StageManager
from auto_battery_research.tools.stage_tools import set_stage_manager


@pytest.fixture(autouse=True)
def init_stage_manager():
    mgr = StageManager(target_goal="单元测试电池研发方案")
    set_stage_manager(mgr)
    return mgr


def test_domain_tools_instantiation():
    tools = get_all_domain_tools()
    assert len(tools) == 9  # Stage 4 四个细粒度工具已收敛为 RunRAGDesign 单链路服务
    names = [t.name for t in tools]
    assert "InspectLiteratureAssets" in names
    assert "InspectVectorDB" in names
    assert "InspectCellEntities" in names
    assert "RunRAGDesign" in names
    assert "RunPhysicsSimulation" in names
    assert "SynthesizeResearchReport" in names


def test_stage_tools_instantiation():
    tools = get_all_stage_tools()
    assert len(tools) == 6
    names = [t.name for t in tools]
    assert "CurrentTips" in names
    assert "Status" in names
    assert "Check" in names
    assert "Complete" in names
    assert "SetStageJournal" in names
    assert "AllStageJournal" in names


def test_stage1_inspect_tool():
    tool = InspectLiteratureAssetsTool()
    res = json.loads(tool._run())
    assert "total_markdown_papers" in res
    assert "ready_for_next_stage" in res


def test_stage2_inspect_tool():
    tool = InspectVectorDBTool()
    res = json.loads(tool._run())
    assert "ready_for_rag" in res


def test_stage3_inspect_tool():
    tool = InspectCellEntitiesTool()
    res = json.loads(tool._run())
    assert "has_mining_assets" in res


def test_stage4_single_service_tool(monkeypatch, init_stage_manager):
    """Stage 4 已收敛为唯一 RunRAGDesign 单链路服务: 验证参数传递、课题回退与规则版本溯源.

    通过 monkeypatch 替换底层 run_rag_design 服务，保持单测离线确定性
    (真实 Planner/Retrieval/Writer/Reviewer 管线由 external 集成测试覆盖)。
    """
    from src.lmllm.RAG.relation_engine import RULES_VERSION

    calls = {}

    def fake_run_rag_design(target_query="", design_query=None, **kwargs):
        calls["target_query"] = target_query
        calls["design_query"] = design_query
        return {
            "success": True,
            "review_status": "APPROVED",
            "scheme_json": "design_scheme.json",
            "key_findings": {"evidence_count": 3},
        }

    monkeypatch.setattr(
        "auto_battery_research.tools.domain_tools.run_rag_design", fake_run_rag_design
    )

    tool = RunRAGDesignTool()
    assert tool.name == "RunRAGDesign"

    # 1. 显式课题 + 设计需求透传
    res = json.loads(tool._run(target_goal="设计400Wh/kg锂金属电池", design_query="高镍三元正极体系"))
    assert res["review_status"] == "APPROVED"
    assert calls["target_query"] == "设计400Wh/kg锂金属电池"
    assert calls["design_query"] == "高镍三元正极体系"

    # 2. 课题留空时回退到当前工作流活跃课题；design_query 留空传 None
    res2 = json.loads(tool._run())
    assert res2["success"] is True
    assert calls["target_query"] == init_stage_manager.target_goal
    assert calls["design_query"] is None

    # 3. RelationEngine 规则版本标识 (provenance.rules_version 溯源锚点)
    assert RULES_VERSION.startswith("C1-C8")


def test_stage_tools_execution(init_stage_manager):
    tips_tool = ToolCurrentTips()
    tips = tips_tool._run()
    assert len(tips) > 0

    status_tool = ToolStatus()
    status = json.loads(status_tool._run())
    assert "current_stage_id" in status

    check_tool = ToolCheck()
    check_res = json.loads(check_tool._run())
    assert "passed" in check_res
