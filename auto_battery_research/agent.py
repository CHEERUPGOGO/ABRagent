"""ABRAgent — 全生命周期化学电池研究自主智能体 (AutoBatteryResearch Agent).

基于 LangChain / LangGraph 认知循环构建的化学电池研发智能体架构体系：
1. 运行时一开始即实例化全局唯一的科研主控智能体。
2. 整合全量 6 阶段领域工具（Stage 1~6）与工作流状态护栏工具（Tips/Status/Check/Complete/Journal）。
3. 严格遵循“大模型在大脑主控位，工作流在规则裁判位”原则。
4. 具备门禁失败自愈反思能力 (Self-Correction on Checker Failure)。
"""

from __future__ import annotations

import os
import sys
import time
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, Generator

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage, BaseMessage

from auto_battery_research.workflow.stage_manager import StageManager
from auto_battery_research.tools.stage_tools import (
    set_stage_manager,
    get_all_stage_tools,
)
from auto_battery_research.tools.domain_tools import get_all_domain_tools
from auto_battery_research.backend.langchain_backend import ABRLangChainBackend
from auto_battery_research.util.logger import (
    log_info,
    log_thought,
    log_observation,
    log_tool_call,
    log_success,
    log_error,
)

L = logging.getLogger("AutoBatteryResearch.ABRAgent")


