"""实验JSON语法重试：标点恢复、内容保护、预算与原方案保留。"""
import json

import pytest

from auto_battery_research.tests.test_experiment_generation import Client, design
from auto_battery_research.tests.test_experiment_design import recipe_payload
from auto_battery_research.util import experiment_design as recipes
from auto_battery_research.util.experiment_json import apply_patch, decode, parse_with_repair
from auto_battery_research.util.scheme_ledger import digest


def syntax_patch(raw, offset, delete="]", insert=""):
    return {"base_hash": digest(raw), "changes": [{"offset": offset, "delete": delete, "insert": insert}]}


def malformed(raw):
    marker = '}, "citations"'
    assert marker in raw
    return raw.replace(marker, ']}, "citations"', 1)


def repair_client(*, failed=False, mutate=None):
    def transform(tag, request, response):
        if tag == "experiment_design":
            return malformed(response)
        if tag == "experiment_json_repair":
            if failed:
                return '{"changes":'
            patch = syntax_patch(request["raw_response"], request["parse_error"]["offset"])
            if mutate:
                mutate(patch, request)
            return json.dumps(patch)
        return response
    return Client(transform=transform)


def test_extra_bracket_recovers_real_plan_and_preserves_generation_audit(tmp_path):
    doc = design(tmp_path)
    client = repair_client()
    result = recipes.generate(tmp_path, doc, client)
    assert [tag for tag, _ in client.calls] == [
        "experiment_design", "experiment_json_repair", "experiment_alignment_review"]
    assert result["sections"] == recipe_payload()["sections"]
    assert result.get("origin") != "engineering_fallback"
    assert client.max_tokens == 9999 and client.output_max_tokens == 20000
    assert not hasattr(client, "single_attempt")
    saved = json.loads((tmp_path / "experiment_draft.json").read_text(encoding="utf-8"))
    audit = saved["audit"]
    with pytest.raises(json.JSONDecodeError):
        json.loads(audit["raw_response"])
    assert audit["json_syntax"]["status"] == "repaired"
    assert json.loads(audit["json_syntax"]["repaired_response"])["sections"] == result["sections"]
    assert audit["generation_method"] == "whole_json"
    assert len(list((tmp_path / "experiment_json_history").glob("*.json"))) == 1


def test_failed_syntax_retry_retains_old_plan_and_both_responses(tmp_path):
    doc = design(tmp_path)
    recipes.generate(tmp_path, doc, Client())
    before = (tmp_path / recipes.FILENAME).read_bytes()
    client = repair_client(failed=True)
    with pytest.raises(ValueError, match="原实验产物已保留"):
        recipes.generate(tmp_path, doc, client, restart=True)
    assert [tag for tag, _ in client.calls] == ["experiment_design", "experiment_json_repair"]
    assert (tmp_path / recipes.FILENAME).read_bytes() == before
    saved = json.loads((tmp_path / "experiment_draft.json").read_text(encoding="utf-8"))["audit"]
    assert saved["json_syntax"]["raw_response"] == '{"changes":'
    assert saved["json_syntax"]["status"] == "failed"
    assert saved["raw_response"]
    # 相同原稿和输入已用过格式修复机会，显式重生成也不能重复发送该补丁请求。
    with pytest.raises(ValueError, match="原实验产物已保留"):
        recipes.generate(tmp_path, doc, client, restart=True)
    assert [tag for tag, _ in client.calls].count("experiment_json_repair") == 1


def test_exhausted_syntax_repair_reports_fallback_truthfully(tmp_path):
    doc = design(tmp_path)
    doc["delivery_contract"] = 1
    client = repair_client(failed=True)
    record = recipes.generate(tmp_path, doc, client)
    assert record["origin"] == "engineering_fallback"
    assert recipes.delivery_status(record, doc=doc)[0] == "FALLBACK"
    assert len(client.calls) == 2
    assert "最多一次" in record["warnings"][0]


@pytest.mark.parametrize("raw,old,new", [
    ('{"a":[1]],"b":"单位μm"}', "]", ""),
    ('{"a":1 "b":2}', "", ","),
])
def test_punctuation_patch_preserves_all_values(raw, old, new):
    with pytest.raises(json.JSONDecodeError) as error:
        json.loads(raw)
    repaired, obj = apply_patch(raw, syntax_patch(raw, error.value.pos, old, new))
    assert json.loads(repaired) == obj
    assert obj["a"] in (1, [1])


@pytest.mark.parametrize("raw,offset,old,new", [
    ('{"a":25}', 5, "2", "3"),
    ('{"text":"[来源]"}', 9, "[", ""),
    ('{"text":"[来源]"}', 10, "", "]"),
    ('{"a":2 5}', 6, " ", ""),
])
def test_format_repair_cannot_change_literals_or_string_punctuation(raw, offset, old, new):
    with pytest.raises(ValueError):
        apply_patch(raw, syntax_patch(raw, offset, old, new))


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', "[]"])
def test_invalid_object_semantics_not_accepted(raw):
    with pytest.raises(ValueError):
        decode(raw)


