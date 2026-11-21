# 数值一致性与正文引用

Stage 4 新设计使用 Writer JSON `version=2`，保留固定五节 `sections`，新增 `parameters` 数值提案。用户锁定和已有非空声明只引用；未锁定空字段可由有依据的工程提案填充。`quantity_kind`、`denominator`、`group_id`、`purpose`、`conditions` 和 `quantity_role` 区分物理量身份。上限、实验封顶和目标分别记录，不因数值不同自动判冲突。

主方案量值用 `{{param:cathode.porosity}}` 引用，程序渲染值和单位；可计算结果使用 `{{derived:condition.calculated_electrolyte_to_cathode_pore_ratio}}`。引用在任何参数提取或审核前生成。新提案的来源由程序绑定到采用它的完整正文段落；后续研究提取不能从自由正文再新增数值主张为主方案参数，未登记的数值保留为候选。

实现包括：

- `numeric_consistency.py`：只读归一明确同义字段及单位；按样品、工况、分母和用途检查重复声明、孔体积/E/P、面载量/厚度/压实密度、固体质量分数和、倍率电流、电压窗口以及显式上下限关系。
- `numeric_bindings.py`：渲染与校验引用，按参数依赖同步所有已签发引用。补丁不能更换引用字段或直接改显示值；文献和对照的同值数字不会被全局替换。
- `design_repair.py` / `repair_interfaces.py`：问题按可唯一定位原句授权；完整依赖作为同一事务，复核看到同步后的正文，最终合并重新检查。数值改变后旧定位队列失效，沿用现有修复/审核预算。

别名只来自显式 `canonical_path` 或有限同义表，例如 `electrolyte.volume_uL`→`electrolyte.volume`，`electrolyte.E_over_P`→`condition.electrolyte_to_pore_volume_ratio`。后者必须明确分母是正极孔体积；“全部孔体积”不混用。单位仅按明确维度转换，未知单位、零分母和冲突依赖保留诊断，不采用默认值。

`ratio_inputs=[分子字段,分母字段]` 可核对已明确的位点比例等无量纲结果；要求单位/数量类型/样品和工况相符，结果在 `calculated.<字段路径>` 中。程序不从化学式猜占位，也不把位点分数或元素系数变化转为总容量损失。Cu/Zn 风险排序、上限是否有科学依据、不同文献是否可比，仍交给 Reviewer。

每项检查保存状态：`CONSISTENT`（已完成比较）、`CALCULATED`（计算完成但无对应声明可比）、`CONFLICT`、`INCOMPLETE`（缺项/歧义）或 `NOT_APPLICABLE`。原值和单位不改写，结果保留公式、输入来源、依赖和缺项。常规算术复算沿用既有2%取整容差；同义原始声明使用严格数值比较，比例声明使用0.1%取整容差。容差只处理数值舍入，不表示科学结论得到验证。

正文未绑定的量值另记 `unbound_mentions`，包含原文位置，归属为 `unclassified`。它们可能是文献、对照或未登记主方案，程序不按邻近关键词擅自判定。不能把“已登记字段没有冲突”解释为全文所有数值均已核验。新增量纲/公式仍需扩展检查器；模型负责归属和语义审核。

## 既有报告

```powershell
abr-cli --audit-stage4-numbers --goal "原始课题"
```

在 `numeric_audit_history/<时间>/preview` 副本中核对当前方案表和正文，新增 `numeric_consistency.json`，报告附录显示已执行与未完成检查。配方、科研正文、文献、参数来源和模型预算不变；不重跑模型/检索/仿真，不把正文里未登记的面积猜成当前计算输入，不自动选择30%或46%。已有科学意见保留。

副本同步实验一致性快照，经 Stage 4/5/6 验收后按文件指纹发布；并发修改拒绝覆盖，失败回滚，原文件保存在 `originals`。报告渲染版本29。新任务的审计随审核及方案表计算自动执行；报告验收重算数值视图，未披露的当前冲突不能作为 COMPLETE 交付。
