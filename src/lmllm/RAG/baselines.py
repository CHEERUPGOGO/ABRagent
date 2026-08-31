"""基线对比模块 — Baseline A(直接问答)和 Baseline B(仅检索拼接)

提供两种基线用于与多智能体 RAG 方案对比.

Baseline A: 单模型直接回答,不依赖知识库检索
Baseline B: TF-IDF 检索 + 简单拼接,不使用 LLM 生成
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .structured_output import (
    classify_question, normalize_latex, save_markdown,
)

# ═══════════════════════════════════════════════════════════════════
# Baseline A: 单模型直接回答(无 RAG)
# ═══════════════════════════════════════════════════════════════════

BASELINE_A_SYSTEM_PROMPT = """你是锂电池领域专家.请直接回答用户的问题.

要求:
- 基于你的知识给出专业回答
- 数值要标注单位和条件
- 不要使用 LaTeX 公式格式
- 如果无法确定,请明确说明
"""

class BaselineA:
    """Baseline A: 单模型直接回答,不依赖知识库.

    """

    name = "Baseline A (直接回答)"

    def __init__(self, llm_client):
        self.llm = llm_client

    def ask(self, question: str) -> str:
        """直接问答,不使用知识库"""
        question = question.strip()
        if not question:
            return "请输入问题."

        if not self.llm.available:
            return (
                "## Baseline A 回退\n\n"
                "当前没有可用的 LLM 后端,无法生成回答.\n\n"
                "请设置 DEEPSEEK_API_KEY 或启动 Ollama 服务."
            )

        try:
            answer = self.llm.chat(
                BASELINE_A_SYSTEM_PROMPT,
                f"请回答以下锂电池相关问题:\n{question}",
                temperature=0.2,
            )
            return answer
        except Exception as e:
            return f"## Baseline A 错误\n\nLLM 调用失败: {e}"

    def run_and_export(
        self, question: str, output_dir: Optional[Path] = None
    ) -> Tuple[str, str]:
        """运行并导出结构化 Markdown"""
        answer = self.ask(question)
        md = build_baseline_a_markdown(question, answer)
        path = save_markdown(md, "baseline_A", output_dir)
        return md, str(path)

def build_baseline_a_markdown(question: str, answer: str) -> str:
    """生成 Baseline A 的结构化输出.

    """
    qtype = classify_question(question)

    challenges = [
        "当前方案未使用知识库检索,回答缺乏显式证据支撑.",
        "当前方案未使用审核机制,可能存在超出知识库范围的内容(幻觉).",
        "当前方案未使用多智能体协作,无法展示任务拆解与过程可追溯性.",
        "当前方案无法提供具体的文献引用和来源追溯.",
    ]

    lines: List[str] = [
        f"# Baseline A(单模型直接回答):{question[:60]}",
        "",
        "## 1. 任务信息",
        f"- 用户问题:{question}",
        f"- 问题类型:{qtype}",
        f"- 方案说明:使用单个 LLM 直接回答,不依赖知识库检索与多智能体协作",
        f"- 生成时间:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 2. 回答",
        answer.strip(),
        "",
        "## 3. 潜在挑战与改进建议",
    ]
    lines.extend([f"- {x}" for x in challenges])

    lines.extend([
        "",
        "## 4. 参考来源",
        "- 本方案无显式知识库引用,答案由单模型直接生成,来源不可追溯.",
        "",
        "## 5. 方案说明",
        "- 方案名称:Baseline A(单模型直接回答)",
        "- 是否使用知识库检索:否",
        "- 是否使用多智能体协作:否",
        "- 是否使用审核机制:否",
        "- 是否支持证据引用:否",
    ])

    return "\n".join(lines)

# ═══════════════════════════════════════════════════════════════════
# Baseline B: 仅检索 + 拼接摘要(无 LLM 生成)
# ═══════════════════════════════════════════════════════════════════

class BaselineB:
    """Baseline B: 仅检索 + 拼接,不使用 LLM 生成.

    """

    name = "Baseline B (仅检索拼接)"

    def __init__(self, kb):
        """
        Args:
            kb: TFIDFKnowledgeBase 实例
        """
        self.kb = kb

    def retrieve(self, question: str, top_k: int = 10) -> Tuple[str, List[Dict[str, Any]]]:
        """仅检索,返回拼接的检索结果作为答案.

        Returns:
            (formatted_markdown, raw_results)
        """
        question = question.strip()
        if not question:
            return "请输入问题.", []

        self.kb.refresh()
        hits = self.kb.search(question, top_k=top_k)

        if not hits:
            return "## 未检索到相关证据\n\n当前知识库中未找到与问题相关的文献段落.", []

        # 去重拼接
        seen_texts = set()
        unique_hits: List[Tuple[Any, float]] = []
        for passage, score in hits:
            if passage.text not in seen_texts:
                seen_texts.add(passage.text)
                unique_hits.append((passage, score))

        results = [
            {
                "passage_id": p.passage_id,
                "source": p.source,
                "score": round(s, 4),
                "text": p.text,
                "metadata": p.metadata,
            }
            for p, s in unique_hits
        ]

        md = build_baseline_b_markdown(question, results)
        return md, results

    def run_and_export(
        self, question: str, top_k: int = 10, output_dir: Optional[Path] = None
    ) -> Tuple[str, str]:
        """运行并导出"""
        md, results = self.retrieve(question, top_k)
        path = save_markdown(md, "baseline_B", output_dir)
        return md, str(path)

def build_baseline_b_markdown(question: str, results: List[Dict[str, Any]]) -> str:
    """生成 Baseline B 的结构化输出.

    """
    qtype = classify_question(question)

    lines: List[str] = [
        f"# Baseline B(仅检索拼接):{question[:60]}",
        "",
        "## 1. 任务信息",
        f"- 用户问题:{question}",
        f"- 问题类型:{qtype}",
        f"- 方案说明:通过 TF-IDF 知识库检索相关段落,直接拼接为答案,不使用 LLM 生成",
        f"- 生成时间:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 返回证据数:{len(results)}",
        "",
        "## 2. 检索证据(拼接为答案)",
    ]

    if not results:
        lines.append("未检索到相关证据.")
    else:
        for i, item in enumerate(results, 1):
            pid = item.get("passage_id", f"E{i}")
            source = item.get("source", "未知")
            score = item.get("score", 0)
            text = normalize_latex(item.get("text", ""))
            lines.append(f"### 证据 {i} [{pid}]")
            lines.append(f"- 来源:{source}")
            lines.append(f"- 相似度:{score}")
            lines.append(f"- 内容:{text}")
            lines.append("")

    lines.extend([
        "## 3. 方案说明",
        "- 方案名称:Baseline B(仅检索拼接)",
        "- 是否使用知识库检索:是(TF-IDF)",
        "- 是否使用多智能体协作:否",
        "- 是否使用 LLM 生成:否",
        "- 是否使用审核机制:否",
        "- 是否支持证据引用:是(原始检索结果)",
        "",
        "## 4. 局限性",
        "- 不进行问题拆解,直接使用原问题检索",
        "- 不生成结构化回答,仅为检索片段拼接",
        "- 缺少 LLM 对证据的理解和整合",
        "- 无法处理需要推理的综合性问题",
    ])

    return "\n".join(lines)

# ═══════════════════════════════════════════════════════════════════
# 对比运行工具
# ═══════════════════════════════════════════════════════════════════

def run_comparison(
    question: str,
    baseline_a: BaselineA,
    baseline_b: BaselineB,
    rag_pipeline,  # RAGPipeline 实例
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """运行三种方案的对比实验.

    Returns:
        {
            "question": ...,
            "baseline_a": {"answer": ..., "markdown": ...},
            "baseline_b": {"answer": ..., "markdown": ..., "results": [...]},
            "rag_multi_agent": {
                "final_answer": ..., "plan": ..., "evidence": ...,
                "writer_output": ..., "reviewer_output": ...,
            },
        }
    """
    from .structured_output import (
        build_answer_markdown, format_evidence_display, format_process_log,
    )

    print(f"[对比实验] 问题: {question}")
    results: Dict[str, Any] = {"question": question}

    # Baseline A
    print("  [1/3] 运行 Baseline A ...")
    try:
        a_md, a_path = baseline_a.run_and_export(question, output_dir)
        results["baseline_a"] = {"markdown": a_md, "path": a_path}
    except Exception as e:
        results["baseline_a"] = {"error": str(e)}

    # Baseline B
    print("  [2/3] 运行 Baseline B ...")
    try:
        b_md, b_path = baseline_b.run_and_export(question, output_dir=output_dir)
        results["baseline_b"] = {"markdown": b_md, "path": b_path}
    except Exception as e:
        results["baseline_b"] = {"error": str(e)}

    # Ours (Multi-Agent RAG)
    print("  [3/3] 运行多智能体 RAG ...")
    try:
        rag_result = rag_pipeline.run(question)
        rag_md = build_answer_markdown(
            question=question,
            final_answer=rag_result["final_answer"],
            plan=rag_result["plan"],
            evidence=rag_result["evidence"],
            reviewer_output=rag_result["reviewer_output"],
            include_process_log=True,
        )
        rag_path = save_markdown(rag_md, "rag_multi_agent", output_dir)
        results["rag_multi_agent"] = {
            "final_answer": rag_result["final_answer"],
            "markdown": rag_md,
            "path": str(rag_path),
            "plan": rag_result["plan"],
            "evidence": rag_result["evidence"],
            "reviewer_output": rag_result["reviewer_output"],
        }
    except Exception as e:
        results["rag_multi_agent"] = {"error": str(e)}

    # 生成对比汇总
    comparison_md = build_comparison_summary(question, results)
    comp_path = save_markdown(comparison_md, "comparison", output_dir)
    results["comparison_markdown"] = comparison_md
    results["comparison_path"] = str(comp_path)

    return results

def build_comparison_summary(question: str, results: Dict[str, Any]) -> str:
    """生成三种方案的对比汇总 Markdown"""
    lines: List[str] = [
        "# RAG 方案对比汇总",
        f"问题: {question}",
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
        "## 方案概览",
        "",
        "| 方案 | 检索 | LLM生成 | 多智能体 | 审核 | 证据引用 |",
        "|------|------|---------|----------|------|----------|",
        "| Baseline A | ✗ | ✓ | ✗ | ✗ | ✗ |",
        "| Baseline B | ✓ (TF-IDF) | ✗ | ✗ | ✗ | ✓ |",
        "| Ours (Multi-Agent) | ✓ (TF-IDF) | ✓ | ✓ (Planner+Writer+Reviewer) | ✓ | ✓ |",
        "",
        "---",
    ]

    for name, key in [
        ("Baseline A", "baseline_a"),
        ("Baseline B", "baseline_b"),
        ("多智能体 RAG", "rag_multi_agent"),
    ]:
        r = results.get(key, {})
        lines.extend([
            f"## {name}",
        ])
        if "error" in r:
            lines.append(f"⚠️ 运行失败: {r['error']}")
        elif "markdown" in r:
            preview = r["markdown"][:300].replace("\n", "\n> ")
            lines.append(f"> {preview}...")
        lines.append("")

    return "\n".join(lines)
