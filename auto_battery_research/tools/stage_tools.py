"""StageTools — 面向 Agent 与 MCP 的工作流统一工具集 (AutoBatteryResearch Agent).

包含函数与 LangChain BaseTool 两种接口形态：
- RoleInfo: 获取智能体角色背景与使命
- Status: 获取工作流全局状态与各 Stage 进度
- CurrentTips: 获取当前活跃 Stage 任务指引与验收指标
- Check: 执行门禁自检，返回结构化诊断 (只自检不推进)
- Complete: 执行终审并推进阶段
- SetStageJournal: 记录阶段研发心得与关键数据
- AllStageJournal: 查看历史阶段日志
- SkipStage / EnableStage: 动态跳过或激活阶段（如 PINN）
- RunStageTask: 驱动底层数据挖掘或 RAG 计算流水线
"""

from __future__ import annotations

import json
import threading
from typing import Dict, Any, List, Optional, Type
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool

from auto_battery_research.workflow.stage_manager import StageManager

# 运行态上下文: 全局单例 + 按课题缓存。
# Web 队列允许并发请求进入，Gradio 事件处理器运行在线程池中，读写这两个
# 模块级容器存在竞态 —— 一律持 _MANAGER_LOCK 访问 (双检 + 缓存写入原子化)。
_GLOBAL_MANAGER: Optional[StageManager] = None
_GOAL_MANAGER_CACHE: Dict[str, StageManager] = {}
_MANAGER_LOCK = threading.RLock()


def get_stage_manager() -> StageManager:
    """获取或初始化全局工作流状态机单例 (线程安全)."""
    global _GLOBAL_MANAGER
    if _GLOBAL_MANAGER is None:
        with _MANAGER_LOCK:
            if _GLOBAL_MANAGER is None:
                _GLOBAL_MANAGER = StageManager()
    return _GLOBAL_MANAGER


def set_stage_manager(mgr: StageManager):
    """注入自定义状态机 (线程安全)."""
    global _GLOBAL_MANAGER
    with _MANAGER_LOCK:
        _GLOBAL_MANAGER = mgr


def get_stage_manager_for_goal(target_goal: Optional[str] = None) -> StageManager:
    """获取指定课题的 StageManager (优先复用全局单例，其次按课题缓存；线程安全).

    避免每次工具调用都重新实例化 StageManager —— 每次实例化都会触发
    全量 Checker 级联 (auto_detect_existing_progress) 与状态文件双写，
    既浪费性能，又可能与主流程的内存状态互相踩踏。
    """
    global _GOAL_MANAGER_CACHE
    if _GLOBAL_MANAGER is not None and (not target_goal or _GLOBAL_MANAGER.target_goal == target_goal):
        return _GLOBAL_MANAGER
    key = (target_goal or "").strip() or "general_research_task"
    with _MANAGER_LOCK:
        mgr = _GOAL_MANAGER_CACHE.get(key)
        if mgr is None:
            mgr = StageManager(target_goal=key)
            _GOAL_MANAGER_CACHE[key] = mgr
    return mgr


# =============================================================================
# Agent / MCP 核心工具函数定义
# =============================================================================

def tool_get_role_info() -> Dict[str, Any]:
    """获取智能体角色背景与使命."""
    mgr = get_stage_manager()
    return {
        "agent_name": mgr.config.get("agent_name", "AutoBatteryResearch Agent"),
        "mission": mgr.mission_info.get("name"),
        "version": mgr.mission_info.get("version"),
        "system_prompt": mgr.mission_info.get("system_prompt"),
        "total_stages": len(mgr.stages),
    }


def tool_get_status() -> Dict[str, Any]:
    """获取工作流全局状态与各 Stage 进度."""
    mgr = get_stage_manager()
    return mgr.get_status()


def tool_get_detail() -> Dict[str, Any]:
    """获取任务与所有阶段的深入明细信息."""
    mgr = get_stage_manager()
    return mgr.get_detail()


