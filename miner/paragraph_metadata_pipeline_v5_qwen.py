#!/usr/bin/env python3
"""v5-qwen — 段落标签标注 + Qwen3-Embedding-8B (GPU) Chroma 入库

依赖: miner/json/metadata/meta_merged.json（文献元数据，否则缺标题/作者）

===== 完整工作流（首次） =====

1. 提取元数据（全量）
   python -m miner.meta_extraction.extract_meta database/type/Lithium_Ion_Metal_Battery -o miner/json/metadata/meta_merged.json

2. 全量入库（分类 + 嵌入 + Chroma）
   python miner/paragraph_metadata_pipeline_v5_qwen.py

===== 新增文献后（增量） =====

1. 元数据增量更新
   python -m miner.meta_extraction.extract_meta --incremental database/type/Lithium_Ion_Metal_Battery -o miner/json/metadata/meta_merged.json

2. 增量入库（自动跳过已处理的文献）
   python miner/paragraph_metadata_pipeline_v5_qwen.py --incremental

===== 调参后重新索引（不重跑 LLM） =====

   python miner/reindex_chroma.py --max-chunk 2000 --overlap 200

===== 相对 v5 的改动 =====
  - 嵌入模型: bge-m3 -> qwen3-embedding:8b (Ollama 管理 GPU 内存，约 4.7GB GGUF)
  - 默认 Chroma 目录/collection 加 _qwen 后缀，避免与 bge-m3 向量混淆
"""

from __future__ import annotations

import gc
import argparse
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from collections import defaultdict
from datetime import datetime
import logging
from pathlib import Path
from typing import Any, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── 非正文过滤日志 ──
_FILTER_LOG_DIR = PROJECT_ROOT / "miner" / "logs"
_FILTER_LOG_DIR.mkdir(parents=True, exist_ok=True)
_filter_logger = logging.getLogger("non_body_filter")
_filter_logger.setLevel(logging.INFO)
_filter_logger.propagate = False
if not _filter_logger.handlers:
    _handler = logging.FileHandler(
        str(_FILTER_LOG_DIR / f"non_body_filter_{datetime.now().strftime('%Y%m%d')}.log"),
        encoding="utf-8",
    )
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _filter_logger.addHandler(_handler)

from miner.cleaning.cleaner_v2_standalone import clean_markdown

COMPONENTS = {"anode", "cathode", "electrolyte"}

# ── 标签体系：6 类中文标签（v2 池 + v3 扩展定义） ──
PRIMARY_LABELS = {"电化学性能", "材料属性与表征", "材料制备", "机理/模拟", "概述", "非正文"}

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

边界：强调"电池组装后的表现"。如果段落描述的是材料自身的理化参数（如离子电导率、模量、热分解温度），应归为"材料属性与表征"。

## 2. 材料属性与表征
覆盖：材料/电解液的本征物理化学参数以及物相、形貌、表面化学、成分分析。
- 离子/电子电导率（mS/cm, S/cm）
- 锂离子扩散系数（cm²/s）
- 电化学窗口（V vs. Li/Li+）
- 迁移数
- 力学性能（模量、硬度、强度、弹性）
- 热学性能（热分解温度、TGA、DSC）
- 孔隙率/比表面积（BET、BJH）
- 带隙/能带
- 黏度、密度、润湿性等其它物性
- 物相/晶体结构：XRD、晶格参数、空间群、Rietveld
- 微观形貌：SEM、TEM、粒径、形貌
- 表面/界面化学：XPS、FTIR、Raman、SEI/CEI成分分析
- 成分分析：EDS、ICP、Mapping

边界一（理化参数侧）：如果段落描述的是电池整体表现（如循环后容量、倍率性能、能量密度），应归为"电化学性能"而非本类。
边界二（结构表征侧）：强调"用什么方法看/测了结构"。如果段落的重点是性能数据（如离子电导率、容量），应归入对应的"材料属性与表征"或"电化学性能"。

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
- 无法明确判断的段落

**重要边界**：如果段落以综述或背景介绍开头，但包含具体的电化学性能数据（如容量、循环圈数、电压衰减率、能量密度、库仑效率等）或理化性质数值（如电导率、扩散系数、电位、窗口等），则**必须归入对应的"电化学性能"或"材料属性与表征"，而非"概述"**。以段落中的**数据密度**为准，不要被开头的综述性语言误导。

