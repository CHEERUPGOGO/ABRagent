# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AutoBatteryResearch Agent (ABRAgent) — an autonomous AI research agent for high-energy-density chemical battery R&D (lithium-metal, solid-state, high-nickel NCM). It runs a 6-stage gated workflow: literature ingestion → semantic vector indexing → material mining / cell assembly → multi-agent RAG design → PINN/P2D physics simulation (skippable) → synthesis report.

Core design principle: **"LLM in the brain seat, workflow in the referee seat"** — the `ABRAgent` (LangChain/LangGraph ReAct loop) autonomously drives **all 6 stages**, while `StageManager` + deterministic `Checkers` act as guardrails that gate stage transitions. `ABRAgent.run_stream()` is the **single execution kernel** (the old no-LLM `AutonomousLoopRunner` was removed); deterministic executors in `_execute_agent_decision_loop` run after the LLM turn as a failure fallback (inspect-then-execute-if-missing, idempotent), so the workflow also completes without an API key (offline tests rely on this).

Comments, log messages, and docs are in Chinese; match that style.

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

- 六节报告由 `util/report_analysis.py` 补充材料论证摘录、文献制备参考、逐轮仿真比较和实验验证路线；当前参数仍来自 `report_contract.py`。原始路径集中在第六节，来源按 DOI/文件归并。Stage 6 对六节布局及分析内容与归档的一致性进行验收。第五部分实验方案由 `util/report_recipe_llm.py` 让大模型综合完整 `design_scheme.md`、最终设计、证据原文与仿真结果生成，程序侧验收引用真实性与参数一致性，记录存 `experiment_plan.json` 并按输入指纹防陈旧；LLM 不可用或验收不过时回退确定性渲染。

- 最终报告使用 `util/report_contract.py` 的最终设计/仿真视图，不嵌入初始 Markdown 数值；实验章节直接渲染有效几何与已声明条件，缺失工艺参数留空，不再回退固定数值配方。报告指纹与关键块在 Stage 6 门禁重算验收。PINN 结果明确携带 `energy_accounting`，电芯比能量是材料级值乘固定系数，不是逐项质量核算或物理上限证明。
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
