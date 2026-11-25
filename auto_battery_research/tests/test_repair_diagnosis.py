"""连续两轮失败的问题审查、策略调整及重启不重发的离线回归。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.repair_diagnosis import diagnose, failed_cases, issue_key


def issue(identifier="i"):
    return {"id": identifier, "type": "writing", "quote": "待修正主张", "reason": "当前主张依据不足",
            "suggestion": "明确待验证边界", "parameter_paths": []}


def failed(identifier="i"):
    item = issue(identifier)
    return {"status": "REJECTED", "remaining_issues": [item], "interfaces": {
        "issues": [item], "interfaces": {"f1": {"issue_ids": [identifier]}}, "transactions": {"g1": ["f1"]}},
        "transactions": {"g1": {"status": "REJECTED", "reason": "引用未归档证据"}},
        "submissions": [{"interface_id": "f1", "replacement": "待修正主张[unknown]"}]}


def test_repeated_issue_uses_content_identity_not_reindexed_id():
    assert failed_cases([issue("new")], [failed("old"), failed("other")]) == [issue("new")]
    assert not failed_cases([issue()], [failed()])
    assert not failed_cases([issue()], [failed(), {"remaining_issues": [], "status": "APPLIED"}, failed()])
    different = dict(issue(), reason="同句中的另一个独立问题")
    assert not failed_cases([different], [failed(), failed()])


def test_diagnosis_sees_failed_submissions_and_is_reused_after_restart():
    seen, saved, state = [], [], {}
    def chat(system, user, **kwargs):
        payload = json.loads(user)
        seen.append(payload)
        assert kwargs["trace_tag"] == "repair_case_review"
        assert saved[-1]["repair_diagnoses"][0]["status"] == "ATTEMPTED"
        return json.dumps({"diagnoses": [{"issue_key": c["issue_key"], "cause": "interface", "action": "repair",
            "reason": "两轮都添加未归档引用，内容修改未进入科学复核", "suggestion": "保留现有引用并明确待验证边界"}
            for c in payload["cases"]]})
    client = SimpleNamespace(available=True, chat=chat)
    result = diagnose(client, "待修正主张。", {}, [issue()], [failed(), failed()], state,
                      lambda: saved.append(copy.deepcopy(state)))
    assert result[0]["action"] == "repair" and len(seen) == 1
    attempts = seen[0]["cases"][0]["previous_attempts"]
    assert len(attempts) == 2 and attempts[0]["submissions"][0]["replacement"].endswith("[unknown]")
    restored = json.loads(json.dumps(state))
    assert diagnose(client, "待修正主张。", {}, [issue()], [failed(), failed()], restored, lambda: None) == result
    assert len(seen) == 1 and not hasattr(client, "single_attempt")


@pytest.mark.parametrize("output", ["中断", "wrong_id", "[]"])
def test_failed_case_review_does_not_approve_or_repeat(output):
    calls, state = [], {}
    def chat(*args, **kwargs):
        calls.append(1)
        if output == "中断":
            raise RuntimeError("连接中断")
        return output if output == "[]" else json.dumps({"diagnoses": [{"issue_key": "wrong", "action": "recheck"}]})
    client = SimpleNamespace(available=True, chat=chat)
    assert not diagnose(client, "待修正主张。", {}, [issue()], [failed(), failed()], state, lambda: None)
    assert state["repair_diagnoses"][0]["status"] == "FAILED"
    assert not diagnose(client, "待修正主张。", {}, [issue()], [failed(), failed()], state, lambda: None)
    assert len(calls) == 1


@pytest.mark.parametrize("action", ["repair", "recheck", "defer"])
def test_pipeline_diagnoses_after_two_failures_before_third_attempt(tmp_path, action):
    from auto_battery_research.util.design_repair import review_design
    from auto_battery_research.util.design_structure import render_design
    from auto_battery_research.tests.test_design_structure import document
    text, contract = render_design(document())
    text += "\n\n待修正主张。"
    calls, reviews = [], []
    def chat(system, user, **kwargs):
        tag, payload = kwargs["trace_tag"], json.loads(user)
        calls.append((tag, payload))
        if tag == "writer_patch":
            number = sum(t == "writer_patch" for t, _ in calls)
            if number == 3:
                assert payload["previous_failures"][-1]["case_review"][0]["action"] == "repair"
            return json.dumps({"base_hash": payload["permission"]["base_hash"], "submissions": [
                {"interface_id": iid, "replacement": "待修正主张[unknown]" if number <= 2 else "该主张尚待验证"}
                for iid in payload["interface_contract"]["interfaces"]]})
        if tag == "repair_case_review":
            assert sum(t == "writer_patch" for t, _ in calls) == 2
            return json.dumps({"diagnoses": [{"issue_key": c["issue_key"], "cause": "interface", "action": action,
                "reason": "前两轮均因引用格式被拒，须改变修复策略", "suggestion": "只改现有主张的确定性，不添加新引用"}
                for c in payload["cases"]]})
        assert tag == "repair_verify"
        return json.dumps({"verdicts": [{"group_id": g, "issue_id": i, "resolved": True, "reason": "已明确未验证边界"}
            for g, txn in payload["transactions"].items() for i in txn["issue_ids"]], "new_issues": []})
    def review(**kwargs):
        reviews.append(kwargs)
        if action == "recheck" and len(reviews) == 2:
            assert "连续失败独立审查" in kwargs["history_context"]
            return {"issues": [], "confidence": "medium"}
        return {"issues": ["主张边界待修正"], "repair_issues": [issue()], "confidence": "low", "error_type": "writing"}
    llm = SimpleNamespace(available=True, chat=chat)
    pipeline = SimpleNamespace(writer=SimpleNamespace(llm=llm), reviewer=SimpleNamespace(llm=llm, run=review),
        stage4_repair_options={"content_rounds": 3},
        _extract_energy_claim=lambda _: None, repair_checkpoint_path=tmp_path / "checkpoint.json")
    seed = {"draft": text, "scheme": {}, "evidence": [], "design_structure": contract}
    result, verdict, *_ = review_design(pipeline, seed, "测试目标")
    assert sum(t == "repair_case_review" for t, _ in calls) == 1
    assert sum(t == "writer_patch" for t, _ in calls) == (3 if action == "repair" else 2)
    assert bool(verdict["issues"]) == (action == "defer")
    assert result.endswith("该主张尚待验证。") if action == "repair" else result == text
    checkpoint = json.loads(pipeline.repair_checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["repair_diagnoses"][0]["status"] == "REVIEWED"


def test_deferred_issue_is_retained_when_another_issue_is_repaired(tmp_path, monkeypatch):
    from auto_battery_research.util import design_repair
    from auto_battery_research.util.design_structure import render_design
    from auto_battery_research.tests.test_design_structure import document
    text, contract = render_design(document())
    text += "\n\n待修正主张。\n\n另一个待修正主张。"
    items = [issue("a"), dict(issue("b"), quote="另一个待修正主张", reason="另一处主张需修正")]
    requests = []
    def review(**kwargs):
        return {"issues": [i["reason"] for i in items], "repair_issues": items, "confidence": "low", "error_type": "writing"}
    def repair(writer, current, verdict, evidence, parameters, question, **kwargs):
        requests.append(verdict)
        current_items = design_repair.review_issues(verdict, current)
        if len(requests) == 3:
            assert [i["quote"] for i in current_items] == ["另一个待修正主张"]
            return current, parameters, {"status": "APPLIED", "remaining_issues": [], "unmapped_summaries": [],
                                         "interfaces": {"issues": current_items}}
        return current, parameters, {"status": "REJECTED", "remaining_issues": current_items,
                                     "interfaces": {"issues": current_items}}
    def chat(system, user, **kwargs):
        assert kwargs["trace_tag"] == "repair_case_review"
        cases = json.loads(user)["cases"]
        return json.dumps({"diagnoses": [{"issue_key": c["issue_key"], "cause": "evidence", "action": "defer" if n == 0 else "repair",
            "reason": "缺少必要实验依据" if n == 0 else "接口格式问题", "suggestion": "补实验后复核" if n == 0 else "修正正文"}
            for n, c in enumerate(cases)]})
    monkeypatch.setattr(design_repair, "repair", repair)
    llm = SimpleNamespace(available=True, chat=chat)
    pipeline = SimpleNamespace(writer=SimpleNamespace(llm=llm), reviewer=SimpleNamespace(llm=llm, run=review),
        stage4_repair_options={"content_rounds": 3},
        _extract_energy_claim=lambda _: None, repair_checkpoint_path=tmp_path / "checkpoint.json")
    seed = {"draft": text, "scheme": {}, "evidence": [], "design_structure": contract}
    _, verdict, *_ = design_repair.review_design(pipeline, seed, "测试目标")
    assert len(requests) == 3
    assert any("缺少必要实验依据" in message for message in verdict["issues"])
    assert not design_repair.review_verdict(text, verdict)[1]
