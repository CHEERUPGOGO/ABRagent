# 高比能液态锂电池多智能体 RAG 系统（`src/lmllm/RAG/`）

面向高比能液态锂电池**材料筛选**与**体系设计**的多智能体 RAG 模块，集成了混合检索（Chroma + BM25 + RRF + 可选 Qwen3 重排序）、Materials Project MCP 物性增强、基于 `RelationEngine` 的领域约束评估（v2.1 软质询矩阵），以及下游与根目录 `pinn/`（SPM PINN）和 `auto_battery_research/` 对接的方案结构化输出能力。

---

## 1. 核心架构与数据流

```
用户问题 / 设计目标
   │
   ▼
[1. PlannerAgent] ──(LLM 拆解；离线可用 rule_decompose_question 保底)
   │
   ▼
[2. RetrievalAgent / MultiRetrieval]
   ├── Chroma 向量检索 (qwen3-embedding:8b) + BM25KnowledgeBase (BM25Okapi)
   ├── RRF 倒数排名融合 (_RRF_K=60, max_passages=10000) + 可选 Qwen3Reranker
   ├── RelationEngine 插桩 A (query_modifiers：条件降权词 & 缓解白名单加权词)
   └── auto_battery_research.tools.materials_project.augment_mp_evidence (MP MCP)
   │
   ▼
[3. WriterAgent]
   ├── 依据检索证据 + RelationEngine 约束评估生成答案（设计题支持五段式输出）
   ├── 支持可选 structured_geometry_enabled 几何参数冻结契约
   └── 离线 fail-closed：LLM 不可用时返回 draft_answer="", fallback=True（无伪造配方）
   │
   ▼
[4. ReviewerAgent] ──(草稿为空时管线直接 fail-closed 跳过审核)
   ├── RelationEngine 插桩 B (check_scheme 规则与能量校验 + confidence 否决权)
   ├── 最多 max_review_rounds=3 轮检索重试（透传 lit_weight / ebook_weight / _writer_scheme）
   └── 离线 fail-closed：返回 confidence="low", error_type="reviewer", fallback=True
```

### 离线降级语义（Fail-Closed）
- `PlannerAgent`：唯一保留 `rule_decompose_question` 纯规则问题拆解的路径。
- `WriterAgent`：当后端 LLM 不可用、返回空内容或耗尽重试时 **fail-closed**，返回空草稿与显式错误状态，不再输出硬编码替代方案。
- `ReviewerAgent`：当草稿为空或审核 LLM 不可用时 **fail-closed**，强制降级为 `confidence="low"`、`fallback=True`，禁止伪装高置信度放行。

---

## 2. 检索、多轮对话与能量/规则引擎

### 2.1 检索后端与重排序
- **检索模式**（`config.RETRIEVAL_MODE`，默认 `"hybrid"`）：
  - `"hybrid"`：Chroma 向量检索 + `BM25KnowledgeBase` 稀疏检索，经 RRF 融合；`both` / `textbook` 场景通过 `MultiRetrieval` 联合文献与教材双库，并支持按 `lit_weight` / `ebook_weight` 加权。
  - `"chroma"`：仅使用 Chroma 向量检索。
  - `"tfidf"`：兼容保留参数名，底层实际路由至 `BM25KnowledgeBase`（`BM25Okapi`）。
- **重排序**：可选启用 `Qwen3Reranker`（`reranker.py`，基于 `Qwen3-Reranker-4B`）。

### 2.2 多轮会话裁剪
- `RAGPipeline.chat()` 使用 `tiktoken`（`cl100k_base`）统计并裁剪历史上下文：默认 `MAX_HISTORY_TOKENS = 4000`、`OVERLAP_TOKENS = 500`。
- `multi_turn/` 子模块提供基于 `SQLite` 的 `HistoryStore` 持久化与批量对比运行器（`baseline_runner.py`）。

### 2.3 领域约束引擎（`RelationEngine`，规则集 v2.1）
- 数据源：
  - `data/constraints.json`（`2.1-coincell-academic`，`2026-10-29`，对外保留 `RULES_VERSION = "C1-C8/v2.1-coincell"`）：共 **7 条生效规则**（`C1–C3, C5–C8`，原 `C4` 已永久移除），外加内置 `C0_UNVERIFIED_MATERIAL` 未登记材料 fail-closed 拦截。
  - `data/candidates.json`（`0.4-llm-registered`，`2026-08-28`）：9 正极、6 负极、49 电解液（7 手工 + 42 LLM 登记）、23 添加剂（6 手工 + 17 LLM 登记）。
  - `data/alias_map.json`（`0.3-llm-registered`，`2026-08-28`）：86 个规范实体键。
- 分级规则动作（`evaluate()` / `check_scheme()`）：

| 规则编号 | 动作类型 (`action`) | 核心语义 |
|---|---|---|
| `C1` / `C2` / `C3` | `challenge` | 裸碳酸酯体系与高压正极/锂金属配对未含 `mitigation_whitelist` 缓解措施时判违规（`feasible=False`）；存在白名单缓解措施时通过质询（`challenges` 记为 `passed`，`feasible=True`） |
| `C5` | `recommend` | 硅基负极（`si_base`）推荐使用 `FEC` 添加剂；缺失时记入 `inclusions` 建议 |
| `C6` | `audit_note` | `LNMO` / `LRMO` 体系记录锰溶出风险审计备注（`audit_notes`，不阻断 `feasible`） |
| `C7` | `coin_cell_eval` | 目标能量 `>= 400 Wh/kg` 时排除 `graphite` 负极（记入 `violations` 并置 `feasible=False`）；非石墨体系记录材料级扣电比能量评估（`coin_cell_evals`） |
| `C8` | `flag_frontier` | 目标能量 `>= 500 Wh/kg` 时记录 `frontier_flags` 前沿探索标记，保留 `feasible=True` |

