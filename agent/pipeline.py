"""agent pipeline — 主入口，编排 Phase 1 -> 2 -> 3 -> flatten"""

import json
import logging
import os
from pathlib import Path
from typing import List, Dict, Optional

from agent.config import (
    DEFAULT_INPUT_ROOT, DEFAULT_OUTPUT_DIR, META_JSON_PATH,
    MIN_PARAGRAPH_LEN, EXTRACTION_MODEL, EXTRACTION_TEMPERATURE,
    MERGE_MODEL, MERGE_TEMPERATURE,
    SKIP_ZH_DOCS, is_zh_doc,
)
from agent.phase0_discovery import discover as phase0_discover
from agent.phase12_extract import run_sequential as run_phase12
from agent.phase3_merge_agent import run_phase3
from agent.flatten_ml import flatten_to_rows, write_csv, write_json

logger = logging.getLogger("AgentPipeline")


def _create_llm(model: str, temperature: float = 0.1):
    from langchain_openai import ChatOpenAI
    import os
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
    if not api_key:
        try:  # 回退到 miner/config.yaml（与 create_llm 一致）
            from miner.config import load_config
            _cfg_llm = load_config().get("llm", {})
            api_key = _cfg_llm.get("api_key", "")
            base_url = os.getenv("DEEPSEEK_API_BASE", _cfg_llm.get("base_url", base_url))
        except Exception:
            pass
    return ChatOpenAI(api_key=api_key, base_url=base_url, model=model,
                      temperature=temperature, max_tokens=1024, request_timeout=120)


def _clean_and_split(file_path: str) -> Optional[str]:
    try:
        from miner.cleaning.cleaner_v2_standalone import clean_markdown
        return clean_markdown(file_path, min_len=0)
    except Exception as e:
        logger.warning(f"clean failed {file_path}: {e}")
        return None


def _get_paragraphs(cleaned: str) -> List[str]:
    if not cleaned:
        return []
    return [p.strip() for p in cleaned.split("\n\n") if len(p.strip()) > MIN_PARAGRAPH_LEN]


def _infer_component(file_path: str) -> str:
    p = Path(file_path)
    for part in reversed(p.parts):
        if part.lower() in ("cathode", "anode", "electrolyte"):
            return part.lower()
    return "unknown"


def _get_formatter_class(component: str):
    try:
        if component == "cathode":
            from miner.cathode_database.cathode_formatter import CathodeFormatter
            return CathodeFormatter
        elif component == "anode":
            from miner.anode_database.anode_formatter import AnodeFormatter
            return AnodeFormatter
        elif component == "electrolyte":
            from miner.electrolyte_database.electrolyte_formatter import ElectrolyteFormatter
            return ElectrolyteFormatter
    except ImportError:
        pass
    return None


def _load_meta():
    if not META_JSON_PATH.exists():
        return {}
    try:
        with open(META_JSON_PATH, encoding="utf-8") as f:
            metas = json.load(f)
        idx = {}
        for m in metas:
            doi = (m.get("doi") or "").strip()
            if doi:
                idx[doi] = m
                idx[doi.replace("/", "_")] = m
        return idx
    except Exception:
        return {}


def _get_doi(file_path: str) -> str:
    import re
    stem = Path(file_path).stem.replace("_", "/")
    if re.match(r"^10\.\d{4,}/", stem):
        return stem
    return ""


def process_single_file(file_path: str, extract_llm=None, merge_llm=None,
                        meta_lookup: dict = None, output_dir: Path = None) -> Optional[Dict]:
    fname = Path(file_path).name
    logger.info(f"\n{'='*60}\n处理: {fname}\n{'='*60}")

    component = _infer_component(file_path)
    doi = _get_doi(file_path)
    meta = (meta_lookup or {}).get(doi) or {}

    # 清洗
    cleaned = _clean_and_split(file_path)
    if not cleaned:
        return None
    paragraphs = _get_paragraphs(cleaned)
    logger.info(f"段落数: {len(paragraphs)}")
    if not paragraphs:
        return None

    formatter_class = _get_formatter_class(component)

    # ── Phase 0: 全篇材料发现 + 初始条件 ──
    logger.info("--- Phase 0: 材料发现 + 初始条件 ---")
    materials, initial_conds = phase0_discover(extract_llm, cleaned, component, Path(file_path).stem)
    logger.info(f"  材料: {len(materials)}, 初始条件: {len(initial_conds)}")

    # ── Phase 1+2: 合并提取（串行, 每段一次 LLM 调用） ──
    logger.info("--- Phase 1+2: 条件+属性提取 ---")
    gc, ac, ai = run_phase12(llm=extract_llm, paragraphs=paragraphs,
                             component=component, ic=initial_conds,
                             materials=materials)
    p12 = {"global_conditions": gc, "conditioned_properties": ac, "intrinsic_properties": ai}
    if not p12 or not p12.get("global_conditions"):
        return {"file": file_path, "component": component, "doi": doi,
                "global_conditions": [], "materials": []}
    global_conditions = p12["global_conditions"]

    # ── Phase 3: 合并匹配 ──
    logger.info("--- Phase 3: 合并匹配 ---")
    merged = run_phase3(merge_llm=merge_llm,
                        global_conditions=global_conditions,
                        conditioned_properties=p12.get("conditioned_properties",[]),
                        intrinsic_properties=p12.get("intrinsic_properties",[]),
                        doi=doi)

    result = {
        "file": file_path,
        "component": component,
        "doi": doi,
        "title": meta.get("title", ""),
        "global_conditions": global_conditions,
        "materials": merged.get("materials", []),
        "discovered_materials": materials,
    }

    # 写单文件结果
    if output_dir:
        stem = Path(file_path).stem
        comp_dir = output_dir / component
        comp_dir.mkdir(parents=True, exist_ok=True)
        write_json([result], comp_dir / f"{stem}_agent.json")
        cond_rows, intr_rows = flatten_to_rows(result)
        if cond_rows:
            from agent.flatten_ml import _write_csv_rows, FLATTEN_CONDITIONED_HEADERS
            _write_csv_rows(cond_rows, FLATTEN_CONDITIONED_HEADERS,
                           comp_dir / f"{stem}_conditioned.csv")
        if intr_rows:
            from agent.flatten_ml import _write_csv_rows, FLATTEN_INTRINSIC_HEADERS
            _write_csv_rows(intr_rows, FLATTEN_INTRINSIC_HEADERS,
                           comp_dir / f"{stem}_intrinsic.csv")

    return result


