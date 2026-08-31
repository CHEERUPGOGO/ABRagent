# 2026-08-20 改动介绍

> 本次提交覆盖两部分：`src/lmllm/RAG`（多库混合检索 + 关系引擎 + 设计任务评测）与 `pinn/`（PINN 物理验证层，全新模块）。
> 上传范围说明见文末第三节。

---

## 一、`src/lmllm/RAG`：修改与新增

### 1. 双库混合检索升级（文献库 + 电子书库）

- **`multi_retrieval.py`（改）**：每个库内部改为 Chroma + BM25 双路召回、RRF 融合排序，跨库之间加权合并。新增 `search_literature` / `search_ebook` 单库接口；新增 `_build_bm25_from_store`，从 Chroma 持久化目录分页读取全部段落构建 BM25 索引。
- **`rag_pipeline.py`（改）**：
  - 接入电子书 Chroma 库与 `MultiRetrieval` 混合检索器；
  - 新增 `_compute_dynamic_weights`：按问题类型（理论关键词 → 电子书加权，实验关键词 → 文献加权）动态分配双库权重；
  - 历史上下文压缩改用 tiktoken 精确 token 计数 + overlap 截断策略；
  - Reviewer 插桩：`_extract_scheme`（提取材料组合方案）、`_extract_energy_claim`（提取能量密度声称值）。
- **`config.py`（改）**：新增 `EBOOK_CHROMA_DIR` / `EBOOK_COLLECTION_NAME` 电子书 Chroma 配置；移除 `FUSION_MODE`。
- **`agents.py`（改）**：`RetrievalAgent` 融合统一为 RRF（移除 weighted 模式及 `chroma_weight` / `fusion_mode` 参数）；Planner / Reviewer 支持可选 `relation_engine` 注入。
- **`prompts.py`（改）**：Planner 提示词新增 `db_type` 数据源选择规则（literature / textbook / both）；Writer 新增设计任务五段式输出结构（目标 → 推荐组合 → 预期指标 → 可行性依据 → 风险与数据缺口）。
- **删除**：`query_router.py`（路由逻辑并入 Planner + MultiRetrieval）；`multi_turn/app_hybrid.py`（旧版 Gradio 界面）。

### 2. 关系引擎与硬规则校验（新增）

- **`relation_engine.py`（新增）**：`RelationEngine` —— 别名归一（`normalize`）、问题实体抽取（`extract_entities`）、约束求值（`evaluate`）、方案校验（`check_scheme`）、检索修饰词生成（`query_modifiers`，插桩 A）、数值-条件绑定检测（`check_numeric_claims`）。引擎不可用时管线自动降级为纯 RAG。
- 数据：`data/alias_map.json`、`data/constraints.json`、`data/candidates.json`。

### 3. 能量模型与 BetterBat 校准（新增）

- **`energy_model.py`（新增）**：材料级/电芯级能量密度估算（`estimate_material_energy` / `estimate_cell_energy` / `estimate_scheme_energy`）、声称值校验（`check_energy_claim`）、体系经验区间检查（`check_energy_in_range`）、BetterBat 电芯数据库校准（`calibrate_active_ratios`）。
- 数据：`data/calibrated/energy_ranges.json`、`data/calibrated/mp_crosscheck.json`。

### 4. 设计任务生成与评测（新增）

- **`task_generator.py`（新增）**：四类设计任务生成 —— 组合推荐（`gen_combination_tasks`）、约束验证负样本（`gen_constraint_tasks`）、掺杂设计（`gen_doping_tasks`）、参数设计（`gen_parameter_tasks`）。
- **`evaluate_design_tasks.py`（新增）**：rule 模式自洽性评测 + pipeline 模式端到端评测。
- 数据：`data/tasks/design_tasks.json`、`data/tasks/eval_report.json`。

### 5. 关系抽取与种子数据（新增）

- **`extractor.py`（新增）**：`RelationExtractor`（DeepSeek API 直连的 ICL few-shot 关系抽取，独立于 RAG 包其余部分）。
- **`schemas/battery_relations.py`（新增）**：各关系类型 schema 定义与 `build_fewshot_prompt`。
- 数据：`data/seed/`（`candidate_sentences.json`、`relations_seed.json`、`review_report.md`）。

### 6. 其他

- **`llm_client.py`（改）**：规则分解问题（`rule_decompose_question`）输出增加 `db_type` 字段。
- **`structured_output.py`（改）**：过程日志记录数据源类型。
- **`scripts/`（新增）**：批量/评测脚本 —— `fetch_datasets.py`（公开数据集下载）、`batch_extract.py`、`gen_seed.py`、`crosscheck_mp.py`、`review_seed.py` 等。

---

## 二、`pinn/`：PINN 物理验证层（全新模块）

阶段 D —— PINN-P2D 物理验证层，替代/增强 PyBaMM 数值求解器：毫秒级代理模型预测、物理一致性约束、可解释置信边界。完整方案见 `PINN_PLAN.md`（路径 A：代理模型，先做；路径 B：真 PINN-SPM，研究深化）。

