"""研报定位共享工具 — 最终研报 fallback 链的单一事实源.

此前同一回退链在 cli.py / agent.py / web/server.py / web/app.py / backend/loop_runner.py
各存一份副本, 且判据已经漂移 (is_legacy_goal(goal) vs 恒真的 is_legacy_task 方法引用)。
统一收敛到这里: 课题目录内 final_research_report → final_report → synthesis_report
→ (可选) design_scheme; 仅历史遗留课题 (is_legacy) 再回退到全局
output/auto_battery_research/ 旧产物, 新课题绝不把全局旧研报冒充本课题产物。
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# 研报文件名候选 (按优先级排序)
REPORT_CANDIDATES: Tuple[str, ...] = (
    "final_research_report.md",
    "final_report.md",
    "battery_research_synthesis_report.md",
)
# Stage 4 设计方案 (研报缺失时的展示回退, 仅消费方显式开启)
SCHEME_CANDIDATE = "design_scheme.md"


_JOURNAL_TABLE_HEADER = (
    "| 阶段 | 阶段名称 | 阶段核心工作与所得 (Notes) | 主要交付物 (Deliverables) |\n"
    "|:---|:---|:---|:---|\n"
)
_JOURNAL_TABLE_FOOTER = "\n\n[详细阶段日志](stage_journals.json)"


def render_stage_journal_table(
    stages: Iterable[Any],
    journals: Optional[List[Dict[str, Any]]],
    force_stage6_passed: bool = False,
    *,
    task_dir: Optional[Path] = None,
) -> str:
    """渲染阶段执行履历 Markdown 表格行 (兼容 BaseStage 与 SimpleNamespace 测试桩)."""
    journal_by_id: Dict[int, Dict[str, Any]] = {}
    for j in (journals or []):
        if isinstance(j, dict) and j.get("stage_id"):
            try:
                journal_by_id[int(j["stage_id"])] = j
            except (TypeError, ValueError):
                continue

    if not journal_by_id:
        return "| — | 阶段履历 | 尚无阶段日志，无法确认执行履历 | 无 |"

    stage_by_id: Dict[int, Any] = {}
    for s in (stages or []):
        if hasattr(s, "id") and getattr(s, "id", None) is not None:
            try:
                stage_by_id[int(s.id)] = s
            except (TypeError, ValueError):
                continue

    row_ids = set(journal_by_id.keys())
    if len(journal_by_id) >= 2:
        for s_id, s in stage_by_id.items():
            if getattr(s, "status", None) in ("PASSED", "SKIPPED", "FALLBACK"):
                row_ids.add(s_id)

    rows: List[str] = []
    for s_id in sorted(row_ids):
        s = stage_by_id.get(s_id)
        j = journal_by_id.get(s_id, {})
        s_name = j.get("stage_name") or getattr(s, "name", None) or f"Stage {s_id}"
        raw_status = getattr(s, "status", "未知") if s is not None else "未知"
        if s_id == 6 and force_stage6_passed:
            status_str = "PASSED"
        elif s_id == 6 and raw_status in ("FAILED", "IN_PROGRESS", "PENDING"):
            status_str = "IN_PROGRESS (待终审)"
        else:
            status_str = str(raw_status)

        dur_raw = j.get("duration_seconds")
        if dur_raw is None:
            dur_raw = getattr(s, "duration_seconds", 0.0)
        try:
            dur = float(dur_raw or 0.0)
        except (TypeError, ValueError):
            dur = 0.0

        deliv_list = j.get("deliverables")
        if deliv_list is None:
            deliv_list = getattr(s, "expected_outputs", None) or (
                ["final_research_report.md", "stage_journals.json"] if s_id == 6 else []
            )
        if s_id == 5 and task_dir is not None:
            # 配置中的仿真交付物是条件产物，只有实际存在时才能展示。
            optional = {"pinn_simulation_result.json", "pinn_simulation_curve.png"}
            deliv_list = [d for d in deliv_list if Path(str(d)).name not in optional
                          or (task_dir / Path(str(d)).name).is_file()]
        s_deliv = ", ".join([Path(str(d)).name for d in deliv_list if d]) or "无"

        if s_id in journal_by_id:
            s_notes = f"状态 {status_str}；耗时 {dur:.1f}s；详细记录见 stage_journals.json"
        else:
            s_notes = f"状态 {status_str}；既有基础资产预检通过"
        rows.append(f"| Stage {s_id} | {s_name} | {s_notes} | `{s_deliv}` |")

    return "\n".join(rows)


def publish_report_audit(
    path: Path,
    stages: Iterable[Any],
    journals: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """终审后仅更新审计状态及阶段执行履历表，原子发布；写入错误交由调用方显式处理。"""
    text = path.read_text(encoding="utf-8")
    stage_list = list(stages or [])
    skipped = [getattr(s, "id", None) for s in stage_list if getattr(s, "status", None) == "SKIPPED"]
    fallback = [getattr(s, "id", None) for s in stage_list if getattr(s, "status", None) == "FALLBACK"]
    summary = "全流程 6 阶段门禁检查全部通过"
    if skipped:
        summary = f"必检阶段门禁全部通过；Stage {skipped} 按配置跳过"
    if fallback:
        summary = f"流程完成；Stage {fallback} FALLBACK (附条件交付，验证未通过或未完成)"
    text, count = re.subn(r"^- 阶段审计:.*$", lambda _: f"- 阶段审计: {summary}", text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError("报告缺少阶段审计字段，无法发布终审结论")
    text = re.sub(
        r"(Stage 6 \(综合研报生成\): 状态 )\[[^\]]*\]",
        r"\1[PASSED]", text,
    )

    if journals is None:
        journal_path = path.parent / "stage_journals.json"
        if journal_path.exists():
            try:
                loaded = json.loads(journal_path.read_text(encoding="utf-8"))
                journals = loaded if isinstance(loaded, list) else []
            except Exception:
                journals = []
        else:
            journals = []

    s6_stage = next((s for s in stage_list if getattr(s, "id", None) == 6), None)
    s6_passed = getattr(s6_stage, "status", None) == "PASSED"

    if "### 阶段执行履历" in text and _JOURNAL_TABLE_HEADER in text:
        h_idx = text.index(_JOURNAL_TABLE_HEADER)
        start_idx = h_idx + len(_JOURNAL_TABLE_HEADER)
        f_idx = text.find(_JOURNAL_TABLE_FOOTER, start_idx)
        if f_idx != -1:
            table_rows = render_stage_journal_table(
                stage_list, journals or [], force_stage6_passed=s6_passed, task_dir=path.parent
            )
            text = text[:start_idx] + table_rows + text[f_idx:]

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
