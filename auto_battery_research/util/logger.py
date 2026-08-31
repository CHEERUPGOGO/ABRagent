"""Logger module for AutoBatteryResearch Agent (Clean industrial logging style)."""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional, Dict

_CONSOLE_SINK: Optional[Callable[[str, str], None]] = None
_LOG_FILE_PATH: Optional[Path] = None
_FILE_LOGGING_ENABLED: bool = False

# 当前会话的实时工具调用统计字典 (精确单会话计数)
_SESSION_TOOL_COUNTS: Dict[str, int] = {
    "Search": 0,
    "LLM": 0,
    "Check": 0,
    "Mining": 0,
    "Physics": 0,
    "Report": 0,
}


def now_str() -> str:
    """时间戳前缀."""
    return datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f")[:-3]


def set_console_sink(sink: Optional[Callable[[str, str], None]]) -> None:
    """设置 TUI 控制台的日志流式输出回调."""
    global _CONSOLE_SINK
    _CONSOLE_SINK = sink


def init_file_logger(log_file: Optional[str] = None, log_dir: str = "log") -> Path:
    """显式启用并初始化持久化日志文件 (默认落入 log/ 目录下)."""
    global _LOG_FILE_PATH, _FILE_LOGGING_ENABLED
    _FILE_LOGGING_ENABLED = True
    if log_file:
        p = Path(log_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        _LOG_FILE_PATH = p
    else:
        p = Path(log_dir)
        p.mkdir(parents=True, exist_ok=True)
        _LOG_FILE_PATH = p / "agent.log"
    return _LOG_FILE_PATH


def disable_file_logger() -> None:
    """关闭文件日志落盘."""
    global _FILE_LOGGING_ENABLED, _LOG_FILE_PATH
    _FILE_LOGGING_ENABLED = False
    _LOG_FILE_PATH = None


def reset_session_tool_counts() -> None:
    """重置当前运行的工具计数器."""
    global _SESSION_TOOL_COUNTS
    _SESSION_TOOL_COUNTS = {
        "Search": 0,
        "LLM": 0,
        "Check": 0,
        "Mining": 0,
        "Physics": 0,
        "Report": 0,
    }


def get_session_tool_counts() -> Dict[str, int]:
    """获取当前会话各工具的实时调用次数."""
    global _SESSION_TOOL_COUNTS
    return dict(_SESSION_TOOL_COUNTS)


def record_tool_dispatch(tool_name: str) -> None:
    """根据调用的工具名称分类递增计数器."""
    global _SESSION_TOOL_COUNTS
    if "Vector" in tool_name or "Ingestion" in tool_name:
        _SESSION_TOOL_COUNTS["Search"] = _SESSION_TOOL_COUNTS.get("Search", 0) + 1
    elif "LLM" in tool_name or "Conversational" in tool_name:
        _SESSION_TOOL_COUNTS["LLM"] = _SESSION_TOOL_COUNTS.get("LLM", 0) + 1
    elif "Constraint" in tool_name or "Check" in tool_name:
        _SESSION_TOOL_COUNTS["Check"] = _SESSION_TOOL_COUNTS.get("Check", 0) + 1
    elif "Mining" in tool_name or "Cell" in tool_name:
        _SESSION_TOOL_COUNTS["Mining"] = _SESSION_TOOL_COUNTS.get("Mining", 0) + 1
    elif "PINN" in tool_name or "PyBaMM" in tool_name or "Physics" in tool_name:
        _SESSION_TOOL_COUNTS["Physics"] = _SESSION_TOOL_COUNTS.get("Physics", 0) + 1
    elif "Report" in tool_name or "Synthesizer" in tool_name:
        _SESSION_TOOL_COUNTS["Report"] = _SESSION_TOOL_COUNTS.get("Report", 0) + 1
    else:
        _SESSION_TOOL_COUNTS["Search"] = _SESSION_TOOL_COUNTS.get("Search", 0) + 1


_MCP_STDIO_ACTIVE: bool = False


def set_mcp_stdio_mode(active: bool = True) -> None:
    """激活或取消 MCP stdio 隔离模式 (所有日志转写 stderr，严禁污染 stdout)."""
    global _MCP_STDIO_ACTIVE
    _MCP_STDIO_ACTIVE = active


def log_raw(text: str, style: str = "white", level: str = "INFO") -> None:
    """向控制台与文件写入标准格式日志."""
    ts = now_str()
    formatted = f"{ts} {level}] {text}"
    
    # 1. 仅在显式启用时写入持久化日志文件
    global _LOG_FILE_PATH, _FILE_LOGGING_ENABLED
    if _FILE_LOGGING_ENABLED and _LOG_FILE_PATH is not None:
        try:
            with open(_LOG_FILE_PATH, "a", encoding="utf-8") as f:
                f.write(formatted + "\n")
        except Exception:
            pass

    # 2. 写入终端控制台 (如果在 MCP stdio 模式下，强制写入 stderr)
    global _CONSOLE_SINK
    if _CONSOLE_SINK:
        try:
            _CONSOLE_SINK(formatted, style)
        except Exception:
            pass
    elif _MCP_STDIO_ACTIVE or os.getenv("ABR_MCP_STDIO") == "1":
        sys.stderr.write(formatted + "\n")
        sys.stderr.flush()
    else:
        print(formatted)


def log_tool_call(tool_name: str, args_summary: str) -> None:
    """记录工具调用并递增实时计数器."""
    record_tool_dispatch(tool_name)
    log_raw(f"ToolCall: {tool_name}({args_summary})", style="bold cyan", level="TOOL")


def log_observation(obs_summary: str) -> None:
    """记录工具返回的观测结果."""
    log_raw(f"Observation: {obs_summary}", style="green", level="OBS")


def log_thought(thought_text: str) -> None:
    """记录智能体思维链/规划思考."""
    log_raw(f"AgentThought: {thought_text}", style="yellow", level="THINK")


def log_info(msg: str) -> None:
    """记录信息."""
    log_raw(msg, style="white", level="INFO")


def log_success(msg: str) -> None:
    """记录成功."""
    log_raw(f"Success: {msg}", style="bold green", level="INFO")


def log_error(msg: str) -> None:
    """记录错误."""
    log_raw(f"Error: {msg}", style="bold red", level="ERROR")
