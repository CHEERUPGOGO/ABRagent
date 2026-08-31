"""BM25 轻量检索器 — 适配锂电池学术文献(中英混合)的段落检索.

使用 rank_bm25 的 BM25Okapi 算法,比 TF-IDF 对学术文献召回更准.
接口与旧 TFIDFKnowledgeBase 兼容,可无缝替换.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from rank_bm25 import BM25Okapi
    HAS_RANK_BM25 = True
except ImportError:
    HAS_RANK_BM25 = False
    class BM25Okapi:
        """Fallback term-frequency scoring when rank_bm25 is missing."""
        def __init__(self, corpus: List[List[str]]):
            self.corpus = corpus

        def get_scores(self, query: List[str]) -> List[float]:
            q_set = set(query)
            scores = []
            for doc in self.corpus:
                doc_set = set(doc)
                score = sum(1.0 for term in q_set if term in doc_set)
                scores.append(float(score))
            return scores



@dataclass
class Passage:
    """知识库段落"""
    source: str       # 来源文件名
    text: str         # 段落文本
    passage_id: str   # 段落唯一 ID
    metadata: dict    # 附加元数据(component, label, doi 等)


class BM25KnowledgeBase:
    """基于 BM25 的中英混合文献检索器.

    使用方式:
        kb = BM25KnowledgeBase()
        kb.add_passage(pid, text, source, meta)
        results = kb.search("NCM811的首次放电容量", top_k=10)
    """

    def __init__(self, kb_dir: Optional[Path] = None):
        self.kb_dir = kb_dir
        self.passages: List[Passage] = []
        self.bm25: Optional[BM25Okapi] = None
        self._tokenized_corpus: List[List[str]] = []
        if kb_dir:
            self.refresh()

    # ── 公开接口 ──────────────────────────────────────────────────────

    def add_passage(self, passage_id: str, text: str, source: str = "inline",
                    metadata: Optional[dict] = None) -> None:
        """直接添加单条段落(不重新分段),用于从 Chroma 对齐构建"""
        self.passages.append(Passage(
            source=source,
            text=text,
            passage_id=passage_id,
            metadata=metadata or {},
        ))

    @property
    def passage_count(self) -> int:
        return len(self.passages)

    def search(self, query: str, top_k: int = 10) -> List[Tuple[Passage, float]]:
        """BM25 检索,返回 (Passage, BM25_score) 列表,按分数降序"""
        if not query.strip() or not self.passages or self.bm25 is None:
            return []

        tokenized_query = self._tokenize(query)
        scores = self.bm25.get_scores(tokenized_query)

        # 按分数降序排列
        scored = [(self.passages[i], float(scores[i]))
                  for i in range(len(self.passages))]
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[:top_k]

    # ── 索引构建 ──────────────────────────────────────────────────────

    def build_index(self) -> None:
        """从当前 passages 列表构建 BM25 索引"""
        if not self.passages:
            self.bm25 = None
            self._tokenized_corpus = []
            return

        self._tokenized_corpus = [self._tokenize(p.text) for p in self.passages]
        self.bm25 = BM25Okapi(self._tokenized_corpus)

    # ── 分词 ──────────────────────────────────────────────────────────

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """中英混合文本分词.

        策略:
        - 英文单词: 完整保留并小写 (mAh, LiNi0.8Co0.1Mn0.1O2)
        - 数字+单位: 保留为一个 token (205mAhg, 3.7V)
        - 中文汉字: 每个汉字作为独立 token
        - 其他符号: 过滤
        """
        tokens: List[str] = []
        for match in re.finditer(
            r"[a-zA-Z]+(?:[0-9.][a-zA-Z]*)*"       # 英文单词(含化学式)
            r"|\d+(?:[.]\d+)?(?:[a-zA-Z/%°℃\u00b2\u00b3\u207a\u207b]+)?"  # 数字+单位
            r"|[\u4e00-\u9fff]",                     # 中文汉字
            text,
        ):
            t = match.group()
            if t and not t.isspace():
                tokens.append(t.lower() if t.isascii() else t)
        return tokens

    # ── 文件读取 (保留兼容,但 hybrid 模式不用) ────────────────────────

    @staticmethod
    def read_file_content(file_path: Path) -> str:
        """读取 .md 或 .txt 文件内容"""
        suffix = file_path.suffix.lower()
        if suffix in {".md", ".txt"}:
            return file_path.read_text(encoding="utf-8", errors="ignore")
        raise RuntimeError(f"暂不支持的文件类型:{suffix}.当前支持 .md / .txt")

    @staticmethod
    def _split_passages(text: str, min_len: int = 20, max_len: int = 300) -> List[str]:
        """将文本分割为适合检索的段落"""
        text = text.replace("\u3000", " ")
        text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
        raw_parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        parts: List[str] = []
        for item in raw_parts:
            item = re.sub(r"\s+", " ", item).strip()
            if len(item) <= max_len:
                if len(item) >= min_len:
                    parts.append(item)
            else:
                sub_parts = re.split(r"(?<=[.！？;.;])", item)
                buf = ""
                for sub in sub_parts:
                    if len(buf) + len(sub) <= max_len:
                        buf += sub
                    else:
                        if len(buf.strip()) >= min_len:
                            parts.append(buf.strip())
                        buf = sub
                if len(buf.strip()) >= min_len:
                    parts.append(buf.strip())
        return parts

    @staticmethod
    def _extract_metadata(file_path: Path, text: str) -> dict:
        """从文件路径和段落文本中提取元数据"""
        meta = {"source_file": file_path.name, "source_stem": file_path.stem}
        parent = file_path.parent.name.lower()
        if parent in ("cathode", "anode", "electrolyte"):
            meta["component"] = parent
        stem = file_path.stem
        if re.match(r"^10\.\d{4,}/", stem.replace("_", "/")):
            meta["doi"] = stem.replace("_", "/")
        return meta

    def refresh(self, kb_dir: Optional[Path] = None) -> None:
        """从目录读取 markdown 文件构建索引 (hybrid 模式不用,保留兼容)"""
        if kb_dir:
            self.kb_dir = kb_dir
        if not self.kb_dir:
            return
        self.passages.clear()
        files = sorted([f for f in self.kb_dir.iterdir() if f.is_file()])
        for file_path in files:
            try:
                raw = self.read_file_content(file_path)
                chunks = self._split_passages(raw)
                base_meta = self._extract_metadata(file_path, raw)
                for idx, chunk in enumerate(chunks, start=1):
                    meta = dict(base_meta)
                    meta["chunk_index"] = idx
                    self.passages.append(Passage(
                        source=file_path.name,
                        text=chunk,
                        passage_id=f"{file_path.stem}-P{idx}",
                        metadata=meta,
                    ))
            except Exception as e:
                print(f"[BM25-KB] 跳过文件 {file_path.name}: {e}")
        self.build_index()
