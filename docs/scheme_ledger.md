# 贯穿课题的统一方案表

默认启用报告优先（`runtime_options.report_first: true`）：新契约记录 `delivery_contract=1` 和独立的 `report_readiness`。类别为 COMPLETE / CONDITIONAL / DIAGNOSTIC，交付完成不等于审核、仿真或性能通过。低置信度、审核服务失败、缺失物性和模型不适用作为限制披露；一致性冲突不能直接采用冲突候选，必须回到当前表并交付诊断。旧契约保持原门禁，设置 report_first=false 可用于严格模式回归。

Stage 4 只有首次完整调用内部 RAG；外层不再通过反复改写请求启动 ReAct 修补。现有可交付方案直接复用；局部补丁兼容唯一 format 包装及匹配的参数哈希，不能借兼容扩大权限。历史格式失败可依据当时授权和原哈希离线重放；版本过期仍拒绝。

Stage 5 先判定模型适用性。不匹配时不要求补齐仿真专用参数，匹配后失败仍保留物理失败状态并继续实验推荐。模型实验推荐失败且无有效缓存时，提供明确标为工程起点的首轮方案；材料无法确定时只生成诊断与定案路径。Stage 4/5 可用 FALLBACK 表示附条件交付，子步骤状态不伪装通过。报告终审强制保留类别、审核、物理限制及待复核事项，禁止把 design_scheme.md 作为最终报告成功返回。

当前流程（Stage 5 v2）：Stage 4 只设计材料和方案表；Stage 5A 仅在匹配且开启时做物理仿真，Stage 5B 始终尝试工艺检索、实验推荐和一致性检查。关闭 PINN 不跳过 5B。`FinalizeStage5` 收尾并复用物理检查点；实验推荐失败不重跑材料 RAG。实验子步骤不设总耗时硬限制，限制检索条数和模型调用次数以控制工作量。已有值必须对齐，非重叠工艺建议不要求直接文献支持，预算假设不作为实测或模型物性。正式报告只渲染当前推荐。

每个课题在首次解析任务目录时建立 `scheme_ledger.json`，中文只读视图为 `scheme_table.md`。当前方案只维护这一份；设计契约、仿真输入、报告均为有版本关联的派生产物。旧课题只导入已有结构化字段，不从初始正文补猜参数，不自动改写历史报告。

## 查看与维护

```powershell
abr-cli --goal "你的课题目标" --scheme-show
abr-cli --goal "你的课题目标" --scheme-update changes.json
abr-cli --goal "你的课题目标" --scheme-unlock cathode.L_um
abr-cli --goal "你的课题目标" --scheme-history
```

更新文件使用 UTF-8 JSON。`base_revision` 必须与当前表相同，避免覆盖其他写入。示例：

```json
{
  "base_revision": 0,
  "reason": "用户确定正极及电极厚度",
  "changes": {
    "cathode.material": {
      "value": "NCM811", "unit": "", "basis": "user_requirement",
      "source": "用户输入", "meaning": "正极材料", "conditions": {}
    },
    "cathode.L_um": {
      "value": 60, "unit": "um", "basis": "engineering_proposal",
      "source": "用户输入", "meaning": "辊压后涂层厚度，不含集流体", "conditions": {}
    }
  }
}
```

用户更新默认锁定，Agent 不能覆盖或更改其来源。解除锁定不改当前值。修改后工作流从 Stage 4 重新评估；旧设计、仿真和报告的版本不一致时门禁拒绝验收。直接修改 JSON 会使指纹失配，请用命令行或统一服务更新。

## 参数与口径

字段按 requirements、cathode、anode、electrolyte、separator、current_collector、packaging、process、condition 分组。每项包含 value、unit、basis、source，可补 meaning、conditions、source_quote、passage_id。未知使用 null，不使用零或模型默认值代替。数值使用声明的规范单位，不自动猜测换算。

- 长度 `L_um/R_s_um` 使用 um；扩散系数 `D_s_m2s` 使用 m2/s；面载 `loading_mg_cm2` 使用 mg/cm2。
- `active_fraction/conductive_fraction/binder_fraction`、`porosity`、`li_filling_fraction` 均为 0–1 小数。
- 多孔锂宿主计算必须明确 `anode.porosity_basis=before_loading`，并提供锂密度、比容量；这些物性不设默认值。
- 电解液添加剂、掺量等在 meaning 中明确分母；孔隙率注明载锂前后；厚度注明是否包含集流体。
- 文献值保留来源及工况；工程声明不等于实测。改变材料会清除未同步更新的该材料专属参数；被锁定的关联字段须先解锁。

派生计算记录依赖和缺口：孔隙体积、载锂后剩余孔隙、面容量、N/P、组件质量及能量预算。完整预算需要负极 composite_mass_mg_cm2、两侧集流体 cathode_mass_mg_cm2/anode_mass_mg_cm2、隔膜和电解液 mass_mg_cm2、封装 mass_mg_cm2、condition.average_discharge_voltage_V。缺依赖时不输出预算值。预算不代替实测电芯比能量。

## 阶段与报告

Stage 4 只维护材料方案及证据，已有有效几何直接继承。Stage 5A 匹配 PINN 且启用时进行参数提取和仿真，否则记录跳过；Stage 5B 始终尝试工艺检索、推荐生成和一致性检查。匹配后物理失败仍生成实验建议，但保留物理硬门禁，不能宣称性能通过。

实验检索复用原文，最多三个本地查询、新增八段、合并二十段/一万六千字符，不建新索引。一次生成、最多一次修正，无总耗时限制。重叠参数必须与方案表一致，非重叠工艺值宽松接受工程建议。设计容量等可用 budget_assumption 标注，不能作为实测或 PINN 物性输入。

