"""Wheel 打包完整性回归测试.

背景 (2026-09 P0 修复): pyproject 打包曾存在两处实质缺陷 ——
1. RelationEngine 运行时数据 (src/lmllm/RAG/data/*.json) 未入 wheel。
   其 _load 对缺失文件静默降级 (空表继续跑)，安装态 C1-C8 硬约束
   门禁沦为空校验，且 RelationEngine 从不抛错、极难被发现；
2. packages.find 同时纳入 src* 与 lmllm*，同一引擎以 src.lmllm 与
   lmllm 两套命名空间重复分发。

本文件在 zip 层面直接断言 wheel 内容，锁死上述回归。构建使用
--no-deps --no-build-isolation，全程离线。
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def _build_wheel(out_dir: Path) -> Path:
    """把当前仓库构建成 wheel (不解析依赖、不联网、不用构建隔离).

    先清掉仓库根的 build/ 暂存目录 —— bdist_wheel 会整树打包 build/lib，
    历史构建残留 (如已从 packages.find 移除的 src.*) 会被原样带进 wheel，
    造成"配置已改、产物仍旧"的假象。
    """
    shutil.rmtree(ROOT_DIR / "build", ignore_errors=True)
    try:
        r = subprocess.run(
            [
                sys.executable, "-m", "pip", "wheel", ".",
                "--no-deps", "--no-build-isolation",
                "-w", str(out_dir),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT_DIR),
            timeout=300,
        )
    except FileNotFoundError:
        pytest.skip("当前环境无 pip，跳过打包回归")
    assert r.returncode == 0, f"wheel 构建失败:\n{r.stdout}\n{r.stderr}"
    wheels = list(out_dir.glob("*.whl"))
    assert wheels, "构建成功但未产出 wheel 产物"
    return wheels[0]


@pytest.fixture(scope="module")
def wheel_names(tmp_path_factory) -> list:
    """模块级只构建一次 wheel，三个用例共享产物文件名清单."""
    whl = _build_wheel(tmp_path_factory.mktemp("whlbuild"))
    with zipfile.ZipFile(whl) as z:
        return z.namelist()


def test_wheel_contains_rag_runtime_data(wheel_names):
    """RelationEngine 三张核心数据表及 data 子目录必须入包，缺失即门禁空转."""
    for required in ("alias_map.json", "candidates.json", "constraints.json"):
        assert f"lmllm/RAG/data/{required}" in wheel_names, f"核心数据表漏打包: {required}"
    nested = [
        n for n in wheel_names
        if n.startswith("lmllm/RAG/data/") and n.endswith(".json") and n.count("/") == 4
    ]
    assert nested, "RAG data 子目录 (calibrated/seed/tasks) 未入 wheel"


def test_wheel_single_namespace(wheel_names):
    """wheel 只允许 lmllm 命名空间，禁止 src/ 前缀条目重复分发."""
    offenders = [n for n in wheel_names if n.startswith("src/")]
    assert not offenders, f"src.* 命名空间泄漏进打包: {offenders[:5]}"


def test_wheel_contains_core_runtime_resources(wheel_names):
    """setting.yaml / 工作流 yaml / TUI 样式 / Web 静态资源必须随包分发."""
    for required in (
        "auto_battery_research/setting.yaml",
        "auto_battery_research/workflow/abr_workflow.yaml",
    ):
        assert required in wheel_names, f"运行时资源漏打包: {required}"
    assert any(
        n.startswith("auto_battery_research/tui/styles/") for n in wheel_names
    ), "TUI tcss 样式漏打包"
    assert any(
        n.startswith("auto_battery_research/web/templates/") for n in wheel_names
    ), "Web 模板漏打包"
