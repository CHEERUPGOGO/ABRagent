# LMLLM 数据挖掘管线实施记录 — 归一化 + Cell 组装

> 创建：2026-08-27（原 NER 落地方案）
> 重写：2026-08-29（按实际实施内容，替代原 NER 方案）
> 状态：管线已跑通（单篇/批量/注册闭环验证通过）

---

## 1. 与原方案（ChemSSP NER）的关系

原方案（本文档旧版）计划引入"词典 NER（路径 A）/ ChemSSP 真 few-shot 模型（路径 B）"。
实际实施后方向变化：

- **不再引入独立 NER 模型层**。实体识别由 **LLM 结构化输出（base_material + mods）+ 规则归一化** 承担，
  效果覆盖了原方案"预筛 + 归一化"的意图，且零训练成本。
- **ChemSSP-main 代码当前不使用**（已加入 .gitignore，仅作方法学参考；原路径 B 的模型 NER 未实施，
  若未来需要 GPU 训练才回来取 `fs_ner_model.py` 的 ProtoSpan/MultiSpan 结构）。
- 原方案的"插桩 1/2/3"思想（预筛、段落过滤、召回校验）演化为：
  插桩 1 → material_norm（材料归一化）；插桩 2/3 → label_norm（属性名校验 + 未命中收集）。

---

## 2. 数据挖掘全流程

```
输入: database/type/Lithium_Ion_Metal_Battery/{cathode|anode|electrolyte}/*.md
  │
  ▼ [pipeline_tok2000.py]
  ├─ clean_rag_tok2000     段落切分（CMAX=2000 tokens）+ 表格行拆成 [TABLE ROW] 段
  ├─ phase0_discovery      LLM 一次调用：材料 + 初始条件
  │    材料输出 name/formula/role + base_material + mods（分层结构化，可省略）
  ├─ material_norm         材料归一化（详见 §3）
  ├─ normalize_condition_components  条件组件归一化：electrolyte→配方ID / counter_electrode→材料ID
  ├─ phase12_extract       include（标签匹配）→ extract（属性提取）逐段两连调
  ├─ phase3_merge_agent    canonical hash 条件去重 + LLM 孤儿匹配 + 按变体归组
  ├─ label_norm            属性名校验（format1 标准标签，变体归一，未命中收集）
  ├─ cell_assembler        cell 组装（电化学属性挂 cell，本征属性留材料）
  └─ flatten_ml            CSV 展平（条件参数保留 + list 展开）
  │
  ▼ 输出（results/{run}/tok2000/）
  ├─ {comp}/{stem}_rag.json          单篇全量（含 materials/cells/unmatched）
  ├─ {comp}/{stem}_cond.csv          条件属性表（ML-ready）
  ├─ _all_rag.json / _all_cond.csv   聚合
  ├─ _unmatched_all.json             材料未命中队列（注册用）
  └─ _unmatched_labels_all.json      属性未命中队列（注册用）

注册闭环: entity_register.py（LLM 判定 merge/create/discard）→ 人工确认 → 更新词表 → 增量自动生效
```

---

## 3. 各模块细节

### 3.1 agent/material_norm.py — 材料归一化层（核心）

匹配策略（按优先级）：
1. **配方串**（电解液）：`parse_formulation` 拆盐/溶剂/添加剂/浓度 → 唯一配方 ID
   `1M LiPF6 in EC/DEC` → `LIPF6_DEC-EC`；`1M LiPF6 in EMC + 2% VC` → `LIPF6_EMC_VC`；
   `1.2M ... + 0.1wt% MPS` → `1.2M_LIPF6_DMC-EC_MPS`（非常规浓度前缀、字母序去重）
2. **LLM base_hint**：phase0 输出的 `base_material`/`mods`（语义判断优先，method 标记 `+llm-base`）
3. **电极改性剥离**（`parse_modifications`）：coating（coated/@/wrapped/encapsulated）、
   dopants（doped/substituted/co-doped）、composite、morphology（nano/porous/hollow/single-crystal）、
   treatment（prelithiated/activated）、based 标记
   `Al2O3-coated NCM811` → base=NCM811 + mods{coating:[Al2O3]} → 变体 id `NCM811_Al2O3-coated`
