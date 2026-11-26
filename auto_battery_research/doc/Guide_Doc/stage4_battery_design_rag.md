# Stage 4: 多智能体 RAG 方案设计指引

## v3内容契约（显式启用）

`runtime_options.stage4_writer.contract_version=3` 的新设计会话通过一次完整Writer内容树生成、程序编译和研究关联投影、科学审核、有限叶节点补丁及独立一致性终审。v3不另生成几何、不重复提取研究摘要、不另写整合稿；主方案参数只声明一次，所有显示由引用生成。补丁保持标题/ID/行列/引用身份，语义复核与全文科学复核都不可省略。参数、正文和派生产物共用发布日志，未完成/混合版本拒绝；所有调用机会先持久占用，中断不重发。Stage 5最终改值最多一次只读复核，不恢复旧科学支持。旧v1/v2、分析任务和检查点维持原路径，真实模型上线验收完成前默认v2。完整接口见 `docs/stage4_content_v3.md`；以下独立几何及Markdown恢复说明适用于旧契约。

## 任务目标
1. 通过唯一设计入口 `RunRAGDesign` → `AbrRagAdapter` → `RAGPipeline` 接收具体化学电池设计目标（如 400 Wh/kg 锂金属电池），并自动从 query/目标提取 `target_energy_wh_kg`（缺失时允许后续结构化补充）。
2. 调度 `Planner`（含 `mp_queries` 规划）→ `Retrieval`（`MultiRetrieval` Chroma+BM25+RRF 双库混合检索 + Materials Project MCP `augment_mp_evidence` 证据补充 + 可选 Reranker）→ 独立结构化几何候选生成 → `Writer`（生成五段式方案，冻结几何字段）→ `Reviewer` + `RelationEngine` 协同审查。
3. 执行 **`RelationEngine` 7 条分级规则 C1–C3, C5–C8**（**C4 已永久移除**；`RULES_VERSION = "C1-C8/v2.4-c1-c3-advisory"`，规则配置见 `src/lmllm/RAG/data/constraints.json` `version="2.4-c1-c3-advisory"`）：
   - **前置材料识别 (`C0_UNVERIFIED_MATERIAL`)**：电极可按当前声明的母体匹配材料类别和相容性规则，掺杂名义式无需单独登记，完整配方仍保留。位点、掺量、机理与性能独立审核，不继承母体容量/物性或PINN资格。电解液可按完整解析的盐/浓度/溶剂/添加成分在`electrolyte_families.json`覆盖范围内分类，或匹配已登记具体配方；未识别部分保留诊断，mol/kg不当mol/L，不猜溶剂比例或LHCE。C0仅表示规则适用范围不足，不等于科学上不可行。
   - **分级动作机制**：
     - `C1/C3` (`action: "recommend"`): 高压正极碳酸酯界面及锂金属界面作为非阻断建议保存在 `recommendations`，未采纳建议不产生违规、不降低置信度、不要求修改锁定配方，也不因此给基线电解液文献降权；保留相关措施的检索加权。Reviewer独立审核具体科学主张，不能把建议当成已实施或已验证的措施。
     - `C2` (`action: "challenge"`): 超高压组合保留 `mitigation_whitelist` 缓解白名单审查；声明匹配缓解措施则放行，未声明则判定违规（`feasible=False`），保留 `retrieval_boost` 检索加权。
     - `C5` (`action: "recommend"`): 硅基负极推荐补充 `additive:FEC`（记录推荐项与缺失提示）。
     - `C6` (`action: "audit_note"`): `LNMO`/`LRMO` 等高锰体系记录 Mn 溶出与界面稳定性审计批注。
     - `C7` (`action: "coin_cell_eval"`): 当 `target_energy >= 400 Wh/kg` 时排除 `graphite` 负极（`feasible=False`）；否则输出材料级能量可行性评估。
     - `C8` (`action: "flag_frontier"`): 当 `target_energy >= 500 Wh/kg` 时允许通过（`feasible=True`）并在 `frontier_flags` 标记为前沿高比能目标。

Stage 4 不生成实验方案。材料设计通过后，由 Stage 5B 根据最终参数补检索工艺证据并生成实验推荐。

Reviewer 对待修问题提供可唯一定位的原句和字段路径。程序签发局部写权限，Writer 只提交补丁，不重写全文或修改锁定字段。补检索保留原引用，审核和修复预算随方案检查点续跑。

