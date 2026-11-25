"""电极预算的等价计算路径；只使用同工况声明，不回填物性或修改方案。"""
import math


def electrode_calculations(comp, get, selected):
    """补齐载量、涂层几何和孔体积的代数路径，并保留独立输入及路径冲突。"""
    def key(name):
        return comp + "." + name
    specs = [
        ("loading_mg_cm2", "calculated_loading_mg_cm2", "mg/cm2", [
            (["L_um", "compaction_g_cm3"], lambda length, density: length * density * .1,
             "干涂层厚度(um) × 压实密度(g/cm3) × 0.1"),
            (["active_loading_mg_cm2", "active_fraction"], lambda active, fraction: active / fraction,
             "活性面载量 / 活性物质量分数")]),
        ("active_loading_mg_cm2", "calculated_active_loading_mg_cm2", "mg/cm2", [
            (["loading_mg_cm2", "active_fraction"], lambda loading, fraction: loading * fraction,
             "总面载量 × 活性物质量分数"),
            (["areal_capacity_mAh_cm2", "capacity_mAh_g"], lambda capacity, specific: capacity / specific * 1000,
             "同工况面容量 / 明确比容量 × 1000")]),
        ("compaction_g_cm3", "calculated_compaction_g_cm3", "g/cm3", [
            (["loading_mg_cm2", "L_um"], lambda loading, length: loading / length * 10,
             "总面载量(mg/cm2) / 干涂层厚度(um) × 10"),
            (["active_loading_mg_cm2", "active_fraction", "L_um"], lambda active, fraction, length: active / fraction / length * 10,
             "活性面载量 / 活性物质量分数 / 干涂层厚度 × 10")]),
        ("L_um", "calculated_thickness_um", "um", [
            (["loading_mg_cm2", "compaction_g_cm3"], lambda loading, density: loading / density * 10,
             "总面载量(mg/cm2) / 压实密度(g/cm3) × 10"),
            (["active_loading_mg_cm2", "active_fraction", "compaction_g_cm3"], lambda active, fraction, density: active / fraction / density * 10,
             "活性面载量 / 活性物质量分数 / 压实密度 × 10")]),
        ("active_fraction", "calculated_active_fraction", "", [
            (["active_loading_mg_cm2", "loading_mg_cm2"], lambda active, loading: active / loading,
             "活性面载量 / 总面载量")]),
        ("pore_volume_uL_cm2", "pore_volume_uL_cm2", "uL/cm2", [
            (["L_um", "porosity"], lambda length, porosity: length * porosity * .1,
             "干涂层厚度(um) × 孔隙率 × 0.1"),
            (["pore_volume", "electrode_area"], lambda volume, area: volume / area,
             "已声明总孔体积 / 极片面积")]),
        ("pore_volume", "calculated_pore_volume_uL", "uL", [
            (["pore_volume_uL_cm2", "electrode_area"], lambda volume, area: volume * area,
             "单位面积孔体积 × 极片面积")]),
        ("areal_capacity_mAh_cm2", "areal_capacity_mAh_cm2", "mAh/cm2", [
            (["active_loading_mg_cm2", "capacity_mAh_g"], lambda active, specific: active * specific / 1000,
             "活性面载量(mg/cm2) × 明确比容量(mAh/g) / 1000")]),
    ]
    rules = [(key(target), key(name), units, [(list(map(key, deps)), fn, formula)
             for deps, fn, formula in alternatives]) for target, name, units, alternatives in specs]
    inputs = {dep for _, _, _, alternatives in rules for deps, _, _ in alternatives for dep in deps}
    inputs.update(target for target, _, _, _ in rules)
    known = {}
    for path in inputs:
        direct = get(path)
        if direct:
            known[path] = dict(direct, quantities={path})

    def candidates(target, units, alternatives):
        found = []
        for deps, fn, formula in alternatives:
            values = [known.get(dep) for dep in deps]
            if any(v is None for v in values):
                continue
            quantities = set().union(*(v["quantities"] for v in values))
            if target in quantities:
                continue  # 反算不能用自身作为输入，也不把往返计算当独立验证。
            try:
                value = fn(*(v["value"] for v in values))
            except (ZeroDivisionError, OverflowError):
                continue
            if not math.isfinite(value) or value < 0 or target.endswith(".active_fraction") and value > 1:
                continue
            found.append({"value": value, "unit": units, "formula": formula, "dependencies": deps,
                          "paths": list(dict.fromkeys(p for v in values for p in v["paths"])),
                          "inputs": {k: {a: b for a, b in v.items() if a != "quantities"} for k, v in zip(deps, values)},
                          "quantities": quantities | {target}})
        return found

    # 每轮只补新增量；所有结果最终与每条可计算路径交叉核对。
    while True:
        added = False
        for target, _, units, alternatives in rules:
            if target in known or target in selected:
                continue
            choices = candidates(target, units, alternatives)
            if choices:
                known[target] = choices[0]
                added = True
        if not added:
            break
    results, conflicts = [], set()
    for target, name, units, alternatives in rules:
        choices = candidates(target, units, alternatives)
        direct = get(target)
        declared_bad = target in selected and direct is None
        available = choices or ([known[target]] if target in known else [])
        all_values = ([direct] if direct else []) + choices
        conflict = bool(all_values and any(not math.isclose(v["value"], all_values[0]["value"], rel_tol=.02, abs_tol=1e-12)
                                           for v in all_values[1:]))
        depends = [dep for dep in alternatives[0][0]]
        missing = [dep for dep in depends if dep not in known]
        applicable = any(dep in known or dep in selected for deps, _, _ in alternatives for dep in deps) or target in selected
        result = {"id": name, "quantity": target, "unit": units, "value": None,
                  "status": "INCOMPLETE" if applicable else "NOT_APPLICABLE", "dependencies": depends,
                  "formula": alternatives[0][2], "missing": missing,
                  "alternatives": [{"formula": formula, "dependencies": deps,
                                    "missing": [dep for dep in deps if dep not in known]}
                                   for deps, _, formula in alternatives]}
        if available:
            chosen = available[0]
            result.update({k: v for k, v in chosen.items() if k not in ("quantities",)})
            result["used_quantities"] = sorted(chosen["quantities"] - {target})
            result["missing"] = []
            result["status"] = "CONSISTENT" if direct and choices else "DECLARED" if direct else "CALCULATED"
            if direct and not choices:
                result.update(formula="当前同口径声明（尚无独立计算路径）", dependencies=[target])
            result["routes"] = [{k: v for k, v in route.items() if k != "quantities"} for route in choices]
            if direct:
                result["declared"] = direct
        if conflict:
            result["status"] = "CONFLICT"
            conflicts.add(target)
            result["paths"] = list(dict.fromkeys(p for v in all_values for p in v["paths"]))
        elif declared_bad:
            result.update(status="INCOMPLETE", value=None, missing=[target + "（声明歧义或单位待确认）"])
        results.append(result)
    # 冲突输入不能通过另一条反算路径流入后续预算。
    changed = True
    while changed:
        changed = False
        for result in results:
            bad = conflicts & set(result.get("used_quantities", []))
            if bad and result["quantity"] not in conflicts:
                result.update(status="INCOMPLETE", value=None, missing=sorted(bad))
                conflicts.add(result["quantity"])
                changed = True
    return results
