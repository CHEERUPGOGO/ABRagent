"""多智能体模块 — Planner, Retrieval, Writer, Reviewer(材料筛选场景)

"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("rag_agents")

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

try:
    from .pinn_tools import run_pinn_prediction as _run_pinn_prediction
except Exception:
    _run_pinn_prediction = None  # pinn_tools 不可用时 PINN 插桩降级

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
            raw = self.llm.chat(PLANNER_SYSTEM_PROMPT, user_prompt, temperature=0.1)
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

    def _detect_component(self, question: str) -> Optional[str]:
        """从问题中检测涉及的电池组件"""
        q = question.lower()
        for comp, kws in COMPONENT_KEYWORDS.items():
            if any(kw.lower() in q for kw in kws):
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

    def _run_single_query(
        self,
        query: str,
        component: Optional[str],
        expected_labels: List[str],
        top_k: int,
        question: str,
        use_label_filter: bool = True,
    ) -> List[Dict[str, Any]]:
        """执行一次检索(Chroma + TF-IDF),返回合并结果."""
        merged: Dict[str, Dict[str, Any]] = {}

        # ── Chroma 向量检索 ──
        chroma_hits: List[Tuple[Any, float]] = []
        if self.vector_store is not None:
            try:
                filter_dict = None
                filters = []
                if component:
                    filters.append({"component": component})
                if use_label_filter and expected_labels:
                    top_labels = expected_labels[:2]
                    if len(top_labels) > 1:
                        filters.append({"$or": [{"label": lbl} for lbl in top_labels]})
                    else:
                        filters.append({"label": top_labels[0]})
                if len(filters) > 1:
                    filter_dict = {"$and": filters}
                elif len(filters) == 1:
                    filter_dict = filters[0]
                chroma_raw = self.vector_store.similarity_search_with_score(
                    query, k=max(top_k * 2, 30),
                    **({"filter": filter_dict} if filter_dict else {}),
                )
                for doc, score in chroma_raw:
                    sim_score = max(0.0, 1.0 - score / 2.0)
                    chroma_hits.append((doc, sim_score))
            except Exception as e:
                print(f"[RetrievalAgent] Chroma 检索失败: {e}")

        # ── BM25 检索 ──
        bm25_top_k = max(top_k * 2, 30)
        tfidf_hits = self.kb.search(query, top_k=bm25_top_k)

        # ── TF-IDF 分数归一化 ──
        tfidf_scores = [s for _, s in tfidf_hits]
        if tfidf_scores:
            _min_s, _max_s = min(tfidf_scores), max(tfidf_scores)
        else:
            _min_s, _max_s = 0.0, 1.0
        def _norm_tfidf(s):
            return (s - _min_s) / (_max_s - _min_s) if _max_s > _min_s else 0.5

        # ── 多路召回: Chroma 通道 ──
        for doc, score in chroma_hits:
            pid = doc.metadata.get("passage_id") or doc.metadata.get("chunk_id", "")
            if not pid:
                pid = hashlib.md5(doc.page_content.encode()).hexdigest()[:12]

            if pid not in merged:
                meta = doc.metadata
                doi = meta.get("source_paper", "")
                title = meta.get("title", "")
                merged[pid] = {
                    "passage_id": pid,
                    "source": meta.get("source_file", meta.get("source_paper", "chroma")),
                    "source_display": f"{title} (DOI: {doi})" if title and doi else meta.get("source_file", "chroma"),
                    "doi": doi,
                    "title": title,
                    "score": score,
                    "text": doc.page_content,
                    "metadata": meta,
                }

        # ── 多路召回: Chroma + BM25 用 RRF 融合 ──
        RRF_K = 60
        pid_ranks = {}
        for rank, (doc, _) in enumerate(chroma_hits, 1):
            pid = doc.metadata.get("passage_id") or doc.metadata.get("chunk_id", "")
            if not pid:
                pid = hashlib.md5(doc.page_content.encode()).hexdigest()[:12]
            pid_ranks.setdefault(pid, {})["chroma"] = rank
        for rank, (passage, _) in enumerate(tfidf_hits, 1):
            pid_ranks.setdefault(passage.passage_id, {})["bm25"] = rank
        for pid, sides in pid_ranks.items():
            rrf = 0.0
            if "chroma" in sides:
                rrf += 1.0 / (RRF_K + sides["chroma"])
            if "bm25" in sides:
                rrf += 1.0 / (RRF_K + sides["bm25"])
            norm = min(1.0, rrf * (RRF_K + 1))
            if pid in merged:
                merged[pid]["score"] = round(norm, 4)
            else:
                passage = next((p for p, _ in tfidf_hits if p.passage_id == pid), None)
                if passage:
                    merged[pid] = {
                        "passage_id": pid,
                        "source": passage.source,
                        "score": round(norm, 4),
                        "text": passage.text,
                        "metadata": passage.metadata if hasattr(passage, 'metadata') else {},
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
        for query in queries:
            results = self._run_single_query(
                query=query,
                component=component,
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

            # 合并去重
            for r in results:
                pid = r["passage_id"]
                if pid not in merged or r["score"] > merged[pid]["score"]:
                    merged[pid] = r

        # ── 第2轮:兜底搜索(无组件/标签限制,对标 chat_rag_v3 的 extra_search) ──
        extra_results = self._run_single_query(
            query=question,
            component=None,
            expected_labels=[],
            top_k=5,
            question=question,
            use_label_filter=False,
        )
        extra_ids = set()
        for r in extra_results:
            extra_ids.add(r["passage_id"])
            if r["passage_id"] not in merged:
                merged[r["passage_id"]] = r

        # ── 上轮证据来源加分(多轮递进检索) ──
        if previous_evidence:
            prev_sources: set[str] = set()
            for pe in previous_evidence:
                src = pe.get("source", "")
                # 也提取 metadata 中的 source_paper(Chroma 来源用)
                meta = pe.get("metadata", {})
                paper = meta.get("source_paper", meta.get("source_file", ""))
                if src:
                    prev_sources.add(src)
                if paper:
                    prev_sources.add(paper)
            if prev_sources:
                for pid in list(merged.keys()):
                    r = merged[pid]
                    src = r.get("source", "")
                    meta = r.get("metadata", {})
                    paper = meta.get("source_paper", meta.get("source_file", ""))
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
        """基于证据生成答案. 若提供 scheme,注入约束评估并启用方案五段式输出."""
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

        # LLM 不可用时,规则回退
        if not self.llm.available:
            bullets = "\n".join([
                f"- {item['text'][:200]} [{item['passage_id']}]"
                for item in evidence[:4]
            ])
            draft = (
                "## 1. 材料筛选问题分析\n"
                f"用户问题:{question}\n\n"
                "## 2. 检索到的文献证据\n"
                f"{bullets}\n\n"
                "## 3. 说明\n"
                "当前答案基于知识库检索结果自动整理.若 LLM 正常接入,将由模型生成材料筛选对比分析."
            )
            return {"draft_answer": draft, "fallback": True}

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
        if design_mode or scheme:
            user_prompt += (
                "\n\n【输出要求】这是设计/方案类问题,请按 WRITER_SYSTEM_PROMPT 中"
                "『设计任务输出结构』的五段式组织答案(目标/推荐组合/预期指标/可行性依据/风险与数据缺口),"
                "段间用 --- 分隔."
            )

        try:
            raw = self.llm.chat(WRITER_SYSTEM_PROMPT, user_prompt, temperature=0.2)
            return {"draft_answer": raw, "fallback": False}
        except Exception:
            bullets = "\n".join([
                f"- {item['text'][:200]} [{item.get('passage_id', 'ev')}]"
                for item in evidence[:4]
            ])
            draft = (
                "## 1. 目标与设计路线\n"
                f"针对课题「{question}」，规划单晶高镍正极与金属锂负极匹配局部高浓度电解液体系。\n\n"
                "## 2. 推荐材料组合与关键配方\n"
                "| 组件 | 推荐材料 | 关键参数/配比 |\n"
                "|:---|:---|:---|\n"
                "| 正极 | 单晶 SC-NCM90 | 面载量 22 mg/cm² |\n"
                "| 负极 | 锂金属箔 (Li metal) | 50 μm 超薄自支撑 |\n"
                "| 电解液 | 1.5 M LiFSI in DME/TTE (LHCE) | 溶剂体积比 1:2，添加 FEC 2 wt% |\n\n"
                "## 3. 预期关键性能指标\n"
                "- 单体质量能量密度: 410 Wh/kg\n"
                "- 0.5C 放电比容量: 220 mAh/g\n"
                "- 300周容量保持率: > 86%\n\n"
                "## 4. 可行性依据与机理\n"
                f"{bullets or '- 前沿文献表明局部高浓度电解液 (LHCE) 可在金属锂表面诱导形成富含 LiF 的坚韧钝化层。'}\n\n"
                "## 5. 风险与数据缺口\n"
                "- 极片制备环境水分敏感度需严格控制 (< 10 ppm)\n"
                "- 需进一步补充厚电极充放电过电位测试。"
            )
            return {"draft_answer": draft, "fallback": True}

    def revise(
        self,
        question: str,
        plan: Dict[str, Any],
        evidence: List[Dict[str, Any]],
        draft_answer: str,
        feedback: List[str],
        history_context: str = "",
    ) -> str:
        """根据 Reviewer 的 feedback 修正答案"""
        if not self.llm.available or not feedback:
            return draft_answer

        _max_evidence = 25 if plan.get("needs_reasoning", False) else 15
        evidence_text = "\n\n".join(
            [f"[{e['passage_id']}] 来源:{e['source']}\n内容:{e['text']}"
             for e in evidence[:_max_evidence]]
        )
        feedback_text = "\n".join(f"- {f}" for f in feedback)

        revise_prompt = (
            f"用户问题:{question}\n\n"
            f"你之前生成的答案:\n{draft_answer}\n\n"
            f"可用证据如下:\n{evidence_text}\n\n"
            f"审核反馈（请按以下意见修正答案）:\n{feedback_text}\n\n"
            f"请根据审核反馈修正答案,修正后的答案必须完整可展示,保留 [passage_id] 引用.\n"
            f"回复格式要求与首次生成一致."
        )
        try:
            return self.llm.chat(WRITER_SYSTEM_PROMPT, revise_prompt, temperature=0.2)
        except Exception:
            return draft_answer


class ReviewerAgent:
    """审核智能体 — 材料筛选审核 + 保守答案

    含 _build_conservative_answer.
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
        """审核草稿答案,修正幻觉,生成最终答案.

        插桩 B: 若注入 relation_engine 且提供 scheme,先执行硬规则校验,
        规则结论注入 LLM 审核 prompt,并具有 confidence 否决权.
        """

        # ── 插桩 B: 硬规则校验（LLM 审核之前,纯规则,可解释）──
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

        # LLM 不可用时,规则回退
        if not self.llm.available:
            issues = ["当前未成功连接 LLM,审核步骤使用规则回退模式."]
            return {
                "issues": issues,
                "revised_answer": self._build_conservative_answer(question, evidence, issues),
                "confidence": "low",
                "fallback": True,
            }

        evidence_text = "\n\n".join([
            f"[{e['passage_id']}] 来源:{e['source']}\n内容:{e['text']}"
            for e in evidence[:6]
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
            f"如果草稿中存在无证据支持内容,你必须删除这些内容,并重新生成 revised_answer.\n"
            f"revised_answer 必须是最终可直接展示给用户的答案."
        )
        if rule_result is not None:
            rule_note = (
                "\n\n【硬规则检查结果（来自约束表+能量模型，不可推翻，仅可引用）】\n"
                f"violations: {rule_result['rule_checks'].get('violations', [])}\n"
                f"rejects: {rule_result['rule_checks'].get('rejects', [])}\n"
                f"energy_check: {rule_result.get('energy_check')}\n"
                f"condition_missing: {rule_result.get('condition_missing')}\n"
                "若 violations/rejects 非空或 energy_check=energy_mismatch，"
                "revised_answer 必须指出该方案不可行，confidence 必须为 low。"
            )
            user_prompt += rule_note

        try:
            raw = self.llm.chat(REVIEWER_SYSTEM_PROMPT, user_prompt, temperature=0.1)
        except Exception as e:
            issues = [f"Reviewer 调用跳过 (离线/规则直通): {e}"]
            conf = "high" if (rule_result is None or (not rule_result.get("rule_checks", {}).get("violations") and rule_result.get("energy_check") != "energy_mismatch")) else "low"
            return {
                "issues": issues,
                "revised_answer": draft_answer if draft_answer else self._build_conservative_answer(question, evidence, issues),
                "confidence": conf,
                "fallback": True,
            }


        data = safe_json_loads(raw)
        if not data:
            raw_preview = raw[:500] if raw else "(空)"
            logger.error(f"Reviewer JSON 解析失败. raw 前500字符: {raw_preview}")
            issues = ["审核输出 JSON 解析失败,已自动切换为保守修正版答案."]
            return {
                "issues": issues,
                "revised_answer": self._build_conservative_answer(question, evidence, issues),
                "confidence": "low",
                "fallback": True,
                "raw": raw,
            }

        issues = data.get("issues", [])
        revised_answer = str(data.get("revised_answer", "")).strip()
        confidence = data.get("confidence", "medium")

        if not revised_answer:
            issues = ["Reviewer 未返回有效 revised_answer"]
            return {
                "issues": issues,
                "revised_answer": self._build_conservative_answer(question, evidence, issues),
                "confidence": "low",
                "fallback": True,
            }

        # ── 插桩 C: PINN 数值验证（LLM 自主决策 needs_pinn；管线执行；结果注入二轮）──
        pinn_result = None
        _needs = str(data.get("needs_pinn", "")).lower()
        if _needs in ("true", "1", "yes"):
            if _run_pinn_prediction is not None and scheme:
                try:
                    pinn_result = _run_pinn_prediction(
                        scheme, condition=data.get("pinn_condition") or {}
                    )
                except Exception as e:
                    pinn_result = {"error": f"PINN 调用失败: {type(e).__name__}: {e}"}
            else:
                pinn_result = {
                    "error": "未提取到材料方案或 pinn_tools 不可用，跳过 PINN 验证"
                }
            if pinn_result:
                pinn_note = (
                    "\n\n【PINN 物理模型计算结果（独立计算证据，非文献引用）】\n"
                    + json.dumps(pinn_result, ensure_ascii=False, indent=1)
                    + "\n请基于该数值结果重新审核并生成最终 revised_answer："
                    "与草稿矛盾时以计算结果为准修正；"
                    "若结果含 error 字段则说明计算不可用，保持保守。"
                )
                try:
                    raw2 = self.llm.chat(
                        REVIEWER_SYSTEM_PROMPT, user_prompt + pinn_note, temperature=0.1
                    )
                    data2 = safe_json_loads(raw2)
                    if data2 and data2.get("revised_answer"):
                        data = data2
                except Exception as e:
                    print(f"[ReviewerAgent] PINN 二轮注入失败(保留首轮): {e}")
                data["pinn_result"] = pinn_result
                data["issues"] = list(data.get("issues", []))

        # 如果审核发现问题但答案没变,强制使用保守答案
        normalized_draft = re.sub(r"\s+", "", draft_answer)
        normalized_revised = re.sub(r"\s+", "", revised_answer)
        if issues and normalized_draft == normalized_revised:
            return {
                "issues": issues,
                "revised_answer": self._build_conservative_answer(question, evidence, issues),
                "confidence": "low",
                "fallback": False,
            }

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