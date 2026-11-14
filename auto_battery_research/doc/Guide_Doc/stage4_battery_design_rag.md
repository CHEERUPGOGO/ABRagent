# Stage 4: 多智能体 RAG 方案设计指引

## 任务目标
1. 通过唯一设计入口 `RunRAGDesign` → `AbrRagAdapter` → `RAGPipeline` 接收具体化学电池设计目标（如 400 Wh/kg 锂金属电池），并自动从 query/目标提取 `target_energy_wh_kg`（缺失时允许后续结构化补充）。
2. 调度 `Planner`（含 `mp_queries` 规划）→ `Retrieval`（`MultiRetrieval` Chroma+BM25+RRF 双库混合检索 + Materials Project MCP `augment_mp_evidence` 证据补充 + 可选 Reranker）→ 独立结构化几何候选生成 → `Writer`（生成五段式方案，冻结几何字段）→ `Reviewer` + `RelationEngine` 协同审查。
3. 执行 **`RelationEngine` 7 条分级规则 C1–C3, C5–C8**（**C4 已永久移除**；代码常量保留兼容标签 `RULES_VERSION = "C1-C8/v2.1-coincell"`，规则配置见 `src/lmllm/RAG/data/constraints.json` `version="2.1-coincell-academic"`）：
   - **前置 fail-closed (`C0_UNVERIFIED_MATERIAL`)**：未在已验证材料词典/库中登记的材料直接拒绝（`feasible=False`）。
   - **分级动作机制**：
     - `C1–C3` (`action: "challenge"`): 带 `mitigation_whitelist` 缓解白名单审查（如高浓/局部高浓或全氟溶剂、LiDFOB/FEC 界面成膜、NCM811 掺杂/包覆或温和上限、金属锂保护层/3D 骨架等）；声明匹配缓解措施则放行，未声明缓解措施的裸配组合判定违规（`feasible=False`；其中 C2 同时带 `retrieval_boost` 检索加权）。
     - `C5` (`action: "recommend"`): 硅基负极推荐补充 `additive:FEC`（记录推荐项与缺失提示）。
     - `C6` (`action: "audit_note"`): `LNMO`/`LRMO` 等高锰体系记录 Mn 溶出与界面稳定性审计批注。
     - `C7` (`action: "coin_cell_eval"`): 当 `target_energy >= 400 Wh/kg` 时排除 `graphite` 负极（`feasible=False`）；否则输出材料级能量可行性评估。
     - `C8` (`action: "flag_frontier"`): 当 `target_energy >= 500 Wh/kg` 时允许通过（`feasible=True`）并在 `frontier_flags` 标记为前沿高比能目标。

## 离线与异常 Fail-Closed 行为
- LLM 不可用或结构化 JSON 输出校验失败时，**严禁编造兜底配方**：`PlannerAgent` 可退化为规则拆解，`WriterAgent` 返回空草案（不填充硬编码模板配方），`ReviewerAgent` 返回 `confidence="low"`、`error_type="reviewer"`、`fallback=True`。
- `AbrRagAdapter` 据此将 `review_status` 标定为 `REJECTED`（正常通过为 `APPROVED`，存在非致命告警/局部缺口时可为 `REVIEWED_WITH_ISSUES`）；产物落盘与 `RAGDesignChecker` 门禁将严格拦截空白、未审定、无真实有效文献证据或未通过 `RelationEngine` 的方案。

## 验收门禁 (`RAGDesignChecker`)
- 课题产物目录 `output/tasks/<task_slug>/`（旧默认课题回退 `output/auto_battery_research/`）下存在 `design_scheme.md`（$\ge 200$ 字符）、`design_scheme.json`、`rag_result.json`、`research_context.json` 及 `design_revisions/` 初始快照。
- `design_scheme.md` 完整包含五段式核心章节（目标与设计路线、推荐组合、预期关键指标、可行性依据、风险与数据缺口）。
- `design_scheme.json` 通过 Reviewer 校验、包含 $\ge 1$ 条非异质有效文献证据、明确声明正极/负极/电解液材料，且通过 `RelationEngine` 分级规则与能量评估检查。

## 几何设计数值要求与结构化传递（契约 v2.0，兼容 v1.1）
工作流在 `Writer` 生成叙事正文之前先独立构建 JSON 几何候选（`geometry_contract.build_geometry_contract`），`Writer` 阶段几何参数冻结，禁止从改写后的 Markdown 正文回写覆盖输入，Markdown 尾部仅作「设计点几何声明」展示。
- **协议标识**：数值布局兼容 `schema_version="1.1"`，新增 `contract_version="2.0"`、`parameter_sources`、`design_revision`（初始为 `0`）与 `geometry_hash`。
- **字段约束与来源规则**：
  - 电极涂敷厚度 `L_um` 与孔隙率 `porosity`：**必须**在正/负极显式设计声明（`stage4_recipe` 或 `stage5_revision`）；
  - 颗粒半径 `R_s_um`：按 PINN 输入口径直接作为**颗粒半径**，可采用显式设计值，或在 `contract_version="2.0"` 下由程序标注为 `model_baseline` / `missing`；
  - 固相扩散系数 `D_s_m2s`（写法如 `2.5e-14`）：仅当存在可核查文献扩散证据（`diffusion_evidence`）时采用设计值；无可靠证据时在 `contract_version="2.0"` 下由程序明确标注为 `model_baseline` / `missing`，绝不伪装成文献实测；
  - 其余条件参数（面载量、压实密度、倍率、`v_min`/`v_max` 等）按已有证据给出；`N/P` 为可选参数，缺失保持空白，不按容量比反向校准厚度。
- **`RAGDesignChecker` geometry 门禁**（`runtime_options.geometry_gate: enforce|warn`，默认 `enforce`）：
  - `scheme.geometry` 缺失 → `GEOMETRY_BLOCK_MISSING`；
  - `schema_version != "1.1"` 或契约要求显式声明的字段缺失/来源不合规 → `GEOMETRY_DECLARATION_INCOMPLETE`；
  - `contract_version="2.0"` 结构化契约校验失败或 `geometry_hash` 失配 → `GEOMETRY_CONTRACT_INVALID`；
  - 数值类型/范围非法（或 `D_s_m2s` 扩散证据验证失败） → `GEOMETRY_VALUE_ILLEGAL`。
  - Stage 5 可经 `ReviseGeometry` 反馈修订几何并写入 `stage5_revision` 来源标记，`RAGDesignChecker` 对合规的 `stage5_revision` 同样放行。
