"""选材分析链与主方案重点排版：真实原句、引用、参数及审核边界。"""
import copy
import re

import pytest

from auto_battery_research.tests.test_research_material_rationale import archived_doc
from auto_battery_research.tests.test_report_narrative import example
from auto_battery_research.util.research_reporting import render_research
from auto_battery_research.util.material_reasoning import archived_decision_chain, render_claim_chain
from auto_battery_research.util.report_narrative import render_design, amount


def chain_doc():
    doc = archived_doc()
    doc["final_answer"] += '''

## 完整论证链
需求（抑制氧释放）→ 瓶颈（层状结构失稳）→ 策略（双位点分离作用）→ A位选Na（同体系文献线索 [na]）→ B位选Cu（钠电先例，跨体系仍需验证 [cu]）→ 合成（温度999 ℃）→ 指标判定（保持率需同条件测量）。
'''
    doc["reviewer_output"] = {"issues": ["[cu] 为钠电体系，不能证明锂电性能。"]}
    return doc


def test_actual_decision_chain_retains_material_link_and_evidence_limitations():
    doc = chain_doc()
    before = copy.deepcopy(doc)
    body = render_research(doc)
    assert "A位选Na（同体系文献线索 [na]）" in body
    assert "B位选Cu（钠电先例，跨体系仍需验证 [cu]）" in body
    assert "该链条仍有证据条件" in body
    assert body.index("需求（抑制氧释放）") < body.index("瓶颈（层状结构失稳）") < body.index("A位选Na（")
    assert "温度999 ℃" not in archived_decision_chain(doc)
    assert "**原文论证（引用可定位）**" not in body and "**推断与待验证判断**" not in body
    assert "#### 2.1." not in body and "课题回应1" not in body
    assert doc == before


def test_unknown_reference_cannot_be_imported_through_decision_chain():
    doc = chain_doc()
    doc["final_answer"] = doc["final_answer"].replace("[cu]）→ 合成", "[unknown]）→ 合成")
    assert not archived_decision_chain(doc)


def test_single_component_claim_has_no_repeated_decision_stage_heading():
    body = render_claim_chain([{"text": "负极采用用户指定金属锂，仍需验证界面 [p1]。", "citations": ["p1"]}])
    assert "文献如何支持材料选择" not in body
    assert "用户指定金属锂" in body and "[p1]" in body


def test_missing_archive_does_not_invent_material_decision_chain():
    doc = archived_doc()
    doc["final_answer"] = ""
    body = render_research(doc)
    assert "没有可定位" in body and not archived_decision_chain(doc)
    assert "A位选Na" not in body and "[na]" not in body


def test_grouped_design_values_and_conditions_survive_emphasis():
    doc = example()
    doc["parameters"].update({
        "cathode.nominal_formula": {"value": "LiNi0.8Co0.1Mn0.1O2", "unit": "", "display_label": "名义式"},
        "cathode.doping.Na.amount": {"value": .03, "unit": "", "quantity_kind": "formula_coefficient", "denominator": "每化学式单位"},
        "cathode.doping.Na.site": {"value": "Li位", "unit": "", "display_label": "Na占位"},
        "electrolyte.salt": {"value": "LiPF6", "unit": "", "conditions": "基线配方，LHCE冲突待核对"},
        "electrolyte.concentration": {"value": 1, "unit": "mol/L", "conditions": "基线配方，LHCE冲突待核对"},
    })
    before = copy.deepcopy(doc)
    body = render_design(doc)
    assert "**LiNi0.8Co0.1Mn0.1O2**" in body and "**60 μm**" in body and "**28%**" in body
    assert "0.03（每化学式单位系数）" in body and "3%" not in body
    assert body.count("基线配方，LHCE冲突待核对") == 1 and "不能自动拼接" in body
    assert "规格1" not in body and "### 3.4" in body
    assert doc == before
    parser = pytest.importorskip("markdown_it").MarkdownIt()
    html = parser.render(body)
    assert "<strong>60 μm</strong>" in html and "<blockquote>" in html and "<ul>" in html
    assert "<pre>" not in html and "????" not in html


def test_rendered_analysis_and_design_tampering_is_detected(tmp_path, monkeypatch):
    import json
    from auto_battery_research.tests.test_final_design_reporting import report_fixture
    from auto_battery_research.tests.test_experiment_design import setup_design
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    mgr, _, _, _ = report_fixture(tmp_path)
    doc, record = setup_design(tmp_path)
    mgr.target_goal = doc["target"]
    for filename in ("pinn_input_spec.json", "pinn_simulation_result.json"):
        (tmp_path / filename).unlink()
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "design_scheme.md").write_text(doc["final_answer"], encoding="utf-8")
    monkeypatch.setattr("auto_battery_research.util.report_recipe_llm._llm_client", lambda *a: pytest.fail("不能重跑实验模型"))
    assert run_synthesis_report(mgr.target_goal, stage_manager=mgr)["success"]
    path = tmp_path / "final_research_report.md"
    content = path.read_text(encoding="utf-8")
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    assert checker.do_check()[0]
    path.write_text(content.replace("**60 μm**", "**99 μm**"), encoding="utf-8")
    assert not checker.do_check()[0]