def _scan_files(input_root: str, component_filter: str = "all") -> List[Dict]:
    """扫描文件，确保同一篇论文只处理一次（按文件名去重）"""
    seen = set()
    tasks = []
    root = Path(input_root)

    if root.is_file():
        if SKIP_ZH_DOCS and is_zh_doc(str(root)):
            logger.info(f"[跳过中文文献] {root.name}")
            return tasks
        stem = root.stem
        comp = _infer_component(str(root))
        if component_filter in ("all", comp):
            tasks.append({"file_path": str(root), "component": comp})
        return tasks

    for root_dir, dirs, files in os.walk(input_root):
        if any(x in root_dir for x in ["Solid_State", "test", "text_cathode"]):
            continue
        dir_basename = os.path.basename(root_dir)
        if dir_basename not in ("cathode", "anode", "electrolyte"):
            continue
        if component_filter != "all" and dir_basename != component_filter:
            continue
        for f in files:
            if not f.endswith(".md"):
                continue
            fp = os.path.join(root_dir, f)
            if SKIP_ZH_DOCS and is_zh_doc(fp):
                continue
            stem = f.replace(".md", "")
            if stem not in seen:
                seen.add(stem)
                tasks.append({
                    "file_path": fp,
                    "component": dir_basename,
                })
    return tasks


def run_pipeline(input_root: str = None, output_dir: str = None,
                 component: str = "all", extract_model: str = None,
                 merge_model: str = None, max_files: int = None) -> List[Dict]:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    input_root = input_root or str(DEFAULT_INPUT_ROOT)
    out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    extract_model = extract_model or EXTRACTION_MODEL
    merge_model = merge_model or MERGE_MODEL

    logger.info(f"提取: {extract_model}, 合并: {merge_model}")
    extract_llm = _create_llm(extract_model, EXTRACTION_TEMPERATURE)
    merge_llm = _create_llm(merge_model, MERGE_TEMPERATURE)

    meta_lookup = _load_meta()
    tasks = _scan_files(input_root, component)
    logger.info(f"文件: {len(tasks)}")

    if max_files:
        tasks = tasks[:max_files]

    all_results = []
    for task in tasks:
        try:
            result = process_single_file(
                file_path=task["file_path"],
                extract_llm=extract_llm,
                merge_llm=merge_llm,
                meta_lookup=meta_lookup,
                output_dir=out_dir,
            )
            if result and result.get("materials"):
                all_results.append(result)
        except Exception as e:
            logger.error(f"处理失败 {task['file_path']}: {e}")
            continue

    if all_results:
        write_csv(all_results, out_dir)
        write_json(all_results, out_dir / "_all_merged.json")

        total_cond = sum(len(flatten_to_rows(r)[0]) for r in all_results)
        total_intr = sum(len(flatten_to_rows(r)[1]) for r in all_results)
        logger.info(f"\n完成: {len(all_results)} 篇, 条件属性 {total_cond}, 本征属性 {total_intr}")

    return all_results


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="agent 三阶段提取管道")
    p.add_argument("-i", dest="input_root", help="输入路径")
    p.add_argument("-o", dest="output_dir", help="输出目录")
    p.add_argument("-c", "--component", default="all",
                   choices=["all", "cathode", "anode", "electrolyte"])
    p.add_argument("--extract-model", help="提取模型")
    p.add_argument("--merge-model", help="合并模型")
    p.add_argument("--max-files", type=int, help="最大文件数")
    args = p.parse_args()
    run_pipeline(**{k: v for k, v in vars(args).items() if v is not None})
