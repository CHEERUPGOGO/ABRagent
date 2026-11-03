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
from contextlib import contextmanager
from pathlib import Path

FILENAME = "scheme_ledger.json"
DOMAINS = {"requirements", "cathode", "anode", "electrolyte", "separator",
           "current_collector", "packaging", "process", "condition"}
UNITS = {"L_um": "um", "R_s_um": "um", "D_s_m2s": "m2/s",
         "loading_mg_cm2": "mg/cm2", "compaction_g_cm3": "g/cm3",
         "density_g_cm3": "g/cm3", "capacity_mAh_g": "mAh/g",
         "areal_capacity_mAh_cm2": "mAh/cm2", "c_rate": "C",
         "temperature_K": "K", "v_min": "V", "v_max": "V",
         "target_energy_wh_kg": "Wh/kg", "volume_uL_cm2": "uL/cm2"}


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
    if "n_p_ratio" in geo:
        fields["anode.n_p_ratio"] = geo["n_p_ratio"]
    return fields


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
    put("packaging.stack_mass_mg_cm2", ["cathode.loading_mg_cm2", "anode.composite_mass_mg_cm2",
        "current_collector.cathode_mass_mg_cm2", "current_collector.anode_mass_mg_cm2",
        "separator.mass_mg_cm2", "electrolyte.mass_mg_cm2"], lambda *v: sum(v), "mg/cm2")
    put("packaging.cell_mass_mg_cm2", ["packaging.stack_mass_mg_cm2", "packaging.mass_mg_cm2"], lambda a,b: a+b, "mg/cm2")
    for label in ("stack", "cell"):
        put(f"packaging.{label}_energy_wh_kg", ["cathode.areal_capacity_mAh_cm2",
            "condition.average_discharge_voltage_V", f"packaging.{label}_mass_mg_cm2"],
            lambda capacity, voltage, mass: capacity*voltage/mass*1000 if mass else None, "Wh/kg")
    return derived, issues


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
        for key in unlock:
            if actor != "user" or key not in data["parameters"]:
                raise ValueError("只有用户能解除已有字段锁定")
            data["parameters"][key]["locked"] = False
        for key, field in changes.items():
            field = copy.deepcopy(field)
            if not isinstance(field, dict):
                raise ValueError(f"{key} 必须为参数对象")
            previous = data["parameters"].get(key, {})
            field.setdefault("unit", canonical_unit(key) or "")
            field.setdefault("meaning", key)
            field.setdefault("conditions", {})
            validate_field(key, field)
            if actor == "agent" and previous.get("locked") and previous.get("value") != field.get("value"):
                raise ValueError(f"用户锁定字段禁止覆盖: {key}")
            if actor == "agent" and previous.get("locked"):
                continue
            field["locked"] = actor == "user" or previous.get("locked", False)
            data["parameters"][key] = field
            if key.endswith(".material") and previous.get("value") != field.get("value"):
                comp = key.split(".")[0]
                for dep in list(data["parameters"]):
                    if dep.startswith(comp + ".") and dep != key and dep not in changes:
                        if data["parameters"][dep].get("locked"):
                            raise ValueError(f"更换材料前需解锁其关联字段: {dep}")
                        del data["parameters"][dep]
        candidate_changed = candidates is not None and candidates != data["candidates"]
        if candidates is not None:
            data["candidates"] = copy.deepcopy(candidates)
        if data["parameters"] == old and not candidate_changed:
            return data
        data["revision"] += 1
        data["derived"], data["issues"] = calculate(data)
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
    errors = []
    if doc.get("ledger_view") is not None and digest(doc["ledger_view"]) != digest(data):
        errors.append("设计中的方案表视图与当前版本不一致")
    if doc.get("ledger_hash") != digest(data) and (data["revision"] > 0 or doc.get("ledger_hash")):
        errors.append("方案表版本已变更，请重新生成设计契约及下游产物")
    for key, v in flatten_scheme(doc.get("scheme") or {}).items():
        if key in data["parameters"] and data["parameters"][key]["value"] != v:
            errors.append(f"设计契约与方案表不一致: {key}")
    return errors + data.get("issues", [])


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
    changes = {}
    for key, value in flatten_scheme(doc.get("scheme") or {}).items():
        previous = data["parameters"].get(key)
        if previous and previous.get("value") == value:
            continue
        changes[key] = {"value": value, "unit": UNITS.get(key.split(".")[-1], ""),
                        "basis": "engineering_proposal", "source": reason}
    for key, field in (doc.get("scheme_parameters") or {}).items():
        if key in flatten_scheme(doc.get("scheme") or {}):
            if field.get("value") != flatten_scheme(doc["scheme"])[key]:
                raise ValueError(f"补充参数与几何契约冲突: {key}")
            continue
        changes[key] = field
    data = update(task_dir, changes, data["revision"], reason=reason,
                  candidates=doc.get("scheme_candidates"))
    doc["ledger_revision"], doc["ledger_hash"] = data["revision"], digest(data)
    doc["ledger_view"] = data
    return doc


def collect_parameters(doc, client):
    """把方案正文转为明确的当前值/候选；只接受可定位的声明，不把文献数值直接定案。"""
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
    payload = {"scheme": doc.get("scheme"), "text": doc.get("final_answer"),
               "issues": (doc.get("reviewer_output") or {}).get("issues"),
               "evidence_ids": [e.get("passage_id") for e in doc.get("evidence") or []]}
    accepted, diagnostics = {}, []
    for _ in range(2):
        try:
            raw = client.chat(prompt, json.dumps(payload, ensure_ascii=False), temperature=0.1,
                              trace_tag="scheme_maintenance")
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
            proposed = json.loads(raw)
            parameters = proposed["parameters"]
            candidates = proposed.get("candidates", [])
            if not isinstance(parameters, dict) or len(parameters) > 60 or not isinstance(candidates, list):
                raise ValueError("方案扩展结构错误")
            errors = []
            if diagnostics:
                for missing in {e["path"] for e in diagnostics[-1]} - set(parameters) - set(accepted) - {"$"}:
                    errors.append({"path": missing, "reason": "重试未返回待修复字段", "suggestion": "仅修复指定字段"})
            for key, field in parameters.items():
                try:
                    field = normalize_source_field(field)
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
            doc["scheme_parameters"], doc["scheme_candidates"] = accepted, candidates
            if not errors:
                return
            diagnostics.append(errors)
            payload.update(repair_errors=errors, accepted_fields=list(accepted), instruction="只返回出错字段，其他字段无修改权限")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            diagnostics.append([{"path": "$", "reason": str(exc), "suggestion": "返回合法参数 JSON"}])
            payload["repair_errors"] = diagnostics[-1]
        except Exception:
            # 服务不可用时保留已有字段，不把维护失败伪装成参数完整。
            return
        finally:
            doc["parameter_diagnostics"] = diagnostics


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
