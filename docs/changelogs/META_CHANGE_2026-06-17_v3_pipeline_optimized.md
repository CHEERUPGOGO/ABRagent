# META_CHANGE 2026-06-17 v3 pipeline + 前端优化

## 更新时间
2026-06-17

## 改动范围
`miner/cleaning/cleaner_v2_standalone.py`
`miner/paragraph_metadata_pipeline_v3.py`
`miner/classification/battery_type_agent.py`
`miner/classification/run_battery_type.py`
`miner/classification/run_component_type.py`
`chat_rag_v3_optimized.py`
`.gitignore`

## 变动原因
v3 优化版向量库在实体问答中暴露出三个核心问题，需要从后端（pipeline）和前端（检索+回答）同时修复。

---

## 详细变更

### 1. cleaner_v2_standalone.py — 清洗逻辑修复

| 变更 | 说明 |
|------|------|
| **删除 "supporting information" 全局关键词** | 之前加到 NOISE_KEYWORDS 后，大量包含 "Figure S17 (Supporting Information)" 的正常正文被误删，导致 1.4 mV 电压衰减数据丢失 |
| **改 "supplementary information" 为限长检测** | 仅过滤 <100 字符的纯声明行（如 "Supporting Information is available..."），不误杀正文 |
| **作者行检测：去 . 再判** | V.（中间名缩写）不再被误判为句号，Nathaniel V. Stanley 等作者行正确识别 |
| **增加出版元数据检测** | Received: ... Accepted: ... Published online: ... 行被过滤 |
| **缩略名作者行检测** | J. Meng, W. Hu, L. Xu... 模式被过滤 |
| **补回 NOISE_KEYWORDS 消费** | 之前定义了但未在 _is_noise_paragraph 中使用 |

### 2. paragraph_metadata_pipeline_v3.py — 标签标注修复

| 变更 | 说明 |
|------|------|
| **"概述"边界规则** | 在 prompt 中新增：如果段落以综述性语言开头但包含具体电化学/理化性能数值，必须归入"电化学性能"或"理化性质"而非"概述" |
| **判定优先级 +1 条** | 新增第 4 条："如果段落以综述性语言开头但后半段有具体的性能/性质数值 → 归入对应的电化学性能或理化性质" |
| **排除固态电池** | iter_markdown_files 中跳过 Solid_State 目录，只处理 Lithium_Ion_Metal_Battery |

### 3. chat_rag_v3_optimized.py — 检索+回答优化

| 变更 | 说明 |
|------|------|
| **并行全局搜索** | 在组件过滤搜索（25段）之外，始终并行搜 10 段不限组件/标签的全局结果 |
| **全局结果加权** | 多渠道来源的段落得分 +0.15 |
| **数值密集段落加权** | 含 >=2 个"数值+单位"的段落在排序中获得额外加分（每命中+0.04，上限 5 次） |
| **Prompt 新增指令** | "回答时优先提炼并呈现所有可量化的具体数值"；"如果问题未指定组件，先识别最相关材料体系" |

### 4. .gitignore — 排除大数据文件

新增排除：database/、papers/、mine/、_bench_*

---

## 删除的文件/目录

| 文件/目录 | 原因 |
|----------|------|
| chat_rag_v3.py | 被 chat_rag_v3_optimized.py 取代 |
| chat_rag_v4.py | 功能合并进 v3 优化版 |
| chat_rag_run1.py | v2 前端，已不使用 |
| high_energy_rag/ | 旧版 RAG 模块，已完全废弃，无外部依赖 |
| miner/paragraph_metadata_pipeline_v2.py | v2 pipeline，已被 v3 取代 |
| miner/chroma/paragraphs_v2* | 旧 v2 向量库 |
| miner/chroma/paragraphs/ | 最早期向量库 |

---

## 验证结果

15 问全部通过：
- LiCoO2 比容量：140 mAh/g（之前漏检）
- Mg/Al 共掺杂电压衰减：1.4 mV/cycle（之前缺失）
- 石墨平衡电位：100 mV
- PZL 电化学窗口：5.37 V

---

## 后续 TODO

1. extraction_pipeline_v5 + unified_agent 试跑 agent 数据挖掘
2. PINN 数据接入
3. 外部晶体结构数据库接入
