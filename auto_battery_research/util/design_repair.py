"""可信补丁合并器：审核只能建议，程序签发局部写权限。"""
import copy
import difflib
import json
import re

from .scheme_ledger import digest, validate_field
from . import design_sections as sections
from .design_sections import design_sections

from .runtime_budgets import bind_repair_options, checkpoint_repair_options, repair_options


def review_verdict(text, review, requirements=None):
    """区分审核服务失败、带保留意见的有效审核和无保留通过。"""
    classified = sections.classify_design_output(text, requirements)
    completed = not classified["blocking_issues"] and review.get("error_type") not in ("reviewer", "structure")
    passed = (completed and not classified["format_issues"] and not review.get("issues")
              and review.get("confidence") in ("high", "medium"))
    return completed, passed


def blocks(text):
    # 分隔符原样保留，未授权段落逐字不变；表格以行作为最小写入单位。
    parts = re.split(r"(\n\s*\n|(?<=\|)\n(?=\|))", text)
    return parts, {f"b{i}": value for i, value in enumerate(parts) if i % 2 == 0 and value.strip()}


def review_issues(review, text):
    """旧审核可显示但没有精确定位就不授予修改权限。"""
    structured = review.get("repair_issues") or []
    result = []
    for i, item in enumerate(structured):
        if not isinstance(item, dict):
            continue
        item = copy.deepcopy(item)
        item["id"] = f"issue-{i}"
        result.append(item)
    return result


def issue_quotes(issue):
    """跨段问题必须由审核明确提供每处精确原句，不按排名或关键词扩大权限。"""
    quotes = issue.get("quotes", [issue.get("quote")])
    if not isinstance(quotes, list) or not quotes or any(not isinstance(q, str) or not q for q in quotes):
        return []
    return list(dict.fromkeys(quotes))


def authorize(text, issues, parameters, research_doc=None):
    from .numeric_bindings import occurrences, dependencies, binding_errors
    _, units = blocks(text)
    grants = {}
    for issue in issues:
        if issue.get("source_only"):
            if issue.get("parameter_source_paths"):
                from .source_metadata import source_permissions
                sources = source_permissions(issue, text, parameters)
                if sources:
                    grants[issue["id"]] = {"blocks": [], "fields": [], "source_fields": sources, "parameter_source_only": True}
                continue
            from .research_source_metadata import permissions
            sources = permissions(issue, research_doc)
            if sources:
                grants[issue["id"]] = {"blocks": [], "fields": [], "source_fields": {}, "research_sources": sources}
            continue
        quotes = issue_quotes(issue)
        located_quotes = [[bid for bid, value in units.items() if value.count(quote) == 1] for quote in quotes]
        if (not quotes or any(len(matches) != 1 or text.count(quote) != 1 for quote, matches in zip(quotes, located_quotes))
                or not issue.get("reason") or not issue.get("suggestion")):
            continue
        matches = list(dict.fromkeys(found[0] for found in located_quotes))
        fields = []
        for key in issue.get("parameter_paths", []):
            field = parameters.get(key)
            from .report_narrative import GROUPS, NAMES
            comp, _, name = key.partition(".")
            local = "\n".join(units[bid] for bid in matches)
            located = key in local or (GROUPS.get(comp, key) in local and NAMES.get(name, key) in local and str((field or {}).get("value")) in local)
            if field and located and not field.get("locked") and key != "requirements.original_request":
                fields.append(key)
        targets = list(matches)
        # 仅明确标注字段路径的直接依赖可自动追加，不能按宽泛关键词扩大权限。
        for bid, value in units.items():
            if any(f"`{key}`" in value for key in fields) and bid not in targets:
                targets.append(bid)
            if not binding_errors(value, parameters) and any(
                    set(fields) & dependencies(kind, key, parameters) for _, _, kind, key, _ in occurrences(value)) and bid not in targets:
                targets.append(bid)
        from .source_metadata import parameter_permissions
        grants[issue["id"]] = {"blocks": targets, "fields": fields,
                               "source_fields": parameter_permissions(text, parameters, targets)}
    return {"base_hash": digest(text), "parameters_hash": digest(parameters), "grants": grants}


def _protected_value_changed(before, after, value):
    """保护已有文字声明；数值重复不用于推断改值，由参数和引用校验负责。"""
    if not isinstance(value, str) or not value:
        return False
    # 文字多提一次不等于改值；旧出现位置须全部原样保留。
    unchanged = difflib.SequenceMatcher(None, before, after, autojunk=False).get_matching_blocks()
    return any(not any(block.a <= m.start() and m.end() <= block.a + block.size for block in unchanged)
               for m in re.finditer(re.escape(value), before))


