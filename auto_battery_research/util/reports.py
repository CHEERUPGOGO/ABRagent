"""研报定位共享工具 — 最终研报 fallback 链的单一事实源.

此前同一回退链在 cli.py / agent.py / web/server.py / web/app.py / backend/loop_runner.py
各存一份副本, 且判据已经漂移 (is_legacy_goal(goal) vs 恒真的 is_legacy_task 方法引用)。
统一收敛到这里: 课题目录内 final_research_report → final_report → synthesis_report
→ (可选) design_scheme; 仅历史遗留课题 (is_legacy) 再回退到全局
output/auto_battery_research/ 旧产物, 新课题绝不把全局旧研报冒充本课题产物。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

# 研报文件名候选 (按优先级排序)
REPORT_CANDIDATES: Tuple[str, ...] = (
    "final_research_report.md",
    "final_report.md",
    "battery_research_synthesis_report.md",
)
# Stage 4 设计方案 (研报缺失时的展示回退, 仅消费方显式开启)
SCHEME_CANDIDATE = "design_scheme.md"


def publish_report_audit(path: Path, stages) -> None:
    """终审后仅更新审计状态，原子发布；写入错误交由调用方显式处理。"""
    import os
    import re
    import uuid

    text = path.read_text(encoding="utf-8")
    skipped = [s.id for s in stages if s.status == "SKIPPED"]
    fallback = [s.id for s in stages if s.status == "FALLBACK"]
    summary = "全流程 6 阶段门禁检查全部通过"
    if skipped:
        summary = f"必检阶段门禁全部通过；Stage {skipped} 按配置跳过"
    if fallback:
        summary = f"流程完成；Stage {fallback} FALLBACK (代理估算)"
    text, count = re.subn(r"^- 阶段审计:.*$", lambda _: f"- 阶段审计: {summary}", text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError("报告缺少阶段审计字段，无法发布终审结论")
    text = re.sub(
        r"(Stage 6 \(综合研报生成\): 状态 )\[[^\]]*\]",
        r"\1[PASSED]", text,
    )
    temporary = path.with_suffix(f".tmp.{uuid.uuid4().hex}")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def resolve_final_report(
    task_dir: Path,
    is_legacy: bool = False,
    scheme_fallback: bool = False,
    global_dir: Optional[Path] = None,
) -> Optional[Path]:
    """定位课题最终研报文件, 返回第一个存在的候选路径; 全部缺失时返回 None.

    Args:
        task_dir: 课题产物目录 (StageManager.get_task_output_dir 的结果)。
        is_legacy: 是否为被认领的历史遗留课题 —— 仅此类课题允许回退全局旧产物。
        scheme_fallback: 研报全缺时是否回退 design_scheme.md (Agent/Web 展示层使用)。
        global_dir: 全局 legacy 产物目录 (通常 <repo>/output/auto_battery_research)。
    """
    candidates = list(REPORT_CANDIDATES) + ([SCHEME_CANDIDATE] if scheme_fallback else [])
    for name in candidates:
        p = task_dir / name
        if p.exists():
            return p
    if is_legacy and global_dir is not None:
        for name in candidates:
            p = global_dir / name
            if p.exists():
                return p
    return None
