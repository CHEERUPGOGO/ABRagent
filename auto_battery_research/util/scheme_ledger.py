"""课题方案唯一数据源：版本提交、用户锁定、单位与确定性派生计算。"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from .material_ids import canonical_field, canonical_parameters, canonical_scheme, assemble_components, identity_path, document_id_errors, VERSION as MATERIAL_ID_VERSION

FILENAME = "scheme_ledger.json"
DOMAINS = {"requirements", "cathode", "anode", "electrolyte", "separator",
           "current_collector", "packaging", "process", "condition"}
UNITS = {"L_um": "um", "R_s_um": "um", "D_s_m2s": "m2/s",
         "loading_mg_cm2": "mg/cm2", "compaction_g_cm3": "g/cm3",
         "density_g_cm3": "g/cm3", "capacity_mAh_g": "mAh/g",
         "areal_capacity_mAh_cm2": "mAh/cm2", "c_rate": "C",
         "temperature_K": "K", "v_min": "V", "v_max": "V",
         "target_energy_wh_kg": "Wh/kg", "volume_uL_cm2": "uL/cm2",
         "C_rate_reference_current": "mA/g"}


def canonical_unit(key):
    leaf = key.split(".")[-1]
    if "fraction" in leaf or leaf == "porosity":
        return ""
    if leaf in UNITS:
        return UNITS[leaf]
    for suffix, unit in (("_mg_cm2", "mg/cm2"), ("_g_cm3", "g/cm3"),
                         ("_mAh_g", "mAh/g"), ("_temperature_K", "K"), ("_voltage_V", "V")):
        if leaf.endswith(suffix):
            return unit
    return None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode("utf-8")).hexdigest()


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ledger_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def task_lock(task_dir):
    """跨进程课题锁；超时明确拒绝，绝不绕过仍存活的写入者。"""
    task_dir = Path(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / ".scheme_ledger.lock"
    deadline = time.monotonic() + 10
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ValueError("方案表写入锁超时，请确认其他写入进程是否仍在运行")
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)


def load(task_dir):
    path = Path(task_dir) / FILENAME
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def flatten_scheme(scheme):
    fields = {}
    for key in ("cathode", "anode", "electrolyte", "separator", "current_collector"):
        if isinstance(scheme.get(key), str):
            identity = (scheme.get("component_identities") or {}).get(key)
            if identity:
                # 完整名义式/配方来源仍引用原字段，不制造 .material 再次覆盖身份。
                fields.update(identity.get("source_values") or {})
            else:
                fields[f"{key}.material"] = scheme[key]
    if scheme.get("additives") is not None:
        fields["electrolyte.additives"] = scheme["additives"]
    if scheme.get("target_energy_wh_kg") is not None:
        fields["requirements.target_energy_wh_kg"] = scheme["target_energy_wh_kg"]
    geo = scheme.get("geometry") or {}
    for comp in ("cathode", "anode"):
        for key in ("L_um", "porosity", "R_s_um", "D_s_m2s", "loading_mg_cm2", "compaction_g_cm3"):
            if key in (geo.get(comp) or {}):
                fields[f"{comp}.{key}"] = geo[comp][key]
    for key, value in (geo.get("condition") or {}).items():
        fields[f"condition.{key}"] = value
    for parent in (scheme.get("rule_parents") or {}).values():
        fields.update(parent.get("source_values") or {})
    if "n_p_ratio" in geo:
        fields["anode.n_p_ratio"] = geo["n_p_ratio"]
    return fields


def project_scheme_parameters(scheme, parameters, reason="方案结构化投影"):
    """预检查与提交共用投影；几何单位来自契约，已有声明及其来源不丢失。"""
    from .physical_parameters import identity, normalized_unit, UNITS as PHYSICAL_UNITS
    from .research_contract import field_equal
    result = copy.deepcopy(parameters)
    for key, value in flatten_scheme(scheme).items():
        previous = parameters.get(key)
        expected = canonical_unit(key)
        if previous and expected is not None and previous.get("unit", expected) != expected:
            raise ValueError(f"{key} 必须采用单位 {expected}")
        if previous and (previous.get("value") == value or field_equal(key, previous.get("value"), value)):
            row = copy.deepcopy(previous)
            row.setdefault("unit", expected or "")
        else:
            row = None
            # 仅主工况的已知物理量别名可提供来源；对照/湿膜等不能晋升主声明。
            for path, original in parameters.items():
                group = identity(path, original)
                if (group != (key, "main", "main", "[]") or not (
                        original.get("value") == value or field_equal(key, original.get("value"), value))):
                    continue
                unit = normalized_unit(original.get("unit", PHYSICAL_UNITS[key]))
                if unit != PHYSICAL_UNITS[key]:
                    raise ValueError(f"物理量单位待确认：{path}")
                row = copy.deepcopy(original)
                row["unit"] = expected or ""
                row["projection_source_path"] = path
                break
            if row is None:
                row = {"value": value, "unit": expected or "", "basis": "engineering_proposal", "source": reason}
                source = (scheme.get("geometry", {}).get("parameter_sources") or {}).get(key, {})
                if source.get("origin") == "model_baseline":
                    row.update(basis="model_reference", source="PINN训练体系模型基准：" + str(source.get("system_id", "")),
                               meaning="模型参考参数，不是本批材料的实测值")
        row["value"] = value
        result[key] = row
    return result


def persist_requirement_bindings(task_dir, requirements, base_revision, review_doc=None):
    """独立提交经原文核验的用户条件，不依赖候选方案是否通过。"""
    from .research_contract import bindings
    from .requirement_structure import item_bindings, normalize_binding
    data = load(task_dir)
    if not data or data["revision"] != base_revision:
        raise ValueError("用户条件登记基于过期方案表")
    if not requirements:
        return data
    if requirements.get("source") != data.get("goal"):
        raise ValueError("用户条件来源与当前课题原文不一致")
    validated = copy.deepcopy(requirements)
    for item in validated.get("items", []):
        item["bindings"] = [normalize_binding(b, item, data["goal"]) for b in item_bindings(item)]
        item["binding"] = None
        for binding in item["bindings"]:
            # 完整文字配方已自带单位时，模型省略独立unit不代表要求改掉既有单位。
            # 仅沿用同字段、同值、原句内明确出现的锁定单位；数值字段仍严格验单位。
            previous = data["parameters"].get(binding["path"], {})
            value, unit = binding.get("value"), previous.get("unit")
            from .research_contract import field_equal
            from .requirement_sources import _unit
            if (previous.get("locked") and isinstance(value, str) and canonical_unit(binding["path"]) is None
                    and binding.get("unit") in (None, "") and isinstance(unit, str) and unit
                    and field_equal(binding["path"], previous.get("value"), value)
                    and re.search(r"\d\s*" + r"\s*".join(re.escape(c) for c in _unit(unit)) + r"(?![a-z0-9/^_])",
                                  unicodedata.normalize("NFKC", value).casefold())):
                binding["unit"] = unit
    fixed = bindings(validated)
    if review_doc:
        from .requirement_review import blocking_fields
        blocked = set(blocking_fields(dict(review_doc, requirements=validated))) & fixed.keys()
        if blocked:
            raise ValueError("用户固定字段误读证据已核验，禁止登记：" + ", ".join(sorted(blocked)))
    saved = update(task_dir, fixed, base_revision, reason="独立登记经用户原文核验的固定条件")
    # 后续Writer、方案提交和报告复核共用同一绑定；提交失败不改调用方的契约。
    requirements["items"] = validated["items"]
    return saved


def ensure(task_dir, goal=""):
    with task_lock(task_dir):
        existing = load(task_dir)
        if existing is not None:
            return existing
        data = {"schema_version": 1, "revision": 0, "goal": goal, "parameters": {},
                "candidates": [], "history": [], "derived": {}, "issues": []}
        legacy = Path(task_dir) / "design_scheme.json"
        if legacy.exists():
            doc = json.loads(legacy.read_text(encoding="utf-8"))
            for key, value in flatten_scheme(doc.get("scheme") or {}).items():
                data["parameters"][key] = {"value": value, "unit": UNITS.get(key.split(".")[-1], ""),
                    "basis": "legacy_declaration", "source": "design_scheme.json", "locked": False,
                    "meaning": "旧结构化声明，未经新增实测验证", "conditions": {}}
        match = re.search(r"(\d+(?:\.\d+)?)\s*Wh\s*/\s*kg", goal, re.I)
        if match:
            data["parameters"]["requirements.target_energy_wh_kg"] = {
                "value": float(match[1]), "unit": "Wh/kg", "basis": "user_requirement",
                "source": "课题目标", "locked": True, "meaning": "课题比能量目标", "conditions": {}}
        if goal:
            data["parameters"]["requirements.original_request"] = {
                "value": goal, "unit": "", "basis": "user_requirement", "source": "课题目标",
                "locked": True, "meaning": "用户原始要求，结构化提取不得削弱", "conditions": {}}
        # 仅识别明确的组件声明；复杂自然语言约束保留原文，不猜测选型。
        for label, comp in (("正极", "cathode"), ("负极", "anode"), ("电解液", "electrolyte")):
            declared = re.search(label + r"\s*(?:材料)?\s*(?:为|采用|指定为|[:：=])\s*([A-Za-z][A-Za-z0-9_-]*)", goal)
            if declared:
                data["parameters"][f"{comp}.material"] = {
                    "value": declared[1], "unit": "", "basis": "user_requirement", "source": "课题明确声明",
                    "locked": True, "meaning": f"用户指定{label}材料", "conditions": {}}
        data["parameters"] = canonical_parameters(data["parameters"])
        data["material_id_version"] = MATERIAL_ID_VERSION
        data["derived"], data["issues"] = calculate(data)
        _save(task_dir, data)
        return data


def calculate(data):
    """只计算依赖完整的量；不回填密度、容量、电压等默认表。"""
    params = data["parameters"]
    def value(key):
        v = (params.get(key) or {}).get("value")
        return v if isinstance(v, (float, int)) and not isinstance(v, bool) else None
    derived, issues = {}, []
    def number(key):
        direct = value(key)
        return direct if direct is not None else (derived.get(key) or {}).get("value")
    def put(key, inputs, formula, unit):
        values = [number(p) for p in inputs]
        derived[key] = {"value": formula(*values) if all(v is not None for v in values) else None,
                        "unit": unit, "dependencies": inputs,
                        "missing": [p for p, v in zip(inputs, values) if v is None]}
    for comp in ("cathode", "anode", "separator"):
        put(f"{comp}.pore_volume_uL_cm2", [f"{comp}.L_um", f"{comp}.porosity"],
            lambda length, porosity: length * porosity * 0.1, "uL/cm2")
    if (params.get("anode.porosity_basis") or {}).get("value") == "before_loading":
        put("anode.remaining_pore_volume_uL_cm2", ["anode.pore_volume_uL_cm2", "anode.li_filling_fraction"],
            lambda volume, fraction: volume * (1-fraction), "uL/cm2")
        put("electrolyte.available_pore_volume_uL_cm2", ["cathode.pore_volume_uL_cm2",
            "anode.remaining_pore_volume_uL_cm2", "separator.pore_volume_uL_cm2"], lambda a,b,c: a+b+c, "uL/cm2")
    if "cathode.electrode_area" in params and params["cathode.electrode_area"].get("unit") in ("cm2", "cm²"):
        put("cathode.calculated_pore_volume_uL", ["cathode.pore_volume_uL_cm2", "cathode.electrode_area"],
            lambda volume, area: volume * area, "uL")
        pore = number("cathode.calculated_pore_volume_uL")
        if pore and (params.get("electrolyte.volume") or {}).get("unit") in ("uL", "μL", "µL"):
            put("condition.calculated_electrolyte_to_cathode_pore_ratio", ["electrolyte.volume", "cathode.calculated_pore_volume_uL"],
                lambda volume, pores: volume / pores, "")
    # 对照原始声明允许展示取整，超差保留依赖项供人工复核。
    for calculated, declared, unit in (("cathode.calculated_pore_volume_uL", "cathode.pore_volume", "uL"),
                                       ("condition.calculated_electrolyte_to_cathode_pore_ratio", "condition.electrolyte_to_pore_volume_ratio", "")):
        row = params.get(declared) or {}
        if declared.startswith("condition.") and row.get("denominator") != "正极孔体积":
            continue
        expected = number(calculated)
        if expected is not None and value(declared) is not None and row.get("unit") == unit and not math.isclose(value(declared), expected, rel_tol=.02):
            issues.append(f"{declared} 与当前几何预算不一致：声明 {value(declared):g}，计算 {expected:g} {unit}")
    put("cathode.areal_capacity_mAh_cm2",
        ["cathode.loading_mg_cm2", "cathode.active_fraction", "cathode.capacity_mAh_g"],
        lambda loading, fraction, capacity: loading * fraction * capacity / 1000, "mAh/cm2")
    # 多孔锂宿主：只有明确载锂前口径才允许按填充率计算锂库存。
    if (params.get("anode.porosity_basis") or {}).get("value") == "before_loading":
        put("anode.li_capacity_mAh_cm2", ["anode.L_um", "anode.porosity", "anode.li_filling_fraction",
            "anode.li_density_g_cm3", "anode.li_capacity_mAh_g"],
            lambda length, porosity, filling, density, capacity: length * 1e-4 * porosity * filling * density * capacity,
            "mAh/cm2")
        n = derived["anode.li_capacity_mAh_cm2"]["value"]
        p = derived["cathode.areal_capacity_mAh_cm2"]["value"]
        derived["anode.n_p_ratio"] = {"value": n / p if n is not None and p else None,
            "unit": "", "dependencies": ["anode.li_capacity_mAh_cm2", "cathode.areal_capacity_mAh_cm2"]}
        stated = value("anode.n_p_ratio")
        if stated is not None and n is not None and p and not math.isclose(stated, n/p, rel_tol=.02):
            issues.append("声明 N/P 与锂库存及正极面容量计算不一致")
    for comp in ("cathode", "anode"):
        length, density, loading = (value(f"{comp}.{k}") for k in ("L_um", "compaction_g_cm3", "loading_mg_cm2"))
        if None not in (length, density, loading) and not math.isclose(length*density*.1, loading, rel_tol=.02):
            issues.append(f"{comp} 厚度、压实密度与面载不一致")
        fractions = [value(f"{comp}.{key}_fraction") for key in ("active", "conductive", "binder")]
        if all(v is not None for v in fractions) and not math.isclose(sum(fractions), 1, abs_tol=.001):
            issues.append(f"{comp} 活性物、导电剂、粘结剂质量分数之和不为一")
    from .physical_parameters import physical_declarations
    _, physical_issues = physical_declarations(params)
    issues.extend(physical_issues)
    from .parameter_presentation import presentation_conflicts
    issues.extend(presentation_conflicts(data))
    put("packaging.stack_mass_mg_cm2", ["cathode.loading_mg_cm2", "anode.composite_mass_mg_cm2",
        "current_collector.cathode_mass_mg_cm2", "current_collector.anode_mass_mg_cm2",
        "separator.mass_mg_cm2", "electrolyte.mass_mg_cm2"], lambda *v: sum(v), "mg/cm2")
    put("packaging.cell_mass_mg_cm2", ["packaging.stack_mass_mg_cm2", "packaging.mass_mg_cm2"], lambda a,b: a+b, "mg/cm2")
    for label in ("stack", "cell"):
        put(f"packaging.{label}_energy_wh_kg", ["cathode.areal_capacity_mAh_cm2",
            "condition.average_discharge_voltage_V", f"packaging.{label}_mass_mg_cm2"],
            lambda capacity, voltage, mass: capacity*voltage/mass*1000 if mass else None, "Wh/kg")
    from .numeric_consistency import audit as numeric_audit, messages
    numerical = numeric_audit(params)
    for key, computed in numerical["derived"].items():
        if key in derived or computed["status"] != "NOT_APPLICABLE":
            derived[key] = {"value": computed["value"] if computed["status"] != "INCOMPLETE" else None,
                            "unit": computed["unit"], "dependencies": computed.get("paths", computed["dependencies"]),
                            "missing": computed["missing"], "formula": computed["formula"], "status": computed["status"]}
    issues.extend(messages(numerical))
    return derived, list(dict.fromkeys(issues))


def validate_field(key, field):
    if not isinstance(key, str) or not isinstance(field, dict):
        raise ValueError("字段更新须为路径到参数对象的映射")
    if key.split(".")[0] not in DOMAINS or len(key.split(".")) < 2:
        raise ValueError(f"未知方案字段: {key}")
    v = field.get("value")
    leaf = key.split(".")[-1]
    expected = canonical_unit(key)
    if expected is not None and v is not None and (not isinstance(v, (float, int)) or isinstance(v, bool)):
        raise ValueError(f"{key} 必须为数值或 null")
    if expected is not None and field.get("unit", expected) != expected:
        raise ValueError(f"{key} 必须采用单位 {expected}")
    if isinstance(v, bool) or isinstance(v, float) and not math.isfinite(v):
        raise ValueError(f"{key} 数值非法")
    if isinstance(v, (float, int)):
        if v < 0 or leaf in ("L_um", "R_s_um", "D_s_m2s", "density_g_cm3") and v <= 0:
            raise ValueError(f"{key} 数值越界")
        if ("fraction" in leaf or leaf == "porosity") and not 0 <= v <= 1:
            raise ValueError(f"{key} 必须为 0–1 小数")
    if v is not None and not field.get("basis"):
        raise ValueError(f"{key} 缺少依据类型 basis")
    if v is not None and not field.get("source"):
        raise ValueError(f"{key} 缺少来源 source")


def _save(task_dir, data):
    # 快照先写，主文件原子提交；中文表是可重建视图。
    raw = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    atomic_write(Path(task_dir) / "scheme_history" / f"revision_{data['revision']}.json", raw)
    atomic_write(Path(task_dir) / FILENAME, raw)
    atomic_write(Path(task_dir) / "scheme_table.md", render(data))


def update(task_dir, changes, base_revision, actor="agent", reason="", unlock=(), candidates=None):
    if actor not in ("agent", "user"):
        raise ValueError("未知维护主体")
    if not isinstance(changes, dict):
        raise ValueError("changes 必须为字段映射")
    with task_lock(task_dir):
        data = load(task_dir)
        if not data or data["revision"] != base_revision:
            raise ValueError("方案表版本过期，请重新读取")
        data = copy.deepcopy(data)
        old = copy.deepcopy(data["parameters"])
        data["parameters"] = canonical_parameters(data["parameters"])
        for key in unlock:
            if actor != "user" or key not in data["parameters"]:
                raise ValueError("只有用户能解除已有字段锁定")
            data["parameters"][key]["locked"] = False
        for key, field in changes.items():
            field = canonical_field(key, field)
            if not isinstance(field, dict):
                raise ValueError(f"{key} 必须为参数对象")
            previous = data["parameters"].get(key, {})
            field.setdefault("unit", canonical_unit(key) or "")
            field.setdefault("meaning", key)
            field.setdefault("conditions", {})
            validate_field(key, field)
            from .research_contract import field_equal
            from .parameter_presentation import ALIASES
            from .material_ids import parent_path
            parent = parent_path(key, field)
            canonical = parent + ".base_formula" if parent else ALIASES.get(key, key)
            if actor == "agent":
                for alias, bound in data["parameters"].items():
                    bound_parent = parent_path(alias, bound)
                    bound_key = bound_parent + ".base_formula" if bound_parent else ALIASES.get(alias, alias)
                    if bound.get("locked") and bound_key == canonical and (
                            not field_equal(key, bound.get("value"), field.get("value"))
                            or bound.get("unit", "") != field.get("unit", "")):
                        raise ValueError("用户锁定字段禁止通过别名覆盖: " + key)
            if actor == "agent" and previous.get("locked") and (not field_equal(key, previous.get("value"), field.get("value")) or previous.get("unit", "") != field.get("unit", "")):
                raise ValueError(f"用户锁定字段禁止覆盖: {key}")
            if actor == "agent" and previous.get("locked"):
                continue
            # 程序校验过的课题原文绑定也受锁定保护；一般Agent字段不能自行加锁。
            requirement_lock = (field.get("basis") == "user_requirement" and field.get("requirement_id")
                                and isinstance(field.get("source_quote"), str) and field["source_quote"]
                                and field["source_quote"] in data.get("goal", ""))
            field["locked"] = bool(actor == "user" or previous.get("locked", False) or requirement_lock)
            data["parameters"][key] = field
            if key.endswith(".material") and not field_equal(key, previous.get("value"), field.get("value")):
                comp = key.split(".")[0]
                for dep in list(data["parameters"]):
                    if dep.startswith(comp + ".") and dep != key and dep not in changes:
                        if data["parameters"][dep].get("locked"):
                            raise ValueError(f"更换材料前需解锁其关联字段: {dep}")
                        del data["parameters"][dep]
        from .physical_parameters import fixed_geometry
        fixed_geometry(data["parameters"])  # 先核对同物理量跨字段声明，冲突不得写入新版本。
        candidate_changed = candidates is not None and candidates != data["candidates"]
        if candidates is not None:
            data["candidates"] = copy.deepcopy(candidates)
        if data["parameters"] == old and not candidate_changed:
            return data
        data["revision"] += 1
        data["material_id_version"] = MATERIAL_ID_VERSION
        data["derived"], data["issues"] = calculate(data)
        budget_issues = [issue for issue in data["issues"] if "与当前几何预算不一致" in issue]
        if budget_issues:
            raise ValueError("；".join(budget_issues))
        data["history"].append({"revision": data["revision"], "actor": actor, "reason": reason,
                                "changed": sorted(k for k in old.keys() | data["parameters"].keys()
                                                  if old.get(k) != data["parameters"].get(k))})
        _save(task_dir, data)
        return data


def render(data):
    def cell(v):
        return str(v if v is not None else "未提供").replace("|", "\\|").replace("\n", " ")
    lines = [f"方案表版本：{data['revision']}", "", "| 字段 | 当前声明值 | 单位 | 依据 | 来源 | 用户锁定 |",
             "|---|---|---|---|---|---|"]
    for key, row in sorted(data["parameters"].items()):
        lines.append("| " + " | ".join(cell(x) for x in (key, row.get("value"), row.get("unit", ""),
            row.get("basis", ""), row.get("source", ""), "是" if row.get("locked") else "否")) + " |")
    lines += ["", "派生计算（依赖缺失时不计算）："]
    for key, row in sorted(data["derived"].items()):
        lines.append(f"- {key}：{cell(row['value'])} {row['unit']}；依赖：{', '.join(row['dependencies'])}")
    lines += ["", "一致性问题："] + ([f"- {x}" for x in data["issues"]] or ["- 当前无已识别的数值冲突；不代表实验验证通过。"])
    if data.get("candidates"):
        lines += ["", "候选扩展（非当前方案，采用前须提交修订）：", "```json",
                  json.dumps(data["candidates"], ensure_ascii=False, indent=2), "```"]
    return "\n".join(lines)


def projection_errors(task_dir, doc):
    data = load(task_dir)
    if not data:
        return []
    errors = document_id_errors(doc)
    if doc.get("ledger_view") is not None and digest(doc["ledger_view"]) != digest(data):
        errors.append("设计中的方案表视图与当前版本不一致")
    if doc.get("ledger_hash") != digest(data) and (data["revision"] > 0 or doc.get("ledger_hash")):
        errors.append("方案表版本已变更，请重新生成设计契约及下游产物")
    for key, v in flatten_scheme(doc.get("scheme") or {}).items():
        if key in data["parameters"] and data["parameters"][key]["value"] != v:
            errors.append(f"设计契约与方案表不一致: {key}")
    from .physical_parameters import physical_declarations
    _, physical_issues = physical_declarations(data["parameters"])
    return list(dict.fromkeys(errors + physical_issues + data.get("issues", [])))


def require_current(task_dir, doc):
    errors = projection_errors(task_dir, doc)
    if doc.get("delivery_contract") == 1 and doc.get("report_readiness", {}).get("category") == "DIAGNOSTIC":
        # 诊断报告允许描述表中已有冲突，但不允许版本或值失配。
        existing = (load(task_dir) or {}).get("issues", [])
        errors = [e for e in errors if e not in existing]
    if errors:
        raise ValueError("; ".join(errors))


def render_experiment(data, result):
    from .report_narrative import render_experiment as narrative_experiment
    return narrative_experiment(data, result)


def align_geometry_parameters(doc, paths, reason):
    """仅供已通过校验的几何修订/快照恢复，同步补充声明的重复值。"""
    canonical = flatten_scheme(doc.get("scheme") or {})
    extras = doc.get("scheme_parameters") or {}
    allowed = {f"{c}.{f}" for c in ("cathode", "anode") for f in ("L_um", "porosity", "R_s_um", "D_s_m2s")}
    updates = {}
    for key in paths:
        if key not in allowed:
            raise ValueError(f"非几何同步字段: {key}")
        if key not in extras or key not in canonical or extras[key].get("value") == canonical[key]:
            continue
        old = copy.deepcopy(extras[key])
        from .report_narrative import label
        extras[key] = {"value": canonical[key], "unit": UNITS.get(key.split(".")[-1], ""),
            "basis": "engineering_proposal", "source": reason, "meaning": label(key, {}), "conditions": {}}
        updates[key] = {"before": old, "after": copy.deepcopy(extras[key])}
    if updates:
        doc.setdefault("geometry_parameter_history", []).append({"reason": reason, "updates": updates})


def commit_design(task_dir, doc, reason="Stage 4 方案定案", base_revision=None):
    data = ensure(task_dir, doc.get("target", ""))
    if base_revision is not None and data["revision"] != base_revision:
        raise ValueError("设计期间方案表已更新，请基于最新版本重新设计")
    if doc.get("ledger_hash") and doc["ledger_hash"] != digest(data):
        raise ValueError("设计修订基于旧方案表，请重新读取当前版本")
    doc["scheme"] = canonical_scheme(doc.get("scheme"))
    doc["scheme_parameters"] = canonical_parameters(doc.get("scheme_parameters"))
    doc["material_id_version"] = MATERIAL_ID_VERSION
    # 授权的新来源值可以重建派生身份；先拦截旧几何/显式身份冲突，再验投影。
    identity_errors = [e for e in document_id_errors(dict(doc, ledger_view=data))
                       if "组件身份来源失效" not in e and "母体规则来源失效" not in e]
    if identity_errors:
        raise ValueError("; ".join(identity_errors))
    current = copy.deepcopy(data["parameters"])
    from .research_contract import bindings, field_equal
    fixed = bindings(doc.get("requirements"))
    for key, field in fixed.items():
        proposed = (doc.get("scheme_parameters") or {}).get(key)
        projected = flatten_scheme(doc["scheme"]).get(key)
        if proposed and (not field_equal(key, proposed.get("value"), field["value"]) or proposed.get("unit", "") != field.get("unit", "")):
            raise ValueError("用户固定要求与主方案不一致: " + key)
        if projected is not None and not field_equal(key, projected, field["value"]):
            raise ValueError("用户固定要求与主方案不一致: " + key)
    doc["scheme_parameters"].update(fixed)
    # 无身份元数据的旧式 scheme 仍是显式声明，不能被旧方案表静默覆盖。
    for comp in ("cathode", "anode", "electrolyte", "separator", "current_collector"):
        key = comp + ".material"
        if comp in doc["scheme"] and comp not in (doc["scheme"].get("component_identities") or {}) and key not in doc["scheme_parameters"]:
            current.pop(key, None)
    doc["scheme"] = assemble_components(doc["scheme"], {**current, **doc["scheme_parameters"]})
    identity_errors = document_id_errors(dict(doc, ledger_view=data))
    if identity_errors:
        raise ValueError("; ".join(identity_errors))
    # .material 锁定不因改走开放 canonical_path 绕过，完整组成来源锁定亦相同。
    for path, previous in data["parameters"].items():
        info = identity_path(path, previous)
        if not info or not previous.get("locked"):
            continue
        comp, kind = info
        identity = (doc["scheme"].get("component_identities") or {}).get(comp, {})
        if identity.get("kind") == kind:
            from .research_contract import field_equal
            for ref in identity.get("parameter_refs", []):
                value = identity["source_values"][ref]
                if not field_equal(comp + ".material" if kind == "material" else path, previous.get("value"), value):
                    raise ValueError("用户锁定身份字段禁止覆盖: " + path)
    if doc.get("requirements"):
        from .requirement_review import blocking_fields
        blocked = blocking_fields(doc)
        if blocked:
            raise ValueError("用户固定字段误读证据已核验，禁止提交对应字段：" + ", ".join(blocked))
    projected_fields = project_scheme_parameters(doc["scheme"], {**current, **doc["scheme_parameters"]}, reason)
    changes = {key: projected_fields[key] for key in flatten_scheme(doc["scheme"])
               if data["parameters"].get(key) != projected_fields[key]}
    identity_refs = {p for row in (doc.get("scheme", {}).get("component_identities") or {}).values()
                     for p in row.get("parameter_refs", [])}
    for key, field in (doc.get("scheme_parameters") or {}).items():
        if key in flatten_scheme(doc.get("scheme") or {}):
            if field.get("value") != flatten_scheme(doc["scheme"])[key]:
                raise ValueError(f"补充参数与几何契约冲突: {key}")
            if key in identity_refs or field.get("basis") == "user_requirement" and field.get("requirement_id"):
                changes[key] = field
            continue
        changes[key] = field
    data = update(task_dir, changes, data["revision"], reason=reason,
                  candidates=doc.get("scheme_candidates"))
    doc["ledger_revision"], doc["ledger_hash"] = data["revision"], digest(data)
    doc["ledger_view"] = data
    return doc


def research_parameters(doc):
    """正文研究记录的当前参数视图，含已接受扩展和用户绑定。"""
    from .research_contract import bindings
    current = copy.deepcopy((doc.get("ledger_view") or {}).get("parameters", {}))
    if not doc.get("research_only"):
        current.update(doc.get("scheme_parameters") or {})
    projected = project_scheme_parameters(doc.get("scheme") or {}, current)
    for key in flatten_scheme(doc.get("scheme") or {}):
        current.setdefault(key, projected[key])
    current.update(bindings(doc.get("requirements")))
    return current


def restore_research_cache(doc, cached):
    """逐字段、逐记录恢复缓存；正文变更不清空其他有效记录，也不增加模型预算。"""
    if not isinstance(cached, dict) or not isinstance(cached.get("research"), dict):
        return False
    if cached.get("research_narrative"):
        # 展示端会按最终正文、字段与审核状态重新验指纹，不能沿用旧内容。
        doc["research_narrative"] = copy.deepcopy(cached["research_narrative"])
    from .research_records import collect_research
    from .research_support import normalize_extracted_research
    from .research_contract import field_equal
    diagnostics = copy.deepcopy(cached.get("parameter_diagnostics") or [])
    errors, accepted = [], {}
    existing = (doc.get("ledger_view") or {}).get("parameters", {})
    current_extras = copy.deepcopy(doc.get("scheme_parameters") or {})
    for key, field in list(current_extras.items()):
        bound = existing.get(key, {})
        if bound.get("locked") and not field_equal(key, bound.get("value"), field.get("value")):
            errors.append({"path": key, "reason": "当前扩展参数与方案表锁定值冲突", "suggestion": "保留方案表锁定值"})
            del current_extras[key]
    declared = {**flatten_scheme(doc.get("scheme") or {}),
                **{key: field.get("value") for key, field in current_extras.items()}}
    for key, original in (cached.get("scheme_parameters") or {}).items():
        try:
            field = canonical_field(key, normalize_source_field(original))
            validate_field(key, field)
            if not field.get("source_quote") or field["source_quote"] not in doc.get("final_answer", ""):
                raise ValueError("缓存参数来源在当前正文失效")
            bound = existing.get(key, {})
            if bound.get("locked") and not field_equal(key, bound.get("value"), field.get("value")):
                raise ValueError("缓存参数与当前锁定值冲突")
            if key in declared and not field_equal(key, declared[key], field.get("value")):
                raise ValueError("缓存参数与当前已声明方案冲突")
            if field.get("basis") == "literature" and field.get("passage_id") not in {e.get("passage_id") for e in doc.get("evidence", [])}:
                raise ValueError("缓存参数文献已不在当前证据中")
            accepted[key] = field
        except (ValueError, TypeError, KeyError) as exc:
            errors.append({"path": key, "reason": str(exc), "suggestion": "仅修复来源失效的参数"})
    doc["scheme_parameters"] = {**accepted, **current_extras}
    doc["scheme_candidates"] = copy.deepcopy(cached.get("scheme_candidates") or [])
    if errors:
        diagnostics.append(errors)
    doc["parameter_diagnostics"] = diagnostics
    doc["maintenance_calls"] = cached.get("maintenance_calls", 0)
    previous = copy.deepcopy(cached["research"])
    current = research_parameters(doc)
    revalidated = collect_research({}, doc, current, previous=previous)
    failed_keys = {(e.get("collection"), e.get("id")) for e in revalidated.get("record_errors", [])}
    # 历史模型提取的单来源失败记录转成待审摘要；有效旧摘录保持原校验语义。
    from .research_records import is_generated_gap
    failed = {}
    # 旧刷新器可能把失败原记录覆盖成空gap；两次维护审计仍留有原始模型记录。
    for attempt in diagnostics:
        for entry in attempt:
            for error in entry.get("record_errors", []):
                if isinstance(error.get("record"), dict) and not is_generated_gap(error["record"]):
                    failed[error.get("collection"), error.get("id")] = error["record"]
    for error in previous.get("record_errors", []):
        if isinstance(error.get("record"), dict) and not is_generated_gap(error["record"]):
            failed[error.get("collection"), error.get("id")] = error["record"]
    accepted_keys = {(collection, row.get("requirement_id" if collection == "answers" else "id"))
                     for collection in ("answers", "candidates", "arguments")
                     for row in revalidated.get(collection, []) if not is_generated_gap(row)}
    raw = {collection: [row for (group, key), row in failed.items()
                       if group == collection and ((group, key) in failed_keys or (group, key) not in accepted_keys)]
           for collection in ("answers", "candidates", "arguments")}
    raw = normalize_extracted_research(raw)
    raw["bound_fields"] = previous.get("bound_fields", {})
    # 从当前参数逐项重验旧绑定，拒绝仍依赖旧材料/旧数值的缓存记录。
    doc["research"] = collect_research(raw, doc, current, previous=previous)
    return True


def collect_parameters(doc, client, max_attempts=2):
    """把方案正文转为明确的当前值/候选；只接受可定位的声明，不把文献数值直接定案。"""
    from .research_contract import COLLECTION_INSTRUCTIONS
    from .research_records import collect_research
    from .parameter_presentation import INSTRUCTIONS as PARAMETER_INSTRUCTIONS, normalize_metadata
    requirements = doc.get("requirements")
    doc["maintenance_calls"] = 0
    from .material_ids import declared_anode_parameters
    current = {**((doc.get("ledger_view") or {}).get("parameters") or {}), **(doc.get("scheme_parameters") or {})}
    declared = declared_anode_parameters(doc, current)
    if declared:
        doc.setdefault("scheme_parameters", {}).update(declared)
    if requirements:
        existing = doc.get("research")
        doc["research"] = collect_research({}, doc, research_parameters(doc), previous=existing)
    if client is None or not getattr(client, "available", False):
        return
    prompt = """你维护电池方案表。仅输出 JSON {"parameters":{},"candidates":[]}。
