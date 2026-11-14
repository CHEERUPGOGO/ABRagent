# AutoBatteryResearch Agent (ABRAgent)

**全生命周期化学电池研究自主智能体与科研工程平台**

AutoBatteryResearch Agent (ABRAgent) 是专为高比能化学电池（锂金属电池、固态电池、高镍三元体系等）研发设计的自主 AI 智能体系统。系统采用 **Agent-Centric（智能体主控）设计哲学**：由全局顶层智能体（`ABRAgent`）驱动 LangChain/LangGraph ReAct 认知循环，融合**学术文献感知、语义向量入库、微观材料与电芯组装挖掘、多智能体 RAG 方案设计（Planner/Retrieval/Writer/Reviewer）、热力学分级约束规则引擎（RelationEngine C1–C3, C5–C8）以及 SPM PINN 物理仿真（纯 numpy 推理）**，实现从海量学术论文到结构化电芯配方方案、物理验证及综合科研研报的端到端自主闭环。

---

## 🌟 核心特性

```
                     ┌──────────────────────────────────────────────────────────┐
                     │          全局主控智能体 ABRAgent (LangGraph ReAct)          │
                     │  - 全部 6 个 Stage 均由大模型主控决策 (LLM in brain seat)  │
                     │  - MemorySaver 线程记忆 + 多轮工具编排 (recursion_limit 25) │
                     │  - 门禁驳回自愈反思：失败诊断回注重试提示词 (Self-Correction) │
                     └──────────────────────────────────────────────────────────┘
                                                  │
                          自主感知 Tips、调度工具、发起 Check 自检与 Complete 推进
                                                  ▼
      ┌─────────────────────────────────────────────────────────────────────────────────────────┐
      │                      工作流与规则护栏 (Workflow & Deterministic Checkers)                │
      │                                                                                         │
      │  [Stage 1~3: 资产感知与增量工具]                                                          │
      │  ├── InspectLiteratureAssets / IngestLiteraturePapers ──▶ Agent 判定文献完备度           │
      │  ├── InspectVectorDB / IndexSemanticVectors           ──▶ Agent 判定向量库与语义打标    │
      │  └── InspectCellEntities / ExtractAndAssembleCells    ──▶ Agent 判定电芯三元组与ML资产  │
      │                                                                                         │
      │  [Stage 4~6: RAG 方案设计、PINN 仿真与研报编译]                                           │
      │  ├── RunRAGDesign / QueryMaterialsProject ──▶ 单链路: Planner→Retrieval(+MP)→Writer→Reviewer│
      │  │                                           + RelationEngine 分级规则 (C1–C3, C5–C8) & 契约 v2.0│
      │  ├── RunPhysicsSimulation / ReviseGeometry ──▶ SPM PINN 放电仿真与几何反馈修订          │
      │  └── SynthesizeResearchReport             ──▶ 六节结构化科研研报 + 指纹一致性核验        │
      │                                                                                         │
      │  [阶段门禁与状态流转工具]                                                                  │
      │  ├── CurrentTips: 读取当前阶段任务指标规范 (防止走偏)                                       │
      │  ├── Check: 触发确定性 Checker；若报错输出 failure_summary 供 Agent 自我反思修复           │
      │  └── Complete: 门禁完全通过后，原子推进工作流至下一阶段                                    │
      └─────────────────────────────────────────────────────────────────────────────────────────┘
```

