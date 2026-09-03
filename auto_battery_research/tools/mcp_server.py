"""MCP Server — Model Context Protocol 服务实现 (AutoBatteryResearch Agent).

支持 stdio 标准输入输出与 JSON-RPC 2.0，供 Claude Code、Qwen-Code、Antigravity 等接入。
完整实现 initialize 握手 / ping 保活 / notification 静默 (不应答)；工具执行期间的
同进程 print() 一律重定向 stderr，保护 stdout 纯 JSON-RPC 流。

冷启动提示: 启动需导入完整智能体依赖链 (langchain 等, 实测约 30~40s)，部分客户端
默认握手超时 30s 会误判连接失败 —— Claude Code 注册时加 `-e MCP_TIMEOUT=120000`。
"""

import sys
import json
import logging
import contextlib
from typing import Dict, Any, List, Optional

from auto_battery_research.tools.stage_tools import (
    tool_get_role_info,
    tool_get_status,
    tool_get_detail,
    tool_get_current_tips,
    tool_check_stage,
    tool_complete_stage,
    tool_set_stage_journal,
    tool_get_all_stage_journal,
    tool_skip_stage,
    tool_enable_stage,
    tool_run_stage_task,
)

from auto_battery_research.tools.file_tools import (
    read_text_file,
    edit_text_file,
    replace_string_in_file,
)

L = logging.getLogger("AutoBatteryResearch.MCPServer")

TOOL_SCHEMAS = [
    {
        "name": "RoleInfo",
        "description": "获取化学电池科研智能体 (AutoBatteryResearch Agent) 的使命与角色背景。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "Status",
        "description": "查询工作流全局进度、各阶段完成状态与当前活跃 Stage。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "Detail",
        "description": "获取任务元数据、各阶段深度配置、检查器列表与产物契约明细。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "CurrentTips",
        "description": "获取当前活跃 Stage 的任务描述、预期产物、参考指南与门禁要求。",
        "inputSchema": {"type": "object", "properties": {}},
    },

    {
        "name": "Check",
        "description": "执行当前或指定 Stage 的门禁自检。只做结构化质量诊断，不推进状态。",
        "inputSchema": {
            "type": "object",
            "properties": {"stage_id": {"type": "integer", "description": "阶段 ID (1-6)，留空表示当前活跃阶段"}},
        },
    },
    {
        "name": "Complete",
        "description": "终审当前阶段并推进：门禁校验完全通过后，自动将状态机推进到下一个有效 Stage。",
        "inputSchema": {
            "type": "object",
            "properties": {"stage_id": {"type": "integer", "description": "阶段 ID (1-6)，留空表示当前活跃阶段"}},
        },
    },
    {
        "name": "SetStageJournal",
        "description": "记录当前或指定 Stage 的研发日志、关键发现与交付物列表。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "stage_id": {"type": "integer", "description": "阶段 ID"},
                "notes": {"type": "string", "description": "阶段研发心得与结论"},
                "deliverables": {"type": "array", "items": {"type": "string"}, "description": "交付物路径列表"},
                "key_findings": {"type": "object", "description": "关键电化学参数与发现键值对"},
            },
            "required": ["notes"],
        },
    },
    {
        "name": "AllStageJournal",
        "description": "查看工作流所有阶段的历史研发日志记录。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "SkipStage",
        "description": "动态跳过指定阶段 (例如 Stage 5 PINN 仿真)。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "stage_id": {"type": "integer", "description": "阶段 ID (如 5)"},
                "reason": {"type": "string", "description": "跳过原因"},
            },
            "required": ["stage_id"],
        },
    },
    {
        "name": "EnableStage",
        "description": "重新激活已跳过的阶段 (例如重新开启 Stage 5 PINN 物理仿真)。",
        "inputSchema": {
            "type": "object",
            "properties": {"stage_id": {"type": "integer", "description": "阶段 ID (如 5)"}},
            "required": ["stage_id"],
        },
    },
    {
        "name": "RunStageTask",
        "description": "执行当前阶段底层数据挖掘、RAG设计或仿真计算流水线。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "stage_id": {"type": "integer", "description": "阶段 ID"},
                "target_query": {"type": "string", "description": "电池研究目标需求"},
            },
        },
    },
    {
        "name": "ReadTextFile",
        "description": "读取本地文本文件内容。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "文件绝对或相对路径"},
                "count": {"type": "integer", "description": "读取行数，-1 为全部"},
                "start_line": {"type": "integer", "description": "起始行号 (从 1 开始)"},
            },
            "required": ["filepath"],
        },
    },
    {
        "name": "EditTextFile",
        "description": "创建、覆盖或追加写入文本文件。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "写入内容"},
                "append": {"type": "boolean", "description": "是否追加模式，默认 false"},
            },
            "required": ["filepath", "content"],
        },
    },
    {
        "name": "ReplaceStringInFile",
        "description": "在文本文件中精确替换字符串。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "文件路径"},
                "old_string": {"type": "string", "description": "被替换文本"},
                "new_string": {"type": "string", "description": "替换为的新文本"},
            },
            "required": ["filepath", "old_string", "new_string"],
        },
    },
]


