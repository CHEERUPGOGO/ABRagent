"""Materials Project official MCP server client and LangChain tool.

The server runs as a stdio child of the configured Python interpreter.
Only MP settings are read from the project .env; process environment takes priority.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
L = logging.getLogger(__name__)
# MP now also returns AlphaID values (e.g. mp-aaaaaaft for legacy mp-149).
MP_ID = re.compile(r"mp-[a-z0-9]+(?:-[a-z0-9]+)*")


def run_sync(coroutine):
    """Support synchronous RAG/CLI calls even inside a web event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coroutine).result()


class MaterialsProjectClient:
    def __init__(self, env_file: Path | None = None):
        from dotenv import dotenv_values

        values = dotenv_values(env_file or ROOT / ".env", interpolate=False)

        def setting(name, default=""):
            return os.environ.get(name, values.get(name) or default)

        self.enabled = setting("MP_MCP_ENABLED", "true").lower() == "true"
        self.api_key = setting("MP_API_KEY").strip()
        self.python = setting("MP_MCP_PYTHON", sys.executable) or sys.executable
        self.timeout = float(setting("MP_MCP_TIMEOUT", "120"))
        self.max_results = int(setting("MP_MCP_MAX_RESULTS", "3"))
        if self.timeout <= 0 or not 1 <= self.max_results <= 10:
            raise ValueError("MP_MCP_TIMEOUT must be positive; MP_MCP_MAX_RESULTS must be 1..10")

    @property
    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        return "ready" if self.api_key else "missing_api_key"

    def _client(self):
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport

        # 隔离 stdio：避免在 Textual TUI、GUI 或 Windows 控制台句柄被重定向时
        # 继承无效的 sys.stderr 引发 [Errno 9] Bad file descriptor
        log_dir = ROOT / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        mcp_log = log_dir / "mcp_mp_stderr.log"

        return Client(
            StdioTransport(
                command=self.python,
                args=["-m", "mp_api.mcp.server"],
                env={"MP_API_KEY": self.api_key, "PYTHONUTF8": "1"},
                keep_alive=False,
                log_file=mcp_log,
            ),
            timeout=self.timeout,
            init_timeout=self.timeout,
        )

    @staticmethod
    def _payload(result) -> dict:
        if getattr(result, "is_error", False) or getattr(result, "isError", False):
            raise RuntimeError("Materials Project MCP tool returned an error")
        # Use the wire-format dict: FastMCP may deserialize .data into generated dataclasses.
        data = getattr(result, "structured_content", None)
        if data is None:
            data = getattr(result, "data", None)
        if hasattr(data, "model_dump"):
            data = data.model_dump(mode="json")
        if data is None:
            texts = [block.text for block in result.content if getattr(block, "type", None) == "text"]
            data = json.loads("".join(texts))
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            raise ValueError("Unexpected Materials Project MCP response schema")
        return data

    @staticmethod
    def _evidence(record: dict, query: str) -> dict | None:
        material_id = str(record.get("id", ""))
        if not MP_ID.fullmatch(material_id):
            raise ValueError("MP response is missing a material id")
        if not record.get("text") and not record.get("metadata"):
            return None
        url = record.get("url") or f"https://materialsproject.org/materials/{material_id}"
        title = record.get("title") or material_id
        metadata = {
            "material_id": material_id,
            "url": url,
            "query": query,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "evidence_type": "computed_material_properties",
            "properties": record.get("metadata") or {},
            "units": {
                "formation_energy_per_atom": "eV/atom", "energy_above_hull": "eV/atom",
                "band_gap": "eV", "density": "g/cm^3", "volume": "angstrom^3",
            },
        }
        text = (
            f"Materials Project 计算材料数据（不能作为实验容量、循环寿命或电芯能量密度的实测证据）。\n"
            f"材料: {title} ({material_id})\n来源: {url}\n"
            f"{record.get('text') or ''}\n"
            f"属性: {json.dumps(metadata['properties'], ensure_ascii=False)}\n"
            f"单位: {json.dumps(metadata['units'], ensure_ascii=False)}"
        )
        return {
            "passage_id": f"MP:{material_id}", "source": url,
            "source_display": f"Materials Project: {title} ({material_id})",
            "title": title, "text": text, "metadata": metadata,
            "score": 1.0, "_source_type": "materials_project",
        }

    async def check(self) -> dict:
        """Check the official server handshake without querying the MP database."""
        async def handshake():
            async with self._client() as client:
                tools = await client.list_tools()
                names = [tool.name for tool in tools]
                if not {"search", "fetch"} <= set(names):
                    raise ValueError("Official server must expose search and fetch")
                return {"success": True, "status": self.status, "tools": names}
        return await asyncio.wait_for(handshake(), timeout=self.timeout)

    async def query(self, query: str, mode: str = "search") -> dict:
        if self.status != "ready":
            return {"success": False, "status": self.status, "evidence": []}
        if mode not in {"search", "fetch"} or not query.strip() or len(query) > 300:
            return {"success": False, "status": "invalid_query", "evidence": []}

        async def retrieve():
            async with self._client() as client:
                if mode == "fetch" or MP_ID.fullmatch(query.strip()):
                    ids = [query.strip()]
                else:
                    payload = self._payload(await client.call_tool("search", {"query": query.strip()}))
                    results = payload.get("results")
                    if not isinstance(results, list):
                        raise ValueError("MP search response is missing results")
                    ids = list(dict.fromkeys(str(item["id"]) for item in results))[:self.max_results]
                    if any(not MP_ID.fullmatch(idx) for idx in ids):
                        raise ValueError("MP search returned invalid material ids")
                evidence = []
                for idx in ids:
                    record = self._payload(await client.call_tool("fetch", {"idx": idx}))
                    item = self._evidence(record, query)
                    if item:
                        evidence.append(item)
                return {"success": True, "status": "ok" if evidence else "no_results", "evidence": evidence}

        try:
            return await asyncio.wait_for(retrieve(), timeout=self.timeout)
        except Exception as exc:
            error = str(exc).replace(self.api_key, "[redacted]")[:500]
            return {"success": False, "status": "mcp_error", "error_type": type(exc).__name__,
                    "error": error or "MCP request timed out", "evidence": []}


class MaterialsProjectArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="化学式、Li-Fe-O 等化学体系、mp-id 或逗号分隔的英文结构关键词")
    mode: Literal["search", "fetch"] = Field(default="search", description="search 查候选并获取详情；fetch 查指定 mp-id 或最稳定的化学式/体系匹配")


class QueryMaterialsProjectTool(BaseTool):
    name: str = "QueryMaterialsProject"
    description: str = (
        "【Stage 4】仅当问题给出明确化学式、化学体系或 mp-id，且需要晶体结构、形成能、凸包能或带隙等计算属性时，"
        "通过官方 Materials Project MCP 查询。不能证明界面键合、氧释放抑制、实验容量或循环寿命。"
        "RunRAGDesign 内部已按需查询 MP，同一设计问题无需预先重复调用。"
    )
    args_schema: type[BaseModel] = MaterialsProjectArgs

    def _run(self, query: str, mode: str = "search") -> str:
        return run_sync(self._arun(query, mode))

    async def _arun(self, query: str, mode: str = "search") -> str:
        result = await MaterialsProjectClient().query(query, mode)
        return json.dumps(result, ensure_ascii=False)


def augment_mp_evidence(retrieval: dict, plan: dict, top_k: int) -> dict:
    """Add planned MP queries to the RAG evidence without replacing local retrieval."""
    queries = plan.get("mp_queries", [])
    if not isinstance(queries, list) or not queries:
        return retrieval
    queries = list(dict.fromkeys(q.strip() for q in queries if isinstance(q, str) and q.strip()))[:3]
    client = MaterialsProjectClient()
    external = {}
    logs = retrieval.setdefault("search_logs", [])
    for query in queries:
        result = run_sync(client.query(query))
        logs.append({"source": "materials_project", "query": query,
                     **{k: v for k, v in result.items() if k != "evidence"}})
        for item in result["evidence"]:
            external[item["passage_id"]] = item
        if result["status"] in {"disabled", "missing_api_key", "mcp_error"}:
            if result["status"] != "disabled":
                L.warning("MP retrieval unavailable: %s (%s)", result["status"], result.get("error_type", "configuration"))
            break
    # Place a bounded number first so Writer's evidence window includes the new source.
    mp_items = list(external.values())[:min(client.max_results, max(0, top_k))]
    local = [item for item in retrieval.get("results", []) if item["passage_id"] not in external]
    retrieval["results"] = (mp_items + local)[:top_k]
    return retrieval


if __name__ == "__main__":
    import argparse

    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Check/query the official MP MCP server")
    parser.add_argument("--check", action="store_true", help="Start official server and list tools (no API query)")
    parser.add_argument("--query", default="mp-149")
    args = parser.parse_args()
    try:
        client = MaterialsProjectClient()
        result = run_sync(client.check() if args.check else client.query(args.query))
    except Exception as exc:
        result = {"success": False, "status": "mcp_error", "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["success"] else 1)
