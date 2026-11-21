"""母体匹配规则但不继承掺杂配方的性能、登记验证或仿真适用性。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.rag import RelationEngine, ReviewerAgent, RULES_VERSION
from auto_battery_research.util.material_ids import assemble_components, component_signature, document_id_errors
from auto_battery_research.util.scheme_ledger import commit_design, flatten_scheme, load, update

pytestmark = pytest.mark.unit
FORMULA = "Li1.17Na0.03Mn0.6Ni0.18Zn0.02O2"
BASE = "Li1.2Mn0.6Ni0.2O2"


def row(value, **metadata):
    return dict(value=value, unit="", basis="engineering_proposal", source="当前正文",
                source_quote=f"声明{value}。", **metadata)


def parameters():
    return {"cathode.nominal_formula": row(FORMULA), "cathode.base_formula": row(BASE),
            "anode.material": row("li_metal"), "electrolyte.material": row("lhce")}


def test_doped_formula_matches_declared_parent_and_keeps_full_identity():
    params = parameters()
    scheme = assemble_components({}, params)
    before = copy.deepcopy(scheme)
    rules = RelationEngine().check_scheme(scheme)
    assert scheme == before and scheme["cathode"] == FORMULA
    assert rules["rules_version"] == RULES_VERSION
    assert not rules["rule_checks"]["rejects"]
    match = rules["material_matches"]["cathode"]
    assert match["rule_material_id"] == "LRMO" and match["basis"] == "parent"
    assert match["declared_value"] == FORMULA and match["matched"]
    assert any(n["rule_id"] == "C6" for n in rules["audit_notes"])
    assert any(n["rule_id"] == "C0_PARENT_MATCH" for n in rules["audit_notes"])
    assert flatten_scheme(scheme)["cathode.base_formula"] == BASE
    assert flatten_scheme(scheme)["cathode.nominal_formula"] == FORMULA
    assert assemble_components(scheme, params) == scheme
    assert not document_id_errors(dict(scheme=scheme, scheme_parameters=params, material_id_version=1))


def test_parent_matching_keeps_c1_c3_and_does_not_exempt_unknown_electrolyte():
    params = parameters()
    params["electrolyte.material"] = row("carbonate_ec")
    rules = RelationEngine().check_scheme(assemble_components({}, params))
    assert not rules["rule_checks"]["rejects"]
    assert {r["rule_id"] for r in rules["rule_checks"]["violations"]} >= {"C1", "C3"}
    params.pop("electrolyte.material")
    params["electrolyte.formulation"] = row("1 mol/L LiPF6/EC+未知溶剂")
    rules = RelationEngine().check_scheme(assemble_components({}, params))
    assert rules["material_matches"]["cathode"]["matched"]
    assert not rules["material_matches"]["electrolyte"]["matched"]
    assert all(FORMULA not in r["reason"] for r in rules["rule_checks"]["rejects"])


@pytest.mark.parametrize("base", [None, "未知母体XQ-27", "富锂母体LRMO与其他物质混合", "li_metal"])
def test_missing_unknown_or_wrong_category_parent_stays_unmatched(base):
    params = parameters()
    if base is None:
        params.pop("cathode.base_formula")
    else:
        params["cathode.base_formula"] = row(base)
    rules = RelationEngine().check_scheme(assemble_components({}, params))
    assert not rules["material_matches"]["cathode"]["matched"]
    assert any(r["rule_id"] == "C0_UNVERIFIED_MATERIAL" for r in rules["rule_checks"]["rejects"])


@pytest.mark.parametrize("metadata", [{"scope": "candidate"}, {"scope": "literature"}, {"conditional": True}, {"complete": False}])
def test_candidate_parent_never_becomes_rule_identity(metadata):
    params = parameters()
    params["cathode.base_formula"].update(metadata)
    scheme = assemble_components({}, params)
    assert "rule_parents" not in scheme
    assert not RelationEngine().check_scheme(scheme)["material_matches"]["cathode"]["matched"]


def test_parent_alias_paths_and_unicode_are_supported_without_guessing():
    params = parameters()
    params.pop("cathode.base_formula")
    params["cathode.parent_formula"] = row("Li₁.₂Mn₀.₆Ni₀.₂O₂")
    params["cathode.parent_declaration"] = row(BASE, canonical_path="cathode.base_formula")
    scheme = assemble_components({}, params)
    assert RelationEngine().check_scheme(scheme)["material_matches"]["cathode"]["rule_material_id"] == "LRMO"
    assert len(scheme["rule_parents"]["cathode"]["parameter_refs"]) == 2
    params["cathode.parent_formula"] = row("NCM811")
    with pytest.raises(ValueError, match="母体声明冲突"):
        assemble_components({}, params)
    broken = assemble_components({}, params, strict=False)
    assert any(r["rule_id"] == "C0_DECLARATION_CONFLICT" for r in RelationEngine().evaluate(broken)["rejects"])


def test_known_material_cannot_claim_unrelated_parent():
    params = parameters()
    params["cathode.nominal_formula"] = row("LiFePO4")
    with pytest.raises(ValueError, match="材料与声明母体冲突"):
        assemble_components({}, params)


def test_changed_withdrawn_or_stale_parent_invalidates_rule_context():
    params = parameters()
    scheme = assemble_components({}, params)
    changed = copy.deepcopy(params)
    changed["cathode.base_formula"] = row("NCM811")
    assert component_signature(scheme) != component_signature(assemble_components(scheme, changed))
    assert any("母体规则来源失效" in e for e in document_id_errors(dict(scheme=scheme, parameters=changed, material_id_version=1)))
    params.pop("cathode.base_formula")
    assert "rule_parents" not in assemble_components(scheme, params)
    scheme["cathode"] = "另一种掺杂材料"
    assert not RelationEngine().check_scheme(scheme)["material_matches"]["cathode"]["matched"]


def test_locked_parent_is_preserved_at_commit_and_cannot_change(tmp_path):
    params = parameters()
    doc = dict(scheme={}, scheme_parameters=params)
    commit_design(tmp_path, doc)
    assert doc["scheme"]["cathode"] == FORMULA
    saved = load(tmp_path)
    locked = update(tmp_path, {"cathode.base_formula": params["cathode.base_formula"]}, saved["revision"], actor="user")
    doc.pop("ledger_hash", None)
    doc["scheme_parameters"]["cathode.base_formula"] = row("NCM811")
    with pytest.raises(ValueError, match="锁定"):
        commit_design(tmp_path, doc)
    assert load(tmp_path) == locked
    with pytest.raises(ValueError, match="锁定"):
        update(tmp_path, {"cathode.open_parent": row("NCM811", canonical_path="cathode.base_formula")}, locked["revision"])
    assert load(tmp_path) == locked


def test_parent_match_does_not_inherit_energy_or_pinn_qualification():
    from auto_battery_research.simulation import match_pinn_system
    scheme = assemble_components({}, parameters())
    rules = RelationEngine().check_scheme(dict(scheme, target_energy_wh_kg=400), claimed_energy=400)
    assert rules["energy_check"] == "na"
    assert rules["coin_cell_evals"][0]["material_energy_wh_kg"] is None
    assert rules["coin_cell_evals"][0]["assessment"] == "not_evaluated"
    assert match_pinn_system(scheme["cathode"], scheme["anode"]) is None


def test_reviewer_keeps_doping_scientific_issues_after_parent_match():
    scheme = assemble_components({}, parameters())
    requests = []
    def chat(system, user, **kw):
        requests.append(user)
        return json.dumps(dict(issues=["Zn占位缺乏直接证据"], confidence="low", error_type="writing"))
    reviewer = ReviewerAgent(SimpleNamespace(available=True, chat=chat), RelationEngine())
    result = reviewer.run(question="评估共掺", draft_answer="拟采用共掺，需验证。", scheme=scheme,
                          evidence=[dict(passage_id="p1", source="离线种子", text="该位点尚需确认。")])
    assert "Zn占位缺乏直接证据" in result["issues"] and result["confidence"] == "low"
    assert '"basis": "parent"' in requests[0] and "掺杂名义式无需另行登记" in requests[0]


def test_report_discloses_parent_scope_and_ignores_stale_match():
    from auto_battery_research.util.report_readiness import notice
    from auto_battery_research.util.report_contract import report_identity
    scheme = assemble_components({}, parameters())
    doc = dict(scheme=scheme, rule_checks=RelationEngine().check_scheme(scheme),
               report_readiness=dict(category="COMPLETE", review_status="REVIEWED"))
    assert "正极按声明母体 LRMO" in notice(doc)
    assert "掺杂位点、性能和协同效应仍需独立验证" in notice(doc)
    old_identity = report_identity(doc, None, None)
    old_matches = doc["rule_checks"].pop("material_matches")
    assert report_identity(doc, None, None) != old_identity
    assert "规则匹配口径" not in notice(doc)
    doc["rule_checks"]["material_matches"] = old_matches
    doc["scheme"]["cathode"] = "其他候选"
    assert "规则匹配口径" not in notice(doc)


def test_adapter_persists_parent_match_and_preserves_nominal_formula(tmp_path):
    from auto_battery_research.tools.rag_adapter import AbrRagAdapter
    params = parameters()
    text = "\n\n".join("## " + name + "\n\n" + "候选仍需同体系实验验证。" * 8 for name in
                       ("目标与设计路线", "推荐组合", "预期关键指标", "可行性依据", "风险与数据缺口"))
    raw = dict(scheme={}, repair_parameters=params, final_answer=text, confidence="medium",
               evidence=[dict(passage_id="p1", source="离线种子", text="材料证据")],
               reviewer_output=dict(issues=["占位待验证"], confidence="low"), review_passed=False, review_completed=True,
               geometry_candidate={"cathode": {"L_um": 25., "porosity": .3, "R_s_um": .2},
                                   "anode": {"L_um": 450., "porosity": 0.}})
    pipeline = SimpleNamespace(run=lambda q: copy.deepcopy(raw), relation_engine=RelationEngine(), writer_llm=None)
    result = AbrRagAdapter(config={"runtime_options": {"report_first": True}}, pipeline=pipeline).run_rag_design("母体匹配测试", tmp_path)
    assert result["success"], result
    doc = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    assert doc["scheme"]["cathode"] == FORMULA
    assert doc["rule_checks"]["material_matches"]["cathode"]["rule_material_id"] == "LRMO"
    assert doc["review_passed"] is False and "占位待验证" in doc["reviewer_output"]["issues"]
    assert doc["provenance"]["rules_version"] == RULES_VERSION
