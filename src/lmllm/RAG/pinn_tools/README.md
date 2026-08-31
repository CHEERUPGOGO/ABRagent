# pinn_tools — PINN 物理验证工具协议（RAG 内"skill"）

> 本目录是 **RAG 项目内部的"工具调用"机制**：让 RAG 管线里的 LLM（Reviewer）
> 自主决定**何时**调用 PINN 物理模型计算材料方案性能，管线执行后把数值结果
> 注入最终答案。与 DeepSeek TUI 的 skill 系统无关，纯项目内实现。

---

## 1. 它解决什么问题

RAG 目前回答"材料方案性能"类问题时，只能引用文献里挖到的数值点。
本模块让管线多一个**独立物理计算**证据来源：

```
用户问 "NCM811 配锂金属负极 0.1C 能量密度多少？"
  → Writer 基于文献草稿答案
  → Reviewer 判断需要数值验证 → JSON 输出 needs_pinn: true
  → 管线执行 PINN 计算（subprocess 独立进程）
  → 计算结果注入 Reviewer 第二轮生成
  → 最终答案引用 PINN 数值 + 文献证据
```

**"告诉模型什么时候用"**：触发条件写在 `protocol.py` 的 `PINN_TOOL_PROMPT` 里，
该文本被拼进 `prompts.py` 的 `REVIEWER_SYSTEM_PROMPT`，由 LLM 自主决策
`needs_pinn` 字段，决定权在模型，不在规则。

## 2. 目录结构

```
src/lmllm/RAG/pinn_tools/
├── __init__.py            # 包导出（run_pinn_prediction / PINN_TOOL_PROMPT / ...）
├── protocol.py            # 工具声明文本（注入 Reviewer prompt 的触发规则）
├── executor.py            # 执行器：scheme → spec.json → subprocess → 结果 dict
├── registry.py            # backend 注册表：backend 名 → worker 命令
└── workers/               # 每个 worker 是自包含独立脚本（进程边界）
    ├── __init__.py
    ├── base.py            # DischargePrediction 契约（输出结构定义）
    ├── dummy.py           # 假实现（链路验证用，默认 backend）
    └── pinnstripes.py     # PINNSTRIPES SPM PINN 推理脚本（权重就绪后启用）
```

## 3. 核心设计：模型无关接口

**换模型（包括整个 PINNSTRIPES 包被替换）接口不变**，靠三条约束保证：

1. **公共代码零 import 任何 PINN 包**。`executor.py` / `protocol.py` / 契约
   不 import PINNSTRIPES 的模块；PINN 的 TensorFlow 等重依赖全部关在 worker 进程内。
2. **进程边界 + JSON 契约**。RAG ↔ PINN 唯一通道是 `subprocess` + JSON 文件。
   每个 worker 是**自包含独立脚本**：
   ```
   python <worker>.py --input spec.json --output pred.json
   ```
   不 import `pinn_tools` 任何东西，可整体搬迁 / 整体删除。
3. **注册表切换**。`registry.py` 的 `WORKERS` 表记录 backend 名 → 命令；
   `PINN_BACKEND` 环境变量选择当前生效的 backend。

### 输入输出契约

**输入**（spec.json，扁平 JSON）：

```json
{
  "cathode": "NCM811",
  "anode": "li_metal",
  "electrolyte": "lhce",
  "c_rate": 0.1,
  "voltage_min": 2.8,
  "voltage_max": 4.3,
  "temperature_C": 25
}
```

**输出**（pred.json，对应 `DischargePrediction`，字段只增不删）：

```json
{
  "v_curve": [4.2, 4.0, 3.9, 3.8, 3.7, 3.6, 3.4, 3.2],
  "q_end_mAh_g": 200.0,
  "v_mean": 3.7,
  "energy_wh_kg": 740.0,
  "confidence": "low",
  "data_gaps": ["dummy 假数据：未接入真实 PINN 模型"],
  "model": "dummy",
  "meta": {}
}
```

| 字段 | 含义 | 单位 |
|---|---|---|
| `v_curve` | 放电电压采样点 | V |
| `q_end_mAh_g` | 放电比容量 | mAh/g |
| `v_mean` | 平均放电电压 | V |
| `energy_wh_kg` | 材料级能量密度 | Wh/kg |
| `confidence` | high / medium / low / unknown | — |
| `data_gaps` | 缺参数字段 | — |
| `model` | 产生结果的 backend 名 | — |
| `meta` | 扩展信息（原始场、残差等，可选） | — |

任何失败返回 `{"error": "...", "hint": "..."}`，**不抛异常**，调用方降级。

## 4. 使用方式

### 4.1 当前状态（默认 dummy）

`registry.py` 中 `dummy` 始终启用，`pinnstripes` 仅在设置了
`PINNSTRIPES_MODEL_DIR` 环境变量时启用。`PINN_BACKEND` 默认 `"dummy"`。

dummy 返回查表假值，用于验证**机制链路**（LLM 触发 → 执行 → 注入），
数值无物理意义（输出里 `data_gaps` 会标注）。

