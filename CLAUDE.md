# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AutoBatteryResearch Agent (ABRAgent) — an autonomous AI research agent for high-energy-density chemical battery R&D (lithium-metal, solid-state, high-nickel NCM). It runs a 6-stage gated workflow: literature ingestion → semantic vector indexing → material mining / cell assembly → multi-agent RAG design → PINN/P2D physics simulation (skippable) → synthesis report.

Core design principle: **"LLM in the brain seat, workflow in the referee seat"** — the `ABRAgent` (LangChain/LangGraph ReAct loop) autonomously drives **all 6 stages**, while `StageManager` + deterministic `Checkers` act as guardrails that gate stage transitions. `ABRAgent.run_stream()` is the **single execution kernel** (the old no-LLM `AutonomousLoopRunner` was removed); deterministic executors in `_execute_agent_decision_loop` run after the LLM turn as a failure fallback (inspect-then-execute-if-missing, idempotent), so the workflow also completes without an API key (offline tests rely on this).

Comments, log messages, and docs are in Chinese; match that style.

Stage 4 局部修复由 `util/design_repair.py` 可信合并器执行：Reviewer 的精确原句、原因、建议经程序定位后签发块/字段白名单；Writer 只返回带基准哈希的补丁，不能全文替换、删除或更改锁定字段。缺失标题/章节共用 `util/design_sections.py` 定义；字段引用统一在参数顶层并兼容旧嵌套。检查点保存草稿、证据、审核与三轮预算，重复调用不重跑完整 RAG；只有用户 `--rebuild-stage4` 可准备新的完整设计。详见 `docs/scheme_ledger.md`。

同段多问题使用 `util/repair_interfaces.py` 的问题接口事务：程序为唯一原句签发精确范围与接口ID；重叠范围合为联合接口。同段、共享字段或明确依赖组成事务，模型批量提交 submissions，不各自覆盖整段。程序校验后一次 `repair_verify` 批量复核问题解决情况和新矛盾；事务全部通过才合并，独立事务可部分成功，最后全量校验及一次写入。失败只重试剩余问题，审计记录接口、提交、复核及COMMITTED/REJECTED/ROLLED_BACK状态。

Stage 6 正文由确定性视图组织：摘要先给候选与结果；材料论证按依据、取舍、推断分组；规格区分工程声明、模型输入和待补数据；实验段落按原顺序编号。完整审核与交付意见去重后集中在第 2.6 节，检查器仍要求每项完整披露。报告温度显示为摄氏度，方案与实验 JSON 保留原始单位；排版变更须递增 `report_narrative.RENDER_VERSION` 并同步渲染验收，不重跑 RAG 或 Stage 5B。

第三部分为“电芯总体方案与关键匹配”：总体组合、极片与容量匹配、电解液与配套、预算与待决事项。条件性基线不能拼接成定案配方；缺少 N/P 不以厚度推断匹配成功。方案表中的 R_s / D_s 在第四部分统一声明，即使未仿真也保留，由报告检查器验收。

## Commands

```bash
# Install (editable). Extras: rag (chroma/ollama), ui (textual/gradio), dev (pytest + setuptools for offline wheel-build tests), all
pip install -e ".[all]"

# Run all offline tests (no API key / services needed; ~3 min; baseline: 105 passed, 1 deselected, 0 warnings — includes 3 wheel-packaging regression tests in test_packaging.py and 4 doctor self-check tests in test_doctor.py)
pytest -m "unit or not external"

# Run one test file / one test
pytest auto_battery_research/tests/test_checkers.py
pytest auto_battery_research/tests/test_checkers.py::test_pinn_checker_skip_logic

# By marker: unit | integration | external | slow
# "external" tests need live services (Ollama, Chroma, LLM APIs) — avoid running them by default
pytest -m "unit"

# CLI (both entry points are equivalent)
abr-cli --status
python auto_battery_research_cli.py --status

# Environment self-check (LLM key / ReAct runtime (langchain+langgraph) / Ollama+embedding model / MinerU token / assets / optional deps)
abr-cli --doctor

# Key CLI modes
abr-cli --run --goal "设计400Wh/kg高比能液态锂金属电池方案"   # full autonomous loop
abr-cli --run --with-pinn                                      # enable Stage 5 physics sim
abr-cli --check / --complete    # diagnose current stage gate / pass & advance
abr-cli --check-stage 4 / --complete-stage 4   # same, against an explicit stage id
abr-cli --detail                # deep dive into task + all-stage detail
abr-cli --tips / --status / --journal / --report
abr-cli --skip-stage 5 / --enable-stage 5
abr-cli --tui                   # Rich terminal UI
abr-cli --web --host 127.0.0.1 --port 7865   # FastAPI read-only web monitor (--web-gradio: legacy Gradio)
abr-cli --mcp                   # stdio MCP server for IDE integration
abr-cli --reset                 # reset the CURRENT goal's workflow back to Stage 1
# Run logs are persisted by default to log/<goal>.log (--no-log disables, --log-file overrides)
```

