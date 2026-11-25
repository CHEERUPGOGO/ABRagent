# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AutoBatteryResearch Agent (ABRAgent) — an autonomous AI research agent for high-energy-density chemical battery R&D (lithium-metal, solid-state, high-nickel NCM). It runs a 6-stage gated workflow: literature ingestion → semantic vector indexing → material mining / cell assembly → multi-agent RAG design → SPM PINN physics simulation (enabled by default; skippable) → synthesis report.

Core design principle: **"LLM in the brain seat, workflow in the referee seat"** — the `ABRAgent` (LangChain/LangGraph ReAct loop) autonomously drives **all 6 stages**, while `StageManager` + deterministic `Checkers` act as guardrails that gate stage transitions. `ABRAgent.run_stream()` is the **single execution kernel** (the old no-LLM `AutonomousLoopRunner` was removed); deterministic executors in `_execute_agent_decision_loop` run after the LLM turn as a failure fallback (inspect-then-execute-if-missing, idempotent) for Stages 1–3, 5, and 6, while Stage 4 RAG fails closed offline when no LLM is available (offline tests mock/stub RAG or verify fail-closed rejection).

Comments, log messages, and docs are in Chinese; match that style.

最终研报分文件契约（渲染v45）：`final_research_report.md`止于第6节参考文献，执行履历、完整覆盖/审核/参数档案保存为`final_research_report_supplement.md`，正文不生成指向补充材料的提示句。两份文件绑定同一identity及report_record，缺失/过期/篡改补充材料拒绝终审；报告、补充材料及记录同批发布、失败回滚，恢复流程同步复制。Stage 6终审刷新补充材料内履历，保留旧单文件读取兼容；不改科研数据或提升审核。详见`docs/final_report_current_record.md`。

锂箔厚度契约（数值审计v5、报告渲染v44、v3编译器v4）：`L_um`表示电极有效层厚度，多孔极片为单面干涂层，`li_metal`负极为锂箔本体，提示词显式声明`anode.L_um`。有限别名`anode.li_foil_thickness_um`保留原路径/数值/来源并关联规范身份；不同材料、同组冲突、非法单位、锁定null及不同工况仍保护。几何恢复与构建共用已有致密锂箔孔隙率0规则，不覆盖显式孔隙率；报告按锂金属身份显示“锂箔厚度”，不套涂层称谓。旧档案可只读重验，不自动提升科学审核或重跑模型。

Writer自报锁定兼容：v2协议规范化忽略模型返回的布尔`locked`并以`ignored_writer_lock_claims`留档，原响应不变；已有方案表的用户锁定与锁定null仍由原规则校验，不能借此解锁或覆盖。其他权限字段及非法类型继续拒绝，恢复沿用原Writer响应和已用预算。详见`docs/protocol_and_experiment_recovery.md`。

参数命名契约（数值审计v4、报告渲染v43）：`parameter_names.py`在Writer生成前提供简短规范字段表（不是必填清单），返回后为有限明确别名登记`canonical_path`，规范字段不得改绑另一物理量。`total_loading_mg_cm2`与`loading_mg_cm2`共享总干涂层身份，活性载量单列；未锁定null可补有依据声明。正文引用、方案表展示、参数附录和缺项判断共用只读`effective_parameters`，同身份唯一有效值消解空占位，原路径/值/来源保留；冲突值、错误单位、锁定null、文献/对照/不同工况及用途不混用。表外字段不猜映射，不新增固定计算链。旧报告须重编译，不自动改档案或提升科学审核。详见`docs/numeric_consistency.md`。

运行预算统一配置：Stage 4 的协议/格式/正文修复、审核服务失败及v3角色配额均来自 `setting.yaml:runtime_options.stage4_repair`；补检索、查询、结果条数及上下文规模来自 `stage4_retrieval`，代码不再设置另一套预算硬上限。门禁与恢复使用当前配置，检查点只保留已用次数和有效配置快照，同一已占用请求不重发。外层阶段次数读取 `max_retries_per_stage`（显式构造参数优先）。详见 `docs/runtime_budgets.md`。

PINN正极母体匹配（渲染v41）：`material_ids.pinn_material_ids`按显式正极母体与实际负极查询注册表；预检、几何基准、范围、spec和推理共用口径。spec独立保存模型材料，完整掺杂配方仍由方案表呈现。只有母体已登记的旧任务可先查询模型，无对应组合为NOT_APPLICABLE；冲突/失效母体拒绝，已匹配时仍验证模型范围及训练包络。母体及来源参与Stage 5指纹，旧输入/结果需重新生成。

Stage 5推荐交付边界（渲染v40）：`experiment_delivery.py`只读区分执行缺项和后续验证。已解析objective的gap可显式声明`gap_kind=validation`，须保留方法/条件/基线/判据/原因/后续安排，并以`recommendation_refs`引用已给出的当前主推荐；最优性未实验确认本身不触发FALLBACK，未知责任/缺推荐/缺执行条件仍阻断。未使用且未锁定的留空电极`D_s_m2s`只作待测物性说明；操作/判据引用、非法值或必需PINN失败仍保护。执行器、Checker、恢复和报告共用分类，原gap/null/科学审核保留；非阻断事项独立展示，不将推荐交付说成性能已证实。

报告渲染v39：当前规则建议在附录独立列为非阻断信息，正文和附录共用报告记录中的规则结果；完整规则结果进入报告指纹，建议变化也须重新编译。刷新规则判定不自动提升既有科学审核状态。

规则v2.4（`C1-C8/v2.4-c1-c3-advisory`）：C1高压正极碳酸酯界面、C3锂金属界面均为`recommend`建议，记录在`recommendations`，不产生硬违规、不强制降低置信度，也不因此给碳酸酯文献降权；措施相关文献仍可加权。Reviewer接收分级结果，未采纳建议本身不得转为阻断问题，具体科学主张仍独立审核。C2保留原质询，C0/C7等原硬门禁继续执行。规则版本参与缓存溯源，历史审核/报告不会因改规则而自动获批，须按当前输入重新核验后发布。

Stage 4内容契约v3（显式启用，默认v2，报告渲染v34）：`runtime_options.stage4_writer.contract_version: 2|3`，新设计会话可用v3，旧会话固定契约、分析不迁移。`util/stage4_document.py`纯编译轻量段落/列表/表格树及稳定叶引用，参数权威仍为scheme_ledger；`stage4_research_projection.py`按显式关联复制正文并按实际字段依赖绑定支持，省去重复研究提取与整合改写；`stage4_consistency.py`持久占用Writer/修复/科学复核/终审机会，失败中断不重发；`stage4_session.py`运行完整生成→编译→科学审核→有限内容树补丁→独立终审。`stage4_publication.py`在课题锁下隔离准备方案提交，日志保护多文件逐个原子替换，门禁拒绝混合/未完成版本，恢复不覆盖外部写入。Stage 5修订/回滚刷新正文与研究字段支持，最终选定最多一次只读一致性复核，不恢复旧几何科学支持；Stage 6直接呈现当前编译正文。未完成18次真实模型对照验收，默认不启用v3。详见`docs/stage4_content_v3.md`。

