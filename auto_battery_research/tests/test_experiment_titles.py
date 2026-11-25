"""实验小标题回归：真实段落主题、旧记录回退及局部修复绑定。"""
import copy
import json
import re
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_experiment_design import recipe_payload, setup_design, slot_response
from auto_battery_research.util import experiment_design as recipes
from auto_battery_research.util import experiment_repair as repair
from auto_battery_research.util.experiment_titles import checked_title, lead_title, normalize_step_titles
from auto_battery_research.util.scheme_ledger import digest


@pytest.mark.parametrize("paragraph,expected", [
    ("结构与成分：循环前后EIS、循环后XRD、ICP检测组成。", "结构与成分"),
    ("循环后XRD表征用于核对杂相，不作为循环性能结果。", "循环后XRD表征"),
    ("循环后XPS分析用于比较表面化学态。", "循环后XPS分析"),
    ("抑氧验证：首轮原位OEMS/DEMS测气体释放。", "抑氧验证"),
    ("固溶区与晶格畸变：先用衍射和成分核对。", "固溶区与晶格畸变"),
    ("排序与取舍：结合平行样误差判断是否保留。", "排序与取舍"),
    ("陌生仪器甲法：记录本批样品的响应并比较基准。", "陌生仪器甲法"),
    ("合成采用{{process.synthesis_route}}，预烧后煅烧。", "合成路线"),
    ("极片按活性材料和导电剂配制，涂布后干燥。", "极片加工"),
    ("对照G0与主组同批同工艺合成，避免批次影响。", "对照工艺"),
    ("首轮设1个主组G1与4个对照组。", "主组与对照"),
    ("每组平行{{testing.parallel_cells}}只扣电，固定工况。", "平行样"),
    ("电解液为{{electrolyte.salt}}，用量在首轮核对。", "电解液配制"),
    ("CR2032（{{packaging.type}}）在惰性气氛装配：先放极片。", "装配"),
    ("EC:DMC比例用户未给，不能当成明确配方。", None),
    ("电解液EC:DMC比例用户未给，需另行确认。", None),
    ("本项采用未知技术先收集记录，循环后再核对响应。", None),
])
def test_legacy_title_uses_explicit_topic_or_leading_action(paragraph, expected):
    assert lead_title(paragraph) == expected


@pytest.mark.parametrize("title", [
    "已达到目标", "成功抑制氧释放", "最佳配方", "机理已证实", "循环保持率90%",
    "450℃煅烧", "4.8V测试", "新增Na/Zn配方", "新配方{{electrolyte.salt}}",
    "**结构与成分**", "结构\n与成分", "[伪造引用]", "虚构催化剂改性",
])
def test_title_cannot_add_recipe_result_or_markup(title):
    assert checked_title(title, "结构与成分：循环后XRD测量杂相，比较抑氧。") is None


def test_titles_generated_in_existing_call_and_included_in_hash(tmp_path):
    doc, _ = setup_design(tmp_path)
    payload = recipe_payload()
    payload["sections"]["testing"] = ["结构与成分：循环后XRD核对杂相。"]
    payload["step_titles"] = {"testing": ["结构与成分"]}
    calls = []
    def chat(system, user, **kwargs):
        calls.append((system, kwargs))
        return slot_response(payload, user, kwargs["trace_tag"])
    record = recipes.generate(tmp_path, doc, SimpleNamespace(available=True, chat=chat), "新实验分析")
    assert [kw["trace_tag"] for _, kw in calls] == ["experiment_design", "experiment_alignment_review"]
    assert "step_titles" in calls[0][0]
    assert record["step_titles"]["testing"] == [{"title": "结构与成分", "paragraph_hash": digest(payload["sections"]["testing"][0])}]
    assert "#### 5.4.1 结构与成分" in recipes.render(record, doc)
    changed = copy.deepcopy(record)
    changed["step_titles"]["testing"][0]["title"] = "XRD表征"
    assert recipes.plan_hash(changed) != record["plan_hash"]
    with pytest.raises(ValueError, match="指纹"):
        recipes.validate(changed, doc)


@pytest.mark.parametrize("metadata", [
    "错误类型", {"testing": "错误类型"}, {"testing": []},
    {"testing": [False]}, {"testing": [{"title": "循环测试", "paragraph_hash": "过期"}]},
    {"testing": ["循环测试"]}, {"testing": [None]},
])
def test_malformed_optional_titles_do_not_block_legacy_render(tmp_path, metadata):
    doc, record = setup_design(tmp_path)
    record["sections"]["testing"] = ["结构与成分：循环后XRD核对杂相。"]
    record["step_titles"] = metadata
    record["plan_hash"] = recipes.plan_hash(record)
    before = copy.deepcopy(record)
    body = recipes.render(record, doc)
    assert "#### 5.4.1 结构与成分" in body and record == before
    normalize_step_titles(record)
    assert "step_titles" not in record