def dispatch_tool_call(name: str, args: Dict[str, Any]) -> Any:
    """根据工具名称分发执行."""
    if name == "RoleInfo":
        return tool_get_role_info()
    elif name == "Status":
        return tool_get_status()
    elif name == "Detail":
        return tool_get_detail()
    elif name == "CurrentTips":
        return tool_get_current_tips()

    elif name == "Check":
        return tool_check_stage(stage_id=args.get("stage_id"))
    elif name == "Complete":
        return tool_complete_stage(stage_id=args.get("stage_id"))
    elif name == "SetStageJournal":
        return tool_set_stage_journal(
            stage_id=args.get("stage_id"),
            notes=args.get("notes", ""),
            deliverables=args.get("deliverables"),
            key_findings=args.get("key_findings"),
        )
    elif name == "AllStageJournal":
        return tool_get_all_stage_journal()
    elif name == "SkipStage":
        # stage_id 缺失时不再静默默认跳 Stage 5 (schema 已声明 required, 缺参应显式报错)
        return tool_skip_stage(stage_id=args.get("stage_id"), reason=args.get("reason", "手动跳过"))
    elif name == "EnableStage":
        return tool_enable_stage(stage_id=args.get("stage_id"))
    elif name == "RunStageTask":
        return tool_run_stage_task(
            stage_id=args.get("stage_id"),
            target_query=args.get("target_query", "设计400Wh/kg高比能液态锂金属电池方案"),
        )
    elif name == "ReadTextFile":
        return read_text_file(
            filepath=args.get("filepath", ""),
            count=args.get("count", -1),
            start_line=args.get("start_line", 1),
        )
    elif name == "EditTextFile":
        return edit_text_file(
            filepath=args.get("filepath", ""),
            content=args.get("content", ""),
            append=args.get("append", False),
        )
    elif name == "ReplaceStringInFile":
        return replace_string_in_file(
            filepath=args.get("filepath", ""),
            old_string=args.get("old_string", ""),
            new_string=args.get("new_string", ""),
        )
    else:
        return {"error": f"Unknown tool: {name}"}


def handle_jsonrpc_request(req: Any) -> Optional[Dict[str, Any]]:
    """处理单条 JSON-RPC 2.0 / MCP 请求帧, 返回应答帧.

    notification (无 id 的请求, 如 notifications/initialized) 返回 None —— 按
    JSON-RPC 规范不得应答; 工具执行期间的同进程 stdout 输出重定向到 stderr。
    """
    if not isinstance(req, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}

    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    # notification: 无 id, 静默吞掉 (不得应答)
    if "id" not in req:
        return None

    if method == "initialize":
        try:
            from auto_battery_research import __version__ as server_version
        except Exception:
            server_version = "1.0.0"
        # 支持客户端请求的协议版本则原样回显, 否则回退本服务默认版本
        protocol_version = params.get("protocolVersion") or "2025-06-18"
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "auto-battery-research", "version": server_version},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOL_SCHEMAS}}

    if method == "tools/call":
        t_name = params.get("name")
        t_args = params.get("arguments") or {}
        try:
            # 工具链内同进程 print() (如 incremental.step_* 的进度输出) 一律转
            # stderr, 防止污染 stdout 上的 JSON-RPC 流 (子进程输出已被 capture)
            with contextlib.redirect_stdout(sys.stderr):
                res = dispatch_tool_call(t_name, t_args)
        except Exception as e:  # 工具内部异常以 isError 结果返回, 不打断 RPC 层
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": f"Tool error: {e}"}], "isError": True},
            }
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}]},
        }

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method {method} not found"}}


def start_stdio_server():
    """以 stdio 方式启动 MCP JSON-RPC 2.0 服务 (保护 stdout 仅输出 JSON-RPC)."""
    import os
    from auto_battery_research.util.logger import set_mcp_stdio_mode
    from auto_battery_research.util.env_loader import load_env
    load_env()
    set_mcp_stdio_mode(True)
    os.environ["ABR_MCP_STDIO"] = "1"

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    while True:
        req_id = None
        try:
            line = sys.stdin.readline()
            if not line:
                break
            req = json.loads(line)
            if isinstance(req, dict):
                req_id = req.get("id")
            resp = handle_jsonrpc_request(req)
            if resp is None:
                continue
        except json.JSONDecodeError as e:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {e}"}}
        except Exception as e:
            # 保留请求 id 关联 (解析失败等拿不到 id 时才为 None)
            resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}}

        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
