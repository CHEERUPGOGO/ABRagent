# Stage 5: PINN 触发判定与材料参数提取指引 (可跳过)

## 任务目标
1. 【触发判定占位】读取 `setting.yaml` 顶层 `pinn_trigger` 配置执行触发判定；具体判定规则 (特定材料 ID 白名单 + 特定性能要求阈值) 待专门 PINN 模块接入时在 `_check_pinn_trigger` 中定义，当前仅由 `enabled` 开关决定。
2. 【参数提取】从 Stage 4 `design_scheme.json` 提取当前方案选中的正极/负极/电解液/添加剂，组装 CellSpec 物理契约 (candidates.json 材料级覆盖 + 缺省参数表回填物理量)，落盘 `pinn_input_spec.json`。
3. 【不执行仿真】真实 PINN/PyBaMM 电化学仿真暂不接入；提取不到的物理量以 `null` 占位，由 `extraction_summary.fields_missing` 标注，待参数库或 PINN 模块补全。

## 产物契约 (`output/tasks/<goal>/pinn_input_spec.json`)
- `trigger`: 触发判定结果 (`enabled` / `triggered` / `reasons`)。
- `scheme`: Stage 4 方案选中的材料 ID 与设计目标。
- `cell_spec`: CellSpec 结构化参数 (cathode / anode / electrolyte / separator / design / condition)。
- `extraction_summary`: 每个组件的 `fields_filled` / `fields_missing` 统计与缺省表覆盖标记。

## 提取链路
`design_scheme.json scheme` → `candidates.json` (材料级 capacity/voltage/formula 覆盖) → `pinn/cell_spec_schema.py` 缺省表 (`DEFAULT_MATERIALS` / `DEFAULT_ELECTROLYTES`) 回填物理量 → `p2d_runner.MATERIAL_PROFILES` 补充电压窗口。缺省表未覆盖的材料 (如 LFP/LCO/hard_carbon) 物理字段保持 `null`。

## 验收门禁 (PINNPhysicsChecker)
- 若 `stage.skip == True`：直接通过门禁；但参数提取仍会在 skip 快速通道中执行 (失败仅记录，不阻断)。
- 若 `stage.skip == False`：课题目录须存在结构完整的 `pinn_input_spec.json` (含 `cell_spec` 块)；历史课题的 `simulation_result.json` 数值校验路径保留兼容。
