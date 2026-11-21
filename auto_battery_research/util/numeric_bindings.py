"""主方案数值以显式字段引用渲染；仅更新已签发引用，不全局替换同值数字。"""
import copy
import re

from .numeric_consistency import audit, canonical, identity, context, UNITS

TOKEN = re.compile(r"\{\{(param|derived):([A-Za-z][A-Za-z0-9_.]*)\}\}")
BOUND = re.compile(r"<!-- abr-(param|derived):([A-Za-z][A-Za-z0-9_.]*) -->(.*?)<!-- /abr-value -->", re.S)

INSTRUCTIONS = '''
【数值单一来源】设计JSON改为version=2，并增加顶层parameters对象（可为空）。
已有current_parameters及fixed_geometry仅引用，不重新声明。新工程参数在parameters登记一次，
每项含value（数值、明确材料/路线文字或null）、unit、basis=engineering_proposal、source、meaning；文字声明用quantity_kind=text。
路径必须以cathode/anode/electrolyte/separator/current_collector/packaging/process/condition开头；
掺杂用cathode.doping.*，合成用process.*，测试条件用condition.*，禁止独立doping/synthesis/test命名空间。
单位只含单位本身，比例口径放denominator；分数按0–1、unit为空串，不写at%（总阳离子）这类混合单位。
可选canonical_path、quantity_kind、denominator、group_id、purpose、conditions、quantity_role。
quantity_role为value/target/lower_bound/upper_bound/experiment_max；允许上限与实验封顶分开，不能仅因数值不同判冲突。
不同角色复用同一canonical_path，不同样品/敏感性情景给不同group_id或purpose；文献数字保留文献引用及原工况，不注册为当前主方案。
正文中所有当前主方案数值用{{param:字段路径}}引用（程序带单位渲染，不重复写单位），
不能重新手写同一数值。正文参数名正常写中文。未声明则留缺口，不为填引用虚构值。
parameters中的每个新声明（包括计量基准、路线等文字）都必须在其适用正文至少引用一次；
只在正文原样写值不算字段引用。不要登记未采用或无法明确绑定样品、工况及用途的参数。
程序派生结果用{{derived:计算ID}}，不在parameters重复登记计算结果：
cathode.pore_volume_uL_cm2，cathode.calculated_pore_volume_uL，
condition.calculated_electrolyte_to_cathode_pore_ratio，condition.calculated_current_density，
cathode.calculated_loading_mg_cm2，cathode.calculated_active_loading_mg_cm2（负极用anode对应字段）。
另支持cathode.areal_capacity_mAh_cm2（负极同理），依赖同工况总面载量、活性质量占比和明确capacity_mAh_g；
该比容量可以是显式工程假设，计算结果不提升为实测；不能从1 C电流定义推断容量。
E/P明确指正极孔体积；其他分母单列，不混用。相关规范输入为electrolyte.volume（uL）、
cathode.electrode_area（cm2）、cathode.L_um（um）、cathode.porosity（0–1）、
condition.c_rate（C）、condition.C_rate_reference_current（mA/g）。
显式比例字段可声明ratio_inputs=[分子字段路径,分母字段路径]，并给quantity_kind=site_fraction或ratio及denominator；
程序只核对已声明的同单位、同工况数量，结果引用{{derived:calculated.该比例字段路径}}，不从化学式猜位点。
数据不全时程序会显示未完成及缺失项，不自动补值。位点比例和元素系数变化不得直接作为容量损失。
'''


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
        if row.get("basis") != "engineering_proposal" or type(row.get("value")) not in (int, float, str, type(None)):
            raise ValueError("提案必须是工程数值、明确文字声明或缺项")
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
            raise ValueError("已有方案字段只能引用，不能重新声明：" + path)
        for key, old in result.items():
            if key != path and identity(key, old) == identity(path, row):
                raise ValueError("已有同义数值字段只能引用，不能用别名重新声明：" + key)
        result[path] = dict(copy.deepcopy(row), locked=False, reference_origin="writer_v2")
    return result


