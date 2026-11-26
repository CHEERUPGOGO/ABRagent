"""真实Planner/检索/Writer/Reviewer/适配器/门禁的v3离线golden。"""
import copy
import json

import pytest

from auto_battery_research.tests.test_stage4_golden import _build_seeded_pipeline, GOLDEN_GOAL, GOLDEN_PLAN
from auto_battery_research.tools.rag_adapter import AbrRagAdapter
from auto_battery_research.util.stage4_document import SECTION_IDS
from auto_battery_research.util.stage4_consistency import DIMENSIONS
from auto_battery_research.util.stage4_publication import publication_errors
from auto_battery_research.util.stage4_session import document_errors


def proposal(value, unit="", **extra):
    return {"value": value, "unit": unit, "basis": "engineering_proposal", "source": "明确工程起点，待实验核验", **extra}


def install(monkeypatch, *, scientific_issue=False, final_issue=False, local_issue=False, matched=False):
    from lmllm.RAG.llm_client import LLMClient
    calls = []
    def chat(self, system, user, temperature=None, trace_tag=None, *, json_mode=False):
        calls.append(trace_tag)
        self.last_response = {"finish_reason": "stop", "attempt": 1}
        if trace_tag == "planner":
            plan = copy.deepcopy(GOLDEN_PLAN)
            plan["requirements"] = {"scheme_required": True, "items": [{"id": "r1", "quote": GOLDEN_GOAL,
                "kind": "objective", "description": "设计高比能液态锂金属电池", "status": "clear", "verification_needed": True, "conditions": []}]}
            return json.dumps(plan, ensure_ascii=False)
        if trace_tag == "writer_v3":
            requirement_blob = user.split("【原始课题要求，逐项回应】\n", 1)[1].split("\n", 1)[0]
            rid = json.loads(requirement_blob)["items"][0]["id"]
            fields = {"cathode.material": proposal("NCM811", quantity_kind="text"),
                      "anode.material": proposal("li_metal", quantity_kind="text"),
                      "electrolyte.material": proposal("lhce", quantity_kind="text"),
                      "cathode.L_um": proposal(70, "um"), "cathode.porosity": proposal(.31),
                      "anode.L_um": proposal(50, "um"), "anode.porosity": proposal(0.)}
            if matched:
                fields["anode.material"] = proposal("si_base", quantity_kind="text")
                fields["anode.L_um"] = proposal(110, "um")
                fields["anode.porosity"] = proposal(.56)
            texts = {
                "goals": "按用户目标研究高比能电池，当前为候选方案，目标未获本课题实验验证。",
                "recommendations": "主组使用 {{component:cathode}}、{{component:anode}} 和 {{component:electrolyte}}。正极厚度 {{param:cathode.L_um}}、孔隙率 {{param:cathode.porosity}}；锂箔厚度 {{param:anode.L_um}}、致密孔隙率 {{param:anode.porosity}}。",
                "metrics": "文献容量仅适用于原始工况，不作为本课题性能承诺 {{cite:golden-P1}}。",
                "feasibility": "高镍正极与锂负极的界面选择有文献依据，适用边界需实验核对 {{cite:golden-P4}}。",
                "risks": "首轮应检查枝晶和界面副反应；尚无当前薄锂电芯实测结果，禁止宣称达标。"}
            return json.dumps({"version": 3, "parameters": fields, "sections": {k: {"blocks": [{"type": "paragraph", "id": k + "_p", "text": texts[k]}]} for k in SECTION_IDS},
                "research_links": {"answers": [{"requirement_id": rid, "status": "answered", "verification": "unverified", "response": [{"leaf_id": "goals_p"}]}], "candidates": [],
                    "arguments": [{"id": "argument_one", "requirement_ids": [rid], "basis": "literature", "text": [{"leaf_id": "feasibility_p"}]}]}}, ensure_ascii=False)
        if trace_tag == "reviewer":
            marker = "【待审结构化主方案与研究回答】\n"
            research = json.loads(user.split(marker, 1)[1].split("\n", 1)[0])["research"]
            from auto_battery_research.util.research_support import FIELDS
            verdicts = []
            for collection in FIELDS:
                for row in research[collection]:
                    verdicts.append({"collection": collection, "id": row.get("requirement_id", row.get("id")), "fingerprint": row["support_fingerprint"],
                        "fields": {f: {"status": "supported", "reason": "按完整原句、引用及条件核验"} for f in FIELDS[collection] if row.get(f)}})
            draft = user.split("草稿答案:\n", 1)[1].split("\n\n请严格", 1)[0]
            repair_needed = local_issue and "当前为候选方案" in draft
            repairs = [{"leaf_id": "goals_p", "quote": "当前为候选方案", "reason": "具体候选边界需更明确", "suggestion": "注明首轮候选", "parameter_paths": []}] if repair_needed else []
            return json.dumps({"issues": ["仍有科学争议"] if scientific_issue else (["需明确候选边界"] if repair_needed else []), "repair_issues": repairs, "confidence": "medium",
                "research_support_review": verdicts,
                "requirement_review": {"source_checked": True, "missing_quotes": [], "misread_ids": [], "unanswered_ids": []}}, ensure_ascii=False)
        if trace_tag == "writer_v3_patch":
            payload = json.loads(user)
            permit = payload["permit"]
            interface = permit["interfaces"][0]
            return json.dumps({"base_hash": permit["base_hash"], "transactions": [{"interface_ids": [interface["id"]],
                "replacements": [{"interface_id": interface["id"], "text": "当前为首轮待验证候选方案"}], "parameters": {}, "additions": []}]}, ensure_ascii=False)
        if trace_tag == "repair_verify":
            payload = json.loads(user)
            return json.dumps({"base_hash": payload["base_hash"], "interfaces": [{"id": p["id"], "resolved": True, "reason": "保持原目标和未验证边界"} for p in payload["permit"]["interfaces"]], "new_issues": []}, ensure_ascii=False)
        if trace_tag in ("consistency", "stage5_consistency"):
            return json.dumps({"version": 1, "dimensions": {d: "issues" if final_issue and d == "limitations" else "passed" for d in DIMENSIONS},
                "issues": [{"dimension": "limitations", "leaf_id": None, "quote": "", "reason": "尚有未定位的科学边界疑问", "suggestion": "人工复核", "parameter_paths": []}] if final_issue else []}, ensure_ascii=False)
        raise AssertionError("不应出现额外生成或隐藏请求：" + str(trace_tag))
    monkeypatch.setattr(LLMClient, "available", property(lambda self: True))
    monkeypatch.setattr(LLMClient, "chat", chat)
    return calls


