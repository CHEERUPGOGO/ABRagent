"""自适应检索离线验收：严格首次窗口、证据实际可见、停止及恢复预算。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util import adaptive_retrieval as ar


def evidence(pid, text="XQ材料在1 C循环条件下的响应", source=None):
    return {"passage_id": pid, "source": source or "paper_" + pid, "text": text, "metadata": {}}


def seed(count=4, fresh=True):
    s = {"question": "比较XQ材料循环与界面", "plan": {"requirements": {"items": [
        {"id": f"r{i}", "kind": "objective", "status": "clear", "description": f"XQ研究目标{i}", "conditions": []}
        for i in range(count)]}, "retrieval_queries": ["XQ初始检索"], "db_type": "literature", "needs_reasoning": False},
        "db_type": "literature", "evidence": [evidence("p0")], "retrieval": {"status": "OK"}, "scheme": {"cathode": "XQ"}}
    ar.init_state(s, fresh=fresh)
    return s


def diagnostic(rid="r0", action="supplement", cause="evidence_gap", queries=None, ids=None):
    return {"requirement_id": rid, "action": action, "cause": cause, "gap": "XQ 1 C循环缺少条件",
            "reason": "目标需要可比条件", "evidence_ids": ids or [], "queries": queries or ["XQ材料 1 C循环 保持率 条件"],
            "db_type": "literature", "focus_component": None, "focus_labels": [], "use_label_filter": False}


def defect(affected=None, kind="wrong_subject"):
    return {"type": kind, "affected_requirement_ids": affected or ["r0", "r1"], "original_problem": "首次查询研究对象误判为YQ",
        "correction": "按XQ材料分别检索循环与界面", "archive_cannot_resolve": True, "evidence_ids": ["p0"],
        "queries": ["XQ材料 循环条件", "XQ材料 界面机制"], "db_type": "literature", "focus_component": None,
        "focus_labels": [], "use_label_filter": False}


def client_gains(rid="r0", pid="new", kind="conditions"):
    return SimpleNamespace(available=True, chat=lambda *a, **k: json.dumps({"gains": [{"requirement_id": rid,
        "passage_id": pid, "kind": kind, "quote": "25 °C", "reason": "补齐目标的实验温度"}]}))


@pytest.mark.parametrize("wrap", [
    lambda text: text,
    lambda text: "```json\n" + text + "\n```",
    lambda text: "首次检索评估如下。\n```json\n" + text + "\n```\n仍需核对研究条件。",
    lambda text: "本轮建议如下：\n" + text + "\n请保留证据边界。",
])
def test_diagnostic_json_accepts_complete_response_with_surrounding_text(wrap):
    review = {"retrieval_initial_defect": None, "retrieval_diagnostics": [diagnostic(ids=["p0"])]}
    assert ar._decode(wrap(json.dumps(review, ensure_ascii=False))) == review


@pytest.mark.parametrize("raw", [
    '{"retrieval_diagnostics": [], "retrieval_diagnostics": [{}]}',
    '{"retrieval_diagnostics": []}\n{"retrieval_diagnostics": [{}]}',
    '```json\n{"retrieval_diagnostics": []}\n```\n```json\n{"gains": []}\n```',
    '说明：{"retrieval_diagnostics": [{"action":"supplement"}',
    '```json\n{"broken": , "nested": {"retrieval_diagnostics": []}}\n```',
    '[{"retrieval_diagnostics": []}]',
])
def test_diagnostic_json_does_not_choose_ambiguous_or_broken_payload(raw):
    with pytest.raises(ValueError):
        ar._decode(raw)


def test_diagnostic_json_keeps_code_fences_inside_parameter_text():
    review = {"retrieval_diagnostics": [], "reason": '说明包含```json {"examples": []}```和[p0]引用'}
    assert ar._decode(json.dumps(review, ensure_ascii=False)) == review


@pytest.mark.parametrize("cause", ["evidence_gap", "missing_conditions", "source_gap", "conflicting_evidence"])
def test_related_evidence_does_not_suppress_explicit_supplement(cause):
    s = seed()
    review = {"retrieval_diagnostics": [diagnostic(cause=cause, ids=["p0"])]}
    action = ar.next_action(review, s, phase="later_review", config=ar.DEFAULTS)
    assert action["action"] == "supplement"
    assert action["queries"] == review["retrieval_diagnostics"][0]["queries"]
    assert "p0" in s["retrieval_state"]["preferred_ids"]
    assert s["retrieval_state"]["window_open"] is False


def test_related_evidence_gaps_can_share_one_query_batch():
    s = seed()
    ds = [diagnostic(f"r{i}", ids=["p0"], queries=[f"XQ材料 候选{i} 独立比较数据"]) for i in range(3)]
    action = ar.next_action({"retrieval_diagnostics": ds}, s, phase="first_review", config=ar.DEFAULTS)
    assert action["action"] == "supplement"
    assert action["requirement_ids"] == ["r0", "r1", "r2"]
    assert action["queries"] == [d["queries"][0] for d in ds]


def test_supplement_without_related_evidence_field_can_execute():
    s = seed()
    row = diagnostic()
    row.pop("evidence_ids")
    searches = []
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(llm=client_gains()),
        retrieve_context=lambda **kwargs: searches.append(kwargs) or {
            "status": "OK", "results": [evidence("new", "XQ在25 °C测试")]})
    assert ar.after_review(pipeline, s, {"retrieval_diagnostics": [row]}, lambda: None)
    assert len(searches) == 1
    assert s["retrieval_state"]["rounds"][0]["status"] == "IMPROVED"


def test_related_evidence_supplement_respects_persisted_budget():
    s = seed()
    s["retrieval_state"]["supplement_rounds"] = ar.DEFAULTS["supplement_rounds"]
    pipeline = SimpleNamespace(retrieve_context=lambda **kwargs: pytest.fail("预算耗尽时不得继续检索"))
    assert not ar.after_review(pipeline, s, {"retrieval_diagnostics": [diagnostic(ids=["p0"])]}, lambda: None)
    assert s["retrieval_state"]["supplement_rounds"] == ar.DEFAULTS["supplement_rounds"]
    assert "针对性补检索预算耗尽" in s["retrieval_state"]["stopped_gaps"].values()


@pytest.mark.parametrize("action,cause", [("reuse", "evidence_gap"), ("supplement", "context_omission")])
def test_sufficient_or_omitted_existing_evidence_still_reuses_archive(action, cause):
    s = seed()
    result = ar.next_action({"retrieval_diagnostics": [diagnostic(action=action, cause=cause, ids=["p0"])]},
                            s, phase="later_review", config=ar.DEFAULTS)
    assert result["action"] == "reuse"
    assert "p0" in s["retrieval_state"]["preferred_ids"]
    assert s["retrieval_state"]["supplement_rounds"] == 0


def test_initial_wrapped_diagnostics_reach_search_gain_review_and_repair_context():
    s = seed()
    review = {"retrieval_initial_defect": None, "retrieval_diagnostics": [diagnostic(ids=["p0"])]}
    calls, searches, saves = [], [], []
    def chat(system, user, **kwargs):
        calls.append(kwargs["trace_tag"])
        if kwargs["trace_tag"] == "retrieval_assess":
            return "首次检索无整体策略错误，补充比较条件。\n```json\n" + json.dumps(review) + "\n```"
        return '证据增益如下。\n```json\n' + client_gains().chat() + '\n```'
    def search(**kwargs):
        assert saves[-1]["inflight"] == 0 and saves[-1]["supplement_rounds"] == 1
        searches.append(kwargs)
        return {"status": "OK", "results": [evidence("new", "XQ在25 °C测试")]}
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(llm=SimpleNamespace(available=True, chat=chat)),
                               retriever=SimpleNamespace(), retrieve_context=search)
    ar.initial_assessment(pipeline, s, lambda: saves.append(copy.deepcopy(s["retrieval_state"])))
    state = s["retrieval_state"]
    assert calls == ["retrieval_assess", "retrieval_gain"]
    assert len(searches) == 1 and searches[0]["queries"] == review["retrieval_diagnostics"][0]["queries"]
    assert state["initial_assessment"] == review and state["rounds"][0]["status"] == "IMPROVED"
    assert not state["diagnostics"]
    assert ar.select_context(s, purpose="repair")[0]["passage_id"] == "new"
    restored = json.loads(json.dumps(s))
    ar.initial_assessment(pipeline, restored, lambda: None)
    ar.after_review(pipeline, restored, review, lambda: None)
    assert len(searches) == 1
    assert restored["retrieval_state"]["supplement_rounds"] == 1


def test_related_evidence_suggestion_runs_real_local_bm25_retrieval():
    from auto_battery_research.rag import RAGPipeline
    from lmllm.RAG.agents import RetrievalAgent
    from lmllm.RAG.bm25_kb import BM25KnowledgeBase
    kb = BM25KnowledgeBase()
    kb.add_passage("new", "XQ材料在25 °C进行1 C循环测试，报告保持率及测试条件。", "paper_new")
    for i in range(4):
        kb.add_passage(f"other{i}", "无关背景材料", f"paper_other{i}")
    kb.build_index()
    # 只初始化内存BM25，仍调用生产检索入口，避免测试建立持久Chroma目录。
    pipeline = RAGPipeline.__new__(RAGPipeline)
    pipeline.retriever = RetrievalAgent(kb)
    pipeline.reviewer = SimpleNamespace(llm=client_gains())
    s = seed()
    review = {"confidence": "low", "retrieval_diagnostics": [diagnostic(ids=["p0"])]}
    assert ar.after_review(pipeline, s, review, lambda: None)
    event = s["retrieval_state"]["rounds"][0]
    assert event["status"] == "IMPROVED"
    assert "new" in event["added_ids"]
    assert event["query_logs"][0]["query"] == review["retrieval_diagnostics"][0]["queries"][0]
    assert any(item.get("status") == "OK" for item in event["channel_diagnostics"])
    assert ar.select_context(s, purpose="repair")[0]["passage_id"] == "new"


def test_severe_initial_defect_only_and_first_window_closes():
    s = seed()
    allowed = ar.next_action({"retrieval_initial_defect": defect()}, s, phase="initial", config=ar.DEFAULTS)
    assert allowed["action"] == "rebuild"
    rejected = ar.next_action({"retrieval_initial_defect": defect(["r0"])}, seed(), phase="initial", config=ar.DEFAULTS)
    assert rejected["action"] == "stop"
    s["retrieval_state"]["window_open"] = False
    assert ar.next_action({"retrieval_initial_defect": defect()}, s, phase="later_review", config=ar.DEFAULTS)["action"] == "stop"
    for cause in ("wrong_source", "systematic_filter"):
        assert not ar.rebuild_allowed(defect(kind=cause), seed(1), seed(1)["retrieval_state"], ar.DEFAULTS)[0]
    one = seed(1)
    assert ar.rebuild_allowed(defect(["r0"]), one, one["retrieval_state"], ar.DEFAULTS)[0]


def test_low_confidence_mixed_review_does_not_mask_local_gap():
    s = seed()
    review = {"confidence": "low", "error_type": "writing", "issues": ["格式错误", "条件缺失"],
              "retrieval_diagnostics": [diagnostic(action="stop", cause="format"), diagnostic("r1")]}
    action = ar.next_action(review, s, phase="first_review", config=ar.DEFAULTS)
    assert action["action"] == "supplement" and action["requirement_ids"] == ["r1"]
    assert action["queries"] == ["XQ材料 1 C循环 保持率 条件"]


@pytest.mark.parametrize("cause", ["format", "protocol", "calculation", "binding", "user_decision", "measurement", "misreading"])
def test_non_retrieval_failures_never_start_search(cause):
    s = seed()
    action = ar.next_action({"retrieval_diagnostics": [diagnostic(cause=cause)]}, s, phase="first_review", config=ar.DEFAULTS)
    assert action["action"] not in ("supplement", "rebuild")


def test_archived_evidence_after_25_is_selected_without_search():
    s = seed()
    s["evidence"] = [evidence(str(i), "无关内容") for i in range(40)]
    s["evidence"].append(evidence("key", "XQ所需温度25 °C"))
    s["retrieval_state"]["working_ids"] = [e["passage_id"] for e in s["evidence"]]
    review = {"retrieval_diagnostics": [diagnostic(action="reuse", cause="context_omission", ids=["key"])]}
    action = ar.next_action(review, s, phase="first_review", config=ar.DEFAULTS)
    assert action["action"] == "reuse"
    pack = ar.select_context(s, purpose="repair")
    assert pack[0]["passage_id"] == "key" and len(pack) <= 25
    assert len(s["evidence"]) == 41


def test_new_evidence_gain_enters_repair_and_keeps_archive_unchanged():
    s = seed()
    state = s["retrieval_state"]
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(llm=client_gains()), retriever=SimpleNamespace(),
        retrieve_context=lambda **k: {"results": [evidence("new", "XQ在25 °C测试")], "status": "OK"})
    before = copy.deepcopy(s["scheme"])
    action = ar.next_action({"retrieval_diagnostics": [diagnostic()]}, s, phase="first_review", config=ar.DEFAULTS)
    saves = []
    assert ar.execute_action(pipeline, s, action, lambda: saves.append(copy.deepcopy(state)), ar.DEFAULTS)
    assert state["supplement_rounds"] == 1 and state["window_open"] is False
    assert state["rounds"][0]["status"] == "IMPROVED" and len(s["evidence"]) == 2
    assert ar.select_context(s, purpose="repair")[0]["passage_id"] == "new"
    assert s["scheme"] == before and saves[0]["inflight"] == 0


def test_no_gain_stops_same_gap_and_restarts_do_not_reset():
    s = seed()
    calls = []
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(llm=SimpleNamespace(available=True, chat=lambda *a, **k: '{"gains":[]}')),
        retrieve_context=lambda **k: calls.append(k) or {"results": [evidence("p0")], "status": "OK"})
    review = {"retrieval_diagnostics": [diagnostic()]}
    ar.after_review(pipeline, s, review, lambda: None)
    restored = json.loads(json.dumps(s))
    ar.after_review(pipeline, restored, review, lambda: None)
    assert len(calls) == 1 and restored["retrieval_state"]["supplement_rounds"] == 1
    assert restored["retrieval_state"]["rounds"][0]["status"] == "NO_GAIN"
    assert restored["retrieval_state"]["stopped_gaps"]


def test_query_signatures_budget_and_slogans():
    s = seed()
    state = s["retrieval_state"]
    spec = ar._search_spec(diagnostic(), s)
    state["attempted_queries"].append(ar._query_signature("XQ材料 1 C循环 保持率 条件", spec))
    assert not ar.query_list(["XQ材料 1 C循环 保持率 条件", "（覆盖≠性能达标）"], state, spec, 3)
    state["supplement_rounds"] = 2
    d = diagnostic(queries=["XQ材料 2 C循环 保持率"])
    assert ar.next_action({"retrieval_diagnostics": [d]}, s, phase="later_review", config=ar.DEFAULTS)["action"] == "stop"
    assert not state["window_open"]


def test_services_and_empty_results_are_not_conflated():
    assert ar.channel_status([{"status": "ERROR"}], []) == "ERROR"
    assert ar.channel_status([{"status": "ERROR"}, {"status": "OK"}], []) == "EMPTY"
    assert ar.channel_status([{"status": "ERROR"}, {"status": "OK"}], [evidence("p")]) == "DEGRADED"
    s = seed()
    s["retrieval"]["status"] = "ERROR"
    assert not ar.rebuild_allowed(defect(), s, s["retrieval_state"], ar.DEFAULTS)[0]
    assert ar.next_action({"retrieval_diagnostics": [diagnostic()]}, s, phase="first_review", config=ar.DEFAULTS)["action"] == "stop"
    s = seed()
    calls = []
    def fail(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("服务不可用")
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(llm=None), retrieve_context=fail)
    action = ar.next_action({"retrieval_diagnostics": [diagnostic()]}, s, phase="first_review", config=ar.DEFAULTS)
    assert not ar.execute_action(pipeline, s, action, lambda: None, ar.DEFAULTS)
    assert len(calls) == 2 and s["retrieval_state"]["service_retries"] == 1
    assert calls[0] == calls[1]


def test_independent_source_and_repeated_gain_are_rejected():
    s = seed()
    same = evidence("new", "条件25 °C", source="paper_p0")
    d = diagnostic(cause="source_gap", ids=["p0"])
    action = ar.next_action({"retrieval_diagnostics": [d]}, s, phase="first_review", config=ar.DEFAULTS)
    gain = {"requirement_id": "r0", "kind": "independent_source", "passage_id": "new", "quote": "25 °C", "reason": "新来源"}
    assert not ar.validate_gains({"gains": [gain]}, action, s, [same])
    gain["kind"] = "conditions"
    assert ar.validate_gains({"gains": [gain]}, action, s, [same])
    s["retrieval_state"]["gain_signatures"].append(ar.digest([gain[k] for k in ("requirement_id", "kind", "passage_id", "quote")]))
    assert not ar.validate_gains({"gains": [gain]}, action, s, [same])


def test_source_changed_under_same_id_never_overwrites():
    s = seed()
    original = copy.deepcopy(s["evidence"])
    candidates, added = ar.merge_archive(s, [evidence("p0", "另一段不同原文")])
    assert not candidates and not added and s["evidence"] == original
    assert s["retrieval_state"]["evidence_conflicts"]


def test_first_review_window_closes_without_action_and_legacy_stays_closed():
    s = seed()
    ar.after_review(SimpleNamespace(), s, {"issues": []}, lambda: None)
    assert not s["retrieval_state"]["window_open"]
    assert ar.next_action({"retrieval_initial_defect": defect()}, s, phase="later_review", config=ar.DEFAULTS)["action"] == "stop"
    assert seed(fresh=False)["retrieval_state"]["window_open"] is False


def test_interrupted_search_is_not_automatically_reissued():
    s = seed()
    s["retrieval_state"].update(inflight=0, supplement_rounds=1, attempted_queries=["issued"])
    s["retrieval_state"]["rounds"] = [{"action": "supplement", "status": "RUNNING"}]
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(llm=None))
    ar.after_review(pipeline, s, {"issues": []}, lambda: None)
    assert s["retrieval_state"]["rounds"][0]["status"] == "INTERRUPTED"
    assert s["retrieval_state"]["supplement_rounds"] == 1


def test_rebuild_working_set_excludes_old_archive_but_preserves_citations():
    s = seed()
    act = ar.next_action({"retrieval_initial_defect": defect()}, s, phase="initial", config=ar.DEFAULTS)
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(llm=client_gains()),
        retrieve_context=lambda **k: {"results": [evidence("new", "XQ正确条件25 °C")], "status": "OK"})
    assert ar.execute_action(pipeline, s, act, lambda: None, ar.DEFAULTS)
    assert s["retrieval_state"]["working_ids"] == ["new"] and len(s["evidence"]) == 2
    pack = ar.select_context(s, purpose="repair", text="旧引用仍须核验[p0]")
    assert {e["passage_id"] for e in pack} == {"p0", "new"}


def test_initial_assessment_is_persistent_and_only_once():
    s = seed()
    calls = []
    client = SimpleNamespace(available=True, chat=lambda *a, **k: calls.append(k["trace_tag"]) or '{"retrieval_diagnostics":[]}')
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(llm=client))
    ar.initial_assessment(pipeline, s, lambda: None)
    ar.initial_assessment(pipeline, json.loads(json.dumps(s)), lambda: None)
    assert calls == ["retrieval_assess"]


def test_context_manifest_records_oversized_cited_passage():
    pack, metadata = ar.evidence_package([evidence("p", "超长" * 100)], text="引用[p]", char_limit=10)
    assert not pack and metadata["omitted_pinned_ids"] == ["p"]


def test_repair_and_verifier_receive_the_same_new_evidence_package():
    from auto_battery_research.util.design_repair import authorize
    from auto_battery_research.util.repair_interfaces import execute
    from auto_battery_research.tests.test_repair_interfaces import Script
    es = [evidence(f"p{i}", "无关背景") for i in range(40)] + [evidence("new", "支持修复的新证据")]
    text = "需要补证据的论断。"
    issues = [{"id": "i1", "quote": "需要补证据的论断", "reason": "需核对[new]", "suggestion": "保留验证边界[new]", "parameter_paths": []}]
    permit = authorize(text, issues, {})
    permit["evidence_ids"] = [e["passage_id"] for e in es]
    script = Script({"需要补证据的论断": "有新依据仍需验证[new]"})
    _, _, event = execute(SimpleNamespace(llm=script), script, text, {}, permit, issues, es)
    packages = [c[1]["evidence"] for c in script.calls]
    assert packages[0] == packages[1]
    assert packages[0][0]["passage_id"] == "new"
    assert event["evidence_context"]["selected_ids"][0] == "new"


def test_cached_initial_retrieval_resume_skips_planner_and_full_retrieval(tmp_path, monkeypatch):
    from auto_battery_research.tests.test_stage4_golden import _build_seeded_pipeline, _install_scripted_llm, GOLDEN_WRITER_DRAFT, GOLDEN_GOAL
    _install_scripted_llm(monkeypatch, GOLDEN_WRITER_DRAFT)
    pipeline = _build_seeded_pipeline(tmp_path)
    pipeline.structured_geometry_enabled = False
    first = pipeline.run(GOLDEN_GOAL)
    saved = {"question": GOLDEN_GOAL, "plan": first["plan"], "db_type": "literature",
        "retrieval": first["retrieval"], "evidence": first["evidence"], "retrieval_state": first["retrieval_state"]}
    pipeline.retrieval_resume = saved
    pipeline.planner.run = lambda *a: pytest.fail("恢复检索检查点不应重跑Planner")
    pipeline.retrieve_context = lambda **k: pytest.fail("恢复检查点不应再次完整召回")
    second = pipeline.run(GOLDEN_GOAL)
    assert second["evidence"] == first["evidence"]
    assert second["retrieval_state"]["initial_assessed"]


def test_same_source_neighbours_only_use_loaded_corpus():
    from auto_battery_research.rag import RAGPipeline
    from lmllm.RAG.bm25_kb import BM25KnowledgeBase
    kb = BM25KnowledgeBase()
    kb.add_passage("near", "XQ实验温度25 °C，倍率1 C", "paper_p0", {"paragraph_index": 11})
    kb.add_passage("wrong", "XQ实验条件不同", "other_paper", {"paragraph_index": 11})
    s = seed()
    s["evidence"][0]["metadata"]["paragraph_index"] = 10
    pipeline = SimpleNamespace(retriever=SimpleNamespace(kb=kb))
    d = diagnostic(cause="missing_conditions", ids=["p0"])
    action = ar.next_action({"retrieval_diagnostics": [d]}, s, phase="first_review", config=ar.DEFAULTS)
    assert [e["passage_id"] for e in ar.source_neighbours(pipeline, s, action)] == ["near"]


def test_initial_service_retry_is_not_a_new_retrieval_strategy():
    s = seed()
    s["retrieval"] = {"status": "ERROR"}
    calls = []
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(llm=None), retrieve_context=lambda **k: calls.append(k) or {"status": "OK", "results": [evidence("recovered")]})
    ar.initial_assessment(pipeline, s, lambda: None)
    assert len(calls) == 1 and calls[0]["queries"] == ["XQ初始检索"]
    assert s["retrieval_state"]["service_retries"] == 1 and s["retrieval_state"]["supplement_rounds"] == 0
    assert s["retrieval_state"]["rebuild_rounds"] == 0
    ar.initial_assessment(pipeline, s, lambda: None)
    assert len(calls) == 1


def test_same_source_query_batch_covers_multiple_requirements():
    s = seed()
    ds = [diagnostic(f"r{i}", queries=[f"XQ目标{i} 测试条件"]) for i in range(4)]
    action = ar.next_action({"retrieval_diagnostics": ds}, s, phase="first_review", config=ar.DEFAULTS)
    assert len(action["queries"]) == 3 and len(action["requirement_ids"]) == 3


def test_execute_rebuild_checks_window_again_and_does_not_loop():
    s = seed()
    action = ar.next_action({"retrieval_initial_defect": defect()}, s, phase="initial", config=ar.DEFAULTS)
    s["retrieval_state"]["window_open"] = False
    pipeline = SimpleNamespace(retrieve_context=lambda **k: pytest.fail("窗口关闭后不能执行重建"))
    assert not ar.execute_action(pipeline, s, action, lambda: None, ar.DEFAULTS)
    assert s["retrieval_state"]["rebuild_rounds"] == 0


def test_malformed_state_does_not_reset_budget():
    s = seed()
    s["retrieval_state"]["supplement_rounds"] = -1
    with pytest.raises(ValueError, match="不能重置"):
        ar.init_state(s)


def test_pending_valid_supplement_is_gated_before_advancing(tmp_path):
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
    from auto_battery_research.tests.test_final_design_reporting import report_fixture
    mgr, _, _, _ = report_fixture(tmp_path)
    s = seed()
    s["retrieval_state"]["pending_diagnostics"] = [diagnostic()]
    assert ar.has_pending(s)
    (tmp_path / "stage4_repair_checkpoint.json").write_text(json.dumps(s), encoding="utf-8")
    (tmp_path / "design_scheme.json").write_text(json.dumps({"delivery_contract": 1}), encoding="utf-8")
    checker = RAGDesignChecker()
    checker.on_init(mgr, {"id": 4}, mgr.config)
    passed, result = checker.do_check()
    assert not passed and result["error_code"] == "STAGE4_RETRIEVAL_PENDING"


def test_preselected_new_evidence_is_not_displaced_by_old_citations():
    from auto_battery_research.util.design_repair import authorize
    from auto_battery_research.util.repair_interfaces import execute
    from auto_battery_research.tests.test_repair_interfaces import Script
    archive = [evidence(f"p{i}", "旧背景") for i in range(30)] + [evidence("new", "新增条件")]
    text = "需要修复。\n\n" + " ".join(f"[p{i}]" for i in range(30))
    issues = [{"id": "i", "quote": "需要修复", "reason": "局部证据不够", "suggestion": "标明边界"}]
    package, selected = ar.evidence_package(archive, text=text, preferred_ids=["new"], limit=25)
    assert package[0]["passage_id"] == "new"
    permit = authorize(text, issues, {})
    permit["evidence_ids"] = [e["passage_id"] for e in archive]
    script = Script({"需要修复": "仍待验证"})
    execute(SimpleNamespace(llm=script), script, text, {}, permit, issues, package, evidence_selection=selected)
    assert all(c[1]["evidence"][0]["passage_id"] == "new" for c in script.calls)


def test_cache_resume_rejects_changed_scheme_or_corpus():
    s = seed()
    s["parameters"] = {"cathode.material": {"value": "XQ"}}
    provenance = {"corpus": {"hash": "v1"}, "vector_index": {"hash": "v1"}, "rules_version": "r1"}
    doc = {"retrieval_state": copy.deepcopy(s["retrieval_state"]), "evidence": copy.deepcopy(s["evidence"]),
           "ledger_view": {"parameters": copy.deepcopy(s["parameters"])}, "provenance": provenance}
    assert ar.cache_resume_matches(s, doc, provenance)
    changed = copy.deepcopy(provenance)
    changed["corpus"]["hash"] = "v2"
    assert not ar.cache_resume_matches(s, doc, changed)
    doc["ledger_view"]["parameters"]["cathode.material"]["value"] = "YQ"
    assert not ar.cache_resume_matches(s, doc, provenance)


def test_format_deliverables_do_not_count_as_core_evidence_requirements():
    s = seed(3)
    s["plan"]["requirements"]["items"][2].update(kind="deliverable", evidence_needed=False, description="输出Markdown")
    assert set(ar.requirements(s)) == {"r0", "r1"}


def test_disabled_supplement_budget_does_not_block_cache():
    s = seed()
    s["retrieval_state"]["budget"] = dict(ar.DEFAULTS, supplement_rounds=0)
    s["retrieval_state"]["pending_diagnostics"] = [diagnostic()]
    assert not ar.has_pending(s)


def test_failed_rebuild_does_not_promote_bad_initial_evidence():
    s = seed()
    action = ar.next_action({"retrieval_initial_defect": defect()}, s, phase="initial", config=ar.DEFAULTS)
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(llm=SimpleNamespace(available=True, chat=lambda *a, **k: '{"gains":[]}')),
        retrieve_context=lambda **k: {"status": "EMPTY", "results": []})
    assert not ar.execute_action(pipeline, s, action, lambda: None, ar.DEFAULTS)
    assert s["retrieval_state"]["working_ids"] == []
    assert len(s["evidence"]) == 1
