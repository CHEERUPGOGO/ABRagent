"""修复既有主方案投影：保留原设计数值与科学意见，副本验收后同步报告。"""
from __future__ import annotations

import copy
import json
import math
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from .scheme_ledger import atomic_write, digest, calculate, load, projection_errors, task_lock

ACTIVE = "cathode.active_loading_mg_cm2"
TOTAL = "cathode.loading_mg_cm2"
DENSITY = "cathode.compaction_g_cm3"


def rebind_tree(value, mapping):
    """只改结构化字段关联及程序标记，原始模型响应和历史审计不重写。"""
    if isinstance(value, str):
        if value in mapping:
            return mapping[value]
        for old, new in mapping.items():
            for prefix, suffix in (("{{param:", "}}"), ("{{", "}}"), ("<!-- abr-param:", " -->")):
                value = value.replace(prefix + old + suffix, prefix + new + suffix)
        return value
    if isinstance(value, list):
        return [rebind_tree(v, mapping) for v in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if key in ("raw_response", "repair_raw_response", "writer_protocol", "protocol_state", "repair_audit",
                   "parameter_diagnostics", "history", "sync_history", "record_errors", "attempts"):
            result[key] = copy.deepcopy(child)
            continue
        result[mapping.get(key, key)] = rebind_tree(child, mapping)
    return result


def repair_parameters(ledger, text):
    """只从已有明确声明修复误占总载量路径；真实冲突或锁定字段拒绝迁移。"""
    data = copy.deepcopy(ledger)
    params = data["parameters"]
    mapping, changed = {}, []
    old = params.get(TOTAL) or {}
    if old.get("value") is not None and re.search(r"活性物质面载量|活性面载量", str(old.get("meaning", ""))):
        if old.get("locked") or params.get(ACTIVE, {}).get("value") is not None:
            raise ValueError("活性面载量迁移存在用户锁定或独立声明，不能任选数值")
        mapping[TOTAL] = ACTIVE
        moved = copy.deepcopy(old)
        moved.update(meaning="正极活性物质面载量，不含导电剂与粘结剂", quantity_kind="physical", canonical_path=ACTIVE)
        params.pop(TOTAL)
        params[ACTIVE] = moved
        changed += [TOTAL, ACTIVE]
    elif params.get(ACTIVE, {}).get("value") is None:
        raise ValueError("原文没有明确的活性面载量声明，不能推断迁移")
    density_alias = "cathode.compaction_density_g_cm3"
    alias = params.get(density_alias) or {}
    if alias.get("value") is not None:
        if params.get(DENSITY, {}).get("value") not in (None, alias["value"]) or params.get(DENSITY, {}).get("locked"):
            raise ValueError("压实密度已有独立冲突或锁定，禁止覆盖")
        params[DENSITY] = copy.deepcopy(alias)
        params[DENSITY].update(meaning="正极干涂层压实密度（原设计核算值，待实测核验）", canonical_path=DENSITY)
        params.pop(density_alias)
        mapping[density_alias] = DENSITY
        changed += [density_alias, DENSITY]
    params = rebind_tree(params, mapping)
    active, fraction = params[ACTIVE], params.get("cathode.active_fraction") or {}
    if type(active.get("value")) not in (int, float) or type(fraction.get("value")) not in (int, float) or not 0 < fraction["value"] <= 1:
        raise ValueError("活性面载量或活性质量分母不完整")
    if active.get("unit") != "mg/cm2" or fraction.get("unit") not in ("", "1", "fraction"):
        raise ValueError("活性载量与质量占比单位不符")
    fraction.update(quantity_kind="mass_fraction", denominator="干涂层总质量")
    total = active["value"] / fraction["value"]
    length, density = params.get("cathode.L_um", {}).get("value"), params.get(DENSITY, {}).get("value")
    if density is None or length is None or not math.isclose(length * density * .1, total, rel_tol=.02):
        raise ValueError("原设计厚度、压实密度和活性载量不自洽，禁止自动选择")
    params[TOTAL] = {"value": total, "unit": "mg/cm2", "basis": "engineering_proposal", "source": "已有活性面载量÷活性质量占比的确定性核算",
                     "source_quote": active.get("source_quote", ""), "meaning": "正极总干涂层面载量（含活性物质、导电剂与粘结剂）",
                     "calculation_inputs": [ACTIVE, "cathode.active_fraction"], "locked": False, "conditions": {}, "quantity_kind": "physical"}
    formula = params.get("cathode.doping.nominal_formula") or {}
    if not formula.get("value"):
        raise ValueError("缺少已声明的完整主正极名义式")
    formula["canonical_path"] = "cathode.nominal_formula"
    formula["meaning"] = "当前主推候选名义化学式（待实验验证）"
    separator = params.get("separator.type")
    if separator:
        separator.update(canonical_path="separator.material", meaning="隔膜型号")
    for path in ("condition.voltage_window", "condition.high_voltage_window"):
        original = params.get(path) or {}
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[–—~-]\s*(\d+(?:\.\d+)?)\s*V\s*", str(original.get("value", "")))
        if not match:
            raise ValueError("电压窗口没有明确的上下限及单位：" + path)
        for side, number in zip(("min", "max"), match.groups()):
            key = "condition.v_" + side if path == "condition.voltage_window" else "condition.high_voltage_v_" + side
            previous = params.get(key) or {}
            if previous.get("locked") or previous.get("value") not in (None, float(number)):
                raise ValueError("截止电压存在锁定或冲突：" + key)
            params[key] = dict(copy.deepcopy(original), value=float(number), unit="V", quantity_kind="physical",
                               meaning=("常规循环" if path == "condition.voltage_window" else "高压应力组") + ("下限电压" if side == "min" else "上限电压"))
            if path != "condition.voltage_window":
                params[key].update(canonical_path="condition.v_" + side, purpose="高压应力组")
            changed.append(key)
    if not re.search(r"半电池不(?:做|设).*?N/P", text):
        raise ValueError("缺少本轮半电池不设N/P的明确声明")
    params["condition.cell_configuration"] = {"value": "锂金属对电极半电池", "unit": "", "basis": "engineering_proposal", "source": "原设计明确半电池对电极声明",
        "source_quote": next(line for line in text.splitlines() if re.search(r"半电池不(?:做|设).*?N/P", line)), "meaning": "本轮评价构型，N/P不作为容量匹配验收项", "locked": False, "quantity_kind": "text"}
    for key in ("condition.C_rate_reference_current", "condition.cycles", "electrolyte.ec_dmc_ratio"):
        if key in params:
            params[key]["basis"] = "budget_assumption"
            params[key]["source"] = "原设计补充的工程设定，用户原文未指定，待首轮核验"
    data["parameters"] = params
    data["revision"] += 1
    data.setdefault("history", []).append({"revision": data["revision"], "actor": "agent", "reason": "修复主方案字段投影，保留原设计数值及科学边界", "changed": sorted(set(changed + [TOTAL, "cathode.doping.nominal_formula", "separator.type", "condition.cell_configuration"]))})
    data["derived"], data["issues"] = calculate(data)
    if data["issues"]:
        raise ValueError("修复参数仍有冲突：" + "；".join(data["issues"]))
    return data, mapping


def repair_design_projection(task_dir, config=None):
    """副本准备、重新绑定与门禁验收；正式发布前核对并发写入，不重跑模型。"""
    from .material_ids import assemble_components, checkpoint_identity
    from .numeric_bindings import refresh
    from .physical_parameters import fixed_geometry, record_geometry_sources
    from .research_records import collect_research
    from .report_readiness import check
    from . import experiment_design as recipes
    from .experiment_consistency import stamp, parameter_pending_fields
    from .stage5_execution import physics_exemption
    from auto_battery_research.rag import RelationEngine
    from auto_battery_research.simulation.geometry_contract import geometry_hash, input_diagnostic
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
    from auto_battery_research.checkers.stage5_checker import Stage5Checker
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    from auto_battery_research.tools.workflow_actions import run_synthesis_report

    task = Path(task_dir).resolve()
    original = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    existing = load(task)
    if (existing.get("parameters", {}).get("condition.cell_configuration", {}).get("value") == "锂金属对电极半电池"
            and existing.get("parameters", {}).get(ACTIVE, {}).get("value") is not None
            and original.get("scheme", {}).get("cathode")
            and not projection_errors(task, original)):
        return {"success": True, "changed": False, "reason": "当前主方案投影已修复"}
    errors = projection_errors(task, original)
    allowed = [e for e in errors if e.startswith("cathode.L_um 厚度、压实密度与面载不一致")]
    if digest(existing) != original.get("ledger_hash") or any(e not in allowed for e in errors):
        raise ValueError("既有方案版本或非本轮修复字段失配：" + "；".join(errors))
    if original.get("writer_contract_version") == 3:
        raise ValueError("当前维护仅适用于已有v2设计")
    if any((task / name).exists() for name in ("pinn_simulation_result.json", "pinn_input_spec.json")):
        raise ValueError("已有物理推理产物，须先通过完整仿真修订同步，不能局部迁移")
    ledger, mapping = repair_parameters(existing, original["final_answer"])
    history = task / "design_projection_repair_history" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    preview, backup = history / "preview", history / "originals"
    preview.mkdir(parents=True)
    backup.mkdir()
    initial = {p.name: p.read_bytes() for p in task.iterdir() if p.is_file() and not p.name.endswith(".lock")}
    for name, data in initial.items():
        (preview / name).write_bytes(data)
        (backup / name).write_bytes(data)
    params = ledger["parameters"]
    doc = rebind_tree(original, mapping)
    doc["final_answer"] = refresh(doc["final_answer"], params)
    doc["scheme_parameters"] = copy.deepcopy(params)
    scheme = assemble_components(doc["scheme"], params, strict=False)
    fixed = fixed_geometry(params)
    geo = copy.deepcopy(scheme["geometry"])
    for path in (TOTAL, DENSITY, "condition.v_min", "condition.v_max"):
        comp, field = path.split(".")
        geo[comp][field] = params[path]["value"]
    geo["material_ids"] = {c: scheme.get(c) for c in ("cathode", "anode")}
    geo["parameter_sources"]["cathode.D_s_m2s"]["material_id"] = scheme["cathode"]
    record_geometry_sources(geo, fixed)
    scheme.update(geometry=geo, geometry_hash=geometry_hash(geo), design_revision=scheme.get("design_revision", 0) + 1)
    doc.update(scheme=scheme, ledger_view=copy.deepcopy(ledger), ledger_revision=ledger["revision"], ledger_hash=digest(ledger))
    doc["research"] = collect_research({}, doc, params, previous=doc.get("research"))
    if isinstance(doc.get("research_narrative"), dict):
        doc["research_narrative"]["status"] = "stale"
    engine = RelationEngine()
    doc["rule_checks"] = engine.check_scheme(scheme, answer_text=doc["final_answer"])
    state = doc["report_readiness"]
    old_issues = state["issues"]
    mechanical = [i for i in old_issues if i.startswith(("结构化材料配方不完整", "极片总面载量派生值", "极片载量口径冲突"))]
    state["issues"] = [i for i in old_issues if i not in mechanical]
    state["issues"].append("主方案字段及载量核算已同步修复；未解决科学意见、工艺条件与参数实测适用性仍须独立验证。")
    state["category"] = "CONDITIONAL"
    doc["physics_input_diagnostic"] = input_diagnostic(scheme, doc.get("evidence", []))
    exempt = physics_exemption(doc)
    if not exempt:
        raise ValueError("修复后匹配到可运行模型，须通过正常Stage 5重新核验，不能代替推理")
    state["physics_status"] = "NOT_APPLICABLE"
    doc["experiment_physics_context"] = {"status": "NOT_APPLICABLE", "result": {}}
    recipe = rebind_tree(recipes.load(preview), mapping)
    pending = recipe.get("pending_parameters") or {}
    if DENSITY in pending and pending[DENSITY].get("record") == {"ref": DENSITY}:
        recipe["parameters"][DENSITY] = pending.pop(DENSITY)["record"]
        for location, row in list(recipe.get("pending_sections", {}).items()):
            if "另一项物理量" in row.get("reason", "") and DENSITY in row.get("text", ""):
                section, index = location.rsplit(":", 1)
                recipe["sections"][section][int(index)] = row["text"]
                del recipe["pending_sections"][location]
    recipe["base_ledger_hash"] = doc["ledger_hash"]
    stamp(recipe, doc)
    recipe["plan_hash"] = recipes.plan_hash(recipe)
    recipe["bindings"] = recipes.validate(recipe, doc, delivery=True)
    doc["experiment_plan_hash"] = recipe["plan_hash"]
    outcome, reasons = recipes.delivery_status(recipe, parameter_pending_fields(doc), doc=doc)
    state["experiment_status"] = outcome
    execution = json.loads((preview / "stage5_execution.json").read_text(encoding="utf-8"))
    execution["physics"] = {"status": "NOT_APPLICABLE", "passed": True, "input_hash": digest(scheme), "reason": exempt,
                            "projection_repair": "修复材料身份后重验注册表；未运行仿真"}
    execution["experiment"].update(status=outcome, plan_hash=recipe["plan_hash"], pending_fields=parameter_pending_fields(doc), reasons=reasons)
    recipe_doc = copy.deepcopy(doc)
    recipe_doc["evidence"] = doc.get("experiment_evidence") or doc["evidence"]
    recipe_doc["physics_context"] = {"status": "NOT_APPLICABLE", "result": {}}
    recipe["input_fingerprint"] = recipes.input_fingerprint(recipe_doc, doc["final_answer"][:12000])
    recipe["plan_hash"] = recipes.plan_hash(recipe)
    doc["experiment_plan_hash"] = recipe["plan_hash"]
    execution["experiment"]["plan_hash"] = recipe["plan_hash"]
    # 旧审核原样备份；本轮不授予新的科学通过或模型调用预算。
    for name in ("rag_result.json", "stage4_repair_checkpoint.json", "stage4_retrieval_checkpoint.json"):
        raw = rebind_tree(json.loads((preview / name).read_text(encoding="utf-8")), mapping)
        if "parameters" in raw:
            raw["parameters"] = copy.deepcopy(params)
        if "repair_parameters" in raw:
            raw["repair_parameters"] = copy.deepcopy(params)
        if "draft" in raw:
            raw["draft"] = refresh(raw["draft"], params)
        if "final_answer" in raw:
            raw["final_answer"] = doc["final_answer"]
        if "scheme" in raw:
            raw["scheme"] = copy.deepcopy(scheme)
        if raw.get("research_draft"):
            raw["research_draft"] = copy.deepcopy(doc)
        if "checkpoint_identity" in raw:
            raw["checkpoint_identity"] = checkpoint_identity(doc["target"], ledger, doc.get("provenance") or {})
        atomic_write(preview / name, json.dumps(raw, ensure_ascii=False, indent=2))
    atomic_write(preview / "scheme_ledger.json", json.dumps(ledger, ensure_ascii=False, indent=2))
    from .scheme_ledger import render
    atomic_write(preview / "scheme_table.md", render(ledger))
    atomic_write(preview / "design_scheme.md", doc["final_answer"])
    for name, data in (("design_scheme.json", doc), ("experiment_plan.json", recipe), ("stage5_execution.json", execution)):
        atomic_write(preview / name, json.dumps(data, ensure_ascii=False, indent=2))
    stage_state = json.loads((task / ".stage_state.json").read_text(encoding="utf-8"))
    stages = [SimpleNamespace(**row, allow_skip=False) for row in stage_state["stages"]]
    mgr = SimpleNamespace(root_dir=Path(__file__).resolve().parents[2], config=config or {}, target_goal=doc["target"], stages=stages,
        is_legacy_task=False, get_task_output_dir=lambda *a: preview,
        get_stage_by_id=lambda i: next(s for s in stages if s.id == i),
        get_all_stage_journal=lambda: json.loads((preview / "stage_journals.json").read_text(encoding="utf-8")))
    errors = check(preview, doc)
    if errors:
        raise ValueError("修复副本契约失效：" + "；".join(errors))
    for number, cls in ((4, RAGDesignChecker), (5, Stage5Checker)):
        gate = cls()
        gate.on_init(mgr, {"id": number}, config or {})
        valid, diagnostic = gate.do_check()
        if not valid:
            raise ValueError("副本阶段验收失败：" + str(diagnostic))
    result = run_synthesis_report(doc["target"], stage_manager=mgr)
    gate = FinalReportChecker()
    gate.on_init(mgr, {"id": 6}, config or {})
    valid, diagnostic = gate.do_check()
    if not result.get("success") or not valid:
        raise ValueError("副本报告验收失败：" + str(result if not result.get("success") else diagnostic))
    names = ("scheme_ledger.json", "scheme_table.md", "design_scheme.json", "design_scheme.md", "rag_result.json",
             "stage4_repair_checkpoint.json", "stage4_retrieval_checkpoint.json", "experiment_plan.json", "experiment_report.json", "stage5_execution.json", "report_record.json", "final_research_report.md", "final_research_report_supplement.md")
    audit = {"model_calls": 0, "field_mapping": mapping, "archived_mechanical_issues": mechanical, "stage4_5_6_checks_pass": True,
             "ledger_revision": ledger["revision"], "workflow_advanced": False}
    atomic_write(history / "audit.json", json.dumps(audit, ensure_ascii=False, indent=2))
    with task_lock(task):
        if any((task / name).read_bytes() != content for name, content in initial.items()):
            raise ValueError("修复期间任务被更新，拒绝覆盖")
        try:
            for name in names:
                text = (preview / name).read_text(encoding="utf-8")
                if name in ("final_research_report.md", "final_research_report_supplement.md"):
                    text = text.replace(str(preview), str(task))
                atomic_write(task / name, text)
            mgr.get_task_output_dir = lambda *a: task
            gate.on_init(mgr, {"id": 6}, config or {})
            if not gate.do_check()[0]:
                raise ValueError("正式报告验收失败")
            atomic_write(task / "scheme_history" / f"revision_{ledger['revision']}.json", json.dumps(ledger, ensure_ascii=False, indent=2))
        except Exception:
            for name in names:
                if name in initial:
                    (task / name).write_bytes(initial[name])
                else:
                    (task / name).unlink(missing_ok=True)
            raise
    return {"success": True, "changed": True, "history": str(history), "report": str(task / "final_research_report.md"), **audit}
