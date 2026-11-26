"""接口级JSON约束的离线回归：验证实际HTTP请求、重试、备用后端及Writer入口。"""
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_openai import ChatOpenAI

from auto_battery_research.rag import LLMClient
from auto_battery_research.util import llm_trace
from auto_battery_research.util.writer_protocol import produce


@pytest.fixture
def transport_client():
    clients = []

    def make(responses, *, backend="openai", status=200):
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            if status != 200:
                return httpx.Response(status, json={"error": {
                    "message": "response_format 不受支持", "type": "invalid_request_error"}})
            text, reason = responses.pop(0)
            return httpx.Response(200, json={
                "id": "offline-json-mode", "object": "chat.completion", "created": 0,
                "model": "offline-model", "choices": [{"index": 0,
                    "message": {"role": "assistant", "content": text}, "finish_reason": reason}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}})

        http = httpx.Client(transport=httpx.MockTransport(handle))
        clients.append(http)
        llm = ChatOpenAI(api_key="offline-key", base_url="https://offline.invalid/v1",
                         model="offline-model", http_client=http, max_retries=0)
        client = LLMClient.__new__(LLMClient)
        client.backend = backend
        client.temperature, client.max_tokens, client.output_max_tokens = .1, 100, 200
        client._model_name, client.last_response = "offline-model", {}
        client._openai_llm = llm if backend == "openai" else None
        client._deepseek_llm = llm if backend == "deepseek" else None
        return client, requests

    yield make
    for client in clients:
        client.close()


@pytest.mark.parametrize("backend", ["openai", "deepseek"])
def test_json_constraint_reaches_http_and_survives_retry(transport_client, monkeypatch, backend):
    client, requests = transport_client([('{"value":', "length"), ('{"value":1}', "stop")], backend=backend)
    records = []
    monkeypatch.setattr(llm_trace, "llm_trace_enabled", lambda: True)
    monkeypatch.setattr(llm_trace, "record_llm_call", lambda *a, **kw: records.append(kw))
    assert client.chat("仅回答结果", "提取数值", json_mode=True) == '{"value":1}'
    assert len(requests) == len(records) == 2
    assert [r.get("max_completion_tokens", r.get("max_tokens")) for r in requests] == [100, 200]
    for request, record in zip(requests, records):
        assert request["response_format"] == {"type": "json_object"}
        assert "JSON" in request["messages"][0]["content"]
        assert record["input"]["response_format"] == request["response_format"]
        assert record["input"]["messages"] == request["messages"]


def test_single_attempt_json_truncation_preserves_raw_without_retry(transport_client):
    raw = '{"sections":'
    client, requests = transport_client([(raw, "length")])
    client.single_attempt = True
    with pytest.raises(RuntimeError) as error:
        client.chat("SYS", "USER", json_mode=True)
    assert error.value.raw_response == raw
    assert error.value.response_meta["truncated"] is True
    assert len(requests) == 1
    assert requests[0]["response_format"] == {"type": "json_object"}


def test_plain_text_call_does_not_inherit_json_constraint(transport_client):
    client, requests = transport_client([('{}', "stop"), ("## 实验步骤\n原始正文", "stop")])
    client.chat("SYS", "USER", json_mode=True)
    assert client.chat("SYS", "USER") == "## 实验步骤\n原始正文"
    assert "response_format" not in requests[1]
    assert requests[1]["messages"][0]["content"] == "SYS"


def test_unsupported_json_constraint_is_not_retried_as_plain_text(transport_client):
    client, requests = transport_client([], status=400)
    with pytest.raises(RuntimeError, match="response_format"):
        client.chat("SYS", "USER", json_mode=True)
    assert len(requests) == 1
    assert requests[0]["response_format"] == {"type": "json_object"}


def test_alternate_backend_keeps_json_constraint(transport_client):
    client, primary = transport_client([], status=400)
    fallback, secondary = transport_client([('{}', "stop")], backend="deepseek")
    client.backend = "auto"
    client._deepseek_llm = fallback._deepseek_llm
    assert client.chat("SYS", "USER", json_mode=True) == '{}'
    assert len(primary) == len(secondary) == 1
    assert primary[0]["response_format"] == secondary[0]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("json_mode", [False, True])
def test_ollama_sends_native_json_format(monkeypatch, json_mode):
    requests = []
    records = []
    monkeypatch.setattr(llm_trace, "llm_trace_enabled", lambda: True)
    monkeypatch.setattr(llm_trace, "record_llm_call", lambda *a, **kw: records.append(kw))

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"message": {"content": '{}'}, "done_reason": "stop"}).encode()

    def urlopen(request, **kwargs):
        requests.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr(LLMClient, "ollama_ready", property(lambda self: True))
    client = LLMClient.__new__(LLMClient)
    client.backend, client.temperature, client.max_tokens = "ollama", .1, 100
    client._model_name = client.ollama_model = "offline-model"
    client.ollama_base_url, client.ollama_timeout = "http://offline.invalid", 10
    assert client.chat("SYS", "USER", json_mode=json_mode) == '{}'
    assert requests[0].get("format") == ("json" if json_mode else None)
    assert records[0]["input"].get("format") == requests[0].get("format")
    assert "response_format" not in records[0]["input"]


def test_structured_writer_enables_constraint_and_rejects_invalid_server_output(transport_client):
    from auto_battery_research.tests.test_design_structure import document
    valid = json.dumps(dict(document(), version=2, parameters={}), ensure_ascii=False)
    for raw in (valid, valid + '}\n</parameter>'):
        client, requests = transport_client([(raw, "stop")])
        state = {}
        result = produce(client, "生成设计", "USER", {}, state, lambda: None)
        assert requests[0]["response_format"] == {"type": "json_object"}
        assert state["raw_response"] == raw
        assert result["fallback"] is (raw != valid)
        if raw != valid:
            assert "Extra data" in result["error"]


def test_json_constraint_cannot_be_silently_ignored_by_transport():
    client = LLMClient.__new__(LLMClient)
    client.backend, client.temperature, client.max_tokens = "openai", .1, 100
    client._model_name = "offline-model"
    client._openai_llm = SimpleNamespace(invoke=lambda messages: pytest.fail("约束未绑定时不能发送"))
    with pytest.raises(RuntimeError, match="不能绑定接口级 JSON 约束"):
        client.chat("SYS", "USER", json_mode=True)