要求层级v1（报告v23）：`requirement_structure.py`验证groups树、items.group_id及复合bindings；上层目标不重复计入回答/实验，明确同义项合并且保留来源，原文覆盖忽略孤立连接词而保留否定。固定电压窗口一项绑定上下限，倍率定义及母体式使用规范字段；无法绑定明确pending，不静默降为工程建议。用户锁定同时拦截值、单位与明确别名绕过。`--reparse-requirements`可迁移既有v2无层级任务，副本加锁只接受原文一致字段，保留原科研正文/值/单位/预算及旧回答测试归档，终审后提交。

需求局部恢复保留有效分组树；个别分组或条目失效只进入显式待解析组，不清空整树或保留悬空group_id。总任务补丁允许扩充经原文定位的source_quotes，保留原描述、已接受条目和旧来源；孤立“以”“为目标”不另建要求，否定仍保留。`util/requirement_archive_repair.py`可从同课题trace中的Planner及requirements_repair原响应零模型修复无下游v2档案：只移除已确认连接词，实质要求ID/正文/方案表/预算不变，副本门禁验收后备份发布；科学审核不提升。

主方案投影维护（渲染v35）：明确别名`cathode.doping.nominal_formula`与`separator.type`分别投影名义式和隔膜材料；`compaction_density_g_cm3`参与规范压实密度核对。`util/design_projection_repair.py`仅按原文明确活性载量修复误占总载量字段，总载量按活性/质量分数确定性计算；厚度与已有压实密度必须同时一致。常规和高压应力窗口分别绑定上下限，显式锂对电极半电池不把缺N/P当匹配失败。已有v2档案在副本同步正文引用、方案表、实验、物理不适用状态与报告，通过4/5/6门禁后备份发布；不生成物性、不重置模型预算，不提升科学审核。

研究来源实质一致性（渲染v36）：`research_sources.py`以程序章节ID/段序号保存来源位置，兼容Unicode/空白与不改变关键事实的解释性增补，材料、数值、单位、否定及验证程度变化不靠相似度放行。摘要审核按字段实际来源段落/参数/文献及相关要求绑定，无关段落不牵连失效；旧审核仍须原指纹完整匹配。参数漏绑可补其现有程序引用原句，不能仅凭同值猜来源。部分arguments存在时仍补充未覆盖的原设计论证；完整原设计进入整合来源包。覆盖/限制ID遗漏先交独立Reviewer核对实际表述，无明显实质不一致且边界已说明可通过，格式与风格建议不否决。`research_consistency_recovery.py`显式维护使用独立留档调用与副本验收，不重置原Stage 4预算；旧科学意见保留。

第二部分只呈现材料选择、机理、候选取舍、掺量依据与科学证据边界；具体实验方案统一放在第五部分。整合与Reviewer提示词均禁止在第二部分安排或诊断实验编号、梯度点、制备热程、装配测试流程及放行判据。来源限制仍逐项核验：科学限制保留，纯操作事项按第五部分职责处理，混合事项只保留科学影响；不得依据原设计稿的旧组号否定当前实验计划。文献原工况可用于解释证据适用范围，不能写成当前实验安排。

固定条件渲染v24由方案表核对值/单位/锁定，一致显示“方案已固定/条件一致”，旧研究摘要提取错误不影响条件声明；科学结论及实验完成状态仍独立。

倍率定义输入规范化支持全角比较符及数值字段JSON字符串；只有用户原句显式 `1 C=数值 mA/g` 才补 `condition.C_rate_reference_current`，不推断倍率或容量。解析失败固定约束保留pending绑定说明。正文当前装配/用户锁定段中的明确金属锂对电极由 `declared_anode_parameters` 离线补提，排除文献/风险/备选/否定且不推断几何；Li金属/锂片等规范为li_metal。`--repair-stage4-inputs`只对无下游产物的既有课题在副本补这两项，重新验C0–C8、研究绑定、检查点身份及Stage 4门禁后回滚保护提交，不重置预算或推进阶段。

新生成课题增加通用研究契约v1（`util/research_contract.py`）：Planner语义提取原文要求，程序验证位置/条件/引用，Reviewer独立检查原始课题覆盖。方案维护在审核前记录完整主方案与结构化回应，已有局部修复机制仍负责变更；Stage 5B v4用主方案ref、分组与目标测试关联，Stage 6确定性输出覆盖表和候选表。完整审核在新报告第6节附录，旧无requirements产物保留第2.6节路径。渲染版本19，论证、条件、实验步骤使用序号及语义缩进；参数通过`parameter_presentation`只读归并，摘要精简、完整来源留附录，掺量口径含混时不换算百分比；纯分析任务不强行补全电芯或仿真。详见 `docs/research_report_contract.md`。

P0可靠性：需求逐项保留，verification_needed=null为未知且仍需测试关联/缺口说明，历史unparsed中的false不免除验证；research记录逐项验收及重试，只有失败记录降级，不清空其他有效回答/候选/论证。部分解析同样用已声明字段建立主方案，禁止回到关键词猜配方。Stage 5B前生成consistency与pending_operations，材料身份只读引用，对照必须绑定group_id及规范变化；有效组别与目标测试在局部修复中保留。Stage 5A 注册表确认材料组合无匹配模型时记 NOT_APPLICABLE 并通过。Stage 5B 按实验参数与方案表的一致性验收，待登记对照和测试覆盖缺口在研报披露。详见`docs/p0_reliability.md`。

Stage 4通过`util/adaptive_retrieval.py`有限改善证据：默认补检索2轮、首次整体严重偏差最多重建1轮、每轮3查询，原策略服务重试全任务最多1次。首次评估/首个有效审核才允许重建，至少一半且不少于两项核心证据要求受影响；`evidence_needed=false`不计分母。后续失败不升级完整检索。预算、签名、窗口和增益在检索/修复检查点持久化，中断不自动重发；文本修复3轮、审核6轮独立计数。完整档案与25段模型包分开，问题引用/有效新增证据优先，修复和复核共享预选包；检索不授予主方案修改权限。有效待补动作会阻止Stage 4提前推进；陈旧缓存拒绝恢复，停止/耗尽后附条件交付。报告指纹含检索状态，附录完整披露。

