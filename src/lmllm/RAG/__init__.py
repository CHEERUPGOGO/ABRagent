"""LMLLM RAG 模块 — 高比能锂电池材料筛选多智能体 RAG 系统

为高比能锂电池材料筛选场景提供:
- Planner → Retrieval → Writer → Reviewer 四阶段流水线
- 每个 Agent 可独立绑定不同 LLM 模型
- Chroma 向量检索 + TF-IDF 双后端

子模块:
- single_turn/  — 单轮 Gradio 问答 + Baseline CLI
- multi_turn/   — 多轮对话 + 批量导出
"""
from .llm_client import (
    LLMClient,
    safe_json_loads,
    rule_decompose_question,
    rule_conservative_answer,
)
from .bm25_kb import BM25KnowledgeBase, Passage
from .agents import (
    PlannerAgent,
    RetrievalAgent,
    WriterAgent,
    ReviewerAgent,
)
from .rag_pipeline import RAGPipeline
from .structured_output import (
    classify_question,
    normalize_latex,
    build_answer_markdown,
    format_evidence_display,
    format_process_log,
    extract_quantitative_summary,
    extract_materials_screening_data,
    save_markdown,
)
from .baselines import (
    BaselineA,
    BaselineB,
    build_baseline_a_markdown,
    build_baseline_b_markdown,
    run_comparison,
)
from .reranker import Qwen3Reranker
from .prompts import (
    get_prompt_summary,
    PROMPT_REGISTRY,
)

__all__ = [
    # LLM
    "LLMClient",
    "create_deepseek_llm",
    "safe_json_loads",
    # 知识库
    "BM25KnowledgeBase",
    "Passage",
    # 智能体
    "PlannerAgent",
    "RetrievalAgent",
    "WriterAgent",
    "ReviewerAgent",
    # Pipeline
    "RAGPipeline",
    # 结构化输出
    "classify_question",
    "normalize_latex",
    "build_answer_markdown",
    "format_evidence_display",
    "format_process_log",
    "extract_quantitative_summary",
    "extract_materials_screening_data",
    "save_markdown",
    # Reranker
    "Qwen3Reranker",
    # 基线
    "BaselineA",
    "BaselineB",
    "run_comparison",
    # 回退函数
    "rule_decompose_question",
    "rule_conservative_answer",
    # 配置
    "PRIMARY_LABELS",
    "LABEL_KEYWORDS",
    "COMPONENT_KEYWORDS",
    "EMBEDDING_MODEL",
    "CHROMA_DIR",
    "COLLECTION_NAME",
    "RETRIEVAL_MODE",
    "PLANNER_MODEL",
    "WRITER_MODEL",
    "REVIEWER_MODEL",
    "ensure_output_dir",
    # Prompt 审计
    "get_prompt_summary",
    "PROMPT_REGISTRY",
]