def apply_patch(text, parameters, permit, patch):
    if not isinstance(patch, dict):
        raise ValueError("PATCH_FORMAT_INVALID: 补丁必须是对象")
    if set(patch) == {"format"} and isinstance(patch["format"], dict):
        patch = patch["format"]
    if "base_hash" not in patch:
        raise ValueError("PATCH_FORMAT_INVALID: 缺少 base_hash")
    if patch.get("base_hash") != digest(text) or permit["base_hash"] != digest(text) or permit["parameters_hash"] != digest(parameters):
        raise ValueError("补丁基准版本已过期")
    if "parameters_hash" in patch and patch["parameters_hash"] != permit["parameters_hash"]:
        raise ValueError("参数基准版本已过期")
    if set(patch) - {"base_hash", "parameters_hash", "edits", "parameters", "sources", "append_sections"}:
        raise ValueError("补丁含未授权顶层字段，禁止全文替换")
    parts, units = blocks(text)
    updated, touched = copy.deepcopy(parameters), set()
    for edit in patch.get("edits", []):
        grant = permit["grants"].get(edit.get("issue_id"), {})
        bid = edit.get("block_id")
        if bid not in grant.get("blocks", []) or bid not in units or bid in touched:
            raise ValueError("段落越权、重复或不存在")
        value = edit.get("text")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("禁止删除段落")
        for key, field in parameters.items():
            from .report_narrative import GROUPS, NAMES
            comp, _, leaf = key.partition(".")
            bound = key in units[bid] or (GROUPS.get(comp, key) in units[bid] and NAMES.get(leaf, key) in units[bid])
            if (field.get("locked") or bound and key not in grant.get("fields", [])) and field.get("value") is not None:
                if _protected_value_changed(units[bid], value, field["value"]):
                    raise ValueError(f"正文不得改变锁定或未授权参数值: {key}")
        from .design_structure import protected_structure
        if protected_structure(value) != protected_structure(units[bid]):
            raise ValueError("内容修复不能修改章节标题")
        old_ids = set(re.findall(r"\[([\w-]+)\]", units[bid]))
        new_ids = set(re.findall(r"\[([\w-]+)\]", value))
        if not new_ids <= old_ids | set(permit.get("evidence_ids", [])):
            raise ValueError("补丁引用未归档证据")
        parts[int(bid[1:])] = value
        touched.add(bid)
    for change in patch.get("parameters", []):
        key = change.get("path")
        grant = permit["grants"].get(change.get("issue_id"), {})
        if key not in grant.get("fields", []) or parameters.get(key, {}).get("locked"):
            raise ValueError("参数越权或用户锁定")
        field = change.get("field")
        if isinstance(field, dict) and set(field) - {"value", "unit", "basis", "source", "source_quote", "passage_id", "meaning", "conditions", "evidence"}:
            raise ValueError("参数补丁不能修改锁定或权限元数据")
        validate_field(key, field)
        if field.get("basis") not in ("engineering_proposal", "literature"):
            raise ValueError("参数来源非法")
        if field.get("basis") == "literature" and field.get("passage_id") not in permit.get("evidence_ids", []):
            raise ValueError("参数缺少文献证据")
        from .material_ids import canonical_field
        # 数值身份、角色和锁定元数据不能由正文补丁重定义。
        updated[key] = canonical_field(key, dict(parameters.get(key, {}), **field))
        if parameters.get(key, {}).get("reference_origin") == "writer_v2":
            from .numeric_consistency import identity as quantity_identity
            if quantity_identity(key, parameters[key]) != quantity_identity(key, updated[key]):
                raise ValueError("数值补丁不能改变对象、工况或计量身份")
    missing = design_sections(text)[2]
    additions = []
    for item in patch.get("append_sections", []):
        name = item.get("name")
        content = item.get("text", "")
        if name not in missing or name not in permit.get("missing_sections", []) or name in additions:
            raise ValueError("新增章节越权")
        if len(content.strip()) < 30 or re.search(r"^#", content, re.M):
            raise ValueError("缺失章节须提供实质正文，不能夹带其他标题")
        parts.append(f"\n\n## {name}\n\n{content}")
        additions.append(name)
    merged = "".join(parts)
    from .numeric_bindings import occurrences, synchronize
    if occurrences(text):
        merged = synchronize(text, merged, parameters, updated)
    from .source_metadata import apply_parameter_sources, changed_parameter_fields
    apply_parameter_sources(merged, parameters, updated, permit["grants"], patch.get("sources", []))
    from .numeric_consistency import audit as numeric_audit
    modified = changed_parameter_fields(parameters, updated)
    old_errors = {(i["id"], i["message"]) for i in numeric_audit(parameters, text)["issues"]}
    new_errors = [i for i in numeric_audit(updated, merged)["issues"]
                  if set(i["paths"]) & modified or (i["id"], i["message"]) not in old_errors]
    if new_errors:
        raise ValueError("补丁数值一致性未通过：" + "；".join(i["message"] for i in new_errors))
    from .design_structure import structure_issues
    if structure_issues(merged):
        raise ValueError("正文补丁破坏程序章节结构：" + "；".join(structure_issues(merged)))
    old_ids = set(re.findall(r"\[([\w-]+)\]", text))
    if not set(re.findall(r"\[([\w-]+)\]", merged)) <= old_ids | set(permit.get("evidence_ids", [])):
        raise ValueError("新增章节引用未归档证据")
    for key, field in updated.items():
        if field != parameters.get(key) and (not field.get("source_quote") or field["source_quote"] not in merged):
            raise ValueError("参数缺少补丁正文中的原始声明")
    return merged, updated


def scheme_with_fields(scheme, fields):
    from .material_ids import assemble_components
    scheme = assemble_components(scheme, fields)
    for key, field in fields.items():
        comp, _, name = key.partition(".")
        value = field.get("value")
        if name in ("L_um", "porosity", "R_s_um", "D_s_m2s") and comp in ("cathode", "anode"):
            scheme.setdefault("geometry", {}).setdefault(comp, {})[name] = value
        elif comp == "condition":
            scheme.setdefault("geometry", {}).setdefault("condition", {})[name] = value
    return scheme


