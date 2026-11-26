"""主方案数值以显式字段引用渲染；仅更新已签发引用，不全局替换同值数字。"""
import copy
import re

from .numeric_consistency import audit, canonical, identity, context, UNITS

TOKEN = re.compile(r"\{\{(param|derived):([A-Za-z][A-Za-z0-9_.]*)\}\}")
BOUND = re.compile(r"<!-- abr-(param|derived):([A-Za-z][A-Za-z0-9_.]*) -->(.*?)<!-- /abr-value -->", re.S)

INSTRUCTIONS = '''
【数值单一来源】设计JSON改为version=2，并增加顶层parameters对象（可为空）。
已有current_parameters及fixed_geometry中的非空值和锁定值仅引用，不重新声明；未锁定null可补充有依据的声明。新工程参数在parameters登记一次，
每项含value（数值、明确材料/路线文字或null）、unit、basis、source、meaning；basis只允许engineering_proposal或user_requirement。
参考文献后明确采用到当前方案的取值仍是待验证的engineering_proposal，文献段落ID及原工况写source；
仅介绍原文献的样品/性能/工况不登记为当前方案参数，不填basis=literature，也不继承原文献实测结论。
用户原文要求可用user_requirement，但不能自行授予锁定或已审核状态，不输出locked；锁定仅由程序核验用户原文后登记。
文字声明可省略quantity_kind，程序按文字处理；通用fraction标签等价于ratio，不据此推断质量/体积或位点分母。
路径必须以cathode/anode/electrolyte/separator/current_collector/packaging/process/condition开头；
掺杂用cathode.doping.*，合成用process.*，测试条件用condition.*，禁止独立doping/synthesis/test命名空间。
当前主方案采用的完整正极名义式使用cathode.nominal_formula（或cathode.doping.nominal_formula）。
若使用cathode.doping.rank1_formula等自定义字段保存已采用的主推完整配方，必须显式给出canonical_path="cathode.nominal_formula"。
名义式属于材料身份声明；字段名中的排行序号不能代替主方案身份关联。母体式仍单列cathode.base_formula。
备选或对照配方须声明对应scope和样品用途，不能将尚未采用的排序候选关联成当前主方案材料。
单位只含单位本身，比例口径放denominator；分数按0–1、unit为空串，不写at%（总阳离子）这类混合单位。
例如同一分母下3 mol%登记value=0.03、unit=""并保留明确denominator；pH等数量用JSON数字7.5，不能写字符串"7.5"。
可选canonical_path、quantity_kind、denominator、group_id、purpose、conditions、quantity_role。
quantity_role为value/target/lower_bound/upper_bound/experiment_max；允许上限与实验封顶分开，不能仅因数值不同判冲突。
upper_bound等用途标签应填quantity_role，quantity_kind仍填physical/ratio等量值类型；两处用途冲突会拒绝。
倍率参数condition.c_rate的unit为C，meaning明确写循环/充放电倍率；参考电流mA/g另存，不用倍率字段承载。
不同角色复用同一canonical_path，不同样品/敏感性情景给不同group_id或purpose；文献数字保留文献引用及原工况，不注册为当前主方案。
规范数值字段（如cathode.L_um、electrolyte.volume）只用于无专属条件的主方案声明。
带conditions/group_id/scope/专属purpose的声明须用独立路径并显式canonical_path指向原规范字段，
例如cathode.dry_coating.L_um配canonical_path="cathode.L_um"，保留conditions="干涂层"；不能通过删除条件回避检查。
正文中所有当前主方案数值用{{param:字段路径}}引用（程序带单位渲染，不重复写单位），
不能重新手写同一数值。正文参数名正常写中文。未声明则留缺口，不为填引用虚构值。
parameters中的每个新声明（包括计量基准、路线等文字）都必须在其适用正文至少引用一次；
只在正文原样写值不算字段引用。不要登记未采用或无法明确绑定样品、工况及用途的参数。
计算方法、输入和结果由模型在正文明确给出，不使用{{derived:...}}；程序不再建立预算派生链。
需要后续采用的计算值按普通工程参数登记，source写明公式、输入来源和适用口径；不得自称实测或程序验证。
ratio_inputs可保留为输入来源说明，不触发程序除法或自动生成结果。位点比例和元素系数变化不得直接作为容量损失。
'''

