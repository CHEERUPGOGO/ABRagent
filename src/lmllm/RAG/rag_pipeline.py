"""主 RAG Pipeline 编排器 — 串联多智能体协作流程(材料筛选场景)"""

import math
import os
import re
from pathlib import Path
from datetime import datetime
import tiktoken
from typing import Dict, List, Any, Optional, Tuple
from .config import (
    DEFAULT_TOP_K, SEARCH_K,
    CHROMA_DIR, COLLECTION_NAME, EMBEDDING_MODEL, EMBEDDING_BASE_URL,
    EBOOK_CHROMA_DIR, EBOOK_COLLECTION_NAME,
    RETRIEVAL_MODE, CHROMA_TOP_K,
    PLANNER_MODEL, WRITER_MODEL, REVIEWER_MODEL,
    RERANKER_MODEL_PATH, RERANKER_ENABLED, RERANKER_TOP_K,
    RERANKER_BATCH_SIZE, RERANKER_ALPHA,
)
from .llm_client import LLMClient, rule_conservative_answer
from .bm25_kb import BM25KnowledgeBase, Passage, normalize_source_path
from .agents import PlannerAgent, RetrievalAgent, WriterAgent, ReviewerAgent
from .multi_retrieval import MultiRetrieval
from .structured_output import (
    build_answer_markdown, format_evidence_display, format_process_log, save_markdown,
)
from .prompts import get_prompt_summary

try:
    from .relation_engine import RelationEngine
except Exception:
    RelationEngine = None  # 关系引擎不可用时降级为纯 RAG
from .reranker import Qwen3Reranker

