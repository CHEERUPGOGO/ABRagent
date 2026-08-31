# -*- coding: utf-8 -*-
"""提取流水线 v3 — 并行版：Phase 0(材料识别) → 全局条件 → 按材料并行处理 → TableAgent

与 v2 的区别：
- 材料间使用 ThreadPoolExecutor 并行处理（借鉴 *_agent_test.py）
- 内联 Material/Performance/Condition Agent（简洁自包含）
- ConditionAgentV2 仅用于全局条件提取后分路
- 支持 cathode/anode/electrolyte 组件感知
"""

import os, sys, json, logging, re
from pathlib import Path
from typing import Any, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from langchain_classic.chains.base import Chain
from langchain_classic.chains.llm import LLMChain
from langchain_core.prompts import PromptTemplate
from langchain_core.callbacks.manager import CallbackManagerForChainRun

from miner.config import create_llm
from miner.cleaning.structured_clean import structured_clean
from miner.extraction_core.pricing import TokenChecker
from miner.extraction_core.material_discovery import MaterialDiscoveryAgent
from miner.extraction_core.base_agent_v2 import BaseAgentV2
from miner.extraction_core.table_agent_v2 import TableAgentV2
from miner.extraction_core.utils import fix_json_escape

# ── Formatter ──
from miner.cathode_database.cathode_formatter import CathodeFormatter
from miner.anode_database.anode_formatter import AnodeFormatter
from miner.electrolyte_database.electrolyte_formatter import ElectrolyteFormatter

# ── Prompts ──
try:
    from miner.cathode_database.cathode_prompts import (
        PROMPT_MATERIAL_INCLUDE, PROMPT_MATERIAL_EXTRACT,
        PROMPT_PERFORMANCE_INCLUDE, PROMPT_PERFORMANCE_EXTRACT,
    )
except ImportError:
    from miner.cathode_database.cathode_prompts1 import (
        PROMPT_CATHODE_MATERIAL_INCLUDE as PROMPT_MATERIAL_INCLUDE,
        PROMPT_CATHODE_MATERIAL_EXTRACT as PROMPT_MATERIAL_EXTRACT,
        PROMPT_CATHODE_PERFORMANCE_INCLUDE as PROMPT_PERFORMANCE_INCLUDE,
        PROMPT_CATHODE_PERFORMANCE_EXTRACT as PROMPT_PERFORMANCE_EXTRACT,
    )
from miner.anode_database.anode_prompts1 import (
    PROMPT_ANODE_MATERIAL_INCLUDE, PROMPT_ANODE_MATERIAL_EXTRACT,
    PROMPT_ANODE_PERFORMANCE_INCLUDE, PROMPT_ANODE_PERFORMANCE_EXTRACT,
)
from miner.electrolyte_database.electrolyte_prompts1 import (
    PROMPT_ELECTROLYTE_MATERIAL_INCLUDE, PROMPT_ELECTROLYTE_MATERIAL_EXTRACT,
    PROMPT_ELECTROLYTE_PERFORMANCE_INCLUDE, PROMPT_ELECTROLYTE_PERFORMANCE_EXTRACT,
)

logger = logging.getLogger("ExtractionPipelineV3")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

COMPONENT_MAP = {"cathode": "cathode", "anode": "anode", "electrolyte": "electrolyte"}

# ==================== 组件配置表 ====================

_COMPONENT_CFG = {
    "cathode": {
        "formatter": CathodeFormatter,
        "prompts": {
            "material_include": PROMPT_MATERIAL_INCLUDE,
            "material_extract": PROMPT_MATERIAL_EXTRACT,
            "perf_include": PROMPT_PERFORMANCE_INCLUDE,
            "perf_extract": PROMPT_PERFORMANCE_EXTRACT,
        }
    },
    "anode": {
        "formatter": AnodeFormatter,
        "prompts": {
            "material_include": PROMPT_ANODE_MATERIAL_INCLUDE,
            "material_extract": PROMPT_ANODE_MATERIAL_EXTRACT,
            "perf_include": PROMPT_ANODE_PERFORMANCE_INCLUDE,
            "perf_extract": PROMPT_ANODE_PERFORMANCE_EXTRACT,
        }
    },
    "electrolyte": {
        "formatter": ElectrolyteFormatter,
        "prompts": {
            "material_include": PROMPT_ELECTROLYTE_MATERIAL_INCLUDE,
            "material_extract": PROMPT_ELECTROLYTE_MATERIAL_EXTRACT,
            "perf_include": PROMPT_ELECTROLYTE_PERFORMANCE_INCLUDE,
            "perf_extract": PROMPT_ELECTROLYTE_PERFORMANCE_EXTRACT,
        }
    },
}

