# AutoBatteryResearch Agent (ABRAgent)

**全生命周期化学电池研究自主智能体与科研工程平台**

AutoBatteryResearch Agent (ABRAgent) 是专为高比能化学电池（锂金属电池、固态电池、高镍三元体系等）研发设计的自主 AI 智能体系统。系统采用 **Agent-Centric（智能体主控）设计哲学**：由全局顶层智能体（`ABRAgent`）驱动 LangChain/LangGraph ReAct 认知循环，融合**学术文献感知、语义向量入库、微观材料与电芯组装挖掘、多智能体 RAG 方案设计（Planner/Retrieval/Writer/Reviewer）、热力学硬约束规则引擎（RelationEngine C1–C8）以及 PyBaMM/P2D 物理偏微分方程仿真**，实现从海量学术论文到结构化电芯配方方案、物理验证及综合科研研报的端到端自主闭环。

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
      │  [Stage 4: 多智能体 RAG 单链路设计服务]                                                   │
      │  └── RunRAGDesignTool                     ──▶ 单链路: Planner→Retrieval→Writer→Reviewer  │
      │                                              + RelationEngine 硬约束 (C1-C8) 核算与溯源   │
      │                                                                                         │
      │  [阶段门禁与状态流转工具]                                                                  │
      │  ├── CurrentTips: 读取当前阶段任务指标规范 (防止走偏)                                       │
      │  ├── Check: 触发确定性 Checker；若报错输出 failure_summary 供 Agent 自我反思修复           │
      │  └── Complete: 门禁完全通过后，原子推进工作流至下一阶段                                    │
      └─────────────────────────────────────────────────────────────────────────────────────────┘
```

1. **智能体主控架构**：顶层 `ABRAgent` 掌控 ReAct 主循环（Thought → Action → Observation），六个阶段全部由大模型自主决策；工作流（`StageManager`）与确定性校验器（`Checkers`）作为外部护栏——**大模型在大脑主控位，工作流在规则裁判位**。确定性执行器作为离线/异常兜底，保证无 API Key 时任务仍可闭环。
2. **确定性门禁与自愈反思**：`Check` 自检不通过时输出 `failure_summary`（error_code / error / next_action），回注重试轮提示词驱动针对性修复；同一阶段多次重试共享 LangGraph 线程记忆。
3. **真实数据铁律**："有则提取、无则留空、禁止编造"。设计方案必须通过 RelationEngine 的全部热力学硬约束（C1–C8）与能量密度核算；证据链必须携带真实 DOI 溯源。
4. **课题级产物隔离**：每个研究课题的产物（设计方案、电芯组装、综合研报、状态文件）独立存放在 `output/tasks/<课题>/`，多课题并行互不污染。
5. **四维多模态交互入口**：CLI 命令行、Rich TUI 终端面板、FastAPI Web 监控大屏（Gradio 后备）、stdio MCP Server（可接入 Cursor / Claude Desktop 等 AI IDE）。

---

## 🔬 六阶段科研工作流

| Stage | 名称 | 核心产物 | 门禁 Checker |
|:---:|---|---|---|
| 1 | 文献解析与组件分类 | `database/type/` 分类文献库 | `IngestionChecker`（三大组件分类完备性） |
| 2 | 语义标注与向量入库 | `miner/chroma/paragraphs_q` 段落向量库 | `VectorDBChecker` |
| 3 | 材料挖掘与电芯组装 | `<task>/cell_assembly/` 结构化电芯实体 | `CellAssemblyChecker`（材料+电芯+溯源完备） |
| 4 | 多智能体 RAG 方案设计 | `<task>/design_scheme.md/.json` | `RAGDesignChecker`（五段式+证据+RelationEngine） |
| 5 | PINN/P2D 物理仿真 | `<task>/simulation_result.json` | `PINNPhysicsChecker`（**默认跳过**） |
| 6 | 综合研报生成 | `<task>/final_research_report.md` | `FinalReportChecker`（五大章节+阶段日志） |

阶段与 Checker 均在 `auto_battery_research/workflow/abr_workflow.yaml` 中声明式定义，`StageManager` 从中加载；工作流状态按课题持久化于 `output/tasks/<课题>/.stage_state.json`，跨 CLI 调用续跑。

---

## 📦 安装指南

推荐 **Python 3.10 ~ 3.12**（Windows / Linux / macOS 均可；3.13 可运行核心功能，物理仿真除外）：

```bash
git clone https://github.com/CHEERUPGOGO/AutoBatteryResearch.git
cd AutoBatteryResearch

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux / macOS:
# source .venv/bin/activate

# 基础安装 (核心智能体与 CLI)
pip install -e .

# 可复现安装 (锁定已验证版本，作为约束不引入额外包)
pip install -e ".[all]" -c requirements-lock.txt

# 按需扩展
pip install -e ".[rag]"       # Chroma 向量知识库 + Ollama 检索
pip install -e ".[ui]"        # Rich TUI + Gradio Web
pip install -e ".[physics]"   # PyBaMM P2D 物理仿真 (Python < 3.13)
pip install -e ".[dev]"       # pytest 测试
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
| **Ollama** (`localhost:11434`) | Stage 2/4 向量检索 (`qwen3-embedding:8b`) | 不可用时检索自动降级为 TF-IDF/BM25 |
| **MinerU Cloud API** | Stage 1 新增 PDF → Markdown 解析 | 云端服务需配置 token；已有文献资产无需触发 |

> 未配置 API Key 时系统自动进入确定性流水线模式（离线兜底），各阶段门禁仍可正常推进。

---

## 🚀 快速上手

### 模式 1：CLI 命令行（`abr-cli` 或 `python auto_battery_research_cli.py`）

