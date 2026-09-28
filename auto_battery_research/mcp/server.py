"""AutoBatteryResearch MCP Server 的 SDK 初始化与传输入口。"""

from mcp.server.fastmcp import FastMCP

from .adapters import AutoBatteryResearchService
from .tools import register_tools


def create_server(service=None, host="127.0.0.1", port=8000):
    server = FastMCP("AutoBatteryResearch", host=host, port=port)
    register_tools(server, service or AutoBatteryResearchService())
    return server


def main():
    import argparse

    parser = argparse.ArgumentParser(description="AutoBatteryResearch domain expert MCP Server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="streamable-http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    create_server(host=args.host, port=args.port).run(transport=args.transport)


if __name__ == "__main__":
    main()