_COMPONENT_CTX = {
    "cathode": "cathode",
    "anode": "lithium metal anode",
    "electrolyte": "electrolyte",
}


# ==================== 内联 Agent（复用 _agent_test.py 模式）====================

def _build_material_agent_cls(Formatter):
    """返回一个 MaterialAgent 类（基于 BaseAgentV2，material_* 视图）。"""
    class MatAgent(BaseAgentV2):
        formatter: Any = None
        def _call(self, inputs, rm=None):
            from miner.extraction_core.errors import LangchainError
            _rm = rm or CallbackManagerForChainRun.get_noop_manager()
            _rm.get_child()
            content = str(inputs.get(self.input_key, ""))
            mid = inputs.get("material_id", "")
            ctx = inputs.get("battery_system_context", "")
            cid = inputs.get("condition_id", "")
            doi = inputs.get("doi", "")
            base = {"content": content, "material_id": mid,
                    "battery_system_context": ctx, "property_types": [],
                    "extracted_info": {}, "doi": doi}
            s = self.formatter
            self.formatter = type("MV", (), {
                "explanation": s.material_explanation,
                "structured_data": s.material_structured_data,
                "information": s.material_information,
                "example_text": s.material_example_text,
            })()
            try:
                return super()._call(inputs, _rm)
            finally:
                self.formatter = s
    return MatAgent


def _build_perf_agent_cls(Formatter):
    """返回一个 PerformanceAgent 类（基于 BaseAgentV2，perf_* 视图，输出performance_types）。"""
    class PerfAgent(BaseAgentV2):
        formatter: Any = None
        base_name: str = "perf-v3"
        def _call(self, inputs, rm=None):
            from miner.extraction_core.errors import LangchainError
            _rm = rm or CallbackManagerForChainRun.get_noop_manager()
            _rm.get_child()
            content = str(inputs.get(self.input_key, ""))
            mid = inputs.get("material_id", "")
            ctx = inputs.get("battery_system_context", "")
            cid = inputs.get("condition_id", "")
            doi = inputs.get("doi", "")
            base = {"content": content, "material_id": mid,
                    "battery_system_context": ctx, "performance_types": [],
                    "extracted_info": {}, "doi": doi}
            s = self.formatter
            self.formatter = type("PV", (), {
                "explanation": s.perf_explanation,
                "structured_data": s.perf_structured_data,
                "information": s.perf_information,
                "example_text": s.perf_example_text,
            })()
            try:
                return super()._call(inputs, _rm)
            finally:
                self.formatter = s
    return PerfAgent


# ==================== Meta ====================

def load_meta_index() -> Dict[str, dict]:
    mp = _PROJECT_ROOT / "miner" / "json" / "meta_merged.json"
    if not mp.exists():
        logger.warning("meta_merged.json 不存在")
        return {}
    with open(mp, encoding="utf-8") as f:
        metas = json.load(f)
    idx = {}
    for m in metas:
        doi = m.get("doi", "")
        fp = m.get("file_path", "")
        if doi:
            idx[doi] = m
            idx[doi.replace("/", "_")] = m
            idx[doi.replace("/", "_") + ".md"] = m
        if fp:
            fname = os.path.basename(fp)
            stem = os.path.splitext(fname)[0]
            idx[fname] = m
            idx[stem] = m
    return idx


def find_meta(meta_lookup: dict, file_path: str) -> dict:
    fname = os.path.basename(file_path)
    stem = os.path.splitext(fname)[0]
    doi = stem.replace("_", "/")
    return (meta_lookup.get(fname) or meta_lookup.get(stem)
            or meta_lookup.get(doi) or meta_lookup.get(doi.replace("/", "_")) or {})


