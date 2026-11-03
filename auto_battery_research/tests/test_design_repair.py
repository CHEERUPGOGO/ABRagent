"""离线验证局部权限、结构修复、字段兼容和可恢复预算。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.design_repair import authorize, apply_patch, blocks, review_design
from auto_battery_research.util.design_sections import normalize_titles, design_sections, REQUIRED_SECTIONS
from auto_battery_research.util.scheme_ledger import normalize_source_field, collect_parameters, digest


def fixture():
    text = "## 可行性依据\n\n正极包覆可改善循环 [p1]。\n\n另一段保持原样。"
    issue = {"id": "i1", "quote": "正极包覆可改善循环", "reason": "证据不充分", "suggestion": "降低确定性"}
    permit = authorize(text, [issue], {})
    permit["evidence_ids"] = ["p1"]
    patch = {"base_hash": digest(text), "edits": [{"issue_id": "i1", "block_id": "b2", "text": "正极包覆有望改善循环，需验证 [p1]。"}]}
    return text, permit, patch


def test_patch_changes_only_authorized_block():
    text, permit, patch = fixture()
    result, _ = apply_patch(text, {}, permit, patch)
    assert "需验证" in result
    assert blocks(result)[1]["b4"] == blocks(text)[1]["b4"]


@pytest.mark.parametrize("attack", ["block", "version", "field", "full", "citation", "heading"])
def test_rejects_unauthorized_mutations_atomically(attack):
    text, permit, patch = fixture()
    original = copy.deepcopy(permit)
    if attack == "block":
        patch["edits"].append({"issue_id": "i1", "block_id": "b4", "text": "越权"})
    elif attack == "version":
        patch["base_hash"] = "stale"
    elif attack == "field":
        patch["parameters"] = [{"issue_id": "i1", "path": "cathode.L_um", "field": {"value": 10}}]
    elif attack == "full":
        patch["draft"] = "全篇替换"
    elif attack == "citation":
        patch["edits"][0]["text"] += " [unknown]"
    else:
        patch["edits"][0]["text"] = "## 新标题\n正文"
    with pytest.raises(ValueError):
        apply_patch(text, {}, permit, patch)
    assert permit == original


def test_field_source_compatibility_and_conflict():
    field = {"value": 1, "source": {"source_quote": "原句", "passage_id": "p1"}}
    result = normalize_source_field(field)
    assert result["source_quote"] == "原句" and result["passage_id"] == "p1"
    assert "source_quote" not in field
    with pytest.raises(ValueError):
        normalize_source_field(dict(field, source_quote="冲突"))


def test_canonical_title_does_not_change_body():
    text = "## 四、论证链与机理\n\n原有正文不变。"
    assert normalize_titles(text).endswith("\n\n原有正文不变。")
    assert "可行性依据" in design_sections(normalize_titles(text))[1]


def test_locked_fields_and_unlocated_issues_have_no_grants():
    fields = {"cathode.L_um": {"value": 60, "locked": True}}
    issue = {"id": "i", "quote": "正极厚度60", "reason": "错误", "suggestion": "改70", "parameter_paths": ["cathode.L_um"]}
    permit = authorize("正极厚度60", [issue], fields)
    assert permit["grants"]["i"]["fields"] == []
    issue["quote"] = "不存在"
    assert not authorize("正极厚度60", [issue], fields)["grants"]


def test_review_service_retry_has_persistent_budget(tmp_path):
    text = "\n\n".join("## " + name + "\n正文" * 20 for name, _ in REQUIRED_SECTIONS)
    calls = []
    def reviewer(**kwargs):
        calls.append(1)
        return {"error_type": "reviewer", "issues": ["服务异常"], "confidence": "low"}
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(run=reviewer), repair_checkpoint_path=tmp_path / "checkpoint.json",
        _extract_energy_claim=lambda text: None)
    seed = {"draft": text, "evidence": [], "scheme": {}}
    out = review_design(pipeline, seed, "目标")
    assert len(calls) == 3 and out[0] == normalize_titles(text)
    saved = json.loads(pipeline.repair_checkpoint_path.read_text(encoding="utf-8"))
    review_design(pipeline, saved, "目标")
    assert len(calls) == 3


def test_collect_parameters_accepts_nested_quote_without_retry():
    calls = []
    def chat(*args, **kwargs):
        calls.append(1)
        return json.dumps({"parameters": {"cathode.coating": {"value": "Al2O3", "unit": "", "basis": "engineering_proposal", "source": {"source_quote": "采用Al2O3"}}}})
    doc = {"final_answer": "采用Al2O3", "evidence": []}
    collect_parameters(doc, SimpleNamespace(available=True, chat=chat))
    assert len(calls) == 1
    assert doc["scheme_parameters"]["cathode.coating"]["source_quote"] == "采用Al2O3"


def test_authorized_field_update_keeps_other_parameters():
    field = {"value": 60, "unit": "um", "basis": "engineering_proposal", "source": "方案"}
    fields = {"cathode.L_um": field, "anode.material": {"value": "Li", "locked": True}}
    text = "正极 `cathode.L_um` 为60。\n\n其余正文。"
    issue = {"id": "i", "quote": text.split("\n")[0], "reason": "极化", "suggestion": "改50", "parameter_paths": ["cathode.L_um"]}
    permit = authorize(text, [issue], fields)
    new = "正极 `cathode.L_um` 为50。"
    patch = {"base_hash": digest(text), "edits": [{"issue_id": "i", "block_id": "b0", "text": new}],
        "parameters": [{"issue_id": "i", "path": "cathode.L_um", "field": dict(field, value=50, source_quote=new)}]}
    result, updated = apply_patch(text, fields, permit, patch)
    assert updated["cathode.L_um"]["value"] == 50 and result.endswith("其余正文。")
    assert updated["anode.material"] == fields["anode.material"]
    assert fields["cathode.L_um"]["value"] == 60
    patch["parameters"][0]["field"]["locked"] = True
    with pytest.raises(ValueError):
        apply_patch(text, fields, permit, patch)


def test_missing_section_can_only_be_appended():
    text = "## 目标与设计路线\n\n原文必须保留。"
    permit = authorize(text, [], {})
    permit["missing_sections"] = ["风险与数据缺口"]
    patch = {"base_hash": digest(text), "append_sections": [{"name": "风险与数据缺口", "text": "应补充同体系循环和结构验证，确认界面副反应及氧释放，并对掺量与位点进行实验对照。"}]}
    result, _ = apply_patch(text, {}, permit, patch)
    assert result.startswith(text) and "## 风险与数据缺口" in result
    patch["append_sections"][0]["name"] = "推荐组合"
    with pytest.raises(ValueError):
        apply_patch(text, {}, permit, patch)


def test_rejected_design_resumes_without_planner_or_writer(tmp_path, monkeypatch):
    from auto_battery_research.tests.test_stage4_golden import (
        _install_scripted_llm, _build_seeded_pipeline, _run_adapter, BAD_ELECTROLYTE_DRAFT)
    _install_scripted_llm(monkeypatch, BAD_ELECTROLYTE_DRAFT)
    pipeline = _build_seeded_pipeline(tmp_path)
    task = tmp_path / "task"
    first = _run_adapter(pipeline, task)
    assert not first["success"]
    def unexpected(*args, **kwargs):
        pytest.fail("恢复局部修复不得调用 Planner 或全文 Writer")
    pipeline.planner.run = unexpected
    pipeline.writer.run = unexpected
    second = _run_adapter(pipeline, task)
    assert not second["success"]
    assert second.get("error_code") != "REPAIR_CHECKPOINT_STALE"
