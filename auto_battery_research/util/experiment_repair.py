"""实验推荐局部校验与补丁；有效参数和有效段落不可重写。"""
import copy
import json
import re

from .scheme_ledger import digest, atomic_write
from .experiment_titles import normalize_step_titles


def normalize_citations(record):
    from .material_ids import canonical_experiment
    record = canonical_experiment(record)
    values = record.get("citations", [])
    if isinstance(values, list):
        record["citations"] = [v.get("passage_id") if isinstance(v, dict) else v for v in values]
    return record


def diagnose(record, doc):
    from .experiment_design import effective_data, overlap_key, TOKEN, SECTIONS, validate, plan_hash
    from .experiment_alignment import issues as alignment_issues
    errors = alignment_issues(record, doc)
    params = record.get("parameters")
    sections = record.get("sections")
    if not isinstance(params, dict) or not isinstance(sections, dict):
        return [{"path": ["parameters" if not isinstance(params, dict) else "sections"], "reason": "实验结构须为对象"}]
    current = effective_data(doc)["parameters"]
    from .research_experiments import diagnose_links, validate_parameter
    errors.extend(diagnose_links(record, doc))
    from .experiment_values import control_value_issues
    bad_groups = {record["groups"][e["path"][1]].get("id")
                  for e in control_value_issues(record, current) if len(e["path"]) == 2
                  and isinstance(record["groups"][e["path"][1]], dict)}
    from .experiment_consistency import parameter_issues, stamp, paragraph_issues
    consistency_errors = parameter_issues(record, doc)
    errors.extend(consistency_errors)
    bad = {e["path"][1] for e in consistency_errors}
    for key, row in params.items():
        reason = None
        from .experiment_values import validate_value
        try:
            validate_value(key, row)
        except (ValueError, TypeError) as exc:
            errors.append({"path": ["parameters", key], "reason": str(exc)})
            bad.add(key)
            continue
        if not isinstance(row, dict):
            reason = "参数须为对象"
        elif row.get("scope") == "control" and row.get("group_id") in bad_groups:
            reason = "参数所属对照变化未通过校验，不能用于操作"
        elif "ref" in row:
            if not isinstance(row["ref"], str) or (current.get(row["ref"]) or {}).get("value") is None:
                reason = "ref必须指向已有非空参数"
        else:
            declared = current.get(overlap_key(key)) or {}
            if declared.get("value") is not None and row.get("scope") != "control":
                from .experiment_alignment import equivalent
                if not equivalent(overlap_key(key), row, declared):
                    reason = "与当前方案冲突，应使用ref引用当前值"
            elif row.get("basis") not in ("engineering_proposal", "budget_assumption") or row.get("value") is None:
                reason = "操作建议须有值并标为engineering_proposal或budget_assumption；文献来源另行保留"
            elif isinstance(row.get("value"), float):
                import math
                if not math.isfinite(row["value"]):
                    reason = "推荐值必须有限"
        if reason:
            errors.append({"path": ["parameters", key], "reason": reason})
            bad.add(key)
        elif isinstance(row, dict):
            try:
                validate_parameter(key, row, doc)
            except ValueError as exc:
                errors.append({"path": ["parameters", key], "reason": str(exc)})
                bad.add(key)
    allowed = {e.get("passage_id") for e in (doc.get("evidence") or []) + (doc.get("experiment_evidence") or [])}
    cites = record.get("citations") or []
    if not isinstance(cites, list) or any(not isinstance(i, str) or i not in allowed for i in cites):
        errors.append({"path": ["citations"], "reason": "citations须为归档passage_id字符串列表"})
    good_params = {k: v for k, v in params.items() if k not in bad}
    for section in SECTIONS:
        paragraphs = sections.get(section)
        if not isinstance(paragraphs, list) or not paragraphs:
            errors.append({"path": ["sections", section], "reason": "缺少本节具体实验步骤"})
            continue
        for n, text in enumerate(paragraphs):
            if isinstance(text, str) and set(TOKEN.findall(text)) & bad:
                continue  # 等待对应参数修复，不扩大正文修改权限。
            probe = copy.deepcopy(record)
            # 一致性组合错误已按真实位置汇总；逐段探测不让其它错误污染当前路径。
            probe.pop("execution_contract", None)
            if probe.get("schema_version") == 5:
                probe["schema_version"] = 4
            probe.update(parameters=good_params, citations=[c for c in cites if isinstance(c, str) and c in allowed] if isinstance(cites, list) else [],
                         sections={s: ["保留当前方案并在小批验证中记录测量口径及实验条件。" * 6] for s in SECTIONS})
            from .experiment_alignment import expand_profiles
            probe["sections"][section] = [expand_profiles(text, record)]
            from .research_experiments import recover_links
            recover_links(probe, doc)
            if "consistency" in record:
                stamp(probe, doc)
            probe["plan_hash"] = plan_hash(probe)
            try:
                bindings = validate(probe, doc)
                from .experiment_protocol import assignment_issues
                conflicts = assignment_issues(text, bindings)
                conflicts += paragraph_issues(record, section, text, bindings)
                if conflicts:
                    raise ValueError("；".join(conflicts) + "；分组变化须单独声明，不能重复改写参数引用")
            except (ValueError, TypeError, KeyError) as exc:
                errors.append({"path": ["sections", section, n], "reason": str(exc)})
                if isinstance(text, str):
                    for missing in set(TOKEN.findall(text)) - set(params):
                        errors.append({"path": ["parameters", missing], "reason": "段落使用的参数尚未声明"})
    return errors


