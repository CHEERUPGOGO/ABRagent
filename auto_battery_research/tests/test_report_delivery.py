"""报告优先链路：不匹配物理体系、未审核及实验模型不可用时仍如实交付。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tools.rag_adapter import AbrRagAdapter
from auto_battery_research.util.design_repair import authorize, apply_patch
from auto_battery_research.util.scheme_ledger import digest


def test_patch_wrappers_and_parameter_hash_still_enforce_scope():
    text = "原段落。\n\n保持原样。"
    permit = authorize(text, [{"id": "i", "quote": "原段落", "reason": "理由", "suggestion": "建议"}], {})
    patch = {"base_hash": digest(text), "parameters_hash": digest({}), "edits": [{"issue_id": "i", "block_id": "b0", "text": "修正段落。"}]}
    assert apply_patch(text, {}, permit, {"format": patch})[0].endswith("保持原样。")
    patch["parameters_hash"] = "stale"
    with pytest.raises(ValueError):
        apply_patch(text, {}, permit, patch)


@pytest.mark.parametrize("diagnostic_mode,matched_mode", [(False, False), (True, False), (False, True)])
def test_report_delivery_end_to_end(tmp_path, monkeypatch, diagnostic_mode, matched_mode):
    from auto_battery_research.tests.test_final_design_reporting import report_fixture
    from auto_battery_research.util.stage5_execution import finalize_stage5
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
    from auto_battery_research.checkers.stage5_checker import Stage5Checker
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    mgr, _, _, _ = report_fixture(tmp_path)
    # 只保留本轮离线流水线生成的产物，不使用 fixture 的旧设计与仿真。
    for name in ("design_scheme.json", "design_scheme.md", "pinn_input_spec.json", "pinn_simulation_result.json"):
        (tmp_path / name).unlink()
    raw = {"final_answer": "\n\n".join("## " + name + "\n\n" + "候选机理需要同体系实验验证。" * 8 for name in ("目标与设计路线", "推荐组合", "可行性依据", "预期关键指标", "风险与数据缺口")),
        "scheme": {"cathode": "LRMO", "anode": "li_metal", "electrolyte": "carbonate_ec"},
        "evidence": [{"passage_id": "p1", "source": "离线测试文献", "text": "锂金属与碳酸酯界面需要验证"}],
        "reviewer_output": {"error_type": "reviewer", "issues": ["审核服务失败"], "confidence": "low"},
        "review_completed": False, "review_passed": False, "confidence": "low",
        "geometry_error": "anode.porosity=0.0", "geometry_candidate": {"cathode": {"L_um": 30, "porosity": .3}, "anode": {"L_um": 50, "porosity": 0}},
        "rule_checks": {"confidence": "low", "rule_checks": {"violations": ["界面风险"]}}}
    pipeline = SimpleNamespace(run=lambda *a: copy.deepcopy(raw), writer_llm=None, llm=None)
    if diagnostic_mode:
        raw["scheme"] = {}
    elif matched_mode:
        raw["scheme"].update(cathode="NCM811", anode="si_base")
    adapter = AbrRagAdapter(config=mgr.config, pipeline=pipeline)
    assert adapter.run_rag_design(mgr.target_goal, tmp_path)["success"]
    saved = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    assert saved["scheme"]["geometry"]["anode"]["porosity"] == 0
    assert saved["report_readiness"]["category"] == ("DIAGNOSTIC" if diagnostic_mode else "CONDITIONAL")
    assert saved["review_completed"] is False
    pipeline.run = lambda *a: pytest.fail("可交付方案不应重复运行RAG")
    assert adapter.run_rag_design(mgr.target_goal, tmp_path)["success"]
    stage4 = RAGDesignChecker()
    stage4.on_init(mgr, {"id": 4}, mgr.config)
    assert stage4.do_check()[0]
    mgr.stages[3].status = "FALLBACK"
    monkeypatch.setattr("auto_battery_research.util.report_recipe_llm._llm_client", lambda *a: None)
    assert finalize_stage5(mgr, client=SimpleNamespace(available=False))["success"]
    mgr.stages[4].status = "FALLBACK"
    stage5 = Stage5Checker()
    stage5.on_init(mgr, {"id": 5}, mgr.config)
    assert stage5.do_check()[0]
    result = run_synthesis_report(mgr.target_goal, stage_manager=mgr)
    assert result["success"], result
    text = (tmp_path / "final_research_report.md").read_text(encoding="utf-8")
    label = "问题诊断报告" if diagnostic_mode else "有条件候选报告"
    assert label in text and "审核服务失败" in text
    assert ("物理验证未通过" if matched_mode else "NOT_APPLICABLE") in text
    assert text.count("审核服务失败") == 1
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    ok, diagnostic = checker.do_check(is_complete=True)
    assert ok, diagnostic
    (tmp_path / "final_research_report.md").write_text(text.replace(label, "已验证报告"), encoding="utf-8")
    assert not checker.do_check(is_complete=True)[0]
