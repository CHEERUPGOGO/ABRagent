#!/usr/bin/env python3
"""v3 — 6 维段落标签标注 + Chroma 入库

每个段落由 LLM 按 6 个维度标注：
  Electrolyte_Type / Material / Processes / Performance / Knowledge_Type / Application

元数据（标题、作者、日期）从 miner/json/meta_merged.json 加载注入 prompt。
组件（anode/cathode/electrolyte）从目录结构推断。
标签由 DeepSeek（flash）生成，关键词兜底。
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from miner.cleaning.cleaner_v2_standalone import clean_markdown

COMPONENTS = {"anode", "cathode", "electrolyte"}

# ── 标签体系：6 类中文标签（v2 池 + v3 扩展定义） ──
PRIMARY_LABELS = {"电化学性能", "理化性质", "结构表征", "材料制备", "机理/模拟", "概述"}

from miner.config import create_llm

PROMPT_TEMPLATE = """# 角色
你是一位电池领域的文献标注专家，专精于液态锂离子电池（LIB）和液态锂金属电池（LMB）的研究。

# 任务
阅读给定的文献元信息（标题、作者、日期、DOI）以及该文献中的一个**段落片段**，从以下 6 个类别中选择**最匹配的 1 个标签**，并输出 JSON 格式。

# 输入格式
标题: {title}
作者: {authors}   (可选，仅作背景参考)
发表日期: {publication_date} (可选，仅作背景参考)
DOI: {doi}       (可选，仅作背景参考)
段落: {chunk_text}   (必填，实际需要标注的文本片段)

# 输出格式
{{"label": "类别名"}}

严格只输出 JSON，不加解释。标签必须是以下 6 类之一：

# 标签定义与边界（请仔细阅读，确保互斥）

## 1. 电化学性能
覆盖：电池整体电化学性能数据与测试分析。
- 能量密度（Wh/kg, Wh/L）、比容量（mAh/g）
- 循环寿命、容量保持率、衰减
- 库仑效率（CE, %）
- 倍率性能（C-rate）、面容量（mAh/cm²）
- 电压/极化/过电位
- 电化学测试（CV、EIS、GITT）的结果分析
- 安全性能

边界：强调"电池组装后的表现"。如果段落描述的是材料自身的理化参数（如离子电导率、模量、热分解温度），应归为"理化性质"。

## 2. 理化性质
覆盖：材料/电解液的本征物理化学参数。
- 离子/电子电导率（mS/cm, S/cm）
- 锂离子扩散系数（cm²/s）
- 电化学窗口（V vs. Li/Li+）
- 迁移数
- 力学性能（模量、硬度、强度、弹性）
- 热学性能（热分解温度、TGA、DSC）
- 孔隙率/比表面积（BET、BJH）
- 带隙/能带
- 黏度、密度、润湿性等其它物性

边界：如果段落描述的是电池整体表现（如循环后容量、倍率性能、能量密度），应归为"电化学性能"。

## 3. 结构表征
覆盖：对材料进行的物相、形貌、表面化学、成分分析。
- 物相/晶体结构：XRD、晶格参数、空间群、Rietveld
- 微观形貌：SEM、TEM、粒径、形貌
- 表面/界面化学：XPS、FTIR、Raman、SEI/CEI成分分析
- 成分分析：EDS、ICP、Mapping

边界：强调"用什么方法看/测了结构"。如果段落的重点是性能数据（如离子电导率、容量），应归入对应的"理化性质"或"电化学性能"。

## 4. 材料制备
覆盖：材料的合成、制备、改性工艺以及电解液配制。
- 前驱体/原料配制、共沉淀、水热
- 烧结、退火、煅烧、球磨、SPS、热压
- 包覆、掺杂、ALD、CVD
- 电极制备：浆料、涂布、辊压、干法
- 电池组装：扣电、软包、电池集成
- 电解液配方设计与配制

