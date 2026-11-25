"""新课题的实验分组与目标关联；复用方案表引用，不创造另一份主方案。"""
import re

from .research_contract import TOKEN
from .research_reporting import cell

INSTRUCTIONS = '''
本任务带通用课题契约，另外输出groups和requirement_tests。
groups=[{"id":"G1","name":"探索性主组","scope":"main","candidate_id":"已选候选ID或null",
"parameter_refs":["主方案全部材料/组成/改性/电解液字段"],"changes":{}},
{"id":"G0","name":"对照名称","scope":"control","parameter_refs":[],
"changes":{"主方案规范字段":{"value":"仅该对照的值","unit":"单位","reason":"改变目的"}}}]。
电芯设计只能有一个main组，主组不设changes，完整材料和改性身份来自方案表，禁止在fabrication.group.main等路径重新定义。
若scheme_required=false且只作分析，groups=[]；不安排无关的制备或装配，相应sections说明不适用及分析所需准备。
recipe类型的新参数须声明canonical_path对应规范方案字段；主方案已有字段只能ref引用；控制变化用scope=control。
requirement_tests=[{"requirement_id":"需要验证的要求ID","status":"planned|gap|not_applicable",
"method":"测试/分析方法", "conditions":"{{参数引用}}", "baseline":"比较对象", "criterion":"用户目标或明确标注的工程判据",
"next_step":"结果不满足时的下一步", "reason":"缺口/不适用的理由"}]。
所有verification_needed=true或null且非background要求都必须有一条关联；未知验证责任给gap，先明确验证方法，不能默认为无需验证。
planned仅表示已有方法安排，不表示条件齐全或验证完成。缺项影响及暂定结论边界由模型在reason、conditions与next_step说明，Reviewer结合全文核对；程序不按缺项引用自动改变目标状态。
gap仍保留已有方法和判据；not_applicable须说明不适用原因，不将未知参数本身视为整项目标不适用。
已经给出明确主推荐、仅最终效果或最优性尚待实验验证时，gap另声明gap_kind="validation"及recommendation_refs=["本版推荐参数键"]；
这些参数须在method/conditions/criterion通过{{参数引用}}实际使用，保留完整方法、条件、基线、判据、原因与后续验证安排。
未给推荐值、缺必要操作条件、验证责任未知或交付遗漏仍为gap_kind="delivery"（省略时同此），不能用validation免除交付。
requirements.groups只是上层目标分组，不是样品groups，也不新增测试行；共享方法可以关联多个独立指标，但不得把容量保持与电压衰减合成一个判据。复合固定条件须遵守全部bindings。
用户阈值不能改写为工程阈值；工程建议须明确标记。组名和正文不得暗含另一套主方案。
参数引用用户目标数值时附requirement_id；比较符号与“显著”等限定必须保留在criterion及判定段落，不得把“显著高于74%”简写成“要求为74%”。
首轮采用一条可执行路线，给分组表和目标测试矩阵所需字段；扩展表征或采购细节放extensions段落数组。
sections中的数值与材料引用parameters；分类名称不能作为unit。'''


def identity_fields(doc):
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    chosen = (doc.get("research") or {}).get("selected_candidate_id")
    refs = set()
    for c in (doc.get("research") or {}).get("candidates", []):
        if c.get("id") == chosen:
            refs.update(c.get("parameter_refs", []))
    refs.update(k for k, v in params.items() if v.get("value") is not None and (
        k.endswith(".material") or k.startswith("electrolyte.") or any(t in k for t in (".doping.", ".dopant", ".nominal_formula", ".coating", ".composition"))))
    return sorted(refs)


def required_tests(doc):
    contract = doc.get("requirements") or {}
    rows = []
    for row in contract.get("items", []):
        legacy_unknown = contract.get("status") == "unparsed" and row.get("status") == "ambiguous"
        if row["kind"] != "background" and (row.get("verification_needed") is not False or legacy_unknown):
            rows.append(dict(row, verification_needed=None) if legacy_unknown else row)
    return rows


def fallback_links(record, doc, reason="实验关联尚未完成"):
    record["groups"] = [{"id": "G1", "name": "当前探索性主组", "scope": "main",
        "candidate_id": (doc.get("research") or {}).get("selected_candidate_id"),
        "parameter_refs": identity_fields(doc), "changes": {}}]
    if doc.get("requirements", {}).get("scheme_required") is False:
        record["groups"] = []
    record["requirement_tests"] = [{"requirement_id": r["id"], "status": "gap", "method": "", "conditions": "",
        "baseline": "", "criterion": "", "reason": reason,
        "next_step": "围绕「" + r["quote"] + "」核对可用测试方法、设备与比较条件后制定验证。"} for r in required_tests(doc)]
    record["extensions"] = []