推荐存 experiment_plan.json，检索证据存 experiment_evidence.json，两个子步骤状态存 stage5_execution.json。Stage 6 只校验并渲染推荐，缓存另存 experiment_report.json。旧完成/跳过态缺少实验方案时回到 Stage 5，保留前四阶段。

仅重新生成实验方案可使用 `abr-cli --goal "你的课题目标" --regenerate-experiment-plan`；该命令不会重跑材料 RAG 或 PINN。模型不可用且没有有效缓存时明确失败，不伪造推荐。失败记录存 experiment_plan_failure.json，不覆盖有效推荐。

历史快照位于 scheme_history；写入使用跨进程锁与原子替换。异常终止留下锁时，确认对应进程退出后再清理，不能绕过活跃写入。

## Stage 5 有限优化与实验局部修复

合法修订和择优回滚会同步补充声明 `scheme_parameters` 中同名几何字段，旧声明与原始引用存 geometry_parameter_history。只同步已授权几何路径，不忽略其他重复声明冲突，也不能绕过用户锁定。已有实验方案会先标记为待几何更新后重新绑定，避免旧实验正文反向冻结仿真优化。历史三次全部因重复几何声明冲突失败且无真实修订的检查点，允许一次自动恢复，原状态归档在 pinn_optimization_history。

Stage 5 收尾在仿真收敛后仍检查目标下限，未达标时主动调用几何修订和下一轮仿真，不依赖外层 ReAct。仅在 PINN 开启、体系匹配、输入身份有效时运行；已有达标结果、无数值目标、不适用模型或关闭物理时不启动优化。提案只允许两电极厚度、孔隙率、颗粒半径；材料、D_s、电压、倍率及锁定值保持不变，继续使用原有范围和来源校验。默认最多三次提案，失败提案也计入持久化预算，重启不能补发同一轮；有效结果仍按原规则归档、择优回滚。`pinn_optimization_state.json` 记录提案、拒绝原因、停止原因和预算。

实验推荐只首次生成完整方案。之后对错误参数、段落、缺失声明和引用清单签发精确路径，模型只能提交匹配基准哈希的 changes 补丁；已通过参数与段落不可覆盖。引用对象先转换为 passage_id 并核对归档来源，不因包装差异重生成全文。失败字段优先回到当前方案表引用；没有可用声明的字段标为待确定，保留其他详细步骤。原稿、错误路径、补丁和最终详细方案存 experiment_draft.json。通用回退模板仅用于首稿整体不可解析等情况，不能替代已有大量有效内容的详细方案。缓存版本更新使旧通用回退不再被当作修复后的完整方案直接复用。

## Stage 4 局部修复权限

同段多问题通过内部接口契约v1提交，不建立逐问题HTTP接口或逐项模型调用。`interface_contract` 记录文档/参数哈希、接口ID、原文起止位置、original、issue_ids、可修改字段及事务分组；模型仅返回 `{base_hash, submissions:[{interface_id,replacement,parameter_changes:[]}]}`，参数项为 `{path,field}`。不能提交自定义位置或扩大权限。

不重叠问题按原始位置从后向前合成段落；重叠问题合成联合接口，一次替换须同时解决全部问题。同段、共享参数及明确依赖的段落是一个事务，缺项、冲突值或任一问题复核失败时整组保留原文。独立事务可继续提交。旧整段补丁仅在接口唯一且范围外文字不变时转换，多个整段版本不猜测合并。

程序校验后，以一次 `repair_verify` 批量调用复核所有候选事务，返回逐问题 verdicts 与按事务定位的 new_issues；无有效复核、遗漏问题或出现新矛盾均不提交。通过的事务统一合并后再校验文档、参数及几何，最后一次原子写入检查点；失败组记录原因并重新定位，已提交组不再生成。接口、提交、校验、复核和提交状态归档在 repair_audit 中；三轮修复预算和报告优先机制保持不变。

正文按稳定位置 ID 分块（表格按行），审核返回 `repair_issues`：精确原句、原因、建议及必要参数路径。旧 `issues` 字符串摘要保留；没有唯一定位的问题不授予写权限。程序签发包含正文哈希、参数哈希、问题编号及允许块/字段的权限清单，模型只返回 JSON 补丁，不拥有文件写入工具。

可信合并器拒绝越权段落、全文替换、删除、标题变更、未知引用、过期版本和用户锁定字段；未授权块逐字不变。非锁定参数也必须在问题段落中明确定位，参数原始声明须出现在修复正文。仅明确引用同一字段路径的直接依赖自动纳入修复范围，模糊关联不扩大权限。参数更新仍由方案表服务重算派生量，并通过原有几何/证据/规则验收后才能定案。权限属于应用层数据合并约束，不是操作系统 ACL。

标题规范化和五段式验收共用定义；只重命名可唯一识别的标题。缺失章节允许新增实质正文，不覆盖原章节。引用字段统一为参数顶层 `source_quote` / `passage_id`，兼容历史嵌套值但冲突报错。抽取重试保留已通过字段，只修出错字段，诊断归档于设计契约。

`stage4_repair_checkpoint.json` 保存草稿、证据、参数、审核、补丁审计及三轮预算；重复工具调用和进程重启不能重置。缺证据时仅定向补检索，不重跑 Planner 或全文 Writer；审核服务失败只重试审核。预算耗尽或问题无法定位时保留未解决意见，不伪装通过。历史失败设计可作为修复基线导入；目标、方案表或语料指纹不匹配时停止并给出诊断。

确需完整重新设计时，由用户运行 `abr-cli --goal "你的课题目标" --rebuild-stage4`，再运行同课题 `--run`。前一个命令仅准备重建并保留旧修复检查点，不发起模型请求。正常修复不使用该命令。