def repair(writer, text, review, evidence, parameters, read_only_context="", verifier=None, geometry_validator=None, evidence_selection=None, research_doc=None):
    issues = review_issues(review, text)
    original_issue_count = len(issues)
    from .research_source_metadata import route_issues
    issues = route_issues(issues, research_doc, text, parameters, excluded=review.get("deferred_research_sources", []))
    from .source_metadata import route_source_issues
    issues = route_source_issues(issues, text, parameters, excluded=review.get("deferred_parameter_sources", []))
    permit = authorize(text, issues, parameters, research_doc)
    permit["missing_sections"] = design_sections(text)[2]
    permit["evidence_ids"] = [e.get("passage_id") for e in evidence]
    if issues:
        from .repair_interfaces import execute
        changed, fields, entry = execute(writer, verifier or writer.llm, text, parameters, permit, issues, evidence,
                                        read_only_context, geometry_validator, review.get("repair_feedback"), evidence_selection, research_doc)
        entry["unmapped_summaries"] = list(review.get("issues", []))[original_issue_count:]
        return changed, fields, entry
    if not permit["grants"] and not permit["missing_sections"]:
        return text, parameters, {"status": "SCOPE_UNRESOLVED", "issues": issues}
    from .adaptive_retrieval import evidence_package
    package, selection = (evidence, evidence_selection) if evidence_selection is not None else evidence_package(evidence, text=text, issues=issues)
    prompt = {"read_only_context": read_only_context, "draft_blocks": blocks(text)[1], "permission": permit, "issues": issues,
              "parameters": parameters, "evidence": package, "evidence_context": selection,
              "return_object_example_do_not_wrap": {"base_hash": permit["base_hash"], "edits": [{"issue_id": "issue-0", "block_id": "b0", "text": "替换段落"}],
                         "parameters": [], "append_sections": []}}
    attempts = repair_options(getattr(writer, "repair_options", None))["patch_attempts"]
    if not attempts:
        return text, parameters, {"status": "REJECTED", "reason": "补丁生成预算为0", "permission": permit}
    for attempt in range(attempts):
        try:
            raw = writer.llm.chat("直接返回JSON补丁对象本身，不要format包装。禁止全文重写。参数修改格式为 {issue_id,path,field}；缺失章节格式为 {name,text}。", json.dumps(prompt, ensure_ascii=False), temperature=0.1, json_mode=True, trace_tag="writer_patch")
            patch = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            merged, fields = apply_patch(text, parameters, permit, patch)
            return merged, fields, {"status": "APPLIED" if merged != text or fields != parameters else "NO_CHANGE", "permission": permit, "patch": patch, "before": digest(text), "after": digest(merged)}
        except Exception as exc:
            protocol_error = isinstance(exc, json.JSONDecodeError) or "PATCH_FORMAT_INVALID" in str(exc)
            if attempt + 1 < attempts and protocol_error:
                prompt["format_error"] = str(exc)
                continue
            return text, parameters, {"status": "REJECTED", "reason": str(exc), "error_type": "protocol" if protocol_error else "validation", "permission": permit}


def pending_structure_repair(seed, *, budget=None):
    """旧结构检查点可恢复格式修补；不要求尚有科学正文修复预算。"""
    from .writer_protocol import completion_recovery_available, syntax_recovery_available
    from .writer_titles import recovery_available as title_recovery_available
    limits = checkpoint_repair_options(seed, budget)
    if (completion_recovery_available(seed) or syntax_recovery_available(seed)
            or title_recovery_available(seed, budget=limits)):
        return True
    review = seed.get("reviewer_output") or {}
    if (review.get("review_status") != "STRUCTURE_INVALID" or seed.get("protocol_exhausted")
            or seed.get("review_rounds", 0) >= limits["review_rounds"]
            or seed.get("review_service_failures", 0) >= limits["review_service_failures"]):
        return False
    classified = sections.classify_design_output(seed.get("draft") or "", (seed.get("plan") or {}).get("requirements"))
    signature = digest({"text": seed.get("draft") or "", "parameters": seed.get("parameters") or {}})
    return (bool(classified["format_issues"]) and seed.get("format_repair_rounds", 0) < limits["format_rounds"]
            and signature not in seed.get("format_attempted_hashes", []))


def structure_recovery_available(task_dir, doc, seed, *, budget=None):
    """生成器与门禁共用恢复条件，已发布的下游产物通过显式恢复入口同步。"""
    return (not doc.get("experiment_contract")
            and not (task_dir / "experiment_plan.json").exists()
            and not (task_dir / "final_research_report.md").exists()
            and pending_structure_repair(seed, budget=budget))


def _repair_bold_marker(text, parameters, issue):
    """按现有段落授权增删两字符标记，保留文字、参数、引用及前后指纹。"""
    item = dict(issue, id="structure-bold")
    permit = authorize(text, [item], parameters)
    grant = permit["grants"].get(item["id"])
    if not grant or len(grant["blocks"]) != 1:
        return text, parameters, {"status": "SCOPE_UNRESOLVED", "issues": [item]}
    block_id = grant["blocks"][0]
    block = blocks(text)[1][block_id]
    if "marker_offset" in item:
        offset = block.index(item["quote"]) + item["marker_offset"]
        replacement = block[:offset] + block[offset + 2:]
    elif "insert_offset" in item:
        offset = block.index(item["quote"]) + item["insert_offset"]
        replacement = block[:offset] + "**" + block[offset:]
    else:
        return text, parameters, {"status": "SCOPE_UNRESOLVED", "issues": [item]}
    patch = {"base_hash": permit["base_hash"], "edits": [{"block_id": block_id, "issue_id": item["id"], "text": replacement}]}
    try:
        changed, fields = apply_patch(text, parameters, permit, patch)
    except ValueError as exc:
        return text, parameters, {"status": "REJECTED", "issues": [item], "reason": str(exc)}
    return changed, fields, {"status": "APPLIED", "kind": "markdown_marker", "issues": [item],
                             "before": digest(text), "after": digest(changed), "permission": permit, "patch": patch}


