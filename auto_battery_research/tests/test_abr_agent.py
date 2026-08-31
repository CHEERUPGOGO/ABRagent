"""Unit tests for ABRAgent core class and ReAct loop.

注入 dummy_key 使单测保持离线确定性 (不真实调用线上 LLM API)；
在线 ReAct 行为由 external 标记的集成测试覆盖。
"""

import pytest
from auto_battery_research.agent import ABRAgent

# 覆盖 setting.yaml 中的真实 key，保证 CI/单测环境零外部依赖
OFFLINE_CONFIG = {"openai": {"openai_api_key": "dummy_key"}, "llm": {"api_key": "dummy_key"}}


def test_abr_agent_initialization():
    agent = ABRAgent(goal="测试锂金属电池方案", skip_pinn=True, verbose=False, config=OFFLINE_CONFIG)
    assert agent.goal == "测试锂金属电池方案"
    assert len(agent.all_tools) == 15  # 9 domain (Stage 4 已收敛为 RunRAGDesign 单服务) + 6 stage
    assert "InspectLiteratureAssets" in agent.tool_map
    assert "RunRAGDesign" in agent.tool_map
    assert "Check" in agent.tool_map
    assert "Complete" in agent.tool_map
    assert agent.manager.target_goal == "测试锂金属电池方案"


def test_abr_agent_run_loop():
    agent = ABRAgent(goal="测试端到端方案设计", skip_pinn=True, verbose=False, config=OFFLINE_CONFIG)
    # 重置工作流
    agent.manager.reset_workflow()

    events = list(agent.run_stream())
    assert len(events) >= 5

    event_types = [e["event"] for e in events]
    assert "start" in event_types
    assert "finish" in event_types

    # 验证是否全部通过
    assert agent.manager.is_all_completed() is True