```bash
# 一键全自动端到端执行 (Stage 5 物理仿真默认快速跳过)
abr-cli --run --goal "设计400Wh/kg高比能液态锂金属电池方案"

# 全自动执行并显式激活 Stage 5 物理仿真
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
    skip_pinn=True,
    verbose=True,
)

for event in agent.run_stream():
    print(f"[{event['event']}] Stage {event['stage_id']} - 进度: {event['progress_ratio']*100:.1f}%")

if agent.manager.is_all_completed():
    print("研发任务全部完成！研报已生成。")
```

### 模式 3：Rich TUI 多面板交互控制台

```bash
abr-cli --tui
```
状态大屏实时跟踪 Stage 1~6 状态与耗时；智能体日志流展示 Thought / Tool Call / Observation；操作栏一键触发 Check、Complete、Run、Reset。

### 模式 4：Web 监控大屏（FastAPI 只读版）

```bash
abr-cli --web --host 127.0.0.1 --port 7865   # 课题列表 / 阶段进度 / 研报渲染 / 运行日志
abr-cli --web-gradio                          # 旧版交互式 Gradio 仪表盘（后备）
```

### 模式 5：接入 AI IDE (stdio MCP Server)

```bash
abr-cli --mcp
```
内置 Model Context Protocol 服务，可直接集成至 Cursor、Claude Desktop 等。

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
              │  design_scheme.md/.json     Stage 4 设计方案 (五段式+证据链)     │
              │  rag_result.json            Stage 4 原始 RAG 结果               │
              │  research_context.json      知识资产溯源快照 (corpus哈希/向量库/规则版本) │
              │  simulation_result.json     Stage 5 仿真结果 (启用时)            │
              │  final_research_report.md   Stage 6 综合研报                    │
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
│   └── loop_runner.py        #   AutonomousLoopRunner (无 LLM 确定性兜底闭环)
├── workflow/stage_manager.py #   StageManager 6 阶段状态机 (声明式: abr_workflow.yaml)
├── checkers/                 #   每阶段一个确定性门禁 Checker
├── tools/
│   ├── domain_tools.py       #   9 个阶段领域工具 (Inspect* + RunRAGDesign 单链路服务 + 执行器)
│   ├── stage_tools.py        #   工作流护栏工具 (Tips/Status/Check/Complete/...)
│   ├── workflow_actions.py   #   工具 → 真实流水线桥接 (增量调度 legacy 脚本)
│   └── rag_adapter.py        #   Stage 4 → RAG 引擎适配器 (输出契约规范化)
├── mining/ · pipeline/       #   统一门面: 再导出 agent/* 挖掘实现与增量流水线核心
├── rag/ · simulation/        #   统一门面: 再导出 src/lmllm/RAG/* 与 pinn/* 物理求解器
├── tui/ · web/               #   Textual TUI; FastAPI 只读监控 (--web) + Gradio 后备 (--web-gradio)
└── tools/mcp_server.py       #   stdio MCP 服务

src/lmllm/RAG/                # Layer 2: 多智能体 RAG 引擎
├── agents.py                 #   Planner → Retrieval → Writer → Reviewer
├── rag_pipeline.py           #   流水线编排
├── multi_retrieval.py        #   混合检索 (Chroma + BM25/TF-IDF 降级)
├── relation_engine.py        #   热力学硬约束 C1–C8
└── prompts.py                #   中央提示词注册表

preprocessing/                # Layer 3: legacy 阶段脚本 (preprocessing/miner 由子进程调度)
miner/                        #   Stage 2 语义打标 + 向量化
agent/                        #   Stage 3 材料挖掘与电芯组装 (经 mining 门面导入)
pinn/                         #   Stage 5 PyBaMM P2D 仿真 (经 simulation 门面导入)
```

---

## ✅ 测试与质量保障

```bash
# 全量离线单元测试 (无需 API Key / 外部服务，约 5 分钟)
pytest -m "unit or not external"

# 单文件 / 单用例
pytest auto_battery_research/tests/test_checkers.py
pytest auto_battery_research/tests/test_checkers.py::test_pinn_checker_skip_logic

# 标记说明: unit | integration | external | slow
# "external" 需要真实服务 (Ollama / Chroma / LLM API)，默认不运行
pytest -m "unit"
```

当前基线：**84 passed, 2 deselected, 0 warnings**（含课题隔离回归 `test_task_isolation.py`、Stage 4 离线 golden `test_stage4_golden.py`、FastAPI Web 监控 `test_web_server.py` 与 MCP 协议层 `test_mcp_server.py`）。单测通过注入 dummy key 保持零外部依赖；确定性 Checker 对空数据、编造数据、异质体系文献均有严格拦截用例。注：Python 3.14 环境可能出现 Pydantic v1 兼容 warning 与依赖缺省导致的 skip，建议 CI 固定 Python 3.12。

---

## 📌 重要行为说明

- **Stage 5 默认跳过**（`runtime_options.skip_pinn_default: true`），`--with-pinn` 或 `abr-cli --enable-stage 5` 激活；PyBaMM 需要 Python < 3.13。
- **工作流状态按课题持久化**：修改阶段交付物或代码后，用 `abr-cli --reset` 让该课题从 Stage 1 重新评估。
- **严格模式**（`runtime_options.strict_mode: true`）下任何 Checker 错误即判失败；`max_retries_per_stage: 3` 约束自愈重试上限。
- **Windows 为主开发平台**：入口脚本自动将 stdout/stderr 重配置为 UTF-8。
- 更多操作细节见 [USAGE.md](USAGE.md)，工程结构与开发约定见 [CLAUDE.md](CLAUDE.md)。
