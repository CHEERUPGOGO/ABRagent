# Stage 5: PINN 触发判定、参数提取与 SPM 仿真指引

## 任务目标
1. 【触发判定】触发 = `setting.yaml` 顶层 `pinn_trigger.enabled` 总开关 AND 方案 (cathode, anode) 匹配 `pinn/models/registry.json` 中已训练的 PINN 体系。关闭开关或未命中体系都只做参数提取，不视为错误。
2. 【参数提取】从 Stage 4 `design_scheme.json` 提取当前方案选中的正极/负极/电解液/添加剂，组装 CellSpec 物理契约 (candidates.json 材料级覆盖 + 缺省参数表回填物理量)，落盘 `pinn_input_spec.json`。
3. 【PINN 仿真】Stage 5 激活 (非 skip) 且体系匹配命中时，调用 `RunPhysicsSimulation` 执行 SPM PINN 放电仿真 (纯 numpy，`pinn/spm_runner.py`)，产物 `pinn_simulation_result.json`。仿真是 **spec 驱动**的：R_s、D_s、电极厚度、孔隙率、C-rate 五个设计参数从 `pinn_input_spec.json` 的 cell_spec 显式输入——全部来自 Stage 4 方案的 `scheme.geometry`（产物契约 v1.1 显式完整声明块：L_um/porosity/R_s_um/D_s_m2s/condition.v_min/v_max，逐字段 provenance=stage4_recipe|params_baseline）(未匹配体系时字段缺失回退体系训练参数)。每个 PINN 体系在 registry `input_ranges` 中声明各输入的有效范围——逐参数越界 (OUT_OF_RANGE) 或组合越界 (训练包络 OUT_OF_ENVELOPE) 都在调用网络之前拦截，不产出仿真结果，错误信息会点名越界字段与合法范围。

## 产物契约
`output/tasks/<goal>/pinn_input_spec.json`：
- `trigger`: 触发判定结果 (`enabled` / `triggered` / `reasons` / `match`——含 `system_id` 与匹配说明)。
- `scheme`: Stage 4 方案选中的材料 ID 与设计目标。
- `cell_spec`: CellSpec 结构化参数 (cathode / anode / electrolyte / separator / design / condition)。
- `extraction_summary`: 每个组件的 `fields_filled` / `fields_missing` 统计与缺省表覆盖标记。

`output/tasks/<goal>/pinn_simulation_result.json`（体系匹配时生成）：
- 标量: `specific_capacity_mAh_g` / `average_voltage_V` / `energy_wh_kg` (电芯级) / `termination` (voltage_cutoff 等) / `pde_residual_loss`。
- 曲线: `discharge_curve.capacity / voltage` (mAh/g 与 V 数组)；同步渲染 `pinn_simulation_curve.png` (V-Q 曲线图, 电压截止线标注)。
- 几何回显: `pinn.design` (实际生效的正/负极 L/ε/ε_s/R_s/D_s) 与 `pinn.spec_inputs` (显式输入及来源) —— 报告第 4 节据此渲染仿真输入几何表。
- 诊断: `pinn` 块 (system_id / 包络状态 / 覆盖参数 / 表面化学计量曲线)。

## 提取与仿真链路
`design_scheme.json scheme` → `candidates.json` (材料级 formula/capacity/电压) → `pinn/input_spec.build_cell_spec` 组装 cell_spec。物理动力学字段 (c_max/D_s/R_p/孔隙率等) **不做缺省表回填**，保持 `null`——由 PINN 体系 `params.json` (训练基准) 或 spec 显式输入提供；电压截止下限运行时取注册表 `post_processing.v_min_cutoff_V`。

PINN 链路：`match_pinn_system(cathode, anode)` → 体系 `params.json` + pOCV 曲线 + npz 权重 → 容量/bc 解析映射 → 训练包络检查 (|bc|/|bc_ref| ∈ [0.02, 15]，越界拒绝执行) → 网络前向 → OCV + Butler–Volmer → 电压截止 (v_min) 截断 → 标量落盘。放电/充电模型在 registry 中成对预留 (`directions.charge`)，当前仅放电接入。

## 验收门禁 (PINNPhysicsChecker，strict=true 硬门禁)
- 若 `stage.skip == True`：直接通过门禁；但参数提取仍会在 skip 快速通道中执行 (失败仅记录，不阻断)，**skip 快速通道不做仿真**。
- 若 `stage.skip == False`：按两种合法模式判定——
  1. **仿真模式** (`trigger.triggered == true`，体系匹配且总开关开启)：必须存在 `pinn_simulation_result.json`，且依次校验
     - `status == CONVERGED`（拦截/异常 payload → `PINN_SIMULATION_NOT_CONVERGED`）；
     - 输入指纹一致：`pinn.spec_hash == pinn_input_spec.json 的 cell_spec_hash`（缺失/失配 → `PINN_RESULT_STALE`，防陈旧结果冒充本轮）；
     - 数值物理区间 (比容量 [50, 4500] mAh/g、平均电压 [1.0, 5.5] V、能量密度 [50, 3000] Wh/kg) + 训练包络 + 残差 ≤ 0.05。
     任一环失败均为硬失败（`abr_workflow.yaml` 中 Stage 5 `strict: true`），阶段状态置 FAILED 并阻断推进。
  2. **提取-only 模式** (`trigger.triggered == false`，未匹配体系/总开关关闭)：`pinn_input_spec.json` 结构完整即通过——这是规范降级路径，不算错误；`triggered=true` 却无结果文件 → `PINN_TRIGGERED_RESULT_MISSING` 硬失败。
- 输入/模型身份绑定：spec 落盘时写入 `cell_spec_hash`（对 cell_spec 规范化 json 的 md5）；结果 payload 的 `pinn` 块统一注记 `spec_hash / scheme_hash / model_version` (体系 + 放电权重内容指纹) `/ run_id`。
- 失效语义：非 CONVERGED 重跑以失败态覆写旧结果并删除旧曲线 PNG；未匹配/总开关关闭时陈旧 result+PNG 一并移除；Stage 6 报告仅在 stage PASSED 且指纹守卫 (`_pinn_result_identity_ok`) 通过时才嵌入数值/几何/曲线。
