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
