"""显式修复既有Stage 4输入：只补当前原文中的倍率定义和漏提的金属锂对电极。"""
import copy
import json
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from .scheme_ledger import atomic_write, digest, load, projection_errors, task_lock, update


def repair_declared_stage4_inputs(mgr):
    """副本验证后原子发布；不重跑模型、不重置修复预算或推进阶段。"""
    from .research_contract import normalize_requirements, bindings, finalize_research
    from .requirement_structure import normalize_rate_definition
    from .material_ids import declared_anode_parameters, assemble_components, checkpoint_identity
    from .research_records import collect_research
    from .report_readiness import check
    from auto_battery_research.rag import RelationEngine
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker

    task = Path(mgr.get_task_output_dir())
    original = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    ledger = load(task)
    if projection_errors(task, original):
        return {"success": False, "error": "当前方案表版本或投影不一致，禁止覆盖"}
    if any((task / n).exists() for n in ("experiment_plan.json", "pinn_input_spec.json", "final_research_report.md")):
        return {"success": False, "error": "已有下游产物，须通过完整迁移同步，不能仅修Stage 4输入"}
    doc = copy.deepcopy(original)
    requirements = copy.deepcopy(doc.get("requirements") or {})
    for item in requirements.get("items", []):
        if item.get("kind") != "constraint":
            continue
        probe = normalize_rate_definition(dict(item, status="clear"))
        if any(b.get("path") == "condition.C_rate_reference_current" for b in probe.get("bindings", [])):
            item.update(probe)
    queries = (doc.get("plan") or {}).get("retrieval_queries", [])
    requirements = normalize_requirements(requirements, doc["target"], queries)
    if [i["id"] for i in requirements["items"]] != [i["id"] for i in original["requirements"]["items"]]:
        return {"success": False, "error": "修复不能改变要求身份或拆分粒度"}
    doc["requirements"] = requirements
    doc.setdefault("plan", {})["requirements"] = copy.deepcopy(requirements)
    current = {**ledger["parameters"], **(doc.get("scheme_parameters") or {})}
    changes = declared_anode_parameters(doc, current)
    rate = bindings(requirements).get("condition.C_rate_reference_current")
    if rate:
        changes["condition.C_rate_reference_current"] = rate
    if all(ledger["parameters"].get(k) == v for k, v in changes.items()) and requirements == original["requirements"]:
        return {"success": True, "changed": False}
    names = ("design_scheme.json", "rag_result.json", "scheme_ledger.json", "scheme_table.md",
             "stage4_repair_checkpoint.json", "stage4_retrieval_checkpoint.json")
    initial = {n: (task / n).read_bytes() for n in names if (task / n).exists()}
    history = task / "stage4_input_repair_history" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    history.mkdir(parents=True)
    for n, data in initial.items():
        (history / n).write_bytes(data)
    stage = history / "preview"
    stage.mkdir()
    for source in task.iterdir():
        if source.is_file():
            shutil.copy2(source, stage / source.name)
    try:
        doc.setdefault("scheme_parameters", {}).update(changes)
        doc["scheme"] = assemble_components(doc.get("scheme"), {**current, **changes})
        # 只提交两项变化，不重提已有扩展参数（历史字段可能存在单独待处理的别名冲突）。
        saved = update(stage, changes, ledger["revision"], reason="修复用户倍率定义绑定与正文明确对电极声明")
        doc.update(ledger_view=saved, ledger_revision=saved["revision"], ledger_hash=digest(saved))
        rules = RelationEngine().check_scheme(doc["scheme"], answer_text=doc["final_answer"])
        doc["rule_checks"] = rules
        state = doc["report_readiness"]
        removed = [w for w in state.get("issues", []) if w.startswith("结构化材料配方不完整 (缺少必要组件)")]
        if removed and all(doc["scheme"].get(c) for c in ("cathode", "anode", "electrolyte")):
            state["issues"] = [w for w in state["issues"] if w not in removed]
            state["category"] = "CONDITIONAL"
        # 新身份仍重新检查C0–C8；不能把字段完整视作科学验证通过。
        for rejection in rules.get("rule_checks", {}).get("rejects", []):
            if rejection.get("reason") not in state["issues"]:
                state["issues"].append(rejection["reason"])
        doc["research"] = collect_research({}, doc, doc["ledger_view"]["parameters"], previous=doc.get("research"))
        finalize_research(doc)
        for key in ("final_answer", "evidence", "repair_audit", "provenance"):
            if doc.get(key) != original.get(key):
                raise ValueError("输入修复不得改写科研原文或审计：" + key)
        for key, row in ledger["parameters"].items():
            if row != doc["ledger_view"]["parameters"].get(key):
                raise ValueError("输入修复不得更改已接受字段：" + key)
        errors = check(stage, doc)
        if errors:
            raise ValueError("; ".join(errors))
        atomic_write(stage / "design_scheme.json", json.dumps(doc, ensure_ascii=False, indent=2))
        identity = checkpoint_identity(doc["target"], doc["ledger_view"], doc.get("provenance") or {})
        for filename in ("rag_result.json", "stage4_repair_checkpoint.json", "stage4_retrieval_checkpoint.json"):
            path = stage / filename
            if not path.exists():
                continue
            content = json.loads(path.read_text(encoding="utf-8"))
            if "plan" in content:
                content["plan"]["requirements"] = copy.deepcopy(requirements)
            if "scheme" in content:
                content["scheme"] = copy.deepcopy(doc["scheme"])
            for key in ("parameters", "repair_parameters"):
                if key in content:
                    content[key].update(changes)
            if "research_draft" in content:
                content["research_draft"] = copy.deepcopy(doc)
            if "rule_checks" in content:
                content["rule_checks"] = copy.deepcopy(rules)
            if "checkpoint_identity" in content:
                content["checkpoint_identity"] = identity
            atomic_write(path, json.dumps(content, ensure_ascii=False, indent=2))
        preview = SimpleNamespace(root_dir=mgr.root_dir, config=mgr.config, target_goal=mgr.target_goal,
                                  is_legacy_task=False, get_task_output_dir=lambda *a, **k: stage)
        checker = RAGDesignChecker()
        checker.on_init(preview, {"id": 4}, mgr.config)
        passed, diagnostic = checker.do_check()
        audit = {"changed_fields": changes, "archived_issues": removed, "check_pass": passed, "diagnostic": diagnostic,
                 "input_hash": digest(original), "output_hash": digest(doc), "model_calls": 0}
        atomic_write(history / "audit.json", json.dumps(audit, ensure_ascii=False, indent=2))
        if not passed:
            raise ValueError(str(diagnostic))
        with task_lock(task):
            if any((task / n).read_bytes() != data for n, data in initial.items()):
                raise ValueError("修复期间任务已变更，拒绝覆盖")
            added = []
            try:
                for n in initial:
                    atomic_write(task / n, (stage / n).read_text(encoding="utf-8"))
                for source in (stage / "scheme_history").glob("*.json"):
                    target = task / "scheme_history" / source.name
                    if target.exists():
                        continue
                    atomic_write(target, source.read_text(encoding="utf-8"))
                    added.append(target)
                checker.on_init(mgr, {"id": 4}, mgr.config)
                valid, final_diag = checker.do_check()
                if not valid:
                    raise ValueError(str(final_diag))
            except Exception:
                for n, data in initial.items():
                    (task / n).write_bytes(data)
                for target in added:
                    target.unlink()
                raise
        return {"success": True, "changed": True, "history": str(history), "fields": list(changes),
                "stage4_check_pass": True, "delivery_category": state["category"], "workflow_advanced": False}
    except (ValueError, TypeError, KeyError, OSError) as exc:
        atomic_write(history / "failure.json", json.dumps({"error": str(exc)}, ensure_ascii=False))
        return {"success": False, "error": str(exc), "history": str(history)}
