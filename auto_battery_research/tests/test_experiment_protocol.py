"""参考实验方法格式：层级/步骤、来源保留、主对照一致性与只读投影。"""
import copy
import re

import pytest

from auto_battery_research.tests.test_experiment_design import setup_design
from auto_battery_research.util import experiment_design as recipes
from auto_battery_research.util.experiment_protocol import (
    assignment_issues, ordered_steps, protocol_parameters, split_operations,
)
from auto_battery_research.util.experiment_titles import normalize_step_titles
from auto_battery_research.util.research_experiments import fallback_links


def test_protocol_method_headings_and_ordered_actions_are_valid_markdown(tmp_path):
    doc, record = setup_design(tmp_path)
    record["sections"]["fabrication"] = [
        "粉体合成：称取当前配方原料，球磨（转速300 rpm，时间6 h），压片后，先预烧，再以低速升温。",
        "粉体合成：取样记录相结构，并保留本批原始数据。",
        "极片制作：采用{{cathode.material}}，涂层厚度按{{cathode.L_um}}验收。",
    ]
    record["step_titles"] = {"fabrication": ["粉体合成", "粉体合成", "极片制作"]}
    normalize_step_titles(record, bind_unbound=True)
    record["plan_hash"] = recipes.plan_hash(record)
    before = copy.deepcopy(record)
    body = recipes.render(record, doc)
    assert body.count("#### 5.2.1 粉体合成") == 1 and "#### 5.2.2 极片制作" in body
    assert body.index("称取当前配方") < body.index("球磨（") < body.index("压片后，先预烧") < body.index("取样记录")
    assert not re.search(r"^\d+\. .*?[，；]$", body, re.M)
    assert "转速300 rpm，时间6 h" in body and record == before
    parser = pytest.importorskip("markdown_it").MarkdownIt()
    html = parser.render(body)
    assert "<h4>5.2.1 粉体合成</h4>" in html and "<ol>" in html and "<li>" in html
    assert "<pre>" not in html


@pytest.mark.parametrize("paragraph", [
    "EIS使用0.1 Hz–100 kHz（扰动5 mV，测试温度25 ℃），记录EC:DMC=1:1体系的阻抗 [p1]。",
    "若XRD出现杂相，则停止电化学测试；只有结构合格才进行后续循环。",
    "未定电压窗口时先核对材料稳定性，不能照搬其他体系。",
    "引用记录“原研究为0.1 C；不能当作1 C结果”，随后记录当前测试条件 [p2]。",
])
def test_splitting_preserves_numbers_conditions_and_qualifications(paragraph):
    parts = split_operations(paragraph)
    assert "".join(parts) == paragraph
    if paragraph.startswith(("若", "未定")):
        assert len(parts) == 1


def test_citations_after_sentence_are_not_detached_as_new_operations():
    assert split_operations("记录原始衍射谱。 [source_1]") == ["记录原始衍射谱。[source_1]"]
    body = ordered_steps(["按当前窗口循环。", "装配流程参照同体系文献 [p1]。"])
    assert "依据与说明" in body and "2. 装配流程" not in body


def test_display_unit_conversion_is_exact_and_does_not_change_thickness():
    original = {"process.electrode.punch_diameter": {"value": 14000, "unit": "um"},
                "cathode.L_um": {"value": 60, "unit": "um"},
                "process.pipe_diameter": {"value": 850, "unit": "um"}}
    before = copy.deepcopy(original)
    view = protocol_parameters(original)
    assert view["process.electrode.punch_diameter"] == {"value": 14, "unit": "mm"}
    assert view["cathode.L_um"] == original["cathode.L_um"]
    assert view["process.pipe_diameter"] == original["process.pipe_diameter"]
    assert original == before


def grouped_plan(tmp_path):
    doc, record = setup_design(tmp_path)
    record.pop("consistency", None)
    record.pop("pending_operations", None)  # 模拟升级前的旧分组记录。
    record.pop("execution_contract", None)
    record.pop("alignment_review", None)
    record["schema_version"] = 4
    doc["requirements"] = {"items": [], "scheme_required": True}
    doc["research"] = {}
    record["parameters"]["cathode.active_fraction"]["canonical_path"] = "cathode.active_fraction"
    doc["ledger_view"]["parameters"]["cathode.doping.Na.amount"] = {"value": .03, "unit": "", "quantity_kind": "formula_coefficient"}
    record["parameters"]["cathode.doping.Na.amount"] = {"ref": "cathode.doping.Na.amount"}
    fallback_links(record, doc)
    record["groups"].append({"id": "G0", "name": "未掺杂对照", "scope": "control", "parameter_refs": [],
        "changes": {"cathode.doping.Na.amount": {"value": 0, "unit": "", "reason": "用于未掺杂基线"}}})
    record["sections"]["groups"] = [
        "G1 主组：Na系数{{cathode.doping.Na.amount}}=0.03。",
        "G0 对照：Na系数{{cathode.doping.Na.amount}}=0。",
        "G2 高掺量探索：Na系数{{cathode.doping.Na.amount}}=0.12。",
        "主组G1与已登记对照使用同批原料，固定其他条件。",
    ]
    record["plan_hash"] = recipes.plan_hash(record)
    return doc, record