4. **别名精确**（小写 + 去空格/连字符/斜杠，含中英别名）
5. **化学式元素计数**（`LiNi0.8Mn0.1Co0.1O2` ≡ `LiNi0.8Co0.1Mn0.1O2`，:g 规范化）
6. **别名包含**（长别名优先，≥3 字符防误命中）

输出 `NormResult`：`canonical_id`（变体 id）+ `base_id`（基础材料）+ `mods`（改性维度）。
未命中收集为 `unmatched`（注册队列种子，保留 LLM 分层信息）。

`normalize_condition_components`：条件里的 `electrolyte` → `electrolyte_id`（配方 ID）、
`counter_electrode` → `counter_electrode_id`（材料 ID）。保留原文，新增 `*_id` 字段。

**参考**：分词/配方解析思路参考 ChemSSP-main 内嵌的 CDEv2 化学分词
（chemdataextractor2，Cambridge Molecular Engineering）；归一化表结构为项目自有。

### 3.2 agent/label_norm.py — 属性名归一化层

- 标准标签集：从 format1 formatter 提取（cathode 65 / anode 52 / electrolyte 60）
- 三级校验：精确 → 规范化变体（`discharge specific capacity initial` → `Discharge_Specific_Capacity_Initial`）→ 未命中收集
- **只做确定性归一，不猜测**（模糊的进注册队列，由人/LLM 判定）
- 批量 10 篇实测：属性未命中 133 → 3（扩充热物性标签后）

### 3.3 agent/cell_assembler.py — 电芯组装层

- `assemble_cells(materials, doi)`：材料变体 + 条件组件 + scenario → cell 实体
- `cell_id` 基于组件组合哈希（working + counter_electrode + electrolyte + scenario + 电压/电流窗口）
- 电化学属性挂 cell，本征属性留材料（图谱拓扑：材料变体 ─belongs_to─> cell ─measured_in─> 电化学属性）

### 3.4 agent/phase3_merge_agent.py — 条件去重 + 归组

- 3a：`CONDITION_HASH_FIELDS` canonical hash 条件去重（电解液配方归一化升级：结构拆解 + 溶剂排序）
- 3b：LLM 孤儿属性匹配
- 3c：按 material_id（变体 id）归组 + 数值相近（<2%）去重
- 修复：`cond_map` 双索引（condition_id 与 canonical_id——属性挂的是 canonical_id）

### 3.5 agent/flatten_ml.py — CSV 展平

- 属性级条件参数保留（`PROP_COND_FIELD_MAP`：cycle_number/cycle_range/scan_rate/prop_c_rate 等 8 列）
- 容量保持率必须带圈数（`{value:89, unit:%, cycle_number:290}` → CSV 有 cycle_number 列）
- list 形态展开（Rate_Capability 每个倍率点一行：0.2C/5C 各一行）
- **真实性原则**：条件参数"有则提取、无则留空、禁编造"（文献没给圈数的实测占 32-81%）

### 3.6 agent/entity_register.py — LLM 辅助注册判定

- 消费 `_unmatched_all.json` + `_unmatched_labels_all.json`，先自动过滤（词表扩充后已能命中的解决）
- DeepSeek 判定 merge/create/discard + 理由 + 置信度 → `_register_suggestions.json`
- 只建议不改词表（归并/新建必须人工确认）
- 实测：66 材料候选 → merge 29 / create 33 / discard 4（配方串按"盐_溶剂_添加剂"唯一 ID 重做 merge）

### 3.7 agent/phase0_discovery.py — LLM 分层输出

- materials 输出可选 `base_material` + `mods`（coating/dopants/composite/morphology/treatment）
- 配方 ID 化的基础：电解液材料按配方串整体作为一个材料

---

## 4. 参考的代码与文献（GitHub 对照）

