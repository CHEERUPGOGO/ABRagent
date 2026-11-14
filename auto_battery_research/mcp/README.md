# AutoBatteryResearch 领域专家 MCP Server

本模块为 AutoBatteryResearch 提供独立的领域专家 MCP 适配层（`auto_battery_research.mcp`）。外部 Agent 通过高层领域工具调用科研能力，不直接暴露 Materials Project、RAG、SPM PINN 等内部科研工具；原有内部 14 工具 stdio 工作流 MCP Server（`abr-cli --mcp` / `auto_battery_research.tools.mcp_server`）仍保留。

## 对外工具

- `battery_consult(question, context?)`：轻量电池问题咨询，复用 TUI 自由问答的 `LLMClient`。不运行六阶段工作流，也不检索或核验证据。成功时只返回回答文本，不提供结构化输出或状态字段；失败通过 MCP 的 `isError` 标记。服务端不保存对话历史，客户端可把相关前文放入 `context`。
- `design_battery_experiment(goal, context?, constraints?, available_resources?)`：调用现有 `ABRAgent.run_stream` 生成完整实验方案。MCP 文本内容为完整 Markdown 报告；结构化输出包含 `report_markdown`、`report_path`、`status`（`complete | partial | error`）和 `error`。未完成或失败会明确标记，不把旧报告当作本次结果。

## 启动与接入

在仓库根目录安装 MCP 依赖并启动服务：

```powershell
pip install -e ".[mcp]"
python -m auto_battery_research.mcp.server --transport streamable-http --host 127.0.0.1 --port 8000
# 或使用控制台脚本入口:
auto-battery-research-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

支持 Streamable HTTP（默认，端点 `http://127.0.0.1:8000/mcp`）和 stdio（启动参数改为 `--transport stdio`）。在支持 MCP 的客户端中按对应传输方式注册该服务，名称可设为 `AutoBatteryResearch`；连接后通过 `list_tools` 确认 `battery_consult` 与 `design_battery_experiment` 两个工具可见。

`127.0.0.1` 仅供同机访问。当前服务没有鉴权，不应直接暴露到公网；跨主机客户端应使用返回的 `report_markdown`，不要依赖服务器本地的 `report_path`。

## 验证

```powershell
# 验证领域专家 MCP CLI 参数与入口加载
python -m auto_battery_research.mcp.server --help

# 运行 14 工具内部工作流 stdio MCP 协议层单元测试 (auto_battery_research/tools/mcp_server.py)
python -m pytest auto_battery_research/tests/test_mcp_server.py -q
```

> 注：`auto_battery_research/mcp/` 目前尚无独立的离线单元测试文件，可通过支持 MCP 的客户端连接后调用 `list_tools` 对 `battery_consult` / `design_battery_experiment` 进行联调验证。
