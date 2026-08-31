# 阶段 D：PINN 物理验证层实施方案（PINN-P2D）

> 状态：方案定稿，待 miner JSON 全量挖掘完成后实施
> 前置依赖：阶段 A（CellSpec 契约）、阶段 B（P2D 求解器）、阶段 C（文献验证闭环）均已完成
> 关联代码：`pinn/cell_spec_schema.py`、`pinn/p2d_runner.py`、`pinn/validate_against_literature.py`

---

## 1. 目标与定位

### 1.1 要解决什么问题

当前链路已经跑通：LLM/RAG 方案 → CellSpec → PyBaMM P2D 求解（30~60s）→ 积分标量 → 文献验证。但有两个痛点：

1. **慢**：PyBaMM casadi 求解单方案 30~60s，RAG Reviewer 做实时交互校验不可接受。
2. **纯数值**：casadi 是黑盒求解器，不能作为"物理约束学习"的研究载体，也无法在数据稀疏时给出置信度。

阶段 D 用 **PINN（物理信息神经网络）** 替代/增强求解器：

- **毫秒级预测**：神经网络前向传播替代数值求解。
- **物理一致性**：损失函数含 PDE 残差，保证预测不违反物理定律（比纯数据 ML 强）。
- **可解释边界**：物理残差大小本身是"该方案是否超出参数空间"的置信信号。

### 1.2 成功标准（可验证）

| 指标 | 目标 | 测量方式 |
|---|---|---|
| 代理模型曲线偏差 | 相对 PyBaMM 真值 < 5% | 留出测试集 V(Q) 曲线逐点偏差 |
| 代理模型标量偏差 | 相对 PyBaMM 真值 < 2% | 比容量/平均电压/能量密度 |
| 真 PINN（SPM）偏差 | 复现 PyBaMM SPM < 3% | 放电曲线对比 |
| 文献验证 | 基准材料偏差 < 10% | 复用 validate_against_literature.py |
| 推理速度 | 单方案 < 50 ms | 接入 Reviewer 后计时 |

---

## 2. 总体架构（数据流）

```
[数据层] miner JSON 全量挖掘（用户进行中）
   │  ↓ 归一化：property_name 对齐 / 单位统一 / 异常值过滤
   ▼
[锚点库] pinn/data/literature_anchors.json
   │  （material × condition × property × value × unit，带 provenance）
   │  ↓ 标定层
   ▼
[标定] c_max 反推（文献比容量 → 有效 c_max）
   │      D_s 标定（GITT 扩散系数，量级校验后覆盖）
   │      active_ratio 校准（BetterBat 电芯级能量密度）
   │  ↓
   ▼
[训练层] 真值生成：PyBaMM 参数空间采样（拉丁超立方）
   │  ├── 路径 A：代理模型（参数向量 → 放电曲线/标量）  ← 先做，能立刻接入 RAG
   │  └── 路径 B：真 PINN（SPM 固相扩散 PDE → DFN 扩展） ← 研究深化
   │  ↓
   ▼
[接入层] RAG Reviewer 插桩 B 增强：
   │  LLM 方案 → CellSpec → PINN 快速预测 → 与文献锚点/经验区间对比 → 可行性判定
   ▼
[输出] 可行性报告（预测值 + 置信度 + 数据缺口）
```

---

## 3. 数据管道（miner 全量挖掘完成后）

### 3.1 现状盘点（2026-08-19）

- `miner/json/` 已挖掘：**90 个 extracted JSON**（anode 42 / electrolyte 26 / cathode 21）
- 段落 items：**1155 段**，其中含性能信息（performance_info）**556 段**、含属性信息（extracted_info）**315 段**
- 全量挖掘完成后规模预估：500~2000 篇文献，性能锚点 3000~20000 个

### 3.2 归一化与清洗（复用 validate_against_literature.py 机制）