def diagnose_links(record, doc):
    from .experiment_values import control_value_issues
    from .experiment_design import effective_data
    control_errors = control_value_issues(record, effective_data(doc)["parameters"])
    if not doc.get("requirements"):
        return control_errors
    errors = list(control_errors)
    def error(path, reason):
        errors.append({"path": path, "reason": reason})
    groups = record.get("groups")
    current = (doc.get("ledger_view") or {}).get("parameters", {})
    analysis_only = doc.get("requirements", {}).get("scheme_required") is False
    if analysis_only and groups == []:
        pass
    elif not isinstance(groups, list) or len([g for g in groups if isinstance(g, dict) and g.get("scope") == "main"]) != 1:
        error(["groups"], "必须声明唯一主组及其方案表引用")
    else:
        ids = set()
        for index, group in enumerate(groups):
            if not isinstance(group, dict) or not isinstance(group.get("id"), str) or group["id"] in ids or group.get("scope") not in ("main", "control"):
                error(["groups", index], "分组身份或范围不合法")
                continue
            ids.add(group["id"])
            refs = group.get("parameter_refs", [])
            changes = group.get("changes", {})
            if not isinstance(refs, list) or any(p not in current for p in refs) or not isinstance(changes, dict):
                error(["groups", index], "实验分组引用未知主方案字段")
                continue
            if group["scope"] == "main":
                if set(refs) != set(identity_fields(doc)) or changes or group.get("candidate_id") != (doc.get("research") or {}).get("selected_candidate_id"):
                    error(["groups", index], "实验主组必须完整引用当前唯一主方案，不能重定义组成")
            elif not changes or any(k not in current or not isinstance(v, dict) or "value" not in v or not v.get("reason") for k, v in changes.items()):
                error(["groups", index], "对照必须用主方案规范字段声明变化及目的")
    tests = record.get("requirement_tests")
    expected = {r["id"] for r in required_tests(doc)}
    if not isinstance(tests, list) or any(not isinstance(t, dict) for t in tests) or len(tests) != len(expected) or {t.get("requirement_id") for t in tests} != expected:
        error(["requirement_tests"], "实验安排遗漏、重复或新增了研究目标")
    else:
        for index, t in enumerate(tests):
            from .experiment_delivery import validation_gap_errors
            for reason in validation_gap_errors(t, record, doc):
                error(["requirement_tests", index], reason)
            if t.get("status") not in ("planned", "gap", "not_applicable"):
                error(["requirement_tests", index], "实验目标状态非法")
            requirement = next(r for r in required_tests(doc) if r["id"] == t["requirement_id"])
            if t.get("status") == "not_applicable" and (requirement.get("verification_needed") is None or doc["requirements"].get("status") == "unparsed"):
                error(["requirement_tests", index], "未知验证责任不能直接标为不适用，应保留gap并明确验证方法")
            keys = ("method", "conditions", "baseline", "criterion", "next_step") if t.get("status") == "planned" else ("reason", "next_step")
            if any(not isinstance(t.get(k), str) or not t[k].strip() for k in keys):
                error(["requirement_tests", index], "测试安排须含方法、条件、基线、判据和下一步；缺口须含原因与获取路径")
            for value in t.values():
                if isinstance(value, str) and set(TOKEN.findall(value)) - set(record.get("parameters", {})):
                    error(["requirement_tests", index], "测试矩阵引用未声明参数")
    extensions = record.get("extensions", [])
    allowed = {e.get("passage_id") for e in (doc.get("evidence") or []) + (doc.get("experiment_evidence") or [])}
    if not isinstance(extensions, list) or any(not isinstance(p, str) or TOKEN.search(p) or set(re.findall(r"\[([\w:-]+)\]", p)) - allowed for p in extensions):
        error(["extensions"], "扩展说明须为中文段落且使用归档引用，不得含未绑定占位符")
    return errors


def recover_links(record, doc, reason="实验关联尚待确认"):
    """独立保留有效组别和测试，不因一个坏对照或一个遗漏目标整表回退。"""
    import copy
    from collections import Counter
    if not doc.get("requirements"):
        from .experiment_values import control_value_issues
        from .experiment_design import effective_data
        bad = {e["path"][1]: e["reason"] for e in control_value_issues(record, effective_data(doc)["parameters"]) if len(e["path"]) == 2}
        kept = []
        groups = record.get("groups", [])
        if not isinstance(groups, list):
            return  # 顶层结构非法仍由终验拒绝，不能猜测整表。
        for index, group in enumerate(groups):
            if index not in bad:
                kept.append(group)
                continue
            entry = {"record": copy.deepcopy(group), "reason": bad[index]}
            if entry not in record.setdefault("pending_groups", []):
                record["pending_groups"].append(entry)
        record["groups"] = kept
        return
    base = {"parameters": record.get("parameters", {})}
    fallback_links(base, doc, reason)
    raw_groups = record.get("groups", [])
    raw_groups = raw_groups if isinstance(raw_groups, list) else []
    counts = Counter(g.get("id") for g in raw_groups if isinstance(g, dict) and isinstance(g.get("id"), str))
    kept, pending = [], []
    for group in raw_groups:
        valid = isinstance(group, dict) and isinstance(group.get("id"), str) and counts[group["id"]] == 1
        probe = copy.deepcopy(base)
        if valid:
            probe["groups"] = [group] if group.get("scope") == "main" else base["groups"] + [group]
            valid = not any(e["path"][0] == "groups" for e in diagnose_links(probe, doc))
        (kept if valid else pending).append(copy.deepcopy(group))
    mains = [g for g in kept if g["scope"] == "main"]
    if doc["requirements"].get("scheme_required") is False:
        pending.extend(kept); kept = []
    else:
        main = mains[0] if len(mains) == 1 else base["groups"][0]
        if len(mains) > 1:
            pending.extend(mains)
        controls = [g for g in kept if g["scope"] == "control" and g["id"] != main["id"]]
        kept = [main] + controls
    record["groups"] = kept
    for group in pending:
        entry = {"record": group, "reason": reason}
        if entry not in record.setdefault("pending_groups", []):
            record["pending_groups"].append(entry)
    raw_tests = record.get("requirement_tests", [])
    raw_tests = raw_tests if isinstance(raw_tests, list) else []
    counts = Counter(t.get("requirement_id") for t in raw_tests if isinstance(t, dict) and isinstance(t.get("requirement_id"), str))
    tests = copy.deepcopy(base["requirement_tests"])
    for index, fallback in enumerate(tests):
        row = next((t for t in raw_tests if isinstance(t, dict) and t.get("requirement_id") == fallback["requirement_id"]), None)
        if not row or counts[row["requirement_id"]] != 1:
            continue
        probe = copy.deepcopy(base)
        probe["requirement_tests"][index] = row
        if not any(e["path"][0] == "requirement_tests" for e in diagnose_links(probe, doc)):
            tests[index] = copy.deepcopy(row)
    record["requirement_tests"] = tests