## 7. 非正文
覆盖：文献中的非技术性内容，不含与电池/材料直接相关的科学数据。
- 致谢、基金项目（Acknowledgments, Funding）
- 作者贡献声明（Author Contributions, CRediT authorship）
- 数据/代码可用性声明（Data Availability, Code Availability）
- 利益冲突声明（Conflict of Interest）
- 附录/补充材料声明（Supplementary Material availability）
- 参考文献列表（References）
- 投稿信息（Received/Accepted dates, Publisher info, Journal info）
- 版权声明（Copyright, License, Publisher note）

边界：如果段落包含致谢或附录声明但同时也报告了具体的性能数据或材料参数 → 按实际数据内容归入对应标签（"电化学性能"/"材料属性与表征"等），而非"非正文"。

# 判定优先级（重要，确保互斥）
1. 如果段落描述了制备方法**并报告了性能数据** → 判断聚焦点是"怎么做"(材料制备)还是"结果多好"(电化学性能)
2. 如果段落包含表征手段（XRD/SEM/XPS）但分析重点是结构结果而非方法本身 → 优先选"材料属性与表征"而非"材料制备"
3. 如果段落同时描述理化参数（电导率等）和电池性能（容量等） → 侧重材料本征归"材料属性与表征"，侧重电池表现归"电化学性能"
4. **如果段落以综述性语言开头但后半段有具体的性能/性质数值 → 归入对应的"电化学性能"或"材料属性与表征"，不得归入"概述"**
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

输出：{{"label": "材料属性与表征"}}

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

META_JSON_PATH = PROJECT_ROOT / "miner" / "json" / "metadata" / "meta_merged.json"


def load_meta_map() -> Dict[str, dict]:
    """加载 meta_merged.json，返回以 DOI 为 key 的元数据字典。"""
    if not META_JSON_PATH.exists():
        print(f"[warn] 未找到元数据文件: {META_JSON_PATH}，段落将不携带标题/作者/日期")
        return {}
    with open(META_JSON_PATH, encoding="utf-8") as f:
        items = json.load(f)
    meta_map = {}
    for item in items:
        meta = {
            "title": item.get("title", "") or "",
            "authors": item.get("authors", "") or "",
            "publication_date": item.get("publication_date", "") or "",
        }
        # 按文件路径索引（优先匹配）
        fp = item.get("file_path", "")
        if fp:
            meta_map[f"path:{fp}"] = meta
        # 按 DOI 索引（后备）
        doi = item.get("doi", "")
        if doi:
            meta_map[f"doi:{doi}"] = meta
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


_DOI_PATTERN = re.compile(r"^10\.\d{4,}/[a-zA-Z0-9._()/%\-]+$")

def doi_from_filename(path: Path) -> str:
    raw = path.stem.replace("_", "/")
    if _DOI_PATTERN.match(raw):
        return raw
    return ""


_REF_PATTERN = re.compile(r"^\d+\.\s+[A-Z]|^\[\d+\]\s")

def _is_reference_text(text: str) -> bool:
    """判断文本是否为参考文献格式（数字. 作者开头）"""
    return bool(_REF_PATTERN.match(text.lstrip()))

def _split_long_chunk(text: str, max_chunk: int = 7500) -> List[str]:
    """对超长无空行分隔的 chunk 做二级切分：按参考文献序号断开"""
    if len(text) <= max_chunk:
        return [text]
    # 尝试按参考文献序号模式切分
    parts = re.split(r"(?=\d+\.\s+[A-Z]|\[\d+\])", text)
    # 如果切不动（模式不匹配），按段落缩略处理
    if len(parts) <= 1:
        return [text]
    result = []
    for p in parts:
        p = p.strip()
        if p:
            result.append(p)
    return result


