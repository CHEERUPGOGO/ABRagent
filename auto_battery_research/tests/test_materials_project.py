"""Offline protocol and integration checks; never query the real MP API."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from auto_battery_research.tools import materials_project as mp


class FetchRecord(BaseModel):
    id: str
    title: str = "Silicon"
    text: str = "Diamond structure"
    metadata: dict = {}


class SearchRecords(BaseModel):
    results: list[FetchRecord]


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ("MP_API_KEY", "MP_MCP_ENABLED", "MP_MCP_PYTHON", "MP_MCP_TIMEOUT", "MP_MCP_MAX_RESULTS"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("MP_API_KEY=test-key\nMP_MCP_MAX_RESULTS=2\n", encoding="utf-8")
    return mp.MaterialsProjectClient(env)


@pytest.fixture
def server(client, monkeypatch):
    fastmcp = pytest.importorskip("fastmcp")
    server = fastmcp.FastMCP("MP protocol fixture")
    calls = []

    @server.tool
    def search(query: str) -> SearchRecords:
        calls.append(("search", query))
        return SearchRecords(results=[FetchRecord(id=idx) for idx in ("mp-149", "mp-149", "mp-13", "mp-1")])

    @server.tool
    def fetch(idx: str) -> FetchRecord:
        calls.append(("fetch", idx))
        return FetchRecord(id=idx, metadata={"formation_energy_per_atom": 0.0, "band_gap": 0.6})

    monkeypatch.setattr(client, "_client", lambda: fastmcp.Client(server))
    return calls


def test_real_fastmcp_protocol_search_fetch(client, server):
    result = mp.run_sync(client.query("Si"))
    assert result["success"]
    assert server == [("search", "Si"), ("fetch", "mp-149"), ("fetch", "mp-13")]
    evidence = result["evidence"][0]
    assert evidence["passage_id"] == "MP:mp-149"
    assert evidence["source"].endswith("/mp-149")
    assert evidence["metadata"]["properties"]["formation_energy_per_atom"] == 0.0
    assert evidence["metadata"]["units"]["formation_energy_per_atom"] == "eV/atom"
    assert "不能作为实验" in evidence["text"]


def test_id_goes_straight_to_fetch(client, server):
    result = mp.run_sync(client.query("mp-149"))
    assert result["success"]
    assert server == [("fetch", "mp-149")]


@pytest.mark.parametrize("query", ["mp-149", "mp-aaaaaaft", "Si"])
def test_live_alpha_id_response_is_accepted(client, monkeypatch, query):
    calls = []

    class OfficialResponseClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            # Minimal response captured from the official 0.46.5 MCP server.
            record = {"id": "mp-aaaaaaft", "title": "mp-149",
                      "url": "https://next-gen.materialsproject.org/materials/mp-149",
                      "text": "Si is diamond structured.",
                      "metadata": {"formula_pretty": "Si", "band_gap": 0.6102999999999996}}
            payload = {"results": [record]} if name == "search" else record
            return SimpleNamespace(structured_content=payload)

    monkeypatch.setattr(client, "_client", OfficialResponseClient)
    result = mp.run_sync(client.query(query))
    assert result["success"]
    assert result["evidence"][0]["passage_id"] == "MP:mp-aaaaaaft"
    assert result["evidence"][0]["source"].endswith("/mp-149")
    if query == "Si":
        assert calls == [("search", {"query": "Si"}), ("fetch", {"idx": "mp-aaaaaaft"})]
    else:
        assert calls == [("fetch", {"idx": query})]


def test_missing_key_does_not_launch_process(client, monkeypatch):
    client.api_key = ""
    monkeypatch.setattr(client, "_client", lambda: pytest.fail("should not start server"))
    assert mp.run_sync(client.query("Si"))["status"] == "missing_api_key"


def test_environment_overrides_dotenv(client, monkeypatch, tmp_path):
    monkeypatch.setenv("MP_API_KEY", "environment-key")
    assert mp.MaterialsProjectClient(tmp_path / ".env").api_key == "environment-key"


def test_text_payload_and_bad_schema():
    result = SimpleNamespace(data=None, content=[SimpleNamespace(type="text", text='{"id":"mp-149"}')])
    assert mp.MaterialsProjectClient._payload(result)["id"] == "mp-149"
    with pytest.raises(ValueError):
        mp.MaterialsProjectClient._payload(SimpleNamespace(data=[]))
    with pytest.raises(RuntimeError):
        mp.MaterialsProjectClient._payload(SimpleNamespace(is_error=True))


def test_server_failure_is_visible_without_secret(client, monkeypatch):
    def fail():
        raise RuntimeError("test-key must not be returned")
    monkeypatch.setattr(client, "_client", fail)
    result = mp.run_sync(client.query("Si"))
    assert result["status"] == "mcp_error"
    assert result["error_type"] == "RuntimeError"
    assert "test-key" not in json.dumps(result)


def test_timeout_is_reported(client, monkeypatch):
    class SlowClient:
        async def __aenter__(self):
            await asyncio.sleep(10)

        async def __aexit__(self, *args):
            return False

    client.timeout = 0.01
    monkeypatch.setattr(client, "_client", SlowClient)
    result = mp.run_sync(client.query("Si"))
    assert result["status"] == "mcp_error"
    assert result["error_type"] == "TimeoutError"


@pytest.mark.asyncio
async def test_langchain_sync_and_async_from_event_loop(client, server, monkeypatch):
    monkeypatch.setattr(mp, "MaterialsProjectClient", lambda: client)
    tool = mp.QueryMaterialsProjectTool()
    assert json.loads(tool.invoke({"query": "mp-149"}))["success"]
    assert json.loads(await tool.ainvoke({"query": "mp-149"}))["success"]


def test_rag_merges_sources_and_reports_missing_key(client, server, monkeypatch):
    monkeypatch.setattr(mp, "MaterialsProjectClient", lambda: client)
    local = {"passage_id": "paper:1", "text": "Experimental evidence", "score": 0.9}
    result = mp.augment_mp_evidence({"results": [local]}, {"mp_queries": ["Si", "Si"]}, 10)
    assert [e["passage_id"] for e in result["results"]] == ["MP:mp-149", "MP:mp-13", "paper:1"]
    assert len(result["search_logs"]) == 1
    client.api_key = ""
    result = mp.augment_mp_evidence({"results": [local]}, {"mp_queries": ["Si"]}, 10)
    assert result["results"] == [local]
    assert result["search_logs"][0]["status"] == "missing_api_key"


def test_no_planned_mp_query_does_not_create_client(monkeypatch):
    monkeypatch.setattr(mp, "MaterialsProjectClient", lambda: pytest.fail("no MP work required"))
    local = {"results": []}
    assert mp.augment_mp_evidence(local, {}, 10) is local


def test_mp_evidence_reaches_writer_and_saved_contract(client, server, tmp_path, monkeypatch):
    from auto_battery_research.tests import test_stage4_golden as golden

    monkeypatch.setattr(mp, "MaterialsProjectClient", lambda: client)
    monkeypatch.setitem(golden.GOLDEN_PLAN, "mp_queries", ["Si"])
    golden._install_scripted_llm(monkeypatch, golden.GOLDEN_WRITER_DRAFT)
    pipeline = golden._build_seeded_pipeline(tmp_path)
    observed = []
    writer_run = pipeline.writer.run

    def capture_writer(**kwargs):
        observed.extend(kwargs["evidence"])
        return writer_run(**kwargs)

    monkeypatch.setattr(pipeline.writer, "run", capture_writer)
    result = golden._run_adapter(pipeline, tmp_path / "task_mp")
    assert result["success"]
    assert any(item["passage_id"] == "MP:mp-149" for item in observed)
    contract = json.loads((tmp_path / "task_mp" / "design_scheme.json").read_text(encoding="utf-8"))
    assert any(item["passage_id"] == "MP:mp-149" for item in contract["evidence"])
    assert "https://materialsproject.org/materials/mp-149" in contract["final_answer"]
    raw = json.loads((tmp_path / "task_mp" / "rag_result.json").read_text(encoding="utf-8"))
    assert any(log.get("source") == "materials_project" and log["status"] == "ok"
               for log in raw["retrieval"]["search_logs"])
