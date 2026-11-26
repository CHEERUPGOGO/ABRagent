"""材料身份规范化与正文引用同步；原声明、物性及补丁保护保持一致。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_design_structure import document
from auto_battery_research.util.design_repair import apply_patch, authorize, blocks, review_design
from auto_battery_research.util.material_ids import canonical_parameters
from auto_battery_research.util.numeric_bindings import (
    binding_errors, display, normalize_material_references, occurrences, refresh, render,
)
from auto_battery_research.util.scheme_ledger import digest


DECLARATION = "锂金属箔（真密度0.534 g/cm³，理论比容量3860 mAh/g）"


def material_document():
    data = document()
    data.update(version=2, parameters={"anode.material": {
        "value": DECLARATION, "unit": "", "basis": "user_requirement",
        "source": "课题声明金属锂负极", "meaning": "负极材料", "quantity_kind": "text",
    }})
    data["sections"]["goals"]["body"] += "负极采用{{param:anode.material}}。"
    data["sections"]["metrics"]["body"] = "旧表述将文献推断写成了本体系已验证结论，需保留证据边界。[p1]"
    return data


def test_writer_normalizes_identity_without_losing_original_properties():
    from auto_battery_research.util.writer_protocol import parse
    data = material_document()
    before = copy.deepcopy(data)
    result = parse(data, {})
    parameters, text = result["numeric_parameters"], result["draft_answer"]
    material = parameters["anode.material"]
    assert material["value"] == "li_metal"
    assert material["original_value"] == DECLARATION
    assert DECLARATION in text and material["source_quote"] in text
    assert material["basis"] == "user_requirement" and material["locked"] is False
    assert not binding_errors(text, parameters) and refresh(text, parameters) == text
    assert result["structured_sections"] == before and data == before


def test_unrelated_paragraph_patch_is_not_blocked_by_existing_material_reference():
    from auto_battery_research.util.design_structure import render_design
    data = material_document()
    text, _ = render_design(data)
    parameters = canonical_parameters(data["parameters"])
    parameters["anode.material"].update(locked=True)
    quote = data["sections"]["metrics"]["body"]
    replacement = "该文献提供跨体系推断，尚未在本体系完成实验验证，需保留证据边界。[p1]"
    issue = {"id": "i", "quote": quote, "reason": "验证程度表述错误", "suggestion": "明确跨体系推断", "parameter_paths": []}
    permit = authorize(text, [issue], parameters)
    bid = permit["grants"]["i"]["blocks"][0]
    changed, fields = apply_patch(text, parameters, permit, {"base_hash": digest(text),
        "edits": [{"issue_id": "i", "block_id": bid, "text": replacement}]})
    assert replacement in changed and DECLARATION in changed
    assert fields == parameters and fields["anode.material"]["locked"]
    assert not binding_errors(changed, fields)
    before_blocks, after_blocks = blocks(text)[1], blocks(changed)[1]
    assert all(after_blocks[k] == v for k, v in before_blocks.items() if k != bid)


def test_review_checkpoint_saves_canonical_identity_and_original_reference_together(tmp_path):
    from auto_battery_research.util.design_structure import render_design
    data = material_document()
    text, contract = render_design(data)
    parameters = copy.deepcopy(data["parameters"])
    parameters["anode.material"]["source_quote"] = next(v for v in blocks(text)[1].values() if DECLARATION in v)
    seed = {"draft": text, "parameters": parameters, "evidence": [], "scheme": {},
            "numeric_contract": 1, "design_structure": contract}
    seen = []

    def review(**kwargs):
        saved = json.loads((tmp_path / "cp.json").read_text(encoding="utf-8"))
        assert saved["parameters"]["anode.material"]["value"] == "li_metal"
        assert saved["parameters"]["anode.material"]["original_value"] == DECLARATION
        assert not binding_errors(saved["draft"], saved["parameters"])
        seen.append(kwargs)
        return {"issues": [], "confidence": "high"}

    pipeline = SimpleNamespace(reviewer=SimpleNamespace(run=review), writer=SimpleNamespace(llm=None),
        _extract_energy_claim=lambda _: None, repair_checkpoint_path=tmp_path / "cp.json")
    changed, verdict, _, fields, _ = review_design(pipeline, seed, "设计")
    assert len(seen) == 1 and not verdict["issues"]
    assert changed == text and fields["anode.material"]["source_quote"] in changed
    assert fields["anode.material"]["original_value"] == DECLARATION


def test_legacy_canonical_label_restores_recorded_declaration_and_source_idempotently():
    raw = {"anode.material": {"value": DECLARATION, "unit": "", "basis": "user_requirement", "locked": True}}
    parameters = canonical_parameters(raw)
    old = "负极采用<!-- abr-param:anode.material -->锂金属<!-- /abr-value -->。"
    parameters["anode.material"]["source_quote"] = old
    before = copy.deepcopy(parameters)
    text, fields = normalize_material_references(old, parameters)
    assert DECLARATION in text and fields["anode.material"]["source_quote"] == text
    assert fields["anode.material"]["locked"] and not binding_errors(text, fields)
    assert parameters == before and normalize_material_references(text, fields) == (text, fields)


@pytest.mark.parametrize("changed", [DECLARATION.replace("3860", "3861"), "石墨", "锂片"])
def test_material_normalization_does_not_accept_unrecorded_display_edits(changed):
    raw = {"anode.material": {"value": DECLARATION, "unit": "", "basis": "engineering_proposal"}}
    parameters = canonical_parameters(raw)
    text = render("负极采用{{param:anode.material}}。", parameters)
    altered = text.replace(DECLARATION, changed)
    normalized, fields = normalize_material_references(altered, parameters)
    assert normalized == altered and binding_errors(normalized, fields)
    permit = {"base_hash": digest(altered), "parameters_hash": digest(fields), "grants": {}}
    with pytest.raises(ValueError, match="补丁引用的显示值"):
        apply_patch(altered, fields, permit, {"base_hash": digest(altered)})


def test_previous_material_original_value_cannot_override_current_identity():
    parameters = {"anode.material": {"value": "li_metal", "original_value": "硅碳", "unit": ""}}
    assert display("param", "anode.material", parameters) == "锂金属"
    text = "负极采用<!-- abr-param:anode.material -->硅碳<!-- /abr-value -->。"
    changed, fields = normalize_material_references(text, parameters)
    assert "original_value" not in fields["anode.material"]
    assert changed == text and binding_errors(changed, fields)


def test_nonmaterial_original_value_and_unknown_material_are_not_reinterpreted():
    parameters = {"cathode.nominal_formula": {"value": "Li1.2Mn0.6Ni0.2O2", "original_value": "旧配方", "unit": ""},
                  "anode.material": {"value": "未知复合材料Q（密度1.23 g/cm³）", "unit": ""}}
    text = render("正极{{param:cathode.nominal_formula}}，负极{{param:anode.material}}。", parameters)
    assert "旧配方" not in text and "密度1.23 g/cm³" in text
    assert normalize_material_references(text, parameters) == (text, parameters)
    assert [key for _, _, _, key, _ in occurrences(text)] == list(parameters)