Stage 4 局部修复由 `util/design_repair.py` 可信合并器执行：Reviewer 的精确原句、原因、建议经程序定位后签发块/字段白名单；Writer 只返回带基准哈希的补丁，不能全文替换、删除或更改锁定字段。缺失标题/章节共用 `util/design_sections.py` 定义；字段引用统一在参数顶层并兼容旧嵌套。检查点保存草稿、证据、审核与三轮预算，重复调用不重跑完整 RAG；只有用户 `--rebuild-stage4` 可准备新的完整设计。详见 `docs/scheme_ledger.md`。

Stage 4程序章节v1：设计类Writer返回五节内容JSON，`util/design_structure.py`在任何提取前生成固定主标题、子节层级/编号和稳定ID引用；分析类任务保留大纲。原始JSON归档，后续只修已渲染正文，标题/锚点冻结；空叶节、重复ID、失效引用及结构清单不一致均拦截。`normalize_titles`不再猜测改名。`--restore-stage4-titles`用原稿和成功补丁哈希链恢复旧标题，在副本中沿用剩余审核预算、归档失效队列、同步实验绑定并终审发布，不重写科学正文或重置预算。详见`docs/design_structure.md`。

Writer小标题格式：v2及可选v3生成和协议修复共用`util/writer_titles.py`的单行纯文本契约，科学量值标题不按编号误杀。编译前按`TITLE_*`一次返回所有错误的准确路径/原值/原因，模型仅用`titles`定点补丁修失败标题；基准哈希、原值与格式复验，正文/参数/ID不变。纯包装修复确定性核对，有歧义的数字前缀不能静默删除，名称变化另经留档`writer_title_verify`，复用各自协议预算且中断不重发；旧空稿标题失败可按原响应和原计数重验，已有下游不自动改写，编译后标题继续冻结。v3编译器版本5。

模型计算契约（数值审计v3、渲染v42）：方案预算与换算由Writer明确给出方法、输入来源、代入、结果/单位和适用口径，Reviewer独立复核；`model_calculations.py`保留方法描述，不是执行图。几何只继承明确声明，不按载量和密度自动补出厚度；主流程不运行固定派生链、不生成`{{derived:计算ID}}`、不因缺某个规范计算字段阻断。下游采用的计算结果可登记为普通工程参数，source保留方法与输入；既有`{{param:字段}}`、值/单位/同义冲突、上下限和用户锁定仍保护。`numeric_consistency.audit`与`scheme_ledger.calculate`默认只检查声明；旧公式仅在显式`legacy_calculations=True`的历史工具中保留，不接入当前审核/报告。旧abr-derived显示不自动重算，授权修复可改为完整模型计算说明；不能借此删除/改绑原始参数引用。正文计算值未绑定本身不报错，旧链缺项不自动变为新必填参数；原科学意见不自动结案。v2/v3共用政策，PINN仿真和C1–C8规则不改。旧报告需显式维护，不重置预算或自动提升审核。详见`docs/numeric_consistency.md`。

来源同步与属性权限（问题接口v3）：`source_metadata.py`按同一原稿的唯一段落/编辑位置，将受正文修改影响的工程参数source_quote同步到候选稿；文献原文、用户条件和锁定来源不随正文变更。已失效来源仅可通过source_fields签发的source_changes更新source_quote，限定字段引用、段落及旧来源哈希，不授予改值/单位/身份权限。`research_source_metadata.py`为已定位的研究记录来源失败或Reviewer明确记录ID签发source_quotes/field_support权限，固定科研文本、状态、引用和身份；正文源变化清除旧支持标记。repair_verify接收同步后的候选参数与来源差异，保留original_parameters作比较；正文/来源依赖同事务、独立问题可部分提交。来源更正归档在repair_audit，沿用正文修复和审核预算，不自动提升科学状态、不重写旧任务。详见`docs/source_metadata.md`。

来源实测补充：旧来源句已不在正文时，parameter_source_paths按已有参数身份签发只含source_quote的专用接口；程序也可为失效工程来源建立精确字段问题，不靠伪造quote扩展正文权限。研究记录的{{字段路径}}是合法绑定，审核按当前值解读。研究副本的scheme及scheme_parameters同步当前参数；科研审核指纹规范1/1.0等同量值表示并兼容旧签名，真实量值或条件变化仍失效。

正文与研究摘要同步纠错：`research_text_repair.py`仅按原field_support来源与正文接口精确授权范围相交签发`research_text_permissions`，Writer可在该正文submission的`research_changes`修改获准非空科研字段，完整记录哈希/字段原值绑定基准。新来源限原绑定依据的同稿迁移且须实际修改，身份/要求绑定/名称排序/状态/验证/参数和文献元数据仍固定。正文、同记录来源及摘要共用事务，在原一次repair_verify中对实际变化字段逐项校验change_hash及supported；遗漏/否决整组回滚，完整记录隔离不算通过。审计沿用research_source_updates并保留changed_fields，清旧支持/审核签名、沿用原预算与恢复，不自动改写旧报告；v3投影仍由其原生内容树维护。详见`docs/source_metadata.md`。

材料引用同步：Writer接收及默认v2审核检查点保存时，`numeric_bindings.normalize_material_references`一并返回规范材料参数与正文；内部ID仍用于匹配，正文显示经同一身份核验的原始声明（含已有物性说明），来源及原始JSON保留。旧程序规范名称可按已有original_value同步，未知文本差异/物性改值/真实换材料继续拒绝；非材料字段不使用original_value替代当前值。详见`docs/material_identity.md`。

电解液组成解析器v2兼容`EC+DMC（EC:DMC=1:1 体积比）`等带名称的比例；名称、顺序及比例项数必须对应全部主体溶剂，不重排或推断口径。未知/重复名称、缺项、非法值、口径冲突和未解析尾部继续拒绝，原配方保留，历史审核状态不自动提升。详见`docs/material_identity.md`。

默认v2正文修复连续失败审查：同一类型/原句/关联字段的问题连续两轮未修复时，`repair_diagnosis.py`调用一次独立`repair_case_review`，传入当前段落、数值计算路径、两轮补丁及拒绝原因。repair指导剩余轮次、recheck在原科学审核预算内复核误判、defer停止无效重试但保留原问题；诊断不直接改稿或授予通过。调用及原响应存repair_diagnoses，恢复不重发。参数补丁兼容嵌套field和扁平写法，原锁定/来源/科学复核仍生效。详见`docs/protocol_and_experiment_recovery.md`。

