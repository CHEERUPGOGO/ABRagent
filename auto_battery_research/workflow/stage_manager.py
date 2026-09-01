"""StageManager — 阶段生命周期与工作流状态机管理器 (AutoBatteryResearch Agent).


- 严格阶段状态流转 (PENDING -> IN_PROGRESS -> PASSED / SKIPPED / FAILED)
- 动态 Skip 机制与即时状态同步
- 确定性门禁判定 (Check 只自检诊断，Complete 终审并推进指针)
- 详细 Mission 与 Stage 详情大屏 (Detail)
- 研发日志持久化与阶段失败审计 (StageJournal)
- 运行时快照机制 (.abr_agent/runtime_config.json)
"""

import os
import json
import importlib
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import threading

from auto_battery_research.stage.base_stage import BaseStage
from auto_battery_research.checkers.base_checker import BaseChecker
from auto_battery_research.util.config import ABRConfigLoader


class StageManager:
    """化学电池研究智能体工作流状态机."""

    def __init__(
        self,
        setting_file: Optional[str] = None,
        workflow_file: Optional[str] = None,
        skip_pinn: Optional[bool] = None,
        target_goal: str = "设计400Wh/kg高比能液态锂金属电池方案",
        overrides: Optional[Dict[str, Any]] = None,
        workspace_root: Optional[str] = None,
    ):
        # 双根拆分: 包根 (配置文件所在) 与工作区根 (产物/状态落盘位置)。
        # 生产环境二者同为一个仓库根；测试注入 workspace_root 即可将全部产物
        # 隔离到临时目录，实现完全 hermetic 的状态机/门禁回归测试。
        self._package_dir = Path(__file__).resolve().parent.parent  # <repo>/auto_battery_research
        self.root_dir = Path(workspace_root).resolve() if workspace_root else self._package_dir.parent
        self.setting_file = Path(setting_file) if setting_file else self._package_dir / "setting.yaml"
        self.workflow_file = Path(workflow_file) if workflow_file else self._package_dir / "workflow" / "abr_workflow.yaml"
        self.target_goal = target_goal

        self.config: Dict[str, Any] = {}
        self.mission_info: Dict[str, Any] = {}
        self.stages: List[BaseStage] = []
        self.current_stage_idx: int = 0
        self.start_time: datetime = datetime.now()
        self._lock = threading.RLock()
        self._task_dir_cache: Dict[str, Path] = {}  # target -> 已解析任务目录 (含存量目录认领结果)
        self._task_dir_legacy: Dict[str, bool] = {}  # target -> 是否为被认领的无哈希历史课题目录
        
        # 1. 使用 ABRConfigLoader 执行分层加载与模板渲染
        self.config_loader = ABRConfigLoader(
            workspace_root=str(self.root_dir),
            setting_file=str(self.setting_file),
            workflow_file=str(self.workflow_file),
            overrides=overrides,
        )
        self.config = self.config_loader.load(target_goal=self.target_goal)
        self.mission_info = self.config.get("mission", {})

        # 2. 构建 Stages 与 Checkers
        self._build_stages()
        
        # 3. 应用默认 skip 设置 (persist=False: 初始化期禁止落盘，
        #    否则会在 _load_state() 之前覆盖持久化进度，导致恢复永远失效)
        default_skip_pinn = self.config.get("runtime_options", {}).get("skip_pinn_default", True)
        self.set_stage_skip(5, skip=default_skip_pinn, reason="默认快速模式跳过" if default_skip_pinn else "", persist=False)

        # 4. 恢复已有状态
        self._load_state()

        # 5. 覆写 PINN skip 选项（若 CLI 显式传入）
        if skip_pinn is not None:
            self.set_stage_skip(5, skip=skip_pinn, reason="CLI 参数覆盖跳过设置" if skip_pinn else "CLI 强制激活仿真")

        # 6. 自动预检已有数据资产
        self.auto_detect_existing_progress()

    def auto_detect_existing_progress(self) -> int:
        """根据已有数据资产自动预检并更新阶段状态 (Stage 1~6)。

        严格"连续前缀验收"语义: 沿阶段顺序扫描，一旦某个阶段无法定论
        (门禁失败 / 恢复出 FAILED、IN_PROGRESS)，其后的下游阶段一律
        保持 PENDING、禁止因残留文件被自动认领 —— 杜绝"中间阶段失败、
        下游阶段凭旧产物提前 PASSED"的跳步漏洞。已由持久化状态确认的
        PASSED/SKIPPED/FALLBACK 前缀保持连续，从首个未定论阶段续扫。
        """
        detected_passed = 0
        TERMINAL_OK = ("PASSED", "SKIPPED", "FALLBACK")
        for s in self.stages:
            if s.status in TERMINAL_OK:
                continue  # 已确认的前缀保持连续 (含 _load_state 恢复的终态)
            if s.skip:
                s.status = "SKIPPED"
                continue
            if s.status != "PENDING":
                break  # 恢复出的 FAILED/IN_PROGRESS 即当前活跃阶段，前缀到此为止

            passed, diag = self.check_stage(s.id, is_complete=False)
            if not passed:
                break  # 上游门禁未过：下游禁止自动认领 (check_stage 已将其置为 FAILED)

            # 检查是否有 Checker 报告了 FALLBACK 终态
            is_fallback = False
            for d in diag.get("diagnostics", []):
                obs = d.get("observed", {})
                if isinstance(obs, dict) and (obs.get("simulation_status") == "FALLBACK" or obs.get("is_fallback") is True):
                    is_fallback = True
                    break
            s.status = "FALLBACK" if is_fallback else "PASSED"
            s.duration_seconds = 1.0
            detected_passed += 1

        # 将当前指针指向首个未完成的阶段；若全部完成则归位至最后一个 Stage
        pointer_moved = False
        for idx, s in enumerate(self.stages):
            if s.status not in ("PASSED", "SKIPPED", "FALLBACK"):
                if self.current_stage_idx != idx:
                    pointer_moved = True
                self.current_stage_idx = idx
                break
        else:
            # 全部阶段已完成：指针归位至最后一个 Stage，避免状态大屏出现
            # "当前活跃阶段: Stage 1 (PASSED) | 进度: 6/6" 的自相矛盾展示
            if self.stages and self.current_stage_idx != len(self.stages) - 1:
                self.current_stage_idx = len(self.stages) - 1
                pointer_moved = True

        if detected_passed > 0 or pointer_moved:
            self._save_state()
        return detected_passed

    def _build_stages(self):
        """解析配置中的 stages 并实例化绑定 Checkers."""
        raw_stages = self.config.get("stages", [])
        self.stages = []

        for s_cfg in raw_stages:
            checkers = []
            for c_cfg in s_cfg.get("checkers", []):
                checker_cls_path = c_cfg.get("class")
                checker_name = c_cfg.get("name")
                strict = c_cfg.get("strict", True)

                checker_inst = None
                load_error = None
                if checker_cls_path:
                    try:
                        module_name, class_name = checker_cls_path.rsplit(".", 1)
                        mod = importlib.import_module(module_name)
                        cls = getattr(mod, class_name)
                        checker_inst = cls(name=checker_name, strict=strict)
                    except Exception as e:
                        load_error = f"{type(e).__name__}: {e}"
                else:
                    load_error = "abr_workflow.yaml 中该 checker 未配置 class 字段"

                if checker_inst is None:
                    # Fail-Closed 门禁语义: Checker 配置/导入错误时不允许静默放行。
                    # 加载失败的门禁永远判失败，直到配置修复 —— 科研护栏宁可阻断不可假通过。
                    print(f"[StageManager] 错误: 无法加载 Checker ({checker_cls_path}): {load_error} — 该门禁已 fail-closed (始终不通过)")
                    from auto_battery_research.checkers.base_checker import BaseChecker

                    _err = load_error or "unknown"

                    class FailedLoadChecker(BaseChecker):
                        def do_check(self, is_complete=False, **kwargs):
                            diag = self.build_diagnostic(
                                passed=False,
                                error_code="CHECKER_LOAD_ERROR",
                                error_msg=f"Checker 加载失败 ({checker_cls_path}): {_err}",
                                next_action="检查 abr_workflow.yaml 中 checker 的 class 拼写/导入路径，修复后重启工作流。",
                            )
                            return False, diag

                    checker_inst = FailedLoadChecker(name=checker_name, strict=strict)

                checkers.append(checker_inst)

            stage_obj = BaseStage(s_cfg, checkers)
            for c in checkers:
                c.on_init(stage_manager=self, stage_info=s_cfg, config=self.config)

            self.stages.append(stage_obj)

    @property
    def state_file_path(self) -> Path:
        """状态持久化文件路径 (优先隔离在课题任务目录下，确保多任务不冲突)."""
        task_dir = self.get_task_output_dir()
        task_state = task_dir / ".stage_state.json"
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_state

    @property
    def journal_file_path(self) -> Path:
        """日志持久化文件路径 (优先写入课题专属 stage_journals.json)."""
        task_dir = self.get_task_output_dir()
        task_journal = task_dir / "stage_journals.json"
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_journal

    def _save_state(self):
        """原子并发安全持久化当前工作流进度 (带课题目标签名与隔离临时文件替换)."""
        import uuid, hashlib
        with self._lock:
            goal_hash = hashlib.md5(self.target_goal.encode("utf-8")).hexdigest()[:10]
            state_data = {
                "version": "1.0.0",
                "agent": "AutoBatteryResearch Agent",
                "target_goal": self.target_goal,
                "target_goal_hash": goal_hash,
                "task_dir_schema": "legacy" if self.is_legacy_task else "hashed",
                "updated_at": datetime.now().isoformat(),
                "current_stage_idx": self.current_stage_idx,
                "stages": [s.to_dict() for s in self.stages],
            }
            target_path = self.state_file_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = target_path.with_suffix(f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(state_data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, target_path)
            except Exception:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                with open(target_path, "w", encoding="utf-8") as f:
                    json.dump(state_data, f, ensure_ascii=False, indent=2)

    def _load_state(self):
        """从文件恢复工作流进度 (严格校验课题目标与哈希匹配)."""
        if not self.state_file_path.exists():
            return
        try:
            with open(self.state_file_path, "r", encoding="utf-8") as f:
                state_data = json.load(f)
            saved_goal = state_data.get("target_goal")
            if saved_goal and saved_goal != self.target_goal:
                # 课题目标不一致，不跨任务复用状态
                return
            self.current_stage_idx = min(state_data.get("current_stage_idx", 0), len(self.stages) - 1)
            saved_stages = {s["id"]: s for s in state_data.get("stages", [])}
            for s in self.stages:
                if s.id in saved_stages:
                    s.status = saved_stages[s.id].get("status", "PENDING")
                    if "skip" in saved_stages[s.id]:
                        s.skip = saved_stages[s.id]["skip"]
        except Exception as e:
            print(f"[StageManager] 警告: 无法恢复 state.json: {e}")

    def get_stage_by_id(self, stage_id: int) -> Optional[BaseStage]:
        """按 1-based ID 查询 stage."""
        for s in self.stages:
            if s.id == stage_id:
                return s
        return None

    def get_current_stage(self) -> BaseStage:
        """获取当前活跃 Stage."""
        if 0 <= self.current_stage_idx < len(self.stages):
            return self.stages[self.current_stage_idx]
        return self.stages[-1]

    def get_status(self) -> Dict[str, Any]:
        """获取整个 Mission 与所有 Stage 的状态摘要."""
        current_s = self.get_current_stage()
        completed_count = sum(1 for s in self.stages if s.status in ("PASSED", "SKIPPED", "FALLBACK"))
        return {
            "mission_name": self.mission_info.get("name", "AutoBatteryResearch Agent"),
            "current_stage_id": current_s.id,
            "current_stage_key": current_s.key,
            "current_stage_name": current_s.name,
            "current_stage_status": current_s.status,
            "is_all_completed": self.is_all_completed(),
            "progress": f"{completed_count}/{len(self.stages)}",
            "stages": [s.to_dict() for s in self.stages],
        }

    def get_detail(self) -> Dict[str, Any]:
        """获取详细 Mission 与所有 Stage 的深入明细 """
        status_info = self.get_status()
        details = {
            "mission_metadata": {
                "name": self.mission_info.get("name"),
                "version": self.mission_info.get("version"),
                "description": self.mission_info.get("description", "").strip(),
                "started_at": self.start_time.isoformat(),
                "target_goal": self.target_goal,
            },
            "environment": {
                "workspace_root": str(self.root_dir),
                "state_file": str(self.state_file_path),
                "journal_file": str(self.journal_file_path),
                "skip_pinn_default": self.config.get("runtime_options", {}).get("skip_pinn_default", True),
            },
            "stages_detail": [],
        }

        for s in self.stages:
            s_dict = s.to_dict()
            s_dict["checker_classes"] = [c.__class__.__name__ for c in s.checkers]
            s_dict["reference_files_count"] = len(s.reference_files)
            s_dict["expected_outputs_count"] = len(s.expected_outputs)
            details["stages_detail"].append(s_dict)

        return details

    def get_current_tips(self) -> str:
        """获取当前阶段的 Task 与 Tips."""
        return self.get_current_stage().get_tips()

    def set_stage_skip(self, stage_id_or_key: Any, skip: bool = True, reason: str = "", persist: bool = True) -> bool:
        """动态设置阶段跳过状态 (严格限制：仅允许跳过支持 skip 的阶段，如 Stage 5 PINN).

        persist=False 供 __init__ 初始化阶段使用: 初始化期的默认 skip 设定
        只是内存态准备，绝不允许先于 _load_state() 落盘 —— 否则构造器会把
        持久化进度覆盖为全新状态再读回自己，导致跨进程恢复永远失效。
        """
        for s in self.stages:
            if s.id == stage_id_or_key or s.key == str(stage_id_or_key):
                if skip and not getattr(s, "allow_skip", s.id == 5):
                    print(f"[StageManager] 拒绝跳过: Stage {s.id} ({s.name}) 为核心必跑阶段，禁止跳过！")
                    return False
                s.skip = skip
                if skip:
                    s.skip_reason = reason or "手动配置跳过"
                    s.status = "SKIPPED"
                else:
                    s.skip_reason = ""
                    if s.status == "SKIPPED":
                        s.status = "PENDING"
                if persist:
                    self._save_state()
                return True
        return False

    def check_stage(self, stage_id: Optional[int] = None, is_complete: bool = False, **kwargs) -> Tuple[bool, Dict[str, Any]]:
        """执行 Stage 门禁检查 (不推进阶段)."""
        stage = self.get_stage_by_id(stage_id) if stage_id else self.get_current_stage()
        if not stage:
            return False, {"check_pass": False, "error": f"找不到指定阶段: {stage_id}"}

        if stage.skip:
            return True, {
                "check_pass": True,
                "stage_id": stage.id,
                "stage_key": stage.key,
                "status": "SKIPPED",
                "message": f"Stage {stage.id} 已配置跳过: {stage.skip_reason}",
                "next_action": "本阶段已跳过，请直接调用 Complete 进行推进。",
            }

        all_passed = True
        diagnostics = []
        failure_summary = None

        for checker in stage.checkers:
            passed, diag = checker.do_check(is_complete=is_complete, **kwargs)
            diagnostics.append(diag)
            if not passed:
                if checker.strict:
                    all_passed = False
                    if failure_summary is None:
                        failure_summary = diag
                else:
                    diag["warning_only"] = True

        result = {
            "check_pass": all_passed,
            "stage_id": stage.id,
            "stage_key": stage.key,
            "stage_name": stage.name,
            "diagnostics": diagnostics,
            "failure_summary": failure_summary,
        }

        if all_passed:
            stage.status = "PASSED" if is_complete else "IN_PROGRESS"
        else:
            stage.status = "FAILED"

        self._save_state()
        return all_passed, result

    def complete_stage(self, stage_id: Optional[int] = None, **kwargs) -> Tuple[bool, Dict[str, Any]]:
        """执行 Complete 动作：严格按照状态机顺序推进，禁止跨阶段越级推进."""
        current_stage = self.get_current_stage()
        target_stage = self.get_stage_by_id(stage_id) if stage_id else current_stage
        if not target_stage:
            return False, {"complete": False, "error": f"找不到指定阶段: {stage_id}"}

        # 严格单阶段推进：只允许完成当前活跃阶段；历史阶段不可重复推进状态机，未来阶段禁止越级
        if target_stage.id != current_stage.id:
            target_idx = next((i for i, s in enumerate(self.stages) if s.id == target_stage.id), -1)
            if target_idx < self.current_stage_idx:
                return False, {
                    "complete": False,
                    "error": f"历史阶段已完成 (Stage {target_stage.id} 状态: {target_stage.status})，无法重复调用 Complete 推进状态机指针。当前活跃阶段为 Stage {current_stage.id} ({current_stage.name})。",
                }
            else:
                return False, {
                    "complete": False,
                    "error": f"无法跨阶段推进：当前活跃阶段为 Stage {current_stage.id} ({current_stage.name})，前置阶段未完成，禁止越级推进到 Stage {target_stage.id}。",
                }

        passed, check_res = self.check_stage(target_stage.id, is_complete=True, **kwargs)

        if not passed and not target_stage.skip:
            return False, {
                "complete": False,
                "message": f"Stage {target_stage.id} 门禁检查未通过，无法推进！",
                "failure_summary": check_res.get("failure_summary"),
                "details": check_res,
            }

        if target_stage.skip:
            target_stage.status = "SKIPPED"
        else:
            # 检查是否有 Checker 报告了 FALLBACK 终态
            is_fallback = False
            for diag in check_res.get("diagnostics", []):
                obs = diag.get("observed", {})
                if isinstance(obs, dict) and (obs.get("simulation_status") == "FALLBACK" or obs.get("is_fallback") is True):
                    is_fallback = True
                    break
            target_stage.status = "FALLBACK" if is_fallback else "PASSED"

        advanced = False
        for next_idx in range(self.current_stage_idx + 1, len(self.stages)):
            next_stage = self.stages[next_idx]
            if next_stage.skip:
                next_stage.status = "SKIPPED"
                continue
            self.current_stage_idx = next_idx
            self.stages[self.current_stage_idx].status = "IN_PROGRESS"
            advanced = True
            break

        if not advanced:
            self.current_stage_idx = len(self.stages) - 1

        self._save_state()

        new_current = self.get_current_stage()
        return True, {
            "complete": True,
            "completed_stage_id": target_stage.id,
            "completed_stage_name": target_stage.name,
            "completed_stage_status": target_stage.status,
            "is_all_completed": self.is_all_completed(),
            "next_stage_id": new_current.id,
            "next_stage_name": new_current.name,
            "message": f"🎉 成功完成 Stage {target_stage.id} ({target_stage.name}, 状态: {target_stage.status})！当前进入 Stage {new_current.id} ({new_current.name})。",
        }

    def is_all_completed(self) -> bool:
        """是否所有阶段都已完成或跳过 (支持 PASSED, SKIPPED, FALLBACK)."""
        return all(s.status in ("PASSED", "SKIPPED", "FALLBACK") for s in self.stages)

    def get_task_output_dir(self, goal: Optional[str] = None) -> Path:
        """获取或创建按课题命名的独立产物子目录.

        目录名 = 截断 slug + 目标全文 MD5 前 8 位 (如 output/tasks/高镍三元正极研究_a1b2c3d4/)，
        避免长课题目标前缀相同时 (旧方案截断至 45 字符) 发生目录冲突与互相覆盖。

        存量目录认领: 旧命名 (纯截断 slug) 的已有目录若确属本课题
        (其 .stage_state.json 的 target_goal 一致，或无状态文件但存在产物)，
        则继续沿用旧目录 —— 保证升级后已有课题的工作流状态连续、不被静默重置。
        被认领的无哈希目录视为"历史存量课题" (is_legacy_task=True)：
        其交付物合法位于全局 output/auto_battery_research/，Checker 仅对
        这类课题保留全局产物读取回退；新哈希课题必须自包含课题目录产物。
        """
        import re
        import hashlib
        target = goal or getattr(self, "target_goal", "general_research_task")
        cached = self._task_dir_cache.get(target)
        if cached is not None and cached.exists():
            return cached

        safe_slug = re.sub(r'[\\/:*?"<>|\s]+', '_', target)[:45].strip('_')
        if not safe_slug:
            safe_slug = "general_research_task"

        hashed_slug = f"{safe_slug}_{hashlib.md5(target.encode('utf-8')).hexdigest()[:8]}"
        tasks_root = self.root_dir / "output" / "tasks"
        task_dir = tasks_root / hashed_slug
        is_legacy = False

        if not task_dir.exists():
            legacy_dir = tasks_root / safe_slug
            if legacy_dir.is_dir():
                legacy_state = legacy_dir / ".stage_state.json"
                if legacy_state.exists():
                    # 状态文件里的目标与本课题一致才认领；不一致或状态损坏
                    # (读不出 target_goal) 都不认领 —— Fail-Closed，防止前缀
                    # 撞名的他课题目录被误并入本课题
                    try:
                        with open(legacy_state, "r", encoding="utf-8") as f:
                            legacy_goal = json.load(f).get("target_goal")
                    except Exception:
                        legacy_goal = None
                    if legacy_goal is not None and legacy_goal == target:
                        task_dir = legacy_dir
                        is_legacy = True
                else:
                    # 已知局限: 无状态但有产物时认领，避免既有产物孤儿化
                    # (slug 前缀撞名的他课题空状态目录理论上可能被误领)
                    try:
                        has_artifacts = any(legacy_dir.iterdir())
                    except OSError:
                        has_artifacts = False
                    if has_artifacts:
                        task_dir = legacy_dir
                        is_legacy = True

        task_dir.mkdir(parents=True, exist_ok=True)
        self._task_dir_cache[target] = task_dir
        self._task_dir_legacy[target] = is_legacy
        return task_dir

    def is_legacy_goal(self, goal: Optional[str] = None) -> bool:
        """指定课题是否为历史存量课题 (需先经 get_task_output_dir 解析过该课题)."""
        target = goal or getattr(self, "target_goal", "")
        return bool(self._task_dir_legacy.get(target, False))

    @property
    def is_legacy_task(self) -> bool:
        """本课题是否为历史存量课题 (任务目录为被认领的无哈希旧目录).

        仅这类课题的交付物允许保留在全局 output/auto_battery_research/，
        Checker 的全局 legacy 读取回退以此属性为唯一开关。
        """
        return self.is_legacy_goal()

    def set_stage_journal(
        self,
        stage_id: Optional[int] = None,
        notes: str = "",
        deliverables: Optional[List[str]] = None,
        key_findings: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """记录指定阶段的详细研发日志 (持久化至全局及课题专用目录)."""
        import uuid
        with self._lock:
            stage = self.get_stage_by_id(stage_id) if stage_id else self.get_current_stage()
            sid = stage.id if stage else 0
            sname = stage.name if stage else "General"

            journals = self.get_all_stage_journal()
            entry = {
                "stage_id": sid,
                "stage_name": sname,
                "timestamp": datetime.now().isoformat(),
                "duration_seconds": getattr(stage, "duration_seconds", 0.0) if stage else 0.0,
                "notes": notes,
                "deliverables": deliverables or [],
                "key_findings": key_findings or {},
            }
            journals = [j for j in journals if j.get("stage_id") != sid]
            journals.append(entry)
            journals.sort(key=lambda x: x.get("stage_id", 0))

            # 1. 写入全局日志文件 (原子安全替换)
            def _atomic_dump(target: Path, obj: Any):
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
                try:
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(obj, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, target)
                except Exception:
                    if tmp.exists():
                        try:
                            tmp.unlink(missing_ok=True)
                        except Exception:
                            pass
                    with open(target, "w", encoding="utf-8") as f:
                        json.dump(obj, f, ensure_ascii=False, indent=2)

            _atomic_dump(self.journal_file_path, journals)

            # 2. 写入课题专用目录 (原子安全替换)
            task_dir = self.get_task_output_dir()
            task_journal_file = task_dir / "stage_journals.json"
            _atomic_dump(task_journal_file, journals)

            # 3. 同步生成人类易读的 stage_journals.md 审计文件
            md_lines = [
                f"# 课题研发阶段日志与审计表 (Task Stage Journal)",
                f"\n> **课题目标**: {getattr(self, 'target_goal', '化学电池科研任务')}",
                f"> **记录时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "\n| Stage | 阶段名称 | 状态 | 耗时 | 交付物 | 关键发现与所得 (Key Findings) |",
                "|:---|:---|:---:|:---:|:---|:---|",
            ]
            for j in journals:
                s_id = j.get("stage_id", 0)
                s_name = j.get("stage_name", "")
                s_dur = f"{j.get('duration_seconds', 0.0):.1f}s"
                s_notes = j.get("notes", "").replace("\n", " ")
                s_deliv = ", ".join([Path(d).name for d in j.get("deliverables", [])]) or "无"
                findings_summary = json.dumps(j.get("key_findings", {}), ensure_ascii=False)
                md_lines.append(f"| Stage {s_id} | {s_name} | 完成 | {s_dur} | `{s_deliv}` | {s_notes} ({findings_summary}) |")

            with open(task_dir / "stage_journals.md", "w", encoding="utf-8") as f:
                f.write("\n".join(md_lines) + "\n")

            return {"status": "success", "recorded_entry": entry, "task_journal_file": str(task_journal_file)}

    def get_all_stage_journal(self) -> List[Dict[str, Any]]:
        """获取所有阶段的历史研发日志."""
        with self._lock:
            # 优先读取课题专属目录日志
            task_journal = self.get_task_output_dir() / "stage_journals.json"
            target_path = task_journal if task_journal.exists() else self.journal_file_path
            if not target_path.exists():
                return []
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []


    def reset_workflow(self, start_stage_id: int = 1):
        """重置工作流状态."""
        default_skip_pinn = self.config.get("runtime_options", {}).get("skip_pinn_default", True)
        self.current_stage_idx = max(0, start_stage_id - 1)
        for s in self.stages:
            if s.id == 5:
                s.skip = default_skip_pinn
                s.skip_reason = "默认快速模式跳过" if default_skip_pinn else ""

            if s.id < start_stage_id:
                s.status = "PASSED"
            elif s.skip:
                s.status = "SKIPPED"
            else:
                s.status = "PENDING"

        if 0 <= self.current_stage_idx < len(self.stages):
            self.stages[self.current_stage_idx].status = "IN_PROGRESS"
        self._save_state()

    def prepare_for_goal(self, goal: str, force_reset: bool = False):
        """当课题目标更新或重新启动任务循环时，自动重置方案设计与下游阶段状态，确保执行真实大循环."""
        is_new_goal = bool(goal and goal != getattr(self, "target_goal", ""))
        self.target_goal = goal
        if is_new_goal or self.is_all_completed() or force_reset:
            self.reset_workflow(start_stage_id=4)

    def switch_goal(self, goal: str) -> None:
        """切换课题目标并按新课题重建内存状态 (运行时目标变更的唯一正式入口).

        禁止直接赋值 target_goal：那会把旧课题的内存进度泄漏给新课题 ——
        state_file_path 是动态跟随 target_goal 的属性，切换后第一次 _save_state
        就会把旧课题的进度写进新课题的 .stage_state.json，造成跨课题状态污染。
        """
        if not goal or goal == self.target_goal:
            return
        self.target_goal = goal
        # 重建声明式默认状态：纯内存操作，不落盘 (新课题可能已有持久化进度待重载，
        # 此处任何 _save_state 都会先覆盖它；故不走会落盘的 set_stage_skip/reset_workflow)
        default_skip_pinn = self.config.get("runtime_options", {}).get("skip_pinn_default", True)
        self.current_stage_idx = 0
        for s in self.stages:
            if s.id == 5:
                s.skip = default_skip_pinn
                s.skip_reason = "默认快速模式跳过" if default_skip_pinn else ""
            s.status = "SKIPPED" if s.skip else "PENDING"
        # 按新课题重载持久化进度 (无状态文件则保持全新 PENDING)，再执行资产预检
        self._load_state()
        self.auto_detect_existing_progress()
