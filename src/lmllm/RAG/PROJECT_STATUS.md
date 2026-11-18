# 高比能液态锂电池设计方案系统 — 实施状态

> 更新: 2026-11-13
> 范围: 锂离子/锂金属**液态**电池（含非水系与水系候选；排除固态、锂硫、锂空、回收、综述）
> 目标: 给定能量密度目标 → 输出**可验证**的材料组合方案（正极+负极+电解液+添加剂）

---

## 1. 系统架构

```
                    ┌─────────────────────────────────────────────────────┐
                    │  任务生成器 task_generator.py                       │
                    │  组合推荐 / 约束验证 / 掺杂设计 / 参数设计          │
                    └──────────────┬──────────────────────────────────────┘
                                   │ 当前快照 26 题（2026-08-18）
                                   ▼
┌──────────────┐   ┌──────────────────────────────────────────────────────┐
│ 用户问题      │──▶│ RAG 管线（Planner → Retrieval → Writer → Reviewer）  │
│ (含能量目标)  │   │  插桩 A: query_modifiers 检索修饰 + MP MCP 物性增强  │
└──────────────┘   │  Writer: 五段式方案 + 可选几何冻结（离线 fail-closed）│
                   │  插桩 B: check_scheme 规则校验 + confidence 否决/重试 │
                   └───────┬──────────────────────────┬───────────────────┘
                           │ 输出设计方案               │ rule_checks
                           ▼                          ▼
                    评测闭环 evaluate_design_tasks.py   错题 → 规则/prompt 迭代
                           │
                           ▼  下游集成（auto_battery_research + pinn/）
                    AbrRagAdapter / PINNPhysicsChecker → Stage 5 SPM PINN

数据资产（src/lmllm/RAG/data/）:
  candidates.json (v0.4) / constraints.json (v2.1) / alias_map.json (v0.3)   领域知识三表
  calibrated/energy_ranges.json + mp_crosscheck.json                          公开库校准
  seed/relations_seed.json（36 关系对象）+ candidate_sentences.json + review_report.md
  tasks/design_tasks.json（26 题快照）+ eval_report.json（管线基线 3/3）
```

可靠性三层架构：
1. **规则层** ✅ — 层级约束表（v2.1: `C1–C3, C5–C8`，`C4` 已移除）+ 能量估算（`energy_model.py`）+ 检索修饰 + 审核否决 + 离线 fail-closed 保护
2. **记录层** ⏳ — NERRE 关系抽取结果入库反查（`extractor.py` 与 `schemas/battery_relations.py` 已就绪，`candidates.json` v0.4 / `alias_map.json` v0.3 已并入首批 LLM 登记材料，全量关系库仍待扩展）
3. **物理层** ✅ — 已通过根目录 `pinn/`（SPM PINN，`pinn/spm_runner.py`、`pinn/input_spec.py`、`pinn/models/registry.json`）与 `auto_battery_research`（`PINNPhysicsChecker` / `AbrRagAdapter`）承接 Stage 5 物理仿真；原 `src/lmllm/RAG/pinn_tools/` 遗留代码已清理移除

---

## 2. 五阶段实施状态

### 阶段 0：领域知识结构化 ✅
| 交付物 | 状态 |
|--------|------|
| `data/candidates.json` | `v0.4-llm-registered`（`2026-08-28`）：9 正极（NCM811/LRMO/LNMO/LCO/LFP/NCA/LMO/NCM442/NCM955）+ 6 负极（graphite/si_base/li_metal/hard_carbon/soft_carbon/LTO）+ 49 电解液（7 手工 + 42 LLM 登记）+ 23 添加剂（6 手工 + 17 LLM 登记）；6 核心活性材料经 MP 结构稳定性验证 |
| `data/constraints.json` | `2.1-coincell-academic`（`2026-10-29`，`RULES_VERSION="C1-C8/v2.1-coincell"`）：保留 **7 条规则**（`C1–C3, C5–C8`，已于 commit `48c4534` 移除 `C4`），采用分级动作语义（`challenge` / `recommend` / `audit_note` / `coin_cell_eval` / `flag_frontier`，并内置 `C0_UNVERIFIED_MATERIAL` 未登记材料拦截）；`C7` 经 BetterBat 实测佐证 |
| `data/alias_map.json` | `v0.3-llm-registered`（`2026-08-28`）：86 个规范实体键的别名映射（含中英文、化学式、水系/高浓/含氟溶剂与添加剂实体） |
| `energy_model.py` | 材料级/电芯级能量估算（`DEFAULT_ACTIVE_RATIOS`: `graphite=0.50`, `si_base=0.45`, `li_metal=0.50`, `default=0.48`；`ENERGY_TOLERANCE=0.35`）；BetterBat 校准产出体系能量区间（Nickel rich 50.0–350.0 Wh/kg） |
| `data/calibrated/` | `energy_ranges.json`（BetterBat v6，141 电芯 4 体系）+ `mp_crosscheck.json`（6 核心活性材料 + 4 电解液/3 添加剂备注） |
| `scripts/fetch_datasets.py` | MP / BetterBat / CDX 数据抓取与 provenance 脚本 |

