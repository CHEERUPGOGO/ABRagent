"""数值一致性只读视图：按对象、工况、用途和计量分母核对，不替用户选择争议值。"""
import copy
import math
import re
import unicodedata

from .scheme_ledger import digest

VERSION = 5


def _safe(value):
    """非法浮点保留诊断身份，但审计本身仍可写为严格JSON。"""
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalid_number": str(value)}
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    return value


UNITS = {
    "electrolyte.volume": "uL", "condition.electrolyte_to_pore_volume_ratio": "",
    "condition.C_rate_reference_current": "mA/g", "condition.current_density": "mA/g",
    "condition.c_rate": "C", "condition.v_min": "V", "condition.v_max": "V",
}
ALIASES = {
    "electrolyte.volume_uL": "electrolyte.volume", "electrolyte.E_over_P": "condition.electrolyte_to_pore_volume_ratio",
    "condition.C_rate": "condition.c_rate", "condition.C_rate_current_density": "condition.C_rate_reference_current",
    "condition.C_rate_reference_current": "condition.C_rate_reference_current",
}
for _comp in ("cathode", "anode"):
    for _key, _unit in (("L_um", "um"), ("porosity", ""), ("electrode_area", "cm2"), ("pore_volume", "uL"),
                        ("pore_volume_uL_cm2", "uL/cm2"),
                        ("loading_mg_cm2", "mg/cm2"), ("active_loading_mg_cm2", "mg/cm2"),
                        ("compaction_g_cm3", "g/cm3"), ("active_fraction", ""), ("conductive_fraction", ""), ("binder_fraction", "")):
        UNITS[f"{_comp}.{_key}"] = _unit
    UNITS[f"{_comp}.capacity_mAh_g"] = "mAh/g"
    UNITS[f"{_comp}.areal_capacity_mAh_cm2"] = "mAh/cm2"
    for _alias, _key in (("thickness_um", "L_um"), ("compaction_density", "compaction_g_cm3"),
                         ("areal_mass_total", "loading_mg_cm2"), ("areal_mass_active", "active_loading_mg_cm2"),
                         ("active_material_mass_fraction", "active_fraction"), ("superP_mass_fraction", "conductive_fraction"),
                         ("PVDF_mass_fraction", "binder_fraction")):
        ALIASES[f"{_comp}.{_alias}"] = f"{_comp}.{_key}"

# 同维度单位仅做明确换算，未列明的单位不猜测。
CONVERSIONS = {
    "um": {"um": 1, "mm": 1000, "cm": 10000, "m": 1e6},
    "cm2": {"cm2": 1, "mm2": .01, "m2": 10000},
    "uL": {"uL": 1, "mL": 1000, "L": 1e6},
    "uL/cm2": {"uL/cm2": 1, "mL/cm2": 1000},
    "mg/cm2": {"mg/cm2": 1, "g/cm2": 1000},
    "g/cm3": {"g/cm3": 1, "kg/m3": .001},
    "mA/g": {"mA/g": 1, "A/g": 1000, "A/kg": 1},
    "mAh/g": {"mAh/g": 1, "Ah/g": 1000},
    "mAh/cm2": {"mAh/cm2": 1, "Ah/cm2": 1000},
    "V": {"V": 1, "mV": .001}, "C": {"C": 1},
    "": {"": 1, "1": 1, "%": .01, "fraction": 1},
    "f.u.": {"f.u.": 1, "fu": 1, "": 1, "1": 1},
}


def canonical(path, row=None):
    from .physical_parameters import ALIASES as physical
    from .parameter_presentation import ALIASES as presentation
    from .parameter_names import ALIASES as names
    key = (row or {}).get("canonical_path") or path
    if row is not None and not row.get("canonical_path") and key in physical:
        from .physical_parameters import identity as physical_identity
        if physical_identity(path, row) is None:
            return key  # 已声明含箔/湿膜的“极片厚度”不能按干涂层厚度别名解释。
    return ALIASES.get(key, physical.get(key, presentation.get(key, names.get(key, key))))


def unit(value):
    return unicodedata.normalize("NFKC", str(value or "")).replace("μ", "u").replace("µ", "u").replace("^", "").replace(" ", "").replace("·g−1", "/g").replace("·g-1", "/g")


