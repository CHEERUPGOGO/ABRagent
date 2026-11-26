"""模型预算契约：无固定计算链、方法与结果保留、旧引用恢复及独立复核。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.numeric_bindings import (INSTRUCTIONS, binding_errors, declarations,
    refresh, render, synchronize)
from auto_battery_research.util.numeric_consistency import audit, render_audit
from auto_battery_research.util.scheme_ledger import calculate
from auto_battery_research.util.design_repair import apply_patch, authorize
from auto_battery_research.util.repair_interfaces import execute
from auto_battery_research.util.model_calculations import REVIEW_INSTRUCTIONS


def field(value, unit="", **extra):
    return dict(value=value, unit=unit, basis="engineering_proposal", source="当前方案明确声明", **extra)


def parameters():
    return {"cathode.active_loading_mg_cm2": field(3, "mg/cm2"),
            "cathode.active_mass_fraction": field(.8, denominator="正极干涂层总质量", quantity_kind="ratio"),
            "cathode.L_um": field(30, "um")}


def historical_placeholder():
    return ("总面载量 = <!-- abr-derived:cathode.calculated_loading_mg_cm2 -->"
            "未完成计算（缺失或歧义输入：cathode.compaction_g_cm3）<!-- /abr-value -->")


def test_default_audit_has_no_fixed_formula_dependencies_or_results():
    params = parameters()
    before = copy.deepcopy(params)
    result = audit(params, "总面载量=3/0.80=3.75 mg/cm²；压实密度=3.75/30×10=1.25 g/cm³。")
    assert result["calculation_policy"] == "model"
    assert result["derived"] == {} and not result["issues"]
    assert not result["checks"] and result["status"] == "CONSISTENT"
    assert result["unbound_mentions"] and params == before
    assert "cathode.active_fraction" not in params


def test_method_descriptions_and_reviewer_own_calculation_responsibility():
    assert "计算方法参考" in INSTRUCTIONS and "总面载量 = 活性物面载量" in INSTRUCTIONS
    assert "代入过程" in INSTRUCTIONS and "不要求注册固定计算ID" in INSTRUCTIONS
    assert "算术" in REVIEW_INSTRUCTIONS and "不因没有derived标记" in REVIEW_INSTRUCTIONS


def test_model_result_can_be_registered_with_method_and_explicit_inputs():
    result = field(3.75, "mg/cm2", meaning="模型计算的总面载量")
    result["source"] = "由已声明的活性面载量3 mg/cm²除以活性质量分数0.80得到3.75 mg/cm²，同一正极干涂层口径"
    params = declarations({"parameters": {"cathode.coating_budget": result}}, parameters())
    text = render("总面载量=3/0.80={{param:cathode.coating_budget}}，属于工程估算。", params)
    assert "3.75" in text and "工程估算" in text and not binding_errors(text, params)
    assert audit(params)["derived"] == {} and params["cathode.coating_budget"]["source"] == result["source"]


def test_new_program_derived_tokens_require_model_to_supply_the_result():
    with pytest.raises(ValueError, match="程序派生引用已停用"):
        render("总面载量{{derived:cathode.calculated_loading_mg_cm2}}", parameters())


def test_historical_derived_display_is_not_recomputed_or_a_binding_failure():
    params = parameters()
    body = render("厚度{{param:cathode.L_um}}；", params) + historical_placeholder()
    updated = copy.deepcopy(params)
    updated["cathode.L_um"]["value"] = 40
    result = refresh(body, updated)
    assert "40 μm" in result and historical_placeholder() in result
    assert not binding_errors(result, updated)


@pytest.mark.parametrize("resolved", [True, False])
def test_authorized_model_calculation_replaces_old_placeholder_after_review(resolved):
    params = parameters()
    text = render("活性面载量{{param:cathode.active_loading_mg_cm2}}，活性质量分数{{param:cathode.active_mass_fraction}}，厚度{{param:cathode.L_um}}。", params)
    original = historical_placeholder()
    text += "\n\n" + original
    replacement = "总面载量=3/0.80=3.75 mg/cm²；压实密度=3.75/30×10=1.25 g/cm³（当前干涂层口径的工程估算）。"
    issue = {"id": "calculation", "quote": original, "reason": "预算未完成", "suggestion": "写清方法和代入结果",
             "parameter_paths": ["cathode.active_fraction"]}
    permit = authorize(text, [issue], params)
    assert permit["grants"]["calculation"]["fields"] == []
    calls = []
    def chat(system, user, **options):
        request = json.loads(user)
        calls.append(options["trace_tag"])
        if options["trace_tag"] == "writer_patch":
            return json.dumps({"base_hash": permit["base_hash"], "submissions": [
                {"interface_id": iid, "replacement": replacement, "parameter_changes": []}
                for iid in request["interface_contract"]["interfaces"]]})
        assert "复核计算方法" in system
        assert all(replacement in "\n".join(t["paragraphs"].values()) for t in request["transactions"].values())
        return json.dumps({"verdicts": [{"group_id": gid, "issue_id": "calculation", "resolved": resolved,
            "reason": "核对了输入、公式、单位和工程估算边界"} for gid in request["transactions"]], "new_issues": []})
    model = SimpleNamespace(chat=chat)
    result, values, record = execute(SimpleNamespace(llm=model), model, text, params, permit, [issue], [])
    assert values == params and "cathode.active_fraction" not in values
    assert calls == ["writer_patch", "repair_verify"]
    if resolved:
        assert record["status"] == "APPLIED" and replacement in result and "abr-derived" not in result
    else:
        assert record["status"] == "REJECTED" and result == text


def test_removing_old_calculation_cannot_remove_or_rebind_an_original_input():
    params = parameters()
    original = render("厚度{{param:cathode.L_um}}；", params) + historical_placeholder()
    legal = render("厚度{{param:cathode.L_um}}；", params) + "模型预算：3/0.8=3.75 mg/cm²。"
    assert "3.75" in synchronize(original, legal, params, params)
    for illegal in ("模型预算：3/0.8=3.75 mg/cm²。", legal.replace("30 μm", "40 μm")):
        with pytest.raises(ValueError):
            synchronize(original, illegal, params, params)


def test_content_patch_cannot_introduce_a_new_program_calculation():
    params = parameters()
    original = render("厚度{{param:cathode.L_um}}。", params)
    with pytest.raises(ValueError, match="不能新增程序派生引用"):
        synchronize(original, original + historical_placeholder(), params, params)


def test_ledger_has_no_implicit_chain_or_missing_budget_requirements():
    params = parameters()
    params.update({"cathode.porosity": field(.65), "cathode.electrode_area": field(1.54, "cm2"),
                   "electrolyte.volume": field(80, "uL")})
    before = copy.deepcopy(params)
    derived, issues = calculate({"parameters": params})
    assert derived == {} and not issues and params == before


def test_report_does_not_turn_old_formula_inputs_into_new_parameter_gaps():
    from auto_battery_research.util.report_narrative import missing_suggestions
    data = {"parameters": dict(parameters(), **{"cathode.D_s_m2s": field(None, "m2/s")}),
            "derived": {"old_loading": {"value": None, "missing": ["cathode.compaction_g_cm3", "cathode.active_fraction"]}}}
    assert set(missing_suggestions(data)) == {"cathode.D_s_m2s"}


def test_model_arithmetic_is_not_overwritten_or_claimed_as_program_validated():
    params = parameters()
    params["cathode.loading_mg_cm2"] = field(999, "mg/cm2", meaning="待Reviewer核对的模型预算结果")
    before = copy.deepcopy(params)
    result = audit(params, "待复核算式：3/0.8=999 mg/cm²。")
    assert result["derived"] == {} and params == before
    assert not any(i["code"] == "CALCULATION_CONFLICT" for i in result["issues"])
    appendix = render_audit(result)
    assert "由模型完成" in appendix and "不表示程序已计算验证" in appendix
    assert "未绑定本身不构成错误" in appendix


@pytest.mark.parametrize("bad", [field(float("nan"), "um"), field(30, "kg"), field(-1, "um")])
def test_basic_input_validation_remains_active(bad):
    result = audit({"cathode.L_um": bad})
    assert result["issues"] and result["issues"][0]["code"] == "INVALID_QUANTITY"


def test_same_scope_alias_conflict_and_user_lock_remain_protected():
    params = {"cathode.L_um": field(30, "um", locked=True),
              "cathode.thickness_um": field(40, "um")}
    assert any(i["code"] == "DECLARATION_CONFLICT" for i in audit(params)["issues"])
    text = render("厚度{{param:cathode.L_um}}。", params)
    issue = {"id": "i", "quote": text, "reason": "修改厚度", "suggestion": "改40", "parameter_paths": ["cathode.L_um"]}
    permit = authorize(text, [issue], params)
    with pytest.raises(ValueError, match="锁定|越权"):
        apply_patch(text, params, permit, {"base_hash": permit["base_hash"], "parameters": [
            {"issue_id": "i", "path": "cathode.L_um", "field": field(40, "um")}]})


def test_writer_v2_accepts_model_computation_using_existing_custom_input_names():
    from auto_battery_research.tests.test_design_structure import document
    from auto_battery_research.util.writer_protocol import normalize, parse
    data = document()
    data.update(version=2, parameters={})
    data["sections"]["goals"]["body"] += (
        "活性面载量{{param:cathode.active_loading_mg_cm2}}，活性质量分数{{param:cathode.active_mass_fraction}}；"
        "总面载量=3/0.80=3.75 mg/cm²，厚度{{param:cathode.L_um}}时压实密度=3.75/30×10=1.25 g/cm³，均为工程估算。")
    result = parse(normalize(data), parameters())
    assert result["calculation_policy"] == "model" and not result["fallback"]
    assert "3.75 mg/cm²" in result["draft_answer"] and "未完成计算" not in result["draft_answer"]
    assert "cathode.active_fraction" not in result["numeric_parameters"]


def test_v3_compiler_accepts_model_method_and_results_without_fixed_ids():
    from auto_battery_research.util.stage4_document import compile_document, SECTION_IDS
    content = {"version": 3, "sections": {key: {"blocks": [{"id": key + "_p", "type": "paragraph",
        "text": "当前方案保留科学适用范围与验证边界。"}]} for key in SECTION_IDS},
        "research_links": {"answers": [], "candidates": [], "arguments": []}}
    content["sections"]["goals"]["blocks"][0]["text"] = (
        "活性面载量{{param:cathode.active_loading_mg_cm2}}除以质量分数{{param:cathode.active_mass_fraction}}，"
        "总面载量=3/0.80=3.75 mg/cm²；厚度{{param:cathode.L_um}}时压实密度=1.25 g/cm³，待科学Reviewer复核。")
    compiled = compile_document(content, parameters())
    assert compiled.valid, compiled.diagnostics
    assert "3.75 mg/cm²" in compiled.markdown and "abr-derived" not in compiled.markdown
    assert all(d["code"] != "NUMERIC_RELATION" for d in compiled.diagnostics)
