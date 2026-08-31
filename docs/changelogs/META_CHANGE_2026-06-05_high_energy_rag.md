# 修改说明 — 整体变更记录

> 未推送至 GitHub 的本地修改记录
> 日期：2026-06-05

---

## 一、high_energy_rag/ 模块（新增）

在 LMLLM 项目根目录下独立创建，不修改 miner/ 下任何代码。

| 文件 | 说明 |
|------|------|
| `__init__.py` | 模块定义 |
| `labels.py` | 标签体系：6一级 + 29二级 + 4技术路线 + 关键词兜底 |
| `cleaner.py` | 段落级清洗：切References、过滤噪音、保留表格/公式/图注 |
| `coarse_pipeline.py` | 主pipeline：清洗→按#标题分割→内容类型检测→LaTeX归一化→超长切分→DeepSeek标标签→Chroma入库 |
| `rag_simple.py` | 方案一：直接 Chroma 检索 + DeepSeek Pro |
| `rag_hybrid.py` | 方案二：Query改写 + 向量/BM25混合 + 重排序 + DeepSeek Pro（默认） |
| `rag_structured.py` | 方案三：问题分类路由 + 结构化JSON增强 |
| `main.py` | CLI 入口：交互模式 + --content-type 过滤 |
| `chat_ui.py` | Gradio 浏览器前端 |
| `analyze_coverage.py` | 二维覆盖度统计 |
| `synthesize_qa.py` | 从 v4/v5 JSON 合成 SFT 问答对 |
| `README.md` | 使用说明 |

**删除**：pipeline.py（被 coarse_pipeline.py 替代）

---

## 二、miner/ 下已有未提交修改

以下文件在本次会话前已存在修改（git status 检出），本次未触碰：

| 文件 | 状态 |
|------|------|
| `miner/extraction_core/extraction_pipeline_v5.py` | 已修改 |
| `miner/extraction_core/unified_agent.py` | 已修改 |
| `miner/json/meta_merged.json` | 已修改 |
| `miner/run_test_agents.py` | 已删除 |
| `miner/paragraph_metadata_pipeline_v2.py` | 新增（此前已有） |

---

## 三、核心功能

| 功能 | 实现 |
|------|------|
| 内容类型检测 | text / table / formula / figure_caption |
| LaTeX 归一化 | Li2MnO3（纯字符，无标记噪音） |
| 章节路径 | section_path: "2.2 Electrochemical Performance" |
| 章节分割 | --split-level h1/h2 |
| 超长保护 | --max-chars 8000 自动拆分子节 |
| 引用追溯 | source_ref: figure/table/equation |
| 向量库 | collection: high_energy_battery_coarse |
