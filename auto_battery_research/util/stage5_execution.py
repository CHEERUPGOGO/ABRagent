"""Stage 5A/5B 独立检查点：物理失败不阻止实验设计，实验失败不重跑物理。"""
import copy
import json
import re
import time
from pathlib import Path

from .scheme_ledger import atomic_write, digest, require_current


def retrieve_process_evidence(mgr, doc, task_dir):
    """轻量本地关键词补检索；复用存量元数据，不建索引或调用检索模型。"""
    terms = ("配浆 slurry coating drying 辊压 calender", "装配 electrolyte formation 化成 注液", "cycling test protocol 测试")
    materials = [str((doc.get("scheme") or {}).get(k, "")).lower() for k in ("cathode", "anode")]
    existing = list(doc.get("evidence") or [])
    candidates = []
    path = Path((mgr.config.get("paths") or {}).get("metadata_file", "miner/json/metadata/meta_merged.json"))
    if not path.is_absolute():
        path = Path(mgr.root_dir) / path
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("passages", raw.get("paragraphs", raw.get("data", list(raw.values()))))
        if isinstance(raw, list):
            for item in raw[:10000]:
                if not isinstance(item, dict):
                    continue
                content = item.get("text") or item.get("content") or item.get("paragraph_text") or ""
                if not isinstance(content, str) or not any(m and m in content.lower() for m in materials):
                    continue
                score = sum(word.lower() in content.lower() for query in terms for word in query.split())
                if score:
                    candidates.append((score, dict(item, text=content, passage_id=item.get("passage_id") or digest(content)[:12])))
    except (OSError, ValueError, TypeError):
        pass
    seen = {e.get("passage_id") for e in existing}
    additions = []
    for _, item in sorted(candidates, key=lambda p: -p[0]):
        if item["passage_id"] not in seen:
            additions.append(item)
            seen.add(item["passage_id"])
        if len(additions) == 8:
            break
    ranked = sorted(existing, key=lambda e: -sum(w.lower() in str(e.get("text", "")).lower() for q in terms for w in q.split()))
    selected, chars = [], 0
    for item in additions + ranked:
        excerpt = str(item.get("text") or "")[:1600]
        if len(selected) >= 20 or chars + len(excerpt) > 16000:
            break
        selected.append(dict(item, text=excerpt))
        chars += len(excerpt)
    atomic_write(Path(task_dir) / "experiment_evidence.json", json.dumps({"queries": list(terms), "evidence": selected,
        "source_fingerprint": digest(doc.get("evidence") or [])}, ensure_ascii=False, indent=2))
    return selected


def physics_exemption(doc):
    """只在方案明确免仿真或注册表确认没有匹配模型时返回原因。"""
    from .research_contract import requires_scheme
    from auto_battery_research.tools.workflow_actions import _match_pinn_system_for_scheme
    if not requires_scheme(doc):
        return "分析类课题未要求电芯放电仿真"
    from .material_ids import pinn_material_ids
    materials = pinn_material_ids(doc.get("scheme") or {}, (doc.get("ledger_view") or {}).get("parameters"))
    match = _match_pinn_system_for_scheme(materials)
    if match.get("status") == "NO_MATCH":
        return "当前材料组合没有匹配的 PINN，未执行放电仿真"
    if not match.get("matched"):
        raise ValueError("；".join(match.get("reasons") or ["PINN 体系匹配未完成"]))
    return None


def report_without_simulation(doc, task_dir):
    """重验已记录的不适用判定，避免旧曲线或旧输入进入研报。"""
    recorded = (doc.get("report_readiness") or {}).get("physics_status") == "NOT_APPLICABLE"
    if not recorded:
        path = Path(task_dir) / "stage5_execution.json"
        if path.exists():
            physics = json.loads(path.read_text(encoding="utf-8")).get("physics") or {}
            recorded = physics.get("status") == "NOT_APPLICABLE" and physics.get("input_hash") == digest(doc.get("scheme"))
    return recorded and bool(physics_exemption(doc))


