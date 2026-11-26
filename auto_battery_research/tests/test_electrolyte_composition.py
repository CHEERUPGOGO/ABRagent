"""电解液按组成分类，保留浓度/比例/角色并继续执行相容性规则。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.rag import RelationEngine, ReviewerAgent
from auto_battery_research.util.electrolyte_composition import parse_electrolyte, classify_electrolyte, looks_like_formulation
from auto_battery_research.util.material_ids import assemble_components, canonical_id, component_signature

pytestmark = pytest.mark.unit
RECIPE = "1 mol/L LiPF6/EC+DMC"


def scheme(recipe=RECIPE):
    return {"cathode": "LRMO", "anode": "li_metal", "electrolyte": recipe,
            "component_identities": {"electrolyte": {"kind": "formulation"}}}


@pytest.mark.parametrize("recipe", [RECIPE, "1M LiPF6 in EC/DMC", "1 M LiPF6 EC + DMC",
    "1.0 mol·L⁻¹ LiPF₆ / EC + DMC", "１ mol／L LiPF₆／EC＋DMC", "1 mol L^-1 LiPF6/EC/DMC",
    "1000 mM LiPF6 in EC/DMC", "1e0 mol/L LiPF6 in EC/DMC"])
def test_equivalent_molarity_is_recognized_without_losing_raw_text(recipe):
    original = scheme(recipe)
    before = copy.deepcopy(original)
    rules = RelationEngine().check_scheme(original)
    match = rules["material_matches"]["electrolyte"]
    assert original == before
    assert match["matched"] and match["basis"] == "composition" and match["rule_material_id"] == "carbonate_ec"
    parsed = match["composition"]
    assert parsed["raw"] == recipe and parsed["solvent_ratio"] is None
    assert parsed["salts"][0]["concentration"]["value"] == 1
    assert parsed["salts"][0]["concentration"]["unit"] == "mol/L"
    assert [r["name"] for r in parsed["solvents"]] == ["EC", "DMC"]
    assert parsed["additives"] == []
    assert not rules["rule_checks"]["rejects"]
    assert not rules["rule_checks"]["violations"]
    assert {r["rule_id"] for r in rules["recommendations"]} >= {"C1", "C3"}
    assert rules["confidence"] == "high"


@pytest.mark.parametrize("unit", ["m", "mol/kg", "mol kg⁻¹", "mol·kg^-1"])
def test_molality_is_never_molarity_or_exact_alias_bypass(unit):
    recipe = f"1{unit} LiPF6 in EC/DEC (1:1 v/v)"
    parsed = parse_electrolyte(recipe)
    assert parsed["complete"] and parsed["salts"][0]["concentration"]["unit"] == "mol/kg"
    rules = RelationEngine().check_scheme(scheme(recipe))
    assert not rules["material_matches"]["electrolyte"]["matched"]
    assert rules["rule_checks"]["rejects"]


@pytest.mark.parametrize("spec,expected_basis", [("(v/v=3/7)", "volume"), ("(3:7 v/v)", "volume"),
    ("（体积比3:7）", "volume"), ("(3:7 w/w)", "mass"), ("(3:7)", None)])
def test_ratio_is_retained_in_declared_order_without_assuming_basis(spec, expected_basis):
    parsed = parse_electrolyte(RECIPE + " " + spec)
    assert parsed["complete"], parsed
    assert parsed["solvent_ratio"]["values"] == [3, 7]
    assert parsed["solvent_ratio"]["basis"] == expected_basis
    assert parsed["solvent_ratio"]["components"] == ["EC", "DMC"]


@pytest.mark.parametrize("spec,values,basis", [
    ("（EC:DMC=1:1 体积比）", [1, 1], "volume"),
    ("(EC:DMC=3:7 v/v)", [3, 7], "volume"),
    ("(EC/DMC=v/v=3/7)", [3, 7], "volume"),
    ("(ec + dmc = 2:3 w/w)", [2, 3], "mass"),
    ("（ＥＣ：ＤＭＣ＝１：１ 体积比）", [1, 1], "volume"),
    ("(EC:DMC=1:4)", [1, 4], None),
])
def test_named_ratio_matches_body_solvents_and_keeps_declared_basis(spec, values, basis):
    recipe = RECIPE + spec
    original = scheme(recipe)
    before = copy.deepcopy(original)
    rules = RelationEngine().check_scheme(original)
    assert original == before
    match = rules["material_matches"]["electrolyte"]
    parsed = match["composition"]
    assert parsed["complete"], parsed
    assert parsed["raw"] == recipe and parsed["parser_version"] == 2
    assert match["matched"] and match["rule_material_id"] == "carbonate_ec"
    assert [s["name"] for s in parsed["solvents"]] == ["EC", "DMC"]
    assert parsed["solvent_ratio"]["components"] == ["EC", "DMC"]
    assert parsed["solvent_ratio"]["values"] == values
    assert parsed["solvent_ratio"]["basis"] == basis
    assert "=" in parsed["solvent_ratio"]["source_text"]
    assert not rules["rule_checks"]["rejects"]
    assert {r["rule_id"] for r in rules["recommendations"]} >= {"C1", "C3"}


def test_named_ratio_supports_three_solvents_and_separate_additive():
    recipe = "1M LiPF6 in EC/DMC/EMC (EC:DMC:EMC=2:3:5 v/v) + 2 wt% FEC"
    parsed = parse_electrolyte(recipe)
    assert parsed["complete"], parsed
    assert parsed["solvent_ratio"]["components"] == ["EC", "DMC", "EMC"]
    assert parsed["solvent_ratio"]["values"] == [2, 3, 5]
    assert parsed["solvent_ratio"]["basis"] == "volume"
    assert parsed["additives"][0]["name"] == "FEC"
    assert parsed["additives"][0]["amount"]["value"] == 2


def test_named_ratio_follows_body_order_without_sorting_or_inferring_volume():
    parsed = parse_electrolyte("1M LiPF6/DMC+EC (DMC:EC=7:3)")
    assert parsed["complete"], parsed
    assert parsed["solvent_ratio"]["components"] == ["DMC", "EC"]
    assert parsed["solvent_ratio"]["values"] == [7, 3]
    assert parsed["solvent_ratio"]["basis"] is None


@pytest.mark.parametrize("spec", [
    "(DMC:EC=1:1 v/v)",  # 即使等比例也不自动交换名称。
    "(EC:DEC=1:1 v/v)", "(EC:XQ27=1:1 v/v)",
    "(EC:EC=1:1 v/v)", "(EC:DMC:EMC=1:1:1 v/v)",
    "(EC:DMC=1:1:1 v/v)", "(EC:DMC=1 v/v)",
    "(EC:DMC=0:1 v/v)", "(EC:DMC=-1:1 v/v)",
    "(EC:DMC=1e309:1 v/v)", "(EC:DMC=v/v=1:1 w/w)",
    "(EC:DMC=1:1 体积比，无添加剂)", "(EC:DMC=1:1 v/v garbage)",
])
def test_named_ratio_conflicts_and_unparsed_tail_remain_fail_closed(spec):
    recipe = RECIPE + spec
    parsed = parse_electrolyte(recipe)
    assert not parsed["complete"] and parsed["diagnostics"] and parsed["raw"] == recipe
    rules = RelationEngine().check_scheme(scheme(recipe))
    assert not rules["material_matches"]["electrolyte"]["matched"]
    assert any(r["rule_id"] == "C0_UNVERIFIED_MATERIAL" for r in rules["rule_checks"]["rejects"])


def test_named_ratio_must_cover_all_body_solvents_at_its_location():
    for recipe in ["1M LiPF6/EC (EC:DMC=1:1 v/v)+DMC",
                   "1M LiPF6/EC+DMC+EMC (EC:DMC=1:1 v/v)",
                   "1M LiPF6/EC+DMC (EC:DMC=1:1 v/v)+2% FEC (1:1)"]:
        parsed = parse_electrolyte(recipe)
        assert not parsed["complete"] and parsed["diagnostics"]


def test_additives_and_diluents_keep_amounts_and_do_not_create_lhce():
    parsed = parse_electrolyte(RECIPE + " (1:1 v/v) + 2 wt% FEC + 1% VC + 30 vol% TTE")
    assert parsed["complete"], parsed
    assert [r["name"] for r in parsed["solvents"]] == ["EC", "DMC"]
    assert [r["name"] for r in parsed["additives"]] == ["FEC", "VC"]
    assert parsed["additives"][0]["amount"]["unit"] == "wt%"
    assert parsed["additives"][1]["amount"]["unit"] == "%"
    assert parsed["diluents"][0]["amount"]["value"] == 30
    rules = RelationEngine().check_scheme(scheme(parsed["raw"]))
    assert not rules["material_matches"]["electrolyte"]["matched"]
    assert rules["material_matches"]["electrolyte"]["rule_material_id"] != "lhce"


def test_explicit_positive_additive_is_seen_by_existing_mitigation_rules():
    rules = RelationEngine().check_scheme(dict(scheme(RECIPE + " + 2 wt% FEC"), cathode="LNMO"))
    assert rules["material_matches"]["electrolyte"]["matched"]
    assert not rules["rule_checks"]["violations"]
    assert {r["rule_id"] for r in rules["challenges"]} == {"C2"}
    assert {r["rule_id"] for r in rules["recommendations"]} >= {"C1", "C3"}


@pytest.mark.parametrize("recipe", [
    "1M LiPF6/EC+XQ27", "1M LiPF6/EC+DMC + FEC", "1M LiPF6/EC+DMC + 2% mystery",
    "1M LiPF6/EC+DMC 未选用FEC", "1M LiPF6/EC+DMC (不加FEC)", "1M LiPF6/EC+DMC + 0% FEC",
    "1M LiPF6/EC+DMC + 200 wt% FEC", "1M LiPF6/EC+DMC + -2% FEC", "1M LiPF6/EC+DMC/",
    "1M LiPF6/EC+DMC (1:1:1 v/v)", "1M LiPF6/EC (1:1 v/v)+DMC", "1M LiPF6/EC+DMC + 2% FEC garbage",
    "1M LiPF6/EC+DMC + 2% FEC + 3% FEC", "1M LiPF6 + 2M LiPF6 in EC/DMC",
    "-1M LiPF6/EC+DMC", "0M LiPF6/EC+DMC", "1e309 M LiPF6/EC+DMC",
])
def test_unparsed_or_ambiguous_parts_never_silently_disappear(recipe):
    parsed = parse_electrolyte(recipe)
    assert not parsed["complete"] and parsed["diagnostics"] and parsed["raw"] == recipe
    rules = RelationEngine().check_scheme(scheme(recipe))
    assert not rules["material_matches"]["electrolyte"]["matched"]
    assert any(r["rule_id"] == "C0_UNVERIFIED_MATERIAL" for r in rules["rule_checks"]["rejects"])


def test_multisalt_concentrations_keep_their_salt_binding_and_need_covered_policy():
    recipe = "1M LiPF6 + 0.2M LiDFOB in EC/DMC"
    parsed = parse_electrolyte(recipe)
    assert parsed["complete"]
    assert [(r["name"], r["concentration"]["value"]) for r in parsed["salts"]] == [("LiPF6", 1), ("LiDFOB", .2)]
    rules = RelationEngine().check_scheme(scheme(recipe))
    assert not rules["material_matches"]["electrolyte"]["matched"]


@pytest.mark.parametrize("recipe", ["4M LiPF6 in EC/DMC", "2M LiFSI in DME/DOL", "1M LiPF6 in PC",
    "1M LiPF6", "LiPF6/EC+DMC", "1M LiPF6 in FEC/EMC"])
def test_uncovered_or_partial_composition_is_not_guessed_as_standard_or_lhce(recipe):
    assert looks_like_formulation(recipe)
    rules = RelationEngine().check_scheme(scheme(recipe))
    assert not rules["material_matches"]["electrolyte"]["matched"]
    assert rules["material_matches"]["electrolyte"]["composition"]["diagnostics"]


def test_exact_registered_recipe_remains_usable_and_category_ids_are_unchanged():
    engine = RelationEngine()
    recipe = "1.2M LiPF6 in EC/DMC (v/v=3/7) + 0.1wt% MPS"
    match = engine.check_scheme(scheme(recipe))["material_matches"]["electrolyte"]
    assert match["matched"] and match["basis"] == "direct"
    assert match["rule_material_id"] == "1.2M_LIPF6_DMC-EC_MPS"
    for name in ["carbonate_ec", "lhce", "ether_ec"]:
        assert engine._scheme_material_id({"electrolyte": name}, "electrolyte") == name


def test_missing_or_broken_family_table_is_fail_closed():
    for data in [{}, {"families": None}, {"families": [{}]}, {"families": [None]}]:
        parsed = classify_electrolyte(RECIPE, data)
        assert parsed["complete"] and parsed["rule_material_id"] is None
    engine = RelationEngine()
    engine.electrolyte_families = {}
    assert not engine.check_scheme(scheme())["material_matches"]["electrolyte"]["matched"]


def test_full_recipe_and_lock_survive_assembly_and_rule_check(tmp_path):
    from auto_battery_research.util.scheme_ledger import ensure, update, commit_design, load
    row = dict(value=RECIPE, unit="", source="用户给定配方", basis="user_requirement", source_quote=RECIPE)
    ledger = ensure(tmp_path, RECIPE)
    ledger = update(tmp_path, {"electrolyte.formulation": row}, ledger["revision"], actor="user")
    params = copy.deepcopy(ledger["parameters"])
    assembled = assemble_components({}, params)
    assert assembled["electrolyte"] == RECIPE and canonical_id(RECIPE) == RECIPE
    before = copy.deepcopy(assembled)
    assert RelationEngine().check_scheme(assembled)["material_matches"]["electrolyte"]["matched"]
    assert assembled == before and params == ledger["parameters"]
    doc = dict(target=RECIPE, scheme=assembled, scheme_parameters={"electrolyte.formulation": row})
    commit_design(tmp_path, doc)
    assert load(tmp_path)["parameters"]["electrolyte.formulation"]["locked"]
    changed = assemble_components({}, {"electrolyte.formulation": dict(row, value="2M LiPF6/EC+DMC")})
    assert component_signature(changed) != component_signature(assembled)


def test_legacy_mining_parser_uses_both_solvents_and_keeps_quantities():
    from auto_battery_research.mining import parse_formulation_structured
    fid, data = parse_formulation_structured(RECIPE + " (1:1 v/v) + 2 wt% FEC")
    assert fid == "LIPF6_DMC-EC_FEC"
    assert data["solvents"] == ["EC", "DMC"] and data["additives"] == ["FEC"]
    assert data["composition"]["solvent_ratio"]["values"] == [1, 1]
    assert data["composition"]["additives"][0]["amount"]["value"] == 2
    fid, data = parse_formulation_structured("1m LiPF6 in EC/DMC")
    assert fid.startswith("1mol/kg_")
    assert data["concentration"] == ["1mol/kg"]


def test_reviewer_and_report_keep_classification_boundary():
    from auto_battery_research.util.report_readiness import notice
    prompts = []
    def chat(system, user, **kwargs):
        prompts.append(user)
        return json.dumps({"issues": ["高压界面仍需验证"], "confidence": "low", "error_type": "writing"})
    engine = RelationEngine()
    reviewer = ReviewerAgent(SimpleNamespace(available=True, chat=chat), engine)
    verdict = reviewer.run(question="核对电解液", draft_answer="采用" + RECIPE, scheme=scheme(),
                           evidence=[dict(passage_id="p1", source="离线种子", text="测试界面稳定性。")])
    assert "高压界面仍需验证" in verdict["issues"]
    assert '"basis": "composition"' in prompts[0]
    doc = dict(scheme=scheme(), rule_checks=engine.check_scheme(scheme()), report_readiness={"category": "CONDITIONAL"})
    assert "电解液规则口径" in notice(doc) and "溶剂比例未声明" in notice(doc)
    doc["scheme"]["electrolyte"] = "2M LiPF6/EC+DMC"
    assert "电解液规则口径" not in notice(doc)


def test_stage4_adapter_saves_composition_and_retains_scientific_caveats(tmp_path):
    from auto_battery_research.tools.rag_adapter import AbrRagAdapter
    from auto_battery_research.util.scheme_ledger import ensure, update
    from auto_battery_research.tests.test_parent_rule_matching import parameters
    params = parameters()
    params.pop("electrolyte.material")
    params["electrolyte.formulation"] = dict(value=RECIPE, unit="", source="用户要求", basis="user_requirement", source_quote=RECIPE)
    ledger = ensure(tmp_path, "按固定配方研究")
    update(tmp_path, {"electrolyte.formulation": params["electrolyte.formulation"]}, ledger["revision"], actor="user")
    text = "\n\n".join("## " + name + "\n\n" + "本课题材料与界面仍需同条件实验验证。" * 10 for name in
                       ("目标与设计路线", "推荐组合", "预期关键指标", "可行性依据", "风险与数据缺口"))
    raw = dict(scheme={}, repair_parameters=params, final_answer=text, evidence=[dict(passage_id="p1", source="离线种子", text="证据")],
               reviewer_output={"issues": ["界面和掺杂仍需验证"], "confidence": "low"}, confidence="low",
               review_passed=False, review_completed=True, geometry_error="仅测试材料分类，几何待定")
    pipeline = SimpleNamespace(run=lambda *a: copy.deepcopy(raw), relation_engine=RelationEngine(), writer_llm=None)
    adapter = AbrRagAdapter(config={"runtime_options": {"report_first": True}}, pipeline=pipeline)
    assert adapter.run_rag_design("按固定配方研究", tmp_path)["success"]
    saved = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    assert saved["scheme"]["electrolyte"] == RECIPE
    assert saved["ledger_view"]["parameters"]["electrolyte.formulation"]["locked"]
    assert saved["rule_checks"]["material_matches"]["cathode"]["rule_material_id"] == "LRMO"
    assert saved["rule_checks"]["material_matches"]["electrolyte"]["basis"] == "composition"
    assert not saved["rule_checks"]["rule_checks"]["rejects"]
    assert not saved["rule_checks"]["rule_checks"]["violations"]
    assert {r["rule_id"] for r in saved["rule_checks"]["recommendations"]} >= {"C1", "C3"}
    assert saved["review_passed"] is False and "界面和掺杂仍需验证" in saved["reviewer_output"]["issues"]