def build_paper_ctx(meta: dict, component: str, file_stem: str) -> dict:
    doi = (meta.get("doi") if meta else None) or file_stem.replace("_", "/")
    return {
        "paper_id": doi or file_stem, "doi": doi,
        "meta_title": (meta.get("title", "")[:80] if meta else ""),
        "meta_authors": (meta.get("authors", "") if meta else ""),
        "meta_year": (meta.get("publication_date", "") if meta else ""),
        "component": component, "file_stem": file_stem,
    }


# ==================== 辅助 ====================

def _safe_invoke(agent, inputs):
    try:
        r = agent.invoke(inputs)
        return r.get("output", {})
    except Exception as e:
        logger.warning(f"Agent invoke failed: {type(e).__name__}: {e}")
        return {}


def _chunk_text(text: str, size: int = 2000) -> List[str]:
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


def _parse_extract(output: str) -> Dict:
    """多级 JSON fallback，同 PerformanceAgentV2。"""
    output = output.replace("```JSON", "").replace("```json", "").replace("```", "").strip()
    if re.search(r"[Ii] do not know", output, re.IGNORECASE):
        return {}
    output = fix_json_escape(output)
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pass
    for pat in [r"^[Oo]kay[,.]?.*?\{", r"^[Hh]ere['s]?.*?\{"]:
        if re.search(pat, output, re.DOTALL):
            m = re.search(r"(\{.*\})", output, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    pass
            break
    try:
        m = re.search(r"(\{(?:[^{}]|(?R))*\})", output, re.DOTALL)
        if m:
            return json.loads(m.group(1))
    except json.JSONDecodeError:
        pass
    return {}


def _expand_table_items(all_items, table_output, ti):
    ei = table_output.get("extracted_info", {})
    pi = table_output.get("performance_info", {})
    if not isinstance(ei, dict):
        ei = {}
    if not isinstance(pi, dict):
        pi = {}
    max_len = 0
    for val in list(ei.values()) + list(pi.values()):
        if isinstance(val, list):
            max_len = max(max_len, len(val))
        elif isinstance(val, dict):
            max_len = max(max_len, 1)
    for row_idx in range(max_len):
        item = {"paragraph": f"table_{ti+1}_row_{row_idx+1}", "source": "table"}
        for prop, val in ei.items():
            if isinstance(val, list) and row_idx < len(val):
                item.setdefault("property_types", []).append(prop)
                item.setdefault("extracted_info", {})[prop] = val[row_idx]
            elif isinstance(val, dict) and row_idx == 0:
                item.setdefault("property_types", []).append(prop)
                item.setdefault("extracted_info", {})[prop] = val
        for prop, val in pi.items():
            if isinstance(val, list) and row_idx < len(val):
                item.setdefault("performance_types", []).append(prop)
                item.setdefault("performance_info", {})[prop] = val[row_idx]
        if item.get("property_types") or item.get("performance_types"):
            all_items.append(item)


def _merge_results(all_items) -> tuple:
    merged_info, merged_perf = {}, {}
    for item in all_items:
        src = item.get("source", "text")

        def _into(dest, sd, sl):
            if not isinstance(sd, dict):
                return
            for prop, val in sd.items():
                entries = []
                if isinstance(val, list):
                    entries = [{**v, "source": sl} for v in val if isinstance(v, dict)]
                elif isinstance(val, dict):
                    v = dict(val)
                    v.setdefault("condition", "")
                    entries = [{**v, "source": sl}]
                if prop not in dest:
                    dest[prop] = []
                dest[prop].extend(entries)

        _into(merged_info, item.get("extracted_info", {}), src)
        _into(merged_perf, item.get("performance_info", {}), src)
    return merged_info, merged_perf


# ==================== 组件工厂 ====================

def _make_agents_for(comp: str, inc_llm, ext_llm, tc):
    """为指定组件创建 MaterialAgent / PerformanceAgent / TableAgent。"""
    cfg = _COMPONENT_CFG[comp]
    fmt = cfg["formatter"]
    prompts = cfg["prompts"]

    MatCls = _build_material_agent_cls(fmt)
    mat_agent = MatCls.from_llm(inc_llm, ext_llm, tc,
        prompt_include=prompts["material_include"],
        prompt_extract=prompts["material_extract"],
        base_name=f"{comp}-material-v3")
    mat_agent.formatter = fmt

    PerfCls = _build_perf_agent_cls(fmt)
    perf_agent = PerfCls.from_llm(inc_llm, ext_llm, tc,
        prompt_include=prompts["perf_include"],
        prompt_extract=prompts["perf_extract"],
        base_name=f"{comp}-perf-v3")
    perf_agent.formatter = fmt

    table_agent = TableAgentV2.from_llm(inc_llm, ext_llm, component=comp, formatter=fmt)

    return mat_agent, perf_agent, table_agent


# ==================== 单材料处理函数（供线程池调用）====================

def _process_one_material(mat, idx, paragraphs, doi, discovery_agent, cond_agent,
                          mat_agent, perf_agent, table_agent, component, doc):
    """处理一个材料（独立函数，供 ThreadPoolExecutor 调用）。"""
    mid = mat["material_id"]
    ctx = (
        f"文献: {doi}\n"
        f"组件类型: {component}\n"
        f"当前材料: {mat['name']} ({mat['role']})"
    )
    logger.info(f"  [并行] 材料 {idx+1}: {mat['name']} [{mid}]")

    all_items = []
    for p in paragraphs:
        item = {"paragraph": p[:200], "source": "text"}

        # Condition (使用全局 cond_agent)
        cid = ""
        co = _safe_invoke(cond_agent, {
            "content": p, "material_id": mid,
            "battery_system_context": ctx, "doi": doi,
            "known_conditions": [],
        })
        conds = co.get("extracted_conditions", [])
        cids = [c.get("condition_id", "") for c in conds if c.get("condition_id")]
        cid = cids[0] if cids else ""
        if conds:
            item["conditions"] = conds

        # Material
        mo = _safe_invoke(mat_agent, {
            "content": p, "material_id": mid,
            "battery_system_context": ctx, "doi": doi,
        })
        item["property_types"] = mo.get("property_types", [])
        item["extracted_info"] = mo.get("extracted_info", {})

        # Performance
        po = _safe_invoke(perf_agent, {
            "content": p, "material_id": mid,
            "battery_system_context": ctx,
            "condition_id": cid, "doi": doi,
        })
        item["performance_types"] = po.get("performance_types", [])
        item["performance_info"] = po.get("extracted_info", {})
        if cid:
            item["condition_id"] = cid

        if item.get("property_types") or item.get("performance_types") or item.get("conditions"):
            all_items.append(item)

    # 表格
    if table_agent and doc.tables:
        for ti, tbl in enumerate(doc.tables):
            to = _safe_invoke(table_agent, {
                "content": tbl, "material_id": mid,
                "doi": doi, "material_context": f"{mat['name']} ({mat.get('short_name', '')})",
            })
            if to and not to.get("skipped") and (
                to.get("property_types") or to.get("performance_types")):
                _expand_table_items(all_items, to, ti)

    merged_info, merged_perf = _merge_results(all_items)

    return {
        "material_id": mid,
        "material_idx": idx,
        "name": mat["name"],
        "short_name": mat.get("short_name", ""),
        "formula": mat.get("formula", ""),
        "role": mat.get("role", ""),
        "description": mat.get("description", ""),
        "n_items": len(all_items),
        "items": all_items,
        "merged": {
            "extracted_info": merged_info,
            "performance_info": merged_perf,
        },
    }


# ==================== 主流程 ====================

def process_file(file_path, agents, component, meta_lookup: dict, max_workers: int = 4):
    """
    agents: {"discovery": ..., "cond": ..., "mat": ..., "perf": ..., "table": ...}
    max_workers: 并行材料数（默认 4）
    """
    fname = os.path.basename(file_path)
    file_stem = os.path.splitext(fname)[0]
    logger.info(f"[{component}] {fname}")

    # 1. 清洗
    try:
        doc = structured_clean(file_path, min_text_len=200, mode="extract")
        if doc is None:
            return {"file": file_path, "component": component, "error": "text_too_short"}
    except Exception as e:
        return {"file": file_path, "component": component, "error": str(e)}

    # 2. 元数据
    meta = doc.meta if doc.meta else {}
    if not meta.get("title"):
        meta.update({k: v for k, v in find_meta(meta_lookup, file_path).items() if v and not meta.get(k)})
    paper_ctx = build_paper_ctx(meta, component, file_stem)
    doi = paper_ctx["doi"]

    # 3. 段落切分
    paragraphs = [p.strip() for p in doc.texts if len(p.strip()) > 100]
    chunked = []
    for p in paragraphs:
        if len(p) > 3000:
            chunked.extend(_chunk_text(p, 2000))
        else:
            chunked.append(p)
    paragraphs = chunked or paragraphs

    discovery_agent = agents.get("discovery")
    cond_agent = agents.get("cond")
    mat_agent = agents.get("mat")
    perf_agent = agents.get("perf")
    table_agent = agents.get("table")

    # 4. Phase 0: 材料识别
    if discovery_agent and doc.clean_text:
        raw_text = doc.clean_text[:50000] if len(doc.clean_text) > 50000 else doc.clean_text
        materials = discovery_agent.discover(raw_text, component, file_stem)
    else:
        materials = [{"name": f"{component} material", "short_name": file_stem,
                      "formula": "", "role": "novel", "description": "",
                      "material_id": file_stem}]

    if not materials:
        logger.info(f"  未识别出材料，跳过")
        return {"file": file_path, "component": component, "paper": paper_ctx,
                "materials": [], "n_items": 0,
                "n_tables": len(doc.tables) if doc.tables else 0,
                "n_figures": len(doc.figures) if doc.figures else 0}

    logger.info(f"  识别到 {len(materials)} 种材料: {[m.get('short_name', m.get('name', '?')) for m in materials]}")

    # 5. 并行材料处理
    results = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(materials))) as pool:
        futures = {
            pool.submit(_process_one_material, m, i, paragraphs, doi,
                        discovery_agent, cond_agent, mat_agent, perf_agent,
                        table_agent, component, doc): i
            for i, m in enumerate(materials)
        }
        for f in as_completed(futures):
            r = f.result()
            results.append(r)

    # 按原始材料顺序排列
    results.sort(key=lambda x: x["material_idx"])

    return {
        "file": file_path,
        "component": component,
        "paper": paper_ctx,
        "n_materials": len(results),
        "materials": results,
        "n_tables": len(doc.tables) if doc.tables else 0,
        "n_figures": len(doc.figures) if doc.figures else 0,
    }