1. **智能体主控架构**：顶层 `ABRAgent` 掌控 ReAct 主循环（Thought → Action → Observation），六个阶段全部由大模型自主决策；工作流（`StageManager`）与确定性校验器（`Checkers`）作为外部护栏——**大模型在大脑主控位，工作流在规则裁判位**。Stage 1–3、5、6 提供确定性执行器离线兜底，而 Stage 4 RAG 在无可用 LLM 时严格执行 fail-closed 拦截（绝不编造虚假配方）。
2. **确定性门禁与自愈反思**：`Check` 自检不通过时输出 `failure_summary`（error_code / error / next_action），回注重试轮提示词驱动针对性修复；同一阶段多次重试共享 LangGraph 线程记忆。
3. **真实数据与分级约束铁律**：「有则提取、无则留空、禁止编造」。设计方案需通过 `RelationEngine` **7 条分级规则**（**C1–C3, C5–C8**，**C4 已永久移除**，`RULES_VERSION="C1-C8/v2.1-coincell"`）：包含未验证材料 `C0_UNVERIFIED_MATERIAL` 前置 fail-closed 拒绝、`C1–C3` 带 `mitigation_whitelist` 缓解白名单审查、`C5` 推荐、 `C6` 审计批注、`C7` $\ge 400$ Wh/kg 石墨负极排除、`C8` $\ge 500$ Wh/kg 前沿目标放行并标记；证据链必须携带真实 DOI 或 Materials Project (`MP:`) 溯源标识。
4. **课题级产物隔离**：每个研究课题的产物（设计方案、电芯组装、综合研报、状态文件）独立存放在 `output/tasks/<课题>/`，多课题并行互不污染。
5. **四维交互入口**：CLI 命令行、Textual/Rich TUI 终端面板、FastAPI Web 监控大屏（Gradio 后备）、领域专家 MCP Server（支持 Streamable HTTP / stdio）。

---

## 🔬 六阶段科研工作流

| Stage | 名称 | 核心产物 | 门禁 Checker |
|:---:|---|---|---|
| 1 | 文献解析与组件分类 | `database/type/` 分类文献库 | `IngestionChecker`（三大组件分类完备性） |
| 2 | 语义标注与向量入库 | `miner/chroma/paragraphs_q` 段落向量库 | `VectorDBChecker` |
| 3 | 材料挖掘与电芯组装 | `<task>/cell_assembly/` 结构化电芯实体 | `CellAssemblyChecker`（材料+电芯+溯源完备） |
| 4 | 多智能体 RAG 方案设计 | `<task>/design_scheme.md/.json`、`rag_result.json`、`research_context.json`、`design_revisions/` | `RAGDesignChecker`（五段式+真实证据+RelationEngine C1–C3/C5–C8+几何契约 v2.0） |
| 5 | PINN 触发判定、参数提取与 SPM 仿真 | `<task>/pinn_input_spec.json`（命中体系时产出 `pinn_simulation_result.json`、`pinn_simulation_curve.png`） | `PINNPhysicsChecker`（**默认启用**，未匹配体系平滑回退参数提取-only；支持 `ReviseGeometry` 反馈修订或 `--skip-pinn` 跳过） |
| 6 | 综合研报生成 | `<task>/final_research_report.md`、`experiment_plan.json` | `FinalReportChecker`（六节结构+契约/指纹校验+阶段日志；兼容历史五段式） |

阶段与 Checker 均在 `auto_battery_research/workflow/abr_workflow.yaml` 中声明式定义，`StageManager` 从中加载；工作流状态按课题持久化于 `output/tasks/<课题>/.stage_state.json`，跨 CLI 调用续跑。

---

## 📦 安装指南

要求 **Python >= 3.10**（Windows / Linux / macOS 均可，CI 固定 **Python 3.12**；若启用 `.[mp]` Materials Project MCP extra 需 **Python 3.11+**）：

```bash
git clone https://github.com/CHEERUPGOGO/AutoBatteryResearch.git
cd AutoBatteryResearch

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux / macOS:
# source .venv/bin/activate

# 基础安装 (核心智能体、CLI 与 FastAPI Web 监控)
pip install -e .

# 可复现安装 (锁定已验证版本，作为约束不引入额外包)
pip install -e ".[all]" -c requirements-lock.txt

# 按需扩展
pip install -e ".[rag]"       # Chroma 向量知识库 + Ollama 检索
pip install -e ".[ui]"        # Textual TUI + Gradio 后备 Web
pip install -e ".[mcp]"       # 领域专家 MCP Server (Streamable HTTP / stdio)
pip install -e ".[mp]"        # Materials Project 官方 MCP Client (需 Python 3.11+)
pip install -e ".[dev]"       # pytest 测试与打包工具
pip install -e ".[all]"       # 一键全功能
```

安装完成后先跑一次环境自检：

```bash
abr-cli --doctor   # LLM Key / Ollama+向量模型 / MinerU Token / 文献资产 / 可选依赖 一次查完
```

