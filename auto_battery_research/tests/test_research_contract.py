"""开放式课题识别、主方案绑定与报告覆盖契约的离线验收。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util import research_contract as contract
from auto_battery_research.util import research_reporting as reporting


def requirement(quote, kind="objective", **kwargs):
    return dict(id="r1", quote=quote, kind=kind, subject="研究对象", description=quote,
                conditions=[], status="clear", verification_needed=True, **kwargs)


CASES = [
    ("比较Zn–Na与Cu–Na，抑制氧释放，1 C保持率显著>74%。", ["比较Zn–Na与Cu–Na", "抑制氧释放", "1 C保持率显著>74%"]),
    ("设计低温电解液；禁止含氟组分；优先降低成本。", ["设计低温电解液", "禁止含氟组分", "优先降低成本"]),
    ("降低硫化物固态界面阻抗；加工温度不超过80 °C；只用现有压片机。", ["降低硫化物固态界面阻抗", "加工温度不超过80 °C", "只用现有压片机"]),
    ("对比采购与自制路线，提高产率并降低烧结温度。", ["对比采购与自制路线", "提高产率", "降低烧结温度"]),
    ("解释钠电硬碳首效损失，提出区分机制的实验，不推荐新配方。", ["解释钠电硬碳首效损失", "提出区分机制的实验", "不推荐新配方"]),
    ("软包达到350 Wh/kg；厚度<5 mm；比较寿命与成本。", ["软包达到350 Wh/kg", "厚度<5 mm", "比较寿命与成本"]),
    ("评价XQ-27的ζ响应；在pulsed模式下提高recovery；使用设备Q9。", ["评价XQ-27的ζ响应", "在pulsed模式下提高recovery", "使用设备Q9"]),
    ("文献在0.1 C保持率80%；本任务在1 C显著改善，FEC仅作对照。", ["文献在0.1 C保持率80%", "本任务在1 C显著改善", "FEC仅作对照"]),
]


@pytest.mark.parametrize("goal,quotes", CASES)
def test_open_requirements_accept_unseen_subjects(goal, quotes):
    """模型语义响应以人工逐项标注模拟；验证接线与契约，不冒充模型准确率测量。"""
    from auto_battery_research.rag import PlannerAgent
    items = [dict(requirement(q), id=f"r{n}") for n, q in enumerate(quotes)]
    raw = {"requirements": {"items": items}, "retrieval_queries": [goal]}
    client = SimpleNamespace(available=True, chat=lambda *a, **k: json.dumps(raw, ensure_ascii=False))
    result = PlannerAgent(client).run(goal)
    assert [i["quote"] for i in result["requirements"]["items"]] == quotes
    assert result["requirements"]["status"] == "parsed"
    assert all(i["binding"] is None for i in result["requirements"]["items"])


def test_background_preference_negation_and_quantity_not_invented():
    goal = "文献80%，目标显著改善；优先FEC；禁止Co。"
    items = [dict(requirement("文献80%", "background"), id="a"),
             dict(requirement("目标显著改善"), id="b"),
             dict(requirement("优先FEC", "preference"), id="c"),
             dict(requirement("禁止Co", "constraint"), id="d")]
    value = contract.normalize_requirements({"items": items}, goal)
    assert not contract.bindings(value)
    items[1]["quantity"] = {"value": 85, "unit": "%", "operator": ">"}
    with pytest.raises(ValueError, match="不能由模型补造"):
        contract.normalize_requirements({"items": items}, goal)
    items[1].pop("quantity")
    items[2]["binding"] = {"path": "electrolyte.additives", "value": "FEC"}
    with pytest.raises(ValueError, match="只有明确"):
        contract.normalize_requirements({"items": items}, goal)


def test_local_repair_only_once_and_unparsed_retained():
    calls = []
    client = SimpleNamespace(available=True, chat=lambda *a, **k: calls.append(k) or "{}")
    goal = "分析陌生机制；使用已有设备。"
    result = contract.attach_requirements({"retrieval_queries": [goal]}, goal, client)
    assert len(calls) == 1
    assert result["requirements"]["status"] == "unparsed"
    assert result["requirements"]["source"] == goal


def research_doc():
    goal = "比较配方并说明验证路径"
    req = contract.normalize_requirements({"items": [requirement(goal)]}, goal)
    sentence = "候选甲为探索性首选，采用配方A，需要同批实验比较验证，暂不能确定最优。"
    params = {"cathode.material": {"value": "配方A", "unit": "", "basis": "engineering_proposal", "source": "正文"}}
    research = {"answers": [{"requirement_id": req["items"][0]["id"], "status": "answered", "response": sentence,
        "source_quote": sentence, "verification": "unverified", "citations": [], "reason": "", "next_step": "", "parameter_refs": []}],
        "candidates": [{"id": "a", "name": "候选甲", "rank": None, "scope": "candidate", "source_quote": sentence,
            "summary": "采用{{cathode.material}}", "rationale": "需要同批实验比较验证", "risks": "暂不能确定最优",
            "citations": [], "parameter_refs": ["cathode.material"]}], "arguments": [], "selected_candidate_id": "a"}
    doc = {"target": goal, "requirements": req, "final_answer": sentence, "evidence": [],
           "scheme": {"cathode": "配方A"}, "ledger_view": {"parameters": params}, "reviewer_output": {}}
    doc["research"] = contract.validate_research(research, doc, params)
    return doc


def test_research_rejects_missing_answers_and_unreviewed_claims():
    doc = research_doc()
    broken = copy.deepcopy(doc["research"])
    broken["answers"] = []
    with pytest.raises(ValueError, match="遗漏"):
        contract.validate_research(broken, doc, doc["ledger_view"]["parameters"])
    broken = copy.deepcopy(doc["research"])
    broken["answers"][0]["response"] = "已经达到99%保持率"
    with pytest.raises(ValueError, match="摘自"):
        contract.validate_research(broken, doc, doc["ledger_view"]["parameters"])


def test_report_coverage_and_selection_fingerprint():
    from auto_battery_research.util.report_contract import report_identity, report_context, render_energy, render_selected_design, validate_report_blocks
    doc = research_doc()
    ctx = report_context(doc, None)
    body = reporting.render_summary(doc) + reporting.render_review_appendix(doc)
    body += "<!-- abr-final-design:start -->\n" + render_selected_design(ctx) + "\n<!-- abr-final-design:end -->"
    body += "<!-- abr-energy:start -->\n" + render_energy(ctx, None, 5) + "\n<!-- abr-energy:end -->"
    assert not validate_report_blocks(body, ctx, None, 5)
    assert validate_report_blocks(body.replace("比较配方并说明验证路径", "其他目标"), ctx, None, 5)
    before = report_identity(doc, None, None)
    doc["research"]["selected_candidate_id"] = None
    assert before != report_identity(doc, None, None)
    assert "400" not in body


def test_explicit_binding_is_locked(tmp_path):
    from auto_battery_research.util import scheme_ledger as ledger
    goal = "电解液使用LiPF6"
    item = requirement(goal, "constraint", binding={"path": "electrolyte.salt", "value": "LiPF6", "unit": ""})
    req = contract.normalize_requirements({"items": [item]}, goal)
    doc = {"target": goal, "scheme": {}, "scheme_parameters": contract.bindings(req)}
    ledger.commit_design(tmp_path, doc)
    assert ledger.load(tmp_path)["parameters"]["electrolyte.salt"]["locked"]
    with pytest.raises(ValueError, match="锁定"):
        ledger.update(tmp_path, {"electrolyte.salt": {"value": "LiFSI", "basis": "engineering_proposal", "source": "候选"}}, 1)


def test_material_binding_keeps_user_lock_in_flattened_scheme(tmp_path):
    from auto_battery_research.util import scheme_ledger as ledger
    goal = "采用NCM811"
    req = contract.normalize_requirements({"items": [requirement(goal, "constraint",
        binding={"path": "cathode.material", "value": "NCM811", "unit": ""})]}, goal)
    doc = {"target": goal, "scheme": {"cathode": "NCM811"}, "scheme_parameters": contract.bindings(req)}
    ledger.commit_design(tmp_path, doc)
    assert doc["ledger_view"]["parameters"]["cathode.material"]["locked"]


def test_units_no_pseudo_units_or_duplicate_temperature():
    from auto_battery_research.util.report_narrative import amount, substitute_parameters
    params = {"process.temp": {"value": 298.15, "unit": "K"}, "process.ramp": {"value": 5, "unit": "K/min"},
              "testing.cycles": {"value": 100, "unit": "cycles"}, "material": {"value": "Na", "unit": "element"}}
    body = substitute_parameters("{{process.temp}} K、{{process.ramp}} K/min、{{testing.cycles}}圈、{{material}}", params)
    assert body == "25 ℃、5 ℃/min、100 圈、Na"
    assert amount("process.delta_temperature", {"value": 10, "unit": "K"}) == "10 ℃（温差）"


def test_experiment_main_and_objective_links(tmp_path):
    from auto_battery_research.util import experiment_design as recipes
    from auto_battery_research.util.research_experiments import fallback_links, diagnose_links, validate_parameter
    doc = research_doc()
    record = {}
    fallback_links(record, doc)
    assert not diagnose_links(record, doc)
    record["groups"][0]["changes"] = {"cathode.material": "另一个配方"}
    assert diagnose_links(record, doc)
    with pytest.raises(ValueError, match="主方案"):
        validate_parameter("fabrication.main.formula", {"value": "其他", "unit": "formula", "role": "recipe", "canonical_path": "cathode.nominal_formula"}, doc)
    doc["delivery_contract"] = 1
    result = recipes.generate(tmp_path, doc, None)
    assert isinstance(recipes.validate(result, doc), dict)
    assert "课题目标" in recipes.render(result, doc)


def test_analysis_report_pipeline_and_tamper_gate(tmp_path, monkeypatch):
    from auto_battery_research.tests.test_final_design_reporting import report_fixture
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.util import scheme_ledger as ledger, experiment_design as recipes
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    mgr, _, _, _ = report_fixture(tmp_path)
    doc = research_doc()
    doc["requirements"]["scheme_required"] = False
    doc["requirements"]["task_type"] = "mechanism"
    mgr.target_goal = doc["target"]
    doc["scheme"] = {}
    doc["research"]["candidates"] = []
    doc["research"]["selected_candidate_id"] = None
    doc["research"]["bound_fields"] = {}
    doc["delivery_contract"] = 1
    doc["report_readiness"] = {"category": "CONDITIONAL", "issues": ["缺少直接实证"], "review_status": "REVIEWED", "physics_status": "NOT_APPLICABLE"}
    for name in ("pinn_input_spec.json", "pinn_simulation_result.json", "design_scheme.json"):
        (tmp_path / name).unlink()
    ledger.ensure(tmp_path, doc["target"])
    ledger.commit_design(tmp_path, doc)
    record = recipes.generate(tmp_path, doc, None)
    doc.update(experiment_contract=2, experiment_plan_hash=record["plan_hash"])
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "design_scheme.md").write_text(doc["final_answer"], encoding="utf-8")
    mgr.stages[4].status = "FALLBACK"
    assert run_synthesis_report(mgr.target_goal, stage_manager=mgr)["success"]
    path = tmp_path / "final_research_report.md"
    body = path.read_text(encoding="utf-8")
    assert reporting.render_summary(doc) in body
    assert "400 Wh/kg" not in body and "缺少直接实证" in body
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    assert checker.do_check(is_complete=True)[0]
    path.write_text(body.replace("当前回答", "遗漏回答"), encoding="utf-8")
    assert not checker.do_check(is_complete=True)[0]


def test_research_rejects_stale_material_after_scheme_update():
    doc = research_doc()
    doc["ledger_view"]["parameters"]["cathode.material"]["value"] = "另一个配方"
    assert any("旧主方案" in e for e in contract.document_errors(doc))
