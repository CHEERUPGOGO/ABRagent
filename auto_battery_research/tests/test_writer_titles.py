"""小标题格式须精确报错，模型只修失败标题，断点恢复不改科研内容或重复调用。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_writer_protocol import example
from auto_battery_research.util.design_structure import TitleFormatError, render_design, writer_instructions
from auto_battery_research.util.scheme_ledger import digest
from auto_battery_research.util.writer_protocol import diagnostics, normalize, produce, protocol_patch
from auto_battery_research.util.writer_titles import apply, issues, recovery_available

PATH = ["sections", "recommendations", "subsections", 0, "title"]


def titled(value):
    data = example()
    data["sections"]["recommendations"]["subsections"][0]["title"] = value
    return data


def patch(data, value="文献依据"):
    return dict(base_hash=digest(normalize(data)), parameters=[], titles=[
        dict(path=list(PATH), old=data["sections"]["recommendations"]["subsections"][0]["title"], new=value)])


@pytest.mark.parametrize("title", [
    "1 C 口径与目标判据", "1C 口径", "0.5 C 倍率性能", "25 ℃ 循环条件", "1.2 V 工作窗口",
    "100 圈循环条件", "保持率 >74% 的判据", "畸变 <1% 的边界", "Zn–Na 推荐组合",
])
def test_scientific_title_is_not_misclassified_as_numbering(title):
    data = titled(title)
    text, structure = render_design(data)
    assert title in text and structure["headings"][2]["title"] == title
    state = {"raw_response": json.dumps(data, ensure_ascii=False)}
    result = produce(SimpleNamespace(available=False), "s", "u", {}, state, lambda: None)
    assert not result["fallback"] and not state.get("repair_attempts")


@pytest.mark.parametrize("title,code", [
    (None, "TITLE_TYPE"), ([], "TITLE_TYPE"), ("", "TITLE_EMPTY"), ("  ", "TITLE_EMPTY"),
    ("标题" * 51, "TITLE_LENGTH"), (" 标题", "TITLE_WHITESPACE"),
    ("## 文献依据", "TITLE_MARKUP"), ("<h3>文献依据</h3>", "TITLE_MARKUP"),
    ("**文献依据**", "TITLE_MARKUP"), ("文献\n依据", "TITLE_MARKUP"),
    ("~~文献依据~~", "TITLE_MARKUP"), ("> 文献依据", "TITLE_MARKUP"),
    ("- 文献依据", "TITLE_MARKUP"), ("文献\x85依据", "TITLE_MARKUP"),
    ("1. 文献依据", "TITLE_NUMBERING"), ("5.1 文献依据", "TITLE_NUMBERING"),
    ("一、文献依据", "TITLE_NUMBERING"), ("(1) 文献依据", "TITLE_NUMBERING"),
])
def test_all_title_failures_have_exact_paths_values_and_format(title, code):
    data = titled(title)
    with pytest.raises(TitleFormatError) as caught:
        render_design(data)
    errors = diagnostics(data, {}, caught.value)
    found = [e for e in errors if e["code"] == code]
    assert len(found) == 1 and found[0]["path"] == PATH and found[0]["title"] == title
    assert found[0]["node_id"] == "candidate_evidence" and "1 C" in found[0]["expected"]
    assert '"subsections", 0, "title"' in str(caught.value)


def test_prompt_and_collector_share_format_and_report_multiple_titles():
    data = titled("1. 文献依据")
    data["sections"]["risks"]["subsections"][0]["title"] = "## 已知风险"
    assert "单行纯文本" in writer_instructions() and "0.5 C" in writer_instructions()
    errors = diagnostics(data, {}, ValueError("其他错误"))
    assert {tuple(i["path"]) for i in errors if i["code"].startswith("TITLE_")} == {
        tuple(PATH), ("sections", "risks", "subsections", 0, "title")}


def test_format_patch_returns_to_model_and_replays_without_touching_body_or_parameters(tmp_path):
    data = titled("### 文献依据")
    raw = json.dumps(data, ensure_ascii=False)
    calls, state = [], {}
    checkpoint = tmp_path / "protocol.json"
    def save():
        checkpoint.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    def chat(system, user, **kw):
        calls.append(kw["trace_tag"])
        if kw["trace_tag"] == "writer":
            return raw
        assert kw["trace_tag"] == "writer_protocol_repair"
        request = json.loads(user)
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert saved["repair_attempts"] == 1
        assert request["errors"][0]["path"] == PATH
        assert request["errors"][0]["title"] == "### 文献依据"
        assert "titles" in system and "TITLE_" in system and "title_format" in request
        return json.dumps(patch(data), ensure_ascii=False)
    client = SimpleNamespace(available=True, chat=chat)
    result = produce(client, "s", "u", {}, state, save)
    expected = copy.deepcopy(data)
    expected["sections"]["recommendations"]["subsections"][0]["title"] = "文献依据"
    assert not result["fallback"] and result["structured_sections"] == expected
    assert state["raw_response"] == raw and calls == ["writer", "writer_protocol_repair"]
    restored = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert produce(client, "s", "u", {}, restored, lambda: None)["structured_sections"] == expected
    assert len(calls) == 2 and restored["repair_attempts"] == 1


def test_multiple_partial_title_rounds_only_receive_remaining_errors():
    data = titled("### 文献依据")
    other = ["sections", "risks", "subsections", 0, "title"]
    data["sections"]["risks"]["subsections"][0]["title"] = "1. 已知风险"
    state, seen = {"raw_response": json.dumps(data)}, []
    def chat(system, user, **kw):
        request = json.loads(user)
        seen.append(request)
        path = PATH if len(seen) == 1 else other
        old, new = ("### 文献依据", "文献依据") if len(seen) == 1 else ("1. 已知风险", "已知风险")
        return json.dumps(dict(base_hash=request["base_hash"], parameters=[], titles=[dict(path=path, old=old, new=new)]))
    result = produce(SimpleNamespace(available=True, chat=chat), "s", "u", {}, state, lambda: None)
    assert not result["fallback"] and state["repair_attempts"] == 2
    assert {tuple(i["path"]) for i in seen[1]["errors"]} == {tuple(other)}


@pytest.mark.parametrize("attack", ["body", "id", "valid_title", "main", "old", "invalid_new", "duplicate", "stale", "extra"])
def test_title_patch_rejects_unauthorized_changes(attack):
    data = titled("### 文献依据")
    proposed = patch(data)
    item = proposed["titles"][0]
    if attack == "body": item["path"][-1] = "body"
    elif attack == "id": item["path"][-1] = "id"
    elif attack == "valid_title": item["path"][-2] = 1
    elif attack == "main": item["path"] = ["sections", "goals", "title"]
    elif attack == "old": item["old"] = "其他原标题"
    elif attack == "invalid_new": item["new"] = "2. 文献依据"
    elif attack == "duplicate": proposed["titles"].append(copy.deepcopy(item))
    elif attack == "stale": proposed["base_hash"] = "陈旧哈希"
    elif attack == "extra": item["body"] = "越权修改"
    before = copy.deepcopy(data)
    with pytest.raises(ValueError):
        protocol_patch(data, proposed, {}, SimpleNamespace(available=False), {}, lambda: None)
    assert data == before


@pytest.mark.parametrize("decision", [True, False, "missing"])
def test_title_rename_requires_independent_review_and_never_repeats(decision):
    data = titled(" ")
    state, calls = {"raw_response": json.dumps(data)}, []
    def chat(system, user, **kw):
        calls.append(kw["trace_tag"])
        request = json.loads(user)
        if kw["trace_tag"] == "writer_protocol_repair":
            return json.dumps(patch(data))
        assert kw["trace_tag"] == "writer_title_verify"
        return json.dumps(dict(input_hash=request["input_hash"], titles=[] if decision == "missing" else [
            dict(index=0, supported=decision, reason="与已有正文一致" if decision else "原标题原意不一致")]))
    client = SimpleNamespace(available=True, chat=chat)
    result = produce(client, "s", "u", {}, state, lambda: None)
    assert result["fallback"] is (decision is not True)
    again = produce(client, "s", "u", {}, json.loads(json.dumps(state)), lambda: None)
    assert again["fallback"] == result["fallback"]
    assert calls == ["writer_protocol_repair", "writer_title_verify"]


def test_failed_title_request_is_persisted_and_not_resent():
    state, calls = {"raw_response": json.dumps(titled("### 文献依据"))}, []
    def interrupted(*a, **kw):
        calls.append(kw["trace_tag"])
        raise RuntimeError("模拟中断")
    client = SimpleNamespace(available=True, chat=interrupted)
    for _ in range(2):
        result = produce(client, "s", "u", {}, state, lambda: None)
        assert result["fallback"] and "subsections" in result["error"]
    assert calls == ["writer_protocol_repair"] and state["repair_attempts"] == 1


def test_legacy_scientific_title_can_reparse_without_resetting_used_budget():
    data = titled("1 C 口径与目标判据")
    state = dict(raw_response=json.dumps(data), error="子节标题必须是无编号的纯文本，ID不能占用主章节",
                 repair_attempts=1, repair_history=[dict(repair_raw_response=json.dumps(
                     dict(base_hash=digest(data), parameters=[])))])
    seed = dict(draft="", writer_request={"question": "原课题"}, writer_protocol=state)
    assert recovery_available(seed)
    result = produce(SimpleNamespace(available=False), "s", "u", {}, state, lambda: None)
    assert not result["fallback"] and state["repair_attempts"] == 1
    assert not recovery_available(seed)


def test_changed_scientific_content_is_never_a_format_only_fix():
    data = titled("### 1 C 保持率 >74% 的判据")
    for new in ("1 C 保持率 >90% 的判据", "1 C 保持率 <74% 的判据"):
        _, records = apply(data, [dict(path=PATH, old=data["sections"]["recommendations"]["subsections"][0]["title"], new=new)])
        assert not records[0]["format_only"]


def test_decimal_prefix_cannot_be_silently_removed_as_numbering():
    data = titled("0.5 倍倍率边界")
    _, records = apply(data, [dict(path=list(PATH), old="0.5 倍倍率边界", new="倍率边界")])
    assert not records[0]["format_only"]


@pytest.mark.parametrize("old,new", [("D_s 的扩散边界", "Ds 的扩散边界"),
                                     ("C* 界面浓度", "C 界面浓度"), ("<Na> 占位", "占位")])
def test_scientific_symbols_and_unknown_tags_are_not_silently_removed(old, new):
    _, records = apply(titled(old), [dict(path=list(PATH), old=old, new=new)])
    assert not records[0]["format_only"]


@pytest.mark.parametrize("old", ["**文献依据**", "<h3>文献依据</h3>", "<h3><b>文献依据</b></h3>"])
def test_only_balanced_known_markup_is_a_format_only_fix(old):
    _, records = apply(titled(old), [dict(path=list(PATH), old=old, new="文献依据")])
    assert records[0]["format_only"]


def test_legacy_empty_patch_replays_then_uses_only_remaining_title_budget():
    data = titled("### 文献依据")
    old = dict(base_hash=digest(data), parameters=[])
    state = dict(raw_response=json.dumps(data), repair_attempts=1,
                 repair_history=[dict(repair_raw_response=json.dumps(old))])
    calls = []
    def chat(system, user, **kw):
        calls.append(kw["trace_tag"])
        return json.dumps(patch(data))
    result = produce(SimpleNamespace(available=True, chat=chat), "s", "u", {}, state, lambda: None,
                     budget={"protocol_rounds": 2})
    assert not result["fallback"], result
    assert state["repair_attempts"] == 2 and calls == ["writer_protocol_repair"]
    assert len(state["repair_history"]) == 2
    assert not produce(SimpleNamespace(available=False), "s", "u", {}, state, lambda: None,
                       budget={"protocol_rounds": 2})["fallback"]


def test_title_repair_respects_zero_budget_and_original_response():
    raw = json.dumps(titled("### 文献依据"))
    state = dict(raw_response=raw)
    def unexpected(*args, **kw):
        pytest.fail("预算为零不能请求模型")
    result = produce(SimpleNamespace(available=True, chat=unexpected), "s", "u", {}, state, lambda: None,
                     budget={"protocol_rounds": 0})
    assert result["fallback"] and state["raw_response"] == raw and not state.get("repair_attempts")


@pytest.mark.parametrize("failure", ["interrupted", "wrong_hash", "duplicate"])
def test_semantic_review_failures_are_persisted_and_never_resent(failure):
    data, calls = titled(""), []
    state = dict(raw_response=json.dumps(data))
    def chat(system, user, **kw):
        calls.append(kw["trace_tag"])
        if kw["trace_tag"] == "writer_protocol_repair":
            return json.dumps(patch(data))
        if failure == "interrupted":
            raise RuntimeError("复核中断")
        req = json.loads(user)
        row = dict(index=0, supported=True, reason="依据正文")
        return json.dumps(dict(input_hash="wrong" if failure == "wrong_hash" else req["input_hash"],
                               titles=[row, row] if failure == "duplicate" else [row]))
    client = SimpleNamespace(available=True, chat=chat)
    for _ in range(2):
        assert produce(client, "s", "u", {}, state, lambda: None)["fallback"]
    assert calls == ["writer_protocol_repair", "writer_title_verify"] and state["repair_attempts"] == 1


def v3_titled(title):
    from auto_battery_research.util.design_structure import SECTION_IDS
    data = dict(version=3, parameters={}, sections={key: {"blocks": [dict(
        type="paragraph", id=key + "_p", text="保留原始科学边界，性能尚需实验验证。")]} for key in SECTION_IDS},
        research_links=dict(answers=[], candidates=[], arguments=[]))
    data["sections"]["recommendations"]["subsections"] = [dict(id="candidate_evidence", title=title,
        blocks=[dict(type="paragraph", id="candidate_p", text="文献依据与适用边界仍需验证。")])]
    return data


@pytest.mark.parametrize("title", ["1 C 口径与目标判据", "0.5 C 倍率性能", "25 ℃ 循环条件", "保持率 >74% 的判据"])
def test_v3_uses_same_scientific_title_contract(title):
    from auto_battery_research.util.stage4_document import compile_document, current_content, writer_instructions
    compiled = compile_document(current_content(v3_titled(title)), {})
    assert compiled.valid, compiled.diagnostics
    assert title in compiled.markdown and "小标题固定格式" in writer_instructions()


@pytest.mark.parametrize("renamed", [False, True])
def test_v3_returns_precise_title_errors_and_applies_only_title_patch(renamed):
    from auto_battery_research.util.stage4_document import compile_document, current_content
    from auto_battery_research.util.stage4_session import produce_v3
    data, state, calls = v3_titled("" if renamed else "### 文献依据"), {}, []
    compiled = compile_document(current_content(data), {})
    issue = next(d for d in compiled.diagnostics if d["code"].startswith("TITLE_"))
    assert issue["source_location"] == PATH and issue["title"] == ("" if renamed else "### 文献依据")
    def chat(system, user, **kw):
        calls.append(kw["trace_tag"])
        if kw["trace_tag"] == "writer_v3":
            return json.dumps(data)
        req = json.loads(user)
        if kw["trace_tag"] == "writer_title_verify":
            assert "blocks" in req["titles"][0]["body"]
            return json.dumps(dict(input_hash=req["input_hash"], titles=[dict(index=0, supported=True, reason="依据原正文")]))
        assert kw["trace_tag"] == "writer_v3_protocol_patch"
        assert req["allowed_paths"] == [] and "title_format" in req
        return json.dumps(dict(base_hash=req["base_hash"], replacements=[], references=[], titles=[
            dict(path=list(PATH), old=issue["title"], new="文献依据")]))
    client = SimpleNamespace(available=True, chat=chat)
    result = produce_v3(client, "s", "u", {}, state, lambda: None, {}, [])
    expected = copy.deepcopy(data)
    expected["sections"]["recommendations"]["subsections"][0]["title"] = "文献依据"
    assert not result["fallback"] and result["writer_content"] == current_content(expected)
    assert result["raw_response"] == json.dumps(data)
    assert produce_v3(client, "s", "u", {}, json.loads(json.dumps(state)), lambda: None, {}, [])["writer_content"] == current_content(expected)
    assert calls == ["writer_v3", "writer_v3_protocol_patch"] + (["writer_title_verify"] if renamed else [])


def test_v3_title_error_does_not_hide_valid_parameter_references():
    from auto_battery_research.util.stage4_document import compile_document, current_content, candidate_parameters
    data = v3_titled("### 文献依据")
    data["parameters"] = {"cathode.L_um": dict(value=80, unit="um", basis="engineering_proposal", source="明确的工程厚度声明")}
    data["sections"]["recommendations"]["subsections"][0]["blocks"][0]["text"] = "本方案厚度{{param:cathode.L_um}}，性能尚需验证。"
    params, errors = candidate_parameters(data["parameters"], {})
    compiled = compile_document(current_content(data), params)
    assert not errors
    assert {d["code"] for d in compiled.diagnostics} == {"TITLE_MARKUP"}
    assert compiled.dependencies["candidate_p"]["parameters"] == ["cathode.L_um"]
