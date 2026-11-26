"""原文直出的参数刷新、表格结构、审核继承及正常报告入口回归。"""
import copy
import json
import re

import pytest

from auto_battery_research.tests.test_design_structure import document
from auto_battery_research.util.design_structure import render_design
from auto_battery_research.util.research_original import available, render, view, render_review_appendix


def original_doc():
    source = document()
    source["version"] = 2
    source["parameters"] = {}
    source["sections"]["recommendations"]["subsections"][1]["body"] = (
        "| 项目 | 参数 | 说明 |\n|---|---|---|\n"
        "| 主方案厚度 | {{param:cathode.L_um}} | 预算输入 |\n"
        "| 文献厚度 | 45 μm | 文献原条件[p1] |\n"
        "| 对照厚度 | {{param:cathode.control_L_um}} | 独立对照 |\n"
        "| 材料 | {{param:cathode.material}} | 当前材料 |\n\n"
        "输入厚度为{{param:cathode.L_um}}，原手算结果为5.08 μL，按同一面积核算。见{{section:occupancy}}。")
    parameters = {"cathode.L_um": {"value": 45, "unit": "um", "meaning": "干涂层厚度"},
                  "cathode.control_L_um": {"value": 45, "unit": "um", "canonical_path": "cathode.L_um", "scope": "control", "group_id": "G2"},
                  "cathode.material": {"value": "材料A", "unit": "", "meaning": "正极材料"}}
    text, structure = render_design(source, parameters)
    return {"final_answer": text, "design_structure": structure, "numeric_contract": {"version": 2},
            "ledger_view": {"revision": 1, "parameters": copy.deepcopy(parameters)},
            "scheme_parameters": copy.deepcopy(parameters), "review_completed": True, "review_passed": True,
            "reviewer_output": {"issues": [], "repair_issues": []}, "evidence": [{"passage_id": "p1", "text": "文献原条件"}]}


def test_whole_source_preserves_tables_and_retargets_internal_links_without_mutation():
    from bs4 import BeautifulSoup
    from markdown_it import MarkdownIt
    doc = original_doc()
    doc["ledger_view"]["parameters"]["cathode.L_um"]["value"] = 60
    doc["ledger_view"]["parameters"]["cathode.material"]["value"] = "材料A|包覆层"
    before = copy.deepcopy(doc)
    text = render(doc)
    html = BeautifulSoup(MarkdownIt("commonmark", {"html": True}).enable("table").render(text), "html.parser")
    table = html.find("table")
    assert table and all(len(row.find_all(["th", "td"])) == 3 for row in table.find_all("tr"))
    assert "主方案厚度60 μm" in table.get_text().replace("\n", "")
    assert "文献厚度45 μm" in table.get_text().replace("\n", "")
    assert "对照厚度45 μm" in table.get_text().replace("\n", "")
    assert "材料A|包覆层" in table.get_text()
    assert "输入厚度为60 μm，原手算结果为5.08 μL" in text
    assert "相关手算结果、比较结论与适用条件仍需随输入复核" in text
    assert "[第 2.5.2 节](#report-design-occupancy)" in text
    assert "### 2.1 目标与设计路线" in text and "### 2.5 风险与数据缺口" in text
    assert "<!-- abr-param:" not in text and "{{param:" not in text
    assert doc == before


def test_same_values_preserve_original_review_and_all_five_sections():
    doc = original_doc()
    assert available(doc)
    data = view(doc)
    assert data["status"] == "reviewed" and not data["parameter_changes"]
    assert len([s for s in data["sections"] if s["level"] == 3]) == 5
    assert "本节复核说明" not in render(doc)
    doc["review_passed"] = False
    doc["reviewer_output"] = {"issues": ["容量推断仍需验证"], "repair_issues": [
        {"quote": "原手算结果为5.08 μL", "reason": "孔体积假设待确认"}]}
    text = render(doc)
    assert "仍有保留意见" in text and "孔体积假设待确认" in text
    assert "原稿审核保留意见" not in text and "容量推断仍需验证" not in text
    assert "容量推断仍需验证" in render_review_appendix(doc)
    doc["review_completed"] = False
    assert "尚未完成有效科学审核" in render(doc)


