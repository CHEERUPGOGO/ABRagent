"""Writer协议恢复有界、原文可追溯，失败和中断都不会重新全文生成。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_design_structure import document
from auto_battery_research.util.writer_protocol import produce, normalize, metadata_patch
from auto_battery_research.util.scheme_ledger import digest


def example():
    return dict(document(), version=2, parameters={})


def test_explicit_main_formula_mapping_survives_writer_and_ledger_commit(tmp_path):
    from auto_battery_research.util.material_ids import assemble_components
    from auto_battery_research.util.scheme_ledger import commit_design, load
    path = "cathode.doping.rank1_formula"
    data = example()
    data["parameters"][path] = {"value": "Li1.17Na0.03Mn0.57Zn0.03Ni0.2O2", "unit": "",
        "basis": "engineering_proposal", "source": "原稿明确采用的首选配方", "quantity_kind": "text",
        "canonical_path": "cathode.nominal_formula", "meaning": "当前主推候选名义式，待实验验证"}
    data["sections"]["recommendations"]["body"] = "本轮采用主推配方{{param:" + path + "}}，性能待实验验证。"
    result = produce(SimpleNamespace(available=False), "s", "u", {},
                     {"raw_response": json.dumps(data, ensure_ascii=False)}, lambda: None)
    assert not result["fallback"], result
    params = result["numeric_parameters"]
    scheme = assemble_components({}, params)
    doc = {"target": "主推名义式显式关联回归", "scheme": scheme, "scheme_parameters": params}
    commit_design(tmp_path, doc)
    assert doc["scheme"]["cathode"] == data["parameters"][path]["value"]
    assert doc["scheme"]["component_identities"]["cathode"]["parameter_refs"] == [path]
    assert load(tmp_path)["parameters"][path]["canonical_path"] == "cathode.nominal_formula"
    assert "cathode.material" not in load(tmp_path)["parameters"]


@pytest.mark.parametrize("scope", ["candidate", "alternative", "control"])
def test_ranked_candidate_mapping_does_not_adopt_unselected_formula(scope):
    from auto_battery_research.util.material_ids import assemble_components
    params = {"cathode.doping.rank1_formula": {"value": "Li1.17Na0.03Mn0.57Zn0.03Ni0.2O2",
              "canonical_path": "cathode.nominal_formula", "scope": scope}}
    assert "cathode" not in assemble_components({}, params)


def test_single_internal_closing_bracket_repair_preserves_raw_and_content():
    from auto_battery_research.util.writer_protocol import single_closure_repair
    data = example()
    raw = json.dumps(data, ensure_ascii=False)
    # recommendations是以subsections结束的对象，只移除这一处数组闭括号。
    broken = raw.replace('}]}, "metrics"', '}}, "metrics"', 1)
    assert broken != raw
    repair = single_closure_repair(broken)
    assert repair and repair["insert"] == "]" and repair["repaired_response"] == raw
    calls = []
    state = {"raw_response": broken, "generation_attempted": True}
    result = produce(SimpleNamespace(available=False, chat=lambda *a, **k: calls.append(k)), "s", "u", {}, state, lambda: None)
    assert not result["fallback"] and not calls and state["raw_response"] == broken
    assert result["structured_sections"] == data
    assert state["syntax_repair"] == repair
    assert not produce(SimpleNamespace(available=False), "s", "u", {}, state, lambda: None)["fallback"]


@pytest.mark.parametrize("raw", ['{"version":2,"sections":', '{"a":1,"a":2}',
    '{"a":[1,}', '{"a":"unterminated}', '{"a":1 "b":2}'])
def test_single_closure_does_not_guess_truncation_duplicate_keys_or_other_syntax(raw):
    from auto_battery_research.util.writer_protocol import single_closure_repair
    assert single_closure_repair(raw) is None


def test_uppercase_id_and_unit_normalized_without_model_repair():
    data = example()
    data["sections"]["risks"]["subsections"][1]["id"] = "Feas_na_A"
    data["sections"]["recommendations"]["subsections"][1]["body"] = "见{{section:Feas_na_A}}，仍须验证。"
    data["parameters"]["cathode.porosity"] = {"value": .3, "unit": "1", "basis": "engineering_proposal", "source": "工程取值", "meaning": "孔隙率"}
    data["sections"]["goals"]["body"] += "孔隙率{{param:cathode.porosity}}。"
    raw = json.dumps(data, ensure_ascii=False)
    calls, state, saved = [], {}, []
    def chat(*a, **kw):
        calls.append(kw["trace_tag"])
        assert saved[-1]["generation_attempted"]
        return raw
    result = produce(SimpleNamespace(available=True, chat=chat), "system", "user", {}, state, lambda: saved.append(copy.deepcopy(state)))
    assert calls == ["writer"] and not result["fallback"]
    assert result["raw_response"] == raw and "design-feas_na_a" in result["draft_answer"]
    assert result["numeric_parameters"]["cathode.porosity"]["value"] == .3


@pytest.mark.parametrize("path,value,unit,canonical", [
    ("cathode.L_um", 20, "μm", "um"), ("anode.L_um", 500, "µm", "um"),
    ("cathode.R_s_um", 2.5, " μm ", "um"), ("cathode.D_s_m2s", 1e-14, "m²/s", "m2/s"),
    ("cathode.active_loading_mg_cm2", 4.5, "mg/cm²", "mg/cm2"),
    ("cathode.electrode_area", 1, "cm²", "cm2"), ("electrolyte.volume", 80, "μL", "uL"),
])
def test_equivalent_unit_spellings_render_without_protocol_request(path, value, unit, canonical):
    data = example()
    data["parameters"] = {path: {"value": value, "unit": unit, "basis": "engineering_proposal", "source": "原始工程声明"}}
    data["sections"]["goals"]["body"] += "工程参数{{param:" + path + "}}，待验证。"
    original = copy.deepcopy(data)
    state = {"raw_response": json.dumps(data, ensure_ascii=False), "generation_attempted": True}
    result = produce(SimpleNamespace(available=False, chat=lambda *a, **k: pytest.fail("等价单位无需模型修复")),
                     "s", "u", {}, state, lambda: None)
    assert not result["fallback"], result
    row = result["numeric_parameters"][path]
    assert row["value"] == value and row["unit"] == canonical and row["source"] == "原始工程声明"
    assert data == original and json.loads(state["raw_response"]) == original
    assert not state.get("repair_attempts")
    assert normalize(result["structured_sections"]) == result["structured_sections"]


@pytest.mark.parametrize("unit", ["mm", "nm", "", "kg", "μm/cm"])
def test_unit_normalization_does_not_guess_scale_or_missing_unit(unit):
    data = example()
    data["parameters"] = {"cathode.L_um": {"value": 20, "unit": unit, "basis": "engineering_proposal", "source": "原声明"}}
    data["sections"]["goals"]["body"] += "厚度{{param:cathode.L_um}}。"
    assert normalize(data)["parameters"]["cathode.L_um"]["unit"] == unit
    result = produce(SimpleNamespace(available=False), "s", "u", {}, {"raw_response": json.dumps(data)}, lambda: None)
    assert result["fallback"] and "必须采用单位" in result["error"]


def test_case_collision_is_not_silently_merged():
    data = example()
    data["sections"]["risks"]["subsections"][0]["id"] = "RANKING"
    with pytest.raises(ValueError, match="重复"):
        normalize(data)


def test_one_schema_patch_preserves_values_text_and_sources_on_resume(tmp_path):
    data = example()
    data["parameters"] = {"synthesis.temperature": {"value": 900, "unit": "℃", "basis": "engineering_proposal", "source": "待验证工程建议"}}
    data["sections"]["goals"]["body"] += "处理温度{{param:synthesis.temperature}}，须验证。[p1]"
    raw, state, calls = json.dumps(data, ensure_ascii=False), {}, []
    checkpoint = tmp_path / "writer.json"
    def save():
        checkpoint.write_text(json.dumps(state), encoding="utf-8")
    def chat(system, user, **kwargs):
        calls.append(kwargs["trace_tag"])
        if kwargs["trace_tag"] == "writer":return raw
        assert json.loads(checkpoint.read_text())["repair_attempts"] == 1
        request = json.loads(user)
        return json.dumps({"base_hash": request["base_hash"], "parameters": [
            {"path": "synthesis.temperature", "new_path": "process.temperature"}]})
    client = SimpleNamespace(available=True, chat=chat)
    result = produce(client, "system", "user", {}, state, save)
    assert not result["fallback"] and calls == ["writer", "writer_protocol_repair"]
    assert result["structured_sections"]["parameters"]["process.temperature"] == data["parameters"]["synthesis.temperature"]
    again = produce(client, "system", "user", {}, state, save)
    assert again["draft_answer"] == result["draft_answer"] and len(calls) == 2


def test_failed_patch_or_interruption_does_not_repeat_model_calls():
    data = example()
    data["parameters"] = {"test.invalid": {"value": 1, "unit": "", "basis": "engineering_proposal", "source": "原声明"}}
    state = {"raw_response": json.dumps(data), "generation_attempted": True}
    calls = []
    def chat(*a, **kw):
        calls.append(kw["trace_tag"])
        raise RuntimeError("服务中断")
    client = SimpleNamespace(available=True, chat=chat)
    assert produce(client, "s", "u", {}, state, lambda: None)["fallback"]
    assert produce(client, "s", "u", {}, state, lambda: None)["fallback"]
    assert calls == ["writer_protocol_repair"] and state["repair_attempts"] == 1
    interrupted = {"generation_attempted": True}
    assert produce(client, "s", "u", {}, interrupted, lambda: None)["fallback"] and len(calls) == 1


def test_saved_patch_response_can_finish_after_restart_without_resending():
    data = example()
    data["parameters"] = {"synthesis.temperature": {"value": 900, "unit": "℃", "basis": "engineering_proposal", "source": "工程取值"}}
    data["sections"]["goals"]["body"] += "处理温度{{param:synthesis.temperature}}。"
    normalized = normalize(data)
    state = {"generation_attempted": True, "raw_response": json.dumps(data), "repair_attempts": 1,
             "repair_raw_response": json.dumps({"base_hash": digest(normalized), "parameters": [
                 {"path": "synthesis.temperature", "new_path": "process.temperature"}]})}
    def forbidden(*a, **kw):
        pytest.fail("已有协议响应不能重复请求模型")
    result = produce(SimpleNamespace(available=True, chat=forbidden), "s", "u", {}, state, lambda: None)
    assert not result["fallback"] and state["repair_attempts"] == 1


def test_changed_current_parameters_cannot_reuse_protocol_state():
    state = {"generation_attempted": True, "raw_response": json.dumps(example()), "parameters_hash": digest({})}
    result = produce(SimpleNamespace(available=True, chat=lambda *a, **k: pytest.fail("不能重发")), "s", "u",
                     {"cathode.L_um": {"value": 50}}, state, lambda: None)
    assert result["fallback"] and "基准已变化" in result["error"]


def test_protocol_patch_cannot_change_value_or_existing_semantics():
    data = example()
    data["parameters"] = {"process.x": {"value": 1, "unit": "mm", "basis": "engineering_proposal", "source": "原文"}}
    for change in ({"path": "process.x", "metadata": {"value": 2}},
                   {"path": "process.x", "metadata": {"unit": "um"}},
                   {"path": "process.x", "new_path": "process.y"}):
        with pytest.raises(ValueError):
            metadata_patch(data, {"base_hash": digest(data), "parameters": [change]})


def test_protocol_patch_cannot_invent_denominator_or_missing_unit():
    data = example()
    data["parameters"] = {"process.ratio": {"value": .5, "unit": None, "basis": "engineering_proposal", "source": "仅声明比值"}}
    for meta in ({"denominator": "总阳离子"}, {"unit": "mol/L"}):
        with pytest.raises(ValueError):
            metadata_patch(data, {"base_hash": digest(data), "parameters": [{"path": "process.ratio", "metadata": meta}]})


def test_saved_valid_response_can_render_offline():
    from auto_battery_research.rag import WriterAgent
    agent = WriterAgent(SimpleNamespace(available=False, chat=lambda *a, **k: pytest.fail("离线不调用模型")))
    agent.protocol_state = {"generation_attempted": True, "raw_response": json.dumps(example())}
    result = agent.run("设计材料方案", {}, [{"passage_id": "p1", "source": "离线文献", "text": "原证据"}])
    assert not result["fallback"] and result["draft_answer"]


def test_false_stop_truncation_has_one_persisted_completion_retry():
    raw = '{"version":2,"sections":{"goals":{"body":"截断'
    state = {"generation_attempted": True, "raw_response": raw,
             "response_meta": {"attempt": 1, "finish_reason": "stop"}}
    calls, saved = [], []
    def chat(*args, **kwargs):
        calls.append(kwargs["trace_tag"])
        assert saved[-1]["completion_attempted"]
        assert client.single_attempt and client.max_tokens == 200
        return json.dumps(example())
    client = SimpleNamespace(available=True, chat=chat, max_tokens=100, output_max_tokens=200)
    result = produce(client, "SYS", "锁定条件", {}, state, lambda: saved.append(copy.deepcopy(state)))
    assert not result["fallback"] and calls == ["writer_completion_retry"]
    assert state["raw_response"] == raw and state["completion_raw_response"]
    assert client.max_tokens == 100 and not hasattr(client, "single_attempt")
    assert produce(client, "SYS", "锁定条件", {}, state, lambda: None)["draft_answer"] == result["draft_answer"]
    assert len(calls) == 1


def test_completion_retry_interruption_cannot_resend():
    raw = '{"sections":{"goals":{"body":"截断'
    state = {"generation_attempted": True, "raw_response": raw, "response_meta": {"attempt": 1}}
    calls = []
    def chat(*args, **kwargs):
        calls.append(kwargs["trace_tag"])
        raise RuntimeError("连接中断")
    client = SimpleNamespace(available=True, chat=chat)
    assert produce(client, "s", "u", {}, state, lambda: None)["fallback"]
    assert produce(client, "s", "u", {}, state, lambda: None)["fallback"]
    assert calls == ["writer_completion_retry"] and state["completion_attempted"]


@pytest.mark.parametrize("raw", ['{"a":1 "b":2}', '{"a":]}', '{"sections":{}}', '非JSON'])
def test_complete_protocol_errors_do_not_trigger_output_retry(raw):
    from auto_battery_research.util.writer_protocol import incomplete_json
    assert not incomplete_json(raw)


def test_transport_second_attempt_cannot_add_third_generation():
    state = {"generation_attempted": True, "raw_response": '{"sections":', "response_meta": {"attempt": 2}}
    result = produce(SimpleNamespace(available=True, chat=lambda *a, **k: pytest.fail("已用完两次输出机会")),
                     "s", "u", {}, state, lambda: None)
    assert result["fallback"] and not state.get("completion_attempted")


def test_empty_writer_failure_does_not_request_more_evidence():
    from auto_battery_research.util.adaptive_retrieval import has_pending, init_state
    seed = {"draft": "", "writer_output": {"error": "截断"}}
    state = init_state(seed, fresh=True)
    state["pending_diagnostics"] = [{"action": "supplement", "queries": ["进一步检索"]}]
    before = copy.deepcopy(state)
    assert not has_pending(seed) and state == before


def test_unspent_completion_retry_is_detected_before_downstream(tmp_path):
    from auto_battery_research.util.design_repair import structure_recovery_available
    seed = {"draft": "", "writer_request": {"question": "原课题"}, "writer_protocol": {
        "raw_response": '{"sections":', "response_meta": {"attempt": 1}}}
    assert structure_recovery_available(tmp_path, {}, seed)
    seed["writer_protocol"]["completion_attempted"] = True
    assert not structure_recovery_available(tmp_path, {}, seed)
    seed["writer_protocol"].pop("completion_attempted")
    (tmp_path / "experiment_plan.json").write_text("{}", encoding="utf-8")
    assert not structure_recovery_available(tmp_path, {}, seed)


def test_empty_design_still_writes_review_checkpoint(tmp_path):
    from auto_battery_research.util.design_repair import review_design
    path = tmp_path / "stage4_repair_checkpoint.json"
    seed = {"draft": "", "evidence": [], "writer_output": {"raw_response": "原始完整响应", "error": "协议失败"}, "writer_protocol": {"repair_attempts": 1}}
    pipeline = SimpleNamespace(reviewer=SimpleNamespace(), repair_checkpoint_path=path)
    _, review, *_ = review_design(pipeline, seed, "原课题")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["writer_output"]["raw_response"] == "原始完整响应"
    assert saved["writer_protocol"]["repair_attempts"] == 1 and review["error_type"] == "structure"


def test_pipeline_protocol_failure_is_resumable_without_full_writer(tmp_path, monkeypatch):
    from auto_battery_research.tests.test_stage4_golden import _build_seeded_pipeline, _run_adapter, _install_scripted_llm, GOLDEN_WRITER_DRAFT
    from auto_battery_research.rag import LLMClient
    _install_scripted_llm(monkeypatch, GOLDEN_WRITER_DRAFT)
    original = LLMClient.chat
    calls = []
    def chat(self, system, user, **kwargs):
        tag = kwargs.get("trace_tag")
        calls.append(tag)
        if tag == "writer":return '{"version":2,"parameters":{},"sections":{}}'
        if tag == "writer_protocol_repair":
            return json.dumps({"base_hash": json.loads(user)["base_hash"], "parameters": []})
        return original(self, system, user, **kwargs)
    monkeypatch.setattr(LLMClient, "chat", chat)
    pipeline = _build_seeded_pipeline(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    (task / "stage4_rebuild_requested.json").write_text("{}", encoding="utf-8")
    _run_adapter(pipeline, task)
    checkpoint = json.loads((task / "stage4_repair_checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["writer_protocol"]["raw_response"] and not checkpoint["draft"]
    assert checkpoint["writer_protocol"]["repair_attempts"] == 1
    assert not (task / "stage4_rebuild_requested.json").exists()
    before = list(calls)
    _run_adapter(pipeline, task)
    assert calls == before and "scheme_maintenance" not in calls
