"""Stage 5 执行器内的有限几何优化，不依赖外层 ReAct。"""
import json
import math
import re

from .scheme_ledger import atomic_write, digest


def optimize(mgr, client=None):
    from auto_battery_research.tools.workflow_actions import (
        run_pinn_simulation, revise_geometry, get_max_geometry_revisions,
        extract_energy_target_wh_kg, get_target_energy_tolerance_pct, _match_pinn_system_for_scheme,
        _pinn_result_identity_ok)
    from auto_battery_research.tools.geometry_revision import count_geometry_revisions, resolve_input_ranges
    path = mgr.get_task_output_dir()
    target = extract_energy_target_wh_kg(mgr.target_goal)
    if target is None or mgr.get_stage_by_id(5).skip or not mgr.config.get("pinn_trigger", {}).get("enabled", False):
        return {"status": "NOT_REQUIRED"}
    threshold = target * (1 - get_target_energy_tolerance_pct(mgr) / 100)
    limit = get_max_geometry_revisions(mgr)
    doc = json.loads((path / "design_scheme.json").read_text(encoding="utf-8"))
    if not _match_pinn_system_for_scheme(doc["scheme"]).get("matched"):
        return {"status": "NOT_REQUIRED"}
    identity = digest([mgr.target_goal, doc.get("generated_at"), doc["scheme"].get("initial_geometry_hash"), threshold])
    state_path = path / "pinn_optimization_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if state.get("identity") != identity:
        state = {"identity": identity, "attempts": 0, "rounds": []}
    if state.get("finished") and not state.get("duplicate_geometry_recovery") and state.get("rounds") and all(
            r.get("status") == "REJECTED" and "补充参数与几何契约冲突" in r.get("error", "") for r in state["rounds"]):
        # 仅恢复没有落盘任何修订的已知旧缺陷，不重置真正执行过的优化预算。
        if count_geometry_revisions(doc["scheme"]) == 0:
            previous = digest(state)
            atomic_write(path / "pinn_optimization_history" / (previous + ".json"), json.dumps(state, ensure_ascii=False, indent=2))
            state = {"identity": identity, "attempts": 0, "rounds": [], "duplicate_geometry_recovery": True,
                     "recovered_from": previous}
    if state.get("finished"):
        return state
    def save():
        atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=2))
    while True:
        doc = json.loads((path / "design_scheme.json").read_text(encoding="utf-8"))
        result_path = path / "pinn_simulation_result.json"
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        energy = result.get("energy_wh_kg")
        if result.get("status") != "CONVERGED" or not _pinn_result_identity_ok(mgr, result.get("pinn")) or not isinstance(energy, (int, float)) or not math.isfinite(energy):
            state["status"] = "NO_VALID_SIMULATION"
            break
        if energy >= threshold:
            state["status"] = "TARGET_MET"
            break
        used = count_geometry_revisions(doc["scheme"])
        if used >= limit or state["attempts"] >= limit:
            state["status"] = "BUDGET_EXHAUSTED"
            break
        index = state["attempts"]
        state["attempts"] += 1
        state["threshold_wh_kg"] = threshold
        state["status"] = "PROPOSING"
        save()  # 调用模型前占用预算，重启不重复消耗。
        record = {"attempt": index + 1, "before_energy_wh_kg": energy}
        try:
            scheme = doc["scheme"]
            ranges = resolve_input_ranges(scheme.get("cathode"), scheme.get("anode"))
            if client is not None and getattr(client, "available", False):
                raw = client.chat('只返回JSON {"revisions":{"cathode.L_um":数值},"reason":"理由"}。仅修订两电极L_um/porosity/R_s_um，保持材料、电解液、电压、倍率、D_s及用户锁定值不变。根据本轮放电结果提出一组范围内的工程候选，不能编造物性。',
                    json.dumps({"target": target, "threshold": threshold, "scheme": scheme,
                        "simulation": {k: v for k, v in result.items() if k not in ("discharge_curve",)},
                        "ranges_SI": ranges, "ledger": doc.get("ledger_view"), "previous_attempts": state["rounds"]}, ensure_ascii=False),
                    temperature=0.1, trace_tag="geometry_optimize")
                proposal = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
                changes = proposal["revisions"]
                reason = proposal.get("reason", "根据未达标放电结果进行有限几何优化")
            else:
                changes = bounded_candidate(scheme, ranges, index)
                reason = "有限几何工程试探；不修改材料物性，最终按有效放电结果择优"
            if isinstance(changes, dict):
                normalized = {}
                for key, value in changes.items():
                    entries = {f"{key}.{field}": val for field, val in value.items()} if key in ("cathode", "anode") and isinstance(value, dict) else {key: value}
                    for field, val in entries.items():
                        if field in normalized and normalized[field] != val:
                            raise ValueError("优化建议包含冲突字段")
                        normalized[field] = val
                changes = normalized
            allowed = {f"{c}.{f}" for c in ("cathode", "anode") for f in ("L_um", "porosity", "R_s_um")}
            if not isinstance(changes, dict) or not changes or not set(changes) <= allowed:
                raise ValueError("优化建议为空或包含未授权字段")
            record.update(revisions=changes, reason=reason)
            nested = {}
            for key, value in changes.items():
                comp, field = key.split(".")
                nested.setdefault(comp, {})[field] = value
            if doc.get("experiment_contract"):
                # 已有实验推荐绑定旧几何，优化后由5B局部校验/重新绑定，不能反向冻结几何。
                doc["experiment_plan_pending"] = doc.pop("experiment_plan_hash", None)
                doc.pop("experiment_contract", None)
                atomic_write(path / "design_scheme.json", json.dumps(doc, ensure_ascii=False, indent=2))
            outcome = revise_geometry(nested, reason=reason, target_query=mgr.target_goal, stage_manager=mgr,
                base_revision=scheme.get("design_revision", 0), base_geometry_hash=scheme.get("geometry_hash"))
            if not outcome.get("success"):
                raise ValueError(outcome.get("error", outcome.get("message", "修订被拒绝")))
            record["status"] = "REVISED"
            state["rounds"].append(record)
            save()
            simulation = run_pinn_simulation(target_query=mgr.target_goal, stage_manager=mgr, c_rate=result.get("c_rate", .5))
            record["status"] = "SIMULATED" if simulation.get("success") else "SIMULATION_FAILED"
            save()
            if not simulation.get("success"):
                state["status"] = "SIMULATION_FAILED"
                break
        except Exception as exc:
            simulation_failed = record.get("status") == "REVISED"
            record.update(status="REJECTED", error=str(exc))
            if record not in state["rounds"]:
                state["rounds"].append(record)
            save()
            if simulation_failed:
                state["status"] = "SIMULATION_FAILED"
                break
    state["finished"] = True
    save()
    return state


def bounded_candidate(scheme, ranges, index):
    """离线候选只改变可设计几何，按注册表范围裁剪，不填补缺失值。"""
    comp = "anode" if index % 3 == 1 else "cathode"
    field = "R_s_um" if index % 3 == 2 else "L_um"
    geo = scheme.get("geometry", {}).get(comp, {})
    value = geo.get(field)
    if not isinstance(value, (int, float)):
        return {}
    candidate = value * (1.1 if comp == "anode" else .8)
    bounds = ranges.get("ne" if comp == "anode" else "pe", {}).get("R_s" if field == "R_s_um" else "thickness", {})
    if bounds:
        candidate = max(bounds["min"] * 1e6, min(candidate, bounds["max"] * 1e6))
    return {f"{comp}.{field}": candidate} if not math.isclose(candidate, value) else {}
