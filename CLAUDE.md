# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AutoBatteryResearch Agent (ABRAgent) — an autonomous AI research agent for high-energy-density chemical battery R&D (lithium-metal, solid-state, high-nickel NCM). It runs a 6-stage gated workflow: literature ingestion → semantic vector indexing → material mining / cell assembly → multi-agent RAG design → SPM PINN physics simulation (enabled by default; skippable) → synthesis report.

Core design principle: **"LLM in the brain seat, workflow in the referee seat"** — the `ABRAgent` (LangChain/LangGraph ReAct loop) autonomously drives **all 6 stages**, while `StageManager` + deterministic `Checkers` act as guardrails that gate stage transitions. `ABRAgent.run_stream()` is the **single execution kernel** (the old no-LLM `AutonomousLoopRunner` was removed); deterministic executors in `_execute_agent_decision_loop` run after the LLM turn as a failure fallback (inspect-then-execute-if-missing, idempotent) for Stages 1–3, 5, and 6, while Stage 4 RAG fails closed offline when no LLM is available (offline tests mock/stub RAG or verify fail-closed rejection).

Comments, log messages, and docs are in Chinese; match that style.

## Commands

```bash
# Install (editable). Extras: mcp, mp (requires Python 3.11+), rag (chroma/ollama), ui (textual/gradio), dev (pytest + setuptools for offline wheel-build tests), all
pip install -e ".[all]"

# Run all offline tests (no API key / services needed — includes wheel-packaging regression tests in test_packaging.py and doctor self-check tests in test_doctor.py)
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
abr-cli --run --goal "设计400Wh/kg高比能液态锂金属电池方案"   # full autonomous loop (Stage 5 PINN enabled by default)
abr-cli --run --skip-pinn                                      # skip Stage 5 physics sim (still extracts pinn_input_spec.json)
abr-cli --run --with-pinn                                      # force-enable Stage 5 physics sim
abr-cli --check / --complete    # diagnose current stage gate / pass & advance
abr-cli --check-stage 4 / --complete-stage 4   # same, against an explicit stage id
abr-cli --detail                # deep dive into task + all-stage detail
abr-cli --tips / --status / --journal / --report
abr-cli --skip-stage 5 / --enable-stage 5
abr-cli --tui                   # Textual terminal UI
abr-cli --web --host 127.0.0.1 --port 7865   # FastAPI read-only web monitor (--web-gradio: legacy Gradio)
abr-cli --mcp                   # 14-tool stdio MCP server for IDE integration
auto-battery-research-mcp       # 2-tool domain-expert MCP server (streamable-http / stdio)
abr-cli --reset                 # reset the CURRENT goal's workflow back to Stage 1
# Run logs are persisted by default to log/<goal>.log (--no-log disables, --log-file overrides)
```

## LLM Configuration

- All LLM access goes through an **OpenAI-compatible** endpoint (`/v1/chat/completions`): MiniMax, DeepSeek, Qwen, OpenAI, Ollama, etc.
- Config lives in `auto_battery_research/setting.yaml`, which supports env-var interpolation with defaults: `$(OPENAI_API_BASE: https://api.minimaxi.com/v1)`. Env vars `OPENAI_API_KEY` / `OPENAI_API_BASE` / `OPENAI_MODEL` override. Priority: env vars > `.env` file (zero-dependency loader in `util/env_loader.py`, loaded at cli/agent/mcp entries; never overrides exported vars) > setting.yaml defaults. Per-role models (planner/extraction/writer/reviewer/checker) are separately configurable under `llm:`.
- ReAct runtime knobs: `llm.temperature` (main agent sampling), `llm.recursion_limit` (default 25; each tool round costs ~2 super-steps — do not lower it below ~12 or the agent gets cut off mid-workflow).
- Embeddings use a **local Ollama** server (`qwen3-embedding:8b` at `localhost:11434`) — Stage 2/4 retrieval uses Chroma + BM25 + RRF via `MultiRetrieval` and falls back to local BM25 lexical retrieval when unavailable (`RETRIEVAL_MODE="tfidf"` is the legacy config key).
- Stage 1 PDF→Markdown conversion uses the **MinerU cloud API** (needs a token); pre-existing literature assets skip it entirely.
- With no/invalid API key, Stages 1–3, 5, and 6 still execute deterministically, while **Stage 4 RAG fails closed** (returns empty draft / low-confidence `REJECTED`, never fabricates a fallback recipe, and is blocked by `RAGDesignChecker`).

