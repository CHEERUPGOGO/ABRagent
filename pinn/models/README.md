# pinn/models — SPM PINN 模型资产目录

Stage 5 (PINN 物理仿真) 的模型注册表与权重资产。加载与推理入口：`pinn/spm_runner.py`
（`load_registry` / `match_pinn_system` / `run_pinn_discharge`）。**本目录文件随 wheel
分发**（见 pyproject.toml package-data），不要在此存放临时文件。

## 目录结构

```
pinn/models/
├── registry.json            # 唯一发现入口：体系 → 匹配规则 + 有效范围 + 模型文件
└── systems/<system_id>/     # 每个化学体系一个目录 (命名 = 负极_正极)
    ├── params.json          # 电化学参数 (SI 单位, {unit, value} 结构)
    ├── pOCV_*.txt           # pOCV 曲线 (2 列: 化学计量比 x [-], 电位 U [V])
    ├── train_report_{ne,pe}.json   # 训练配置与指标 (ref_check.rms → pde_residual_loss)
    ├── golden_io.json       # 导出时生成的网络金样本 (输入, TF输出)，离线测试锁定等价性
    └── weights/
        ├── discharge_ne.npz # 负电极 PINN 权重 (从 TF SavedModel 导出)
        └── discharge_pe.npz # 正电极 PINN 权重
```

## 关键约定

- **匹配只看 (cathode, anode)**：registry `match.cathode / match.anode` 是 candidates.json
  风格的 id 别名列表（如 GrSi 的负极默认只匹配 `si_base`）。电解液不参与匹配——SPM
  只用 params 中的 c_e0，方案电解液不同时结果仍以 params 为基准。
- **spec 驱动的显式外部输入**：R_s、D_s、电极厚度、孔隙率取自 `pinn_input_spec.json`
  的 cell_spec（`cathode.L/epsilon` → 正极，`anode.L/epsilon` → 负极，`material.R_p/D_s`
  → R_s/D_s），C-rate 取 `condition.c_rate`；spec 未提供的字段回退体系 params.json。
- **逐参数有效范围（input_ranges）**：每个体系必须显式声明 thickness/porosity/R_s/D_s
  （分电极）与 c_rate 的有效范围，`run_pinn_discharge` 在调用网络前逐参数校验，越界
  返回 OUT_OF_RANGE（错误信息点名字段与范围）；组合约束由 `envelope`
  (|bc|/|bc_ref| ∈ [0.02, 15]) 第二层拦截 (OUT_OF_ENVELOPE)。两层都在网络前向之前。
- **权重是纯 numpy 消费的 npz**：网络结构 `Input(3)→BN→Dense(30,ELU)×2→Dense(1)`
  (float64)。不要手工改动 npz；任何模型更新必须走导出脚本
  `scripts/export_pinn_spm_weights.py`（内含 TF vs numpy 前向对拍自校验）。
- **放电/充电配对**：`directions.discharge` 与 `directions.charge` 成对预留；当前仅有
  放电。充电模型就位后：导出 npz 到 `weights/charge_{ne,pe}.npz` + registry
  `directions.charge` 填路径 + 声明 input_ranges 即可，`run_pinn_charge` 无需改代码。

## 新增一个化学体系的步骤

1. 在 `PINN-SPM-fast-prototyping` 训练（或复用 `train_universal` 流程），得到
   `UNI SPM NE/PE` SavedModel 与 `params_<负极>_<正极>.json` + pOCV txt。
2. 建 `systems/<system_id>/`，拷入 params/pOCV/train_report。
3. 运行 `conda run -n pinn210 python scripts/export_pinn_spm_weights.py \
   --system <system_id> --models-dir <SavedModel目录> --source-params <params.json>`
   （自校验通过才会写 npz + golden_io.json）。
4. 在 `registry.json` 增加条目（match 别名 + directions + **input_ranges 逐参数有效
   范围** + envelope）。
5. 跑 `pytest auto_battery_research/tests/test_stage5_pinn_inference.py -q` 验证。
