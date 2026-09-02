"""主 RAG Pipeline 编排器 — 串联多智能体协作流程(材料筛选场景)"""

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
from .bm25_kb import BM25KnowledgeBase, Passage
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
    """多智能体 RAG 主 Pipeline(高比能锂电池材料筛选场景).

    数据流:
    paragraph_metadata_pipeline_v5_qwen.py 入库
      → papers → clean → split paragraphs → LLM label → qwen3-embedding:8b → Chroma
        ↑                                                                    ↓
        └────────────────── RAGPipeline 检索(同一嵌入模型) ────────────────┘

    每个 Agent 可绑定不同模型:
    - Planner: 轻量模型(如 deepseek-v4-flash)做快速规划
    - Writer: 强模型(如 deepseek-v4-pro)做材料筛选对比
    - Reviewer: 强模型做审核

    使用方式:
        # 默认:Chroma 向量检索(与入库同一数据库)
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
    ):
        """
        Args:
            kb_dir: (可选) 原始 Markdown 文件夹,用于 TF-IDF 辅助检索
            llm_backend: "auto" | "openai" | "deepseek" | "ollama" | "rule"
            llm_model: 统一模型名(当未单独指定 Agent 模型时使用)
            chroma_dir: Chroma 持久化目录
            chroma_collection: Chroma collection 名
            retrieval_mode: "chroma" | "hybrid" | "tfidf"
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
    def _init_bm25_from_chroma(chroma_dir: str, collection_name: str) -> "BM25KnowledgeBase":
        """从 Chroma 集合或本地段落 JSON 构建 BM25 索引, 保持 passage_id 对齐."""
        import hashlib
        import json
        from .config import PROJECT_ROOT

        kb = BM25KnowledgeBase()
        loaded = False

        # 1. 尝试从 Chroma 集合读取
        try:
            import chromadb
            client = chromadb.PersistentClient(str(chroma_dir))
            collection = client.get_collection(collection_name)
            total = collection.count()
            if total > 0:
                print(f"[RAGPipeline] BM25 从 Chroma 构建: {total} 条段落")
                batch_size = 1000
                offset = 0
                while offset < total:
                    batch = collection.get(
                        offset=offset, limit=batch_size,
                        include=["documents", "metadatas"],
                    )
                    for i, text in enumerate(batch["documents"]):
                        meta = batch["metadatas"][i]
                        pid = hashlib.md5(text.encode()).hexdigest()[:12]
                        source = meta.get("source_file", meta.get("source_paper", "chroma"))
                        kb.add_passage(passage_id=pid, text=text, source=str(source), metadata=meta)
                    offset += batch_size
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
                                    kb.add_passage(passage_id=pid, text=txt, source=str(src), metadata=item)
                except Exception:
                    pass


        # 构建 BM25 索引
        kb.build_index()
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

        if db_type == "textbook":
            # 电子书 Chroma + BM25 + RRF
            if not self.ebook_vector_store:
                print("[retrieve_context] 电子书库未加载,降级为 both")
                return self.retrieve_context(
                    "both", question, queries, focus_component, focus_labels,
                    use_label_filter, previous_evidence, top_k,
                )
            all_results: Dict[str, Dict[str, Any]] = {}
            for q in queries:
                try:
                    batch = self.multi_retriever.search_ebook(q, top_k=max(top_k, 20))
                    for r in batch:
                        pid = r["passage_id"]
                        if pid not in all_results or r["score"] > all_results[pid].get("score", 0):
                            all_results[pid] = r
                except Exception as e:
                    print(f"[retrieve_context] 电子书检索失败({q}): {e}")
            results = sorted(all_results.values(), key=lambda x: x["score"], reverse=True)
            return {
                "db_type": "textbook",
                "results": results[:top_k],
                "search_logs": [],
            }

        elif db_type == "both":
            # 双库加权混合检索（各库内部 Chroma+BM25+RRF）
            _lw = lit_weight if lit_weight is not None else 0.7
            _ew = ebook_weight if ebook_weight is not None else 0.3
            try:
                all_results: Dict[str, Dict[str, Any]] = {}
                for q in queries:
                    batch = self.multi_retriever.search(
                        q, top_k_lit=15, top_k_ebook=10, top_k_final=30,
                        lit_weight=_lw, ebook_weight=_ew,
                    )
                    for r in batch:
                        pid = r["passage_id"]
                        if pid not in all_results or r["score"] > all_results[pid].get("score", 0):
                            all_results[pid] = r
                results = sorted(all_results.values(), key=lambda x: x["score"], reverse=True)
                return {
                    "db_type": "both",
                    "results": results[:top_k],
                    "search_logs": [],
                }
            except Exception as e:
                print(f"[retrieve_context] 双库检索失败({e}),降级为文献库")
                # fall through to literature

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
        plan = self.planner.run(question, history_context)
        print(f"  -> 拆解为 {len(plan.get('retrieval_queries', []))} 个子问题")
        print(f"  -> Planner 模式: {'LLM 生成' if not plan.get('fallback') else '规则回退'}")

        # ── Stage 2: Retrieval (db_type 路由) ──
        # 推荐/推理类问题关闭标签硬过滤,让 reranker 做纯语义筛选
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

        # ── Stage 3: Writer ──
        print("  [3/4] Writer 生成中...")
        _writer_scheme = self._extract_scheme(question, plan)
        writer_output = self.writer.run(
            question=question,
            plan=plan,
            evidence=evidence,
            history_context=history_context,
            scheme=_writer_scheme,
        )
        print(f"  -> {'规则回退' if writer_output.get('fallback') else 'LLM 生成'}")

        # ── Stage 4: Reviewer (带分层回退) ──
        max_review_rounds = 3
        _draft = writer_output.get("draft_answer", "")
        _current_plan = dict(plan)
        _current_queries = list(_current_plan.get("retrieval_queries", [question]))
        _current_evidence = list(evidence)
        _final = None
        for _round in range(max_review_rounds):
            print(f"  [4/4] Reviewer 审核中 (第{_round+1}轮)...")
            _scheme = self._extract_scheme(question, _current_plan, draft_text=_draft)
            _claimed_energy = self._extract_energy_claim(_draft)
            reviewer_output = self.reviewer.run(
                question=question,
                evidence=_current_evidence,
                draft_answer=_draft,
                history_context=history_context,
                scheme=_scheme,
                claimed_energy=_claimed_energy,
            )

            _confidence = reviewer_output.get("confidence", "low")
            _issues = reviewer_output.get("issues", [])
            _error_type = reviewer_output.get("error_type", "writing")
            print(f"  -> 置信度: {_confidence}, 错误类型: {_error_type}, 问题数: {len(_issues)}")

            if _confidence in ("high", "medium"):
                _final = reviewer_output.get("revised_answer", _draft)
                break

            if _error_type == "retrieval" and _issues:
                # ── 缺证据 → 回退到检索（追加，保留已有证据）──
                print(f"  -> 证据不足,回退检索 (第{_round+2}轮)...")
                new_queries = self._issues_to_queries(_issues)
                _current_queries = list(set(_current_queries + new_queries))
                _current_plan["retrieval_queries"] = _current_queries
                _retry_retrieval = self.retrieve_context(
                    db_type=_db_type,
                    question=question,
                    queries=_current_queries,
                    focus_component=_current_plan.get("focus_component"),
                    focus_labels=_current_plan.get("focus_labels"),
                    use_label_filter=not _current_plan.get("needs_reasoning", False),
                    previous_evidence=previous_evidence,
                    top_k=top_k,
                )
                # 合并新旧证据，去重
                _merged: Dict[str, Any] = {e["passage_id"]: e for e in _current_evidence}
                for e in _retry_retrieval.get("results", []):
                    if e["passage_id"] not in _merged:
                        _merged[e["passage_id"]] = e
                _current_evidence = sorted(_merged.values(), key=lambda x: x.get("score", 0), reverse=True)[:top_k]
                print(f"  -> 合并后共 {len(_current_evidence)} 条")
                # 重新 reranker
                if self._reranker_enabled and self.reranker and _current_evidence:
                    _current_evidence = self.reranker.rerank(
                        query=question, passages=_current_evidence,
                        top_k=RERANKER_TOP_K, alpha=RERANKER_ALPHA,
                    )
                # 完整重新 Writer
                _retry_writer = self.writer.run(
                    question=question, plan=_current_plan,
                    evidence=_current_evidence, history_context=history_context,
                )
                _draft = _retry_writer.get("draft_answer", _draft)
            elif _error_type == "writing" and _issues:
                # ── 文字/学术错误 → 回退到 Writer 修正 ──
                print(f"  -> 文字修正,回退 Writer (第{_round+2}轮)...")
                _draft = self.writer.revise(
                    question=question, plan=_current_plan,
                    evidence=_current_evidence, draft_answer=_draft,
                    feedback=_issues, history_context=history_context,
                )
            else:
                break

            # 最后一轮：修正完后直接输出，不再 Review
            if _round == max_review_rounds - 1:
                _final = _draft
                break

        final_answer = _final or writer_output.get("draft_answer", "无法生成材料筛选答案.")

        # 追加参考来源
        ref_section = self._build_reference_section(evidence)
        final_answer = final_answer + ref_section

        # 提取结构化材料组合方案 (供 Stage 5 物理仿真与门禁检查消费)
        extracted_scheme = self._extract_scheme(question, plan, final_answer)
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
        if rule_conf == "low":
            confidence = "low"
        elif rule_conf in ("high", "medium"):
            confidence = rule_conf
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
            "final_answer": final_answer,
            "evidence": evidence,
            "confidence": confidence,
            "scheme": extracted_scheme or {},
            "rule_checks": rule_checks,
            "timestamp": datetime.now().isoformat(),
        }


    def _extract_scheme(self, question: str, plan: Dict, draft_text: str = "") -> Optional[Dict[str, Any]]:
        """从问题+规划+答案正文中提取材料组合方案（供 RelationEngine / Stage 5 硬规则与物理仿真消费）."""
        if self.relation_engine is None:
            return None
        try:
            combined = f"{question}\n{draft_text}"
            entities = self.relation_engine.extract_entities(combined)
            scheme = {}
            for cat in ("cathode", "anode", "electrolyte"):
                ids = entities.get(cat) or []
                if ids:
                    if cat == "electrolyte":
                        # 针对电解液，若同时出现局部高浓/含氟与常规碳酸酯别名，优先采用特化体系
                        if "lhce" in ids:
                            scheme[cat] = "lhce"
                        elif "fluorinated" in ids:
                            scheme[cat] = "fluorinated"
                        elif "high_concentration" in ids:
                            scheme[cat] = "high_concentration"
                        else:
                            scheme[cat] = ids[0]
                    elif cat == "cathode":
                        # 优先采用高比能正极
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
            # 提取添加剂
            additives = []
            for a_kw in ["fec", "vc", "lidfob", "dto", "dtd"]:
                if a_kw in combined.lower():
                    additives.append(a_kw.upper())
            if additives:
                scheme["additives"] = additives
            # 提取目标能量密度
            claimed = self._extract_energy_claim(combined)
            if claimed:
                scheme["target_energy_wh_kg"] = claimed
            return scheme or None
        except Exception:
            return None




    @staticmethod
    def _extract_energy_claim(text: str) -> Optional[float]:
        """从答案文本提取声称的能量密度数值（Wh/kg）。"""
        if not text:
            return None
        m = re.search(r"(\d+(?:\.\d+)?)\s*Wh/kg", text)
        return float(m.group(1)) if m else None

    @staticmethod
    def _issues_to_queries(issues: List[str]) -> List[str]:
        """从 Reviewer issues 中提取缺失的关键词,转为补充检索查询"""
        queries = []
        for issue in issues:
            # 提取"XX 缺乏证据"中的关键名词
            text = issue.lower()
            # 常见模式: 缺少XXX证据/未找到XXX文献/XXX不足
            for kw in ["缺乏", "缺少", "不足", "未找到", "未发现", "没有"]:
                if kw in text:
                    # 取 kw 之后的 20 个字作为查询词
                    idx = text.index(kw) + len(kw)
                    snippet = text[idx:idx+30].strip().rstrip(".,;:！？。，；：")
                    # 只取有意义的词
                    snippet = re.sub(r'[的证据文献数据研究]', '', snippet).strip()
                    if len(snippet) > 2 and snippet not in queries:
                        queries.append(snippet)
                    break
        return queries[:3]  # 最多追加 3 个新查询

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

        uniq: List[str] = []
        seen = set()
        for item in evidence:
            ref_key = item.get("source", "") or item.get("doi", "") or item["passage_id"]
            if ref_key not in seen:
                seen.add(ref_key)
                doi = item.get("doi", "")
                title = item.get("title", "")
                if title:
                    ref_line = f"- {title}" + (f" (DOI: {doi})" if doi else "")
                else:
                    ref_line = f"- {item.get('source', '')}"
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