class ABRAgent:
    """全生命周期化学电池研究自主智能体 (AutoBatteryResearch Agent)."""

    def __init__(
        self,
        goal: str = "设计400Wh/kg高比能液态锂金属电池方案",
        config_file: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        skip_pinn: Optional[bool] = None,
        stream_output: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        on_stage_update: Optional[Callable[[int, str, float], None]] = None,
        max_stage_retries: int = 3,
        verbose: bool = True,
        enable_file_log: bool = False,
        log_file: Optional[str] = None,
        manager: Optional[StageManager] = None,
    ):
        self.goal = goal
        self.max_retries = max_stage_retries
        self.verbose = verbose
        self.on_stage_update = on_stage_update
        self.stream_output = stream_output
        self.enable_file_log = bool(enable_file_log or log_file)

        # 日志落盘路径配置 (默认不开启，开启时落入 log/ 目录下)
        import re
        from auto_battery_research.util.logger import init_file_logger, disable_file_logger
        if self.enable_file_log:
            if log_file and log_file != "default":
                self.log_file_path = Path(log_file)
            else:
                clean_name = re.sub(r'[\/:*?"<>| ]+', '_', self.goal)[:40]
                self.log_file_path = ROOT_DIR / "log" / f"{clean_name}.log"
            self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
            init_file_logger(str(self.log_file_path))
        else:
            self.log_file_path = None
            disable_file_logger()

        # 0. 加载仓库根目录 .env (幂等；不覆盖已有系统环境变量)，保证 Python API 直连用户也能吃到配置
        from auto_battery_research.util.env_loader import load_env
        load_env()

        # 1. 加载配置
        self.config = self._load_config(config_file, config)

        # 2. 初始化工作流状态机与全局单例注入
        if manager is not None:
            self.manager = manager
            if self.goal and getattr(manager, "target_goal", None) != self.goal:
                # 课题目标变更：走正式切换路径重载新课题状态。禁止直接赋值 target_goal ——
                # 那会把旧课题的内存进度泄漏进新课题 (state_file_path 动态跟随目标，
                # 下一次 _save_state 即写入新课题目录，造成跨课题状态污染)。
                switch_fn = getattr(manager, "switch_goal", None)
                if callable(switch_fn):
                    switch_fn(self.goal)
                else:
                    manager.target_goal = self.goal
        else:
            self.manager = StageManager(skip_pinn=skip_pinn, target_goal=self.goal)
        set_stage_manager(self.manager)

        # 3. 组装全量工具箱 (Domain Tools + Stage Workflow Tools)
        self.domain_tools = get_all_domain_tools()
        self.stage_tools = get_all_stage_tools()
        self.all_tools = self.domain_tools + self.stage_tools
        self.tool_map = {t.name: t for t in self.all_tools}

        # 4. 初始化 LangChain / LangGraph 后端
        self.backend = ABRLangChainBackend(
            config=self.config,
            on_token=on_token,
            streaming=stream_output,
        )
        self.backend.bind_tools(self.all_tools)

        # 5. 构建智能体系统提示词 (System Prompt)
        self.system_prompt = self._build_system_prompt()
        self.messages: List[BaseMessage] = [SystemMessage(content=self.system_prompt)]

    def _load_config(self, config_file: Optional[str], config_override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """加载 setting.yaml 与覆盖配置 (递归解析 $(VAR: default) 环境变量)."""
        from auto_battery_research.util.config import resolve_env_vars
        base_cfg = {}
        setting_path = ROOT_DIR / "auto_battery_research" / "setting.yaml"
        if setting_path.exists():
            try:
                import yaml
                with open(setting_path, "r", encoding="utf-8") as f:
                    base_cfg = yaml.safe_load(f) or {}
            except Exception:
                pass
        if config_file and Path(config_file).exists():
            try:
                import yaml
                with open(config_file, "r", encoding="utf-8") as f:
                    file_cfg = yaml.safe_load(f) or {}
                    base_cfg.update(file_cfg)
            except Exception:
                pass
        if config_override:
            base_cfg.update(config_override)
        return resolve_env_vars(base_cfg)

    def _build_system_prompt(self) -> str:
        """构建智能体专家角色认知与工作流守则."""
        return (
            "你是一位顶尖的化学电池领域科学家与全周期自主研发智能体 (AutoBatteryResearch Agent, ABRAgent)。\n"
            "你精通高比能锂离子电池、液态/固态锂金属电池的材料微观表征、电解液溶剂化结构设计、物理偏微分方程仿真 (P2D/PINN) 与热力学硬约束核算。\n\n"
            "【核心工作准则】\n"
            "1. **工作流状态机约束**：研发过程严格由 6 个 Stage 组成。在每个阶段，必须先调用 `CurrentTips` 明确当前任务验收指标。\n"
            "2. **工具驱动**：\n"
            "   - Stage 1: 使用 `InspectLiteratureAssets` 探测文献，若缺失则调用 `IngestLiteraturePapers`。\n"
            "   - Stage 2: 使用 `InspectVectorDB` 探测向量库，若缺失则调用 `IndexSemanticVectors`。\n"
            "   - Stage 3: 使用 `InspectCellEntities` 探测电芯，若缺失则调用 `ExtractAndAssembleCells`。\n"
            "   - Stage 4: 调用一次 `RunRAGDesign` 单链路服务 (内部完成 Planner -> Retrieval -> Writer -> Reviewer 与 RelationEngine 硬约束核算)。\n"
            "   - Stage 5: 调用 `RunPhysicsSimulation` 执行 P2D/PINN 物理验证（若未跳过）。\n"
            "   - Stage 6: 调用 `SynthesizeResearchReport` 整合全流程成果并生成最终综合研报。\n"
            "3. **确定性门禁质检 (Check & Complete)**：\n"
            "   - 完成当前阶段工作后，必须调用 `Check` 进行严格自检。\n"
            "   - 若 `Check` 发现错误，仔细阅读返回的 `failure_summary` 与 `next_action`，进行针对性自我修正与重试。\n"
            "   - 当 `Check` 100% 通过后，调用 `Complete` 正式推进阶段指针。\n"
            "4. **研发日志**：推进前调用 `SetStageJournal` 记录阶段关键洞察。\n"
        )

    def _notify(self, stage_id: int, status: str, duration: float = 0.0):
        if self.on_stage_update:
            try:
                self.on_stage_update(stage_id, status, duration)
            except Exception:
                pass

    def log(self, msg: str):
        if self.verbose:
            print(msg, flush=True)
        # 同步写入全局 agent.log (仅在启用时)
        try:
            from auto_battery_research.util.logger import log_raw
            log_raw(msg)
        except Exception:
            pass

    def run_stream(self, goal: Optional[str] = None) -> Generator[Dict[str, Any], None, None]:
        """流式执行全流程 ReAct 智能体研发大循环."""
        if goal:
            self.goal = goal
            self.manager.prepare_for_goal(self.goal)
        elif self.manager.is_all_completed():
            self.manager.prepare_for_goal(self.goal)

        start_time = datetime.now()
        logs = []

        def append_log(msg: str):
            ts = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
            line = f"{ts} {msg}"
            logs.append(msg)
            self.log(msg)
            # 仅在显式开启日志落盘时写入文件 (默认 log/ 目录)
            if self.enable_file_log and self.log_file_path:
                try:
                    with open(self.log_file_path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except Exception:
                    pass

        append_log("=" * 75)
        append_log("[START] ABRAgent (Agent-Centric Autonomous Loop)")
        append_log(f"Mission Goal: {self.goal}")
        append_log(f"Started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        if self.enable_file_log and self.log_file_path:
            append_log(f"Log File: {self.log_file_path.resolve()}")
        append_log("=" * 75)

        total_stages = max(len(self.manager.stages), 6)

        yield {
            "event": "start",
            "stage_id": self.manager.get_current_stage().id,
            "progress_ratio": 0.02,
            "status": self.manager.get_status(),
            "tips": self.manager.get_current_tips(),
            "diag": {"status": "STARTING", "goal": self.goal},
            "log": "\n".join(logs),
            "report": "*ABRAgent 智能体已就绪，正在自主执行 6 阶段研发任务...*",
            "journal": self.manager.get_all_stage_journal(),
        }

        stage_loop_count = 0
        max_total_steps = total_stages * (self.max_retries + 2)

        while not self.manager.is_all_completed() and stage_loop_count < max_total_steps:
            stage_loop_count += 1
            curr_stage = self.manager.get_current_stage()
            t0 = time.time()
            stage_idx = curr_stage.id - 1
            base_ratio = stage_idx / total_stages

            # 1. 检查是否跳过
            if curr_stage.skip:
                append_log(f"\n>>> [Stage {curr_stage.id}/{total_stages}] {curr_stage.name} (Key: {curr_stage.key})")
                append_log(f"[SKIP] 阶段已跳过: {curr_stage.skip_reason or '默认加速模式'}")
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
                    "progress_ratio": (stage_idx + 1) / total_stages,
                    "status": self.manager.get_status(),
                    "tips": self.manager.get_current_tips(),
                    "diag": {"status": "SKIPPED", "reason": curr_stage.skip_reason or "默认跳过"},
                    "log": "\n".join(logs),
                    "report": "*科研任务执行中...*",
                    "journal": self.manager.get_all_stage_journal(),
                }
                continue

            # 2. 激活阶段
            curr_stage.start_running()
            self._notify(curr_stage.id, "IN_PROGRESS", 0.0)
            append_log(f"\n>>> [Stage {curr_stage.id}/{total_stages}] 正在推进: {curr_stage.name} (Key: {curr_stage.key})")

            tips = curr_stage.get_tips()
            append_log(f"[TIPS] 阶段规范: {curr_stage.description}")

            yield {
                "event": "stage_start",
                "stage_id": curr_stage.id,
                "progress_ratio": base_ratio + (0.2 / total_stages),
                "status": self.manager.get_status(),
                "tips": tips,
                "diag": {"status": "IN_PROGRESS", "stage_name": curr_stage.name},
                "log": "\n".join(logs),
                "report": "*ABRAgent 正在自主规划并调度工具执行当前阶段...*",
                "journal": self.manager.get_all_stage_journal(),
            }

            # 3. 智能体自主执行与自检重试大循环
            stage_passed = False
            diag = {}
            last_failure: Optional[Dict[str, str]] = None
            for attempt in range(1, self.max_retries + 1):
                append_log(f"[AGENT] Stage {curr_stage.id} 智能体决策与工具调度 (尝试 {attempt}/{self.max_retries})...")

                # 构造本轮给 Agent 的环境感知提示 (重试轮携带上一轮门禁驳回诊断，形成 Self-Correction 反思回路)
                prompt_content = (
                    f"【当前阶段】Stage {curr_stage.id}: {curr_stage.name} (Key: {curr_stage.key})\n"
                    f"【研发总目标】{self.goal}\n"
                    f"【阶段指引与验收指标】\n{tips}\n"
                )
                if last_failure:
                    prompt_content += (
                        f"\n【上一轮门禁驳回 (Self-Correction)】\n"
                        f"错误码: {last_failure['code']}\n"
                        f"问题诊断: {last_failure['error']}\n"
                        f"修复建议: {last_failure['next_action']}\n"
                        f"请优先针对上述诊断修复本阶段产物后重新自检，遵循\"有则提取、无则留空、禁止编造\"。\n"
                    )
                prompt_content += (
                    "\n请作为 ABRAgent 主控智能体，自主调用对应领域的工具完成本阶段产物构建与验证。"
                )
                stage_msgs = [
                    SystemMessage(content=self.system_prompt),
                    HumanMessage(content=prompt_content),
                ]

                # 驱动 Agent 执行工具调用链
                agent_res = self._execute_agent_decision_loop(curr_stage.id, attempt, stage_msgs)
                summary_act = agent_res.get('summary', '工具调度完成')
                append_log(f"    Agent 动作总结: {summary_act}")
                self.messages.append(HumanMessage(content=prompt_content))
                self.messages.append(AIMessage(content=f"已完成 Stage {curr_stage.id} 工具调用: {summary_act}"))

                # 门禁严格自检
                check_passed, diag = self.manager.check_stage(curr_stage.id, is_complete=False)

                if check_passed:
                    append_log(f"[CHECK] Stage {curr_stage.id} 确定性质量门禁验证 PASSED.")
                    stage_passed = True
                    yield {
                        "event": "stage_checked",
                        "stage_id": curr_stage.id,
                        "progress_ratio": base_ratio + (0.8 / total_stages),
                        "status": self.manager.get_status(),
                        "tips": tips,
                        "diag": diag,
                        "log": "\n".join(logs),
                        "report": "*当前阶段门禁自检通过...*",
                        "journal": self.manager.get_all_stage_journal(),
                    }
                    break
                else:
                    curr_stage.fail_count += 1
                    fail_sum = diag.get("failure_summary") or {}
                    err_code = fail_sum.get("error_code", "ERR_GATE_FAILED")
                    err_msg = fail_sum.get("error", "未满足验收标准")
                    next_act = fail_sum.get("next_action", "请检查产物并重试")
                    append_log(f"[FAIL] 门禁未通过: [{err_code}] {err_msg}")
                    append_log(f"  --> 自愈行动指引: {next_act}")

                    # 记录驳回诊断：下一轮重试的 stage_msgs 会携带该反思上下文反馈给大模型，
                    # 同时 LangGraph 线程记忆 (同 stage 稳定 thread_id) 中保留了上一轮工具观测
                    last_failure = {"code": err_code, "error": err_msg, "next_action": next_act}

                    yield {
                        "event": "stage_failed_attempt",
                        "stage_id": curr_stage.id,
                        "progress_ratio": base_ratio + (0.5 / total_stages),
                        "status": self.manager.get_status(),
                        "tips": tips,
                        "diag": diag,
                        "log": "\n".join(logs),
                        "report": f"*阶段门禁自检未通过: [{err_code}]，正在进行智能体自愈修复...*",
                        "journal": self.manager.get_all_stage_journal(),
                    }
                    if attempt < self.max_retries:
                        time.sleep(1)

            elapsed_stage = time.time() - t0
            curr_stage.finish_running(status="PASSED" if stage_passed else "FAILED", duration=elapsed_stage)

            # 4. 记录日志与推进阶段
            if stage_passed or curr_stage.skip:
                append_log(f"[JOURNAL] 记录 Stage {curr_stage.id} 研发日志...")
                notes = f"Stage {curr_stage.id} ({curr_stage.name}) 顺利完成，通过确定性门禁质检 (耗时: {elapsed_stage:.1f}s)"
                delivs = curr_stage.expected_outputs
                findings = {"status": "PASSED", "duration_seconds": round(elapsed_stage, 1)}

                self.manager.set_stage_journal(
                    stage_id=curr_stage.id,
                    notes=notes,
                    deliverables=delivs,
                    key_findings=findings,
                )
                comp_ok, comp_res = self.manager.complete_stage(curr_stage.id)
                self._notify(curr_stage.id, "PASSED", elapsed_stage)
                append_log(f"[COMPLETE] {comp_res.get('message', '阶段成功推进')}")

                yield {
                    "event": "stage_completed",
                    "stage_id": curr_stage.id,
                    "progress_ratio": (stage_idx + 1) / total_stages,
                    "status": self.manager.get_status(),
                    "tips": self.manager.get_current_tips(),
                    "diag": {"status": "PASSED", "message": comp_res.get("message")},
                    "log": "\n".join(logs),
                    "report": "*科研任务持续推进中...*",
                    "journal": self.manager.get_all_stage_journal(),
                }
            else:
                append_log(f"[ERROR] Stage {curr_stage.id} 超过最大重试次数，工作流暂停。")
                self._notify(curr_stage.id, "FAILED", elapsed_stage)
                break

        total_elapsed = (datetime.now() - start_time).total_seconds()
        all_done = self.manager.is_all_completed()

        append_log("\n" + "=" * 75)
        append_log(f"[{'DONE' if all_done else 'INCOMPLETE'}] ABRAgent 执行结束: {'全流程成功闭环' if all_done else '部分完成'}")
        append_log(f"总耗时: {total_elapsed:.1f}s")

        task_dir = self.manager.get_task_output_dir(self.goal)
        cand_reports = [
            task_dir / "final_research_report.md",
            task_dir / "final_report.md",
            task_dir / "battery_research_synthesis_report.md",
            ROOT_DIR / "output" / "auto_battery_research" / "final_research_report.md",
        ]
        found_rf = next((p for p in cand_reports if p.exists()), None)
        final_report_text = ""
        if found_rf:
            try:
                with open(found_rf, "r", encoding="utf-8") as rf:
                    final_report_text = rf.read()
                append_log(f"最终研发报告已生成: {found_rf.resolve()}")
            except Exception:
                pass

        yield {
            "event": "finish",
            "stage_id": self.manager.get_current_stage().id,
            "progress_ratio": 1.0 if all_done else 0.85,
            "status": self.manager.get_status(),
            "tips": self.manager.get_current_tips(),
            "diag": {"status": "DONE" if all_done else "PARTIAL"},
            "log": "\n".join(logs),
            "report": final_report_text or "*科研任务执行完毕。*",
            "journal": self.manager.get_all_stage_journal(),
        }

    def _execute_agent_decision_loop(
        self,
        stage_id: int,
        attempt: int,
        stage_messages: Optional[List[BaseMessage]] = None,
    ) -> Dict[str, Any]:
        """执行阶段内智能体的自主工具调用调度 (真正接入 LangChain/LangGraph ReAct 循环)."""
        executed_actions = []
        llm_invoked = False

        # 1. 优先尝试通过 LangChain / LangGraph 后端调用大模型自主 ReAct 决策
        try:
            openai_api_key = (
                os.getenv("OPENAI_API_KEY")
                or self.config.get("openai", {}).get("openai_api_key")
                or self.config.get("llm", {}).get("api_key")
            )
            is_valid_key = (
                openai_api_key
                and str(openai_api_key).strip()
                and str(openai_api_key).strip() not in ("dummy_key", "none", "None", "")
                and not str(openai_api_key).startswith("$(")
            )
            if is_valid_key:
                stage = self.manager.get_stage_by_id(stage_id)
                stage_name = stage.name if stage else f"Stage {stage_id}"
                call_msgs = stage_messages or self.messages
                # thread_id 按课题+阶段稳定：同一阶段内多次重试共享 LangGraph 线程记忆，
                # 上一轮已执行的工具调用与观测对反思修复轮可见
                goal_tag = hashlib.md5(self.goal.encode("utf-8")).hexdigest()[:8]
                self.log(f"    [LLM-ReAct] 正在唤醒大模型进行 Stage {stage_id} ({stage_name}) 动态深度推理与工具调用...")
                response = self.backend.invoke(call_msgs, thread_id=f"abr_{goal_tag}_stage_{stage_id}")
                llm_invoked = True

                # 解析 response 中的 AIMessage 或 messages 列表
                if isinstance(response, dict) and "messages" in response:
                    out_msgs = response["messages"]
                    for m in out_msgs:
                        if hasattr(m, "tool_calls") and m.tool_calls:
                            for tc in m.tool_calls:
                                t_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                                executed_actions.append(f"LLM ToolCall: {t_name}")
                        elif isinstance(m, AIMessage) and m.content:
                            self.messages.append(m)
                            self.log(f"    [LLM-Thought] {m.content[:200]}...")
                elif isinstance(response, AIMessage):
                    self.messages.append(response)
                    if response.content:
                        self.log(f"    [LLM-Thought] {response.content[:200]}...")
                    if hasattr(response, "tool_calls") and response.tool_calls:
                        for tc in response.tool_calls:
                            t_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                            executed_actions.append(f"LLM ToolCall: {t_name}")
        except Exception as e:
            self.log(f"    [LLM-Notice] 大模型在线推理异常/离线 ({e})，启用确定性领域工具调度保证任务完成。")

        # 2. 领域工具调度保障 (无论是在线还是离线，均确保阶段必须的领域产物被完整构建)
        if stage_id == 1:
            # Stage 1: 文献解析与分类
            t_inspect = self.tool_map.get("InspectLiteratureAssets")
            t_ingest = self.tool_map.get("IngestLiteraturePapers")
            insp_res = json.loads(t_inspect._run() if t_inspect else "{}")
            executed_actions.append(f"InspectLiteratureAssets -> {insp_res.get('total_markdown_papers', 0)} papers")
            if not insp_res.get("ready_for_next_stage") and t_ingest:
                t_ingest._run()
                executed_actions.append("IngestLiteraturePapers")

        elif stage_id == 2:
            # Stage 2: 语义标注与向量入库
            t_inspect = self.tool_map.get("InspectVectorDB")
            t_index = self.tool_map.get("IndexSemanticVectors")
            insp_res = json.loads(t_inspect._run() if t_inspect else "{}")
            executed_actions.append(f"InspectVectorDB -> ready={insp_res.get('ready_for_rag')}")
            if not insp_res.get("ready_for_rag") and t_index:
                t_index._run()
                executed_actions.append("IndexSemanticVectors")

        elif stage_id == 3:
            # Stage 3: 材料挖掘与电芯组装
            t_inspect = self.tool_map.get("InspectCellEntities")
            t_mine = self.tool_map.get("ExtractAndAssembleCells")
            insp_res = json.loads(t_inspect._run() if t_inspect else "{}")
            executed_actions.append(f"InspectCellEntities -> has_assets={insp_res.get('has_mining_assets')}")
            if not insp_res.get("has_mining_assets") and t_mine:
                t_mine._run()
                executed_actions.append("ExtractAndAssembleCells")

        elif stage_id == 4:
            # Stage 4: 多智能体 RAG 方案设计单链路服务 (Planner -> Retrieval -> Writer -> Reviewer + RelationEngine)
            task_dir = self.manager.get_task_output_dir(self.goal)
            scheme_file = task_dir / "design_scheme.json"

            scheme_valid = False
            if scheme_file.exists():
                try:
                    with open(scheme_file, "r", encoding="utf-8") as f:
                        sd = json.load(f)
                        if len(sd.get("evidence", [])) > 0 and sd.get("review_status") == "APPROVED":
                            scheme_valid = True
                except Exception:
                    pass

            # 幂等：LLM 轮已执行过单链路服务且产物有效时，不再重复落盘
            if not scheme_valid and not any("RunRAGDesign" in a for a in executed_actions):
                t_rag = self.tool_map.get("RunRAGDesign")
                if t_rag:
                    rag_res = json.loads(t_rag._run(target_goal=self.goal))
                    executed_actions.append(
                        f"RunRAGDesign -> review_status={rag_res.get('review_status', 'unknown')}"
                    )

        elif stage_id == 5:
            # Stage 5: PINN 物理仿真
            t_pinn = self.tool_map.get("RunPhysicsSimulation")
            if t_pinn:
                t_pinn._run(target_goal=self.goal)
                executed_actions.append("RunPhysicsSimulation")

        elif stage_id == 6:
            # Stage 6: 综合研报生成 (若大模型已调用则不重复执行)
            has_llm_report = any("SynthesizeResearchReport" in a for a in executed_actions)
            if not has_llm_report:
                t_report = self.tool_map.get("SynthesizeResearchReport")
                if t_report:
                    t_report._run(target_goal=self.goal)
                    executed_actions.append("SynthesizeResearchReport")

        return {
            "success": True,
            "llm_invoked": llm_invoked,
            "actions": executed_actions,
            "summary": " | ".join(executed_actions),
        }

    def run(self, goal: Optional[str] = None) -> bool:
        """非流式阻塞执行全流程."""
        last_event = {}
        for event in self.run_stream(goal=goal):
            last_event = event
        return self.manager.is_all_completed()