# ==================== Agent 注册表 ====================

def build_agent_registry(inc_llm, ext_llm, tc, components=None):
    if components is None:
        components = ["cathode", "anode", "electrolyte"]

    discovery_agent = MaterialDiscoveryAgent.from_llm(inc_llm)
    registry = {}

    for comp in components:
        mat_agent, perf_agent, table_agent = _make_agents_for(comp, inc_llm, ext_llm, tc)

        # ConditionAgentV2 — 按组件创建，但共享同一个 inc/ext
        try:
            from miner.extraction_core.condition_agent_v2 import ConditionAgentV2
            cond_agent = ConditionAgentV2.from_llm(inc_llm, ext_llm, tc)
        except Exception:
            # 降级：用内联 ConditionAgent
            cond_agent = _make_inline_cond_agent(comp, inc_llm, ext_llm, tc)

        registry[comp] = {
            "discovery": discovery_agent,
            "cond": cond_agent,
            "mat": mat_agent,
            "perf": perf_agent,
            "table": table_agent,
        }

    return registry


def _make_inline_cond_agent(comp, inc_llm, ext_llm, tc):
    """降级：创建内联 ConditionAgent。"""
    from langchain_classic.chains.llm import LLMChain
    from langchain_core.prompts import PromptTemplate
    from miner.extraction_core.errors import LangchainError

    INC_PROMPT = PromptTemplate(
        input_variables=["battery_system_context", "material_id", "paragraph"],
        template="""Determine if the paragraph describes test conditions for {material_id}.
Context: {battery_system_context}
Paragraph: {paragraph}
If yes, list relevant parameter names as a JSON array. If no, return [].
JSON:""")

    EXT_PROMPT = PromptTemplate(
        input_variables=["battery_system_context", "material_id", "known_conditions_summary", "paragraph"],
        template="""Extract test conditions from the paragraph.
Material: {material_id} | Context: {battery_system_context}
Known conditions: {known_conditions_summary}
Output JSON: {{"temperature": {{"value": ..., "unit": "C"}}, "c_rate_or_current": {{"value": ..., "unit": ""}},
  "voltage_range": {{"min": ..., "max": ..., "unit": "V"}}, "electrolyte": "...",
  "cycle_number": ..., "electrode_config": "...", "source_text": "..."}}
Paragraph: {paragraph}
JSON:""")

    class InlineCondAgent(Chain):
        include_chain: LLMChain = LLMChain(llm=inc_llm, prompt=INC_PROMPT)
        extract_chain: LLMChain = LLMChain(llm=ext_llm, prompt=EXT_PROMPT)
        input_key: str = "content"
        output_key: str = "output"

        @property
        def input_keys(self): return [self.input_key]
        @property
        def output_keys(self): return [self.output_key]

        def _call(self, inputs, run_manager=None):
            return {"output": {"extracted_conditions": []}}

    return InlineCondAgent()