def apply_local(record, patch, errors):
    if not isinstance(patch, dict) or set(patch) != {"base_hash", "changes"} or patch["base_hash"] != digest(record):
        raise ValueError("实验修复必须提交匹配基准的changes补丁，不能重写完整方案")
    allowed = {tuple(e["path"]) for e in errors}
    updated, touched = copy.deepcopy(record), set()
    if not isinstance(patch["changes"], list):
        raise ValueError("changes须为数组")
    for change in patch["changes"]:
        if not isinstance(change, dict) or set(change) != {"path", "value"}:
            raise ValueError("补丁每项仅允许path和value")
        path = tuple(change["path"])
        if path not in allowed or path in touched:
            raise ValueError("实验修复越权或重复路径")
        parent = updated
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = copy.deepcopy(change["value"])
        touched.add(path)
    from .material_ids import canonical_experiment
    updated = canonical_experiment(updated)
    updated.setdefault("warnings", []).extend(normalize_step_titles(updated))
    return updated


def repair_record(record, doc, client, task_dir, fingerprint, *, generation_audit=None):
    from .experiment_design import plan_hash, validate, overlap_key, effective_data
    initial = copy.deepcopy(record)
    audit = copy.deepcopy(generation_audit or {})
    audit.update(input_fingerprint=fingerprint, base_hash=digest(record), errors=[])
    def save():
        atomic_write(task_dir / "experiment_draft.json", json.dumps({"audit": audit, "original": initial, "record": record}, ensure_ascii=False, indent=2))
    save()
    try:
        record = normalize_citations(record)
        from .experiment_values import separate_pending
        record, audit["value_gaps"] = separate_pending(record)
        if "consistency" in record:
            from .experiment_consistency import stamp
            stamp(record, doc)
        record.setdefault("warnings", []).extend(normalize_step_titles(record))
        errors = diagnose(record, doc)
        audit.update(base_hash=digest(record), errors=errors)
    except Exception as exc:
        audit["validation_error"] = str(exc)
        save()
        raise
    save()
    if errors and client is not None and getattr(client, "available", False):
        try:
            audit["repair_attempted"] = True
            save()
            raw = client.chat('仅返回局部JSON补丁 {"base_hash":"输入哈希","changes":[{"path":["parameters","字段路径"],"value":修复后的参数对象}]}。只能替换errors中列出的路径，保留其他所有参数和段落。修复引用、来源属性及缺失值；source用于来源描述，操作建议basis用engineering_proposal，文献出处仍保留。段落路径为["sections","节名",索引]。',
                json.dumps({"base_hash": digest(record), "record": record, "errors": errors,
                    "scheme": doc.get("scheme"), "ledger": doc.get("ledger_view"), "evidence": doc.get("evidence")}, ensure_ascii=False),
                temperature=0.1, json_mode=True, trace_tag="experiment_repair")
            audit["repair_raw_response"] = raw
            save()
            patch = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            updated = apply_local(record, patch, errors)
            if doc.get("requirements"):
                from .research_experiments import recover_links
                valid_links = copy.deepcopy(record)
                recover_links(valid_links, doc)
                for collection, key in (("groups", "id"), ("requirement_tests", "requirement_id")):
                    for row in valid_links[collection]:
                        original_rows = record.get(collection) or []
                        index = next((i for i, original in enumerate(original_rows) if original == row), None)
                        authorized = index is not None and any(e["path"][:2] == [collection, index] for e in errors)
                        if row in original_rows and not authorized and not any(v == row for v in updated.get(collection, [])):
                            raise ValueError("局部修复不得修改已通过的" + collection + "记录：" + row[key])
            record = updated
            if "consistency" in record:
                from .experiment_consistency import stamp
                stamp(record, doc)
                record["plan_hash"] = plan_hash(record)
            audit["patch"] = patch
        except Exception as exc:
            audit["repair_error"] = str(exc)
        save()
    remaining = diagnose(record, doc)
    audit["remaining"] = remaining
    # 修复失败只处理错误单元，保存详细方案中的其他已通过内容。
    current = effective_data(doc)["parameters"]
    def withdraw(key, reason):
        """失效单元移入待定档案；引用它的操作单独保留，不把字符串塞回数值字段。"""
        from .experiment_design import TOKEN
        old = record["parameters"].pop(key, None)
        record.setdefault("pending_parameters", {}).setdefault(key, {"record": old, "reason": reason})
        for section, paragraphs in record["sections"].items():
            for index, paragraph in enumerate(paragraphs):
                if key in TOKEN.findall(paragraph):
                    location = f"{section}:{index}"
                    record.setdefault("pending_sections", {}).setdefault(location, {"text": paragraph, "reason": reason})
                    paragraphs[index] = "本项操作尚未定案：相关参数缺少可用声明，先核对来源或小批验证；原步骤和失败原因保留于待定档案。"
        for test in record.get("requirement_tests", []):
            if any(key in TOKEN.findall(v) for v in test.values() if isinstance(v, str)):
                record.setdefault("pending_tests", []).append(copy.deepcopy(test))
                test.update(status="gap", method="", conditions="", baseline="", criterion="",
                            reason=reason, next_step="先确认本项操作参数，再恢复对应测试安排。")
    pending = remaining
    for _ in range(2):
        for error in pending:
            path = error["path"]
            if path[0] == "parameters" and len(path) == 2:
                key = path[1]
                invalid = record["parameters"].get(key) or {}
                canonical = invalid.get("canonical_path", overlap_key(key))
                if not isinstance(canonical, str):
                    canonical = overlap_key(key)
                declared = current.get(canonical) or {}
                if invalid.get("scope") == "control":
                    # 失效对照不能悄悄退回主组值；仅撤下该参数及依赖它的步骤。
                    withdraw(key, error["reason"])
                elif declared.get("value") is not None:
                    record["parameters"][key] = {"ref": canonical}
                else:
                    withdraw(key, error["reason"])
            elif path[0] == "citations":
                allowed = {e.get("passage_id") for e in (doc.get("evidence") or []) + (doc.get("experiment_evidence") or [])}
                record["citations"] = [i for i in record.get("citations", []) if isinstance(i, str) and i in allowed]
            elif path[0] == "sections" and len(path) >= 2:
                text = "本项操作尚未定案：" + error["reason"] + "。保留当前材料及固定条件，先核对原始文献或开展小批对照后确定本项，不将未确认条件作为已验证工艺。"
                if len(path) == 3:
                    record.setdefault("pending_sections", {}).setdefault(f"{path[1]}:{path[2]}", {
                        "text": record["sections"][path[1]][path[2]], "reason": error["reason"]})
                    record["sections"][path[1]][path[2]] = text
                else:
                    record["sections"][path[1]] = [text]
            elif path[0] in ("groups", "requirement_tests"):
                if path[0] == "requirement_tests" and len(path) == 3:
                    row = record["requirement_tests"][path[1]]
                    record.setdefault("pending_tests", []).append(copy.deepcopy(row))
                    row[path[2]] = "本项安排与主方案尚有冲突，待局部核对后确定。"
                    row.update(status="gap", reason=error["reason"])
                else:
                    from .research_experiments import recover_links
                    recover_links(record, doc, error["reason"])
            elif path[0] == "extensions":
                record["extensions"] = []
        if "consistency" in record:
            from .experiment_consistency import stamp
            stamp(record, doc)
        pending = diagnose(record, doc)
        save()
        if not pending:
            break
        remaining += [e for e in pending if e not in remaining]
    record["warnings"] = list(record.get("warnings") or []) + normalize_step_titles(record) + ["局部待确定：" + str(e) for e in remaining]
    record["repair_status"] = "PARTIAL" if remaining or record.get("pending_parameters") else "REPAIRED"
    audit["remaining"] = remaining
    audit["unresolved"] = pending
    record["plan_hash"] = plan_hash(record)
    try:
        record["bindings"] = validate(record, doc)
    except Exception as exc:
        audit["final_error"] = str(exc)
        raise
    finally:
        save()
    return record
