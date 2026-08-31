#!/usr/bin/env python3
"""Classify cleaned markdown paragraphs and ingest them into Chroma.

Each markdown paragraph becomes one JSON record and one Chroma document. The
component label is inferred from classified folders named anode, cathode, or
electrolyte. The synthesis/property label is produced by DeepSeek with a small
keyword fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional


COMPONENTS = {"anode", "cathode", "electrolyte"}
LABELS = {"合成", "性质", "other"}
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from miner.config import create_llm

PROMPT_TEMPLATE = """你是锂电池文献段落分类助手。请判断下面段落属于哪一类。

只允许选择一个 label：
- 合成：材料制备、合成路线、处理工艺、烧结、退火、涂覆、组装制备等。
- 性质：材料结构表征、电化学性能、容量、循环、倍率、阻抗、离子电导率、稳定性等。
- other：背景、摘要泛述、引言、图表说明、参考文献、致谢、数据声明或无法判断。

严格只输出 JSON，不要解释，不要包含 reason：
{{"label": "合成"}}

段落：
{paragraph}
"""

SYNTHESIS_KEYWORDS = [
    "synthesized",
    "prepared",
    "fabricated",
    "calcined",
    "annealed",
    "coated",
    "mixed",
    "dissolved",
    "stirred",
    "heated",
    "dried",
    "assembled",
    "制备",
    "合成",
    "退火",
    "煅烧",
    "涂覆",
]

PROPERTY_KEYWORDS = [
    "capacity",
    "retention",
    "cycle",
    "cycling",
    "rate",
    "conductivity",
    "impedance",
    "voltage",
    "coulombic",
    "xrd",
    "sem",
    "tem",
    "xps",
    "性能",
    "容量",
    "循环",
    "倍率",
    "电导率",
    "阻抗",
    "表征",
]


@dataclass(frozen=True)
class ParagraphItem:
    source_file: Path
    doi: str
    component: str
    paragraph_index: int
    paragraph: str


def require_packages() -> None:
    missing = [
        name
        for name in ("langchain_chroma", "langchain_ollama", "chromadb")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        packages = ", ".join(missing)
        raise RuntimeError(
            f"Missing required package(s): {packages}. "
            "Install them with: pip install langchain-chroma chromadb langchain-ollama"
        )


def resolve_project_path(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def infer_component(path: Path) -> Optional[str]:
    for part in reversed(path.parts):
        part_lower = part.lower()
        if part_lower in COMPONENTS:
            return part_lower
    return None


def doi_from_filename(path: Path) -> str:
    return path.stem.replace("_", "/")


def split_paragraphs(text: str, min_length: int) -> List[str]:
    chunks = re.split(r"\n\s*\n+", text)
    paragraphs = []
    for chunk in chunks:
        paragraph = re.sub(r"[ \t]+", " ", chunk.strip())
        paragraph = re.sub(r"\n+", " ", paragraph)
        if len(paragraph) >= min_length:
            paragraphs.append(paragraph)
    return paragraphs


def iter_markdown_files(input_root: Path) -> Iterable[Path]:
    for path in sorted(input_root.rglob("*.md")):
        if infer_component(path):
            yield path


def collect_paragraphs(input_root: Path, min_length: int, limit: Optional[int]) -> List[ParagraphItem]:
    items: List[ParagraphItem] = []
    for md_path in iter_markdown_files(input_root):
        component = infer_component(md_path)
        if not component:
            continue
        text = md_path.read_text(encoding="utf-8")
        doi = doi_from_filename(md_path)
        for idx, paragraph in enumerate(split_paragraphs(text, min_length=min_length)):
            items.append(
                ParagraphItem(
                    source_file=md_path,
                    doi=doi,
                    component=component,
                    paragraph_index=idx,
                    paragraph=paragraph,
                )
            )
            if limit is not None and len(items) >= limit:
                return items
    return items


def parse_label(raw_output: str) -> Optional[str]:
    cleaned = raw_output.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    json_match = re.search(r"\{.*?\}", cleaned, re.DOTALL)
    if not json_match:
        return None
    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        return None
    label = str(data.get("label", "")).strip()
    return label if label in LABELS else None


def keyword_label(paragraph: str) -> str:
    text = paragraph.lower()
    synthesis_score = sum(1 for kw in SYNTHESIS_KEYWORDS if kw.lower() in text)
    property_score = sum(1 for kw in PROPERTY_KEYWORDS if kw.lower() in text)
    if synthesis_score > property_score and synthesis_score > 0:
        return "合成"
    if property_score > 0:
        return "性质"
    return "other"


def build_label_chain() -> Any:
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import PromptTemplate

    llm = create_llm("classification")
    prompt = PromptTemplate(template=PROMPT_TEMPLATE, input_variables=["paragraph"])
    return prompt | llm | StrOutputParser()


def classify_paragraphs(items: List[ParagraphItem]) -> List[dict]:
    chain = build_label_chain()
    records = []
    for i, item in enumerate(items, 1):
        try:
            raw = chain.invoke({"paragraph": item.paragraph})
            label = parse_label(raw) or keyword_label(item.paragraph)
        except Exception as exc:
            print(
                f"[warn] DeepSeek failed for {item.source_file.name} paragraph {item.paragraph_index}: {exc}. "
                "Using keyword fallback.",
                file=sys.stderr,
            )
            label = keyword_label(item.paragraph)

        records.append(
            {
                "source_paper": item.doi,
                "metadata": [item.component, label],
                "paragraph_context": item.paragraph,
                "_chroma_metadata": {
                    "source_paper": item.doi,
                    "component": item.component,
                    "label": label,
                    "source_file": str(item.source_file),
                    "paragraph_index": item.paragraph_index,
                },
                "_id": make_document_id(item, label),
            }
        )
        print(f"[{i}/{len(items)}] {item.doi} #{item.paragraph_index} -> {item.component}/{label}")
    return records


def make_document_id(item: ParagraphItem, label: str) -> str:
    try:
        source_key = str(item.source_file.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        source_key = str(item.source_file.resolve()).replace("\\", "/")
    raw_id = "|".join(
        [
            item.doi,
            item.component,
            source_key,
            str(item.paragraph_index),
            label,
            item.paragraph,
        ]
    )
    return "para-" + hashlib.sha1(raw_id.encode("utf-8")).hexdigest()


def write_json(records: List[dict], output_json: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    public_records = [
        {
            "source_paper": record["source_paper"],
            "metadata": record["metadata"],
            "paragraph_context": record["paragraph_context"],
        }
        for record in records
    ]
    output_json.write_text(
        json.dumps(public_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def validate_ollama_embedding(embeddings: Any, model: str) -> None:
    try:
        vector = embeddings.embed_query("test")
    except Exception as exc:
        raise RuntimeError(
            f"Ollama embedding model '{model}' is not available. "
            f"Make sure Ollama is running and run: ollama pull {model}"
        ) from exc
    if not vector:
        raise RuntimeError(f"Ollama embedding model '{model}' returned an empty vector.")


def ingest_chroma(records: List[dict], chroma_dir: Path, collection: str, base_url: str, model: str) -> None:
    from langchain_core.documents import Document
    from langchain_chroma import Chroma
    from langchain_ollama import OllamaEmbeddings

    embeddings = OllamaEmbeddings(model=model, base_url=base_url)

    chroma_dir.mkdir(parents=True, exist_ok=True)
    vector_store = Chroma(
        collection_name=collection,
        embedding_function=embeddings,
        persist_directory=str(chroma_dir),
    )
    records = dedupe_record_ids(records)
    documents = [
        Document(
            page_content=record["paragraph_context"],
            metadata=record["_chroma_metadata"],
        )
        for record in records
    ]
    ids = [record["_id"] for record in records]
    if documents:
        ensure_unique_ids(ids)
        vector_store.add_documents(documents=documents, ids=ids)

    results = vector_store.similarity_search("battery synthesis performance", k=1)
    if results:
        meta = results[0].metadata
        print(
            "[chroma] sample result: "
            f"{meta.get('source_paper')} {meta.get('component')}/{meta.get('label')}"
        )


def dedupe_record_ids(records: List[dict]) -> List[dict]:
    seen: dict[str, int] = {}
    deduped = []
    for record in records:
        record_id = record["_id"]
        count = seen.get(record_id, 0)
        seen[record_id] = count + 1
        if count:
            record = dict(record)
            record["_id"] = f"{record_id}|dup{count}"
        deduped.append(record)
    return deduped


def ensure_unique_ids(ids: List[str]) -> None:
    seen = set()
    duplicates = []
    for doc_id in ids:
        if doc_id in seen:
            duplicates.append(doc_id)
        else:
            seen.add(doc_id)
    if duplicates:
        sample = ", ".join(duplicates[:5])
        raise RuntimeError(
            f"Duplicate Chroma document IDs generated before ingest: {len(set(duplicates))}. "
            f"Examples: {sample}"
        )


def validate_runtime(base_url: str, model: str) -> None:
    require_packages()
    from langchain_ollama import OllamaEmbeddings

    embeddings = OllamaEmbeddings(model=model, base_url=base_url)
    validate_ollama_embedding(embeddings, model)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify markdown paragraphs and ingest them into local Ollama-backed Chroma."
    )
    parser.add_argument("--input-root", default="database/type")
    parser.add_argument("--output-json", default="miner/json/paragraph_metadata.json")
    parser.add_argument("--chroma-dir", default="miner/chroma/paragraphs")
    parser.add_argument("--collection", default="battery_paragraphs")
    parser.add_argument("--min-length", type=int, default=50)
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--embedding-model", default="bge-m3")
    parser.add_argument("--limit", type=int, default=None, help="Limit paragraphs for smoke tests.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_root = resolve_project_path(args.input_root)
    output_json = resolve_project_path(args.output_json)
    chroma_dir = resolve_project_path(args.chroma_dir)

    if not input_root.is_dir():
        raise FileNotFoundError(f"Input root does not exist: {input_root}")

    validate_runtime(base_url=args.ollama_base_url, model=args.embedding_model)
    items = collect_paragraphs(input_root, min_length=args.min_length, limit=args.limit)
    if not items:
        raise RuntimeError(f"No markdown paragraphs found under: {input_root}")

    print(f"[scan] collected {len(items)} paragraphs from {input_root}")
    records = classify_paragraphs(items)
    write_json(records, output_json)
    print(f"[json] wrote {len(records)} records to {output_json}")
    ingest_chroma(
        records,
        chroma_dir=chroma_dir,
        collection=args.collection,
        base_url=args.ollama_base_url,
        model=args.embedding_model,
    )
    print(f"[chroma] ingested {len(records)} paragraphs into {chroma_dir}")


if __name__ == "__main__":
    main()
