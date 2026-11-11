"""双库加权混合检索 — 文献 Chroma + 电子书 Chroma（各库内部 Chroma+BM25+RRF）

每个库内部做 Chroma + BM25 双路召回，RRF 融合排序。
融合后的分数用于跨库加权合并。

用法:
    from .multi_retrieval import MultiRetrieval
    mr = MultiRetrieval()
    results = mr.search("能斯特方程推导")         # 双库加权
    ebook = mr.search_ebook("锂离子电池原理", 10)  # 单库(RRF)
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .bm25_kb import BM25KnowledgeBase, normalize_source_path, resolve_paper_title_map

# ── 默认配置（与现有文献库和电子书库路径一致） ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LIT_CHROMA_DIR = str(_PROJECT_ROOT / "miner" / "chroma" / "paragraphs_q")
_LIT_COLLECTION = "battery_paragraphs_q"
_EBOOK_CHROMA_DIR = str(_PROJECT_ROOT / "miner" / "chroma" / "ebooks")
_EBOOK_COLLECTION = "ebook_chunks"
_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", os.getenv("EMBEDDING_BASE_URL", "http://localhost:11434"))
_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:8b")

_RRF_K = 60  # RRF 常数（与文献库 RetrievalAgent 一致）


class MultiRetrieval:
    """双库混合检索器。

    每个库内部使用 Chroma + BM25 双路召回 + RRF 融合排序，
    跨库之间加权融合后排序。
    """

    def __init__(
        self,
        lit_chroma_dir: str = _LIT_CHROMA_DIR,
        lit_collection: str = _LIT_COLLECTION,
        ebook_chroma_dir: str = _EBOOK_CHROMA_DIR,
        ebook_collection: str = _EBOOK_COLLECTION,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        lit_weight: float = 0.7,
        ebook_weight: float = 0.3,
    ):
        self.lit_weight = lit_weight
        self.ebook_weight = ebook_weight
        resolved_base_url = base_url or os.getenv("OLLAMA_BASE_URL", _OLLAMA_BASE_URL)
        resolved_model = model or os.getenv("EMBEDDING_MODEL", _EMBEDDING_MODEL)

        try:
            from langchain_chroma import Chroma
            from langchain_ollama import OllamaEmbeddings
        except ImportError as e:
            raise ImportError(
                "MultiRetrieval 检索模块依赖缺失: 请安装 'auto-battery-research[rag]' (pip install langchain-chroma langchain-ollama chromadb)"
            ) from e

        try:
            embeddings = OllamaEmbeddings(
                model=resolved_model,
                base_url=resolved_base_url,
                client_kwargs={"timeout": 15.0},
            )
        except Exception:
            embeddings = OllamaEmbeddings(
                model=resolved_model,
                base_url=resolved_base_url,
            )

        self.lit_store = Chroma(
            collection_name=lit_collection,
            embedding_function=embeddings,
            persist_directory=lit_chroma_dir,
        )

        # 文献库 BM25
        self.lit_bm25: Optional[BM25KnowledgeBase] = None
        try:
            self.lit_bm25 = self._build_bm25_from_store(lit_chroma_dir, lit_collection)
            print(f"[MultiRetrieval] 文献 BM25: {self.lit_bm25.passage_count} 段落")
        except Exception as e:
            print(f"[MultiRetrieval] 文献 BM25 构建失败: {e}")

        # 电子书 Chroma + BM25
        has_ebook = Path(ebook_chroma_dir).exists()
        self.ebook_store = (
            Chroma(
                collection_name=ebook_collection,
                embedding_function=embeddings,
                persist_directory=ebook_chroma_dir,
            )
            if has_ebook
            else None
        )
        self._ebook_available = has_ebook

        self.ebook_bm25: Optional[BM25KnowledgeBase] = None
        if has_ebook:
            try:
                self.ebook_bm25 = self._build_bm25_from_store(ebook_chroma_dir, ebook_collection)
                print(f"[MultiRetrieval] 电子书 BM25: {self.ebook_bm25.passage_count} 段落")
            except Exception as e:
                print(f"[MultiRetrieval] 电子书 BM25 构建失败: {e}")

    # ── BM25 构建 ──────────────────────────────────────────────

    @staticmethod
    def _build_bm25_from_store(chroma_dir: str, collection_name: str, max_passages: int = 10000) -> BM25KnowledgeBase:
        """从 Chroma 持久化目录或本地段落 JSON 读取段落，构建 BM25 索引。
        注: 当向量库规模极大 (如 >10,000 条) 时，采样前 max_passages 条构建内存 BM25，
        全量检索由 Chroma ANN 向量索引负责，避免初始化阻塞数分钟。
        """
        import json
        kb = BM25KnowledgeBase()
        paper_title_map: Dict[str, str] = {}
        loaded = False
        try:
            import chromadb
            client = chromadb.PersistentClient(str(chroma_dir))
            collection = client.get_collection(collection_name)
            total = collection.count()
            if total > 0:
                target_count = min(total, max_passages)
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
                        meta = dict(metas[i]) if i < len(metas) and metas[i] else {}
                        if meta.get("source_file"):
                            meta["source_file"] = normalize_source_path(str(meta["source_file"]))
                        raw_source = meta.get("source_file") or meta.get("source_paper") or "chroma"
                        source = normalize_source_path(str(raw_source)) or "chroma"
                        pid = str(meta.get("passage_id") or meta.get("chunk_id") or hashlib.md5(text.encode()).hexdigest()[:12])
                        title = str(meta.get("title") or "").strip()
                        sp = str(meta.get("source_paper") or "").strip()
                        sf = str(meta.get("source_file") or "").strip()
                        if title:
                            if sp and sp not in paper_title_map:
                                paper_title_map[sp] = title
                            if sf and sf not in paper_title_map:
                                paper_title_map[sf] = title
                        kb.add_passage(passage_id=pid, text=text, source=source, metadata=meta)
                    offset += curr_limit
                loaded = True
        except Exception:
            pass

        if not loaded:
            para_json_candidates = [
                _PROJECT_ROOT / "miner" / "json" / "100" / "paragraph_metadata_v4.json",
                _PROJECT_ROOT / "miner" / "json" / "100" / "paragraph_metadata_v4_20260622_155323.json",
                _PROJECT_ROOT / "miner" / "json" / "test_paragraphs.json",
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
                                    meta = dict(item)
                                    if meta.get("source_file"):
                                        meta["source_file"] = normalize_source_path(str(meta["source_file"]))
                                    pid = str(meta.get("_id") or meta.get("passage_id") or hashlib.md5(txt.encode()).hexdigest()[:12])
                                    raw_src = meta.get("source_file") or meta.get("source_paper") or meta.get("source") or "Academic Paper"
                                    src = normalize_source_path(str(raw_src)) or "Academic Paper"
                                    title = str(meta.get("title") or "").strip()
                                    sp = str(meta.get("source_paper") or "").strip()
                                    sf = str(meta.get("source_file") or "").strip()
                                    if title:
                                        if sp and sp not in paper_title_map:
                                            paper_title_map[sp] = title
                                        if sf and sf not in paper_title_map:
                                            paper_title_map[sf] = title
                                    kb.add_passage(passage_id=pid, text=txt, source=src, metadata=meta)
                except Exception:
                    pass

        for p in kb.passages:
            meta = p.metadata if isinstance(p.metadata, dict) else {}
            if not str(meta.get("title") or "").strip():
                sp = str(meta.get("source_paper") or "").strip()
                sf = normalize_source_path(str(meta.get("source_file") or p.source or ""))
                backfilled = paper_title_map.get(sp) or paper_title_map.get(sf) or ""
                if backfilled:
                    meta["title"] = backfilled
                    p.metadata = meta

        kb.paper_title_map = paper_title_map
        kb.build_index()
        return kb


    # ── 单库 Chroma + BM25 + RRF ──────────────────────────────

    def _search_hybrid_rrf(
        self,
        store: Chroma,
        bm25: Optional[BM25KnowledgeBase],
        query: str,
        top_k: int,
        source_type: str = "literature",
        component: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """单库：Chroma + BM25 双路召回，RRF 融合。

        Args:
            store: Chroma 向量存储
            bm25: BM25 索引（可为 None，退化为纯 Chroma）
            query: 检索问题
            top_k: 返回前 k 条
            source_type: 来源标记（"literature" / "ebook"）
            component: 可选组件过滤（"cathode" / "anode" / "electrolyte"），仅对 literature 生效

        Returns:
            按 RRF 分数降序的结果列表
        """
        use_comp = component.strip().lower() if (component and source_type == "literature") else None
        k_chroma = max(top_k, 20)
        diagnostics = getattr(self, "last_diagnostics", [])

        # ── Chroma 通道 ──
        raw_chroma: List[Tuple[Any, float]] = []
        if store is not None:
            try:
                if use_comp:
                    try:
                        raw_chroma = list(store.similarity_search_with_score(
                            query, k=k_chroma, filter={"component": use_comp}
                        ))
                        filter_accepted = True
                    except TypeError:
                        raw_chroma = list(store.similarity_search_with_score(query, k=k_chroma))
                        filter_accepted = False
                    if not raw_chroma and filter_accepted:
                        raw_chroma = list(store.similarity_search_with_score(query, k=k_chroma))
                    if raw_chroma:
                        comp_filtered = [
                            (doc, sc) for doc, sc in raw_chroma
                            if not ((getattr(doc, "metadata", None) or {}).get("component"))
                            or str((getattr(doc, "metadata", None) or {}).get("component")).strip().lower() == use_comp
                        ]
                        if comp_filtered:
                            raw_chroma = comp_filtered
                else:
                    raw_chroma = list(store.similarity_search_with_score(query, k=k_chroma))
            except Exception as e:
                diagnostics.append({"query": query, "source_type": source_type, "channel": "vector", "status": "ERROR", "error": str(e)})
                print(f"[MultiRetrieval] Chroma 检索失败: {e}")
            else:
                diagnostics.append({"query": query, "source_type": source_type, "channel": "vector", "status": "OK", "hits": len(raw_chroma)})

        # ── BM25 通道 ──
        raw_bm25: List[Tuple[Any, float]] = []
        if bm25 is not None:
            try:
                bm25_fetch_k = max(top_k * 5, 300) if use_comp else max(top_k, 20)
                hits = bm25.search(query, top_k=bm25_fetch_k)
                if use_comp and hits:
                    filtered_hits = [
                        (p, s) for p, s in hits
                        if not ((getattr(p, "metadata", None) or {}).get("component"))
                        or str((getattr(p, "metadata", None) or {}).get("component")).strip().lower() == use_comp
                    ]
                    if filtered_hits:
                        raw_bm25 = filtered_hits[:max(top_k, 20)]
                    elif not raw_chroma:
                        raw_bm25 = hits[:max(top_k, 20)]
                    else:
                        raw_bm25 = []
                else:
                    raw_bm25 = hits[:max(top_k, 20)]
            except Exception as e:
                diagnostics.append({"query": query, "source_type": source_type, "channel": "bm25", "status": "ERROR", "error": str(e)})
                print(f"[MultiRetrieval] BM25 检索失败: {e}")
            else:
                diagnostics.append({"query": query, "source_type": source_type, "channel": "bm25", "status": "OK", "hits": len(raw_bm25)})
        self.last_diagnostics = diagnostics

        # ── 汇总同论文题名映射 ──
        cache = getattr(self, "_chroma_title_cache", None)
        if cache is None:
            cache = {}
            self._chroma_title_cache = cache
        paper_title_map = resolve_paper_title_map(
            store,
            bm25,
            raw_chroma,
            raw_bm25,
            title_cache=cache,
        )

        def _build_hit(pid: str, raw_source: str, score: float, text: str, raw_meta: Optional[dict]) -> Dict[str, Any]:
            meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
            if meta.get("source_file"):
                meta["source_file"] = normalize_source_path(str(meta["source_file"]))
            source = normalize_source_path(str(meta.get("source_file") or raw_source or meta.get("source_paper") or "unknown"))
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
            return {
                "passage_id": pid,
                "source": source,
                "source_display": source_display,
                "doi": doi,
                "title": title,
                "score": score,
                "text": text,
                "metadata": meta,
                "_source_type": source_type,
            }

        chroma_results: List[Dict[str, Any]] = []
        for doc, score in raw_chroma:
            meta = getattr(doc, "metadata", None) or {}
            pid = meta.get("passage_id") or meta.get("chunk_id") or hashlib.md5(doc.page_content.encode()).hexdigest()[:12]
            sim = max(0.0, 1.0 - score / 2.0)
            chroma_results.append(_build_hit(str(pid), str(meta.get("source_paper") or "unknown"), sim, doc.page_content, meta))

        bm25_results: List[Dict[str, Any]] = []
        for passage, _score in raw_bm25:
            meta = getattr(passage, "metadata", None) or {}
            bm25_results.append(_build_hit(str(passage.passage_id), str(getattr(passage, "source", "unknown")), 1.0, passage.text, meta))

        # ── 单通道退化 ──
        if not bm25_results:
            return chroma_results[:top_k]
        if not chroma_results:
            return bm25_results[:top_k]

        # ── RRF 融合 ──
        rank_map: Dict[str, float] = {}
        for rank, r in enumerate(chroma_results):
            rank_map[r["passage_id"]] = rank_map.get(r["passage_id"], 0.0) + 1.0 / (_RRF_K + rank + 1)
        for rank, r in enumerate(bm25_results):
            rank_map[r["passage_id"]] = rank_map.get(r["passage_id"], 0.0) + 1.0 / (_RRF_K + rank + 1)

        all_by_pid: Dict[str, Dict[str, Any]] = {}
        for r in chroma_results + bm25_results:
            pid = r["passage_id"]
            if pid not in all_by_pid:
                all_by_pid[pid] = r
            else:
                prev = all_by_pid[pid]
                prev_title = str(prev.get("title") or (prev.get("metadata") or {}).get("title") or "").strip()
                new_title = str(r.get("title") or (r.get("metadata") or {}).get("title") or "").strip()
                if prev_title and not new_title:
                    r = dict(r)
                    r["title"] = prev_title
                    r_meta = dict(r.get("metadata") or {})
                    r_meta["title"] = prev_title
                    r["metadata"] = r_meta
                    doi = r.get("doi") or r_meta.get("source_paper") or ""
                    r["source_display"] = f"{prev_title} (DOI: {doi})" if doi else prev_title
                all_by_pid[pid] = r

        fused = []
        for pid, r in all_by_pid.items():
            r = dict(r)
            r["score"] = round(rank_map.get(pid, 0.0), 6)
            fused.append(r)

        return sorted(fused, key=lambda x: x["score"], reverse=True)[:top_k]

    # ── 单库混合检索（对外接口） ──────────────────────────────

    def search_literature(
        self, query: str, top_k: int = 20, component: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """文献库单库：Chroma + BM25 + RRF。"""
        return self._search_hybrid_rrf(
            self.lit_store, self.lit_bm25, query, top_k,
            source_type="literature",
            component=component,
        )

    def search_ebook(
        self, query: str, top_k: int = 20,
    ) -> List[Dict[str, Any]]:
        """电子书单库：Chroma + BM25 + RRF。"""
        if not self.ebook_store:
            print("[MultiRetrieval] 电子书库未加载")
            return []
        return self._search_hybrid_rrf(
            self.ebook_store, self.ebook_bm25, query, top_k,
            source_type="ebook",
        )

    # ── 混合检索：文献 + 电子书 ───────────────────────────────

    def search(
        self,
        query: str,
        top_k_lit: int = 20,
        top_k_ebook: int = 10,
        top_k_final: int = 25,
        lit_weight: Optional[float] = None,
        ebook_weight: Optional[float] = None,
        component: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """双库检索 → 各库 RRF → 加权融合 → 最终排序。

        Args:
            query: 用户问题
            top_k_lit:   文献库取前几条
            top_k_ebook: 电子书库取前几条
            top_k_final: 最终返回前几条
            lit_weight:   (可选)覆写文献库权重,默认 self.lit_weight
            ebook_weight: (可选)覆写电子书权重,默认 self.ebook_weight
            component:    (可选)文献库组件过滤(cathode/anode/electrolyte)

        Returns:
            按融合分数降序排列的结果列表。
        """
        _lit_w = lit_weight if lit_weight is not None else self.lit_weight
        _ebk_w = ebook_weight if ebook_weight is not None else self.ebook_weight

        lit_results = self._search_hybrid_rrf(
            self.lit_store, self.lit_bm25, query, top_k_lit,
            source_type="literature",
            component=component,
        )

        ebook_results = []
        if self.ebook_store:
            ebook_results = self._search_hybrid_rrf(
                self.ebook_store, self.ebook_bm25, query, top_k_ebook,
                source_type="ebook",
            )

        merged: Dict[str, Dict[str, Any]] = {}
        if _lit_w > 0:
            for r in lit_results:
                merged[r["passage_id"]] = r
        if _ebk_w > 0:
            for r in ebook_results:
                if r["passage_id"] not in merged:
                    factor = _ebk_w / max(_lit_w, 0.01)
                    r["score"] = round(r["score"] * factor, 6)
                    merged[r["passage_id"]] = r

        results = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
        return results[:top_k_final]

    # ── 状态 ────────────────────────────────────────────────────

    @property
    def status(self) -> str:
        parts = [f"文献: {self.lit_store._collection.count()} 段"]
        if self.lit_bm25:
            parts.append(f"文献 BM25: {self.lit_bm25.passage_count}")
        if self._ebook_available and self.ebook_store:
            try:
                cnt = self.ebook_store._collection.count()
                parts.append(f"电子书: {cnt} 段")
            except Exception:
                parts.append("电子书: 0 段")
            if self.ebook_bm25:
                parts.append(f"电子书 BM25: {self.ebook_bm25.passage_count}")
        else:
            parts.append("电子书: 未加载")
        return f"混合检索 | {' | '.join(parts)}"