# ==================== CLI ====================

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="v3 并行提取流水线")
    p.add_argument("-i", "--input", default="database/type")
    p.add_argument("-o", "--output-dir", default="miner/json")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--component", default="all", choices=["all", "cathode", "anode", "electrolyte"])
    p.add_argument("--workers", type=int, default=4, help="并行材料线程数")
    p.add_argument("--model-fast", default="classification")
    p.add_argument("--model-pro", default="extraction")
    args = p.parse_args()

    idir = args.input if os.path.isabs(args.input) else str(_PROJECT_ROOT / args.input)
    odir = args.output_dir if os.path.isabs(args.output_dir) else str(_PROJECT_ROOT / args.output_dir)
    os.makedirs(odir, exist_ok=True)

    inc = create_llm(args.model_fast)
    ext = create_llm(args.model_pro)
    tc = TokenChecker(getattr(inc, "model_name", ""), getattr(ext, "model_name", ""))

    meta_index = load_meta_index()

    components = ["cathode", "anode", "electrolyte"] if args.component == "all" else [args.component]
    registry = build_agent_registry(inc, ext, tc, components)
    logger.info(f"Agent registry: {list(registry.keys())}")

    tasks = []
    for root, dirs, files in os.walk(idir):
        comp = os.path.basename(root).lower()
        if comp not in COMPONENT_MAP:
            continue
        for f in files:
            if f.lower().endswith(".md"):
                tasks.append({"file_path": os.path.join(root, f), "component": COMPONENT_MAP[comp]})
    if args.component != "all":
        tasks = [t for t in tasks if t["component"] == args.component]
    if args.limit > 0:
        tasks = tasks[:args.limit]
    logger.info(f"{len(tasks)} tasks | components={list(registry.keys())}")

    all_results = []
    for i, t in enumerate(tasks, 1):
        comp = t["component"]
        logger.info(f"[{i}/{len(tasks)}] {comp}: {os.path.basename(t['file_path'])}")
        try:
            res = process_file(t["file_path"], registry[comp], comp, meta_index,
                               max_workers=args.workers)
            all_results.append(res)
            base = os.path.splitext(os.path.basename(t["file_path"]))[0]
            out_path = os.path.join(odir, f"{base}_{comp}_extracted_v3.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            logger.info(f"    -> {out_path}  (materials={res.get('n_materials',0)})")
        except Exception as e:
            logger.error(f"Error: {e}")
            all_results.append({"file": t["file_path"], "error": str(e)})

    summary = os.path.join(odir, "_pipeline_v3_summary.json")
    with open(summary, "w", encoding="utf-8") as f:
        json.dump({"pipeline_version": "v3", "n_tasks": len(all_results),
                   "token_summary": tc.summary() if tc else {}, "results": all_results},
                  f, ensure_ascii=False, indent=2)
    ok = sum(1 for r in all_results if "error" not in r)
    print(f"\n✅ [v3] {ok}/{len(tasks)} files -> {odir}")
