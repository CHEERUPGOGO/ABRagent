# ABRAgent 工具箱 (tools/)

本目录是 AutoBatteryResearch Agent (ABRAgent) 的执行层。设计原则为
**"LLM 坐主脑位，工作流坐裁判位"**：智能体通过这里暴露的工具感知资产、执行流水线、
接受门禁裁决；`StageManager` + 确定性 Checkers 负责阶段推进的最终把关。

```
ABRAgent (ReAct 主循环)
   │  全局维护 15 个 LangChain BaseTool (9 领域 + 6 护栏)
   │  运行时由 backend.bind_stage_tools 按 Stage 1~6 物理隔离与动态裁剪
   ▼
┌────────────────┐    真正干活     ┌──────────────────────┐
│  domain_tools  │ ──────────────▶ │  workflow_actions    │ ──▶ legacy 流水线 / src/lmllm/RAG / pinn
│  stage_tools   │                │  (fail-closed 桥接)  │
└────────────────┘                └──────────────────────┘
   ▲ CLI / Web / TUI / MCP 直接调用函数层 (不经过 LLM)
```

## 模块文件总览

| 文件 | 角色 | 说明 |
|:---|:---|:---|
| `domain_tools.py` | Agent 领域工具集 | 9 个 Stage 1~6 业务工具 (LangChain `BaseTool`)，入参动态绑定当前活跃课题 |
| `stage_tools.py` | 工作流护栏工具集 | 6 个门禁/状态/日志工具 + 供 CLI/MCP/Web 直接调用的函数层 |
| `workflow_actions.py` | 干活桥接层 | 工具 → 真实流水线的唯一入口，fail-closed：资产缺失时实际执行流水线而非报空 |
| `rag_adapter.py` | Stage 4 适配器 | 桥接 `src/lmllm/RAG` 引擎，转换并校验 Stage 4 输出契约 (design_scheme + provenance 溯源) |
| `file_tools.py` | 安全文件工具 | 带 Workspace 路径沙箱的读/写/替换，杜绝路径穿越 |
| `knowledge_retriever.py` | 轻量知识检索 | 跨语言化学概念映射 + 5471+ 真实学术段落的关键词检索 |
| `mcp_server.py` | MCP stdio 服务 | 向 IDE 暴露 14 个工具 (`abr-cli --mcp`) |
| `__init__.py` | 包导出 | 函数层工具 + MCP 启动入口 |

---

## 一、领域工具集 (domain_tools.py · 9 个)

按 6 阶段工作流划分，每阶段包含"感知探测 (Inspect*)" 与 "执行落地"两类。
`agent.py` 启动时通过 `STAGE_ALLOWED_DOMAIN_TOOLS` 为各阶段独立裁剪并编译隔离的 ReAct Agent，**杜绝跨阶段抢跑与工具幻觉**。

| 工具名 | 阶段 | 功能 | 关键参数 |
|:---|:---|:---|:---|
| `InspectLiteratureAssets` | Stage 1 | 探测已解析分类的文献资产：各组件 (正极/负极/电解液) Markdown 数量与状态 | `database_dir` (默认 `database/type`) |
| `IngestLiteraturePapers` | Stage 1 | 增量文献解析流水线：PDF → DOI 提取 → MinerU Markdown → 分类入库 | `input_pdf_dir` (默认 `papers/pdf`), `max_files` (默认 5) |
| `InspectVectorDB` | Stage 2 | 探测 Chroma 向量库与段落标注元数据：向量数、6 类语义标签分布 | `chroma_dir` (默认 `miner/chroma/paragraphs_q`) |
| `IndexSemanticVectors` | Stage 2 | 段落 6 类语义打标 + Qwen3-Embedding 向量化持久化入库 | `max_papers` (默认 5), `incremental` (默认 True) |
| `InspectCellEntities` | Stage 3 | 探测已挖掘材料数据与已组装电芯实体：电芯数、正负极/电解液三元组分布 | `cell_dir` (留空由 StageManager 解析) |
| `ExtractAndAssembleCells` | Stage 3 | 材料微观表征挖掘 + 三层归一化 + 电芯实体组装流水线 | `sample_limit` (默认 10), `target_query` (留空动态绑定当前活跃课题) |
| `RunRAGDesign` | Stage 4 | **Stage 4 唯一落盘入口**：单链路 Planner → Retrieval → Writer → Reviewer + RelationEngine C1–C8 硬约束核算，产出 `design_scheme.md/.json`、`rag_result.json` | `target_goal` (留空动态绑定当前活跃课题), `design_query` |
| `RunPhysicsSimulation` | Stage 5 | PyBaMM Newman P2D / PINN 代理仿真：充放电曲线与能量密度标定 (默认跳过) | `target_goal` (留空动态绑定当前活跃课题), `current_rate` (默认 "0.2C") |
| `SynthesizeResearchReport` | Stage 6 | 汇总全链路产物编译最终综合研报 `final_research_report.md` | `target_goal` (留空动态绑定当前活跃课题) |

> ⚠️ **Stage 4 收敛与抢跑防御**：
> 1. Stage 4 业务入口收敛为 `RunRAGDesign` 单服务，所有入口共用 `workflow_actions.run_rag_design` 唯一落盘链路；
> 2. 大模型在 Stage 4 运行时，Stage 6 研报工具被物理隔离隐身，彻底阻断大模型在方案刚生成完毕时跨阶段调用 `SynthesizeResearchReport` 的抢跑幻觉。