## LLM Configuration

- All LLM access goes through an **OpenAI-compatible** endpoint (`/v1/chat/completions`): MiniMax, DeepSeek, Qwen, OpenAI, Ollama, etc.
- Config lives in `auto_battery_research/setting.yaml`, which supports env-var interpolation with defaults: `$(OPENAI_API_BASE: https://api.minimaxi.com/v1)`. Env vars `OPENAI_API_KEY` / `OPENAI_API_BASE` / `OPENAI_MODEL` override. Priority: env vars > `.env` file (zero-dependency loader in `util/env_loader.py`, loaded at cli/agent/mcp entries; never overrides exported vars) > setting.yaml defaults. Per-role models (planner/extraction/writer/reviewer/checker) are separately configurable under `llm:`.
- ReAct runtime knobs: `llm.temperature` (main agent sampling), `llm.recursion_limit` (default 25; each tool round costs ~2 super-steps — do not lower it below ~12 or the agent gets cut off mid-workflow).
- Embeddings use a **local Ollama** server (`qwen3-embedding:8b` at `localhost:11434`) — Stage 2/4 retrieval requires it running; retrieval falls back to TF-IDF/BM25 when unavailable.
- Stage 1 PDF→Markdown conversion uses the **MinerU cloud API** (needs a token); pre-existing literature assets skip it entirely.
- With no/invalid API key the system runs in deterministic pipeline mode (offline fallback) and the gates still advance.

## Architecture

### Layer 1: Agent orchestration — `auto_battery_research/`