def validate_parameter(key, row, doc):
    """参数可开放扩展，但实验不能另建主配方；组成字段需引用当前方案。"""
    if "ref" in row or row.get("scope") == "control":
        return
    from .experiment_design import overlap_key
    canonical = row.get("canonical_path", overlap_key(key))
    current = (doc.get("ledger_view") or {}).get("parameters", {})
    declared = current.get(canonical) or {}
    if declared.get("locked") and declared.get("value") is None:
        raise ValueError("用户锁定的空字段须先明确，不能由实验建议填充: " + canonical)
    if not doc.get("requirements"):
        return
    identity = identity_fields(doc)
    if canonical in identity:
        raise ValueError("实验主方案字段必须ref引用: " + canonical)
    if row.get("role") == "recipe" and not row.get("canonical_path"):
        raise ValueError("新增配方参数须声明canonical_path，不能绕过主方案检查")
    if row.get("unit") in ("formula", "formula_unit", "element", "salt", "solvent_system") or re.search(r"(?:^|\.)(?:formula|nominal_formula|doping|dopant|composition)(?:\.|$)", key):
        raise ValueError("材料组成或掺量须先进入Stage 4主方案，再供实验引用")
    if declared.get("value") is not None and (row.get("value") != declared["value"]
                                              or (row.get("unit") or "") != (declared.get("unit") or "")):
        raise ValueError("实验规范字段与主方案冲突: " + canonical)


def render_groups(record, doc):
    if not record.get("groups"):
        return "本课题以研究分析为主，未安排新的电芯主组；验证方法按课题目标列出。"
    parts = ["| 样品编号 | 分组名称 | 组别 | 组成、变量与目的 |", "|---|---|---|---|"]
    from .report_narrative import describe
    for group in record.get("groups", []):
        changes = "；".join(describe(k, v) + ("；" + v["reason"] if v.get("reason") else "") for k, v in group.get("changes", {}).items())
        parts.append(f"| {cell(group['id'])} | {cell(group.get('name') or group['id'])} | {'主组' if group['scope'] == 'main' else '对照'} | {cell(changes or '完整采用第3节主方案；材料、组成和固定条件不另行变更')} |")
    return "\n".join(parts)


def render_tests(record, doc, bindings):
    from .report_narrative import substitute_parameters
    from .experiment_values import substitute_pending
    labels = {r["id"]: r["description"] for r in doc["requirements"]["items"]}
    lines = ["方法安排不代表条件齐全或验证完成；具体限制与后续工作见各项目标说明及科学审核。", "",
             "| 课题目标 | 方法与条件 | 比较基线 / 判据 | 说明与待解决事项 | 下一步 |", "|---|---|---|---|---|"]
    for t in record.get("requirement_tests", []):
        def show(key):
            from .experiment_alignment import expand_profiles
            return cell(substitute_parameters(substitute_pending(expand_profiles(t.get(key, ""), record), record), bindings))
        method = "；".join(show(key) for key in ("method", "conditions") if t.get(key)) or "—"
        prefix = {"gap": "待补充说明：", "not_applicable": "不适用说明："}.get(t.get("status"), "")
        if t.get("status") == "gap" and t.get("gap_kind") == "validation":
            from .experiment_delivery import validation_gap_errors
            if not validation_gap_errors(t, record, doc):
                prefix = "推荐已给出，待实验验证："
        note = prefix + show("reason")
        lines.append(f"| {cell(labels.get(t.get('requirement_id'), '待关联课题要求'))} | {method} | {show('baseline')} / {show('criterion')} | {note} | {show('next_step')} |")
    return "\n".join(lines)
