# LMLLM Changelog (2026-07-01)

## 概览

本次更新主要涉及: 标签体系重构、元数据绑定、断点续传、Chroma 增量入库、OOM 修复、Pipeline 流式处理。

---

## 新增文件

### Qwen 版本 Pipeline

| 文件 | 说明 |
|------|------|
| `miner/paragraph_metadata_pipeline_v5_qwen.py` | v5 qwen 版本, 嵌入模型从 bge-m3 切换为 qwen3-embedding:8b, Chroma 目录/collection 加 _qwen 后缀 |

### 工具脚本

| 文件 | 说明 |
|------|------|
| `miner/reindex_chroma.py` | 从已有 JSON 重新切分 + 重新入库 Chroma（调 chunk 参数不重跑 LLM） |
| `para_viewer.py` | 段落查看工具 |
| `rename_to_doi.py` | MD 文件批量按 DOI 重命名 |

### RAG 前端

| 文件 | 说明 |
|------|------|
| `chat_rag_v3_optimized_qwen.py` | Qwen 版本的 RAG 聊天界面 |

### Agent 流水线

| 文件 | 说明 |
|------|------|
| `agent/` | Phase 0/1/2/3 全套 Agent 流水线 |

---

## 修改文件

### 标签体系重构 — 6类合并

**`miner/paragraph_metadata_pipeline_v5.py`** (改):
- `PRIMARY_LABELS`: 7类(理化性质+结构表征分开) → 6类(合并为材料属性与表征)
- Prompt #2 + #3 合并: 理化性质完整内容 + 结构表征完整内容 → 材料属性与表征（不删减）
- Prompt #6 概述: 移除致谢/参考文献/作者信息，归入非正文
- 判定优先级: 所有理化性质/结构表征引用改为材料属性与表征
- 示例2: 理化性质 → 材料属性与表征
- `keyword_result`: 理化性质+结构表征 scores 合并为材料属性与表征
- split_paragraphs: 移除最后的后处理硬切逻辑(3000/7500字符切分)，只保留语义合并
- `_split_long_chunk` 阈值: 3000→7500
- `_split_long_records` overlap: 200→750

**`miner/paragraph_metadata_pipeline_v5_qwen.py`** (新建):
- 同步所有 v5.py 的标签体系修改
- 嵌入模型默认值: bge-m3 → qwen3-embedding:8b
- 默认输出路径加 _qwen 后缀，与 bge-m3 版本隔离
- `META_JSON_PATH` 改为 `miner/json/metadata/meta_merged.json`

### 元数据绑定

**`miner/paragraph_metadata_pipeline_v5.py`** + **`miner/paragraph_metadata_pipeline_v5_qwen.py`**:
- `load_meta_map()`: 索引从纯 DOI → 同时按 file_path 和 DOI 建立索引
- 元数据查询: 优先按文件路径匹配，其次 DOI（避免跨文件夹错配）
- 启动时自动回填: 检测已有 JSON 中缺 title/date/date 的段落，从 meta_map 按 source_file 回填

**`miner/meta_extraction/extract_meta.py`**:
- `--incremental` 参数: 只处理新增 .md 文件，按 file_path 去重追加
- 增量合并: 新结果与已有 JSON 合并写入，不覆盖

### 断点续传 + 流式处理

**`miner/paragraph_metadata_pipeline_v5_qwen.py`**:
- 新增 `collect_paper_groups`: 流式生成器，逐篇 yield 文献段落，不一次性加载全量进内存
- 新增 `_split_long_records`: 从 main() 中提取的独立函数，入库前过滤非正文+超长段双存
- 新增 `write_json_append`: 逐篇追加写入 JSON，原子 rename 防损坏
- 新增 `group_items_by_paper`: 按 (doi, component) 分组
- main() 重写: 逐篇 classify → split → checkpoint → gc.collect()
- Chroma 入库: 从 JSON 全量读取，增量去重（检查已存在 doc IDs）

### 死循环修复

**`miner/paragraph_metadata_pipeline_v5.py`** + **`miner/paragraph_metadata_pipeline_v5_qwen.py`**:
- `split_paragraphs`: 修复段落长度在 3000~3200 字（或 7500~7700 字）时 while 循环永不终止的死循环 bug
- `_split_long_records`: 修复同样的死循环 bug
- 修复 `del processed` 后 `print(len(processed))` 的 UnboundLocalError

### Merge 改进

**`preprocessing/merge_markdown.py`**:
- 单 md 文件处理: 之前 len(md_files)<=1 时跳过，改为处理单个文件的文件夹
- `run_merge`: md_count>=2 → md_count>=1
- 输出描述更新

---

## 删除文件

| 文件 | 原因 |
|------|------|
| `src/lmllm/rag/` (全部) | 旧版 RAG 架构，已被 agent/ 流水线替代 |
| `tests/` | 旧版测试 |

---

## 关键决策

1. **标签合并**: 理化性质+结构表征 → 材料属性与表征。这两个标签在电池文献中界限模糊（同一段落常同时描述电导率、XRD、SEM），合并后减少 LLM 歧义，提升分类精度
2. **元数据绑定策略**: 按 file_path 匹配而非 DOI。防止跨文件夹匹配错误，且不需要预先对齐 DOI 格式
3. **断点续传**: 逐篇流式 + 逐篇 checkpoint。OOM 或 SSH 断线不丢数据，重新 --incremental 继续
4. **Chroma 增量入库**: 检查已有 doc IDs，避免重复 embedding。调 chunk 参数只需 reindex_chroma.py，不需重跑 LLM
5. **标签前不硬切**: LLM 分类时看到完整语义段落，上下文不破碎。入库前再按 max_chunk 切分
6. **入库分块优化**: max_chunk=7500+overlap=750(10%)，保持检索精度和上下文完整度平衡

---

## 数据状态

| 项目 | 数值 |
|------|------|
| 单篇文献 | 463 篇（anode 232 + cathode 107 + electrolyte 124） |
| 段落(3000无硬切后) | 11,039 段 |
| 入库分块(2000+200) | 16,490 条（含子段） |
| 平均段落长度 | ~1,500 字 |
| 标签体系 | 6 类（电化学性能/材料属性与表征/材料制备/机理/模拟/概述/非正文） |
| 嵌入模型 | qwen3-embedding:8b (GPU) |
| 向量库 | Chroma → miner/chroma/paragraphs_q/ |
| 元数据覆盖 | 463 篇（100%，按 file_path 绑定） |
