"""课题首尾空白不应破坏需求原文、引用位置与方案表来源校验。"""
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.rag import PlannerAgent
from auto_battery_research.tools.rag_adapter import AbrRagAdapter
from auto_battery_research.util import scheme_ledger


@pytest.mark.parametrize("padding", [" ", "\t", "\n\u3000"])
@pytest.mark.parametrize("available", [False, True])
def test_planner_preserves_original_source_and_offsets(tmp_path, padding, available):
    quote = "电解液使用LiPF6"
    goal = padding + quote + "；" + quote + "。" + padding
    start = goal.rindex(quote)
    raw = {"retrieval_queries": [quote], "requirements": {"items": [{
        "id": "salt", "quote": quote, "start": start, "kind": "constraint",
        "description": quote, "conditions": [], "status": "clear",
        "verification_needed": False,
        "binding": {"path": "electrolyte.salt", "value": "LiPF6", "unit": ""},
    }]}}
    client = SimpleNamespace(available=available, chat=lambda *a, **k: json.dumps(raw, ensure_ascii=False))
    requirements = PlannerAgent(client).run(goal)["requirements"]
    assert requirements["source"] == goal
    for row in requirements["items"]:
        assert goal[row["start"]:row["end"]] == row["quote"]
    if available:
        assert requirements["status"] == "parsed"
        assert requirements["items"][0]["start"] == start
    ledger = scheme_ledger.ensure(tmp_path, goal)
    saved = scheme_ledger.persist_requirement_bindings(tmp_path, requirements, ledger["revision"])
    assert saved["goal"] == goal
    assert saved["parameters"]["requirements.original_request"]["value"] == goal
    if available:
        assert saved["parameters"]["electrolyte.salt"]["locked"]


def test_standalone_pipeline_preserves_original_requirement_source(tmp_path, monkeypatch):
    from auto_battery_research.tests.test_stage4_golden import (
        GOLDEN_GOAL, GOLDEN_WRITER_DRAFT, _build_seeded_pipeline, _install_scripted_llm,
    )
    _install_scripted_llm(monkeypatch, GOLDEN_WRITER_DRAFT)
    pipeline = _build_seeded_pipeline(tmp_path)
    goal = " \t" + GOLDEN_GOAL + "\n"
    result = pipeline.run(goal)
    assert result["plan"]["requirements"]["source"] == goal
    for row in result["plan"]["requirements"]["items"]:
        assert goal[row["start"]:row["end"]] == row["quote"]


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("separate_design_request", [False, True])
def test_adapter_registers_padded_goal_in_both_contracts(tmp_path, monkeypatch, version, separate_design_request):
    from auto_battery_research.tests.test_stage4_golden import (
        GOLDEN_GOAL, GOLDEN_WRITER_DRAFT, _build_seeded_pipeline, _install_scripted_llm,
    )
    if version == 3:
        from auto_battery_research.tests.test_stage4_session_v3 import install
        install(monkeypatch)
    else:
        _install_scripted_llm(monkeypatch, GOLDEN_WRITER_DRAFT)
    pipeline = _build_seeded_pipeline(tmp_path)
    goal = " " + GOLDEN_GOAL + "\t\n"
    request = GOLDEN_GOAL if separate_design_request else goal
    config = {"runtime_options": {"stage4_writer": {"contract_version": version},
                                   "stage4_retrieval": {"enabled": False}}}
    task_dir = tmp_path / "task"
    result = AbrRagAdapter(config=config, pipeline=pipeline).run_rag_design(request, task_dir, task_goal=goal)
    assert result["success"], result
    document = json.loads((task_dir / "design_scheme.json").read_text(encoding="utf-8"))
    assert document["target"] == goal
    assert document["design_request"] == request
    assert document["requirements"]["source"] == goal
    for row in document["requirements"]["items"]:
        assert goal[row["start"]:row["end"]] == row["quote"]
    assert scheme_ledger.load(task_dir)["goal"] == goal
