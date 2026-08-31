# LMLLM Changelog (2026-06-24)

## 概览

本次更新主要涉及: 文献综述过滤增强、段落切分优化、RAG检索策略改进、Unstructured集成方案。

---

## 新增文件

### RAG 前端

| 文件 | 说明 |
|------|------|
| `chat_rag_v5_demo.py` | RAG 演示版前端 (Gradio, 端口7871, 干净界面, 无版本号) |

### Pipeline 代码

| 文件 | 说明 |
|------|------|
| `pipeline_incremental.py` | 增量处理工具 |
| `miner/paragraph_metadata_pipeline_v4.py` | v4 段落切分流水线 |
| `miner/paragraph_metadata_pipeline_v5.py` | v5 段落切分流水线 (参考文献隔离+二级切分+max_chunk上限) |
| `miner/extraction_core/extraction_pipeline_v6.py` | v6 并行数据提取流水线 |
| `miner/extraction_core/extraction_pipeline_v7.py` | v7 提取流水线 |
| `miner/classification/run_component_type_100.py` | 组件分类适配器 |

### Unstructured 集成 (新架构)

| 文件 | 说明 |
|------|------|
| `src/lmllm/rag/__init__.py` | 包入口 |
| `src/lmllm/rag/md_parser.py` | Unstructured 解析器 (partition_md + chunk_by_title + HTML表格后处理) |
| `src/lmllm/rag/pipeline.py` | RAG 入库 pipeline (解析→LLM标签→Chroma) |
| `src/lmllm/rag/extraction_agent.py` | 材料数据提取 Agent (Phase 0→1→2, 表格专用提取) |

### 工具脚本

| 文件 | 说明 |
|------|------|
| `_json2csv.py` | extraction JSON 转 CSV 工具 |
| `_json_property_stats.py` | 属性频次统计脚本 |
| `_view_extraction.py` | extraction JSON 查看工具 |
| `_change_log_20260618.md` | 旧版变更日志 |
| `USAGE.md` | 使用说明 |

### 测试

| 文件 | 说明 |
|------|------|
| `tests/test_unstructured_rag.py` | Unstructured RAG Pipeline 测试 (4个测试用例) |

### 图片素材 (1/ 目录)

| 文件 | 说明 |
|------|------|
| `1/材料性质频次统计.png` | 材料性质频次柱状图 |
| `1/电池性能频次统计.png` | 电池性能 Top10 频次柱状图 |
| `1/Elemental_Composition_scatter.png` | Elemental Composition (anode) 柱状图 |
| `1/Initial_Coulombic_Efficiency_scatter.png` | Initial Coulombic Efficiency 散点图 |
| `1/Initial_Coulombic_Efficiency_fitted.png` | Initial Coulombic Efficiency 拟合图 |
| `1/高频词材料性质标签汇总柱状图.py` | 柱状图绘制脚本 |
| `1/_rebuild_chroma.py` | Chroma 批量重建脚本 |

### CSV 数据 (`miner/json/csv/`)

| 文件 | 说明 |
|------|------|
| `all_extracted.csv` | 77篇 extraction JSON 合并 CSV (2834行) |
| `property_stats.csv` | 属性频次统计表 (104种属性) |
| `锂电池属性翻译-带性能性质分类_豆包AI生成.xlsx` | 属性翻译与分类表 |

---

## 修改文件

### 电池类型分类 — 综述过滤增强

**`miner/classification/battery_type_agent.py`**:
- `REVIEW_KEYWORDS` 扩展: 新增 `opinion`, `comment`, `viewpoint`, `critical review`, `recent progress`, `current status` 等
- Prompt 修改: 明确要求非原创研究论文 (perspective/opinion/comment) 判为 `is_review=true`
- 明确排除非锂电池: 铝离子、钠离子、钾离子、锌离子、镁离子电池等 `is_li_battery=false`

**`miner/classification/run_battery_type.py`**:
- 综述过滤: 从只查标题 → 查标题+内容前3000字
- 组件过滤: 只输出 `Li-ion/Li-metal` 类型, 跳过 Li-S / Li-air / Solid-state
- 修复 bug: `is_review=True` 时正确 `continue` 跳过复制

### 段落切分

**`miner/paragraph_metadata_pipeline_v5.py`** (新建):
- `split_paragraphs` 四层改进: 空行切分→合并规则→表格隔离→参考文献检测
- 新增 `_split_long_chunk`: 超长 chunk (>3000字) 按参考文献序号自动断开
- 新增 `_REF_PATTERN`: 检测 `数字.大写字母` 格式的参考文献段
- 新增 `max_chunk` 硬上限: 3000字 + overlap 200字重叠窗口
- 参考文献段不向前合并到正文

### RAG 检索策略

**`chat_rag_v3_optimized.py`**:
- Top-K 从 5 提升到 10
- 检索置信度: `similarity_search_with_score` → cosine distance 归一化 [0,1]
- 答案置信度: Prompt 强制 LLM 输出 `[置信度: 高/中/低]`
- 加权公式: `cosine_conf×2 + 标签分 + 兜底分 + 数值密度分`
- 同文献压制: 同篇文献超过 2 段, 后续段落 score×0.8
- 修复 `scanned_labels` 和 `extra_ids` 的 tuple 解包 bug

### 数据提取

**`miner/extraction_core/extraction_pipeline_v5.py`**: +5 行

**`miner/extraction_core/unified_agent.py`**: +18 行

**`miner/format1/anode_structured_data.py`**: +126 行 (格式扩展)

**`miner/format1/cathode_structured_data.py`**: +140 行 (格式扩展)

**`miner/format1/electrolyte_structured_data.py`**: +104 行 (格式扩展)

**`miner/format1/electrolyte_information.py`**: +4 行

---

## 删除文件

| 文件 | 原因 |
|------|------|
| `high_energy_rag/` (全部) | 旧版 RAG pipeline, 已被新架构替代 |
| `miner/paragraph_metadata_pipeline_v2.py` | 旧版 v2 流水线 |
| `test_results/` (全部) | 旧版测试结果和 PPT 生成脚本 |

---

## 关键决策

1. **综述过滤策略**: 从仅标题关键词 → 标题+内容+LLM 联合判断, 200篇中过滤出95篇综述
2. **段落切分**: 保留语义段落优先原则, 设为3000字硬上限+200字重叠, 93%段落不受影响
3. **RAG 检索**: 三轮递进 (组件过滤25+补搜5+兜底10), 公式重排+同文献压制
4. **向量库**: 从 Qdrant → Chroma (单机研究规模, 2611条段落)
5. **Embedding**: bge-m3 (1024维, Ollama本地部署)
6. **数据范围**: 仅保留 Li-ion/Li-metal 类型, 删除中文摘要/段落, 清除铝离子电池等非锂电池
7. **元数据**: 从 markdown 文件重新提取 title/authors/doi 并绑定到段落
8. **Unstructured 方案**: 新建 `src/lmllm/rag/` 目录, 实现替代 cleaner_v2 + split_paragraphs 的模块化版本

---

## 数据状态

| 项目 | 数值 |
|------|------|
| 原始文献 | 200篇 |
| 综述过滤后 | 105篇研究论文 |
| Li-ion/Li-metal | 80篇 (anode 37+ cathode 21+ electrolyte 22) |
| 段落切分 | 2,591段 |
| 平均段落长度 | 1,367字 |
| >3000字段 | 6.8% |
| v6 extraction JSON | 77篇 / 2,834条属性记录 |
| Chroma 向量 | 2,591条 (bge-m3 / 1024维) |
