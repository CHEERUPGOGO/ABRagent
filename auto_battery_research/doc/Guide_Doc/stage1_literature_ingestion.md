# Stage 1: 文献解析与组件分类指引

## 任务目标
1. 提取原始 PDF 论文中的 DOI 并按规范重命名。
2. 调用 MinerU 转换管道提取结构化 Markdown 与表格。
3. 合并主文献正文与 SI 补充材料（`papers/merged/`）。
4. 运行双语学术文本清洗与体系/组件分类（`database/type/**/{cathode,anode,electrolyte}`）。

## 验收门禁 (IngestionChecker)
- `database/type/` 或 `papers/merged/` 必须存在有效的 `.md` 文献。
- 所有 `.md` 文献文件大小不得小于 50 字节（排除空文件与损坏文件）。
- 涵盖正极、负极或电解质等至少一种分类产物。
