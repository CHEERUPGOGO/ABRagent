"""PINN 输入诊断和自动修复权限；契约身份失败不能伪装成范围修复。"""
from auto_battery_research.simulation import build_cell_spec, preflight_pinn_inputs
from auto_battery_research.simulation.geometry_contract import geometry_hash, validate_geometry_contract, strict_number
from auto_battery_research.tools.geometry_revision import collect_geometry_violations, resolve_input_ranges

AUTO_FIELDS = {f"{c}.{f}" for c in ("cathode", "anode") for f in ("L_um", "porosity", "R_s_um")}


def diagnose(doc, c_rate=.5):
    scheme = doc.get("scheme") or {}
    geo = scheme.get("geometry") or {}
    ranges = resolve_input_ranges(scheme.get("cathode"), scheme.get("anode"), scheme=scheme)
    errors = []
    if geo.get("schema_version") != "1.1" or not isinstance(geo.get("provenance"), dict):
        errors.append("缺少有效结构化几何声明")
    if geo.get("contract_version") == "2.0":
        errors.extend(validate_geometry_contract(geo, scheme, doc.get("evidence") or [], check_ranges=False))
        try:
            if scheme.get("geometry_hash") != geometry_hash(geo):
                errors.append("设计几何哈希失配")
        except (ValueError, TypeError):
            errors.append("设计几何含非有限数值")
    else:
        for comp in ("cathode", "anode"):
            for field in ("L_um", "porosity", "R_s_um", "D_s_m2s"):
                if not strict_number((geo.get(comp) or {}).get(field)):
                    errors.append(f"{comp}.{field} 缺失或不是有限数值")
    for key, value in (geo.get("condition") or {}).items():
        if key in ("v_min", "v_max", "c_rate") and value is not None and not strict_number(value):
            errors.append(f"condition.{key} 不是有限数值")
    if errors:
        return {"status": "CONTRACT_INVALID", "errors": errors, "error": "; ".join(errors), "allowed_fields": []}
    violations = collect_geometry_violations(geo, ranges)
    if violations:
        paths = {v.split("=", 1)[0] for v in violations}
        result = {"status": "OUT_OF_RANGE", "error": "; ".join(violations), "violations": violations,
                  "allowed_fields": sorted(paths & AUTO_FIELDS)}
        if not paths <= AUTO_FIELDS:
            result.update(status="UNREPAIRABLE_INPUT", error="输入含自动修复权限以外的阻断：" + result["error"], allowed_fields=[])
    else:
        try:
            result = preflight_pinn_inputs(build_cell_spec(scheme, {}, c_rate=c_rate), scheme=scheme, c_rate=c_rate)
        except Exception as exc:
            result = {"status": "ERROR", "error": str(exc)}
        result = dict(result)
        result["allowed_fields"] = sorted(AUTO_FIELDS) if result["status"] == "OUT_OF_ENVELOPE" else []
        # N/P 可导致有效厚度越界，不能靠改未越界的单个声明值绕开系统约束。
        if result["status"] == "OUT_OF_RANGE":
            result.update(status="UNREPAIRABLE_INPUT", error="有效工况或系统约束越界：" + result.get("error", ""))
    locks = (doc.get("ledger_view") or {}).get("parameters") or {}
    locked = {k for k, row in locks.items() if isinstance(row, dict) and row.get("locked")}
    if result["status"] == "OUT_OF_RANGE" and locked & set(result["allowed_fields"]):
        result.update(status="UNREPAIRABLE_INPUT", error="越界字段受用户锁定：" + ", ".join(sorted(locked & set(result["allowed_fields"]))), allowed_fields=[])
    else:
        result["allowed_fields"] = sorted(set(result["allowed_fields"]) - locked)
    if result["status"] == "OUT_OF_ENVELOPE" and not result["allowed_fields"]:
        result.update(status="UNREPAIRABLE_INPUT", error="训练包络越界且可修订几何全部锁定")
    result["ranges_SI"] = ranges
    return result


def validate_candidate(doc, scheme, c_rate=.5):
    candidate = dict(doc, scheme=scheme)
    diagnostic = diagnose(candidate, c_rate)
    if diagnostic["status"] != "VALID":
        raise ValueError(diagnostic.get("error", "候选输入未通过校验"))
    return diagnostic
