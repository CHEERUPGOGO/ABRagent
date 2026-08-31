# -*- coding: utf-8 -*-
"""提取流水线 v2 — Phase 0(材料识别) → ConditionV2 → MaterialV2 → PerformanceV2 → Table

与 v1 的区别：
- ConditionAgent → ConditionAgentV2
- CathodeAgent / AnodeMaterialAgent → CathodeAgentV2 / BaseAgentV2 子类
- CathodePerformanceAgent / PerformanceAgent → PerformanceAgentV2 / BaseAgentV2 子类
- 整体流程不变：先识别材料，再按材料分路处理

2025-05 — v2 重构
"""

import os, sys, json, logging
from pathlib import Path
from typing import Dict, List, Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from langchain_classic.chains.llm import LLMChain
from langchain_core.prompts import PromptTemplate

from miner.config import create_llm
from miner.cleaning.structured_clean import structured_clean
from miner.extraction_core.pricing import TokenChecker
from miner.extraction_core.table_agent_v2 import TableAgentV2
from miner.extraction_core.base_agent_v2 import BaseAgentV2
from miner.extraction_core.condition_agent_v2 import ConditionAgentV2
from miner.extraction_core.material_discovery import MaterialDiscoveryAgent

# ── 尝试导入现成的 CathodeAgentV2 / PerformanceAgentV2 ──
try:
    from miner.cathode_database.cathode_agent_v2 import CathodeAgentV2
except ImportError:
    CathodeAgentV2 = None  # 回退到 BaseAgentV2 构建

try:
    from miner.extraction_core.performance_agent_v2 import PerformanceAgentV2
except ImportError:
    PerformanceAgentV2 = None

# ── Formatter ──
from miner.cathode_database.cathode_formatter import CathodeFormatter
from miner.anode_database.anode_formatter import AnodeFormatter
from miner.electrolyte_database.electrolyte_formatter import ElectrolyteFormatter

# ── 各组件的 prompts (使用 v1 prompts，v2 agent 复用同一套 prompt 模板) ──
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

