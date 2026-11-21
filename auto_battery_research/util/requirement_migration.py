"""通用要求重拆：保留科研内容和预算，在副本完成关联及终审后提交。"""
import copy
import json
from datetime import datetime

from .scheme_ledger import atomic_write, digest, require_current, task_lock
from .research_contract import PLANNER_INSTRUCTIONS, attach_requirements, bindings, field_equal, gap_answer
from .requirement_sources import SPLIT_VERSION
from .requirement_structure import HIERARCHY_VERSION, item_bindings


def decode(raw):
    import re
    value = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
    if not isinstance(value, dict):
        raise ValueError("要求重拆响应须为对象")
    return value


def preview_requirements(root, goal):
    """CLI预览只读既有任务和配置；不构造会写状态的StageManager。"""
    from pathlib import Path
    from types import SimpleNamespace
    from .config import ABRConfigLoader
    matches = []
    for path in (Path(root) / "output" / "tasks").glob("*/design_scheme.json"):
        if json.loads(path.read_text(encoding="utf-8")).get("target") == goal:
            matches.append(path.parent)
    if len(matches) != 1:
        return {"success": False, "error": "请使用唯一既有课题的完整原文"}
    mgr = SimpleNamespace(root_dir=Path(root), config=ABRConfigLoader(workspace_root=str(root)).load(goal, persist_snapshot=False), target_goal=goal,
                          get_task_output_dir=lambda: matches[0])
    return reparse_requirements(mgr, dry_run=True)


def validate_mapping(old, new, proposed, raw_items):
    """每个旧要求都有明确去向；绑定和条件不因更名或合并而丢失。"""
    from .research_contract import _id
    id_map = {r["id"]: _id(r["quote"], r["description"], r.get("start", new["source"].index(r["quote"]))) for r in raw_items}
    by_new = {r["id"]: r for r in new["items"]}
    old_ids = {r["id"] for r in old["items"]}
    if not isinstance(proposed, list) or len(proposed) != len(old_ids) or {r.get("old_id") for r in proposed} != old_ids:
        raise ValueError("每个旧要求必须且只能有一个迁移去向")
    result = {}
    for entry in proposed:
        relation = entry.get("relation")
        if relation not in ("equivalent", "merged", "revised", "task", "group", "background") or not entry.get("reason"):
            raise ValueError("迁移关系或解释缺失")
        target = id_map.get(entry.get("new_id"), entry.get("new_id"))
        source = next(r for r in old["items"] if r["id"] == entry["old_id"])
        if relation == "group":
            if item_bindings(source) or source.get("quantity") or source["kind"] not in ("objective", "deliverable"):
                raise ValueError("固定约束或数量目标不能移入上层分组")
            group = next((g for g in new.get("groups", []) if g["id"] == entry.get("group_id")), None)
            if not group or not any(source["quote"] in q for q in group["source_quotes"]):
                raise ValueError("上层分组未保留旧目标原文")
            child_groups = {group["id"]}
            for _ in new.get("groups", []):
                child_groups.update(g["id"] for g in new["groups"] if g.get("parent_id") in child_groups)
            if not any(i.get("group_id") in child_groups for i in new["items"]):
                raise ValueError("上层分组缺少可独立验收的子项")
            target = None
        elif relation == "task":
            if item_bindings(source) or source.get("quantity") or source["kind"] not in ("deliverable", "background"):
                raise ValueError("固定约束或数量目标不能移入总任务")
            if not any(source["quote"] in q for q in new["task"]["source_quotes"]):
                raise ValueError("总任务未保留旧要求原文")
            target = None
        else:
            if target not in by_new:
                raise ValueError("迁移引用未知新要求")
            dest = by_new[target]
            if relation == "background" and (dest["kind"] != "background" or source["kind"] != "background"):
                raise ValueError("独立要求不能降为背景")
            if relation != "background" and dest["kind"] == "background":
                raise ValueError("不能隐藏独立要求到背景")
            destination_bindings = {b["path"]: b for b in item_bindings(dest)}
            for a in item_bindings(source):
                b = destination_bindings.get(a["path"], {})
                if not field_equal(a["path"], a["value"], b.get("value")) or a.get("unit") != b.get("unit"):
                    raise ValueError("重拆不能改变用户固定字段")
            if set(source.get("conditions", [])) - set(dest.get("conditions", [])):
                raise ValueError("迁移丢失原要求条件")
            if source.get("quantity") and any(source["quantity"].get(k) != (dest.get("quantity") or {}).get(k) for k in ("value", "operator")):
                raise ValueError("迁移改变原数值目标或比较关系")
            if source.get("quantity"):
                from .requirement_sources import _unit
                if _unit(source["quantity"]["unit"]) != _unit((dest.get("quantity") or {}).get("unit", "")):
                    raise ValueError("迁移改变目标量纲或单位")
            if relation == "equivalent" and source.get("verification_needed") != dest.get("verification_needed"):
                raise ValueError("等价迁移不能改变验收责任，语义修正须声明revised")
        result[source["id"]] = dict(entry, new_id=target)
    for target in by_new:
        if not any(e.get("new_id") == target for e in result.values()) and by_new[target]["kind"] != "background":
            # 真正遗漏项允许新增，但必须有定位原句并在独立审核中确认。
            if not by_new[target].get("quote"):
                raise ValueError("新要求缺少原文")
    return result