- `agent.py` — `ABRAgent`, the global master agent. Per stage: builds a stage prompt, drives the backend ReAct loop, then runs deterministic executors. Self-correction: on `Check` failure the `failure_summary` (error_code/error/next_action) is stored in `last_failure` and injected into the **next retry's prompt** (【上一轮门禁驳回】block). LangGraph `thread_id` is `abr_{goal_md5[:8]}_stage_{id}` — stable across retries within a stage so `MemorySaver` thread memory accumulates.
- `backend/langchain_backend.py` — LLM runtime. Builds the agent via `langchain.agents.create_agent` (langgraph's `create_react_agent` is deprecated, V2.0 removes it) with a fallback chain: create_agent → create_react_agent → raw `model.bind_tools`. On `GraphRecursionError` it harvests the partial message state from the checkpointer instead of discarding it. `ContextTrimmer` drops orphan `ToolMessage`s whose `AIMessage(tool_calls)` was trimmed away.
- `workflow/stage_manager.py` — `StageManager`, the 6-stage state machine. Stages are **declaratively defined** in `workflow/abr_workflow.yaml` (keys, checker classes, expected_outputs, skip flags; parsed into `stage/base_stage.py`'s `BaseStage` objects); checkers are loaded by import path string — **a failed checker load is fail-closed** (`CHECKER_LOAD_ERROR`, the gate always fails until the yaml/import is fixed), never silently substituted with an always-pass checker. On startup it auto-detects existing data assets and pre-passes completed stages; the pointer lands on the last stage when everything is done. `get_task_output_dir()` names task dirs `slug45_md5(goal)[:8]` to avoid prefix collisions, but **adopts legacy un-hashed dirs** whose `.stage_state.json` target matches (don't break existing tasks on upgrade).
- `checkers/` — one deterministic gate checker per stage (`BaseChecker` subclasses). `Check` = diagnose only (no state change); `Complete` = verify then atomically advance the stage pointer. **Path resolution is task-first**: checkers prefer `stage_manager.get_task_output_dir()` artifacts; the global `output/auto_battery_research/` fallback is gated by `BaseChecker.allow_global_legacy_fallback` — only adopted legacy task dirs (un-hashed `output/tasks/{slug45}/`, `StageManager.is_legacy_task`) or standalone checkers (no stage_manager) may read global artifacts; new hashed tasks must be self-contained and never read global. `auto_detect_existing_progress()` enforces strict sequential-prefix claiming: a downstream stage is only auto-claimed when every prior stage is terminal-OK.
- `tools/` — the agent's toolbox:
  - `domain_tools.py` — 9 stage domain tools (`Inspect*` asset probes + `Ingest/Index/Extract/RunRAGDesign/Run/Synthesize` executors). Stage 4 is converged to a single `RunRAGDesignTool` — all entry points (CLI/TUI/Web/MCP/Agent) go through the one `run_rag_design` service (Planner/Retrieval/Writer/Reviewer live pipeline-internally in `src/lmllm/RAG/`); nothing else may write `design_scheme.*`.
  - `stage_tools.py` — workflow guardrail tools (`CurrentTips`, `Status`, `Check`, `Complete`, `SetStageJournal`, `SkipStage`, `EnableStage`). They read the module-level singleton wired via `set_stage_manager()`; the singleton + per-goal cache are guarded by `_MANAGER_LOCK` (Gradio handlers run threaded). The web UI queues events with `default_concurrency_limit=1` — do not raise it, tool runtime state is process-global. **Always fetch managers via `get_stage_manager_for_goal(goal)`** (reuses the global singleton or a per-goal cache) — constructing `StageManager` directly triggers the full checker cascade + state double-write and can race the main flow. Its `resolve_effective_goal(target, active)` folds synonymous goals (case/whitespace/punctuation, ≥6-char substring, same `Wh/kg` figure) back to the active goal — all four domain tools resolve their goal through it, and synonymous requests reuse the global singleton instead of spawning a per-goal manager.
  - `file_tools.py` — workspace-sandboxed file utilities; `validate_workspace_path()` rejects any path resolving outside the repo root (path-traversal guard).
  - `workflow_actions.py` — the bridge from tools to real work; fail-closed: when assets are missing it actually runs the pipelines (imports `step_mineru/step_merge/step_classify` from `auto_battery_research.pipeline.incremental`, subprocesses `miner/paragraph_metadata_pipeline_v5_qwen.py --incremental`, imports `run_tok2000` via `auto_battery_research.mining`). `_merged_literature_dirs()` resolves merged-literature dirs as: canonical `paths.papers_merged_dir` (`papers/merged`) + legacy `papers/text_merged` where the old cleaning pipeline's data lives — count both, don't hardcode either.
  - `rag_adapter.py` — bridges Stage 4 to the RAG engine in `src/lmllm/RAG/` and converts results into the Stage 4 output contract. Validates fail-closed **before** writing, stamps `review_status: APPROVED|REJECTED` into `design_scheme.json` (consumed by `agent.py`'s scheme-valid precheck), always writes the artifacts (REJECTED output is kept for diagnosis), and pins a `provenance` block (corpus manifest hash / vector-index fingerprint / `RULES_VERSION` from `relation_engine.py`) plus `research_context.json` for reproducibility.
  - `mcp_server.py` — MCP stdio server.
- `mining/`, `pipeline/`, `rag/`, `simulation/` — unified import facades re-exporting the legacy implementations (`agent/*`, `src/lmllm/RAG/*`, `pinn/*`); prefer these over importing legacy locations directly. Heavy deps (chromadb/torch) stay guarded inside the source modules.
- `tui/`, `web/` — Textual TUI; web: FastAPI read-only monitor (`web/server.py`, main `--web` entry, parses task state from disk without constructing StageManager) + legacy Gradio dashboard (`web/app.py`, `--web-gradio`).

### Layer 2: RAG engine — `src/lmllm/RAG/`

Multi-agent material-screening RAG: **Planner → Retrieval → Writer → Reviewer** (`agents.py`, orchestrated by `rag_pipeline.py`). Key pieces: `multi_retrieval.py` (hybrid Chroma + BM25/TF-IDF), `relation_engine.py` (thermodynamic hard constraints **C1–C8** that every design scheme must pass), `structured_output.py`, `prompts.py` (central prompt registry).

### Layer 3: Legacy stage scripts (subprocess-scheduled; `agent/`, `pinn/`, `src/lmllm/RAG/` are also imported via the Layer 1 facades)

- `preprocessing/` — PDF → DOI extraction → MinerU Markdown → merge (Stage 1)
- `miner/` — paragraph semantic labeling + Chroma vectorization (Stage 2); `miner/paragraph_metadata_pipeline_v5_qwen.py --incremental`
- `agent/` — 2000-token material mining, normalization, cell entity assembly, ML dataset export (Stage 3); `agent/pipeline_tok2000.py`
- `pinn/` — SPM PINN simulation (`spm_runner.py` + `models/` registry) and the Stage 5 input-spec contract (`input_spec.py`)

Entry wrappers: `pipeline_incremental.py` (legacy pipeline wrapper; core in `auto_battery_research.pipeline.incremental`), `auto_battery_research_cli.py` / `abr_cli.py` (CLI), `scripts/legacy_rag/chat_rag_v3_optimized*.py` / `chat_rag_v5_demo.py` (archived standalone RAG chat demos).

### Data flow / paths

All paths are configured in `auto_battery_research/setting.yaml` under `paths:` (relative to repo root). **Artifacts are task-scoped: each goal gets `output/tasks/<sanitized-goal>/`** and multi-goal runs never overwrite each other. The global `output/auto_battery_research/` is a read-only fallback for legacy artifacts only — do not add new writes there.

```
papers/pdf → papers/merged (canonical; legacy data in papers/text_merged) → database/type/{battery system}/{cathode,anode,electrolyte}
→ miner/json/metadata/meta_merged.json + miner/chroma/paragraphs_q (Chroma)
→ output/tasks/<goal>/
     .stage_state.json · stage_journals.json/.md · cell_assembly/
     design_scheme.md/.json · rag_result.json · pinn_input_spec.json · final_research_report.md
```

## Important Behaviors

- Stage 5 合法几何修订与择优恢复必须同步 `scheme_parameters` 中同路径的重复声明（`align_geometry_parameters`），旧原句/来源留在 geometry_parameter_history，不能把旧提取值当成第二份冻结几何；用户锁定和其他字段冲突仍由方案表提交校验。旧优化检查点若三次均因“补充参数与几何契约冲突”被拒且从未产生真实修订，可一次性归档恢复预算；已执行修订的预算不重置。

- Stage 5 的有限 PINN 优化已移入 `util/pinn_optimization.py`，由收尾执行器主动运行，不依赖外层 ReAct。收敛不等于达标：仅匹配、开启且结果身份有效时比较目标容差；未达标最多按 max_geometry_revisions 提案/修订/重跑，检查点持久化预算，最后统一择优。仅调整未锁定的可设计几何，不修改 D_s 或材料；模型不可用时采用范围内的有限工程候选。实验推荐使用 `util/experiment_repair.py` 逐字段、逐段校验，一次首稿加一次局部补丁，保留已通过内容；修复失败只把错误单元标为待确定。引用对象可提取真实 passage_id 再核验，未知引用仍拒绝。

- 报告优先为默认模式（`runtime_options.report_first: true`）。新的 `delivery_contract=1` 将报告交付类别与学术/物理验证状态分开：Stage 4 可在附条件或诊断状态推进，Stage 5 匹配失败或物理失败不阻断实验建议，Stage 6 必须如实呈现限制。下文严格几何与仿真门禁仍约束真正的仿真输入和旧契约，不再成为所有新课题出报告的前置条件。禁止为了通过门禁虚填 D_s、把致密锂箔更换为多孔宿主，或把失效仿真数值写成性能结论。`util/report_readiness.py` 维护交付状态与披露正文；报告指纹及终审都校验该状态。

- 课题身份比较使用 NFKC（含化学式下标数字）、空格、常见标点及连接号归一化，但保留当前课题原文和已有目录哈希。运行中领域工具通过 `bind_active_goal` 固定当前管理器课题，模型参数不能隐式切换目录；明确切换必须走正式工作流入口。RAG 设计请求独立于 `target` 保存为 `design_request`，执行服务从选定管理器取得目录。既有同义目录不自动搬迁或覆盖。

- Stage 5 v2 分为物理与实验：`util/stage5_execution.py` 执行 `finalize_stage5`，物理仅匹配且开启时运行，实验始终尝试；`checkers/stage5_checker.py` 组合验收。`--skip-pinn`/兼容跳过操作仅关闭物理，不能跳过实验。旧 SKIPPED 或缺实验推荐的完成态重开 Stage 5。Stage 4 不生成实验方案；5B 复用文献并最多三个本地工艺查询、八个补充段落、二十段/一万六千字符输入，一次主生成加一次修正，六千 token，无总耗时限制。重点硬校验与当前方案表重叠字段；非重叠工艺/预算假设允许工程建议并提示，不回填物性或 PINN 输入。检查点保存 `stage5_execution.json`，证据保存 `experiment_evidence.json`。`FinalizeStage5` 和 `--regenerate-experiment-plan` 支持只重试实验；Stage 6 只读取渲染。

- 实验推荐契约 v3 由 `util/experiment_design.py` 维护，来源阶段为 5，参数引用与独立工程假设分别保存。检索补充证据不改写材料方案，失败保留有效推荐与诊断。Stage 5 组合门禁要求物理合法终态和有效实验推荐，Stage 6 只做参数对齐的文字投影。

- 方案表与报告表达分离：`util/report_narrative.py` 专门生成方案正文、实验步骤、参数对照与缺口建议的中文段落，内部 `scheme_table.md` 仍保留表格。报告不输出方案表或候选 JSON；缺口候选支持无数值的获取方法及采用判据，离线时也生成确定性获取路径。报告与实验计划指纹包含渲染版本，门禁重算正文防篡改。候选仍单独归档，不能自动成为当前方案。

- 统一课题方案表：`util/scheme_ledger.py` 维护任务目录的 `scheme_ledger.json`，首次解析课题目录建立，中文视图为 `scheme_table.md`，修订快照为 `scheme_history/`。用户更新默认锁定；Agent 只能更新未锁字段，提交必须匹配基准版本。Stage 4 的设计契约是表的派生视图，几何修订及择优回滚也提交该表；各门禁校验当前指纹。旧任务只导入结构化字段，不从旧正文补参数。新的报告路径由程序渲染方案表和派生计算，模型仅为缺失字段生成单独标记的结构化候选，不再转录初始方案数值；无方案表的历史独立调用保留旧报告路径。CLI 维护方式及规范单位见 `docs/scheme_ledger.md`。

- 六节报告由 `util/report_analysis.py` 补充材料论证、逐轮仿真比较及参考来源；当前参数来自 `report_contract.py`。第五部分由 Stage 5B 的 `experiment_plan.json` 通过 `util/report_recipe_llm.py` 只读投影为文字，渲染记录存 `experiment_report.json`，不在报告阶段另调用模型生成实验路线。Stage 6 对六节布局、推荐主体、参数绑定、仿真说明与归档的一致性验收；不存在有效推荐时明确失败，不用空缺列表代替完整方案。
- 第二部分通过 `util/material_arguments.py` 从 Stage 4 的 `final_answer` / `evidence` 恢复具体选材论证、候选比较及机理推断，保留已归档引用与未解决审核意见；当前材料与改性规格从方案表读取，过滤旧几何/工艺，候选表转为文字且不保留主观评分。不增加检索或模型调用，不以固定通用优点代替真实论证；论证和证据变动纳入报告输入指纹，门禁重算第二部分。

- 最终报告使用 `util/report_contract.py` 的最终设计/仿真视图，不嵌入初始 Markdown 数值；实验章节直接渲染有效几何与已声明条件，缺失工艺参数由 Stage 5B 给工程推荐，不写成实测或模型物性。报告指纹与关键块在 Stage 6 门禁重算验收。PINN 结果明确携带 `energy_accounting`，电芯比能量是材料级值乘固定系数，不是逐项质量核算或物理上限证明。
- N/P 可选且不要求模型生成；缺失时不校准两电极厚度。Stage 5 的 Check/Complete 共用幂等择优收尾。阶段完成后不再兜底重复仿真；阶段日志合并保留详细发现。
- RAG Writer/Reviewer 共享证据覆盖，Reviewer 仅返回简短判定。`review_completed` 表示有效审核响应，`review_passed` 表示无未解决意见且置信度合格；预算耗尽但仍有内容意见时仅作为 `REVIEWED_WITH_ISSUES` 待验证候选推进，保留真实低置信度和意见到报告，不能称为 APPROVED。响应失败/结构不全/硬规则失败仍拒绝。LLM 响应记录 finish_reason/推理 token；OpenAI-compatible 空白或截断最多扩大预算重试一次，仍失败则拒绝。工作流 RAG 预算来自 LLM_MAX_TOKENS 或 llm.max_tokens，重试上限 llm.output_max_tokens。

- 新几何主通道由 `simulation/geometry_contract.py` 定义：Stage 4 在 Writer 前独立生成结构化候选，Adapter 验收后写入 `contract_version=2.0` 的兼容 v1.1 数值块；新任务不依赖正文正则抽取。R_s 是可设计半径；D_s 修改需任务证据原文的材料、数值/单位、温度与恒定参数适用性验证。`ReviseGeometry` 对新契约要求基准版本/哈希，逐轮保存设计快照；历史正则产物保持兼容。离线缺结构化候选时明确拒绝，不编造几何。

- **Stage 5 (PINN) is enabled by default** (`skip_pinn_default: false`). It always extracts the selected materials' physical parameters to `pinn_input_spec.json` (extraction also runs on the skip fast path; fields that cannot be extracted stay `null` — no default-table backfill). When the scheme's (cathode, anode) matches a trained PINN system in `pinn/models/registry.json` and the `pinn_trigger.enabled` kill-switch is on, it runs the SPM PINN discharge simulation — pure-numpy `pinn/spm_runner.py`, npz weights exported from TF SavedModels by `scripts/export_pinn_spm_weights.py` (run in conda env `pinn210`), golden-IO equivalence locked by tests — and writes `pinn_simulation_result.json`. The simulation is **spec-driven**: R_s / D_s / thickness / porosity / C-rate are explicit external inputs taken from the spec (missing fields fall back to the system's trained params), and each system declares per-parameter valid ranges (`input_ranges` in the registry) — out-of-range (OUT_OF_RANGE) or out-of-envelope (OUT_OF_ENVELOPE) inputs are intercepted before the network runs. Converged runs also render `pinn_simulation_curve.png` (V-Q chart) into the task dir and the Stage 6 report embeds it. Stage 4's `design_scheme.json` carries an explicit complete geometry declaration block (`scheme.geometry`, contract v1.1: per-electrode L_um/porosity/R_s_um/D_s_m2s + N/P + voltage window, every field provenance-tagged `stage4_recipe`, `stage5_revision` (Stage 5 feedback revision) or `params_baseline`; normalized in rag_adapter via `complete_geometry_block`, declaration table appended to `design_scheme.md`). It flows into the spec as explicit inputs, so the PINN simulates the declared design point and the report shows both the declaration (section 3) and the effective geometry (section 4). Unmatched systems fall back to extraction-only. Stage 5 is a **hard gate** (`strict: true`): a triggered run (matched system + kill-switch on) must produce a `pinn_simulation_result.json` with `status: CONVERGED` whose `pinn.spec_hash` matches the spec's `cell_spec_hash` — non-converged runs are persisted as failure payloads (overwriting any stale success, PNG invalidated) and `run_pinn_simulation` returns `success: false`; unmatched/kill-switch-off runs legitimately pass as extraction-only. The Stage 6 report embeds numbers/geometry/curve only when the same identity guard (`_pinn_result_identity_ok`) passes. The old PyBaMM P2D path (`p2d_runner.py`/`cell_spec_schema.py`) was removed — `pinn/output/` legacy artifacts and the checker's legacy branch are gone; the web demo TAB now runs the PINN. Stage 4's `RAGDesignChecker` hard-gates the geometry declaration (`runtime_options.geometry_gate: enforce|warn`, default enforce): `scheme.geometry` (contract v1.1) must explicitly carry both electrodes' thickness/porosity/R_s/D_s — 8 hard fields — with `stage4_recipe` or `stage5_revision` provenance (`GEOMETRY_BLOCK_MISSING` / `GEOMETRY_DECLARATION_INCOMPLETE` / `GEOMETRY_VALUE_ILLEGAL`); all other fields are optional — declared values are range-checked against the registry, missing ones fall back to system defaults. Stage 5 closes the feedback loop: `RunPhysicsSimulation` returns a deterministic target verdict (`key_findings.target` — energy goal parsed from the goal text, tolerance `runtime_options.target_energy_tolerance_pct` default ±5%, `met` flag) plus the effective-geometry echo; when not met the agent calls `ReviseGeometry` (8 revisable fields across both electrodes, registry-range validated fail-closed, budget `runtime_options.max_geometry_revisions` default 3, provenance `stage5_revision`, revision log rendered into `design_scheme.md` and `scheme.geometry_revisions`) and re-runs the simulation. Every round is archived (`pinn_simulation_history.json` index + `pinn_simulation_round_{k}.result/.spec.json` sidecars; cleared on NO_MATCH/kill-switch-off). At `complete_stage` a deterministic finisher restores the best CONVERGED round by `energy_wh_kg` if the target is still unmet (sidecar result/spec restore + geometry snapshot rollback + selection audit entry; no-op when any round met the target, no numeric target exists, or the last round is already the best). The checker additionally guards `pinn.scheme_hash` against the current scheme projection (`util.hashing.stage5_scheme_projection`, which includes geometry — `PINN_SCHEME_STALE`), closing the "geometry revised without re-simulation" window. `complete_geometry_block` is idempotent (re-normalization never overwrites declared values with baseline), and spec-side illegal geometry values fail closed (`ValueError` from `build_cell_spec`) instead of silently reverting to the trained baseline.
- Workflow state is sticky **per goal**: `output/tasks/<goal>/.stage_state.json` is reused across runs. After changing stage deliverables/code, use `abr-cli --reset` (or delete that goal's state file) to force re-evaluation from Stage 1. **Goal identity is normalized, not exact-matched**: `resolve_effective_goal()` treats case/whitespace/punctuation variants, ≥6-char substrings, and identical `Wh/kg` figures of the active goal as the same goal so LLM wording drift cannot fork a new task dir. The default goal has one source of truth — `util/constants.py:DEFAULT_GOAL`; changing it orphans existing task state dirs, and goal strings must never be hardcoded elsewhere (the 400Wh default was deliberately scrubbed from tools/checkers).
- **LLM full-trace recording is opt-in** (`runtime_options.llm_trace: false` by default; enable via `abr-cli --trace`, setting.yaml, or env `LLM_TRACE` — priority CLI > setting > env). When on, `util/llm_trace.py`'s process-singleton recorder appends one JSONL event per LLM call (full input/output messages incl. tool_calls & ToolMessage observations, token usage, duration, backend/model, trace_tag for the RAG role) plus `prompt_assembly` events, into the goal's `llm_trace/trace_<session>.jsonl` + `trace_meta.json` (no-goal processes fall back to `log/llm_trace/`). Hook points: `ABRLangChainBackend.invoke` (ReAct brain — offline short-circuit recorded as `backend="offline"`), `src/lmllm/RAG/llm_client.chat` (trace_tag from callers: planner/writer/writer_revise/reviewer/report_recipe/baseline), `backend/llm_client.generate` (TUI Q&A / MCP consult), `RAG/extractor._call`, and legacy mining factories (`miner/config.create_llm`, `agent/pipeline._create_llm`) via `LLMTraceCallbackHandler`. Recording is fail-silent by design — a broken recorder must never affect the run.
- Strict mode (`runtime_options.strict_mode: true`) makes hard checkers fail the stage on any error; `max_retries_per_stage: 3` bounds self-healing loops.
- Stage rule from the mission system prompt: never fabricate data — "有则提取、无则留空、禁止编造" (extract what exists, leave blank otherwise, never invent). Design schemes must pass all C1–C8 constraints in `relation_engine.py`.
- Windows is the primary dev platform — entry points reconfigure stdout/stderr to UTF-8; keep that pattern when adding new entry scripts.
- Unit tests must stay offline: `tests/test_abr_agent.py` injects `OFFLINE_CONFIG` (dummy key) so no live API calls happen. Anything needing live services gets the `external` marker.
- When adding a new checker or stage, register it in `workflow/abr_workflow.yaml` (checker classes are resolved by dotted import path) — `StageManager` discovers everything from there.