parameters 键用 cathode/anode/electrolyte/separator/current_collector/packaging/process/condition 前缀。
每项包含 value, unit, basis, source, meaning, conditions。basis 仅 engineering_proposal 或 literature。
仅提取正文明确采用且没有未解决审核冲突的参数；歧义、文献对照、假设块及待筛选数值放 candidates，禁止自行补数值。
source 为来源描述；source_quote 和 passage_id 必须放在每个参数对象顶层，不可嵌套。
source_quote 必须是输入正文中逐字存在的支持句；literature 还必须提供真实 passage_id。
比例统一 0–1，长度 um，密度 g/cm3，面载 mg/cm2，浓度 mol/L；不得改写已有几何字段。
孔隙率须注明载锂前/后；掺量和添加剂须注明分母；无明确口径则放候选。
材料模型物性不可标为实测。输出最多六十个参数。"""
    prompt += PARAMETER_INSTRUCTIONS
    if doc.get("numeric_contract"):
        prompt += "\n带abr-param/abr-derived标记的数值由程序登记；只引用current_parameters，不重新提取或覆盖这些数值。文献和候选数字不得晋升为主方案。原句摘录保留标记内的显示值。"
    payload = {"scheme": doc.get("scheme"), "text": doc.get("final_answer"),
               "issues": (doc.get("reviewer_output") or {}).get("issues"),
               "evidence_ids": [e.get("passage_id") for e in doc.get("evidence") or []]}
    if requirements:
        prompt = prompt.replace('{"parameters":{},"candidates":[]}',
            '{"parameters":{},"candidates":[],"research":{"answers":[],"candidates":[],"arguments":[],"selected_candidate_id":null}}', 1)
        prompt += COLLECTION_INSTRUCTIONS
        from .research_support import INSTRUCTIONS
        prompt += INSTRUCTIONS
        payload.update(requirements=requirements, current_parameters=research_parameters(doc))
        from .research_records import identity_bindings
        payload["selection_required_refs"] = list(identity_bindings(research_parameters(doc)))
        prompt += "\n选中材料候选的parameter_refs须覆盖selection_required_refs；其字段值仍须有正文来源。未能形成完整主方案关联时selected_candidate_id=null，不影响候选本身的研究记录。"
    if doc.get("research_only"):
        prompt = """你只维护既有电池方案的研究记录。仅输出JSON {"research":{"answers":[],"candidates":[],"arguments":[],"selected_candidate_id":null}}。
