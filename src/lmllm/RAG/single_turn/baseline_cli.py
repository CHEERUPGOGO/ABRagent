"""Baseline A/B CLI 工具 — 高比能锂电池材料筛选"""

from typing import Dict, List

from ..baselines import BaselineA, BaselineB, build_baseline_a_markdown, build_baseline_b_markdown
from ..llm_client import LLMClient
from ..bm25_kb import BM25KnowledgeBase
from ..config import ensure_output_dir

def run_baseline_a(question: str, output_dir: Path | None = None) -> str:
    """运行 Baseline A(单模型直接回答),返回 Markdown."""
    llm = LLMClient(backend="auto")
    baseline = BaselineA(llm)
    answer = baseline.ask(question)
    md = build_baseline_a_markdown(question, answer)

    if output_dir:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = question[:30].replace("/", "_").replace(" ", "_")
        path = output_dir / f"baseline_A_{safe}_{ts}.md"
        path.write_text(md, encoding="utf-8")
        print(f"[Saved] {path}")

    return md

def run_baseline_b(question: str, output_dir: Path | None = None) -> str:
    """运行 Baseline B(TF-IDF 检索 + 拼接摘要),返回 Markdown."""
    kb = BM25KnowledgeBase()
    baseline = BaselineB(kb)
    md, results = baseline.retrieve(question, top_k=10)

    if output_dir:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = question[:30].replace("/", "_").replace(" ", "_")
        path = output_dir / f"baseline_B_{safe}_{ts}.md"
        path.write_text(md, encoding="utf-8")
        print(f"[Saved] {path}")

    return md

def main():
    parser = argparse.ArgumentParser(
        description="Baseline A/B CLI — 高比能锂电池材料筛选对比工具"
    )
    parser.add_argument(
        "question", nargs="?", default="NCM811的首次放电容量是多少？",
        help="要回答的问题"
    )
    parser.add_argument(
        "--baseline", choices=["A", "B"], default="A",
        help="Baseline 方案:A=单模型直接回答, B=TF-IDF检索拼接 (default: A)"
    )
    parser.add_argument(
        "--save", action="store_true",
        help="保存结果到 RAG/output/ 目录"
    )
    args = parser.parse_args()

    output_dir = ensure_output_dir() if args.save else None

    if args.baseline == "A":
        md = run_baseline_a(args.question, output_dir)
    else:
        md = run_baseline_b(args.question, output_dir)

    print(md)

if __name__ == "__main__":
    main()
