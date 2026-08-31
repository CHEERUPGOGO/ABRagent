# CHANGELOG 2026-08-29 — 材料数据挖掘管线升级（归一化 + Cell 组装）

## 本次改动概要

将 agent 数据挖掘管线从"LLM 直接抽取 + 自由文本落库"升级为
**三层归一化 + 电芯（cell）组装**：材料/属性/条件各自归一化，
电化学属性挂到 cell 实体，为知识图谱和 ML 提供结构化的数据载体。

---

## 新增文件（agent/）

| 文件 | 功能 |
|---|---|
| `material_norm.py` | 材料归一化层（核心）：别名/化学式元素计数/配方 ID/电极改性剥离 → 变体 id + base_id + mods；条件组件归一化（electrolyte→配方ID、counter_electrode→材料ID） |
| `label_norm.py` | 属性名归一化层：format1 标准标签校验（精确/变体/未命中收集），不猜测不自动改写 |
| `cell_assembler.py` | 电芯组装层：材料变体 × 条件组件 × scenario → cell 实体，电化学属性挂 cell，本征属性留材料 |
| `entity_register.py` | LLM 辅助注册判定：消费未命中队列，判定 merge/create/discard（只建议，人工确认后改词表） |
| `NER_PLAN.md` | 重写为实施记录（原 NER 方案已废弃），含流程/模块细节/参考文献/运行方式 |

## 修改文件

| 文件 | 改动 |
|---|---|
| `agent/pipeline_tok2000.py` | 材料归一化插桩（phase0 后）+ 未命中收集聚合 + 条件组件归一化 + label 校验 + cell 组装 |
| `agent/phase0_discovery.py` | prompt 扩展：材料输出可选 base_material + mods（分层结构化） |
| `agent/phase3_merge_agent.py` | 电解液配方归一化升级（结构拆解+溶剂排序）+ 修复 cond_map 双索引 bug |
| `agent/flatten_ml.py` | 属性级条件参数保留（cycle_number/rate 等 8 列）+ 倍率 list 展开（每个 rate 点一行） |
| `miner/{cathode,anode,electrolyte}_database/*_formatter.py` | 材料标签集扩充（热物性/力学/结构） |
| `miner/format1/` 12 个标签定义文件 | 新增 8 个标准标签（electrolyte: Thermal_Conductivity/Thermal_Diffusivity/Specific_Surface_Area；cathode: Adhesion_Strength/Mesoscopic_Porosity；anode: Adhesion_Strength/Mesoscopic_Porosity 等） |
| `src/lmllm/RAG/data/candidates.json` | 词表 15 → 87 实体（正极/负极/电解液/添加剂 + 配方实体 + LLM 注册） |
| `src/lmllm/RAG/data/alias_map.json` | 别名归一表扩充（中英 + 化学式 + 配方名） |
| `.gitignore` | 排除第三方文献代码 ChemSSP-main/ |

## 核心能力

1. **材料归一化**：配方 ID（`1M LiPF6 in EC/DEC` → `LIPF6_DEC-EC`）、电极改性剥离
   （`Al2O3-coated NCM811` → base=NCM811 + coating=[Al2O3] → `NCM811_Al2O3-coated`）、
   化学式元素计数（`LiNi0.8Mn0.1Co0.1O2` ≡ `LiNi0.8Co0.1Mn0.1O2`）、中英别名
2. **属性名校验**：format1 标准标签（cathode 65/anode 52/electrolyte 60），变体归一，未命中进注册队列
3. **条件组件归一化**：electrolyte_id（配方 ID）+ counter_electrode_id（材料 ID），cell 配置规范化
4. **Cell 组装**：`cell_id` 基于组件组合哈希；电化学属性挂 cell，本征属性挂材料
5. **CSV 完整性**：容量保持率带 cycle_number、倍率性能每 rate 点一行；**真实性原则**（有则提取、无则留空、禁编造）
6. **注册闭环**：未命中队列 → LLM 判定（merge/create/discard）→ 人工确认 → 词表扩充 → 增量自动生效

## 验证结果

- 词表 15 → 87（LLM 注册 66 候选：merge 29/create 33/discard 4）
- 批量 10 篇：材料未命中 0 → 62 命中（94%）；属性未命中 133 → 3
- 端到端三篇（cathode/anode/electrolyte 改性文献）：LLM 分层输出语义准确、cell 组装 8/4/16 个、归属链全部一致
- 模块自测：material_norm / label_norm / cell_assembler 全部通过

## 参考的代码与文献

- ChemSSP-main（Zhang et al., J. Chem. Inf. Model. 2025, 65, 4334−4345）：化学分词/配方解析思路（CDEv2）、材料嵌入（Mat2Vec）；few-shot 模型 NER 未实施，仅保留参考
- LLMB-master（Lee et al., ACS Cent. Sci. 2026, 12, 484−496）：以 cell 为单位挖掘的设计启发
- extractor.py 参考 NERRE（Dagdelen et al., Nat. Commun. 2024）：项目原有

## 运行方式

```bash
# 批量跑文献
python agent/pipeline_tok2000.py -i database/type/Lithium_Ion_Metal_Battery -o results/tok2000normtest --max-files 10
# 注册判定
python agent/entity_register.py --materials results/.../_unmatched_all.json --labels results/.../_unmatched_labels_all.json --out results/...
# 模块自测
python agent/material_norm.py && python agent/label_norm.py && python agent/cell_assembler.py
```