def split_paragraphs(text: str, min_length: int) -> List[str]:
    """按空行切分段落，带后合并逻辑避免断句截断。

    合并规则（基于 clean_text.py + bullet 扩展）：
      1. 前段不以 .!? 结尾 → 合并（如 "reasons:" 冒号结尾场景）
      2. 当前段以小写字母开头 → 合并到前段
      3. 当前段以 bullet（- * • 数字.）开头 → 合并到前段
        但以数字. + 大写字母开头的参考文献段除外 → 不向前合并
      4. 表格与正文互不粘合（table 边界检测）
    """
    chunks = re.split(r"\n\s*\n+", text)
    paragraphs = []
    for chunk in chunks:
        paragraph = re.sub(r"[ \t]+", " ", chunk.strip())
        paragraph = re.sub(r"\n+", " ", paragraph)
        if len(paragraph) >= min_length:
            # 二级切分：超长 chunk 按参考文献序号断开
            sub_parts = _split_long_chunk(paragraph)
            paragraphs.extend(sub_parts)

    # 后合并：修复空行截断
    if not paragraphs:
        return paragraphs

    merged = [paragraphs[0]]
    for para in paragraphs[1:]:
        prev_text = merged[-1]
        prev_end = prev_text.rstrip()[-1] if prev_text.rstrip() else ""
        ends_sentence = prev_end in (".", "!", "?", "。", "！", "？")
        curr_stripped = para.lstrip()
        curr_start = curr_stripped[0] if curr_stripped else ""
        starts_with_bullet = bool(re.match(r"^[\-\*•]\s", curr_stripped))
        starts_with_num = bool(re.match(r"^\d+[\.\)]\s", curr_stripped))
        starts_with_lower = curr_start.islower() if curr_start else False
        starts_with_table = curr_stripped.startswith("<table") or curr_stripped.startswith("(Continued)")
        prev_is_table = prev_text.lstrip().startswith("<table")
        is_reference = bool(_REF_PATTERN.match(curr_stripped))

        if starts_with_table or prev_is_table:
            merged.append(para)
        elif is_reference:
            # 参考文献段不向前合并，独立成段
            merged.append(para)
        elif not ends_sentence or starts_with_bullet or starts_with_num or starts_with_lower:
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


def _dedup_key(doi: str, title: str, authors: str) -> str:
    """优先 DOI，降级到 title+authors"""
    if doi and not doi.startswith("_"):
        return f"doi:{doi}"
    key = f"{title[:50]}_{authors[:30]}"
    if key.strip("_"):
        return f"title:{key}"
    return ""

def collect_paragraphs(input_root: Path, min_length: int, limit: Optional[int],
                       meta_map: Dict[str, dict], existing_keys: set = None,
                       incremental: bool = False,
                       max_papers: Optional[int] = None) -> List[ParagraphItem]:
    items: List[ParagraphItem] = []
    seen_papers: set[str] = set()
    for md_path in iter_markdown_files(input_root):
        component = infer_component(md_path)
        if not component:
            continue
        doi = doi_from_filename(md_path)
        # 优先按文件路径匹配，其次 DOI
        fp_key = f"path:{str(md_path.resolve())}"
        doi_key = f"doi:{doi}"
        meta = meta_map.get(fp_key) or meta_map.get(doi_key, {})
        dedup = _dedup_key(doi, meta.get("title", ""), meta.get("authors", ""))
        if incremental and existing_keys and dedup and dedup in existing_keys:
            print(f"[skip] {md_path.name}: 已入库", file=sys.stderr)
            continue
        # 按文献提前终止（在清洗前，避免浪费CPU）
        dedup_key = _dedup_key(doi, meta.get("title", ""), meta.get("authors", ""))
        if max_papers is not None and len(seen_papers) >= max_papers:
            break
        seen_papers.add(dedup_key) if dedup_key else None

        cleaned = clean_markdown(str(md_path), min_len=0)
        if not cleaned:
            print(f"[skip] {md_path.name}: 清洗后内容为空", file=sys.stderr)
            continue
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