边界：如果段落同时描述了制备方法和所得性能数据，判断**聚焦点**。重点在"怎么做"→归入本类；重点在"结果有多好"→归入对应性能类。

## 5. 机理/模拟
覆盖：理论计算、模拟仿真、反应机理分析。
- DFT、第一性原理、分子动力学、Monte Carlo
- 能带、态密度、扩散能垒、形成能
- 相场模拟、有限元、PINN
- 反应机理、失效/衰减机理
- 枝晶生长、成核、界面反应机理
- 热力学/相图

边界：如果段落包含模拟计算也报告了实验数据，按主要目的判断。以理论分析为主→本类；以工艺优化为主→"材料制备"；以性能报告为主→"电化学性能"。

## 6. 概述
覆盖：无法归入以上 5 类的其他内容。
- 引言、综述、背景介绍
- 应用场景描述（EV、储能、航空、消费电子、医疗）
- 致谢、参考文献、作者信息
- 无法明确判断的段落

**重要边界**：如果段落以综述或背景介绍开头，但包含具体的电化学性能数据（如容量、循环圈数、电压衰减率、能量密度、库仑效率等）或理化性质数值（如电导率、扩散系数、电位、窗口等），则**必须归入对应的"电化学性能"或"理化性质"，而非"概述"**。以段落中的**数据密度**为准，不要被开头的综述性语言误导。

# 判定优先级（重要，确保互斥）
1. 如果段落描述了制备方法**并报告了性能数据** → 判断聚焦点是"怎么做"(材料制备)还是"结果多好"(电化学性能)
2. 如果段落包含表征手段（XRD/SEM/XPS）但分析重点是结构结果而非方法本身 → 优先选"结构表征"而非"材料制备"
3. 如果段落同时描述理化参数（电导率等）和电池性能（容量等） → 侧重材料本征归"理化性质"，侧重电池表现归"电化学性能"
4. **如果段落以综述性语言开头但后半段有具体的性能/性质数值 → 归入对应的"电化学性能"或"理化性质"，不得归入"概述"**
5. 如果段落完全无法判断 → 归入"概述"

# 示例1
输入：
标题: "High-energy lithium metal battery with localized high-concentration electrolyte"
作者: "Zhang et al."
发表日期: "2023"
DOI: "10.1016/j.ensm.2023.01.001"
段落: "The LMB cells were assembled using Li-metal anode and NMC811 cathode. The electrolyte was a localized high-concentration LiFSI in DME. SEM images show a dense SEI layer. The cells delivered 410 Wh/kg and retained 85% after 200 cycles."

输出：{{"label": "电化学性能"}}

# 示例2
输入：
标题: "Ionic conductivity and thermal stability of LiFSI in DME solutions"
作者: "Wang et al."
发表日期: "2022"
DOI: "10.1002/ente.202200123"
段落: "The ionic conductivity of 1 M LiFSI in DME reaches 8.5 mS/cm at 25°C. The electrolyte exhibits an electrochemical window up to 4.8 V vs. Li/Li+."

输出：{{"label": "理化性质"}}

# 示例3
输入：
标题: "Synthesis of NMC811 cathode with improved performance"
作者: "Li et al."
发表日期: "2021"
DOI: "10.1016/j.jpowsour.2021.229512"
段落: "NMC811 powder was synthesized via coprecipitation method. The precursor Ni0.8Co0.1Mn0.1(OH)2 was mixed with LiOH and calcined at 800°C for 12h in oxygen atmosphere. The resulting material was characterized by XRD."

输出：{{"label": "材料制备"}}

