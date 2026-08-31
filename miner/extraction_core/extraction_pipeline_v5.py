# -*- coding: utf-8 -*-
"""提取流水线 v5 — 统一提取 (Unified Agent) + 规则筛选 + 表格上下文 + 后处理

工作流（对应 TECHNICAL_GUIDE.md）：
  原始 md → 规则清洗 → 表格上下文提取 → 段落/表格块切分 → 规则筛选
  → 灰区 include 兜底 → unified agent 一次抽取 condition/material/performance
  → 后处理 → 原结构 JSON

与旧版 (v2/v3/v4) 区别：
  - 不再分 condition / material / performance 三个 agent
  - 使用 UnifiedExtractionAgent 一次 LLM 调用完成全部抽取
  - 新增 table_context.py 提取表格块
  - 新增 rule_screening.py 预筛，减少 LLM 调用
  - 新增 postprocess.py 确定性后处理

用法:
  python -m miner.extraction_core.extraction_pipeline_v5 \\
      -i database/type -o miner/json --component all
"""

import os, sys, json, logging
import concurrent.futures
from pathlib import Path
from typing import Dict, List

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from miner.config import create_llm
from miner.cleaning.clean_text1 import clean_text
from miner.extraction_core.material_discovery import MaterialDiscoveryAgent
from miner.extraction_core.pricing import TokenChecker
from miner.extraction_core.errors import LangchainError
from miner.meta_extraction.extract_meta import extract_meta_from_file
from miner.extraction_core.unified_agent import UnifiedExtractionAgent
from miner.extraction_core.table_context import extract_table_contexts
from miner.extraction_core.rule_screening import llm_include_fallback, screen_extraction_unit
from miner.cathode_database.cathode_formatter import CathodeFormatter
from miner.anode_database.anode_formatter import AnodeFormatter
from miner.electrolyte_database.electrolyte_formatter import ElectrolyteFormatter

logger = logging.getLogger("ExtractionPipelineV5")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

COMPONENT_MAP = {"cathode": "cathode", "anode": "anode", "electrolyte": "electrolyte"}

# ==================== Meta 关联 ====================


def load_meta_index() -> Dict[str, dict]:
    """加载 meta_merged.json，建立 doi / 文件名 / stem 的多路索引"""
    mp = _PROJECT_ROOT / "miner" / "json" / "meta_merged.json"
    if not mp.exists():
        logger.warning("meta_merged.json 不存在，跳过 meta 关联")
        return {}

    with open(mp, encoding="utf-8") as f:
        metas = json.load(f)

    idx: Dict[str, dict] = {}
    for m in metas:
        doi = (m.get("doi") or "").strip()
        fp = m.get("file_path") or ""

        if doi:
            doi_as_stem = doi.replace("/", "_")
            idx[doi] = m
            idx[doi_as_stem] = m
            idx[doi_as_stem + ".md"] = m

        if fp:
            fname = os.path.basename(fp)
            stem = os.path.splitext(fname)[0]
            idx[fname] = m
            idx[stem] = m

    logger.info(f"加载 meta 索引: {len(idx)} 条")
    return idx


def find_meta_for_file(meta_lookup: dict, file_path: str) -> dict:
    """多路查找当前文件的 meta"""
    fname = os.path.basename(file_path)
    stem = os.path.splitext(fname)[0]
    doi_from_name = stem.replace("_", "/")

    return (
        meta_lookup.get(fname)
        or meta_lookup.get(stem)
        or meta_lookup.get(doi_from_name)
        or meta_lookup.get(doi_from_name.replace("/", "_"))
        or {}
    )


def build_material_context(meta: dict, component: str, file_stem: str) -> dict:
    """从 meta + 文件名构建 Agent 上下文"""
    mid = file_stem
    doi = (meta.get("doi") if meta else None) or file_stem.replace("_", "/")
    paper_id = doi or file_stem
    title = meta.get("title", "")[:80] if meta else ""
    ctx = f"文献: {title}\nDOI: {doi}\n组件类型: {component}"

    return {
        "paper_id": paper_id,
        "material_id": mid,
        "battery_system_context": ctx,
        "doi": doi,
        "meta_title": title,
        "meta_authors": meta.get("authors", "") if meta else "",
        "meta_year": meta.get("publication_date", "") if meta else "",
    }


# ==================== 文件扫描 ====================


