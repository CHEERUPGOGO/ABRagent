"""DomainTools — 面向 ABRAgent 的电池科研领域工具集 (Stage 1 ~ Stage 6).

包含：
- Stage 1: InspectLiteratureAssetsTool, IngestLiteraturePapersTool
- Stage 2: InspectVectorDBTool, IndexSemanticVectorsTool
- Stage 3: InspectCellEntitiesTool, ExtractAndAssembleCellsTool
- Stage 4: RunRAGDesignTool (单链路服务; Planner/Retrieval/Writer/Reviewer 为管线内部能力)
- Stage 5: RunPhysicsSimulationTool
- Stage 6: SynthesizeResearchReportTool
"""

from __future__ import annotations

import os
import sys
import json
import time
import re
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Type
from pydantic import BaseModel, Field

from langchain_core.tools import BaseTool

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from auto_battery_research.util.logger import (
    log_tool_call,
    log_observation,
    log_thought,
    log_info,
    log_success,
    log_error,
)
from auto_battery_research.tools.workflow_actions import (
    run_literature_ingestion,
    run_vector_indexing,
    run_data_mining,
    run_rag_design,
    run_pinn_simulation,
    generate_synthesis_report,
    _get_target_task_dir,
    _merged_literature_dirs,
)


# =============================================================================
# Stage 1: 文献解析与分类工具 (Stage 1 Domain Tools)
# =============================================================================

class InspectLiteratureArgs(BaseModel):
    database_dir: Optional[str] = Field(
        default="database/type",
        description="分类文献库根目录路径 (例如 database/type 或 papers/merged)"
    )

class InspectLiteratureAssetsTool(BaseTool):
    name: str = "InspectLiteratureAssets"
    description: str = (
        "【Stage 1 工具】感知探测本地已解析与分类的学术文献资产。"
        "返回当前各组件（正极 cathode、负极 anode、电解液 electrolyte 等）的 Markdown 文献数量与状态。"
    )
    args_schema: Type[BaseModel] = InspectLiteratureArgs

    def _run(self, database_dir: Optional[str] = "database/type") -> str:
        log_tool_call(self.name, f"database_dir='{database_dir}'")
        target_path = ROOT_DIR / (database_dir or "database/type")

        md_count = 0
        categories = {}
        if target_path.exists():
            for sub in target_path.iterdir():
                if sub.is_dir():
                    count = len(list(sub.rglob("*.md")))
                    categories[sub.name] = count
                    md_count += count
        # 合并产物：规范路径 (papers/merged) + 历史数据路径 (papers/text_merged)
        for mdir in _merged_literature_dirs():
            if mdir.exists():
                merged_count = len(list(mdir.rglob("*.md")))
                if merged_count:
                    categories[mdir.name] = merged_count
                    md_count += merged_count

        res = {
            "exists": md_count > 0,
            "total_markdown_papers": md_count,
            "categories": categories,
            "ready_for_next_stage": md_count >= 1,
            "suggestion": "文献资产充足，可调用 Check 门禁自检" if md_count >= 1 else "缺少已解析文献，请调用 IngestLiteraturePapers 进行解析",
        }
        log_observation(f"文献探测完成: 发现 {md_count} 篇 Markdown 文献")
        return json.dumps(res, ensure_ascii=False, indent=2)


class IngestLiteratureArgs(BaseModel):
    input_pdf_dir: Optional[str] = Field(
        default="papers/pdf",
        description="待解析的原始 PDF 文件目录"
    )
    max_files: Optional[int] = Field(
        default=5,
        description="最大解析篇数限制 (默认 5 篇)"
    )

class IngestLiteraturePapersTool(BaseTool):
    name: str = "IngestLiteraturePapers"
    description: str = (
        "【Stage 1 工具】触发增量文献解析与分类流水线。"
        "从 PDF 提取 DOI、转换为 Markdown 并分类入库至正负极与电解液目录。"
    )
    args_schema: Type[BaseModel] = IngestLiteratureArgs

    def _run(self, input_pdf_dir: Optional[str] = "papers/pdf", max_files: Optional[int] = 5) -> str:
        log_tool_call(self.name, f"input_pdf_dir='{input_pdf_dir}', max_files={max_files}")
        res = run_literature_ingestion(input_pdf_dir=input_pdf_dir, max_files=max_files or 5)
        return json.dumps(res, ensure_ascii=False, indent=2)