def tool_get_current_tips() -> str:
    """获取当前活跃 Stage 任务指引与验收指标."""
    mgr = get_stage_manager()
    return mgr.get_current_tips()


def tool_check_stage(stage_id: Optional[int] = None) -> Dict[str, Any]:
    """执行门禁自检 (只自检诊断，不推进状态)."""
    mgr = get_stage_manager()
    passed, diag = mgr.check_stage(stage_id=stage_id, is_complete=False)
    return {
        "passed": passed,
        "stage_id": stage_id or mgr.get_current_stage().id,
        "diagnostic": diag,
    }


def tool_complete_stage(stage_id: Optional[int] = None) -> Dict[str, Any]:
    """执行阶段终审并通过：通过后自动推进至下一阶段."""
    mgr = get_stage_manager()
    passed, res = mgr.complete_stage(stage_id=stage_id)
    return res


def tool_set_stage_journal(
    stage_id: Optional[int] = None,
    notes: str = "",
    deliverables: Optional[List[str]] = None,
    key_findings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """记录阶段研发日志与关键数据."""
    mgr = get_stage_manager()
    return mgr.set_stage_journal(
        stage_id=stage_id,
        notes=notes,
        deliverables=deliverables,
        key_findings=key_findings,
    )


def tool_get_all_stage_journal() -> List[Dict[str, Any]]:
    """查看所有阶段的历史研发日志."""
    mgr = get_stage_manager()
    return mgr.get_all_stage_journal()


def tool_skip_stage(stage_id: int, reason: str = "手动跳过") -> Dict[str, Any]:
    """动态跳过指定阶段 (例如 Stage 5 PINN 仿真)."""
    mgr = get_stage_manager()
    success = mgr.set_stage_skip(stage_id, skip=True, reason=reason)
    return {
        "success": success,
        "stage_id": stage_id,
        "status": "SKIPPED" if success else "NOT_FOUND",
        "reason": reason,
    }


def tool_enable_stage(stage_id: int) -> Dict[str, Any]:
    """动态激活已跳过的阶段 (例如重新开启 Stage 5 PINN 仿真)."""
    mgr = get_stage_manager()
    success = mgr.set_stage_skip(stage_id, skip=False)
    return {
        "success": success,
        "stage_id": stage_id,
        "status": "PENDING" if success else "NOT_FOUND",
        "message": f"Stage {stage_id} 已重新激活为必检阶段。",
    }


def tool_run_stage_task(
    stage_id: Optional[int] = None,
    target_query: str = "设计400Wh/kg高比能液态锂金属电池方案",
    **kwargs
) -> Dict[str, Any]:
    """驱动执行当前或指定 Stage 的底层计算与挖掘任务."""
    from auto_battery_research.tools.workflow_actions import (
        run_literature_ingestion,
        run_vector_indexing,
        run_data_mining,
        run_rag_design,
        run_pinn_simulation,
        generate_synthesis_report,
    )
    mgr = get_stage_manager()
    curr = mgr.get_stage_by_id(stage_id) if stage_id else mgr.get_current_stage()
    sid = curr.id

    if sid == 1:
        return run_literature_ingestion(target_query=target_query, **kwargs)
    elif sid == 2:
        return run_vector_indexing(target_query=target_query, **kwargs)
    elif sid == 3:
        return run_data_mining(target_query=target_query, max_files=kwargs.get("max_files", 5), **kwargs)
    elif sid == 4:
        return run_rag_design(target_query=target_query, **kwargs)
    elif sid == 5:
        if curr.skip:
            return {"success": True, "message": "Stage 5 PINN 物理仿真已配置跳过，无需执行计算。"}
        return run_pinn_simulation(target_query=target_query, **kwargs)
    elif sid == 6:
        return generate_synthesis_report(target_query=target_query, stage_manager=mgr, **kwargs)
    else:
        return {"success": False, "error": f"未知 Stage ID: {sid}"}


# =============================================================================
# LangChain BaseTool 类封装 (供 ABRAgent 及 LangGraph 引擎使用)
# =============================================================================

class EmptyArgs(BaseModel):
    pass


class ToolCurrentTips(BaseTool):
    name: str = "CurrentTips"
    description: str = (
        "【工作流护栏工具】获取当前活跃 Stage 的任务指引、输入规范与验收指标。"
        "智能体在每轮决策前应优先调用此工具以感知当前阶段目标，防止走偏。"
    )
    args_schema: Type[BaseModel] = EmptyArgs

    def _run(self) -> str:
        return tool_get_current_tips()


class ToolStatus(BaseTool):
    name: str = "Status"
    description: str = (
        "【工作流状态工具】获取 6 阶段全局状态矩阵与推进进度（包含各 Stage 状态 PASSED/IN_PROGRESS/PENDING/SKIPPED）。"
    )
    args_schema: Type[BaseModel] = EmptyArgs

    def _run(self) -> str:
        return json.dumps(tool_get_status(), ensure_ascii=False, indent=2)


class CheckStageArgs(BaseModel):
    stage_id: Optional[int] = Field(
        default=None,
        description="可选指定校验的 Stage ID，默认当前活跃阶段"
    )

class ToolCheck(BaseTool):
    name: str = "Check"
    description: str = (
        "【确定性门禁质检工具】对当前或指定阶段产物执行严格的确定性门禁检查 (Checker)。"
        "只执行自检并返回结构化诊断（passed、error_code、failure_summary、next_action），绝不擅自推进状态。"
    )
    args_schema: Type[BaseModel] = CheckStageArgs

    def _run(self, stage_id: Optional[int] = None) -> str:
        return json.dumps(tool_check_stage(stage_id), ensure_ascii=False, indent=2)


class CompleteStageArgs(BaseModel):
    stage_id: Optional[int] = Field(
        default=None,
        description="可选指定终审通过的 Stage ID，默认当前活跃阶段"
    )

class ToolComplete(BaseTool):
    name: str = "Complete"
    description: str = (
        "【阶段终审推进工具】在 Check 自检 100% 通过后调用，原子推进当前工作流指针至下一阶段。"
        "若门禁未通过，此操作将失败并拒绝推进。"
    )
    args_schema: Type[BaseModel] = CompleteStageArgs

    def _run(self, stage_id: Optional[int] = None) -> str:
        return json.dumps(tool_complete_stage(stage_id), ensure_ascii=False, indent=2)


class SetJournalArgs(BaseModel):
    notes: str = Field(
        description="当前阶段的研发心得、核心洞察或处理摘要"
    )
    stage_id: Optional[int] = Field(
        default=None,
        description="记录日志的 Stage ID，默认当前活跃阶段"
    )

class ToolSetStageJournal(BaseTool):
    name: str = "SetStageJournal"
    description: str = (
        "【研发日志工具】在推进阶段前或完成重要里程碑时，记录当前阶段的科研心得与交付物路径。"
    )
    args_schema: Type[BaseModel] = SetJournalArgs

    def _run(self, notes: str, stage_id: Optional[int] = None) -> str:
        return json.dumps(tool_set_stage_journal(stage_id=stage_id, notes=notes), ensure_ascii=False, indent=2)


class ToolGetAllStageJournal(BaseTool):
    name: str = "AllStageJournal"
    description: str = (
        "【历史日志工具】查看所有已完成阶段的历史研发日志与关键数据溯源。"
    )
    args_schema: Type[BaseModel] = EmptyArgs

    def _run(self) -> str:
        return json.dumps(tool_get_all_stage_journal(), ensure_ascii=False, indent=2)


def get_all_stage_tools() -> List[BaseTool]:
    """获取所有工作流状态机护栏工具."""
    return [
        ToolCurrentTips(),
        ToolStatus(),
        ToolCheck(),
        ToolComplete(),
        ToolSetStageJournal(),
        ToolGetAllStageJournal(),
    ]
