"""LLM 全量轨迹记录单元测试.

覆盖:
1. recorder 核心: 默认关闭零写盘 / 开启写 JSONL (seq 递增) + trace_meta / 环境变量
   LLM_TRACE 引导 / 课题目录切换开新会话 / 并发追加 seq 唯一;
2. serialize_message 字段保真 (AIMessage+tool_calls+usage_metadata / ToolMessage /
   HumanMessage) 与 extract_usage 归一化;
3. RAG LLMClient.chat 挂钩: 完整输入(system/user 全文)+输出+trace_tag+usage 记录,
   异常路径记 error 后按原语义抛出;
4. ReAct 后端挂钩: offline 兜底路径记 backend=offline 事件 (含 trim 前后消息数);
5. 回归保障: 用例间 recorder 状态隔离 (autouse fixture), 不泄漏到其他测试。

全部离线运行 (不触网)。
"""

import json
import threading
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from auto_battery_research.util import llm_trace
from auto_battery_research.util.llm_trace import (
    configure_llm_trace,
    extract_usage,
    get_llm_trace_recorder,
    llm_trace_enabled,
    record_llm_call,
    serialize_message,
)


@pytest.fixture(autouse=True)
def _isolated_recorder(tmp_path, monkeypatch):
    """用例间隔离: 清环境变量 + 重置单例状态 + 结束后强制关闭 (不泄漏到其他测试)."""
    monkeypatch.delenv("LLM_TRACE", raising=False)
    rec = get_llm_trace_recorder()
    with rec._lock:
        rec._enabled = False
        rec._trace_dir = None
        rec._session_file = None
        rec._seq = 0
        rec._meta = {}
        rec._meta_written = False
    yield
    configure_llm_trace(enabled=False)


def _read_events(trace_dir: Path):
    files = sorted(trace_dir.glob("trace_*.jsonl"))
    assert files, "未生成轨迹文件"
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines], files[0]


# ══════════════════ 1. recorder 核心 ══════════════════

def test_disabled_records_nothing(tmp_path):
    """默认关闭: record 零写盘 (opt-in 语义)."""
    configure_llm_trace(trace_dir=tmp_path)  # enabled=None → 环境变量(未设) → 关闭
    assert llm_trace_enabled() is False
    assert record_llm_call("test.site", input={"a": 1}) is None
    assert list(tmp_path.glob("trace_*.jsonl")) == []


def test_enabled_writes_jsonl_and_meta(tmp_path):
    """开启后: JSONL 逐行追加 (seq 递增) + trace_meta.json 元数据."""
    configure_llm_trace(enabled=True, trace_dir=tmp_path, goal="课题A",
                        source="test", meta={"model": "test-model"})
    assert llm_trace_enabled() is True
    record_llm_call("site.one", input={"messages": [1]}, backend="openai")
    record_llm_call("site.two", output={"text": "ok"})
    events, session = _read_events(tmp_path)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[0]["event"] == "llm_call" and events[0]["call_site"] == "site.one"
    assert events[0]["backend"] == "openai" and events[0]["input"] == {"messages": [1]}
    assert "ts" in events[0]
    meta = json.loads((tmp_path / "trace_meta.json").read_text(encoding="utf-8"))
    assert meta["goal"] == "课题A" and meta["model"] == "test-model"
    assert meta["session_file"] == session.name


def test_env_var_bootstrap(tmp_path, monkeypatch):
    """环境变量 LLM_TRACE=1 可独立开启 (MCP/TUI 无配置场景)."""
    monkeypatch.setenv("LLM_TRACE", "1")
    configure_llm_trace(trace_dir=tmp_path)
    assert llm_trace_enabled() is True
    monkeypatch.setenv("LLM_TRACE", "0")
    configure_llm_trace(trace_dir=tmp_path)
    assert llm_trace_enabled() is False


