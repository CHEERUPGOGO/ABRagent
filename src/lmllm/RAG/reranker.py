"""Qwen3-Reranker-4B CrossEncoder 重排序模块

使用 transformers 加载本地模型,在检索之后对结果进行重排序,
提升召回段落的相关性精度。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    HAS_TORCH = True
except ImportError:
    class _DummyTorch:
        @staticmethod
        def no_grad():
            def decorator(func):
                return func
            return decorator
    torch = _DummyTorch()
    AutoModelForCausalLM = None
    AutoTokenizer = None
    HAS_TORCH = False

logger = logging.getLogger("rag_reranker")




class Qwen3Reranker:
    """Qwen3-Reranker-4B 重排序器

    基于 transformers 加载本地 Qwen3-Reranker-4B 模型,
    对 (query, passage) 对计算相关性分数并重排结果。
    """

    def __init__(
        self,
        model_path: str = "/home/ls/xiaoyue/models/Qwen3-Reranker-4B",
        torch_dtype: str = "auto",
        max_length: int = 8192,
        batch_size: int = 8,
    ):
        if not HAS_TORCH:
            raise ImportError("PyTorch and transformers are required to initialize Qwen3Reranker.")
        self.model_path = model_path
        self.max_length = max_length
        self.batch_size = batch_size


        logger.info(f"加载 Qwen3-Reranker-4B 从 {model_path}")

        dtype_map = {"bf16": torch.bfloat16, "fp16": torch.float16}
        _dtype = dtype_map.get(torch_dtype, "auto")

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_path,
            padding_side="left",
            trust_remote_code=True,
        )
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        self.model = AutoModelForCausalLM.from_pretrained(
            model_path,
            torch_dtype=_dtype,
            device_map="auto",
            trust_remote_code=True,
        )
        self.model.eval()

        self.token_false_id = self.tokenizer.convert_tokens_to_ids("no")
        self.token_true_id = self.tokenizer.convert_tokens_to_ids("yes")

        # 模板常量 (Qwen3-Reranker 官方格式)
        self.prefix = (
            "<|im_start|>system\nJudge whether the Document meets the requirements "
            "based on the Query and the Instruct provided. Note that the answer can "
            'only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
        )
        self.suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        self.prefix_tokens = self.tokenizer.encode(self.prefix, add_special_tokens=False)
        self.suffix_tokens = self.tokenizer.encode(self.suffix, add_special_tokens=False)

        param_count = self.model.num_parameters() / 1e9
        mem_gb = torch.cuda.memory_allocated() / 1024**3 if torch.cuda.is_available() else 0
        logger.info(f"模型加载完成: {param_count:.2f}B, 显存 {mem_gb:.2f} GB")

    # ── 格式化和推理 ──────────────────────────────────────────────────

    def _format_pair(
        self, query: str, doc: str, instruction: Optional[str] = None
    ) -> str:
        """按 Qwen3-Reranker 模板格式化单对 (query, doc)"""
        if instruction is None:
            instruction = (
                "Given a web search query, retrieve relevant passages "
                "that answer the query"
            )
        return (
            f"<Instruct>: {instruction}\n"
            f"<Query>: {query}\n"
            f"<Document>: {doc}"
        )

    @torch.no_grad()
    def _compute_scores(self, texts: List[str]) -> List[float]:
        """批量计算相关性分数

        Returns:
            每个 passage 的 relevance score, 取值范围 (0, 1),
            表示 "yes" token 的 softmax 概率。
        """
        all_scores: List[float] = []

        for i in range(0, len(texts), self.batch_size):
            batch_texts = texts[i : i + self.batch_size]

            # 1. Tokenize (不加 padding, 保留原始长度)
            inputs = self.tokenizer(
                batch_texts,
                padding=False,
                truncation="longest_first",
                return_attention_mask=False,
                max_length=self.max_length - len(self.prefix_tokens) - len(self.suffix_tokens),
            )

            # 2. 拼接 prefix + text + suffix
            for j, ids in enumerate(inputs["input_ids"]):
                inputs["input_ids"][j] = self.prefix_tokens + ids + self.suffix_tokens

            # 3. Pad 到统一长度
            batch = self.tokenizer.pad(
                inputs, padding=True, return_tensors="pt", max_length=self.max_length
            )
            batch = {k: v.to(self.model.device) for k, v in batch.items()}

            # 4. Forward
            outputs = self.model(**batch)
            batch_logits = outputs.logits[:, -1, :]  # (B, vocab_size)

            # 5. 取 yes/no token 的 logit → softmax → yes 概率
            true_vec = batch_logits[:, self.token_true_id]
            false_vec = batch_logits[:, self.token_false_id]
            stacked = torch.stack([false_vec, true_vec], dim=1)  # (B, 2)
            log_probs = torch.nn.functional.log_softmax(stacked, dim=1)
            scores = log_probs[:, 1].exp().tolist()  # yes 概率

            all_scores.extend(scores)

        return all_scores

    # ── 外部接口 ──────────────────────────────────────────────────────

    def rerank(
        self,
        query: str,
        passages: List[Dict[str, Any]],
        top_k: Optional[int] = None,
        instruction: Optional[str] = None,
        alpha: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """对检索结果进行语义重排序

        Args:
            query: 原始用户问题
            passages: 检索结果列表, 每项应包含 ``passage_id`` / ``source`` / ``score``
                      以及 ``clean_text`` 或 ``text`` 字段作为排序文本
            top_k: 重排后保留的条数 (默认保留全部, 重新排序)
            instruction: 自定义指令 (默认使用搜索相关性指令)
            alpha: 原始分数与 reranker 分数的融合权重
                   ``new_score = (1-alpha) * norm(orig) + alpha * reranker_score``
                   其中 norm(orig) 为 min-max 归一化到 [0, 1]

        Returns:
            按 new_score 降序的 passage 列表, 每项新增 ``reranker_score`` 字段
        """
        if not passages:
            return []

        # 提取文本用于 rerank
        texts = []
        for p in passages:
            txt = p.get("clean_text") or p.get("text") or ""
            texts.append(txt)

        # 格式化 (query, doc) 对
        formatted = [self._format_pair(query, t, instruction) for t in texts]

        # 计算 reranker 分数
        reranker_scores = self._compute_scores(formatted)
        _r_min, _r_max = min(reranker_scores), max(reranker_scores)
        logger.info(
            f"Rerank 完成: {len(reranker_scores)} 条, "
            f"分数 [{_r_min:.4f}, {_r_max:.4f}]"
        )

        # 归一化原始分数到 [0, 1]
        orig_scores = [p.get("score", 0.0) for p in passages]
        _min, _max = min(orig_scores), max(orig_scores)
        if _max > _min:
            norm_orig = [(s - _min) / (_max - _min) for s in orig_scores]
        else:
            norm_orig = [0.5] * len(orig_scores)

        # 融合分数: new = (1 - alpha) * norm(orig) + alpha * reranker
        results: List[Dict[str, Any]] = []
        for i, p in enumerate(passages):
            new_score = (1.0 - alpha) * norm_orig[i] + alpha * reranker_scores[i]
            results.append({
                **p,
                "reranker_score": round(reranker_scores[i], 6),
                "score": round(new_score, 6),
            })

        results.sort(key=lambda x: x["score"], reverse=True)

        if top_k is not None:
            results = results[:top_k]

        return results

    def compute_pair(self, query: str, doc: str) -> float:
        """计算单对 (query, doc) 的相关性分数 (调试用)"""
        text = self._format_pair(query, doc)
        return self._compute_scores([text])[0]
