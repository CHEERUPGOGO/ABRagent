"""命令行入口启动回归，不创建课题或调用外部服务。"""

import subprocess
import sys

import pytest


@pytest.mark.unit
def test_console_entrypoint_help():
    result = subprocess.run(
        [sys.executable, "-c", "from auto_battery_research.cli import main; main()", "--help"],
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert b"--scheme-show" in result.stdout
    assert b"--trace" in result.stdout
