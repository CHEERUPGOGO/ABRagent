# -*- coding: utf-8 -*-
"""提取流水线 v7 — 并行 + 标签聚焦提取 (Label-Aware Unified Agent)

v7 相对 v6 的改动：
  - Flash include 从 YES/NO 改为标签选择（返回具体 field 名）
  - Pro 抽取 prompt 只包含 Flash 选中的标签字段（减小 prompt、提高精度）
  - 保留 v6 的全部特性：并行段落抽取、增量 resume、后合并 condition_id

用法:
  python -m miner.extraction_core.extraction_pipeline_v7 \\
      -i database/type -o miner/json --component all --resume
"""

import os, sys, json, logging
from datetime import datetime
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
from miner.extraction_core.rule_screening import screen_extraction_unit, ScreeningDecision
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


# ==================== v7: 标签选择器 ====================


def llm_label_selector(llm, text: str, component: str,
                       label_candidates: str, token_checker=None) -> List[str]:
    """用 fast LLM 从候选标签中选出与段落相关的具体标签名

    Args:
        llm: ChatOpenAI 实例（fast model）
        text: 段落文本
        component: 组件类型
        label_candidates: 候选标签列表，每行 "- 标签名: 说明"
        token_checker: Token 计数器（可选）

    Returns:
        选出的标签名列表；失败返回 [\"all\"] 降级
    """
    if not label_candidates:
        return ["all"]

    prompt = (
        f"你是一个锂电池 {component} 文献筛选助手。"
        f"阅读以下段落，判断它涉及了下面列表中的哪些标签。\n"
        f"只返回涉及的标签名称的 JSON 数组。如果都不相关，返回 []。\n\n"
        f"可用标签：\n{label_candidates}\n\n"
        f"输出格式：[\"Label1\", \"Label2\"]\n\n"
        f"段落内容：\n\n{text[:2000]}"
    )

    try:
        response = llm.invoke(prompt)
        raw = response.content if hasattr(response, "content") else str(response)
        raw_clean = raw.strip().replace("```json", "").replace("```", "").strip()
        labels = json.loads(raw_clean) if raw_clean.startswith("[") else []
        if not isinstance(labels, list):
            labels = []
        labels = [str(l).strip() for l in labels if l]
        if token_checker:
            token_checker.record(f"label-select-{component}", prompt, raw,
                                 "labels" if labels else "skip")
        return labels if labels else ["all"]
    except Exception as e:
        logger.warning(f"Label selector failed: {type(e).__name__}: {e}")
        return ["all"]


# ==================== v7: 标签聚焦 Agent ====================


