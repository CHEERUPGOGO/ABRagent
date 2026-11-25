"""从归档规划和修复响应恢复需求分组；副本验收后发布，不修改科研正文或预算。"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from .scheme_ledger import atomic_write, task_lock

FILES = ("design_scheme.json", "rag_result.json", "stage4_repair_checkpoint.json", "stage4_retrieval_checkpoint.json")


def sync_contract(value, old, new, removed):
    """只同步活动需求与关联；原响应及历史审计留在原位置和备份中。"""
    if isinstance(value, list):
        return [sync_contract(v, old, new, removed) for v in value]
    if not isinstance(value, dict):
        return value
    result = copy.deepcopy(value)
    for key, child in value.items():
        if key in ("repair_audit", "parameter_diagnostics", "diagnostics", "history", "requirement_history", "record_errors"):
            continue  # 历史错误和科学意见不是活动关联，不改写审计。
        if key == "requirements" and isinstance(child, dict) and child.get("source") == old["source"]:
            result[key] = copy.deepcopy(new)
        elif key == "answers" and isinstance(child, list):
            result[key] = [sync_contract(r, old, new, removed) for r in child if r.get("requirement_id") not in removed]
        elif key == "research_support_review" and isinstance(child, list):
            result[key] = [sync_contract(r, old, new, removed) for r in child
                           if not (r.get("collection") == "answers" and r.get("id") in removed)]
        elif key in ("requirement_ids", "affected_requirement_ids", "misread_ids", "unanswered_ids", "reported_misread_ids") and isinstance(child, list):
            result[key] = [i for i in child if i not in removed]
        else:
            result[key] = sync_contract(child, old, new, removed)
    if "research" in result and isinstance(result["research"], dict):
        result["research"]["status"] = "partial"
        result["research"]["coverage_reviewed"] = False
    if isinstance(result.get("research_narrative"), dict):
        result["research_narrative"]["status"] = "stale"
    return result


def repair_archived_groups(task_dir, config=None, root_dir=None):
    """只恢复原分组与总任务来源，保留全部实质要求的ID和内容；不推进阶段。"""
    from .requirement_recovery import recover_requirements, merge_requirement_patch
    from .requirement_structure import merge_exact_duplicates, normalize_groups
    from .requirement_sources import meaningful_gap
    from .research_contract import bindings, document_errors
    from .report_readiness import check
    from .scheme_ledger import projection_errors
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker

    task = Path(task_dir).resolve()
    initial = {name: (task / name).read_bytes() for name in FILES if (task / name).exists()}
    doc = json.loads(initial["design_scheme.json"])
    old = doc["requirements"]
    if not any("分组" in error or "层级" in error for error in document_errors(doc)):
        return {"success": True, "changed": False, "reason": "当前需求分组无需恢复"}
    if projection_errors(task, doc):
        raise ValueError("方案表版本或参数投影已失配，拒绝恢复")
    if doc.get("writer_contract_version") == 3 or any((task / name).exists() for name in (
            "experiment_plan.json", "pinn_input_spec.json", "final_research_report.md")):
        raise ValueError("当前恢复仅用于无下游产物的v2任务，不能覆盖已发布研报或实验")
    planner, patch, evidence = None, None, None
    for trace in sorted((task / "llm_trace").glob("trace_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        for line in trace.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("goal") != old["source"] or event.get("trace_tag") not in ("planner", "requirements_repair"):
                continue
            output = event.get("output") or {}
            if output.get("status") != "ok" or not isinstance(output.get("text"), str):
                continue
            parsed = json.loads(output["text"])
            if event["trace_tag"] == "planner":
                planner, patch = parsed, None
            elif planner is not None:
                patch = parsed.get("requirements", parsed)
                evidence = {"trace": trace.name, "planner_response": planner, "repair_response": patch}
        if planner is not None and patch is not None:
            break
    if planner is None or patch is None:
        raise ValueError("缺少同一课题的规划及需求修复原响应，禁止猜测分组")
    raw = merge_exact_duplicates(copy.deepcopy(planner["requirements"]))
    queries = planner.get("retrieval_queries", [])
    _, accepted = recover_requirements(raw, old["source"], queries)
    merged = merge_exact_duplicates(merge_requirement_patch(raw, patch, accepted, old["source"]))
    new, _ = recover_requirements(merged, old["source"], queries)
    normalize_groups(new, old["source"], new["items"])
    original_items = {i["id"]: i for i in old["items"]}
    retained = {i["id"] for i in new["items"]}
    if any(original_items.get(i["id"]) != i for i in new["items"]):
        raise ValueError("修复会改动既有实质要求，拒绝零模型分组恢复")
    removed = set(original_items) - retained
    if not removed or any(meaningful_gap(original_items[i]["quote"]) or
                          original_items[i].get("binding") or original_items[i].get("bindings") for i in removed):
        raise ValueError("只能撤下程序误补的孤立连接词，不能删除真实要求或用户条件")
    if bindings(new) != bindings(old):
        raise ValueError("需求分组恢复不得改变用户固定绑定")

    history = task / "requirement_group_repair_history" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    preview, originals = history / "preview", history / "originals"
    preview.mkdir(parents=True)
    originals.mkdir()
    snapshot = {p.name: p.read_bytes() for p in task.iterdir() if p.is_file() and not p.name.endswith(".lock")}
    for name, data in snapshot.items():
        (preview / name).write_bytes(data)
        (originals / name).write_bytes(data)
    atomic_write(history / "source_responses.json", json.dumps(evidence, ensure_ascii=False, indent=2))
    updates = {}
    for name, data in initial.items():
        fixed = sync_contract(json.loads(data), old, new, removed)
        # 修复不能改正文、方案表、参数、科研状态及持久化预算。
        before = json.loads(data)
        for key in ("scheme", "ledger_view", "ledger_hash", "parameters", "scheme_parameters", "repair_parameters",
                    "final_answer", "draft", "evidence", "repair_rounds", "review_rounds", "maintenance_calls",
                    "writer_protocol", "review_completed", "review_passed", "review_status", "confidence"):
            if fixed.get(key) != before.get(key):
                raise ValueError("恢复改动受保护内容：" + name + "." + key)
        updates[name] = json.dumps(fixed, ensure_ascii=False, indent=2)
        atomic_write(preview / name, updates[name])
    fixed_doc = json.loads(updates["design_scheme.json"])
    errors = check(preview, fixed_doc)
    if errors:
        raise ValueError("恢复副本未通过契约验收：" + "；".join(errors))
    manager = SimpleNamespace(root_dir=Path(root_dir or Path(__file__).resolve().parents[2]), config=config or {},
                              target_goal=old["source"], is_legacy_task=False, get_task_output_dir=lambda *a: preview)
    checker = RAGDesignChecker()
    checker.on_init(manager, {"id": 4}, config or {})
    passed, diagnostic = checker.do_check()
    audit = {"source": evidence["trace"], "removed_connectors": [original_items[i] for i in sorted(removed)],
             "restored_group_ids": [g["id"] for g in new["groups"]], "retained_requirement_ids": sorted(retained),
             "model_calls": 0, "stage4_check_pass": passed, "diagnostic": diagnostic, "workflow_advanced": False}
    atomic_write(history / "audit.json", json.dumps(audit, ensure_ascii=False, indent=2))
    if not passed:
        raise ValueError("恢复副本Stage 4门禁未通过：" + str(diagnostic.get("error")))
    with task_lock(task):
        if any(not (task / name).exists() or (task / name).read_bytes() != data for name, data in snapshot.items()):
            raise ValueError("恢复期间任务已变更，拒绝覆盖")
        try:
            for name, text in updates.items():
                atomic_write(task / name, text)
            checker.on_init(SimpleNamespace(**{**manager.__dict__, "get_task_output_dir": lambda *a: task}), {"id": 4}, config or {})
            valid, diagnostic = checker.do_check()
            if not valid:
                raise ValueError("发布后Stage 4验收失败：" + str(diagnostic.get("error")))
        except Exception:
            for name in updates:
                (task / name).write_bytes(snapshot[name])
            raise
    return {"success": True, "changed": True, "groups": len(new["groups"]), "requirements": len(new["items"]),
            "removed_connectors": [original_items[i]["quote"] for i in sorted(removed)], "stage4_check_pass": True,
            "history": str(history), "workflow_advanced": False}
