"""历史实验审核JSON解析与独立参数校验兼容，不再维护内容槽协议。"""
from __future__ import annotations

import json
import math
import re

from .scheme_ledger import digest

VERSION = 1
MAX_PARAMETERS = 80
MAX_CONTROLS = 8
MAX_RESPONSE_CHARS = 160000
MAX_RECORD_CHARS = 6000
FIELD = re.compile(r"(?:cathode|anode|electrolyte|separator|current_collector|packaging|process|condition|fabrication|assembly|testing)\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*\Z")
PROPOSAL_KEYS = {"value", "unit", "basis", "source", "meaning", "role", "rationale", "canonical_path",
                 "quantity_kind", "quantity_role", "denominator", "conditions", "purpose", "scope", "group_id",
                 "value_type", "expected_type", "reason", "acquisition_method", "range_order"}


def strict_json(raw):
    """整条记录必须完整，拒绝重复键和非有限常量，不截取内层对象。"""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("重复字段：" + key)
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("非法数值：" + value)
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)










def canonical_parameter_path(path):
    from .experiment_design import overlap_key
    from .physical_parameters import ALIASES as physical_aliases
    from .parameter_presentation import ALIASES
    path = overlap_key(path)
    path = physical_aliases.get(path, path)
    return ALIASES.get(path, path)


def validate_control(value, doc, index):
    from .experiment_design import effective_data
    from .experiment_values import validate_control_changes
    if not isinstance(value, dict) or set(value) != {"name", "changes"}:
        raise ValueError("对照仅填写name和changes，ID、主组和权限由程序生成")
    if not isinstance(value["name"], str) or not value["name"].strip() or len(value["name"]) > 100:
        raise ValueError("对照名称须为简短文本")
    changes, current = value["changes"], effective_data(doc)["parameters"]
    if not isinstance(changes, dict):
        raise ValueError("对照须明确变化及目的")
    for row in changes.values():
        if not isinstance(row, dict) or set(row) != {"value", "unit", "reason"}:
            raise ValueError("对照变化仅允许已有规范字段的值、单位及原因")
    validate_control_changes(changes, current)
    if doc.get("requirements", {}).get("scheme_required") is False:
        raise ValueError("分析类课题不创建电芯对照组")
    return {"id": "G" + str(index + 1), "name": value["name"], "scope": "control",
            "parameter_refs": [], "changes": changes}


