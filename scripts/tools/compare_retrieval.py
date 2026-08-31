#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 对比三粒度 Chroma 库的检索召回效果（不调 LLM）

用法:
  python compare_retrieval.py "高电压正极材料"
  python compare_retrieval.py --k 15 "锂金属负极枝晶"
  python compare_retrieval.py --interactive   # 交互模式
"""

import argparse, hashlib, sys
from pathlib import Path
from typing import List
from collections import Counter

from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma

BASE = Path(__file__).resolve().parent / "miner" / "chroma"
COLLECTION = "battery_paragraphs_q"
MODEL = "qwen3-embedding:8b"
OLLAMA_URL = "http://localhost:11434"

CONFIGS = [
    {"name": "7500/750 (paragraphs_q)",     "path": str(BASE / "paragraphs_q")},
    {"name": "2000/200 (paragraphs_q_1)",   "path": str(BASE / "paragraphs_q_1")},
    {"name": "3000/300 (paragraphs_q_2)",   "path": str(BASE / "paragraphs_q_2")},
]


def load_store(path: str):
    embeddings = OllamaEmbeddings(model=MODEL, base_url=OLLAMA_URL)
    return Chroma(
        collection_name=COLLECTION,
        embedding_function=embeddings,
        persist_directory=path,
    )


def normalize_latex(text: str) -> str:
    import re
    text = re.sub(r"\$\$(.*?)\$\$", r" \1 ", text, flags=re.DOTALL)
    text = re.sub(r"\$(.*?)\$", r" \1 ", text)
    for cmd in ["mathrm", "text", "mathbf", "mathcal", "mathsf", "mathit"]:
        text = re.sub(r"\\" + cmd + r"\{([^}]+)\}", r"\1", text)
    text = re.sub(r"\\([a-zA-Z*]+)", r" ", text)
    text = re.sub(r"[_{}|]", "", text)
    text = re.sub(r" +", " ", text).strip()
    return text


def compare_retrieval(query: str, top_k: int = 10):
    print(f"\n{'='*80}")
    print(f"查询: {query}")
    print(f"Top-K: {top_k}")
    print(f"{'='*80}\n")

    for cfg in CONFIGS:
        store = load_store(cfg["path"])
        docs_with_scores = store.similarity_search_with_score(query, k=top_k)
        print(f"─── {cfg['name']} ───")
        print(f"  检索结果: {len(docs_with_scores)} 段")

        label_cnt: Counter = Counter()
        comp_cnt: Counter = Counter()
        paper_set = set()

        for rank, (doc, score) in enumerate(docs_with_scores, 1):
            meta = doc.metadata
            doi = meta.get("source_paper", "?")
            comp = meta.get("component", "?")
            label = meta.get("label", "?")
            snippet = normalize_latex(doc.page_content)[:120]
            conf_pct = round((1 - score / 2) * 100, 1)
            print(f"  #{rank:2d} [dist={score:.4f}, conf={conf_pct}%] {doi} | {comp}/{label}")
            print(f"       {snippet}")

            label_cnt[label] += 1
            comp_cnt[comp] += 1
            paper_set.add(doi)

        print(f"  ── 统计 ──")
        print(f"  来源文献: {len(paper_set)} 篇")
        print(f"  组件分布: {dict(comp_cnt)}")
        print(f"  标签分布: {dict(label_cnt)}")
        print()
        store._client.close()

    # 跨库 overlap 分析
    print(f"{'='*80}")
    print(f"跨库重叠分析")
    print(f"{'='*80}")

    for i, cfg_i in enumerate(CONFIGS):
        for j, cfg_j in enumerate(CONFIGS):
            if i >= j:
                continue
            store_i = load_store(cfg_i["path"])
            store_j = load_store(cfg_j["path"])
            docs_i = store_i.similarity_search(query, k=top_k)
            docs_j = store_j.similarity_search(query, k=top_k)
            hashes_i = set(hashlib.md5(d.page_content.encode()).hexdigest() for d in docs_i)
            hashes_j = set(hashlib.md5(d.page_content.encode()).hexdigest() for d in docs_j)
            overlap = len(hashes_i & hashes_j)
            total = len(hashes_i | hashes_j)
            pct = round(overlap / total * 100, 1) if total else 0
            print(f"  {cfg_i['name'][:22]:22s} ↔ {cfg_j['name'][:22]:22s}: {overlap}/{total} ({pct}%)")
            store_i._client.close()
            store_j._client.close()


def interactive_mode(top_k: int):
    print("交互模式 — 输入查询 (Ctrl+C 退出)\n")
    try:
        while True:
            q = input(">>> ").strip()
            if q:
                compare_retrieval(q, top_k=top_k)
    except (KeyboardInterrupt, EOFError):
        print("\n退出")


def main():
    parser = argparse.ArgumentParser(description="CLI 三粒度 Chroma 检索对比")
    parser.add_argument("query", nargs="?", default=None, help="搜索查询")
    parser.add_argument("--k", type=int, default=10, help="Top-K (默认 10)")
    parser.add_argument("--interactive", action="store_true", help="交互模式")
    args = parser.parse_args()

    if args.interactive or args.query is None:
        interactive_mode(top_k=args.k)
    elif args.query:
        compare_retrieval(args.query, top_k=args.k)


if __name__ == "__main__":
    main()
