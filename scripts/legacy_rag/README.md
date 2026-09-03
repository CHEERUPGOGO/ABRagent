# 历史演示与工具脚本归档 (Legacy RAG & Tool Scripts)

本目录归档了早期版本开发中用于单点功能测试与独立界面展示的脚本，以便历史追溯与查阅。核心生产功能均已收敛至 `auto_battery_research` 架构内。

## 归档文件列表

1. **`chat_rag_v3_optimized.py`**
   - **用途**：早期针对 RAG 检索策略优化开发的独立 Gradio 对话前端。
   - **特点**：包含针对文献段落的标签加权与兜底检索逻辑。

2. **`chat_rag_v3_optimized_qwen.py`**
   - **用途**：适配 Qwen 模型作为后端的独立 RAG 交互测试界面。

3. **`chat_rag_v5_demo.py`**
   - **用途**：轻量演示版 RAG 界面（默认端口 7871），用于快速向用户展示文献检索回答效果。

4. **`reader.py`**
   - **用途**：早期基于 `llm_miner` 架构的期刊文献解析器草稿。
   - **状态**：已弃用（已被 `preprocessing/` 与 MinerU 解析流水线全面替代）。