def preserve_mapped_conditions(old, raw, proposed):
    """映射不授予删条件权限；程序原样补回旧条件，新增条件仍须原文校验。"""
    result = copy.deepcopy(raw)
    sources = {r["id"]: r for r in old["items"]}
    destinations = {r["id"]: r for r in result["items"]}
    groups = result.get("groups", [])
    for entry in proposed or []:
        source = sources.get(entry.get("old_id"), {})
        targets = [destinations[entry["new_id"]]] if entry.get("new_id") in destinations else []
        if entry.get("relation") == "group":
            descendants = {entry.get("group_id")}
            for _ in groups:
                descendants.update(g["id"] for g in groups if g.get("parent_id") in descendants)
            targets = [r for r in result["items"] if r.get("group_id") in descendants]
        for target in targets:
            target["conditions"] = list(dict.fromkeys(target.get("conditions", []) + source.get("conditions", [])))
    return result


def strengthen_bindings(ledger, contract):
    """在副本加锁：原值/单位与原文相符才晋升，已有锁不能撤回或改值。"""
    from .scheme_ledger import calculate
    result = copy.deepcopy(ledger)
    parameters = result["parameters"]
    changes = []
    from .parameter_presentation import ALIASES
    for path, field in bindings(contract).items():
        for alias, existing in list(parameters.items()):
            if alias != path and ALIASES.get(alias, alias) == ALIASES.get(path, path):
                if not field_equal(path, existing.get("value"), field["value"]) or existing.get("unit", "") != field.get("unit", ""):
                    raise ValueError("固定条件绑定与既有别名值或单位冲突: " + alias)
                promoted = dict(existing, locked=True, basis="user_requirement", requirement_id=field["requirement_id"],
                                source_quote=field["source_quote"], source=field["source"])
                if existing != promoted:
                    parameters[alias] = promoted
                    changes.append(alias)
        previous = parameters.get(path)
        if previous is not None and (not field_equal(path, previous.get("value"), field["value"]) or previous.get("unit", "") != field.get("unit", "")):
            raise ValueError("固定条件绑定与既有方案值或单位冲突: " + path)
        updated = dict(previous or {}, **field)
        if previous != updated:
            changes.append(path)
            parameters[path] = updated
    if changes:
        result["revision"] += 1
        result.setdefault("history", []).append({"revision": result["revision"], "actor": "user",
            "reason": "按用户授权加强课题固定条件绑定，保持既有值及单位", "changed": changes})
        result["derived"], result["issues"] = calculate(result)
    return result, changes