@pytest.mark.parametrize("mutation", ["missing", "null", "conditions", "scope", "denominator"])
def test_unusable_current_parameters_do_not_restore_old_values(mutation):
    doc = original_doc()
    params = doc["ledger_view"]["parameters"]
    if mutation == "missing":
        params.pop("cathode.L_um")
    elif mutation == "null":
        params["cathode.L_um"].update(value=None, locked=True)
    elif mutation == "conditions":
        params["cathode.L_um"]["conditions"] = {"sample": "不同样品"}
    elif mutation == "scope":
        params["cathode.L_um"].update(scope="control", group_id="G3")
    else:
        params["cathode.L_um"]["quantity_kind"] = "ratio"
        params["cathode.L_um"]["denominator"] = "其他分母"
    text = render(doc)
    assert "| 主方案厚度 | 45 μm |" not in text
    assert "待核对" in text or "未确定" in text
    assert "| 对照厚度 | 45 μm |" in text


def test_same_identity_renamed_field_can_supply_current_value():
    doc = original_doc()
    params = doc["ledger_view"]["parameters"]
    row = params.pop("cathode.L_um")
    params["cathode.thickness_um"] = dict(row, value=70)
    assert "| 主方案厚度 | 70 μm |" in render(doc)
    params["cathode.other_thickness"] = dict(row, value=80, canonical_path="cathode.L_um")
    assert "| 主方案厚度 | 待核对" in render(doc)


def test_compact_layout_preserves_source_examples_and_reference_titles():
    from auto_battery_research.util.report_layout import compile_main
    doc = original_doc()
    source = "原文3.4 V与0.5 C。\n```markdown\n## 3. 电芯总体方案与关键匹配\n第5节\n```\n"
    references = "### 文献1：方法手册第五部分\n原题名表5-1\n"
    report = ("<!-- abr-report-layout:6 -->\n## 1. 研发摘要\n详见第3节、第1、2、5节。\n"
              "## 2. 材料选择与文献依据\n" + source
              + "## 3. 电芯总体方案与关键匹配\n重复参数\n"
              "## 4. 仿真与优化分析\n电压3.4 V。\n"
              "## 5. 实验路线与验证计划\n### 5.1 分组\n表5-1。采用第3节方案，结果见第4节。\n"
              "## 6. 参考文献\n" + references)
    output = compile_main(report, doc)
    assert source in output and output.endswith(references)
    assert "重复参数" not in output and "电压3.4 V" in output
    assert "详见第2节、第1、2、4节" in output
    assert "### 4.1 分组\n表4-1。采用第2节方案，结果见第3节。" in output


def test_bad_source_structure_cannot_silently_fall_back_or_inject_sections():
    doc = original_doc()
    doc["final_answer"] = doc["final_answer"].replace("## 3. 预期关键指标", "## 6. 预期关键指标")
    with pytest.raises(ValueError, match="原文结构无效"):
        render(doc)


def test_normal_report_entry_uses_original_even_when_integration_failed():
    from auto_battery_research.util.report_analysis import render_materials
    from auto_battery_research.util.report_contract import render_selected_design
    from auto_battery_research.util.report_record import project
    doc = original_doc()
    doc["research_narrative"] = {"status": "failed", "diagnostics": ["来源编号错误"]}
    assert "### 2.2 推荐组合" in render_materials(doc)
    assert "完整整合审核" not in render_materials(doc)
    text = render_selected_design({"ledger_view": doc["ledger_view"], "research_doc": doc})
    assert "设计核算方法、代入过程及适用假设见第2节" in text
    assert "没有可直接引用的完整设计预算" not in text
    projected = project({"design": doc, "experiment": {}, "execution": {}})
    assert projected["original"]["source_hash"] == view(doc)["source_hash"]
    assert not any("当前论证审核已过期" in issue for issue in projected["active_issues"])


