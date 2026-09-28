"""AutoBatteryResearch 服务：包装已有执行内核，不增加科研循环。"""

import asyncio
import importlib
import json
import logging
import contextlib
import sys
import threading
from pathlib import Path

L = logging.getLogger("AutoBatteryResearch.MCP")
_EXECUTION_LOCK = threading.Lock()

from pydantic import ValidationError

from .schemas import (
    AutoBatteryResearchConsultInput, AutoBatteryResearchExperimentInput,
)


def create_autobatteryresearch_agent(goal):
    from auto_battery_research.agent import ABRAgent

    return ABRAgent(goal=goal, verbose=False, enable_file_log=False)


class AutoBatteryResearchService:
    """可独立用于 Python API 的领域专家服务。"""

    def __init__(self, agent_factory=None, llm_factory=None):
        # 在 stdio 读写线程启动前加载 Windows 原生扩展，不在工具线程首次加载。
        importlib.import_module("numpy")
        self.agent_factory = agent_factory or create_autobatteryresearch_agent
        self.llm_factory = llm_factory

    async def consult(self, question, context=None):
        request = AutoBatteryResearchConsultInput(question=question, context=context)
        question, context = request.question, request.context
        prompt = question
        if context:
            prompt += f"\n\n用户提供的上下文：\n{context}"
        return await asyncio.to_thread(self._run_consult, prompt)

    def _run_consult(self, prompt):
        from auto_battery_research.backend.llm_client import LLMClient
        from auto_battery_research.util.config import resolve_env_vars
        from auto_battery_research.util.env_loader import load_env
        import yaml

        # 与 TUI 自由问答使用同一配置和 LLMClient，但不初始化 StageManager/ABRAgent。
        with _EXECUTION_LOCK, contextlib.redirect_stdout(sys.stderr):
            load_env()
            setting_path = Path(__file__).resolve().parents[1] / "setting.yaml"
            config = resolve_env_vars(yaml.safe_load(setting_path.read_text(encoding="utf-8")) or {})
            client = self.llm_factory(config) if self.llm_factory else LLMClient(config)
            system_prompt = (
                "你是一名顶尖电化学储能与电池材料专家科研智能体。请结合前沿电化学知识，"
                "以严谨、专业、清晰的语言准确回答用户的学术或工程问题。"
                "回答需条理分明，使用标准化学分子式（如 LiNi0.8Co0.1Mn0.1O2、LiFSI）与适度数据支撑。"
                "当前未进行资料检索或实验验证；不确定的结论应明确说明，不要编造来源或数据。"
            )
            answer = client.generate(prompt, system_prompt=system_prompt)
            if not isinstance(answer, str):
                raise TypeError("LLM returned a non-text answer")
            return answer

    async def design_battery_experiment(
        self, goal, context=None, constraints=None, available_resources=None,
    ):
        try:
            request = AutoBatteryResearchExperimentInput(
                goal=goal, context=context, constraints=constraints,
                available_resources=available_resources,
            )
        except ValidationError:
            return self._experiment_error("invalid_input", "goal 必须是非空字符串，其他参数类型必须符合 schema")
        research_goal = request.goal
        if request.context:
            research_goal += f"\n\n用户提供的上下文：\n{request.context}"
        if request.constraints:
            research_goal += "\n\n科研与实验约束：\n" + json.dumps(request.constraints, ensure_ascii=False, sort_keys=True)
        if request.available_resources:
            research_goal += "\n\n可用材料与设备：\n" + json.dumps(request.available_resources, ensure_ascii=False, sort_keys=True)
        try:
            return await asyncio.to_thread(self._run_experiment, research_goal)
        except Exception as exc:
            L.exception("AutoBatteryResearch experiment design failed")
            error_type = "agent_execution_error"
            if isinstance(exc, TimeoutError):
                error_type = "timeout"
            elif isinstance(exc, ConnectionError):
                error_type = "external_tool_error"
            return self._experiment_error(error_type, "AutoBatteryResearch execution failed")

    @staticmethod
    def _experiment_error(error_type, message):
        return {
            "report_markdown": "", "report_path": None, "status": "error",
            "error": {"type": error_type, "message": message},
        }

    def _run_experiment(self, goal):
        with _EXECUTION_LOCK, contextlib.redirect_stdout(sys.stderr):
            agent = self.agent_factory(goal)
            report_path = agent.manager.get_task_output_dir(goal) / "final_research_report.md"
            before = report_path.stat() if report_path.is_file() else None
            final = {}
            for event in agent.run_stream(goal=goal):
                final = event
            if final.get("event") != "finish":
                raise RuntimeError("AutoBatteryResearch did not return a finish event")
            report = final.get("report", "")
            if not report.strip() or report.strip() == "*科研任务执行完毕。*":
                return self._experiment_error("missing_report", "AutoBatteryResearch 未生成完整报告")
            if not report_path.is_file():
                return self._experiment_error("missing_report_file", "AutoBatteryResearch 最终报告文件不存在")
            after = report_path.stat()
            if before and (before.st_ino, before.st_ctime_ns, before.st_mtime_ns, before.st_size) == (
                after.st_ino, after.st_ctime_ns, after.st_mtime_ns, after.st_size
            ):
                return self._experiment_error("missing_report_file", "AutoBatteryResearch 本次未生成新的最终报告文件")
            if report_path.read_text(encoding="utf-8") != report:
                return self._experiment_error("report_mismatch", "AutoBatteryResearch 报告正文与保存文件不一致")
            if final.get("diag", {}).get("status") != "DONE":
                return {
                    "report_markdown": report, "report_path": str(report_path.resolve()),
                    "status": "partial",
                    "error": {"type": "insufficient_evidence", "message": "AutoBatteryResearch 工作流未完成"},
                }
            return {
                "report_markdown": report, "report_path": str(report_path.resolve()),
                "status": "complete", "error": None,
            }