def finalize_stage5(mgr, design_only=False, client=None, *, restart_experiment=False):
    from auto_battery_research.checkers.pinn_physics_checker import PINNPhysicsChecker
    from auto_battery_research.tools.workflow_actions import run_pinn_simulation, select_best_stage5_result
    from . import experiment_design as recipes
    from .report_recipe_llm import _llm_client
    task_dir = mgr.get_task_output_dir()
    doc_path = task_dir / "design_scheme.json"
    state = {"physics": {}, "experiment": {}}
    try:
        prior = json.loads((task_dir / "stage5_execution.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior = {}
    def save():
        atomic_write(task_dir / "stage5_execution.json", json.dumps(state, ensure_ascii=False, indent=2))
    def save_doc():
        if doc.get("writer_contract_version") == 3:
            from .stage4_publication import publish_document
            publish_document(task_dir, doc, reason="Stage 5下游状态同步")
        else:
            atomic_write(doc_path, json.dumps(doc, ensure_ascii=False, indent=2))
    try:
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        require_current(task_dir, doc)
        old_recipe = recipes.load(task_dir)
        if old_recipe and old_recipe.get("origin") == "engineering_fallback" and not design_only:
            # 通用回退模板不是几何优化的固定约束；保留文件，标记待本轮重新生成。
            doc["experiment_plan_pending"] = doc.pop("experiment_plan_hash", None)
            doc.pop("experiment_contract", None)
            save_doc()
        checker = PINNPhysicsChecker()
        checker.on_init(mgr, {"id": 5}, mgr.config)
        started = time.monotonic()
        identity = digest(doc.get("scheme"))
        try:
            exemption = physics_exemption(doc)
            if exemption:
                valid = True
                state["physics"] = {"status": "NOT_APPLICABLE", "passed": True, "input_hash": identity,
                                    "reason": exemption, "duration_seconds": time.monotonic()-started}
            else:
                if doc.get("delivery_contract") == 1 and (doc.get("scheme", {}).get("geometry") or {}).get("contract_version") != "2.0":
                    raise ValueError("物理输入尚未形成有效结构化契约；不使用训练默认值代替当前设计声明")
                valid, diagnostic = checker.do_check()
                identity = digest(doc.get("scheme"))
                already_failed = prior.get("physics", {}).get("input_hash") == identity and prior.get("physics", {}).get("status") == "FAILED"
                needs_extraction = mgr.get_stage_by_id(5).skip
                if needs_extraction and not design_only and not already_failed:
                    run_pinn_simulation(target_query=mgr.target_goal, stage_manager=mgr)
                if not mgr.get_stage_by_id(5).skip and not design_only:
                    from .pinn_optimization import optimize
                    from auto_battery_research.tools.workflow_actions import _match_pinn_system_for_scheme
                    if _match_pinn_system_for_scheme(doc.get("scheme") or {}).get("matched") and mgr.config.get("pinn_trigger", {}).get("enabled", False):
                        state["optimization"] = optimize(mgr, client=client if client is not None else _llm_client(mgr))
                    elif (not valid) and not already_failed:
                        run_pinn_simulation(target_query=mgr.target_goal, stage_manager=mgr)
                    select_best_stage5_result(mgr)
                    identity = digest(json.loads(doc_path.read_text(encoding="utf-8")).get("scheme"))
                valid, diagnostic = checker.do_check()
                state["physics"] = {"status": "SKIPPED" if mgr.get_stage_by_id(5).skip else ("PASSED" if valid else "FAILED"),
                                    "passed": valid, "input_hash": identity, "diagnostic": diagnostic, "duration_seconds": time.monotonic()-started}
                spec_path = task_dir / "pinn_input_spec.json"
                if valid and spec_path.exists():
                    spec = json.loads(spec_path.read_text(encoding="utf-8"))
                    if not (spec.get("trigger") or {}).get("triggered"):
                        state["physics"].update(status="SKIPPED", reason=(spec.get("trigger") or {}).get("reasons"))
        except Exception as exc:
            valid = False
            state["physics"] = {"status": "FAILED", "passed": False, "input_hash": identity,
                "diagnostic": {"error": str(exc)}, "duration_seconds": time.monotonic()-started}
        save()
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        if doc.get("writer_contract_version") == 3:
            from .stage4_session import stage5_final_review
            stage5_final_review(task_dir, doc, client if client is not None else _llm_client(mgr),
                                repair_budget=(mgr.config.get("runtime_options") or {}).get("stage4_repair", {}))
        if (doc.get("scheme", {}).get("geometry") or {}).get("contract_version") == "2.0":
            from auto_battery_research.simulation.geometry_contract import input_diagnostic
            doc["physics_input_diagnostic"] = input_diagnostic(doc["scheme"], doc.get("evidence") or [])
        if state.get("optimization"):
            from .stage5_attempts import summary
            doc["pinn_attempt_summary"] = summary(mgr, doc)
        if doc.get("delivery_contract") == 1:
            readiness = doc["report_readiness"]
            readiness["physics_status"] = state["physics"]["status"]
            # 替换本轮已重验的旧物理诊断，保留独立审核和实验问题。
            readiness["issues"] = [issue for issue in readiness.get("issues", [])
                                   if not str(issue).startswith(("物理验证未完成：", "物理输入仍待修复："))]
            if not valid:
                readiness["category"] = "CONDITIONAL" if readiness["category"] != "DIAGNOSTIC" else "DIAGNOSTIC"
                readiness["issues"].append("物理验证未完成：" + str(state["physics"].get("diagnostic")))
        started = time.monotonic()
        from .issue_resolution import resolve_numeric_issues
        resolve_numeric_issues(doc)
        recipe_doc = copy.deepcopy(doc)
        recipe_doc["evidence"] = retrieve_process_evidence(mgr, doc, task_dir)
        result_path = task_dir / "pinn_simulation_result.json"
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() and valid and state["physics"]["status"] == "PASSED" else {}
        recipe_doc["physics_context"] = {"status": state["physics"]["status"], "result": result}
        recipe_doc["experiment_physics_context"] = recipe_doc["physics_context"]
        # 补检索证据属于实验推荐，不改写材料设计或方案表。
        doc["experiment_evidence"] = recipe_doc["evidence"]
        doc["experiment_physics_context"] = recipe_doc["physics_context"]
        if restart_experiment:
            recipe = recipes.generate(task_dir, recipe_doc, client if client is not None else _llm_client(mgr), doc.get("final_answer", "")[:12000], restart=True)
        else:
            recipe = recipes.generate(task_dir, recipe_doc, client if client is not None else _llm_client(mgr), doc.get("final_answer", "")[:12000])
        if doc.get("requirements"):
            doc["experiment_extensions"] = recipe.get("extensions", [])
        doc.update(stage5_contract=2, experiment_contract=2, experiment_plan_hash=recipe["plan_hash"])
        save_doc()
        from .experiment_delivery import blocking_pending_fields, assessment
        pending_fields = blocking_pending_fields(recipe, doc)
        recipes.require_current(task_dir, doc)
        experiment_status, reasons = recipes.delivery_status(recipe, pending_fields, doc=doc)
        conditional = experiment_status != "PASSED"
        state["experiment"] = {"status": experiment_status, "plan_hash": recipe["plan_hash"],
            "pending_fields": pending_fields, "reasons": reasons, "origin": recipe.get("origin"),
            "validation_notes": assessment(recipe, doc)["validation_notes"],
            "duration_seconds": time.monotonic()-started}
        if doc.get("report_readiness"):
            readiness = doc["report_readiness"]
            previous = "实验方案含待定字段、分组或目标验证缺口；相应操作须先确认，其他有效内容已保留。"
            message = "实验交付限制：" + "；".join(reasons)
            readiness["issues"] = [item for item in readiness.get("issues", []) if item not in (previous, "实验方案与方案表存在待核对的参数。")
                                   and not str(item).startswith("实验交付限制：")]
            readiness["experiment_status"] = experiment_status
            if conditional:
                if readiness["category"] == "COMPLETE":
                    readiness["category"] = "CONDITIONAL"
                readiness["issues"].append(message)
            save_doc()
        save()
        return {"success": bool(valid or design_only or doc.get("delivery_contract") == 1), "key_findings": state,
                "message": ("Stage 5B 仅交付工程回退模板，具体实验方案尚未生成" if experiment_status == "FALLBACK" else
                            "Stage 5B 附条件实验方案已生成，待定项确认后实施" if conditional else "Stage 5B 实验推荐已完成")}
    except Exception as exc:
        state["experiment"] = {"status": "FAILED", "error": str(exc)}
        save()
        return {"success": False, "error_code": "STAGE5_EXPERIMENT_FAILED", "error": str(exc), "key_findings": state}
