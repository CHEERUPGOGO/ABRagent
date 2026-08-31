#!/usr/bin/env python3
"""从已有 JSON 重新切分 + 重新入库 Chroma

用法:
  python reindex_chroma.py                                        # 使用默认值
  python reindex_chroma.py --max-chunk 2500 --overlap 250         # 调参
  python reindex_chroma.py --input other.json --chroma-dir new    # 指定路径

不调 LLM，只调 embedding 模型。修改 chunk 参数后无需重跑分类。
"""

import argparse, hashlib, json, sys
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── 超长段双存（同 _split_long_records 逻辑） ──

def split_records(records: List[dict], max_chunk: int, overlap: int) -> List[dict]:
    """标签后入库前：过滤非正文 + 超长段双存"""
    splitted = []
    for rec in records:
        text = rec.get("paragraph_context", "")
        label = rec.get("label", "")
        if label == "非正文" or not text:
            continue
        if len(text) <= max_chunk or text.startswith("<table"):
            splitted.append(rec)
            continue
        base_id = rec.get("_id", "unknown")
        # 整段入口
        full_rec = dict(rec)
        full_rec["_id"] = f"{base_id}|full"
        if "_chroma_metadata" in full_rec:
            full_rec["_chroma_metadata"]["is_full_paragraph"] = True
        splitted.append(full_rec)
        # 子段
        n = 1
        pos = 0
        while pos < len(text):
            end = min(pos + max_chunk, len(text))
            seg = text[pos:end]
            if end < len(text):
                last_dot = seg.rfind(". ")
                if last_dot > max_chunk // 3:
                    seg = seg[:last_dot + 1]
            if seg.strip():
                sub = dict(rec)
                sub["paragraph_context"] = seg.strip()
                sub["_id"] = f"{base_id}|sp{n}"
                if "_chroma_metadata" in sub:
                    sub["_chroma_metadata"]["parent_full_id"] = f"{base_id}|full"
                splitted.append(sub)
                n += 1
            if end >= len(text):
                break
            pos = end - overlap
    return splitted


# ── Chroma 入库（增量去重） ──

def dedupe_record_ids(records: List[dict]) -> List[dict]:
    seen: dict[str, int] = {}
    deduped = []
    for rec in records:
        rid = rec.get("_id", "")
        count = seen.get(rid, 0)
        seen[rid] = count + 1
        if count:
            rec = dict(rec)
            rec["_id"] = f"{rid}|dup{count}"
        deduped.append(rec)
    return deduped


def ensure_unique_ids(ids: List[str]) -> None:
    seen = set()
    dups = []
    for i in ids:
        if i in seen:
            dups.append(i)
        else:
            seen.add(i)
    if dups:
        raise RuntimeError(f"Duplicate IDs: {len(set(dups))}, e.g. {dups[:3]}")


def ingest_chroma(records: List[dict], chroma_dir: Path, collection: str,
                  base_url: str, model: str) -> None:
    from langchain_core.documents import Document
    from langchain_chroma import Chroma
    from langchain_ollama import OllamaEmbeddings

    embeddings = OllamaEmbeddings(model=model, base_url=base_url, num_ctx=4096, mirostat=0)
    chroma_dir.mkdir(parents=True, exist_ok=True)
    # 小批次写入，防止 Ollama 超时断开
    vector_store = Chroma(
        collection_name=collection,
        embedding_function=embeddings,
        persist_directory=str(chroma_dir),
    )

    existing_ids = set(vector_store.get()["ids"])
    records = dedupe_record_ids(records)
    documents = [
        Document(
            page_content=rec["paragraph_context"],
            metadata=rec.get("_chroma_metadata", {}),
        )
        for rec in records
    ]
    ids = [rec["_id"] for rec in records]
    new = [(d, i) for d, i in zip(documents, ids) if i not in existing_ids]
    if new:
        nd, ni = map(list, zip(*new))
        ensure_unique_ids([str(i) for i in ni])
        # 分批写入，防止 Ollama 超时断开
        BATCH = 200
        added = 0
        for batch_start in range(0, len(nd), BATCH):
            batch_docs = nd[batch_start:batch_start + BATCH]
            batch_ids = ni[batch_start:batch_start + BATCH]
            vector_store.add_documents(documents=batch_docs, ids=batch_ids)
            added += len(batch_docs)
            print(f"[chroma] batch {added}/{len(nd)}")
        print(f"[chroma] added {len(nd)} new (skipped {len(ids)-len(nd)} existing)")
    else:
        print("[chroma] all already in Chroma, nothing to add")


# ── 主流程 ──

def main():
    parser = argparse.ArgumentParser(description="从 JSON 重新切分 + 重新入库 Chroma（不调 LLM）")
    parser.add_argument("--input", default="miner/json/Chrome/paragraph_metadata_q.json",
                        help="输入 JSON 路径")
    parser.add_argument("--chroma-dir", default="miner/chroma/paragraphs_q",
                        help="Chroma 目录")
    parser.add_argument("--collection", default="battery_paragraphs_q",
                        help="Chroma collection 名称")
    parser.add_argument("--max-chunk", type=int, default=2000,
                        help="入库前 max_chunk（字符数，默认 2000）")
    parser.add_argument("--overlap", type=int, default=200,
                        help="子段重叠字符数（默认 200）")
    parser.add_argument("--embedding-model", default="qwen3-embedding:8b",
                        help="Ollama embedding 模型")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--output-json", default=None,
                        help="输出新 JSON（不指定则覆盖输入）")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = PROJECT_ROOT / input_path
    if not input_path.exists():
        print(f"输入不存在: {input_path}")
        sys.exit(1)

    chroma_dir = Path(args.chroma_dir)
    if not chroma_dir.is_absolute():
        chroma_dir = PROJECT_ROOT / chroma_dir

    output_path = Path(args.output_json) if args.output_json else input_path
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path

    # 1. 读取 JSON
    records = json.loads(input_path.read_text(encoding="utf-8"))
    print(f"[input] {len(records)} records from {input_path}")

    # 2. 重新切分
    new_records = split_records(records, args.max_chunk, args.overlap)
    print(f"[split] {len(new_records)} records (max_chunk={args.max_chunk}, overlap={args.overlap})")

    # 3. 写入新 JSON
    path_bak = input_path.with_suffix(".json.bak")
    if output_path == input_path:
        import shutil
        shutil.copy2(input_path, path_bak)
        print(f"[bak] 原文件备份至 {path_bak}")

    output_path.write_text(json.dumps(new_records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[json] 写入 {output_path}")

    # 4. 入库 Chroma（增量去重）
    ingest_chroma(new_records, chroma_dir, args.collection,
                  args.ollama_base_url, args.embedding_model)
    print(f"[done]")


if __name__ == "__main__":
    main()