## 二、工作流护栏工具集 (stage_tools.py · 6 个)

裁决与状态感知类工具。`Check` 只诊断不推进，`Complete` 才原子推进阶段指针。

| 工具名 | 功能 | 关键参数 |
|:---|:---|:---|
| `CurrentTips` | 获取当前阶段任务指引、输入规范与验收指标 (每轮决策前应优先调用) | 无 |
| `Status` | 获取 6 阶段全局状态矩阵 (PASSED/IN_PROGRESS/PENDING/SKIPPED) | 无 |
| `Check` | 确定性门禁自检：返回 passed / error_code / failure_summary / next_action，**不改变状态** | `stage_id` (默认当前阶段) |
| `Complete` | 终审推进：Check 全部通过后原子推进工作流指针，未过则拒绝 | `stage_id` (默认当前阶段) |
| `SetStageJournal` | 记录当前阶段科研心得与交付物路径 | `stage_id`, `notes` |
| `AllStageJournal` | 查看全部历史阶段研发日志与数据溯源 | 无 |

## 三、函数层工具 (stage_tools.py 模块函数)

CLI / Web / TUI / MCP 不经过 LLM 直接调用的函数，与上述 BaseTool 共用同一实现：

`tool_get_role_info` · `tool_get_status` · `tool_get_detail` · `tool_get_current_tips` ·
`tool_check_stage` · `tool_complete_stage` · `tool_set_stage_journal` ·
`tool_get_all_stage_journal` · `tool_skip_stage` · `tool_enable_stage` · `tool_run_stage_task`

其中 `tool_skip_stage` / `tool_enable_stage` / `tool_run_stage_task` **不注册给 Agent**
(跳阶段/重跑属运维操作，对应 `abr-cli --skip-stage 5` / `--enable-stage 5`)。

## 四、workflow_actions.py 干活入口

领域工具的实际执行体，fail-closed —— 资产缺失时真实执行流水线：

| 函数 | 阶段 | 实际驱动 |
|:---|:---|:---|
| `run_literature_ingestion` | Stage 1 | `step_mineru/step_merge/step_classify` (pipeline_incremental) |
| `run_vector_indexing` | Stage 2 | 子进程 `paragraph_metadata_pipeline_v5_qwen.py --incremental` |
| `run_data_mining` | Stage 3 | `agent/pipeline_tok2000.run` |
| `run_rag_design` | Stage 4 | 经 `rag_adapter` → `src/lmllm/RAG` 引擎 |
| `run_pinn_simulation` | Stage 5 | `pinn/p2d_runner` (默认 skip) |
| `run_synthesis_report` | Stage 6 | 模板 + LLM 动态生成第 5 章 → 只写规范名 `final_research_report.md` |

别名兼容：`generate_synthesis_report` / `run_generate_synthesis_report` 均指向 `run_synthesis_report`。

## 五、MCP 工具面 (mcp_server.py · 14 个)

`abr-cli --mcp` 启动 stdio 服务，经 `dispatch_tool_call` 分发：

- **状态感知**: `RoleInfo` · `Status` · `Detail` · `CurrentTips`
- **门禁裁决**: `Check` · `Complete`
- **日志**: `SetStageJournal` · `AllStageJournal`
- **运维**: `SkipStage` · `EnableStage` · `RunStageTask`
- **沙箱文件**: `ReadTextFile` · `EditTextFile` · `ReplaceStringInFile`

## 六、关键行为约定

- **管理器获取**：一律通过 `get_stage_manager_for_goal(goal)` 复用全局单例/按课题缓存
  (受 `_MANAGER_LOCK` 保护，Gradio 多线程安全)；直接构造 `StageManager` 会触发
  checker 级联与状态双写，可能与主流程竞争。
- **路径解析 task-first**：产物只落 `output/tasks/<goal>/`；全局
  `output/auto_battery_research/` 仅为已收养的 legacy 课题只读回退，禁止新写入。
- **文件工具沙箱**：`file_tools` 所有路径先过 `validate_workspace_path` 校验
  严格位于仓库根内，拦截 `../` 路径穿越。
- **阶段工具物理隔离 (防抢跑机制)**：`ABRAgent` 运行时在各 Stage 仅向大模型暴露当前阶段授权的领域工具与通用治理工具 (`Check`, `Complete`, `CurrentTips`, `Status`, `SetStageJournal`, `RoleInfo`)。例如在 Stage 4 中，Stage 6 研报工具被物理剔除，从认知层面彻底杜绝大模型“提前把后续阶段工具一并调用”的越界幻觉。
- **课题目标动态绑定 (零硬编码)**：所有领域工具 (`SynthesizeResearchReport`, `RunPhysicsSimulation`, `ExtractAndAssembleCells` 等) 的课题入参默认值均为 `""`。执行时自动动态回退至 `get_stage_manager().target_goal`，杜绝任何硬编码（如 400Wh）导致的跨课题参数污染。
- **离线可用**：无有效 API Key 时工具链走确定性回退 (规则模板/TF-IDF 检索)，
  门禁仍可推进，单测 (`tests/`) 全程离线。