def run(monkeypatch, tmp_path, **flags):
    calls = install(monkeypatch, **flags)
    pipeline = _build_seeded_pipeline(tmp_path)
    config = {"runtime_options": {"stage4_writer": {"contract_version": 3}, "stage4_retrieval": {"enabled": False}}}
    adapter = AbrRagAdapter(config=config, pipeline=pipeline)
    task = tmp_path / "task"
    outcome = adapter.run_rag_design(GOLDEN_GOAL, task, task_goal=GOLDEN_GOAL)
    return outcome, task, calls, adapter


def test_v3_golden_only_complete_writer_and_two_review_roles(monkeypatch, tmp_path):
    result, task, calls, adapter = run(monkeypatch, tmp_path)
    assert result["success"], result
    doc = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    assert doc["writer_contract_version"] == 3
    assert doc["review_status"] == "APPROVED", doc["report_readiness"]
    assert doc["review_passed"]
    assert not publication_errors(task)
    assert not document_errors(doc), document_errors(doc)
    from auto_battery_research.tests.test_stage4_golden import _gate_check
    passed, diagnostic = _gate_check(task, GOLDEN_GOAL, tmp_path)
    assert passed, json.dumps(diagnostic, ensure_ascii=False)
    from auto_battery_research.util.stage4_reporting import render
    report = render(doc)
    assert "高镍正极与锂负极的界面选择有文献依据" in report
    assert calls.count("writer_v3") == 1
    assert calls.count("reviewer") == 1
    assert calls.count("consistency") == 1
    assert not set(calls) & {"geometry_design", "scheme_maintenance", "research_narrative", "research_narrative_review"}
    assert "parameters" not in doc["writer_content"]
    before = list(calls)
    adapter.config["runtime_options"]["stage4_writer"]["contract_version"] = 2
    assert adapter.run_rag_design(GOLDEN_GOAL, task, task_goal=GOLDEN_GOAL)["success"]
    assert calls == before