def review_design(pipeline, seed, question, history_context="", top_k=50):
    """正文、格式和审核按YAML预算独立计数，恢复不重置已用次数。"""
    limits = bind_repair_options(pipeline, seed)
    service_limit = limits["review_service_failures" if getattr(pipeline, "report_first", False)
                           else "strict_review_service_failures"]
    from .scheme_ledger import atomic_write, flatten_scheme, canonical_unit
    question = seed.setdefault("review_context", question)
    requirements = (seed.get("plan") or {}).get("requirements")
    pipeline.reviewer.requirements = requirements
    if seed.get("research_draft"):
        pipeline.reviewer.research_draft = seed["research_draft"]
    # 渲染必须早于参数/研究记录提取；恢复审核绝不重新编号或改名。
    text = seed["draft"]
    evidence = seed["evidence"]
    parameters = copy.deepcopy(seed.get("parameters") or (getattr(pipeline, "scheme_context", None) or {}).get("parameters", {}))
    from .adaptive_retrieval import init_state, select_context, after_review, options as retrieval_options
    retrieval_state = init_state(seed)
    for key, value in flatten_scheme(seed.get("scheme") or {}).items():
        parameters.setdefault(key, {"value": value, "unit": canonical_unit(key) or "", "basis": "engineering_proposal", "source": "初始设计"})
    audit = list(seed.get("repair_audit", []))
    review = seed.get("reviewer_output") or {"issues": ["尚未审核"], "error_type": "reviewer", "confidence": "low"}
    if not text.strip():
        reason = (seed.get("writer_output") or {}).get("error") or "Writer 未能生成有效方案草稿"
        review = {"issues": [reason], "confidence": "low", "error_type": "structure", "review_status": "STRUCTURE_INVALID"}
        seed.update(draft=text, reviewer_output=review, parameters=parameters, repair_audit=audit)
        path = getattr(pipeline, "repair_checkpoint_path", None)
        if path:
            atomic_write(path, json.dumps(seed, ensure_ascii=False, indent=2))
            path.with_name("stage4_rebuild_requested.json").unlink(missing_ok=True)
        return text, review, evidence, parameters, audit
    path = getattr(pipeline, "repair_checkpoint_path", None)
    # 历史格式错误可离线重放；仍使用当时程序签发的权限，不能扩大范围。
    if path and not seed.get("protocol_replayed") and audit:
        traces = sorted(path.parent.glob("llm_trace/trace_*.jsonl"), key=lambda p: p.stat().st_mtime)
        if traces:
            try:
                records = [json.loads(line) for line in traces[-1].read_text(encoding="utf-8").splitlines()]
                outputs = [r.get("output", {}).get("text", "") for r in records if r.get("trace_tag") == "writer_patch"]
                for entry, raw in zip(audit, outputs):
                    if entry.get("status") != "REJECTED" or not entry.get("permission"):
                        continue
                    try:
                        patch = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
                        text, parameters = apply_patch(text, parameters, entry["permission"], patch)
                        entry.update(status="REPLAYED", patch=patch, after=digest(text))
                        review = dict(review, error_type="reviewer", issues=list(review.get("issues", [])) + ["格式兼容重放后的修改尚未完成复核"])
                    except (ValueError, TypeError, KeyError):
                        continue
            except (OSError, ValueError, TypeError):
                pass
        seed["protocol_replayed"] = True
    def save():
        nonlocal text
        from .numeric_bindings import normalize_material_references
        text, normalized = normalize_material_references(text, parameters)
        parameters.update(normalized)
        seed.update(draft=text, evidence=evidence, parameters=parameters, repair_audit=audit, reviewer_output=review,
                    scheme=scheme_with_fields(seed.get("scheme"), parameters))
        if seed.get("numeric_contract"):
            from .numeric_consistency import audit as numeric_audit
            seed["numeric_consistency"] = numeric_audit(parameters, text)
        if seed.get("research_draft"):
            pipeline.reviewer.research_draft = seed["research_draft"]
        candidate = seed.get("geometry_candidate")
        if isinstance(candidate, dict):
            for key, field in parameters.items():
                if not field.get("source_quote"):
                    continue
                comp, _, name = key.partition(".")
                if comp in ("cathode", "anode") and name in ("L_um", "porosity", "R_s_um", "D_s_m2s"):
                    candidate.setdefault(comp, {})[name] = field["value"]
                elif comp == "condition" and name in ("v_min", "v_max", "c_rate"):
                    candidate.setdefault("condition", {})[name] = field["value"]
        if path:
            atomic_write(path, json.dumps(seed, ensure_ascii=False, indent=2))
            path.with_name("stage4_rebuild_requested.json").unlink(missing_ok=True)
        pipeline.reviewer.retrieval_context = {"window_open": retrieval_state["window_open"],
            "initial_strategy": retrieval_state["initial_strategy"], "rounds": retrieval_state["rounds"],
            "archive_manifest": [{"passage_id": e.get("passage_id"), "source": e.get("source"), "preview": e.get("text", "")[:300]} for e in evidence]}
    # 历史正文计数原样保留，不推测退还旧预算；新格式计数独立且恢复不重置。
    seed.setdefault("format_repair_rounds", 0)
    def classify_current():
        from .design_structure import structure_issues
        classified = sections.classify_design_output(text, requirements)
        classified["blocking_issues"] = list(dict.fromkeys(classified["blocking_issues"]
            + structure_issues(text, seed.get("design_structure"))))
        return classified
    def repair_local_format():
        nonlocal text
        classified = classify_current()
        signature = digest({"text": text, "parameters": parameters})
        attempted = seed.setdefault("format_attempted_hashes", [])
        if (classified["format_issues"] and seed["format_repair_rounds"] < limits["format_rounds"]
                and signature not in attempted):
            seed["format_repair_rounds"] += 1
            attempted.append(signature)
            save()  # 在写入前占用机会，中断后不重置。
            changed, fields, entry = _repair_bold_marker(text, parameters, classified["format_issues"][0])
            entry["budget"] = "format"
            if entry["status"] == "APPLIED":
                if (fields != parameters or changed.replace("**", "") != text.replace("**", "")
                        or sections.classify_design_output(changed, requirements)["format_issues"]
                        or sections.BOLD_ERROR in sections.design_output_issues(changed, requirements)):
                    entry.update(status="REJECTED", reason="格式修复只能增删标记并通过标记复检")
                else:
                    text = changed
                    seed.pop("pending_review", None)
                    seed.pop("reviewed_hash", None)
            audit.append(entry)
            save()
        return classify_current()

    def retain_format_diagnostics(verdict):
        # 格式未修复时仍记录科学审核结果，但不能把正文标为无保留通过。
        verdict = dict(verdict)
        old = verdict.get("format_review_issues", [])
        local = sections.classify_design_output(text, requirements)["format_issues"]
        messages = [f"{sections.FORMAT_ERROR}（第{i['line']}行），格式修复未完成，科学审核独立记录。" for i in local]
        verdict["format_issues"] = local
        verdict["format_review_issues"] = messages
        verdict["issues"] = list(dict.fromkeys([s for s in verdict.get("issues", []) if s not in old] + messages))
        if messages and verdict.get("error_type") not in ("structure", "reviewer"):
            verdict["review_status"] = "REVIEWED_WITH_ISSUES"
        return verdict

    def retain_numeric_diagnostics(verdict):
        if not seed.get("numeric_contract"):
            return verdict
        from .numeric_consistency import audit as numeric_audit, messages, repair_issues as numeric_repair_issues
        from .numeric_bindings import binding_errors
        snapshot = numeric_audit(parameters, text)
        seed["numeric_consistency"] = snapshot
        pipeline.reviewer.numeric_consistency = snapshot
        verdict = dict(verdict)
        previous = verdict.get("numeric_review_issues", [])
        current = messages(snapshot) + binding_errors(text, parameters)
        verdict["issues"] = list(dict.fromkeys([i for i in verdict.get("issues", []) if i not in previous] + current))
        verdict["numeric_review_issues"] = current
        verdict["repair_issues"] = [r for r in verdict.get("repair_issues", []) if not r.get("numeric_id")] + numeric_repair_issues(snapshot, text)
        return verdict

    save()
    repair_local_format()
    # 持久化预算，工具重复调用和进程重启均不能重置。
    # 审核、检索和正文修复分别计数；没有修补正文的检索不消耗正文预算。
    while ((seed.get("repair_rounds", 0) < limits["content_rounds"] or seed.get("review_rounds", 0) == 0)
           and seed.get("review_rounds", 0) < limits["review_rounds"]
           and not seed.get("protocol_exhausted")
           and seed.get("review_service_failures", 0) < service_limit):
        seed["review_rounds"] = seed.get("review_rounds", 0) + 1
        save()
        errors = repair_local_format()["blocking_issues"]
        if seed.get("numeric_contract") and seed.get("pending_review_hash") != digest(text):
            seed.pop("pending_review", None)
        if seed.get("pending_review") and not errors:
            review = seed.pop("pending_review")
        elif errors:
            review = {"issues": errors, "repair_issues": [], "confidence": "low", "error_type": "structure", "review_status": "STRUCTURE_INVALID"}
        else:
            from .research_support import refresh_for_review, finish_review
            refresh_for_review(pipeline, seed, text, parameters, evidence, repair_records=False)
            save()
            package = select_context(seed, purpose="reviewer", text=text, issues=review.get("repair_issues", []), config=retrieval_options(pipeline))
            review = retain_numeric_diagnostics(review)
            review = pipeline.reviewer.run(question=question, evidence=package, draft_answer=text,
                history_context=history_context, scheme=seed.get("scheme"), claimed_energy=pipeline._extract_energy_claim(text))
            finish_review(pipeline, seed, review, parameters, evidence, package)
            review["review_status"] = "SERVICE_ERROR" if review.get("error_type") == "reviewer" else ("REVIEWED_WITH_ISSUES" if review.get("issues") else "APPROVED")
            seed["reviewed_hash"] = digest(text)
        review = retain_numeric_diagnostics(retain_format_diagnostics(review))
        save()
        if review.get("error_type") == "reviewer":
            seed["review_service_failures"] = seed.get("review_service_failures", 0) + 1
            save()
            if seed["review_service_failures"] >= service_limit:
                break
            continue
        retrieval_improved = not errors and after_review(pipeline, seed, review, save)
        evidence = seed["evidence"]
        from .research_source_metadata import route_issues
        routed = route_issues(review_issues(review, text), seed.get("research_draft"), text, parameters)
        from .source_metadata import route_source_issues
        routed = route_source_issues(routed, text, parameters)
        if any(i.get("source_only") for i in routed):
            review = dict(review, repair_issues=routed,
                issues=list(dict.fromkeys(review.get("issues", []) + [i["reason"] for i in routed if i.get("source_only")])))
        if not review.get("issues"):
            if retrieval_improved:
                continue  # 新证据须先重新审核，不把取回证据冒充正文已修复。
            break
        if seed.get("repair_rounds", 0) >= limits["content_rounds"]:
            break
        if errors and (sections.BOLD_ERROR in errors or sections.FENCE_ERROR in errors
                       or not (sections.design_sections(text)[2] and (requirements or {}).get("scheme_required", True))):
            audit.append({"status": "SCOPE_UNRESOLVED", "issues": errors, "reason": "结构问题缺少可安全修改的唯一原文范围"})
            save()
            break
        from .repair_diagnosis import diagnose, issue_key
        deferred_notes = []
        cases = diagnose(getattr(pipeline.reviewer, "llm", None), text, parameters,
                         review_issues(review, text), audit, seed, save,
                         select_context(seed, purpose="repair_case_review", text=text,
                                        issues=review.get("repair_issues", []), config=retrieval_options(pipeline)), question)
        if cases:
            review = dict(review, repair_feedback=list(review.get("repair_feedback", [])) + [{"case_review": cases}])
            rechecks = [c for c in cases if c["action"] == "recheck"]
            signature = digest(rechecks) if rechecks else None
            if signature and signature not in seed.setdefault("case_rechecks", []) and seed.get("review_rounds", 0) < limits["review_rounds"]:
                seed["case_rechecks"].append(signature)
                seed["review_rounds"] += 1
                seed.pop("pending_review", None)
                save()
                from .research_support import finish_review
                package = select_context(seed, purpose="case_recheck", text=text,
                                         issues=review.get("repair_issues", []), config=retrieval_options(pipeline))
                review = pipeline.reviewer.run(question=question, evidence=package,
                    draft_answer=text, history_context=history_context + "\n连续失败独立审查（仅供重新核验，不能视为已结案）：\n"
                    + json.dumps(rechecks, ensure_ascii=False), scheme=seed.get("scheme"), claimed_energy=pipeline._extract_energy_claim(text))
                finish_review(pipeline, seed, review, parameters, evidence, package)
                review = retain_numeric_diagnostics(retain_format_diagnostics(review))
                seed["reviewed_hash"] = digest(text)
                save()
                if review.get("error_type") in ("reviewer", "structure"):
                    break
                if not review.get("issues"):
                    break
            deferred = {c["issue_key"] for c in cases if c["action"] == "defer"}
            if deferred:
                deferred_notes = ["连续失败审查保留：" + c["reason"] + "；" + c["suggestion"]
                                  for c in cases if c["action"] == "defer"]
                deferred_sources = [(r["collection"], r["id"]) for i in review.get("repair_issues", [])
                                    if issue_key(i) in deferred for r in i.get("research_sources", [])]
                review["deferred_research_sources"] = list(dict.fromkeys(
                    [tuple(r) for r in review.get("deferred_research_sources", [])] + deferred_sources))
                review["deferred_parameter_sources"] = list(dict.fromkeys(review.get("deferred_parameter_sources", [])
                    + [p for i in review.get("repair_issues", []) if issue_key(i) in deferred for p in i.get("parameter_source_paths", [])]))
                review = dict(review, repair_issues=[i for i in review.get("repair_issues", []) if issue_key(i) not in deferred])
                if not review["repair_issues"]:
                    save()
                    break  # 问题仍在issues中披露，不用不适用的补丁接口反复尝试。
        seed["repair_rounds"] = seed.get("repair_rounds", 0) + 1
        save()
        package = select_context(seed, purpose="repair", text=text, issues=review.get("repair_issues", []), config=retrieval_options(pipeline))
        changed, fields, entry = repair(pipeline.writer, text, review, package, parameters, question,
            verifier=getattr(pipeline.reviewer, "llm", None),
            geometry_validator=lambda fields: validate_repair_geometry(fields, parameters, seed, evidence),
            evidence_selection=retrieval_state["context_history"][-1], research_doc=seed.get("research_draft"))
        if deferred_notes:
            entry["unmapped_summaries"] = list(dict.fromkeys(entry.get("unmapped_summaries", []) + deferred_notes))
        from .source_metadata import changed_parameter_fields
        changed_inputs = changed_parameter_fields(parameters, fields)
        if entry["status"] == "APPLIED" and changed_inputs:
            try:
                from auto_battery_research.simulation.geometry_contract import build_geometry_contract, validate_geometry_contract
                candidate = copy.deepcopy(seed.get("geometry_candidate"))
                next_scheme = scheme_with_fields(seed.get("scheme"), fields)
                for key, field in fields.items():
                    if key not in changed_inputs:
                        continue
                    comp, _, name = key.partition(".")
                    if name == "material":
                        raise ValueError("材料变更涉及关联物性，须先扩大关联字段授权或显式重新设计")
                    if isinstance(candidate, dict) and comp in ("cathode", "anode") and name in ("L_um", "porosity", "R_s_um", "D_s_m2s"):
                        candidate.setdefault(comp, {})[name] = field["value"]
                        if name == "D_s_m2s":
                            candidate.setdefault("diffusion_evidence", {})[key] = field.get("evidence")
                    elif isinstance(candidate, dict) and comp == "condition":
                        candidate.setdefault("condition", {})[name] = field["value"]
                if isinstance(candidate, dict):
                    geo = build_geometry_contract(candidate, next_scheme, evidence)
                    violations = validate_geometry_contract(geo, next_scheme, evidence)
                    if violations:
                        raise ValueError("; ".join(violations))
                    seed["geometry_candidate"] = candidate
                    next_scheme["geometry"] = geo
                    seed["scheme"] = next_scheme
            except (ValueError, TypeError, KeyError) as exc:
                entry.update(status="REJECTED", reason=str(exc))
                entry.pop("remaining_issues", None)
                entry["resolved_issue_ids"] = []
                for transaction in entry.get("transactions", {}).values():
                    if transaction.get("status") == "COMMITTED":
                        transaction.update(status="ROLLED_BACK", reason=str(exc))
        source_research = entry.pop("_research_draft", None)
        if entry["status"] == "APPLIED" and source_research is not None:
            seed["research_draft"] = source_research
            pipeline.reviewer.research_draft = source_research
            if entry.get("research_source_updates"):
                seed.pop("research_reviewed_signature", None)
                seed.pop("pending_review", None)
        audit.append(entry)
        if entry.get("error_type") == "protocol":
            # 本轮协议输出机会耗尽，不消耗实质修复轮数；记录终止，避免空转。
            seed["repair_rounds"] -= 1
            seed["protocol_exhausted"] = True
            save()
            break
        if entry["status"] == "SCOPE_UNRESOLVED":
            seed["repair_rounds"] -= 1  # 没有签发局部权限，未调用补丁写入。
            entry["issues"] = entry.get("issues") or review.get("issues", [])
        if entry["status"] in ("SCOPE_UNRESOLVED", "NO_CHANGE"):
            save()
            if retrieval_improved:
                continue
            break
        if entry["status"] == "APPLIED":
            numeric_changed = fields != parameters or bool(entry.get("research_source_updates"))
            text, parameters = changed, fields
            if seed.get("numeric_contract") and numeric_changed:
                # 数值或引用变化后不能沿用上一正文的定位队列；用当前版本重新审。
                seed.pop("pending_review", None)
                seed.pop("reviewed_hash", None)
                review = retain_numeric_diagnostics(review)
                save()
                continue
            if errors:
                seed.pop("pending_review", None)
                seed.pop("reviewed_hash", None)
                save()
                continue  # 章节补齐后，重新验结构并交学术 Reviewer。
        if "remaining_issues" in entry:
            remaining = entry["remaining_issues"]
            summaries = [i["reason"] for i in remaining] + entry.get("unmapped_summaries", [])
            review = dict(review, repair_issues=remaining, issues=summaries,
                          confidence="medium" if not summaries else "low")
            review["repair_feedback"] = [{"reason": d.get("reason"), "quotes": [i.get("quote") for i in remaining]}
                for d in entry.get("transactions", {}).values() if d.get("status") == "REJECTED"]
            seed["reviewed_hash"] = digest(text)
            if summaries:
                seed["pending_review"] = review
                if seed.get("numeric_contract"):
                    seed["pending_review_hash"] = digest(text)
            else:
                save()
                break
        save()
    repair_local_format()
    # 不以最后一次补丁是否成功判断研究审核版本：成功后又被拒也必须同步最终正文。
    from .research_support import refresh_for_review, finish_review, review_signature, missing_support_fields
    research_doc = refresh_for_review(pipeline, seed, text, parameters, evidence)
    research_stale = research_doc is not None and seed.get("research_reviewed_signature") != review_signature(research_doc, parameters)
    legacy_stale = not research_doc and audit and audit[-1].get("status") == "APPLIED" and seed.get("reviewed_hash") != digest(text)
    can_review = lambda: (seed.get("review_rounds", 0) < limits["review_rounds"]
                         and seed.get("review_service_failures", 0) < service_limit)
    structure_stale = review.get("error_type") == "structure" and audit and audit[-1].get("status") == "APPLIED"
    format_stale = (sections.classify_design_output(text, requirements)["format_issues"]
                    and seed.get("reviewed_hash") != digest(text))
    if (research_stale or legacy_stale or structure_stale or format_stale) and can_review():
        if not classify_current()["blocking_issues"]:
            seed["review_rounds"] = seed.get("review_rounds", 0) + 1
            save()
            package = select_context(seed, purpose="final_review", text=text, issues=review.get("repair_issues", []), config=retrieval_options(pipeline))
            review = retain_numeric_diagnostics(review)
            review = pipeline.reviewer.run(question=question, evidence=package, draft_answer=text,
                history_context=history_context, scheme=seed.get("scheme"), claimed_energy=pipeline._extract_energy_claim(text))
            finish_review(pipeline, seed, review, parameters, evidence, package)
            seed["reviewed_hash"] = digest(text)
            if review.get("error_type") == "reviewer":
                seed["review_service_failures"] = seed.get("review_service_failures", 0) + 1
            # 最终复核仅记录决策和关闭窗口，不再开启无法用于正文修复的检索。
            retrieval_state["window_open"] = False
            retrieval_state["pending_diagnostics"] = review.get("retrieval_diagnostics", [])
    # 漏返字段的补漏计入配置中的审核总预算；科学否决不能被补漏覆盖。
    if (research_doc and seed.get("research_reviewed_signature") == review_signature(research_doc, parameters)
            and not seed.get("research_support_completion_attempted") and can_review()
            and missing_support_fields(research_doc["research"], research_doc, parameters)):
        from .research_recovery import complete_missing_support_review
        seed["research_support_completion_attempted"] = True
        seed["review_rounds"] = seed.get("review_rounds", 0) + 1
        save()
        package = select_context(seed, purpose="final_review", text=text, issues=review.get("repair_issues", []), config=retrieval_options(pipeline))
        review, _ = complete_missing_support_review(pipeline.reviewer, research_doc, review, package)
        # 本轮诊断由当前逐字段结果重新生成，完整历程仍在检查点和调用记录中。
        review["issues"] = [s for s in review.get("issues", []) if not s.startswith("研究摘要待核验：")]
        finish_review(pipeline, seed, review, parameters, evidence, package)
    review = retain_numeric_diagnostics(retain_format_diagnostics(review))
    if seed.get("numeric_contract"):
        seed.pop("pending_review", None)
    save()
    if seed.get("research_draft"):
        from .research_narrative import integrate
        from .research_original import available as original_available
        from .research_contract import finalize_research
        doc = seed["research_draft"]
        doc["reviewer_output"] = copy.deepcopy(review)
        finalize_research(doc)
        if not original_available(doc):
            integrate(doc, getattr(getattr(pipeline, "writer", None), "llm", None), pipeline.reviewer,
                      state=seed.setdefault("research_narrative_state", {}), save=save)
        save()
    return text, review, evidence, parameters, audit