def test_trace_dir_switch_starts_new_session(tmp_path):
    """切换课题目录 → 新会话文件, seq 重新计数."""
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    configure_llm_trace(enabled=True, trace_dir=dir_a, goal="g1")
    record_llm_call("site", input={})
    configure_llm_trace(enabled=True, trace_dir=dir_b, goal="g2")
    record_llm_call("site", input={})
    ev_a, _ = _read_events(dir_a)
    ev_b, _ = _read_events(dir_b)
    assert [e["seq"] for e in ev_a] == [1]
    assert [e["seq"] for e in ev_b] == [1]
    assert ev_b[0]["goal"] == "g2"


def test_concurrent_append_seq_unique(tmp_path):
    """多线程并发追加: 100 条记录 seq 唯一且连续 (线程安全)."""
    configure_llm_trace(enabled=True, trace_dir=tmp_path)

    def _worker():
        for _ in range(25):
            record_llm_call("site.concurrent", input={})

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    events, _ = _read_events(tmp_path)
    assert sorted(e["seq"] for e in events) == list(range(1, 101))


def test_record_failure_is_silent(tmp_path):
    """记录动作内部异常 (如 trace_dir 是已存在的普通文件) 绝不抛出到主流程."""
    blocker = tmp_path / "blocker"
    blocker.write_text("plain file, not a directory", encoding="utf-8")
    configure_llm_trace(enabled=True, trace_dir=blocker)  # mkdir 必然失败
    assert record_llm_call("site", input={}) is None  # 静默, 不抛出


# ══════════════════ 2. 序列化与用量提取 ══════════════════

def test_serialize_message_fidelity():
    msg = AIMessage(content="hello")
    msg.tool_calls = [{"name": "Check", "args": {"stage_id": 5}, "id": "call_1"}]
    msg.usage_metadata = {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}
    d = serialize_message(msg)
    assert d["type"] == "AIMessage" and d["role"] == "ai" and d["content"] == "hello"
    assert d["tool_calls"][0]["name"] == "Check"
    assert d["usage_metadata"]["total_tokens"] == 7

    tool_msg = ToolMessage(content="observation text", name="Check", tool_call_id="call_1")
    d2 = serialize_message(tool_msg)
    assert d2["role"] == "tool" and d2["tool_call_id"] == "call_1"
    assert d2["content"] == "observation text"

    assert serialize_message(HumanMessage(content="hi"))["role"] == "human"
    assert serialize_message("raw string") == "raw string"
    assert serialize_message(None) is None