### 阶段 1：NERRE 关系抽取 ✅
| 交付物 | 状态 |
|--------|------|
| `schemas/battery_relations.py` | 三类关系 schema（`doping` / `compatibility` / `performance`）+ 内嵌 ICL 示例 |
| `data/seed/relations_seed.json` | 36 个关系对象（`doping` 10 + `compatibility` 14 + `performance` 12），含 `review_report.md` 种子审核记录 |
| `extractor.py` | `RelationExtractor`（默认 `deepseek-chat`）+ `parse_relations` 结构化解析 |

### 阶段 2：任务生成器 ✅
| 交付物 | 状态 |
|--------|------|
| `task_generator.py` | 四类任务生成逻辑；`gen_constraint_tasks` 遍历 `action in ("exclude", "challenge", "coin_cell_eval")` 的规则构造负样本（按 v2.1 规则重生成将产出 4 道 `constraint_check`：`C1`, `C2`, `C3`, `C7`，`C6` 已改为 `audit_note`） |
| `data/tasks/design_tasks.json` | 仓库当前保留的 `2026-08-18` 快照共 26 题：组合推荐 3 + 约束验证 5（C1, C2, C3, C6, C7）+ 掺杂设计 6 + 参数设计 12（正 21 / 负 5） |

### 阶段 3：RAG 管线插桩与 P0 加固 ✅
| 交付物 | 状态 |
|--------|------|
| 混合检索与重排序 | `MultiRetrieval`（`_RRF_K=60`, `max_passages=10000`）融合 Chroma（`qwen3-embedding:8b`）+ `BM25KnowledgeBase`（`BM25Okapi`）；支持可选 `Qwen3Reranker` |
| 插桩 A（`RetrievalAgent` / `query_modifiers`） | 基于 `RelationEngine.query_modifiers()` 生成 `exclude_terms`（`_ids_to_terms` 过滤 `<2` 字符短别名）与 `boost_terms`，支持 `challenge` / `warn` / `coin_cell_eval` 条件词修饰；并调用 `augment_mp_evidence` 注入 MP MCP 物性证据 |
| 插桩 B（`ReviewerAgent` / `check_scheme`） | `target_energy_wh_kg` 已接入 `RelationEngine._resolve_field()`；`check_scheme()` 先行校验并注入审核 prompt，具备 `confidence` 否决权；Reviewer 仅输出结构化 verdict（最多 3 轮检索重试，透传 `lit_weight`、`ebook_weight` 与 `_writer_scheme`） |
| Writer 方案五段式与几何冻结 | 注入约束评估并启用五段式方案输出；支持 `structured_geometry_enabled` 几何冻结；`_extract_scheme` 优先提取问题中的目标比能量，`_extract_energy_claim` 优先解析 `## 预期...指标` 段落 |
| 离线 Fail-Closed 保护 | `PlannerAgent` 保留 `rule_decompose_question` 纯规则拆解；`WriterAgent` 移除硬编码 `SC-NCM90` 假配方回退（离线返回 `draft_answer=""`, `fallback=True`, `error=...`）；`ReviewerAgent` 离线返回 `confidence="low"`, `error_type="reviewer"`, `fallback=True`，草稿为空时管线直接 fail-closed 跳过审核 |
| 多轮上下文管理 | `RAGPipeline.chat()` 采用 `tiktoken`（`cl100k_base`）按 `MAX_HISTORY_TOKENS=4000`、`OVERLAP_TOKENS=500` 滑动窗口裁剪历史；`multi_turn/` 提供 `SQLite` `HistoryStore` |

### 阶段 4：评测闭环 ✅
| 指标 | 基线 |
|------|------|
| `rule` 自洽性 | 对现有 `design_tasks.json`（26 题快照）运行 `evaluate_rule` 为 `26/26`（`1.0`；原 `C6` 负样本 `LRMO + graphite + carbonate_ec` 因同时违反 `C1` 仍判 `feasible=False`） |
| `pipeline` 约束验证 | `data/tasks/eval_report.json` 记录 `3/3`（`hit_rate=1.0`，三题实测耗时 `707.3s` / `597.6s` / `991.4s`，约 `10–17 分钟/题`） |

---

## 3. 关键决策记录

