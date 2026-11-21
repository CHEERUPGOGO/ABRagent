"""Stage 5 共用尝试账本：模型调用前占用，修订与仿真共用同一个机会。"""
import copy
import json
import uuid
from datetime import datetime

from .scheme_ledger import atomic_write, digest, task_lock

STATE_FILE = "pinn_optimization_state.json"


def _identity(mgr, doc):
    from auto_battery_research.tools.workflow_actions import extract_energy_target_wh_kg, get_target_energy_tolerance_pct
    target = extract_energy_target_wh_kg(mgr.target_goal)
    threshold = target * (1 - get_target_energy_tolerance_pct(mgr) / 100) if target is not None else None
    return digest([mgr.target_goal, doc.get("generated_at"), doc["scheme"].get("initial_geometry_hash"), threshold])


def _read(mgr, doc):
    from auto_battery_research.tools.geometry_revision import count_geometry_revisions
    path = mgr.get_task_output_dir() / STATE_FILE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    if not isinstance(state, dict):
        raise ValueError("PINN 尝试账本损坏，不能自动重置预算")
    identity = _identity(mgr, doc)
    epoch = digest([mgr.target_goal, doc.get("generated_at"), doc["scheme"].get("initial_geometry_hash"),
                    doc["scheme"].get("cathode"), doc["scheme"].get("anode")])
    same_epoch = state.get("budget_identity") == epoch if state.get("budget_identity") else state.get("identity") == identity
    if not same_epoch:
        state = {"identity": identity, "attempts": 0, "rounds": []}
    rounds = state.get("rounds", [])
    if not isinstance(rounds, list) or any(not isinstance(r, dict) for r in rounds) or type(state.get("attempts", 0)) is not int:
        raise ValueError("PINN 尝试记录格式错误，不能重置预算")
    used = count_geometry_revisions(doc["scheme"])
    if (state.get("finished") and not state.get("duplicate_geometry_recovery") and rounds
            and used == 0 and all(r.get("status") == "REJECTED"
                and "补充参数与几何契约冲突" in r.get("error", "") for r in rounds)):
        # 只恢复没有执行任何修订的已知旧缺陷；真正执行过的预算不重置。
        previous = digest(state)
        state = {"identity": identity, "attempts": 0, "rounds": [],
                 "duplicate_geometry_recovery": True, "recovered_from": previous, "recovery_archive": state}
    state["attempts"] = max(0, used, state.get("attempts", 0), len(state.get("rounds", [])))
    state["schema_version"] = 2
    state["budget_identity"] = epoch
    state.setdefault("rounds", [])
    committed = {e.get("attempt_id"): e for e in doc["scheme"].get("geometry_revisions", []) or [] if isinstance(e, dict) and e.get("kind") == "revision"}
    for record in state["rounds"]:
        entry = committed.get(record.get("attempt_id")) if record.get("attempt_id") else None
        if entry and record.get("status") in ("RESERVED", "PROPOSED"):
            record.update(status="COMMITTED", geometry_hash=entry.get("geometry_hash"), design_revision=entry.get("design_revision"))
    return state


def load(mgr, doc=None):
    if doc is None:
        doc = json.loads((mgr.get_task_output_dir() / "design_scheme.json").read_text(encoding="utf-8"))
    return _read(mgr, doc)


def budget(mgr, doc=None):
    from auto_battery_research.tools.workflow_actions import get_max_geometry_revisions
    state = load(mgr, doc)
    limit = get_max_geometry_revisions(mgr)
    return {"used": state["attempts"], "max": limit, "remaining": max(0, limit - state["attempts"])}


def summary(mgr, doc=None):
    """报告生成和只读检查器共享同一摘要，不以旧报告内的计数代替账本。"""
    state = load(mgr, doc)
    result = {k: state.get(k) for k in ("identity", "status", "rounds", "diagnostic")}
    result.update(budget(mgr, doc))
    return result


def publish_summary(mgr):
    """报告消费当前课题账本摘要，保留每次候选与拒绝原因。"""
    with task_lock(mgr.get_task_output_dir()):
        path = mgr.get_task_output_dir() / "design_scheme.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        current = summary(mgr, doc)
        if doc.get("pinn_attempt_summary") != current:
            doc["pinn_attempt_summary"] = current
            atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=2))
        return doc


def _write(mgr, state):
    archived = state.pop("recovery_archive", None)
    if archived is not None:
        atomic_write(mgr.get_task_output_dir() / "pinn_optimization_history" / (state["recovered_from"] + ".json"),
                     json.dumps(archived, ensure_ascii=False, indent=2))
    atomic_write(mgr.get_task_output_dir() / STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2))


def reserve(mgr, mode, base_geometry_hash):
    """在课题锁内读取最新预算及身份，模型调用前原子登记一次机会。"""
    from auto_battery_research.simulation.geometry_contract import geometry_hash
    from auto_battery_research.tools.workflow_actions import get_max_geometry_revisions
    with task_lock(mgr.get_task_output_dir()):
        doc = json.loads((mgr.get_task_output_dir() / "design_scheme.json").read_text(encoding="utf-8"))
        if geometry_hash(doc["scheme"]["geometry"]) != base_geometry_hash:
            raise ValueError("尝试基于陈旧几何，请重新读取当前设计")
        state = _read(mgr, doc)
        if state["attempts"] >= get_max_geometry_revisions(mgr):
            raise ValueError("REVISION_BUDGET_EXHAUSTED：PINN 修订预算已耗尽")
        state["attempts"] += 1
        record = {"attempt_id": uuid.uuid4().hex, "attempt": state["attempts"], "mode": mode,
                  "status": "RESERVED", "base_geometry_hash": base_geometry_hash,
                  "timestamp": datetime.now().isoformat()}
        state["rounds"].append(record)
        state.update(status="PROPOSING", finished=False)
        _write(mgr, state)
        return copy.deepcopy(record)


def update(mgr, attempt_id, **fields):
    with task_lock(mgr.get_task_output_dir()):
        state = load(mgr)
        record = next((r for r in state["rounds"] if r.get("attempt_id") == attempt_id), None)
        if record is None:
            raise ValueError("PINN 尝试身份不存在或属于其他设计")
        record.update(fields)
        _write(mgr, state)
        return copy.deepcopy(record)


def finish(mgr, status, **fields):
    with task_lock(mgr.get_task_output_dir()):
        state = load(mgr)
        state.update(status=status, finished=True, **fields)
        _write(mgr, state)
        return state


def simulation_completed(mgr, response):
    """验证仿真只关联已提交的同一设计尝试，不额外计次。"""
    from auto_battery_research.simulation.geometry_contract import geometry_hash
    doc = json.loads((mgr.get_task_output_dir() / "design_scheme.json").read_text(encoding="utf-8"))
    current = geometry_hash(doc["scheme"]["geometry"])
    state = load(mgr, doc)
    for record in reversed(state["rounds"]):
        if record.get("status") == "COMMITTED" and record.get("geometry_hash") == current:
            update(mgr, record["attempt_id"], status="SIMULATED" if response.get("success") else "SIMULATION_FAILED",
                   simulation_status=(response.get("key_findings", {}).get("pinn_run") or {}).get("status"),
                   simulation_diagnostic=response.get("key_findings", {}).get("input_diagnostic"),
                   simulation_error=(response.get("error") or response.get("message")) if not response.get("success") else None)
            return
