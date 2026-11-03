"""可信补丁合并器：审核只能建议，程序签发局部写权限。"""
import copy
import json
import re

from .scheme_ledger import digest, validate_field
from .design_sections import design_sections, normalize_titles


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


def authorize(text, issues, parameters):
    _, units = blocks(text)
    grants = {}
    for issue in issues:
        quote = issue.get("quote")
        matches = [bid for bid, value in units.items() if isinstance(quote, str) and quote and quote in value]
        if len(matches) != 1 or not issue.get("reason") or not issue.get("suggestion"):
            continue
        fields = []
        for key in issue.get("parameter_paths", []):
            field = parameters.get(key)
            from .report_narrative import GROUPS, NAMES
            comp, _, name = key.partition(".")
            local = units[matches[0]]
            located = key in local or (GROUPS.get(comp, key) in local and NAMES.get(name, key) in local and str((field or {}).get("value")) in local)
            if field and located and not field.get("locked") and key != "requirements.original_request":
                fields.append(key)
        targets = list(matches)
        # 仅明确标注字段路径的直接依赖可自动追加，不能按宽泛关键词扩大权限。
        for bid, value in units.items():
            if any(f"`{key}`" in value for key in fields) and bid not in targets:
                targets.append(bid)
        grants[issue["id"]] = {"blocks": targets, "fields": fields}
    return {"base_hash": digest(text), "parameters_hash": digest(parameters), "grants": grants}


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
    if set(patch) - {"base_hash", "parameters_hash", "edits", "parameters", "append_sections"}:
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
                token = str(field["value"])
                if token in units[bid] and units[bid].count(token) != value.count(token):
                    raise ValueError(f"正文不得改变锁定或未授权参数值: {key}")
            if bound and key in grant.get("fields", []) and str(field.get("value")) in units[bid]:
                token = str(field.get("value"))
                if units[bid].count(token) != value.count(token) and not any(c.get("path") == key for c in patch.get("parameters", [])):
                    raise ValueError(f"正文参数变更必须同时提交授权字段补丁: {key}")
        if re.findall(r"^#{1,4} .+$", value, re.M) != re.findall(r"^#{1,4} .+$", units[bid], re.M):
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
        updated[key] = copy.deepcopy(field)
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
    old_ids = set(re.findall(r"\[([\w-]+)\]", text))
    if not set(re.findall(r"\[([\w-]+)\]", merged)) <= old_ids | set(permit.get("evidence_ids", [])):
        raise ValueError("新增章节引用未归档证据")
    for key, field in updated.items():
        if field != parameters.get(key) and (not field.get("source_quote") or field["source_quote"] not in merged):
            raise ValueError("参数缺少补丁正文中的原始声明")
    return merged, updated


def scheme_with_fields(scheme, fields):
    scheme = copy.deepcopy(scheme or {})
    for key, field in fields.items():
        comp, _, name = key.partition(".")
        value = field.get("value")
        if name == "material" and comp in ("cathode", "anode", "electrolyte"):
            scheme[comp] = value
        elif name in ("L_um", "porosity", "R_s_um", "D_s_m2s") and comp in ("cathode", "anode"):
            scheme.setdefault("geometry", {}).setdefault(comp, {})[name] = value
        elif comp == "condition":
            scheme.setdefault("geometry", {}).setdefault("condition", {})[name] = value
    return scheme