class RAGPipeline:
    """多智能体 RAG 主 Pipeline(高比能锂电池材料筛选与方案设计场景).

    数据流:
    文献 / 教材入库 → clean → split → Chroma(qwen3-embedding:8b) & BM25KnowledgeBase
        ↑                                                                          ↓
        └──── RAGPipeline 检索 (MultiRetrieval RRF / RelationEngine 插桩 A / MP MCP) ────┘

    每个 Agent 可绑定不同模型（通过 config.py 或构造函数参数配置）:
    - Planner: 轻量模型做快速问题拆解（离线时仅 Planner 可用规则拆解）
    - Writer: 强模型做材料筛选对比与五段式方案生成（离线 fail-closed）
    - Reviewer: 强模型结合 RelationEngine 插桩 B 做规则与幻觉审核（离线 fail-closed）

    使用方式:
        # 默认: hybrid 混合检索（Chroma + BM25 + RRF，见 config.RETRIEVAL_MODE）
        pipeline = RAGPipeline()
        result = pipeline.run("NCM811和LRMO哪个能量密度更高？")
        print(result["final_answer"])
    """

    def __init__(
        self,
        kb_dir: Optional[Path] = None,
        llm_backend: str = "auto",
        llm_model: Optional[str] = None,
        chroma_dir: Optional[str] = None,
        chroma_collection: Optional[str] = None,
        retrieval_mode: str = "chroma",
        # 每个 Agent 可独立绑定模型
        planner_model: Optional[str] = None,
        writer_model: Optional[str] = None,
        reviewer_model: Optional[str] = None,
        reranker_enabled: Optional[bool] = None,
        # 电子书 Chroma
        ebook_chroma_dir: Optional[str] = None,
        ebook_collection: Optional[str] = None,
        # 显式注入 API 配置
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        max_tokens: Optional[int] = None,
        output_max_tokens: Optional[int] = None,
    ):
        """
        Args:
            kb_dir: (可选) 原始 Markdown 文件夹,用于 BM25 稀疏检索
            llm_backend: "auto" | "openai" | "deepseek" | "ollama" | "rule"
            llm_model: 统一模型名(当未单独指定 Agent 模型时使用)
            chroma_dir: Chroma 持久化目录
            chroma_collection: Chroma collection 名
            retrieval_mode: "chroma" | "hybrid" | "tfidf" ("tfidf" 为兼容保留值, 底层使用 BM25)
            planner_model: Planner Agent 专用模型名(覆盖统一模型)
            writer_model: Writer Agent 专用模型名
            reviewer_model: Reviewer Agent 专用模型名
            reranker_enabled: 是否启用 Qwen3-Reranker-4B 重排序
            api_key: 显式注入的 OpenAI / MiniMax API Key
            api_base: 显式注入的 OpenAI / MiniMax API Base URL
        """
        self.retrieval_mode = retrieval_mode
        self.api_key = api_key
        self.api_base = api_base
        self.max_tokens = max_tokens
        self.output_max_tokens = output_max_tokens

        # BM25 知识库 (hybrid 模式从 Chroma 构建)
        self.kb = BM25KnowledgeBase()
        if retrieval_mode == "hybrid":
            try:
                self.kb = self._init_bm25_from_chroma(
                    chroma_dir or CHROMA_DIR, chroma_collection or COLLECTION_NAME,
                )
            except Exception as e:
                print(f"[RAGPipeline] TF-IDF 从 Chroma 构建失败: {e}")

        # Chroma 向量存储(文献库)
        self.vector_store = None
        if retrieval_mode in ("chroma", "hybrid"):
            self.vector_store = self._init_chroma(
                chroma_dir or CHROMA_DIR,
                chroma_collection or COLLECTION_NAME,
            )

        # 电子书 Chroma 向量存储(教科书/原理库)
        self.ebook_vector_store = None
        _ebook_dir = ebook_chroma_dir or EBOOK_CHROMA_DIR
        _ebook_coll = ebook_collection or EBOOK_COLLECTION_NAME
        if Path(_ebook_dir).exists():
            self.ebook_vector_store = self._init_chroma(_ebook_dir, _ebook_coll)
            if self.ebook_vector_store:
                print(f"[RAGPipeline] 电子书 Chroma 已连接: {_ebook_coll} @ {_ebook_dir}")

        # 双库混合检索器（封装 Chroma+BM25+RRF，供 textbook/both 路由使用）
        self.multi_retriever = MultiRetrieval(
            lit_chroma_dir=chroma_dir or CHROMA_DIR,
            lit_collection=chroma_collection or COLLECTION_NAME,
            ebook_chroma_dir=_ebook_dir,
            ebook_collection=_ebook_coll,
            base_url=os.getenv("OLLAMA_BASE_URL", EMBEDDING_BASE_URL),
            model=os.getenv("EMBEDDING_MODEL", EMBEDDING_MODEL),
        )

        # 统一的 LLM 客户端(作为轮换默认值)
        self._default_model = llm_model
        self._backend = llm_backend

        # 创建各 Agent 的 LLM 实例(llm_backend)
        self.planner_llm = self._create_agent_llm(planner_model or PLANNER_MODEL or llm_model, llm_backend)
        self.writer_llm = self._create_agent_llm(writer_model or WRITER_MODEL or llm_model, llm_backend)
        self.reviewer_llm = self._create_agent_llm(reviewer_model or REVIEWER_MODEL or llm_model, llm_backend)

        # 存储实际使用的模型名(用于状态展示)
        self._planner_model_name = planner_model or PLANNER_MODEL or llm_model or "default"
        self._writer_model_name = writer_model or WRITER_MODEL or llm_model or "default"
        self._reviewer_model_name = reviewer_model or REVIEWER_MODEL or llm_model or "default"

        # 兼容:也保留统一 LLM 客户端(供外部 Baseline 使用)
        self.llm = LLMClient(model_name=llm_model, backend=llm_backend, api_key=self.api_key, api_base=self.api_base)


        # 智能体
        # 关系引擎（阶段3插桩: 约束过滤 + 规则校验; 不可用时降级为纯 RAG）
        self.relation_engine = None
        if RelationEngine is not None:
            try:
                self.relation_engine = RelationEngine()
            except Exception as e:
                print(f"[RAGPipeline] 关系引擎初始化失败(降级为纯RAG): {e}")

        self.planner = PlannerAgent(self.planner_llm)
        self.retriever = RetrievalAgent(
            self.kb,
            vector_store=self.vector_store,
            relation_engine=self.relation_engine,
        )
        self.writer = WriterAgent(self.writer_llm)
        self.reviewer = ReviewerAgent(self.reviewer_llm, relation_engine=self.relation_engine)

        # ── Reranker (Qwen3-Reranker-4B) ──
        self._reranker_enabled = RERANKER_ENABLED if reranker_enabled is None else reranker_enabled
        self.reranker: Optional[Qwen3Reranker] = None
        if self._reranker_enabled:
            try:
                self.reranker = Qwen3Reranker(
                    model_path=RERANKER_MODEL_PATH,
                    batch_size=RERANKER_BATCH_SIZE,
                )
                print(f"[RAGPipeline] Reranker 已启用: {RERANKER_MODEL_PATH}")
            except Exception as e:
                print(f"[RAGPipeline] Reranker 加载失败,已禁用: {e}")
                self._reranker_enabled = False

        # 多轮递进检索:缓存上轮 evidence 用于下轮来源加权
        self._last_evidence: Optional[List[Dict[str, Any]]] = None

    def _create_agent_llm(self, model_name: Optional[str], backend: str) -> LLMClient:
        """为 Agent 创建独立的 LLM 实例."""
        return LLMClient(
            model_name=model_name or None,
            backend=backend,
            api_key=getattr(self, "api_key", None),
            api_base=getattr(self, "api_base", None),
            max_tokens=getattr(self, "max_tokens", None),
            output_max_tokens=getattr(self, "output_max_tokens", None),
        )


    @staticmethod
    def _init_chroma(
        chroma_dir: str,
        collection_name: str,
    ):
        """初始化 Chroma 向量存储,使用与入库一致的 qwen3-embedding:8b"""
        try:
            from langchain_ollama import OllamaEmbeddings
            from langchain_chroma import Chroma

            persist_path = Path(chroma_dir)
            if not persist_path.exists():
                print(f"[RAGPipeline] Chroma 目录不存在: {chroma_dir},跳过向量检索")
                return None

            embeddings = OllamaEmbeddings(
                model=EMBEDDING_MODEL,
                base_url=EMBEDDING_BASE_URL,
            )
            store = Chroma(
                collection_name=collection_name,
                embedding_function=embeddings,
                persist_directory=str(persist_path),
            )
            _ = store.similarity_search("test", k=1)
            print(f"[RAGPipeline] Chroma 已连接: {collection_name} @ {chroma_dir}")
            return store
        except Exception as e:
            print(f"[RAGPipeline] Chroma 初始化失败: {e}")
            return None

    @staticmethod
    def _init_bm25_from_chroma(chroma_dir: str, collection_name: str, max_passages: int = 10000) -> "BM25KnowledgeBase":
        """从 Chroma 集合或本地段落 JSON 构建 BM25 索引, 保持 passage_id 对齐.
        注: 超过 max_passages 时采样前 max_passages 条构建内存 BM25，全量检索由 Chroma ANN 向量检索负责。
        """
        import hashlib
        import json
        from .config import PROJECT_ROOT

        kb = BM25KnowledgeBase()
        loaded = False
        paper_title_map: Dict[str, str] = {}

        def _collect_passage(pid: str, text: str, raw_source: str, raw_meta: Optional[dict]) -> None:
            meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
            if meta.get("source_file"):
                meta["source_file"] = normalize_source_path(str(meta["source_file"]))
            source = normalize_source_path(
                str(raw_source or meta.get("source_file") or meta.get("source_paper") or "chroma")
            )
            if meta.get("source"):
                meta["source"] = normalize_source_path(str(meta["source"]))

            title = str(meta.get("title") or "").strip()
            if title:
                sp = str(meta.get("source_paper") or "").strip()
                sf = str(meta.get("source_file") or "").strip()
                if sp and sp not in paper_title_map:
                    paper_title_map[sp] = title
                if sf and sf not in paper_title_map:
                    paper_title_map[sf] = title
                if source and source not in paper_title_map:
                    paper_title_map[source] = title

            kb.add_passage(passage_id=pid, text=text, source=source, metadata=meta)

        # 1. 尝试从 Chroma 集合读取
        try:
            import chromadb
            client = chromadb.PersistentClient(str(chroma_dir))
            collection = client.get_collection(collection_name)
            total = collection.count()
            if total > 0:
                target_count = min(total, max_passages)
                print(f"[RAGPipeline] BM25 从 Chroma 构建: {target_count}/{total} 条段落 (全量走向量检索)")
                batch_size = 1000
                offset = 0
                while offset < target_count:
                    curr_limit = min(batch_size, target_count - offset)
                    batch = collection.get(
                        offset=offset, limit=curr_limit,
                        include=["documents", "metadatas"],
                    )
                    docs = batch.get("documents", [])
                    metas = batch.get("metadatas", [])
                    for i, text in enumerate(docs):
                        meta = metas[i] if i < len(metas) and metas[i] else {}
                        raw_pid = meta.get("passage_id") or meta.get("chunk_id")
                        pid = str(raw_pid) if raw_pid else hashlib.md5(text.encode()).hexdigest()[:12]
                        source = meta.get("source_file", meta.get("source_paper", "chroma"))
                        _collect_passage(pid, text, str(source), meta)
                    offset += curr_limit
                loaded = True
        except Exception:
            pass

        # 2. 如果 Chroma 不可用，从本地 5,471 段落数据源直接构建 BM25 索引
        if not loaded:
            para_json_candidates = [
                PROJECT_ROOT / "miner" / "json" / "100" / "paragraph_metadata_v4.json",
                PROJECT_ROOT / "miner" / "json" / "100" / "paragraph_metadata_v4_20260622_155323.json",
                PROJECT_ROOT / "miner" / "json" / "test_paragraphs.json",
            ]
            para_f = next((p for p in para_json_candidates if p.exists()), None)
            if para_f:
                try:
                    with open(para_f, "r", encoding="utf-8", errors="ignore") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            for idx, item in enumerate(data[:3000]):
                                txt = item.get("paragraph_context") or item.get("text", "")
                                if txt:
                                    pid = item.get("_id") or item.get("passage_id") or hashlib.md5(txt.encode()).hexdigest()[:12]
                                    src = item.get("source_paper") or item.get("source") or "Academic Paper"
                                    _collect_passage(str(pid), txt, str(src), item)
                except Exception:
                    pass

        # 第二遍遍历: 用 sibling chunks 的题名回填缺失 title 的段落
        if paper_title_map:
            for passage in kb.passages:
                meta = passage.metadata if isinstance(passage.metadata, dict) else {}
                if not str(meta.get("title") or "").strip():
                    sp = str(meta.get("source_paper") or "").strip()
                    sf = normalize_source_path(str(meta.get("source_file") or passage.source or ""))
                    backfill = paper_title_map.get(sp) or paper_title_map.get(sf)
                    if backfill:
                        if not isinstance(passage.metadata, dict):
                            passage.metadata = {}
                        passage.metadata["title"] = backfill

        # 构建 BM25 索引并挂载 paper_title_map
        kb.build_index()
        if paper_title_map:
            paper_title_map.update(getattr(kb, "paper_title_map", {}) or {})
            kb.paper_title_map = paper_title_map
        print(f"[RAGPipeline] BM25 索引完成: {kb.passage_count} 段落")
        return kb


    @property
    def status(self) -> str:
        """知识库与检索系统状态"""
        parts = []
        parts.append(f"Planner: {self._planner_model_name}")
        parts.append(f"Writer: {self._writer_model_name}")
        parts.append(f"Reviewer: {self._reviewer_model_name}")
        parts.append(f"知识库: {self.kb.passage_count} 段落")
        if self.vector_store:
            parts.append("向量检索: 已启用")
        if self._reranker_enabled and self.reranker:
            parts.append("Reranker: 已启用")
        return " | ".join(parts)

    def runtime_status(self) -> str:
        """详细的运行时状态"""
        lines = [
            f"- Planner 模型:{self._planner_model_name} | {self.planner_llm.status_text()}",
            f"- Writer 模型:{self._writer_model_name} | {self.writer_llm.status_text()}",
            f"- Reviewer 模型:{self._reviewer_model_name} | {self.reviewer_llm.status_text()}",
            f"- 检索模式:{self.retrieval_mode}",
        ]
        if self.vector_store:
            lines.append(f"- Chroma 集合:{COLLECTION_NAME}")
        if self.ebook_vector_store:
            lines.append(f"- 电子书 Chroma 集合:{EBOOK_COLLECTION_NAME}")
        else:
            lines.append("- 电子书 Chroma:未加载")
        lines.append(f"- 混合检索器: {self.multi_retriever.status}")
        return "\n".join(lines)

    def refresh_kb(self, kb_dir: Optional[Path] = None) -> None:
        """刷新知识库索引"""
        self.kb.refresh(kb_dir)

    def _compute_dynamic_weights(
        self, question: str, plan: Dict[str, Any],
    ) -> Tuple[float, float]:
        """根据问题内容动态计算双库权重。

        Returns:
            (lit_weight, ebook_weight)
        """
        q = question.lower()

        # 理论/原理关键词 → 电子书加权
        theory_kws = [
            "原理", "公式", "定义", "概念", "推导", "理论", "机理", "机制",
            "thermodynamic", "kinetic", "nernst", "能斯特",
            "电化学窗口", "离子电导", "扩散系数", "迁移数",
            "极化", "过电位", "sei", "cei", "成膜",
            "枝晶", "dendrite", "成核", "nucleation",
        ]
        # 实验/实证关键词 → 文献加权
        exp_kws = [
            "容量", "能量密度", "电压", "循环", "倍率",
            "掺杂", "包覆", "合成", "制备", "测试",
            "ncm", "nca", "lfp", "lrmo", "llzo",
            "比容量", "库仑效率", "容量保持",
            "capacity", "density", "voltage",
            "review", "最新", "进展", "对比", "比较",
        ]

        theory_score = sum(1 for kw in theory_kws if kw in q)
        exp_score = sum(1 for kw in exp_kws if kw in q)

        # 也看 task_understanding
        tu = (plan.get("task_understanding") or "").lower()
        theory_score += sum(0.5 for kw in theory_kws if kw in tu)
        exp_score += sum(0.5 for kw in exp_kws if kw in tu)

        total = theory_score + exp_score
        if total == 0:
            return 0.7, 0.3  # 默认

        # 映射到 [0.0, 1.0] 区间，但保证至少 0.1 的权重
        lit_w = max(0.1, min(1.0, exp_score / total))
        ebk_w = max(0.1, min(1.0, theory_score / total))
        return round(lit_w, 2), round(ebk_w, 2)

    def retrieve_context(
        self,
        db_type: str,
        question: str,
        queries: List[str],
        focus_component: Optional[str] = None,
        focus_labels: Optional[List[str]] = None,
        use_label_filter: bool = True,
        previous_evidence: Optional[List[Dict[str, Any]]] = None,
        top_k: int = DEFAULT_TOP_K,
        lit_weight: Optional[float] = None,
        ebook_weight: Optional[float] = None,
    ) -> Dict[str, Any]:
        """根据 db_type 路由到不同检索源。

        Args:
            db_type: "literature" | "textbook" | "both"
            question: 用户原始问题
            queries: Planner 生成的子检索问题列表
            focus_component: 焦点组件
            focus_labels: 焦点标签
            use_label_filter: 是否使用标签过滤
            previous_evidence: 上轮证据(多轮递进检索用)
            top_k: 最终返回条数
            lit_weight: (可选)文献库权重,仅 both 模式生效
            ebook_weight: (可选)电子书权重,仅 both 模式生效

        Returns:
            {"db_type": str, "results": List[Dict], "search_logs": List[Dict]}
        """
        import hashlib
        from auto_battery_research.util.adaptive_retrieval import channel_status

        if db_type == "textbook":
            # 电子书 Chroma + BM25 + RRF
            if not getattr(self, "ebook_vector_store", None):
                print("[retrieve_context] 电子书库未加载,降级为 both")
                fallback = self.retrieve_context(
                    "both", question, queries, focus_component, focus_labels,
                    use_label_filter, previous_evidence, top_k,
                    lit_weight=lit_weight, ebook_weight=ebook_weight,
                )
                fallback.setdefault("channel_diagnostics", []).append({"channel": "textbook", "status": "UNAVAILABLE", "reason": "教科书库未加载，已回退"})
                fallback["status"] = channel_status(fallback["channel_diagnostics"], fallback.get("results", []))
                return fallback
            all_results: Dict[str, Dict[str, Any]] = {}
            channel_diagnostics = []
            for q in queries:
                try:
                    self.multi_retriever.last_diagnostics = []
                    batch = self.multi_retriever.search_ebook(q, top_k=max(top_k, 20))
                    if not isinstance(batch, (list, tuple)):
                        continue
                    for r in batch:
                        if not isinstance(r, dict) or "passage_id" not in r:
                            continue
                        pid = r["passage_id"]
                        r_item = dict(r)
                        if r_item.get("source"):
                            r_item["source"] = normalize_source_path(r_item["source"])
                        if pid not in all_results:
                            all_results[pid] = r_item
                        else:
                            if not all_results[pid].get("title") and r_item.get("title"):
                                all_results[pid]["title"] = r_item["title"]
                            if r_item.get("score", 0) > all_results[pid].get("score", 0):
                                if not r_item.get("title") and all_results[pid].get("title"):
                                    r_item["title"] = all_results[pid]["title"]
                                all_results[pid] = r_item
                    channel_diagnostics.extend(self.multi_retriever.last_diagnostics)
                except Exception as e:
                    channel_diagnostics.append({"query": q, "channel": "textbook", "status": "ERROR", "error": str(e)})
                    print(f"[retrieve_context] 电子书检索失败({q}): {e}")
            results = sorted(all_results.values(), key=lambda x: x.get("score", 0), reverse=True)
            return {
                "db_type": "textbook",
                "results": results[:top_k],
                "search_logs": [], "channel_diagnostics": channel_diagnostics, "status": channel_status(channel_diagnostics, results),
            }

        elif db_type == "both":
            # 双库混合检索: 文献通道走 RetrievalAgent.run (保留组件过滤/母块晋升/关联约束),
            # 电子书通道在可用时按权重缩并合入
            _lw = lit_weight if lit_weight is not None else 0.7
            _ew = ebook_weight if ebook_weight is not None else 0.3

            retrieval = self.retriever.run(
                question=question,
                queries=queries,
                focus_component=focus_component,
                focus_labels=focus_labels,
                use_label_filter=use_label_filter,
                previous_evidence=previous_evidence,
                top_k_per_query=top_k,
            )
            channel_diagnostics = list(retrieval.get("channel_diagnostics") or [])

            all_results: Dict[str, Dict[str, Any]] = {}
            if _lw > 0:
                for r in retrieval.get("results", []):
                    if not isinstance(r, dict) or "passage_id" not in r:
                        continue
                    r_item = dict(r)
                    r_item.setdefault("_source_type", "literature")
                    if r_item.get("source"):
                        r_item["source"] = normalize_source_path(r_item["source"])
                    pid = r_item["passage_id"]
                    if pid not in all_results:
                        all_results[pid] = r_item
                    else:
                        if not all_results[pid].get("title") and r_item.get("title"):
                            all_results[pid]["title"] = r_item["title"]
                        if r_item.get("score", 0) > all_results[pid].get("score", 0):
                            if not r_item.get("title") and all_results[pid].get("title"):
                                r_item["title"] = all_results[pid]["title"]
                            all_results[pid] = r_item

            mr = getattr(self, "multi_retriever", None)
            if mr is not None and bool(getattr(mr, "_ebook_available", False)) and _ew > 0:
                factor = _ew / max(_lw, 0.01)
                for q in queries:
                    try:
                        mr.last_diagnostics = []
                        batch = mr.search_ebook(q, top_k=10)
                        channel_diagnostics.extend(mr.last_diagnostics)
                        if not isinstance(batch, (list, tuple)):
                            continue
                        for r in batch:
                            if not isinstance(r, dict) or "passage_id" not in r:
                                continue
                            r_item = dict(r)
                            r_item.setdefault("_source_type", "ebook")
                            if r_item.get("source"):
                                r_item["source"] = normalize_source_path(r_item["source"])
                            r_item["score"] = round(float(r_item.get("score", 0.0)) * factor, 6)
                            pid = r_item["passage_id"]
                            if pid not in all_results:
                                all_results[pid] = r_item
                            else:
                                if not all_results[pid].get("title") and r_item.get("title"):
                                    all_results[pid]["title"] = r_item["title"]
                                if r_item["score"] > all_results[pid].get("score", 0):
                                    if not r_item.get("title") and all_results[pid].get("title"):
                                        r_item["title"] = all_results[pid]["title"]
                                    all_results[pid] = r_item
                    except Exception as e:
                        channel_diagnostics.append({"query": q, "channel": "textbook", "status": "ERROR", "error": str(e)})
                        print(f"[retrieve_context] 双库电子书通道检索失败({q}): {e}")

            results = sorted(all_results.values(), key=lambda x: x.get("score", 0), reverse=True)
            result = {
                "db_type": "both",
                "results": results[:top_k],
                "search_logs": retrieval.get("search_logs", []),
                "channel_diagnostics": channel_diagnostics,
                "status": channel_status(channel_diagnostics, results),
            }
            if retrieval.get("constraint_log"):
                result["constraint_log"] = retrieval["constraint_log"]
            return result

        # 默认: literature (文献库)
        retrieval = self.retriever.run(
            question=question,
            queries=queries,
            focus_component=focus_component,
            focus_labels=focus_labels,
            use_label_filter=use_label_filter,
            previous_evidence=previous_evidence,
            top_k_per_query=top_k,
        )
        for r in retrieval.get("results", []):
            r["_source_type"] = "literature"
        result = {
            "db_type": "literature",
            "results": retrieval.get("results", []),
            "search_logs": retrieval.get("search_logs", []),
            "channel_diagnostics": retrieval.get("channel_diagnostics", []),
            "status": channel_status(retrieval.get("channel_diagnostics", []), retrieval.get("results", [])),
        }
        if retrieval.get("constraint_log"):
            result["constraint_log"] = retrieval["constraint_log"]
        return result

    def run(
        self,
        question: str,
        history_context: str = "",
        top_k: int = DEFAULT_TOP_K,
        previous_evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """执行完整的多智能体 RAG 问答.

        Returns:
            {
                "question": str,
                "plan": Dict,
                "retrieval": Dict,
                "writer_output": Dict,
                "reviewer_output": Dict,
                "final_answer": str,
                "evidence": List[Dict],
                "timestamp": str,
            }
        """
        from auto_battery_research.util.design_repair import resume_pipeline
        if getattr(self, "repair_resume", None):
            return resume_pipeline(self, self.repair_resume, question, history_context, top_k)
        question = question.strip()
        if not question:
            return {
                "question": question,
                "final_answer": "请输入问题.",
                "evidence": [],
                "timestamp": datetime.now().isoformat(),
            }

        print(f"[RAG Pipeline] 材料筛选问题: {question}")

        # ── Stage 1: Planner ──
        print("  [1/4] Planner 规划中...")
        saved_retrieval = getattr(self, "retrieval_resume", None)
        plan = saved_retrieval["plan"] if saved_retrieval else self.planner.run(getattr(self, "requirement_source", None) or question, history_context)
        from auto_battery_research.util.research_contract import bindings, field_equal
        self.reviewer.requirements = plan.get("requirements")
        if getattr(self, "scheme_context", None) is not None and plan.get("requirements"):
            import copy
            self.scheme_context = copy.deepcopy(self.scheme_context)
            for key, field in bindings(plan["requirements"]).items():
                previous = self.scheme_context["parameters"].get(key, {})
                if previous.get("locked") and not field_equal(key, previous.get("value"), field["value"]):
                    raise ValueError("课题固定值与已有用户锁定冲突: " + key)
                self.scheme_context["parameters"][key] = field
        print(f"  -> 拆解为 {len(plan.get('retrieval_queries', []))} 个子问题")
        print(f"  -> Planner 模式: {'LLM 生成' if not plan.get('fallback') else '规则回退'}")

        # ── Stage 2: Retrieval (db_type 路由) ──
        # 推荐/推理类问题关闭标签硬过滤,让 reranker 做纯语义筛选
        if saved_retrieval:
            retrieval = saved_retrieval["retrieval"]
            evidence = saved_retrieval["evidence"]
            _db_type = saved_retrieval["db_type"]
        else:
            _needs_reasoning = plan.get("needs_reasoning", False)
            # 获取 db_type, 强制校验合法性；LLM 失败/格式错误时兜底为 both
            _db_type = plan.get("db_type", "both")
            if plan.get("fallback", False):
                _db_type = "both"
            if _db_type not in ("literature", "textbook", "both"):
                print(f"  -> 无效 db_type={_db_type}, 兜底为 both")
                _db_type = "both"
            print(f"  [2/4] 检索中 (db_type={_db_type})...")
            # 双库模式时动态计算权重
            _lit_w, _ebk_w = None, None
            if _db_type == "both":
                _lit_w, _ebk_w = self._compute_dynamic_weights(question, plan)
                print(f"  -> 动态权重: lit={_lit_w}, ebook={_ebk_w}")
            retrieval = self.retrieve_context(
                db_type=_db_type,
                question=question,
                queries=plan.get("retrieval_queries", [question]),
                focus_component=plan.get("focus_component"),
                focus_labels=plan.get("focus_labels"),
                use_label_filter=not _needs_reasoning,
                previous_evidence=previous_evidence,
                top_k=top_k,
                lit_weight=_lit_w,
                ebook_weight=_ebk_w,
            )
            from auto_battery_research.tools.materials_project import augment_mp_evidence
            retrieval = augment_mp_evidence(retrieval, plan, top_k)
            evidence = retrieval.get("results", [])[:top_k]
            print(f"  -> 召回 {len(evidence)} 条证据 (数据源: {retrieval.get('db_type', _db_type)})")

            # ── Stage 2.5: Reranker 重排序 ──
            if self._reranker_enabled and self.reranker and evidence:
                print("  [2.5/4] Reranker 语义重排序中...")
                evidence = self.reranker.rerank(
                    query=question,
                    passages=evidence,
                    top_k=RERANKER_TOP_K,
                    alpha=RERANKER_ALPHA,
                )
                print(f"  -> 重排后保留 {len(evidence)} 条")


        from auto_battery_research.util.adaptive_retrieval import initial_assessment, select_context, options as retrieval_options
        retrieval_seed = {"question": getattr(self, "requirement_source", None) or question, "plan": plan,
                          "db_type": _db_type, "retrieval": retrieval, "evidence": list(evidence)}
        def save_retrieval():
            path = getattr(self, "repair_checkpoint_path", None)
            if path:
                from auto_battery_research.util.scheme_ledger import atomic_write
                import json
                atomic_write(path.with_name("stage4_retrieval_checkpoint.json"), json.dumps({
                    **retrieval_seed, "checkpoint_identity": getattr(self, "repair_checkpoint_identity", None)}, ensure_ascii=False, indent=2))
        # 写作之前评估首次召回；可复用检查点，不重复检索/重开窗口。
        saved_retrieval = getattr(self, "retrieval_resume", None)
        if saved_retrieval:
            retrieval_seed = saved_retrieval
        initial_assessment(self, retrieval_seed, save_retrieval)
        evidence = retrieval_seed["evidence"]
        self.writer.requirement_items = list((plan.get("requirements") or {}).get("items", []))
        self.writer.preferred_evidence_ids = retrieval_seed["retrieval_state"]["preferred_ids"]
        self.writer.working_evidence_ids = retrieval_seed["retrieval_state"]["working_ids"]
        self.writer.context_options = retrieval_options(self)
        writer_evidence = select_context(retrieval_seed, purpose="writer", config=retrieval_options(self))
        save_retrieval()

        # ── Stage 3: Writer ──
        print("  [3/4] Writer 生成中...")
        _writer_scheme = self._extract_scheme(question, plan)
        if plan.get("requirements"):
            # 语义指定值和已定方案优先；问题中的对照、否定或背景提及不自动成为选型。
            from auto_battery_research.util.research_contract import bindings as requirement_bindings
            declared = dict((getattr(self, "scheme_context", None) or {}).get("parameters", {}))
            declared.update(requirement_bindings(plan["requirements"]))
            _writer_scheme = {}
            for comp in ("cathode", "anode", "electrolyte"):
                value = declared.get(comp + ".material", {}).get("value")
                if value is not None:
                    _writer_scheme[comp] = (self.relation_engine.normalize(str(value)) if self.relation_engine else None) or value
        inherited_design = getattr(self, "inherited_design", None)
        if inherited_design:
            _writer_scheme = _writer_scheme or {}
            for component in ("cathode", "anode", "electrolyte"):
                if not _writer_scheme.get(component):
                    _writer_scheme[component] = inherited_design.get(component)
        geometry_candidate = None
        geometry_error = None
        writer_question = question
        scheme_context = getattr(self, "scheme_context", None)
        if scheme_context:
            from auto_battery_research.util.scheme_ledger import render
            writer_question += "\n当前方案表（用户锁定值不可修改；有冲突只提出候选）：\n" + render(scheme_context)
        # 工作流设计模式：先冻结结构化参数，再供 Writer/Reviewer 引用。
        if (getattr(self, "structured_geometry_enabled", False) and plan.get("requirements", {}).get("scheme_required", True)
                and (not plan.get("requirements")
                     or all((_writer_scheme or {}).get(c) for c in ("cathode", "anode", "electrolyte")))):
            try:
                import json
                from auto_battery_research.simulation.geometry_contract import GEOMETRY_SYSTEM_PROMPT, build_geometry_contract, constrain_generated_np
                from auto_battery_research.tools.geometry_revision import resolve_input_ranges
                from .llm_client import safe_json_loads
                materials = {k: (_writer_scheme or {}).get(k) for k in ("cathode", "anode", "electrolyte")}
                inherited = getattr(self, "inherited_design", None)
                if inherited and all(inherited.get(k) == materials.get(k) for k in ("cathode", "anode", "electrolyte")):
                    geometry = inherited["geometry"]
                    geometry_candidate = {c: {f: geometry[c].get(f) for f in ("L_um", "porosity", "R_s_um", "D_s_m2s")} for c in ("cathode", "anode")}
                else:
                    raw = self.writer_llm.chat(GEOMETRY_SYSTEM_PROMPT, json.dumps({
                        "design_request": question, "materials": materials, "evidence": evidence[:25],
                        "current_scheme_table": scheme_context,
                        "ranges_SI": resolve_input_ranges(materials.get("cathode"), materials.get("anode")),
                    }, ensure_ascii=False), temperature=0.1, trace_tag="geometry_design")
                    geometry_candidate = constrain_generated_np(safe_json_loads(raw), getattr(self, "original_design_request", question))
                    geometry = build_geometry_contract(geometry_candidate, materials, evidence)
                writer_question += "\n【已校验结构化几何，正文不得变更；工程候选不是用户实测值】\n" + json.dumps(geometry, ensure_ascii=False)
            except Exception as exc:
                geometry_error = str(exc)
        writer_output = self.writer.run(
            question=writer_question,
            plan=plan,
            evidence=writer_evidence,
            history_context=history_context,
            scheme=_writer_scheme,
        )
        print(f"  -> {'规则回退' if writer_output.get('fallback') else 'LLM 生成'}")

        # ── Stage 4: Reviewer (带分层回退) ──
        from auto_battery_research.util.design_repair import review_design, review_verdict
        baseline_scheme = self._extract_scheme(question, plan, writer_output.get("draft_answer", "")) or _writer_scheme
        # 新课题主方案由已审核声明维护；正文候选不决定电解液或添加剂。
        if plan.get("requirements"):
            baseline_scheme = dict(_writer_scheme or {})
            baseline_scheme.pop("additives", None)
        for component in ("cathode", "anode", "electrolyte"):
            if (_writer_scheme or {}).get(component):
                baseline_scheme[component] = _writer_scheme[component]
        if baseline_scheme and locals().get("geometry"):
            baseline_scheme["geometry"] = geometry
        seed = {"question": question, "draft": writer_output.get("draft_answer", ""), "evidence": evidence,
                "plan": plan, "retrieval": retrieval, "db_type": _db_type,
                "writer_output": writer_output, "scheme": baseline_scheme,
                "geometry_candidate": geometry_candidate, "geometry_error": geometry_error,
                "checkpoint_identity": getattr(self, "repair_checkpoint_identity", None)}
        seed["retrieval_state"] = retrieval_seed["retrieval_state"]
        from auto_battery_research.util.research_contract import prepare_review
        prepare_review(self, seed)
        _draft, reviewer_output, evidence, repair_parameters, repair_audit = review_design(
            self, seed, writer_question, history_context, top_k)
        final_answer = _draft
        review_completed, review_passed = review_verdict(_draft, reviewer_output, plan.get("requirements"))

        # 追加参考来源（仅当存在有效正文时追加）
        if final_answer.strip():
            ref_section = self._build_reference_section(evidence)
            final_answer = final_answer + ref_section

        # 提取结构化材料组合方案 (供 Stage 5 物理仿真与门禁检查消费)
        extracted_scheme = seed.get("scheme") or _writer_scheme or self._extract_scheme(question, plan, writer_output.get("draft_answer", ""))
        rule_checks = {}
        if self.relation_engine:
            if extracted_scheme:
                try:
                    rule_checks = self.relation_engine.check_scheme(
                        extracted_scheme,
                        claimed_energy=self._extract_energy_claim(final_answer),
                        answer_text=final_answer,
                    )
                except Exception as e:
                    rule_checks = {
                        "status": "ERROR",
                        "error": str(e),
                        "rule_checks": {"violations": [f"RelationEngine failure: {e}"], "rejects": [], "inclusions": []},
                        "confidence": "low",
                    }
            else:
                rule_checks = {
                    "status": "SCHEME_EXTRACTION_FAILED",
                    "rule_checks": {"violations": ["未能从生成的方案或问题中提取结构化材料实体 (cathode/anode/electrolyte)"], "rejects": [], "inclusions": []},
                    "confidence": "low",
                }

        # 综合评定最终置信度 (以最终生成的结构化方案及 RelationEngine 校验为准)
        rule_conf = rule_checks.get("confidence") if isinstance(rule_checks, dict) else None
        rev_conf = reviewer_output.get("confidence", "medium")
        if not review_passed or rule_conf == "low":
            confidence = "low"
        elif rule_conf in ("high", "medium"):
            confidence = "medium" if "medium" in (rule_conf, rev_conf) else "high"
        else:
            confidence = rev_conf


        return {
            "schema_version": "1.0",
            "question": question,
            "target": question,
            "plan": plan,
            "db_type": retrieval.get("db_type", _db_type),
            "retrieval": retrieval,
            "writer_output": writer_output,
            "reviewer_output": reviewer_output,
            "review_passed": review_passed,
            "review_completed": review_completed,
            "final_answer": final_answer,
            "evidence": evidence,
            "confidence": confidence,
            "scheme": extracted_scheme or {},
            "geometry_candidate": geometry_candidate,
            "repair_parameters": repair_parameters,
            "repair_audit": repair_audit,
            "retrieval_state": seed.get("retrieval_state"),
            "research_draft": seed.get("research_draft"),
            "geometry_error": geometry_error,
            "rule_checks": rule_checks,
            "timestamp": datetime.now().isoformat(),
        }


    # 用户问题 (goal) 中点名体系的锚定关键词: 方案提取优先与 goal 一致,
    # 防止泛正文中的对比性提及 (如"对比锂金属") 抢走材料位
    _ANCHOR_KEYWORDS: Dict[str, Dict[str, Tuple[str, ...]]] = {
        "cathode": {
            "NCM811": ("ncm811", "811", "ni90", "高镍"),
            "LRMO": ("lrmo", "富锂", "无钴富锂", "li1.2mn0.6ni0.2o2", "li1.2ni0.2mn0.6o2"),
            "LNMO": ("lnmo", "尖晶石", "高压尖晶石", "lini0.5mn1.5o4"),
            "LFP": ("lfp", "磷酸铁锂"),
            "LCO": ("lco", "钴酸锂"),
        },
        "anode": {
            "si_base": ("硅", "si-c", "si/c", "silicon", "si anode"),
            "li_metal": ("锂金属", "金属锂", "li metal", "li-metal", "lithium metal", "锂箔", "li箔"),
            "graphite": ("石墨", "graphite"),
        },
        "electrolyte": {
            "carbonate_ec": (
                "lipf6", "lipf₆", "ec/dmc", "ec+dmc", "ec/emc", "ec+emc",
                "碳酸酯", "常规碳酸酯", "carbonate", "1 mol/l", "1m lipf6", "1m lipf₆",
            ),
            "lhce": ("lhce", "局部高浓", "localized high-concentration"),
            "high_concentration": ("高浓", "hce", "high-concentration"),
            "fluorinated": ("含氟", "氟化", "fluorinated"),
            "ether_ec": ("醚类", "ether", "dol", "dme"),
            "solid_sulfide": ("硫化物", "sulfide"),
            "solid_oxide": ("氧化物", "oxide"),
            "polymer": ("聚合物", "polymer"),
        },
    }

    @classmethod
    def _anchored_pick(cls, cat: str, ids: List[str], question: str) -> Optional[str]:
        """按用户问题中点名的体系锚定候选 id；问题未点名任何候选时返回 None。"""
        q = (question or "").lower()
        anchors = cls._ANCHOR_KEYWORDS.get(cat, {})
        for id_ in ids:
            if any(kw in q for kw in anchors.get(id_, ())):
                return id_
        return None

    # 下标数字与小数点归一化映射（用于化学式与配比提取）
    _SUB_TRANS = str.maketrans("₀₁₂₃₄₅₆₇₈₉․", "0123456789.")
    _VALENCE_UNICODE_RE = re.compile(
        r"(?<![⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻])(?:[⁰¹²³⁴⁵⁶⁷⁸⁹]*[⁺⁻]+)(?![⁰¹²³⁴⁵⁶⁷⁸⁹])"
    )
    _VALENCE_ASCII_RE = re.compile(
        r"(?<=[A-Za-z\)\]\d₀-₉])\^(?:\{\d*[+-]+\}|\d+[+-]+|[+-]+(?![\d₀-₉]))"
    )
    _VALENCE_HTML_RE = re.compile(
        r"(?<=[A-Za-z\)\]\d₀-₉])<sup>\s*\d*[+-]+\s*</sup>",
        re.IGNORECASE,
    )
    # 改性策略关键词
    _MOD_KEYWORDS = (
        "共掺杂", "双位点掺杂", "掺杂", "doping", "包覆", "coating",
        "单晶", "single crystal", "zn-na", "zn–na", "cu-na", "cu–na",
    )
    _ADDITIVE_CANONICAL_CASING = {
        "fec": "FEC",
        "vc": "VC",
        "lidfob": "LiDFOB",
        "libob": "LiBOB",
        "dto": "DTO",
        "dtd": "DTD",
        "ps": "PS",
        "pes": "PES",
        "lipf6": "LiPF6",
        "lifsi": "LiFSI",
    }
    _ADDITIVE_DETECT_KEYS = ("fec", "vc", "lidfob", "libob", "dto", "dtd")

    @classmethod
    def _canonicalize_additive(cls, name: str) -> str:
        """Normalize electrolyte additive/salt casing (e.g., LIDFOB -> LiDFOB)."""
        cleaned = str(name or "").strip()
        if not cleaned:
            return ""
        return cls._ADDITIVE_CANONICAL_CASING.get(cleaned.lower(), cleaned)

    @classmethod
    def _normalize_legacy_additives(cls, value: Any) -> Any:
        """Normalize legacy additive strings/lists such as LIDFOB -> LiDFOB."""
        if isinstance(value, str):
            return re.sub(
                r"(?<![A-Za-z0-9])(lidfob|libob|lipf6|lifsi)(?![A-Za-z0-9])",
                lambda m: cls._ADDITIVE_CANONICAL_CASING[m.group(1).lower()],
                value,
                flags=re.IGNORECASE,
            )
        if isinstance(value, list):
            return [
                cls._canonicalize_additive(item)
                for item in value
                if str(item or "").strip()
            ]
        return value
    _DOPANT_ELEMS = (
        "Na", "Zn", "Cu", "Ti", "Fe", "Al", "Mg", "Zr", "Nb", "W", "Mo",
        "B", "F", "K", "Ca", "Cr", "Ga", "Ge", "La", "Y", "Si", "Sn",
        "Sr", "Ba", "V", "Ta", "Ce",
    )

    @classmethod
    def _strip_valence_annotations(cls, text: str) -> str:
        """安全剥离化学价态/电荷上标（如 Ni²⁺、Fe³⁺、Mn^4+、O^{2-}），不破坏 10⁻¹⁴ / 10^-14 / m²·s⁻¹."""
        s = text or ""
        s = cls._VALENCE_HTML_RE.sub("", s)
        s = cls._VALENCE_UNICODE_RE.sub("", s)
        s = cls._VALENCE_ASCII_RE.sub("", s)
        return s

    @classmethod
    def _format_stoich(cls, val: float) -> str:
        rounded = round(val, 6)
        if abs(rounded - 1.0) < 1e-6:
            return ""
        s = f"{rounded:.6f}".rstrip("0").rstrip(".")
        return s or "0"

    @classmethod
    def _merge_adjacent_elements(cls, formula: str) -> str:
        """合并相邻同元素计数量并去除尾部冗余零（如 Ni0.16Ni0.04 → Ni0.2，O2.0 → O2）."""
        tokens = re.findall(r"\[|\]|\(|\)|[A-Z][a-z]?(?:\d+(?:\.\d+)?)?|\d+(?:\.\d+)?", formula)
        if not tokens:
            return formula
        out: List[str] = []
        last_elem: Optional[Tuple[str, float]] = None

        def _flush() -> None:
            nonlocal last_elem
            if last_elem is not None:
                el, val = last_elem
                out.append(f"{el}{cls._format_stoich(val)}")
                last_elem = None

        for tok in tokens:
            if tok in ("[", "]", "(", ")") or tok[0].isdigit():
                _flush()
                out.append(tok)
                continue
            m = re.match(r"^([A-Z][a-z]?)(\d*(?:\.\d+)?)$", tok)
            if not m:
                _flush()
                out.append(tok)
                continue
            el, num_str = m.group(1), m.group(2)
            val = float(num_str) if num_str else 1.0
            if last_elem is not None and last_elem[0] == el:
                last_elem = (el, last_elem[1] + val)
            else:
                _flush()
                last_elem = (el, val)
        _flush()
        return "".join(out)

    @classmethod
    def _normalize_formula(cls, formula: str) -> str:
        """规范化正极化学式：去价态上标、下标转 ASCII、合并相邻同元素、去小数冗余零."""
        clean_f = cls._strip_valence_annotations(formula or "").translate(cls._SUB_TRANS)
        clean_f = re.sub(r"\s+", "", clean_f)
        if not clean_f:
            return ""
        return cls._merge_adjacent_elements(clean_f)

    @classmethod
    def _canonical_formula_key(cls, formula: str) -> str:
        """生成跨写法比对用的规范键（在 _normalize_formula 基础上剥离无外乘数括号并再次合并相邻同元素）."""
        norm = cls._normalize_formula(formula)
        if not norm:
            return ""
        if not re.search(r"[\)\]]\d", norm):
            norm = re.sub(r"[\[\]\(\)]", "", norm)
            norm = cls._merge_adjacent_elements(norm)
        return norm

    @classmethod
    def _formulas_equivalent(cls, f1: str, f2: str) -> bool:
        """判断两化学式在去价态、合并拆分同元素与去括号后是否等价."""
        k1 = cls._canonical_formula_key(f1)
        k2 = cls._canonical_formula_key(f2)
        return bool(k1) and k1 == k2

    @classmethod
    def _extract_markdown_rank1_formula(
        cls,
        d_norm: str,
        formula_re: re.Pattern,
        non_cathode_re: re.Pattern,
        base_canonical_keys: set,
        has_dopant_fn,
    ) -> Optional[str]:
        """从 Markdown 推荐排序表中提取 Rank #1 / 首选 / 主推荐行的化学式."""
        lines = d_norm.splitlines()
        sep_re = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$")
        rank_hdr_re = re.compile(r"排名|排序|推荐序|推荐位|优先级|推荐档|档位|rank|priority|tier", re.IGNORECASE)
        formula_hdr_re = re.compile(r"名义化学式|化学式|配方|正极(?:化学式|材料)?|formula|composition|candidate", re.IGNORECASE)
        option_hdr_re = re.compile(r"方案|类型|候选|推荐|级别|candidate|option|序号", re.IGNORECASE)
        rank_ctx_re = re.compile(r"推荐|排行|排名|候选|排序|ranking|candidate|最优", re.IGNORECASE)
        rank1_cell_re = re.compile(
            r"^(?:#?\s*1(?![0-9.])|rank\s*[=:#]?\s*1(?![0-9.])|no\.?\s*1(?![0-9.])|top\s*1(?![0-9.])"
            r"|第\s*[1一]\s*[位名]?|首选|主推荐|主推|主选|优先推荐)(?:$|[\s（(\[,，:：\-—].*)",
            re.IGNORECASE,
        )

        i = 0
        while i + 1 < len(lines):
            if "|" not in lines[i] or not sep_re.match(lines[i + 1]):
                i += 1
                continue
            headers = [re.sub(r"[*`]+", "", c).strip() for c in lines[i].strip().strip("|").split("|")]
            rank_idx: Optional[int] = None
            formula_idx: Optional[int] = None
            for idx, hdr in enumerate(headers):
                if rank_idx is None and rank_hdr_re.search(hdr):
                    rank_idx = idx
                if formula_idx is None and formula_hdr_re.search(hdr):
                    formula_idx = idx
            prev_heading = ""
            for k in range(i - 1, max(-1, i - 5), -1):
                if lines[k].strip():
                    prev_heading = lines[k].strip()
                    break
            is_rank_table = rank_idx is not None or (
                formula_idx is not None
                and headers
                and (option_hdr_re.search(headers[0]) or rank_ctx_re.search(prev_heading))
            )
            j = i + 2
            rows: List[List[str]] = []
            while j < len(lines) and "|" in lines[j] and not sep_re.match(lines[j]):
                row_cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                if any(row_cells):
                    rows.append(row_cells)
                j += 1
            if is_rank_table and rows:
                check_idx = rank_idx if rank_idx is not None else 0
                for cells in rows:
                    if check_idx >= len(cells):
                        continue
                    rank_clean = re.sub(r"[*`]+", "", cells[check_idx]).strip()
                    if not rank1_cell_re.match(rank_clean):
                        continue
                    search_cells = []
                    if formula_idx is not None and formula_idx < len(cells):
                        search_cells.append(cells[formula_idx])
                    for c_idx, cell in enumerate(cells):
                        if c_idx != formula_idx:
                            search_cells.append(cell)
                    for cell in search_cells:
                        cell_clean = re.sub(r"[*`]+", "", cell)
                        cands = [
                            f for f in formula_re.findall(cell_clean)
                            if not non_cathode_re.match(f)
                            and cls._canonical_formula_key(f) not in base_canonical_keys
                        ]
                        doped = [f for f in cands if has_dopant_fn(f)]
                        if doped:
                            return doped[0]
                        if cands:
                            return cands[0]
            i = j
        return None

    @classmethod
    def _extract_primary_route_and_scope(cls, clean: str) -> Tuple[Optional[str], str]:
        """识别主推荐前驱体/合成路线，并切出路线 A / 主推路线的局部作用域文本."""
        route_specs = (
            (
                "carbonate_coprecipitation",
                r"碳酸盐共沉淀法?|\([^)]+\)CO3|Na2CO3[^\n]{0,20}(?:沉淀|共沉淀)|carbonate\s+coprecipitation",
                r"碳酸盐共沉淀法?|碳酸盐|\([^)]+\)CO3|carbonate\s+coprecipitation",
            ),
            (
                "hydroxide_coprecipitation",
                r"氢氧化物共沉淀法?|\([^)]+\)\(OH\)2|hydroxide\s+coprecipitation",
                r"氢氧化物共沉淀法?|氢氧化物|\([^)]+\)\(OH\)2|hydroxide\s+coprecipitation",
            ),
            (
                "sol_gel",
                r"溶胶[-—]?凝胶法?|sol[- ]?gel",
                r"溶胶[-—]?凝胶法?|sol[- ]?gel",
            ),
            (
                "solid_state",
                r"高温固相(?:反应|合成|烧结)?法?|球磨[-—、/]?(?:高温)?固相(?:反应|合成|法)?|固相球磨(?:合成)?法?|固相(?:反应|合成|烧结)法|固相法|solid[- ]?state(?:\s+(?:reaction|synthesis|method|route))?",
                r"高温固相(?:反应|合成|烧结)?法?|球磨[-—、/]?(?:高温)?固相(?:反应|合成|法)?|固相球磨(?:合成)?法?|固相(?:反应|合成|烧结)法|固相法|solid[- ]?state(?:\s+(?:reaction|synthesis|method|route))?",
            ),
        )
        primary_tag_re = r"(?:(?:合成)?路线\s*A|Route\s*A|主推荐(?:合成)?(?:路线|方法)?|主推(?:路线|方案|方法)?|主路线|首选(?:合成)?(?:路线|方法)?|优先推荐)"
        secondary_tag_re = re.compile(
            r"(?:(?:合成)?路线\s*[BC]|Route\s*[BC]|对照(?:路线|组|方法)?|备选(?:路线|方法)?|参比(?:路线)?)",
            re.IGNORECASE,
        )
        neg_prefix_re = re.compile(r"(?:不建议|不应|不推荐|避免|非|并非)[^\n；;。]{0,12}$")

        # Tier 0: 显式 Route A / 主推 / 首选标签（跨 4 种路线一视同仁，按子句边界切分）
        tier0_hits: List[Tuple[int, str]] = []
        for r_key, _general_pat, primary_pat in route_specs:
            pat_fwd = re.compile(rf"{primary_tag_re}[^\n；;。]{{0,45}}?(?:{primary_pat})", re.IGNORECASE)
            pat_bwd = re.compile(
                rf"(?:{primary_pat})[^\n；;。]{{0,35}}?(?:为主推|（主推）|\(主推\)|（首选）|\(首选\)|主推|主推荐|主路线|首选|路线\s*A\b|Route\s*A\b)",
                re.IGNORECASE,
            )
            for pat in (pat_fwd, pat_bwd):
                for m in pat.finditer(clean):
                    clause_start = max(
                        clean.rfind("\n", 0, m.start()),
                        clean.rfind("；", 0, m.start()),
                        clean.rfind(";", 0, m.start()),
                        clean.rfind("。", 0, m.start()),
                    ) + 1
                    clause_prefix = clean[clause_start:m.start()]
                    if secondary_tag_re.search(clause_prefix) or secondary_tag_re.search(m.group(0)):
                        continue
                    if neg_prefix_re.search(clause_prefix):
                        continue
                    tier0_hits.append((m.start(), r_key))

        chosen_route: Optional[str] = None
        if tier0_hits:
            tier0_hits.sort(key=lambda x: x[0])
            chosen_route = tier0_hits[0][1]
        else:
            # Tier 1: 一般性提及，跳过明确标注为对照/备选/路线B/C 的命中
            non_ctrl_hits: List[Tuple[int, int, str]] = []
            all_hits: List[Tuple[int, int, str]] = []
            for tie_idx, (r_key, general_pat, _primary_pat) in enumerate(route_specs):
                for m in re.finditer(general_pat, clean, re.IGNORECASE):
                    clause_start = max(
                        clean.rfind("\n", 0, m.start()),
                        clean.rfind("；", 0, m.start()),
                        clean.rfind(";", 0, m.start()),
                        clean.rfind("。", 0, m.start()),
                    ) + 1
                    clause_end_candidates = [
                        p for p in (
                            clean.find("\n", m.end()),
                            clean.find("；", m.end()),
                            clean.find(";", m.end()),
                            clean.find("。", m.end()),
                        ) if p != -1
                    ]
                    clause_end = min(clause_end_candidates) if clause_end_candidates else len(clean)
                    clause = clean[clause_start:clause_end]
                    all_hits.append((m.start(), tie_idx, r_key))
                    if not secondary_tag_re.search(clause):
                        non_ctrl_hits.append((m.start(), tie_idx, r_key))
            pool = non_ctrl_hits or all_hits
            if pool:
                pool.sort()
                chosen_route = pool[0][2]

        # 定位主路线代码块作用域
        lines = clean.splitlines()
        primary_header_re = re.compile(primary_tag_re, re.IGNORECASE)
        secondary_line_re = re.compile(
            r"^\s*(?:[-*•#+\d.)、]+\s*)*(?:(?:合成)?路线\s*[BC]\b|Route\s*[BC]\b|对照(?:路线|组|方法)|备选(?:路线|方法)|参比(?:路线))",
            re.IGNORECASE,
        )
        route_pat_map = {k: re.compile(gp, re.IGNORECASE) for k, gp, _ in route_specs}
        chosen_pat = route_pat_map.get(chosen_route) if chosen_route else None

        scope_lines: List[str] = []
        # 优先查找含主推/路线A标签的路线起点
        for idx, line in enumerate(lines):
            m_pri = primary_header_re.search(line)
            if not m_pri:
                continue
            m_sec_same = secondary_tag_re.search(line)
            if m_sec_same and m_sec_same.start() < m_pri.start():
                # 同一行前半句是路线B、后半句才是路线A：仅截后半句
                line_tail = line[m_pri.start():]
                if chosen_pat is None or chosen_pat.search(line_tail):
                    scope_lines = [line_tail]
                    break
                continue
            if m_sec_same and m_sec_same.start() > m_pri.start():
                # 同一行先路线A后路线B：截断到路线B前
                line_head = line[m_pri.start():m_sec_same.start()]
                if chosen_pat is None or chosen_pat.search(line_head):
                    scope_lines = [line_head]
                    break
                continue
            if chosen_pat and not chosen_pat.search(line) and "路线" not in line and "Route" not in line:
                continue
            lead_indent = len(line) - len(line.lstrip())
            is_heading = bool(re.match(r"^\s*#{1,4}\s", line))
            is_bullet = bool(re.match(r"^\s*(?:[-*•]|\d+[.)、])\s+", line))
            block = [line]
            for j in range(idx + 1, len(lines)):
                nxt = lines[j]
                stripped = nxt.strip()
                if stripped in ("---", "***", "===") or re.match(r"^#{1,4}\s", stripped):
                    break
                if secondary_line_re.match(nxt):
                    break
                nxt_indent = len(nxt) - len(nxt.lstrip())
                if is_bullet and stripped and nxt_indent <= lead_indent:
                    if re.match(r"^\s*(?:[-*•]|\d+[.)、])\s+", nxt):
                        break
                if is_heading and stripped and primary_header_re.search(nxt) is None:
                    # headings continue until next heading / secondary / divider
                    pass
                block.append(nxt)
            scope_lines = block
            break

        # 次优：若无显式路线A标题，按已锁定前驱体路线匹配首个非对照列表块/段落
        if not scope_lines and chosen_pat is not None:
            for idx, line in enumerate(lines):
                if not chosen_pat.search(line) or secondary_tag_re.search(line):
                    continue
                is_bullet = bool(re.match(r"^\s*(?:[-*•]|\d+[.)、])\s+", line))
                if not is_bullet and not re.match(r"^\s*#{1,4}\s", line):
                    continue
                lead_indent = len(line) - len(line.lstrip())
                block = [line]
                for j in range(idx + 1, len(lines)):
                    nxt = lines[j]
                    stripped = nxt.strip()
                    if stripped in ("---", "***", "===") or re.match(r"^#{1,4}\s", stripped):
                        break
                    if secondary_line_re.match(nxt):
                        break
                    if is_bullet and stripped and (len(nxt) - len(nxt.lstrip())) <= lead_indent:
                        if re.match(r"^\s*(?:[-*•]|\d+[.)、])\s+", nxt):
                            break
                    block.append(nxt)
                scope_lines = block
                break

        return chosen_route, "\n".join(scope_lines)

    @classmethod
    def _extract_calcination_from_text(
        cls, scope: str, allow_untagged: bool = False
    ) -> Optional[Dict[str, Any]]:
        """从给定文本作用域提取主煅烧温度与保温时间（跳过预烧/干燥/退火与升温速率）."""
        if not scope.strip():
            return None
        t_rng = r"(\d{3,4}(?:\.\d+)?)(?:\s*[~–—\-到至]\s*(\d{3,4}(?:\.\d+)?))?"
        h_rng = r"(\d+(?:\.\d+)?)(?:\s*[~–—\-到至]\s*(\d+(?:\.\d+)?))?"
        deg = r"\s*°?\s*[C℃](?!\s*/\s*(?:min|h|s))"
        pre_skip_re = re.compile(r"(?:预烧|预煅烧|pre-?calc|烘干|干燥|除水|退火|升温速率|速率)", re.IGNORECASE)
        post_skip_re = re.compile(
            r"^\s*(?:[（(][^）)]*[）)])?\s*(?:预烧|预煅烧|预热|烘干|干燥|退火|pre-?calc|drying)",
            re.IGNORECASE,
        )

        # Pass 1: 关键字在温度前
        p1 = re.compile(
            rf"(?:主烧|主煅烧|最终烧结|高温烧结|煅烧|烧结|calcination|sintering)"
            rf"\s*(?:温度|条件)?[:：为于]?\s*{t_rng}{deg}"
            rf"(?:(?:\s*[/,，、]|(?:\s*下|\s*，?\s*(?:保温|维持|恒温|持续)?))\s*{h_rng}\s*(?:h|小时))?",
            re.IGNORECASE,
        )
        # Pass 2: 温度在关键字前（时间可在关键词前或关键词后）
        p2 = re.compile(
            rf"{t_rng}{deg}"
            rf"(?:\s*(?:/|保温|维持|恒温)?\s*{h_rng}\s*(?:h|小时))?"
            rf"\s*(?:下|条件|条件下)?(?:进行)?(?:主烧|主煅烧|最终烧结|高温烧结|煅烧|烧结|calcination|sintering)"
            rf"(?:\s*(?:保温|维持|恒温)?\s*{h_rng}\s*(?:h|小时))?",
            re.IGNORECASE,
        )
        # Pass 3 (仅主路线内部启用): 未带"煅烧"字样但成对出现的 T °C / H h 高温步骤（如 → 900–950 °C/10–12 h）
        p3 = re.compile(
            rf"{t_rng}{deg}\s*(?:/|，|,|保温|维持|恒温)\s*{h_rng}\s*(?:h|小时)",
            re.IGNORECASE,
        )

        def _is_valid_match(m: re.Match, check_min_temp: bool = False) -> bool:
            if check_min_temp and float(m.group(1)) < 550.0:
                return False
            pre_win = scope[max(0, m.start() - 20):m.start()]
            last_sep = max(
                pre_win.rfind("\n"),
                pre_win.rfind("→"),
                pre_win.rfind("后"),
                pre_win.rfind("再"),
                pre_win.rfind("；"),
                pre_win.rfind(";"),
                pre_win.rfind("。"),
                pre_win.rfind("，"),
                pre_win.rfind(","),
            )
            local_pre = pre_win[last_sep + 1:] if last_sep >= 0 else pre_win
            if pre_skip_re.search(local_pre):
                return False
            post_win = scope[m.end():m.end() + 18]
            if post_skip_re.search(post_win):
                return False
            return True

        def _build_res(t1: str, t2: Optional[str], h1: Optional[str], h2: Optional[str]) -> Dict[str, Any]:
            res: Dict[str, Any] = {}
            t_lo = float(t1)
            if t2:
                t_hi = float(t2)
                res["calcination_temp_c"] = round((t_lo + t_hi) / 2.0, 2)
                res["calcination_temp_range_c"] = f"{t1}-{t2}"
            else:
                res["calcination_temp_c"] = t_lo
            if h1:
                h_lo = float(h1)
                if h2:
                    h_hi = float(h2)
                    res["calcination_hours"] = round((h_lo + h_hi) / 2.0, 2)
                    res["calcination_hours_range"] = f"{h1}-{h2}"
                else:
                    res["calcination_hours"] = h_lo
            return res

        for m in p1.finditer(scope):
            if _is_valid_match(m):
                return _build_res(m.group(1), m.group(2), m.group(3), m.group(4))
        for m in p2.finditer(scope):
            if _is_valid_match(m):
                h1 = m.group(3) or m.group(5)
                h2 = m.group(4) if m.group(3) else m.group(6)
                return _build_res(m.group(1), m.group(2), h1, h2)
        if allow_untagged:
            for m in p3.finditer(scope):
                if _is_valid_match(m, check_min_temp=True):
                    return _build_res(m.group(1), m.group(2), m.group(3), m.group(4))
        return None

    @classmethod
    def _extract_design_anchors(cls, question: str, draft_text: str) -> Dict[str, Any]:
        """从问题与正文提取关键设计锚点，供跨章节一致性与规则核查消费（只记录真实出现的字段）."""
        q_norm = cls._strip_valence_annotations(question or "").translate(cls._SUB_TRANS)
        d_norm = cls._strip_valence_annotations(draft_text or "").translate(cls._SUB_TRANS)
        combined_norm = f"{q_norm}\n{d_norm}"
        clean = re.sub(r"\*+", "", combined_norm)
        anchors: Dict[str, Any] = {}

        # 1. primary_formula: 排除 question 中作为基体出现的化学式及简单无机锂盐；
        # 优先顺序: Markdown 排序表 Row #1 -> 显式主推荐声明 -> 正文掺杂候选 -> 其余有效正极候选
        formula_re = re.compile(
            r"(?<![A-Za-z])Li(?:\d+(?:\.\d+)?)?(?:[A-Z][a-z]?(?:\d+(?:\.\d+)?)?|\((?:[A-Z][a-z]?(?:\d+(?:\.\d+)?)?){2,}\)(?:\d+(?:\.\d+)?)?|\[(?:[A-Z][a-z]?(?:\d+(?:\.\d+)?)?){2,}\](?:\d+(?:\.\d+)?)?)+O\d+(?:\.\d+)?(?![A-Za-z0-9])"
        )
        non_cathode_re = re.compile(
            r"^Li(?:\d+(?:\.\d+)?)?(?:C\d*|N\d*|Cl\d*|S\d*|B\d*|Br\d*|I\d*|P\d*|Si\d*)O\d+(?:\.\d+)?$"
        )

        def _has_dopant(formula: str) -> bool:
            elems: Dict[str, float] = {}
            for el, num in re.findall(r"([A-Z][a-z]?)(\d*(?:\.\d+)?)", formula):
                if el not in ("Li", "O"):
                    elems[el] = elems.get(el, 0.0) + (float(num) if num else 1.0)
            is_phosphate = "P" in elems
            for el in cls._DOPANT_ELEMS:
                if is_phosphate and el == "Fe":
                    continue
                if el in elems and elems[el] < 0.25:
                    return True
            return False

        q_formulas = [f for f in formula_re.findall(q_norm) if not non_cathode_re.match(f)]
        base_formulas = {
            f for f in q_formulas
            if not _has_dopant(f)
            or re.search(r"(?:改进|基体|base|未掺|原始)[^Li\n]{0,15}" + re.escape(f), q_norm, re.IGNORECASE)
        }
        base_canonical_keys = {cls._canonical_formula_key(f) for f in base_formulas if cls._canonical_formula_key(f)}

        d_candidates = [
            f for f in formula_re.findall(d_norm)
            if not non_cathode_re.match(f)
            and cls._canonical_formula_key(f) not in base_canonical_keys
        ]
        doped_candidates = [f for f in d_candidates if _has_dopant(f)]

        rank1_formula = cls._extract_markdown_rank1_formula(
            d_norm, formula_re, non_cathode_re, base_canonical_keys, _has_dopant
        )
        explicit_formula: Optional[str] = None
        if not rank1_formula:
            neg_formula_re = re.compile(r"(?:禁用|排除|不应|不可|并非)[^\n]{0,10}$")
            for m_exp in re.finditer(
                r"(?:^|[\n。；;])\s*(?:[-*•]\s*)?(?:\*{1,2})?"
                r"(?:唯一最优名义化学式|唯一最优化学式|最优名义化学式|主推荐(?:名义)?化学式|主推荐配方|主选(?:名义)?化学式|主选配方|首选(?:名义)?化学式|首选配方|Primary\s+Formula)"
                r"(?:\*{1,2})?\s*[:：是为]?\s*([^\n]+)",
                d_norm,
                re.IGNORECASE,
            ):
                seg = re.sub(r"[*`]+", "", m_exp.group(1))
                exp_cands = [
                    f for f in formula_re.findall(seg)
                    if not non_cathode_re.match(f)
                    and cls._canonical_formula_key(f) not in base_canonical_keys
                    and not neg_formula_re.search(seg[:seg.find(f)])
                ]
                exp_doped = [f for f in exp_cands if _has_dopant(f)]
                if exp_doped:
                    explicit_formula = exp_doped[0]
                    break
                if exp_cands:
                    explicit_formula = exp_cands[0]
                    break

        if rank1_formula:
            anchors["primary_formula"] = rank1_formula
        elif explicit_formula:
            anchors["primary_formula"] = explicit_formula
        elif doped_candidates:
            anchors["primary_formula"] = doped_candidates[0]
        elif d_candidates:
            anchors["primary_formula"] = d_candidates[0]
        else:
            q_doped = [
                f for f in q_formulas
                if cls._canonical_formula_key(f) not in base_canonical_keys and _has_dopant(f)
            ]
            if q_doped:
                anchors["primary_formula"] = q_doped[0]

        # 剔除仅描述对照组行的表项，避免对照组限值（如 | 对照 | ... | x_A ≤0.5 mol% |）抢占主方案上限
        non_control_clean = "\n".join(
            line for line in clean.splitlines()
            if not re.match(r"^\s*\|\s*(?:对照|control)\b", line, re.IGNORECASE)
        )
        limit_search_texts = (non_control_clean, clean) if non_control_clean != clean else (clean,)

        # 2. doping_upper_limit_mol_pct (支持 mol% 与 at%，支持上限与范围上限如 1–5 at%)
        m_limit = None
        for search_txt in limit_search_texts:
            m_limit = (
                re.search(
                    r"总掺杂(?:量|摩尔分数|上限)?[^。\n；;]{0,25}?(?:≤|<=|不超过|上限为?)\s*(\d+(?:\.\d+)?)\s*(?:mol|at)\s*%",
                    search_txt, re.IGNORECASE,
                )
                or re.search(
                    r"(?:总掺(?:杂)?(?:量|摩尔分数|上限)?|total(?:\s+dop\w*)?)[^。\n；;]{0,25}?(?:(?:<=|≤|<|上限[:：]?)\s*|\d+(?:\.\d+)?\s*[~–—-]\s*)(\d+(?:\.\d+)?)\s*(?:mol|at)\s*%",
                    search_txt, re.IGNORECASE,
                )
                or re.search(
                    r"(?:<=|≤|<|低于|不超过)\s*(\d+(?:\.\d+)?)\s*(?:mol|at)\s*%[^。\n；;]{0,15}(?:上限|总掺)",
                    search_txt, re.IGNORECASE,
                )
                or re.search(
                    r"(?:总掺(?:杂)?(?:量|摩尔分数|上限)?|total\s+dop\w*)[^。\n；;]{0,60}?(?:<=|≤|<|上限|不超过)[^。\n；;]{0,35}?(\d+(?:\.\d+)?)\s*(?:mol|at)\s*%",
                    search_txt, re.IGNORECASE,
                )
                or re.search(
                    r"(?:总掺(?:杂)?(?:量|摩尔分数|上限)?|total\s+dop\w*)[^。\n；;]{0,25}?(\d+(?:\.\d+)?)\s*(?:mol|at)\s*%\s*上限",
                    search_txt, re.IGNORECASE,
                )
            )
            if m_limit:
                break
        if m_limit:
            anchors["doping_upper_limit_mol_pct"] = float(m_limit.group(1))

        # 2b. optional A-site / B-site upper limits (x_A / y_B)
        m_a_limit = None
        for search_txt in limit_search_texts:
            m_a_limit = (
                re.search(
                    r"(?<![A-Za-z0-9_])x_A(?![A-Za-z0-9_])(?:\s*[（(][^）)\n]{1,16}[）)])?\*{0,2}[^。\n；;]{0,20}?(?:≤|<=|上限为?|不超过)\s*(\d+(?:\.\d+)?)\s*(?:mol|at)\s*%",
                    search_txt, re.IGNORECASE,
                )
                or re.search(
                    r"(?<![A-Za-z0-9_])x_A(?![A-Za-z0-9_])(?:\s*[（(][^）)\n]{1,16}[）)])?\*{0,2}\s*(?:搜索)?(?:区间|范围)\s*[:：]?\s*\d+(?:\.\d+)?\s*[~–—-]\s*(\d+(?:\.\d+)?)\s*(?:mol|at)\s*%",
                    search_txt, re.IGNORECASE,
                )
            )
            if m_a_limit:
                break
        if m_a_limit:
            anchors["a_site_upper_limit_mol_pct"] = float(m_a_limit.group(1))

        m_b_limit = None
        for search_txt in limit_search_texts:
            m_b_limit = (
                re.search(
                    r"(?<![A-Za-z0-9_])y_B(?![A-Za-z0-9_])(?:\s*[（(][^）)\n]{1,16}[）)])?\*{0,2}[^。\n；;]{0,20}?(?:≤|<=|上限为?|不超过)\s*(\d+(?:\.\d+)?)\s*(?:mol|at)\s*%",
                    search_txt, re.IGNORECASE,
                )
                or re.search(
                    r"(?<![A-Za-z0-9_])y_B(?![A-Za-z0-9_])(?:\s*[（(][^）)\n]{1,16}[）)])?\*{0,2}\s*(?:搜索)?(?:区间|范围)\s*[:：]?\s*\d+(?:\.\d+)?\s*[~–—-]\s*(\d+(?:\.\d+)?)\s*(?:mol|at)\s*%",
                    search_txt, re.IGNORECASE,
                )
            )
            if m_b_limit:
                break
        if m_b_limit:
            anchors["b_site_upper_limit_mol_pct"] = float(m_b_limit.group(1))

        # 3 & 4. precursor_type 与路线作用域内的 calcination_temp_c / calcination_hours
        chosen_route, primary_route_scope = cls._extract_primary_route_and_scope(clean)
        if chosen_route:
            anchors["precursor_type"] = chosen_route

        calc_info = None
        if primary_route_scope:
            calc_info = cls._extract_calcination_from_text(primary_route_scope, allow_untagged=True)
        if not calc_info:
            # 兜底扫描全文，先剥离显式路线 B/C 与对照路线行以防跨路线串用
            secondary_line_filter = re.compile(
                r"(?:(?:合成)?路线\s*[BC]\b|Route\s*[BC]\b|对照(?:路线|组|方法)|备选(?:路线|方法))",
                re.IGNORECASE,
            )
            filtered_lines = [ln for ln in clean.splitlines() if not secondary_line_filter.search(ln)]
            calc_info = cls._extract_calcination_from_text("\n".join(filtered_lines), allow_untagged=False)
        if not calc_info:
            calc_info = cls._extract_calcination_from_text(clean, allow_untagged=False)
        if calc_info:
            anchors.update(calc_info)

        # 5. al_foil_um (支持单值与范围如 15–16 μm，支持 Al 与 铝)
        m_al = re.search(
            r"(\d+(?:\.\d+)?(?:\s*[~–—-]\s*\d+(?:\.\d+)?)?)\s*(?:μm|um)\s*(?:(?:电池级|涂碳)\s*)?(?:Al|铝)\s*(?:箔|foil)"
            r"|(?:Al|铝)\s*(?:箔|foil)[^\n,，；;。]{0,18}?(\d+(?:\.\d+)?(?:\s*[~–—-]\s*\d+(?:\.\d+)?)?)\s*(?:μm|um)",
            clean, re.IGNORECASE,
        )
        if m_al:
            raw_al = (m_al.group(1) or m_al.group(2)).strip()
            if re.search(r"[~–—-]", raw_al):
                anchors["al_foil_um"] = re.sub(r"\s*[~–—-]\s*", "-", raw_al)
            else:
                anchors["al_foil_um"] = float(raw_al)

        # 6. separator (支持 Celgard 系列与常见聚合物/涂覆隔膜)
        m_sep = re.search(r"Celgard\s*(\d{3,4}[A-Za-z]*)", clean, re.IGNORECASE)
        if m_sep:
            anchors["separator"] = f"Celgard {m_sep.group(1)}"
        else:
            m_sep_fb = re.search(
                r"(?:隔膜|separator)[^\n:：,，；;。]{0,8}?(?:[:：]|为|选用|采用)\s*(PP/PE/PP|Whatman\s*GF/[A-DF]|芳纶涂覆隔膜|陶瓷涂覆隔膜)",
                clean, re.IGNORECASE,
            )
            if m_sep_fb:
                anchors["separator"] = re.sub(r"\s+", " ", m_sep_fb.group(1).strip())

        # 7. cathode_diameter_mm & cathode_area_cm2
        m_diam = re.search(
            r"(?:冲片|冲裁|冲(?=\s*[φΦ])|辊压后冲|正极|圆片|极片|直径)[^\n\d]{0,12}[φΦ]?\s*(\d+(?:\.\d+)?)\s*mm"
            r"|[φΦ]\s*(\d+(?:\.\d+)?)\s*mm\s*(?:正极|圆片|极片|冲片)",
            clean, re.IGNORECASE,
        )
        if m_diam:
            diam = float(m_diam.group(1) or m_diam.group(2))
            anchors["cathode_diameter_mm"] = diam
            anchors["cathode_area_cm2"] = round(math.pi * (diam / 20.0) ** 2, 2)

        # 8. electrolyte_volume_uL
        m_vol = re.search(
            r"(?:注液量|注液|电解液[^\n\d]{0,8}(?:用量|体积))[^\n\d]{0,18}?"
            r"(\d+(?:\.\d+)?(?:\s*[~–—-]\s*\d+(?:\.\d+)?)?)\s*(?:μL|uL)",
            clean, re.IGNORECASE,
        )
        if m_vol:
            anchors["electrolyte_volume_uL"] = re.sub(r"\s+", "", m_vol.group(1))

        # 9. slurry_ratio
        m_slurry = re.search(
            r"(?:活性物质|正极活性|浆料|极片配方|配比|PVDF|Super\s*P|粘结剂|黏结剂|slurry|active)"
            r"[^\n]{0,45}?(\d+(?:\.\d+)?\s*:\s*\d+(?:\.\d+)?\s*:\s*\d+(?:\.\d+)?)",
            clean, re.IGNORECASE,
        )
        if m_slurry:
            anchors["slurry_ratio"] = re.sub(r"\s+", "", m_slurry.group(1))

        # 添加剂状态：
        # 1) 若 user_query 显式锁定添加剂剂量/固定配方，或提及添加剂且正文未标注为待定量候选筛选 -> locked
        # 2) 若正文显式标注为候选/待定量/待实验筛选且 user_query 未锁定具体剂量 -> candidate_screening
        # 3) 若仅正文出现添加剂而 user_query 未提及 -> candidate_screening
        q_lower = str(question or "").translate(cls._SUB_TRANS).lower()
        d_lower = str(draft_text or "").translate(cls._SUB_TRANS).lower()
        q_additives = [
            a_kw for a_kw in cls._ADDITIVE_DETECT_KEYS
            if re.search(rf"(?<![a-z0-9]){re.escape(a_kw)}(?![a-z0-9])", q_lower)
        ]
        d_additives = [
            a_kw for a_kw in cls._ADDITIVE_DETECT_KEYS
            if re.search(rf"(?<![a-z0-9]){re.escape(a_kw)}(?![a-z0-9])", d_lower)
        ]
        d_is_candidate_screening = bool(
            re.search(
                r"(?:候选(?:成膜|界面|功能)?添加剂|已列入方案候选添加剂|添加剂未定量|"
                r"作为候选(?:成膜|界面)?添加剂|具体比例[^。\n；;]{0,20}?(?:待|不预设|未预设)|"
                r"未预设(?:具体)?(?:比例|用量|加量)|比例[^。\n；;]{0,12}?待(?:实验|定量)?筛选|"
                r"待实验定量筛选|candidate\s+additive|screening\s+additive)",
                d_lower,
                re.IGNORECASE,
            )
        )
        if q_additives:
            add_union = "|".join(re.escape(k) for k in q_additives)
            q_has_locked_additive = bool(
                re.search(
                    rf"\d+(?:\.\d+)?\s*(?:wt\.?\s*%|vol\.?\s*%|mol\s*%|%)\s*(?:的\s*)?(?:{add_union})(?![a-z0-9])"
                    rf"|(?<![a-z0-9])(?:{add_union})\s*(?:[（(:：]\s*)?\d+(?:\.\d+)?\s*(?:wt\.?\s*%|vol\.?\s*%|mol\s*%|%)"
                    rf"|(?:固定添加(?!剂)|锁定(?:含)?|必须(?:使用|含)|严格采用)\s*[^。\n；;]{{0,16}}?(?:{add_union})(?![a-z0-9])",
                    q_lower,
                    re.IGNORECASE,
                )
            )
            if q_has_locked_additive or not d_is_candidate_screening:
                anchors["additive_status"] = "locked"
            else:
                anchors["additive_status"] = "candidate_screening"
        elif d_additives:
            anchors["additive_status"] = "candidate_screening"

        return anchors

    def _extract_scheme(self, question: str, plan: Dict, draft_text: str = "") -> Optional[Dict[str, Any]]:
        """从问题+规划+答案正文中提取材料组合方案（优先采用问题中的目标比能量；供 RelationEngine 校验与下游物理仿真消费）."""
        if self.relation_engine is None:
            return None
        try:
            # 1. 优先提取用户课题 (goal/question) 中显式指定的材料组件（用户输入具有最高权威）
            q_entities = self.relation_engine.extract_entities(question) if question else {}

            combined = f"{question}\n{draft_text}"
            entities = self.relation_engine.extract_entities(combined)
            scheme: Dict[str, Any] = {}
            user_locked: List[str] = []
            for cat in ("cathode", "anode", "electrolyte"):
                # 优先级 1: 用户课题 (goal) 明确点名指定了该组件 → 直接锁定，绝不允许正文/文献对比提及抢位
                q_ids = q_entities.get(cat) or []
                if q_ids:
                    anchored_q = self._anchored_pick(cat, q_ids, question)
                    scheme[cat] = anchored_q or q_ids[0]
                    user_locked.append(cat)
                    continue

                # 优先级 2: 用户课题未直接命名组件，但包含锚定关键词，从整体候选集合中锚定
                ids = entities.get(cat) or []
                if ids:
                    anchored = self._anchored_pick(cat, ids, question)
                    if anchored:
                        scheme[cat] = anchored
                    elif cat == "electrolyte":
                        # 若问题完全未指定电解液，按正文推荐次序
                        if "lhce" in ids:
                            scheme[cat] = "lhce"
                        elif "fluorinated" in ids:
                            scheme[cat] = "fluorinated"
                        elif "high_concentration" in ids:
                            scheme[cat] = "high_concentration"
                        else:
                            scheme[cat] = ids[0]
                    elif cat == "cathode":
                        if "NCM811" in ids:
                            scheme[cat] = "NCM811"
                        elif "LRMO" in ids:
                            scheme[cat] = "LRMO"
                        elif "LNMO" in ids:
                            scheme[cat] = "LNMO"
                        elif "LCO" in ids:
                            scheme[cat] = "LCO"
                        elif "LFP" in ids:
                            scheme[cat] = "LFP"
                        else:
                            scheme[cat] = ids[0]
                    elif cat == "anode":
                        if "li_metal" in ids:
                            scheme[cat] = "li_metal"
                        elif "si_base" in ids:
                            scheme[cat] = "si_base"
                        elif "graphite" in ids:
                            scheme[cat] = "graphite"
                        else:
                            scheme[cat] = ids[0]
                    else:
                        scheme[cat] = ids[0]
            if user_locked:
                scheme["user_locked_components"] = user_locked
            # 提取改性策略关键词
            combined_lower = combined.lower()
            modifications = [kw for kw in self._MOD_KEYWORDS if kw in combined_lower]
            if modifications:
                scheme["modifications"] = modifications
            # 提取设计锚点
            anchors = self._extract_design_anchors(question, draft_text)
            if anchors:
                scheme["design_anchors"] = anchors
            # 提取添加剂（使用词边界避免 effect/defect 等英语单词误命中 fec，并采用规范大小写）
            additives = []
            for a_kw in self._ADDITIVE_DETECT_KEYS:
                if re.search(rf"(?<![a-z0-9]){re.escape(a_kw)}(?![a-z0-9])", combined_lower):
                    additives.append(self._ADDITIVE_CANONICAL_CASING[a_kw])
            if additives:
                scheme["additives"] = self._normalize_legacy_additives(additives)
            # 提取目标能量密度（课题显式指标优先，避免正文前文对比基线抢占）
            claimed = (
                (self._extract_energy_claim(question) if question else None)
                or self._extract_energy_claim(draft_text)
            )
            if claimed:
                scheme["target_energy_wh_kg"] = claimed
            # 提取几何设计数值（面载量/压实/孔隙率/厚度/N-P，供 Stage 5 PINN 验证推荐设计点）
            geometry = self._extract_geometry(combined)
            if geometry:
                scheme["geometry"] = geometry
            return scheme or None
        except Exception:
            return None

    # ── 几何参数正则提取（按组件关键词就近归属，区间取中值） ──────────────

    # 数值区间：支持 12–20 / 12~20 / 12-20 / 12 到 20 等写法
    _NUM = r"\d+(?:\.\d+)?"
    _RANGE = _NUM + r"(?:\s*[~–—\-到]\s*" + _NUM + r")?"
    # 科学计数法尾数×10^指数的指数部分 (含 Unicode 上标 ⁻¹⁴ 形式)
    _SUP_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
    # 扩散系数单位: m²/s | m2/s | m²·s⁻¹ (cm²/s 同构, ×1e-4 换算)
    _D_UNIT = r"(m|cm)\s*(?:²|2|\^2)\s*[·/]?\s*s(?:⁻¹)?"

    # 组件锚定关键词（数值归属到其左侧最近的组件词）
    _GEOMETRY_ANCHORS = {
        "cathode": ("正极", "cathode"),
        "anode": ("负极", "硅碳", "硅基", "Si-C", "Si/C", "anode"),
    }

    @classmethod
    def _parse_sci_number(cls, raw: str) -> Optional[float]:
        """把 1e-14 / 2.5×10⁻¹⁴ / 10^-14 / 0.5 等写法解析为 float (扩散系数专用).

        文献中 D_s 常以科学计数法给出, 基础 _NUM 不覆盖; 这里统一归一:
        Unicode 上标 ⁻¹⁴ → ^-14, ×/x/· → *, 随后 2.5*10^-14 → 2.5E-14 交给 float。
        """
        t = re.sub(r"\s+", "", str(raw)).replace("−", "-").replace("×", "*").replace("·", "")
        t = t.replace("X", "*").replace("x", "*")
        t = re.sub(
            r"[⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+",
            lambda m: "^" + m.group().replace("⁻", "-").replace("⁺", "+").translate(cls._SUP_DIGITS),
            t,
        )
        if "^" in t:
            t = t.replace("*10^", "E")
            t = re.sub(r"(?<![\d.])10\^", "1E", t)  # 裸 10^-14 (无尾数)
        try:
            return float(t)
        except ValueError:
            return None

    @classmethod
    def _extract_geometry(cls, text: str) -> Dict[str, Dict[str, float]]:
        """从五段式叙事提取几何设计数值；只记录真实出现的值，缺失字段不写入.

        字段: loading_mg_cm2（面载量, mg/cm²）、compaction_g_cm3（压实密度,
        g/cm³）、porosity_pct（孔隙率, %）、thickness_um（极片厚度, μm）、
        R_s_um（颗粒粒径/半径, μm, 按 PINN 输入口径直接作为 R_s）、
        D_s_m2s（固相扩散系数, m²/s, 支持科学计数法与 cm²/s 换算）；
        区间写法取中值。N/P 比为电芯级，单独放 geometry["n_p_ratio"]；
        放电倍率为电芯级条件，放 geometry["condition"]["c_rate"]（可选）。

        组件归属: 优先按五段式 Markdown 标题（### ...正极/负极...）切片；
        无标题时退化为就近锚点归属（数值左侧最近的组件关键词）。
        """
        # 组件标题边界（五段式: 章节标题决定其管辖文本段的组件归属）
        section_spans = sorted(
            (m.start(), comp)
            for m in re.finditer(r"^#{1,6}[^\n]*$", text, re.MULTILINE)
            for comp, kws in cls._GEOMETRY_ANCHORS.items()
            if any(kw.lower() in m.group().lower() for kw in kws)
        )
        # 内联锚点（同节内就近归属；跳过“匹配/对比/相对/参照”等引申提及）
        inline_anchors = sorted(
            (m.start(), comp)
            for comp, kws in cls._GEOMETRY_ANCHORS.items()
            for kw in kws
            for m in re.finditer(r"(?<!匹配)(?<!对比)(?<!相对)(?<!参照)" + re.escape(kw), text, re.IGNORECASE)
        )

        def owner(pos: int) -> Optional[str]:
            sec_hits = [(s, comp) for s, comp in section_spans if s <= pos]
            inl_hits = [(s, comp) for s, comp in inline_anchors if s <= pos]
            if sec_hits and inl_hits:
                s_sec, comp_sec = sec_hits[-1]
                s_inl, comp_inl = inl_hits[-1]
                return comp_inl if s_inl >= s_sec else comp_sec
            if sec_hits:
                return sec_hits[-1][1]
            if inl_hits:
                return inl_hits[-1][1]
            return None

        li_metal_anode_in_text = bool(re.search(
            r"(?:负极[^。\n]{0,20}(?:金属锂|锂金属|锂箔|li_metal|li\s*箔)|"
            r"(?<!对比)(?:金属锂|锂金属|锂箔)\s*负极|"
            r"对电极(?:/参比电极)?[^。\n]{0,12}(?:金属锂|锂金属|锂箔))",
            text, re.IGNORECASE,
        ))

        def mid(rng: str) -> Optional[float]:
            nums = re.findall(cls._NUM, rng)
            if not nums:
                return None
            vals = [float(n) for n in nums]
            return sum(vals) / len(vals)

        geometry: Dict[str, Dict[str, float]] = {"cathode": {}, "anode": {}}

        def record(comp: Optional[str], field: str, value: Optional[float]) -> None:
            if comp and comp in geometry and field not in geometry[comp] and value is not None:
                geometry[comp][field] = value

        # 面载量: 载量/loading 紧跟 mg·cm⁻² / mg/cm²（区分 mAh·cm⁻² 面容量）
        for m in re.finditer(
                r"(?:面?载量|loading)[^\d]{0,8}(" + cls._RANGE + r")\s*mg\s*[·/·]?\s*cm(?:⁻²|²|\^2|-2)",
                text, re.IGNORECASE):
            record(owner(m.start()), "loading_mg_cm2", mid(m.group(1)))
        # 压实密度: 压实(密度)? 紧跟 g·cm⁻³ / g/cm³（锂金属真密度 0.534 g/cm³，跳过 >0.6 的非物理锂负极压实）
        for m in re.finditer(
                r"压实(?:密度)?[^\d]{0,8}(" + cls._RANGE + r")\s*g\s*[·/]?\s*cm(?:⁻³|³|\^3|-3)",
                text, re.IGNORECASE):
            comp = owner(m.start())
            val = mid(m.group(1))
            if comp == "anode" and val is not None and val > 0.6:
                local_ctx = text[max(0, m.start() - 80):min(len(text), m.end() + 40)]
                if li_metal_anode_in_text or re.search(
                    r"(?<!对比)(?:锂箔|金属锂|锂金属|li_metal|li\s*箔)", local_ctx, re.IGNORECASE
                ):
                    continue
            record(comp, "compaction_g_cm3", val)
        # 孔隙率: X–Y %
        for m in re.finditer(
                r"孔隙率[^\d%]{0,8}(" + cls._RANGE + r")\s*%", text):
            record(owner(m.start()), "porosity_pct", mid(m.group(1)))
        # 极片厚度: 厚度 紧跟 X μm（锚定"厚度"，避免颗粒 D50 的 μm 干扰）
        for m in re.finditer(
                r"(?:涂[布敷]厚度|极片厚度|电极厚度|厚度)[^\d]{0,6}(" + cls._NUM + r")\s*(?:μm|um|微米)",
                text, re.IGNORECASE):
            record(owner(m.start()), "thickness_um", mid(m.group(1)))
        # 颗粒粒径/半径: 粒径|颗粒粒径|颗粒半径|D50 紧跟 X μm (长关键词在前,
        # 避免"粒径"吃掉"颗粒粒径"; 声明值按 PINN 输入口径直接作为 R_s)
        for m in re.finditer(
                r"(?:颗粒粒径|颗粒半径|一次颗粒|粒径|D50)[^\d]{0,6}(" + cls._RANGE + r")\s*(?:μm|um|微米)",
                text, re.IGNORECASE):
            record(owner(m.start()), "R_s_um", mid(m.group(1)))
        # 固相扩散系数: 扩散系数|D_s 紧跟科学计数法数值 + m²/s (cm²/s ×1e-4 换算);
        # 数值窗口独立捕获后逐 token 解析 (1e-14 / 2.5×10⁻¹⁴ / 10^-14), 区间取中值
        # (区间分隔符仅 ~–—到, ASCII 连字符保留给指数负号)
        for m in re.finditer(
                r"(?:固相扩散系数|锂离子扩散系数|扩散系数|D\s*_\s*s)\s*(?:为|是|大约|约为|约|≈|=|＝|[:：])?\s*"
                r"([\d.][^\s，,。；;]{0,24}?)\s*" + cls._D_UNIT,
                text, re.IGNORECASE):
            unit_prefix = (m.group(2) or "").lower()  # 捕获组: 1=数值窗口, 2=(m|cm)
            tokens = re.split(r"[~–—到]", m.group(1))
            vals = [cls._parse_sci_number(tok) for tok in tokens]
            vals = [v for v in vals if v is not None]
            if not vals:
                continue
            v = sum(vals) / len(vals)
            if unit_prefix == "cm":
                v *= 1e-4  # cm²/s → m²/s
            record(owner(m.start()), "D_s_m2s", v)
        # N/P 比（电芯级）
        m = re.search(r"N\s*/\s*P(?:\s*比)?[^\d]{0,6}(" + cls._NUM + r")", text, re.IGNORECASE)
        if m:
            v = mid(m.group(1))
            if v is not None:
                geometry["n_p_ratio"] = v
        # 电压窗口: 电压/工作窗口 X–Y V（电芯级条件，v_min/v_max 取区间端点）
        for m in re.finditer(
                r"(?:电压窗口|工作窗口|电压范围|截止电压)[^\d]{0,10}(" + cls._RANGE + r")\s*V",
                text, re.IGNORECASE):
            nums = re.findall(cls._NUM, m.group(1))
            if nums:
                cond = geometry.setdefault("condition", {})
                cond.setdefault("v_min", float(nums[0]))
                if len(nums) > 1:
                    cond.setdefault("v_max", float(nums[-1]))
        # 放电倍率: 倍率关键词锚定 X C / XC（区分容量 mAh·cm⁻² 与材料名中的 C）
        for m in re.finditer(
                r"(?:放电倍率|充放电倍率|倍率)[^\d]{0,6}(" + cls._NUM + r")\s*C\b",
                text, re.IGNORECASE):
            v = mid(m.group(1))
            if v is not None:
                geometry.setdefault("condition", {}).setdefault("c_rate", v)

        # 清掉无命中的组件桶，全部为空则返回空 dict（下游据此省略 geometry 键）
        return {k: v for k, v in geometry.items() if v}




    @staticmethod
    def _extract_energy_claim(text: str) -> Optional[float]:
        """从答案或问题文本提取目标/声称能量密度数值（Wh/kg）。"""
        if not text:
            return None
        # 1. 优先匹配"预期指标"章节中的数值声明
        sec_m = re.search(
            r"##\s*(?:\d+\.\s*)?预期[^\n]*指标.*?(?=\n##\s|\n---|\Z)",
            text,
            re.DOTALL,
        )
        if sec_m:
            m = re.search(r"(\d+(?:\.\d+)?)\s*Wh/kg", sec_m.group(0), re.IGNORECASE)
            if m:
                return float(m.group(1))
        # 2. 其次匹配显式能量密度指标声明句
        m_claim = re.search(
            r"(?:单体(?:质量)?|电芯(?:级)?(?:质量)?|目标|预期)?能量密度[^\n\d]{0,12}?(?:约|约为|达|达到|为|不低于|>=|≥|:|：)?\s*(\d+(?:\.\d+)?)\s*Wh/kg",
            text,
            re.IGNORECASE,
        )
        if m_claim:
            return float(m_claim.group(1))
        m = re.search(r"(\d+(?:\.\d+)?)\s*Wh/kg", text, re.IGNORECASE)
        return float(m.group(1)) if m else None

    @staticmethod
    def _issues_to_queries(issues) -> List[str]:
        """兼容入口只接受结构化查询，不再截取审核套话组成检索问题。"""
        queries = [q for issue in issues if isinstance(issue, dict) for q in issue.get("queries", []) if isinstance(q, str) and q.strip()]
        return list(dict.fromkeys(queries))[:3]

    def run_with_export(
        self, question: str,
        include_process_log: bool = True,
        output_dir: Optional[Path] = None,
    ) -> Tuple[str, str]:
        """运行并导出结构化 Markdown."""
        result = self.run(question)

        md = build_answer_markdown(
            question=question,
            final_answer=result["final_answer"],
            plan=result["plan"],
            evidence=result["evidence"],
            reviewer_output=result["reviewer_output"],
            include_process_log=include_process_log,
        )

        path = save_markdown(md, "rag_materials_screening", output_dir)
        return md, str(path)

    # ── 多轮对话支持 ──

    def chat(
        self,
        question: str,
        chat_history: Optional[List[Tuple[str, str]]] = None,
    ) -> Dict[str, Any]:
        """多轮对话接口.

        Args:
            question: 当前用户问题
            chat_history: [(user_msg, assistant_msg), ...] 的历史记录

        Returns:
            同 run() 的返回格式
        """
        history_context = ""
        if chat_history:
            MAX_HISTORY_TOKENS = 4000
            OVERLAP_TOKENS = 500
            enc = tiktoken.get_encoding("cl100k_base")

            # 从最新轮次往前,精确计算每轮 token 数
            rounds = []
            for user_msg, assistant_msg in reversed(chat_history):
                user_tok = len(enc.encode(user_msg))
                asst_tok = len(enc.encode(assistant_msg or ""))
                rounds.append({
                    "user": user_msg,
                    "assistant": assistant_msg,
                    "tokens": user_tok + asst_tok + 20,
                })

            budget = MAX_HISTORY_TOKENS
            kept = []
            for r in rounds:
                if r["tokens"] <= budget:
                    kept.append(r)
                    budget -= r["tokens"]
                elif budget > OVERLAP_TOKENS:
                    # 超预算但还有 overlap 额度:压缩这一轮,保留用户核心 + 助手摘要
                    kept.append({
                        "user": r["user"][:100],
                        "assistant": (r["assistant"] or "")[:50],
                        "tokens": 0,
                    })
                    break
                else:
                    break

            parts = []
            for r in reversed(kept):
                parts.append(f"用户: {r['user']}")
                if r["assistant"]:
                    parts.append(f"助手: {r['assistant']}...")
            history_context = "\n".join(parts)

        # 多轮递进检索:将上轮 evidence 传给检索器,来源匹配加权
        result = self.run(
            question,
            history_context=history_context,
            previous_evidence=self._last_evidence,
        )

        # 缓存本轮 evidence 供下轮使用
        self._last_evidence = result.get("evidence", [])

        return result

    # ── 辅助方法 ──

    @staticmethod
    def _build_reference_section(evidence: List[Dict[str, Any]]) -> str:
        """构建参考来源章节"""
        if not evidence:
            return "\n\n## 参考来源\n- 暂无可用来源"

        title_map: Dict[str, str] = {}
        for item in evidence:
            title = (item.get("title") or "").strip()
            if not title:
                continue
            meta = item.get("metadata") or {}
            norm_src = normalize_source_path(item.get("source", ""))
            norm_sf = normalize_source_path(meta.get("source_file", ""))
            sp = (meta.get("source_paper") or "").strip()
            doi = (item.get("doi") or meta.get("doi") or "").strip()
            for k in (norm_src, norm_sf, sp, doi):
                if k and k not in title_map:
                    title_map[k] = title

        dedup_order: List[str] = []
        dedup_items: Dict[str, Dict[str, Any]] = {}
        for item in evidence:
            meta = item.get("metadata") or {}
            norm_src = normalize_source_path(item.get("source", ""))
            norm_sf = normalize_source_path(meta.get("source_file", ""))
            sp = (meta.get("source_paper") or "").strip()
            doi = (item.get("doi") or meta.get("doi") or "").strip()
            title = (item.get("title") or "").strip()
            if not title:
                for k in (sp, norm_src, norm_sf, doi):
                    if k and k in title_map:
                        title = title_map[k]
                        break

            ref_key = norm_src or doi or str(item.get("passage_id", ""))
            if ref_key not in dedup_items:
                dedup_order.append(ref_key)
                dedup_items[ref_key] = {
                    "source": norm_src,
                    "doi": doi,
                    "title": title,
                    "_source_type": item.get("_source_type", ""),
                    "source_display": item.get("source_display", ""),
                    "passage_id": item.get("passage_id", ""),
                }
            else:
                cur = dedup_items[ref_key]
                if not cur["title"] and title:
                    cur["title"] = title
                if not cur["doi"] and doi:
                    cur["doi"] = doi
                if not cur["source_display"] and item.get("source_display"):
                    cur["source_display"] = item["source_display"]

        uniq: List[str] = []
        for ref_key in dedup_order:
            rec = dedup_items[ref_key]
            doi = rec["doi"]
            title = rec["title"]
            if rec["_source_type"] == "materials_project":
                ref_line = f"- [{rec['source_display'] or title}]({rec['source']}) [{rec['passage_id']}]"
            elif title:
                ref_line = f"- {title}" + (f" (DOI: {doi})" if doi else "")
            else:
                ref_line = f"- {rec['source']}"
            uniq.append(ref_line)

        lines = ["", "", "## 参考来源"]
        for u in uniq:
            lines.append(u)
        return "\n".join(lines)

    def get_evidence_display(self, retrieval: Optional[Dict[str, Any]] = None) -> str:
        """格式化证据展示"""
        if retrieval:
            return format_evidence_display(retrieval)
        return ""

    def get_prompt_summary(self) -> str:
        """获取所有 Prompt 汇总(用于审计展示)"""
        return get_prompt_summary()
