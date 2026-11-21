"""实验前一致性快照：保留有效设计，将冲突字段和相关操作明确标为待定。"""
import re

from .scheme_ledger import digest, flatten_scheme
from .research_contract import bindings as requirement_bindings, field_equal, TOKEN
from .parameter_presentation import ALIASES, normalized, semantic_conditions, unit

VERSION = 1
INSTRUCTIONS = '''
输入consistency为程序完成的主方案一致性核对。pending_fields中的字段尚未定案，不能自行改值、删除组分或任选一套配方。
保留其他有效实验内容；涉及待定字段的配料与装配只给条件性安排，在gaps说明须先解决的声明，不声称可以直接执行。
新增材料组成、添加剂、溶剂及掺杂只能先作为方案候选，不能由实验步骤悄悄写入主方案。
scope=control的实验参数必须同时给group_id和canonical_path，值/单位与该对照groups.changes一致，不得用于主组。
原始课题没有提及某添加剂不等于明确禁止；存在审核争议时保留为待确认，不推定用户未说的限制。
'''


def identity_path(path):
    return (path.endswith(".material") or path.startswith("electrolyte.")
            or any(t in path for t in (".doping.", ".dopant", ".nominal_formula", ".composition", ".coating")))


def preflight(doc):
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    issues = []
    def add(path, reason, source):
        issue = {"path": path, "reason": reason, "source": source}
        if issue not in issues:
            issues.append(issue)
    for path, value in flatten_scheme(doc.get("scheme") or {}).items():
        if path.partition(".")[0] in ("cathode", "anode") and path.partition(".")[2] in ("L_um", "porosity", "R_s_um", "D_s_m2s"):
            continue  # 几何修订有独立的提交/同步契约，不能在预检中阻断其事务内试算。
        if path in params and not field_equal(path, value, params[path].get("value")):
            add(path, "当前方案与方案表声明不一致", "scheme_ledger")
    for path, expected in requirement_bindings(doc.get("requirements") or {}).items():
        actual = params.get(path, {})
        if not field_equal(path, actual.get("value"), expected["value"]) or actual.get("unit", "") != expected.get("unit", ""):
            add(path, "用户明确固定值与当前方案不一致", "user_requirement")
    from .physical_parameters import physical_declarations
    _, physical_issues = physical_declarations(params)
    for reason in physical_issues:
        for path in sorted(set(re.findall(r"[a-z_]+(?:\.[A-Za-z0-9_]+)+", reason)) & params.keys()):
            add(path, reason, "physical_quantity")
    canonical = {}
    for path, row in params.items():
        key = (ALIASES.get(path, path), row.get("quantity_kind"), row.get("denominator"), semantic_conditions(row.get("conditions") or {}))
        if row.get("value") is None:
            continue
        value = (normalized(row["value"]), unit(row.get("unit", "")))
        if key in canonical and canonical[key][1] != value:
            add(path, "同一参数的别名声明不同值或单位", "parameter_alias")
            add(canonical[key][0], "同一参数的别名声明不同值或单位", "parameter_alias")
        else:
            canonical[key] = (path, value)
    from .report_readiness import unresolved_issues
    for issue in unresolved_issues(doc):
        if issue.startswith("实验交付限制："):
            continue  # 本轮输出的科研意见只披露，不作为下一轮输入冲突重新建快照。
        if not any(w in issue for w in ("冲突", "错绑", "不一致")):
            continue
        for path in re.findall(r"[a-z_]+(?:\.[A-Za-z0-9_]+)+", issue):
            if path in params:
                add(path, "已有审核提出声明冲突，尚待核对：" + issue, "review")
    if doc.get("numeric_contract"):
        from .numeric_consistency import document_audit
        for issue in document_audit(doc)["issues"]:
            for path in issue["paths"]:
                add(path, issue["message"], "numeric_consistency")
    result = {"version": VERSION, "status": "PENDING" if issues else "READY", "issues": issues,
              "pending_fields": sorted({i["path"] for i in issues})}
    result["fingerprint"] = digest(result)
    return result


def paragraph_pending(paragraph, section, doc, snapshot):
    """按明确字段引用及组件操作判断影响范围，不擅自解释争议配方。"""
    if section not in ("fabrication", "assembly", "testing"):
        return []
    refs = TOKEN.findall(paragraph)
    names = {"cathode": "正极", "anode": "负极", "electrolyte": "电解液", "separator": "隔膜",
             "current_collector": "集流体", "condition": "测试条件", "process": "工艺"}
    pending = []
    for path in snapshot["pending_fields"]:
        domain = path.partition(".")[0]
        if path in refs or (identity_path(path) and (any(r.startswith(domain + ".") for r in refs)
                or names.get(domain, "\0") in paragraph)):
            pending.append(path)
        elif section == "assembly" and identity_path(path):
            pending.append(path)  # 配方未统一前，整电芯装配只能作为条件性安排。
    return pending