# =============================================================================
# Stage 2: 语义标注与向量入库工具 (Stage 2 Domain Tools)
# =============================================================================

class InspectVectorDBArgs(BaseModel):
    chroma_dir: Optional[str] = Field(
        default="miner/chroma/paragraphs_q",
        description="Chroma 向量数据库目录"
    )

class InspectVectorDBTool(BaseTool):
    name: str = "InspectVectorDB"
    description: str = (
        "【Stage 2 工具】感知探测 Chroma 语义向量数据库与段落标注元数据。"
        "返回已入库的段落向量数量、6类语义标签分布与可用性状态。"
    )
    args_schema: Type[BaseModel] = InspectVectorDBArgs

    def _run(self, chroma_dir: Optional[str] = "miner/chroma/paragraphs_q") -> str:
        log_tool_call(self.name, f"chroma_dir='{chroma_dir}'")
        meta_candidates = [
            ROOT_DIR / "miner" / "json" / "100" / "paragraph_metadata_v4.json",
            ROOT_DIR / "miner" / "json" / "100" / "paragraph_metadata_v4_20260622_155323.json",
            ROOT_DIR / "miner" / "json" / "test_paragraphs.json",
            ROOT_DIR / "miner" / "json" / "metadata" / "meta_merged.json",
        ]
        found_meta = next((p for p in meta_candidates if p.exists()), None)
        chroma_path = ROOT_DIR / (chroma_dir or "miner/chroma/paragraphs_q")

        total_paras = 0
        if found_meta:
            try:
                with open(found_meta, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        total_paras = len(data)
                    elif isinstance(data, dict):
                        total_paras = len(data.get("paragraphs", data.get("data", [])))
            except Exception:
                pass

        chroma_ready = chroma_path.exists() and len(list(chroma_path.iterdir())) > 0

        res = {
            "metadata_file_found": str(found_meta.relative_to(ROOT_DIR)) if found_meta else None,
            "total_paragraphs": total_paras,
            "chroma_db_exists": chroma_ready,
            "ready_for_rag": total_paras > 0 or chroma_ready,
            "suggestion": "向量库与元数据已就绪，可调用 Check 门禁自检" if (total_paras > 0 or chroma_ready) else "缺少向量索引，请调用 IndexSemanticVectors 进行入库",
        }
        log_observation(f"向量库探测完成: 发现 {total_paras} 段语义元数据, Chroma就绪={chroma_ready}")
        return json.dumps(res, ensure_ascii=False, indent=2)


class IndexSemanticVectorsArgs(BaseModel):
    max_papers: Optional[int] = Field(
        default=5,
        description="处理最大论文数"
    )
    incremental: Optional[bool] = Field(
        default=True,
        description="是否采用增量模式"
    )

class IndexSemanticVectorsTool(BaseTool):
    name: str = "IndexSemanticVectors"
    description: str = (
        "【Stage 2 工具】触发段落 6 类语义打标与 Qwen3-Embedding 向量化持久化入库。"
    )
    args_schema: Type[BaseModel] = IndexSemanticVectorsArgs

    def _run(self, max_papers: Optional[int] = 5, incremental: Optional[bool] = True) -> str:
        log_tool_call(self.name, f"max_papers={max_papers}, incremental={incremental}")
        res = run_vector_indexing(incremental=incremental if incremental is not None else True, max_papers=max_papers or 5)
        return json.dumps(res, ensure_ascii=False, indent=2)


# =============================================================================
# Stage 3: 材料挖掘与电芯组装工具 (Stage 3 Domain Tools)
# =============================================================================

class InspectCellEntitiesArgs(BaseModel):
    cell_dir: Optional[str] = Field(
        default="",
        description="电芯实体存储目录 (留空则由 StageManager 或默认路径解析)"
    )

class InspectCellEntitiesTool(BaseTool):
    name: str = "InspectCellEntities"
    description: str = (
        "【Stage 3 工具】感知探测已挖掘抽取的微观材料数据与已组装的电芯 (Cell) 实体。"
        "返回已组装电芯数、正负极与电解液特征三元组分布。"
    )
    args_schema: Type[BaseModel] = InspectCellEntitiesArgs

    def _run(self, cell_dir: Optional[str] = "") -> str:
        log_tool_call(self.name, f"cell_dir='{cell_dir}'")
        extracted_candidates = list((ROOT_DIR / "miner" / "json").glob("*_extracted*.json"))
        target_dir = ROOT_DIR / cell_dir if cell_dir else ROOT_DIR / "output/auto_battery_research/cell_assembly"
        
        cells_count = 0
        if target_dir.exists():
            for f in target_dir.glob("*.json"):
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                        if isinstance(data, list):
                            cells_count += len(data)
                        elif isinstance(data, dict):
                            cells_count += len(data.get("cells", [1]))
                except Exception:
                    pass

        has_data = cells_count > 0 or len(extracted_candidates) > 0

        res = {
            "extracted_json_files": [str(p.relative_to(ROOT_DIR)) for p in extracted_candidates[:5]],
            "assembled_cells_count": cells_count,
            "has_mining_assets": has_data,
            "suggestion": "材料与电芯实体数据就绪，可调用 Check 门禁自检" if cells_count > 0 else "缺少电芯组装数据，请调用 ExtractAndAssembleCells 执行数据挖掘",
        }
        log_observation(f"电芯实体探测完成: 发现 {cells_count} 个电芯实体, {len(extracted_candidates)} 个抽取源文件")
        return json.dumps(res, ensure_ascii=False, indent=2)


class ExtractAndAssembleCellsArgs(BaseModel):
    sample_limit: Optional[int] = Field(
        default=10,
        description="最大提取样本数量"
    )
    target_query: Optional[str] = Field(
        default="",
        description="课题目标 (用于课题专属目录落盘)"
    )

class ExtractAndAssembleCellsTool(BaseTool):
    name: str = "ExtractAndAssembleCells"
    description: str = (
        "【Stage 3 工具】触发材料微观表征挖掘、三层归一化与真实电芯 (Cell) 实体组装流水线。"
    )
    args_schema: Type[BaseModel] = ExtractAndAssembleCellsArgs

    def _run(self, sample_limit: Optional[int] = 10, target_query: Optional[str] = "") -> str:
        goal = (target_query or "").strip()
        if not goal:
            from auto_battery_research.tools.stage_tools import get_stage_manager
            goal = get_stage_manager().target_goal
        log_tool_call(self.name, f"sample_limit={sample_limit}, target_query='{goal}'")
        res = run_data_mining(max_files=sample_limit or 10, target_query=goal)
        return json.dumps(res, ensure_ascii=False, indent=2)


# =============================================================================
# Stage 4: 多智能体 RAG 方案设计服务工具 (Stage 4 Domain Tools)
# =============================================================================

class RunRAGDesignArgs(BaseModel):
    target_goal: Optional[str] = Field(
        default="",
        description="课题目标 (留空则取当前工作流活跃课题)"
    )
    design_query: Optional[str] = Field(
        default="",
        description="设计需求描述 (留空则同课题目标)"
    )

class RunRAGDesignTool(BaseTool):
    """Stage 4 唯一落盘入口: 单链路收敛 Planner -> Retrieval -> Writer -> Reviewer + RelationEngine.

    无论 CLI / TUI / Web / MCP 还是 Agent 工具调用，都经由 run_rag_design 服务执行
    同一条管线；design_scheme.md/.json 仅由该链路写入课题任务目录。Planner/Retrieval/
    Writer/Reviewer 作为管线内部能力保留在 src/lmllm/RAG 中，不再作为独立落盘工具暴露，
    避免双写路径造成契约漂移与课题隔离破坏。
    """

    name: str = "RunRAGDesign"
    description: str = (
        "【Stage 4 服务工具】执行多智能体 RAG 电池方案设计单链路流水线 "
        "(Planner -> Retrieval -> Writer -> Reviewer + RelationEngine C1-C8 硬约束核算)，"
        "生成 design_scheme.md/.json 与 rag_result.json 并写入课题任务目录。"
        "证据检索、方案撰写与规则审查在管线内部完成，无需单独调用其他 Stage 4 工具。"
    )
    args_schema: Type[BaseModel] = RunRAGDesignArgs

    def _run(self, target_goal: Optional[str] = "", design_query: Optional[str] = "") -> str:
        goal = (target_goal or "").strip()
        if not goal:
            from auto_battery_research.tools.stage_tools import get_stage_manager
            goal = get_stage_manager().target_goal
        dq = (design_query or "").strip() or None
        log_tool_call(self.name, f"target_goal='{goal}', design_query='{dq}'")
        res = run_rag_design(target_query=goal, design_query=dq)
        return json.dumps(res, ensure_ascii=False, indent=2)

# =============================================================================
# Stage 5: PINN / P2D 物理仿真工具 (Stage 5 Domain Tools)
# =============================================================================

class RunPhysicsSimulationArgs(BaseModel):
    target_goal: Optional[str] = Field(
        default="",
        description="待仿真方案课题 (留空则动态绑定当前工作流活跃课题)"
    )
    current_rate: Optional[str] = Field(
        default="0.2C",
        description="放电倍率 (如 0.1C, 0.2C, 0.5C, 1.0C)"
    )

class RunPhysicsSimulationTool(BaseTool):
    name: str = "RunPhysicsSimulation"
    description: str = (
        "【Stage 5 仿真工具】调用 PyBaMM Newman P2D 偏微分方程求解器或 PINN 物理代理模型进行电池充放电曲线仿真与能量密度标定。"
    )
    args_schema: Type[BaseModel] = RunPhysicsSimulationArgs

    def _run(self, target_goal: Optional[str] = "", current_rate: Optional[str] = "0.2C") -> str:
        goal = (target_goal or "").strip()
        if not goal:
            from auto_battery_research.tools.stage_tools import get_stage_manager
            goal = get_stage_manager().target_goal
        log_tool_call(self.name, f"target_goal='{goal}', current_rate='{current_rate}'")
        res = run_pinn_simulation(target_query=goal, current_rate=current_rate or "0.2C")
        return json.dumps(res, ensure_ascii=False, indent=2)


# =============================================================================
# Stage 6: 综合研报生成工具 (Stage 6 Domain Tools)
# =============================================================================

class SynthesizeResearchReportArgs(BaseModel):
    target_goal: Optional[str] = Field(
        default="",
        description="研发总课题 (留空则动态绑定当前工作流活跃课题)"
    )

class SynthesizeResearchReportTool(BaseTool):
    name: str = "SynthesizeResearchReport"
    description: str = (
        "【Stage 6 报告工具】汇总全流程文献资产、电芯数据挖掘、RAG 材料设计方案与物理仿真结果，生成最终综合科研报告 (final_research_report.md)。"
    )
    args_schema: Type[BaseModel] = SynthesizeResearchReportArgs

    def _run(self, target_goal: Optional[str] = "") -> str:
        goal = (target_goal or "").strip()
        if not goal:
            from auto_battery_research.tools.stage_tools import get_stage_manager
            goal = get_stage_manager().target_goal
        log_tool_call(self.name, f"target_goal='{goal}'")
        res = generate_synthesis_report(target_query=goal)
        return json.dumps(res, ensure_ascii=False, indent=2)


# =============================================================================
# 导出全量领域工具实例列表 (Export Domain Tools)
# =============================================================================

def get_all_domain_tools() -> List[BaseTool]:
    """获取所有电池科研领域工具实例."""
    return [
        # Stage 1
        InspectLiteratureAssetsTool(),
        IngestLiteraturePapersTool(),
        # Stage 2
        InspectVectorDBTool(),
        IndexSemanticVectorsTool(),
        # Stage 3
        InspectCellEntitiesTool(),
        ExtractAndAssembleCellsTool(),
        # Stage 4 (单链路服务: 管线内部保留 Planner/Retrieval/Writer/Reviewer)
        RunRAGDesignTool(),
        # Stage 5
        RunPhysicsSimulationTool(),
        # Stage 6
        SynthesizeResearchReportTool(),
    ]
