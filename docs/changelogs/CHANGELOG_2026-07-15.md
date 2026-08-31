# CHANGELOG — 2026-07-15

> 本次修改：BM25 检索器替换、RRF 多路融合、双库混合检索、Reviewer 修正循环、电子书预处理支持。
> 排除：`项目进展_2026-07-13.pptx`

---

## 1. BM25 检索器替换 TF-IDF

**涉及文件**:
- `src/lmllm/RAG/bm25_kb.py`（新增, 182 行）
- `src/lmllm/RAG/tfidf_kb.py`（删除, 285 行）
- `src/lmllm/RAG/__init__.py`
- `src/lmllm/RAG/rag_pipeline.py`
- `src/lmllm/RAG/multi_turn/baseline_runner.py`
- `src/lmllm/RAG/single_turn/baseline_cli.py`
- `src/lmllm/RAG/config.py`

**摘要**:
- 用基于 `rank_bm25` 的 `BM25KnowledgeBase` 替换旧的 `TFIDFKnowledgeBase`
- BM25 对中英文学术文献的召回准确率优于 TF-IDF
- 接口完全兼容，`Passage` 数据类保持不变
- `config.py` 新增 `BM25_INDEX_DIR` 配置

---

## 2. 检索融合策略：权重融合 + RRF 双模式

**涉及文件**: `src/lmllm/RAG/agents.py`

**新增功能**:
- `RetrievalAgent` 支持 `fusion_mode` 参数（`"weighted"`／`"rrf"`）
- **weighted**（加权融合）：Chroma 向量分 + BM25 分按 `chroma_weight` 加权
- **RRF**（Reciprocal Rank Fusion）：用排名代替分数，RRF_K=60，对分数尺度不敏感的数据适应性更好
- Chroma 检索 top_k 翻倍（`top_k*2+30`），BM25 同步扩大召回池
- BM25 原始分数做 min-max 归一化后再融合，消除分数分布差异

---

## 3. Reviewer 修正循环（多轮反馈）

**涉及文件**: `src/lmllm/RAG/rag_pipeline.py`

**新增功能**:
- Reviewer 发现问题后自动触发最多 2 轮修正循环
- 按错误类型分路：
  - **证据缺失**：提取缺失关键词 → 补充检索 → 重新 Writer
  - **文字/学术错误**：回退 Writer 调用 `revise()` 修正
- `_issues_to_queries()` 从 Reviewer issues 中提取缺失关键词，转为补充检索查询

---

## 4. 多轮上下文 Token 预算控制

**涉及文件**: `src/lmllm/RAG/rag_pipeline.py`（`chat()` 方法）

- 旧代码：固定取最近 3 轮对话
- 新代码：按中英文 token 估算（中文 ×2.0，英文 ×0.25，助手 ×0.3），`MAX_HISTORY_TOKENS=4000` 预算内自适应取最大轮数

---

## 5. 双库混合检索（文献 + 电子书）

**新增文件**:
- `src/lmllm/RAG/multi_retrieval.py`（155 行）— 双库加权混合检索
- `src/lmllm/RAG/query_router.py`（43 行）— LLM 意图路由
- `src/lmllm/RAG/multi_turn/app_hybrid.py`（564 行）— 双库混合检索 Gradio 界面

**功能**:
- `MultiRetrieval` 同时检索文献 Chroma + 电子书 Chroma，加权融合
- `QueryRouter` 判断问题意图（experimental / theory / mixed），动态调整权重

---

## 6. 电子书预处理管道（新增）

**新增文件**:
- `preprocessing/ebook_to_markdown.py` — PDF → Markdown（MinerU API）
- `preprocessing/ebook_merge.py` — Markdown 分片合并
- `miner/ebook_ingest.py` — 电子书 Chroma 增量入库

---

## 7. 融合模式对比前端（新增）

**新增文件**: `compare_fusion_frontend.py`

- Gradio 双栏对比 weighted 与 RRF 两种融合模式

---

## 8. 其他微调

- `src/lmllm/RAG/reranker.py`: `pad` 参数修复长序列溢出
- `src/lmllm/RAG/multi_turn/app.py`: `retrieval_mode` 从硬编码改为读配置
- `src/lmllm/RAG/prompts.py`: Reviewer Prompt 增加子任务拆解指引
- `src/lmllm/RAG/config.py`: 新增 `REVIEWER_MAX_TOKENS`、`FUSION_MODE`、电子书配置
