"""参数语义和信息分层：真实形状记录、未知扩展、比例口径与不可丢失的来源。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util import parameter_presentation as view
from auto_battery_research.util.report_narrative import amount, describe, render_design
from auto_battery_research.util.research_reporting import selected_summary, render_review_appendix


def row(value, unit="", **kwargs):
    return dict(value=value, unit=unit, meaning="测试参数", basis="engineering_proposal", source="原文甲", **kwargs)


def data():
    return {"revision": 1, "parameters": {
        "cathode.material": row("LRMO"), "anode.material": row("li_metal"), "electrolyte.material": row("carbonate_ec"),
        "cathode.nominal_formula": row("Li1.2-xNax(Mn0.6Ni0.2-yZny)O2", "formula_unit",
            conditions={"x": "0.02–0.03", "y": "0.01–0.02", "anode": "li_metal", "electrolyte": "LiPF6/EC+DMC"}),
        "cathode.doping.na.x_min": row(.02, "fraction of Li sites (x)", conditions={"denominator": "Li位", "formula_unit": True}),
        "cathode.doping.na.x_max": row(.03, "fraction of Li sites (x)", conditions={"denominator": "Li位", "formula_unit": True}),
        "cathode.doping.na.center": row(.03, "fraction of Li sites (x center)", conditions={"denominator": "Li位", "combination": "3×3"}),
        "condition.v_min": row(2, "V"), "condition.v_max": row(4.6, "V"),
        "condition.rest_temperature": row(30, "°C", conditions={"duration": "24 h"}),
        "condition.rest_time": row(24, "h", conditions={"temperature": "30 °C"}),
        "process.drying_temperature": row(90, "°C", conditions={"vacuum": True, "time_h": 4}),
    }, "derived": {}, "issues": [], "candidates": []}


def test_conditions_are_named_separately_and_flags_not_printed():
    body = view.render_conditions({"duration": "24 h", "time_h": 4, "denominator": "Li位", "vacuum": True, "formula_unit": True})
    assert "持续时间：24 h" in body and "持续时间：4 h" in body
    assert "真空" in body and "计量基准：Li位" in body
    assert "True" not in body and "补充参数" not in body
    assert amount("condition.temperature_C", row(25, "°C")) == "25 ℃"


def test_unseen_conditions_explicit_name_or_pending_appendix():
    conditions = [{"name": "脉冲恢复时间", "value": 8, "unit": "ms"}]
    assert view.render_conditions(conditions) == "脉冲恢复时间：8 ms"
    assert "另有1项" in view.render_conditions({"zeta_protocol": "Q9模式"})
    archived = view.render_conditions({"zeta_protocol": "Q9模式"}, complete=True)
    assert "zeta_protocol" in archived and "Q9模式" in archived
    with pytest.raises(ValueError, match="中文含义"):
        view.normalize_metadata("process.unknown", row(1, conditions={"zeta_protocol": "Q9模式"}))


def test_fraction_and_formula_coefficients_do_not_share_conversion():
    assert amount("cathode.doping.na.amount", row(.03, "", quantity_kind="formula_coefficient")) == "0.03（每化学式单位系数）"
    assert amount("cathode.doping.na.amount", row(.03, "", quantity_kind="site_fraction", denominator="Li位摩尔数")) == "3%"
    assert amount("cathode.doping.na_fraction", row(3, "%", quantity_kind="site_fraction", denominator="Li位摩尔数")) == "3 %"
    ambiguous = data()["parameters"]["cathode.doping.na.x_min"]
    assert amount("cathode.doping.na.x_min", ambiguous) == "0.02（计量口径待确认）"
    with pytest.raises(ValueError, match="说明和分母"):
        view.normalize_metadata("cathode.doping.na.x_min", ambiguous)


def test_equivalent_aliases_merge_all_sources_but_conditions_do_not():
    d = {"parameters": {
        "condition.v_min": row(2, "V", conditions={"temperature_C": 25}),
        "condition.voltage_min": dict(row(2.0, "V", conditions={"temperature_C": 25}), source="原文乙", passage_id="p2"),
        "condition.voltage_max": row(4.6, "V", conditions={"temperature_C": 25}),
        "process.drying_temperature": row(90, "°C", conditions={"time_h": 4}),
        "process.post_drying_temperature": row(90, "°C", conditions={"time_h": 2}),
    }}
    before = copy.deepcopy(d)
    rows = view.parameter_view(d)
    assert len(rows) == 4 and d == before
    merged = next(r for r in rows if r["canonical_key"] == "condition.v_min")
    assert len(merged["source_fields"]) == 2 and len(merged["sources"]) == 2
    appendix = view.render_parameter_appendix(d)
    assert "原文甲" in appendix and "原文乙" in appendix and "[p2]" in appendix
    assert len(view.combined_rows(rows)) == 4  # 截止电压不能误合成推荐窗口。


def test_ranges_combine_center_only_when_basis_and_conditions_match():
    d = {"parameters": {"cathode.doping.xx.amount_min": row(.01, "", quantity_kind="formula_coefficient", conditions={"site": "TM位"}),
        "cathode.doping.xx.amount_max": row(.03, "", quantity_kind="formula_coefficient", conditions={"site": "TM位"}),
        "cathode.doping.xx.center": row(.02, "", quantity_kind="formula_coefficient", conditions={"site": "TM位"})}}
    rows = view.combined_rows(view.parameter_view(d))
    assert len(rows) == 1 and rows[0]["center_value"] == .02
    assert len(rows[0]["source_fields"]) == 3
    reversed_data = {"parameters": dict(reversed(list(d["parameters"].items())))}
    assert len(view.combined_rows(view.parameter_view(reversed_data))) == 1
    d["parameters"]["cathode.doping.xx.center"]["conditions"] = {"site": "其他位"}
    assert len(view.combined_rows(view.parameter_view(d))) == 2


def test_summary_is_brief_and_detailed_values_appear_once_in_main():
    d = data()
    doc = {"ledger_view": d, "research": {}, "requirements": {}, "scheme": {}}
    before = copy.deepcopy(doc)
    summary = selected_summary(doc)
    body = render_design(d)
    appendix = render_review_appendix(doc)
    assert len(summary) < 260 and "x_min" not in summary and "3×3" not in summary
    assert "规格" in body and "0.02–0.03（计量口径待确认）" in body
    assert "放电截止电压为2 V" in body and "充电截止电压为4.6 V" in body
    assert "静置" not in body and "24 h" not in body
    assert "90 ℃" in appendix and "30 ℃" in appendix and "24 h" in appendix
    assert "补充参数" not in summary + body + appendix and "True" not in summary + body + appendix
    assert "fraction of" not in summary + body + appendix
    assert doc == before


def test_list_condition_references_and_unknown_metadata_fail_closed():
    params = {"condition.temperature_K": row(298.15, "K")}
    condition = [{"name": "测试温度", "ref": "condition.temperature_K"}]
    normalized = view.normalize_metadata("process.delay", row(1, "ms", conditions=condition), params)
    assert view.render_conditions(normalized["conditions"], parameters=params) == "测试温度：25 ℃"
    with pytest.raises(ValueError, match="未知字段"):
        view.normalize_metadata("process.delay", row(1, "ms", conditions=condition), {})
    with pytest.raises(ValueError, match="布尔"):
        view.normalize_metadata("process.delay", row(1, conditions=[{"name": "参数标志", "value": True}]))


def test_local_metadata_repair_preserves_valid_fields():
    from auto_battery_research.util.scheme_ledger import collect_parameters
    calls = []
    valid = row(20, "ms", source_quote="恢复时间20 ms。")
    invalid = row(25, "°C", source_quote="测试温度25 °C。", conditions={"unseen": "Q9"})
    repaired = dict(invalid, conditions=[{"name": "测试模式", "value": "Q9", "unit": ""}])
    def chat(*args, **kwargs):
        calls.append(args)
        return json.dumps({"parameters": {"process.delay": valid, "process.temperature": invalid} if len(calls) == 1
                           else {"process.temperature": repaired}, "candidates": []}, ensure_ascii=False)
    doc = {"final_answer": "恢复时间20 ms。测试温度25 °C。", "scheme": {}, "evidence": []}
    collect_parameters(doc, SimpleNamespace(available=True, chat=chat))
    assert len(calls) == 2 and doc["scheme_parameters"]["process.delay"] == valid
    assert doc["scheme_parameters"]["process.temperature"]["conditions"] == repaired["conditions"]


def test_conflicting_aliases_are_not_deduplicated_or_accepted(tmp_path):
    from auto_battery_research.util import scheme_ledger
    d = {"parameters": {"condition.v_min": row(2, "V"), "condition.voltage_min": row(2.5, "V")}}
    assert len(view.parameter_view(d)) == 2
    assert view.presentation_conflicts(d)
    assert scheme_ledger.calculate(d)[1]


def test_unknown_parameter_retained_in_appendix_with_precise_metadata():
    d = {"parameters": {"process.zeta": row(8, "ms", display_label="ζ响应延迟", conditions=[{"name": "测试模式", "value": "pulsed"}])}}
    appendix = view.render_parameter_appendix(d)
    assert "ζ响应延迟：8 ms" in appendix and "pulsed" in appendix
    assert "补充参数" not in appendix


def test_parameter_appendix_is_part_of_final_report_validation():
    from auto_battery_research.tests.test_research_contract import research_doc
    from auto_battery_research.util.report_contract import report_context, render_selected_design, render_energy, validate_report_blocks
    from auto_battery_research.util.research_reporting import render_summary, render_review_appendix
    doc = research_doc()
    context = report_context(doc, None)
    body = render_summary(doc) + "\n\n" + render_review_appendix(doc)
    body += "\n<!-- abr-final-design:start -->\n" + render_selected_design(context) + "\n<!-- abr-final-design:end -->"
    body += "\n<!-- abr-energy:start -->\n" + render_energy(context, None, 5) + "\n<!-- abr-energy:end -->"
    assert not validate_report_blocks(body, context, None, 5)
    assert validate_report_blocks(body.replace("完整参数与适用条件", "删去参数附录"), context, None, 5)


def test_markdown_tables_and_numbering_are_valid():
    parser = pytest.importorskip("markdown_it").MarkdownIt().enable("table")
    html = parser.render(render_design(data()) + "\n\n" + view.render_parameter_appendix(data()))
    assert "<table>" in html and "<ol>" in html and "<pre>" not in html
