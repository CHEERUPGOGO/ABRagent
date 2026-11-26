"""声明与引用保护，以及显式历史计算诊断的离线回归。"""
import copy
import json
from functools import partial
from types import SimpleNamespace

import pytest

from auto_battery_research.util.numeric_consistency import audit, canonical, normalized_number
from auto_battery_research.util.numeric_bindings import render, refresh, binding_errors, declarations, occurrences
from auto_battery_research.util.design_repair import apply_patch, authorize, blocks, review_design
from auto_battery_research.util.scheme_ledger import digest

# 旧公式工具只在本文件显式调用；默认模型计算契约见test_model_calculations。
audit = partial(audit, legacy_calculations=True)


def row(value, unit="", **extra):
    return dict({"value": value, "unit": unit, "basis": "engineering_proposal", "source": "明确工程声明"}, **extra)


def params():
    return {"cathode.L_um": row(25, "um"), "cathode.porosity": row(.46),
            "cathode.electrode_area": row(2.01, "cm2"), "electrolyte.volume_uL": row(60, "uL")}


def test_aliased_units_feed_the_same_calculation_without_mutation():
    data = params()
    data["electrolyte.volume_uL"] = row(.06, "mL")
    data["cathode.electrode_area"] = row(201, "mm2")
    data["electrolyte.E_over_P"] = row(40, denominator="正极孔体积", quantity_kind="ratio")
    old = copy.deepcopy(data)
    result = audit(data)
    calc = result["derived"]["condition.calculated_electrolyte_to_cathode_pore_ratio"]
    assert calc["status"] == "CONFLICT" and calc["value"] == pytest.approx(60/(25*.46*.1*2.01))
    assert result["issues"][0]["code"] == "CALCULATION_CONFLICT" and data == old


def test_missing_area_or_conflicting_porosity_never_falls_back_to_defaults():
    data = params()
    data.pop("cathode.electrode_area")
    result = audit(data)["derived"]["condition.calculated_electrolyte_to_cathode_pore_ratio"]
    assert result["value"] is None and result["status"] == "INCOMPLETE"
    data = params()
    data["cathode.alternative_porosity"] = row(.30, canonical_path="cathode.porosity")
    result = audit(data)
    assert any(i["code"] == "DECLARATION_CONFLICT" for i in result["issues"])
    assert result["derived"]["condition.calculated_electrolyte_to_cathode_pore_ratio"]["value"] is None


def test_scope_literature_conditions_and_denominators_are_not_merged():
    data = params()
    data["cathode.other"] = row(.3, canonical_path="cathode.porosity", group_id="sensitivity")
    data["cathode.literature"] = row(.2, canonical_path="cathode.porosity", basis="literature", passage_id="p1")
    data["cathode.Zn_TM"] = row(.025, canonical_path="cathode.Zn_ratio", quantity_kind="site_fraction", denominator="TM位")
    data["cathode.Zn_total"] = row(.01, canonical_path="cathode.Zn_ratio", quantity_kind="site_fraction", denominator="总阳离子")
    result = audit(data)
    assert not result["issues"]
    assert result["derived"]["cathode.pore_volume_uL_cm2"]["value"] == pytest.approx(1.15)
    assert not any("capacity" in key and value["value"] is not None for key, value in result["derived"].items())


def test_upper_bound_and_experimental_ceiling_are_distinct_roles():
    data = {"cathode.Na_limit": row(.05, "f.u.", canonical_path="cathode.Na_amount", quantity_kind="formula_coefficient", quantity_role="upper_bound"),
            "cathode.Na_max_test": row(.04, "f.u.", canonical_path="cathode.Na_amount", quantity_kind="formula_coefficient", quantity_role="experiment_max")}
    assert not audit(data)["issues"]
    data["cathode.Na_max_test"]["value"] = .06
    assert audit(data)["issues"][0]["code"] == "INVALID_BOUNDS"


def test_explicit_site_ratios_are_separate_from_capacity_claims():
    data = {"cathode.Zn_amount": row(.02, quantity_kind="formula_coefficient"),
            "cathode.TM_amount": row(.8, quantity_kind="formula_coefficient"),
            "cathode.total_cations": row(2, quantity_kind="formula_coefficient"),
            "cathode.Zn_TM": row(.025, quantity_kind="site_fraction", denominator="TM位",
                                  ratio_inputs=["cathode.Zn_amount", "cathode.TM_amount"]),
            "cathode.Zn_total": row(.01, quantity_kind="site_fraction", denominator="总阳离子",
                                     ratio_inputs=["cathode.Zn_amount", "cathode.total_cations"])}
    result = audit(data)
    assert not result["issues"]
    assert result["derived"]["calculated.cathode.Zn_TM"]["value"] == pytest.approx(.025)
    assert result["derived"]["calculated.cathode.Zn_total"]["value"] == .01
    assert not any("capacity" in key and value["value"] is not None for key, value in result["derived"].items())
    data["cathode.TM_amount"]["group_id"] = "different_sample"
    assert audit(data)["derived"]["calculated.cathode.Zn_TM"]["status"] == "INCOMPLETE"