def context(row):
    """缺少条件不代表可与有条件的声明合并；文献使用来源作为独立对象。"""
    from .parameter_presentation import semantic_conditions
    conditions = copy.deepcopy(row.get("conditions") or {})
    purpose = row.get("purpose")
    if isinstance(conditions, dict):
        purpose = purpose or conditions.pop("purpose", None) or conditions.pop("用途", None)
    elif isinstance(conditions, list):
        purposes = [c for c in conditions if isinstance(c, dict) and c.get("name") in ("用途", "purpose")]
        if len(purposes) == 1:
            purpose = purpose or purposes[0].get("value")
            conditions.remove(purposes[0])
    if not purpose or purpose in ("主组", "主方案", "主循环", "主测试", "main_cycle", "primary"):
        purpose = "main"
    scope = row.get("group_id") or row.get("scope") or "main"
    if row.get("basis") in ("literature", "literature_measurement"):
        scope = "literature:" + str(row.get("passage_id") or row.get("source") or "unknown")
    return str(scope), str(purpose), semantic_conditions(conditions)


def identity(path, row):
    key = canonical(path, row)
    kind = row.get("quantity_kind") or ("physical" if key in UNITS else "unknown")
    denominator = str(row.get("denominator") or "")
    # 这两个身份由字段定义明确，其他比例无分母不自动推定。
    if key.endswith(".porosity"):
        kind, denominator = "volume_fraction", "电极总体积"
    if key.endswith((".active_fraction", ".conductive_fraction", ".binder_fraction")):
        kind, denominator = "mass_fraction", denominator or "干涂层总质量"
        electrode = "正极" if key.startswith("cathode.") else "负极"
        if denominator == electrode + "干涂层总质量":
            denominator = "干涂层总质量"
    if key == "condition.electrolyte_to_pore_volume_ratio":
        kind = "ratio"
    if kind == "physical" and key in UNITS and UNITS[key]:
        denominator = ""
    role = row.get("quantity_role") or "value"
    scope = context(row)
    if key == "condition.C_rate_reference_current" and scope[1] == "倍率换算基准":
        scope = scope[0], "main", scope[2]
    return key, *scope, kind, denominator, role


def normalized_number(path, row):
    value = row.get("value")
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("不是有限数值")
    target = UNITS.get(canonical(path, row))
    if row.get("quantity_kind") == "formula_coefficient":
        target = "f.u."
    if target is None and row.get("quantity_kind") in ("mass_fraction", "volume_fraction", "site_fraction", "ratio"):
        target = ""
    source = unit(row.get("unit"))
    target = source if target is None else target
    if target not in CONVERSIONS:
        return float(value), target
    factor = CONVERSIONS[target].get(source)
    if factor is None:
        raise ValueError(f"单位{source or '空'}不能换算为{target or '无量纲'}")
    return float(value) * factor, target