def repair(writer, text, review, evidence, parameters, read_only_context="", verifier=None, geometry_validator=None):
    issues = review_issues(review, text)
    permit = authorize(text, issues, parameters)
    permit["missing_sections"] = design_sections(text)[2]
    permit["evidence_ids"] = [e.get("passage_id") for e in evidence]
    if issues:
        from .repair_interfaces import execute
        changed, fields, entry = execute(writer, verifier or writer.llm, text, parameters, permit, issues, evidence,
                                        read_only_context, geometry_validator, review.get("repair_feedback"))
        entry["unmapped_summaries"] = list(review.get("issues", []))[len(issues):]
        return changed, fields, entry
    if not permit["grants"] and not permit["missing_sections"]:
        return text, parameters, {"status": "SCOPE_UNRESOLVED", "issues": issues}
    prompt = {"read_only_context": read_only_context, "draft_blocks": blocks(text)[1], "permission": permit, "issues": issues,
              "parameters": parameters, "evidence": evidence[:25],
              "return_object_example_do_not_wrap": {"base_hash": permit["base_hash"], "edits": [{"issue_id": "issue-0", "block_id": "b0", "text": "替换段落"}],
                         "parameters": [], "append_sections": []}}
    for attempt in range(2):
        try:
            raw = writer.llm.chat("直接返回JSON补丁对象本身，不要format包装。禁止全文重写。参数修改格式为 {issue_id,path,field}；缺失章节格式为 {name,text}。", json.dumps(prompt, ensure_ascii=False), temperature=0.1, trace_tag="writer_patch")
            patch = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            merged, fields = apply_patch(text, parameters, permit, patch)
            return merged, fields, {"status": "APPLIED" if merged != text or fields != parameters else "NO_CHANGE", "permission": permit, "patch": patch, "before": digest(text), "after": digest(merged)}
        except Exception as exc:
            protocol_error = isinstance(exc, json.JSONDecodeError) or "PATCH_FORMAT_INVALID" in str(exc)
            if attempt == 0 and protocol_error:
                prompt["format_error"] = str(exc)
                continue
            return text, parameters, {"status": "REJECTED", "reason": str(exc), "error_type": "protocol" if protocol_error else "validation", "permission": permit}


