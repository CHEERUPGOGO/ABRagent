# CHANGELOG — 2026-07-09

> 本次修改涵盖：RAG 多轮对话系统修复、非正文过滤日志、以及近期其他模块的增量改动。
> NLP/ 目录下文件不在此记录范围内。

---

## 1. RAG 多轮对话系统 — Reviewer JSON 解析修复 + 模型路由修正

**日期**: 2026-07-08  
**涉及文件**:
- `src/lmllm/RAG/config.py`
- `src/lmllm/RAG/llm_client.py`
- `src/lmllm/RAG/agents.py`

### 1.1 `src/lmllm/RAG/llm_client.py` — `safe_json_loads` 增强

**问题**: Reviewer Agent 调用 DeepSeek API 返回的 JSON 被截断（max_tokens=4096 不够）或含控制字符，导致三层解析全部失败，触发回退模式。

**修复**:
- 在原有三层解析（直接 `json.loads` → ` ```json ` 代码块提取 → `JSONDecoder.raw_decode`）之后增加**第四层启发式修复**：
  - **4a)** 移除不可见控制字符（保留 `\t` `\n` `\r`）后重试解析
  - **4b)** 提取最外层 `{...}`，检测括号不平衡（截断标志），自动补齐缺失的 `}` 和 `]`

### 1.2 `src/lmllm/RAG/llm_client.py` + `src/lmllm/RAG/config.py` — 修复 DeepSeek 模型路由

**问题**: `LLMClient.__init__` 的 `model_name` 参数只赋值给了 `self.ollama_model`，走 DeepSeek API 时所有 Agent 始终使用硬编码的 `DEEPSEEK_CLASSIFICATION_MODEL`（`deepseek-v4-flash`），导致 UI 中为 Writer/Reviewer 配置的 `deepseek-v4-pro` 实际不生效。

**修复**:
- `config.py`: `create_deepseek_llm()` 新增 `model_name` 参数，显式传入时直接覆盖 `model_map`
- `llm_client.py`: `LLMClient.__init__` 将 `model_name` 传递给 `create_deepseek_llm`，同时存储到 `self._deepseek_model_name`

**效果**:

| Agent | 修复前（DeepSeek API） | 修复后 |
|-------|----------------------|--------|
| Planner | `deepseek-v4-flash` | `deepseek-v4-flash` |
| Writer | `deepseek-v4-flash` ❌ | `deepseek-v4-pro` ✅ |
| Reviewer | `deepseek-v4-flash` ❌ | `deepseek-v4-pro` ✅ |

### 1.3 `src/lmllm/RAG/config.py` — `LLM_MAX_TOKENS` 提升

- `LLM_MAX_TOKENS`: `4096` → `8192`
- 为 Reviewer 的 `revised_answer`（完整中文答案 + JSON 壳）提供充足的输出空间，减少截断概率

### 1.4 `src/lmllm/RAG/agents.py` — Reviewer 解析失败时记录日志

- 新增 `logging.getLogger("rag_agents")`
- `ReviewerAgent.run()` 中 `safe_json_loads` 返回 `None` 时，记录 `raw[:500]` 到错误日志

---

## 2. RAG 多轮对话系统 — 切换会话时恢复检索证据和过程日志

**日期**: 2026-07-08  
**涉及文件**:
- `src/lmllm/RAG/multi_turn/history_store.py`
- `src/lmllm/RAG/multi_turn/app.py`

### 2.1 `src/lmllm/RAG/multi_turn/history_store.py`

**问题**: 切换会话后 `📋 检索证据` 和 `📊 过程日志` 面板显示为占位文字，无法恢复上次提问的结果。

**修复**:
- `messages` 表新增两列（schema 迁移，兼容旧数据库）：
  - `evidence_display TEXT DEFAULT ''` — 格式化后的检索证据 Markdown
  - `process_log TEXT DEFAULT ''` — 格式化后的过程日志 Markdown
- 新增 `_column_exists()` 方法，用 `PRAGMA table_info` 检测列是否存在
- `add_message()` 新增 `evidence_display` 和 `process_log` 参数
- `get_messages()` 返回这两个新字段

### 2.2 `src/lmllm/RAG/multi_turn/app.py`

- `respond_multi_turn()`: 落库助手消息时传入 `evidence_display=evidence_md`、`process_log=log_md`
- 新增 `load_last_display(session_id)` 函数：遍历会话消息，提取最后一条 assistant 消息的 `evidence_display` 和 `process_log`
- `switch_session()`: 调用 `load_last_display()` 恢复两个面板的内容

---

## 3. 非正文段落过滤日志

**日期**: 2026-07-09  
**涉及文件**:
- `miner/paragraph_metadata_pipeline_v5_qwen.py`

**问题**: `_split_long_records()` 中对 `label == "非正文"` 的段落直接 `continue` 跳过，没有任何记录。

**修复**:
- 新增独立的 `non_body_filter` logger，日志写入 `miner/logs/non_body_filter_YYYYMMDD.log`
- 每条被过滤的段落记录: DOI、组件、段落序号、标题前 60 字、段落内容前 200 字
- 整篇文献全部被判为非正文时，额外记录一条 `WARNING` 级别汇总日志

---

## 4. 其他模块增量改动

**日期**: 2026-07-03 ~ 2026-07-08  
**涉及文件**:
- `miner/classification/battery_type_agent.py`
- `miner/classification/run_battery_type.py`
- `miner/meta_extraction/extract_meta.py`
- `miner/reindex_chroma.py`
- `preprocessing/pdf_to_markdown.py`
- `compare_qa_frontend.py`
- `compare_retrieval.py`

### 4.1 `miner/classification/battery_type_agent.py` — 柔性/可穿戴电池分类

- 新增 `FLEXIBLE_BATTERY_KEYWORDS` 关键词列表
- LLM prompt 新增第 5 条判断规则: `is_flexible_battery`
- 输出 JSON 新增 `is_flexible_battery` 字段
- 关键词回退方法新增 `is_flexible_battery_by_keyword()`

### 4.2 `miner/classification/run_battery_type.py` — 柔性电池跳过 + 原子写入

- `is_flexible_battery=True` 的文献自动跳过
- 增量处理记录写入改为原子替换（`.tmp` → `os.replace`）

### 4.3 `miner/meta_extraction/extract_meta.py` — DOI 提取修复

- `extract_doi()` 新增预处理：修复被换行/空格断开的 DOI

### 4.4 `miner/reindex_chroma.py` — 分批写入 + 默认参数调整

- `ingest_chroma()`: 大向量入 Chroma 时分批写入（BATCH=200），防止 Ollama 超时
- 默认参数：`max-chunk` 7500→2000，`overlap` 750→200

### 4.5 `preprocessing/pdf_to_markdown.py` — 每日限额

- 新增 `--daily-limit` 参数，限制今日最大处理文件数

### 4.6 `compare_qa_frontend.py` — 三粒度 Chroma 库问答对比前端（新增）

- Gradio 三栏对比界面，每个 Chroma DB 独立对话，可切换对比三种 chunk 策略（paragraphs_q / paragraphs_q_1 / paragraphs_q_2）的召回回答差异
- 用法: `python compare_qa_frontend.py`（默认端口 7872）

### 4.7 `compare_retrieval.py` — 三粒度 Chroma 库检索召回对比 CLI（新增）

- 命令行工具，对比三粒度 Chroma 库的检索召回效果（不调 LLM，纯检索对比）
- 支持交互模式: `python compare_retrieval.py --interactive`
- 用法: `python compare_retrieval.py "高电压正极材料"`

---

## 新增文件清单

### `src/lmllm/RAG/` 模块（全部为本次新增）：

```
src/lmllm/RAG/
├── __init__.py
├── agents.py                  # Planner / Retrieval / Writer / Reviewer 四智能体
├── baselines.py               # Baseline A / B 对比实验
├── config.py                  # 统一配置（LLM / 检索 / 路径 / 标签体系）
├── llm_client.py              # 统一 LLM 客户端（DeepSeek API + Ollama 双后端）
├── prompts.py                 # 各 Agent 的 System Prompt
├── rag_pipeline.py            # 主 Pipeline 编排器
├── structured_output.py       # 结构化 Markdown 输出 + LaTeX 归一化
├── tfidf_kb.py                # TF-IDF 知识库
├── multi_turn/
│   ├── __init__.py
│   ├── app.py                 # 多轮对话 Gradio 界面
│   ├── baseline_runner.py     # 多轮 Baseline 对比运行器
│   └── history_store.py       # SQLite 对话历史持久化
└── single_turn/
    ├── __init__.py
    ├── app.py                 # 单轮 Gradio 界面
    └── baseline_cli.py        # Baseline CLI 入口
```

### 项目根目录新增工具：

```
compare_qa_frontend.py         # 三粒度 Chroma 库问答对比前端（Gradio）
compare_retrieval.py           # 三粒度 Chroma 库检索召回对比 CLI
```
