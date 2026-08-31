"""Baseline 批量运行器 — 自动导出结构化 Markdown 到 RAG/output/ 目录.

启动:
    cd /home/ls/xiaoyue/LLM2/LMLLM
    # 全量运行
    python -m src.lmllm.RAG.multi_turn.baseline_runner
    # 运行指定问题
    python -m src.lmllm.RAG.multi_turn.baseline_runner --baseline A --index 1,2
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..baselines import BaselineA, BaselineB, build_baseline_a_markdown, build_baseline_b_markdown, run_comparison
from ..llm_client import LLMClient
from ..bm25_kb import BM25KnowledgeBase
from ..config import ensure_output_dir

# ════════════════════════════════════════════════════════════
# 预设问题集 — 高比能锂电池材料筛选场景
# ════════════════════════════════════════════════════════════

QUESTIONS = [
    "NCM811和LRMO哪个能量密度更高？对比它们的首次放电容量和工作电压.",
    "下一代高比能锂电池用什么负极材料？对比硅基负极和锂金属负极的优劣势.",
    "固态电解质的离子电导率和电化学窗口对比,哪种更适用于高比能锂电池？",
    "高电压正极材料(NCM811/LRMO/LNMO)在4.5V以上的容量衰减原因是什么？",
    "从文献证据出发,推荐一套高比能锂电池正极/负极/电解液的材料组合方案.",
]

# None 表示全部运行;填 1/2/3/4/5 表示只跑指定问题
RUN_ONLY_INDEX: Optional[int] = None

def sanitize(text: str) -> str:
    return text[:40].replace("/", "_").replace(" ", "_").replace("?", "").replace("？", "")

def run_baseline_a_batch(
    questions: Optional[List[str]] = None,
    output_dir: Optional[Path] = None,
) -> List[str]:
    """批量运行 Baseline A"""
    questions = questions or QUESTIONS
    llm = LLMClient(backend="auto")
    baseline = BaselineA(llm)
    out_dir = output_dir or ensure_output_dir()

    paths = []
    for i, q in enumerate(questions, 1):
        print(f"\n[Baseline A] 问题 {i}/{len(questions)}: {q[:60]}...")
        answer = baseline.ask(q)
        md = build_baseline_a_markdown(q, answer)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"baseline_A_Q{i}_{sanitize(q)}_{ts}.md"
        path.write_text(md, encoding="utf-8")
        print(f"  -> [Saved] {path}")
        paths.append(str(path))
    return paths

def run_baseline_b_batch(
    questions: Optional[List[str]] = None,
    output_dir: Optional[Path] = None,
) -> List[str]:
    """批量运行 Baseline B"""
    questions = questions or QUESTIONS
    kb = BM25KnowledgeBase()
    baseline = BaselineB(kb)
    out_dir = output_dir or ensure_output_dir()

    paths = []
    for i, q in enumerate(questions, 1):
        print(f"\n[Baseline B] 问题 {i}/{len(questions)}: {q[:60]}...")
        md, results = baseline.retrieve(q, top_k=10)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"baseline_B_Q{i}_{sanitize(q)}_{ts}.md"
        path.write_text(md, encoding="utf-8")
        print(f"  -> [Saved] {path}")
        paths.append(str(path))
    return paths

def run_batch_comparison(
    questions: Optional[List[str]] = None,
    output_dir: Optional[Path] = None,
) -> str:
    """批量运行三种方案对比,生成汇总报告."""
    questions = questions or QUESTIONS
    out_dir = output_dir or ensure_output_dir()

    all_results = []
    for i, q in enumerate(questions, 1):
        print(f"\n[对比] 问题 {i}/{len(questions)}: {q[:60]}...")
        pipeline = None
        try:
            from ..rag_pipeline import RAGPipeline
            pipeline = RAGPipeline(
                llm_backend="auto", retrieval_mode="chroma",
            )
            baseline_a = BaselineA(pipeline.llm)
            baseline_b = BaselineB(pipeline.kb)

            # 这里简化处理,直接跑三种方案
            a_answer = baseline_a.ask(q)
            b_md, _ = baseline_b.retrieve(q, top_k=10)
            rag_result = pipeline.run(q)

            all_results.append({
                "question": q,
                "baseline_a": a_answer[:500],
                "baseline_b_summary": b_md[:500],
                "rag_answer": rag_result["final_answer"][:500],
            })
        except Exception as e:
            print(f"  -> 错误: {e}")
            continue

    # 生成汇总
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_lines = [
        "# 高比能锂电池材料筛选 — Baseline A/B/Ours 对比汇总",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
    ]
    for r in all_results:
        summary_lines.extend([
            f"## 问题: {r['question']}",
            "",
            "### Baseline A(单模型直接回答)",
            r["baseline_a"][:300] + "...",
            "",
            "### Baseline B(TF-IDF 检索拼接)",
            r["baseline_b_summary"][:300] + "...",
            "",
            "### Ours(多智能体 RAG)",
            r["rag_answer"][:300] + "...",
            "",
            "---",
            "",
        ])

    summary_path = out_dir / f"ABOurs_comparison_{ts}.md"
    summary_md = "\n".join(summary_lines)
    summary_path.write_text(summary_md, encoding="utf-8")
    print(f"\n[汇总] {summary_path}")

    return summary_md

def main():
    parser = argparse.ArgumentParser(
        description="批量运行 Baseline A/B — 高比能锂电池材料筛选"
    )
    parser.add_argument(
        "--baseline", choices=["A", "B", "comparison"], default="comparison",
        help="方案: A/B/comparison (default: comparison 即三种方案)"
    )
    parser.add_argument(
        "--index", type=str, default=None,
        help="运行指定问题(逗号分隔,如 1,2,3),默认全部"
    )
    args = parser.parse_args()

    # 选择问题
    questions = QUESTIONS
    if args.index:
        indices = [int(x.strip()) - 1 for x in args.index.split(",")]
        questions = [QUESTIONS[i] for i in indices if 0 <= i < len(QUESTIONS)]

    out_dir = ensure_output_dir()

    if args.baseline == "A":
        paths = run_baseline_a_batch(questions, out_dir)
        print(f"\n完成. {len(paths)} 个文件已保存到 {out_dir}")
    elif args.baseline == "B":
        paths = run_baseline_b_batch(questions, out_dir)
        print(f"\n完成. {len(paths)} 个文件已保存到 {out_dir}")
    else:
        summary = run_batch_comparison(questions, out_dir)
        print(f"\n完成. 汇总已保存.")

if __name__ == "__main__":
    main()