def review_design(pipeline, seed, question, history_context="", top_k=50):
    """可恢复的三轮审核；正文问题只产生补丁，审核异常不改正文。"""
    from .scheme_ledger import atomic_write, flatten_scheme, canonical_unit
    question = seed.setdefault("review_context", question)
    text = normalize_titles(seed["draft"])
    evidence = seed["evidence"]
    parameters = copy.deepcopy(seed.get("parameters") or (getattr(pipeline, "scheme_context", None) or {}).get("parameters", {}))
    for key, value in flatten_scheme(seed.get("scheme") or {}).items():
        parameters.setdefault(key, {"value": value, "unit": canonical_unit(key) or "", "basis": "engineering_proposal", "source": "初始设计"})
    audit = list(seed.get("repair_audit", []))
    review = seed.get("reviewer_output") or {"issues": ["尚未审核"], "error_type": "reviewer", "confidence": "low"}
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
        seed.update(draft=text, evidence=evidence, parameters=parameters, repair_audit=audit, reviewer_output=review,
                    scheme=scheme_with_fields(seed.get("scheme"), parameters))
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
    save()
    # 持久化预算，工具重复调用和进程重启均不能重置。
    while seed.get("repair_rounds", 0) < 3 and not seed.get("protocol_exhausted") and not (getattr(pipeline, "report_first", False) and seed.get("review_service_failures", 0) >= 2):
        seed["repair_rounds"] = seed.get("repair_rounds", 0) + 1
        save()
        from .design_sections import design_output_issues
        errors = design_output_issues(text)
        if seed.get("pending_review") and not errors:
            review = seed.pop("pending_review")
        elif errors:
            review = {"issues": errors, "repair_issues": [], "confidence": "low", "error_type": "structure", "review_status": "STRUCTURE_INVALID"}
        else:
            review = pipeline.reviewer.run(question=question, evidence=evidence, draft_answer=text,
                history_context=history_context, scheme=seed.get("scheme"), claimed_energy=pipeline._extract_energy_claim(text))
            review["review_status"] = "SERVICE_ERROR" if review.get("error_type") == "reviewer" else ("REVIEWED_WITH_ISSUES" if review.get("issues") else "APPROVED")
            seed["reviewed_hash"] = digest(text)
        save()
        if review.get("error_type") == "reviewer":
            seed["review_service_failures"] = seed.get("review_service_failures", 0) + 1
            save()
            if getattr(pipeline, "report_first", False) and seed["review_service_failures"] >= 2:
                break
            continue
        if not review.get("issues"):
            break
        if review.get("error_type") == "retrieval" and not seed.get("supplemented"):
            seed["supplemented"] = True
            save()
            queries = pipeline._issues_to_queries(review["issues"])[:3]
            extra = pipeline.retrieve_context(db_type=seed.get("db_type", "literature"), question=seed.get("question", question),
                queries=queries, previous_evidence=evidence, top_k=top_k)
            merged = {e["passage_id"]: e for e in evidence}
            merged.update({e["passage_id"]: e for e in extra.get("results", [])})
            evidence = list(merged.values())
        changed, fields, entry = repair(pipeline.writer, text, review, evidence, parameters, question,
            verifier=getattr(pipeline.reviewer, "llm", None),
            geometry_validator=lambda fields: validate_repair_geometry(fields, parameters, seed, evidence))
        if entry["status"] == "APPLIED" and fields != parameters:
            try:
                from auto_battery_research.simulation.geometry_contract import build_geometry_contract, validate_geometry_contract
                candidate = copy.deepcopy(seed.get("geometry_candidate"))
                next_scheme = scheme_with_fields(seed.get("scheme"), fields)
                for key, field in fields.items():
                    if field == parameters.get(key):
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
        audit.append(entry)
        if entry.get("error_type") == "protocol":
            # 两次协议输出均失败，不消耗实质修复轮数；记录终止，避免空转。
            seed["repair_rounds"] -= 1
            seed["protocol_exhausted"] = True
            save()
            break
        if entry["status"] in ("SCOPE_UNRESOLVED", "NO_CHANGE"):
            save()
            break
        if entry["status"] == "APPLIED":
            text, parameters = changed, fields
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
            else:
                save()
                break
        save()
    # 最后一轮修改后仍须复核，不能把修改前审核冒充修改后通过。
    if audit and audit[-1].get("status") == "APPLIED" and seed.get("reviewed_hash") != digest(text):
        from .design_sections import design_output_issues
        if not design_output_issues(text):
            review = pipeline.reviewer.run(question=question, evidence=evidence, draft_answer=text,
                history_context=history_context, scheme=seed.get("scheme"), claimed_energy=pipeline._extract_energy_claim(text))
            seed["reviewed_hash"] = digest(text)
        save()
    return text, review, evidence, parameters, audit


def validate_repair_geometry(fields, original, seed, evidence):
    """事务提交前复用真实物性与几何规则，不写入检查点。"""
    from auto_battery_research.simulation.geometry_contract import build_geometry_contract, validate_geometry_contract
    if fields == original:
        return
    candidate = copy.deepcopy(seed.get("geometry_candidate"))
    scheme = scheme_with_fields(seed.get("scheme"), fields)
    for key, field in fields.items():
        if field == original.get(key):
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
    text, review, evidence, fields, audit = review_design(pipeline, seed, question, history_context, top_k)
    scheme = copy.deepcopy(seed.get("scheme") or {})
    if scheme.get("geometry"):
        from auto_battery_research.simulation.geometry_contract import validate_geometry_contract
        if not validate_geometry_contract(scheme["geometry"], scheme, evidence):
            pipeline.inherited_design = scheme
    rules = pipeline.relation_engine.check_scheme(scheme, claimed_energy=pipeline._extract_energy_claim(text), answer_text=text) if pipeline.relation_engine else {}
    from .design_sections import design_output_issues
    completed = not design_output_issues(text) and review.get("error_type") not in ("reviewer", "structure")
    return dict(seed, final_answer=text + pipeline._build_reference_section(evidence), evidence=evidence,
        reviewer_output=review, review_completed=completed,
        review_passed=completed and not review.get("issues"), confidence=review.get("confidence", "low"),
        scheme=scheme, rule_checks=rules, repair_parameters=fields, repair_audit=audit)
