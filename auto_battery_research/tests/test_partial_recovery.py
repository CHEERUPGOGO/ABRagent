"""P0：未知需求保留验证责任，局部结构化失败不抹去有效记录。"""
import copy
import json
from types import SimpleNamespace

from auto_battery_research.util import research_contract as contract
from auto_battery_research.util.research_experiments import required_tests, fallback_links
from auto_battery_research.util.research_records import collect_research
from auto_battery_research.tests.test_research_contract import research_doc, requirement


def raw_requirements():
    return {"task_type": "design", "scheme_required": True, "items": [
        dict(requirement("采用NCM811", "constraint", binding={"path": "cathode.material", "value": "NCM811", "unit": ""}),
             id="material", verification_needed=False),
        dict(requirement("保持率>74%"), id="retention", quantity={"value": 85, "unit": "%", "operator": ">"}),
    ]}


def test_failed_requirement_keeps_valid_binding_and_unknown_test_responsibility():
    goal = "采用NCM811；保持率>74%。"
    plan = contract.attach_requirements({"requirements": raw_requirements()}, goal)
    req = plan["requirements"]
    assert req["status"] == "partial" and req["task_type"] == "design"
    assert contract.bindings(req)["cathode.material"]["value"] == "NCM811"
    pending = next(r for r in req["items"] if r["quote"] == "保持率>74%")
    assert pending["quantity"] is None and pending["verification_needed"] is None
    doc = {"requirements": req}
    assert [r["id"] for r in required_tests(doc)] == [pending["id"]]
    record = {}
    fallback_links(record, doc)
    assert record["requirement_tests"][0]["status"] == "gap"


def test_repair_locks_valid_requirements_and_only_fixes_failed_item():
    raw = raw_requirements()
    fixed = copy.deepcopy(raw)
    fixed["items"][0]["binding"]["value"] = "LFP"
    fixed["items"][1]["quantity"]["value"] = 74
    calls = []
    def chat(system, user, **kwargs):
        calls.append(json.loads(user))
        return json.dumps(fixed)
    req = contract.attach_requirements({"requirements": raw}, "采用NCM811；保持率>74%。",
        SimpleNamespace(available=True, chat=chat))["requirements"]
    assert len(calls) == 1 and calls[0]["accepted_items"][0]["id"] == "material"
    assert req["status"] == "parsed"
    assert contract.bindings(req)["cathode.material"]["value"] == "NCM811"
    assert req["items"][1]["quantity"]["value"] == 74


def test_failed_repair_preserves_uncovered_user_text_and_does_not_exempt_old_fallback():
    goal = "采用NCM811；保持率>74%；必须给对照。"
    req = contract.attach_requirements({"requirements": raw_requirements()}, goal,
        SimpleNamespace(available=True, chat=lambda *a, **k: "not json"))["requirements"]
    assert "必须给对照" in [r["quote"] for r in req["items"]]
    assert len(required_tests({"requirements": req})) == 2
    old = contract.fallback_requirements(goal)
    for row in old["items"]:
        row["verification_needed"] = False
    assert len(required_tests({"requirements": old})) == len(old["items"])


def two_answer_doc():
    doc = research_doc()
    quote = "采用配方A；比较循环性能"
    req = contract.normalize_requirements({"items": [dict(requirement("采用配方A"), id="a"),
        dict(requirement("比较循环性能"), id="b")]}, quote)
    doc.update(target=quote, requirements=req)
    first = doc["research"]["answers"][0]
    first["requirement_id"] = req["items"][0]["id"]
    second_quote = "循环性能尚需同条件比较，不能直接宣称最优。"
    doc["final_answer"] += "\n" + second_quote
    second = dict(first, requirement_id=req["items"][1]["id"], source_quote=second_quote, response=second_quote)
    doc["research"]["answers"] = [first, second]
    doc["research"]["arguments"] = [{"id": "mechanism", "requirement_ids": [first["requirement_id"]],
        "title": "", "text": first["response"], "source_quote": first["source_quote"], "citations": [],
        "basis": "inference", "parameter_refs": []}]
    return doc


def test_bad_answer_does_not_erase_candidate_argument_or_other_answer():
    doc = two_answer_doc()
    raw = copy.deepcopy(doc["research"])
    raw["answers"][1]["response"] = "性能已经达到99%"
    result = collect_research(raw, doc, doc["ledger_view"]["parameters"])
    assert result["answers"][0] == raw["answers"][0]
    assert result["answers"][1]["status"] == "gap"
    assert result["candidates"] == raw["candidates"] and result["arguments"] == raw["arguments"]
    assert result["selected_candidate_id"] == "a" and result["status"] == "partial"


def test_retry_only_failed_record_keeps_passed_records_and_clears_resolved_error():
    doc = two_answer_doc()
    raw = copy.deepcopy(doc["research"])
    raw["answers"][1]["citations"] = ["missing"]
    first = collect_research(raw, doc, doc["ledger_view"]["parameters"])
    result = collect_research({"answers": [doc["research"]["answers"][1]]}, doc,
        doc["ledger_view"]["parameters"], previous=first)
    assert not result["record_errors"] and result["status"] == "complete"
    assert result["answers"] == doc["research"]["answers"]
    assert result["selected_candidate_id"] == "a" and result["arguments"]