1. **property_name 对齐**：`PROPERTY_ALIASES` 已包含 agent/miner 真实命名
   （`Discharge_Specific_Capacity_Initial` → `specific_capacity` 等）。
   全量挖掘后若有新命名，追加到 `cell_spec_schema.PROPERTY_ALIASES`。
2. **单位统一**：`cm²/s → m²/s`（×1e-4）、`S/cm → S/m`（×100）、`mg/cm² → kg/m²`（×0.01）。
3. **异常值过滤（物理量级校验）**：
   - 比容量：0 ~ 500 mAh/g
   - 能量密度：0 ~ 2000 Wh/kg（材料级）
   - 固相扩散系数：1e-16 ~ 1e-13 m²/s（超出即拒绝，见 `calibrate_ds`）
   - 孔隙率/体积分数：0 ~ 1
4. **条件标签完整性分级**：
   - 严格锚点：scenario + 倍率 + 电压窗口齐全（与 P2D 模拟同条件）
   - 宽松锚点：缺条件标签，仅做量级参考
   - 验证对比**只用严格锚点做结论**，宽松锚点做辅助。

### 3.3 锚点库构建

```python
# 目标产物 pinn/data/literature_anchors.json
{
  "version": "1.0",
  "source": "miner/json 全量",
  "n_anchors": 12000,
  "anchors": [
    {
      "doi": "10.1016/...",
      "material": "GP-NCM",
      "profile": "NCM811",          # material_to_profile 映射
      "component": "cathode",
      "property": "specific_capacity",
      "value_mAh_g": 213.31,
      "condition": {"scenario": "half_cell_test", "c_rate": 0.1,
                    "v_min": 2.8, "v_max": 4.3, "temperature_C": 30},
      "provenance": {"source": "miner", "confidence": "medium"},
      "is_strict": true
    }
  ]
}
```

实现：`pinn/build_anchor_library.py`（遍历 miner/json 全量 extracted JSON + CSV，归一化后落盘）。

### 3.4 标定流程（用锚点校准方程系数）

**c_max 反推**（已完成 NCM811/Ni96，扩展到全材料）：

```
specific = c_max × Δstoich × F / (ρ × 3600)
→ c_max = specific × ρ × 3600 / (Δstoich × F)
```

- Δstoich 由 OCP 曲线和电压窗口决定（对 NCM 系 2.8-4.3V ≈ 0.71，从 PyBaMM 验证反推）
- 每个材料的 profile 填入标定后的 c_max（`p2d_runner.MATERIAL_PROFILES`）

**D_s 标定**：GITT 扩散系数点经 `calibrate_ds` 量级校验后，覆盖
`Positive particle diffusivity [m2.s-1]`。当前 2 个点量级异常被拒，全量挖掘后
期待获得有效点。

**active_ratio 校准**：用 BetterBat 商业电芯级能量密度 / P2D 材料级能量密度
反推各负极体系的电芯级折算系数（替代当前工程经验 0.35/0.42/0.50）。

---

## 4. 模型设计

### 4.1 路径 A：PINN 代理模型（Surrogate，推荐先做）

**定位**：不直接解 PDE，而是用 PyBaMM 物理模型生成大量真值数据，
训练神经网络拟合"参数 → 放电曲线/标量"。本质是**物理数据驱动的 ML**，
数据全部来自物理模型（自洽），比纯文献数据 ML 外推可靠得多。

**输入特征**（CellSpec 参数向量，标准化后）：

```
x = [c_max, L, porosity, ε_s, mass_loading, D_s, k_ref,
     c_e0, κ, t_plus, D_e, c_rate, V_min, V_max, T]     # ~15 维
```

**输出**：
- 主任务：放电曲线 V(Q)，离散为 64 个点（V 和 Q 的等距采样）
- 辅助任务（多头）：标量 [Q_end, V_mean, E_material]

**架构**：

```
输入 (15) → Linear(128) → Swish → Linear(256) → Swish → Linear(256) → Swish
  → 残差块 ×2 → 分支1: Linear → 64（曲线）
             → 分支2: Linear → 3（标量）
```

