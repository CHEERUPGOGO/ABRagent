"""同段多问题修复：精确范围、联合接口、批量复核及事务回退。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.design_repair import authorize, repair
from auto_battery_research.util.repair_interfaces import build_interfaces, execute, normalize_submissions


def issue(i, quote, fields=None):
    return {"id": i, "quote": quote, "reason": "原文依据不足", "suggestion": "降低确定性并修正引用", "parameter_paths": fields or []}


def permit_for(text, issues, fields=None):
    permit = authorize(text, issues, fields or {})
    permit["evidence_ids"] = ["p1"]
    return permit


class Script:
    def __init__(self, replacements, failed=(), new_issue=False, malformed=False, mutate=None):
        self.replacements, self.failed = replacements, failed
        self.calls = []
        self.new_issue, self.malformed, self.mutate = new_issue, malformed, mutate

    def chat(self, system, user, **kwargs):
        data = json.loads(user)
        self.calls.append((kwargs["trace_tag"], data))
        if kwargs["trace_tag"] == "writer_patch":
            rows = [{"interface_id": i, "replacement": self.replacements.get(v["original"], v["original"] + "（待验证）")}
                    for i, v in data["interface_contract"]["interfaces"].items()]
            patch = {"base_hash": data["permission"]["base_hash"], "submissions": rows}
            if self.mutate:
                self.mutate(patch, data)
            return json.dumps(patch)
        if self.malformed:
            return '{}'
        return json.dumps({"verdicts": [{"group_id": g, "issue_id": i, "resolved": i not in self.failed,
            "reason": "已检查原句和引用"} for g, c in data["transactions"].items() for i in c["issue_ids"]],
            "new_issues": [{"group_id": next(iter(data["transactions"])), "reason": "合并引入矛盾"}] if self.new_issue else []})


def run(text, issues, script, fields=None):
    fields = fields or {}
    permit = permit_for(text, issues, fields)
    return execute(SimpleNamespace(llm=script), script, text, fields, permit, issues, [{"passage_id": "p1"}])


def test_nonoverlapping_issues_merge_in_one_paragraph():
    text = "前文不变。坏引用，错误阈值。后文不变。\n\n其他段落不变。"
    issues = [issue("i1", "坏引用"), issue("i2", "错误阈值")]
    script = Script({"坏引用": "待核实引用", "错误阈值": "待验证阈值"})
    result, fields, entry = run(text, issues, script)
    assert result == "前文不变。待核实引用，待验证阈值。后文不变。\n\n其他段落不变。"
    assert entry["resolved_issue_ids"] == ["i1", "i2"]
    assert len(entry["interfaces"]["interfaces"]) == 2
    assert [c[0] for c in script.calls] == ["writer_patch", "repair_verify"]


def test_overlapping_issues_get_single_joint_interface():
    text = "Na半径34%，需验证。"
    issues = [issue("i1", "Na半径34%"), issue("i2", "半径34%")]
    contract = build_interfaces(text, {}, issues, permit_for(text, issues))
    assert len(contract["interfaces"]) == 1
    interface = next(iter(contract["interfaces"].values()))
    assert interface["issue_ids"] == ["i1", "i2"] and interface["original"] == "Na半径34%"
    result, _, entry = run(text, issues, Script({"Na半径34%": "Na半径失配需复核"}))
    assert result == "Na半径失配需复核，需验证。" and entry["status"] == "APPLIED"


def test_one_failed_issue_rolls_back_paragraph_but_not_other_paragraph():
    text = "错误一和错误二。\n\n独立错误三。"
    issues = [issue("i1", "错误一"), issue("i2", "错误二"), issue("i3", "错误三")]
    result, _, entry = run(text, issues, Script({}, failed=["i2"]))
    assert result.startswith("错误一和错误二。\n\n") and "错误三（待验证）" in result
    assert [i["id"] for i in entry["remaining_issues"]] == ["i1", "i2"]


@pytest.mark.parametrize("kind", ["forged", "duplicate", "range", "reference", "version"])
def test_invalid_submissions_never_change_text(kind):
    text = "原句。\n\n未授权段落。"
    def mutate(patch, data):
        if kind == "forged":
            patch["submissions"][0]["interface_id"] = "forged"
        elif kind == "duplicate":
            patch["submissions"] *= 2
        elif kind == "range":
            patch["submissions"][0]["start"] = 0
        elif kind == "reference":
            patch["submissions"][0]["replacement"] += "[unknown]"
        else:
            patch["base_hash"] = "stale"
    result, _, entry = run(text, [issue("i", "原句")], Script({}, mutate=mutate))
    assert result == text and entry["status"] == "REJECTED"


@pytest.mark.parametrize("malformed,new_issue", [(True, False), (False, True)])
def test_failed_semantic_review_never_commits(malformed, new_issue):
    text = "原句。"
    result, _, entry = run(text, [issue("i", "原句")], Script({}, malformed=malformed, new_issue=new_issue))
    assert result == text and entry["status"] == "REJECTED"


def test_shared_parameter_conflict_is_single_transaction():
    fields = {"cathode.L_um": {"value": 60, "unit": "um", "basis": "engineering_proposal", "source": "声明"}}
    text = "正极 `cathode.L_um` 为60。\n\n对照声明 `cathode.L_um` 是60。"
    issues = [issue("i1", "正极 `cathode.L_um` 为60", ["cathode.L_um"]), issue("i2", "对照声明 `cathode.L_um` 是60", ["cathode.L_um"])]
    def mutate(patch, data):
        for n, row in enumerate(patch["submissions"]):
            row["parameter_changes"] = [{"path": "cathode.L_um", "field": dict(fields["cathode.L_um"], value=50+n, source_quote=row["replacement"])}]
    before = copy.deepcopy(fields)
    result, updated, entry = run(text, issues, Script({}, mutate=mutate), fields)
    assert len(entry["interfaces"]["transactions"]) == 1
    assert result == text and updated == before and fields == before


def test_old_duplicate_paragraph_versions_not_guessed():
    text = "问题一，问题二。"
    issues = [issue("i1", "问题一"), issue("i2", "问题二")]
    permit = permit_for(text, issues)
    contract = build_interfaces(text, {}, issues, permit)
    patch = {"base_hash": permit["base_hash"], "edits": [{"issue_id": "i1", "block_id": "b0", "text": "修正一，问题二。"},
        {"issue_id": "i2", "block_id": "b0", "text": "整段改写，修正二。"}]}
    with pytest.raises(ValueError):
        normalize_submissions(patch, contract, text)


def test_missing_interface_holds_whole_paragraph():
    text = "错误一和错误二。"
    result, _, entry = run(text, [issue("i1", "错误一"), issue("i2", "错误二")],
        Script({}, mutate=lambda patch, data: patch["submissions"].pop()))
    assert result == text and entry["status"] == "REJECTED"


def test_eight_issues_in_three_paragraphs_do_not_submit_duplicate_blocks():
    # 回归实际运行中8条审核意见集中在少量段落、整批补丁因重复block_id失败的模式。
    text = "Na半径失配34%，容量出处不符，倍率口径不符。\n\n烧结温度无依据，保温时间无依据，推荐评分主观。\n\n首效体系含钴，首效测试条件不符。"
    quotes = ["Na半径失配34%", "容量出处不符", "倍率口径不符", "烧结温度无依据", "保温时间无依据", "推荐评分主观", "首效体系含钴", "首效测试条件不符"]
    result, _, entry = run(text, [issue(f"i{n}", q) for n, q in enumerate(quotes)], Script({}))
    assert entry["status"] == "APPLIED" and len(entry["resolved_issue_ids"]) == 8
    assert len(entry["patch"]["edits"]) == 3
    assert len({e["block_id"] for e in entry["patch"]["edits"]}) == 3


def test_retry_only_failed_paragraph_and_preserve_checkpoint(tmp_path):
    from auto_battery_research.util.design_repair import review_design
    from auto_battery_research.util.design_sections import REQUIRED_SECTIONS
    class RetryScript(Script):
        def chat(self, system, user, **kwargs):
            output = super().chat(system, user, **kwargs)
            if kwargs["trace_tag"] == "repair_verify":
                self.failed = ()
            return output
    script = RetryScript({}, failed=["issue-0"])
    text = "\n\n".join("## " + n + "\n\n" + "保留的背景说明。" * 10 for n, _ in REQUIRED_SECTIONS) + "\n\n错误甲。\n\n错误乙。"
    reviews = []
    def review(**kwargs):
        reviews.append(1)
        return {"issues": ["错误甲", "错误乙"], "repair_issues": [issue("x", "错误甲"), issue("y", "错误乙")], "confidence": "low", "error_type": "writing"}
    pipeline = SimpleNamespace(writer=SimpleNamespace(llm=script), reviewer=SimpleNamespace(run=review, llm=script),
        _extract_energy_claim=lambda *a: None, repair_checkpoint_path=tmp_path / "checkpoint.json")
    seed = {"draft": text, "scheme": {}, "evidence": []}
    result, review, _, _, audit = review_design(pipeline, seed, "目标")
    generated = [p for tag, p in script.calls if tag == "writer_patch"]
    assert len(reviews) == 1 and len(generated) == 2
    assert len(generated[1]["interface_contract"]["issues"]) == 1
    assert generated[1]["interface_contract"]["issues"][0]["quote"] == "错误甲"
    assert generated[1]["previous_failures"]
    assert result.count("错误乙（待验证）") == 1 and "错误甲（待验证）" in result
    assert not review["issues"] and all(a["status"] == "APPLIED" for a in audit)
    saved = json.loads(pipeline.repair_checkpoint_path.read_text(encoding="utf-8"))
    assert saved["draft"] == result and len(saved["repair_audit"]) == 2
