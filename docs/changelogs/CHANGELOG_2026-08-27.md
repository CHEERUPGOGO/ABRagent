# CHANGELOG 2026-08-27

> 本次提交范围（**仅**用户指定的路径）：`agent/`、`src/lmllm/RAG/`、`miner/`
> （排除 `miner/backfill_chroma_missing.py`、`miner/logs/`）、`extract_doi_dedup.py`、
> `extract_doi_only.py` + 本文档
> 其余工作区改动（papers/、preprocessing/、pinn/、PINNSTRIPES-main/ 等）一律不上传
> 上传目标：GitHub main 分支
> 文档版本：v1.0（2026-08-27）

---

## 概览

| 模块 | 性质 | 变更要点 |
|---|---|---|
| `src/lmllm/RAG/pinn_tools/` | 新增 | PINN 物理验证工具协议（RAG 内"工具调用"机制） |
| `src/lmllm/RAG/agents.py` 等 | 修改 | Reviewer 插桩 C：LLM 自主决策 needs_pinn → 执行 PINN → 结果注入二轮 |
| `agent/` | 新增+修改 | token 切分（2000/2500/3500）与中英对比实验系列 + 中文文献过滤 |
| `miner/` | 新增+修改 | 数据质量治理层 + 属性标签体系扩充（首效/SEI/Rct 等） |
| `extract_doi_*.py` | 新增+修改 | DOI 提取去重多模式脚本 + extract_doi_only 命令行化 |

---

## 1. src/lmllm/RAG — PINN 物理验证工具协议（核心新增）

### 1.1 新增 `pinn_tools/` 包（9 文件 + README.md）

**定位**：RAG 项目内部的"工具调用"机制。让 RAG 管线里的 LLM（Reviewer）
自主决定**何时**调用 PINN 物理模型计算材料方案性能，管线执行后把数值结果
注入最终答案。与 DeepSeek TUI 的 skill 系统无关，纯项目内实现。

```
src/lmllm/RAG/pinn_tools/
├── __init__.py            # 包导出
├── protocol.py            # 工具声明文本（注入 Reviewer prompt 的触发规则）
├── executor.py            # 执行器：scheme → spec.json → subprocess → 结果 dict
├── registry.py            # backend 注册表：backend 名 → worker 命令
├── README.md              # 模块文档（用途/使用方式/换模型流程/故障排查）
└── workers/
    ├── base.py            # DischargePrediction 契约（输出结构定义）
    ├── dummy.py           # 假实现（链路验证用，默认 backend）
    └── pinnstripes.py     # PINNSTRIPES SPM PINN 推理脚本（权重就绪后启用）
```

**核心设计——模型无关接口**（换整个 PINNSTRIPES 包只改 registry 一行）：

1. **公共代码零 import 任何 PINN 包**：executor/protocol/契约不 import
   PINNSTRIPES 模块，TensorFlow 等重依赖全在 worker 进程内。
2. **进程边界 + JSON 契约**：RAG ↔ PINN 唯一通道是 subprocess + JSON 文件；
   每个 worker 是自包含独立脚本（`python <worker>.py --input spec.json --output pred.json`），
   可整体搬迁/删除。
3. **注册表切换**：`PINN_BACKEND` 环境变量选择 backend（默认 dummy，
   pinnstripes 需 `PINNSTRIPES_MODEL_DIR` + `PINNSTRIPES_UTIL_DIR`）。

**输出契约**（`DischargePrediction`，字段只增不删）：`v_curve`（放电曲线）、
`q_end_mAh_g`（比容量）、`v_mean`（平均电压）、`energy_wh_kg`（能量密度）、
`confidence`、`data_gaps`、`model`、`meta`。失败返回 `{"error", "hint"}` 不抛异常。

### 1.2 修改 3 个 RAG 文件（PINN 插桩 C）