**训练数据生成**（`pinn/gen_training_data.py`）：

1. 参数空间拉丁超立方采样：以 NCM811 profile 为基准，各参数 ±30% 变化，
   倍率 0.1C~5C，电压窗口按材料 profile
2. 每组调用 `p2d_runner.run_discharge` 生成曲线（复用现有求解器）
3. 规模：3000~10000 组（按 30~60s/组，并行 8 进程约 3~10 小时）
4. 80% 训练 / 10% 验证 / 10% 测试（严格按参数空间划分，不随机泄漏）

**损失**：

```
L = MSE(V_pred, V_true) + λ1 · MSE(scalar_pred, scalar_true)
  + λ2 · 物理正则（V 对 Q 单调性惩罚 + 能量守恒 |V_mean×Q_end - E|）
```

**训练**：Adam 1e-3 → 1e-4，early stopping，batch 128，~200 epochs。

**验证**：留出测试集（PyBaMM 真值）曲线偏差 < 5%、标量偏差 < 2%。

### 4.2 路径 B：真 PINN（物理残差求解）

**定位**：真正的 PINN——神经网络表示 PDE 解，损失含方程残差。研究价值高，
工程量大。分两步走。

**第一步：SPM（单粒子模型）**

控制方程（无量纲化后）：

```
固相扩散（球坐标）：
  ∂c/∂t = (D_s / R²) · (1/r̃²) · ∂/∂r̃ (r̃² ∂c/∂r̃)      # 0<r̃<1, t>0

边界条件：
  ∂c/∂r̃|r̃=0 = 0
  -D_s/R · ∂c/∂r̃|r̃=1 = j / (F · a_s)                    # Butler-Volmer 通量

Butler-Volmer（正极表面过电位）：
  j = i0 · [exp(αa·η·F/RT) - exp(-αc·η·F/RT)]
  η = φ_s - φ_e - U_ocp(c_surf)

输出电压：
  V = φ_s,pos - φ_s,neg
```

PINN 结构：

```
输入 (t, r̃) → MLP(3×64, tanh) → c(t, r̃)
损失：
  L = λ_pde · ||∂c/∂t - 扩散算子||²        # 残差点：时间×径向网格 ~5000 点
    + λ_bc · ||边界条件||²
    + λ_ic · ||c(0, r̃) - c_init||²
    + λ_data · ||c(t_snap, r̃) - PyBaMM 快照||²   # 少量监督锚定
```

训练策略：Adam 前 5000 步（学习率 1e-3）→ L-BFGS 精修；
自适应损失权重（GradNorm 或简单手动调度）；对时间域做归一化。

验证：与 PyBaMM SPM（`pybamm.lithium_ion.SPM`）同参数放电曲线对比 < 3%。

**第二步：DFN 扩展（可选，研究级）**

加入电解液相扩散与电势方程，用**分域 PINN**（正极/隔膜/负极分别建模，
界面连续性条件拼接）。预计 1~2 周工作量，收敛困难时保留 casadi 作为最终求解器，
PINN 作为快速近似 + 置信度估计器。

### 4.3 路径 A vs 路径 B 分工

| 维度 | 路径 A（代理模型） | 路径 B（真 PINN） |
|---|---|---|
| 训练数据 | PyBaMM 采样 3000~10000 组 | PDE 残差 + 少量 PyBaMM 快照 |
| 训练时间 | 10~30 分钟 | 数小时~数天 |
| 预测速度 | 毫秒级 | 毫秒级 |
| 外推能力 | 参数空间内插值 | 物理约束下可外推 |
| 工程风险 | 低 | 高（DFN 收敛困难） |
| 用途 | RAG Reviewer 实时校验 | 研究深化 + 置信度估计 |

**建议**：A 先行（1-2 天可接入 RAG），B 作为研究主线持续推进。

---

## 5. 与现有代码衔接