def validate_repair_geometry(fields, original, seed, evidence):
    """事务提交前复用真实物性与几何规则，不写入检查点。"""
    from auto_battery_research.simulation.geometry_contract import build_geometry_contract, validate_geometry_contract
    from .source_metadata import changed_parameter_fields
    changed_inputs = changed_parameter_fields(original, fields)
    if not changed_inputs:
        return
    candidate = copy.deepcopy(seed.get("geometry_candidate"))
    scheme = scheme_with_fields(seed.get("scheme"), fields)
    for key, field in fields.items():
        if key not in changed_inputs:
            continue
        comp, _, name = key.partition(".")
        if name == "material":
            raise ValueError("材料变更涉及关联物性，须先扩大授权或重新设计")
        if isinstance(candidate, dict) and comp in ("cathode", "anode") and name in ("L_um", "porosity", "R_s_um", "D_s_m2s"):
            candidate.setdefault(comp, {})[name] = field["value"]
            if name == "D_s_m2s":
                candidate.setdefault("diffusion_evidence", {})[key] = field.get("evidence")
        elif isinstance(candidate, dict) and comp == "condition":
            candidate.setdefault("condition", {})[name] = field["value"]
    if isinstance(candidate, dict):
        geo = build_geometry_contract(candidate, scheme, evidence)
        errors = validate_geometry_contract(geo, scheme, evidence)
        if errors:
            raise ValueError("; ".join(errors))


