"""组件由完整已声明身份组装，来源、局部修复与科学门禁保持一致。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.material_ids import (
    assemble_components, canonical_document, canonical_scheme, document_id_errors,
)
from auto_battery_research.util.scheme_ledger import commit_design, flatten_scheme, load, update, ensure
from auto_battery_research.util.design_repair import scheme_with_fields
from auto_battery_research.rag import RelationEngine

pytestmark = pytest.mark.unit


def row(value, **kwargs):
    return dict(value=value, unit="", basis="engineering_proposal", source="正文明确声明",
                meaning="完整名义组成", source_quote=f"采用{value}。", **kwargs)


def test_nominal_formula_builds_identity_without_inventing_material_and_is_idempotent():
    formula = "LiNi0.8Co0.1Mn0.1O2"
    parameters = {"cathode.nominal_formula": row(formula)}
    before = copy.deepcopy(parameters)
    scheme = assemble_components({}, parameters)
    assert scheme["cathode"] == "NCM811"
    identity = scheme["component_identities"]["cathode"]
    assert identity["kind"] == "nominal_formula"
    assert identity["parameter_refs"] == ["cathode.nominal_formula"]
    assert flatten_scheme(scheme) == {"cathode.nominal_formula": formula}
    assert parameters == before
    assert assemble_components(scheme, parameters) == scheme


def test_complete_recipe_is_not_reduced_to_contained_salt_or_parent():
    recipe = "1.2 M LiFSI + EC/DMC (1:1 v/v) + 2 wt% FEC"
    formula = "LiNi0.8Co0.1Mn0.1O2 + 0.03 Na取代"
    parameters = {"electrolyte.formulation": row(recipe), "cathode.nominal_formula": row(formula)}
    scheme = assemble_components({}, parameters)
    assert scheme["electrolyte"] == recipe
    assert scheme["cathode"] == formula
    assert canonical_scheme(scheme) == scheme
    assert not document_id_errors(dict(scheme=scheme, scheme_parameters=parameters, material_id_version=1))
    rules = RelationEngine().check_scheme(dict(scheme, anode="li_metal"))
    assert rules["confidence"] == "low"
    assert any(r["rule_id"] == "C0_UNVERIFIED_MATERIAL" for r in rules["rule_checks"]["rejects"])
    from pinn.spm_runner import match_pinn_system
    assert match_pinn_system(formula, "si_base") is None


def test_open_field_uses_explicit_canonical_path_and_keeps_original_reference():
    parameters = {"cathode.main_formula": row("未知材料XQ-27", canonical_path="cathode.nominal_formula")}
    scheme = assemble_components({}, parameters)
    assert scheme["cathode"] == "未知材料XQ-27"
    assert flatten_scheme(scheme) == {"cathode.main_formula": "未知材料XQ-27"}


@pytest.mark.parametrize("metadata", [
    {"scope": "candidate"}, {"scope": "control"}, {"scope": "literature"},
    {"conditional": True}, {"status": "conditional"}, {"complete": False},
    {"conditions": {"conditional": True}},
])
def test_conditional_or_candidate_recipe_does_not_become_main_identity(metadata):
    parameters = {"electrolyte.formulation": row("配方A", **metadata)}
    assert "electrolyte" not in assemble_components({"electrolyte": "配方A"}, parameters)


def test_partial_parameters_do_not_invent_recipe():
    parameters = {"electrolyte.salt": row("LiFSI"), "electrolyte.solvent": row("DME"),
                  "electrolyte.concentration": row(1.2), "cathode.base_formula": row("NCM811"),
                  "cathode.doping.Na": row(.03)}
    assert assemble_components({}, parameters) == {}


def test_explicit_material_has_priority_but_conflicting_full_identity_is_rejected():
    parameters = {"cathode.material": row("NCM 811"), "cathode.nominal_formula": row("改性NCM811名义式")}
    assert assemble_components({}, parameters)["cathode"] == "NCM811"
    parameters = {"cathode.formula": row("LiMnO2"), "cathode.nominal_formula": row("LiNiO2")}
    with pytest.raises(ValueError, match="身份声明冲突"):
        assemble_components({}, parameters)
    diagnostic = assemble_components({}, parameters, strict=False)
    assert "cathode" not in diagnostic and diagnostic["component_identity_errors"]


def test_known_material_and_different_known_formula_are_not_silently_accepted():
    with pytest.raises(ValueError, match="身份声明冲突"):
        assemble_components({}, {"cathode.material": row("NCM811"),
                                 "cathode.nominal_formula": row("LiFePO4")})


def test_equivalent_full_fields_are_stable_and_source_refs_validate():
    parameters = {"cathode.formulation": row("LiNi0.8Co0.1Mn0.1O2"),
                  "cathode.nominal_formula": row("LiNi0.8Mn0.1Co0.1O2")}
    scheme = assemble_components({}, parameters)
    assert scheme["cathode"] == "NCM811"
    assert not document_id_errors(dict(scheme=scheme, scheme_parameters=parameters, material_id_version=1))


def test_formula_patch_updates_and_withdrawn_source_removes_stale_identity():
    parameters = {"cathode.nominal_formula": row("LiMnO2")}
    scheme = scheme_with_fields({}, parameters)
    changed = scheme_with_fields(scheme, {"cathode.nominal_formula": row("LiNiO2")})
    assert changed["cathode"] == "LiNiO2"
    assert flatten_scheme(changed) == {"cathode.nominal_formula": "LiNiO2"}
    assert "cathode" not in scheme_with_fields(changed, {})


def test_commit_keeps_actual_source_and_lock_and_repeated_commit_is_noop(tmp_path):
    doc = {"scheme_parameters": {"cathode.nominal_formula": row("LiMnO2")}, "scheme": {}}
    commit_design(tmp_path, doc)
    revision = load(tmp_path)["revision"]
    assert "cathode.material" not in load(tmp_path)["parameters"]
    assert load(tmp_path)["parameters"]["cathode.nominal_formula"]["source_quote"] == "采用LiMnO2。"
    commit_design(tmp_path, doc)
    assert load(tmp_path)["revision"] == revision
    data = update(tmp_path, {"cathode.nominal_formula": row("LiMnO2")}, revision, actor="user")
    doc.pop("ledger_hash")
    doc["scheme_parameters"]["cathode.nominal_formula"] = row("LiNiO2")
    with pytest.raises(ValueError, match="锁定"):
        commit_design(tmp_path, doc)
    assert load(tmp_path) == data


def test_authorized_formula_revision_rebuilds_projection_and_stale_sources_are_detected(tmp_path):
    doc = {"scheme_parameters": {"cathode.nominal_formula": row("LiMnO2")}, "scheme": {}}
    commit_design(tmp_path, doc)
    doc["scheme_parameters"]["cathode.nominal_formula"] = row("LiNiO2")
    assert any("来源失效" in e for e in document_id_errors(doc))
    commit_design(tmp_path, doc)
    assert doc["scheme"]["cathode"] == "LiNiO2"
    assert not document_id_errors(doc)


def test_canonical_document_keeps_alias_source_values_consistent():
    parameters = {"anode.material": row("硅碳")}
    scheme = assemble_components({}, parameters)
    doc = canonical_document(dict(scheme=scheme, scheme_parameters=parameters))
    assert not document_id_errors(doc)


def test_open_material_alias_and_locked_formula_cannot_bypass_existing_lock(tmp_path):
    parameters = {"anode.main_material": row("硅碳", canonical_path="anode.material")}
    doc = canonical_document(dict(scheme=assemble_components({}, parameters), scheme_parameters=parameters))
    assert not document_id_errors(doc)
    data = ensure(tmp_path)
    update(tmp_path, {"cathode.nominal_formula": row("LiMnO2")}, data["revision"], actor="user")
    with pytest.raises(ValueError, match="锁定|身份声明冲突"):
        commit_design(tmp_path, dict(scheme={}, scheme_parameters={
            "cathode.new_formula": row("LiNiO2", canonical_path="cathode.nominal_formula")}))


def test_prepare_review_builds_identity_from_already_accepted_formula():
    from auto_battery_research.util.research_contract import prepare_review, fallback_requirements
    parameters = {"cathode.nominal_formula": row("LiMnO2"), "anode.material": row("硅碳"),
                  "electrolyte.formulation": row("完整配方XQ-27")}
    seed = dict(plan={"requirements": fallback_requirements("设计电池方案")}, scheme={}, draft="正文", evidence=[])
    pipeline = SimpleNamespace(writer=SimpleNamespace(llm=None), reviewer=SimpleNamespace(),
                               scheme_context={"parameters": parameters})
    prepare_review(pipeline, seed)
    assert seed["scheme"]["cathode"] == "LiMnO2"
    assert seed["scheme"]["electrolyte"] == "完整配方XQ-27"
    assert "cathode.material" not in seed["parameters"]
    assert seed["research_draft"]["scheme"] == seed["scheme"]


def test_adapter_assembles_before_geometry_and_preserves_unknown_gate(tmp_path, monkeypatch):
    from auto_battery_research.tools.rag_adapter import AbrRagAdapter
    from auto_battery_research.simulation import geometry_contract
    parameters = {"cathode.nominal_formula": row("LiMnO2"), "anode.material": row("硅碳"),
                  "electrolyte.formulation": row("完整配方XQ-27")}
    raw = dict(scheme={}, final_answer="\n".join(r["source_quote"] for r in parameters.values()), evidence=[{"passage_id": "p1"}],
               research_draft={"scheme_parameters": parameters}, geometry_candidate={})
    pipeline = SimpleNamespace(run=lambda q: copy.deepcopy(raw), relation_engine=RelationEngine(), writer_llm=None)
    seen = []
    def reject_geometry(candidate, scheme, evidence):
        seen.append(copy.deepcopy(scheme))
        raise ValueError("测试几何未提供")
    monkeypatch.setattr(geometry_contract, "build_geometry_contract", reject_geometry)
    adapter = AbrRagAdapter(config={"runtime_options": {"report_first": False}}, pipeline=pipeline)
    result = adapter.run_rag_design("测试组件组装", tmp_path)
    assert not result["success"]
    assert seen and seen[0]["cathode"] == "LiMnO2" and seen[0]["electrolyte"] == "完整配方XQ-27"
    assert "缺少必要组件" not in result["error"]
    assert "C0_UNVERIFIED_MATERIAL" in result["error"]
    doc = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    assert doc["scheme"]["electrolyte"] == "完整配方XQ-27"
    assert "cathode.material" not in doc["scheme_parameters"]


def test_report_identity_uses_formula_and_full_recipe_without_mutating_scientific_inputs():
    from auto_battery_research.util.report_narrative import component_paragraph, render_design
    from auto_battery_research.util.research_reporting import selected_summary, _render_current_choice
    formula = "Li₁.₁₆Na₀.₀₃Zn₀.₀₁Ni₀.₂Mn₀.₆O₂"
    recipe = "1 mol/L LiPF6/DMC–EMC（体积比1:1）"
    parameters = {"cathode.nominal_formula": row(formula), "anode.material": row("锂片"),
                  "electrolyte.formulation": dict(row(recipe), unit=None)}
    data = dict(parameters=parameters, derived={}, candidates=[])
    doc = dict(ledger_view=data, requirements={"scheme_required": True}, research={})
    before = copy.deepcopy(doc)
    for body in (render_design(data), selected_summary(doc), _render_current_choice(doc)):
        assert formula in body and recipe in body and "锂片" in body
        assert "None" not in body and "正极的具体规格尚未确定" not in body and "电解液的具体规格尚未确定" not in body
    assert "名义组成" in component_paragraph(data, "cathode", material_only=True)
    assert "配方" in component_paragraph(data, "electrolyte", material_only=True)
    assert doc == before and "cathode.material" not in parameters
    assert selected_summary(doc).count(formula) == 1


def test_report_identity_never_promotes_partial_or_conditional_recipe():
    from auto_battery_research.util.report_narrative import component_paragraph
    from auto_battery_research.util.research_reporting import selected_summary
    parameters = {"electrolyte.formulation": row("待定配方", conditional=True),
                  "cathode.base_formula": row("母体材料"), "electrolyte.salt": row("LiPF6")}
    data = dict(parameters=parameters)
    assert "尚未确定" in component_paragraph(data, "electrolyte", material_only=True)
    assert "尚未确定" in component_paragraph(data, "cathode", material_only=True)
    summary = selected_summary(dict(ledger_view=data, requirements={}, research={}))
    assert "待定配方" not in summary and "母体材料" not in summary and "LiPF6" not in summary


def test_report_identity_handles_explicit_open_canonical_path():
    from auto_battery_research.util.report_narrative import component_paragraph
    parameters = {"cathode.main_formula": row("未知材料XQ-27", canonical_path="cathode.nominal_formula")}
    assert "正极名义组成为未知材料XQ-27" in component_paragraph(dict(parameters=parameters), "cathode", material_only=True)


def test_explicit_lithium_counter_electrode_is_extracted_offline_with_exact_source():
    from auto_battery_research.util.scheme_ledger import collect_parameters
    from auto_battery_research.util.material_ids import source_matches
    quote = "| 锂金属扣式半电池 | 对电极为 Li 金属，评价正极本征行为 |"
    doc = dict(final_answer="## 用户锁定电池口径\n" + quote, scheme={})
    collect_parameters(doc, None)
    field = doc["scheme_parameters"]["anode.material"]
    assert field["value"] == "li_metal" and field["original_value"] == "Li 金属"
    assert field["source_quote"] == quote and source_matches("anode.material", field["value"], quote)
    assert not any(k.startswith("anode.") and k != "anode.material" for k in doc["scheme_parameters"])


@pytest.mark.parametrize("text", [
    "## 文献检索范围\n文献采用Li 金属对电极。", "## 风险与数据缺口\nLi 金属对电极提供锂源。",
    "## 电池装配\n对电极可选Li 金属或石墨。", "## 电池装配\n不采用Li 金属对电极。",
    "## 电池装配\n对电极为Li 金属或石墨。", "## 电池装配\n仅声明锂金属扣式半电池。",
])
def test_background_optional_or_negated_counter_electrode_is_not_extracted(text):
    from auto_battery_research.util.material_ids import declared_anode_parameters
    assert declared_anode_parameters(dict(final_answer=text), {}) == {}


def test_declared_counter_electrode_does_not_override_existing_identity_or_research_only():
    from auto_battery_research.util.material_ids import declared_anode_parameters
    doc = dict(final_answer="## 电池装配\n对电极为Li 金属。")
    assert declared_anode_parameters(doc, {"anode.material": row("graphite")}) == {}
    assert declared_anode_parameters(dict(doc, research_only=True), {}) == {}