完整沿用current_parameters，禁止返回或修改顶层parameters/candidates；主配方、材料、几何和正文只读。
每条回答包含requirement_id,status(answered/partial/gap),verification(unverified/literature/not_applicable),
response,reason,next_step,source_quotes,field_support,citations,parameter_refs。
每个非background要求只返回一条，partial/gap须包含明确原因和后续验证/获取方法。
每个材料候选包含id,name,rank(正文明确顺序或null),scope(candidate/control/literature),summary,rationale,risks,
source_quotes,field_support,citations,parameter_refs。selected_candidate_id只能指向引用完整current_parameters材料身份的候选；无法确认则null。
论证包含id,requirement_ids,title,text,basis(literature/inference),source_quotes,field_support,citations,parameter_refs。
只整理既有正文中的研究回答和备选，不能新写科研方案。未执行的本课题计算/实验不能标为已验证。
已通过记录保持不变；只返回record_errors中的失败记录及未生成的候选、论证。
""" + INSTRUCTIONS
        payload["instruction"] = "只返回research，parameters与candidates不允许改写；完整沿用current_parameters。"
        payload.update(accepted_research=doc.get("research"),
                       record_errors=(doc.get("research") or {}).get("record_errors", []))
    accepted = copy.deepcopy(doc.get("scheme_parameters") or {}) if not doc.get("research_only") else {}
    diagnostics = []
    history = copy.deepcopy(doc.get("parameter_diagnostics") or [])
    research_diagnostics = []
    for _ in range(min(2, max_attempts)):
        try:
            doc["maintenance_calls"] += 1
            raw = client.chat(prompt, json.dumps(payload, ensure_ascii=False), temperature=0.1,
                              trace_tag="scheme_maintenance")
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
            proposed = json.loads(raw)
            if not isinstance(proposed, dict):
                raise ValueError("方案维护响应必须为JSON对象")
            partial = bool(diagnostics) or bool(doc.get("research_only"))
            parameters = proposed.get("parameters", {}) if partial else proposed["parameters"]
            candidates = proposed.get("candidates", doc.get("scheme_candidates", [])) if partial else proposed.get("candidates", [])
            if not isinstance(parameters, dict) or len(parameters) > 60 or not isinstance(candidates, list):
                raise ValueError("方案扩展结构错误")
            errors = []
            if doc.get("research_only"):
                if parameters or "candidates" in proposed:
                    errors.append({"path": "$scope", "reason": "研究恢复响应的顶层参数/候选无修改权限，已忽略",
                                   "suggestion": "只返回research；有效研究记录继续保留"})
                parameters = {}
            if diagnostics:
                for missing in {e["path"] for e in diagnostics[-1]} - set(parameters) - set(accepted) - {"$", "$research", "$scope"}:
                    errors.append({"path": missing, "reason": "重试未返回待修复字段", "suggestion": "仅修复指定字段"})
            for key, field in parameters.items():
                try:
                    field = canonical_field(key, normalize_source_field(field))
                    field = normalize_metadata(key, field, {**((doc.get("ledger_view") or {}).get("parameters", {})), **accepted})
                    if doc.get("numeric_contract") and type(field.get("value")) in (int, float) and key not in accepted:
                        # 新数值必须在Writer的声明区登记后由程序引用，不能从自由正文反向成为主方案。
                        candidates.append({"path": key, "field": field, "reason": "数值未在Writer参数声明中登记，保留为待确认候选"})
                        continue
                    if key in accepted:
                        if accepted[key] != field:
                            raise ValueError("已通过字段禁止修改")
                        continue
                    if diagnostics and "$" not in {e["path"] for e in diagnostics[-1]} and key not in {e["path"] for e in diagnostics[-1]}:
                        raise ValueError("重试不能新增未授权字段")
                    validate_field(key, field)
                    quote = field.get("source_quote")
                    if not quote or quote not in (doc.get("final_answer") or ""):
                        raise ValueError("缺少可定位的顶层 source_quote")
                    if field["basis"] not in ("engineering_proposal", "literature"):
                        raise ValueError("扩展参数不能冒充本方案实测")
                    if field["basis"] == "literature" and field.get("passage_id") not in payload["evidence_ids"]:
                        raise ValueError("扩展参数缺少真实 passage_id")
                    accepted[key] = field
                except (ValueError, TypeError, KeyError) as exc:
                    errors.append({"path": key, "reason": str(exc), "suggestion": "仅修复该字段，引用放在参数对象顶层"})
            if not doc.get("research_only"):
                doc["scheme_parameters"], doc["scheme_candidates"] = accepted, candidates
            if requirements:
                try:
                    current = research_parameters(doc)
                    current.update(accepted)
                    from .research_support import normalize_extracted_research
                    raw_research = normalize_extracted_research(proposed.get("research"))
                    doc["research"] = collect_research(raw_research, doc, current, previous=doc.get("research"))
                    research_diagnostics = doc["research"].get("diagnostics", [])
                    if research_diagnostics:
                        errors.append({"path": "$research", "reason": "研究记录部分验收失败",
                            "record_errors": doc["research"]["record_errors"],
                            "suggestion": "仅返回失败记录；必须使用source_quotes和逐字段field_support，不能退回单数source_quote"})
                except (ValueError, TypeError, KeyError) as exc:
                    research_diagnostics.append(str(exc))
                    errors.append({"path": "$research", "reason": str(exc), "suggestion": "仅修复research，复用已通过参数"})
            if not errors:
                return
            diagnostics.append(errors)
            payload.update(repair_errors=errors, accepted_fields=accepted,
                accepted_research=doc.get("research"), instruction="只返回出错字段/研究记录，其他通过项无修改权限；research可以引用accepted_fields")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            diagnostics.append([{"path": "$", "reason": str(exc), "suggestion": "返回合法参数 JSON"}])
            payload["repair_errors"] = diagnostics[-1]
        except Exception as exc:
            # 服务不可用时保留已有字段，不把维护失败伪装成参数完整。
            diagnostics.append([{"path": "$service", "reason": str(exc), "error_type": type(exc).__name__,
                                 "suggestion": "检查模型服务后重试，已通过记录保持不变"}])
            return
        finally:
            doc["parameter_diagnostics"] = history + diagnostics
            if requirements and research_diagnostics:
                doc["research"]["diagnostics"] = research_diagnostics


def normalize_source_field(field):
    """兼容历史嵌套引用；冲突时拒绝，不猜测。"""
    if not isinstance(field, dict):
        raise ValueError("参数必须为对象")
    field = copy.deepcopy(field)
    source = field.get("source")
    if isinstance(source, dict):
        for key in ("source_quote", "passage_id"):
            if key in source:
                if key in field and field[key] != source[key]:
                    raise ValueError(f"{key} 顶层与嵌套值冲突")
                field[key] = source[key]
    return field
