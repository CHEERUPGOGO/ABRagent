"""Web package export for auto_battery_research."""

from .app import create_web_app, launch_web_server
from .server import launch_fastapi_web_server

__all__ = [
    "create_web_app",
    "launch_web_server",          # 旧版 Gradio 仪表盘 (后备入口, --web-gradio)
    "launch_fastapi_web_server",  # FastAPI 只读监控大屏 (主入口, --web)
]