def scan_files(input_root, requested_component="all"):
    """扫描输入路径，返回任务列表"""
    tasks = []
    input_path = Path(input_root)
    if input_path.is_file():
        if input_path.suffix.lower() != ".md":
            return tasks
        parent_comp = input_path.parent.name.lower()
        if requested_component == "all":
            components = [COMPONENT_MAP[parent_comp]] if parent_comp in COMPONENT_MAP else list(COMPONENT_MAP.values())
        else:
            components = [requested_component]
        return [{"file_path": str(input_path), "component": comp} for comp in components]

    for root, dirs, files in os.walk(input_root):
        if any(x in root for x in ["Solid_State", "/test/", "/text_cathode/"]):
            continue
        comp = os.path.basename(root).lower()
        if comp not in COMPONENT_MAP:
            continue
        for f in files:
            if f.lower().endswith(".md"):
                tasks.append({"file_path": os.path.join(root, f), "component": COMPONENT_MAP[comp]})
    return tasks


# ==================== 文本切分 ====================


def _chunk_text(text: str, size: int = 2000) -> List[str]:
    """按字符数切分大段文本，在句号处断句"""
    if len(text) <= size:
        return [text]
    sentences = text.replace("\n", " ").split(". ")
    chunks, buf = [], ""
    for s in sentences:
        cand = (buf + ". " + s).strip()
        if len(cand) > size and buf:
            chunks.append(buf.strip())
            buf = s
        else:
            buf = cand
    if buf:
        chunks.append(buf.strip())
    return chunks


# ==================== Agent 安全调用 ====================


def _safe_invoke(agent, inputs):
    try:
        r = agent.invoke(inputs)
        return r.get("output", {})
    except Exception as e:
        logger.warning(f"Agent invoke failed: {type(e).__name__}: {e}")
        return {}


# ==================== 单文件处理 ====================


def process_file(file_path, agent, component, meta_lookup: dict,
                 include_llm=None, token_checker=None, discovery_agent=None):
    """用 unified agent 处理一篇文献"""
    fname = os.path.basename(file_path)
    file_stem = os.path.splitext(fname)[0]
    logger.info(f"[{component}] {fname}")

    # 1. 表格上下文提取（清洗前）
    table_contexts = extract_table_contexts(file_path)

    # 2. 规则清洗
    try:
        cleaned = clean_text(file_path, min_text_len=200, mode="extract")
        if cleaned is None and not table_contexts:
            return {"file": file_path, "component": component, "error": "text_too_short"}
        if cleaned is None:
            cleaned = ""
    except Exception as e:
        if not table_contexts:
            return {"file": file_path, "component": component, "error": str(e)}
        cleaned = ""

    # 3. 匹配元数据（未命中时从文件现场提取）
    meta = find_meta_for_file(meta_lookup, file_path)
    if not meta:
        try:
            meta = extract_meta_from_file(file_path)
            logger.info(f"  → 未命中索引，从文件现场提取 meta: {meta.get('title','')[:60]}")
        except Exception as e:
            logger.warning(f"  现场提取 meta 失败: {e}")
            meta = {}
    ctx_info = build_material_context(meta, component, file_stem)
    mid = ctx_info["material_id"]
    doi = ctx_info["doi"]

    # 3.5 Phase 0: 材料发现
    materials = [{"name": f"{component} material", "short_name": file_stem,
                  "formula": "", "role": "novel", "material_id": mid}]
    if discovery_agent and cleaned and len(cleaned) > 200:
        try:
            discovered = discovery_agent.discover(cleaned, component, file_stem)
            if discovered:
                materials = discovered
                logger.info(f"  → 发现 {len(materials)} 种材料: "
                            f"{[m.get('short_name', m.get('name', '?')) for m in materials]}")
        except Exception as e:
            logger.warning(f"  Phase 0 材料发现失败: {e}")

    # 将材料信息注入 context
    material_desc = "; ".join(
        f"{m.get('name','?')} ({m.get('formula','')}) [{m.get('role','')}]"
        for m in materials
    )
    title = ctx_info.get("meta_title", "")
    ctx_info["battery_system_context"] = (
        f"文献: {title}\nDOI: {doi}\n组件类型: {component}\n"
        f"材料: {material_desc}"
    )
    ctx_info["materials"] = materials

    # 4. 分段
    paragraphs = [p.strip() for p in cleaned.split("\n\n") if len(p.strip()) > 100]
    if (not paragraphs or (len(paragraphs) == 1 and len(paragraphs[0]) > 3000)) and len(cleaned) > 100:
        paragraphs = _chunk_text(cleaned, 2000)
    paragraphs.extend(table_contexts)

    UnifiedExtractionAgent.reset_counter()
    global_conditions = []
    items = []

    # 5. 逐段处理：规则筛选 → include 兜底 → unified extract
    for para in paragraphs:
        decision = screen_extraction_unit(para, component)
        if decision.action == "include":
            if include_llm is None:
                continue
            try:
                decision = llm_include_fallback(include_llm, para, component, decision, token_checker)
            except Exception as e:
                logger.warning(f"Include fallback failed: {type(e).__name__}: {e}")
                continue
        if decision.action != "extract":
            continue

        item = {"paragraph": para[:200]}
        out = _safe_invoke(agent, {
            "content": para,
            "material_id": mid,
            "battery_system_context": ctx_info["battery_system_context"],
            "doi": doi,
            "known_conditions": global_conditions,
            "focus_tasks": ", ".join(decision.focus_tasks),
        })

        # condition
        conds = out.get("conditions", [])
        global_conditions.extend(conds)
        cid = out.get("condition_id", "")
        if conds:
            item["conditions"] = conds

        # material
        item["property_types"] = out.get("property_types", [])
        item["extracted_info"] = out.get("extracted_info", {})

        # performance
        performance_types = out.get("performance_types", [])
        if performance_types:
            item["performance_types"] = performance_types
            item["performance_info"] = out.get("performance_info", {})
            if cid:
                item["condition_id"] = cid

        if item.get("property_types") or item.get("performance_types") or item.get("conditions"):
            items.append(item)

    # 6. 组装输出
    return {
        "file": file_path,
        "component": component,
        "paper_id": ctx_info.get("paper_id", file_stem),
        "doi": doi,
        "material_id": mid,
        "meta": {k: ctx_info[k] for k in ["meta_title", "meta_authors", "meta_year"] if k in ctx_info},
        "materials": materials,
        "n_materials": len(materials),
        "table_items": table_contexts,
        "n_items": len(items),
        "items": items,
    }


