"""多智能体模块 — Planner, Retrieval, Writer, Reviewer(材料筛选场景)

"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("rag_agents")

from .bm25_kb import normalize_source_path, resolve_paper_title_map
from .config import (
    PRIMARY_LABELS, LABEL_KEYWORDS, COMPONENT_KEYWORDS,
    PLANNER_MODEL, WRITER_MODEL, REVIEWER_MODEL,
    RETRIEVAL_TOP_K_PER_QUERY, SEARCH_K, DEFAULT_TOP_K,
)
from .llm_client import (
    safe_json_loads, rule_decompose_question, rule_conservative_answer,
)
from .prompts import (
    PLANNER_SYSTEM_PROMPT,
    WRITER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
)

try:
    from .relation_engine import RelationEngine
except Exception:
    RelationEngine = None  # 关系引擎不可用时降级为纯 RAG

class PlannerAgent:
    """任务规划智能体 — 拆解问题/规划回答结构(材料筛选导向)

    Prompt 全部针对材料筛选场景.
    """

    name = "Planner Agent"

    def __init__(self, llm_client, relation_engine=None):
        self.llm = llm_client
        self.relation_engine = relation_engine

    def run(self, question: str, history_context: str = "") -> Dict[str, Any]:
        """拆解用户问题为子检索问题."""
        question = question.strip()
        if not question:
            return {
                "task_understanding": "",
                "retrieval_queries": [],
                "answer_outline": [],
                "db_type": "both",
                "fallback": True,
            }

        # LLM 不可用时,规则回退
        if not self.llm.available:
            return rule_decompose_question(question)

        # 构建 Planner 输入
        user_prompt = f"用户问题:{question}"
        if history_context:
            user_prompt = (
                f"{history_context}\n\n"
                f"当前问题(结合上文理解):{question}"
            )
        user_prompt += "\n请按要求输出 JSON."

        try:
            raw = self.llm.chat(PLANNER_SYSTEM_PROMPT, user_prompt, temperature=0.1, trace_tag="planner")
            data = safe_json_loads(raw)
        except Exception:
            data = None

        if not data:
            return {
                **rule_decompose_question(question),
                "raw": raw if 'raw' in locals() else "",
            }

        # 确保必要字段存在
        data.setdefault("task_understanding", f"用户想了解高比能锂电池材料筛选:{question}")
        data.setdefault("retrieval_queries", [question])
        data.setdefault("answer_outline", ["材料概述", "核心性能指标", "筛选建议", "数据缺口"])
        data.setdefault("focus_labels", [])
        data.setdefault("focus_component", None)
        data.setdefault("needs_reasoning", False)
        data.setdefault("db_type", "both")
        data["fallback"] = False

        return data

class RetrievalAgent:
    """检索智能体 — 多子问题检索 + 合并去重 + 加权排序(材料筛选导向)

    融合 chat_rag_v3_optimized.py 的策略:
    - 组件过滤 + 标签加权 + 数值密度加分
    - 兜底搜索(避免组件/标签过滤遗漏)
    - 同文献压制(同一文献超过 2 段降权 20%)
    """

    name = "Retrieval Agent"

    def __init__(self, kb, vector_store=None, relation_engine=None):
        self.kb = kb
        self.vector_store = vector_store
        self.relation_engine = relation_engine

    def _classify_question(self, question: str) -> str:
        """识别问题类型(材料筛选专用)"""
        from .config import QTYPE_KEYWORDS
        q = question.strip()
        for t, kws in QTYPE_KEYWORDS.items():
            if any(kw in q for kw in kws):
                return t
        return "general"

    @staticmethod
    def _has_component_keyword(text_lower: str, comp: str) -> bool:
        """检查小写文本中是否出现指定组件的关键词（短 ASCII 词避免子串误匹配）."""
        check_text = text_lower
        if comp == "anode":
            check_text = re.sub(
                r"(?:cr20\d\d\s*)?(?:锂金属|金属锂|锂片|li[\s\-]?metal)\s*(?:半电池|扣式半电池|扣式电池|对电极|参比电极|half[\s\-]?cell|counter\s+electrode|coin\s+cell)",
                " ",
                check_text,
            )
        for kw in COMPONENT_KEYWORDS.get(comp, ()):
            k = kw.lower()
            if len(k) <= 2 and k.isascii():
                if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", check_text):
                    return True
            elif k in check_text:
                return True
        if comp == "cathode" and re.search(
            r"(?<![a-z])(?:li|na)\d*(?:\.\d+)?[a-z0-9().\-]*(?:mn|ni|co|fe|v|cr|ru)[a-z0-9().\-]*(?:o\d|po4|f\d)",
            text_lower,
        ):
            return True
        return False

    def _detect_component(self, question: str) -> Optional[str]:
        """从问题中检测涉及的电池组件"""
        q = question.lower()
        for comp in COMPONENT_KEYWORDS:
            if self._has_component_keyword(q, comp):
                return comp
        return None

    def _detect_labels(self, question: str) -> List[str]:
        """从问题中检测涉及的标签类型"""
        q = question.lower()
        scores = {}
        for label, kws in LABEL_KEYWORDS.items():
            s = sum(1 for kw in kws if kw.lower() in q)
            if s > 0:
                scores[label] = s
        return sorted(scores, key=scores.get, reverse=True) if scores else []

    def _doc_bonus(self, question: str, source: str) -> float:
        """根据问题类型给特定来源文档加分.

        材料筛选场景:筛选型问题给 review/综述文档加分,数值型问题给性能文档加分.
        """
        qtype = self._classify_question(question)
        s = source.lower()

        # 筛选型问题:综述/对比文档更有价值
        if qtype == "screening":
            if any(k in s for k in ["review", "comparison", "综述", "对比"]):
                return 0.06
        # 数值型问题:带具体数据的段落
        if qtype == "numeric":
            # 通过文件名中的性能关键词加分
            if any(k in s for k in ["capacity", "conductivity", "voltage", "性能"]):
                return 0.04

        return 0.0

    def _numeric_score(self, text: str) -> float:
        """数值密度加分 — 段落中数值型数据越多,越可能是性能数据"""
        num_hits = len(re.findall(
            r"\d+(?:\.\d+)?\s*(mAh|mA|mV|V|Wh|W|mS|S|°C|℃|%)", text
        ))
        return 0.04 * min(num_hits, 5)

    def _label_bonus(self, metadata: dict, expected_labels: List[str]) -> float:
        """标签匹配加分 — 段落标签与问题期望标签匹配时加分"""
        lbl = metadata.get("label", "")
        if not lbl or not expected_labels:
            return 0.0
        for i, el in enumerate(expected_labels):
            if lbl == el:
                return 0.15 * (1 / (i + 1))
        return 0.0

    def _build_title_map(
        self,
        chroma_hits: List[Tuple[Any, float]],
        tfidf_hits: List[Tuple[Any, float]],
    ) -> Dict[str, str]:
        """汇总 kb.paper_title_map、当前批次同论文段落与 Chroma sibling chunks 的题名映射."""
        cache = getattr(self, "_chroma_title_cache", None)
        if cache is None:
            cache = {}
            self._chroma_title_cache = cache
        return resolve_paper_title_map(
            getattr(self, "vector_store", None),
            getattr(self, "kb", None),
            chroma_hits,
            tfidf_hits,
            title_cache=cache,
        )

    @staticmethod
    def _normalize_entry_meta(
        raw_meta: Optional[dict],
        raw_source: str,
        paper_title_map: Dict[str, str],
    ) -> Tuple[dict, str, str, str, str]:
        """规范化路径并按 paper_title_map 回填缺失题名."""
        meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        if meta.get("source_file"):
            meta["source_file"] = normalize_source_path(str(meta["source_file"]))
        source = normalize_source_path(
            str(meta.get("source_file") or raw_source or meta.get("source_paper") or "chroma")
        )
        sp = str(meta.get("source_paper") or "").strip()
        doi = str(meta.get("doi") or sp or "")
        title = str(meta.get("title") or "").strip()
        if not title:
            title = (
                paper_title_map.get(sp)
                or paper_title_map.get(meta.get("source_file", ""))
                or paper_title_map.get(source)
                or ""
            )
            if title:
                meta["title"] = title
        source_display = f"{title} (DOI: {doi})" if title and doi else (title or source)
        return meta, source, doi, title, source_display

    @staticmethod
    def _merge_entry_keep_title(
        target: Dict[str, Dict[str, Any]],
        pid: str,
        candidate: Dict[str, Any],
        prefer_higher_score: bool = True,
    ) -> None:
        """合并候选条目并保证已有非空 title 不被空 title 覆盖."""
        if pid not in target:
            target[pid] = candidate
            return
        existing = target[pid]
        ex_title = str(existing.get("title") or (existing.get("metadata") or {}).get("title") or "").strip()
        cand_title = str(candidate.get("title") or (candidate.get("metadata") or {}).get("title") or "").strip()
        if prefer_higher_score and candidate.get("score", 0) > existing.get("score", 0):
            chosen = dict(candidate)
            if ex_title and not cand_title:
                chosen["title"] = ex_title
                c_meta = dict(chosen.get("metadata") or {})
                c_meta["title"] = ex_title
                chosen["metadata"] = c_meta
                doi = chosen.get("doi") or c_meta.get("source_paper") or ""
                chosen["source_display"] = f"{ex_title} (DOI: {doi})" if doi else ex_title
            target[pid] = chosen
        elif cand_title and not ex_title:
            existing["title"] = cand_title
            if isinstance(existing.get("metadata"), dict):
                existing["metadata"]["title"] = cand_title
            doi = existing.get("doi") or (existing.get("metadata") or {}).get("source_paper") or ""
            existing["source_display"] = f"{cand_title} (DOI: {doi})" if doi else cand_title

    def _run_single_query(
        self,
        query: str,
        component: Optional[str],
        expected_labels: List[str],
        top_k: int,
        question: str,
        use_label_filter: bool = True,
    ) -> List[Dict[str, Any]]:
        """执行一次检索(Chroma + BM25),返回合并结果."""
        merged: Dict[str, Dict[str, Any]] = {}

        # ── Chroma 向量检索 ──
        chroma_hits: List[Tuple[Any, float]] = []
        if self.vector_store is not None:
            k_chroma = max(top_k * 2, 30)
            comp_filter = {"component": component} if component else None
            label_filter = None
            if use_label_filter and expected_labels:
                top_labels = expected_labels[:2]
                if len(top_labels) > 1:
                    label_filter = {"$or": [{"label": lbl} for lbl in top_labels]}
                else:
                    label_filter = {"label": top_labels[0]}

            def _combine_filters(*parts: Optional[dict]) -> Optional[dict]:
                valid = [p for p in parts if p]
                if len(valid) > 1:
                    return {"$and": valid}
                return valid[0] if valid else None

            def _call_chroma(filt: Optional[dict]) -> Tuple[List[Any], bool]:
                if filt:
                    try:
                        return list(self.vector_store.similarity_search_with_score(query, k=k_chroma, filter=filt)), True
                    except TypeError:
                        return list(self.vector_store.similarity_search_with_score(query, k=k_chroma)), False
                return list(self.vector_store.similarity_search_with_score(query, k=k_chroma)), False

            try:
                filter_dict = _combine_filters(comp_filter, label_filter)
                chroma_raw, filter_accepted = _call_chroma(filter_dict)
                if not chroma_raw and filter_dict and filter_accepted:
                    if comp_filter and label_filter:
                        chroma_raw, _ = _call_chroma(comp_filter)
                    if not chroma_raw and label_filter:
                        chroma_raw, _ = _call_chroma(label_filter)
                    if not chroma_raw:
                        chroma_raw, _ = _call_chroma(None)

                if component and chroma_raw:
                    comp_filtered = [
                        (doc, sc) for doc, sc in chroma_raw
                        if not ((getattr(doc, "metadata", None) or {}).get("component"))
                        or (getattr(doc, "metadata", None) or {}).get("component") == component
                    ]
                    if comp_filtered:
                        chroma_raw = comp_filtered

                for doc, score in chroma_raw:
                    sim_score = max(0.0, 1.0 - score / 2.0)
                    chroma_hits.append((doc, sim_score))
            except Exception as e:
                print(f"[RetrievalAgent] Chroma 检索失败: {e}")

        # ── BM25 检索 ──
        bm25_top_k = max(top_k * 2, 30)
        bm25_fetch_k = max(bm25_top_k * 5, 300) if component else bm25_top_k
        raw_tfidf_hits = self.kb.search(query, top_k=bm25_fetch_k) if self.kb is not None else []
        if component and raw_tfidf_hits:
            filtered_tfidf = [
                (p, s) for p, s in raw_tfidf_hits
                if not ((getattr(p, "metadata", None) or {}).get("component"))
                or (getattr(p, "metadata", None) or {}).get("component") == component
            ]
            if filtered_tfidf:
                tfidf_hits = filtered_tfidf[:bm25_top_k]
            elif not chroma_hits:
                tfidf_hits = raw_tfidf_hits[:bm25_top_k]
            else:
                tfidf_hits = []
        else:
            tfidf_hits = raw_tfidf_hits[:bm25_top_k]

        paper_title_map = self._build_title_map(chroma_hits, tfidf_hits)

        # ── 多路召回: Chroma 通道 ──
        for doc, score in chroma_hits:
            raw_meta = getattr(doc, "metadata", None) or {}
            pid = raw_meta.get("passage_id") or raw_meta.get("chunk_id", "")
            if not pid:
                pid = hashlib.md5(doc.page_content.encode()).hexdigest()[:12]

            meta, source, doi, title, source_display = self._normalize_entry_meta(
                raw_meta,
                raw_meta.get("source_paper", "chroma"),
                paper_title_map,
            )
            entry = {
                "passage_id": pid,
                "source": source,
                "source_display": source_display,
                "doi": doi,
                "title": title,
                "score": score,
                "text": doc.page_content,
                "metadata": meta,
            }
            self._merge_entry_keep_title(merged, pid, entry, prefer_higher_score=False)

        # ── 多路召回: Chroma + BM25 用 RRF 融合 ──
        RRF_K = 60
        pid_ranks: Dict[str, Dict[str, int]] = {}
        for rank, (doc, _) in enumerate(chroma_hits, 1):
            raw_meta = getattr(doc, "metadata", None) or {}
            pid = raw_meta.get("passage_id") or raw_meta.get("chunk_id", "")
            if not pid:
                pid = hashlib.md5(doc.page_content.encode()).hexdigest()[:12]
            pid_ranks.setdefault(pid, {}).setdefault("chroma", rank)
        for rank, (passage, _) in enumerate(tfidf_hits, 1):
            pid_ranks.setdefault(passage.passage_id, {}).setdefault("bm25", rank)

        bm25_by_pid = {p.passage_id: p for p, _ in tfidf_hits}
        for pid, sides in pid_ranks.items():
            rrf = 0.0
            if "chroma" in sides:
                rrf += 1.0 / (RRF_K + sides["chroma"])
            if "bm25" in sides:
                rrf += 1.0 / (RRF_K + sides["bm25"])
            norm = min(1.0, rrf * (RRF_K + 1))
            passage = bm25_by_pid.get(pid)
            if pid in merged:
                merged[pid]["score"] = round(norm, 4)
                if passage and not merged[pid].get("title"):
                    _, _, _, p_title, p_disp = self._normalize_entry_meta(
                        getattr(passage, "metadata", None),
                        getattr(passage, "source", "bm25"),
                        paper_title_map,
                    )
                    if p_title:
                        merged[pid]["title"] = p_title
                        if isinstance(merged[pid].get("metadata"), dict):
                            merged[pid]["metadata"]["title"] = p_title
                        merged[pid]["source_display"] = p_disp
            elif passage is not None:
                meta, source, doi, title, source_display = self._normalize_entry_meta(
                    getattr(passage, "metadata", None),
                    getattr(passage, "source", "bm25"),
                    paper_title_map,
                )
                merged[pid] = {
                    "passage_id": pid,
                    "source": source,
                    "source_display": source_display,
                    "doi": doi,
                    "title": title,
                    "score": round(norm, 4),
                    "text": passage.text,
                    "metadata": meta,
                }

        # ── 子段到父块提升:命中子段时替换为完整父段 ──
        if self.vector_store is not None:
            parent_ids_needed = {entry["metadata"].get("parent_full_id", "") for entry in merged.values()
                                 if isinstance(entry.get("metadata"), dict) and entry["metadata"].get("parent_full_id")}
            if parent_ids_needed:
                try:
                    result = self.vector_store.get(ids=list(parent_ids_needed))
                    parent_texts = {}
                    for i, doc_id in enumerate(result.get("ids", [])):
                        if i < len(result.get("documents", [])):
                            parent_texts[doc_id] = result["documents"][i]
                    for entry in merged.values():
                        pid = entry["metadata"].get("parent_full_id") if isinstance(entry.get("metadata"), dict) else None
                        if pid and pid in parent_texts:
                            entry["text"] = parent_texts[pid]
                except Exception as e:
                    print(f"[RetrievalAgent] 父块提升失败: {type(e).__name__}: {e}")

        return list(merged.values())


    def run(
        self,
        question: str,
        queries: List[str],
        focus_component: Optional[str] = None,
        focus_labels: Optional[List[str]] = None,
        top_k_per_query: int = None,
        use_label_filter: bool = True,
        previous_evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """执行多子问题检索,融合 chat_rag_v3 的兜底和去重策略.

        Args:
            question: 原始用户问题
            queries: Planner 拆解的子检索问题列表
            focus_component: 目标组件(cathode/anode/electrolyte)
            focus_labels: 目标标签
            top_k_per_query: 每个子问题的 top_k
            use_label_filter: 是否启用 Chroma 标签硬过滤(推荐类问题可关闭)
            previous_evidence: 上轮召回的 evidence 列表,来源匹配时加权 +0.12

        Returns:
            {"queries": [...], "results": [...], "search_logs": [...]}
        """
        if top_k_per_query is None:
            top_k_per_query = RETRIEVAL_TOP_K_PER_QUERY

        component = focus_component or self._detect_component(question)
        expected_labels = focus_labels or self._detect_labels(question)

        merged: Dict[str, Dict[str, Any]] = {}
        search_logs: List[Dict[str, Any]] = []

        # ── 第1轮:对每个子问题独立检索 ──
        targeted_comps: set[str] = set()
        for query in queries:
            q_comp = component
            if component:
                detected_q_comp = self._detect_component(query)
                if (
                    detected_q_comp
                    and detected_q_comp != component
                    and not self._has_component_keyword(query.lower(), component)
                ):
                    q_comp = detected_q_comp
            if q_comp:
                targeted_comps.add(q_comp)
            results = self._run_single_query(
                query=query,
                component=q_comp,
                expected_labels=expected_labels,
                top_k=top_k_per_query,
                question=question,
                use_label_filter=use_label_filter,
            )
            brief_hits = [
                {"passage_id": r["passage_id"], "source": r["source"], "score": r["score"]}
                for r in results
            ]
            search_logs.append({"query": query, "hits": brief_hits})

            # 合并去重（保留已有非空 title）
            for r in results:
                self._merge_entry_keep_title(merged, r["passage_id"], r, prefer_higher_score=True)

        # ── 第2轮:兜底搜索(无组件/标签限制,对标 chat_rag_v3 的 extra_search) ──
        extra_results = self._run_single_query(
            query=question,
            component=None,
            expected_labels=[],
            top_k=5,
            question=question,
            use_label_filter=False,
        )
        has_primary_or_generic = (
            any(
                not ((h.get("metadata") or {}).get("component"))
                or (h.get("metadata") or {}).get("component") == component
                for h in (*merged.values(), *extra_results)
            )
            if component
            else False
        )
        extra_ids = set()
        for r in extra_results:
            extra_ids.add(r["passage_id"])
            if r["passage_id"] not in merged:
                merged[r["passage_id"]] = dict(r)
        if component and has_primary_or_generic:
            for entry in merged.values():
                r_comp = (entry.get("metadata") or {}).get("component")
                if r_comp and r_comp != component and r_comp not in targeted_comps:
                    entry["score"] = round(float(entry.get("score", 0.0)) * 0.35, 6)

        # ── 上轮证据来源加分(多轮递进检索) ──
        if previous_evidence:
            prev_sources: set[str] = set()
            for pe in previous_evidence:
                src = normalize_source_path(pe.get("source", ""))
                # 也提取 metadata 中的 source_paper(Chroma 来源用)
                meta = pe.get("metadata", {}) or {}
                paper = meta.get("source_paper") or normalize_source_path(meta.get("source_file", ""))
                if src:
                    prev_sources.add(src)
                if paper:
                    prev_sources.add(paper)
            if prev_sources:
                for pid in list(merged.keys()):
                    r = merged[pid]
                    src = normalize_source_path(r.get("source", ""))
                    meta = r.get("metadata", {}) or {}
                    paper = meta.get("source_paper") or normalize_source_path(meta.get("source_file", ""))
                    if src in prev_sources or paper in prev_sources:
                        r["score"] += 0.12

        results_list = sorted(merged.values(), key=lambda x: x["score"], reverse=True)

        # ── 插桩 A: 约束过滤（关系引擎可用时）──
        constraint_log = None
        if self.relation_engine is not None:
            try:
                entities = self.relation_engine.extract_entities(question)
                scheme = {}
                for cat in ("cathode", "anode", "electrolyte"):
                    ids = entities.get(cat) or []
                    if ids:
                        scheme[cat] = ids[0]
                if scheme:
                    mods = self.relation_engine.query_modifiers(scheme)
                    exclude_terms, boost_terms = mods["exclude_terms"], mods["boost_terms"]
                    filtered, boosted = [], []
                    for r in results_list:
                        text = (r.get("text", "") + " " + r.get("source", "")).lower()
                        if any(t.lower() in text for t in exclude_terms):
                            r["score"] -= 0.5  # 违反约束的段落降权（保留可追溯）
                            filtered.append(r.get("passage_id"))
                        if any(t.lower() in text for t in boost_terms):
                            r["score"] += 0.15
                            boosted.append(r.get("passage_id"))
                    results_list.sort(key=lambda x: x["score"], reverse=True)
                    constraint_log = {
                        "exclude_terms": exclude_terms[:8],
                        "boost_terms": boost_terms[:8],
                        "downgraded": filtered[:10],
                        "boosted": boosted[:10],
                    }
            except Exception as e:
                print(f"[RetrievalAgent] 约束过滤失败(降级): {type(e).__name__}: {e}")

        result = {
            "queries": queries,
            "results": results_list,
            "search_logs": search_logs,
        }
        if constraint_log:
            result["constraint_log"] = constraint_log
        return result

def _build_scheme_consistency_note(
    scheme: Optional[Dict[str, Any]],
    design_mode: bool = False,
    for_reviewer: bool = False,
) -> str:
    """构建 Stage 4 写作/修订/审核共享的方案锚点与一致性提示."""
    parts: List[str] = []
    if scheme and "electrolyte" in (scheme.get("user_locked_components") or []):
        parts.append(
            f"【用户锁定电解液约束】用户课题已显式锁定电解液为 `{scheme.get('electrolyte')}`（按课题原文配方执行）。"
            "主推荐组合、排行与主测试方案必须严格使用该锁定电解液，严禁将主推荐切换为 LHCE 或其他电解液体系；"
            "仅可将 LHCE/含氟添加剂列为界面机理对照组，并以同批未掺杂基线相对评估循环保持率。"
        )
    if scheme and scheme.get("additives"):
        canon_map = {
            "fec": "FEC", "vc": "VC", "lidfob": "LiDFOB", "libob": "LiBOB",
            "dto": "DTO", "dtd": "DTD", "ps": "PS", "pes": "PES",
            "lipf6": "LiPF6", "lifsi": "LiFSI",
        }
        add_list = ", ".join(
            canon_map.get(str(a).strip().lower(), str(a).strip())
            for a in scheme["additives"]
        )
        additive_status = (scheme.get("design_anchors") or {}).get("additive_status")
        if additive_status == "locked":
            parts.append(
                f"【用户锁定添加剂约束】用户课题已显式指定添加剂：{add_list}。"
                "主推荐与主要测试方案必须保留该锁定添加剂，严禁误标为「未锁定」或「未选用」。"
            )
        else:
            parts.append(
                f"【候选添加剂措辞约束】本方案已列入候选添加剂：{add_list}。"
                "必须明确标注为「已列入方案候选添加剂，具体比例与化成制度待实验定量筛选」，严禁误标为「未锁定」或「未选用」。"
            )
    if scheme and scheme.get("design_anchors"):
        parts.append(
            f"【已提取方案锚点（须跨五段与表格严格统一）】{scheme['design_anchors']}"
        )
    if design_mode or scheme:
        role_verb = "请逐项核查并拦截以下不一致或不当外推" if for_reviewer else "五段全文与所有表格必须严格遵守"
        parts.append(
            f"【Stage 4 一致性与证据边界清单】{role_verb}：\n"
            "1) 缺乏直接低掺杂二元实验证据时，Rank #1 须标为「主推候选名义化学式（待实验验证）」，严禁称「唯一最优」；全文统一单一掺杂基准（`总阳离子` 或 `TM`，勿混用 `总金属` 与 `总阳离子`），第 2/5 节位点上限（`x_A`、`y_B`）与总掺杂上限须覆盖全部梯度验证点，且与路线 A、主煅烧温区/时长及硬件参数五段一致；\n"
            "2) 高掺杂 DFT（如 α=0.5）或单元素代用文献数据不得当成低剂量共掺杂定量预期，表格与正文须同步标出「跨体系推断/无直接二元实验」；第 2 节排序表与正文须解释多圈保持率目标区间相对更高单元素/不同工况文献值的保守原因，第 4/5 节风险清单须包含惰性掺杂离子对 TM 氧化还原容量的稀释风险；\n"
            "3) 全文仅锁定唯一 `1 C` 换算基准；极片载量严格按 `总面载量 = 活性面载量 / 活性物质占比`（如 `4.5 / 0.80 = 5.625 mg/cm²`，严禁 `× 9/8`）及 `压实密度 = 总面载量 / 极片厚度` 核算；扣电在 `E/P = V_E / V_pore >= 3×` 时须严格标为「扣电过量/泛液测试基准（相较常规 100–120 μL 属减量富液，绝非贫液/低液量）」，严禁称「低液量测试基准」或「贴近孔体积」；死后 ICP-OES/XPS 须覆盖全部候选组与对照组涉及的所有过渡金属与掺杂元素。"
        )
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


class WriterAgent:
    """答案生成智能体 — 基于证据生成材料筛选答案

    Prompt 针对材料筛选场景.
    """

    name = "Writer Agent"

    def __init__(self, llm_client, relation_engine=None):
        self.llm = llm_client
        self.relation_engine = relation_engine

    def run(
        self,
        question: str,
        plan: Dict[str, Any],
        evidence: List[Dict[str, Any]],
        history_context: str = "",
        scheme: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """基于证据生成答案. 若提供 scheme,注入约束评估并启用方案五段式输出; LLM 不可用时 fail-closed 返回空草稿与错误信息."""
        if not evidence:
            return {
                "draft_answer": "未检索到有效证据,无法生成可靠的材料筛选结论.",
                "fallback": True,
            }

        _max_evidence = 25 if plan.get("needs_reasoning", False) else 15
        evidence_text = "\n\n".join(
            [f"[{e['passage_id']}] 来源:{e['source']}\n内容:{e['text']}"
             for e in evidence[:_max_evidence]]
        )
        outline = plan.get("answer_outline", ["材料概述", "核心性能指标", "筛选建议", "数据缺口"])

        # LLM 不可用时禁止回退为写死的虚构材料配方，显式失败并交由上游门禁拦截
        if not self.llm.available:
            return {
                "draft_answer": "",
                "fallback": True,
                "error": "LLM 后端不可用，无法基于检索证据生成方案（已禁用虚构配方回退）",
            }

        user_prompt = (
            f"用户问题:{question}\n\n"
            f"计划输出结构:{str(outline)}\n\n"
            f"可用证据如下:\n{evidence_text}\n\n"
            f"请依据证据生成一个适合材料筛选场景的结构化中文回答.\n"
            f"必须在关键结论后保留 [passage_id] 引用.\n"
            f"回答应以材料筛选为导向:对比候选材料/标注测试条件/给出数据缺口."
        )

        # ── 方案模式: 注入约束评估 + 五段式指示 ──
        design_mode = any(k in question for k in ("设计", "推荐", "方案"))
        if scheme and self.relation_engine is not None:
            try:
                ev = self.relation_engine.evaluate(scheme)
                if ev["violations"] or ev["rejects"] or ev["inclusions"]:
                    scheme_note = (
                        "\n\n【约束引擎评估(纯规则,供写作参考,不可违反)】\n"
                        f"方案: {scheme}\n"
                        f"violations(违规): {ev['violations']}\n"
                        f"rejects(拒绝): {ev['rejects']}\n"
                        f"inclusions(需补充): {ev['inclusions']}\n"
                        "若 violations/rejects 非空,该组合不可行,回答中必须指出并给出替代方向."
                    )
                    user_prompt += scheme_note
            except Exception as e:
                print(f"[WriterAgent] 约束评估失败(降级): {type(e).__name__}: {e}")
        user_prompt += _build_scheme_consistency_note(scheme, design_mode=design_mode, for_reviewer=False)
        if design_mode or scheme:
            user_prompt += (
                "\n\n【输出要求】这是设计/方案类问题,请按 WRITER_SYSTEM_PROMPT 中"
                "『设计任务输出结构』的五段式组织答案(目标/推荐组合/预期指标/可行性依据/风险与数据缺口),"
                "段间用 --- 分隔."
            )

        try:
            raw = self.llm.chat(WRITER_SYSTEM_PROMPT, user_prompt, temperature=0.2, trace_tag="writer")
            return {"draft_answer": raw, "fallback": False}
        except Exception as exc:
            return {"draft_answer": "", "fallback": True, "error": str(exc)}

    def revise(
        self,
        question: str,
        plan: Dict[str, Any],
        evidence: List[Dict[str, Any]],
        draft_answer: str,
        feedback: List[str],
        history_context: str = "",
        scheme: Optional[Dict[str, Any]] = None,
    ) -> str:
        """按带精确原句的审核意见局部修复；纯摘要不授予写权限。"""
        if not self.llm.available or not feedback:
            return draft_answer
        from auto_battery_research.util.design_repair import repair
        issues = [item for item in feedback if isinstance(item, dict)]
        if not issues:
            return draft_answer
        context = question + _build_scheme_consistency_note(
            scheme, design_mode=any(k in question for k in ("设计", "推荐", "方案")), for_reviewer=False,
        )
        changed, _, _ = repair(self, draft_answer, {"repair_issues": issues}, evidence, {}, context)
        return changed


class ReviewerAgent:
    """审核智能体 — 材料筛选与方案审核

    含 _build_conservative_answer；LLM 审核不可用时 fail-closed 返回低置信度 verdict。
    """

    name = "Reviewer Agent"

    def __init__(self, llm_client, relation_engine=None):
        self.llm = llm_client
        self.relation_engine = relation_engine

    def _build_conservative_answer(
        self,
        question: str,
        evidence: List[Dict[str, Any]],
        issues: Optional[List[str]] = None,
    ) -> str:
        """构建保守版本答案 — 审核不可用时,提供文献证据摘要供参考."""
        lines: List[str] = []

        lines.append("## 说明")
        if issues:
            for issue in issues:
                lines.append(f"- {issue}")
        lines.append("- 当前因 LLM 审核流程未正常完成,以下提供检索到的高相关性文献段落摘要供参考.")
        lines.append("- 如需完整分析,请稍后重试或检查 LLM 服务状态.")
        lines.append("")

        lines.append("## 检索到的关键文献段落")
        for i, item in enumerate(evidence[:5], 1):
            title = item.get("title", "")
            doi = item.get("doi", "")
            snippet = item["text"][:300]
            pid = item["passage_id"]

            if title:
                lines.append(f"### {i}. {title}")
            else:
                lines.append(f"### {i}. [{pid}]")
            if doi:
                lines.append(f"DOI: {doi}")
            lines.append("")
            lines.append(snippet)
            lines.append("")
            lines.append(f"> 段落标识: [{pid}]")
            lines.append("")

        return "\n".join(lines)

    def run(
        self,
        question: str,
        evidence: List[Dict[str, Any]],
        draft_answer: str,
        history_context: str = "",
        scheme: Optional[Dict[str, Any]] = None,
        claimed_energy: Optional[float] = None,
    ) -> Dict[str, Any]:
        """审核草稿答案、核查幻觉并给出审核 verdict 与置信度（离线或异常时 fail-closed 为 confidence="low"）.

        插桩 B: 若注入 relation_engine 且提供 scheme,先执行规则校验,
        规则结论注入 LLM 审核 prompt,并具有 confidence 否决权.
        """

        # ── 插桩 B: 规则校验（LLM 审核之前,纯规则,可解释）──
        rule_result = None
        if self.relation_engine is not None and scheme:
            try:
                rule_result = self.relation_engine.check_scheme(
                    scheme,
                    claimed_energy=claimed_energy,
                    answer_text=draft_answer,
                )
            except Exception as e:
                print(f"[ReviewerAgent] 规则校验失败(降级): {type(e).__name__}: {e}")
        if not evidence:
            conservative = "未找到可用证据,无法生成可靠的材料筛选结论."
            return {
                "issues": ["未找到证据"],
                "revised_answer": conservative,
                "confidence": "low",
                "fallback": True,
            }

        if not draft_answer.strip():
            return {
                "issues": ["Writer 未生成有效答案"],
                "revised_answer": self._build_conservative_answer(question, evidence),
                "confidence": "low",
                "fallback": True,
            }

        # LLM 不可用时不能仅凭规则放行高置信度（无法核查正文引用与事实一致性）
        if not self.llm.available:
            rule_issues = []
            if rule_result:
                rule_issues = (
                    rule_result.get("rule_checks", {}).get("violations", [])
                    or rule_result.get("rule_checks", {}).get("rejects", [])
                )
            issues = ["当前未成功连接 LLM，无法完成内容与引用真实性审核。", *rule_issues]
            return {
                "issues": issues,
                "revised_answer": draft_answer,
                "confidence": "low",
                "error_type": "reviewer",
                "fallback": True,
                "rule_result": rule_result,
            }

        # 覆盖 Writer 的全部证据，另补草稿引用的其余段落，避免只看前六条误判引用。
        cited = set(re.findall(r"\[([^\]]+)\]", draft_answer))
        review_evidence = [e for i, e in enumerate(evidence) if i < 25 or e.get("passage_id") in cited]
        evidence_text = "\n\n".join([
            f"[{e['passage_id']}] 来源:{e['source']}\n内容:{e['text']}"
            for e in review_evidence
        ])

        user_prompt = ""
        if history_context:
            user_prompt += history_context + "\n\n"
            user_prompt += "注意:上面历史上下文只用于理解当前追问,不可作为事实证据.\n\n"

        user_prompt += (
            f"用户问题:{question}\n\n"
            f"证据:\n{evidence_text}\n\n"
            f"草稿答案:\n{draft_answer}\n\n"
            f"请严格按 JSON 格式输出.\n"
            f"审核关注:材料筛选数值的准确性/条件完整性/比较公平性.\n"
            f"指出需修复的问题，仅返回简短审核 JSON，不生成或改写正文。"
            f"同时返回 repair_issues 数组，每项包含 type、severity、quote（草稿中的精确原句，必须唯一定位）、reason（错误原因）、suggestion（修改建议）、parameter_paths（仅确需更改的方案字段路径）。"
            f"同段多个问题逐项列出，quote尽量采用能唯一定位的最短原文；重叠问题由程序合并为联合接口，不要返回改写后的段落。"
            f"issues 仍为字符串摘要。无精确定位的问题不会获得写权限。"
        )
        if scheme:
            user_prompt += _build_scheme_consistency_note(scheme, design_mode=False, for_reviewer=True)
        if rule_result is not None:
            rule_note = (
                "\n\n【硬规则检查结果（来自约束表+能量模型，不可推翻，仅可引用）】\n"
                f"violations: {rule_result['rule_checks'].get('violations', [])}\n"
                f"rejects: {rule_result['rule_checks'].get('rejects', [])}\n"
                f"energy_check: {rule_result.get('energy_check')}\n"
                f"condition_missing: {rule_result.get('condition_missing')}\n"
                "若 violations/rejects 非空或 energy_check=energy_mismatch，"
                "issues 必须指出该方案不可行，confidence 必须为 low；只返回判定，不生成正文。"
            )
            user_prompt += rule_note

        try:
            raw = self.llm.chat(REVIEWER_SYSTEM_PROMPT, user_prompt, temperature=0.1, trace_tag="reviewer")
        except Exception as e:
            issues = [f"Reviewer 调用失败: {e}"]
            return {
                "issues": issues,
                "revised_answer": draft_answer if draft_answer else self._build_conservative_answer(question, evidence, issues),
                "confidence": "low",
                "error_type": "reviewer",
                "fallback": True,
            }


        data = safe_json_loads(raw)
        if not isinstance(data, dict) or data.get("confidence") not in ("low", "medium", "high") or not isinstance(data.get("issues"), list):
            raw_preview = raw[:500] if raw else "(空)"
            logger.error(f"Reviewer JSON 解析失败. raw 前500字符: {raw_preview}")
            issues = ["审核输出 JSON 无效，保持草稿待审；不得标记审核通过。"]
            return {
                "issues": issues,
                "revised_answer": self._build_conservative_answer(question, evidence, issues),
                "confidence": "low",
                "error_type": "reviewer",
                "fallback": True,
                "raw": raw,
            }

        issues = data.get("issues", [])
        # 只审核当前草稿；Reviewer 私自给出的新正文未经审核，不能作为最终产物。
        revised_answer = draft_answer
        data["revised_answer"] = draft_answer
        confidence = data.get("confidence", "medium")

        if not revised_answer:
            issues = ["Reviewer 未返回有效 revised_answer"]
            return {
                "issues": issues,
                "revised_answer": self._build_conservative_answer(question, evidence, issues),
                "confidence": "low",
                "fallback": True,
            }

        if issues:
            data["confidence"] = "low"
            if data.get("error_type") not in ("writing", "retrieval"):
                data["error_type"] = "writing"

        # ── 插桩 B 收尾: rule_checks 附到输出, confidence 规则否决 ──
        if rule_result is not None:
            data["rule_checks"] = rule_result
            hard_fail = (
                not rule_result["rule_checks"].get("violations", []) == []
                or not rule_result["rule_checks"].get("rejects", []) == []
                or rule_result.get("energy_check") == "energy_mismatch"
            )
            if hard_fail:
                data["confidence"] = "low"
                data["error_type"] = "writing"
                data["issues"] = list(data.get("issues", [])) + [
                    f"硬规则拦截: {rule_result['rule_checks'].get('violations', []) or rule_result['rule_checks'].get('rejects', [])}"
                ]
            elif rule_result.get("condition_missing") and data.get("confidence") == "high":
                data["confidence"] = "medium"

        data["fallback"] = False
        return data
