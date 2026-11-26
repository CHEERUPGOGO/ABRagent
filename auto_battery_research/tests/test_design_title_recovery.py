"""旧稿恢复须证明来源、保留成功补丁、沿用预算并拒绝陈旧定位。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.design_repair import blocks
from auto_battery_research.util.design_structure import START, END, SECTION_TITLES, structure_issues
from auto_battery_research.util.design_title_recovery import _old_normalize, restore_titles
from auto_battery_research.util.scheme_ledger import digest
from auto_battery_research.util.stage4_review_recovery import _review_restored_titles


def legacy_seed():
    parts = []
    for number, title in enumerate(SECTION_TITLES.values(), 1):
        parts.append(f"# {number}. {title}\n\n本节结论仍需依据同体系证据和实测结果核验。")
        if number == 2:
            parts.append("## 2.1 文献依据\n\n此处论证需要修订。\n\n## 2.2 推荐组合排行\n\n须先核对占位，见5.2。")
        if number == 5:
            parts.append("## 5.1 已知风险\n\n存在占位和活性容量的不确定性。\n\n## 5.2 数据缺口与获取路径\n\n采用独立结构表征检验占位。")
    archived = "\n\n".join(parts)
    before = _old_normalize(archived)
    split, units = blocks(before)
    bid = next(k for k, v in units.items() if v == "此处论证需要修订。")
    changed = "此处已经接受正文修订，仍保留定量性能待验证的边界。[p1]"
    split[int(bid[1:])] = changed
    after = "".join(split)
    return {"writer_output": {"draft_answer": archived}, "draft": after,
        "repair_rounds": 3, "review_rounds": 5, "format_repair_rounds": 0, "parameters": {}, "scheme": {}, "evidence": [],
        "reviewer_output": {"issues": ["需要同体系验证", "标题重复"], "confidence": "low"},
        "pending_review": {"issues": ["旧标题定位"]},
        "repair_audit": [{"status": "APPLIED", "before": digest(before), "after": digest(after),
            "patch": {"base_hash": digest(before), "edits": [{"block_id": bid, "text": changed}], "parameters": []}}]}


def test_restore_uses_original_headings_and_keeps_every_accepted_content_byte():
    seed = legacy_seed()
    snapshot = copy.deepcopy(seed)
    text, contract, audit = restore_titles(seed)
    assert not structure_issues(text, contract)
    assert "### 2.1 文献依据" in text and "### 2.2 推荐组合排行" in text
    assert "### 5.2 数据缺口与获取路径" in text and "见5.2" in text
    undo = text.removeprefix(START + "\n\n").removesuffix("\n\n" + END)
    for replacement in audit["headings"]:
        assert undo.count(replacement["after"]) == 1
        undo = undo.replace(replacement["after"], replacement["before"])
    assert undo == seed["draft"] and seed == snapshot


@pytest.mark.parametrize("mutate", [
    lambda s: s.pop("writer_output"),
    lambda s: s.update(draft=s["draft"] + "\n未经归档的修改"),
    lambda s: s["repair_audit"][0].update(after="tampered"),
    lambda s: s["repair_audit"][0]["patch"]["edits"][0].update(text="错误版本"),
    lambda s: s["repair_audit"][0]["patch"].update(append_sections=[{"name": "其他章节"}]),
])
def test_unproven_history_cannot_restore_by_position(mutate):
    seed = legacy_seed()
    mutate(seed)
    with pytest.raises(ValueError):
        restore_titles(seed)


@pytest.mark.parametrize("budget,error", [(5, False), (5, True), (6, False)])
def test_title_review_is_one_persisted_attempt_with_original_budget(tmp_path, budget, error):
    seed = legacy_seed()
    seed["draft"], seed["design_structure"], _ = restore_titles(seed)
    seed["title_recovery"] = {"prior_review": copy.deepcopy(seed["reviewer_output"])}
    seed["reviewer_output"]["error_type"] = "reviewer"
    seed["review_rounds"] = budget
    calls = []
    def review(**kwargs):
        calls.append(kwargs)
        saved = json.loads((tmp_path / "stage4_repair_checkpoint.json").read_text(encoding="utf-8"))
        assert saved["title_review_attempted"] and saved["review_rounds"] == 6
        if error:
            raise RuntimeError("测试服务不可用")
        return {"issues": ["需要同体系验证"], "confidence": "low", "error_type": "writing"}
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(run=review), writer=SimpleNamespace(llm=None),
        _extract_energy_claim=lambda _: None, retrieval_options={"supplement_rounds": 0, "rebuild_rounds": 0})
    text, verdict, *_ = _review_restored_titles(pipeline, seed, "原始课题", tmp_path)
    assert text == seed["draft"] and "需要同体系验证" in verdict["issues"]
    _review_restored_titles(pipeline, seed, "原始课题", tmp_path)
    assert len(calls) == (1 if budget == 5 else 0)
    assert seed["repair_rounds"] == 3 and seed["format_repair_rounds"] == 0 and seed["review_rounds"] == 6
    assert "pending_review" not in seed


def test_fixed_title_markers_in_code_are_preserved():
    from auto_battery_research.tests.test_design_structure import document
    from auto_battery_research.util.design_structure import render_design
    data = document()
    snippet = "```text\n## 文献依据\n见5.9 {{section:missing}}\n```\n\n行内示例 `{{section:missing}}` 仅作语法说明。"
    data["sections"]["goals"]["body"] += "\n\n" + snippet
    rendered, contract = render_design(data)
    assert snippet in rendered and not structure_issues(rendered, contract)