参数协议报错需要扩展权限时，先由`writer_protocol_advice`给出逐字段修改建议和正文定位，再由程序签发固定转换编号。只允许等值百分比/数值表示、明确采用的文献取值转待验证工程建议，以及带专属作用域的新声明迁移到独立路径；保留来源、工况和候选角色。作用域迁移编号由程序固定同组件路径和原`canonical_path`，只同步正文参数引用与计算依赖，不能自由改路径或删除conditions/group_id/scope/purpose；`writer_protocol_verify`另作只读复核，不代替完整科学审核。已有/锁定参数不开放此权限，建议与复核中断不重发，沿用YAML协议轮次及原检查点；旧空补丁只在剩余额度内新开权限v2轮次，不重置已用次数。

格式与科学修复分开：可唯一定位、只需增删 `**` 的局部格式错误由程序修补，不阻断科学审核。`format_repair_rounds` 默认最多三次，独立于科学正文 `repair_rounds` 默认三轮和审核默认六轮；这些额度及协议补丁次数统一读取 `runtime_options.stage4_repair`，补检索与上下文规模读取 `stage4_retrieval`，代码不另设预算上限。格式失败仍保留科学审核结果及格式诊断，不能无保留通过。末尾疑似截断、未闭合代码围栏或章节缺失仍先恢复正文完整性，不允许用格式修补放行缺失内容。旧预算不自动退还，同版本格式失败不自动重试。

## 收尾论证整合

最终正文、结构化研究记录与支持性审核稳定后，按原始课题组织面向读者的连贯论证。
第二部分只讲材料选择、机理、候选取舍、掺量依据与证据边界。具体实验分组、梯度点、制备热程、装配及测试流程、仪器组合和放行判据统一放第五部分；原稿中的实验编号问题不得带入第二部分。限制审核区分科学结论与执行待办，纯操作事项按第五部分职责处理，不能因第二部分未复述而否决。
内部逐项回答、候选、论证和所有科学限制保留；整合段落关联需求、来源、参数、引用和限制。
Reviewer独立审核覆盖、逐段支持与限制是否真实保留，不能把跨体系推断或待验证观点升级为实证。
生成及审核各最多两次，调用前记录检查点预算。通过后写入`research_narrative`，交Stage 6只读展示。
整合审核未整体通过时，第二部分逐段展示：通过部分正常呈现，有问题的段落以原稿引用块标明待复核原因；不得因此丢弃其他有效段落或改写原审核状态。输入变化使旧审核失效时，旧稿及完整逐项内容保留在附录，不作为当前已确认结论。

## 离线与异常 Fail-Closed 行为
- LLM 不可用或结构化 JSON 输出校验失败时，**严禁编造兜底配方**：`PlannerAgent` 可退化为规则拆解，`WriterAgent` 返回空草案（不填充硬编码模板配方），`ReviewerAgent` 返回 `confidence="low"`、`error_type="reviewer"`、`fallback=True`。
- `AbrRagAdapter` 据此将 `review_status` 标定为 `REJECTED`（正常通过为 `APPROVED`，存在非致命告警/局部缺口时可为 `REVIEWED_WITH_ISSUES`）；产物落盘与 `RAGDesignChecker` 门禁将严格拦截空白、未审定、无真实有效文献证据或未通过 `RelationEngine` 的方案。

## 验收门禁 (`RAGDesignChecker`)
- 课题产物目录 `output/tasks/<task_slug>/`（旧默认课题回退 `output/auto_battery_research/`）下存在 `design_scheme.md`（$\ge 200$ 字符）、`design_scheme.json`、`rag_result.json`、`research_context.json` 及 `design_revisions/` 初始快照。
- `design_scheme.md` 完整包含五段式核心章节（目标与设计路线、推荐组合、预期关键指标、可行性依据、风险与数据缺口）。
- `design_scheme.json` 通过 Reviewer 校验、包含 $\ge 1$ 条非异质有效文献证据、明确声明正极/负极/电解液材料，且通过 `RelationEngine` 分级规则与能量评估检查。

## 方案预算与换算

总/活性面载量、压实密度、孔体积、E/P、面容量和N/P等预算由模型完成，正文写清方法、已知输入及来源、代入过程、结果/单位和适用口径；Reviewer独立复核。计算方法说明见`util/model_calculations.py`，不构成程序执行链。不要使用`{{derived:...}}`，也不要为了某个固定公式补造规范字段或缺失物性。已有输入仍用`{{param:...}}`引用；后续采用的计算结果可作为普通工程参数登记，并在source保留方法和依据。旧abr-derived占位可在授权原句内改成完整计算说明，原参数引用、锁定、单位和来源继续保护。PINN是独立的实际数值仿真，不由文字预算替代。

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