def test_literature_field_is_not_used_as_main_geometry_by_legacy_calculator():
    from auto_battery_research.util.scheme_ledger import calculate
    data = {"cathode.L_um": row(25, "um"), "cathode.porosity": row(.3, basis="literature", passage_id="p1")}
    derived, _ = calculate({"parameters": data}, legacy_calculations=True)
    assert derived["cathode.pore_volume_uL_cm2"]["value"] is None


def test_ratio_cycle_is_incomplete():
    data = {"cathode.a": row(.2, quantity_kind="ratio", denominator="指定分母", ratio_inputs=["cathode.a", "cathode.b"]),
            "cathode.b": row(1, quantity_kind="ratio")}
    result = audit(data)["derived"]["calculated.cathode.a"]
    assert result["status"] == "INCOMPLETE" and result["value"] is None


def test_unknown_denominator_is_incomplete_and_never_compared_as_cathode_pores():
    data = params()
    data["electrolyte.E_over_P"] = row(40, denominator="全部孔体积")
    result = audit(data)["derived"]["condition.calculated_electrolyte_to_cathode_pore_ratio"]
    assert result["status"] == "INCOMPLETE" and "分母" in result["missing"][0]


@pytest.mark.parametrize("value,units", [(float('nan'), "um"), (True, "um"), (25, "kg"), (float('inf'), "um")])
def test_invalid_numbers_and_units_are_reported(value, units):
    result = audit({"cathode.L_um": row(value, units)})
    assert result["issues"][0]["code"] == "INVALID_QUANTITY"


def test_loading_fraction_and_rate_checks():
    data = {"cathode.L_um": row(25, "um"), "cathode.compaction_density": row(2.25, "g/cm3"),
        "cathode.areal_mass_total": row(5.625, "mg/cm2"), "cathode.areal_mass_active": row(4.5, "mg/cm2"),
        "cathode.active_material_mass_fraction": row(.8), "cathode.superP_mass_fraction": row(.1), "cathode.PVDF_mass_fraction": row(.1),
        "condition.C_rate": row(.5, "C"), "condition.C_rate_current_density": row(200, "mA/g"), "condition.current_density": row(100, "mA/g")}
    result = audit(data)
    assert not result["issues"]
    assert result["derived"]["condition.calculated_current_density"]["value"] == 100
    data["condition.current_density"]["value"] = 125
    assert any(i["code"] == "CALCULATION_CONFLICT" for i in audit(data)["issues"])


def binding_draft():
    data = params()
    template = ("主方案孔隙率{{param:cathode.porosity}}。\n\n| 主组 | {{param:cathode.porosity}} |\n\n"
                "注液量{{param:electrolyte.volume_uL}}，E/P={{derived:condition.calculated_electrolyte_to_cathode_pore_ratio}}。\n\n"
                "文献中孔隙率46%，另一对照组为30%，保留原工况。[p1]")
    text = render(template, data, legacy_calculations=True)
    quote = blocks(text)[1]["b0"]
    data["cathode.porosity"]["source_quote"] = quote
    return text, data


def test_parameter_patch_updates_inputs_and_preserves_historical_derived_snapshot():
    text, data = binding_draft()
    issue = {"id": "i", "quote": blocks(text)[1]["b0"], "reason": "已有依据需要调整", "suggestion": "改为0.3", "parameter_paths": ["cathode.porosity"]}
    permission = authorize(text, [issue], data)
    assert len(permission["grants"]["i"]["blocks"]) == 2
    field = dict(data["cathode.porosity"], value=.3)
    patch = {"base_hash": digest(text), "parameters": [{"issue_id": "i", "path": "cathode.porosity", "field": field}]}
    updated, changed = apply_patch(text, data, permission, patch)
    assert [v for _, _, kind, key, v in occurrences(updated) if key == "cathode.porosity"] == ["30%", "30%"]
    assert "25.957" in updated and "39.801" not in updated and not binding_errors(updated, changed)
    assert "文献中孔隙率46%，另一对照组为30%" in updated
    assert changed["cathode.porosity"]["source_quote"] in updated
    assert refresh(updated, changed) == updated and data["cathode.porosity"]["value"] == .46