---

## ⚙️ 运行时配置

### 大模型后端 (OpenAI 兼容协议)

支持 MiniMax、DeepSeek、OpenAI、Qwen、Ollama 等任意 OpenAI 兼容后端。

**方式 A：环境变量（优先级最高）**

```powershell
# Windows PowerShell
$env:OPENAI_API_KEY = "你的_API_KEY"
$env:OPENAI_API_BASE = "https://api.minimaxi.com/v1"
$env:OPENAI_MODEL = "MiniMax-M2.7-highspeed"
```

```bash
# Linux / macOS / Bash
export OPENAI_API_KEY="你的_API_KEY"
export OPENAI_API_BASE="https://api.minimaxi.com/v1"
export OPENAI_MODEL="MiniMax-M2.7-highspeed"
```

**方式 B：配置文件 `auto_battery_research/setting.yaml`**

```yaml
openai:
  openai_api_key: "你的_API_KEY"
  openai_api_base: "https://api.minimaxi.com/v1"
  model_name: "MiniMax-M2.7-highspeed"

llm:
  temperature: 0.1        # 主控 ReAct 模型采样温度
  recursion_limit: 25     # ReAct 循环递归深度上限 (每轮工具调用约耗 2 个 super-step)
```

**方式 C：仓库根目录 `.env` 文件（零依赖加载，推荐本地开发）**

```bash
cp .env.example .env   # 然后编辑填写
```

> 三种方式优先级：环境变量 > `.env` > `setting.yaml` 内置默认值（`.env` 不覆盖已导出的系统变量；MinerU Token 同样支持 `MINERU_TOKEN` 写入 `.env`）。

配置支持 `$(ENV_VAR: 默认值)` 环境变量插值；按角色（planner/extraction/writer/reviewer/checker）可分别指定模型。

### 本地依赖服务

| 服务 | 用途 | 说明 |
|---|---|---|
| **Ollama** (`localhost:11434`) | Stage 2/4 向量检索 (`qwen3-embedding:8b`) | 不可用时检索降级为本地 BM25 词法检索（配置键 `RETRIEVAL_MODE` 支持 `hybrid`/`chroma`/`tfidf`） |
| **MinerU Cloud API** | Stage 1 新增 PDF → Markdown 解析 | 云端服务需配置 token；已有文献资产无需触发 |

> 未配置 API Key 时，Stage 1–3 与 Stage 5–6 可通过确定性逻辑/本地求解执行；**Stage 4 RAG 采用严格 fail-closed 策略**——LLM 不可用时不会编造兜底配方，产物标记 `REJECTED` 并由 `RAGDesignChecker` 拦截。

### Materials Project 官方 MCP（可选）

在运行项目的 Python 环境中安装 `pip install -e ".[mp]"`，并在项目根目录的 `.env` 中填写 `MP_API_KEY`。`MP_MCP_PYTHON` 留空时使用当前解释器；若项目运行在其他环境，将它设为安装了 `mp-api[mcp]` 的解释器路径。FastMCP Client 会按需启动官方 `mp_api.mcp.server`，无需手动常驻 Server。配置和握手命令见 [Materials Project MCP 接入说明](docs/materials_project_mcp.md)。

Stage 4 的 Planner **仅在两个条件同时成立时**生成 `mp_queries`：问题明确给出化学式、化学体系或 `mp-id`，且回答确实需要晶体结构、晶相稳定性、形成能、能量高于凸包、带隙等计算属性。查询在本地文献检索之后、Writer 生成方案之前执行；`RunRAGDesign` 已包含此步骤，同一设计问题无需先让 Agent 再调用一次 `QueryMaterialsProject`。

| 问题示例 | 预期 `mp_queries` | 主要证据 |
|---|---|---|
| `LaFeO3 的空间群和能量高于凸包是多少？` | `["LaFeO3"]` | MP 计算属性 |
| `LaFeO3 包覆后 1 C 循环 150 次容量保持率是多少？` | `[]` | 本地实验文献 |

