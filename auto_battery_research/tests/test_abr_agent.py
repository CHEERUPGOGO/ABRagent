"""Unit tests for ABRAgent core class and ReAct loop.

注入 dummy_key 使单测保持离线确定性 (不真实调用线上 LLM API)；
在线 ReAct 行为由 external 标记的集成测试覆盖。
"""

import pytest
from auto_battery_research.agent import ABRAgent

# 覆盖 setting.yaml 中的真实 key，保证 CI/单测环境零外部依赖
OFFLINE_CONFIG = {"openai": {"openai_api_key": "dummy_key"}, "llm": {"api_key": "dummy_key"}}


def test_abr_agent_initialization():
    agent = ABRAgent(goal="测试锂金属电池方案", skip_pinn=True, verbose=False, enable_file_log=False, config=OFFLINE_CONFIG)
    assert agent.goal == "测试锂金属电池方案"
    assert len(agent.all_tools) == 17  # 11 domain (含 MP 查询 / ReviseGeometry) + 6 stage
    assert "InspectLiteratureAssets" in agent.tool_map
    assert "RunRAGDesign" in agent.tool_map
    assert "Check" in agent.tool_map
    assert "Complete" in agent.tool_map
    assert agent.manager.target_goal == "测试锂金属电池方案"


def test_abr_agent_run_loop(tmp_path, monkeypatch):
    # enable_file_log=False：文件日志已默认开启，单测显式关闭避免在仓库 log/ 下落测试日志
    agent = ABRAgent(goal="测试端到端方案设计", skip_pinn=True, verbose=False, enable_file_log=False, config=OFFLINE_CONFIG)
    # 重置工作流
    agent.manager.reset_workflow()

    # 显式提供离线脚本审核产物，不依赖过去落盘的“Reviewer异常却high”旧文件。
    from auto_battery_research.tests.test_stage4_golden import (
        _install_scripted_llm, _build_seeded_pipeline, _run_adapter, GOLDEN_WRITER_DRAFT,
    )
    _install_scripted_llm(monkeypatch, GOLDEN_WRITER_DRAFT)
    pipeline = _build_seeded_pipeline(tmp_path)
    prepared = _run_adapter(pipeline, agent.manager.get_task_output_dir(), agent.goal)
    assert prepared["success"], prepared.get("error")

    events = list(agent.run_stream())
    assert len(events) >= 5

    event_types = [e["event"] for e in events]
    assert "start" in event_types
    assert "finish" in event_types

    # 验证是否全部通过
    assert agent.manager.is_all_completed() is True


def test_completed_stage_does_not_run_simulation_again():
    """LLM 已在本轮推进后，兜底调度不能再次覆盖仿真归档。"""
    from types import SimpleNamespace
    stage = SimpleNamespace(status="IN_PROGRESS", name="PINN")
    def complete(*args, **kwargs):
        stage.status = "PASSED"
        return {"messages": []}
    def unexpected(**kwargs):
        pytest.fail("阶段完成后不应重复仿真")
    agent = ABRAgent.__new__(ABRAgent)
    agent.config = {"llm": {"api_key": "offline-script"}}
    agent.goal = "仿真去重测试"
    agent.messages = []
    agent.log = lambda *args: None
    agent.manager = SimpleNamespace(get_stage_by_id=lambda i: stage)
    agent.backend = SimpleNamespace(invoke=complete)
    agent.tool_map = {"RunPhysicsSimulation": SimpleNamespace(_run=unexpected)}
    agent._execute_agent_decision_loop(5, 1)