**`agents.py`（+30 行）**——`ReviewerAgent.run` 新增插桩 C：
- 解析 Reviewer JSON 输出的 `needs_pinn` 决策字段（容错 "true"/"1"/"yes"）
- 触发且管线提取到材料方案（`scheme`）时，调 `run_pinn_prediction(scheme, pinn_condition)`
- 结果（含 error）注入第二轮 LLM 审核：`【PINN 物理模型计算结果】` 块，
  LLM 以计算结果为准修正草稿，矛盾时以计算为准
- 二轮失败保留首轮结果；`pinn_result` 附加到输出
- 新增 `import json`、`from .pinn_tools import run_pinn_prediction`（try/except 降级）

**`prompts.py`（+10 行）**——`REVIEWER_SYSTEM_PROMPT`：
- JSON 输出格式新增 `needs_pinn`（bool）和 `pinn_condition`（工况：c_rate/
  voltage_min/voltage_max/temperature_C）字段
- prompt 末尾拼接 `PINN_TOOL_PROMPT`（触发条件：方案数值性能/草稿数值交叉验证/
  用户要求计算模拟；不触发：综述/定性/无方案）

**`structured_output.py`（+17 行）**——`format_process_log`：
- 新增 `### 5.1 PINN 数值验证（独立物理计算）` 展示块（模型名/置信度/
  比容量/平均电压/能量密度/数据缺口；error 时显示"计算不可用"）
- 前端 Gradio「过程日志」Tab 可直观测评 PINN 触发与执行结果

### 1.3 RAG 其他变更

- 新增 `PROJECT_STATUS.md`：高比能液态锂电池设计方案系统实施状态文档
  （架构图、26 题 ground truth 评测、插桩 A/B 定位）
- 修改 `data/tasks/eval_report.json`：设计任务评测报告更新

---

## 2. agent/ — token 切分与中英对比实验

### 2.1 新增实验脚本（8 个）

| 文件 | 说明 |
|---|---|
| `clean_rag_tok2000.py` / `2500` / `3500` | clean_rag 的 token 版（DeepSeek 真实 tokenizer，CMAX=2000/2500/3500 tokens），与字符版 rag_clean 对比 |
| `pipeline_tok2000.py` / `2500` / `3500` | 对应 token 版挖掘管线 |
| `merge_tok_results.py` | 按 DOI 合并挖掘结果 → merged_all.json + CSV（离线、幂等，不重调 LLM） |
| `run_zh_en_compare.py` | 5 pipeline × 6 篇（3 英 3 中）提取对比，可选 `--pipeline` 单独跑 |

### 2.2 修改（9 个文件）

- `config.py`（+20）：新增中文文献过滤 `SKIP_ZH_DOCS`/`ZH_RATIO_THRESHOLD`（0.05）
  与 `is_zh_doc()`——数据挖掘线跳过中文字符占比 ≥5% 的文档（双语期刊 0.6~1.2%
  不误判，知网中文 ≥13.2% 正确跳过）；入库线不受影响
- `prompts.py`（+49）：新增实验相关 prompt
- `phase12_extract.py`（+53）、`pipeline.py`/`pipeline_c1500.py`/`pipeline_c4000.py`/
  `pipeline_rag_clean.py`/`pipeline_v6_clean.py`（+27~46）：支持 tok 系列与对比实验
- `phase3_merge_agent.py`（+158）：合并逻辑加固——condition_id 有无分离、
  `_safe_val_str` 脏数据防御（LLM value 可能是 dict/list）、`_norm_num` 数值归一化、
  `_merge_prop_list` 同材料同属性数值相近（相对偏差 <2%）合并（text+table 来源合并）、
  `_has_value` 空提取过滤

### 2.3 新增实验产物 `output/`

- `test3/`、`tok2000_mine/`、`tok_vs_char/`、`zh_en_compare/`：
  各实验组 CSV/JSON 结果 + `对比报告_chunk参数.md` + `对比数据完整表.csv` + 统计表

---

## 3. miner/ — 数据质量层 + 标签体系扩充

### 3.1 新增模块