def test_extract_usage_variants():
    msg = AIMessage(content="x")
    msg.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert extract_usage(msg) == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    rm_msg = AIMessage(content="y")
    rm_msg.response_metadata = {"token_usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}
    assert extract_usage(rm_msg) == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    assert extract_usage(AIMessage(content="z")) is None


# ══════════════════ 3. RAG LLMClient.chat 挂钩 ══════════════════

class _FakeOpenAILLM:
    temperature = 0.1

    def __init__(self, content: str = " final answer ", fail: bool = False):
        self._content = content
        self._fail = fail

    def invoke(self, messages):
        if self._fail:
            raise ConnectionError("boom: network down")
        resp = AIMessage(content=self._content)
        resp.usage_metadata = {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}
        return resp


def _make_rag_client(fail: bool = False):
    from lmllm.RAG.llm_client import LLMClient
    client = LLMClient(backend="openai")
    client._openai_llm = _FakeOpenAILLM(fail=fail)  # 替换真实 ChatOpenAI, 离线可测
    client.backend = "openai"
    return client


def test_rag_chat_records_full_io(tmp_path):
    """chat 挂钩: system/user 全文 + 输出全文 + trace_tag + usage 一次成档."""
    configure_llm_trace(enabled=True, trace_dir=tmp_path)
    client = _make_rag_client()
    result = client.chat("SYSTEM PROMPT", "USER PROMPT", temperature=0.2, trace_tag="writer")
    assert "final answer" in result
    events, _ = _read_events(tmp_path)
    assert len(events) == 1
    ev = events[0]
    assert ev["call_site"] == "lmllm.RAG.llm_client.chat"
    assert ev["trace_tag"] == "writer" and ev["backend"] == "openai"
    assert ev["input"]["messages"][0] == {"role": "system", "content": "SYSTEM PROMPT"}
    assert ev["input"]["messages"][1] == {"role": "user", "content": "USER PROMPT"}
    assert "final answer" in ev["output"]["text"]
    assert ev["output"]["status"] == "ok"
    assert ev["usage"]["total_tokens"] == 7


def test_rag_chat_records_error_and_reraises(tmp_path):
    """异常路径: error 事件落档后按原语义抛出 RuntimeError."""
    configure_llm_trace(enabled=True, trace_dir=tmp_path)
    client = _make_rag_client(fail=True)
    with pytest.raises(RuntimeError, match="OpenAI/MiniMax API 调用失败"):
        client.chat("SYS", "USER", trace_tag="writer")
    events, _ = _read_events(tmp_path)
    assert events[0]["output"]["status"] == "error"
    assert "boom" in events[0]["error"]


def test_rag_chat_no_trace_when_disabled(tmp_path):
    """关闭时 chat 不产生任何记录 (零开销路径)."""
    configure_llm_trace(enabled=False, trace_dir=tmp_path)
    client = _make_rag_client()
    assert "final answer" in client.chat("S", "U")
    assert list(tmp_path.glob("trace_*.jsonl")) == []


# ══════════════════ 4. ReAct 后端挂钩 ══════════════════

def test_backend_invoke_offline_recorded(tmp_path):
    """offline 兜底路径也记录 (backend=offline), 输入消息与 thread/stage 元数据入档."""
    from auto_battery_research.backend.langchain_backend import ABRLangChainBackend
    configure_llm_trace(enabled=True, trace_dir=tmp_path)
    backend = ABRLangChainBackend(config={
        "openai": {"openai_api_key": "dummy_key"},
        "llm": {"api_key": "dummy_key"},
    })
    resp = backend.invoke([HumanMessage(content="stage prompt body")],
                          thread_id="abr_demo_s5_a1", stage_id=5)
    assert "Offline Mode" in resp.content
    events, _ = _read_events(tmp_path)
    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == "llm_call"
    assert ev["call_site"] == "backend.langchain_backend.invoke"
    assert ev["backend"] == "offline"
    assert ev["thread_id"] == "abr_demo_s5_a1" and ev["stage_id"] == 5
    assert ev["input"]["post_trim_count"] == 1
    assert ev["input"]["messages"][0]["content"] == "stage prompt body"
    assert ev["output"]["status"] == "ok"


def test_trace_callback_handler_records_start_end(tmp_path):
    """legacy 工厂回调: on_chat_model_start/on_llm_end 成对记录输入与输出."""
    configure_llm_trace(enabled=True, trace_dir=tmp_path)
    handler = llm_trace.LLMTraceCallbackHandler(call_site="miner.config.create_llm")
    handler.on_chat_model_start({}, [[HumanMessage(content="mine prompt")]], run_id="r1")
    gen = AIMessage(content="mined result")
    gen.usage_metadata = {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11}

    class _Gen:
        message = gen
        text = None

    class _Resp:
        generations = [[_Gen()]]
        llm_output = {}

    handler.on_llm_end(_Resp(), run_id="r1")
    events, _ = _read_events(tmp_path)
    assert [e["phase"] for e in events] == ["start", "end"]
    assert events[0]["input"]["messages"][0]["content"] == "mine prompt"
    assert events[1]["output"]["messages"][0]["content"] == "mined result"
    assert events[1]["usage"]["total_tokens"] == 11


def test_trace_callback_list_disabled_returns_empty():
    """tracing 关闭时工厂回调注入为空列表 (零开销)."""
    configure_llm_trace(enabled=False)
    assert llm_trace.trace_callback_list("x") == []