def test_authorized_parameter_patch_accepts_already_refreshed_display():
    text, data = binding_draft()
    original = blocks(text)[1]["b0"]
    issue = {"id": "i", "quote": original, "reason": "修订工程孔隙率", "suggestion": "调整为30%",
             "parameter_paths": ["cathode.porosity"]}
    permit = authorize(text, [issue], data)
    replacement = original.replace("46%", "30%")
    field = dict(data["cathode.porosity"], value=.3, source_quote=replacement)
    patched, parameters = apply_patch(text, data, permit, {"base_hash": digest(text),
        "edits": [{"issue_id": "i", "block_id": "b0", "text": replacement}],
        "parameters": [{"issue_id": "i", "path": "cathode.porosity", "field": field}]})
    assert "30%" in patched and not binding_errors(patched, parameters)
    assert parameters["cathode.porosity"]["value"] == .3
    with pytest.raises(ValueError):
        apply_patch(text, data, permit, {"base_hash": digest(text),
            "edits": [{"issue_id": "i", "block_id": "b0", "text": replacement}]})


def test_numeric_lock_and_alias_protection_and_marker_tampering():
    text, data = binding_draft()
    data["cathode.porosity"]["locked"] = True
    issue = {"id": "i", "quote": blocks(text)[1]["b0"], "reason": "更改", "suggestion": "改30%", "parameter_paths": ["cathode.porosity"]}
    permit = authorize(text, [issue], data)
    with pytest.raises(ValueError, match="锁定|越权"):
        apply_patch(text, data, permit, {"base_hash": digest(text), "parameters": [{"issue_id": "i", "path": "cathode.porosity", "field": row(.3)}]})
    with pytest.raises(ValueError, match="同义"):
        declarations({"parameters": {"cathode.other": row(.3, canonical_path="cathode.porosity")}}, data)
    body = blocks(text)[1]["b0"]
    for new in (body.replace("46%", "30%"), "主方案孔隙率46%。"):
        with pytest.raises(ValueError):
            apply_patch(text, data, permit, {"base_hash": digest(text), "edits": [{"issue_id": "i", "block_id": "b0", "text": new}]})


def test_writer_v2_registers_parameters_before_rendering_and_research_extraction():
    from auto_battery_research.rag import WriterAgent
    from auto_battery_research.tests.test_design_structure import document
    data = document()
    data.update(version=2, parameters={"electrolyte.volume": row(60, "uL", meaning="主方案注液量")})
    data["sections"]["goals"]["body"] += "主方案注液量{{param:electrolyte.volume}}。"
    agent = WriterAgent(SimpleNamespace(available=True, chat=lambda *a, **k: json.dumps(data, ensure_ascii=False)))
    out = agent.run("设计材料方案", {}, [{"passage_id": "p1", "source": "离线文献", "text": "可靠来源。"}])
    assert not out["fallback"] and out["numeric_contract"] == 1
    assert "60 μL" in out["draft_answer"]
    assert out["numeric_parameters"]["electrolyte.volume"]["source_quote"] in out["draft_answer"]


def test_conflicts_survive_reviewer_approval_without_authorized_sources(tmp_path):
    from auto_battery_research.tests.test_design_structure import document
    from auto_battery_research.util.design_structure import render_design
    text, contract = render_design(document())
    data = params()
    data["cathode.conflicting_porosity"] = row(.3, canonical_path="cathode.porosity")
    seen = []
    def review(**kwargs):
        seen.append(kwargs)
        return {"issues": [], "confidence": "high"}
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(run=review), writer=SimpleNamespace(llm=None),
        _extract_energy_claim=lambda _: None, repair_checkpoint_path=tmp_path / "cp.json")
    seed = {"draft": text, "parameters": data, "evidence": [], "scheme": {}, "numeric_contract": 1, "design_structure": contract}
    _, verdict, *_ = review_design(pipeline, seed, "设计")
    assert len(seen) == 1 and any("数值一致性" in i for i in verdict["issues"])
    assert seed["numeric_consistency"]["issues"] and seed.get("repair_rounds", 0) == 0


