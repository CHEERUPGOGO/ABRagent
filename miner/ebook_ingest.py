#!/usr/bin/env python3
"""电子书 markdown → Chroma 入库

使用 TokenTextSplitter 按 token 切分章节，增量写入独立 Chroma 集合。
与文献入库完全隔离（不同目录、不同 collection、不同元数据字段）。

用法:
    python miner/ebook_ingest.py
    python miner/ebook_ingest.py --ebook-root papers/ebook/merged \\
        --chroma-dir miner/chroma/ebooks --collection ebook_chunks
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import TokenTextSplitter

# ── 路径 ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_EBOOK_ROOT = _PROJECT_ROOT / "papers" / "ebook" / "merged"
_DEFAULT_CHROMA_DIR = _PROJECT_ROOT / "miner" / "chroma" / "ebooks"
_DEFAULT_COLLECTION = "ebook_chunks"
_DEFAULT_OUTPUT_JSON = _PROJECT_ROOT / "miner" / "json" / "Chrome" / "ebook_chunks.json"

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("ebook_ingest")


# ════════════════════════════════════════════════════════════
# 章节拆分
# ════════════════════════════════════════════════════════════

# 章节标题黑名单（目录/前言/版权，无可检索正文）
_SKIP_TITLES = {
    "contents", "table of contents", "preface", "preface to the third edition",
    "preface to the second edition", "preface to the first edition",
    "about the authors", "copyright", "dedication",
    "符号表", "目录", "前言", "序", "序言", "编者按",
}


def split_by_headings(text: str) -> List[Dict[str, str]]:
    """按 Markdown 标题拆分为章节，跳过目录/前言等。

    Returns:
        [{"title": "Chapter 1...", "content": "..."}, ...]
    """
    heading_pattern = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
    matches = list(heading_pattern.finditer(text))

    if not matches:
        return [{"title": "(全文)", "content": text.strip()}]

    sections = []
    for i, match in enumerate(matches):
        title = match.group(2).strip()
        if title.lower() in _SKIP_TITLES:
            continue
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            sections.append({"title": title, "content": content})

    preamble = text[:matches[0].start()].strip()
    if preamble:
        sections.insert(0, {"title": "(前言)", "content": preamble})

    return sections


# ════════════════════════════════════════════════════════════
# 记录与 ID
# ════════════════════════════════════════════════════════════

def make_ebook_id(source_file: str, chunk_seq: int) -> str:
    """生成唯一 ID（增量去重用）"""
    raw = f"ebk|{source_file}|{chunk_seq}"
    return "ebk-" + hashlib.sha1(raw.encode()).hexdigest()


def build_metadata(source_file: str, book_title: str,
                   chapter_title: str, chunk_seq: int,
                   total_chunks: int, chunk_text: str) -> dict:
    """电子书 chunk 的 Chroma metadata"""
    return {
        "_type": "ebook",
        "book_title": book_title,
        "chapter_title": chapter_title,
        "source_file": source_file,
        "chunk_seq": chunk_seq,
        "total_chunks": total_chunks,
        "preview": chunk_text[:120].replace("\n", " "),
    }


# ════════════════════════════════════════════════════════════
# 预处理（合并公式、保护表格）
# ════════════════════════════════════════════════════════════

def preprocess_chapter(text: str) -> str:
    """预合并：公式多行 → 单行，表格加边界标记。

    避免 TokenTextSplitter 把 $$...$$ 跨行公式切成碎片，
    或把表格行分散到不同 chunk。
    """
    # 1. $$...$$ 公式块 → 合并为单行（保留 $$ 边界）
    text = re.sub(
        r'\$\$(.*?)\$\$',
        lambda m: '$$' + ' '.join(m.group(1).split()) + '$$',
        text, flags=re.DOTALL,
    )
    # 2. \[...\] 公式块 → 合并为单行
    text = re.sub(
        r'\\\[(.*?)\\\]',
        lambda m: '\\[' + ' '.join(m.group(1).split()) + '\\]',
        text, flags=re.DOTALL,
    )
    # 3. 去掉 Markdown 图片标记（`![]()` 和 `<img>`）— 检索无意义
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)       # ![alt](url) → alt
    text = re.sub(r'<img[^>]+>', '', text, flags=re.IGNORECASE)  # <img ...>
    # 4. Markdown 表格前插入空行作为边界（防止表头表体被切开）
    text = re.sub(r'(\|[^\n]+\|)\s*\n\s*(\|[-\s|]+\|)', r'\n\n\1\n\2', text)
    return text


# ════════════════════════════════════════════════════════════
# 处理单本书
# ════════════════════════════════════════════════════════════

def process_book(file_path: Path,
                 splitter: TokenTextSplitter,
                 chunk_size: int,
                 overlap: int) -> List[dict]:
    """将一本电子书的 markdown 拆分为多段 Chroma 记录。"""
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    book_title = file_path.stem.replace("_", " ").replace("-", " ")
    source_file = str(file_path.relative_to(_PROJECT_ROOT)).replace("\\", "/")

    sections = split_by_headings(content)
    records: List[dict] = []
    total_chunks = 0

    for sec in sections:
        chapter_title = sec["title"]
        chapter_text = preprocess_chapter(sec["content"])

        chunks = splitter.split_text(chapter_text)

        for chunk_text in chunks:
            total_chunks += 1
            records.append({
                "text": chunk_text,
                "metadata": build_metadata(
                    source_file=source_file,
                    book_title=book_title,
                    chapter_title=chapter_title,
                    chunk_seq=total_chunks,
                    total_chunks=0,
                    chunk_text=chunk_text,
                ),
                "_id": make_ebook_id(source_file, total_chunks),
            })

    for rec in records:
        rec["metadata"]["total_chunks"] = total_chunks

    logger.info(f"  {file_path.name}: {len(sections)} 章节 → {len(records)} chunk")
    return records


# ════════════════════════════════════════════════════════════
# 写入 Chroma（增量）
# ════════════════════════════════════════════════════════════

def ingest_chroma(records: List[dict],
                  chroma_dir: Path,
                  collection: str,
                  base_url: str,
                  model: str) -> None:
    """增量写入 Chroma。"""
    embeddings = OllamaEmbeddings(model=model, base_url=base_url)
    chroma_dir.mkdir(parents=True, exist_ok=True)

    vector_store = Chroma(
        collection_name=collection,
        embedding_function=embeddings,
        persist_directory=str(chroma_dir),
    )

    ids = [r["_id"] for r in records]
    documents = [
        Document(page_content=r["text"], metadata=r["metadata"])
        for r in records
    ]

    check_batch = 10000
    existing_ids: set = set()
    for start in range(0, len(ids), check_batch):
        batch = ids[start:start + check_batch]
        try:
            result = vector_store.get(ids=batch)
            existing_ids.update(result.get("ids", []))
        except Exception:
            pass

    to_add = [
        (documents[i], ids[i])
        for i in range(len(ids))
        if ids[i] not in existing_ids
    ]

    if to_add:
        new_docs, new_ids = zip(*to_add)
        vector_store.add_documents(list(new_docs), ids=list(new_ids))
        logger.info(f"[chroma] 新增 {len(new_docs)} 条, 跳过 {len(existing_ids)} 条已有")
    else:
        logger.info(f"[chroma] 全部 {len(ids)} 条已有, 无变化")


# ════════════════════════════════════════════════════════════
# JSON 备份
# ════════════════════════════════════════════════════════════

def write_json_append(records: List[dict], output_path: Path) -> None:
    """增量写入 JSON（读取已有 → 合并去重 → 写回）。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    if output_path.exists():
        try:
            for r in json.loads(output_path.read_text(encoding="utf-8")):
                existing[r["_id"]] = r
        except Exception:
            pass

    for r in records:
        existing[r["_id"]] = r

    tmp = output_path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(list(existing.values()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(output_path)
    logger.info(f"[json] 增量: 新增 {len(records)}, 总计 {len(existing)} 条 → {output_path}")


# ════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="电子书 markdown → Chroma 入库")
    parser.add_argument("--ebook-root", default=str(_DEFAULT_EBOOK_ROOT),
                        help="电子书 markdown 目录")
    parser.add_argument("--chroma-dir", default=str(_DEFAULT_CHROMA_DIR),
                        help="Chroma 持久化路径")
    parser.add_argument("--collection", default=_DEFAULT_COLLECTION,
                        help="Chroma collection 名")
    parser.add_argument("--chunk-size", type=int, default=1024,
                        help="TokenTextSplitter chunk_size (默认 1024)")
    parser.add_argument("--overlap", type=int, default=256,
                        help="chunk_overlap (默认 256)")
    parser.add_argument("--output-json", default=str(_DEFAULT_OUTPUT_JSON),
                        help="JSON 备份路径")
    parser.add_argument("--no-chroma", action="store_true",
                        help="仅生成 JSON, 不入 Chroma")
    parser.add_argument("--base-url", default="http://localhost:11434",
                        help="Ollama base URL")
    parser.add_argument("--model", default="qwen3-embedding:8b",
                        help="Ollama embedding 模型名")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    ebook_root = Path(args.ebook_root)
    if not ebook_root.is_dir():
        logger.error(f"电子书目录不存在: {ebook_root}")
        sys.exit(1)

    md_files = sorted(ebook_root.glob("*.md"))
    if not md_files:
        logger.error(f"{ebook_root} 中没有 .md 文件")
        sys.exit(1)

    logger.info(f"电子书根目录: {ebook_root}")
    logger.info(f"找到 {len(md_files)} 本书")
    logger.info(f"Chroma → {args.chroma_dir} / {args.collection}")
    logger.info(f"切分参数: chunk_size={args.chunk_size}, overlap={args.overlap}")
    logger.info("")

    splitter = TokenTextSplitter(
        chunk_size=args.chunk_size,
        chunk_overlap=args.overlap,
        encoding_name="cl100k_base",
    )

    all_records: List[dict] = []
    for md_path in md_files:
        logger.info(f"[{md_files.index(md_path)+1}/{len(md_files)}] {md_path.name}")
        records = process_book(md_path, splitter, args.chunk_size, args.overlap)
        all_records.extend(records)

    logger.info(f"\n共处理 {len(all_records)} 条 chunk, 来自 {len(md_files)} 本书")

    write_json_append(all_records, Path(args.output_json))

    if not args.no_chroma:
        ingest_chroma(
            records=all_records,
            chroma_dir=Path(args.chroma_dir),
            collection=args.collection,
            base_url=args.base_url,
            model=args.model,
        )

    logger.info("完成.")


if __name__ == "__main__":
    main()