def test_local_paragraph_repair_invalidates_only_changed_title(tmp_path):
    doc, record = setup_design(tmp_path)
    record["sections"]["testing"] = ["循环测试：先做基准组循环。", "抑氧验证：通过测气记录气体释放。"]
    record["step_titles"] = {"testing": ["循环测试", "抑氧验证"]}
    normalize_step_titles(record, bind_unbound=True)
    before = copy.deepcopy(record)
    errors = [{"path": ["sections", "testing", 0], "reason": "方法须更正"}]
    updated = repair.apply_local(record, {"base_hash": digest(record), "changes": [{
        "path": errors[0]["path"], "value": "结构与成分：循环后XRD核对杂相。"}]}, errors)
    assert updated["step_titles"]["testing"][0] is None
    assert updated["step_titles"]["testing"][1] == before["step_titles"]["testing"][1]
    assert updated["parameters"] == before["parameters"] and record == before
    updated["plan_hash"] = recipes.plan_hash(updated)
    body = recipes.render(updated, doc)
    assert "#### 5.4.1 结构与成分" in body and "#### 5.4.2 抑氧验证" in body
    assert "#### 5.4.1 循环测试" not in body


def test_deterministic_failed_repair_drops_title_without_new_call(tmp_path):
    doc, record = setup_design(tmp_path)
    record["sections"]["testing"] = ["循环测试：已通过实验验证。"]
    record["step_titles"] = {"testing": ["循环测试"]}
    normalize_step_titles(record, bind_unbound=True)
    record["plan_hash"] = recipes.plan_hash(record)
    updated = repair.repair_record(record, doc, None, tmp_path, "离线回退")
    assert "step_titles" not in updated
    assert "本项操作尚未定案" in recipes.render(updated, doc)


def test_legacy_render_keeps_paragraphs_order_and_markdown(tmp_path):
    from auto_battery_research.util.report_narrative import substitute_parameters
    doc, record = setup_design(tmp_path)
    record["sections"]["groups"] = ["主组采用当前方案。对照组G0只改变待验证因素。固定条件采用同批材料。"]
    record["sections"]["testing"] = ["本项采用陌生技术进行记录，循环后核对响应。"]
    record["sections"]["gaps"] = [f"缺少第{i}项原始记录，须独立获取。" for i in range(1, 12)]
    record["plan_hash"] = recipes.plan_hash(record)
    before = copy.deepcopy(record)
    body = recipes.render(record, doc)
    assert "#### 5.4.1 实验操作" in body and "11. 缺少第11项" in body
    assert len(re.findall(r"^### 5\.[1-6] ", body, re.M)) == 6
    bindings = recipes.validate(record, doc)
    from auto_battery_research.util.experiment_protocol import split_operations, protocol_parameters
    bindings = protocol_parameters(bindings)
    for section in record["sections"].values():
        fragments = [substitute_parameters(p, bindings) for paragraph in section for p in split_operations(paragraph)]
        positions = [body.index(p.rstrip("，；。")) for p in fragments]
        assert positions == sorted(positions)
    assert record == before


def test_same_topic_can_legitimately_repeat(tmp_path):
    doc, record = setup_design(tmp_path)
    record["sections"]["testing"] = ["结构与成分：循环前XRD核对杂相。", "结构与成分：循环后XRD核对杂相。"]
    record["step_titles"] = {"testing": ["结构与成分", "结构与成分"]}
    normalize_step_titles(record, bind_unbound=True)
    record["plan_hash"] = recipes.plan_hash(record)
    body = recipes.render(record, doc)
    assert body.count("#### 5.4.1 结构与成分") == 1
    assert "1. 结构与成分：循环前XRD" in body and "2. 结构与成分：循环后XRD" in body


def test_report_final_gate_covers_bound_titles_and_deterministic_projection(tmp_path, monkeypatch):
    from auto_battery_research.tests.test_final_design_reporting import report_fixture
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    mgr, _, _, _ = report_fixture(tmp_path)
    doc, record = setup_design(tmp_path)
    mgr.target_goal = doc["target"]
    for name in ("pinn_input_spec.json", "pinn_simulation_result.json"):
        (tmp_path / name).unlink()
    (tmp_path / "design_scheme.md").write_text(doc["final_answer"], encoding="utf-8")
    record["sections"]["testing"] = ["结构与成分：循环后XRD核对杂相。"]
    record["step_titles"] = {"testing": ["结构与成分"]}
    normalize_step_titles(record, bind_unbound=True)
    record["plan_hash"] = recipes.plan_hash(record)
    doc["experiment_plan_hash"] = record["plan_hash"]
    (tmp_path / "design_scheme.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (tmp_path / recipes.FILENAME).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    before = (tmp_path / recipes.FILENAME).read_bytes()
    monkeypatch.setattr("auto_battery_research.util.report_recipe_llm._llm_client", lambda *a: pytest.fail("Stage 6不应调用模型"))
    result = run_synthesis_report(mgr.target_goal, stage_manager=mgr)
    assert result["success"], result
    path = tmp_path / "final_research_report.md"
    text = path.read_text(encoding="utf-8")
    assert "#### 5.4.1 结构与成分" in text and (tmp_path / recipes.FILENAME).read_bytes() == before
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    assert checker.do_check(is_complete=True)[0]
    path.write_text(text.replace("#### 5.4.1 结构与成分", "#### 5.4.1 循环测试"), encoding="utf-8")
    assert not checker.do_check(is_complete=True)[0]