def remap_research(research, old, new, mapping):
    """合并回答不继承已回答/已验证结论；等价项保留文本，摘要审核指纹失效。"""
    research = copy.deepcopy(research or {})
    answers = {a["requirement_id"]: a for a in research.get("answers", [])}
    result = []
    for item in new["items"]:
        if item["kind"] == "background":
            continue
        sources = [k for k, e in mapping.items() if e.get("new_id") == item["id"]]
        if len(sources) == 1 and mapping[sources[0]]["relation"] == "equivalent" and sources[0] in answers:
            row = copy.deepcopy(answers[sources[0]])
            row["requirement_id"] = item["id"]
            result.append(row)
        else:
            row = gap_answer(item, "要求已按独立验收内容重拆，旧回答不能直接代表合并后的完整回应")
            old_paths = [answers[k].get("next_step", "") for k in sources if k in answers]
            row["next_step"] = "；".join(dict.fromkeys(p for p in old_paths if p)) or row["next_step"]
            # 同义合并保留一份实质回答，全部旧记录另行归档；覆盖与验证结论仍待审核。
            candidates = [answers[k] for k in sources if k in answers and answers[k].get("status") != "gap"
                          and answers[k].get("reason") and answers[k].get("next_step")]
            if sources and all(mapping[k]["relation"] in ("equivalent", "merged", "revised") for k in sources) and candidates:
                row = copy.deepcopy(candidates[0])
                row.update(requirement_id=item["id"], status="partial", verification="unverified")
            row["archived_answers"] = [copy.deepcopy(answers[k]) for k in sources if k in answers]
            result.append(row)
    research.setdefault("requirement_history", []).append({"requirements": copy.deepcopy(old), "answers": copy.deepcopy(list(answers.values()))})
    research["answers"] = result
    for row in research.get("candidates", []) + research.get("arguments", []) + result:
        row.pop("support_review", None)
        row.pop("support_fingerprint", None)
        if "requirement_ids" in row:
            row["requirement_ids"] = list(dict.fromkeys(mapping[i]["new_id"] for i in row["requirement_ids"] if i in mapping and mapping[i]["new_id"]))
    research["arguments"] = [r for r in research.get("arguments", []) if r.get("requirement_ids")]
    research.update(coverage_reviewed=False, status="partial")
    research["record_errors"] = []
    research["diagnostics"] = ["要求已重拆；旧科研审核意见保留，变更回应仍需核验"]
    return research


def remap_tests(record, new, mapping):
    """保留原测试方法、条件与判据；多项合并取保守gap，不拼造可执行结论。"""
    from .research_experiments import required_tests
    record = copy.deepcopy(record)
    old = record.get("requirement_tests", [])
    result = []
    for item in required_tests({"requirements": new}):
        sources = [t for t in old if mapping.get(t.get("requirement_id"), {}).get("new_id") == item["id"]]
        if len(sources) == 1 and mapping[sources[0]["requirement_id"]]["relation"] == "equivalent":
            row = copy.deepcopy(sources[0]); row["requirement_id"] = item["id"]
        else:
            row = {"requirement_id": item["id"], "status": "gap"}
            for key in ("method", "conditions", "baseline", "criterion", "next_step", "reason"):
                row[key] = "；".join(dict.fromkeys(t.get(key, "") for t in sources if t.get(key)))
            row["reason"] = "要求已合并，原方法与条件保留，完整验收安排需重新确认。" + row["reason"]
            row["next_step"] = row["next_step"] or "根据当前完整要求补齐验证方法与条件"
        result.append(row)
    record.setdefault("requirement_test_history", []).append(copy.deepcopy(old))
    record["requirement_tests"] = result
    return record