def audit(parameters, text="", *, legacy_calculations=False):
    """默认只核对声明；旧程序计算仅供显式历史诊断，不参与当前设计执行链。"""
    params = parameters or {}
    issues, checks, derived, entries = [], [], {}, {}
    def issue(code, message, paths, **extra):
        sources = []
        for path in paths:
            row = params.get(path, {})
            quote = row.get("source_quote")
            positions = [m.start() for m in re.finditer(re.escape(quote), text)] if isinstance(quote, str) and quote else []
            sources.append({"path": path, "value": row.get("value"), "unit": row.get("unit"),
                "source": row.get("source"), "quote": quote, "positions": positions})
        result = dict(code=code, message=message, paths=list(dict.fromkeys(paths)), sources=sources, **extra)
        result["id"] = digest([code, result["paths"], extra.get("context")])[:16]
        issues.append(result)
    from .parameter_names import foil_material_errors, is_foil_thickness
    for error in foil_material_errors(params):
        issue("PARAMETER_MATERIAL_CONFLICT", error,
              [path for path, row in params.items() if isinstance(row, dict) and is_foil_thickness(path, row)])
    for path, row in params.items():
        if not isinstance(row, dict):
            continue
        from .parameter_names import checked_name
        try:
            checked_name(path, row)
        except (ValueError, TypeError) as exc:
            issue("PARAMETER_NAME_CONFLICT", str(exc), [path])
            continue
        if row.get("value") is None:
            continue
        if type(row.get("value")) not in (int, float, bool) and canonical(path, row) not in UNITS:
            continue
        key = identity(path, row)
        try:
            value, units = normalized_number(path, row)
            if value < 0 or key[0].endswith((".porosity", "_fraction")) and value > 1:
                raise ValueError("数值或分数范围非法")
        except ValueError as exc:
            issue("INVALID_QUANTITY", f"{path}：{exc}", [path])
            entries.setdefault(key, []).append({"path": path, "value": None, "unit": None})
            continue
        entries.setdefault(key, []).append({"path": path, "value": value, "unit": units})
    for key, rows in entries.items():
        actual = [(r["value"], r["unit"]) for r in rows]
        if len(rows) > 1 and any(u != actual[0][1] or v is None or actual[0][0] is None
                                or not math.isclose(v, actual[0][0], rel_tol=1e-9, abs_tol=1e-12) for v, u in actual):
            issue("DECLARATION_CONFLICT", "同对象、工况、口径和用途存在不同声明：" + key[0],
                  [r["path"] for r in rows], context=list(key[1:]))

    scopes = sorted({k[1:4] for k in entries if k[1] != "main" or k[2] != "main" or k[3] != "[]"} | {("main", "main", "[]")})
    scopes = [s for s in scopes if not s[0].startswith("literature:")]
    for scope in scopes:
        selected = {}
        for key, rows in entries.items():
            if key[1:4] == scope and key[-1] == "value":
                selected.setdefault(key[0], []).append((key, rows))
        def get(key):
            found = selected.get(key, [])
            if not found and key == "condition.C_rate_reference_current":
                found = [(k, rows) for k, rows in entries.items() if k[0] == key and k[1:4] == (scope[0], "main", "[]") and k[-1] == "value"]
            if len(found) != 1:
                return None
            signature, rows = found[0]
            if any(r["value"] is None or r["unit"] != rows[0]["unit"] or
                   not math.isclose(r["value"], rows[0]["value"], rel_tol=1e-9, abs_tol=1e-12) for r in rows):
                return None
            if key == "condition.electrolyte_to_pore_volume_ratio" and signature[-2] != "正极孔体积":
                return None
            if key.endswith((".active_fraction", ".conductive_fraction", ".binder_fraction")) and signature[-2] != "干涂层总质量":
                return None
            return {"value": rows[0]["value"], "unit": rows[0]["unit"], "paths": [r["path"] for r in rows]}
        if legacy_calculations:
            cache = {}
            def calculate(name, dependencies, formula, description, units, declared=None, tolerance=.02):
                inputs = [cache.get(k) or get(k) for k in dependencies]
                actual = get(declared) if declared else None
                applicable = any(i is not None for i in inputs) or any(k in selected for k in dependencies) or declared in selected
                missing = [k for k, v in zip(dependencies, inputs) if v is None]
                result = {"id": name, "context": list(scope), "formula": description, "dependencies": dependencies,
                          "missing": missing, "status": "INCOMPLETE" if applicable else "NOT_APPLICABLE", "value": None, "unit": units}
                if applicable and not missing:
                    try:
                        value = formula(*(r["value"] for r in inputs))
                        if not math.isfinite(value):
                            raise ValueError("计算结果非有限值")
                        result.update(value=value, status="CALCULATED")
                        cache[name] = {"value": value, "unit": units, "paths": list(dict.fromkeys(p for r in inputs for p in r["paths"]))}
                        result["inputs"] = {k: v for k, v in zip(dependencies, inputs)}
                        result["paths"] = cache[name]["paths"]
                        if declared and declared in selected and actual is None:
                            result.update(status="INCOMPLETE", missing=[declared + "（声明歧义或分母待确认）"])
                        elif actual:
                            result["declared"] = actual
                            result["status"] = "CONSISTENT" if math.isclose(actual["value"], value, rel_tol=tolerance, abs_tol=1e-12) else "CONFLICT"
                            if result["status"] == "CONFLICT":
                                issue("CALCULATION_CONFLICT", f"{declared} 声明{actual['value']:.6g}，按当前输入计算为{value:.6g} {units}",
                                      cache[name]["paths"] + actual["paths"], context=list(scope), expected=value, formula=description)
                    except (ZeroDivisionError, ValueError, OverflowError):
                        result.update(status="INCOMPLETE", missing=["依赖值不能用于该公式（如分母为零）"])
                checks.append(result)
                if scope == ("main", "main", "[]"):
                    derived[name] = copy.deepcopy(result)
            for comp in ("cathode", "anode"):
                from .derived_quantities import electrode_calculations
                for result in electrode_calculations(comp, get, selected):
                    result["context"] = list(scope)
                    if result["status"] == "CONFLICT":
                        issue("CALCULATION_CONFLICT", f"{result['quantity']} 的声明或多条计算路径不一致",
                              result["paths"], context=list(scope), expected=result["value"], formula=result["formula"])
                    if result["status"] in ("CALCULATED", "CONSISTENT", "DECLARED"):
                        cache[result["id"]] = {"value": result["value"], "unit": result["unit"], "paths": result["paths"]}
                    checks.append(result)
                    if scope == ("main", "main", "[]"):
                        derived[result["id"]] = copy.deepcopy(result)
                fractions = [get(f"{comp}.{k}_fraction") for k in ("active", "conductive", "binder")]
                if any(fractions):
                    missing = [k for k, r in zip(("active", "conductive", "binder"), fractions) if r is None]
                    total = sum(r["value"] for r in fractions) if not missing else None
                    state = "INCOMPLETE" if missing else "CONSISTENT" if math.isclose(total, 1, abs_tol=.001) else "CONFLICT"
                    checks.append({"id": comp + ".mass_fraction_sum", "context": list(scope), "status": state, "missing": missing, "value": total})
                    if state == "CONFLICT":
                        issue("FRACTION_SUM", f"{comp} 活性物、导电剂与粘结剂质量分数和为{total:g}，不等于1", [p for r in fractions for p in r["paths"]], context=list(scope))
            calculate("condition.calculated_electrolyte_to_cathode_pore_ratio", ["electrolyte.volume", "cathode.calculated_pore_volume_uL"],
                      lambda volume, pores: volume / pores, "注液量 / 正极孔体积（不含负极与隔膜）", "", "condition.electrolyte_to_pore_volume_ratio")
            calculate("condition.calculated_current_density", ["condition.c_rate", "condition.C_rate_reference_current"],
                      lambda rate, ref: rate * ref, "倍率(C) × 1 C基准电流(mA/g)", "mA/g", "condition.current_density")
        lower, upper = get("condition.v_min"), get("condition.v_max")
        if lower and upper and lower["value"] >= upper["value"]:
            issue("INVALID_BOUNDS", "电压下限必须小于上限", lower["paths"] + upper["paths"], context=list(scope))
    # 任意数值字段可显式声明允许上下限、实验最大值和实际目标；不从命名或数值猜角色。
    families = {}
    for key, rows in entries.items():
        if key[-1] in ("lower_bound", "upper_bound", "experiment_max", "target"):
            families.setdefault(key[:-1], {})[key[-1]] = rows
    for key, roles in families.items():
        for smaller, larger in (("lower_bound", "upper_bound"), ("experiment_max", "upper_bound"),
                                ("target", "upper_bound"), ("lower_bound", "target")):
            a, b = roles.get(smaller, []), roles.get(larger, [])
            if len(a) == len(b) == 1 and a[0]["value"] is not None and b[0]["value"] is not None and a[0]["unit"] == b[0]["unit"]:
                conflict = a[0]["value"] > b[0]["value"]
                checks.append({"id": key[0] + ":" + smaller + "<=" + larger, "context": list(key[1:4]),
                               "status": "CONFLICT" if conflict else "CONSISTENT", "missing": []})
                if conflict:
                    issue("INVALID_BOUNDS", f"{key[0]} 的{smaller}超过{larger}", [a[0]["path"], b[0]["path"]], context=list(key[1:4]))
    if legacy_calculations:
        for path, row in params.items():
            refs = row.get("ratio_inputs") if isinstance(row, dict) else None
            if refs is None:
                continue
            result = {"id": "calculated." + path, "context": list(context(row)), "status": "INCOMPLETE",
                      "value": None, "unit": "", "formula": "明确分子 / 明确分母；不推导容量损失",
                      "dependencies": refs if isinstance(refs, list) else [], "missing": []}
            if (not isinstance(refs, list) or len(refs) != 2 or any(not isinstance(k, str) or k not in params for k in refs)
                    or row.get("quantity_kind") not in ("site_fraction", "ratio") or not row.get("denominator")):
                result["missing"] = ["比例需要两个明确字段引用、比例类型和分母说明"]
            else:
                try:
                    a, b = (params[k] for k in refs)
                    if path in refs or a.get("ratio_inputs") or b.get("ratio_inputs"):
                        raise ValueError("比例依赖必须是独立原始声明，不能循环或引用另一未独立确认的比例")
                    av, au = normalized_number(refs[0], a)
                    bv, bu = normalized_number(refs[1], b)
                    if au != bu or a.get("quantity_kind") != b.get("quantity_kind") or context(a) != context(b) or context(a) != context(row):
                        raise ValueError("分子分母的维度、对象或工况不一致")
                    if not bv:
                        raise ValueError("分母为零")
                    if any(set(i["paths"]) & set(refs) for i in issues):
                        raise ValueError("比例依赖存在未解决冲突")
                    value = av / bv
                    result.update(value=value, status="CALCULATED", paths=refs,
                                  inputs={refs[0]: {"value": av, "unit": au}, refs[1]: {"value": bv, "unit": bu}})
                    if row.get("value") is not None:
                        declared, _ = normalized_number(path, row)
                        result["status"] = "CONSISTENT" if math.isclose(declared, value, rel_tol=.001, abs_tol=1e-12) else "CONFLICT"
                        if result["status"] == "CONFLICT":
                            issue("RATIO_CONFLICT", f"{path} 按已声明分母计算为{value:g}，与声明{declared:g}不符", refs + [path], expected=value)
                except (ValueError, TypeError, ZeroDivisionError) as exc:
                    result["missing"] = [str(exc)]
            checks.append(result)
            derived[result["id"]] = result
    result = {"version": VERSION, "calculation_policy": "legacy_program" if legacy_calculations else "model",
              "checks": checks, "derived": derived, "issues": issues,
              "status": "CONFLICT" if issues else "INCOMPLETE" if any(c["status"] == "INCOMPLETE" for c in checks) else "CONSISTENT"}
    result["unbound_mentions"] = unbound_mentions(text)
    if legacy_calculations and result["unbound_mentions"] and result["status"] == "CONSISTENT":
        result["status"] = "INCOMPLETE"
    result["fingerprint"] = digest(_safe({"version": VERSION, "parameters": params, "text": text, "calculation_policy": result["calculation_policy"]}))
    return _safe(result)


