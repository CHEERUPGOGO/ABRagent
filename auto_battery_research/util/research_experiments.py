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
所有verification_needed=true且非background要求都必须有一条关联；没有可执行方法时给gap及原因和获取方法。
用户阈值不能改写为工程阈值；工程建议须明确标记。组名和正文不得暗含另一套主方案。
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
    return [r for r in doc.get("requirements", {}).get("items", []) if r.get("verification_needed") and r["kind"] != "background"]


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
    if not doc.get("requirements"):
        return []
    errors = []
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
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("id"), str) or group["id"] in ids or group.get("scope") not in ("main", "control"):
                error(["groups"], "分组身份或范围不合法")
                break
            ids.add(group["id"])
            refs = group.get("parameter_refs", [])
            changes = group.get("changes", {})
            if not isinstance(refs, list) or any(p not in current for p in refs) or not isinstance(changes, dict):
                error(["groups"], "实验分组引用未知主方案字段")
                break
            if group["scope"] == "main":
                if set(refs) != set(identity_fields(doc)) or changes or group.get("candidate_id") != (doc.get("research") or {}).get("selected_candidate_id"):
                    error(["groups"], "实验主组必须完整引用当前唯一主方案，不能重定义组成")
            elif not changes or any(k not in current or not isinstance(v, dict) or "value" not in v or not v.get("reason") for k, v in changes.items()):
                error(["groups"], "对照必须用主方案规范字段声明变化及目的")
    tests = record.get("requirement_tests")
    expected = {r["id"] for r in required_tests(doc)}
    if not isinstance(tests, list) or any(not isinstance(t, dict) for t in tests) or len(tests) != len(expected) or {t.get("requirement_id") for t in tests} != expected:
        error(["requirement_tests"], "实验安排遗漏、重复或新增了研究目标")
    else:
        for t in tests:
            if t.get("status") not in ("planned", "gap", "not_applicable"):
                error(["requirement_tests"], "实验目标状态非法")
            keys = ("method", "conditions", "baseline", "criterion", "next_step") if t.get("status") == "planned" else ("reason", "next_step")
            if any(not isinstance(t.get(k), str) or not t[k].strip() for k in keys):
                error(["requirement_tests"], "测试安排须含方法、条件、基线、判据和下一步；缺口须含原因与获取路径")
            for value in t.values():
                if isinstance(value, str) and set(TOKEN.findall(value)) - set(record.get("parameters", {})):
                    error(["requirement_tests"], "测试矩阵引用未声明参数")
    extensions = record.get("extensions", [])
    allowed = {e.get("passage_id") for e in (doc.get("evidence") or []) + (doc.get("experiment_evidence") or [])}
    if not isinstance(extensions, list) or any(not isinstance(p, str) or TOKEN.search(p) or set(re.findall(r"\[([\w:-]+)\]", p)) - allowed for p in extensions):
        error(["extensions"], "扩展说明须为中文段落且使用归档引用，不得含未绑定占位符")
    return errors


def validate_parameter(key, row, doc):
    """参数可开放扩展，但实验不能另建主配方；组成字段需引用当前方案。"""
    if not doc.get("requirements") or "ref" in row or row.get("scope") == "control":
        return
    identity = identity_fields(doc)
    canonical = row.get("canonical_path", key)
    current = (doc.get("ledger_view") or {}).get("parameters", {})
    if canonical in identity:
        raise ValueError("实验主方案字段必须ref引用: " + canonical)
    if row.get("role") == "recipe" and not row.get("canonical_path"):
        raise ValueError("新增配方参数须声明canonical_path，不能绕过主方案检查")
    if row.get("unit") in ("formula", "formula_unit", "element", "salt", "solvent_system") or re.search(r"(?:^|\.)(?:formula|nominal_formula|doping|dopant|composition)(?:\.|$)", key):
        raise ValueError("材料组成或掺量须先进入Stage 4主方案，再供实验引用")
    if canonical in current and row.get("value") != current[canonical].get("value"):
        raise ValueError("实验规范字段与主方案冲突: " + canonical)


def render_groups(record, doc):
    if not record.get("groups"):
        return "本课题以研究分析为主，未安排新的电芯主组；验证方法按课题目标列出。"
    parts = ["| 分组 | 范围 | 变化与目的 |", "|---|---|---|"]
    from .report_narrative import describe
    for group in record.get("groups", []):
        changes = "；".join(describe(k, v) + "；" + v["reason"] for k, v in group.get("changes", {}).items())
        parts.append(f"| {cell(group.get('name') or group['id'])} | {'主组' if group['scope'] == 'main' else '对照'} | {cell(changes or '完整采用第3节主方案；材料、组成和固定条件不另行变更')} |")
    return "\n".join(parts)


def render_tests(record, doc, bindings):
    from .report_narrative import substitute_parameters
    labels = {r["id"]: r["description"] for r in doc["requirements"]["items"]}
    lines = ["| 课题目标 | 方法与条件 | 比较基线 / 判据 | 下一步 |", "|---|---|---|---|"]
    for t in record.get("requirement_tests", []):
        def show(key):
            return cell(substitute_parameters(t.get(key, ""), bindings))
        method = show("method") + "；" + show("conditions") if t["status"] == "planned" else "待补充：" + show("reason")
        lines.append(f"| {cell(labels[t['requirement_id']])} | {method} | {show('baseline')} / {show('criterion')} | {show('next_step')} |")
    return "\n".join(lines)
