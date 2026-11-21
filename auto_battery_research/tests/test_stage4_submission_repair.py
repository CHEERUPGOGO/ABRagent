"""单位投影、独立用户绑定及零模型提交恢复的离线回归。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util import scheme_ledger as ledger
from auto_battery_research.util.research_contract import normalize_requirements, fallback_research
from auto_battery_research.util.stage4_submission_repair import repair_stage4_submission


def field(value, unit, **kwargs):
    return dict(value=value, unit=unit, basis="engineering_proposal", source="课题正文", **kwargs)


def requirements():
    goal = "采用NCM811正极，匹配金属锂负极和lhce电解液。"
    rows = []
    for n, (quote, path, value) in enumerate([
        ("采用NCM811正极", "cathode.material", "NCM811"),
        ("匹配金属锂负极", "anode.material", "金属锂"),
        ("lhce电解液", "electrolyte.material", "lhce"),
    ]):
        rows.append(dict(id=str(n), quote=quote, description=quote, kind="constraint", conditions=[],
                         binding=dict(path=path, value=value, unit="")))
    return goal, normalize_requirements(dict(items=rows, task_type="design", scheme_required=True), goal)


def test_projection_adds_only_contract_units_and_preserves_source(tmp_path):
    scheme = {"geometry": {"cathode": {"L_um": 25., "compaction_g_cm3": 2.25}, "anode": {"L_um": 450.}}}
    params = {"cathode.L_um": field(25, "um", source_quote="厚度25 μm", locked=True),
              "cathode.compaction_density": field(2.25, "g/cm3", source_quote="压实2.25 g/cm³"),
              "anode.thickness_um": field(450, "um", source_quote="锂箔450 μm")}
    original = copy.deepcopy(params)
    projected = ledger.project_scheme_parameters(scheme, params)
    assert params == original
    assert projected["cathode.L_um"]["source_quote"] == params["cathode.L_um"]["source_quote"]
    assert projected["cathode.L_um"]["locked"]
    assert projected["anode.L_um"]["unit"] == "um"
    assert projected["anode.L_um"]["source_quote"] == "锂箔450 μm"
    assert projected["cathode.compaction_g_cm3"]["unit"] == "g/cm3"
    assert not ledger.calculate({"parameters": projected})[1]
    ledger.ensure(tmp_path)
    doc = dict(target="", scheme=scheme, scheme_parameters=params)
    ledger.commit_design(tmp_path, doc)
    assert doc["ledger_view"]["parameters"]["cathode.L_um"]["source_quote"] == "厚度25 μm"
    assert not doc["ledger_view"]["issues"]


@pytest.mark.parametrize("attack", ["wrong_unit", "alias_value"])
def test_projection_still_rejects_real_unit_and_alias_conflicts(attack):
    scheme = {"geometry": {"cathode": {"L_um": 25}}}
    params = {"cathode.L_um": field(25, "mm")} if attack == "wrong_unit" else {"cathode.thickness_um": field(30, "um")}
    if attack == "wrong_unit":
        with pytest.raises(ValueError, match="单位"):
            ledger.project_scheme_parameters(scheme, params)
    else:
        projected = ledger.project_scheme_parameters(scheme, params)
        from auto_battery_research.util.physical_parameters import fixed_geometry
        with pytest.raises(ValueError, match="同一工况"):
            fixed_geometry(projected)


def test_independent_user_bindings_survive_failed_candidate(tmp_path):
    goal, req = requirements()
    ledger.ensure(tmp_path, goal)
    saved = ledger.persist_requirement_bindings(tmp_path, req, 0)
    before = copy.deepcopy(saved)
    candidate = dict(target=goal, scheme={"cathode": "NCM811", "anode": "li_metal", "electrolyte": "lhce",
                                         "geometry": {"cathode": {"L_um": 25}}},
                     scheme_parameters={"cathode.thickness_um": field(30, "um")}, requirements=req)
    with pytest.raises(ValueError, match="同一工况"):
        ledger.commit_design(tmp_path, candidate, base_revision=saved["revision"])
    assert ledger.load(tmp_path) == before
    assert all(saved["parameters"][k]["locked"] for k in ["cathode.material", "anode.material", "electrolyte.material"])
    assert ledger.persist_requirement_bindings(tmp_path, req, saved["revision"]) == saved


@pytest.mark.parametrize("missing_unit", [None, ""])
def test_repeat_text_requirement_preserves_locked_inline_unit(tmp_path, missing_unit):
    from auto_battery_research.util.research_contract import bindings
    value = "1 mol/L LiPF₆/EC+DMC"
    goal = "采用" + value + "电解液。"
    data = ledger.ensure(tmp_path, goal)
    locked = ledger.update(tmp_path, {"electrolyte.formulation": field(value, "mol/L")}, data["revision"], actor="user")
    req = normalize_requirements({"items": [dict(id="el", quote=goal, description=goal, kind="constraint",
        conditions=[], binding=dict(path="electrolyte.formulation", value=value, unit=missing_unit))]}, goal)
    saved = ledger.persist_requirement_bindings(tmp_path, req, locked["revision"])
    assert saved == locked
    assert bindings(req)["electrolyte.formulation"]["unit"] == "mol/L"
    assert ledger.persist_requirement_bindings(tmp_path, req, saved["revision"]) == saved
    doc = dict(target=goal, scheme={}, scheme_parameters={}, requirements=req)
    ledger.commit_design(tmp_path, doc)
    assert doc["ledger_view"]["parameters"]["electrolyte.formulation"] == locked["parameters"]["electrolyte.formulation"]


@pytest.mark.parametrize("attack", ["explicit_unit", "different_value", "unit_not_inline", "numeric_without_unit"])
def test_repeat_requirement_does_not_weaken_locks(tmp_path, attack):
    value, path, unit = "1 mol/L LiPF₆/EC+DMC", "electrolyte.formulation", "mol/L"
    if attack == "numeric_without_unit":
        value, path, unit = 4.6, "condition.v_max", "V"
    goal = f"采用{value}，候选2 mol/L LiPF₆/EC+DMC；电压4.6 V。"
    data = ledger.ensure(tmp_path, goal)
    locked = ledger.update(tmp_path, {path: field(value, "V" if attack == "unit_not_inline" else unit)}, data["revision"], actor="user")
    req = {"source": goal, "items": [dict(id="el", quote=goal, description=goal, kind="constraint", conditions=[],
        binding=dict(path=path, value="2 mol/L LiPF₆/EC+DMC" if attack == "different_value" else value,
                     unit="V" if attack == "explicit_unit" else None))]}
    original = copy.deepcopy(req)
    with pytest.raises(ValueError):
        ledger.persist_requirement_bindings(tmp_path, req, locked["revision"])
    assert ledger.load(tmp_path) == locked
    assert req == original


def test_adapter_candidate_conflict_keeps_independently_saved_conditions(tmp_path):
    from auto_battery_research.tools.rag_adapter import AbrRagAdapter
    goal, req = requirements()
    raw = dict(plan={"requirements": req}, final_answer="已有方案说明。" * 100,
               scheme={"cathode": "NCM811", "anode": "li_metal", "electrolyte": "lhce"},
               repair_parameters={"cathode.L_um": field(25, "um", source_quote="已有方案说明"),
                                  "cathode.thickness_um": field(30, "um", source_quote="已有方案说明")},
               evidence=[], reviewer_output={"issues": ["科学保留意见"], "confidence": "low"},
               review_completed=True, review_passed=False, geometry_error="无匹配几何", confidence="low")
    pipeline = SimpleNamespace(run=lambda *a: copy.deepcopy(raw), writer_llm=None)
    outcome = AbrRagAdapter(config={"runtime_options": {"report_first": True}}, pipeline=pipeline).run_rag_design(goal, tmp_path)
    assert outcome["success"]
    doc = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    saved = ledger.load(tmp_path)
    assert doc["report_readiness"]["category"] == "DIAGNOSTIC"
    for key in ["cathode.material", "anode.material", "electrolyte.material"]:
        assert saved["parameters"][key]["locked"]
        assert doc["ledger_view"]["parameters"][key] == saved["parameters"][key]


@pytest.mark.parametrize("attack", ["source", "fabricated", "negative", "unit", "version"])
def test_independent_binding_validation_is_fail_closed(tmp_path, attack):
    goal, req = requirements()
    ledger.ensure(tmp_path, goal)
    if attack == "source":
        req["source"] = "别的课题"
    elif attack == "fabricated":
        req["items"][0]["binding"]["value"] = "fabricated"
    elif attack == "negative":
        req["items"][0]["kind"] = "preference"
    elif attack == "unit":
        req["items"][0]["bindings"] = [dict(path="cathode.L_um", value=25, unit="um")]
    with pytest.raises(ValueError):
        ledger.persist_requirement_bindings(tmp_path, req, 99 if attack == "version" else 0)
    assert ledger.load(tmp_path)["revision"] == 0


def setup_failed_submission(task):
    from auto_battery_research.util.adaptive_retrieval import init_state
    from auto_battery_research.util.report_readiness import prepare, restore_diagnostic
    goal, req = requirements()
    saved = ledger.ensure(task, goal)
    text = "\n\n".join("## " + name + "\n\n" + "机理与性能需要独立验证。" * 10 for name in (
        "目标与设计路线", "推荐组合", "预期关键指标", "可行性依据", "风险与数据缺口"))
    text += "\n\n正极厚度25 μm、压实密度2.25 g/cm³；负极厚度450 μm。"
    params = {"cathode.L_um": field(25, "um", source_quote="正极厚度25 μm"),
              "cathode.compaction_density": field(2.25, "g/cm3", source_quote="压实密度2.25 g/cm³"),
              "anode.thickness_um": field(450, "um", source_quote="负极厚度450 μm")}
    from auto_battery_research.simulation.geometry_contract import build_geometry_contract
    geo = build_geometry_contract({"cathode": {"L_um": 25, "porosity": .3, "R_s_um": .2},
                                   "anode": {"L_um": 450, "porosity": 0}, "condition": {}},
                                  {"cathode": "NCM811", "anode": "li_metal"}, [])
    geo["cathode"]["compaction_g_cm3"] = 2.25
    scheme = dict(cathode="NCM811", anode="li_metal", electrolyte="lhce", geometry=geo)
    doc = dict(target=goal, requirements=req, plan={"requirements": copy.deepcopy(req)}, final_answer=text,
               scheme=scheme, scheme_parameters=params, ledger_view=saved, evidence=[], repair_audit=[],
               reviewer_output={"issues": ["科学保留意见"], "confidence": "low"}, confidence="low",
               review_completed=True, review_passed=False, provenance={}, research=fallback_research(req, "原记录缺口"))
    prepare(doc, saved, [])
    from auto_battery_research.util.requirement_review import normalize_review
    normalize_review(doc["reviewer_output"], doc)
    seed = dict(draft=text, evidence=[], scheme=copy.deepcopy(scheme), parameters=copy.deepcopy(params),
                plan=copy.deepcopy(doc["plan"]), research_draft=copy.deepcopy(doc), maintenance_calls=2,
                repair_rounds=3, review_rounds=5, format_repair_rounds=0, checkpoint_identity="old")
    init_state(seed)
    doc["retrieval_state"] = copy.deepcopy(seed["retrieval_state"])
    restore_diagnostic(doc, saved, "同一条件下参数声明冲突：压实密度; 同一条件下参数声明冲突：涂层厚度")
    for name, value in [("design_scheme.json", doc), ("stage4_repair_checkpoint.json", seed), ("rag_result.json", seed),
                        ("stage4_retrieval_checkpoint.json", {"checkpoint_identity": "old", "plan": seed["plan"]})]:
        ledger.atomic_write(task / name, json.dumps(value, ensure_ascii=False))
    ledger.atomic_write(task / "design_scheme.md", doc["final_answer"])
    ledger.atomic_write(task / ".stage_state.json", '{"status":"FAILED"}')
    mgr = SimpleNamespace(root_dir=task, config={"paths": {"output_dir": str(task)}}, target_goal=goal,
                          is_legacy_task=False, get_task_output_dir=lambda: task)
    return mgr, doc, seed


def test_submission_recovery_preserves_scientific_status_and_budgets(tmp_path):
    mgr, old, seed = setup_failed_submission(tmp_path)
    result = repair_stage4_submission(mgr)
    assert result["success"], result
    assert result["model_calls"] == 0 and not result["workflow_advanced"]
    doc = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    assert doc["reviewer_output"] == old["reviewer_output"] and doc["review_passed"] is False
    assert doc["scheme"]["anode"] == "li_metal"
    restored = json.loads((tmp_path / "stage4_repair_checkpoint.json").read_text(encoding="utf-8"))
    for key in ["repair_rounds", "review_rounds", "format_repair_rounds", "maintenance_calls", "retrieval_state", "draft"]:
        assert restored[key] == seed[key]
    assert (tmp_path / ".stage_state.json").read_text() == '{"status":"FAILED"}'
    assert repair_stage4_submission(mgr)["changed"] is False


@pytest.mark.parametrize("kind", ["preview", "publish", "downstream", "real_conflict"])
def test_submission_recovery_never_overwrites_failed_validation(tmp_path, monkeypatch, kind):
    mgr, _, _ = setup_failed_submission(tmp_path)
    if kind == "downstream":
        (tmp_path / "experiment_plan.json").write_text('{}')
    elif kind == "real_conflict":
        doc = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
        doc["rejected_designs"][-1]["scheme_parameters"]["anode.thickness_um"]["value"] = 400
        (tmp_path / "design_scheme.json").write_text(json.dumps(doc), encoding="utf-8")
    else:
        from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
        calls = []
        def check(*a, **kw):
            calls.append(1)
            return (kind == "publish" and len(calls) == 1), {"error": "模拟拒绝"}
        monkeypatch.setattr(RAGDesignChecker, "do_check", check)
    initial = {f.name: f.read_bytes() for f in tmp_path.iterdir() if f.is_file()}
    assert not repair_stage4_submission(mgr)["success"]
    assert all((tmp_path / name).read_bytes() == content for name, content in initial.items())