Writer协议恢复v2/实验状态v30：生成前保存检查点，原始响应与YAML指定轮次的协议补丁保留在writer_protocol；repair_history按轮保存补丁及独立引用复核，每轮只修剩余错误，重启复用已存调用。兼容数组/点分参数路径、fraction→ratio、文字类型省略和同值重复声明；新user_requirement保留来源但不自动锁定或视为已审核。ID大小写与等价单位可确定性恢复，模型仅修局部路径/元数据/引用，不改科研数值与来源；无修改补丁或语义否决停止恢复。空稿仍持久化，停止无效几何/参数提取。Stage 5B未锁定null不是冲突基准，无单位对照的null/空串等价；锁定null/真实值/材料身份仍保护。局部失败撤下错误参数与依赖步骤，原文留pending档案，终验异常也保存。执行器和门禁共用实验PASSED/CONDITIONAL/FALLBACK判定，工程模板不可因空pending_fields被标通过。详见`docs/protocol_and_experiment_recovery.md`。

Writer用途与倍率兼容：误置于quantity_kind的target/lower_bound/upper_bound/experiment_max转存quantity_role，与既有用途冲突拒绝，不推断比例种类。仅规范倍率身份、有限正值、原meaning明确倍率且无冲突/否定/待确认、未锁定新声明的空单位可补C；normalize及metadata_patch共用can_complete_c_rate_unit。显式单位、既有锁定缺项、参考电流和物理数值不变，原响应/预算/科研审核保留。

重生成监控补充：完整文字用户条件同值且内含既有锁定单位时，Planner省略独立unit可沿用旧单位并同步要求绑定，不能改值或显式单位。Writer接口误报stop但JSON中途结束时，保留原响应并仅用尚未消耗的第二次完整输出机会（调用前持久化，不重置科研预算）；空稿不反复触发无效补检索。正文未引用的新参数仍拒绝，不能为交付而自动删除声明或猜测绑定。

Writer引用修复v2：协议错误一次汇总参数、正文引用及全部未绑定字段；`writer_references.py`按精确body路径和唯一原句将等值量值/原声明文字替换为参数引用，不改值、来源、工况、标题或已有标记。正文绑定经程序核对单位/分母后独立`writer_reference_verify`逐项审核语义归属，基准哈希和调用机会按轮持久化，中断不重发；与元数据修复共用YAML的protocol_rounds协议补丁总预算，历史计数保留。合法局部补丁继续保留，后续只修剩余问题，成功后进入原学术审核流程。

Writer缺失内部闭括号仅在解析器报错点、字符串外且堆栈唯一确定时补一个字符，完整JSON与五节须同时成立；原响应、位置和哈希归档，截断尾部/重复键/正文不猜修。面容量等预算按模型计算契约给出方法与结果；没有明确同工况比容量就说明缺口，不能借用1 C电流定义。

Stage 4流程完整性：研究审核指纹规范空conditions并有限兼容旧写表默认值，真实值/单位/工况变化仍失效；锁定文字可增加提及但原出现位置须保留，完整声明由事务复核保护。整合审核严格验证外层JSON及逐项结构，唯一内部闭括号可离线恢复；纯协议失败只用剩余审核次数重审原稿，不重生成。检查点/适配器可按已有主工况参数确定性构建几何，无需新模型调用；缺项、非法值、D_s证据及锁定null仍拒绝。候选name与candidate_role/display_name分离，材料名和角色必须同原句有据，不自动恢复首选。详见`docs/stage4_flow_integrity.md`。

候选展示元数据兼容：`candidate_metadata.py`允许同一表格行/分句中有依据的简称和排行标签（如Rank 1 Zn–Na），display_name无需等于材料名＋角色。角色缺省可保留为空，只有同条来源明确且与scope一致的唯一角色才补齐；不按排行猜主推身份。显式角色、材料身份与排行仍核对来源，数字系数/循环次数不能作排序依据，错材料/错排行/新增科学结论拒绝。旧候选格式错误可在collect_research重新验收；仅原已登记首选被隔离且重新满足全部当前身份引用时恢复该引用，显式清空和新选项不推断。元数据修复不授予科学审核，不改正文、材料或证据。详见`docs/research_report_contract.md`。

Stage 5B已回退完整JSON生成：`experiment_design.generate`恢复程序组装之前的SYSTEM_PROMPT，由模型一次返回完整参数、六节正文、分组、目标安排及引用，`experiment_repair`最多一次局部补丁。默认不再运行分批槽位生成器或额外全文科学审核；`experiment_review`只用于既有程序化产物的科学审核兼容读取，当前主方案一致性核对见下段。原输入身份v9兼容v7/v8旧缓存；显式`--regenerate-experiment-plan`备份旧计划和程序化检查点后生成新稿，成功时撤下旧检查点。新生成不伪造generation_contract或科学审核通过状态。详见`docs/stage5_program_assembly.md`。

Stage 5主方案一致性（实验schema v5、输入v10、渲染v37）：保留完整JSON生成，由`experiment_alignment.py`从当前方案声明建立按制备/装配/测试/判据分区的继承清单；遗漏重复文字不丢失已定条件，备选路线不晋升主路线。本征物性不强制转为操作。`{{profile:main_cycle|high_voltage|formation}}`只展开各工况已声明字段，程序检查工况错配与“已定值写未定案”。`experiment_alignment_review.py`只核对主方案与实验的明确语义矛盾，最多两次审核及一次白名单文字补丁；按输入签名持久保存调用，不重发中断，不改变科学审核。无模型/协议失败保留待核对状态。旧schema只读兼容，显式`--sync-experiment-plan`在副本同步、局部修订并审核，4/5/6验收后加锁发布，不重跑设计或仿真；真实物性缺项仍留空。`issue_resolution.py`仅对经当前数值核对证明已解决的旧载量诊断登记结案，保留原意见与依赖指纹，参数变化即失效；科学与工艺适用性意见不被自动清除。详见`docs/stage5_main_scheme_alignment.md`。

统一最终报告记录（渲染v38）：`report_record.py`在课题锁内读取并校验当前设计、方案表、实验计划、Stage 5执行状态、有效仿真及来源版本，形成`report_record.json`；最新指被当前方案选定且引用一致，不能按mtime挑选。全篇渲染共用该记录，摘要从完整参数读取比例，附录当前回应定位本版论证及实验分组，不重复旧Stage 4执行提案；原回答与旧审核保存在同一记录的来源档案，科学保留状态不提升。报告和记录按来源哈希联合发布，输入变化或失败回滚；Checker验证记录ID、当前来源、重建投影和章节。Stage 6完成日志属于输出，不使自身失效，前五阶段日志变化仍失效。用户目标引用保留比较符与显著性，未报告倍率不能断言为非1 C。所有副本维护发布须同时携带report_record.json。详见`docs/final_report_current_record.md`。

