# Stage 4 内容契约 v3

## 启用与兼容

`runtime_options.stage4_writer.contract_version` 只允许 `2`、`3`。当前默认保持 `2`；v3 用隔离课题显式验证。真实模型对照未完成，不能把离线golden当作科研质量上线验收。配置回滚只影响新会话。已有v1/v2设计、检查点和分析任务仍沿原路径；已有v3会话继续v3。`--rebuild-stage4` 归档当前正文、原稿、发布日志及检查点后，新会话使用当前配置，不批量迁移 `output/tasks`。

```yaml
runtime_options:
  stage4_writer:
    contract_version: 3
```

## 数据与编译

参数、单位、来源、锁定以 `scheme_ledger.json` 为权威；当前正文以 `writer_content` 为权威；审核只由带当前输入指纹的真实请求授予。Writer返回一个完整JSON：`version=3`、`parameters`、五节 `sections`、`research_links`。原始提案只保留于请求档案，当前内容树没有可独立覆盖方案表的参数副本。

五节ID为 `goals/recommendations/metrics/feasibility/risks`。章节含 `blocks` 和可选 `subsections`，子节最多三层。块只有：

- paragraph：`{id,type:"paragraph",text}`。
- list：`{id,type:"list",ordered,items:[{id,text}]}`。
- table：`{id,type:"table",columns:[{id,name}],rows:[{id,cells:{列ID:文本}}]}`。

表格叶ID由 `表ID__行ID__列ID` 确定。ID全篇唯一、英文小写开头，修复保持稳定。正文使用普通中文和Unicode符号；编译器生成标题编号、转义、列表和表格分隔线。模型不得提供Markdown标题、围栏或HTML。

引用支持 `{{param:字段路径}}`、`{{section:章节ID}}`、`{{cite:passage_id}}`、`{{component:cathode}}`、`{{candidate:候选ID}}`。预算和换算由模型直接写清方法、输入、代入、结果/单位及口径，Reviewer复核，不再使用`{{derived:计算ID}}`或固定计算链。下游采用的结果可登记普通工程参数并保留计算依据。已知但缺失的几何字段显示具体缺项，其他未知引用拒绝。既有量值身份、别名、样品/工况/分母/用途规则及精确扩散证据标准不变；未绑定新提案不能静默删除。程序不从倍率参考电流推断容量，不从厚度证明N/P，不从盐浓度猜LHCE，不继承母体的物性或PINN资格。

`compile_document(content, parameter_view, requirements, evidence)` 是无模型、无写文件、不修改输入的纯函数。返回Markdown、章节、叶来源和真实坐标、依赖、研究投影、全部诊断、内容哈希及渲染指纹。Writer、适配器、门禁和报告共用此编译器。

## 研究关联与审核

`research_links.answers` 的 response/reason/next_step，candidates 的 summary/rationale/risks，arguments 的 text 均使用 `[{leaf_id,quote?}]`。quote省略表示整叶，提供时必须在指定叶内唯一逐字定位，不能截断引用。程序投影兼容研究记录及字段来源/参数/文献依赖，不再调用研究重复提取或整合改写。

科学Reviewer查看原始要求、完整正文、候选参数、研究来源、预检查和受限证据包；独立终审查看六维一致性。字段定位不表示科学支持，终审通过不能覆盖科学否决。支持指纹按实际字段关联绑定，无关段落变化不撤销其他字段支持；相关文本、参数、要求或证据变化失效。

局部修复只能替换已授权叶的精确片段、修改已授权未锁参数或为已核验遗漏要求在指定章节追加受限内容。标题、ID、行列、引用和来源身份冻结，同段/同参数依赖合并事务。程序校验后做事务语义复核和全文科学审核。拒绝补丁、原始响应和成功补丁都保存，恢复不覆盖有效修改。

默认配置为：Writer完整生成一次，传输截断最多一次完整重试；协议元数据/等值引用恢复最多一次。常规科学修复最多两轮，总共三轮，其中一轮预留终审；全文科学审核最多六轮；事务复核每轮一次；终审至多两稿、每稿一次协议重审。调度轮数及角色请求配额统一由 `setting.yaml` 的 `runtime_options.stage4_repair` 设置，代码不另设预算上限，详见[运行预算配置](runtime_budgets.md)。所有机会在请求前持久占用，失败、中断和协议拒绝不重发、不退款。v3关闭客户端及SDK隐藏重试和失败后的后端切换。

## 发布、恢复与下游

v3在课题锁内重读方案表并核验基准，在隔离目录复用方案提交规则准备全部产物；`stage4_publication.json` 保存前后文件文本与哈希，逐文件临时写入及原子替换，全量验收后标complete。这不是多个文件天然整体原子；门禁拒绝未完成和混合版本。恢复只允许目标仍等于日志旧版或本事务新版，其他写入出现即停止。发布日志历史在 `stage4_publication_history/`。

检查点保存契约、请求、当前内容、参数、编译视图、审核、补丁和剩余预算；规划、首次检索也持久化，重启不重跑已发出的请求。损坏v3状态不回退v2。无法编译的原稿保存在 `stage4_v3_diagnostic.json`，不伪造五节正文、不覆盖已接受方案。

Stage 5直接修订、自动优化及择优回滚共用发布和同步服务，刷新正文引用、研究投影及实验绑定，仅撤销受影响字段支持。最终选定后同会话最多一次只读一致性复核；不会恢复依赖旧几何的科学支持。失败或复核后再次改值显示未复核。Stage 6直接呈现当前编译科研正文，保留原顺序与引用，参数和计算仍从当前方案表渲染；附录使用来源链接及真实审核状态，报告渲染版本34。

## 验证与上线

实施基线：`8de981a9895f7ba4dd37b2f25a8e02fa263dc912`，原离线套件1171项通过。种子语料沿用 `test_stage4_golden.py`，新测试在 `test_stage4_document_v3.py`、`test_stage4_publication_v3.py`、`test_stage4_session_v3.py`。

真实模型对照必须显式使用external场景，在隔离目录固定同模型、原始要求和证据库，比较六类课题各三次。上线需要18次至少17次首次协议/编译通过、不低于v2基线；所有预设数据和锁定冲突拦截，合法反例不过度误报；需求、论证和来源适用性不低于基线。完成该验收前默认保持v2。

`python scripts/evaluate_stage4_v3.py` 只列出六类任务，不联网。显式执行 `python scripts/evaluate_stage4_v3.py --run-external --output output/stage4_v3_evaluation` 才调用真实模型；同配置运行v2/v3各18次，独立目录记录角色调用、耗时、编译及审核状态。`comparison.json` 的人工质量项保持null，未人工确认不授予上线。