def unbound_mentions(text):
    """仅列出未绑定量值，不凭数字附近的关键词猜测主方案或文献归属。"""
    from .design_structure import prose_lines
    pattern = re.compile(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?\s*(?:%|mol%|f\.u\.|μm|µm|um|μL|uL|mL|mA/g|mg/cm[²2]|g/cm[³3]|°C|℃|Wh/kg|V\b|C\b|圈)")
    result = []
    for offset, line in prose_lines(text):
        visible = re.sub(r"<!-- abr-(?:param|derived):.*?<!-- /abr-value -->|\[[^\]]+\](?:\([^)]*\))?", lambda m: " " * len(m[0]), line)
        for match in pattern.finditer(visible):
            result.append({"start": offset + match.start(), "end": offset + match.end(), "quote": match[0],
                           "context": line[max(0, match.start() - 30):match.end() + 45], "scope": "unclassified"})
    return result


def messages(result):
    return ["数值一致性：" + i["message"] for i in result["issues"]]


def document_audit(doc):
    return audit((doc.get("ledger_view") or {}).get("parameters", doc.get("parameters") or doc.get("scheme_parameters") or {}),
                 doc.get("final_answer") or doc.get("draft") or "")


def repair_issues(result, text):
    """只按明确原句授予跨段事务；缺来源的诊断仍保留，但不凭字段名猜写入范围。"""
    found = []
    for row in result["issues"]:
        quotes = list(dict.fromkeys(s["quote"] for s in row["sources"] if s.get("quote") and len(s["positions"]) == 1))
        if not quotes or any(not s.get("quote") or len(s["positions"]) != 1 for s in row["sources"]):
            continue
        found.append({"type": "numeric_consistency", "severity": "high", "numeric_id": row["id"],
            "quote": quotes[0], "quotes": quotes, "reason": "数值一致性：" + row["message"],
            "suggestion": "核对全部来源与工况；仅修改有依据且未锁定的字段及关联表述。禁止任选冲突值或编造缺项。", "parameter_paths": row["paths"]})
    return found


def render_audit(result):
    """已计算、未完成和冲突分开展示，不把无错误误称为全部验证通过。"""
    active = [c for c in result["checks"] if c["status"] != "NOT_APPLICABLE"]
    model = result.get("calculation_policy") == "model"
    lines = ["### 数值一致性核对", ("程序仅检查参数声明、单位、同口径冲突及上下限；预算与换算由模型完成，方法、代入过程和结果由Reviewer复核，不表示程序已计算验证。"
             if model else "历史程序计算诊断，仅供追溯；不作为当前设计的自动计算链或科学审核结论。")]
    if result.get("unbound_mentions"):
        lines.append(f"正文另有{len(result['unbound_mentions'])}处量值供Reviewer核对计算、对象、工况和用途；模型计算过程可直接写明数值，未绑定本身不构成错误。完整位置见 numeric_consistency.json。")
    lines += ["- " + i["message"] for i in result["issues"]]
    labels = {"condition.calculated_electrolyte_to_cathode_pore_ratio": "注液量与正极孔体积比（E/P）",
              "condition.calculated_current_density": "倍率对应电流", "condition.C_rate_reference_current": "1 C基准电流",
              "condition.c_rate": "测试倍率", "electrolyte.volume": "注液量"}
    for comp, name in (("cathode", "正极"), ("anode", "负极")):
        for key, label in (("pore_volume_uL_cm2", "单位面积孔体积"), ("calculated_pore_volume_uL", "总孔体积"),
                           ("calculated_loading_mg_cm2", "总面载量"), ("calculated_active_loading_mg_cm2", "活性物面载量"),
                           ("calculated_compaction_g_cm3", "压实密度"), ("calculated_thickness_um", "干涂层厚度"),
                           ("calculated_active_fraction", "活性物质量分数"),
                           ("mass_fraction_sum", "干涂层质量分数和"), ("electrode_area", "极片面积"),
                           ("L_um", "干涂层厚度"), ("porosity", "孔隙率"), ("compaction_g_cm3", "压实密度"),
                           ("active_fraction", "活性物质量分数")):
            labels[comp + "." + key] = name + label
    states = {"CALCULATED": "已计算，尚无同口径声明可比较", "CONSISTENT": "数值核对一致",
              "DECLARED": "沿用明确声明，尚无独立计算路径可核对", "CONFLICT": "计算路径或声明不一致"}
    for c in active:
        name = labels.get(c["id"], c["id"])
        scope = c.get("context") or []
        if scope and scope != ["main", "main", "[]"]:
            name += "（" + "；".join(v for v in scope if v not in ("main", "[]")) + "）"
        if c["status"] == "INCOMPLETE":
            lines.append("- 未完成：" + name + "；缺失或歧义输入：" + "、".join(labels.get(k, k) for k in c["missing"]))
        elif "formula" in c:
            lines.append(f"- {name}：{c['value']:.6g} {c.get('unit', '')}；计算依据：{c['formula']}；{states.get(c['status'], c['status'])}。")
        else:
            lines.append("- " + name + "：" + states.get(c["status"], c["status"]) + "。")
    if not active and not result["issues"]:
        lines.append("当前未发现声明冲突；模型计算和科学结论仍须独立审核。" if model else "没有满足适用条件的数值检查，不能据此认定全文一致。")
    return "\n\n".join(lines)