仅出现材料名称并不触发 MP。包覆工艺、电解液配方、界面 M–O 键是否形成、氧释放是否减少及循环性能，仍需实验或文献证据；MP 的本体计算属性不能直接证明这些结论。运行后可检查课题目录下 `rag_result.json`：`plan.mp_queries` 是规划的查询，`retrieval.search_logs` 是实际调用状态，最终 `evidence` 中的 `MP:` 条目及方案引用才表明 MP 数据进入了答案。

---

## 🚀 快速上手

### 模式 1：CLI 命令行（`abr-cli` 或 `python auto_battery_research_cli.py`）

```bash
# 一键全自动端到端执行 (Stage 5 默认启用；未匹配 PINN 注册表体系时自动仅提取参数)
abr-cli --run --goal "设计400Wh/kg高比能液态锂金属电池方案"

# 跳过 Stage 5 物理仿真（快速方案生成模式，仍提取 pinn_input_spec.json）
abr-cli --run --goal "设计400Wh/kg高比能液态锂金属电池方案" --skip-pinn

# 显式强制激活 Stage 5 物理仿真 (等同默认配置)
abr-cli --run --goal "设计400Wh/kg高比能液态锂金属电池方案" --with-pinn

# 分步调试与门禁交互
abr-cli --status              # 6 阶段全局状态矩阵与进度
abr-cli --tips                # 当前 Stage 任务要求与验收指标
abr-cli --check               # 确定性门禁自检 (只诊断不推进)
abr-cli --complete            # 终审通过并原子推进阶段指针
abr-cli --skip-stage 5        # 动态跳过指定阶段
abr-cli --enable-stage 5      # 重新激活已跳过阶段
abr-cli --journal             # 查看全阶段历史研发日志 (StageJournal)
abr-cli --report              # 终端高亮渲染最终综合研报
abr-cli --reset               # 重置当前课题工作流状态至 Stage 1
```

### 模式 2：Python API 编程接入

```python
from auto_battery_research.agent import ABRAgent

agent = ABRAgent(
    goal="设计400Wh/kg高比能液态锂金属电池方案",
    skip_pinn=False,
    verbose=True,
)

for event in agent.run_stream():
    print(f"[{event['event']}] Stage {event['stage_id']} - 进度: {event['progress_ratio']*100:.1f}%")

if agent.manager.is_all_completed():
    print("研发任务全部完成！研报已生成。")
```

### 模式 3：Textual / Rich TUI 多面板交互控制台

```bash
abr-cli --tui
```
状态大屏实时跟踪 Stage 1~6 状态与耗时；智能体日志流展示 Thought / Tool Call / Observation；操作栏一键触发 Check、Complete、Run、Reset。

### 模式 4：Web 监控大屏（FastAPI 只读版）

```bash
abr-cli --web --host 127.0.0.1 --port 7865   # 课题列表 / 阶段进度 / 研报渲染 / 运行日志
abr-cli --web-gradio                          # 旧版交互式 Gradio 仪表盘（后备）
```

### 模式 5：领域专家 MCP Server

```bash
pip install -e ".[mcp]"
python -m auto_battery_research.mcp.server --transport streamable-http
# 或通过控制台入口:
auto-battery-research-mcp --transport stdio
```

默认 HTTP 端点为 `http://127.0.0.1:8000/mcp`，也支持 `--transport stdio`。对外提供轻量文本咨询 `battery_consult` 和完整实验方案 `design_battery_experiment`；内部科研工具不直接暴露。接口与返回约定见 [MCP Server 说明](auto_battery_research/mcp/README.md)。原有 `abr-cli --mcp` 14 工具 stdio 入口仍保留。

---

## 📂 数据流与产物目录

