# Stage 5: PINN 物理仿真校验指引 (可跳过)

## 任务目标
1. 【默认配置：跳过 (Skip)】快速验证模式下，系统直接放行本阶段，保留 RAG 规则验证的理论指标。
2. 【激活模式】将设计方案参数映射至 `CellSpec` 契约，调用 PyBaMM Newman P2D 偏微分方程求解器或 PINN 神经网络代理模型，预测连续放电曲线与电芯有效能量密度。

## 验收门禁 (PINNPhysicsChecker)
- 若 `stage.skip == True`：直接通过门禁。
- 若 `stage.skip == False`：检查 `simulation_result.json`，校验比容量 ($0\sim 600\text{ mAh/g}$)、平均电压 ($1.0\sim 5.5\text{ V}$)、能量密度 ($0\sim 2500\text{ Wh/kg}$) 是否处于合理物理区间。