Stage 5B参数缺项v2（报告v33）：完整JSON生成保留`experiment_values.py`的值检查和独立缺项登记，数值量纲不可用未知说明冒充，范围/序列使用明确类型。`pending_parameters`保留原文、哈希、原因与获取方法，不进入活动参数和bindings；`pending:`仅显示缺项。报告仍展示各目标已有方法与限制。真实参数/单位/锁定冲突继续保护；旧依赖和审核只读兼容，不恢复旧目标状态或补授审核。`--repair-experiment-values`仍通过副本及Stage 5/6验收后发布，失败回滚。

Stage 5B误转换/对照校验补充：旧字符串序列只接受明确JSON数组；分数、千位分隔、裸逗号/斜杠列表不推断数组，保持原文待确认。`groups.changes`在入口、局部恢复、缓存/终验与状态判断共用值及单位校验，缺requirements也不能绕过；坏对照与显式依赖参数隔离，不退回主组值。旧检查点恢复发现坏对照时保留原响应/预算并阻断后续调用。此项不改科研判据、材料选择或科学审核范围。

Stage 5B审核回写边界：`report_readiness.issues`中的`实验交付限制：`是本轮实验输出的汇总，`experiment_consistency.preflight`和实验冲突提示不把它重新识别为输入方案冲突；完整科学意见仍在实验审核与报告披露。Stage 4独立意见、方案表与真实值/单位冲突继续核对，避免输出汇总改变快照和缓存身份后触发重复生成。

Stage 4 格式与完整性分离：`design_sections.classify_design_output`区分可唯一定位的局部粗体错误和截断/缺章。表格单元格末尾孤立`**`只删除两字符，唯一非末行且有句末标点的普通正文只补闭标记，参数/文字/引用不变；末尾半句、多处歧义、未闭合围栏及章节缺失仍阻断学术审核。局部格式不阻断Reviewer，程序独立`format_repair_rounds`最多三次，不占正文`repair_rounds`三次或审核六次；格式失败/耗尽仍审核科学内容并保留格式诊断，不能APPROVED。尝试签名调用前持久化，同版本失败不自动重放；历史正文计数原样保留、不推测退还预算。代码围栏和行内代码不参与粗体统计。无下游产物时适配器和门禁共同要求恢复未耗尽的格式修复；已有报告用`--resume-stage4-review`，在`stage4_review_history`副本恢复原预算、冻结当前方案，阶段和报告验收后回滚保护提交。科学否决仍保留。

同段多问题使用 `util/repair_interfaces.py` 的问题接口事务v3：程序为唯一原句签发精确范围与接口ID；重叠范围合为联合接口，同一问题多处原子提交。模型批量提交submissions后，按实际改动范围、参数读写（含规范别名、正文输入引用和ratio_inputs）、显式上下限约束及来源原句依赖重分组；仅同段或共享只读字段不强制绑定。独立事务可分别接受，通过的同段片段基于原始坐标一次合成，不拼接多个整段版本。一次`repair_verify`复核各事务及合并预览，包括模型计算的算术与口径；事务全部通过才合并，最后全量校验及一次写入。失败只重试剩余问题，审计保留实际依赖与COMMITTED/REJECTED/ROLLED_BACK状态；原预算、锁定和原始参数引用保护不变。

Stage 4 提交可靠性：`scheme_ledger.project_scheme_parameters`由预检查/正式提交共用，程序几何投影带规范单位并保留当前来源/条件/锁定，同值主工况物理别名可引用来源，真实数值/显式错误单位仍拦截。Planner原文绑定通过`persist_requirement_bindings`独立登记，候选工程提交失败不撤销用户固定条件。跨行同问题使用Reviewer的`quotes`数组逐处唯一定位后组成事务，不按排名或关键词扩权；任一原句无效即不授权。已有单位投影误报的诊断任务用`--repair-stage4-submission`从拒绝候选与检查点零模型恢复，在副本验Stage 4并回滚保护发布；已有下游产物拒绝局部恢复，科学审核、修复/检索预算及阶段状态不变。

Stage 6 正文由确定性视图组织：摘要先给候选与结果；材料论证按依据、取舍、推断分组；规格区分工程声明、模型输入和待补数据；实验段落按原顺序编号。完整审核与交付意见去重后集中在第 2.6 节，检查器仍要求每项完整披露。报告温度显示为摄氏度，方案与实验 JSON 保留原始单位；排版变更须递增 `report_narrative.RENDER_VERSION` 并同步渲染验收，不重跑 RAG 或 Stage 5B。

选材理由渲染v13：通用课题第2.4节明确呈现首选的rationale、引用和risks；结构化arguments缺失时，第2.3节通过material_arguments只读恢复原文论证，并保留同段体系/条件限定。未知引用、旧规格和单列装配条件不纳入理由；摘录不提升为已审核结论，不恢复首选或排名。只需重渲染Stage 6；旧无requirements路径保持兼容。

多段摘要v17：`research_support.py`支持source_quotes与field_support，原句逐字定位而摘要允许忠实归纳。现有Reviewer逐字段返回research_support_review，程序将审核绑定摘要/正文/参数/文献指纹，变更即失效；未核验字段回退原文，不提升验证层级。Stage 4维护总预算两次，审核前刷新失效记录，Adapter后提取无审核标记即待核验。局部重试可只返回research。`--repair-research-records`固定既有方案，在任务副本恢复/审核研究记录并生成终审后替换报告，原始产物备份至research_repair_history；失败不覆盖。候选比较2.2始终显示。

研究提取与恢复：新模型记录只用`source_quotes/field_support`，普通参数仍用`source_quote`。单来源模型输出兼容为待审摘要，不能继承逐字摘录的可信性；历史有效摘录保持旧校验。`scheme_ledger.restore_research_cache`逐项重验来源、当前声明、锁定和文献，正文不完全一致时仍保留有效记录；旧失败原记录可从维护审计恢复。程序gap不覆盖真实失败原因，零维护预算仍执行离线重验，不重新请求模型或把摘要标成已验证。

研究同步修复：Stage 4两次维护预算分为初稿一次与收尾一次；中间正文补丁只离线重验，收尾按最终正文/记录/参数/证据签名决定是否需复核，不能以最后一条补丁APPLIED或REJECTED判断。审核签名及漏返字段补审占用持久化预算，补审最多一次且计入六轮审核总额，重启不重发；预算耗尽仍更新记录并保留待审。来源定位仅兼容成对加粗/行内代码标记，必须唯一匹配并保存正文真实摘录及坐标，数值/否定/条件变化不模糊匹配。已补齐的漏审诊断进入`research_diagnostic_history`，科学争议继续保留，Stage 6按当前字段审核状态展示。

