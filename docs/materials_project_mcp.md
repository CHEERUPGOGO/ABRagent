# Materials Project MCP 接入

项目使用 LangChain 工具 + FastMCP Client，启动 `mp-api` 自带的
`mp_api.mcp.server`，通过 stdio 调用官方 `search(query)` 和 `fetch(idx)`。
Server 和 Client 均使用 chem Conda 环境，代码中不实现 Materials Project Server。

本机环境：`E:/Chem/conda_env/chem/python.exe`。已安装官方 `mp-api 0.46.5`、
`FastMCP 2.14.7`，沿用现有 `LangChain 1.3.15`；
重新安装时在项目根目录使用该解释器执行 `-m pip install -e '.[rag,mp,dev]'`。
MP extra 需要 Python 3.11 或更高版本。

## 配置

项目根目录 `.env`（已被 Git 忽略）中填写：

```dotenv
MP_API_KEY=填写你的 Materials Project API Key
MP_MCP_ENABLED=true
MP_MCP_PYTHON=E:/Chem/conda_env/chem/python.exe
MP_MCP_TIMEOUT=120
MP_MCP_MAX_RESULTS=3
```

`MP_MCP_PYTHON` 指向安装了 `mp-api[mcp]` 的 Python，留空使用当前解释器。
系统环境变量优先于 `.env`。仅将 MP API Key 显式传给官方子进程。
每次查询会建立连接、启动 Server 并在结束/异常时关闭，无需手动启动后台服务。
`MP_MCP_ENABLED=false` 可关闭查询；未填 Key 时返回 `missing_api_key`，保留本地检索结果。

## 使用与验证

在 PowerShell、项目根目录执行：

```powershell
# 启动官方 Server、验证握手和工具发现，不查询材料（启动会联网检查 MP 服务）。
& 'E:/Chem/conda_env/chem/python.exe' -m auto_battery_research.tools.materials_project --check

# 填入 Key 后查询真实材料（直接 fetch mp-id）。
& 'E:/Chem/conda_env/chem/python.exe' -m auto_battery_research.tools.materials_project --query mp-149

# 搜索化学式，再获取最多 MP_MCP_MAX_RESULTS 条详情。
& 'E:/Chem/conda_env/chem/python.exe' -m auto_battery_research.tools.materials_project --query LiFePO4
```

LangChain Agent 的 Stage 4 工具箱增加 `QueryMaterialsProject`，支持 `search`/`fetch`。
主 RAG 流程则由 Planner 输出 `mp_queries`（最多 3 条），在本地检索之后、Writer
之前自动补充证据。纯实验容量/循环寿命问题应保持 `mp_queries=[]`。
示例问题：比较 LiFePO4 与 LiCoO2 的晶体结构和热力学稳定性，并给出文献依据。

证据 ID 为 `MP:mp-149`，保留材料网页、查询内容、查询时间、计算属性及单位。
MP 的分数只用于证据排序，不代表材料性能或科学置信度。
计算属性不能作为实验容量、循环寿命或电芯能量密度的实测证据。
MP 查询状态保存在 `rag_result.json` 的 `retrieval.search_logs` 中；超时、协议异常
会返回 `mcp_error` 和异常类型，不会伪造材料记录。

官方实现：https://github.com/materialsproject/api/tree/main/mp_api/mcp