```
papers/pdf ──(MinerU 解析+合并+分类)──▶ papers/merged → database/type/{电池体系}/{cathode,anode,electrolyte}
                                              │
                    (v5_qwen 语义打标 + qwen3-embedding 向量化)
                                              ▼
                              miner/chroma/paragraphs_q (Chroma 段落库)
                                              │
                    (Tok2000 材料挖掘 + 归一化 + 电芯组装)
                                              ▼
                              miner/json/*_extracted*.json
                                              │
              ┌───────────────────────────────┴───────────────────────────────┐
              │              每个课题独立存放: output/tasks/<课题>/              │
              │  .stage_state.json          工作流状态 (跨调用持久化)            │
              │  stage_journals.json        阶段研发日志                        │
              │  cell_assembly/             Stage 3 电芯组装产物                │
              │  design_scheme.md/.json     Stage 4 设计方案 (五段式+证据链+几何v2.0)│
              │  design_revisions/          Stage 4/5 几何设计与修订快照        │
              │  rag_result.json            Stage 4 原始 RAG 结果               │
              │  research_context.json      知识资产溯源快照 (corpus哈希/向量库/规则版本)│
              │  pinn_input_spec.json       Stage 5 PINN 触发判定+材料参数提取清单 │
              │  pinn_simulation_result.json Stage 5 SPM PINN 仿真结果 (命中体系)│
              │  pinn_simulation_curve.png  Stage 5 V-Q 放电曲线图 (命中体系)   │
              │  experiment_plan.json       Stage 6 实验方案生成记录与指纹      │
              │  final_research_report.md   Stage 6 综合研报 (六节结构)         │
              └───────────────────────────────────────────────────────────────┘
```

> 旧版全局目录 `output/auto_battery_research/` 仅作为历史产物的读取回退，新产物一律写入课题目录，多课题互不覆盖。

---

## 🏗️ 项目结构（三层架构）

```
auto_battery_research/        # Layer 1: 智能体编排
├── agent.py                  #   ABRAgent 主控 ReAct 循环 + 自愈反思
├── backend/
│   ├── langchain_backend.py  #   LangGraph create_agent + MemorySaver 运行时
│   └── llm_client.py         #   OpenAI 兼容 LLM 客户端
├── workflow/stage_manager.py #   StageManager 6 阶段状态机 (声明式: abr_workflow.yaml)
├── checkers/                 #   每阶段一个确定性门禁 Checker
├── simulation/               #   几何契约 (geometry_contract.py) 与 SPM 仿真门面
├── tools/
│   ├── domain_tools.py       #   11 个阶段领域工具 (含 QueryMaterialsProject 与 ReviseGeometry)
│   ├── stage_tools.py        #   6 个工作流护栏工具 (CurrentTips/Status/Check/Complete/SetStageJournal/AllStageJournal)
│   ├── workflow_actions.py   #   工具 → 真实流水线桥接 (增量调度 legacy 脚本)
│   ├── rag_adapter.py        #   Stage 4 → RAG 引擎适配器 (契约规范化与 fail-closed 审查)
│   └── mcp_server.py         #   原有 14 工具 stdio MCP 入口
├── mining/ · pipeline/       #   统一门面: 再导出 agent/* 挖掘实现与增量流水线核心
├── rag/                      #   统一门面: 再导出 src/lmllm/RAG/*
├── mcp/                      #   领域专家 MCP Server（Streamable HTTP / stdio）
└── tui/ · web/               #   Textual TUI; FastAPI 只读监控 (--web) + Gradio 后备 (--web-gradio)

src/lmllm/RAG/                # Layer 2: 多智能体 RAG 引擎
├── agents.py                 #   Planner → Retrieval → Writer → Reviewer
├── rag_pipeline.py           #   流水线编排 (单链路设计 + target_energy_wh_kg 提取)
├── multi_retrieval.py        #   双库混合检索 (Chroma + BM25 + RRF，降级本地 BM25)
├── relation_engine.py        #   热力学分级约束 C1–C3, C5–C8 (C4 已移除；v2.1-coincell)
└── prompts.py                #   中央提示词注册表

preprocessing/                # Layer 3: legacy 阶段脚本 (preprocessing/miner 由子进程调度)
miner/                        #   Stage 2 语义打标 + 向量化
agent/                        #   Stage 3 材料挖掘与电芯组装 (经 mining 门面导入)
pinn/                         #   Stage 5 SPM PINN 仿真 (经 simulation 门面导入)
```

---

## ✅ 测试与质量保障

