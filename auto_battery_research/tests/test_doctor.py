"""doctor 环境自检离线单测 — ReAct 运行时检查项.

背景: pyproject base 依赖曾不含 langchain/langgraph，二者缺失时 backend 退化为
裸 bind_tools 模型，invoke 仍传 LangGraph dict 协议 → 每阶段必抛
[LLM-Notice] Invalid input type <class 'dict'>。--doctor 新增 ReAct 运行时
检查项，让该症状在自检阶段即可定位，本文件锁死其判定逻辑。
"""

import sys

import pytest

from auto_battery_research.util import doctor

pytestmark = pytest.mark.unit


def test_agent_runtime_row_ok_when_runtime_present():
    """本环境 (base 依赖) 已装 langchain/langgraph 时应报 OK."""
    try:
        from langchain.agents import create_agent  # noqa: F401
        from langgraph.prebuilt import create_react_agent  # noqa: F401
    except Exception as e:  # pragma: no cover - 仅在残缺环境触发
        pytest.skip(f"环境未安装完整 ReAct 运行时: {e}")
    row = doctor._agent_runtime_row()
    assert row[0] == "ReAct 运行时"
    assert row[1] == doctor.OK
    assert "就绪" in row[2]


def test_agent_runtime_row_warn_when_both_missing(monkeypatch):
    """两个编译入口同时缺失 (朋友踩中的场景) → WARN + 症状特征提示.

    sys.modules 置 None 模拟 ImportError: `from X import Y` 直接失败，
    不触碰真实已导入模块，monkeypatch 结束后自动还原。
    """
    monkeypatch.setitem(sys.modules, "langchain.agents", None)
    monkeypatch.setitem(sys.modules, "langgraph.prebuilt", None)
    row = doctor._agent_runtime_row()
    assert row[0] == "ReAct 运行时"
    assert row[1] == doctor.WARN
    assert "均缺失" in row[2]
    # 修复建议必须带上日志特征，方便按图索骥
    assert "[LLM-Notice]" in row[3]
    assert "Invalid input type" in row[3]


def test_agent_runtime_row_warn_when_single_missing(monkeypatch):
    """仅一个入口缺失 → 仍可回退另一入口编译 ReAct，WARN 但不判 FAIL."""
    monkeypatch.setitem(sys.modules, "langchain.agents", None)
    row = doctor._agent_runtime_row()
    assert row[1] == doctor.WARN
    assert "create_agent" in row[2]
    assert "回退" in row[3]


def test_run_doctor_checks_includes_agent_runtime_row():
    """自检报告必须包含 ReAct 运行时条目 (Ollama 探测离线失败不影响)."""
    rows = doctor.run_doctor_checks()
    names = [r[0] for r in rows]
    assert "ReAct 运行时" in names, f"doctor 检查项缺失: {names}"