def resume_pipeline(pipeline, seed, question, history_context="", top_k=50):
    bind_repair_options(pipeline, seed)
    if not seed.get("draft") and seed.get("writer_request") and "writer_protocol" in seed:
        from .scheme_ledger import atomic_write
        pipeline.writer.protocol_state = seed["writer_protocol"]
        pipeline.writer.current_parameters = seed.get("writer_parameters", {})
        def save_protocol():
            path = getattr(pipeline, "repair_checkpoint_path", None)
            if path:
                atomic_write(path, json.dumps(seed, ensure_ascii=False, indent=2))
        pipeline.writer.save_protocol = save_protocol
        output = pipeline.writer.run(**seed["writer_request"])
        seed.update(writer_output=output, draft=output.get("draft_answer", ""),
                    design_structure=output.get("design_structure"), numeric_contract=output.get("numeric_contract"))
        if seed["draft"]:
            seed["parameters"] = output.get("numeric_parameters", seed.get("parameters", {}))
            from .research_contract import prepare_review
            prepare_review(pipeline, seed)
        save_protocol()
    if seed.get("draft") and seed.get("geometry_candidate") is None and not seed.get("geometry_error"):
        # 协议恢复可能在主材料第一次定案后才具备几何构建条件。
        scheme = seed.get("scheme") or {}
        if not (scheme.get("geometry") or {}).get("contract_version"):
            from auto_battery_research.simulation.geometry_contract import geometry_from_declared_parameters
            try:
                candidate, geo = geometry_from_declared_parameters(seed.get("parameters") or {}, scheme, seed["evidence"])
                seed.update(geometry_candidate=candidate)
                seed["scheme"]["geometry"] = geo
            except ValueError as exc:
                seed["geometry_error"] = str(exc)
    text, review, evidence, fields, audit = review_design(pipeline, seed, question, history_context, top_k)
    scheme = copy.deepcopy(seed.get("scheme") or {})
    if scheme.get("geometry"):
        from auto_battery_research.simulation.geometry_contract import validate_geometry_contract
        if not validate_geometry_contract(scheme["geometry"], scheme, evidence):
            pipeline.inherited_design = scheme
    rules = pipeline.relation_engine.check_scheme(scheme, claimed_energy=pipeline._extract_energy_claim(text), answer_text=text) if pipeline.relation_engine else {}
    completed, passed = review_verdict(text, review, (seed.get("plan") or {}).get("requirements"))
    return dict(seed, final_answer=text + pipeline._build_reference_section(evidence) if text.strip() else "", evidence=evidence,
        reviewer_output=review, review_completed=completed,
        review_passed=passed, confidence=review.get("confidence", "low"),
        scheme=scheme, rule_checks=rules, repair_parameters=fields, repair_audit=audit)