研究审核选包优先使用结构化`citations`，不能从研究JSON数组的方括号文本反推引用；仍受25段/60000字符上限限制，未入包不得授予supported。显式恢复在提取结束后重验实际保留记录，拒绝的重复提交留维护审计，不继续污染当前记录错误清单。

连贯论证v1（渲染v26）：Stage 4正文及研究记录收尾后由`util/research_narrative.py`按原始prompt生成主题自然段，段落绑定requirement/source/parameter/citation/limitation IDs；内部逐项记录只读。Reviewer以`review_research_narrative`独立核对覆盖、逐段支持与每项未解决限制，生成与审核各最多两次（独立于原正文修复预算，调用前持久化）；中断/耗尽不自动重发。摘要只保留主要结论、证据边界和下一步，完整覆盖表、逐项回应及候选表始终移附录。`current_narrative`仍只接受整篇通过；`display_narrative`在双指纹和来源有效时逐段展示，通过段落正常呈现，待复核段落用引用块保留原稿并标具体原因，整体覆盖/未定位问题另行披露，不提升原审核状态。输入、原句、参数、证据或科学审核变化使旧稿失效；无有效当前整合稿时正文只保留归档论证与方案说明，不恢复冗长逐项回应。`--integrate-research-narrative`仅补整合与独立审核，在副本通过终审后更新旧课题报告，不重检索、不改配方或Stage 5方案。

需求审核边界：`requirement_review.py`验证`misread_evidence`的用户原句、正文错误解释及固定字段expected/actual值和单位。固定材料一致但科学解释有争议，只进入正文/摘要审核；只有已定位且值/单位确有差异的`user_binding`误读可阻断对应字段。无证据旧ID保留为reported/待复核，不拥有字段阻断权限。Adapter/commit/finalize均按当前输入重验；诊断回退保留失败设计与未受影响研究记录，不整批替换gap。详见`docs/research_report_contract.md`。

主方案表述v18：第2.4节从方案表独立呈现当前组成，选材记录可按引用的当前名义式唯一关联，必须来源可定位且绑定未过期；仅用于报告展示，不恢复首选标识或排名。理由、风险分别展示通过摘要或原文与科学核验原因，身份关联诊断留附录。Stage 6只读重渲染。

材料身份v19：`util/material_ids.py`复用既有别名规则统一结构化value，原名存original_value，原文不替换。材料先归一再构建几何，契约绑定material_ids与模型基准版本；局部合并不得写回别名。同义转换保留锁定和关联字段，真实换材料须重验几何。`--migrate-material-ids [--dry-run]`在副本迁移当前方案、检查点及实验绑定并终审；旧快照和陈旧仿真归档，显式越界设计值保持不变。输入阻断原因进入报告并按指纹验收。详见`docs/material_identity.md`。

组件身份组装统一用 `material_ids.assemble_components`：显式 `.material` 优先，缺失可引用完整名义式/配方（含 `canonical_path`）；身份来源保存在 `scheme.component_identities`，`flatten_scheme` 回到真实来源字段，不补造 `.material`。完整组成只精确匹配别名，备选/条件性基线及零散配方参数不晋升主方案；已知冲突拒绝提交，用户锁定与旧几何材料绑定仍有效。

母体规则匹配v2.2：明确的电极 `base_formula`/`parent_formula`/`mother_formula` 通过已验收参数投影为 `scheme.rule_parents`，绑定当前完整材料和原字段；C0及C1–C8按该母体识别类别和相容性，掺杂名义式无需独立登记。未知、冲突或失效母体仍拦截。`material_matches.basis=parent`、Reviewer提示及报告说明保留母体匹配边界，容量、实测物性及协同/性能结论仍须独立证据；PINN另按显式正极母体与实际负极查询注册表。C0表示登记不足，不能说成实验不可行。母体变动使规则签名失效，历史报告需显式重建/同步，不能只改审核状态。

电解液组成分类v2.3（报告渲染v28）：`util/electrolyte_composition.py`共用确定性解析，完整字符串保留，mol/L、M、mol·L⁻¹归一而mol/kg/m独立；EC+DMC均为溶剂，比例无则留空，添加剂/稀释剂及加量单列。`data/electrolyte_families.json`显式限制分类范围（当前为1 mol/L LiPF6 + EC/DMC/EMC/DEC及列明添加剂），不得凭浓度推断高浓或LHCE，多盐/未知/否定/不完整尾部不借盐子串放行。已登记具体配方仍可精确匹配，M/m不得混淆。`material_matches.basis=composition`披露解析及规则类别，只消除登记缺口，C1–C3和性能审核继续；分类不改配方、不继承性能或PINN资格。规则版本`C1-C8/v2.4-c1-c3-advisory`，分类表须随wheel分发，旧课题不自动改写审核。

报告渲染v22：`component_identity_text`只读复用组件投影，摘要/2.4/3.1展示完整名义式或配方，类别名与具体组成并存时都保留；不因缺少 `.material` 显示组件未定，也不补写参数。空单位null不渲染为None。研究恢复首轮提供当前失败记录清单，仍须逐字来源定位及支持性复核，不放宽gap验收。

第2.4节样品编号可通过当前名义式引用及支持编号/公式的逐字原句唯一关联，完整记录验收仍必需，不恢复首选或排名。研究恢复最多补一次漏返字段审核，仅合并匹配指纹和白名单字段，已审核状态不覆盖；不支持或缺审核继续待核验。

第5节段落主题由Stage 5B在原调用中生成可选`step_titles`，程序以原段落哈希绑定；局部修复改写正文后旧标题自动失效。`util/experiment_titles.py`校验短标题及正文术语，拒绝标题中的数值结果、额外声明和Markdown。旧记录或无效标题只读回退到明确的开头主题/动作，无法确认时用中性编号，不扫描前45字关键词；同主题分段允许重名。标题纳入实验哈希，Stage 6不增加模型调用，不变更配方与正文。

实验方法排版v14：`util/experiment_protocol.py`参考示例文档的分级方法标题与悬挂编号步骤，连续同主题段落归并，只在明确标点/顺序动作边界分步，条件句、括号、来源和原顺序保留。样品编号/组成来自结构化groups；未登记对照单列待确认，不让主组引用被写成“0.03=0”。新稿的重复赋值冲突进入既有局部修复，旧稿只读提示；审核已声明的配方冲突在操作前与相关方法旁标注。直径可等值显示为mm，源参数不变。详见`docs/experimental_protocol_format.md`。