def test_transaction_verifier_sees_updated_dependencies_and_commits_together():
    from auto_battery_research.util.repair_interfaces import execute
    text, data = binding_draft()
    issue = {"id": "i", "quote": blocks(text)[1]["b0"], "reason": "调整工程取值", "suggestion": "改为30%", "parameter_paths": ["cathode.porosity"]}
    permit = authorize(text, [issue], data)
    seen = []
    def chat(system, user, **kwargs):
        payload = json.loads(user)
        seen.append(kwargs["trace_tag"])
        if kwargs["trace_tag"] == "writer_patch":
            interfaces = payload["interface_contract"]["interfaces"]
            return json.dumps({"base_hash": payload["permission"]["base_hash"], "submissions": [
                {"interface_id": iid, "replacement": entry["original"], "parameter_changes": [
                    {"path": "cathode.porosity", "field": dict(data["cathode.porosity"], value=.3)}] if n == 0 else []}
                for n, (iid, entry) in enumerate(interfaces.items())]})
        rendered = "\n".join(p for txn in payload["transactions"].values() for p in txn["paragraphs"].values())
        assert "30%" in rendered and "46%" not in rendered
        return json.dumps({"verdicts": [{"group_id": g, "issue_id": i, "resolved": True, "reason": "全部依赖已同步"}
                                        for g, txn in payload["transactions"].items() for i in txn["issue_ids"]], "new_issues": []})
    llm = SimpleNamespace(chat=chat)
    changed, fields, entry = execute(SimpleNamespace(llm=llm), llm, text, data, permit, [issue], [])
    assert seen == ["writer_patch", "repair_verify"] and entry["status"] == "APPLIED"
    assert fields["cathode.porosity"]["value"] == .3 and not binding_errors(changed, fields)


def test_plain_research_quote_can_locate_hidden_numeric_markers_without_relaxing_values():
    from auto_battery_research.util.research_support import locate_sources
    text, _ = binding_draft()
    row = {"source_quotes": ["主方案孔隙率46%。"]}
    spans = locate_sources(row, {"final_answer": text})
    assert text[spans[0]["start"]:spans[0]["end"]] == blocks(text)[1]["b0"]
    with pytest.raises(ValueError):
        locate_sources({"source_quotes": ["主方案孔隙率30%。"]}, {"final_answer": text})


def test_new_proposal_can_fill_only_an_unlocked_null_slot():
    data = {"cathode.L_um": row(None, "um", locked=False)}
    proposed = {"parameters": {"cathode.L_um": row(25, "um")}}
    assert declarations(proposed, data)["cathode.L_um"]["value"] == 25
    data["cathode.L_um"]["locked"] = True
    with pytest.raises(ValueError):
        declarations(proposed, data)


def test_areal_capacity_uses_explicit_capacity_not_c_rate_reference():
    params = {"cathode.loading_mg_cm2": row(15.4, "mg/cm2"), "cathode.active_fraction": row(.8),
              "cathode.capacity_mAh_g": row(250, "mAh/g"), "condition.C_rate_reference_current": row(200, "mA/g")}
    result = audit(params)["derived"]["cathode.areal_capacity_mAh_cm2"]
    assert result["value"] == pytest.approx(3.08)
    assert "condition.C_rate_reference_current" not in result["paths"]
    params.pop("cathode.capacity_mAh_g")
    missing = audit(params)["derived"]["cathode.areal_capacity_mAh_cm2"]
    assert missing["value"] is None and "cathode.capacity_mAh_g" in missing["missing"]


def test_areal_capacity_does_not_mix_other_scope_or_wrong_units():
    params = {"cathode.loading_mg_cm2": row(15.4, "mg/cm2"), "cathode.active_fraction": row(.8),
              "cathode.capacity_mAh_g": row(250, "mA/g")}
    assert audit(params)["derived"]["cathode.areal_capacity_mAh_cm2"]["value"] is None
    params["cathode.capacity_mAh_g"].update(unit="mAh/g", purpose="对照")
    assert audit(params)["derived"]["cathode.areal_capacity_mAh_cm2"]["value"] is None


def test_unregistered_literals_are_visible_as_unclassified_not_auto_rewritten():
    text, data = binding_draft()
    result = audit(data, text)
    assert [m["quote"] for m in result["unbound_mentions"]] == ["46%", "30%"]
    assert all(m["scope"] == "unclassified" for m in result["unbound_mentions"])


def test_changed_metadata_cannot_hide_existing_numeric_conflict():
    text, data = binding_draft()
    data["cathode.alias"] = row(.46, canonical_path="cathode.porosity")
    issue = {"id": "i", "quote": blocks(text)[1]["b0"], "reason": "修改", "suggestion": "改30%", "parameter_paths": ["cathode.porosity"]}
    permit = authorize(text, [issue], data)
    patch = {"base_hash": digest(text), "parameters": [{"issue_id": "i", "path": "cathode.porosity", "field": dict(data["cathode.porosity"], value=.3)}]}
    with pytest.raises(ValueError, match="数值一致性"):
        apply_patch(text, data, permit, patch)