### 4.2 启用 PINNSTRIPES（真实模型）

```bash
export PINNSTRIPES_MODEL_DIR=/path/to/model_folder   # 含 best.weights.h5 + config.json
export PINNSTRIPES_UTIL_DIR=/path/to/pinn_spm_param/util
export PINN_BACKEND=pinnstripes
```

> 前提：
> 1. 权重已训练（`integration_spm` 生成真值 → `main.py` 训练出 `best.weights.h5`）
> 2. `workers/pinnstripes.py` 中"场 → 放电曲线"后处理 TODO 已补全
>    （按 `util/spm.py` 的 rescale 定义，`V(t) = phis_c - phie - U_ocp(cs_surf)`，
>    积分出标量）
> 3. 推理环境（TensorFlow/Keras/tf2jax）就绪；若与 RAG 环境不同，
>    把 `registry.py` 里 `pinnstripes` 的 `cmd[0]` 改成该环境的 python 路径

### 4.3 换模型（新 PINN 包替换 PINNSTRIPES）

三步，RAG 侧零改动：

1. 新包写一个**自包含 worker 脚本**，实现 `--input spec.json --output pred.json`
   协议，内部负责把新包的输出映射成 `DischargePrediction` 结构；
2. `registry.py` 的 `WORKERS` 加一行（backend 名 → 命令）；
3. `export PINN_BACKEND=<新名>`。

## 5. 触发规则（LLM 何时调用）

写在 `protocol.py` → 拼进 `prompts.py` 的 `REVIEWER_SYSTEM_PROMPT`：

- **触发**（`needs_pinn: true`）：问题涉及具体材料组合方案的数值性能
  （容量/电压/能量密度/倍率）；草稿答案声称方案数值需交叉验证；
  用户明确要求"计算/验证/模拟/预测"。
- **不触发**（`needs_pinn: false`）：纯文献综述、定性对比、
  无具体材料组合方案的问题。

Reviewer JSON 输出示例：

```json
{
  "issues": [],
  "revised_answer": "...",
  "confidence": "medium",
  "needs_pinn": true,
  "pinn_condition": {"c_rate": 0.1, "voltage_min": 2.8, "voltage_max": 4.3}
}
```

管线侧执行前提：**能从问题提取到材料方案**（`rag_pipeline._extract_scheme`
匹配 `RAG/data/candidates.json` 的 id/别名）。提取不到时 PINN 不执行，
`pinn_result` 返回 `{"error": "未提取到材料方案..."}` 并注入二轮，
LLM 保持保守。

## 6. 代码调用方式（CLI / Python）

```bash
# 直接调 worker（自包含）
python src/lmllm/RAG/pinn_tools/workers/dummy.py \
    --input /tmp/spec.json --output /tmp/pred.json

# 经 executor（RAG 标准入口）
cd /home/ls/xiaoyue/LLM2/LMLLM
conda activate py3134_conda
python -c 'import sys;sys.path.insert(0,"src");from lmllm.RAG.pinn_tools import run_pinn_prediction,backend_status
print(backend_status())
r = run_pinn_prediction({"cathode":"NCM811","anode":"li_metal"},{"c_rate":0.1})
print(r)'
```

## 7. 前端测试（Gradio）

```bash
conda activate py3134_conda
export DEEPSEEK_API_KEY="your-key"
python src/lmllm/RAG/single_turn/app.py     # 端口 7860
```

- 触发组问题：`NCM811 配锂金属负极，0.1C 放电，能量密度大约能到多少？`
  → 「📊 过程日志」Tab 应出现 `### 5.1 PINN 数值验证` 块，
  回答引用 PINN 数值
- 对照组问题：`硅基负极和石墨负极哪个循环寿命更长？` → 无 5.1 块

## 8. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 日志无 5.1 块 | LLM 判定 `needs_pinn: false` | 问题不够"方案数值"导向；调 `protocol.py` 触发措辞 |
| `pinn_result.error` = "未提取到材料方案" | 问题不含 candidates.json 里的材料名/别名 | 问题里加材料名 |
| `pinn_result.error` = "PINN backend 不可用" | `PINN_BACKEND` 指向未启用的 backend | 检查环境变量 |
| `pinn_result.error` = "model-dir 无效或未训练" | 权重未训练或路径错 | 训练或修正 `PINNSTRIPES_MODEL_DIR` |
| `pinn_result.error` = "后处理未实现" | `pinnstripes.py` TODO 未补 | 补场→曲线换算 |

## 9. 待办

- [ ] `workers/pinnstripes.py` 场 → 放电曲线后处理（需对照训练参数集）
- [ ] 训练 PINNSTRIPES 权重并验证（对照 PyBaMM SPM / 文献锚点）
- [ ] （可选）接入 `pinn/cell_spec_schema.py` 的 CellSpec 契约做参数归一化
- [ ] （可选）暴露为 MCP 工具供项目外客户端复用

---

*文档版本：v1.0（2026-08-27）。接口变更时同步更新本文件。*