def display(kind, key, parameters, calculation=None):
    from .parameter_presentation import value_text
    if kind == "param":
        if key not in parameters:
            raise ValueError("引用了未登记参数：" + key)
        row = parameters[key]
        return value_text(key, row) if row.get("value") is not None else "未确定（" + (row.get("meaning") or key) + "缺少声明）"
    result = calculation or audit(parameters)
    row = result["derived"].get(key)
    if row is None:
        raise ValueError("引用了未知计算项：" + key)
    if row["status"] in ("INCOMPLETE", "NOT_APPLICABLE") or row.get("value") is None:
        return "未完成计算（缺失或歧义输入：" + "、".join(row["missing"]) + "）"
    return value_text(key, {"value": row["value"], "unit": row["unit"]})


def render(text, parameters):
    from .design_structure import prose_lines
    if "<!-- abr-param:" in text or "<!-- abr-derived:" in text:
        raise ValueError("Writer不能自行声明程序数值标记")
    result = audit(parameters)
    edits = [(offset + m.start(), offset + m.end(), m[1], m[2])
             for offset, line in prose_lines(text) for m in TOKEN.finditer(line)]
    for start, end, kind, key in reversed(edits):
        text = text[:start] + f"<!-- abr-{kind}:{key} -->" + display(kind, key, parameters, result) + "<!-- /abr-value -->" + text[end:]
    if any("{{param:" in line or "{{derived:" in line for _, line in prose_lines(text)):
        raise ValueError("数值引用语法无效")
    return text


def occurrences(text):
    from .design_structure import prose_lines
    # 数值引用不可跨行；代码示例不授予参数写权限。
    return [(offset + m.start(), offset + m.end(), m[1], m[2], m[3])
            for offset, line in prose_lines(text) for m in BOUND.finditer(line)]


def refresh(text, parameters):
    calculation = audit(parameters)
    for start, end, kind, key, _ in reversed(occurrences(text)):
        text = text[:start] + f"<!-- abr-{kind}:{key} -->" + display(kind, key, parameters, calculation) + "<!-- /abr-value -->" + text[end:]
    return text


def binding_errors(text, parameters):
    from .design_structure import prose_lines
    issues = []
    calculation = audit(parameters)
    for _, _, kind, key, shown in occurrences(text):
        try:
            expected = display(kind, key, parameters, calculation)
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
    for key, row in result.items():
        if row.get("reference_origin") != "writer_v2":
            continue
        matching = [body for body in blocks(text)[1].values() if any(
            kind == "param" and path == key or kind == "derived" and path == "calculated." + key
            for _, _, kind, path, _ in occurrences(body))]
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
        return {key}
    return set(audit(parameters)["derived"].get(key, {}).get("paths", []))


def synchronize(old_text, new_text, old_params, new_params):
    """保留全部已签发引用及人工正文，只刷新引用值；同义声明和锁定仍须一致。"""
    if [(kind, key) for _, _, kind, key, _ in occurrences(old_text)] != [(kind, key) for _, _, kind, key, _ in occurrences(new_text)]:
        raise ValueError("内容补丁不能删除、新增或重新绑定数值引用")
    # 模型只能改正文和获授权的参数，不能直接篡改程序显示值。
    if binding_errors(new_text, old_params):
        raise ValueError("补丁不能直接修改数值引用的显示值")
    changed = refresh(new_text, new_params)
    for key, row in new_params.items():
        if row.get("source_quote") and row.get("basis") != "literature":
            refreshed_quote = refresh(row["source_quote"], new_params)
            if refreshed_quote != row["source_quote"] and refreshed_quote in changed:
                row["source_quote"] = refreshed_quote
    if binding_errors(changed, new_params):
        raise ValueError("更新后数值引用无法完成一致校验")
    return changed