logger = logging.getLogger("ExtractionPipelineV2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

COMPONENT_MAP = {"cathode": "cathode", "anode": "anode", "electrolyte": "electrolyte"}


# ==================== v2 Agent 工厂 ====================

def _make_material_agent_v2_cls(Formatter, base_name):
    """创建一个 MaterialAgentV2 子类（基于 BaseAgentV2，使用 material_* 视图）。"""

    class MaterialAgentV2(BaseAgentV2):
        formatter: Any = Formatter

        def _call(self, inputs, run_manager=None):
            from langchain_core.callbacks.manager import CallbackManagerForChainRun
            from miner.extraction_core.errors import LangchainError

            _rm = run_manager or CallbackManagerForChainRun.get_noop_manager()
            _rm.get_child()
            content = str(inputs.get(self.input_key, ""))
            mid = inputs.get("material_id", "")
            ctx = inputs.get("battery_system_context", "")
            cid = inputs.get("condition_id", "")
            doi = inputs.get("doi", "")

            base = {"content": content, "material_id": mid,
                    "battery_system_context": ctx, "property_types": [],
                    "extracted_info": {}, "doi": doi}

            fmt = self.formatter
            # 使用 material_* 视图（只包含材料属性标签，不含性能标签）
            expl = "\n".join(f"- {k}: {v}" for k, v in fmt.material_explanation.items())

            prompt_text = self.prompt_include.format(
                battery_system_context=ctx, material_id=mid,
                condition_id=cid, explanation=expl, paragraph=content)
            try:
                inc = self.include_chain.llm.invoke(prompt_text)
                inc = inc.content if hasattr(inc, 'content') else str(inc)
            except Exception as e:
                raise LangchainError(chain_name="Include", original_error=e)
            if self.token_checker:
                self.token_checker.record(f"{self.base_name}-include", prompt_text, inc, "include")

            ptypes = self._parse_include(inc)
            # 后置过滤：只保留确属材料（非性能）的标签
            material_keys = set(fmt.material_keys())
            ptypes = [p for p in ptypes if p in material_keys]
            if not ptypes:
                return {"output": base}

            st = info = ex = ps = ""
            for p in ptypes:
                try:
                    st += f"- {fmt.material_structured_data[p]}\n"
                    info += f"- {fmt.material_information[p]}\n"
                    ex += f"- {fmt.material_example_text[p]}\n"
                    ps += f"{p}, "
                except KeyError:
                    pass
            if not ps:
                return {"output": {**base, "property_types": ptypes}}

            ext_prompt = self.prompt_extract.format(
                battery_system_context=ctx, material_id=mid,
                condition_id=cid, prop=ps, structured_data=st,
                information=info, example=ex, paragraph=content)
            try:
                ext = self.extract_chain.llm.invoke(ext_prompt)
                ext = ext.content if hasattr(ext, 'content') else str(ext)
            except Exception as e:
                raise LangchainError(chain_name="Extract", original_error=e)
            if self.token_checker:
                self.token_checker.record(f"{self.base_name}-extract", ext_prompt, ext, "extract")

            return {"output": {**base, "property_types": ptypes,
                               "extracted_info": self._parse_extract(ext)}}

    return MaterialAgentV2


def _make_performance_agent_v2_cls(Formatter, base_name):
    """创建一个 PerformanceAgentV2 子类（基于 BaseAgentV2，使用 perf_* 视图）。"""

    class PerformanceAgentV2Bridge(BaseAgentV2):
        formatter: Any = Formatter

        def _call(self, inputs, run_manager=None):
            from langchain_core.callbacks.manager import CallbackManagerForChainRun
            from miner.extraction_core.errors import LangchainError

            _rm = run_manager or CallbackManagerForChainRun.get_noop_manager()
            _rm.get_child()
            content = str(inputs.get(self.input_key, ""))
            mid = inputs.get("material_id", "")
            ctx = inputs.get("battery_system_context", "")
            cid = inputs.get("condition_id", "")
            doi = inputs.get("doi", "")

            base = {"content": content, "material_id": mid,
                    "battery_system_context": ctx, "performance_types": [],
                    "extracted_info": {}, "doi": doi}

            fmt = self.formatter
            # 使用 perf_* 视图（只包含性能标签）
            expl = "\n".join(f"- {k}: {v}" for k, v in fmt.perf_explanation.items())

            prompt_text = self.prompt_include.format(
                battery_system_context=ctx, material_id=mid,
                condition_id=cid, explanation=expl, paragraph=content)
            try:
                inc = self.include_chain.llm.invoke(prompt_text)
                inc = inc.content if hasattr(inc, 'content') else str(inc)
            except Exception as e:
                raise LangchainError(chain_name="Include", original_error=e)
            if self.token_checker:
                self.token_checker.record(f"{self.base_name}-include", prompt_text, inc, "include")

            ptypes = self._parse_include(inc)
            # 后置过滤：只保留性能标签
            perf_keys = set(fmt.performance_keys())
            ptypes = [p for p in ptypes if p in perf_keys]
            if not ptypes:
                return {"output": base}

            st = info = ex = ps = ""
            for p in ptypes:
                try:
                    st += f"- {fmt.perf_structured_data[p]}\n"
                    info += f"- {fmt.perf_information[p]}\n"
                    ex += f"- {fmt.perf_example_text[p]}\n"
                    ps += f"{p}, "
                except KeyError:
                    pass
            if not ps:
                return {"output": {**base, "performance_types": ptypes}}

            ext_prompt = self.prompt_extract.format(
                battery_system_context=ctx, material_id=mid,
                condition_id=cid, prop=ps, structured_data=st,
                information=info, example=ex, paragraph=content)
            try:
                ext = self.extract_chain.llm.invoke(ext_prompt)
                ext = ext.content if hasattr(ext, 'content') else str(ext)
            except Exception as e:
                raise LangchainError(chain_name="Extract", original_error=e)
            if self.token_checker:
                self.token_checker.record(f"{self.base_name}-extract", ext_prompt, ext, "extract")

            return {"output": {**base, "performance_types": ptypes,
                               "extracted_info": self._parse_extract(ext)}}

    return PerformanceAgentV2Bridge


# ==================== Meta 关联 ====================

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


def build_paper_context(meta: dict, component: str, file_stem: str) -> dict:
    doi = (meta.get("doi") if meta else None) or file_stem.replace("_", "/")
    paper_id = doi or file_stem
    title = meta.get("title", "")[:80] if meta else ""
    return {
        "paper_id": paper_id,
        "doi": doi,
        "meta_title": title,
        "meta_authors": meta.get("authors", "") if meta else "",
        "meta_year": meta.get("publication_date", "") if meta else "",
        "component": component,
        "file_stem": file_stem,
    }


# ==================== 辅助 ====================

def scan_files(input_root):
    tasks = []
    for root, dirs, files in os.walk(input_root):
        comp = os.path.basename(root).lower()
        if comp not in COMPONENT_MAP:
            continue
        for f in files:
            if f.lower().endswith(".md"):
                tasks.append({"file_path": os.path.join(root, f), "component": COMPONENT_MAP[comp]})
    return tasks


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


def _expand_table_items(all_items, table_output, ti):
    ei = table_output.get("extracted_info", {})
    pi = table_output.get("performance_info", {})
    if not isinstance(ei, dict):
        ei = {}
    if not isinstance(pi, dict):
        pi = {}
    is_comp = table_output.get("is_comparison_table", False)
    max_len = 0
    for val in list(ei.values()) + list(pi.values()):
        if isinstance(val, list):
            max_len = max(max_len, len(val))
        elif isinstance(val, dict):
            max_len = max(max_len, 1)
    for row_idx in range(max_len):
        item = {
            "paragraph": f"table_{ti+1}_row_{row_idx+1}",
            "source": "table",
            "is_comparison_table": is_comp,
        }
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
    merged_info = {}
    merged_perf = {}
    for item in all_items:
        src = item.get("source", "text")

        def _merge_into(dest, src_dict, source_label):
            if not isinstance(src_dict, dict):
                return
            for prop, val in src_dict.items():
                entries = []
                if isinstance(val, list):
                    entries = [{**v, "source": source_label} for v in val if isinstance(v, dict)]
                elif isinstance(val, dict):
                    v = dict(val)
                    v.setdefault("condition", "")
                    entries = [{**v, "source": source_label}]
                if prop not in dest:
                    dest[prop] = []
                dest[prop].extend(entries)

        _merge_into(merged_info, item.get("extracted_info", {}), src)
        _merge_into(merged_perf, item.get("performance_info", {}), src)
    return merged_info, merged_perf


# ==================== 主流程 ====================

def process_file(file_path, agents, component, meta_lookup: dict):
    """
    agents: {"discovery":..., "condition":..., "material":..., "perf":..., "table":...}
    meta_lookup: doi/filename → meta(回退)

    返回:
        {"file": ..., "paper": {...}, "materials": [
            {"material_id": ..., "name": ..., "short_name": ..., "formula": ...,
             "role": ..., "description": ..., "n_items": ..., "items": [...],
             "merged": {"extracted_info": ..., "performance_info": ...}},
        ], "n_tables": ..., "n_figures": ...}
    """
    fname = os.path.basename(file_path)
    file_stem = os.path.splitext(fname)[0]
    logger.info(f"[{component}] {fname}")

    # 1. 结构化清洗
    try:
        doc = structured_clean(file_path, min_text_len=200, mode="extract")
        if doc is None:
            return {"file": file_path, "component": component, "error": "text_too_short"}
    except Exception as e:
        return {"file": file_path, "component": component, "error": str(e)}

    # 2. 元数据
    meta = doc.meta if doc.meta else {}
    if not meta.get("title"):
        fallback = find_meta_for_file(meta_lookup, file_path)
        meta.update({k: v for k, v in fallback.items() if v and not meta.get(k)})
    paper_ctx = build_paper_context(meta, component, file_stem)
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

    # ===============================
    # Phase 0: 材料识别
    # ===============================
    discovery_agent = agents.get("discovery")
    if discovery_agent and doc.clean_text:
        raw_text = doc.clean_text if len(doc.clean_text) < 50000 else doc.clean_text[:50000]
        materials = discovery_agent.discover(raw_text, component, file_stem)
        logger.info(f"  识别到 {len(materials)} 种材料: {[m.get('short_name', m.get('name','?')) for m in materials]}")
    else:
        materials = [{
            "name": f"{component} material",
            "short_name": file_stem,
            "formula": "",
            "role": "novel",
            "description": "",
            "material_id": file_stem,
        }]
        logger.info(f"  无材料识别器，使用降级单材料模式: {file_stem}")

    if not materials:
        logger.info(f"  未识别出{component}材料，跳过")
        return {
            "file": file_path, "component": component,
            "paper": paper_ctx,
            "materials": [],
            "n_items": 0,
            "n_tables": len(doc.tables) if doc.tables else 0,
            "n_figures": len(doc.figures) if doc.figures else 0,
        }

    # ===============================
    # Phase 1-3: 按材料分路处理
    # ===============================
    cond_agent = agents.get("condition")
    mat_agent = agents.get("material")
    perf_agent = agents.get("perf")
    table_agent = agents.get("table")

    # ConditionAgentV2 是类级计数器，每个文件开始时重置
    if cond_agent and hasattr(cond_agent, "reset_counter"):
        cond_agent.reset_counter()

    per_material_results = []

    for mat in materials:
        mid = mat["material_id"]
        ctx = (
            f"文献: {paper_ctx['meta_title']}\n"
            f"DOI: {paper_ctx['doi']}\n"
            f"组件类型: {component}\n"
            f"当前材料: {mat['name']} ({mat['role']})"
        )
        logger.info(f"  处理材料: {mat['name']} [{mid}]")

        # 每个材料单独的条件计数
        if cond_agent and hasattr(cond_agent, "reset_counter"):
            cond_agent.reset_counter()

        global_conditions = []
        all_items = []

        for p in paragraphs:
            item = {"paragraph": p[:200], "source": "text"}

            # Step 1: Condition (v2)
            cid = ""
            if cond_agent:
                co = _safe_invoke(cond_agent, {
                    "content": p, "material_id": mid,
                    "battery_system_context": ctx, "doi": doi,
                    "known_conditions": global_conditions,
                })
                conds = co.get("extracted_conditions", [])
                global_conditions.extend(conds)
                cids = [c.get("condition_id", "") for c in conds if c.get("condition_id")]
                cid = cids[0] if cids else ""
                if conds:
                    item["conditions"] = conds

            # Step 2: Material properties (v2)
            if mat_agent:
                mo = _safe_invoke(mat_agent, {
                    "content": p, "material_id": mid,
                    "battery_system_context": ctx, "doi": doi,
                })
                item["property_types"] = mo.get("property_types", [])
                item["extracted_info"] = mo.get("extracted_info", {})

            # Step 3: Performance (v2)
            if perf_agent:
                po = _safe_invoke(perf_agent, {
                    "content": p, "material_id": mid,
                    "battery_system_context": ctx,
                    "condition_id": cid, "doi": doi,
                })
                # v2 性能 agent 输出 "performance_types" 字段
                item["performance_types"] = po.get("performance_types", [])
                item["performance_info"] = po.get("extracted_info", {})
                if cid:
                    item["condition_id"] = cid

            if item.get("property_types") or item.get("performance_types") or item.get("conditions"):
                all_items.append(item)

        # 表格提取
        if table_agent and doc.tables:
            for ti, tbl in enumerate(doc.tables):
                to = _safe_invoke(table_agent, {
                    "content": tbl, "material_id": mid,
                    "doi": doi, "component": component,
                    "material_context": f"{mat['name']} ({mat.get('short_name', '')})",
                })
                if to and not to.get("skipped") and (to.get("property_types") or to.get("performance_types")):
                    _expand_table_items(all_items, to, ti)

        # 按属性归并
        merged_info, merged_perf = _merge_results(all_items)

        # 构建 conditions 索引
        conditions_index = {}
        for c in global_conditions:
            cid_key = c.get("condition_id", "")
            if cid_key:
                conditions_index[cid_key] = c

        # 按 battery_configuration 分组性能数据
        perf_by_config = {"half-cell": {}, "full-cell": {}, "symmetric-cell": {}, "unknown": {}}
        for perf_type, entries in merged_perf.items():
            for entry in entries:
                cid_key = entry.get("condition_id", "")
                cfg = conditions_index.get(cid_key, {}).get("battery_configuration", "unknown")
                if cfg not in perf_by_config:
                    cfg = "unknown"
                if perf_type not in perf_by_config[cfg]:
                    perf_by_config[cfg][perf_type] = []
                perf_by_config[cfg][perf_type].append(entry)

        per_material_results.append({
            "material_id": mid,
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
            "conditions_index": conditions_index,
            "performance_by_config": {k: v for k, v in perf_by_config.items() if v},
        })

    return {
        "file": file_path,
        "component": component,
        "paper": paper_ctx,
        "n_materials": len(per_material_results),
        "materials": per_material_results,
        "n_tables": len(doc.tables) if doc.tables else 0,
        "n_figures": len(doc.figures) if doc.figures else 0,
    }


# ==================== Agent 注册表 ====================

def build_agent_registry(inc_llm, ext_llm, tc, components=None):
    """
    为指定组件构建 agent 字典。

    Args:
        inc_llm: include 链 LLM（fast 模型）
        ext_llm: extract 链 LLM（pro 模型）
        tc: TokenChecker
        components: 要生成的组件列表，默认全部

    Returns:
        {component_name: {"discovery":..., "condition":..., "material":..., "perf":..., "table":...}}
    """
    if components is None:
        components = ["cathode", "anode", "electrolyte"]

    discovery_agent = MaterialDiscoveryAgent.from_llm(inc_llm)
    cond_agent = ConditionAgentV2.from_llm(inc_llm, ext_llm, tc)

    # 按组件创建感知标签体系的 TableAgentV2
    formatter_map = {
        "cathode": CathodeFormatter,
        "anode": AnodeFormatter,
        "electrolyte": ElectrolyteFormatter,
    }
    table_agents = {
        comp: TableAgentV2.from_llm(inc_llm, ext_llm, component=comp, formatter=fmt)
        for comp, fmt in formatter_map.items()
    }

    registry = {}

    for comp in components:
        if comp == "cathode":
            # ── 正极：优先用现成的 CathodeAgentV2 / PerformanceAgentV2 ──
            if CathodeAgentV2 is not None:
                mat_agent = CathodeAgentV2.from_llm(inc_llm, ext_llm, tc)
            else:
                MaterialCls = _make_material_agent_v2_cls(
                    CathodeFormatter, "cathode-material-v2")
                mat_agent = MaterialCls.from_llm(inc_llm, ext_llm, tc,
                    prompt_include=PROMPT_MATERIAL_INCLUDE,
                    prompt_extract=PROMPT_MATERIAL_EXTRACT,
                    base_name="cathode-material-v2")

            if PerformanceAgentV2 is not None:
                perf_agent = PerformanceAgentV2.from_llm(inc_llm, ext_llm, tc)
            else:
                PerfCls = _make_performance_agent_v2_cls(
                    CathodeFormatter, "cathode-perf-v2")
                perf_agent = PerfCls.from_llm(inc_llm, ext_llm, tc,
                    prompt_include=PROMPT_PERFORMANCE_INCLUDE,
                    prompt_extract=PROMPT_PERFORMANCE_EXTRACT,
                    base_name="cathode-perf-v2")

        elif comp == "anode":
            # ── 负极：基于 BaseAgentV2 工厂构建 ──
            AnodeMaterialCls = _make_material_agent_v2_cls(
                AnodeFormatter, "anode-material-v2")
            mat_agent = AnodeMaterialCls.from_llm(inc_llm, ext_llm, tc,
                prompt_include=PROMPT_ANODE_MATERIAL_INCLUDE,
                prompt_extract=PROMPT_ANODE_MATERIAL_EXTRACT,
                base_name="anode-material-v2")

            AnodePerfCls = _make_performance_agent_v2_cls(
                AnodeFormatter, "anode-perf-v2")
            perf_agent = AnodePerfCls.from_llm(inc_llm, ext_llm, tc,
                prompt_include=PROMPT_ANODE_PERFORMANCE_INCLUDE,
                prompt_extract=PROMPT_ANODE_PERFORMANCE_EXTRACT,
                base_name="anode-perf-v2")

        elif comp == "electrolyte":
            # ── 电解质：基于 BaseAgentV2 工厂构建 ──
            ElectrolyteMaterialCls = _make_material_agent_v2_cls(
                ElectrolyteFormatter, "electrolyte-material-v2")
            mat_agent = ElectrolyteMaterialCls.from_llm(inc_llm, ext_llm, tc,
                prompt_include=PROMPT_ELECTROLYTE_MATERIAL_INCLUDE,
                prompt_extract=PROMPT_ELECTROLYTE_MATERIAL_EXTRACT,
                base_name="electrolyte-material-v2")

            ElectrolytePerfCls = _make_performance_agent_v2_cls(
                ElectrolyteFormatter, "electrolyte-perf-v2")
            perf_agent = ElectrolytePerfCls.from_llm(inc_llm, ext_llm, tc,
                prompt_include=PROMPT_ELECTROLYTE_PERFORMANCE_INCLUDE,
                prompt_extract=PROMPT_ELECTROLYTE_PERFORMANCE_EXTRACT,
                base_name="electrolyte-perf-v2")

        else:
            raise ValueError(f"Unknown component: {comp}")

        registry[comp] = {
            "discovery": discovery_agent,
            "condition": cond_agent,
            "material": mat_agent,
            "perf": perf_agent,
            "table": table_agents[comp],
        }

    return registry


# ==================== CLI ====================

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="v2 提取流水线：Phase 0(材料识别)→ConditionV2→MaterialV2→PerfV2 按材料分路")
    p.add_argument("-i", "--input", default="database/type")
    p.add_argument("-o", "--output-dir", default="miner/json")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--component", default="all", choices=["all", "cathode", "anode", "electrolyte"])
    p.add_argument("--model-fast", default="classification")
    p.add_argument("--model-pro", default="extraction")
    args = p.parse_args()

    idir = args.input if os.path.isabs(args.input) else str(_PROJECT_ROOT / args.input)
    odir = args.output_dir if os.path.isabs(args.output_dir) else str(_PROJECT_ROOT / args.output_dir)
    os.makedirs(odir, exist_ok=True)

    inc = create_llm(args.model_fast)
    ext = create_llm(args.model_pro)
    tc = TokenChecker(getattr(inc, 'model_name', ''), getattr(ext, 'model_name', ''))

    # 加载 meta 索引
    meta_index = load_meta_index()

    # 确定要创建哪些组件
    components = ["cathode", "anode", "electrolyte"] if args.component == "all" else [args.component]

    # 构建 agent 注册表
    agent_registry = build_agent_registry(inc, ext, tc, components)
    logger.info(f"Agent registry: {list(agent_registry.keys())}")

    # 扫描文件
    tasks = scan_files(idir)
    if args.component != "all":
        tasks = [t for t in tasks if t["component"] == args.component]
    if args.limit > 0:
        tasks = tasks[:args.limit]
    logger.info(f"{len(tasks)} tasks | components={list(agent_registry.keys())}")

    all_results = []
    for i, t in enumerate(tasks, 1):
        comp = t["component"]
        logger.info(f"[{i}/{len(tasks)}] {comp}: {os.path.basename(t['file_path'])}")
        try:
            res = process_file(t["file_path"], agent_registry[comp], comp, meta_index)
            all_results.append(res)
            base = os.path.splitext(os.path.basename(t["file_path"]))[0]
            out_path = os.path.join(odir, f"{base}_{comp}_extracted_v2.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            logger.info(f"    → {out_path}  (materials={res.get('n_materials',0)}, items={sum(m.get('n_items',0) for m in res.get('materials',[]))})")
        except Exception as e:
            logger.error(f"Error: {e}")
            all_results.append({"file": t["file_path"], "error": str(e)})

    summary = os.path.join(odir, "_pipeline_v2_summary.json")
    with open(summary, "w", encoding="utf-8") as f:
        json.dump({
            "pipeline_version": "v2",
            "n_tasks": len(all_results),
            "token_summary": tc.summary() if tc else {},
            "results": all_results,
        }, f, ensure_ascii=False, indent=2)
    ok = sum(1 for r in all_results if "error" not in r)
    print(f"\n✅ [v2] {ok}/{len(tasks)} files → {odir}")