def remap_tree(value, old, new, mapping):
    """只改结构化关联键；原句、历史轮次和预算保持原样。"""
    if isinstance(value, list):
        return [remap_tree(v, old, new, mapping) for v in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, v in value.items():
        if key == "requirements" and isinstance(v, dict) and v.get("source") == old["source"]:
            result[key] = copy.deepcopy(new)
        elif key == "research" and isinstance(v, dict):
            result[key] = remap_research(v, old, new, mapping)
        elif key == "requirement_id" and isinstance(v, str) and v in mapping:
            result[key] = mapping[v]["new_id"]
        elif key in ("requirement_ids", "affected_requirement_ids", "misread_ids", "reported_misread_ids", "unanswered_ids") and isinstance(v, list):
            result[key] = list(dict.fromkeys(mapping[i]["new_id"] if i in mapping else i for i in v if i not in mapping or mapping[i]["new_id"]))
        elif key == "requirement_review" and isinstance(v, dict):
            result[key] = dict(remap_tree(v, old, new, mapping), source_checked=False)
        elif key == "stopped_gaps" and isinstance(v, dict):
            # 键是旧要求+缺口的签名，完整保留；总预算不能因迁移重开。
            result[key] = copy.deepcopy(v)
        else:
            result[key] = remap_tree(v, old, new, mapping)
    return result


def reparse_requirements(mgr, dry_run=False, client=None, reviewer=None, resume_history=None):
    from .research_recovery import _research_client
    from .research_records import collect_research
    from .material_migration import _preview_manager
    from .experiment_design import plan_hash, validate, input_fingerprint
    from .experiment_consistency import stamp
    from .research_contract import finalize_research
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    task = mgr.get_task_output_dir()
    original = json.loads((task / "design_scheme.json").read_text(encoding="utf-8"))
    initial = {p.name: p.read_bytes() for p in task.iterdir() if p.is_file() and not p.name.endswith(".lock")}
    require_current(task, original)
    old = original.get("requirements")
    if not old or not old.get("items"):
        return {"success": False, "error": "要求重拆需要已有课题契约"}
    if old.get("split_version") == SPLIT_VERSION and old.get("hierarchy_version") == HIERARCHY_VERSION and old.get("status") == "parsed":
        return {"success": True, "changed": False, "requirements": old}
    if resume_history is None:
        client = client or _research_client(mgr, "planner")
        reviewer = reviewer or _research_client(mgr, "reviewer")
        if client is None or reviewer is None or not client.available or not reviewer.available:
            return {"success": False, "error": "要求重拆需要规划和独立审核模型，未修改原任务"}
        for model in (client, reviewer):
            model.single_attempt = True
    plan_path = task / "rag_result.json"
    raw_plan = json.loads(plan_path.read_text(encoding="utf-8")).get("plan", {}) if plan_path.exists() else {}
    queries = raw_plan.get("retrieval_queries", [])
    if resume_history is not None:
        from pathlib import Path
        saved = Path(resume_history)
        if json.loads((saved / "design_scheme.json").read_text(encoding="utf-8")) != original:
            return {"success": False, "error": "已审核迁移方案对应旧任务，拒绝恢复"}
        proposal = json.loads((saved / "proposal.json").read_text(encoding="utf-8"))
        new = proposal["requirements"]
        verdict = proposal["verdict"]
        if verdict.get("approved") is not True or verdict.get("issues"):
            return {"success": False, "error": "历史迁移没有有效独立审核"}
        new = attach_requirements({"requirements": new, "retrieval_queries": queries}, old["source"])["requirements"]
        mapping = validate_mapping(old, new, list(proposal["mapping"].values()), new["items"])
    else:
        try:
            proposal = decode(client.chat(PLANNER_INSTRUCTIONS + '\n额外返回mapping，每个旧要求一项：{"old_id":"旧ID","new_id":"新items的ID或null","relation":"equivalent|merged|revised|task|group|background","group_id":"移入上层时的groups ID，其余省略","reason":"语义依据"}。不得改方案。equivalent要求完整验收责任相同，verification_needed沿用原值；改变验证责任须标revised。merged只用于多个同义旧项归为一个新项。task只容纳总任务动作/对象或失败保留的裸对象片段。group可保存已有上层综合目标，必须有原文和独立子项；不能移走有独立指标/工况/数量的要求。所有固定字段value/unit及条件保持不变；原已知固定条件补齐规范bindings，母体式与改性式分开。保留不同科学指标；循环稳定性等上层目标放groups，子指标独立验收。',
                json.dumps({"goal": old["source"], "old_requirements": old, "queries": queries,
                            "declared_fields": {k: {"value": v.get("value"), "unit": v.get("unit", "")} for k, v in original.get("ledger_view", {}).get("parameters", {}).items()}}, ensure_ascii=False), temperature=.1, trace_tag="requirements_reparse"))
            proposal["requirements"] = preserve_mapped_conditions(old, proposal["requirements"], proposal.get("mapping"))
            new = attach_requirements({"requirements": proposal["requirements"], "retrieval_queries": queries}, old["source"], client)["requirements"]
            if new.get("status") != "parsed" or new.get("split_version") != SPLIT_VERSION or new.get("hierarchy_version") != HIERARCHY_VERSION:
                raise ValueError("新拆分仍未通过原文校验，拒绝迁移")
            mapping = validate_mapping(old, new, proposal.get("mapping"), proposal["requirements"]["items"])
            verdict = decode(reviewer.chat('独立检查要求重拆与原文：总任务不是独立回应，所有独立目标/约束/否定/偏好/明确交付必须保留；仅同一对象、指标、条件和强度的同义要求可合并。检查groups保留上层目标、不同子指标仍分别验收；固定条件须完整bindings，数值/单位与原文一致，母体式不能锁死改性式。检查task是否隐藏遗漏、映射equivalent是否语义和验收责任完全等价。只返回JSON {"approved":true或false,"issues":[]}，不修改配方或回答。',
                json.dumps({"goal": old["source"], "old": old, "new": new, "mapping": mapping}, ensure_ascii=False), temperature=.1, trace_tag="requirements_split_review"))
            if verdict.get("approved") is not True or verdict.get("issues") != []:
                raise ValueError("独立拆分审核未通过：" + str(verdict.get("issues")))
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            return {"success": False, "error": str(exc)}
    try:
        _, lock_changes = strengthen_bindings(original.get("ledger_view") or {"revision": 0, "parameters": {}, "history": []}, new)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    summary = {"success": True, "dry_run": dry_run, "changed": True, "binding_changes": lock_changes, "before": len([i for i in old["items"] if i["kind"] != "background"]),
               "after": len([i for i in new["items"] if i["kind"] != "background"]), "requirements": new, "mapping": mapping}
    if dry_run:
        return summary
    history = task / "requirement_migration_history" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stage = history / "preview"
    stage.mkdir(parents=True)
    if any((task / name).read_bytes() != data for name, data in initial.items()):
        return {"success": False, "error": "拆分规划期间任务已变化，拒绝覆盖"}
    for name, data in initial.items():
        (history / name).write_bytes(data)
        (stage / name).write_bytes(data)
    atomic_write(history / "proposal.json", json.dumps(dict(summary, verdict=verdict), ensure_ascii=False, indent=2))
    try:
        doc = remap_tree(original, old, new, mapping)
        retained_answers = copy.deepcopy(doc.get("research", {}).get("answers", []))
        doc["research"] = collect_research({}, doc, doc.get("ledger_view", {}).get("parameters", {}), previous=doc.get("research"))
        # 程序生成的缺口并非自由摘要；等价条目保留原方法/原因，不因校验空原句而重写模板。
        for index, answer in enumerate(doc["research"].get("answers", [])):
            prior = next((r for r in retained_answers if r["requirement_id"] == answer["requirement_id"]), None)
            if prior and prior.get("status") == "gap" and prior.get("response") == "待补充" and not prior.get("source_quote") and not prior.get("source_quotes"):
                doc["research"]["answers"][index] = prior
            elif prior and prior.get("archived_answers"):
                answer["archived_answers"] = prior["archived_answers"]
        prior_research = remap_research(original.get("research"), old, new, mapping)
        doc["research"]["requirement_history"] = prior_research["requirement_history"]
        doc["requirements_split_review"] = {"approved": True, "fingerprint": digest(new), "verdict": verdict}
        writes = {}
        for name in ("scheme_ledger.json", "rag_result.json", "stage4_repair_checkpoint.json", "stage4_retrieval_checkpoint.json"):
            if name in initial:
                writes[name] = remap_tree(json.loads(initial[name]), old, new, mapping)
        ledger = writes.get("scheme_ledger.json")
        if ledger is not None:
            ledger, lock_changes = strengthen_bindings(ledger, new)
            writes["scheme_ledger.json"] = ledger
            doc.setdefault("scheme_parameters", {}).update(bindings(new))
            doc.update(ledger_view=copy.deepcopy(ledger), ledger_hash=digest(ledger), ledger_revision=ledger["revision"])
            atomic_write(stage / "scheme_ledger.json", json.dumps(ledger, ensure_ascii=False, indent=2))
            from .scheme_ledger import render
            atomic_write(stage / "scheme_table.md", render(ledger))
            for content in writes.values():
                if isinstance(content, dict) and content.get("ledger_hash") == original.get("ledger_hash"):
                    content.update(ledger_hash=doc["ledger_hash"], ledger_view=copy.deepcopy(ledger))
                    if "scheme_parameters" in content:
                        content["scheme_parameters"].update(bindings(new))
        finalize_research(doc)
        doc["research"]["requirement_history"] = prior_research["requirement_history"]
        for name in ("experiment_plan.json", "experiment_draft.json"):
            if name not in initial:
                continue
            record = remap_tests(json.loads(initial[name]), new, mapping)
            record["base_ledger_hash"] = doc.get("ledger_hash")
            stamp(record, doc)
            record["input_fingerprint"] = input_fingerprint(doc, doc.get("final_answer", "")[:12000])
            record["plan_hash"] = plan_hash(record)
            if name == "experiment_plan.json":
                record["bindings"] = validate(record, doc)
            else:
                # 草稿可能尚无generated_stage，保留其待审核状态，不冒充已验收实验。
                record.pop("bindings", None)
            writes[name] = record
            if name == "experiment_plan.json":
                doc["experiment_plan_hash"] = record["plan_hash"]
        if "pinn_input_spec.json" in initial and ledger:
            spec = json.loads(initial["pinn_input_spec.json"])
            spec.update(ledger_hash=doc["ledger_hash"], ledger_revision=doc["ledger_revision"], declared_scheme_parameters=ledger["parameters"])
            writes["pinn_input_spec.json"] = spec
        writes["design_scheme.json"] = doc
        for key in ("scheme", "final_answer", "evidence"):
            if doc.get(key) != original.get(key):
                raise ValueError("要求迁移不得改写科研内容：" + key)
        if ledger:
            before = original.get("ledger_view", {}).get("parameters", {})
            after = ledger["parameters"]
            if any(k not in after or v.get("value") != after[k].get("value") or v.get("unit") != after[k].get("unit")
                   or v.get("locked") and not after[k].get("locked") for k, v in before.items()):
                raise ValueError("要求迁移不得改变已有方案值/单位或削弱锁定")
            if set(after) - set(before) - set(bindings(new)):
                raise ValueError("要求迁移只能新增有原文依据的固定字段")
            from .material_ids import checkpoint_identity
            for name in ("stage4_repair_checkpoint.json", "stage4_retrieval_checkpoint.json"):
                if name in writes:
                    writes[name]["checkpoint_identity"] = checkpoint_identity(doc["target"], ledger, doc.get("provenance") or {})
        doc["requirements_migration"] = {"history": str(history.relative_to(task)), "mapping": mapping, "before_hash": digest(old), "after_hash": digest(new)}
        for name, content in writes.items():
            atomic_write(stage / name, json.dumps(content, ensure_ascii=False, indent=2))
        preview = _preview_manager(mgr, stage)
        require_current(stage, doc)
        output = run_synthesis_report(stage_manager=preview)
        checker = FinalReportChecker(); checker.on_init(preview, {"id": 6}, mgr.config)
        passed, diagnostic = checker.do_check() if output.get("success") else (False, output)
        atomic_write(history / "audit.json", json.dumps(dict(summary, passed=passed, diagnostic=diagnostic), ensure_ascii=False, indent=2))
        if not passed:
            raise ValueError("要求迁移副本未通过报告终审：" + str(diagnostic))
        names = set(writes) | {"final_research_report.md", "experiment_report.json", "scheme_table.md"}
        with task_lock(task):
            if any((task / name).read_bytes() != data for name, data in initial.items()):
                raise ValueError("迁移期间任务已变化，拒绝覆盖")
            try:
                for name in names:
                    if (stage / name).exists():
                        text = (stage / name).read_text(encoding="utf-8").replace(str(stage), str(task)) if name.endswith(".md") else (stage / name).read_text(encoding="utf-8")
                        atomic_write(task / name, text)
                checker.on_init(mgr, {"id": 6}, mgr.config)
                valid, diag = checker.do_check()
                if not valid:
                    raise ValueError(str(diag))
            except Exception:
                for name in names:
                    if name in initial:
                        (task / name).write_bytes(initial[name])
                    else:
                        (task / name).unlink(missing_ok=True)
                raise
        return dict(summary, history=str(history), report_file=str(task / "final_research_report.md"))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        atomic_write(history / "failure.json", json.dumps({"error": str(exc)}, ensure_ascii=False))
        return {"success": False, "error": str(exc), "history": str(history)}
