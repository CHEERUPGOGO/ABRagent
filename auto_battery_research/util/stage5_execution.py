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


def finalize_stage5(mgr, design_only=False, client=None):
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
    try:
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        require_current(task_dir, doc)
        old_recipe = recipes.load(task_dir)
        if old_recipe and old_recipe.get("origin") == "engineering_fallback" and not design_only:
            # 通用回退模板不是几何优化的固定约束；保留文件，标记待本轮重新生成。
            doc["experiment_plan_pending"] = doc.pop("experiment_plan_hash", None)
            doc.pop("experiment_contract", None)
            atomic_write(doc_path, json.dumps(doc, ensure_ascii=False, indent=2))
        checker = PINNPhysicsChecker()
        checker.on_init(mgr, {"id": 5}, mgr.config)
        started = time.monotonic()
        identity = digest(doc.get("scheme"))
        try:
            if doc.get("delivery_contract") == 1:
                from auto_battery_research.tools.workflow_actions import _match_pinn_system_for_scheme
                match = _match_pinn_system_for_scheme(doc.get("scheme") or {})
                if not match.get("matched"):
                    raise ValueError("NOT_APPLICABLE：当前体系没有匹配的 PINN；不要求补齐仿真专用参数")
                if (doc.get("scheme", {}).get("geometry") or {}).get("contract_version") != "2.0":
                    raise ValueError("物理输入尚未形成有效结构化契约；不使用训练默认值代替当前设计声明")
            valid, diagnostic = checker.do_check()
            identity = digest(doc.get("scheme"))
            already_failed = prior.get("physics", {}).get("input_hash") == identity and prior.get("physics", {}).get("status") == "FAILED"
            needs_extraction = mgr.get_stage_by_id(5).skip
            if (not valid or needs_extraction) and not design_only and not already_failed:
                run_pinn_simulation(target_query=mgr.target_goal, stage_manager=mgr)
            if not mgr.get_stage_by_id(5).skip and not design_only:
                valid_now, _ = checker.do_check()
                if valid_now:
                    from .pinn_optimization import optimize
                    state["optimization"] = optimize(mgr, client=client if client is not None else _llm_client(mgr))
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
        if doc.get("delivery_contract") == 1:
            readiness = doc["report_readiness"]
            readiness["physics_status"] = state["physics"]["status"]
            if not valid:
                readiness["category"] = "CONDITIONAL" if readiness["category"] != "DIAGNOSTIC" else "DIAGNOSTIC"
                readiness["issues"].append("物理验证未完成：" + str(state["physics"].get("diagnostic")))
            if "NOT_APPLICABLE" in str(state["physics"].get("diagnostic")):
                state["physics"]["status"] = readiness["physics_status"] = "NOT_APPLICABLE"
        started = time.monotonic()
        recipe_doc = copy.deepcopy(doc)
        recipe_doc["evidence"] = retrieve_process_evidence(mgr, doc, task_dir)
        result_path = task_dir / "pinn_simulation_result.json"
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() and valid and state["physics"]["status"] != "SKIPPED" else {}
        recipe_doc["physics_context"] = {"status": state["physics"]["status"], "result": result}
        # 补检索证据属于实验推荐，不改写材料设计或方案表。
        doc["experiment_evidence"] = recipe_doc["evidence"]
        recipe = recipes.generate(task_dir, recipe_doc, client if client is not None else _llm_client(mgr), doc.get("final_answer", "")[:12000])
        doc.update(stage5_contract=2, experiment_contract=2, experiment_plan_hash=recipe["plan_hash"])
        atomic_write(doc_path, json.dumps(doc, ensure_ascii=False, indent=2))
        state["experiment"] = {"status": "PASSED", "plan_hash": recipe["plan_hash"], "duration_seconds": time.monotonic()-started}
        save()
        return {"success": bool(valid or design_only or doc.get("delivery_contract") == 1), "key_findings": state, "message": "Stage 5B 实验推荐已完成"}
    except Exception as exc:
        state["experiment"] = {"status": "FAILED", "error": str(exc)}
        save()
        return {"success": False, "error_code": "STAGE5_EXPERIMENT_FAILED", "error": str(exc), "key_findings": state}
