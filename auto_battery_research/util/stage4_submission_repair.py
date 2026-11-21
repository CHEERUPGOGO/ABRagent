"""恢复单位投影误报导致的Stage 4提交失败；副本验收、无模型、预算不变。"""
import copy
import json
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from .scheme_ledger import (
    atomic_write, calculate, commit_design, digest, load, persist_requirement_bindings,
    project_scheme_parameters, projection_errors, task_lock,
)

CONFLICTS = {"同一条件下参数声明冲突：压实密度", "同一条件下参数声明冲突：涂层厚度"}
PREFIX = "本轮候选与当前方案存在未解决冲突，未将该候选作为推荐；以下仅保留研究分析，参数以已接受方案表为准。\n\n"
OUTPUTS = ("design_scheme.json", "design_scheme.md", "rag_result.json", "research_context.json",
           "scheme_ledger.json", "scheme_table.md", "stage4_repair_checkpoint.json", "stage4_retrieval_checkpoint.json")


def repair_stage4_submission(mgr):
    """只恢复已归档且数值一致的单位投影失败，不改科研结论、不推进阶段。"""
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
    from auto_battery_research.rag import RelationEngine
    from auto_battery_research.simulation.geometry_contract import validate_geometry_contract, geometry_hash
    from .material_ids import checkpoint_identity, document_id_errors
    from .research_contract import finalize_research
    from .research_records import collect_research
    from .report_readiness import prepare, check

    task = Path(mgr.get_task_output_dir())
    if any((task / n).exists() for n in ("experiment_plan.json", "pinn_input_spec.json", "final_research_report.md")):
        return {"success": False, "error": "已有下游产物，禁止仅修Stage 4提交；须完整同步迁移"}
    initial = {f.name: f.read_bytes() for f in task.iterdir() if f.is_file() and f.name != ".scheme_ledger.lock"}
    try:
        original = json.loads(initial["design_scheme.json"])
        seed = json.loads(initial["stage4_repair_checkpoint.json"])
        ledger = load(task)
        if projection_errors(task, original):
            raise ValueError("当前方案表或设计身份不一致，禁止覆盖")
        if original.get("submission_repair") and not check(task, original):
            return {"success": True, "changed": False, "workflow_advanced": False}
        if not original.get("final_answer", "").startswith(PREFIX):
            raise ValueError("当前产物不是单位投影冲突的诊断回退")
        text = original["final_answer"][len(PREFIX):]
        if text.split("\n\n## 参考来源")[0].strip() != seed["draft"].strip():
            raise ValueError("拒绝候选与当前修复正文不一致")
        if original.get("evidence") != seed.get("evidence"):
            raise ValueError("检查点证据与当前设计不一致")
        rejected = original.get("rejected_designs", [])[-1]
        reasons = set(rejected["reason"].split("; "))
        if not reasons or not reasons <= CONFLICTS:
            raise ValueError("拒绝原因不限于已知单位投影冲突，不能自动恢复")
        candidate = copy.deepcopy(rejected["scheme"])
        params = copy.deepcopy(rejected["scheme_parameters"])
        preview = copy.deepcopy(ledger)
        preview["parameters"].update(params)
        preview["parameters"] = project_scheme_parameters(candidate, preview["parameters"])
        conflicts = calculate(preview)[1]
        if conflicts:
            raise ValueError("规范投影后仍有真实冲突：" + "; ".join(conflicts))
        for key, row in params.items():
            if row.get("basis") == "user_requirement":
                continue  # 下方独立重新验证用户原句，不能信任档案自封锁定。
            if row.get("value") is not None and (not row.get("source_quote") or row["source_quote"] not in text):
                raise ValueError("候选参数来源失效：" + key)
        geo = candidate.get("geometry") or {}
        geometry_errors = validate_geometry_contract(geo, candidate, original.get("evidence", []))
        if geometry_errors:
            raise ValueError("; ".join(geometry_errors))
        candidate["geometry_hash"] = geometry_hash(geo)
        history = task / "stage4_submission_repair_history" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        stage = history / "preview"
        stage.mkdir(parents=True)
        for name, content in initial.items():
            (history / name).write_bytes(content)
            (stage / name).write_bytes(content)
        if (task / "scheme_history").exists():
            shutil.copytree(task / "scheme_history", stage / "scheme_history")
        doc = copy.deepcopy(original)
        doc.update(scheme=candidate, scheme_parameters=params, final_answer=text)
        saved = persist_requirement_bindings(stage, doc["requirements"], ledger["revision"], review_doc=doc)
        doc.update(ledger_view=saved, ledger_revision=saved["revision"], ledger_hash=digest(saved))
        if document_id_errors(doc):
            raise ValueError("; ".join(document_id_errors(doc)))
        commit_design(stage, doc, base_revision=saved["revision"])
        rules = RelationEngine().check_scheme(doc["scheme"], answer_text=text)
        doc["rule_checks"] = rules
        # 只撤除这次程序提交故障的诊断，所有学术否决和证据限制保持原样。
        warnings = [s for s in original["report_readiness"].get("issues", [])
                    if s != "一致性冲突，已保留方案表而未采用冲突候选：" + rejected["reason"]]
        prepare(doc, doc["ledger_view"], warnings)
        research = rejected.get("research") or (seed.get("research_draft") or {}).get("research")
        doc["research"] = collect_research({}, doc, doc["ledger_view"]["parameters"], previous=research)
        finalize_research(doc)
        for key in ("reviewer_output", "repair_audit", "evidence", "confidence", "review_completed", "review_passed"):
            if doc.get(key) != original.get(key):
                raise ValueError("提交恢复不得改动科学审核或正文修复历史：" + key)
        doc["submission_repair"] = {"input_hash": digest(original), "archived_reason": rejected["reason"], "model_calls": 0}
        identity = checkpoint_identity(doc["target"], doc["ledger_view"], doc.get("provenance") or {})
        for name in ("rag_result.json", "stage4_repair_checkpoint.json", "stage4_retrieval_checkpoint.json"):
            if name not in initial:
                continue
            content = json.loads(initial[name])
            if "scheme" in content:
                content["scheme"] = copy.deepcopy(doc["scheme"])
            if "rule_checks" in content:
                content["rule_checks"] = copy.deepcopy(rules)
            if "research_draft" in content:
                content["research_draft"] = copy.deepcopy(doc)
            for key in ("parameters", "repair_parameters"):
                if key in content:
                    content[key] = project_scheme_parameters(doc["scheme"], {**content[key], **doc["ledger_view"]["parameters"]})
            if "checkpoint_identity" in content:
                content["checkpoint_identity"] = identity
            atomic_write(stage / name, json.dumps(content, ensure_ascii=False, indent=2))
        atomic_write(stage / "design_scheme.json", json.dumps(doc, ensure_ascii=False, indent=2))
        from pinn.input_spec import format_geometry_markdown
        atomic_write(stage / "design_scheme.md", text + "\n\n---\n\n" + format_geometry_markdown(doc["scheme"]["geometry"]) + "\n")
        manager = SimpleNamespace(root_dir=mgr.root_dir, config=mgr.config, target_goal=mgr.target_goal,
                                  is_legacy_task=False, get_task_output_dir=lambda *a, **k: stage)
        checker = RAGDesignChecker()
        checker.on_init(manager, {"id": 4}, mgr.config)
        passed, diagnostic = checker.do_check()
        audit = {"input_hash": digest(original), "output_hash": digest(doc), "model_calls": 0,
                 "workflow_advanced": False, "stage4_check_pass": passed, "diagnostic": diagnostic}
        atomic_write(history / "audit.json", json.dumps(audit, ensure_ascii=False, indent=2))
        if not passed:
            raise ValueError(str(diagnostic))
        with task_lock(task):
            current = {f.name: f.read_bytes() for f in task.iterdir() if f.is_file() and f.name != ".scheme_ledger.lock"}
            if current != initial:
                raise ValueError("修复期间任务文件已变化，拒绝覆盖")
            added = []
            try:
                for name in OUTPUTS:
                    if name in initial:
                        atomic_write(task / name, (stage / name).read_text(encoding="utf-8"))
                for source in (stage / "scheme_history").glob("*.json"):
                    target = task / "scheme_history" / source.name
                    if target.exists():
                        continue
                    atomic_write(target, source.read_text(encoding="utf-8"))
                    added.append(target)
                checker.on_init(mgr, {"id": 4}, mgr.config)
                passed, diagnostic = checker.do_check()
                if not passed:
                    raise ValueError(str(diagnostic))
            except Exception:
                for name in OUTPUTS:
                    if name in initial:
                        (task / name).write_bytes(initial[name])
                for target in added:
                    target.unlink()
                raise
        return {"success": True, "changed": True, "history": str(history), "stage4_check_pass": True,
                "delivery_category": doc["report_readiness"]["category"], "model_calls": 0, "workflow_advanced": False}
    except (ValueError, TypeError, KeyError, OSError) as exc:
        if "history" in locals():
            atomic_write(history / "failure.json", json.dumps({"error": str(exc)}, ensure_ascii=False))
        return {"success": False, "error": str(exc)}
