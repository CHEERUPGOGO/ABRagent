"""包级惰性导入回归测试.

背景: 包 __init__ 曾急切导入 ABRAgent / RAG 门面 / langchain 全家, 导致
`abr-cli --mcp` 冷启动到握手就绪实测 30s+，超过 MCP 客户端默认 30s 握手
超时被误判连接失败。现改为 PEP 562 惰性导出 —— 本文件锁定该行为不回退。

全部断言在子进程中进行, 不污染本测试进程的 sys.modules。
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

# 探针: 仅 import 包本身, 检查重依赖链是否被连带拉起
_LAZY_PROBE = (
    "import sys, json, auto_battery_research; "
    "print(json.dumps({"
    "'agent': 'auto_battery_research.agent' in sys.modules, "
    "'cli': 'auto_battery_research.cli' in sys.modules, "
    "'rag_facade': 'auto_battery_research.rag' in sys.modules, "
    "'simulation_facade': 'auto_battery_research.simulation' in sys.modules, "
    "'src_rag': any(m == 'src.lmllm' or m.startswith('src.lmllm.') for m in sys.modules), "
    "'langchain': any(m == 'langchain' or m.startswith('langchain.') for m in sys.modules), "
    "'numpy': 'numpy' in sys.modules, "
    "}))"
)


def _run_py(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT_DIR),
        timeout=300,
    )


def test_package_import_is_lazy():
    """import auto_battery_research 不得连带拉起 agent/RAG/langchain/numpy 重链."""
    r = _run_py(_LAZY_PROBE)
    assert r.returncode == 0, r.stderr
    loaded = json.loads(r.stdout.strip().splitlines()[-1])
    for heavy in ("agent", "cli", "rag_facade", "simulation_facade", "src_rag", "langchain", "numpy"):
        assert loaded[heavy] is False, f"急切导入了重依赖: {heavy}"


def test_lazy_attributes_resolve():
    """惰性导出的符号首次属性访问可正常解析并缓存 (选用轻量条目, 不触发重链)."""
    code = (
        "import sys\n"
        "import auto_battery_research as abr\n"
        "from auto_battery_research import StageManager, AutonomousLoopRunner, cli_main\n"
        "assert abr.StageManager is StageManager\n"
        "assert abr.AutonomousLoopRunner is AutonomousLoopRunner\n"
        "assert callable(cli_main)\n"
        "import auto_battery_research.pipeline as pl\n"
        "assert abr.pipeline is pl\n"
        "# 缓存生效: 命名空间已落位, 不再依赖 __getattr__\n"
        "assert 'StageManager' in vars(abr)\n"
        "# 未知属性按标准 AttributeError 抛出\n"
        "try:\n"
        "    abr.not_exist\n"
        "    raise SystemExit('should raise AttributeError')\n"
        "except AttributeError:\n"
        "    pass\n"
        "assert 'cli' in dir(abr) and 'ABRAgent' in dir(abr)\n"
        "print('lazy-ok')\n"
    )
    r = _run_py(code)
    assert r.returncode == 0, r.stderr
    assert "lazy-ok" in r.stdout
