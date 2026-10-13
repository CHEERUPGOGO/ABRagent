# Stage 4: 多智能体 RAG 方案设计指引

## 任务目标
1. 接收具体化学电池设计目标（如 400Wh/kg 锂金属电池）。
2. 调度 Planner $\rightarrow$ Retrieval $\rightarrow$ Writer $\rightarrow$ Reviewer 四大智能体协同生成五段式方案。
3. 执行 C1-C8 热力学与化学相容性硬约束审查（如高压正极与碳酸酯不兼容检测）。

## 验收门禁 (RAGDesignChecker)
- `output/auto_battery_research/design_scheme.md` 存在且字数 $\ge 200$ 字符。
- 完整包含五段式核心章节（目标与设计路线、推荐组合、预期关键指标、可行性依据、风险与数据缺口）。


## 几何设计数值要求（供 Stage 5 PINN 验证消费；Stage 4 门禁硬性检查）
方案的正/负极章节**必须**给出电极涂敷厚度 (μm) 与孔隙率 (%)——这 4 个值是 Stage 5 PINN 仿真的硬性设计输入，`RAGDesignChecker` 做 geometry 硬门禁（`runtime_options.geometry_gate: enforce|warn`，默认 enforce）：
- `scheme.geometry` 块缺失 → `GEOMETRY_BLOCK_MISSING`；
- 4 值缺失或非显式声明（provenance ≠ `stage4_recipe`）→ `GEOMETRY_DECLARATION_INCOMPLETE`；
- 提供了但类型/范围非法（如厚度 -10 μm、孔隙率 1.2，含全部可选字段）→ `GEOMETRY_VALUE_ILLEGAL`。

其余几何/条件参数**鼓励给出、不强制**（声明则采用并校验，缺省走默认链）：面载量 (mg/cm²)、压实密度 (g/cm³)、N/P 比、放电倍率 (C)、电压窗口 (V)（截止电压缺省用体系 `post_processing.v_min_cutoff_V`）、颗粒粒径 R_s (μm)、固相扩散系数 D_s (m²/s)（缺省回退体系训练基准 params.json）。

以上数值会被结构化提取并**归一化为显式完整几何声明块**写入 `design_scheme.json` 的 `scheme.geometry`（产物契约 v1.1：逐字段值 + provenance：`stage4_recipe`=叙事提取，`params_baseline`=体系训练参数兜底，`invalid`=提供了非法值；归一化幂等，重复执行不会把设计值覆盖回基准）；`design_scheme.md` 尾部同步渲染「设计点几何声明」表。Stage 5 的 PINN 直接在声明设计点做放电仿真验证，几何与验证结果一起进入 Stage 6 报告。