### 2.4 能量估算模型（`energy_model.py`）
- 提供材料级与电芯级估算（`estimate_scheme_energy`）及活性物质比例校准（`calibrate_active_ratios`）。
- 默认活性物质质量占比 `DEFAULT_ACTIVE_RATIOS`：`graphite: 0.50`、`si_base: 0.45`、`li_metal: 0.50`、`default: 0.48`；合理性校验容差 `ENERGY_TOLERANCE = 0.35`。
- `data/calibrated/energy_ranges.json` 汇总 BetterBat v6 的 141 颗电芯 / 4 类体系实测区间（Nickel rich 实测 `50.0–350.0 Wh/kg`）。

---

## 3. 配置与快速上手

### 3.1 LLM 与检索配置（`config.py`）
`config.py` 会读取环境变量及项目配置 YAML（`setting.yaml` / `miner/config.yaml`）：
- **LLM 后端优先级**：`OpenAI / MiniMax API`（`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `MINIMAX_API_KEY`）→ `DeepSeek API`（`DEEPSEEK_API_KEY`）→ `Ollama` 本地服务 → `rule` 模式。
- **主要检索默认参数**：`RETRIEVAL_MODE = "hybrid"`、`DEFAULT_TOP_K = 50`、`SEARCH_K = 20`、`RETRIEVAL_TOP_K_PER_QUERY = 40`、`PRIMARY_LABELS`（6 类一级段落标签）。

### 3.2 Python 调用与 CLI / Web 入口

```python
from src.lmllm.RAG import RAGPipeline

pipeline = RAGPipeline()
result = pipeline.run("目标 450 Wh/kg 的高镍三元正极配什么负极和电解液？")
print(result["final_answer"])
```

常用命令行入口：
```bash
# 1. RelationEngine 约束与 check_scheme 自测
python -m src.lmllm.RAG.relation_engine

# 2. 启动单轮 / 多轮 Gradio Web 界面
python -m src.lmllm.RAG.single_turn.app   # 默认端口 7860
python -m src.lmllm.RAG.multi_turn.app    # 默认端口 7861

# 3. 规则自洽性评测与端到端管线评测
python -m src.lmllm.RAG.evaluate_design_tasks --mode rule               # 秒级（会更新 data/tasks/eval_report.json）
python -m src.lmllm.RAG.evaluate_design_tasks --mode pipeline --limit 3 # 慢（eval_report.json 实测约 10–17 分钟/题）

# 4. 生成设计任务集 / 抽取单句关系
python -m src.lmllm.RAG.task_generator
python -m src.lmllm.RAG.extractor "句子文本" --type performance
```

---

## 4. 目录结构

```text
src/lmllm/RAG/
├── __init__.py                 # 模块导出与说明
├── config.py                   # 环境变量/YAML 配置、模型与检索常量
├── llm_client.py               # 多后端 LLM 客户端、JSON 解析与离线降级工具
├── prompts.py                  # Planner / Writer / Reviewer / BaselineA 系统 Prompt 注册表
├── agents.py                   # PlannerAgent / RetrievalAgent / WriterAgent / ReviewerAgent
├── rag_pipeline.py             # 多智能体主调度、方案/能量/几何解析、chat() 上下文管理
├── bm25_kb.py                  # BM25KnowledgeBase (BM25Okapi) 稀疏检索与词元归一化
├── multi_retrieval.py          # 文献+教材双库 Chroma+BM25+RRF 混合检索引擎
├── reranker.py                 # Qwen3Reranker 重排序封装
├── relation_engine.py          # RelationEngine 约束绑定与校验引擎 (v2.1)
├── energy_model.py             # 材料级/电芯级比能量估算与校准模型
├── structured_output.py        # 证据表与定量汇总 Markdown 格式化
├── extractor.py                # RelationExtractor (NERRE ICL 关系抽取)
├── task_generator.py           # 四类设计评测任务生成器
├── evaluate_design_tasks.py    # 规则自洽性 (rule) 与端到端管线 (pipeline) 评测器
├── baselines.py                # BaselineA (直答) 与 BaselineB (BM25 拼接)
├── PROJECT_STATUS.md           # 模块阶段实施状态与基线记录
├── README.md                   # 本文档
├── schemas/
│   └── battery_relations.py    # doping / compatibility / performance 关系 Schema
├── single_turn/
│   ├── __init__.py
│   ├── app.py                  # 单轮 Gradio UI (端口 7860)
│   └── baseline_cli.py         # 单轮对比 CLI 工具
├── multi_turn/
│   ├── __init__.py
│   ├── app.py                  # 多轮 Gradio UI (端口 7861)
│   ├── baseline_runner.py      # 批量基线对比运行器
│   └── history_store.py        # SQLite 会话历史持久化
├── data/
│   ├── alias_map.json          # 86 个实体规范键与别名表 (v0.3)
│   ├── candidates.json         # 候选材料与电化学参数库 (v0.4)
│   ├── constraints.json        # 7 条层级约束规则 C1–C3, C5–C8 (v2.1)
│   ├── calibrated/             # energy_ranges.json (BetterBat v6), mp_crosscheck.json
│   ├── seed/                   # relations_seed.json (36 条), candidate_sentences.json, review_report.md
│   └── tasks/                  # design_tasks.json (26 题快照), eval_report.json
└── scripts/                    # 数据抓取、种子审核、批量抽取与历史验证/修复脚本
```
