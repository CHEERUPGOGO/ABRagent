"""空响应/截断重试及审核证据完整性离线回归。"""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from auto_battery_research.rag import LLMClient
from lmllm.RAG.llm_client import LLMOutputError
from lmllm.RAG.agents import WriterAgent, ReviewerAgent


def client_with_responses(responses):
    class Transport:
        def __init__(self):
            self.budgets = []
            self.prompts = []

        def bind(self, **kwargs):
            self.budgets.append(kwargs["max_tokens"])
            return self

        def invoke(self, messages):
            self.prompts.append(messages)
            return responses.pop(0)

    client = LLMClient.__new__(LLMClient)
    client.backend = "openai"
    client.temperature = .1
    client.max_tokens = 100
    client.output_max_tokens = 200
    client._model_name = "scripted"
    client.last_response = {}
    client._openai_llm = Transport()
    return client


def test_length_and_empty_responses_retry_with_frozen_input():
    first = AIMessage(content="被截断的正文", response_metadata={"finish_reason": "length"})
    final = AIMessage(content="完整正文", response_metadata={"finish_reason": "stop"})
    client = client_with_responses([first, final])
    assert client.chat("SYS", "冻结参数 R_s=2.7", trace_tag="writer") == "完整正文"
    assert client._openai_llm.budgets == [100, 200]
    assert all("冻结参数 R_s=2.7" in p[1].content for p in client._openai_llm.prompts)
    assert client.last_response["truncated"] is False


def test_exhausted_output_is_not_success_or_fabricated_writer_fallback():
    responses = [AIMessage(content="", response_metadata={"finish_reason": "length"}) for _ in range(4)]
    client = client_with_responses(responses)
    with pytest.raises(LLMOutputError):
        client.chat("SYS", "USER")
    writer = WriterAgent(client)
    out = writer.run("设计电池", {}, [{"passage_id": "p", "source": "s", "text": "原文"}])
    assert out["draft_answer"] == ""
    assert out["fallback"] is True and out["error"]


def test_reviewer_receives_writer_evidence_and_returns_verdict_only():
    calls = []
    def chat(system, user, **kwargs):
        calls.append(user)
        return json.dumps({"confidence": "high", "issues": [], "error_type": "none"})
    llm = SimpleNamespace(available=True, chat=chat)
    evidence = [{"passage_id": f"p{i}", "source": "s", "text": f"证据{i}"} for i in range(30)]
    draft = "方案引用 [p24] 和 [p29]。"
    out = ReviewerAgent(llm).run("问题", evidence, draft)
    assert "[p24] 来源:s" in calls[0] and "[p29] 来源:s" in calls[0]
    assert out["revised_answer"] == draft and out["confidence"] == "high"


@pytest.mark.parametrize("response", ["", '{"issues": [', '{"issues": [], "revised_answer": "残缺"}'])
def test_invalid_reviewer_cannot_approve(response):
    llm = SimpleNamespace(available=True, chat=lambda *a, **kw: response)
    out = ReviewerAgent(llm).run("问题", [{"passage_id": "p", "source": "s", "text": "证据"}], "正文")
    assert out["confidence"] == "low" and out["error_type"] == "reviewer"


def test_reviewer_transport_failure_cannot_be_overridden_by_rules():
    def fail(*args, **kwargs):
        raise LLMOutputError("截断")
    out = ReviewerAgent(SimpleNamespace(available=True, chat=fail)).run(
        "问题", [{"passage_id": "p", "source": "s", "text": "证据"}], "正文")
    assert out["confidence"] == "low" and out["error_type"] == "reviewer"


def test_full_headings_do_not_hide_truncated_body():
    from auto_battery_research.util.design_sections import design_output_issues
    draft = "\n".join(f"## {title}\n" + "有证据则提取，无证据留空。" * 6 for title in (
        "目标与设计路线", "推荐组合", "预期关键指标", "可行性依据", "风险与数据缺口"))
    assert not design_output_issues(draft)
    assert any("截断" in e for e in design_output_issues(draft + "但**电芯级"))
