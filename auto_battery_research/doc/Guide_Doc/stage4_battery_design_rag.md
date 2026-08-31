# Stage 4: 多智能体 RAG 方案设计指引

## 任务目标
1. 接收具体化学电池设计目标（如 400Wh/kg 锂金属电池）。
2. 调度 Planner $\rightarrow$ Retrieval $\rightarrow$ Writer $\rightarrow$ Reviewer 四大智能体协同生成五段式方案。
3. 执行 C1-C8 热力学与化学相容性硬约束审查（如高压正极与碳酸酯不兼容检测）。

## 验收门禁 (RAGDesignChecker)
- `output/auto_battery_research/design_scheme.md` 存在且字数 $\ge 200$ 字符。
- 完整包含五段式核心章节（目标与设计路线、推荐组合、预期关键指标、可行性依据、风险与数据缺口）。