def test_interrupted_retry_is_reserved_before_call_and_not_resent(tmp_path):
    raw = '{"a":[1]],"b":2}'
    audit = {}
    calls = []
    class Interrupted:
        available = True
        def chat(self, *args, **kwargs):
            calls.append(kwargs["trace_tag"])
            assert audit["json_syntax"]["status"] == "requesting"
            raise KeyboardInterrupt()
    client = Interrupted()
    with pytest.raises(KeyboardInterrupt):
        parse_with_repair(raw, client, tmp_path, "input", audit, lambda: None, lambda: None)
    assert not hasattr(client, "single_attempt")
    with pytest.raises(ValueError, match="已占用"):
        parse_with_repair(raw, client, tmp_path, "input", {}, lambda: None, lambda: None)
    assert len(calls) == 1


def test_changed_source_during_syntax_retry_never_publishes_plan(tmp_path):
    doc = design(tmp_path)
    def mutate(patch, request):
        (tmp_path / "scheme_ledger.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="输入已变化"):
        recipes.generate(tmp_path, doc, repair_client(mutate=mutate))
    assert not (tmp_path / recipes.FILENAME).exists()


def test_content_validation_still_runs_after_syntax_recovery(tmp_path, monkeypatch):
    doc = design(tmp_path)
    client = repair_client()
    from auto_battery_research.util import experiment_repair
    called = []
    def reject(record, *args, **kwargs):
        called.append(record)
        raise ValueError("模拟科学内容校验失败")
    monkeypatch.setattr(experiment_repair, "repair_record", reject)
    with pytest.raises(ValueError, match="模拟科学内容校验失败"):
        recipes.generate(tmp_path, doc, client)
    assert called and not (tmp_path / recipes.FILENAME).exists()
    saved = json.loads((tmp_path / "experiment_draft.json").read_text(encoding="utf-8"))["audit"]
    assert saved["json_syntax"]["status"] == "repaired"

def test_successful_syntax_response_replays_without_another_model_call(tmp_path):
    raw = '{"a":[1]],"b":2}'
    calls = []
    class Once:
        available = True
        def chat(self, system, user, **kwargs):
            calls.append(kwargs)
            request = json.loads(user)
            assert self.single_attempt is True and kwargs["json_mode"] is True
            return json.dumps(syntax_patch(raw, request["parse_error"]["offset"]))
    client = Once()
    first_audit, next_audit = {}, {}
    first = parse_with_repair(raw, client, tmp_path, "input", first_audit, lambda: None, lambda: None)
    second = parse_with_repair(raw, client, tmp_path, "input", next_audit, lambda: None, lambda: None)
    assert first == second == {"a": [1], "b": 2}
    assert len(calls) == 1 and not hasattr(client, "single_attempt")
    assert first_audit["json_syntax"] == next_audit["json_syntax"]


def test_syntax_transport_truncation_keeps_failed_response_without_hidden_retry(tmp_path):
    from langchain_core.messages import AIMessage
    from auto_battery_research.tests.test_llm_output_integrity import client_with_responses
    client = client_with_responses([AIMessage(
        content='{"base_hash":', response_metadata={"finish_reason": "length"})])
    audit = {}
    with pytest.raises(ValueError, match="格式修复请求失败"):
        parse_with_repair('{"a":[1]]}', client, tmp_path, "input", audit, lambda: None, lambda: None)
    assert client._openai_llm.budgets == [100]
    assert audit["json_syntax"]["failed_raw_response"] == '{"base_hash":'
    assert not hasattr(client, "single_attempt")


def test_original_audit_survives_later_content_validation_exception(tmp_path, monkeypatch):
    doc = design(tmp_path)
    original_validate = recipes.validate
    def fail_after_repair(record, *args, **kwargs):
        if "repair_status" in record:
            raise ValueError("模拟后续终验失败")
        return original_validate(record, *args, **kwargs)
    monkeypatch.setattr(recipes, "validate", fail_after_repair)
    with pytest.raises(ValueError, match="模拟后续终验失败"):
        recipes.generate(tmp_path, doc, repair_client())
    audit = json.loads((tmp_path / "experiment_draft.json").read_text(encoding="utf-8"))["audit"]
    assert audit["json_syntax"]["status"] == "repaired"
    assert audit["raw_response"] == audit["json_syntax"]["original_response"]
    assert audit["final_error"] == "模拟后续终验失败"


def test_repair_cannot_change_valid_array_shape_before_error():
    raw = '{"a":[1]],"b":2}'
    patch = {"base_hash": digest(raw), "changes": [
        {"offset": 5, "delete": "[", "insert": ""},
        {"offset": 7, "delete": "]", "insert": ""},
        {"offset": 8, "delete": "]", "insert": ""}]}
    with pytest.raises(ValueError, match="定位当前解析错误"):
        apply_patch(raw, patch)


def test_multiple_syntax_errors_follow_parser_and_keep_original_offsets():
    raw = '{"a":1,,"b":2,}'
    patch = {"base_hash": digest(raw), "changes": [
        {"offset": 7, "delete": ",", "insert": ""},
        {"offset": 13, "delete": ",", "insert": ""}]}
    repaired, value = apply_patch(raw, patch)
    assert value == {"a": 1, "b": 2} and json.loads(repaired) == value