| 模块 | 角色 | 改动 |
|---|---|---|
| `pinn/cell_spec_schema.py` | 输入特征标准化的契约 | 加 `to_feature_vector(spec)` 和归一化参数表 |
| `pinn/p2d_runner.py` | 真值生成器 | `run_discharge` 加批量模式（多组参数循环） |
| `pinn/validate_against_literature.py` | 文献验证集 | 锚点库改为读 `literature_anchors.json` |
| `pinn/build_anchor_library.py` | 新建：全量锚点库构建 | — |
| `pinn/gen_training_data.py` | 新建：PyBaMM 参数采样 | — |
| `pinn/surrogate_model.py` | 新建：代理模型（路径 A） | — |
| `pinn/pinn_spm.py` | 新建：真 PINN（路径 B 第一步） | — |
| `src/lmllm/RAG/relation_engine.py` | 插桩 B 接入点 | `check_scheme` 增加 PINN 数值校验后端 |

**RAG 接入点（最终形态）**：

```python
# relation_engine.check_scheme() 增强
def check_scheme_with_pinn(self, scheme: Dict) -> Dict:
    spec = candidates_scheme_to_cell_spec(scheme)
    pred = pinn_predict(spec)          # 毫秒级
    verdict = validate_against_anchors(pred, spec, anchor_library)
    return {
        "feasible": verdict.feasible,
        "pred_energy_Wh_kg": pred.E_cell,
        "confidence": verdict.confidence,     # 物理残差/锚点密度
        "data_gaps": verdict.data_gaps,       # 缺参数的字段
    }
```

Reviewer Agent 把该结果作为"数值校验证据"写入最终答案，
实现对 LLM 方案的"物理可行性论证"。

---

## 6. 里程碑（miner 全量挖掘完成后按序执行）

| 里程碑 | 内容 | 预计 | 产出 |
|---|---|---|---|
| M1 | 数据管道：锚点库 + c_max/D_s 全材料标定 | 1~2 天 | `literature_anchors.json` + 更新 `MATERIAL_PROFILES` |
| M2 | 代理模型：采样 + 训练 + 验证 | 2~3 天 | `surrogate_model.py` + 测试集偏差报告 |
| M3 | 真 PINN-SPM：PDE 残差训练 | 3~5 天 | `pinn_spm.py` + PyBaMM SPM 对比报告 |
| M4 | RAG 接入：Reviewer 数值校验后端 | 1 天 | `check_scheme_with_pinn` 集成 |
| M5 | （可选）真 PINN-DFN | 1~2 周 | 分域 PINN + 置信度估计 |

每个里程碑的验收 = 第 1.2 节成功标准中对应指标达成。

---

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 文献数据噪音（条件标签缺失/单位错误） | 锚点库污染 | 严格/宽松分级；物理量级校验拒绝异常值；多源交叉验证 |
| 代理模型参数空间外推不可靠 | Reviewer 误判 | 输出"参数空间边界"信号；超出范围降级为 casadi 求解 |
| 真 PINN 训练不收敛（DFN） | 进度延迟 | 先 SPM；两阶段优化（Adam+L-BFGS）；时间域归一化；不收敛则 PINN 降级为近似器 |
| 采样数据量大（10000 组 × 60s） | 训练数据生成慢 | 并行 8 进程；先 3000 组起步；SPM 快采样（秒级）用于预训练 |
| PyBaMM 版本升级 API 变化 | 代码失效 | 锁定 pybamm==26.8.0（requirements 记录） |

---

## 8. 前置检查清单（挖掘完 miner 后）

1. `miner/json/` 全量 extracted JSON 已就位（`build_anchor_library.py` 能遍历到）
2. `agent/output/rag_clean/*.csv` 已刷新（含全量锚点）
3. `pybamm` 版本锁定 26.8.0，`p2d_runner` 回归通过（NCM811 192.5 mAh/g）
4. 新出现的 property_name 已补进 `PROPERTY_ALIASES`
5. 记录全量锚点数与组件分布（对照 3.1 节基线）

---

*文档版本：v1.0（2026-08-19）。实施时如遇偏差，以实际数据为准并回填本方案。*
