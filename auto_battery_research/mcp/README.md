# AutoBatteryResearch 领域专家 MCP Server

本次更新为现有 AutoBatteryResearch 增加独立的 MCP 适配层。外部 Agent 调用领域能力，不直接调用 Materials Project、RAG、PINN、PyBaMM 等内部科研工具。原有科研工作流无需改造。

## 对外工具

- `battery_consult(question, context?)`：轻量电池问题咨询，复用 TUI 自由问答的 `LLMClient`。不运行六阶段工作流，也不检索或核验证据。成功时只返回回答文本，不提供结构化输出或状态字段；失败通过 MCP 的 `isError` 标记。服务端不保存对话历史，客户端可把相关前文放入 `context`。
- `design_battery_experiment(goal, context?, constraints?, available_resources?)`：调用现有 `ABRAgent.run_stream` 生成完整实验方案。MCP 文本内容为完整 Markdown 报告；结构化输出包含 `report_markdown`、`report_path`、`status`（`complete | partial | error`）和 `error`。未完成或失败会明确标记，不把旧报告当作本次结果。

## 启动与接入

在仓库根目录安装 MCP 依赖并启动服务：

```powershell
pip install -e ".[mcp]"
python -m auto_battery_research.mcp.server --transport streamable-http --host 127.0.0.1 --port 8000
```

支持 Streamable HTTP（默认，端点 `http://127.0.0.1:8000/mcp`）和 stdio（启动参数改为 `--transport stdio`）。在支持 MCP 的客户端中按对应传输方式注册该服务，名称可设为 `AutoBatteryResearch`；连接后通过 `list_tools` 确认两个工具可见。

`127.0.0.1` 仅供同机访问。当前服务没有鉴权，不应直接暴露到公网；跨主机客户端应使用返回的 `report_markdown`，不要依赖服务器本地的 `report_path`。

## 验证

```powershell
python -m pytest auto_battery_research/tests/test_autobatteryresearch_service.py auto_battery_research/tests/test_autobatteryresearch_mcp_tools.py auto_battery_research/tests/test_autobatteryresearch_mcp_server.py auto_battery_research/tests/test_autobatteryresearch_experiment.py -q
```