第三部分为“电芯总体方案与关键匹配”：总体组合、极片与容量匹配、电解液与配套、预算与待决事项。条件性基线不能拼接成定案配方；缺少 N/P 不以厚度推断匹配成功。方案表中的 R_s / D_s 在第四部分统一声明，即使未仿真也保留，由报告检查器验收。

研报v16：第二节由`material_reasoning.py`按决策环节组织归档原句，去掉逐证据通用小标题；显式原文决策链保留引用、对象/条件和未验证边界，不新增材料推断或消除审核争议。第三节通过`design_presentation.py`分组显示名义组成、改性、极片、配套、工况和预算，关键值加粗，废除逐字段“规格N”布局。仍复用参数语义视图，完整来源留附录；不同口径和条件不强行合并。Stage 6只读重排，不重跑RAG/实验模型。

## Commands

参数协议扩展权限v1：`writer_protocol_advice.py`先对报错新字段生成有限转换候选，调用`writer_protocol_advice`给出逐字段建议及精确正文定位，程序再签发绑定正文/方案基准的correction编号。只处理明确百分比同分母分数表示、完整有限数值字符串，以及正文明确采用且来源已归档的文献取值转待验证工程建议；不能改原source、样品、工况、分母、候选角色或已有/锁定方案。补丁仅选择编号，另由`writer_protocol_verify`只读复核实际采用与等值含义，否决/缺项/中断不合并。建议、补丁、复核归档在原repair_history，每轮共用YAML protocol_rounds额度、调用前占用且恢复不重发。旧无权限空补丁可在显式恢复时使用原剩余机会进入新授权；新空补丁仍停止。协议通过不授予科学支持，也不自动重写既有报告。

参数作用域权限v2：本轮报错且已引用的新工况声明可由程序签发`scope_relocation`编号，迁入同组件稳定独立路径并保留原`canonical_path`；value/unit/basis/source/conditions/scope/group_id/purpose等不改，原字段类型/单位/范围、已有方案和锁定别名仍验。程序只同步param、字段级derived引用及ratio_inputs依赖，模型不得自由改路径或删除条件；建议与独立复核均必需，专属工况不晋升主方案。旧v1历史按原权限重放，不补发建议；旧空补丁遇新增作用域权限时只在剩余YAML轮次内新开v2尝试，预算耗尽/语义否决/中断不重试。详见`docs/protocol_and_experiment_recovery.md`。

要求拆分v2（报告渲染v21）：Planner按独立验收内容生成task总任务与items；泛泛动作/研究对象不进入回应，同对象/指标/条件/强度的同义表述合并，不同工况/指标/否定/明确交付分别保留。source_quotes/source_spans保留附加原文，总任务也参与覆盖，Reviewer独立检查语义遗漏。quantity.operator可为null，未给比较符号不补>=；数值和单位须同句联合定位，Wh_kg等书写差异可归一，原单位留存。`--reparse-requirements [--dry-run]`用通用规划+独立审核映射旧要求，副本同步回答/实验/检查点关联后终审并回滚保护；不改方案值或重置已有预算。旧契约继续兼容，不自动批量迁移。

Stage 5 输入修复与优化共用三次修订机会（研报渲染v20）：`util/pinn_inputs.py`区分身份/来源与范围失败，匹配且开启时即使无有效仿真或能量目标，`pinn_optimization.optimize`也可让LLM修复六项未锁定几何的单参数/训练包络越界。候选经共享`preflight_pinn_inputs`校验后才提交；自动修复不改D_s、倍率、电压或锁定字段。`stage5_attempts.py`在调用前持久占用attempt_id，拒绝/中断/验证失败也计次，工具和执行器共用预算；首次仿真及验证仿真不另计次，旧计数取较大值。重启不重发已占用模型请求，已提交候选只恢复仿真。无LLM保留机会；耗尽择优有效历史，否则物理失败进入既有5B/附条件交付。报告披露修复与优化次数及阻断，摘要纳入指纹。

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

Multi-agent material-screening RAG: **Planner → Retrieval → Writer → Reviewer** (`agents.py`, orchestrated by `rag_pipeline.py` with `target_energy_wh_kg` extraction and structured geometry generation). Key pieces: `multi_retrieval.py` (Chroma + BM25 + RRF dual-corpus retrieval + Materials Project MCP `augment_mp_evidence`), `relation_engine.py` (**7 tiered rules C1–C3, C5–C8**; `C4` permanently removed; `RULES_VERSION="C1-C8/v2.4-c1-c3-advisory"` recorded for parent-based rule matching — `C0_UNVERIFIED_MATERIAL` fail-closed reject, `C1/C3` 非阻断 `recommend`、`C2` 保留 `challenge` 与 `mitigation_whitelist`, `C5` `recommend` `additive:FEC`, `C6` `audit_note`, `C7` `coin_cell_eval` excludes `graphite` at ≥400 Wh/kg, `C8` `flag_frontier` ≥500 Wh/kg allowed+flagged), `structured_output.py`, `prompts.py` (central prompt registry).

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

