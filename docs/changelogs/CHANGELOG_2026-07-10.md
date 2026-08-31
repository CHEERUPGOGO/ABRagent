# CHANGELOG — 2026-07-10

> 本次修改：Qwen3-Reranker-4B 重排序集成 + 检索策略优化 + Prompt 增强。
> 仅记录 `src/lmllm/RAG/` 下的修改。

---

## 1. Qwen3-Reranker-4B CrossEncoder 重排序（新增模块）

**日期**: 2026-07-09  
**涉及文件**:
- `src/lmllm/RAG/reranker.py`（新增）
- `src/lmllm/RAG/__init__.py`

**新增** `src/lmllm/RAG/reranker.py`:
- 基于 `transformers` 加载本地 `Qwen3-Reranker-4B` 模型
- 按 Qwen3-Reranker 官方模板格式化 `(query, passage)` 对
- 批量计算相关性分数（`yes` token 的 softmax 概率）
- `rerank()` 接口：将 reranker 分数与检索原始分数按 `alpha` 权重融合，返回重排结果
- 支持 `batch_size` 控制、`max_length=8192`
- 加载路径：`/home/ls/xiaoyue/models/Qwen3-Reranker-4B`

**修改** `__init__.py`:
- 导出 `Qwen3Reranker` 类

---

## 2. Reranker 配置 + 检索参数调整

**日期**: 2026-07-09  
**涉及文件**: `src/lmllm/RAG/config.py`

**新增配置项**:

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RERANKER_MODEL_PATH` | `/home/ls/xiaoyue/models/Qwen3-Reranker-4B` | 模型路径 |
| `RERANKER_ENABLED` | `true` | 是否启用（环境变量控制） |
| `RERANKER_TOP_K` | `10` | 重排后保留条数 |
| `RERANKER_BATCH_SIZE` | `8` | 推理批次大小 |
| `RERANKER_ALPHA` | `0.7` | 融合权重（越高越信任 reranker） |

**检索参数调整**:

| 参数 | 旧值 | 新值 |
|------|------|------|
| `DEFAULT_TOP_K` | 10 | 20 |
| `RETRIEVAL_TOP_K_PER_QUERY` | 15 | 30 |

---

## 3. RetrievalAgent 增加 `use_label_filter` 参数

**日期**: 2026-07-09  
**涉及文件**: `src/lmllm/RAG/agents.py`

- `_run_single_query()` 新增 `use_label_filter: bool = True` 参数
- 当 `use_label_filter=False` 时，关闭 Chroma 标签（label）硬过滤，让 reranker 做纯语义筛选
- `run()` 透传该参数到内部调用
- 兜底搜索（extra_search）始终关闭标签过滤（`use_label_filter=False`）

---

## 4. RAG Pipeline 集成 Reranker（Stage 2.5）

**日期**: 2026-07-09  
**涉及文件**: `src/lmllm/RAG/rag_pipeline.py`

- 初始化时根据配置加载 `Qwen3Reranker`（加载失败自动禁用）
- `run()` 中新增 **Stage 2.5**：Retrieval 之后、Writer 之前，调用 `reranker.rerank()` 重排序
- 推荐/推理类问题（`needs_reasoning=True`）自动关闭 Chroma 标签硬过滤，让 reranker 做主筛选
- `status` / `runtime_status()` 显示 Reranker 启用状态

**Pipeline 数据流更新**:

```
[2/4] Retrieval → [2.5/4] Reranker 重排序 → [3/4] Writer → [4/4] Reviewer
```

---

## 5. Planner/Writer Prompt 增强 — 创新推荐黑名单逻辑

**日期**: 2026-07-09  
**涉及文件**: `src/lmllm/RAG/prompts.py`

**Planner Prompt**:
- 创新推荐类问题：必须在 `answer_outline` 中安排"文献中已有方案汇总（黑名单）"章节
- `retrieval_queries` 应拆分为检索现有研究的子问题，确保覆盖已有方案

**Writer Prompt**:
- 新增**第 0 步**：先梳理检索证据中每一篇文献实际研究过的方案，汇总为"已有方案清单（黑名单）"
- 推荐方案**不得出现在黑名单中**（即使参数/比例不同也视为已被验证）
- 每个推荐方案给出推荐指数(1-10)和科学依据
- 新增用户约束条件严格遵循要求