- `data_quality/clean_extracted.py`：提取后数据清洗（数值异常值过滤 + 单位归一化
  + 质量报告）——挖掘流水线**末端数据治理层**，补上现有 pipeline 缺少的
  数值异常值过滤环节

### 3.2 修改

- `format1/` 属性标签体系扩充（anode/cathode 的 structured_data / information /
  example_text / explanation）：新增并细化性能标签定义——
  `Reversible_Capacity_First_Cycle`（可逆比容量，区分 First_Lithiation_Capacity）、
  `SEI_Resistance`、`Charge_Transfer_Resistance`、`Capacity_Retention`、ICE 等，
  每项含中英双语定义、Alternative names、示例 JSON、提取要求
  （`ANODE_PERFORMANCE_LABELS` 扩充至 28、`CATHODE_PERFORMANCE_LABELS` 19，含断言校验）
- `anode_database/anode_formatter.py`、`cathode_database/cathode_formatter.py`：小修
- `cleaning/cleaner_v2_standalone.py`（+34）：清洗逻辑更新
- `pricing.py`（+15）：计费估算调整

---

## 4. extract_doi_*.py — DOI 提取脚本

- **`extract_doi_dedup.py`（新增）**：多模式脚本——模式一（默认）：从 PDF 提取
  DOI，按 DOI 重命名复制到 pdf_doi/，重名自动跳过并比较文件大小保留较大者；
  模式二（`--check-existing`）：检查 database/type/ 下是否已有同名 md（仅报告）
- **`extract_doi_only.py`（+74 行重写）**：argparse 命令行化——
  `--input`/`--output`/`--no-doi`/`--skip-no-doi`；保留原始子文件夹结构，
  成功 → `pdf_doi/{子文件夹}/{DOI}/原文件名.pdf`，无 DOI → `pdf_no_doi/` 或
  `--skip-no-doi` 时原地跳过待手动处理；支持直接传子文件夹路径

---

## 5. 完整文件清单

### 新增（未跟踪 → 纳入本提交）
```
agent/clean_rag_tok2000.py  agent/clean_rag_tok2500.py  agent/clean_rag_tok3500.py
agent/pipeline_tok2000.py   agent/pipeline_tok2500.py   agent/pipeline_tok3500.py
agent/merge_tok_results.py  agent/run_zh_en_compare.py
agent/output/test3/         agent/output/tok2000_mine/   agent/output/tok_vs_char/
agent/output/zh_en_compare/
miner/data_quality/__init__.py  miner/data_quality/clean_extracted.py
src/lmllm/RAG/pinn_tools/（9 文件 + README.md）
src/lmllm/RAG/PROJECT_STATUS.md
extract_doi_dedup.py
CHANGELOG_2026-08-27.md（本文档）
```

### 修改（已跟踪文件）
```
agent/config.py  agent/phase12_extract.py  agent/phase3_merge_agent.py
agent/pipeline.py  agent/pipeline_c1500.py  agent/pipeline_c4000.py
agent/pipeline_rag_clean.py  agent/pipeline_v6_clean.py  agent/prompts.py
extract_doi_only.py
miner/anode_database/anode_formatter.py
miner/cathode_database/cathode_formatter.py
miner/cleaning/cleaner_v2_standalone.py
miner/format1/andode_explanation.py  miner/format1/anode_example_text.py
miner/format1/anode_information.py   miner/format1/anode_structured_data.py
miner/format1/cathode_example_text.py  miner/format1/cathode_explanation.py
miner/format1/cathode_information.py   miner/format1/cathode_structured_data.py
miner/format1/electrolyte_explanation.py
miner/pricing.py
src/lmllm/RAG/agents.py  src/lmllm/RAG/prompts.py
src/lmllm/RAG/structured_output.py  src/lmllm/RAG/data/tasks/eval_report.json
```

### 明确排除（不上传）
```
miner/backfill_chroma_missing.py
miner/logs/（全部日志文件）
```