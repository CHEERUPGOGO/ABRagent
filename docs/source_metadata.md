# 正文来源同步与精确授权

Stage 4 正文修复可能更正配方、计算结果或条件说明。其他参数和研究记录引用的旧段落必须同步，否则后续复核会把旧来源中的内容当作当前声明。来源同步在候选构造时完成，和正文经过同一次独立复核后提交。

## 工程参数来源

`source_metadata.synchronize_parameters`只处理未锁定工程参数的`source_quote`。原摘录必须在旧稿中唯一定位，包含该参数的已签发`abr-param`引用；通过同一段落的编辑位置映射到新稿，新摘录仍须唯一、包含该字段且显示值与候选参数一致。整段来源随该段更新，局部原句仅按其对应范围更新。不能唯一映射、来源已失效或替换切断摘录边界时，不猜测新来源。

值、单位、来源说明`source`、basis、工况、身份和用户锁定不因来源同步改变。文献和用户条件的原文来源不自动改写。原值与旧来源保留在补丁审计及原始调用档案中。

需要显式修复时，程序在问题的授权段落内找到实际引用的参数，签发`source_fields`，每项仅允许`source_quote`属性，绑定旧来源哈希和可用段落。Writer提交：

```json
{
  "interface_id": "程序签发的接口编号",
  "replacement": "接口当前原句或获准改写",
  "parameter_changes": [],
  "source_changes": [
    {"path": "cathode.doping.x_A", "source_quote": "授权段落中唯一存在且含该字段abr-param引用的原句"}
  ]
}
```

`source_changes`不能夹带value、unit、basis、source、conditions、locked或其他属性，也不能改绑到另一段或另一个字段。字段不在数值写权限`fields`内时，可获独立来源权限，不因此获得数值写权限。只有来源字段基准一致、候选原句合法且独立复核通过才提交。

旧来源句已不在当前正文时，Reviewer可返回`parameter_source_paths`，或由程序对可引用的工程字段检测来源失效，按字段身份签发`kind=parameter_source`接口。此时只提交`interface_id/source_quote`，不提交正文replacement或参数改值；接口固定目标字段和可定位段落。已明确defer的来源问题不在同轮自动重新入队。

## 研究记录来源

程序来源诊断或Reviewer的`research_sources:[{"collection":"answers","id":"已有记录ID"}]`明确定位问题记录。程序签发`kind=research_source`接口，只允许同一记录的`source_quotes`和`field_support`，不要求改写正文或重新生成研究回答：

```json
{
  "interface_id": "程序签发的研究来源接口编号",
  "source_quotes": ["当前正文的唯一原句一", "当前正文的唯一原句二"],
  "field_support": {"response": [0, 1], "reason": [1], "next_step": [1]}
}
```

record_hash绑定完整原记录；科研文本、状态、参数引用、文献编号、候选身份及排序不可修改。来源必须逐条唯一定位，非空摘要字段必须有有效关联，并通过原有数值、材料、引用与条件检查。缺充分原文时保留失败，不允许编造来源或借修改状态结案。

研究文本的`{{字段路径}}`是合法主方案引用，Reviewer按当前参数值和工况核对；不能将保留绑定误报为未渲染错误，也不能为了来源修复把partial/gap升级为科学已验证状态。

已接受正文的来源摘录也会按编辑位置同步到研究记录；来源改变即清除旧support_review并重新建立定位与指纹。局部repair_verify核对新来源对原摘要的支持关系，正式科学Reviewer仍须按当前指纹独立审核。来源修复本身不授予科学支持或性能验证。

## 正文与关联摘要同步纠错

默认v2正文接口附带可选`research_text_permissions`：程序仅为原记录中非空科研文本、且其原`field_support`所绑定唯一原句与本接口授权范围相交的字段签发权限。只支持`answers.response/reason/next_step`、`candidates.summary/rationale/risks`、`arguments.text`；记录级哈希、字段原值及字段哈希均绑定本轮基准。单来源旧记录按其明确原句兼容；不能定位、无明确绑定的字段不猜测授权，v3编译投影仍走其原生内容树修复。

若正文改正计算、否定或科学结论后，原摘要也需更正，Writer在相应正文submission内提交：

```json
{
  "interface_id": "程序签发的正文接口编号",
  "replacement": "获准的正文纠正",
  "research_changes": [{
    "collection": "answers",
    "id": "原requirement_id",
    "record_hash": "权限中的完整记录哈希",
    "fields": [{
      "field": "response",
      "old": "该字段精确原值",
      "new": "与本轮正文纠正一致的摘要",
      "source_quotes": ["该字段原绑定依据在候选正文中的完整迁移原句"]
    }]
  }]
}
```

无需改写摘要时省略`research_changes`，不发送空字段补丁。新来源只可取该字段原绑定依据按同稿编辑位置迁移后的原句，且至少一条确实被本事务修改；不能借同段、同值或关键词扩展来源。程序保留其他科研字段和引用元数据；不能清空字段、添加记录、改绑参数，或改写身份、要求关联、候选名称/角色/排序/首选、状态及验证程度。来源专用`kind=research_source`权限保持原边界，不能夹带科研文本修改。

同记录涉及的正文接口与来源接口联为事务；字段改写先经过数值、参数、文献、来源及完整记录验收，记录被隔离或降为占位不算成功。`repair_verify`在原有一次调用中同时接收前后正文、`combined_preview.research`和逐字段`research_text_changes`，须逐项返回`research_verdicts`中的`group_id/collection/id/field/change_hash/supported/reason`。原问题resolved不能代替字段支持复核，遗漏或否决任一字段即整组拒绝；独立无依赖问题仍可提交。

审计沿用`research_source_updates`，`reason=body_summary_correction`并记录`changed_fields`的前后文本、新来源和授权原句；检查点保存同步后的研究稿并清除旧支持及审核签名。局部复核通过不产生正式`support_review`，仍使用原正文修复及后续科学审核预算；恢复不重发已完成调用，既有发布产物不自动重写。

## 事务、复核与恢复

问题接口版本3沿用实际修改范围和参数依赖分组。来源依赖另一正文修改时一起验收；无依赖的来源问题可独立提交。复核包中`original_parameters`只作旧版比较，`combined_preview.parameters`为完整候选主表，各事务的`parameters`列出该事务实际变化字段，来源差异另附`source_updates/research_source_updates`。

研究审核包中的scheme及component_identities也按当前参数重新投影，避免参数已更新而研究缓存仍携带旧配方。来源摘录变化不视为更换材料或改变物理输入；实际值、单位、身份及其他参数属性变化仍进入原校验。

研究副本的scheme_parameters同时刷新为当前参数，避免摘要整合时旧扩展表覆盖新来源。审核指纹将1与1.0等同量值表示规范为同一身份，兼容历史指纹；实际数值、单位、工况或科研内容变化仍使旧审核失效。

提交审计保留前后来源。任一事务被否决，不保留其正文或来源更新；最终组合再次校验。研究来源更新在检查点保存当前研究记录，清除旧审核签名，后续使用剩余审核机会。修复与审核沿用现有YAML预算，恢复不重置计数，不重发已完成调用；已发布任务不因升级代码而自动改写。