def test_groups_use_registered_changes_and_do_not_render_main_value_equals_control_value(tmp_path):
    doc, record = grouped_plan(tmp_path)
    before = copy.deepcopy((doc, record))
    body = recipes.render(record, doc)
    assert "| G1 |" in body and "| G0 | 未掺杂对照 | 对照 |" in body
    assert "用于未掺杂基线" in body and "主组G1与已登记对照使用同批原料" in body
    assert "待登记对照" in body and "G2 高掺量探索" in body
    assert "=0" not in body and "0.12" not in body and "cathode.doping.Na.amount" not in body
    assert (doc, record) == before


def test_custom_group_identifiers_and_empty_registry_keep_general_instructions():
    from auto_battery_research.util.experiment_protocol import render_group_notes
    record = {"groups": [{"id": "MAIN"}], "sections": {"groups": [
        "MAIN 主组：采用已登记配方。", "主组MAIN使用同批原料。"]}}
    assert render_group_notes(record, {}) == "主组MAIN使用同批原料。"
    record = {"groups": [], "sections": {"groups": ["后续若安排G0，先登记对照变化。"]}}
    body = render_group_notes(record, {})
    assert "后续若安排G0，先登记对照变化。" in body and "待登记对照" in body


def test_conflicting_literal_is_not_an_executable_step(tmp_path):
    doc, record = setup_design(tmp_path)
    record.pop("consistency", None)
    record.pop("pending_operations", None)  # 旧记录只读提示；新记录由一致性门禁拒绝。
    record["sections"]["fabrication"] = ["极片厚度{{cathode.L_um}}=99，涂布后称重。"]
    record["plan_hash"] = recipes.plan_hash(record)
    body = recipes.render(record, doc)
    assert "本项条件待确认" in body and "60 μm" in body
    assert "60 μm=99" not in body and "涂布后称重" not in body
    assert not assignment_issues("厚度{{x}}=60", {"x": {"value": 60, "unit": "um"}})


def test_new_plan_repairs_only_the_conflicting_parameter_sentence(tmp_path):
    from auto_battery_research.util.experiment_repair import diagnose
    doc, record = setup_design(tmp_path)
    record["sections"]["fabrication"] = ["极片厚度{{cathode.L_um}}=99，涂布后称重。"]
    record["plan_hash"] = recipes.plan_hash(record)
    errors = diagnose(record, doc)
    assert len(errors) == 1 and errors[0]["path"] == ["sections", "fabrication", 0]
    assert "引用值" in errors[0]["reason"]


def test_reviewed_recipe_conflict_is_visible_before_relevant_method(tmp_path):
    doc, record = setup_design(tmp_path)
    doc["ledger_view"]["parameters"]["electrolyte.additives"] = {"value": ["FEC"], "unit": ""}
    doc["reviewer_output"] = {"issues": ["electrolyte.additives与实验配方冲突，需确认。"]}
    record["parameters"]["electrolyte.material"] = {"ref": "electrolyte.material"}
    record["sections"]["assembly"] = ["电解液配制：沿用{{electrolyte.material}}，具体添加剂需核对。"]
    from auto_battery_research.util.experiment_consistency import stamp
    from auto_battery_research.util.experiment_alignment import attach
    attach(record, doc)
    stamp(record, doc)
    record["plan_hash"] = recipes.plan_hash(record)
    body = recipes.render(record, doc)
    assert body.index("实施前核对") < body.index("### 5.1")
    assert "电解液添加剂与实验配方冲突" in body and "electrolyte.additives" not in body
    assert "**条件待确认。**" in body.split("### 5.3", 1)[1].split("### 5.4", 1)[0]
    assert doc["ledger_view"]["parameters"]["electrolyte.additives"]["value"] == ["FEC"]


def test_narrative_conflict_stays_disclosed_without_becoming_an_operation_block(tmp_path):
    from auto_battery_research.util.experiment_protocol import consistency_notice, affected_by_conflict
    from auto_battery_research.util.report_readiness import unresolved_issues
    doc, record = setup_design(tmp_path)
    issue = "当前论证：paragraphs[p1]：electrolyte.material的科学归因与文献条件冲突，表述待复核。"
    doc["_report_record"] = {"view": {"active_issues": [issue]}}
    before = copy.deepcopy(doc)
    body = recipes.render(record, doc)
    assert "实施前核对" not in body
    assert not affected_by_conflict(["沿用{{electrolyte.material}}装配。"], doc)
    assert issue in unresolved_issues(doc) and doc == before
    snapshot = {"issues": [{"path": "electrolyte.material", "reason": "用户固定配方与方案表冲突", "source": "user_requirement"}]}
    assert "用户固定配方与方案表冲突" in consistency_notice(doc, snapshot)


def test_analysis_only_does_not_add_sample_groups_or_template_experiments(tmp_path):
    from auto_battery_research.tests.test_research_contract import research_doc
    doc = research_doc()
    doc["delivery_contract"] = 1
    doc["requirements"]["scheme_required"] = False
    record = recipes.generate(tmp_path, doc, None)
    body = recipes.render(record, doc)
    assert "不安排新的材料制备" in body and "本次不要求组装新电芯" in body
    assert "软包" not in body and "Celgard" not in body and "| G1 |" not in body
