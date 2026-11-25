"""同段多问题修复：精确范围、联合接口、批量复核及事务回退。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util.design_repair import authorize, repair
from auto_battery_research.util.repair_interfaces import build_interfaces, execute, normalize_submissions


def issue(i, quote, fields=None):
    return {"id": i, "quote": quote, "reason": "原文依据不足", "suggestion": "降低确定性并修正引用", "parameter_paths": fields or []}


def permit_for(text, issues, fields=None):
    permit = authorize(text, issues, fields or {})
    permit["evidence_ids"] = ["p1"]
    return permit


class Script:
    def __init__(self, replacements, failed=(), new_issue=False, malformed=False, mutate=None):
        self.replacements, self.failed = replacements, failed
        self.calls = []
        self.new_issue, self.malformed, self.mutate = new_issue, malformed, mutate

    def chat(self, system, user, **kwargs):
        data = json.loads(user)
        self.calls.append((kwargs["trace_tag"], data))
        if kwargs["trace_tag"] == "writer_patch":
            rows = [{"interface_id": i, "replacement": self.replacements.get(v["original"], v["original"] + "（待验证）")}
                    for i, v in data["interface_contract"]["interfaces"].items()]
            patch = {"base_hash": data["permission"]["base_hash"], "submissions": rows}
            if self.mutate:
                self.mutate(patch, data)
            return json.dumps(patch)
        if self.malformed:
            return '{}'
        return json.dumps({"verdicts": [{"group_id": g, "issue_id": i, "resolved": i not in self.failed,
            "reason": "已检查原句和引用"} for g, c in data["transactions"].items() for i in c["issue_ids"]],
            "new_issues": [{"group_id": next(iter(data["transactions"])), "reason": "合并引入矛盾"}] if self.new_issue else []})


def run(text, issues, script, fields=None):
    fields = fields or {}
    permit = permit_for(text, issues, fields)
    return execute(SimpleNamespace(llm=script), script, text, fields, permit, issues, [{"passage_id": "p1"}])


def test_nonoverlapping_issues_merge_in_one_paragraph():
    text = "前文不变。坏引用，错误阈值。后文不变。\n\n其他段落不变。"
    issues = [issue("i1", "坏引用"), issue("i2", "错误阈值")]
    script = Script({"坏引用": "待核实引用", "错误阈值": "待验证阈值"})
    result, fields, entry = run(text, issues, script)
    assert result == "前文不变。待核实引用，待验证阈值。后文不变。\n\n其他段落不变。"
    assert entry["resolved_issue_ids"] == ["i1", "i2"]
    assert len(entry["interfaces"]["interfaces"]) == 2
    assert [c[0] for c in script.calls] == ["writer_patch", "repair_verify"]


def test_repeated_calculation_value_does_not_block_same_paragraph_review():
    """回放面载量算式重复4.5时，同事务的界面说明仍进入独立语义复核。"""
    from auto_battery_research.util.numeric_bindings import render, binding_errors
    fields = {"cathode.active_loading_mg_cm2": {"value": 4.5, "unit": "mg/cm2", "locked": False},
              "cathode.active_fraction": {"value": .8, "unit": "", "locked": False}}
    text = render("活性面载量{{param:cathode.active_loading_mg_cm2}}，活性占比{{param:cathode.active_fraction}}；总面载量待计算；锂负极界面待说明。", fields)
    issues = [issue("calculation", "总面载量待计算"), issue("interface", "锂负极界面待说明")]
    script = Script({"总面载量待计算": "总面载量=4.5/0.80=5.625 mg/cm²",
                     "锂负极界面待说明": "锂负极界面须监控库仑效率与过电位"})
    result, updated, entry = run(text, issues, script, fields)
    assert entry["status"] == "APPLIED" and len(entry["resolved_issue_ids"]) == 2
    assert "总面载量=4.5/0.80=5.625" in result and "须监控" in result
    assert updated == fields and not binding_errors(result, fields)
    assert [tag for tag, _ in script.calls] == ["writer_patch", "repair_verify"]


def test_multi_quote_issue_repairs_two_rows_in_one_atomic_transaction():
    text = "#3 保持率≥80%。\n\n#4 保持率≥80%。\n\n未授权说明不变。"
    item = dict(issue("i", "#3 保持率≥80%"), quotes=["#3 保持率≥80%", "#4 保持率≥80%"])
    script = Script({q: q + "（未验证工程目标）" for q in item["quotes"]})
    result, _, entry = run(text, [item], script)
    assert result.count("未验证工程目标") == 2
    assert len(entry["interfaces"]["transactions"]) == 1 and len(entry["interfaces"]["interfaces"]) == 2
    assert entry["resolved_issue_ids"] == ["i"] and result.endswith("未授权说明不变。")
    assert [tag for tag, _ in script.calls] == ["writer_patch", "repair_verify"]


def test_multi_quote_issue_missing_location_has_no_partial_permission():
    text = "#3 保持率≥80%。"
    item = dict(issue("i", "#3 保持率≥80%"), quotes=["#3 保持率≥80%", "不存在的#4行"])
    permit = permit_for(text, [item])
    assert not permit["grants"]
    assert build_interfaces(text, {}, [item], permit)["unresolved"] == ["i"]


def test_multi_quote_transaction_requires_all_rows_and_rolls_back_together():
    text = "#3 保持率≥80%。\n\n#4 保持率≥80%。"
    item = dict(issue("i", "#3 保持率≥80%"), quotes=["#3 保持率≥80%", "#4 保持率≥80%"])
    def omit(patch, data):
        patch["submissions"] = patch["submissions"][:1]
    result, _, entry = run(text, [item], Script({}, mutate=omit))
    assert result == text and not entry["resolved_issue_ids"] and entry["status"] == "REJECTED"


def test_overlapping_issues_get_single_joint_interface():
    text = "Na半径34%，需验证。"
    issues = [issue("i1", "Na半径34%"), issue("i2", "半径34%")]
    contract = build_interfaces(text, {}, issues, permit_for(text, issues))
    assert len(contract["interfaces"]) == 1
    interface = next(iter(contract["interfaces"].values()))
    assert interface["issue_ids"] == ["i1", "i2"] and interface["original"] == "Na半径34%"
    result, _, entry = run(text, issues, Script({"Na半径34%": "Na半径失配需复核"}))
    assert result == "Na半径失配需复核，需验证。" and entry["status"] == "APPLIED"


def test_one_failed_issue_preserves_independent_edits_in_same_paragraph():
    text = "错误一和错误二。\n\n独立错误三。"
    issues = [issue("i1", "错误一"), issue("i2", "错误二"), issue("i3", "错误三")]
    result, _, entry = run(text, issues, Script({}, failed=["i2"]))
    assert result == "错误一（待验证）和错误二。\n\n独立错误三（待验证）。"
    assert [i["id"] for i in entry["remaining_issues"]] == ["i2"]
    assert entry["resolved_issue_ids"] == ["i1", "i3"]


@pytest.mark.parametrize("kind", ["forged", "duplicate", "range", "reference", "version"])
def test_invalid_submissions_never_change_text(kind):
    text = "原句。\n\n未授权段落。"
    def mutate(patch, data):
        if kind == "forged":
            patch["submissions"][0]["interface_id"] = "forged"
        elif kind == "duplicate":
            patch["submissions"] *= 2
        elif kind == "range":
            patch["submissions"][0]["start"] = 0
        elif kind == "reference":
            patch["submissions"][0]["replacement"] += "[unknown]"
        else:
            patch["base_hash"] = "stale"
    result, _, entry = run(text, [issue("i", "原句")], Script({}, mutate=mutate))
    assert result == text and entry["status"] == "REJECTED"


@pytest.mark.parametrize("malformed,new_issue", [(True, False), (False, True)])
def test_failed_semantic_review_never_commits(malformed, new_issue):
    text = "原句。"
    result, _, entry = run(text, [issue("i", "原句")], Script({}, malformed=malformed, new_issue=new_issue))
    assert result == text and entry["status"] == "REJECTED"


def test_shared_parameter_conflict_is_single_transaction():
    fields = {"cathode.L_um": {"value": 60, "unit": "um", "basis": "engineering_proposal", "source": "声明"}}
    text = "正极 `cathode.L_um` 为60。\n\n对照声明 `cathode.L_um` 是60。"
    issues = [issue("i1", "正极 `cathode.L_um` 为60", ["cathode.L_um"]), issue("i2", "对照声明 `cathode.L_um` 是60", ["cathode.L_um"])]
    def mutate(patch, data):
        for n, row in enumerate(patch["submissions"]):
            row["parameter_changes"] = [{"path": "cathode.L_um", "field": dict(fields["cathode.L_um"], value=50+n, source_quote=row["replacement"])}]
    before = copy.deepcopy(fields)
    result, updated, entry = run(text, issues, Script({}, mutate=mutate), fields)
    assert len(entry["interfaces"]["transactions"]) == 1
    assert result == text and updated == before and fields == before


def test_flat_parameter_change_is_equivalent_to_nested_field():
    fields = {"cathode.L_um": {"value": 60, "unit": "um", "basis": "engineering_proposal", "source": "原始工程声明"}}
    text = "正极 `cathode.L_um` 为60，待调整。"
    items = [issue("i", text, ["cathode.L_um"])]
    replacement = "正极 `cathode.L_um` 为50，已修订工程建议。"
    def mutate(patch, data):
        patch["submissions"][0]["parameter_changes"] = [{"path": "cathode.L_um", "value": 50, "unit": "um",
            "basis": "engineering_proposal", "source": "局部工程修订", "source_quote": replacement}]
    result, params, entry = run(text, items, Script({text: replacement}, mutate=mutate), fields)
    assert entry["status"] == "APPLIED" and params["cathode.L_um"]["value"] == 50
    assert result == replacement and fields["cathode.L_um"]["value"] == 60


def test_old_duplicate_paragraph_versions_not_guessed():
    text = "问题一，问题二。"
    issues = [issue("i1", "问题一"), issue("i2", "问题二")]
    permit = permit_for(text, issues)
    contract = build_interfaces(text, {}, issues, permit)
    patch = {"base_hash": permit["base_hash"], "edits": [{"issue_id": "i1", "block_id": "b0", "text": "修正一，问题二。"},
        {"issue_id": "i2", "block_id": "b0", "text": "整段改写，修正二。"}]}
    with pytest.raises(ValueError):
        normalize_submissions(patch, contract, text)


def test_missing_interface_holds_only_its_independent_transaction():
    text = "错误一和错误二。"
    result, _, entry = run(text, [issue("i1", "错误一"), issue("i2", "错误二")],
        Script({}, mutate=lambda patch, data: patch["submissions"].pop()))
    assert result == "错误一（待验证）和错误二。" and entry["status"] == "APPLIED"
    assert entry["resolved_issue_ids"] == ["i1"]
    assert [i["id"] for i in entry["remaining_issues"]] == ["i2"]


def test_semantic_verifier_only_receives_program_validated_transactions():
    text = "错误甲。\n\n错误乙。"

    def remove_one_submission(patch, data):
        patch["submissions"] = [row for row in patch["submissions"]
                                if data["interface_contract"]["interfaces"][row["interface_id"]]["original"] != "错误甲"]

    script = Script({}, mutate=remove_one_submission)
    result, _, entry = run(text, [issue("i1", "错误甲"), issue("i2", "错误乙")], script)
    verification = next(data for tag, data in script.calls if tag == "repair_verify")
    assert [i["id"] for i in verification["issues"]] == ["i2"]
    assert {i for group in verification["transactions"].values() for i in group["issue_ids"]} == {"i2"}
    assert result == "错误甲。\n\n错误乙（待验证）。"
    assert entry["resolved_issue_ids"] == ["i2"]


def test_eight_issues_in_three_paragraphs_do_not_submit_duplicate_blocks():
    # 回归实际运行中8条审核意见集中在少量段落、整批补丁因重复block_id失败的模式。
    text = "Na半径失配34%，容量出处不符，倍率口径不符。\n\n烧结温度无依据，保温时间无依据，推荐评分主观。\n\n首效体系含钴，首效测试条件不符。"
    quotes = ["Na半径失配34%", "容量出处不符", "倍率口径不符", "烧结温度无依据", "保温时间无依据", "推荐评分主观", "首效体系含钴", "首效测试条件不符"]
    result, _, entry = run(text, [issue(f"i{n}", q) for n, q in enumerate(quotes)], Script({}))
    assert entry["status"] == "APPLIED" and len(entry["resolved_issue_ids"]) == 8
    assert len(entry["patch"]["edits"]) == 3
    assert len({e["block_id"] for e in entry["patch"]["edits"]}) == 3


def test_retry_only_failed_range_in_same_paragraph_and_preserve_checkpoint(tmp_path):
    from auto_battery_research.util.design_repair import review_design
    from auto_battery_research.util.design_sections import REQUIRED_SECTIONS
    class RetryScript(Script):
        def chat(self, system, user, **kwargs):
            output = super().chat(system, user, **kwargs)
            if kwargs["trace_tag"] == "repair_verify":
                self.failed = ()
            return output
    script = RetryScript({}, failed=["issue-0"])
    text = "\n\n".join("## " + n + "\n\n" + "保留的背景说明。" * 10 for n, _ in REQUIRED_SECTIONS) + "\n\n错误甲。错误乙。"
    reviews = []
    def review(**kwargs):
        reviews.append(1)
        return {"issues": ["错误甲", "错误乙"], "repair_issues": [issue("x", "错误甲"), issue("y", "错误乙")], "confidence": "low", "error_type": "writing"}
    pipeline = SimpleNamespace(writer=SimpleNamespace(llm=script), reviewer=SimpleNamespace(run=review, llm=script),
        _extract_energy_claim=lambda *a: None, repair_checkpoint_path=tmp_path / "checkpoint.json")
    seed = {"draft": text, "scheme": {}, "evidence": []}
    result, review, _, _, audit = review_design(pipeline, seed, "目标")
    generated = [p for tag, p in script.calls if tag == "writer_patch"]
    assert len(reviews) == 1 and len(generated) == 2
    assert len(generated[1]["interface_contract"]["issues"]) == 1
    assert generated[1]["interface_contract"]["issues"][0]["quote"] == "错误甲"
    assert generated[1]["previous_failures"]
    assert result.count("错误乙（待验证）") == 1 and "错误甲（待验证）" in result
    assert not review["issues"] and all(a["status"] == "APPLIED" for a in audit)
    saved = json.loads(pipeline.repair_checkpoint_path.read_text(encoding="utf-8"))
    assert saved["draft"] == result and len(saved["repair_audit"]) == 2


def test_shared_permission_without_parameter_write_is_not_a_dependency():
    fields = {"cathode.L_um": {"value": 60, "unit": "um", "basis": "engineering_proposal", "source": "原声明"}}
    a, b = "解释甲 `cathode.L_um` 为60", "解释乙 `cathode.L_um` 是60"
    text = a + "，" + b + "。"
    items = [issue("a", a, ["cathode.L_um"]), issue("b", b, ["cathode.L_um"])]
    result, updated, entry = run(text, items, Script({}, failed=["a"]), fields)
    assert len(entry["interfaces"]["transactions"]) == 2
    assert result == a + "，" + b + "（待验证）。" and updated == fields
    assert entry["resolved_issue_ids"] == ["b"]


def test_area_metadata_commits_while_unrelated_calculation_remains_unresolved():
    from auto_battery_research.util.numeric_bindings import render
    fields = {"cathode.active_loading_mg_cm2": {"value": 3, "unit": "mg/cm2"},
              "cathode.L_um": {"value": 30, "unit": "um"},
              "cathode.electrode_area": {"value": 1.54, "unit": "cm2", "basis": "engineering_proposal", "source": "16 mm冲片面积"}}
    calc = render("总面载量{{derived:cathode.calculated_loading_mg_cm2}}", fields, legacy_calculations=True)
    area = render("冲片面积{{param:cathode.electrode_area}}", fields)
    text = calc + "；" + area + "。"
    items = [issue("calc", calc), issue("area", area, ["cathode.electrode_area"])]
    replacement = area + "（14 mm冲片）"
    def mutate(patch, data):
        for row in patch["submissions"]:
            if row["replacement"] == replacement:
                row["parameter_changes"] = [{"path": "cathode.electrode_area", "field": dict(fields["cathode.electrode_area"],
                    source="14 mm冲片面积", source_quote="14 mm冲片")}]
    before = copy.deepcopy(fields)
    script = Script({calc: calc, area: replacement}, failed=["calc"], mutate=mutate)
    result, updated, entry = run(text, items, script, fields)
    assert entry["resolved_issue_ids"] == ["area"] and [i["id"] for i in entry["remaining_issues"]] == ["calc"]
    assert result == calc + "；" + replacement + "。"
    assert updated["cathode.electrode_area"]["source"] == "14 mm冲片面积"
    assert updated["cathode.electrode_area"]["value"] == 1.54 and fields == before
    assert len(entry["interfaces"]["transactions"]) == 2


@pytest.mark.parametrize("alias", [False, True])
def test_actual_parameter_write_and_model_calculation_input_remain_atomic(alias):
    from auto_battery_research.util.numeric_bindings import render
    path = "cathode.thickness_um" if alias else "cathode.L_um"
    fields = {path: {"value": 30, "unit": "um", "basis": "engineering_proposal", "source": "原声明"},
              "cathode.porosity": {"value": .5, "unit": ""}}
    thickness = render("厚度{{param:" + path + "}}", fields)
    pore = render("孔体积按厚度{{param:" + path + "}}×孔隙率{{param:cathode.porosity}}×0.1=1.5 μL/cm²", fields)
    text = thickness + "；" + pore + "。"
    def mutate(patch, data):
        for row in patch["submissions"]:
            if data["interface_contract"]["interfaces"][row["interface_id"]]["original"] == thickness:
                row["parameter_changes"] = [{"path": path, "field": dict(fields[path], value=40, source_quote=thickness)}]
    result, updated, entry = run(text, [issue("thickness", thickness, [path]), issue("pore", pore)],
                                  Script({thickness: thickness, pore: pore}, failed=["pore"], mutate=mutate), fields)
    assert len(entry["interfaces"]["transactions"]) == 1
    assert result == text and updated == fields and entry["status"] == "REJECTED"
    assert not entry["resolved_issue_ids"]


def test_parameter_source_quote_dependency_is_atomic():
    fields = {"process.temperature": {"value": 50, "unit": "℃", "basis": "engineering_proposal", "source": "原声明"}}
    # 定位字段写权限，原句包含完整字段以避免隐式扩大授权。
    text = "温度声明需确认 `process.temperature` 为50；来源描述错误。"
    first = "温度声明需确认 `process.temperature` 为50"
    def change(patch, data):
        for row in patch["submissions"]:
            if data["interface_contract"]["interfaces"][row["interface_id"]]["original"] == first:
                row["parameter_changes"] = [{"path": "process.temperature", "field": dict(fields["process.temperature"],
                    source="局部工程声明", source_quote="温度50 ℃；来源为工程建议")}]
    script = Script({first: "温度50 ℃", "来源描述错误": "来源为工程建议"}, failed=["source"], mutate=change)
    result, _, entry = run(text, [issue("temp", first, ["process.temperature"]), issue("source", "来源描述错误")], script, fields)
    assert len(entry["interfaces"]["transactions"]) == 1
    assert result == text and entry["status"] == "REJECTED"
    assert [tag for tag, _ in script.calls] == ["writer_patch", "repair_verify"]


def test_overlapping_joint_interface_failure_remains_atomic():
    text = "Na半径34%，未验证。"
    items = [issue("a", "Na半径34%"), issue("b", "半径34%")]
    result, _, entry = run(text, items, Script({"Na半径34%": "Na半径需复核"}, failed=["b"]))
    assert result == text and len(entry["interfaces"]["interfaces"]) == 1
    assert not entry["resolved_issue_ids"]


def test_rejected_local_reference_does_not_block_independent_range():
    text = "证据错误；操作措辞错误。"
    script = Script({"证据错误": "证据待核实[unknown]", "操作措辞错误": "操作需先核对"})
    result, _, entry = run(text, [issue("evidence", "证据错误"), issue("wording", "操作措辞错误")], script)
    assert result == "证据错误；操作需先核对。" and entry["resolved_issue_ids"] == ["wording"]
    request = next(data for tag, data in script.calls if tag == "repair_verify")
    assert [item["id"] for item in request["issues"]] == ["wording"]


def test_unresolved_issue_in_same_paragraph_protects_only_its_range():
    text = "需保留的争议；可修的措辞。"
    unresolved = issue("unknown", "需保留的争议")
    unresolved["suggestion"] = ""
    result, _, entry = run(text, [unresolved, issue("wording", "可修的措辞")], Script({"可修的措辞": "修正后的措辞"}))
    assert result == "需保留的争议；修正后的措辞。"
    assert entry["resolved_issue_ids"] == ["wording"] and entry["interfaces"]["unresolved"] == ["unknown"]


def test_unresolved_overlapping_issue_still_blocks_replacement():
    text = "需保留的争议。"
    unresolved = issue("unknown", "保留的争议")
    unresolved["suggestion"] = ""
    result, _, entry = run(text, [unresolved, issue("wording", "需保留的争议")], Script({"需保留的争议": "全部改写"}))
    assert result == text and entry["status"] == "REJECTED"


def test_semantic_verifier_receives_composed_same_paragraph_preview():
    script = Script({"错误一": "修正一", "错误二": "修正二"})
    result, _, entry = run("错误一，错误二。", [issue("a", "错误一"), issue("b", "错误二")], script)
    request = next(data for tag, data in script.calls if tag == "repair_verify")
    assert request["combined_preview"]["paragraphs"] == {"b0": result}
    assert len(request["transactions"]) == 2 and len(entry["patch"]["edits"]) == 1


def test_missing_verdict_rejects_only_its_independent_transaction():
    class MissingVerdict(Script):
        def chat(self, system, user, **kwargs):
            response = super().chat(system, user, **kwargs)
            if kwargs["trace_tag"] == "repair_verify":
                response = json.loads(response)
                response["verdicts"] = [v for v in response["verdicts"] if v["issue_id"] != "b"]
                return json.dumps(response)
            return response
    result, _, entry = run("错误甲，错误乙。", [issue("a", "错误甲"), issue("b", "错误乙")], MissingVerdict({}))
    assert result == "错误甲（待验证），错误乙。" and entry["resolved_issue_ids"] == ["a"]


def test_final_combined_validation_failure_rolls_back_all_selected_edits():
    fields = {"process.a": {"value": 1, "unit": "", "basis": "engineering_proposal", "source": "声明"},
              "process.b": {"value": 1, "unit": "", "basis": "engineering_proposal", "source": "声明"}}
    a, b = "甲 `process.a` 为1", "乙 `process.b` 为1"
    text = a + "，" + b + "。"
    items = [issue("a", a, ["process.a"]), issue("b", b, ["process.b"])]
    def mutate(patch, data):
        for row in patch["submissions"]:
            original = data["interface_contract"]["interfaces"][row["interface_id"]]["original"]
            path = "process.a" if original == a else "process.b"
            row["parameter_changes"] = [{"path": path, "field": dict(fields[path], value=2, source_quote=row["replacement"])}]
    def validate(updated):
        if updated["process.a"]["value"] + updated["process.b"]["value"] > 3:
            raise ValueError("组合约束未通过")
    script = Script({a: a.replace("为1", "为2"), b: b.replace("为1", "为2")}, mutate=mutate)
    result, updated, entry = execute(SimpleNamespace(llm=script), script, text, fields, permit_for(text, items, fields), items,
                                     [{"passage_id": "p1"}], geometry_validator=validate)
    assert result == text and updated == fields and entry["status"] == "REJECTED"
    assert all(group["status"] == "ROLLED_BACK" for group in entry["transactions"].values())
    assert len(entry["interfaces"]["transactions"]) == 2


def test_parameter_ratio_input_dependency_remains_atomic():
    fields = {"cathode.doping.x": {"value": 1, "unit": "", "basis": "engineering_proposal", "source": "声明"},
              "cathode.doping.total": {"value": 2, "unit": ""},
              "cathode.doping.fraction": {"value": .5, "unit": "", "ratio_inputs": ["cathode.doping.x", "cathode.doping.total"],
                  "quantity_kind": "ratio", "denominator": "总量"}}
    a, b = "系数 `cathode.doping.x` 为1", "比例 `cathode.doping.fraction` 待核实"
    text = a + "，" + b + "。"
    def mutate(patch, data):
        for row in patch["submissions"]:
            if data["interface_contract"]["interfaces"][row["interface_id"]]["original"] == a:
                row["parameter_changes"] = [{"path": "cathode.doping.x", "field": dict(fields["cathode.doping.x"],
                    source="修正来源", source_quote=row["replacement"])}]
    result, _, entry = run(text, [issue("x", a, ["cathode.doping.x"]), issue("ratio", b)], Script({}, failed=["ratio"], mutate=mutate), fields)
    assert result == text and entry["status"] == "REJECTED"
    assert len(entry["interfaces"]["transactions"]) == 1


def test_fraction_arithmetic_is_left_to_model_combined_review():
    from auto_battery_research.util.numeric_bindings import render
    fields = {"cathode." + k + "_fraction": {"value": v, "unit": "", "basis": "engineering_proposal", "source": "声明"}
              for k, v in [("active", .8), ("binder", .1), ("conductive", .1)]}
    a = render("活性占比{{param:cathode.active_fraction}}", fields)
    b = render("粘结剂占比{{param:cathode.binder_fraction}}", fields)
    text = a + "；" + b + "。"
    def mutate(patch, data):
        for row in patch["submissions"]:
            original = data["interface_contract"]["interfaces"][row["interface_id"]]["original"]
            path, value = ("cathode.active_fraction", .7) if original == a else ("cathode.binder_fraction", .2)
            row["parameter_changes"] = [{"path": path, "field": dict(fields[path], value=value, source_quote=row["replacement"])}]
    result, updated, entry = run(text, [issue("a", a, ["cathode.active_fraction"]), issue("b", b, ["cathode.binder_fraction"])],
                                  Script({a: a, b: b}, mutate=mutate), fields)
    assert entry["status"] == "APPLIED" and len(entry["interfaces"]["transactions"]) == 2
    assert updated["cathode.active_fraction"]["value"] == .7 and updated["cathode.binder_fraction"]["value"] == .2
    assert "70%" in result and "20%" in result


def test_bound_constraint_keeps_related_numeric_updates_atomic():
    from auto_battery_research.util.numeric_bindings import render
    fields = {"process." + role: {"value": value, "unit": "℃", "canonical_path": "process.temperature",
        "quantity_role": role, "basis": "engineering_proposal", "source": "声明"}
        for role, value in [("lower_bound", 1), ("upper_bound", 3)]}
    a = render("下限{{param:process.lower_bound}}", fields)
    b = render("上限{{param:process.upper_bound}}", fields)
    def mutate(patch, data):
        for row in patch["submissions"]:
            original = data["interface_contract"]["interfaces"][row["interface_id"]]["original"]
            path, value = ("process.lower_bound", 4) if original == a else ("process.upper_bound", 5)
            row["parameter_changes"] = [{"path": path, "field": {"value": value, "unit": "℃", "basis": "engineering_proposal",
                "source": "范围修订", "source_quote": row["replacement"]}}]
    result, updated, entry = run(a + "；" + b + "。", [issue("a", a, ["process.lower_bound"]), issue("b", b, ["process.upper_bound"])],
                                  Script({a: a, b: b}, mutate=mutate), fields)
    assert entry["status"] == "APPLIED" and len(entry["interfaces"]["transactions"]) == 1
    assert updated["process.lower_bound"]["value"] == 4 and updated["process.upper_bound"]["value"] == 5


@pytest.mark.parametrize("shared_scope", [True, False])
def test_voltage_window_constraint_keeps_joint_updates_atomic(shared_scope):
    from auto_battery_research.util.numeric_bindings import render
    fields = {"condition." + role: {"value": value, "unit": "V", "basis": "engineering_proposal", "source": "声明"}
              for role, value in [("v_min", 1), ("v_max", 3)]}
    if not shared_scope:
        fields["condition.v_max"]["conditions"] = {"用途": "高压应力"}
    a = render("电压下限{{param:condition.v_min}}", fields)
    b = render("电压上限{{param:condition.v_max}}", fields)
    def mutate(patch, data):
        for row in patch["submissions"]:
            original = data["interface_contract"]["interfaces"][row["interface_id"]]["original"]
            path, value = ("condition.v_min", 4) if original == a else ("condition.v_max", 5)
            row["parameter_changes"] = [{"path": path, "field": dict(fields[path], value=value, source_quote=row["replacement"])}]
    _, updated, entry = run(a + "；" + b + "。", [issue("a", a, ["condition.v_min"]), issue("b", b, ["condition.v_max"])],
                            Script({a: a, b: b}, mutate=mutate), fields)
    assert entry["status"] == "APPLIED" and len(entry["interfaces"]["transactions"]) == (1 if shared_scope else 2)
    assert updated["condition.v_min"]["value"] == 4 and updated["condition.v_max"]["value"] == 5
