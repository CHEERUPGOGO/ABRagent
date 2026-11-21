"""完整实验JSON生成、局部修复和历史产物兼容回归。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_experiment_design import recipe_payload, slot_response
from auto_battery_research.util import experiment_design as recipes, scheme_ledger as ledger
from auto_battery_research.util import experiment_review as review


def design(tmp_path, requirements=False):
    ledger.ensure(tmp_path)
    doc = {"target": "程序组装实验方案的离线测试", "scheme": {
        "cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce",
        "geometry": {"cathode": {"L_um": 60}}}, "evidence": [], "final_answer": "保持主方案并比较同批样品"}
    ledger.commit_design(tmp_path, doc)
    if requirements:
        doc["requirements"] = {"scheme_required": True, "status": "parsed", "items": [
            {"id": "r1", "kind": "objective", "quote": "核对循环保持率", "description": "核对循环保持率", "verification_needed": None},
            {"id": "r2", "kind": "objective", "quote": "比较结构变化", "description": "比较结构变化", "verification_needed": True}]}
    return doc


class Client:
    available = True
    max_tokens = 9999
    output_max_tokens = 20000

    def __init__(self, payload=None, transform=None):
        self.payload = payload or recipe_payload()
        self.transform = transform
        self.calls = []

    def chat(self, system, user, **kw):
        tag = kw["trace_tag"]
        request = json.loads(user)
        self.calls.append((tag, request))
        response = slot_response(self.payload, user, tag)
        return self.transform(tag, request, response) if self.transform else response


def checkpoint(tmp_path):
    return json.loads((tmp_path / "experiment_generation_checkpoint.json").read_text(encoding="utf-8"))


def test_whole_json_generation_uses_one_model_call_and_restores_client_config(tmp_path):
    doc = design(tmp_path)
    client = Client()
    result = recipes.generate(tmp_path, doc, client)
    assert [tag for tag, _ in client.calls] == ["experiment_design"]
    assert result["sections"] == recipe_payload()["sections"]
    assert not result.get("generation_contract") and not result.get("scientific_review")
    assert client.max_tokens == 9999 and client.output_max_tokens == 20000
    assert not hasattr(client, "single_attempt")
    assert not (tmp_path / "experiment_generation_checkpoint.json").exists()
    saved = json.loads((tmp_path / "experiment_draft.json").read_text(encoding="utf-8"))
    assert json.loads(saved["audit"]["raw_response"])["sections"] == recipe_payload()["sections"]
    assert saved["audit"]["generation_method"] == "whole_json"


def test_explicit_restart_archives_program_checkpoint_and_replaces_plan(tmp_path):
    doc = design(tmp_path)
    client = Client()
    first = recipes.generate(tmp_path, doc, client)
    old_checkpoint = {"accepted": {"route": "历史程序组装"}, "batches": {"plan": [{"raw_response": "旧响应"}]}}
    path = tmp_path / "experiment_generation_checkpoint.json"
    path.write_text(json.dumps(old_checkpoint), encoding="utf-8")
    result = recipes.generate(tmp_path, doc, client, restart=True)
    assert len(client.calls) == 2 and not path.exists()
    assert not result.get("generation_contract")
    archives = list((tmp_path / "experiment_generation_history").glob("before_whole_json_*.json"))
    values = [json.loads(p.read_text(encoding="utf-8")) for p in archives]
    assert old_checkpoint in values and first in values


def test_program_assembled_cache_keeps_scientific_verdict_without_model_calls(tmp_path):
    doc = design(tmp_path)
    client = Client()
    record = recipes.generate(tmp_path, doc, client)
    record.update(generation_contract=1, origin="program_assembled")
    signature = ledger.digest(review.subject(record, doc))
    checks = [{"id": key, "status": "needs_revision" if key == "methods_and_controls" else "supported", "reason": "历史审核意见"} for key in review.CHECKS]
    record["scientific_review"] = {"status": "NEEDS_REVISION", "subject_hash": signature, "checks": checks,
        "requirement_ids": [], "issues": [{"check_id": "methods_and_controls", "unit_id": "sections.testing",
                                             "reason": "历史科学问题仍未解决", "suggestion": "统一比较方法"}]}
    record["input_fingerprint"] = recipes.input_fingerprint(doc, version=8)
    record["plan_hash"] = recipes.plan_hash(record)
    path = tmp_path / recipes.FILENAME
    path.write_text(json.dumps(record), encoding="utf-8")
    before = path.read_bytes()
    loaded = recipes.generate(tmp_path, doc, client)
    assert loaded == record and len(client.calls) == 1 and path.read_bytes() == before
    assert review.status(loaded, doc)[0] == "NEEDS_REVISION"
    assert "历史科学问题仍未解决" in recipes.render(loaded, doc)


def test_invalid_complete_json_keeps_original_plan_and_raw_response(tmp_path):
    doc = design(tmp_path)
    first = recipes.generate(tmp_path, doc, Client())
    before = (tmp_path / recipes.FILENAME).read_bytes()
    client = Client(transform=lambda tag, request, raw: '{"parameters":')
    with pytest.raises(ValueError, match="原实验产物已保留"):
        recipes.generate(tmp_path, doc, client, restart=True)
    assert (tmp_path / recipes.FILENAME).read_bytes() == before
    assert len(client.calls) == 1
    draft = json.loads((tmp_path / "experiment_draft.json").read_text(encoding="utf-8"))
    assert draft["audit"]["raw_response"] == '{"parameters":'


def test_concurrent_input_change_prevents_complete_json_publication(tmp_path):
    doc = design(tmp_path)
    def transform(tag, request, raw):
        (tmp_path / "scheme_ledger.json").write_text("{}", encoding="utf-8")
        return raw
    doc["delivery_contract"] = 1
    with pytest.raises(ValueError, match="输入已变化"):
        recipes.generate(tmp_path, doc, Client(transform=transform))
    assert not (tmp_path / recipes.FILENAME).exists()