@pytest.mark.parametrize("flags", [{"scientific_issue": True}, {"final_issue": True}])
def test_terminal_consistency_cannot_override_scientific_issues(monkeypatch, tmp_path, flags):
    result, task, calls, _ = run(monkeypatch, tmp_path, **flags)
    assert result["success"], result
    doc = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    assert doc["review_status"] == "REVIEWED_WITH_ISSUES"
    assert not doc["review_passed"]
    assert doc["report_readiness"]["category"] != "COMPLETE"


def test_v3_rendered_markdown_tamper_fails(monkeypatch, tmp_path):
    result, task, calls, adapter = run(monkeypatch, tmp_path)
    assert result["success"], result
    (task / "design_scheme.md").write_text("用户另行写入", encoding="utf-8")
    assert not adapter.run_rag_design(GOLDEN_GOAL, task, task_goal=GOLDEN_GOAL)["success"]


def test_stage5_geometry_sync_and_once_only_review(monkeypatch, tmp_path):
    result, task, calls, adapter = run(monkeypatch, tmp_path)
    assert result["success"], result
    from auto_battery_research.util.scheme_ledger import align_geometry_parameters
    from auto_battery_research.simulation.geometry_contract import geometry_hash
    from auto_battery_research.util.stage4_publication import publish_document
    from auto_battery_research.util.stage4_session import stage5_final_review
    doc = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    doc["scheme"]["geometry"]["cathode"]["L_um"] = 75
    doc["scheme"]["geometry_hash"] = geometry_hash(doc["scheme"]["geometry"])
    doc["scheme"]["design_revision"] = 1
    align_geometry_parameters(doc, ["cathode.L_um"], "合法工程修订")
    publish_document(task, doc, commit=True, reason="几何同步测试")
    assert "75 μm" in doc["final_answer"] or "75 um" in doc["final_answer"]
    assert doc["consistency_review"]["status"] == "stale"
    assert not publication_errors(task)
    stage5_final_review(task, doc, adapter.pipeline.reviewer_llm)
    assert calls.count("stage5_consistency") == 1
    assert not doc["review_passed"]
    stage5_final_review(task, doc, adapter.pipeline.reviewer_llm)
    assert calls.count("stage5_consistency") == 1
    doc["scheme"]["geometry"]["cathode"]["L_um"] = 80
    doc["scheme"]["geometry_hash"] = geometry_hash(doc["scheme"]["geometry"])
    align_geometry_parameters(doc, ["cathode.L_um"], "复核后再次改值")
    publish_document(task, doc, commit=True, reason="复核后改值")
    stage5_final_review(task, doc, adapter.pipeline.reviewer_llm)
    assert calls.count("stage5_consistency") == 1
    assert doc["consistency_review"]["status"] == "unreviewed"


def test_resume_returned_writer_does_not_repeat_calls(monkeypatch, tmp_path):
    result, task, calls, adapter = run(monkeypatch, tmp_path)
    assert result["success"], result
    state = json.loads((task / "stage4_repair_checkpoint.json").read_text(encoding="utf-8"))
    state["phase"] = "consistency"
    state.pop("result")
    adapter.pipeline.repair_checkpoint_path = task / "stage4_repair_checkpoint.json"
    adapter.pipeline.repair_resume = state
    prior = list(calls)
    resumed = adapter.pipeline.run(GOLDEN_GOAL)
    assert resumed["writer_contract_version"] == 3
    assert calls == prior


def test_content_patch_survives_resume_and_full_scientific_review(monkeypatch, tmp_path):
    result, task, calls, adapter = run(monkeypatch, tmp_path, local_issue=True)
    assert result["success"], result
    doc = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    assert "当前为首轮待验证候选方案" in doc["final_answer"]
    assert doc["content_revision"] == 1
    assert calls.count("writer_v3") == 1
    assert calls.count("writer_v3_patch") == 1
    assert calls.count("repair_verify") == 1
    assert calls.count("reviewer") == 2
    state = json.loads((task / "stage4_repair_checkpoint.json").read_text(encoding="utf-8"))
    state["phase"] = "scientific_review"
    state.pop("result")
    adapter.pipeline.repair_resume = state
    before = list(calls)
    resumed = adapter.pipeline.run(GOLDEN_GOAL)
    assert "当前为首轮待验证候选方案" in resumed["final_answer"]
    assert calls == before


