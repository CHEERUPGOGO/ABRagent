# Stage 4 程序章节契约

设计类 Writer 一次返回 `version=2` 的 JSON（数值引用见 `numeric_consistency.md`）；章节渲染契约仍为v1，程序按照固定 ID 生成五个二级主标题：

| ID | 主标题 |
|---|---|
| goals | 1. 目标与设计路线 |
| recommendations | 2. 推荐组合 |
| metrics | 3. 预期关键指标 |
| feasibility | 4. 可行性依据 |
| risks | 5. 风险与数据缺口 |

`sections` 必须恰好包含这五个字段。每节包含 `body` 字符串及可选的 `subsections` 数组。子节包含全篇唯一的 `id`、不带编号的 `title`、`body` 和可选的下级 `subsections`；最多三层子节。模型只负责段落、表格、文献引用和子节名称，编号和 Markdown 层级由程序生成。分析类任务（`scheme_required=false`）保留自由大纲，不强行套五节。

正文跨节引用使用 `{{section:occupancy}}` 这样的稳定 ID。渲染器生成相应的章节号和内部链接；未知 ID、重复 ID、重复 JSON 字段、缺失主节、空叶节或只有“待补充”的占位内容会明确失败。不能以空标题代替研究内容；缺数据时须写明原因和获取路径。围栏与行内代码中的示例不参与引用替换。

实现位于 `auto_battery_research/util/design_structure.py`。渲染发生在 Writer 出口，早于方案参数和研究记录提取。`draft_answer` 是后续提取、Reviewer、局部补丁共用的正文；原始 JSON 保存为 `raw_response` 与 `structured_sections`，程序标题清单保存为 `design_structure`。正文修订后不重新渲染初稿，避免覆盖已接受补丁。常规补丁不能修改任意层级的标题、锚点或结构边界；合并后校验章节完整性及引用目标。Stage 4 门禁同时核对 JSON 正文、Markdown 和标题清单。

`normalize_titles()` 仅保留兼容入口，不再按关键词替换旧标题。普通恢复不修改历史章节，也不重置审核、修复或检索预算。Stage 6 六节研报布局不变。

## 旧稿显式恢复

```powershell
abr-cli --restore-stage4-titles --goal "原始课题"
```

该入口从检查点的 Writer 原稿重现历史规范化结果，并逐项重放已接受正文补丁、核对前后哈希。只有完整链条与当前草稿一致、主节和子节归属明确时，才恢复标题并生成结构清单；没有原稿、历史链断裂或未知正文改动均拒绝猜测。

恢复在任务目录的 `stage4_title_history/<时间>/preview` 副本执行。当前正文全部保留，只替换标题并增加程序结构标记；配方、参数、文献和已接受正文补丁不变。旧审核及待修定位归档，旧队列不能继续获得修改权限。最多使用原有六轮审核预算中剩余的一次完整 Reviewer 调用，调用前持久化；不重新检索、生成正文、提取参数、运行仿真或生成实验方案。没有审核机会或服务失败时保留科学意见并标记当前正文未完成重新审核，不能自动升级为 APPROVED。

副本同步实验绑定并重新渲染报告，通过 Stage 4/5/6 验收后，按文件指纹核对并发布；并发修改拒绝覆盖，提交失败回滚。原产物保存在 `originals`。重复执行已完成恢复的任务不会重复消耗模型预算。
