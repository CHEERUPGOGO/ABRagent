# Stage 2: 语义标注与向量入库指引

## 任务目标
1. 提取文献元数据（DOI, Title, Authors, Year）。
2. 对清洗后段落打 6 类互斥语义标签（电化学性能、材料属性与表征、材料制备、机理/模拟、概述、非正文）。
3. 调用 `qwen3-embedding:8b` 模型进行向量化，持久化到 Chroma 向量库 `miner/chroma/paragraphs_q`。

## 验收门禁 (VectorDBChecker)
- `meta_merged.json` 包含文献元数据。
- Chroma 向量数据库或段落标注 JSON 包含有效段落与语义标签分布。