def validate_proposal(value, doc, groups, accepted):
    """模型只提建议，不复制固定值、不授予锁定/审核权限、不回填本征物性。"""
    from .experiment_design import effective_data, overlap_key, parameter_validation_key
    from .experiment_consistency import parameter_issues, stamp, identity_path
    from .physical_parameters import identity, physical_declarations
    from .parameter_presentation import QUANTITY_KINDS
    from .research_experiments import validate_parameter
    from .scheme_ledger import validate_field
    if not isinstance(value, dict) or set(value) - (PROPOSAL_KEYS | {"field"}):
        raise ValueError("参数提交包含非法字段或权限元数据")
    key = value.get("field")
    if not isinstance(key, str) or not FIELD.fullmatch(key):
        raise ValueError("参数字段命名空间非法")
    row = {k: v for k, v in value.items() if k != "field"}
    if not {"value", "unit", "basis", "source", "meaning", "role"} <= row.keys():
        raise ValueError("参数建议缺少值、单位、依据、来源、含义或用途")
    from .experiment_values import validate_value, validate_scalar_fields, make_pending, numeric_field
    unresolved = row["value"] is None and "expected_type" in row
    if unresolved:
        if row["expected_type"] not in ("number", "range", "sequence", "text"):
            raise ValueError("缺项expected_type非法")
        if numeric_field(key, row) and row["expected_type"] == "text":
            raise ValueError("数值字段的缺项不能声明为文字")
        make_pending(key, row, explicit=True)
    else:
        validate_value(key, row)
    if any(not isinstance(row[k], str) for k in ("unit", "basis", "source", "meaning", "role")) or not row["meaning"].strip() or not row["source"].strip():
        raise ValueError("单位和来源等说明须为明确文本")
    if row["basis"] not in ("engineering_proposal", "budget_assumption"):
        raise ValueError("实验新增参数只能是工程建议或预算假设，不能冒充实测物性")
    if row["role"] not in ("recipe", "processing", "assembly", "formation", "testing"):
        raise ValueError("参数用途非法")
    canonical = row.get("canonical_path", overlap_key(key))
    if not isinstance(canonical, str) or not FIELD.fullmatch(canonical):
        raise ValueError("canonical_path必须为规范字段")
    canonical = canonical_parameter_path(canonical)
    natural = canonical_parameter_path(key)
    if "canonical_path" in row and (natural in effective_data(doc)["parameters"] or identity_path(natural)) and canonical != natural:
        raise ValueError("已有物理量或材料身份路径不能改绑到另一字段")
    if canonical.endswith("D_s_m2s") or any(word in (key + " " + row.get("meaning", "")).lower() for word in ("diffusion", "d_s_m2s", "扩散系数")):
        raise ValueError("Stage 5B 不新填本征扩散系数；须通过已有物性证据通道登记")
    current = effective_data(doc)["parameters"]
    declared = current.get(canonical) or {}
    if key in accepted or key in current and current[key].get("value") is not None:
        raise ValueError("已有参数只能使用程序提供的引用，不能重新赋值")
    if declared.get("locked") and declared.get("value") is None:
        raise ValueError("用户锁定的空字段不能由实验建议填充")
    if row.get("scope", "main") not in ("main", "control"):
        raise ValueError("参数范围只允许主组或已登记对照")
    if "quantity_kind" in row and row["quantity_kind"] not in QUANTITY_KINDS:
        raise ValueError("参数量值类型非法")
    if any(k in row and not isinstance(row[k], str) for k in ("rationale", "quantity_role", "denominator", "purpose", "group_id")):
        raise ValueError("参数用途、分母及分组须明确为文本")
    if "conditions" in row and not isinstance(row["conditions"], (dict, list, str)):
        raise ValueError("参数工况须为明确条件记录")
    if row.get("scope") != "control" and row.get("group_id") not in (None, "G1", "main"):
        raise ValueError("非对照参数不能绑定其他实验组")
    if row.get("scope") != "control":
        if declared.get("value") is not None or identity_path(canonical):
            raise ValueError("主方案固定参数及材料身份只能引用，不能另建声明")
        physical = identity(canonical, row)
        if physical and any(identity(path, old) == physical and old.get("value") is not None for path, old in current.items()):
            raise ValueError("已有物理量或别名只能引用当前方案")
        for path, prior in current.items():
            other = canonical_parameter_path(prior.get("canonical_path") or path)
            if other == canonical and (prior.get("value") is not None or prior.get("locked")):
                raise ValueError("已有字段或锁定空值不能通过别名重新赋值")
        for path, prior in accepted.items():
            if "ref" in prior or prior.get("scope") == "control":
                continue
            other = canonical_parameter_path(prior.get("canonical_path") or path)
            if other == canonical and (prior.get("conditions") or {}) == (row.get("conditions") or {}) and prior.get("purpose") == row.get("purpose"):
                raise ValueError("同一工况的参数已经登记，不能用另一条路径重复定义")
    validate_parameter(key, row, doc)
    probe = stamp({"parameters": {key: row}, "groups": groups}, doc)
    errors = parameter_issues(probe, doc)
    if errors:
        raise ValueError("；".join(e["reason"] for e in errors))
    # 已知物理字段严格校验；开放工艺字段保留单位声明，由全文审核确认适用性。
    if unresolved:
        return key, make_pending(key, row, explicit=True)
    validate_scalar_fields(canonical, row)
    if row.get("value_type") in ("range", "sequence"):
        if identity(canonical, row) is not None:
            raise ValueError("主方案单点物理声明不能替换为实验范围或序列")
        return key, row
    combined = dict(current)
    combined.update({p: r for p, r in accepted.items() if "ref" not in r and r.get("scope") != "control"})
    if row.get("scope") != "control":
        combined[key] = row
    _, before = physical_declarations(current)
    _, after = physical_declarations(combined)
    if set(after) - set(before):
        raise ValueError("新增参数与当前物理声明冲突：" + "；".join(sorted(set(after) - set(before))))
    return key, row