# 现在，请根据以下信息输出标签：
标题: {title}
作者: {authors}
发表日期: {publication_date}
DOI: {doi}
段落: {chunk_text}
"""

KEYWORD_GROUPS = {}  # 保留空 dict 兼容历史导入

# 展平所有 base 关键词用于兜底打分
_KEYWORD_FLAT = {}
for primary, group in KEYWORD_GROUPS.items():
    _KEYWORD_FLAT[primary] = group["base"]

# ══════════════════════════════════════════════════════
# Meta 加载 — 从 meta_merged.json 获取标题/作者/日期
# ══════════════════════════════════════════════════════

META_JSON_PATH = PROJECT_ROOT / "miner" / "json" / "meta_merged.json"


def load_meta_map() -> Dict[str, dict]:
    """加载 meta_merged.json，返回以 DOI 为 key 的元数据字典。"""
    if not META_JSON_PATH.exists():
        print(f"[warn] 未找到元数据文件: {META_JSON_PATH}，段落将不携带标题/作者/日期")
        return {}
    with open(META_JSON_PATH, encoding="utf-8") as f:
        items = json.load(f)
    meta_map = {}
    for item in items:
        doi = item.get("doi", "")
        if doi:
            meta_map[doi] = {
                "title": item.get("title", "") or "",
                "authors": item.get("authors", "") or "",
                "publication_date": item.get("publication_date", "") or "",
            }
    return meta_map


@dataclass(frozen=True)
class ParagraphItem:
    source_file: Path
    doi: str
    component: str
    paragraph_index: int
    paragraph: str
    title: str = ""
    authors: str = ""
    publication_date: str = ""


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
    """按空行切分段落，带后合并逻辑避免断句截断。

    合并规则（基于 clean_text.py + bullet 扩展）：
      1. 前段不以 .!? 结尾 → 合并（如 "reasons:" 冒号结尾场景）
      2. 当前段以小写字母开头 → 合并到前段
      3. 当前段以 bullet（- * • 数字.）开头 → 合并到前段
    """
    chunks = re.split(r"\n\s*\n+", text)
    paragraphs = []
    for chunk in chunks:
        paragraph = re.sub(r"[ \t]+", " ", chunk.strip())
        paragraph = re.sub(r"\n+", " ", paragraph)
        if len(paragraph) >= min_length:
            paragraphs.append(paragraph)

    # 后合并：修复空行截断
    if not paragraphs:
        return paragraphs

    merged = [paragraphs[0]]
    for para in paragraphs[1:]:
        prev_text = merged[-1]
        prev_end = prev_text.rstrip()[-1] if prev_text.rstrip() else ""
        ends_sentence = prev_end in (".", "!", "?")
        curr_stripped = para.lstrip()
        curr_start = curr_stripped[0] if curr_stripped else ""
        starts_with_bullet = bool(re.match(r"^[\-\*•]\s", curr_stripped))
        starts_with_num = bool(re.match(r"^\d+[\.\)]\s", curr_stripped))
        starts_with_lower = curr_start.islower() if curr_start else False

        if not ends_sentence or starts_with_bullet or starts_with_num or starts_with_lower:
            merged[-1] = prev_text + " " + para
        else:
            merged.append(para)
    return merged


def iter_markdown_files(input_root: Path) -> Iterable[Path]:
    for path in sorted(input_root.rglob("*.md")):
        if "Solid_State" in str(path):
            continue
        if infer_component(path):
            yield path


def collect_paragraphs(input_root: Path, min_length: int, limit: Optional[int], meta_map: Dict[str, dict]) -> List[ParagraphItem]:
    items: List[ParagraphItem] = []
    for md_path in iter_markdown_files(input_root):
        component = infer_component(md_path)
        if not component:
            continue
        cleaned = clean_markdown(str(md_path), min_len=0)
        if not cleaned:
            print(f"[skip] {md_path.name}: 清洗后内容为空", file=sys.stderr)
            continue
        doi = doi_from_filename(md_path)
        meta = meta_map.get(doi, {})
        for idx, paragraph in enumerate(split_paragraphs(cleaned, min_length=min_length)):
            items.append(
                ParagraphItem(
                    source_file=md_path,
                    doi=doi,
                    component=component,
                    paragraph_index=idx,
                    paragraph=paragraph,
                    title=meta.get("title", ""),
                    authors=meta.get("authors", ""),
                    publication_date=meta.get("publication_date", ""),
                )
            )
            if limit is not None and len(items) >= limit:
                return items
    return items


def parse_result(raw_output: str) -> Optional[str]:
    """解析 LLM 返回的单标签 JSON。"""
    cleaned = raw_output.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    json_match = re.search(r"\{.*?\}", cleaned, re.DOTALL)
    if not json_match:
        return None
    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        return None
    label = str(data.get("label", "")).strip()
    return label if label in PRIMARY_LABELS else None


def keyword_result(paragraph: str) -> str:
    """关键词兜底：返回 6 个标签中评分最高的一个。"""
    text = paragraph.lower()
    scores = {label: 0 for label in PRIMARY_LABELS}

    # 电化学性能
    scores["电化学性能"] += sum(1 for kw in [
        "capacity", "retention", "cycle", "cycling", "rate capability",
        "voltage", "coulombic", "c-rate", "mah g", "a g", "ma g",
        "energy density", "power density", "polarization", "overpotential",
        "wh kg", "wh l", "areal capacity", "eis", "gitt",
        "容量", "循环", "倍率", "库仑", "能量密度", "过电位", "极化", "面容量",
    ] if kw.lower() in text)

    # 理化性质
    scores["理化性质"] += sum(1 for kw in [
        "conductivity", "ionic conductivity", "electronic conductivity",
        "s cm", "diffusion coefficient", "transference number",
        "electrochemical window", "impedance", "modulus", "hardness",
        "young", "mechanical", "thermal", "tga", "dsc",
        "decomposition temperature", "porosity", "pore", "bet",
        "band gap", "bandgap",
        "电导率", "离子电导", "扩散系数", "迁移数",
        "电化学窗口", "模量", "孔隙率", "带隙", "能带",
    ] if kw.lower() in text)

    # 结构表征
    scores["结构表征"] += sum(1 for kw in [
        "xrd", "x-ray diffraction", "sem", "tem", "xps", "raman", "ftir",
        "ft-ir", "lattice parameter", "space group", "crystal structure",
        "particle size", "morphology", "eds", "icp",
        "晶格", "空间群", "晶体结构", "粒度", "形貌", "衍射",
        "surface film", "sei", "cei",
    ] if kw.lower() in text)

    # 材料制备
    scores["材料制备"] += sum(1 for kw in [
        "synthesized", "prepared", "fabricated", "calcined", "annealed",
        "coated", "doped", "mixed", "dissolved", "stirred", "heated", "dried",
        "assembled", "precipitation", "sol-gel", "hydrothermal",
        "ball mill", "sintering", "coprecipitation", "co-precipitation",
        "slurry", "tape casting", "hot press", "sps",
        "atomic layer", "ald", "cvd",
        "制备", "合成", "退火", "煅烧", "涂覆", "烧结", "共沉淀",
        "水热", "球磨", "组装", "干法", "包覆", "掺杂", "溶液配制",
    ] if kw.lower() in text)

    # 机理/模拟
    scores["机理/模拟"] += sum(1 for kw in [
        "dft", "first-principles", "first principles", "density functional",
        "molecular dynamics", "ab initio", "nudged elastic band",
        "band structure", "density of states", "bader charge",
        "migration barrier", "activation energy", "formation energy",
        "mechanism", "degradation", "failure", "nucleation",
        "dendrite", "crack", "space charge", "dead layer",
        "phase field", "finite element",
        "dft", "第一性原理", "分子动力学", "能带", "扩散能垒",
        "机理", "失效", "成核", "枝晶", "相场", "有限元",
        "monte carlo", "pinn",
    ] if kw.lower() in text)

    # 概述（引言/综述/背景/体系分类/应用场景 等）
    scores["概述"] += sum(1 for kw in [
        "introduction", "review", "overview", "background", "summary",
        "lithium-sulfur", "lithium-air", "li-s", "li-air", "solid state",
        "future perspective", "challenge", "prospect", "recent advance",
        "electric vehicle", "grid storage", "consumer electronics",
        "energy storage system", "portable", "mobile",
        "引言", "综述", "背景", "概述", "挑战", "展望", "进展",
        "锂硫", "固态", "锂空", "储能", "电动汽车",
        "conclusion", "concluding", "summary", "summary and outlook",
    ] if kw.lower() in text)

    best_label = max(scores, key=scores.get)
    return best_label if scores[best_label] > 0 else "概述"


def build_label_chain() -> Any:
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import PromptTemplate

    llm = create_llm("classification")
    prompt = PromptTemplate(
        template=PROMPT_TEMPLATE,
        input_variables=["title", "authors", "publication_date", "doi", "chunk_text"],
    )
    return prompt | llm | StrOutputParser()


def classify_paragraphs(items: List[ParagraphItem]) -> List[dict]:
    chain = build_label_chain()
    records = []
    for i, item in enumerate(items, 1):
        try:
            raw = chain.invoke({
                "title": item.title,
                "authors": item.authors,
                "publication_date": item.publication_date,
                "doi": item.doi,
                "chunk_text": item.paragraph,
            })
            label = parse_result(raw)
            if label is None:
                label = keyword_result(item.paragraph)
        except Exception as exc:
            print(
                f"[warn] DeepSeek failed for {item.source_file.name} paragraph {item.paragraph_index}: {exc}. "
                "Using keyword fallback.",
                file=sys.stderr,
            )
            label = keyword_result(item.paragraph)

        records.append({
            "source_paper": item.doi,
            "title": item.title,
            "authors": item.authors,
            "publication_date": item.publication_date,
            "component": item.component,
            "label": label,
            "paragraph_context": item.paragraph,
            "_chroma_metadata": {
                "source_paper": item.doi,
                "title": item.title,
                "component": item.component,
                "label": label,
                "source_file": str(item.source_file),
                "paragraph_index": item.paragraph_index,
            },
            "_id": make_document_id(item, label),
        })
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
    public_records = []
    for record in records:
        public_records.append({
            "source_paper": record["source_paper"],
            "title": record.get("title", ""),
            "authors": record.get("authors", ""),
            "publication_date": record.get("publication_date", ""),
            "component": record["component"],
            "label": record["label"],
            "paragraph_context": record["paragraph_context"],
        })
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
        tag = meta.get("label", "?")
        print(
            "[chroma] sample result: "
            f"{meta.get('source_paper')} {meta.get('component')}/{tag}"
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
        description="v3 — 6 维段落标签标注 + Chroma 入库"
    )
    parser.add_argument("--input-root", default="database/type")
    parser.add_argument("--output-json", default="miner/json/paragraph_metadata_v3.json")
    parser.add_argument("--chroma-dir", default="miner/chroma/paragraphs_v3")
    parser.add_argument("--collection", default="battery_paragraphs_v3")
    parser.add_argument("--min-length", type=int, default=50)
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--embedding-model", default="bge-m3")
    parser.add_argument("--limit", type=int, default=None, help="Limit paragraphs for smoke tests.")
    parser.add_argument("--meta-json", default=str(META_JSON_PATH), help="元数据 JSON 路径")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_root = resolve_project_path(args.input_root)
    output_json = resolve_project_path(args.output_json)
    chroma_dir = resolve_project_path(args.chroma_dir)
    meta_json = Path(args.meta_json)

    if not input_root.is_dir():
        raise FileNotFoundError(f"Input root does not exist: {input_root}")

    validate_runtime(base_url=args.ollama_base_url, model=args.embedding_model)

    # 加载元数据
    meta_map = load_meta_map() if meta_json.exists() else {}
    if meta_map:
        print(f"[meta] loaded {len(meta_map)} paper metadata entries")
    else:
        print("[meta] no metadata loaded — paragraphs will lack title/authors/date")

    items = collect_paragraphs(input_root, min_length=args.min_length, limit=args.limit, meta_map=meta_map)
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