@pytest.mark.parametrize("with_research", [False, True])
def test_existing_task_maintenance_skips_llm_for_original(tmp_path, monkeypatch, with_research):
    from types import SimpleNamespace
    from auto_battery_research.util import research_recovery
    doc = original_doc()
    if with_research:
        doc.update(requirements={"items": []}, research={"answers": []})
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(research_recovery, "require_current", lambda *a: None)
    monkeypatch.setattr(research_recovery, "_research_client", lambda *a: pytest.fail("原文直出不能调用模型"))
    def publish(mgr, history, original, changed, verdict, **kw):
        assert original == changed == doc and kw["review_calls"] == 0
        assert (history / "design_scheme.json").exists()
        return {"success": True}
    monkeypatch.setattr(research_recovery, "_publish_recovery", publish)
    result = research_recovery.integrate_research_narrative(SimpleNamespace(get_task_output_dir=lambda: tmp_path))
    assert result["success"] and result["report_mode"] == "original"
    assert result["generation_calls"] == result["review_calls"] == 0


def test_published_report_uses_current_value_and_rejects_tampered_original(tmp_path):
    from auto_battery_research.tests.test_final_design_reporting import report_fixture
    from auto_battery_research.tests.test_experiment_design import setup_design
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    mgr, _, _, _ = report_fixture(tmp_path)
    doc, _ = setup_design(tmp_path)
    source = document()
    source.update(version=2, parameters={})
    source["sections"]["recommendations"]["subsections"][1]["body"] = (
        "| 参数 | 当前采用值 |\n|---|---|\n| 厚度 | {{param:cathode.L_um}} |")
    old = copy.deepcopy(doc["ledger_view"]["parameters"])
    old["cathode.L_um"]["value"] = 45
    doc["final_answer"], doc["design_structure"] = render_design(source, old)
    doc["scheme_parameters"] = {"cathode.L_um": old["cathode.L_um"]}
    doc["evidence"] = [{"passage_id": "p1", "text": "同批基准验证", "source": "离线证据"}]
    doc.update(review_completed=True, review_passed=False, reviewer_output={"issues": ["原稿推论仍需验证"]})
    mgr.target_goal = doc["target"]
    for name in ("pinn_input_spec.json", "pinn_simulation_result.json"):
        (tmp_path / name).unlink()
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "design_scheme.md").write_text(doc["final_answer"], encoding="utf-8")
    result = run_synthesis_report(doc["target"], stage_manager=mgr)
    assert result["success"], result
    report = tmp_path / "final_research_report.md"
    text = report.read_text(encoding="utf-8")
    assert "| 厚度 | 60 μm |" in text and "| 厚度 | 45 μm |" not in text
    assert "原稿推论仍需验证" not in text and "参数版本已更新" in text
    assert "原稿审核保留意见" not in text and "abr-final-design" not in text
    assert re.findall(r"^## (\d+)\. ", text, re.M) == ["1", "2", "3", "4", "5"]
    assert "## 3. 仿真与优化分析" in text and "## 4. 实验路线与验证计划" in text
    assert "### 4.1 " in text and "### 5.1 " not in text
    assert "## 5. 参考文献" in text and "电芯总体方案与关键匹配" not in text
    assert "原稿中的实验提案与本轮执行安排请结合第4节核对" in text
    supplement_path = tmp_path / "final_research_report_supplement.md"
    supplement = supplement_path.read_text(encoding="utf-8")
    assert "### 原稿审核保留意见\n\n- 原稿推论仍需验证" in supplement
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    passed, diagnostic = checker.do_check()
    assert passed, diagnostic
    supplement_path.write_text(supplement.replace("### 原稿审核保留意见", "### 遗漏意见"), encoding="utf-8")
    passed, diagnostic = checker.do_check()
    assert not passed and diagnostic["error_code"] == "REPORT_FINAL_INPUT_MISMATCH"
    supplement_path.write_text(supplement, encoding="utf-8")
    report.write_text(text.replace("| 厚度 | 60 μm |", "| 厚度 | 45 μm |"), encoding="utf-8")
    passed, diagnostic = checker.do_check()
    assert not passed and diagnostic["error_code"] == "REPORT_FINAL_INPUT_MISMATCH"
