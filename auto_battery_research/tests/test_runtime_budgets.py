"""YAML预算可增减；恢复、证据上下文与门禁使用相同的有效配置。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util import adaptive_retrieval as ar
from auto_battery_research.util.runtime_budgets import bind_repair_options, repair_options
from auto_battery_research.tests.test_adaptive_retrieval import seed as retrieval_seed, diagnostic, evidence, client_gains
from auto_battery_research.tests.test_structure_review_recovery import draft, pipeline
from auto_battery_research.util.design_repair import review_design


@pytest.mark.parametrize("value", [-1, True, 1.5, "bad", None])
def test_invalid_budget_is_rejected(value):
    with pytest.raises(ValueError):
        repair_options({"content_rounds": value})


def test_resume_can_review_and_repair_format_beyond_old_limits(tmp_path):
    text = draft() + "\n\n| 字段 | 工程建议**\n\n独立后续说明。"
    calls = []
    engine = pipeline(tmp_path, lambda **kw: calls.append(kw["draft_answer"]) or {"issues": [], "confidence": "high"})
    engine.stage4_repair_options = {"format_rounds": 4, "content_rounds": 5, "review_rounds": 9}
    state = {"draft": text, "scheme": {}, "evidence": [], "format_repair_rounds": 3,
             "repair_rounds": 3, "review_rounds": 6}
    result, verdict, *_ = review_design(engine, state, "预算恢复课题")
    assert calls == [result] and not verdict["issues"]
    assert state["review_rounds"] == 7 and state["format_repair_rounds"] == 4
    assert state["repair_rounds"] == 3
    saved = json.loads(engine.repair_checkpoint_path.read_text(encoding="utf-8"))
    assert saved["repair_budget"]["review_rounds"] == 9
    assert saved["repair_rounds"] == 3


def test_lowered_review_budget_does_not_reset_used_count(tmp_path):
    state = {"draft": draft(), "scheme": {}, "evidence": [], "review_rounds": 6,
             "reviewer_output": {"issues": ["科学意见保留"], "confidence": "low"}}
    engine = pipeline(tmp_path, lambda **kw: pytest.fail("已超过新配额，不能调用"))
    engine.stage4_repair_options = {"review_rounds": 2}
    _, verdict, *_ = review_design(engine, state, "降低预算")
    assert state["review_rounds"] == 6 and "科学意见保留" in verdict["issues"]


def test_service_failure_budget_can_exceed_old_cap_and_survives_resume(tmp_path):
    calls = []
    engine = pipeline(tmp_path, lambda **kw: calls.append(1) or {
        "issues": ["离线模拟服务失败"], "confidence": "low", "error_type": "reviewer"})
    engine.report_first = True
    engine.stage4_repair_options = {"review_rounds": 10, "review_service_failures": 4}
    state = {"draft": draft(), "scheme": {}, "evidence": []}
    review_design(engine, state, "服务失败预算")
    assert len(calls) == 4 and state["review_service_failures"] == 4
    review_design(engine, state, "服务失败预算")
    assert len(calls) == 4 and state["review_rounds"] == 4


def test_supplement_rounds_queries_and_result_count_are_not_clamped():
    state = retrieval_seed()
    state["retrieval_state"]["supplement_rounds"] = 2
    calls, snapshots = [], []
    engine = SimpleNamespace(retrieval_options={"supplement_rounds": 5, "queries_per_round": 5,
        "results_per_round": 60}, reviewer=SimpleNamespace(llm=client_gains()))
    def retrieve(**kw):
        calls.append(kw)
        assert snapshots[-1]["supplement_rounds"] == 3
        return {"status": "OK", "results": [evidence("new", "XQ温度25 °C")], "search_logs": []}
    engine.retrieve_context = retrieve
    queries = [f"XQ材料 1 C 循环 第{n}组温度" for n in range(5)]
    action = ar.next_action({"retrieval_diagnostics": [diagnostic(queries=queries)]}, state,
                            phase="later_review", config=ar.options(engine))
    assert ar.execute_action(engine, state, action,
                             lambda: snapshots.append(copy.deepcopy(state["retrieval_state"])), ar.options(engine))
    assert calls[0]["queries"] == queries and calls[0]["top_k"] == 60
    assert state["retrieval_state"]["supplement_rounds"] == 3
    assert not ar.execute_action(engine, state, action, lambda: None, ar.options(engine))
    assert len(calls) == 1


def test_writer_and_reviewer_receive_more_than_old_context_cap():
    from auto_battery_research.rag import WriterAgent, ReviewerAgent
    entries = [evidence(f"entry_{n}", ("条件待验证。" * 400) + f"尾段{n}") for n in range(45)]
    selected, metadata = ar.evidence_package(entries, limit=45, char_limit=200000)
    assert len(selected) == 45 and metadata["chars"] > 60000
    prompts = []
    client = SimpleNamespace(available=True, chat=lambda system, user, **kw: prompts.append(user) or '{"issues":[],"confidence":"medium"}')
    writer = WriterAgent(client)
    writer.context_options = {"context_passages": 45, "context_chars": 200000}
    writer.run("只分析证据", {"needs_reasoning": True, "requirements": {"scheme_required": False}}, selected)
    ReviewerAgent(client).run("只分析证据", selected, "适用条件仍需核验。")
    assert len(prompts) == 2 and all("尾段44" in prompt for prompt in prompts)


def test_v3_larger_quota_preserves_request_identity_and_failed_attempts():
    from auto_battery_research.util.stage4_consistency import request, available
    state, snapshots = {}, []
    engine = SimpleNamespace(stage4_repair_options={"v3_requests": {"scientific_review": 8}})
    bind_repair_options(engine, state)
    def save():
        snapshots.append(copy.deepcopy(state))
    def invoke():
        assert snapshots[-1]["requests"][-1]["status"] == "requested"
        return "审核结果"
    for n in range(7):
        request(state, "scientific_review", str(n), save, invoke)
    assert available(state, "scientific_review") == 1
    def fail():
        raise RuntimeError("模拟中断")
    with pytest.raises(RuntimeError):
        request(state, "scientific_review", "failed", save, fail)
    engine.stage4_repair_options = {"v3_requests": {"scientific_review": 12}}
    bind_repair_options(engine, state)
    with pytest.raises(ValueError, match="已占用"):
        request(state, "scientific_review", "failed", save, invoke)
    assert available(state, "scientific_review") == 4
    assert request(state, "scientific_review", "0", save, lambda: pytest.fail("不能重发同稿")) == "审核结果"


def test_patch_generation_budget_allows_third_protocol_attempt():
    from auto_battery_research.tests.test_repair_interfaces import Script, issue, permit_for
    from auto_battery_research.util.repair_interfaces import execute
    text, issues = "错误引用。", [issue("i1", "错误引用")]
    script = Script({"错误引用": "引用适用性待验证"})
    original_chat, attempts = script.chat, []
    def chat(system, user, **kw):
        if kw["trace_tag"] == "writer_patch":
            attempts.append(1)
            if len(attempts) < 3:
                return "{broken"
        return original_chat(system, user, **kw)
    script.chat = chat
    result, _, log = execute(SimpleNamespace(llm=script, repair_options={"patch_attempts": 3}),
        script, text, {}, permit_for(text, issues), issues, [{"passage_id": "p1"}])
    assert len(attempts) == 3 and log["status"] == "APPLIED"
    assert result == "引用适用性待验证。"


def test_writer_protocol_budget_allows_repair_after_three_prior_attempts():
    from auto_battery_research.tests.test_writer_protocol import example
    from auto_battery_research.util.writer_protocol import produce
    data = example()
    data["parameters"] = {"synthesis.temperature": {"value": 900, "unit": "℃",
        "basis": "engineering_proposal", "source": "工程取值"}}
    data["sections"]["goals"]["body"] += "处理温度{{param:synthesis.temperature}}。"
    state = {"raw_response": json.dumps(data), "repair_attempts": 3,
             "repair_history": [{"repair_raw_response": "{}"} for _ in range(3)]}
    calls = []
    def chat(system, user, **kw):
        payload = json.loads(user)
        calls.append(kw["trace_tag"])
        return json.dumps({"base_hash": payload["base_hash"], "parameters": [
            {"path": "synthesis.temperature", "new_path": "process.temperature"}]})
    result = produce(SimpleNamespace(available=True, chat=chat), "s", "u", {}, state,
                     lambda: None, budget={"protocol_rounds": 4})
    # 已返回的拒绝补丁只在本地重放；调高预算后只发出新增的第四次请求。
    assert not result["fallback"], result
    assert calls == ["writer_protocol_repair"] and state["repair_attempts"] == 4


def test_v3_regular_repair_schedule_can_exceed_two_rounds(monkeypatch, tmp_path):
    from auto_battery_research.tests.test_stage4_session_v3 import install
    from auto_battery_research.tests.test_stage4_golden import _build_seeded_pipeline, GOLDEN_GOAL
    from auto_battery_research.tools.rag_adapter import AbrRagAdapter
    from lmllm.RAG.llm_client import LLMClient
    install(monkeypatch)
    original = LLMClient.chat
    calls = []
    def chat(self, system, user, temperature=None, trace_tag=None, *, json_mode=False):
        if trace_tag == "writer_v3_patch":
            calls.append(trace_tag)
            payload = json.loads(user)
            permit = payload["permit"]
            interface = permit["interfaces"][0]
            return json.dumps({"base_hash": permit["base_hash"], "transactions": [{
                "interface_ids": [interface["id"]], "replacements": [{"interface_id": interface["id"],
                "text": f"第{len(calls)}轮候选方案"}], "parameters": {}, "additions": []}]})
        raw = original(self, system, user, temperature=temperature, trace_tag=trace_tag, json_mode=json_mode)
        if trace_tag == "reviewer" and len(calls) < 4:
            data = json.loads(raw)
            quote = "当前为候选方案" if not calls else f"第{len(calls)}轮候选方案"
            data.update(issues=["候选边界待明确"], repair_issues=[{"leaf_id": "goals_p", "quote": quote,
                "reason": "候选边界待明确", "suggestion": "保持未验证边界进一步限定", "parameter_paths": []}])
            return json.dumps(data)
        return raw
    monkeypatch.setattr(LLMClient, "chat", chat)
    config = {"runtime_options": {"stage4_writer": {"contract_version": 3}, "stage4_repair": {
        "v3_regular_rounds": 4, "v3_requests": {"repair": 5, "repair_verify": 5, "scientific_review": 8}}}}
    adapter = AbrRagAdapter(config=config, pipeline=_build_seeded_pipeline(tmp_path))
    task = tmp_path / "task"
    result = adapter.run_rag_design(GOLDEN_GOAL, task, task_goal=GOLDEN_GOAL)
    assert result["success"], result
    assert len(calls) == 4
    checkpoint = json.loads((task / "stage4_repair_checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["regular_rounds"] == 4 and checkpoint["final_rounds"] == 0
    assert "第4轮候选方案" in checkpoint["result"]["final_answer"]


def test_gate_uses_current_yaml_instead_of_exhausted_checkpoint_snapshot(tmp_path):
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
    state = retrieval_seed()
    state.update(draft="当前正文完整", repair_rounds=3, review_rounds=6)
    state["retrieval_state"].update(supplement_rounds=2, pending_diagnostics=[diagnostic()])
    state["retrieval_state"]["budget"] = dict(ar.DEFAULTS, supplement_rounds=2)
    for name, data in (("stage4_repair_checkpoint.json", state), ("design_scheme.json", {"delivery_contract": 1})):
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "design_scheme.md").write_text("当前正文完整", encoding="utf-8")
    checker = RAGDesignChecker()
    checker.config = {"paths": {"output_dir": str(tmp_path)}, "runtime_options": {
        "stage4_retrieval": {"supplement_rounds": 4}, "stage4_repair": {"content_rounds": 5, "review_rounds": 9}}}
    passed, details = checker.do_check()
    assert not passed and details["error_code"] == "STAGE4_RETRIEVAL_PENDING"


@pytest.mark.parametrize("explicit,expected", [(None, 7), (4, 4)])
def test_agent_stage_retry_budget_reads_yaml_and_retains_explicit_override(explicit, expected):
    from auto_battery_research.agent import ABRAgent
    from auto_battery_research.tests.test_abr_agent import OFFLINE_CONFIG
    config = dict(OFFLINE_CONFIG, runtime_options={"max_retries_per_stage": 7})
    agent = ABRAgent(goal="运行预算配置离线测试", config=config, max_stage_retries=explicit,
                     skip_pinn=True, verbose=False, enable_file_log=False)
    assert agent.max_retries == expected
