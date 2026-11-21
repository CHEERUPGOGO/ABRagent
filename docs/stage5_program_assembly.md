# Stage 5B 完整方案生成（已回退程序组装）

Stage 5B已恢复为一次完整实验方案JSON生成，程序核对后必要时最多请求一次局部补丁。Stage 4、PINN及Stage 6主流程保持现状。

## 当前路径

- `experiment_design.SYSTEM_PROMPT`恢复程序组装之前的提示。模型维护参数、六节正文、分组、逐目标安排及引用；程序添加时间、输入身份和来源阶段。
- `experiment_repair`核对完整初稿，只允许替换错误路径，保留有效参数和段落。常规一次生成，最多一次局部修复，预算上限6000 token。
- 方案表、值、单位、锁定条件、文献引用及对照范围继续校验。独立缺项登记和数值类型保护保留。
- 原始响应写入`experiment_draft.json`。格式失败保留错误和原稿，不覆盖已有有效计划。
- 不再运行分批内容槽、程序预组装主组或额外全文Reviewer。新稿不写程序化generation_contract，也不生成科学审核通过证明。

## 旧产物兼容

已有v2/v3/v4实验计划继续读取，输入身份兼容v7/v8。既有`generation_contract=1`产物的审核结论保留，未完成、过期和否决意见不被回退操作提升。

`--regenerate-experiment-plan`备份旧计划和`experiment_generation_checkpoint.json`到任务的`experiment_generation_history/`后生成完整新稿，成功后撤下旧检查点；失败保留旧计划与检查点。默认读取不改写已有产物或触发模型。

Stage 6继续渲染实验计划，保留逐目标已有方法、判据、限制和下一步。未知参数独立披露获取方法。`--repair-experiment-values`仍通过副本验收后发布，失败回滚。