from .model_calculations import INSTRUCTIONS as CALCULATION_INSTRUCTIONS
from .parameter_names import INSTRUCTIONS as NAME_INSTRUCTIONS
INSTRUCTIONS += NAME_INSTRUCTIONS + CALCULATION_INSTRUCTIONS


def normalize_declaration(row):
    """通用比例标签与文字类型兼容处理，保留原值、来源和分母。"""
    row = copy.deepcopy(row)
    kind = row.get("quantity_kind")
    if isinstance(kind, str) and kind in ("target", "lower_bound", "upper_bound", "experiment_max"):
        if row.get("quantity_role", kind) != kind:
            raise ValueError("量值类型中的用途标签与quantity_role冲突")
        row["quantity_role"] = kind
        # 用途不等于量值类型；只保留显式分母/单位，不能猜成质量或位点分数。
        row["quantity_kind"] = ("text" if isinstance(row.get("value"), str) else
                                "ratio" if row.get("denominator") else
                                "physical" if row.get("unit") else "other")
    if row.get("quantity_kind") == "fraction":
        row["quantity_kind"] = "ratio"
    if isinstance(row.get("value"), str):
        row.setdefault("quantity_kind", "text")
    return row


def declarations(data, current):
    """新提案不覆盖已有字段或同义身份；来源必须通过正文引用登记。"""
    from .scheme_ledger import validate_field
    result = copy.deepcopy(current or {})
    proposed = data.get("parameters", {})
    if not isinstance(proposed, dict):
        raise ValueError("parameters必须是字段到参数的映射")
    for path, row in proposed.items():
        if not isinstance(row, dict) or set(row) - {"value", "unit", "basis", "source", "meaning", "canonical_path", "quantity_kind", "denominator", "group_id", "scope", "purpose", "conditions", "quantity_role", "ratio_inputs"}:
            raise ValueError("数值提案包含非法字段或权限元数据")
        from .parameter_names import checked_name
        row = checked_name(path, normalize_declaration(row))
        if row.get("basis") not in ("engineering_proposal", "user_requirement") or type(row.get("value")) not in (int, float, str, type(None)):
            raise ValueError("新声明须为工程建议或用户要求的数值、文字或缺项")
        from .parameter_presentation import QUANTITY_KINDS
        if row.get("quantity_kind") is not None and (not isinstance(row["quantity_kind"], str) or row["quantity_kind"] not in QUANTITY_KINDS):
            raise ValueError("quantity_kind必须是已知量值类型：" + path)
        if isinstance(row.get("value"), str) and (row.get("quantity_kind") != "text" or not row["value"].strip()):
            raise ValueError("文字声明必须使用quantity_kind=text，数值不能包装为字符串：" + path)
        if isinstance(row.get("value"), str) and re.fullmatch(r"\s*[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*%?\s*", row["value"]):
            raise ValueError("数值不能包装为文字声明：" + path)
        if row.get("quantity_role", "value") not in ("value", "target", "lower_bound", "upper_bound", "experiment_max"):
            raise ValueError("未知数值用途")
        validate_field(path, row)
        if path in UNITS and context(row) != ("main", "main", "[]"):
            raise ValueError("专属样品或工况须使用独立路径和canonical_path，不能占用主方案字段：" + path)
        if path in result and (result[path].get("locked") or result[path].get("value") is not None):
            if (row.get("value") == result[path].get("value")
                    and (row.get("unit") or "") == (result[path].get("unit") or "")
                    and identity(path, row) == identity(path, normalize_declaration(result[path]))):
                continue  # 同值重复声明沿用当前记录及锁定，不因格式冗余拒绝全文。
            raise ValueError("已有方案字段只能引用，不能重新声明：" + path)
        for key, old in result.items():
            if (key != path and identity(key, old) == identity(path, row)
                    and (old.get("locked") or old.get("value") is not None)):
                raise ValueError("已有同义数值字段只能引用，不能用别名重新声明：" + key)
        result[path] = dict(copy.deepcopy(row), locked=False, reference_origin="writer_v2")
    from .parameter_names import foil_material_errors
    if errors := foil_material_errors(result):
        raise ValueError("；".join(errors))
    return result