1. **不做知识图谱**：关系用 JSON 表承载（`alias_map` / `candidates` / `constraints`），规模可控、精度优先；图谱是后续大规模关系入库后的演进选项。
2. **关系抽取 = NERRE schema 方法**（Dagdelen et al. Nat. Commun. 2024）：schema 先行、少样本提示、parsability 优先。
3. **公开库分角色**：Materials Project（结构稳定性与物性增强，离线快照 + `augment_mp_evidence` MCP 集成）、BetterBat（能量区间校准，141 电芯）；材料级兼容与掺杂关系靠文献抽取与人工审核沉淀。
4. **任务生成器与可验证性**：所有任务携带 ground truth 或规则依赖，支持 `evaluate_rule` 秒级重算验证与 `evaluate_pipeline` 端到端评估。
5. **约束分级与软质询化（v2.1）**：移除已废弃的 `C4`，保留 `C1–C3, C5–C8` 共 7 条规则，同时保留 `RULES_VERSION="C1-C8/v2.1-coincell"` 以兼容既有契约测试；将 `C1–C3` 统一为 `action="challenge"`（裸碳酸酯判违规，配对 `mitigation_whitelist` 缓解措施时放行并记录 `challenges`）；`C5` 推荐 `FEC`；`C6` 降为 `audit_note`（锰溶出审计提示）；`C7` 在 `target_energy >= 400 Wh/kg` 时排除 `graphite` 并对其余体系做扣电材料级比能量校核；`C8` 在 `target_energy >= 500 Wh/kg` 时允许探索并追加 `frontier_flags`。
6. **离线 Fail-Closed 与物理层解耦**：当 LLM 不可用或发生异常时，禁止生成伪造推荐配方（原 `SC-NCM90` 占位输出）或高置信度放行；Stage 5 物理层由根目录 `pinn/`（SPM PINN）与 `auto_battery_research/`（`AbrRagAdapter` / `PINNPhysicsChecker`）独立承接。

---

## 4. 遗留项与后续路线

| 项 | 说明 | 触发时机 |
|----|------|---------|
| 任务快照按 v2.1 重建 | 当前 `design_tasks.json` 仍为 `2026-08-18` 快照（26 题含旧 C6 负样本）；按现行 `task_generator.py` 与 v2.1 规则重建将产出 4 道 `constraint_check` | 需要刷新评测集时 |
| 批量关系抽取扩展 | 扩充 `scripts/batch_extract.py` 并完善化学式顺序变体归一与未登录实体入库审核 | 需要扩展材料/关系库时 |
| 种子与机理扩展 | 增加添加剂缓解类 `conditionally_compatible` 句型与掺杂机理实例 | 抽取覆盖不足时 |
| 端到端评测扩展 | 在真实 LLM 在线环境下运行全量任务及 `doping` / `parameter` 子集（单题约 `10–17 分钟`） | 完整回归基线时 |
| 物理层模型扩展 | 基于根目录 `pinn/` 现有 SPM PINN 进一步接入 P2D 与更多化学体系 | 拓展电化学仿真精度时 |

---

## 5. 环境与运行

- **配置来源**（`config.py`）：支持环境变量与 YAML（`setting.yaml` / `miner/config.yaml`）；LLM 后端优先级为 `OpenAI / MiniMax API`（`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `MINIMAX_API_KEY`）→ `DeepSeek API`（`DEEPSEEK_API_KEY`）→ `Ollama` 本地模型 → `rule` 模式（仅 `PlannerAgent` 支持规则拆解；`WriterAgent` / `ReviewerAgent` 离线 fail-closed）。
- **常用命令**：
```bash
python -m src.lmllm.RAG.relation_engine                        # RelationEngine 约束自测
python -m src.lmllm.RAG.task_generator                         # 重新生成设计任务集
python -m src.lmllm.RAG.evaluate_design_tasks --mode rule      # 规则自洽性检查（秒级；注意会覆写 eval_report.json）
python -m src.lmllm.RAG.evaluate_design_tasks --mode pipeline --limit 3  # 端到端管线评测（约 10–17 分钟/题）
python -m src.lmllm.RAG.single_turn.app                        # 单轮 Gradio Web UI (端口 7860)
python -m src.lmllm.RAG.multi_turn.app                         # 多轮 Gradio Web UI (端口 7861)
python -m src.lmllm.RAG.extractor "句子" --type performance     # 单句 NERRE 关系抽取
```

---

## 6. 领域知识备忘（v2.1 约束表核心依据）

- **C1 / C2 / C3（`challenge` 分级质询）**：常规碳酸酯氧化窗口有限，高压正极（`LNMO` / `LRMO` / 高电压富镍等）及锂金属负极（`li_metal`）在**裸碳酸酯电解液**（未配对 `mitigation_whitelist` 添加剂/高浓/局部高浓/氟代/硝酸锂等缓解手段）下触发违规；具备白名单缓解措施时通过质询（`feasible=True`）并记录 `challenges`。
- **C4 已移除**：原第 4 条规则已在 commit `48c4534` 中永久删除，当前规则编号保留 `C1–C3, C5–C8`。
- **C5（`recommend`）**：硅基负极（`si_base`）强烈推荐搭配 `FEC` 添加剂成膜，缺失时记入 `inclusions` 推荐而不直接否决。
- **C6（`audit_note`）**：`LNMO` / `LRMO` 等高电压/富锂锰基正极需关注高温/长循环 Mn 溶出抑制策略，记入 `audit_notes` 审计备注。
- **C7（`coin_cell_eval`）**：目标能量密度 `>= 400 Wh/kg` 时排除 `graphite` 负极（BetterBat 实测 Nickel rich 电芯级上限 `350.0 Wh/kg`），非石墨体系则按扣电材料级 `volt * cap` 做可行性提示。
- **C8（`flag_frontier`）**：目标能量密度 `>= 500 Wh/kg` 标记为高比能前沿探索目标（`frontier_flags`），不作硬排除（`feasible=True`）。