def test_retry_cannot_modify_passed_answer_even_with_another_valid_quote():
    doc = two_answer_doc()
    first = collect_research(doc["research"], doc, doc["ledger_view"]["parameters"])
    changed = copy.deepcopy(doc["research"]["answers"][0])
    changed["response"] = "采用配方A"
    changed["source_quote"] = doc["final_answer"]
    result = collect_research({"answers": [changed]}, doc, doc["ledger_view"]["parameters"], first)
    assert result["answers"][0] == first["answers"][0]
    assert any("禁止" in e["reason"] or "标题" in e["reason"] for e in result["record_errors"])


def test_collection_failure_retains_valid_records_across_calls():
    from auto_battery_research.util.scheme_ledger import collect_parameters
    doc = two_answer_doc()
    raw = copy.deepcopy(doc["research"])
    raw["answers"][1]["response"] = "编造的结果"
    doc.pop("research")
    calls = []
    def chat(system, user, **kwargs):
        calls.append(json.loads(user))
        if len(calls) == 1:
            return json.dumps({"parameters": {}, "research": raw})
        raise RuntimeError("模拟服务中断")
    collect_parameters(doc, SimpleNamespace(available=True, chat=chat))
    assert len(calls) == 2 and calls[1]["accepted_research"]["candidates"]
    assert doc["research"]["answers"][0]["status"] == "answered"
    assert doc["research"]["answers"][1]["status"] == "gap"
    assert doc["research"]["arguments"] and doc["research"]["candidates"]


def test_finalize_missing_answer_only_fills_that_gap():
    doc = two_answer_doc()
    first = copy.deepcopy(doc["research"]["answers"][0])
    doc["research"]["answers"] = [first]
    contract.finalize_research(doc)
    assert doc["research"]["answers"][0] == first
    assert doc["research"]["answers"][1]["status"] == "gap"
    assert doc["research"]["arguments"] and doc["research"]["candidates"]


def test_final_record_validation_removes_only_invalid_argument():
    doc = two_answer_doc()
    bad = copy.deepcopy(doc["research"]["arguments"][0])
    bad.update(id="bad", text="不在原文中的结论")
    doc["research"]["arguments"].append(bad)
    contract.finalize_research(doc)
    assert len(doc["research"]["arguments"]) == 1
    assert doc["research"]["candidates"] and all(a["status"] == "answered" for a in doc["research"]["answers"])
    assert doc["research"]["status"] == "partial"


def test_partial_requirements_reach_writer_without_guessing_materials_or_additives(monkeypatch):
    from auto_battery_research.rag import RAGPipeline
    goal = "采用NCM811；保持率>74%。"
    requirements = contract.attach_requirements({"requirements": raw_requirements()}, goal)["requirements"]
    draft = "\n\n".join("## " + title + "\n" + "保留已声明材料，其他条件需进一步研究。" * 5
        for title in ("目标与设计路线", "推荐组合", "预期关键指标", "可行性依据", "风险与数据缺口"))
    received = []
    pipeline = RAGPipeline.__new__(RAGPipeline)
    pipeline.structured_geometry_enabled = True
    pipeline._reranker_enabled = False
    pipeline.relation_engine = None
    pipeline.planner = SimpleNamespace(run=lambda *a: {"db_type": "literature", "retrieval_queries": [goal],
        "requirements": requirements})
    pipeline.writer_llm = SimpleNamespace(available=False, chat=lambda *a, **k: (_ for _ in ()).throw(AssertionError("未知材料不应触发几何生成")))
    def write(**kwargs):
        received.append(kwargs["scheme"])
        return {"draft_answer": draft}
    pipeline.writer = SimpleNamespace(run=write, llm=pipeline.writer_llm)
    pipeline.reviewer = SimpleNamespace(run=lambda **k: {"confidence": "high", "issues": [],
        "requirement_review": {"source_checked": True, "missing_quotes": [], "misread_ids": [], "unanswered_ids": []}}, llm=pipeline.writer_llm)
    pipeline.retrieve_context = lambda **k: {"results": [{"passage_id": "p1", "source": "离线文献", "text": "材料证据", "score": 1.0}]}
    pipeline._extract_scheme = lambda *a, **k: {"cathode": "LFP", "anode": "li_metal", "electrolyte": "lhce", "additives": ["FEC"]}
    pipeline._extract_energy_claim = lambda *a: None
    pipeline._build_reference_section = lambda e: ""
    monkeypatch.setattr("auto_battery_research.tools.materials_project.augment_mp_evidence", lambda retrieval, plan, top_k: retrieval)
    result = pipeline.run(goal)
    assert len(received) == 1
    assert {k: v for k, v in received[0].items() if k != "component_identities"} == {"cathode": "NCM811"}
    assert received[0]["component_identities"]["cathode"]["parameter_refs"] == ["cathode.material"]
    assert "additives" not in result["scheme"] and result["scheme"]["cathode"] == "NCM811"