def display(kind, key, parameters, calculation=None, *, effective=None):
    from .parameter_presentation import value_text
    if kind == "param":
        if key not in parameters:
            raise ValueError("引用了未登记参数：" + key)
        from .parameter_names import effective_parameters
        row = (effective if effective is not None else effective_parameters(parameters))[key]
        from .material_ids import canonical_field, identity_path
        row = canonical_field(key, row)
        info = identity_path(key, row)
        if info and info[1] == "material" and "original_value" in row:
            # 材料ID用于匹配，引用保留同一材料的原始声明及其中的物性说明。
            row["value"] = row["original_value"]
        return value_text(key, row) if row.get("value") is not None else "未确定（" + (row.get("meaning") or key) + "缺少声明）"
    result = calculation or audit(parameters)
    if result.get("calculation_policy") != "legacy_program":
        raise ValueError("程序派生引用已停用，请由模型在正文给出计算方法、输入、结果及单位：" + key)
    row = result["derived"].get(key)
    if row is None:
        raise ValueError("引用了未知计算项：" + key)
    if row["status"] in ("INCOMPLETE", "NOT_APPLICABLE") or row.get("value") is None:
        return "未完成计算（缺失或歧义输入：" + "、".join(row["missing"]) + "）"
    return value_text(key, {"value": row["value"], "unit": row["unit"]})


def render(text, parameters, *, legacy_calculations=False):
    from .design_structure import prose_lines
    if "<!-- abr-param:" in text or "<!-- abr-derived:" in text:
        raise ValueError("Writer不能自行声明程序数值标记")
    result = audit(parameters, legacy_calculations=legacy_calculations)
    from .parameter_names import effective_parameters
    effective = effective_parameters(parameters)
    edits = [(offset + m.start(), offset + m.end(), m[1], m[2])
             for offset, line in prose_lines(text) for m in TOKEN.finditer(line)]
    for start, end, kind, key in reversed(edits):
        text = text[:start] + f"<!-- abr-{kind}:{key} -->" + display(kind, key, parameters, result, effective=effective) + "<!-- /abr-value -->" + text[end:]
    if any("{{param:" in line or "{{derived:" in line for _, line in prose_lines(text)):
        raise ValueError("数值引用语法无效")
    return text


def occurrences(text):
    from .design_structure import prose_lines
    # 数值引用不可跨行；代码示例不授予参数写权限。
    return [(offset + m.start(), offset + m.end(), m[1], m[2], m[3])
            for offset, line in prose_lines(text) for m in BOUND.finditer(line)]


def refresh(text, parameters):
    from .parameter_names import effective_parameters
    effective = effective_parameters(parameters)
    for start, end, kind, key, _ in reversed(occurrences(text)):
        if kind == "param":
            text = text[:start] + f"<!-- abr-param:{key} -->" + display(kind, key, parameters, effective=effective) + "<!-- /abr-value -->" + text[end:]
    return text


def normalize_material_references(text, parameters):
    """一并规范材料参数和已签发引用，保留原声明；未知显示差异留待诊断。"""
    from .material_ids import canonical_parameters, identity_path
    from .parameter_presentation import value_text
    normalized = canonical_parameters(parameters)

    def synchronize_materials(body):
        for start, end, kind, key, shown in reversed(occurrences(body)):
            row = normalized.get(key)
            info = identity_path(key, row)
            if kind != "param" or row is None or not info or info[1] != "material":
                continue
            expected = display(kind, key, normalized)
            # 兼容旧程序输出的规范名称，不能借身份同义放行物性数字或正文篡改。
            accepted = (display(kind, key, parameters), value_text(key, row), expected)
            if shown in accepted:
                body = body[:start] + f"<!-- abr-param:{key} -->" + expected + "<!-- /abr-value -->" + body[end:]
        return body

    changed = synchronize_materials(text)
    for row in normalized.values():
        quote = row.get("source_quote")
        if quote and row.get("basis") != "literature":
            refreshed = synchronize_materials(quote)
            if refreshed != quote and refreshed in changed:
                row["source_quote"] = refreshed
    return changed, normalized


