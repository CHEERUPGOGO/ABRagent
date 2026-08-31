# LMLLM RAG 模块 — 高比能锂电池材料筛选多智能体 RAG 系统

## 概述

基于 NLP 课程 Demo 的多智能体架构，为**高比能锂电池材料筛选**场景提供 **Planner → Retrieval → Writer → Reviewer** 四阶段检索增强问答系统。

**核心特点:**
- **材料筛选导向**: Prompt、检索策略、输出格式全部围绕高比能锂电池候选材料的性能对比与筛选
- **多智能体协同**: 问题拆解（Planner）→ 多维度检索（Retrieval）→ 证据答案生成（Writer）→ 幻觉审核（Reviewer）
- **多模型隔离**: 每个 Agent 可独立绑定不同 LLM（Planner 用 flash、Writer/Reviewer 用 pro）
- **双检索后端**: Chroma 向量检索（主流） + TF-IDF 文本检索（fallback）
- **Prompt 审计**: 所有系统 Prompt 集中在 `prompts.py`，可通过界面查阅
- **规则回退**: LLM 不可用时自动降级为关键词匹配和证据拼接
- **基线对比**: 内置 Baseline A（直接问答）和 Baseline B（仅检索拼接）
- **两种交互模式**: 导出所有核心类
├── README.md            # 本文件
├── config.py            # 统一配置（LLM、标签、路径、多模型环境变量）
├── prompts.py           # Prompt 注册表（材料筛选场景，支持审计）
├── llm_client.py        # LLM 客户端（DeepSeek API + Ollama，自动选择后端）
├── tfidf_kb.py          # TF-IDF 轻量检索器（段落分割 + 向量化）
├── agents.py            # 四大 Agent（Planner / Retrieval / Writer / Reviewer）
├── rag_pipeline.py      # 主 Pipeline 编排器（单轮 + 多轮 + 导出）
├── structured_output.py # 结构化输出（材料筛选 Markdown + 定量数据提取）
├── baselines.py         # Baseline A/B 对比（单模型 / 仅检索）
│
├── single_turn/         # 含对话导出）
│   └── baseline_runner.py  # Baseline 批量运行器（三种方案对比）
│
└── output/              # 输出目录（Markdown 导出文件）
```

---

## 快速使用

### 安装依赖

```bash
pip install scikit-learn langchain-openai langchain-core langchain-chroma chromadb gradio
# Ollama 需要单独安装并拉取模型:
# ollama pull qwen3:8b
# ollama pull qwen3-embedding:8b
```

### 环境变量

```bash
export DEEPSEEK_API_KEY="your-key"          # DeepSeek API（至少设置一项）
# 或启动 Ollama 服务（默认 http://127.0.0.1:11434）

# 每个 Agent 可独立指定模型（留空则使用统一模型）:
export PLANNER_MODEL="deepseek-v4-flash"    # 规划用轻量模型
export WRITER_MODEL="deepseek-v4-pro"       # 生成用强模型
export REVIEWER_MODEL="deepseek-v4-pro"     # 审核用强模型
```

### 命令行问答

```python
from src.lmllm.RAG import RAGPipeline

pipeline = RAGPipeline()
result = pipeline.run("NCM811和LRMO哪个能量密度更高？")
print(result["final_answer"])   # 材料筛选对比答案
print(result["evidence"])       # 检索到的证据段落

# 导出结构化 Markdown 报告（含证据引用、定量数据、过程日志）
md, path = pipeline.run_with_export("固态电解质的离子电导率对比")
# 文件保存到 src/lmllm/RAG/output/
```

### 多轮对话

```python
result = pipeline.chat(
    "那它的循环稳定性怎么样？",     # 追问（"它"关联上文材料名）
    chat_history=[
        ("NCM811和LRMO哪个能量密度更高？", "NCM811 在 0.1C 下约 200 mAh/g..."),
    ]
)
```

### 启动 Gradio 界面

```bash
# 单轮版（端口 7860，3

# 代码对比
from src.lmllm.RAG import RAGPipeline, BaselineA, BaselineB, run_comparison
pipeline = RAGPipeline()
baseline_a = BaselineA(pipeline.llm)
baseline_b = BaselineB(pipeline.kb)
comp = run_comparison("高电压正极材料的容量衰减", baseline_a, baseline_b, pipeline)
print(comp["comparison_markdown"])
```

---

## 两种交互模式对比

| 维度 | single_turn/ | multi_turn/ |
|------|-------------|------------|
| **每次独立处理 | 多轮，上下文记忆（最近 3 轮） |
| **Gradio 端口** | 7860 | 7861 |
| **输出面板** | 回答 / 证据 / 日志 / 状态 / Prompt | 对话气泡 + 证据 / 日志 / 状态 / Prompt + 导出 |
| **证据留存** | 仅实时展示 | 实时展示 + 一键导出 Markdown |
| **Baseline** | CLI 单问题 | CLI 批量运行 |
| **适用场景** | 独立问答、实验对比 | 追问链、方案讨论、汇报导出 |

---

## 证据留存与上下文管理

### 证据留存方式

| 方式 | 触发 | 输出位置 |
|------|------|---------|
| `run_with_export()` | 代码调用 | `output/rag_materials_screening_*.md` |
| 多轮版「导出对话」按钮 | Gradio 手动点击 | `output/multi_turn_dialogue_*.md` |
| `baseline_cli.py --save` | CLI 参数 | `output/baseline_A_*.md` 或 `baseline_B_*.md` |
| `baseline_runner.py` | 批量运行 | `output/baseline_{A/B}_Q{1-5}_*.md` + `ABOurs_comparison_*.md` |

导出内容包含：问题、答案、定量数据摘要、置信度、审核意见、证据段落 + 引用来源、过程日志。

### 上下文历史（仅多轮版）

```
用户追问
  → Gradio chat_history 状态（保留所有轮次）
    → pipeline.chat(question, chat_history=tuples)
      → 提取最近 3 轮 → 构建 history_context 文本
        → 传入 Planner（拆解问题时理解上文指代）
        → 传入 Writer（生成答案时参考上文材料名）
        → 传入 Reviewer（审核上下文连续性）
