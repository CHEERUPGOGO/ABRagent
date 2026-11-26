"""Unit tests for ABRAgent core class and ReAct loop.

注入 dummy_key 使单测保持离线确定性 (不真实调用线上 LLM API)；
Stage 5 脚本客户端显式注入，不依赖开发机环境中的 API Key；
在线 ReAct 行为由 external 标记的集成测试覆盖。
"""

import pytest
from auto_battery_research.agent import ABRAgent

# 覆盖 setting.yaml 中的真实 key，保证 CI/单测环境零外部依赖
OFFLINE_CONFIG = {"openai": {"openai_api_key": "dummy_key"}, "llm": {"api_key": "dummy_key"}}


def test_abr_agent_initialization():
    agent = ABRAgent(goal="测试锂金属电池方案", skip_pinn=True, verbose=False, enable_file_log=False, config=OFFLINE_CONFIG)
    assert agent.goal == "测试锂金属电池方案"
    assert len(agent.all_tools) == 18  # 11 domain (含 MP 查询 / ReviseGeometry) + 6 stage
    assert "InspectLiteratureAssets" in agent.tool_map
    assert "RunRAGDesign" in agent.tool_map
    assert "Check" in agent.tool_map
    assert "Complete" in agent.tool_map
    assert agent.manager.target_goal == "测试锂金属电池方案"


@pytest.mark.parametrize("experiment_model_available", [True, False], ids=["scripted", "unavailable"])
def test_abr_agent_run_loop(tmp_path, monkeypatch, experiment_model_available):
    from auto_battery_research.util import env_loader, report_recipe_llm
    # 复现 CI 无凭据环境；本机 .env 和环境变量不能决定实验分支。
    monkeypatch.setattr(env_loader, "load_env", lambda: False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # enable_file_log=False：文件日志已默认开启，单测显式关闭避免在仓库 log/ 下落测试日志
    agent = ABRAgent(goal="测试端到端方案设计", skip_pinn=True, verbose=False, enable_file_log=False, config=OFFLINE_CONFIG)
    # 每次测试独立目录，不能复用仓库中已提交方案或过期修复检查点。
    (tmp_path / "task").mkdir()
    agent.manager._task_dir_cache[agent.goal] = tmp_path / "task"
    # 重置工作流
    agent.manager.reset_workflow()

    # 显式提供离线脚本审核产物，不依赖过去落盘的“Reviewer异常却high”旧文件。
    from auto_battery_research.tests.test_stage4_golden import (
        _install_scripted_llm, _build_seeded_pipeline, GOLDEN_WRITER_DRAFT,
    )
    _install_scripted_llm(monkeypatch, GOLDEN_WRITER_DRAFT)
    pipeline = _build_seeded_pipeline(tmp_path)
    assert report_recipe_llm._llm_client(agent.manager) is None
    if experiment_model_available:
        # 仅模拟 chat/available 不能越过客户端工厂的凭据检查，须显式提供脚本客户端。
        monkeypatch.setattr(report_recipe_llm, "_llm_client", lambda mgr: pipeline.writer_llm)
    from auto_battery_research.tools.rag_adapter import AbrRagAdapter
    # 本例故意保留脚本目标与实际目标的解析缺口，走默认报告优先模式验证附条件交付。
    prepared = AbrRagAdapter(config={"paths": {}, "runtime_options": {"report_first": True}}, pipeline=pipeline).run_rag_design(
        target_query=agent.goal, task_dir=agent.manager.get_task_output_dir())
    assert prepared["success"], prepared.get("error")

    events = list(agent.run_stream())
    assert len(events) >= 5

    event_types = [e["event"] for e in events]
    assert "start" in event_types
    assert "finish" in event_types

    # 验证是否全部通过
    assert agent.manager.is_all_completed() is True
    import json
    execution = json.loads((agent.manager.get_task_output_dir() / "stage5_execution.json").read_text(encoding="utf-8"))
    assert execution["experiment"]["status"] == ("CONDITIONAL" if experiment_model_available else "FALLBACK")
    assert execution["experiment"]["reasons"]
    assert not execution["experiment"]["pending_fields"]
    recipe = json.loads((agent.manager.get_task_output_dir() / "experiment_plan.json").read_text(encoding="utf-8"))
    assert recipe["generated_stage"] == 5
    assert (recipe.get("origin") == "engineering_fallback") is (not experiment_model_available)


def test_completed_stage_does_not_run_simulation_again(monkeypatch):
    """LLM 已在本轮推进后，兜底调度不能再次覆盖仿真归档。"""
    from types import SimpleNamespace
    # 此处后端为本地函数桩，显式启用该分支，避免外部 dummy_key 跳过模拟调用。
    monkeypatch.setenv("OPENAI_API_KEY", "offline-script")
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