```bash
# 全量离线单元测试 (无需 API Key / 外部服务)
pytest -m "unit or not external"

# 单文件 / 单用例
pytest auto_battery_research/tests/test_checkers.py
pytest auto_battery_research/tests/test_checkers.py::test_pinn_checker_skip_logic

# 标记说明: unit | integration | external | slow
# "external" 需要真实服务 (Ollama / Chroma / LLM API)，默认不运行
pytest -m "unit"
```

离线单测与回归测试集通过注入 dummy key 保持零外部依赖（覆盖课题隔离 `test_task_isolation.py`、Stage 4 离线 golden `test_stage4_golden.py`、几何契约 `test_geometry_contract.py`、Stage 5 仿真与几何修订 `test_stage5_pinn_inference.py` / `test_stage5_geometry_revision.py` / `test_stage5_param_extraction.py`、Stage 6 研报契约 `test_final_design_reporting.py`、FastAPI Web 监控 `test_web_server.py`、MCP 协议层 `test_mcp_server.py` 与包级惰性导入 `test_package_init.py` 等）；确定性 Checker 对空数据、编造数据、异质体系文献与几何越界均有严格拦截用例。CI 固定运行在 **Python 3.12**。

---

## 📌 重要行为说明

- **Stage 5 默认启用**（`runtime_options.skip_pinn_default: false` 且 `pinn_trigger.enabled: true`）：方案体系匹配 `pinn/models/registry.json`（如 `GrSi_NMC811`）即执行纯 numpy SPM PINN 放电仿真，未匹配体系平滑回退参数提取-only；`--skip-pinn` 或 `abr-cli --skip-stage 5` 可手动跳过（参数提取仍执行）。Stage 5 仿真未达目标能量容差时，可通过 `ReviseGeometry`（默认最多 3 轮，携带 `base_revision` 与 `base_geometry_hash` 校验）反馈修订几何，Check/Complete 共用择优收尾逻辑（阶段完成后不再重复仿真）。
- **结构化几何参数通道与 Stage 4 契约 v2.0**：Stage 4 在 Writer 生成正文前独立生成并校验结构化几何候选，参数声明表由程序渲染，`Writer` 阶段冻结且禁止从改写后的正文回写覆盖输入。`scheme.geometry` 保留 v1.1 数值布局兼容仿真，并以 `contract_version="2.0"`、`parameter_sources`、`design_revision` 与 `geometry_hash` 标识新通道：正/负极厚度 `L_um` 与孔隙率 `porosity` 必须显式设计；颗粒**半径** `R_s_um` 可采用设计值或标注 `model_baseline`/`missing`；`D_s_m2s` 仅在课题证据支持下修改，否则标注匹配模型基准/缺失，不伪装成文献实测。`N/P` 为可选参数，缺失时保持正负极声明厚度而不反向校准。设计快照存于课题 `design_revisions/`，门禁由 `RAGDesignChecker`（`runtime_options.geometry_gate: enforce|warn`，默认 `enforce`）校验。
- **Stage 4 离线/异常 fail-closed 与 Stage 6 六节研报契约**：当 LLM 不可用或 Reviewer 响应无效时，Stage 4 不编造兜底配方且绝不标定为 `APPROVED`（标记 `REJECTED` 并由门禁拦截）。Stage 6 最终报告采用六节结构（`<!-- abr-report-contract:2 -->` 与 `<!-- abr-report-layout:6 -->`），以选定设计、实际生效几何及指纹匹配的仿真产物为准，明确目标容差判定与固定系数能量折算口径；第五部分实验路线由大模型综合完整方案/证据/仿真生成并通过引用与参数一致性校验（`experiment_plan.json` 记录输入指纹），不可用或未过验收时回退确定性渲染（不补入无来源固定工艺数值）。
- **工作流状态按课题持久化**：修改阶段交付物或代码后，用 `abr-cli --reset` 让该课题从 Stage 1 重新评估。
- **严格模式**（`runtime_options.strict_mode: true`）下任何 Checker 错误即判失败；`max_retries_per_stage: 3` 约束自愈重试上限。
- **Windows 为主开发平台**：入口脚本自动将 stdout/stderr 重配置为 UTF-8。
- 更多操作细节见 [USAGE.md](USAGE.md)，工程结构与开发约定见 [CLAUDE.md](CLAUDE.md)。