def collect_paper_groups(input_root: Path, min_length: int, limit: Optional[int],
                         meta_map: Dict[str, dict], existing_keys: set = None,
                         incremental: bool = False,
                         max_papers: Optional[int] = None):
    """流式生成器：按文献逐个 yield (paper_key, List[ParagraphItem])。
    不一次性加载全部文献到内存，避免 OOM。"""
    seen_papers: set[str] = set()
    for md_path in iter_markdown_files(input_root):
        component = infer_component(md_path)
        if not component:
            continue
        doi = doi_from_filename(md_path)
        # 优先按文件路径匹配，其次 DOI
        rel_path = str(md_path.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
        fp_key = f"path:{rel_path}"
        doi_key = f"doi:{doi}"
        meta = meta_map.get(fp_key) or meta_map.get(doi_key, {})
        dedup = _dedup_key(doi, meta.get("title", ""), meta.get("authors", ""))
        if incremental and existing_keys and dedup and dedup in existing_keys:
            print(f"[skip] {md_path.name}: 已入库", file=sys.stderr)
            continue
        dedup_key = _dedup_key(doi, meta.get("title", ""), meta.get("authors", ""))
        if max_papers is not None and len(seen_papers) >= max_papers:
            break
        seen_papers.add(dedup_key) if dedup_key else None

        cleaned = clean_markdown(str(md_path), min_len=0)
        if not cleaned:
            print(f"[skip] {md_path.name}: 清洗后内容为空", file=sys.stderr)
            continue
        paper_items = []
        for idx, paragraph in enumerate(split_paragraphs(cleaned, min_length=min_length)):
            paper_items.append(
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
        if paper_items:
            yield (doi, component), paper_items


def group_items_by_paper(items: List[ParagraphItem]) -> List[List[ParagraphItem]]:
    """将 flat ParagraphItem 列表按 (doi, component) 分组，每篇文献的每个组件为一组。"""
    groups: dict[tuple[str, str], list[ParagraphItem]] = defaultdict(list)
    for item in items:
        groups[(item.doi, item.component)].append(item)
    return list(groups.values())


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

    # 材料属性与表征
    scores["材料属性与表征"] += sum(1 for kw in [
        "conductivity", "ionic conductivity", "electronic conductivity",
        "s cm", "diffusion coefficient", "transference number",
        "electrochemical window", "impedance", "modulus", "hardness",
        "young", "mechanical", "thermal", "tga", "dsc",
        "decomposition temperature", "porosity", "pore", "bet",
        "band gap", "bandgap",
        "电导率", "离子电导", "扩散系数", "迁移数",
        "电化学窗口", "模量", "孔隙率", "带隙", "能带",
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


_label_chain_cache = None

def build_label_chain() -> Any:
    global _label_chain_cache
    if _label_chain_cache is not None:
        return _label_chain_cache
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import PromptTemplate

    llm = create_llm("classification")
    prompt = PromptTemplate(
        template=PROMPT_TEMPLATE,
        input_variables=["title", "authors", "publication_date", "doi", "chunk_text"],
    )
    _label_chain_cache = prompt | llm | StrOutputParser()
    return _label_chain_cache


def classify_paragraphs(items: List[ParagraphItem]) -> List[dict]:
    chain = build_label_chain()
    records = []
    for i, item in enumerate(items, 1):
        label = None
        for attempt in range(3):
            try:
                raw = chain.invoke({
                    "title": item.title,
                    "authors": item.authors,
                    "publication_date": item.publication_date,
                    "doi": item.doi,
                    "chunk_text": item.paragraph,
                })
                label = parse_result(raw)
                if label is not None:
                    break
            except Exception as exc:
                if attempt < 2:
                    print(f"  [retry {attempt+1}] {item.doi} #{item.paragraph_index}: {exc}")
        if label is None:
            print(
                f"[warn] LLM failed for {item.source_file.name} #{item.paragraph_index}. "
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
                "source_file": str(item.source_file.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/"),
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


def write_json_append(records_to_append: List[dict], output_json: Path) -> None:
    """追加模式写入 JSON，原子 rename 防损坏。

    读取已有文件 → 去重合并 → 写入 .tmp → rename 替换原文件。
    """
    output_json.parent.mkdir(parents=True, exist_ok=True)
    all_records: List[dict] = []
    if output_json.exists():
        try:
            all_records = json.loads(output_json.read_text(encoding="utf-8"))
        except Exception:
            pass
    seen = {(r.get("source_paper"), r.get("paragraph_context")) for r in all_records}
    for r in records_to_append:
        key = (r["source_paper"], r["paragraph_context"])
        if key not in seen:
            all_records.append(r)
            seen.add(key)
    tmp = output_json.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(all_records, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(output_json)


def write_json_snapshot(records: List[dict], output_json: Path) -> None:
    """全量写入 + 时间戳快照，在全部处理完成后调用。"""
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = output_json.parent / f"{output_json.stem}_{timestamp}.json"
    snapshot_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
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

    # 增量：只写入不在 Chroma 中的 doc IDs
    records = dedupe_record_ids(records)
    documents = [Document(page_content=r["paragraph_context"], metadata=r["_chroma_metadata"]) for r in records]
    ids = [record["_id"] for record in records]

    # 批量检查 ID 是否存在，避免一次性 get() 全部导致 SQL 变量超限
    existing_ids = set()
    check_batch = 10000
    for start in range(0, len(ids), check_batch):
        batch_ids = ids[start:start+check_batch]
        try:
            result = vector_store.get(ids=batch_ids)
            existing_ids.update(result.get("ids", []))
        except Exception:
            pass  # batch 不存在时继续
    print(f"[chroma] 已有 {len(existing_ids)} 条，新增 {len(ids)-len(existing_ids)} 条")

    new_docs = [(d, i) for d, i in zip(documents, ids) if i not in existing_ids]
    if new_docs:
        new_doc_list, new_id_list = zip(*new_docs)
        ensure_unique_ids(list(new_id_list))
        batch_size = 2000  # Chroma max batch is 5461; keep low to avoid OOM on embedding
        total = len(new_doc_list)
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            vector_store.add_documents(
                documents=list(new_doc_list[start:end]),
                ids=list(new_id_list[start:end]),
            )
            print(f"[chroma] batch {start // batch_size + 1}: added {end - start} paragraphs")
        print(f"[chroma] added {total} new paragraphs total (skipped {len(ids) - total} existing)")
    else:
        print("[chroma] all paragraphs already in Chroma, nothing to add")

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
        description="v5-qwen — 7 维段落标签标注 + Qwen3-Embedding-8B (GPU) Chroma 入库"
    )
    parser.add_argument("--input-root", default="database/type")
    parser.add_argument("--output-json", default="miner/json/Chrome/paragraph_metadata_q.json")
    parser.add_argument("--chroma-dir", default="miner/chroma/paragraphs_q")
    parser.add_argument("--collection", default="battery_paragraphs_q")
    parser.add_argument("--min-length", type=int, default=50)
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--embedding-model", default="qwen3-embedding:8b")
    parser.add_argument("--limit", type=int, default=None, help="Limit paragraphs for smoke tests.")
    parser.add_argument("--meta-json", default=str(META_JSON_PATH), help="元数据 JSON 路径")
    parser.add_argument("--incremental", action="store_true", help="增量模式：跳过已入库的文献")
    parser.add_argument("--max-papers", type=int, default=None, help="限制处理文献数（文献级，而非段落级）")
    return parser.parse_args()


def _split_long_records(records: List[dict], max_chunk: int = 7500, overlap: int = 750) -> List[dict]:
    """过滤非正文 + 超长段双存。从 main() 中提取为独立函数，供逐篇处理时复用。"""
    splitted = []
    for rec in records:
        label = rec.get("label", "")
        if label == "非正文":
            _filter_logger.info(
                "[非正文] doi=%s | comp=%s | #%s | %s | %s",
                rec.get("source_paper", "?"),
                rec.get("_chroma_metadata", {}).get("component", "?"),
                rec.get("_chroma_metadata", {}).get("paragraph_index", "?"),
                (rec.get("title", "") or "?")[:60],
                rec.get("paragraph_context", "")[:200],
            )
            continue
        text = rec.get("paragraph_context", "")
        if not text:
            continue
        if len(text) <= max_chunk or text.startswith("<table"):
            splitted.append(rec)
            continue
        MAX_CHUNK = max_chunk
        OVERLAP = overlap
        base_id = rec.get("_id", "unknown")
        full_rec = dict(rec)
        full_rec["paragraph_context"] = text
        full_rec["_id"] = f"{base_id}|full"
        if "_chroma_metadata" in full_rec:
            full_rec["_chroma_metadata"]["is_full_paragraph"] = True
        splitted.append(full_rec)
        n = 1
        pos = 0
        while pos < len(text):
            end = min(pos + MAX_CHUNK, len(text))
            seg = text[pos:end]
            if end < len(text):
                last_dot = seg.rfind(". ")
                if last_dot > MAX_CHUNK // 3:
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
            pos = end - OVERLAP
        print(f"  [split] {base_id[:40]}: {len(text)}字 -> 1整段 + {n-1}子段")
    return splitted


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
        # 自动回填已有 JSON 中缺 title 的段落
        if output_json.exists():
            try:
                backfilled = 0
                bak = json.loads(output_json.read_text(encoding="utf-8"))
                for r in bak:
                    if r.get("title"):
                        continue
                    sf = r.get("_chroma_metadata", {}).get("source_file", "")
                    meta = meta_map.get(f"path:{sf}") if sf else None
                    if meta:
                        for fld in ["title", "authors", "publication_date"]:
                            r[fld] = meta.get(fld, "")
                        if "_chroma_metadata" in r:
                            for fld in ["title", "authors", "publication_date"]:
                                r["_chroma_metadata"][fld] = r[fld]
                        backfilled += 1
                if backfilled:
                    output_json.write_text(json.dumps(bak, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"[meta] backfilled {backfilled} records with title/authors/date")
            except Exception as e:
                print(f"[warn] 回填元数据失败: {e}")
    else:
        print("[meta] no metadata loaded — paragraphs will lack title/authors/date")

    existing_keys = set()
    if args.incremental and output_json.exists():
        try:
            existing = json.loads(output_json.read_text(encoding="utf-8"))
            for d in existing:
                k = _dedup_key(d.get("source_paper", ""), d.get("title", ""), d.get("authors", ""))
                if k: existing_keys.add(k)
            print(f"[incremental] 已有 {len(existing)} 段来自 {len(existing_keys)} 篇文献")
        except Exception as e:
            print(f"[warn] 读取现有 JSON 失败: {e}")

    # 流式逐篇收集，避免一次性加载全量数据到内存
    all_records: List[dict] = []
    paper_count = 0
    paper_generator = collect_paper_groups(
        input_root, min_length=args.min_length, limit=args.limit,
        meta_map=meta_map, existing_keys=existing_keys,
        incremental=args.incremental,
        max_papers=args.max_papers)

    for paper_key, group in paper_generator:
        paper_count += 1
        doi, comp = paper_key
        total_papers_suffix = f"/{args.max_papers}" if args.max_papers else ""
        print(f"--- [{paper_count}{total_papers_suffix}] {doi} ({comp}) ---")
        records = classify_paragraphs(group)
        processed = _split_long_records(records)
        if processed:
            n_segs = len(processed)
            write_json_append(processed, output_json)
            all_records.extend(processed)
            del processed, records, group
            gc.collect()
            print(f"  -> checkpoint: {n_segs} 段入库")
        else:
            _filter_logger.warning(
                "[整篇跳过] 全部为非正文/空 doi=%s comp=%s rows=%d",
                doi, comp, len(records),
            )
            del records, group
            gc.collect()
            print(f"  -> 全部为非正文/空，跳过")

    # 全部处理完成：写入完整快照
    if all_records:
        # 增量模式：合并已有数据，避免旧记录丢失
        if args.incremental:
            merged = all_records
            if output_json.exists():
                old = json.loads(output_json.read_text(encoding="utf-8"))
                merged = old + all_records
            write_json_snapshot(merged, output_json)
        else:
            write_json_snapshot(all_records, output_json)
        print(f"[json] snapshot: {len(all_records)} records in {output_json}")
    else:
        print("[scan] no new paragraphs collected")

    # Chroma 入库（从 JSON 读取全量数据，增量去重在 ingest_chroma 内部）
    chroma_records = []
    if output_json.exists():
        try:
            chroma_records = json.loads(output_json.read_text(encoding="utf-8"))
            print(f"[chroma] loading {len(chroma_records)} records from {output_json}")
        except Exception as e:
            print(f"[warn] 读取 JSON 失败: {e}")
    if chroma_records:
        ingest_chroma(
            chroma_records,
            chroma_dir=chroma_dir,
            collection=args.collection,
            base_url=args.ollama_base_url,
            model=args.embedding_model,
        )
    print(f"[chroma] done ({chroma_dir})")


if __name__ == "__main__":
    main()