# ==================== CLI 入口 ====================

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="提取流水线 v5: 统一 Agent + 规则筛选 + 表格上下文 + 后处理")
    p.add_argument("-i", "--input", default="database/type",
                   help="输入目录或单个 .md 文件")
    p.add_argument("-o", "--output-dir", default="miner/json",
                   help="输出目录")
    p.add_argument("--limit", type=int, default=0,
                   help="限制处理任务数")
    p.add_argument("--component", default="all",
                   choices=["all", "cathode", "anode", "electrolyte"],
                   help="组件类型")
    p.add_argument("--parallel", type=int, default=5,
                   help="并行处理的任务数（缺省5）")
    p.add_argument("--model-fast", default="classification",
                   help="include 兜底使用的 fast model 类型")
    p.add_argument("--model-pro", default="extraction",
                   help="抽取使用的 pro model 类型")
    args = p.parse_args()

    idir = args.input if os.path.isabs(args.input) else str(_PROJECT_ROOT / args.input)
    odir = args.output_dir if os.path.isabs(args.output_dir) else str(_PROJECT_ROOT / args.output_dir)
    os.makedirs(odir, exist_ok=True)

    inc_llm = create_llm(args.model_fast)
    ext_llm = create_llm(args.model_pro)
    tc = TokenChecker(getattr(inc_llm, "model_name", ""), getattr(ext_llm, "model_name", ""))

    # 加载 meta 索引
    meta_index = load_meta_index()

    # 注册 Agent（Extraction + Discovery）
    agent_registry = {}
    discovery_registry = {}
    formatter_map = {
        "cathode": CathodeFormatter,
        "anode": AnodeFormatter,
        "electrolyte": ElectrolyteFormatter,
    }
    for comp_name in ["cathode", "anode", "electrolyte"]:
        if args.component in ("all", comp_name):
            agent_registry[comp_name] = UnifiedExtractionAgent.from_llm(
                ext_llm, formatter_map[comp_name], comp_name, tc)
            discovery_registry[comp_name] = MaterialDiscoveryAgent.from_llm(ext_llm)

    tasks = scan_files(idir, args.component)
    if args.component != "all":
        tasks = [t for t in tasks if t["component"] == args.component]
    if args.limit > 0:
        tasks = tasks[:args.limit]
    logger.info(f"{len(tasks)} tasks | components={list(agent_registry.keys())}")

    all_results = []
    for i, t in enumerate(tasks, 1):
        logger.info(f"[{i}/{len(tasks)}] {t['component']}: {os.path.basename(t['file_path'])}")
        try:
            res = process_file(
                t["file_path"], agent_registry[t["component"]],
                t["component"], meta_index, inc_llm, tc,
                discovery_agent=discovery_registry.get(t["component"]),
            )
            all_results.append(res)
            base = os.path.splitext(os.path.basename(t["file_path"]))[0]
            out_path = os.path.join(odir, f"{base}_{t['component']}_extracted.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error: {e}")
            all_results.append({"file": t["file_path"], "error": str(e)})

    summary_path = os.path.join(odir, "_pipeline_summary.json")
    unified_calls = UnifiedExtractionAgent.call_count()
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "n_tasks": len(all_results),
            "unified_calls": unified_calls,
            "token_summary": tc.summary(),
            "results": all_results,
        }, f, ensure_ascii=False, indent=2)

    ok = sum(1 for r in all_results if "error" not in r)
    print(f"\n✅ {ok}/{len(tasks)} files → {odir}  (unified calls: {unified_calls})")
