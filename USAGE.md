# AutoBatteryResearch & LMLLM 使用指南

**化学电池全生命周期科研智能体 与 文献自动化数据挖掘系统**

---

## 快速开始

### 方式一：启动 Rich 交互式终端控制台 (TUI)
```bash
python auto_battery_research_cli.py --tui
# 或直接运行：
python auto_battery_research_cli.py
```

### 方式二：启动 Gradio 交互式 Web 仪表盘 (Web Dashboard)
```bash
# 启动 Web 大屏 (浏览器打开 http://127.0.0.1:7865)
python auto_battery_research_cli.py --web
```

### 方式三：AutoBatteryResearch Agent 6 阶段全自动科研循环
```bash
# 1. 一键运行从文献解析、向量库、材料挖掘、RAG设计到研报生成的完整闭环
python auto_battery_research_cli.py --run --goal "设计400Wh/kg高比能液态锂金属电池方案"

# 2. 查看当前工作流与各阶段状态
python auto_battery_research_cli.py --status

# 3. 激活 Stage 5 执行 PINN 物理仿真
python auto_battery_research_cli.py --run --with-pinn

# 4. 查看各阶段历史研发日志
python auto_battery_research_cli.py --journal
```

---

## 核心文件与指令一览

| 文件 / 指令 | 作用 | 输入 | 输出 | 运行方式 |
|---|---|---|---|---|
| `auto_battery_research_cli.py --tui` | **Rich 终端多面板交互控制台** | 交互式命令 | 状态矩阵、诊断面板、实时研报 | `python auto_battery_research_cli.py --tui` |
| `auto_battery_research_cli.py --web` | **Gradio 交互式 Web 仪表盘** | Web 浏览器操作 | 工作流大屏、RAG方案、放电曲线 | `python auto_battery_research_cli.py --web` |
| `auto_battery_research_cli.py --run` | **全自动自主科研循环** | 研究目标 | 结构化电芯、RAG方案、综合研报 | `python auto_battery_research_cli.py --run` |
| `abr_cli.py` | 智能体别名快捷入口 | CLI 参数 | 同上 | `python abr_cli.py --status` |
| `pipeline_incremental.py` | 全流程增量协调器 | `papers/pdf/*.pdf` | 各步骤各自输出 | `python pipeline_incremental.py` |
| `miner/paragraph_metadata_pipeline_v5_qwen.py` | 段落标签标注 + 向量库入库 | `database/type/` | Chroma + `paragraph_metadata_q.json` | `python miner/paragraph_metadata_pipeline_v5_qwen.py --incremental` |
| `agent/pipeline_tok2000.py` | 材料挖掘与电芯组装 (Tok2000) | `database/type/` | 结构化电芯 JSON / ML CSV | `python agent/pipeline_tok2000.py` |