def test_v3_stage4_to_experiment_and_report_no_stage4_regeneration(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from auto_battery_research.util.stage5_execution import finalize_stage5
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    result, task, calls, adapter = run(monkeypatch, tmp_path)
    assert result["success"], result
    stages = [SimpleNamespace(id=i, status="PASSED", skip=False, skip_reason="", allow_skip=False) for i in range(1, 7)]
    mgr = SimpleNamespace(config={"paths": {"output_dir": str(task)}}, root_dir=tmp_path,
        target_goal=GOLDEN_GOAL, stages=stages, is_legacy_task=False, get_task_output_dir=lambda *a, **k: task,
        get_stage_by_id=lambda i: stages[i-1], get_all_stage_journal=lambda: [])
    (task / "stage_journals.json").write_text(json.dumps([{"stage_id": 4, "notes": "离线golden内容编译及审核完成"}], ensure_ascii=False), encoding="utf-8")
    before = list(calls)
    monkeypatch.setattr("auto_battery_research.util.report_recipe_llm._llm_client", lambda *a: None)
    outcome = finalize_stage5(mgr, client=SimpleNamespace(available=False))
    assert outcome["success"], outcome
    assert outcome["key_findings"]["physics"]["status"] == "NOT_APPLICABLE"
    assert not publication_errors(task)
    report = run_synthesis_report(GOLDEN_GOAL, stage_manager=mgr)
    assert report["success"], report
    assert calls == before
    text = (task / "final_research_report.md").read_text(encoding="utf-8")
    assert "高镍正极与锂负极的界面选择有文献依据" in text
    assert "独立一致性终审" not in text
    assert "独立一致性终审" in (task / "final_research_report_supplement.md").read_text(encoding="utf-8")
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    passed, diagnostic = checker.do_check(is_complete=True)
    assert passed, json.dumps(diagnostic, ensure_ascii=False)


def test_matched_geometry_preserves_precise_model_reference_across_resume(monkeypatch, tmp_path):
    result, task, calls, adapter = run(monkeypatch, tmp_path, matched=True)
    assert result["success"], result
    doc = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    assert doc.get("geometry_error") is None, doc.get("geometry_error")
    assert doc["scheme"]["geometry"]["parameter_sources"]["cathode.R_s_um"]["origin"] == "model_baseline"
    assert doc["ledger_view"]["parameters"]["cathode.D_s_m2s"]["model_reference"]["params_hash"]
    assert not document_errors(doc)
    state = json.loads((task / "stage4_repair_checkpoint.json").read_text(encoding="utf-8"))
    state["phase"] = "consistency"
    state.pop("result")
    adapter.pipeline.repair_resume = state
    before = list(calls)
    resumed = adapter.pipeline.run(GOLDEN_GOAL)
    assert resumed.get("geometry_error") is None
    assert calls == before


def test_v3_direct_geometry_tool_publishes_current_content(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from auto_battery_research.tools.workflow_actions import revise_geometry
    from auto_battery_research.util.stage5_attempts import publish_summary
    result, task, calls, adapter = run(monkeypatch, tmp_path, matched=True)
    assert result["success"], result
    doc = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    mgr = SimpleNamespace(config={"runtime_options": {"max_geometry_revisions": 3}}, target_goal=GOLDEN_GOAL,
        get_task_output_dir=lambda *a, **kw: task, is_legacy_task=False)
    outcome = revise_geometry({"cathode": {"L_um": 75}}, reason="明确工程修订", stage_manager=mgr,
        base_revision=0, base_geometry_hash=doc["scheme"]["geometry_hash"])
    assert outcome["success"], outcome
    updated = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    assert updated["scheme"]["geometry"]["cathode"]["L_um"] == 75
    assert "75" in updated["final_answer"]
    assert updated["consistency_review"]["status"] == "stale"
    assert not publication_errors(task)
    publish_summary(mgr)
    assert not publication_errors(task)
