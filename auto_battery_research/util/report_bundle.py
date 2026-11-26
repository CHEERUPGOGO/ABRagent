"""主研报与独立补充材料的版本绑定及回滚保护发布。"""
from __future__ import annotations

import re
from pathlib import Path

from .scheme_ledger import atomic_write, task_lock

MAIN_FILENAME = "final_research_report.md"
SUPPLEMENT_FILENAME = "final_research_report_supplement.md"
MARKER = "<!-- abr-report-bundle:1 -->"


def validate_parts(main: str, supplement: str | None) -> list[str]:
    """旧单文件报告兼容读取，新报告必须有同一版本的补充材料。"""
    if "<!-- abr-report-bundle:" not in main:
        return []
    if main.count(MARKER) != 1 or not supplement or supplement.count(MARKER) != 1:
        return ["独立补充材料缺失或报告分文件契约不支持"]
    for name in ("identity", "record"):
        pattern = r"<!-- abr-report-" + name + r":[^\r\n>]+ -->"
        expected = re.findall(pattern, main)
        actual = re.findall(pattern, supplement)
        if expected != actual or (name == "identity" and len(expected) != 1):
            return ["主研报与补充材料没有使用同一当前记录"]
    if "### 阶段执行履历" not in supplement:
        return ["独立补充材料缺少阶段执行履历"]
    return []


def load_supplement(report_path: Path, main: str) -> str | None:
    if "<!-- abr-report-bundle:" not in main:
        return None
    try:
        supplement = (report_path.parent / SUPPLEMENT_FILENAME).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("独立补充材料缺失或无法读取，请重新生成研报") from exc
    errors = validate_parts(main, supplement)
    if errors:
        raise ValueError("；".join(errors))
    return supplement


def publish_files(task_dir, files):
    """同一课题锁内逐个原子替换；任一失败恢复全部原文件。"""
    task = Path(task_dir)
    with task_lock(task):
        replace_files(task, files)


def replace_files(task_dir, files):
    """沿用调用方已有锁边界；不在审核发布等已有事务中嵌套课题锁。"""
    task = Path(task_dir)
    previous = {name: (task / name).read_bytes() if (task / name).exists() else None for name in files}
    try:
        for name, content in files.items():
            atomic_write(task / name, content)
    except Exception:
        for name, content in previous.items():
            if content is None:
                (task / name).unlink(missing_ok=True)
            else:
                (task / name).write_bytes(content)
        raise