```

- 保留轮次：**最近 3 轮**（兼顾上下文和 token）
- 每轮截断：**200 字符**
- 示例：先问"NCM811 和 LRMO 哪个能量密度更高？"，再追问"那它的循环稳定性呢？"——系统自动理解"它"指上文材料

---

## 智能体详解

### Planner Agent
- 材料筛选导向的问题拆解（按材料体系/组件拆分子检索问题）
- 检测涉及的标签和组件（cathode/anode/electrolyte）
- 规划回答结构（结论摘要 → 性能对比 → 条件标注 → 筛选建议 → 数据缺口）

### Retrieval Agent
- 三阶段检索：组件过滤 → 标签缺失补检索 → 无限制兜底搜索
- Chroma 向量 + TF-IDF 混合融合（默认权重 0.6:0.4）
- 加权排序：标签匹配加分 + 数值密度加分 + 组件匹配加分 + 文档类型加分
- 同文献压制：同一文献超过 2 段降权 20%

### Writer Agent
- 只依据给定证据生成材料筛选对比答案
- 要求标注测试条件（倍率、温度、电解液）
- 关键结论后标注 `[passage_id]` 引用

### Reviewer Agent
- 审核数值准确性 + 条件完整性 + 比较公平性
- 幻觉删除 + 保守答案降级
- 输出置信度（high / medium / low）

---

## 配置项

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `DEEPSEEK_API_KEY` | `""` | DeepSeek API Key |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama 地址 |
| `OLLAMA_MODEL` | `qwen3:8b` | 统一 Ollama 模型 |
| `PLANNER_MODEL` | `""` | Planner 专用模型 |
| `WRITER_MODEL` | `""` | Writer 专用模型 |
| `REVIEWER_MODEL` | `""` | Reviewer 专用模型 |
| `CHROMA_DIR` | `miner/chroma/paragraphs_q` | Chroma 向量库路径 |
| `EMBEDDING_MODEL` | `qwen3-embedding:8b` | 嵌入模型 |
| `DEFAULT_TOP_K` | `10` | 返回证据数 |
| `SEARCH_K` | `20` | 检索上限 |

---

## 预设问题集（多轮批量对比用）

`multi_turn/baseline_runner.py` 内置 5 个高比能锂电池材料筛选问题：

1. NCM811 和 LRMO 哪个能量密度更高？对比它们的首次放电容量和工作电压。
2. 下一代高比能锂电池用什么负极材料？对比硅基负极和锂金属负极的优劣势。
3. 固态电解质的离子电导率和电化学窗口对比，哪种更适用于高比能锂电池？
4. 高电压正极材料（NCM811/LRMO/LNMO）在 4.5V 以上的容量衰减原因是什么？
5. 从文献证据出发，推荐一套高比能锂电池正极/负极/电解液的材料组合方案。

---

#
| RAG 模块 | NLP 来源 | 借鉴内容 |
|---------|---------|---------|
| `single_turn/app.py` | `` | 单轮多智能体 Gradio 界面 |
| `single_turn/baseline_cli.py` | `baseline_*.py` | Baseline CLI 工具 |
| `multi_turn/app.py` | `ours_multiturn.py` | 多轮对话 Gradio + 导出 |
| `multi_turn/baseline_runner.py` | `baseline_A/B.py` | 批量 Baseline 运行器 |
| `prompts.py` | `` PROMPT_REGISTRY | Prompt 注册表 + 审计 |
| `rag_pipeline.py` (多模型) | `` MultiAgentQASystem | 每 Agent 独立 LLM |
| `agents.py` (保守答案) | `` _build_conservative_answer | 保守答案降级 |
| `agents.py` (检索策略) | `chat_rag_v3_optimized.py` | 标签加权 + 兜底搜索 |
| `config.py` (标签体系) | `miner/paragraph_metadata_pipeline_v5_qwen.py` | 6 类标签 + 高比能关键词扩充 |

---

## 许可

与 LMLLM 项目一致。