- **`cell_spec_schema.py`（新增）**：CellSpec 契约 —— `MaterialSpec` / `ElectrodeSpec` / `ElectrolyteSpec` / `SeparatorSpec` / `CellDesignSpec` / `TestCondition` / `PerformanceAnchor` / `Provenance`；miner JSON → CellSpec 解析、缺省字段补全（`fill_missing`）、PyBaMM 参数字典转换（`to_pybamm_dict`）、物理合理性校验（`validate`）、方案能量密度估算（`estimate_scheme_energy`）。
- **`p2d_runner.py`（新增）**：PyBaMM P2D 求解封装 —— 参数构建（`build_parameter_values`，含半电池锂金属对电极）、放电曲线确定性积分（`integrate_curve`：比容量/平均电压/能量密度）、BetterBat 电芯级对标（`compare_to_betterbat`）、GITT 扩散系数量级校验（`calibrate_ds`）、`run_discharge` 入口。
- **`validate_against_literature.py`（新增）**：文献锚点验证闭环 —— 严格/宽松锚点分级（`load_anchors`）、偏差分级 excellent/ok/warning（`_verdict`）、逐点对比报告（`compare_group`）。
- **`_probe_ocp.py`（新增）**：PyBaMM OCP 函数 / 参数集化学体系 / 多材料覆盖资源探测脚本。
- **`output/`（新增）**：验证产物 —— `validation_report.json`、`validation_report_multi.json`、`curve_NCM811|li_metal|lhce_c0.1.json`（放电曲线）。

---

## 三、本次上传说明

- **上传**：`pinn/` 全部、`src/lmllm/RAG/` 的修改与新增、`.gitignore`、本文件。
- **不上传**：
  - `src/lmllm/RAG升级前/` —— 升级前旧版快照，仅本地保留；
  - `src/lmllm/RAG/data/raw/` —— 公开数据集下载产物（MP 快照 / BetterBat xlsx），已加入 `.gitignore`；
  - `src/lmllm/RAG/output/logs/` —— 运行日志。
- **`.gitignore` 更新**：新增 `src/lmllm/RAG/data/raw/` 与 `src/miner/` 规则。

---

## 四、中文文献入库支持（清洗与切分双语化）

支持中文文献（含中英双语期刊）走同一条入库链路，英文路径零改动。

- **`miner/cleaning/cleaner_v2_standalone.py`**（10 处双语规则并联）：
  - 中文噪音词：收稿日期/修回日期/录用日期/网络首发/基金项目/作者简介/中图分类号/文献标志码/文章编号 等知网特有字段；
  - 中文图注（图1/表 2/图S1）、中文章节标题（2.1 电解液）、中文参考文献（"[1] 张三…"）、中文地址/单位（大学/学院/研究院/研究所）、中文投稿信息、中文摘要（含"摘要："冒号形式）、中文引言定位（引言/前言/绪论）；
  - 短中文段保护：含中文、单行、≥4 字符或含中文标点的短段不再被误杀（多行页面残片与单字残字仍按噪音过滤）。
- **`miner/paragraph_metadata_pipeline_v5_qwen.py`**（3 处切分修正）：句末标点支持中文（。！？）；参考文献 `[n]` 模式（GB/T 7714），二级切分与独立成段共用。
- **`入库流程.md`**：新增"步骤 3.1 中文文献入库说明"。
- 验证：库内 3431 篇英文文献清洗输出**逐字节不变**（回归通过）；22 篇中文/双语文献清洗正常、噪音无残留。

## 五、agent 数据展平修复（CIP/AGG 展开不完整问题）

问题：`_all_intrinsic_data.csv` 中 CIP/AGG 等对象值部分未展开，混入 Python 字符串形态（`"{'CIP': 40.7, 'AGG': 0}"`），且同一条数据重复多行。根因：① LLM 输出的 value 结构不稳定（unit 混入 dict / value 嵌套 / 字符串三种形态）；② CSV 生成存在多条路径且 append 混写。

- **`agent/flatten_ml.py`**：
  - 新增 `_normalize_property_value()`：统一三种 value 形态 —— dict 内 unit 键抽到 unit 列、`value` 嵌套解一层、Python 风格字符串安全解析回 dict（`ast.literal_eval`）；
  - 条件表 / 本征表两条展平路径均接入归一化；
  - `write_csv` 去掉 `append=True`，改为全量覆盖写（消除 pipeline 重跑导致的重复行）。
- **`agent/json_to_csv.py`**：`flatten_paper` 委托 `flatten_ml.flatten_to_rows`，消除第二套不展平的 CSV 逻辑。
- 效果：CIP/AGG 等对象值稳定输出 `属性名_CIP` / `属性名_AGG` 独立行，无重复、无字符串形态；标量属性（如 `Ionic_Conductivity`）不受影响。
- 重新生成 CSV：`python agent/merge_results.py`（不重跑 LLM）。