- **Stage 5 (PINN) is enabled by default** (`skip_pinn_default: false`). It always extracts the selected materials' physical parameters to `pinn_input_spec.json` (extraction also runs on the skip fast path; fields that cannot be extracted stay `null` — no default-table backfill). When the scheme's (cathode, anode) matches a trained PINN system in `pinn/models/registry.json` and the `pinn_trigger.enabled` kill-switch is on, it runs the SPM PINN discharge simulation — pure-numpy `pinn/spm_runner.py`, npz weights exported from TF SavedModels by `scripts/export_pinn_spm_weights.py` (run in conda env `pinn210`), golden-IO equivalence locked by tests — and writes `pinn_simulation_result.json`. The simulation is **spec-driven**: R_s / D_s / thickness / porosity / C-rate are explicit external inputs taken from the spec (missing fields fall back to the system's trained params), and each system declares per-parameter valid ranges (`input_ranges` in the registry) — out-of-range (OUT_OF_RANGE) or out-of-envelope (OUT_OF_ENVELOPE) inputs are intercepted before the network runs. Converged runs also render `pinn_simulation_curve.png` (V-Q chart) into the task dir and the Stage 6 report embeds it. Stage 4's `design_scheme.json` carries a complete geometry declaration block (`scheme.geometry`, numeric layout `schema_version="1.1"` with `contract_version="2.0"` + `parameter_sources` + `design_revision` + `geometry_hash`: per-electrode `L_um`/`porosity`/`R_s_um`/`D_s_m2s` + optional `N/P` + voltage window; declaration table appended to `design_scheme.md`). It flows into the spec as explicit inputs, so the PINN simulates the declared design point and the report shows both the declaration (section 3) and the effective geometry (section 4). Unmatched systems fall back to extraction-only. Stage 5 is a **hard gate** (`strict: true`): a triggered run (matched system + kill-switch on) must produce a `pinn_simulation_result.json` with `status: CONVERGED` whose `pinn.spec_hash` matches the spec's `cell_spec_hash` — non-converged runs are persisted as failure payloads (overwriting any stale success, PNG invalidated) and `run_pinn_simulation` returns `success: false`; unmatched/kill-switch-off runs legitimately pass as extraction-only. The Stage 6 report embeds numbers/geometry/curve only when the same identity guard (`_pinn_result_identity_ok`) passes. The old PyBaMM P2D path (`p2d_runner.py`/`cell_spec_schema.py`) was removed — `pinn/output/` legacy artifacts and the checker's legacy branch are gone; the web demo TAB now runs the PINN. Stage 4's `RAGDesignChecker` hard-gates the geometry declaration (`runtime_options.geometry_gate: enforce|warn`, default enforce): under `contract_version="2.0"`, both electrodes' `L_um` and `porosity` must be explicit (`stage4_recipe` or `stage5_revision`), while `R_s_um` may be explicit or `model_baseline`/`missing` and `D_s_m2s` may be explicit (only with verified `diffusion_evidence`) or `model_baseline`/`missing` (legacy v1.1 records without `contract_version="2.0"` require all 8 fields as `stage4_recipe`/`stage5_revision`; violations emit `GEOMETRY_BLOCK_MISSING` / `GEOMETRY_DECLARATION_INCOMPLETE` / `GEOMETRY_CONTRACT_INVALID` / `GEOMETRY_VALUE_ILLEGAL` — where `GEOMETRY_CONTRACT_INVALID` covers `contract_version="2.0"` `validate_geometry_contract` and `geometry_hash` failures, while `GEOMETRY_VALUE_ILLEGAL` covers numeric type/range and diffusion-evidence checks); optional fields are range-checked against the registry when declared. Stage 5 closes the feedback loop: `RunPhysicsSimulation` returns a deterministic target verdict (`key_findings.target` — energy goal parsed from the goal text, tolerance `runtime_options.target_energy_tolerance_pct` default ±5%, `met` flag) plus `geometry_context` and effective-geometry echo; when not met the agent calls `ReviseGeometry` (carrying `base_revision` and `base_geometry_hash`, registry-range validated fail-closed, `D_s_m2s` edits requiring verified `evidence_claims`, budget `runtime_options.max_geometry_revisions` default 3, provenance `stage5_revision`, revision log rendered into `design_scheme.md` and `scheme.geometry_revisions`) and re-runs the simulation. Every round is archived (`pinn_simulation_history.json` index + `pinn_simulation_round_{k}.result/.spec.json` sidecars; cleared on NO_MATCH/kill-switch-off). At `complete_stage` (shared with `check_stage`) a deterministic finisher restores the best CONVERGED round by `energy_wh_kg` if the target is still unmet (sidecar result/spec restore + geometry snapshot rollback + selection audit entry; no-op when any round met the target, no numeric target exists, or the last round is already the best). The checker additionally guards `pinn.scheme_hash` against the current scheme projection (`util.hashing.stage5_scheme_projection`, which includes geometry — `PINN_SCHEME_STALE`), and validates `contract_version="2.0"` + `geometry_hash`, closing the "geometry revised without re-simulation" window. `complete_geometry_block` is idempotent (re-normalization never overwrites declared values with baseline), and spec-side illegal geometry values fail closed (`ValueError` from `build_cell_spec`) instead of silently reverting to the trained baseline.
- Workflow state is sticky **per goal**: `output/tasks/<goal>/.stage_state.json` is reused across runs. After changing stage deliverables/code, use `abr-cli --reset` (or delete that goal's state file) to force re-evaluation from Stage 1. **Goal identity is normalized, not exact-matched**: `resolve_effective_goal()` treats case/whitespace/punctuation variants, ≥6-char substrings, and identical `Wh/kg` figures of the active goal as the same goal so LLM wording drift cannot fork a new task dir. The default goal has one source of truth — `util/constants.py:DEFAULT_GOAL`; changing it orphans existing task state dirs, and goal strings must never be hardcoded elsewhere (the 400Wh default was deliberately scrubbed from tools/checkers).
- **LLM full-trace recording is opt-in** (`runtime_options.llm_trace: false` by default; enable via `abr-cli --trace`, setting.yaml, or env `LLM_TRACE` — priority CLI > setting > env). When on, `util/llm_trace.py`'s process-singleton recorder appends one JSONL event per LLM call (full input/output messages incl. tool_calls & ToolMessage observations, token usage, duration, backend/model, trace_tag for the RAG role) plus `prompt_assembly` events, into the goal's `llm_trace/trace_<session>.jsonl` + `trace_meta.json` (no-goal processes fall back to `log/llm_trace/`). Hook points: `ABRLangChainBackend.invoke` (ReAct brain — offline short-circuit recorded as `backend="offline"`), `src/lmllm/RAG/llm_client.chat` (trace_tag from callers: planner/writer/writer_revise/reviewer/report_recipe/baseline), `backend/llm_client.generate` (TUI Q&A / MCP consult), `RAG/extractor._call`, and legacy mining factories (`miner/config.create_llm`, `agent/pipeline._create_llm`) via `LLMTraceCallbackHandler`. Recording is fail-silent by design — a broken recorder must never affect the run.
- Strict mode (`runtime_options.strict_mode: true`) makes hard checkers fail the stage on any error; `max_retries_per_stage: 3` bounds self-healing loops.
- Stage rule from the mission system prompt: never fabricate data — "有则提取、无则留空、禁止编造" (extract what exists, leave blank otherwise, never invent). Design schemes must pass `RelationEngine`'s 7 tiered rules (`C1–C3, C5–C8`; `C4` permanently removed; `RULES_VERSION="C1-C8/v2.4-c1-c3-advisory"`) including `C0_UNVERIFIED_MATERIAL` rejection and `C1/C3` 非阻断建议及 `C2` `mitigation_whitelist` 检查.
- Windows is the primary dev platform — entry points reconfigure stdout/stderr to UTF-8; keep that pattern when adding new entry scripts.
- Unit tests must stay offline: `tests/test_abr_agent.py` injects `OFFLINE_CONFIG` (dummy key) so no live API calls happen. Anything needing live services gets the `external` marker.
- When adding a new checker or stage, register it in `workflow/abr_workflow.yaml` (checker classes are resolved by dotted import path) — `StageManager` discovers everything from there.
