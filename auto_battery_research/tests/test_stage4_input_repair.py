"""现有课题输入修复：无模型调用、原句绑定、预算保留和失败回滚。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.stage4_input_repair import repair_declared_stage4_inputs
from auto_battery_research.util.scheme_ledger import commit_design, atomic_write, load
from auto_battery_research.util.research_contract import normalize_requirements, gap_answer
from auto_battery_research.tests.test_requirement_hierarchy import raw_contract
from auto_battery_research.tests.test_research_contract import requirement


def fixture(task):
    goal = "研究材料；1 C＝250 mA/g的倍率定义。"
    item = dict(requirement("1 C＝250 mA/g的倍率定义", "constraint"), id="rate", group_id="fixed",
                bindings=[dict(path="condition.C_rate_reference_current", value=250, unit="mA/g")])
    req = normalize_requirements(raw_contract(goal, [item]), goal)
    # 模拟旧契约降级后丢失固定字段，但其他科研内容已有完整方案表。
    req["items"][0].update(status="ambiguous", quantity=None, binding=None)
    req["items"][0].pop("bindings")
    req["status"] = "partial"
    req["diagnostics"] = ["items[0]：未知数量比较关系"]
    doc = dict(target=goal, scheme={"cathode": "未知正极", "electrolyte": "未知完整配方"},
        final_answer="## 用户锁定电池口径\n对电极为 Li 金属。", evidence=[], provenance={}, repair_audit=[])
    commit_design(task, doc)
    doc.update(requirements=req, plan={"requirements": copy.deepcopy(req)}, delivery_contract=1,
               report_readiness=dict(category="DIAGNOSTIC", review_status="REVIEWED", issues=["科学争议待复核"], physics_status="NOT_EVALUATED"),
               research=dict(answers=[gap_answer(req["items"][0])], candidates=[], arguments=[], selected_candidate_id=None), reviewer_output={})
    atomic_write(task / "design_scheme.json", json.dumps(doc, ensure_ascii=False))
    atomic_write(task / "design_scheme.md", doc["final_answer"])
    seed=dict(plan=copy.deepcopy(doc["plan"]), scheme=copy.deepcopy(doc["scheme"]), parameters={},
              research_draft=copy.deepcopy(doc), maintenance_calls=2, repair_rounds=3, review_rounds=3,
              checkpoint_identity="old", retrieval_state=dict(supplement_rounds=2, rebuild_rounds=1))
    atomic_write(task / "stage4_repair_checkpoint.json", json.dumps(seed, ensure_ascii=False))
    atomic_write(task / ".stage_state.json", '{"status":"FAILED"}')
    return SimpleNamespace(root_dir=task, target_goal=goal, config={"paths": {}}, get_task_output_dir=lambda *a, **k: task), doc, seed


def test_existing_task_repair_preserves_content_budget_and_does_not_advance(tmp_path, monkeypatch):
    mgr, before, seed = fixture(tmp_path)
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
    monkeypatch.setattr(RAGDesignChecker, "do_check", lambda *a, **k: (True, {}))
    result = repair_declared_stage4_inputs(mgr)
    assert result["success"] and not result["workflow_advanced"]
    doc = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    data = load(tmp_path)
    assert data["parameters"]["anode.material"]["value"] == "li_metal"
    assert data["parameters"]["condition.C_rate_reference_current"]["value"] == 250
    assert data["parameters"]["condition.C_rate_reference_current"]["locked"]
    assert doc["requirements"]["status"] == "parsed"
    assert doc["final_answer"] == before["final_answer"] and doc["evidence"] == before["evidence"]
    for k, v in before["ledger_view"]["parameters"].items():
        assert data["parameters"][k] == v
    saved = json.loads((tmp_path / "stage4_repair_checkpoint.json").read_text(encoding="utf-8"))
    for k in ("maintenance_calls", "repair_rounds", "review_rounds", "retrieval_state"):
        assert saved[k] == seed[k]
    assert (tmp_path / ".stage_state.json").read_text() == '{"status":"FAILED"}'
    assert repair_declared_stage4_inputs(mgr) == {"success": True, "changed": False}


def test_repair_preview_failure_never_publishes(tmp_path, monkeypatch):
    mgr, _, _ = fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
    monkeypatch.setattr(RAGDesignChecker, "do_check", lambda *a, **k: (False, {"error": "测试拒绝"}))
    assert not repair_declared_stage4_inputs(mgr)["success"]
    assert all((tmp_path / name).read_bytes() == value for name, value in before.items())


def test_repair_commit_failure_rolls_back(tmp_path, monkeypatch):
    mgr, _, _ = fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
    calls = []
    def check(*a, **k):
        calls.append(1)
        return len(calls) == 1, {}
    monkeypatch.setattr(RAGDesignChecker, "do_check", check)
    assert not repair_declared_stage4_inputs(mgr)["success"]
    assert len(calls) == 2
    assert all((tmp_path / name).read_bytes() == value for name, value in before.items())