## Architecture

### Layer 1: Agent orchestration — `auto_battery_research/`

- `agent.py` — `ABRAgent`, the global master agent. Per stage: builds a stage prompt, drives the backend ReAct loop, then runs deterministic executors. Self-correction: on `Check` failure the `failure_summary` (error_code/error/next_action) is stored in `last_failure` and injected into the **next retry's prompt** (【上一轮门禁驳回】block). LangGraph `thread_id` is `abr_{goal_md5[:8]}_stage_{id}` — stable across retries within a stage so `MemorySaver` thread memory accumulates.
- `backend/langchain_backend.py` — LLM runtime. Builds the agent via `langchain.agents.create_agent` (langgraph's `create_react_agent` is deprecated, V2.0 removes it) with a fallback chain: create_agent → create_react_agent → raw `model.bind_tools`. On `GraphRecursionError` it harvests the partial message state from the checkpointer instead of discarding it. `ContextTrimmer` drops orphan `ToolMessage`s whose `AIMessage(tool_calls)` was trimmed away.
- `workflow/stage_manager.py` — `StageManager`, the 6-stage state machine. Stages are **declaratively defined** in `workflow/abr_workflow.yaml` (keys, checker classes, expected_outputs, skip flags; parsed into `stage/base_stage.py`'s `BaseStage` objects); checkers are loaded by import path string — **a failed checker load is fail-closed** (`CHECKER_LOAD_ERROR`, the gate always fails until the yaml/import is fixed), never silently substituted with an always-pass checker. On startup it auto-detects existing data assets and pre-passes completed stages; the pointer lands on the last stage when everything is done. `get_task_output_dir()` names task dirs `slug45_md5(goal)[:8]` to avoid prefix collisions, but **adopts legacy un-hashed dirs** whose `.stage_state.json` target matches (don't break existing tasks on upgrade).
- `checkers/` — one deterministic gate checker per stage (`BaseChecker` subclasses). `Check` = diagnose only (no state change); `Complete` = verify then atomically advance the stage pointer. **Path resolution is task-first**: checkers prefer `stage_manager.get_task_output_dir()` artifacts; the global `output/auto_battery_research/` fallback is gated by `BaseChecker.allow_global_legacy_fallback` — only adopted legacy task dirs (un-hashed `output/tasks/{slug45}/`, `StageManager.is_legacy_task`) or standalone checkers (no stage_manager) may read global artifacts; new hashed tasks must be self-contained and never read global. `auto_detect_existing_progress()` enforces strict sequential-prefix claiming: a downstream stage is only auto-claimed when every prior stage is terminal-OK.
- `tools/` — the agent's toolbox:
  - `domain_tools.py` — 11 stage domain tools (`Inspect*` x3 asset probes + `IngestLiteraturePapers`, `IndexSemanticVectors`, `ExtractAndAssembleCells`, `RunRAGDesign`, `QueryMaterialsProject`, `RunPhysicsSimulation`, `ReviseGeometry`, `SynthesizeResearchReport`). Stage 4 writes converge on a single path: `RunRAGDesign` (`RunRAGDesignTool`) → `run_rag_design` → `AbrRagAdapter` → `RAGPipeline` (`Planner/Retrieval(+MP)/Writer/Reviewer` in `src/lmllm/RAG/`); nothing else may write `design_scheme.*`.
  - `stage_tools.py` — 6 workflow guardrail tools (`CurrentTips`, `Status`, `Check`, `Complete`, `SetStageJournal`, `AllStageJournal`) plus `RoleInfo` / `SkipStage` / `EnableStage` / `RunStageTask` helpers. They read the module-level singleton wired via `set_stage_manager()`; the singleton + per-goal cache are guarded by `_MANAGER_LOCK` (Gradio handlers run threaded). The web UI queues events with `default_concurrency_limit=1` — do not raise it, tool runtime state is process-global. **Always fetch managers via `get_stage_manager_for_goal(goal)`** (reuses the global singleton or a per-goal cache) — constructing `StageManager` directly triggers the full checker cascade + state double-write and can race the main flow. Its `resolve_effective_goal(target, active)` folds synonymous goals (case/whitespace/punctuation, ≥6-char substring, same `Wh/kg` figure) back to the active goal — domain tools and inspectors resolve their goal through it so synonymous requests reuse the global singleton instead of spawning a per-goal manager.
  - `file_tools.py` — workspace-sandboxed file utilities; `validate_workspace_path()` rejects any path resolving outside the repo root (path-traversal guard).
  - `workflow_actions.py` — the bridge from tools to real work; fail-closed: when assets are missing it actually runs the pipelines (imports `step_mineru/step_merge/step_classify` from `auto_battery_research.pipeline.incremental`, subprocesses `miner/paragraph_metadata_pipeline_v5_qwen.py --incremental`, imports `run_tok2000` via `auto_battery_research.mining`). `_merged_literature_dirs()` resolves merged-literature dirs as: canonical `paths.papers_merged_dir` (`papers/merged`) + legacy `papers/text_merged` where the old cleaning pipeline's data lives — count both, don't hardcode either.
  - `rag_adapter.py` — bridges Stage 4 to the RAG engine in `src/lmllm/RAG/` and converts results into the Stage 4 output contract. Validates fail-closed **before** writing, stamps `review_status: APPROVED|REVIEWED_WITH_ISSUES|REJECTED`, `design_revision`, and `geometry_hash` into `design_scheme.json` (consumed by `agent.py`'s scheme-valid precheck), saves `design_revisions/` snapshots, always writes the artifacts (`REJECTED` output is kept for diagnosis), and pins a `provenance` block (corpus manifest hash / vector-index fingerprint / `RULES_VERSION` from `relation_engine.py`) plus `research_context.json` for reproducibility.
  - `mcp_server.py` — 14-tool stdio MCP server (`abr-cli --mcp`).
- `mcp/` — 2-tool domain-expert MCP server (`auto_battery_research.mcp.server`, Streamable HTTP / stdio via `auto-battery-research-mcp`), exposing `battery_consult` and `design_battery_experiment` without leaking internal workflow tools.
- `mining/`, `pipeline/`, `rag/`, `simulation/` — unified import facades re-exporting the legacy implementations (`agent/*`, `src/lmllm/RAG/*`, `pinn/*`) plus Stage 4/5 `simulation/geometry_contract.py`; prefer these over importing legacy locations directly. Heavy deps (chromadb/torch) stay guarded inside the source modules.
- `tui/`, `web/` — Textual TUI; web: FastAPI read-only monitor (`web/server.py`, main `--web` entry, parses task state from disk without constructing StageManager) + legacy Gradio dashboard (`web/app.py`, `--web-gradio`).

### Layer 2: RAG engine — `src/lmllm/RAG/`

Multi-agent material-screening RAG: **Planner → Retrieval → Writer → Reviewer** (`agents.py`, orchestrated by `rag_pipeline.py` with `target_energy_wh_kg` extraction and structured geometry generation). Key pieces: `multi_retrieval.py` (Chroma + BM25 + RRF dual-corpus retrieval + Materials Project MCP `augment_mp_evidence`), `relation_engine.py` (**7 tiered rules C1–C3, C5–C8**; `C4` permanently removed; `RULES_VERSION="C1-C8/v2.1-coincell"` retained for provenance/tests — `C0_UNVERIFIED_MATERIAL` fail-closed reject, `C1–C3` `challenge` with `mitigation_whitelist`, `C5` `recommend` `additive:FEC`, `C6` `audit_note`, `C7` `coin_cell_eval` excludes `graphite` at ≥400 Wh/kg, `C8` `flag_frontier` ≥500 Wh/kg allowed+flagged), `structured_output.py`, `prompts.py` (central prompt registry).

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
     .stage_state.json · stage_journals.json · cell_assembly/ · design_revisions/
     design_scheme.md/.json · rag_result.json · research_context.json
     pinn_input_spec.json · pinn_simulation_result.json · pinn_simulation_curve.png
     experiment_plan.json · final_research_report.md
```

## Important Behaviors

- 六节报告由 `util/report_analysis.py` 补充材料论证摘录、文献制备参考、逐轮仿真比较和实验验证路线；当前参数仍来自 `report_contract.py`。原始路径集中在第六节，来源按 DOI/文件归并。Stage 6 对六节布局及分析内容与归档的一致性进行验收。第五部分实验方案由 `util/report_recipe_llm.py` 让大模型综合完整 `design_scheme.md`、最终设计、证据原文与仿真结果生成，程序侧验收引用真实性与参数一致性，记录存 `experiment_plan.json` 并按输入指纹防陈旧；LLM 不可用或验收不过时回退确定性渲染。

- 最终报告使用 `util/report_contract.py` 的最终设计/仿真视图，不嵌入初始 Markdown 数值；实验章节直接渲染有效几何与已声明条件，缺失工艺参数留空，不再回退固定数值配方。报告指纹与关键块在 Stage 6 门禁重算验收。PINN 结果明确携带 `energy_accounting`，电芯比能量是材料级值乘固定系数，不是逐项质量核算或物理上限证明。
- N/P 可选且不要求模型生成；缺失时不校准两电极厚度。Stage 5 的 Check/Complete 共用幂等择优收尾。阶段完成后不再兜底重复仿真；阶段日志合并保留详细发现。
- RAG Writer/Reviewer 共享证据覆盖，Reviewer 仅返回简短判定。`review_completed` 表示有效审核响应，`review_passed` 表示无未解决意见且置信度合格；预算耗尽但仍有内容意见时仅作为 `REVIEWED_WITH_ISSUES` 待验证候选推进，保留真实低置信度和意见到报告，不能称为 APPROVED。响应失败/结构不全/硬规则失败仍拒绝。LLM 响应记录 finish_reason/推理 token；OpenAI-compatible 空白或截断最多扩大预算重试一次，仍失败则拒绝。工作流 RAG 预算来自 LLM_MAX_TOKENS 或 llm.max_tokens，重试上限 llm.output_max_tokens。

- 新几何主通道由 `simulation/geometry_contract.py` 定义：Stage 4 在 Writer 前独立生成结构化候选，Adapter 验收后写入 `contract_version=2.0` 的兼容 v1.1 数值块；新任务不依赖正文正则抽取。R_s 是可设计半径；D_s 修改需任务证据原文的材料、数值/单位、温度与恒定参数适用性验证。`ReviseGeometry` 对新契约要求基准版本/哈希，逐轮保存设计快照；历史正则产物保持兼容。离线缺结构化候选时明确拒绝，不编造几何。

- **Stage 5 (PINN) is enabled by default** (`skip_pinn_default: false`). It always extracts the selected materials' physical parameters to `pinn_input_spec.json` (extraction also runs on the skip fast path; fields that cannot be extracted stay `null` — no default-table backfill). When the scheme's (cathode, anode) matches a trained PINN system in `pinn/models/registry.json` and the `pinn_trigger.enabled` kill-switch is on, it runs the SPM PINN discharge simulation — pure-numpy `pinn/spm_runner.py`, npz weights exported from TF SavedModels by `scripts/export_pinn_spm_weights.py` (run in conda env `pinn210`), golden-IO equivalence locked by tests — and writes `pinn_simulation_result.json`. The simulation is **spec-driven**: R_s / D_s / thickness / porosity / C-rate are explicit external inputs taken from the spec (missing fields fall back to the system's trained params), and each system declares per-parameter valid ranges (`input_ranges` in the registry) — out-of-range (OUT_OF_RANGE) or out-of-envelope (OUT_OF_ENVELOPE) inputs are intercepted before the network runs. Converged runs also render `pinn_simulation_curve.png` (V-Q chart) into the task dir and the Stage 6 report embeds it. Stage 4's `design_scheme.json` carries a complete geometry declaration block (`scheme.geometry`, numeric layout `schema_version="1.1"` with `contract_version="2.0"` + `parameter_sources` + `design_revision` + `geometry_hash`: per-electrode `L_um`/`porosity`/`R_s_um`/`D_s_m2s` + optional `N/P` + voltage window; declaration table appended to `design_scheme.md`). It flows into the spec as explicit inputs, so the PINN simulates the declared design point and the report shows both the declaration (section 3) and the effective geometry (section 4). Unmatched systems fall back to extraction-only. Stage 5 is a **hard gate** (`strict: true`): a triggered run (matched system + kill-switch on) must produce a `pinn_simulation_result.json` with `status: CONVERGED` whose `pinn.spec_hash` matches the spec's `cell_spec_hash` — non-converged runs are persisted as failure payloads (overwriting any stale success, PNG invalidated) and `run_pinn_simulation` returns `success: false`; unmatched/kill-switch-off runs legitimately pass as extraction-only. The Stage 6 report embeds numbers/geometry/curve only when the same identity guard (`_pinn_result_identity_ok`) passes. The old PyBaMM P2D path (`p2d_runner.py`/`cell_spec_schema.py`) was removed — `pinn/output/` legacy artifacts and the checker's legacy branch are gone; the web demo TAB now runs the PINN. Stage 4's `RAGDesignChecker` hard-gates the geometry declaration (`runtime_options.geometry_gate: enforce|warn`, default enforce): under `contract_version="2.0"`, both electrodes' `L_um` and `porosity` must be explicit (`stage4_recipe` or `stage5_revision`), while `R_s_um` may be explicit or `model_baseline`/`missing` and `D_s_m2s` may be explicit (only with verified `diffusion_evidence`) or `model_baseline`/`missing` (legacy v1.1 records without `contract_version="2.0"` require all 8 fields as `stage4_recipe`/`stage5_revision`; violations emit `GEOMETRY_BLOCK_MISSING` / `GEOMETRY_DECLARATION_INCOMPLETE` / `GEOMETRY_CONTRACT_INVALID` / `GEOMETRY_VALUE_ILLEGAL` — where `GEOMETRY_CONTRACT_INVALID` covers `contract_version="2.0"` `validate_geometry_contract` and `geometry_hash` failures, while `GEOMETRY_VALUE_ILLEGAL` covers numeric type/range and diffusion-evidence checks); optional fields are range-checked against the registry when declared. Stage 5 closes the feedback loop: `RunPhysicsSimulation` returns a deterministic target verdict (`key_findings.target` — energy goal parsed from the goal text, tolerance `runtime_options.target_energy_tolerance_pct` default ±5%, `met` flag) plus `geometry_context` and effective-geometry echo; when not met the agent calls `ReviseGeometry` (carrying `base_revision` and `base_geometry_hash`, registry-range validated fail-closed, `D_s_m2s` edits requiring verified `evidence_claims`, budget `runtime_options.max_geometry_revisions` default 3, provenance `stage5_revision`, revision log rendered into `design_scheme.md` and `scheme.geometry_revisions`) and re-runs the simulation. Every round is archived (`pinn_simulation_history.json` index + `pinn_simulation_round_{k}.result/.spec.json` sidecars; cleared on NO_MATCH/kill-switch-off). At `complete_stage` (shared with `check_stage`) a deterministic finisher restores the best CONVERGED round by `energy_wh_kg` if the target is still unmet (sidecar result/spec restore + geometry snapshot rollback + selection audit entry; no-op when any round met the target, no numeric target exists, or the last round is already the best). The checker additionally guards `pinn.scheme_hash` against the current scheme projection (`util.hashing.stage5_scheme_projection`, which includes geometry — `PINN_SCHEME_STALE`), and validates `contract_version="2.0"` + `geometry_hash`, closing the "geometry revised without re-simulation" window. `complete_geometry_block` is idempotent (re-normalization never overwrites declared values with baseline), and spec-side illegal geometry values fail closed (`ValueError` from `build_cell_spec`) instead of silently reverting to the trained baseline.
- Workflow state is sticky **per goal**: `output/tasks/<goal>/.stage_state.json` is reused across runs. After changing stage deliverables/code, use `abr-cli --reset` (or delete that goal's state file) to force re-evaluation from Stage 1. **Goal identity is normalized, not exact-matched**: `resolve_effective_goal()` treats case/whitespace/punctuation variants, ≥6-char substrings, and identical `Wh/kg` figures of the active goal as the same goal so LLM wording drift cannot fork a new task dir. The default goal has one source of truth — `util/constants.py:DEFAULT_GOAL`; changing it orphans existing task state dirs, and goal strings must never be hardcoded elsewhere (the 400Wh default was deliberately scrubbed from tools/checkers).
- **LLM full-trace recording is opt-in** (`runtime_options.llm_trace: false` by default; enable via `abr-cli --trace`, setting.yaml, or env `LLM_TRACE` — priority CLI > setting > env). When on, `util/llm_trace.py`'s process-singleton recorder appends one JSONL event per LLM call (full input/output messages incl. tool_calls & ToolMessage observations, token usage, duration, backend/model, trace_tag for the RAG role) plus `prompt_assembly` events, into the goal's `llm_trace/trace_<session>.jsonl` + `trace_meta.json` (no-goal processes fall back to `log/llm_trace/`). Hook points: `ABRLangChainBackend.invoke` (ReAct brain — offline short-circuit recorded as `backend="offline"`), `src/lmllm/RAG/llm_client.chat` (trace_tag from callers: planner/writer/writer_revise/reviewer/report_recipe/baseline), `backend/llm_client.generate` (TUI Q&A / MCP consult), `RAG/extractor._call`, and legacy mining factories (`miner/config.create_llm`, `agent/pipeline._create_llm`) via `LLMTraceCallbackHandler`. Recording is fail-silent by design — a broken recorder must never affect the run.
- Strict mode (`runtime_options.strict_mode: true`) makes hard checkers fail the stage on any error; `max_retries_per_stage: 3` bounds self-healing loops.
- Stage rule from the mission system prompt: never fabricate data — "有则提取、无则留空、禁止编造" (extract what exists, leave blank otherwise, never invent). Design schemes must pass `RelationEngine`'s 7 tiered rules (`C1–C3, C5–C8`; `C4` permanently removed; `RULES_VERSION="C1-C8/v2.1-coincell"`) including `C0_UNVERIFIED_MATERIAL` rejection and `C1–C3` `mitigation_whitelist` checks.
- Windows is the primary dev platform — entry points reconfigure stdout/stderr to UTF-8; keep that pattern when adding new entry scripts.
- Unit tests must stay offline: `tests/test_abr_agent.py` injects `OFFLINE_CONFIG` (dummy key) so no live API calls happen. Anything needing live services gets the `external` marker.
- When adding a new checker or stage, register it in `workflow/abr_workflow.yaml` (checker classes are resolved by dotted import path) — `StageManager` discovers everything from there.
