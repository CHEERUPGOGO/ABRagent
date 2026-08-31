"""AutonomousLoopRunner — 全自动自主执行循环引擎 (AutoBatteryResearch Agent).

负责自动读取 Tips、调用阶段流水线、自检门禁、根据 failure_summary 自愈重试、记录 Journal 并推进 Stage。
支持动态回调 (on_stage_update) 实现 TUI 面板毫秒级实时刷新。
采用严谨专业的无表情工业日志风格。
"""

import sys
import time
import logging
from typing import Dict, Any, Optional, Callable
from datetime import datetime
from pathlib import Path


if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from auto_battery_research.workflow.stage_manager import StageManager
from auto_battery_research.tools.stage_tools import (
    tool_get_status,
    tool_get_current_tips,
    tool_check_stage,
    tool_complete_stage,
    tool_set_stage_journal,
    tool_run_stage_task,
)

L = logging.getLogger("AutoBatteryResearch.LoopRunner")


class AutonomousLoopRunner:
    """全自动工作流自主执行器."""

    def __init__(
        self,
        manager: Optional[StageManager] = None,
        goal: str = "设计400Wh/kg高比能液态锂金属电池方案",
        max_stage_retries: int = 3,
        verbose: bool = True,
        on_stage_update: Optional[Callable[[int, str, float], None]] = None,
    ):
        self.goal = goal
        self.manager = manager or StageManager(target_goal=goal)
        if manager is not None and hasattr(manager, "target_goal"):
            if getattr(manager, "target_goal", None) != goal:
                # 课题目标变更：走 StageManager 的正式切换路径 (重置内存状态后重载
                # 新课题的持久化进度 + 资产预检)。禁止直接赋值 target_goal —— 那会把
                # 旧课题的内存进度泄漏进新课题 (state_file_path 动态跟随目标，
                # 下一次 _save_state 即写入新课题目录，造成跨课题状态污染)。
                switch_fn = getattr(manager, "switch_goal", None)
                if callable(switch_fn):
                    switch_fn(goal)
                else:
                    manager.target_goal = goal
        self.max_retries = max_stage_retries
        self.verbose = verbose
        self.on_stage_update = on_stage_update

    def log(self, msg: str):
        if self.verbose:
            print(msg)

    def _notify(self, stage_id: int, status: str, duration: float = 0.0):
        if self.on_stage_update:
            try:
                self.on_stage_update(stage_id, status, duration)
            except Exception:
                pass

    def run_stream(self):
        """以生成器方式逐步执行全流程自主循环，流式推送实时阶段状态、终端日志与诊断信息."""
        start_time = datetime.now()
        logs = []

        def append_log(msg: str):
            logs.append(msg)
            self.log(msg)

        append_log("=" * 70)
        append_log("[START] AutoBatteryResearch Agent Autonomous Loop")
        append_log(f"Target Goal: {self.goal}")
        append_log(f"Started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        append_log("=" * 70)

        yield {
            "event": "start",
            "stage_id": self.manager.get_current_stage().id,
            "progress_ratio": 0.02,
            "status": self.manager.get_status(),
            "tips": self.manager.get_current_tips(),
            "diag": {"status": "STARTING", "goal": self.goal},
            "log": "\n".join(logs),
            "report": "*科研任务已启动，正在按序执行 6 阶段流水线与门禁检查...*",
            "journal": self.manager.get_all_stage_journal(),
        }

        stage_loop_count = 0
        total_stages_count = max(len(self.manager.stages), 6)
        max_total_steps = total_stages_count * (self.max_retries + 1)

        while not self.manager.is_all_completed() and stage_loop_count < max_total_steps:
            stage_loop_count += 1
            curr_stage = self.manager.get_current_stage()
            t0 = time.time()
            stage_idx = curr_stage.id - 1
            base_ratio = stage_idx / total_stages_count

            # 1. 检查是否跳过
            if curr_stage.skip:
                append_log(f"\n>>> [Stage {curr_stage.id}/{total_stages_count}] {curr_stage.name} (Key: {curr_stage.key})")
                append_log(f"[SKIP] Stage marked as skipped: {curr_stage.skip_reason or 'default accelerated mode'}")
                self.manager.set_stage_journal(
                    stage_id=curr_stage.id,
                    notes=f"Stage skipped: {curr_stage.skip_reason}",
                    deliverables=[],
                    key_findings={"status": "SKIPPED"},
                )
                curr_stage.status = "SKIPPED"
                curr_stage.duration_seconds = 0.0
                self._notify(curr_stage.id, "SKIPPED", 0.0)
                self.manager.complete_stage(curr_stage.id)

                yield {
                    "event": "stage_skipped",
                    "stage_id": curr_stage.id,
                    "progress_ratio": (stage_idx + 1) / total_stages_count,
                    "status": self.manager.get_status(),
                    "tips": self.manager.get_current_tips(),
                    "diag": {"status": "SKIPPED", "reason": curr_stage.skip_reason or "默认跳过"},
                    "log": "\n".join(logs),
                    "report": "*科研任务执行中...*",
                    "journal": self.manager.get_all_stage_journal(),
                }
                continue

            curr_stage.start_running()
            self._notify(curr_stage.id, "IN_PROGRESS", 0.0)
            append_log(f"\n>>> [Stage {curr_stage.id}/{total_stages_count}] Processing: {curr_stage.name} (Key: {curr_stage.key})")

            # 2. 获取 Tips
            tips = curr_stage.get_tips()
            short_desc = curr_stage.description[:80] + "..." if len(curr_stage.description) > 80 else curr_stage.description
            append_log(f"[TIPS] Guidelines: {short_desc}")

            yield {
                "event": "stage_start",
                "stage_id": curr_stage.id,
                "progress_ratio": base_ratio + (0.3 / total_stages_count),
                "status": self.manager.get_status(),
                "tips": tips,
                "diag": {"status": "IN_PROGRESS", "stage_name": curr_stage.name},
                "log": "\n".join(logs),
                "report": "*科研任务执行中...*",
                "journal": self.manager.get_all_stage_journal(),
            }

            # 3. 循环尝试执行与门禁校验
            stage_passed = False
            task_res = {}
            for attempt in range(1, self.max_retries + 1):
                append_log(f"[EXEC] Executing Stage {curr_stage.id} pipeline (Attempt {attempt}/{self.max_retries})...")
                task_res = tool_run_stage_task(stage_id=curr_stage.id, target_query=self.goal)
                if task_res.get("message"):
                    append_log(f"    Result: {task_res.get('message')}")

                check_passed, diag = self.manager.check_stage(curr_stage.id, is_complete=False)

                if check_passed:
                    append_log(f"[CHECK] Stage {curr_stage.id} deterministic gate verification PASSED.")
                    stage_passed = True
                    yield {
                        "event": "stage_checked",
                        "stage_id": curr_stage.id,
                        "progress_ratio": base_ratio + (0.8 / total_stages_count),
                        "status": self.manager.get_status(),
                        "tips": tips,
                        "diag": diag,
                        "log": "\n".join(logs),
                        "report": "*科研任务执行中...*",
                        "journal": self.manager.get_all_stage_journal(),
                    }
                    break
                else:
                    curr_stage.fail_count += 1
                    fail_sum = diag.get("failure_summary") or {}
                    append_log(f"[FAIL] Attempt {attempt} failed: [{fail_sum.get('error_code', 'ERR')}] {fail_sum.get('error', 'Unmet requirements')}")
                    append_log(f"Suggestion: {fail_sum.get('next_action', 'Retrying...')}")
                    yield {
                        "event": "stage_failed_attempt",
                        "stage_id": curr_stage.id,
                        "progress_ratio": base_ratio + (0.5 / total_stages_count),
                        "status": self.manager.get_status(),
                        "tips": tips,
                        "diag": diag,
                        "log": "\n".join(logs),
                        "report": "*科研任务执行中 (正在自愈重试)...*",
                        "journal": self.manager.get_all_stage_journal(),
                    }
                    if attempt < self.max_retries:
                        append_log("[RETRY] Executing self-healing retry action...")
                        time.sleep(1)

            elapsed_stage = time.time() - t0
            curr_stage.finish_running(status="PASSED" if stage_passed else "FAILED", duration=elapsed_stage)

            # 4. 记录日志与推进
            if stage_passed or curr_stage.skip:
                append_log(f"[JOURNAL] Recording Stage {curr_stage.id} journal...")
                notes = (
                    task_res.get("journal_notes")
                    or task_res.get("message")
                    or f"Stage {curr_stage.id} completed and passed gate check (elapsed: {elapsed_stage:.1f}s)"
                )
                delivs = task_res.get("deliverables") or curr_stage.expected_outputs
                findings = task_res.get("key_findings") or {"status": "PASSED", "duration_seconds": round(elapsed_stage, 1)}

                self.manager.set_stage_journal(
                    stage_id=curr_stage.id,
                    notes=notes,
                    deliverables=delivs,
                    key_findings=findings,
                )
                comp_ok, comp_res = self.manager.complete_stage(curr_stage.id)
                self._notify(curr_stage.id, "PASSED", elapsed_stage)
                append_log(f"[COMPLETE] {comp_res.get('message', 'Stage advanced successfully')}")

                yield {
                    "event": "stage_completed",
                    "stage_id": curr_stage.id,
                    "progress_ratio": (stage_idx + 1) / total_stages_count,
                    "status": self.manager.get_status(),
                    "tips": self.manager.get_current_tips(),
                    "diag": {"status": "PASSED", "message": comp_res.get("message")},
                    "log": "\n".join(logs),
                    "report": "*科研任务执行中...*",
                    "journal": self.manager.get_all_stage_journal(),
                }
            else:
                append_log(f"[ERROR] Stage {curr_stage.id} retry limit exceeded, workflow paused.")
                self._notify(curr_stage.id, "FAILED", elapsed_stage)
                break

        total_elapsed = (datetime.now() - start_time).total_seconds()
        all_done = self.manager.is_all_completed()

        append_log("\n" + "=" * 70)
        append_log(f"[{'DONE' if all_done else 'INCOMPLETE'}] AutoBatteryResearch loop finished: {'Completed successfully' if all_done else 'Partially completed'}")
        append_log(f"Total Elapsed: {total_elapsed:.1f}s")
        task_dir = self.manager.get_task_output_dir(self.goal)
        cand_reports = [
            task_dir / "final_research_report.md",
            task_dir / "final_report.md",
            task_dir / "battery_research_synthesis_report.md",
            Path("output/auto_battery_research/final_research_report.md"),
            Path("output/auto_battery_research/final_report.md"),
            Path("output/auto_battery_research/battery_research_synthesis_report.md"),
        ]
        found_rf = next((p for p in cand_reports if p.exists()), None)
        report_text = "*未找到研报*"
        if found_rf:
            with open(found_rf, "r", encoding="utf-8") as f:
                report_text = f.read()
            append_log(f"Final Report File: {found_rf}")
        append_log("=" * 70 + "\n")

        yield {
            "event": "finished",
            "stage_id": total_stages_count,
            "progress_ratio": 1.0,
            "status": self.manager.get_status(),
            "tips": self.manager.get_current_tips(),
            "diag": {"status": "ALL_COMPLETED" if all_done else "INCOMPLETE", "elapsed_seconds": round(total_elapsed, 1)},
            "log": "\n".join(logs),
            "report": report_text,
            "journal": self.manager.get_all_stage_journal(),
        }

    def run(self) -> Dict[str, Any]:
        """执行端到端全流程自主循环 (同步封装)."""
        last_step = {}
        for step in self.run_stream():
            last_step = step

        task_dir = self.manager.get_task_output_dir(self.goal)
        cand_reports = [
            task_dir / "final_research_report.md",
            task_dir / "final_report.md",
            task_dir / "battery_research_synthesis_report.md",
            Path("output/auto_battery_research/final_research_report.md"),
            Path("output/auto_battery_research/final_report.md"),
            Path("output/auto_battery_research/battery_research_synthesis_report.md"),
        ]
        found_rf = next((p for p in cand_reports if p.exists()), None)

        return {
            "success": self.manager.is_all_completed(),
            "elapsed_seconds": last_step.get("diag", {}).get("elapsed_seconds", 0),
            "report_file": found_rf or (task_dir / "final_research_report.md"),
            "status": self.manager.get_status(),
        }