| 模块/设计 | 参考来源 | 使用方式 |
|---|---|---|
| 化学分词/配方解析思路 | ChemSSP-main 内嵌 `CDEv2_tokenize.py`（源自 chemdataextractor2, Cambridge Molecular Engineering）| 仅思路，自研轻量正则实现 |
| 材料嵌入思想 | ChemSSP-main 内嵌 `Mat2Vec.py`（mat2vec）| 仅思想，未使用（ML 兜底候选） |
| few-shot 模型 NER（路径 B） | ChemSSP-main `fs_ner_model.py`/`fs_ner_train.py`（Zhang et al., *Rapid Adaptation of Chemical NER Using Few-Shot Learning and LLM Distillation*, J. Chem. Inf. Model. 2025, 65, 4334−4345）| 未实施，保留参考 |
| 以 cell 为单位的挖掘 | LLMB-master（Lee et al., *LLMB: AI Agent for Lithium Metal Battery Research*, ACS Cent. Sci. 2026, 12, 484−496）| cell_assembler 设计启发 |
| ICL 关系抽取 | `src/lmllm/RAG/extractor.py` 参考 NERRE（Dagdelen et al., Nat. Commun. 2024）| 项目原有，未改动 |
| 实体归一化表结构 | 项目自有（candidates.json + alias_map.json，relation_engine 同源）| 数据单一来源 |

**ChemSSP-main 的用途**：方法学参考（few-shot 少样本、LLM 蒸馏、化学分词、材料嵌入），
代码未 import、未复用、未上传 GitHub（已 .gitignore）。路径 B 激活条件：GPU + 锂电池 BIO 数据 + 确认 few-shot 收益。

---

## 5. 终端运行方式

```bash
# ── 环境 ──
export DEEPSEEK_API_KEY="your-key"
#（可选）DEEPSEEK_API_BASE、DEEPSEEK_MODEL

# ── 单篇运行（输出到独立目录，目录名备注文献）──
python agent/pipeline_tok2000.py \
    -i database/type/Lithium_Ion_Metal_Battery/cathode/10.1016_xxx.md \
    -o results/tok2000normtest_10.1016_xxx --max-files 1

# ── 批量运行（输出到指定目录）──
python agent/pipeline_tok2000.py \
    -i database/type/Lithium_Ion_Metal_Battery \
    -o results/tok2000normtest --max-files 10

# 参数：-i 输入文件/目录；-o 输出根（实际写入 {od}/tok2000/{comp}/）；
#       -c 组件过滤（all/cathode/anode/electrolyte）；--max-files N；--extract-model/--merge-model

# ── 注册判定（消费未命中队列）──
python agent/entity_register.py \
    --materials results/tok2000normtest/tok2000/_unmatched_all.json \
    --labels results/tok2000normtest/tok2000/_unmatched_labels_all.json \
    --out results/tok2000normtest/tok2000

# ── 模块自测（不依赖 API）──
python agent/material_norm.py     # 词表归一化 + 改性分层
python agent/label_norm.py        # 属性名校验
python agent/cell_assembler.py    # cell 组装
```

**增量机制**：`{od}/tok2000/{comp}/{stem}_rag.json` 存在则跳过该篇（断点续跑）。
**注册闭环**：批量跑 → `_unmatched_*.json` → entity_register 判定 → 人工确认 → 更新
`src/lmllm/RAG/data/candidates.json` + `alias_map.json`（version+1）→ 存量重映射/增量自动生效。

---

## 6. 词表体系（src/lmllm/RAG/data/）

- `candidates.json`：规范实体（当前 87 个：正极/负极/电解液/添加剂 + 配方实体），version 0.4-llm-registered
- `alias_map.json`：别名归一表（中英 + 化学式 + 缩写 + 配方名），version 0.3-llm-registered
- 与 `src/lmllm/RAG/relation_engine.py` 共享同一份数据（数据单一来源，各自加载）
- 扩充路径：LLM 注册（entity_register）+ 手工（seed-manual，置信度分级）
