# Stage 4: 多智能体 RAG 方案设计指引

## 任务目标
1. 接收具体化学电池设计目标（如 400Wh/kg 锂金属电池）。
2. 调度 Planner $\rightarrow$ Retrieval $\rightarrow$ Writer $\rightarrow$ Reviewer 四大智能体协同生成五段式方案。
3. 执行 C1-C8 热力学与化学相容性硬约束审查（如高压正极与碳酸酯不兼容检测）。

## 验收门禁 (RAGDesignChecker)
- `output/auto_battery_research/design_scheme.md` 存在且字数 $\ge 200$ 字符。
- 完整包含五段式核心章节（目标与设计路线、推荐组合、预期关键指标、可行性依据、风险与数据缺口）。


## 几何设计数值要求（供 Stage 5 PINN 验证消费）
方案的正/负极章节应给出可提取的几何设计数值：面载量 (mg/cm²)、压实密度 (g/cm³)、孔隙率 (%)、N/P 比、极片涂敷厚度 (μm)。这些数值会被结构化提取并**归一化为显式完整几何声明块**写入 `design_scheme.json` 的 `scheme.geometry`（产物契约 v1.1：每个 PINN 输入字段——两电极的 L/孔隙率/R_s/D_s + N/P + 电压窗口——逐字段给出值与 provenance：`stage4_recipe`=叙事提取，`params_baseline`=体系训练参数兜底）；`design_scheme.md` 尾部同步渲染「设计点几何声明」表。Stage 5 的 PINN 直接在声明设计点做放电仿真验证，几何与验证结果一起进入 Stage 6 报告。