def parameter_pending_fields(doc):
    """Stage 5 门禁只采用当前数值/绑定冲突，科学审核意见仍在快照和报告披露。"""
    snapshot = preflight(doc)
    return sorted({item["path"] for item in snapshot["issues"] if item["source"] != "review"})


def stamp(record, doc):
    record["consistency"] = preflight(doc)
    pending = {}
    for section, paragraphs in record.get("sections", {}).items():
        if isinstance(paragraphs, list):
            for n, paragraph in enumerate(paragraphs):
                if isinstance(paragraph, str):
                    fields = paragraph_pending(paragraph, section, doc, record["consistency"])
                    if fields:
                        pending[f"{section}:{n}"] = fields
    record["pending_operations"] = pending
    return record


def validate_snapshot(record, doc):
    if "consistency" not in record:
        return  # 旧实验记录沿用原门禁；重新生成时建立快照。
    expected = stamp({"sections": record.get("sections", {})}, doc)
    operations = record.get("pending_operations")
    if record.get("parameter_value_contract") == 1 and isinstance(operations, dict):
        # 只读兼容旧版混入的缺项引用；实际方案冲突仍必须完整保留，不改旧内容或审核。
        from .experiment_values import pending_rows
        value_gaps = pending_rows(record).keys() - set(expected["consistency"]["pending_fields"])
        operations = {key: [field for field in fields if field not in value_gaps]
                      for key, fields in operations.items()}
        operations = {key: fields for key, fields in operations.items() if fields}
    if record.get("consistency") != expected["consistency"] or operations != expected["pending_operations"]:
        raise ValueError("实验一致性核对已过期或待定操作标记缺失，须重新核对当前方案")


def parameter_issues(record, doc):
    """阻止材料身份、主组参数以及对照范围通过实验命名空间绕过方案表。"""
    if "consistency" not in record:
        return []
    from .experiment_design import overlap_key, effective_data
    from .physical_parameters import identity as physical_identity
    current = effective_data(doc)["parameters"]
    groups = {g.get("id"): g for g in record.get("groups", []) if isinstance(g, dict) and isinstance(g.get("id"), str)}
    errors = []
    for key, row in record.get("parameters", {}).items():
        if not isinstance(row, dict):
            continue
        canonical = row.get("canonical_path", overlap_key(key))
        reason = None
        if not isinstance(canonical, str):
            reason = "canonical_path必须为规范字段字符串"
        elif "ref" in row:
            reference = row["ref"]
            if not isinstance(reference, str):
                reason = "ref必须为规范字段字符串"
            elif canonical in current and reference != canonical:
                expected = physical_identity(canonical, current[canonical])
                if expected is not None:
                    if reference not in current or physical_identity(reference, current[reference]) != expected:
                        reason = "已声明字段不能引用另一项物理量或工况"
                elif ALIASES.get(reference, reference) != ALIASES.get(canonical, canonical):
                    reason = "已声明字段不能引用另一项主方案参数"
        elif row.get("scope") == "control":
            group = groups.get(row.get("group_id"), {}) if isinstance(row.get("group_id"), str) else {}
            changed = group.get("changes", {}).get(canonical, {})
            if group.get("scope") != "control" or not changed or not field_equal(canonical, row.get("value"), changed.get("value")) or row.get("unit", "") != changed.get("unit", ""):
                reason = "对照参数须绑定已登记group_id、canonical_path及相同值/单位"
        elif identity_path(canonical):
            reason = "材料组成与电解液须先在主方案定案，实验只能ref引用"
        if reason:
            errors.append({"path": ["parameters", key], "reason": reason})
    return errors


def paragraph_issues(record, section, paragraph, bindings):
    if "consistency" not in record:
        return []
    from .experiment_protocol import assignment_issues
    errors = assignment_issues(paragraph, bindings)
    for key in TOKEN.findall(paragraph):
        row = record.get("parameters", {}).get(key, {})
        if row.get("scope") == "control":
            group_id = row.get("group_id")
            if not isinstance(group_id, str) or group_id not in paragraph or any(w in paragraph for w in ("主组", "主方案")):
                errors.append("对照参数只能用于其已登记组别，不能进入主组或未指明分组的操作")
    return errors