def binding_errors(text, parameters):
    from .design_structure import prose_lines
    from .parameter_names import effective_parameters
    effective = effective_parameters(parameters)
    issues = []
    for _, _, kind, key, shown in occurrences(text):
        if kind == "derived":
            continue  # 历史派生显示交由模型复核，不自动重算或沿用旧链作为门禁。
        try:
            expected = display(kind, key, parameters, effective=effective)
            if shown != expected:
                issues.append("数值引用与当前参数不一致：" + key)
        except ValueError as exc:
            issues.append(str(exc))
    remaining = BOUND.sub("", "\n".join(line for _, line in prose_lines(text)))
    if any(marker in remaining for marker in ("<!-- abr-param:", "<!-- abr-derived:", "<!-- /abr-value -->", "{{param:", "{{derived:")):
        issues.append("数值引用标记不完整或尚未渲染")
    return list(dict.fromkeys(issues))


class SourceBindingError(ValueError):
    """保留全部未绑定字段，供协议修复和诊断使用。"""

    def __init__(self, issues):
        self.issues = issues
        super().__init__("；".join(issue["reason"] for issue in issues))


def source_parameters(text, parameters):
    """程序记录新提案所在完整段落，一次汇总全部未采用的声明。"""
    from .design_repair import blocks
    result = copy.deepcopy(parameters)
    issues = []
    paragraphs = [(body, occurrences(body)) for body in blocks(text)[1].values()]
    references = {(kind, path) for _, refs in paragraphs for _, _, kind, path, _ in refs}
    linked = {(kind, path): dependencies(kind, path, parameters) for kind, path in references}
    for key, row in result.items():
        if row.get("reference_origin") != "writer_v2":
            continue
        matching = [body for body, refs in paragraphs if any(
            kind == "param" and key in linked[(kind, path)] or kind == "derived" and path == "calculated." + key
            for _, _, kind, path, _ in refs)]
        if not matching:
            issues.append({"code": "UNBOUND_PARAMETER", "path": ["parameters", key],
                           "reason": "工程提案未在正文中采用：" + key})
        else:
            row["source_quote"] = matching[0]
    if issues:
        raise SourceBindingError(issues)
    return result


def dependencies(kind, key, parameters):
    if kind == "param":
        row = parameters.get(key, {})
        if row.get("value") is not None or row.get("locked"):
            return {key}
        from .parameter_names import checked_name
        linked = {key}
        for path, other in parameters.items():
            try:
                checked_name(path, other)
                if identity(path, other) == identity(key, row):
                    linked.add(path)
            except (ValueError, TypeError):
                continue
        return linked
    return set()


def synchronize(old_text, new_text, old_params, new_params):
    """原始参数引用仍冻结；授权正文中的历史派生标记可改为模型计算说明。"""
    old = occurrences(old_text)
    new = occurrences(new_text)
    if [(kind, key) for _, _, kind, key, _ in old if kind == "param"] != [(kind, key) for _, _, kind, key, _ in new if kind == "param"]:
        raise ValueError("内容补丁不能删除、新增或重新绑定数值引用")
    from collections import Counter
    if Counter(key for _, _, kind, key, _ in new if kind == "derived") - Counter(key for _, _, kind, key, _ in old if kind == "derived"):
        raise ValueError("不能新增程序派生引用，请直接写明模型计算方法和结果")
    # 模型只能改正文和获授权的参数，不能直接篡改程序显示值。
    for _, _, kind, key, shown in new:
        if kind == "derived":
            continue
        if shown not in (display(kind, key, old_params), display(kind, key, new_params)):
            raise ValueError("补丁引用的显示值必须与修改前或已授权修改后的参数一致")
    changed = refresh(new_text, new_params)
    from .source_metadata import synchronize_parameters
    synchronize_parameters(old_text, changed, old_params, new_params)
    if binding_errors(changed, new_params):
        raise ValueError("更新后数值引用无法完成一致校验")
    return changed
