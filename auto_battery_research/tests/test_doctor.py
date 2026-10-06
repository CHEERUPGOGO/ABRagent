"""doctor 环境自检离线单测 — ReAct 运行时与 PINN 模型资产检查项.

背景: pyproject base 依赖曾不含 langchain/langgraph，二者缺失时 backend 退化为
裸 bind_tools 模型，invoke 仍传 LangGraph dict 协议 → 每阶段必抛
[LLM-Notice] Invalid input type <class 'dict'>。--doctor 新增 ReAct 运行时
检查项，让该症状在自检阶段即可定位，本文件锁死其判定逻辑。
另锁死 PINN 仿真环境检查项: numpy 缺失 FAIL / registry 缺失或资产残缺 WARN /
资产齐备 OK (trigger 开关只影响 detail)，以及 PyBaMM 检测项的退役回归锁。
"""

import json
import sys
from pathlib import Path

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


# ────────────────────────── PINN 仿真环境检查项 ──────────────────────────


def _fabricate_pinn_assets(models_dir: Path) -> None:
    """伪造一套与真实布局一致的完整 PINN 资产 (registry 内路径相对 MODELS_DIR)."""
    sys_rel = "systems/FakeA_FakeB"
    (models_dir / sys_rel / "weights").mkdir(parents=True)
    (models_dir / sys_rel / "params.json").write_text("{}", encoding="utf-8")
    (models_dir / sys_rel / "weights" / "discharge_ne.npz").write_bytes(b"PK\x03\x04")
    (models_dir / sys_rel / "weights" / "discharge_pe.npz").write_bytes(b"PK\x03\x04")
    registry = {"schema_version": "1.0", "systems": [{
        "system_id": "FakeA_FakeB",
        "match": {"cathode": ["FakeA"], "anode": ["FakeB"]},
        "params": f"{sys_rel}/params.json",
        "directions": {"discharge": {"ne": f"{sys_rel}/weights/discharge_ne.npz",
                                     "pe": f"{sys_rel}/weights/discharge_pe.npz"},
                       "charge": None},
    }]}
    (models_dir / "registry.json").write_text(json.dumps(registry), encoding="utf-8")


def test_pinn_row_ok_with_real_repo_assets():
    """真实仓库资产 (GrSi_NMC811) 齐备 → OK；trigger 开启反映在 detail."""
    row = doctor._pinn_row({"enabled": True})
    assert row[0] == "PINN 仿真环境"
    assert row[1] == doctor.OK, f"真实资产应全绿: {row}"
    assert "GrSi_NMC811" in row[2]
    assert "trigger=开启" in row[2]


def test_pinn_row_ok_with_fabricated_assets(tmp_path):
    """tmp 伪造完整资产 → OK，且逻辑不依赖真实仓库；trigger 关闭只改 detail 不降级."""
    _fabricate_pinn_assets(tmp_path)
    row = doctor._pinn_row({}, models_dir=tmp_path)
    assert row[1] == doctor.OK, f"完整伪造资产应 OK: {row}"
    assert "FakeA_FakeB" in row[2]
    # kill-switch 关闭是有意配置: 不产生警告，仅 detail 标注
    assert "总开关关闭" in row[2]


def test_pinn_row_warn_when_registry_missing(tmp_path):
    """空目录 (registry.json 缺失) → WARN，Stage 5 全部回退参数提取."""
    row = doctor._pinn_row({}, models_dir=tmp_path)
    assert row[1] == doctor.WARN
    assert "registry.json" in row[2]


def test_pinn_row_warn_when_system_incomplete(tmp_path):
    """注册表声明体系但权重 npz 缺失 (只拷一半) → WARN 且点名体系与文件."""
    _fabricate_pinn_assets(tmp_path)
    (tmp_path / "systems" / "FakeA_FakeB" / "weights" / "discharge_pe.npz").unlink()
    row = doctor._pinn_row({"enabled": True}, models_dir=tmp_path)
    assert row[1] == doctor.WARN
    assert "FakeA_FakeB" in row[2]
    assert "discharge_pe.npz" in row[2]


def test_pinn_row_fail_when_numpy_missing(monkeypatch):
    """numpy 缺失 (base 依赖) → FAIL 而非 WARN: PINN 前向完全无法运行."""
    monkeypatch.setitem(sys.modules, "numpy", None)
    row = doctor._pinn_row({})
    assert row[1] == doctor.FAIL
    assert "numpy" in row[2]


def test_no_pybamm_in_doctor():
    """回归锁: PyBaMM 检测项已退役 — extras 表与自检结果均不得再出现."""
    for label, module, _hint in doctor._OPTIONAL_EXTRAS:
        assert "pybamm" not in module.lower(), f"extras 表残留 PyBaMM: {label}"
    rows = doctor.run_doctor_checks()
    names = [r[0] for r in rows]
    assert not any("pybamm" in n.lower() for n in names), f"自检结果残留 PyBaMM: {names}"
    # 新 PINN 检查项必须在报告中
    assert "PINN 仿真环境" in names
