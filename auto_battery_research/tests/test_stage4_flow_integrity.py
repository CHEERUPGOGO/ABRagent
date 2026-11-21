"""实际运行暴露的五类流程问题：保留审核、锁定、完整响应与恢复依据。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_research_support import sample, verdict
from auto_battery_research.tests.test_research_narrative import fixture, review
from auto_battery_research.util.research_support import normalize, apply_review, supported, fingerprint
from auto_battery_research.util import research_narrative as narrative
from auto_battery_research.util.scheme_ledger import digest


@pytest.mark.parametrize("empty", [None, {}, []])
def test_empty_conditions_preserve_review_after_ledger_commit(tmp_path, empty):
    from auto_battery_research.util.scheme_ledger import ensure, update
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    key = row["parameter_refs"][0]
    params[key].update(basis="engineering_proposal", source="正文明确声明")
    params[key].pop("conditions", None)
    row = normalize(row, "answers", doc, params)
    doc["research"] = {"answers": [row]}
    value = verdict("answers", row, doc)
    value["fields"]["reason"] = {"status": "unsupported", "reason": "原科学否决保持"}
    apply_review(doc["research"], [value], doc, params, doc["evidence"])
    saved = ensure(tmp_path)
    committed = update(tmp_path, params, saved["revision"])
    assert committed["parameters"][key]["conditions"] == {}
    assert supported(row, "response", doc, committed["parameters"])
    assert not supported(row, "reason", doc, committed["parameters"])
    params[key]["conditions"] = empty
    assert supported(row, "response", doc, params)
    params[key]["conditions"] = {"temperature_K": 310}
    assert not supported(row, "response", doc, params)


@pytest.mark.parametrize("change", ["value", "unit", "denominator", "conditions", "text", "evidence"])
def test_true_research_input_changes_invalidate_review(change):
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    row = normalize(row, "answers", doc, params)
    original = fingerprint(row, doc, params)
    key = row["parameter_refs"][0]
    if change == "text": doc["final_answer"] += "科学条件已变更。"
    elif change == "evidence": doc["evidence"][0]["text"] += "新内容"
    else: params[key][change] = {"v_min": 2.5} if change == "conditions" else "new"
    assert fingerprint(row, doc, params) != original


def test_legacy_mixed_empty_conditions_keep_original_scientific_verdict():
    from auto_battery_research.util.research_records import collect_research
    doc, row = sample()
    params = doc["ledger_view"]["parameters"]
    key = row["parameter_refs"][0]
    params[key].update(reference_origin="writer_v2", conditions={})
    params["condition.legacy"] = {"value": "原条件", "conditions": {}}
    row["parameter_refs"].append("condition.legacy")
    row = normalize(row, "answers", doc, params)
    fp = fingerprint(row, doc, params, _legacy_conditions="writer_default")
    row["support_review"] = {"fingerprint": fp, "fields": {
        "response": {"status": "supported", "reason": "原审核有效"},
        "reason": {"status": "unsupported", "reason": "科学否决保留"}}}
    assert supported(row, "response", doc, params) and not supported(row, "reason", doc, params)
    recovered = collect_research({}, doc, params, previous={"answers": [row]})
    result = recovered["answers"][0]
    assert result["support_review"]["fingerprint"] == fingerprint(result, doc, params)
    assert result["support_review"]["fields"]["reason"]["status"] == "unsupported"
    params[key]["value"] = "新材料"
    assert not supported(result, "response", doc, params)


def test_additional_material_mention_enters_semantic_review_and_keeps_locked_field():
    from auto_battery_research.tests.test_repair_interfaces import Script, issue, permit_for
    from auto_battery_research.util.repair_interfaces import execute
    old = "主方案负极为金属锂。电解液风险需要验证。金属锂按致密箔处理。"
    quote = "电解液风险需要验证。"
    replacement = "电解液风险需要验证，并核对其与金属锂的兼容性。"
    fields = {"anode.active_material": {"value": "金属锂", "locked": True}}
    issues = [issue("i", quote)]
    script = Script({quote: replacement})
    changed, parameters, audit = execute(SimpleNamespace(llm=script), script, old, fields,
        permit_for(old, issues, fields), issues, [])
    assert changed == old.replace(quote, replacement) and parameters == fields
    assert audit["status"] == "APPLIED" and [c[0] for c in script.calls] == ["writer_patch", "repair_verify"]


@pytest.mark.parametrize("replacement", ["主方案负极为石墨。", "主方案负极为石墨，金属锂仅为对照。"])
def test_material_change_is_not_authorized_by_repetition(replacement):
    from auto_battery_research.tests.test_repair_interfaces import Script, issue, permit_for
    from auto_battery_research.util.repair_interfaces import execute
    text = "主方案负极为金属锂。"
    fields = {"anode.material": {"value": "金属锂", "locked": True}}
    issues = [issue("i", text)]
    script = Script({text: replacement}, new_issue=True)
    changed, params, audit = execute(SimpleNamespace(llm=script), script, text, fields,
        permit_for(text, issues, fields), issues, [])
    assert changed == text and params == fields and audit["status"] == "REJECTED"


def test_safe_parser_does_not_salvage_inner_review_object():
    from auto_battery_research.rag import LLMClient
    import importlib
    parse = importlib.import_module(LLMClient.__module__).safe_json_loads
    broken = '{"input_fingerprint":"x","coverage":{"status":"supported","reason":"原文"},"notes":["备注"}'
    assert parse(broken) is None
    assert parse('说明\n{"answer":1}\n结束') == {"answer": 1}
    assert parse('```json\n{"answer":1}\n```') == {"answer": 1}


def test_narrative_review_bracket_recovery_keeps_full_verdict_and_raw():
    doc, raw = fixture()
    draft = narrative.normalize(raw, doc)
    expected = dict(review(doc, draft), notes=["保留说明"])
    text = json.dumps(expected, ensure_ascii=False)
    broken = text[:-2] + text[-1]  # 仅notes数组缺失内部闭括号。
    result = narrative.decode_review_response(broken, draft, doc)
    assert {k: v for k, v in result.items() if k != "protocol_record"} == expected
    assert result["protocol_record"]["raw_response"] == broken
    assert result["protocol_record"]["syntax_repair"]["insert"] == "]"
    assert narrative.apply_review(draft, result, doc, doc["evidence"])["status"] == "approved"


@pytest.mark.parametrize("mode", ["broken", "inner", "missing", "duplicate", "stale"])
def test_protocol_retry_reviews_same_narrative_without_regeneration(mode):
    doc, raw = fixture()
    state, calls, drafts = {}, [], []
    def generate(*a, **kw):
        calls.append("generate")
        return json.dumps(raw)
    def audit(**kw):
        calls.append("review")
        drafts.append(copy.deepcopy(kw["narrative"]["sections"]))
        good = review(kw["doc"], kw["narrative"])
        if len(drafts) > 1: return good
        if mode == "broken": raise narrative.ReviewProtocolError("无效JSON", '{"bad":')
        if mode == "inner": return good["coverage"]
        if mode == "missing": good["paragraphs"] = []
        if mode == "duplicate": good["paragraphs"] *= 2
        if mode == "stale": good["input_fingerprint"] = "stale"
        return good
    narrative.integrate(doc, SimpleNamespace(available=True, chat=generate),
                        SimpleNamespace(review_research_narrative=audit), state)
    assert calls == ["generate", "review", "review"] and drafts[0] == drafts[1]
    assert state["generation_calls"] == 1 and state["review_calls"] == 2
    assert narrative.current_narrative(doc)


def test_protocol_retry_interruption_keeps_spent_budget():
    doc, raw = fixture()
    state = {}
    def fail(**kw): raise narrative.ReviewProtocolError("格式缺失")
    def save():
        if state.get("phase") == "review_retry": raise KeyboardInterrupt()
    client = SimpleNamespace(available=True, chat=lambda *a, **k: json.dumps(raw))
    with pytest.raises(KeyboardInterrupt):
        narrative.integrate(doc, client, SimpleNamespace(review_research_narrative=fail), state, save)
    assert state["review_calls"] == 1
    client.chat = lambda *a, **k: pytest.fail("只恢复审核")
    narrative.integrate(doc, client, SimpleNamespace(review_research_narrative=lambda **kw: review(kw["doc"], kw["narrative"])), state)
    assert state["review_calls"] == 2 and narrative.current_narrative(doc)


def declared_geometry():
    def row(value, unit): return {"value": value, "unit": unit, "source": "已定主方案", "basis": "engineering_proposal"}
    params = {"cathode.L_um": row(70, "um"), "cathode.porosity": row(.35, ""), "cathode.R_s_um": row(.2, "um"),
              "cathode.D_s_m2s": row(None, "m2/s"), "anode.L_um": row(100, "um"), "anode.porosity": row(0, ""),
              "anode.D_s_m2s": row(None, "m2/s"), "condition.v_max": row(4.6, "V"), "condition.c_rate": row(1, "C")}
    scheme = {"cathode": "Li1.17Na0.03Mn0.6Ni0.18Zn0.02O2", "anode": "li_metal", "electrolyte": "carbonate_ec"}
    return params, scheme


def test_declared_geometry_recovery_preserves_values_missing_data_and_sources():
    from auto_battery_research.simulation.geometry_contract import geometry_from_declared_parameters, validate_geometry_contract
    params, scheme = declared_geometry()
    before = copy.deepcopy(params)
    candidate, geo = geometry_from_declared_parameters(params, scheme, [])
    assert geo["cathode"]["L_um"] == 70 and geo["cathode"]["D_s_m2s"] is None
    assert geo["anode"]["D_s_m2s"] is None and not validate_geometry_contract(geo, scheme, [])
    assert "已定主方案" in geo["parameter_sources"]["cathode.L_um"]["declaration"]
    assert "n_p_ratio" not in candidate and params == before


@pytest.mark.parametrize("attack", ["missing", "unit", "scope", "diffusion", "conflict", "geometry"])
def test_declared_recovery_rejects_incomplete_or_conflicting_inputs(attack):
    from auto_battery_research.simulation.geometry_contract import geometry_from_declared_parameters
    params, scheme = declared_geometry()
    if attack == "missing": params.pop("cathode.L_um")
    if attack == "unit": params["cathode.L_um"]["unit"] = "mm"
    if attack == "scope": params["cathode.L_um"]["group_id"] = "control"
    if attack == "diffusion": params["cathode.D_s_m2s"]["value"] = 1e-14
    if attack == "conflict": scheme["geometry"] = {"cathode": {"L_um": 80}}
    if attack == "geometry": scheme["geometry"] = {"contract_version": "2.0", "material_ids": {"cathode": "other"}}
    before = copy.deepcopy(params)
    with pytest.raises(ValueError): geometry_from_declared_parameters(params, scheme, [])
    assert params == before


def test_adapter_resumes_declared_geometry_without_llm_or_budget_reset(tmp_path, monkeypatch):
    from auto_battery_research.tools.rag_adapter import AbrRagAdapter
    from auto_battery_research.util.scheme_ledger import ensure
    from auto_battery_research.util.material_ids import checkpoint_identity
    from auto_battery_research.util.design_sections import REQUIRED_SECTIONS
    params, scheme = declared_geometry()
    goal = "研究声明材料的循环稳定性"
    ledger = ensure(tmp_path, goal)
    text = "\n\n".join("## " + title + "\n\n" + "已有声明仍需独立实验验证。" * 10 for title, _ in REQUIRED_SECTIONS)
    for row in params.values(): row["source_quote"] = "已有声明仍需独立实验验证。"
    checkpoint = {"checkpoint_identity": checkpoint_identity(goal, ledger, {}), "draft": text,
                  "review_rounds": 4, "repair_rounds": 3}
    (tmp_path / "stage4_repair_checkpoint.json").write_text(json.dumps(checkpoint), encoding="utf-8")
    llm = SimpleNamespace(available=True, chat=lambda *a, **kw: pytest.fail("恢复不能请求新几何或重写正文"))
    raw = {"final_answer": text, "scheme": scheme, "repair_parameters": params,
           "evidence": [{"passage_id": "p", "text": "归档依据", "source": "测试文献"}], "geometry_candidate": None,
           "review_completed": True, "review_passed": False, "reviewer_output": {"issues": ["科学保留意见"], "confidence": "low"},
           "rule_checks": {"confidence": "high", "rule_checks": {"violations": [], "rejects": []}}, "plan": {}}
    pipeline = SimpleNamespace(run=lambda *a: copy.deepcopy(raw), writer_llm=None)
    adapter = AbrRagAdapter(config={"runtime_options": {"report_first": True}}, pipeline=pipeline)
    monkeypatch.setattr(adapter, "_build_provenance", lambda: {})
    result = adapter.run_rag_design(goal, tmp_path)
    assert result["success"], result
    saved = json.loads((tmp_path / "design_scheme.json").read_text(encoding="utf-8"))
    assert saved["scheme"]["geometry"]["contract_version"] == "2.0"
    assert saved["scheme"]["geometry"]["cathode"]["D_s_m2s"] is None
    assert not saved["review_passed"] and "科学保留意见" in saved["reviewer_output"]["issues"]
    assert json.loads((tmp_path / "stage4_repair_checkpoint.json").read_text(encoding="utf-8")) == checkpoint


def test_candidate_name_and_role_are_split_only_with_same_source():
    from auto_battery_research.util.research_records import normalize_candidate_label, collect_research
    from auto_battery_research.tests.test_research_support import choice_doc
    doc = choice_doc()
    doc["final_answer"] += "\n\n| Zn–Na | 主推候选（待验证） |"
    row = copy.deepcopy(doc["research"]["candidates"][0])
    row.update(id="zn_na", name="Zn–Na 主推候选", scope="candidate")
    row["source_quotes"].append("| Zn–Na | 主推候选（待验证） |")
    row.pop("support_review", None)
    normalized = normalize_candidate_label(row)
    assert normalized["name"] == "Zn–Na" and normalized["display_name"] == row["name"]
    assert normalize_candidate_label(normalized) == normalized
    research = collect_research({"candidates": [row]}, doc, doc["ledger_view"]["parameters"])
    assert research["candidates"][0]["name"] == "Zn–Na"
    assert research["selected_candidate_id"] is None


@pytest.mark.parametrize("name,quote", [("Zn–Na 主推候选", "| Cu–Na | 主推候选 |"),
    ("C3 主推候选", "| C31 | 主推候选 |"), ("Zn–Na 最优候选", "| Zn–Na | 主推候选 |")])
def test_candidate_label_does_not_guess_material_or_promote_role(name, quote):
    from auto_battery_research.util.research_records import normalize_candidate_label
    row = {"name": name, "scope": "candidate", "source_quotes": [quote]}
    assert normalize_candidate_label(row) == row


def test_explicit_candidate_role_has_optional_display_name_and_requires_local_source():
    from auto_battery_research.util.research_records import normalize_candidate_label
    row = {"name": "Zn–Na", "scope": "candidate", "candidate_role": "主推候选",
           "source_quotes": ["| Zn–Na | 主推候选（待验证） |"]}
    result = normalize_candidate_label(row)
    assert result["display_name"] == "Zn–Na 主推候选" and "display_name" not in row
    row["source_quotes"] = ["Zn–Na只是对照", "Cu–Na是主推候选"]
    with pytest.raises(ValueError): normalize_candidate_label(row)
    row["source_quotes"] = ["| Zn–Na | 备选候选 |\n| Cu–Na | 主推候选 |"]
    with pytest.raises(ValueError): normalize_candidate_label(row)
    row["source_quotes"] = ["Zn–Na是备选候选，Cu–Na是主推候选。"]
    with pytest.raises(ValueError): normalize_candidate_label(row)