class UnifiedExtractionAgentV7(UnifiedExtractionAgent):
    """v7 版：prompt 只包含 Flash 选中的标签字段"""

    def _build_prompt_kwargs(self, inputs: dict) -> dict:
        """重写：从 focus_tasks 解析具体标签名，只保留匹配的字段"""
        formatter = self.formatter_class()
        focus_raw = inputs.get("focus_tasks", "all")
        focus_set = set(l.strip() for l in focus_raw.split(",") if l.strip())

        # if "all" still in focus_set, delegate to parent for full list
        if "all" in focus_set or not focus_set:
            return super()._build_prompt_kwargs(inputs)

        def _filter_focus(keys, explanations):
            result = []
            for k in keys:
                if k in focus_set:
                    try:
                        expl = explanations.get(k, "")
                        result.append(f"- {k}: {expl[:200]}")
                    except Exception:
                        result.append(f"- {k}")
            return result

        def _filter_struct(keys, struct_data):
            result = []
            for k in keys:
                if k in focus_set:
                    try:
                        sd = struct_data.get(k, "")
                        if sd:
                            result.append(f"  {k}: {sd[:300]}")
                    except Exception:
                        pass
            return result

        condition_keys = []
        if hasattr(formatter, "condition_keys"):
            for k in formatter.condition_keys():
                try:
                    expl = getattr(formatter, "explanation", {}).get(k, "")
                    condition_keys.append(f"- {k}: {expl[:200]}")
                except Exception:
                    condition_keys.append(f"- {k}")

        material_labels = _filter_focus(formatter.material_keys(),
                                        formatter.material_explanation)
        material_struct_lines = _filter_struct(formatter.material_keys(),
                                               formatter.material_structured_data)
        perf_labels = _filter_focus(formatter.performance_keys(),
                                    formatter.perf_explanation)
        perf_struct_lines = _filter_struct(formatter.performance_keys(),
                                           formatter.perf_structured_data)

        material_struct_block = (
            "Material property format (structured_data):\n"
            + "\n".join(material_struct_lines)
            if material_struct_lines else ""
        )
        perf_struct_block = (
            "Performance format (structured_data):\n"
            + "\n".join(perf_struct_lines)
            if perf_struct_lines else ""
        )
        focus_inst = (
            f"\n## Focus\nExtract ONLY the following labels: {focus_raw}"
        )

        return {
            "component": self.component,
            "material_id": inputs.get("material_id", ""),
            "battery_system_context": inputs.get("battery_system_context", ""),
            "doi": inputs.get("doi", ""),
            "known_conditions": json.dumps(inputs.get("known_conditions", []),
                                           ensure_ascii=False),
            "focus_tasks": focus_raw,
            "focus_instruction": focus_inst,
            "condition_fields": "\n".join(condition_keys) if condition_keys
                else "- temperature, c_rate, current_density, voltage_range, ...",
            "material_fields": "\n".join(material_labels) if material_labels
                else "（no relevant material property labels in focus set）",
            "material_structured_data": material_struct_block,
            "performance_fields": "\n".join(perf_labels) if perf_labels
                else "（no relevant performance labels in focus set）",
            "performance_structured_data": perf_struct_block,
            "content": inputs.get("content", ""),
        }


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

    from concurrent.futures import ThreadPoolExecutor, as_completed

    UnifiedExtractionAgentV7.reset_counter()
    items = []

    # ── 构建标签候选列表（从 agent 的 formatter） ──
    fmt = agent.formatter_class()
    label_lines = []
    if hasattr(fmt, "material_explanation"):
        for k in fmt.material_keys():
            try:
                expl = fmt.material_explanation.get(k, "")[:120]
                label_lines.append(f"- {k}: {expl}")
            except Exception:
                label_lines.append(f"- {k}")
    if hasattr(fmt, "perf_explanation"):
        for k in fmt.performance_keys():
            try:
                expl = fmt.perf_explanation.get(k, "")[:120]
                label_lines.append(f"- {k}: {expl}")
            except Exception:
                label_lines.append(f"- {k}")
    label_candidates = "\n".join(label_lines)

    # ── Phase 1: 段落预筛选（串行，规则 + 标签选择） ──
    screened = []  # [(para, decision), ...]
    for para in paragraphs:
        decision = screen_extraction_unit(para, component)
        if decision.action == "include":
            if include_llm is None:
                continue
            try:
                selected = llm_label_selector(include_llm, para, component,
                                              label_candidates, token_checker)
                if not selected or selected == ["all"]:
                    continue
                decision = ScreeningDecision("extract", 0.7, selected,
                                             f"Labels: {','.join(selected[:5])}")
            except Exception as e:
                logger.warning(f"Label selector failed: {type(e).__name__}: {e}")
                continue
        elif decision.action == "extract":
            # 规则判 extract 的段落，也用 Flash 做一次标签聚焦
            if include_llm is not None:
                try:
                    selected = llm_label_selector(include_llm, para, component,
                                                  label_candidates, token_checker)
                    if selected and selected != ["all"]:
                        decision = ScreeningDecision("extract", 0.85, selected,
                                                     f"rule+Labels: {','.join(selected[:5])}")
                except Exception:
                    pass  # 失败保持原有 extract + ["all"]
        if decision.action == "extract":
            screened.append((para, decision))

    # ── Phase 2: 并行抽取（各段落独立，去掉了 global_conditions 串联） ──
    with ThreadPoolExecutor(max_workers=8) as exe:
        futures = {}
        for para, decision in screened:
            fut = exe.submit(_safe_invoke, agent, {
                "content": para,
                "material_id": mid,
                "battery_system_context": ctx_info["battery_system_context"],
                "doi": doi,
                "known_conditions": [],
                "focus_tasks": ", ".join(decision.focus_tasks),
            })
            futures[fut] = (para, decision)

        for fut in as_completed(futures):
            para, decision = futures[fut]
            out = fut.result()
            if not out:
                continue

            item = {"paragraph": para[:200]}

            # condition
            conds = out.get("conditions", [])
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

    # ── Phase 3: 后合并 — 按参数字段值去重 condition_id ──
    seen_conds = {}
    for item in items:
        for c in item.get("conditions", []):
            key = tuple(
                sorted((k, str(v)) for k, v in c.items()
                       if k not in ("condition_id", "material_id", "battery_system_context"))
            )
            if key in seen_conds:
                c["condition_id"] = seen_conds[key]
            else:
                new_id = f"C{len(seen_conds) + 1:03d}"
                c["condition_id"] = new_id
                seen_conds[key] = new_id

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
    p.add_argument("--resume", action="store_true",
                   help="增量模式：跳过输出目录中已存在的 JSON 文件")
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
            agent_registry[comp_name] = UnifiedExtractionAgentV7.from_llm(
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
        base = os.path.splitext(os.path.basename(t["file_path"]))[0]
        out_path = os.path.join(odir, f"{base}_{t['component']}_extracted.json")
        if args.resume and os.path.exists(out_path):
            logger.info(f"[skip] {base}: 已存在")
            continue
        logger.info(f"[{i}/{len(tasks)}] {t['component']}: {os.path.basename(t['file_path'])}")
        try:
            res = process_file(
                t["file_path"], agent_registry[t["component"]],
                t["component"], meta_index, inc_llm, tc,
                discovery_agent=discovery_registry.get(t["component"]),
            )
            all_results.append(res)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error: {e}")
            all_results.append({"file": t["file_path"], "error": str(e)})

    summary_data = {
        "n_tasks": len(all_results),
        "unified_calls": UnifiedExtractionAgent.call_count(),
        "token_summary": tc.summary(),
        "results": all_results,
    }
    summary_path = os.path.join(odir, "_pipeline_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)

    # 时间戳快照副本
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = os.path.join(odir, f"_pipeline_summary_{ts}.json")
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)

    ok = sum(1 for r in all_results if "error" not in r)
    uc_val = summary_data.get("unified_calls", 0)
    print(f"\n✅ {ok}/{len(tasks)} files → {odir}  (unified calls: {uc_val})")
