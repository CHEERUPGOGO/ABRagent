"""固定章节在提取之前生成，标题不再误改，引用及局部补丁受同一结构约束。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.design_sections import design_output_issues, normalize_titles
from auto_battery_research.util.design_structure import (
    SECTION_IDS, SECTION_TITLES, START, END, headings, parse_writer_output, render_design, structure_issues,
)
from auto_battery_research.util.design_repair import apply_patch, authorize, blocks, review_design
from auto_battery_research.util.scheme_ledger import digest


def document():
    result = {"version": 1, "sections": {key: {"body": "证据仍需在相同母体与测试条件下验证，保留实验边界。[p1]"}
                                        for key in SECTION_IDS}}
    result["sections"]["recommendations"] = {"subsections": [
        {"id": "candidate_evidence", "title": "文献依据", "body": "该证据仅支持单掺，不能证明双位点协同。[p1]"},
        {"id": "ranking", "title": "推荐组合排行", "body": "占位尚待证实，见{{section:occupancy}}。"}]}
    result["sections"]["risks"] = {"subsections": [
        {"id": "known_risks", "title": "已知风险", "body": "需要区分惰性稀释和活性容量的变化。"},
        {"id": "occupancy", "title": "数据缺口与获取路径", "body": "采用独立结构表征核对占位，并设置同批母体对照。"}]}
    return result


def test_subsection_keywords_do_not_become_main_titles_and_refs_follow_ids():
    data = document()
    text, contract = render_design(data)
    assert [h[3] for h in headings(text) if h[2] == 2] == [f"{n}. {t}" for n, t in enumerate(SECTION_TITLES.values(), 1)]
    assert "### 2.1 文献依据" in text and "### 2.2 推荐组合排行" in text
    assert "### 5.2 数据缺口与获取路径" in text
    assert "见[第 5.2 节](#design-occupancy)" in text
    assert not structure_issues(text, contract) and not design_output_issues(text)
    assert normalize_titles(text) == text
    data["sections"]["risks"]["subsections"].reverse()
    reordered, _ = render_design(data)
    assert "见[第 5.1 节](#design-occupancy)" in reordered


@pytest.mark.parametrize("mutate", [
    lambda d: d["sections"].pop("risks"),
    lambda d: d["sections"].update(extra={"body": "多余章节"}),
    lambda d: d["sections"]["goals"].update(body=""),
    lambda d: d["sections"]["goals"].update(body="待补充"),
    lambda d: d["sections"]["metrics"].update(body="## 可行性依据\n随意生成主标题"),
    lambda d: d["sections"]["metrics"].update(body="伪标题\n===\n额外正文"),
    lambda d: d["sections"]["metrics"].update(body="<h2>风险与数据缺口</h2>\n额外正文"),
    lambda d: d["sections"]["metrics"].update(body="见{{section:missing}}"),
    lambda d: d["sections"]["metrics"].update(body="需要验证，见5.2。"),
    lambda d: d["sections"]["risks"]["subsections"][0].update(id="ranking"),
    lambda d: d["sections"]["risks"]["subsections"][0].update(title="5.1 已知风险"),
])
def test_bad_structure_is_not_replaced_with_empty_headings(mutate):
    data = document()
    mutate(data)
    with pytest.raises(ValueError):
        render_design(data)


def test_duplicate_json_keys_fail_instead_of_silently_overwriting_sections():
    raw = json.dumps(document(), ensure_ascii=False).replace('"version": 1', '"version": 1, "version": 1')
    with pytest.raises(ValueError, match="重复"):
        parse_writer_output(raw)


@pytest.mark.parametrize("before,after", [
    ("## 3. 预期关键指标", "## 4. 可行性依据"),
    ("### 2.2 推荐组合排行", "### 2.4 推荐组合排行"),
    ("### 5.2 数据缺口与获取路径", "## 5.2 数据缺口与获取路径"),
    ("(#design-occupancy)", "(#design-missing)"),
    ("[第 5.2 节]", "[第 5.1 节]"),
    ('<a id="design-ranking"></a>', '<a id="design-known_risks"></a>'),
    ("采用独立结构表征核对占位，并设置同批母体对照。", "待补充"),
    (START, ""),
    (END, ""),
])
def test_final_structure_check_rejects_corruption(before, after):
    text, contract = render_design(document())
    assert structure_issues(text.replace(before, after), contract)


def test_plain_content_patch_keeps_titles_and_links_but_cannot_mutate_them():
    text, _ = render_design(document())
    quote = "该证据仅支持单掺，不能证明双位点协同。"
    issue = {"id": "i", "quote": quote, "reason": "需要注明条件", "suggestion": "补适用边界"}
    permit = authorize(text, [issue], {})
    bid = permit["grants"]["i"]["blocks"][0]
    body = blocks(text)[1][bid]
    patch = {"base_hash": digest(text), "edits": [{"issue_id": "i", "block_id": bid,
             "text": body.replace(quote, quote + "还需核对倍率和圈数。")}]}
    updated, _ = apply_patch(text, {}, permit, patch)
    assert [h[1:] for h in headings(updated)] == [h[1:] for h in headings(text)]
    assert "见[第 5.2 节](#design-occupancy)" in updated
    patch["edits"][0]["text"] = body + "\n## 新标题"
    with pytest.raises(ValueError, match="标题"):
        apply_patch(text, {}, permit, patch)


def test_writer_renders_before_consumers_and_preserves_analysis_mode():
    from auto_battery_research.rag import WriterAgent
    calls = []
    def chat(system, user, **kwargs):
        calls.append(user)
        return json.dumps(dict(document(), version=2, parameters={}), ensure_ascii=False)
    writer = WriterAgent(SimpleNamespace(available=True, chat=chat))
    evidence = [{"passage_id": "p1", "source": "离线文献", "text": "证据适用条件。"}]
    out = writer.run("设计材料方案", {}, evidence)
    assert out["fallback"] is False and "### 5.2" in out["draft_answer"]
    assert not structure_issues(out["draft_answer"], out["design_structure"])
    assert "程序生成章节契约" in calls[0] and len(calls) == 1
    writer.llm.chat = lambda *args, **kwargs: "# 机理分析\n\n## 文献依据\n仍需验证。"
    analysis = writer.run("分析材料机制", {"requirements": {"scheme_required": False}}, evidence)
    assert analysis["draft_answer"].startswith("# 机理分析") and "design_structure" not in analysis
    invalid = writer.run("设计材料方案", {}, evidence)
    assert invalid["fallback"] is True and invalid["draft_answer"] == "" and invalid["raw_response"]


def test_resume_review_does_not_renumber_or_consume_title_repair_budget(tmp_path):
    text, contract = render_design(document())
    seen = []
    def review(**kwargs):
        seen.append(kwargs["draft_answer"])
        return {"issues": [], "confidence": "high"}
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(run=review), writer=SimpleNamespace(llm=None),
        _extract_energy_claim=lambda _: None, repair_checkpoint_path=tmp_path / "checkpoint.json")
    seed = {"draft": text, "evidence": [], "scheme": {}, "design_structure": contract}
    result = review_design(pipeline, seed, "设计")
    assert seen == [text] and result[0] == text
    assert seed.get("repair_rounds", 0) == 0 and seed["format_repair_rounds"] == 0
    saved = json.loads(pipeline.repair_checkpoint_path.read_text(encoding="utf-8"))
    assert saved["draft"] == text and saved["design_structure"] == contract


def test_archived_markdown_is_not_rewritten_by_keyword_normalization():
    old = "# 二、推荐组合与关键配方\n\n## 2.1 文献依据\n\n## 2.2 推荐组合排行\n\n见5.2。\n\n## 5.2 数据缺口与获取路径"
    assert normalize_titles(old) == old


def test_changed_manifest_cannot_reach_extraction_or_reviewer(tmp_path, monkeypatch):
    from auto_battery_research.util.research_contract import prepare_review
    import auto_battery_research.util.scheme_ledger as ledger
    text, contract = render_design(document())
    contract["headings"][0]["title"] = "陈旧标题"
    seed = {"draft": text, "design_structure": contract, "plan": {"requirements": {
        "source": "原始课题", "items": [], "scheme_required": True}}, "scheme": {}, "evidence": []}
    def forbidden(*a, **kw):
        pytest.fail("结构未通过，不能消耗提取或审核预算")
    monkeypatch.setattr(ledger, "collect_parameters", forbidden)
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(run=forbidden), writer=SimpleNamespace(llm=None),
        _extract_energy_claim=lambda _: None, repair_checkpoint_path=tmp_path / "checkpoint.json")
    prepare_review(pipeline, seed)
    assert "research_draft" not in seed
    _, verdict, *_ = review_design(pipeline, seed, "原始课题")
    assert verdict["error_type"] == "structure" and any("结构不一致" in x for x in verdict["issues"])
