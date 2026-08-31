# Stage 6: 综合研发报告生成指引

## 任务目标
1. 聚合前 5 个阶段的全流程日志、文献证据、电芯挖掘数据、RAG 方案与物理仿真结论。
2. 输出排版规整、技术机理扎实的 Markdown 综合研报与全流程研发日志。

## 验收门禁 (FinalReportChecker)
- `output/tasks/<task_slug>/final_research_report.md` (或 `output/auto_battery_research/final_research_report.md`) 存在且字数 $\ge 400$ 字符。
- 完整包含研发摘要、文献与电芯数据概况、电池体系设计方案、物理仿真与验证、实验配方与落地建议五大核心模块。
- 研发日志 `stage_journals.json` / `abr_agent_journal.json` 同步持久化。

