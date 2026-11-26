"""Stage 5 有限输入修复及几何优化；预算在调用模型前占用。"""
import json
import math
import re

from . import stage5_attempts as attempts
from .pinn_inputs import AUTO_FIELDS, diagnose
from .scheme_ledger import load as load_ledger, require_current, digest


def optimize(mgr, client=None):
    from .hashing import canonical_hash, stage5_scheme_projection
    from auto_battery_research.tools.workflow_actions import (
        run_pinn_simulation, revise_geometry, extract_energy_target_wh_kg,
        get_target_energy_tolerance_pct, _match_pinn_system_for_scheme, _pinn_result_identity_ok)
    from auto_battery_research.simulation.geometry_contract import geometry_hash
    path = mgr.get_task_output_dir()
    doc = json.loads((path / "design_scheme.json").read_text(encoding="utf-8"))
    if mgr.get_stage_by_id(5).skip or not mgr.config.get("pinn_trigger", {}).get("enabled", False) or not _match_pinn_system_for_scheme(doc["scheme"]).get("matched"):
        return {"status": "NOT_REQUIRED"}
    require_current(path, doc)
    target = extract_energy_target_wh_kg(mgr.target_goal)
    threshold = target * (1 - get_target_energy_tolerance_pct(mgr) / 100) if target is not None else None
    initial_run = False
    state = attempts.load(mgr, doc)
    # 已占用模型请求记中断，不自动重发；已提交候选只恢复验证仿真。
    for record in state["rounds"]:
        if record.get("attempt_id") and record.get("status") in ("RESERVED", "PROPOSED"):
            attempts.update(mgr, record["attempt_id"], status="INTERRUPTED", error="模型请求中断，保留已用机会")
    while True:
        doc = json.loads((path / "design_scheme.json").read_text(encoding="utf-8"))
        require_current(path, doc)
        ledger = load_ledger(path)
        if ledger:
            doc["ledger_view"] = ledger
        scheme = doc["scheme"]
        result_path = path / "pinn_simulation_result.json"
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        rate = (scheme.get("geometry", {}).get("condition") or {}).get("c_rate")
        if not isinstance(rate, (int, float)):
            rate = result.get("c_rate") or (result.get("pinn") or {}).get("c_rate") or .5
        diagnostic = diagnose(doc, rate)
        state = attempts.load(mgr, doc)
        if diagnostic["status"] in ("CONTRACT_INVALID", "UNREPAIRABLE_INPUT", "ERROR"):
            return attempts.finish(mgr, "INPUT_BLOCKED", diagnostic=diagnostic)
        current_hash = geometry_hash(scheme["geometry"])
        valid = result.get("status") == "CONVERGED" and _pinn_result_identity_ok(mgr, result.get("pinn"))
        if valid:
            # spec仍是旧版时单看spec_hash会误收旧结果；同时绑定当前方案与完整物理门禁。
            valid = result.get("pinn", {}).get("scheme_hash") == canonical_hash(stage5_scheme_projection(scheme))
        if valid:
            from auto_battery_research.checkers.pinn_physics_checker import PINNPhysicsChecker
            checker = PINNPhysicsChecker()
            checker.on_init(mgr, {"id": 5}, mgr.config)
            valid, _ = checker.do_check()
        energy = result.get("energy_wh_kg")
        valid = valid and isinstance(energy, (int, float)) and not isinstance(energy, bool) and math.isfinite(energy)
        if diagnostic["status"] == "VALID" and valid:
            attempts.simulation_completed(mgr, {"success": True, "key_findings": {"pinn_run": {"status": "CONVERGED"}}})
            if threshold is None:
                return attempts.finish(mgr, "SIMULATION_VALID", diagnostic=diagnostic)
            if energy >= threshold:
                return attempts.finish(mgr, "TARGET_MET", threshold_wh_kg=threshold, diagnostic=diagnostic)
            mode = "energy_optimization"
            locked = {k for k, row in (doc.get("ledger_view") or {}).get("parameters", {}).items() if row.get("locked")}
            allowed = sorted(AUTO_FIELDS - locked)
        elif diagnostic["status"] in ("OUT_OF_RANGE", "OUT_OF_ENVELOPE"):
            mode, allowed = "input_repair", diagnostic["allowed_fields"]
            if attempts.budget(mgr, doc)["remaining"] == 0:
                return attempts.finish(mgr, "BUDGET_EXHAUSTED", diagnostic=diagnostic)
            if client is None or not getattr(client, "available", False):
                return attempts.finish(mgr, "LLM_UNAVAILABLE", diagnostic=diagnostic)
        elif diagnostic["status"] == "VALID":
            pending = next((r for r in reversed(state["rounds"]) if r.get("status") == "COMMITTED" and r.get("geometry_hash") == current_hash), None)
            try:
                prior = json.loads((path / "stage5_execution.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                prior = {}
            already_failed = prior.get("physics", {}).get("status") == "FAILED" and prior.get("physics", {}).get("input_hash") == digest(scheme)
            if initial_run or (already_failed and not pending):
                return attempts.finish(mgr, "NO_VALID_SIMULATION", diagnostic=diagnostic)
            matching_result = (result and _pinn_result_identity_ok(mgr, result.get("pinn"))
                               and result.get("pinn", {}).get("scheme_hash") == canonical_hash(stage5_scheme_projection(scheme)))
            if matching_result and result.get("status") == "ERROR" and not pending:
                return attempts.finish(mgr, "NO_VALID_SIMULATION", diagnostic=diagnostic)
            initial_run = True
            response = run_pinn_simulation(stage_manager=mgr, c_rate=rate)
            if not response.get("success"):
                return attempts.finish(mgr, "SIMULATION_FAILED", diagnostic=response)
            continue
        else:
            return attempts.finish(mgr, "INPUT_BLOCKED", diagnostic=diagnostic)
        if attempts.budget(mgr, doc)["remaining"] == 0:
            return attempts.finish(mgr, "BUDGET_EXHAUSTED", diagnostic=diagnostic)
        if not allowed:
            return attempts.finish(mgr, "INPUT_BLOCKED", diagnostic={"error": "没有可修订的未锁定几何"})
        record = attempts.reserve(mgr, mode, current_hash)
        attempt_id = record["attempt_id"]
        attempts.update(mgr, attempt_id, allowed_fields=allowed, before_energy_wh_kg=energy if valid else None,
                        diagnostic=diagnostic, c_rate=rate)
        try:
            if client is not None and getattr(client, "available", False):
                raw = client.chat(
                    '只返回JSON {"revisions":{"anode.R_s_um":数值},"reason":"理由"}。仅修改allowed_fields中的字段，'
                    '修改必须为有限JSON数值并通过给定范围和训练包络。材料、电解液、电压、倍率、D_s及用户锁定值不得改变。'
                    + ('本轮尚无有效仿真，仅修复输入阻断，可一次修复多个授权字段。不能编造性能结果。' if mode == "input_repair" else '根据有效放电结果提出工程优化候选，不编造物性。'),
                    json.dumps({"mode": mode, "allowed_fields": allowed, "target": target, "threshold": threshold,
                        "scheme": scheme, "diagnostic": diagnostic, "ranges_SI": diagnostic.get("ranges_SI", {}),
                        "simulation": {k: v for k, v in result.items() if k != "discharge_curve"},
                        "ledger": doc.get("ledger_view"), "previous_attempts": state["rounds"]}, ensure_ascii=False),
                    temperature=.1, json_mode=True, trace_tag="geometry_input_repair" if mode == "input_repair" else "geometry_optimize")
                proposal = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
                changes = normalize_changes(proposal.get("revisions"))
                reason = proposal.get("reason") or ("修复 PINN 输入范围/包络" if mode == "input_repair" else "有限几何能量优化")
            else:
                changes = bounded_candidate(scheme, diagnostic.get("ranges_SI", {}), record["attempt"] - 1)
                reason = "有限几何工程试探；不修改材料物性，最终按有效放电结果择优"
            if not changes or not set(changes) <= set(allowed):
                raise ValueError("候选为空或包含未授权字段")
            attempts.update(mgr, attempt_id, status="PROPOSED", revisions=changes, reason=reason)
            nested = {}
            for key, value in changes.items():
                comp, field = key.split(".")
                nested.setdefault(comp, {})[field] = value
            response = revise_geometry(nested, reason=reason, stage_manager=mgr,
                base_revision=scheme.get("design_revision", 0), base_geometry_hash=current_hash,
                attempt_id=attempt_id, c_rate=rate)
            if not response.get("success"):
                continue
            simulation = run_pinn_simulation(stage_manager=mgr, c_rate=rate)
            if not simulation.get("success"):
                saved = json.loads((path / "design_scheme.json").read_text(encoding="utf-8"))
                after = diagnose(saved, rate)
                if after["status"] not in ("OUT_OF_RANGE", "OUT_OF_ENVELOPE"):
                    return attempts.finish(mgr, "SIMULATION_FAILED", diagnostic=simulation)
        except Exception as exc:
            latest = next(r for r in attempts.load(mgr)["rounds"] if r.get("attempt_id") == attempt_id)
            if latest.get("status") == "COMMITTED":
                attempts.update(mgr, attempt_id, status="SIMULATION_FAILED", error=str(exc))
                return attempts.finish(mgr, "SIMULATION_FAILED", diagnostic={"error": str(exc)})
            attempts.update(mgr, attempt_id, status="REJECTED", error=str(exc))


def normalize_changes(changes):
    if not isinstance(changes, dict):
        raise ValueError("候选 revisions 必须是对象")
    normalized = {}
    for key, value in changes.items():
        entries = {f"{key}.{field}": val for field, val in value.items()} if key in ("cathode", "anode") and isinstance(value, dict) else {key: value}
        for field, val in entries.items():
            if field in normalized and normalized[field] != val:
                raise ValueError("候选包含冲突字段")
            normalized[field] = val
    return normalized


def bounded_candidate(scheme, ranges, index):
    """离线能量优化只改变可设计几何；输入修复没有离线替代候选。"""
    comp = "anode" if index % 3 == 1 else "cathode"
    field = "R_s_um" if index % 3 == 2 else "L_um"
    value = scheme.get("geometry", {}).get(comp, {}).get(field)
    if not isinstance(value, (int, float)):
        return {}
    candidate = value * (1.1 if comp == "anode" else .8)
    bounds = ranges.get("ne" if comp == "anode" else "pe", {}).get("R_s" if field == "R_s_um" else "thickness", {})
    if bounds:
        candidate = max(bounds["min"] * 1e6, min(candidate, bounds["max"] * 1e6))
    return {f"{comp}.{field}": candidate} if not math.isclose(candidate, value) else {}
