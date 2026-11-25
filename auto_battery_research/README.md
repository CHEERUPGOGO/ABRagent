# AutoBatteryResearch Agent (ABRAgent)

> **全生命周期化学电池自主研究与设计智能体系统**  
> 采用配置驱动的多阶段工作流架构，实现从学术文献入库、语义向量化、材料电芯数据挖掘、多智能体 RAG 方案设计、SPM PINN 物理仿真验证（默认启用，可按需跳过）到最终综合研发研报生成的全流程自动化闭环。

---

## 📖 目录

- [一、核心架构与设计理念](#一核心架构与设计理念)
- [二、系统全景架构与数据流](#二系统全景架构与数据流)
- [三、六大核心阶段与交付物详解](#三六大核心阶段与交付物详解)
- [四、确定性门禁检查器体系 (Checkers)](#四确定性门禁检查器体系-checkers)
- [五、Stage 5 (PINN 物理仿真) 弹性跳过机制](#五stage-5-pinn-物理仿真-弹性跳过机制)
- [六、六大多维运行与交互模式](#六六大多维运行与交互模式)
  - [1. Textual 多面板终端交互界面 (Interactive TUI)](#1-textual-多面板终端交互界面-interactive-tui)
  - [2. Web 监控大屏 (FastAPI) 与 Gradio 后备](#2-web-监控大屏-fastapi-与-gradio-后备)
  - [3. 全自动自主循环模式 (Autonomous Loop)](#3-全自动自主循环模式-autonomous-loop)
  - [4. CLI 命令行交互与单步调试](#4-cli-命令行交互与单步调试)
  - [5. MCP Server 模式 (供 Claude Code / Qwen / Antigravity 接入)](#5-mcp-server-模式-供-claude-code--qwen--antigravity-接入)
  - [6. Python 编程接口 (API)](#6-python-编程接口-api)
- [七、项目目录与配置文件清单](#七项目目录与配置文件清单)
- [八、大模型配置与环境说明](#八大模型配置与环境说明)
- [九、常见问题与故障排查 (FAQ)](#九常见问题与故障排查-faq)

---

## 一、核心架构与设计理念

在传统大模型科研辅助中，长链条任务往往面临幻觉、跳步、虚假交付以及缺乏物理约束等问题。**AutoBatteryResearch Agent (ABRAgent)** 建立了一套以智能体为主体、以工作流为护栏的严密全生命周期自动化科研工程体系：

1. **智能体主控体系 (Agent-Centric Architecture)**：
   - 运行时顶层实例化唯一的全局科研主控智能体 `ABRAgent`，基于 LangChain / LangGraph 认知循环驱动思考、工具调用与反思。
   - 工作流（`StageManager`）与确定性门禁（`Checkers`）作为外部裁判与护栏，防止智能体产生幻觉或偏离目标。
2. **Stage 1~6 细粒度领域工具与护栏工具**：
   - `domain_tools.py` 提供 **11 个阶段领域工具**（`InspectLiteratureAssets`、`IngestLiteraturePapers`、`InspectVectorDB`、`IndexSemanticVectors`、`InspectCellEntities`、`ExtractAndAssembleCells`、`RunRAGDesign`、`QueryMaterialsProject`、`RunPhysicsSimulation`、`ReviseGeometry`、`SynthesizeResearchReport`），由 Agent 按阶段白名单自主调度。
   - `stage_tools.py` 提供 **6 个工作流护栏工具**（`CurrentTips`、`Status`、`Check`、`Complete`、`SetStageJournal`、`AllStageJournal`），配合 `RoleInfo` 控制门禁自检与状态推进。
3. **分阶段状态机推进与门禁自检 (Check & Complete)**：
   - 任务被严格划分为 6 个按序执行的 Stage。
   - `Check` 动作负责门禁自检（只诊断不推进），返回包含 `error_code`、`failure_summary` 与 `next_action` 的结构化诊断，指导智能体自我修复；`Complete` 动作在门禁合格后原子推进阶段指针。
4. **Stage 5 (PINN) 弹性跳过与几何反馈机制**：
   - 将物理求解层（SPM PINN 放电仿真，纯 numpy）设为默认启用且可配置跳过，未匹配 `pinn/models/registry.json` 的体系平滑回退参数提取-only；仿真未达目标能量容差时支持 `ReviseGeometry` 反馈修订与择优回滚。
5. **阶段研发日志持久化 (Stage Journal)**：
   - 各阶段推进前调用 `SetStageJournal` 记录关键发现与交付物路径，课题目录持久化 `stage_journals.json`（兼容旧名 `abr_agent_journal.json`）便于复盘审计。

---

## 二、系统全景架构与数据流

```
                    ┌────────────────────────────────────────────────────────┐
                    │            用户目标 (如: 400Wh/kg 锂金属电池设计)          │
                    └───────────────────────────┬────────────────────────────┘
                                                │
                                                ▼
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│                         AutoBatteryResearch Agent 工作流状态机 (按课题隔离输出)             │
│                                                                                            │
│   [Stage 1] 文献解析与分类 ─────────► [IngestionChecker] ────────► papers/merged/          │
│        │                                                              database/type/       │
│        ▼                                                                                   │
│   [Stage 2] 语义标注与向量入库 ─────► [VectorDBChecker] ─────────► meta_merged.json        │
│        │                                                              Chroma paragraphs    │
│        ▼                                                                                   │
│   [Stage 3] 材料挖掘与电芯组装 ─────► [CellAssemblyChecker] ────► <task>/cell_assembly/    │
│        │                                                                                   │
│        ▼                                                                                   │
│   [Stage 4] 多智能体 RAG 方案设计 ──► [RAGDesignChecker] ────────► <task>/design_scheme.*  │
│        │    (单链路 + C1-C3/C5-C8 分级规则 + 几何契约 v2.0 + 离线 fail-closed)             │
│        ▼                                                                                   │
│   [Stage 5] PINN 触发/提取/SPM仿真 ──► [PINNPhysicsChecker] ─────► <task>/pinn_input_spec.json│
│        │    (默认启用; 命中体系产出仿真结果+曲线+ReviseGeometry修订)                         │
│        ▼                                                                                   │
│   [Stage 6] 综合研发报告生成 ───────► [FinalReportChecker] ──────► <task>/final_research_report.md│
│                                                                    experiment_plan.json+日志│
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 三、六大核心阶段与交付物详解

| Stage | 阶段 Key | 阶段名称 | 核心职责 | 必须交付物 | 默认状态 |
|:---:|:---|:---|:---|:---|:---:|
| **1** | `literature_ingestion` | **文献解析与组件分类** | PDF DOI 提取、MinerU 转换、主附录合并、中英双语清洗、电池 4 类与正/负/电解质 3 类归档 | `database/type/**/{cathode,anode,electrolyte}/*.md` | **必跑** |
| **2** | `semantic_vector_indexing` | **语义标注与向量入库** | 提取文献元数据，对段落标注 6 类互斥语义标签，经 `qwen3-embedding:8b` 向量化持久化至 Chroma 库 | `meta_merged.json`、`miner/chroma/paragraphs_q/` | **必跑** |
| **3** | `data_mining_cell_assembly` | **材料挖掘与电芯组装** | 2000-tokens 切分、Phase 0 材料发现、三层归一化、format1 属性校验、Cell 实体组装与 ML 数据集展平 | `*_extracted.json`、`output/tasks/<task_slug>/cell_assembly/` | **必跑** |
| **4** | `battery_rag_design` | **多智能体 RAG 方案设计** | `RunRAGDesign` → `AbrRagAdapter` → `RAGPipeline`（`Planner → Retrieval (+MP) → Writer → Reviewer`），提取 `target_energy_wh_kg`，执行 `RelationEngine` 7 条分级规则（`C1–C3, C5–C8`，**C4 已移除**，`RULES_VERSION="C1-C8/v2.4-c1-c3-advisory"`）与几何契约 v2.0；LLM 不可用时 fail-closed 拒绝，不编造兜底配方 | `design_scheme.json`、`design_scheme.md`、`rag_result.json`、`research_context.json`、`design_revisions/` | **必跑** |
| **5** | `pinn_physics_simulation` | **PINN 触发判定、参数提取与 SPM 仿真** | `pinn_trigger.enabled`（默认 `true`）AND 方案匹配 `pinn/models/registry.json`（如 `GrSi_NMC811`）即执行纯 numpy SPM PINN 放电仿真；未匹配则仅输出参数提取契约；未达标时支持 `ReviseGeometry`（默认最多 3 轮）反馈修订并择优回滚 | `pinn_input_spec.json`；命中体系时产出 `pinn_simulation_result.json`、`pinn_simulation_curve.png` | **默认启用**（支持 `--skip-pinn` 跳过） |
| **6** | `synthesis_report_generation` | **综合研发报告生成** | 基于选定设计与指纹匹配的仿真产物编译六节结构化研报，校验第五部分大模型实验方案或回退确定性渲染，确保输入变更后重编一致 | `final_research_report.md`、`experiment_plan.json`、`stage_journals.json` | **必跑** |

---

## 四、确定性门禁检查器体系 (Checkers)

每个 Stage 均绑定专属 Checker（继承自 [`BaseChecker`](file:///d:/llm-main/auto_battery_research/checkers/base_checker.py)），实现确定性门禁检查。

### 1. 结构化诊断契约 (Diagnostic Contract)
当检查不通过时，Checker 返回统一结构化诊断字典：
```json
{
  "check_pass": false,
  "checker_name": "RAGDesignChecker",
  "stage_id": 4,
  "stage_key": "battery_rag_design",
  "error_code": "DESIGN_SCHEME_SECTIONS_INCOMPLETE",
  "error": "设计方案缺少关键五段式模块: 推荐组合, 风险与数据缺口",
  "observed": { "found_sections": ["目标与设计路线", "预期关键指标", "可行性依据"] },
  "expected": "必须包含五段式完整结构（目标路线、推荐组合、预期指标、可行性依据、风险缺口）",
  "next_action": "在方案中补齐缺失章节并重新校验"
}
```

### 2. 门禁检查器一览
- **`IngestionChecker` (Stage 1)**: 检查分类目录、排除 $<50$ 字节损坏空文件、确保正极/负极/电解质分类完备。
- **`VectorDBChecker` (Stage 2)**: 检查元数据数组有效性、6 类语义标签覆盖率、Chroma 库持久化文件健康度。
- **`CellAssemblyChecker` (Stage 3)**: 检查材料归一化 ID (`canonical_id` / `base_id`)、电解液配方拆解、电芯组装 `cell_id` 与 ML CSV 列对齐。
- **`RAGDesignChecker` (Stage 4)**: 审查五段式报告结构（$\ge 200$ 字符）、Reviewer 合法完成（拦截离线/失败 `REJECTED` 产物）、$\ge 1$ 条非异质真实文献/MP 证据、`RelationEngine` 7 条分级规则（`C1–C3, C5–C8`，C4 已移除；包含 `C0_UNVERIFIED_MATERIAL` 拦截、`C1/C3` 非阻断建议、`C2` 缓解白名单 `challenge`、`C5` 推荐、`C6` 审计批注、`C7` $\ge 400$ Wh/kg 排除石墨、`C8` $\ge 500$ Wh/kg 前沿标记放行）及 `scheme.geometry` 契约 v2.0（`L_um`/`porosity` 必填显式声明，`R_s_um`/`D_s_m2s` 按证据与基准规则核验，`geometry_hash` 校验）。
- **`PINNPhysicsChecker` (Stage 5)**: 若配置跳过则直接放行；若激活且 `triggered=true` 则强制校验 `pinn_simulation_result.json`（`status=="CONVERGED"`、`spec_hash`、`scheme_hash`、几何契约 v2.0 与 `geometry_hash`、物理数值区间、训练包络、`pde_residual_loss <= 0.05`）；若 `triggered=false`（未匹配体系/总开关关闭）则校验 `pinn_input_spec.json` 结构完整即可通过。
- **`FinalReportChecker` (Stage 6)**: 检查综合研报篇幅（$\ge 400$ 字符）、六节结构（`<!-- abr-report-layout:6 -->`：研发摘要、材料选择与文献依据、最终设计方案、仿真与优化分析、实验路线与验证计划、参考来源附录；兼容历史五段式）、`<!-- abr-report-contract:2 -->` 与 `report_identity` 输入指纹一致性、`experiment_plan.json`（或 `abr-experiment-plan-fallback`）及阶段日志持久化（`stage_journals.json` / `abr_agent_journal.json`）。

---

## 五、Stage 5 (PINN 物理仿真) 弹性跳过机制

### 1. 当前语义与默认行为
- Stage 5 **默认启用**：触发判定 = `pinn_trigger.enabled` 总开关 AND 方案 `(cathode, anode)` 匹配 `pinn/models/registry.json` 已训练体系（如 `GrSi_NMC811`）；命中即执行纯 numpy SPM PINN 放电仿真（`pinn/spm_runner.py`）并落盘 `pinn_simulation_result.json` 与 `pinn_simulation_curve.png`，未匹配/总开关关闭平滑回退参数提取-only（提取不到的物理动力学字段保持 `null`）。
- Stage 4 门禁校验 `scheme.geometry`（兼容 v1.1 数值字段与 `contract_version="2.0"`：`L_um` 与 `porosity` 必须显式设计，`R_s_um` 为颗粒半径、`D_s_m2s` 仅凭可核查 `diffusion_evidence` 修改否则保持基准/缺失；`RAGDesignChecker` geometry 门禁 `runtime_options.geometry_gate: enforce|warn`）。
- Stage 5 仿真未达课题能量目标容差（`target_energy_tolerance_pct: 5`）时，可通过 `ReviseGeometry`（携带 `base_revision` 与 `base_geometry_hash` 并发/版本保护，默认预算 `max_geometry_revisions: 3`）修订几何并重跑；`Check` 与 `Complete` 共用择优收尾逻辑。
- 旧 PyBaMM `p2d_runner.py` 已移除；Web 页仿真演示 TAB 经 `auto_battery_research.simulation` facade 复用 `pinn/spm_runner.py`。

### 2. 灵活激活/跳过的三种方式

#### 方式 A：命令行参数控制
```bash
# 默认启用 Stage 5 执行触发判定、参数提取与 SPM PINN 仿真 (命中体系时)
python auto_battery_research_cli.py --run --with-pinn

# 显式强制跳过 Stage 5 仿真 (仅快速提取 pinn_input_spec.json)
python auto_battery_research_cli.py --run --skip-pinn

# 动态跳过/激活指定阶段
python auto_battery_research_cli.py --skip-stage 5
python auto_battery_research_cli.py --enable-stage 5
```

#### 方式 B：全局配置文件控制
在 [`auto_battery_research/setting.yaml`](file:///d:/llm-main/auto_battery_research/setting.yaml) 中修改：
```yaml
runtime_options:
  skip_pinn_default: false  # 默认启用 Stage 5；改为 true 可默认跳过
pinn_trigger:
  enabled: true             # Stage 5 PINN 仿真总开关 (kill-switch)
```

#### 方式 C：工具/MCP/TUI 动态调用
```python
tool_enable_stage(stage_id=5)   # 激活
tool_skip_stage(stage_id=5, reason="离线快速评估")  # 跳过
```

---

## 六、六大多维运行与交互模式

### 1. Textual 多面板终端交互界面 (Interactive TUI)
吸收顶级工程控制台多面板布局，提供**任务阶段矩阵、实时门禁自检诊断、任务指南与交互式控制台 Shell**：

```bash
# 启动交互式 TUI 控制台 (无参数运行时默认启动)
python auto_battery_research_cli.py --tui
# 或
python auto_battery_research_cli.py
```

- **TUI 交互指令速查**：
  - `status` / `st`：刷新并查看 6 阶段状态矩阵（`detail` / `dt` 查看详情）
  - `tips` / `t`：查看当前阶段任务要求与验收指标
  - `check [id]` / `c`：执行当前阶段门禁自检（只诊断不推进）
  - `complete [id]` / `cmp`：终审并通过当前阶段（推进到下一阶段）
  - `run [goal]` / `r`：实时启动自主循环引擎
  - `skip <id>` / `s`：动态跳过指定阶段（如 `skip 5`）
  - `enable <id>` / `e`：重新激活指定阶段（如 `enable 5`）
  - `journal` / `j`：查看全阶段研发日志
  - `report` / `rep`：终端内 Markdown 渲染查看最新综合研报
  - `web` / `ui`：直接从终端调起 Web 监控大屏
  - `reset`：重置工作流状态机至 Stage 1

---

### 2. Web 监控大屏 (FastAPI) 与 Gradio 后备
默认 `--web` 启动 **FastAPI 只读监控大屏**：课题列表、6 阶段进度矩阵、综合研报 Markdown 渲染与运行日志轮询，适合跑任务时用浏览器旁路观察（读端不构造 StageManager，跨进程安全）：

```bash
# FastAPI 只读监控大屏 (默认端口 7865, 被占用时自动顺延)
python auto_battery_research_cli.py --web

# 旧版交互式 Gradio 仪表盘 (后备, 支持交互操作与 --share)
python auto_battery_research_cli.py --web-gradio --host 0.0.0.0 --port 7865 --share
```

- **Gradio 后备版四大核心模块**：
  1. 📊 **智能体工作流大屏**：实时可视化 6 阶段状态卡片、一键全自动自主循环、阶段门禁自检/终审、综合研报 Markdown 在线预览。
  2. 🧪 **多智能体 RAG 方案设计**：4-Agent 协同规划（目标与设计路线、推荐组合、预期关键指标、可行性依据、风险与数据缺口）与 `RelationEngine` 分级规则（`C1–C3, C5–C8`）审计。
  3. 📚 **文献资产与电芯数据挖掘**：实时统计磁盘 PDF、合并 Markdown、结构化电芯与文献元数据资产，并支持电化学性能检索。
  4. ⚡ **PINN 物理仿真与放电曲线**：输入放电倍率 (`C-rate`)，调用 `GrSi_NMC811` SPM PINN 实时求解并绘制电压-容量连续放电曲线与能量密度。

---

### 3. 全自动自主循环模式 (Autonomous Loop)
智能体自动遍历各阶段、获取 Tips、调用流水线、自检门禁、遇到错误自动根据 `next_action` 自愈重试、记录 Journal 并推进至终点。

```bash
# 运行默认目标
python auto_battery_research_cli.py --run

# 指定具体电池研究目标
python auto_battery_research_cli.py --run --goal "设计450Wh/kg固液混合电解质超高镍锂金属电池方案"
```

---

### 4. CLI 命令行交互与单步调试
```bash
# 查看全局状态表格
python auto_battery_research_cli.py --status

# 查看当前活跃阶段的任务与 Tips
python auto_battery_research_cli.py --tips

# 对当前阶段执行门禁自检 (只诊断不推进)
python auto_battery_research_cli.py --check

# 对指定 Stage 4 执行门禁检查
python auto_battery_research_cli.py --check-stage 4

# 终审并通过当前阶段 (推进到下一阶段)
python auto_battery_research_cli.py --complete

# 查看所有阶段的历史研发日志
python auto_battery_research_cli.py --journal

# 重置工作流状态至 Stage 1
python auto_battery_research_cli.py --reset
```

---

### 5. MCP Server 模式 (供 Claude Code / Qwen / Antigravity 接入)
```bash
# 完整 14 工具内部工作流 stdio MCP Server
python auto_battery_research_cli.py --mcp

# 轻量领域专家 MCP Server (2 个外部工具，支持 streamable-http / stdio)
python -m auto_battery_research.mcp.server --transport streamable-http
```

---

### 6. Python 编程接口 (API)
```python
from auto_battery_research import StageManager, ABRAgent
from auto_battery_research.tools import tool_get_status, tool_check_stage, tool_run_stage_task

# 1. 创建状态机 (默认 skip_pinn=False 启用 Stage 5)
mgr = StageManager(skip_pinn=False)

# 2. 查询状态与 Tips
print(mgr.get_status())
print(mgr.get_current_tips())

# 3. 运行单个任务与检查
tool_run_stage_task(target_query="设计380Wh/kg富锂锰基电池")
passed, diag = mgr.check_stage()
print(f"检查通过: {passed}, 诊断: {diag}")

# 4. 驱动全自动自主循环 (单一执行内核: LLM 主控 ReAct; Stage 1-3/5-6 可离线确定性执行, Stage 4 离线 fail-closed)
agent = ABRAgent(manager=mgr, goal="设计400Wh/kg高比能锂金属电池方案")
success = agent.run()
print(f"全流程闭环: {success}, 研报: {mgr.get_task_output_dir(agent.goal) / 'final_research_report.md'}")
```

---

## 七、项目目录与配置文件清单

```text
d:/llm-main/
├── auto_battery_research/             # 智能体核心框架包
│   ├── __init__.py                    # PEP 562 惰性导出入口
│   ├── agent.py                       # 全局主控智能体 ABRAgent
│   ├── setting.yaml                   # 全局路径、LLM与运行参数配置
│   ├── cli.py                         # CLI 核心实现
│   ├── README.md                      # 本文档
│   ├── workflow/
│   │   ├── abr_workflow.yaml          # 6 阶段使命与任务定义文件
│   │   └── stage_manager.py           # 工作流状态机与课题级目录管理
│   ├── stage/
│   │   └── base_stage.py              # 阶段实体与 Tips 渲染
│   ├── checkers/                      # 确定性门禁检查器集
│   │   ├── base_checker.py            # Checker 基类与诊断契约
│   │   ├── ingestion_checker.py       # Stage 1 检查器
│   │   ├── vector_db_checker.py       # Stage 2 检查器
│   │   ├── cell_assembly_checker.py   # Stage 3 检查器
│   │   ├── rag_design_checker.py      # Stage 4 检查器
│   │   ├── pinn_physics_checker.py    # Stage 5 检查器 (支持触发/提取-only/skip)
│   │   └── final_report_checker.py    # Stage 6 检查器
│   ├── simulation/
│   │   └── geometry_contract.py       # 几何契约 v2.0 与 diffusion_evidence 校验
│   ├── tools/                         # 工具箱与协议服务
│   │   ├── domain_tools.py            # 11 个阶段领域工具
│   │   ├── stage_tools.py             # 6 个工作流护栏工具 (+ RoleInfo)
│   │   ├── workflow_actions.py        # 科学计算/数据挖掘流水线包装
│   │   ├── rag_adapter.py             # Stage 4 RAG 单链路适配器
│   │   ├── geometry_revision.py       # Stage 5 ReviseGeometry 反馈修订工具
│   │   ├── materials_project.py       # Materials Project 官方 MCP Client 包装
│   │   ├── file_tools.py              # 文本与文件工具
│   │   └── mcp_server.py              # 14 工具 stdio MCP Server 实现
│   ├── mcp/                           # 领域专家 MCP Server (2 工具 HTTP/stdio)
│   ├── backend/
│   │   ├── llm_client.py              # 统一大模型连接器
│   │   └── langchain_backend.py       # LangGraph ReAct 运行时 (ABRAgent 后端)
│   ├── tui/                           # Textual 多面板终端交互界面
│   │   └── app.py                     # TUI 应用程序与交互控制台
│   ├── web/                           # FastAPI 只读监控大屏 + Gradio 后备仪表盘
│   │   ├── server.py                  # FastAPI 只读大屏 (--web)
│   │   └── app.py                     # Gradio 后备仪表盘 (--web-gradio)
│   ├── doc/
│   │   ├── Guide_Doc/                 # 各 Stage 详细操作指南 (Stage 1~6)
│   │   └── templates/                 # 研报参考模板
│   └── tests/                         # 自动化单元测试与回归测试集
│       ├── test_stage_manager.py
│       ├── test_checkers.py
│       ├── test_abr_agent.py
│       ├── test_stage4_golden.py
│       ├── test_geometry_contract.py
│       └── ...
│
├── auto_battery_research_cli.py       # 根目录快捷执行入口
├── abr_cli.py                         # 别名快捷入口
├── requirements-lock.txt              # 已验证精确版本锁定 (constraints 语义)
├── pyproject.toml                     # 项目打包配置
└── output/tasks/<task_slug>/          # 按课题隔离的产物与状态目录 (legacy 回退 output/auto_battery_research/)
    ├── .stage_state.json              # 工作流阶段持久化状态
    ├── stage_journals.json            # 全流程各阶段研发日志
    ├── cell_assembly/                 # Stage 3 电芯组装与挖掘产物
    ├── design_scheme.md               # Stage 4 五段式设计方案
    ├── design_scheme.json             # Stage 4 结构化设计方案 (含 geometry v2.0)
    ├── design_revisions/              # Stage 4/5 几何设计与修订快照
    ├── rag_result.json                # Stage 4 原始 RAG 结果与搜索日志
    ├── research_context.json          # 知识资产溯源快照
    ├── pinn_input_spec.json           # Stage 5 PINN 触发判定与参数契约
    ├── pinn_simulation_result.json    # Stage 5 SPM PINN 仿真结果 (命中体系时)
    ├── pinn_simulation_curve.png      # Stage 5 V-Q 放电曲线图 (命中体系时)
    ├── experiment_plan.json           # Stage 6 大模型实验方案与输入指纹
    └── final_research_report.md       # Stage 6 综合研发报告 (六节结构)
```

---

## 八、大模型配置与环境说明

AutoBatteryResearch Agent 基于通用标准 **OpenAI 兼容接口**构建，可无缝对接任何提供标准 `/v1/chat/completions` 的商业与开源模型（如 MiniMax、DeepSeek、OpenAI、Qwen、Claude、Ollama 等）。

默认配置在 [`auto_battery_research/setting.yaml`](file:///d:/llm-main/auto_battery_research/setting.yaml) 中定义，支持通过环境变量动态注入：

```yaml
# 通用 OpenAI 兼容 API 配置
openai:
  model_name: "$(OPENAI_MODEL: MiniMax-M2.7-highspeed)"
  openai_api_base: "$(OPENAI_API_BASE: https://api.minimaxi.com/v1)"
  openai_api_key: "$(OPENAI_API_KEY:)"
```

### 1. 环境变量动态覆盖 (可选)
如果需要切换至其他大模型服务或使用专属 API Key，只需在终端导出环境变量：
```bash
# 覆盖模型名称 (如 MiniMax-M2.7-highspeed, deepseek-chat, gpt-4o, qwen-max 等)
export OPENAI_MODEL="MiniMax-M2.7-highspeed"

# 覆盖 API Base 接口地址 (如 https://api.minimaxi.com/v1, https://api.deepseek.com/v1 等)
export OPENAI_API_BASE="https://api.minimaxi.com/v1"

# 覆盖 API Key
export OPENAI_API_KEY="your-api-key"
```

### 2. 安装核心依赖
```bash
pip install -e ".[all]" -c requirements-lock.txt
```

### 3. 运行离线单元测试集
```bash
pytest -m "unit or not external"
```

---

## 九、常见问题与故障排查 (FAQ)

**Q1: 为什么 Stage 5 (PINN) 有时只生成 `pinn_input_spec.json` 而没有仿真结果？**  
A: Stage 5 默认启用，但是否执行 SPM 仿真取决于触发判定 = 总开关（`pinn_trigger.enabled`）AND 方案体系匹配 `pinn/models/registry.json`（当前包含 `GrSi_NMC811`）。未匹配体系（如锂金属负极）平滑回退参数提取-only，属合法降级而非错误；也可用 `--skip-pinn` / TUI `skip 5` 手动跳过仿真。

**Q2: 如何在没有图形界面的服务器或 WSL 中查看 Web 仪表盘？**  
A: 在 WSL 或服务器终端执行 `python auto_battery_research_cli.py --web --host 0.0.0.0 --port 7865`，然后直接在 Windows 浏览器中打开 `http://127.0.0.1:7865` 即可。

**Q3: 门禁检查失败时智能体如何自愈？**  
A: Checker 会返回明确的 `failure_summary`，包含 `error_code` 和 `next_action`。`ABRAgent` 会将驳回诊断注入下一轮重试提示词形成 Self-Correction 反思回路，最多重试 3 次；在交互模式下，用户可直接依据 `next_action` 修复对应文件。
