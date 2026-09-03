"""MCP stdio 协议层离线单元测试 (不启动服务进程、不构造 StageManager)."""
import io
import sys
import contextlib

import auto_battery_research.tools.mcp_server as mcp_server
from auto_battery_research.tools.mcp_server import (
    TOOL_SCHEMAS,
    handle_jsonrpc_request,
)


def test_initialize_handshake():
    """initialize 必须返回 protocolVersion/capabilities/serverInfo, 否则标准客户端握手失败."""
    resp = handle_jsonrpc_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "0"}},
    })
    result = resp["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "auto-battery-research"
    assert result["serverInfo"]["version"]


def test_initialize_echoes_client_protocol_version():
    resp = handle_jsonrpc_request({
        "jsonrpc": "2.0", "id": 2, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    })
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    # 客户端未带版本时回退服务默认版本
    resp2 = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {}})
    assert resp2["result"]["protocolVersion"] == "2025-06-18"


def test_notification_gets_no_response():
    """无 id 的 notification (如 notifications/initialized) 按 JSON-RPC 规范不得应答."""
    assert handle_jsonrpc_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_ping_returns_empty_result():
    resp = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 4, "method": "ping"})
    assert resp["result"] == {}
    assert resp["id"] == 4


def test_unknown_method_returns_32601_with_id():
    resp = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 5, "method": "resources/list"})
    assert resp["error"]["code"] == -32601
    assert resp["id"] == 5


def test_invalid_request_frame():
    resp = handle_jsonrpc_request(["not", "a", "dict"])
    assert resp["error"]["code"] == -32600


def test_tools_list_schema():
    resp = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 6, "method": "tools/list"})
    tools = resp["result"]["tools"]
    assert len(tools) == len(TOOL_SCHEMAS) == 14
    names = {t["name"] for t in tools}
    assert {"RoleInfo", "Status", "Check", "Complete", "SkipStage", "RunStageTask"} <= names


def test_tools_call_unknown_tool_is_result_not_rpc_error():
    resp = handle_jsonrpc_request({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "NoSuchTool", "arguments": {}},
    })
    # 工具层错误作为正常 result 内容返回, 不升级为 RPC error
    assert "error" not in resp
    assert "Unknown tool" in resp["result"]["content"][0]["text"]


def test_tools_call_stdout_guard(monkeypatch):
    """工具执行期间的同进程 print() 必须被重定向 stderr, 不得污染 stdout JSON-RPC 流."""
    def fake_dispatch(name, args):
        print("POLLUTION_LINE")
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "dispatch_tool_call", fake_dispatch)
    captured_err, captured_out = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stderr", captured_err)
    with contextlib.redirect_stdout(captured_out):
        resp = mcp_server.handle_jsonrpc_request({
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "Status", "arguments": {}},
        })
    assert captured_out.getvalue() == ""  # stdout 零污染
    assert "POLLUTION_LINE" in captured_err.getvalue()  # print 落到 stderr
    assert '"ok": true' in resp["result"]["content"][0]["text"]


def test_tools_call_tool_exception_is_iserror(monkeypatch):
    """工具内部异常以 isError result 返回并保留请求 id, 不打断 RPC 层."""
    def boom(name, args):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mcp_server, "dispatch_tool_call", boom)
    resp = mcp_server.handle_jsonrpc_request({
        "jsonrpc": "2.0", "id": 10, "method": "tools/call",
        "params": {"name": "Status", "arguments": {}},
    })
    assert "error" not in resp
    assert resp["id"] == 10
    assert resp["result"]["isError"] is True
    assert "kaboom" in resp["result"]["content"][0]["text"]
