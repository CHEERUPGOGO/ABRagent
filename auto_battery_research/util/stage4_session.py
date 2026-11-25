"""Stage 4 v3 独立编排：一次完整生成、编译、科学审核、局部修复及终审。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .scheme_ledger import atomic_write, digest
from .stage4_document import (compile_document, decode_document, current_content,
                              candidate_parameters, COMPILER_VERSION)
from .stage4_consistency import request, available, single_attempt, audit_consistency, authorize, apply_patch
from .runtime_budgets import bind_repair_options


def design_mode(question, plan, scheme=None):
    """设计/分析模式共用同一判定；明确分析要求优先。"""
    required = (plan.get("requirements") or {}).get("scheme_required")
    if required is not None:
        return required is True
    return bool(scheme or any(k in question for k in ("设计", "推荐", "方案")))


def produce_v3(client, system, prompt, current, state, save, requirements, evidence):
    """所有原始响应持久保存；传输截断最多一次完整重试，不内部重发。"""
    signature = digest({"system": system, "prompt": prompt, "parameters": current})
    def call():
        if not client or not client.available:
            raise ValueError("Writer模型不可用，不能编造设计")
        with single_attempt(client):
            raw = client.chat(system, prompt, temperature=.2, json_mode=True, trace_tag="writer_v3")
        return {"raw_response": raw, "response_meta": copy.deepcopy(getattr(client, "last_response", {}))}
    try:
        output = request(state, "writer", signature, save, call)
    except Exception as exc:
        record = next((r for r in state.get("requests", []) if r["role"] == "writer" and r["signature"] == signature), {})
        if not record.get("response_meta", {}).get("truncated"):
            return {"draft_answer": "", "error": str(exc), "raw_response": record.get("raw_response", ""), "writer_contract_version": 3}
        output = {"raw_response": record.get("raw_response", ""), "response_meta": record.get("response_meta", {})}
    from .writer_protocol import incomplete_json, single_closure_repair
    raw = output["raw_response"]
    if output["response_meta"].get("truncated") or incomplete_json(raw):
        def retry():
            old = getattr(client, "max_tokens", None)
            try:
                if isinstance(old, int):
                    client.max_tokens = min(old * 2, getattr(client, "output_max_tokens", old * 2))
                with single_attempt(client):
                    response = client.chat(system, prompt + "\n前一响应传输截断，请在单个完整JSON中精简重复元数据，保留全部科研内容和五节。", temperature=.2, json_mode=True, trace_tag="writer_v3_retry")
                return {"raw_response": response, "response_meta": copy.deepcopy(getattr(client, "last_response", {}))}
            finally:
                if isinstance(old, int):
                    client.max_tokens = old
        try:
            output = request(state, "writer_retry", signature, save, retry)
            raw = output["raw_response"]
        except Exception as exc:
            return {"draft_answer": "", "error": str(exc), "raw_response": raw, "writer_contract_version": 3}
    def parse(value):
        data = decode_document(value)
        if set(data) != {"version", "parameters", "sections", "research_links"} or data.get("version") != 3:
            raise ValueError("Writer v3输出须包含version/parameters/sections/research_links")
        params, issues = candidate_parameters(data["parameters"], current)
        compiled = compile_document(current_content(data), params, requirements, evidence)
        compiled.diagnostics[:0] = issues
        return data, params, compiled
    try:
        data, params, compiled = parse(raw)
    except ValueError as exc:
        recovery = single_closure_repair(raw)
        if recovery:
            state["syntax_repair"] = recovery
            save()
            try:
                data, params, compiled = parse(recovery["repaired_response"])
            except ValueError as inner:
                return {"draft_answer": "", "error": str(inner), "raw_response": raw, "writer_contract_version": 3}
        else:
            return {"draft_answer": "", "error": str(exc), "raw_response": raw, "writer_contract_version": 3}
    if not compiled.valid:
        # 协议补丁只改已授权元数据和失败小标题；科学叶文本、值、来源身份冻结。
        from .writer_titles import INSTRUCTIONS as TITLE_INSTRUCTIONS, apply as apply_titles, verify as verify_titles
        paths = []
        for issue in compiled.diagnostics:
            loc = issue["source_location"]
            if issue["code"] == "ID_INVALID" and isinstance(loc, list):
                node = data
                try:
                    for part in loc:
                        node = node[part]
                    if isinstance(node, dict) and "id" in node:
                        paths.append(loc + ["id"])
                except (KeyError, TypeError, IndexError):
                    pass
        unbound = [d for d in compiled.diagnostics if d["code"] == "UNBOUND_PARAMETER"]
        title_errors = [d for d in compiled.diagnostics if d["code"].startswith("TITLE_")]
        if paths or unbound or title_errors:
            base = digest(data)
            def protocol_patch():
                with single_attempt(client):
                    response = client.chat("只修已授权协议元数据和TITLE_报错的小标题，不改科研文本、参数值或来源。"
                        "titles仅含path/old/new，path须等于错误的source_location，old须等于原标题；保持原意及科学量值。",
                        json.dumps({"base_hash": base, "allowed_paths": paths, "diagnostics": compiled.diagnostics, "document": data,
                            "title_format": TITLE_INSTRUCTIONS,
                            "unbound_parameters": unbound,
                            "reference_recovery": "仅允许未绑定新提案，以完整等值原声明替换为param引用，不改变值、来源、样品、工况或分母。references每项含field/leaf_id/quote/value_quote。",
                            "response_shape": {"base_hash": base, "replacements": [{"path": [], "value": "已授权ID"}],
                                               "references": [], "titles": [{"path": [], "old": "原标题", "new": "修正名称"}]}}, ensure_ascii=False),
                        temperature=.1, json_mode=True, trace_tag="writer_v3_protocol_patch")
                return {"raw_response": response, "response_meta": copy.deepcopy(getattr(client, "last_response", {}))}
            try:
                patch = request(state, "protocol_patch", base, save, protocol_patch)
                parsed = decode_document(patch["raw_response"])
                if (not {"base_hash", "replacements", "references"} <= set(parsed)
                        or set(parsed) - {"base_hash", "replacements", "references", "titles"}
                        or parsed["base_hash"] != base or not isinstance(parsed["replacements"], list)):
                    raise ValueError("协议补丁基准或结构非法")
                changed = copy.deepcopy(data)
                for replacement in parsed["replacements"]:
                    if not isinstance(replacement, dict) or set(replacement) != {"path", "value"} or replacement["path"] not in paths:
                        raise ValueError("协议元数据补丁越权")
                    node = changed
                    for key in replacement["path"][:-1]:
                        node = node[key]
                    node[replacement["path"][-1]] = replacement["value"]
                changed, title_records = apply_titles(changed, parsed.get("titles", []))
                title_state = state.setdefault("title_reviews", {}).setdefault(base, {})
                verify_titles(client, title_records, title_state, save)
                from .stage4_document import recover_references
                changed, records = recover_references(changed, parsed["references"], params, compiled)
                if records:
                    verify_input = {"base_hash": base, "bindings": records, "before": data, "after": changed, "parameters": params}
                    def verify():
                        with single_attempt(client):
                            response = client.chat("独立核对等值引用恢复的样品/工况/分母/用途/文献归属，不能假定同值就是同参数。"
                                "返回JSON{base_hash:给定基准,bindings:[{index:编号,supported:布尔,reason:具体依据}]}，每项恰好一次。",
                                json.dumps(verify_input, ensure_ascii=False), temperature=0, json_mode=True, trace_tag="writer_reference_verify")
                        return {"raw_response": response, "response_meta": copy.deepcopy(getattr(client, "last_response", {}))}
                    verified = request(state, "reference_verify", digest(verify_input), save, verify)
                    verdict = decode_document(verified["raw_response"])
                    rows = verdict.get("bindings")
                    if (set(verdict) != {"base_hash", "bindings"} or verdict["base_hash"] != base or not isinstance(rows, list)
                            or len(rows) != len(records) or any(not isinstance(v, dict) or type(v.get("index")) is not int
                                or v.get("supported") is not True or not isinstance(v.get("reason"), str) or not v["reason"].strip() for v in rows)
                            or {v["index"] for v in rows} != set(range(len(records)))):
                        raise ValueError("等值引用语义复核未通过")
                candidate_data, candidate_view, candidate_compiled = parse(json.dumps(changed, ensure_ascii=False))
                if not candidate_compiled.valid:
                    raise ValueError("协议补丁后仍有错误")
                data, params, compiled = candidate_data, candidate_view, candidate_compiled
                state["protocol_patch_status"] = "APPLIED"
            except Exception as exc:
                state["protocol_patch_status"] = "REJECTED"
                state["protocol_patch_error"] = str(exc)
            save()
    return {"writer_contract_version": 3, "writer_content": current_content(data), "parameter_view": params,
            "draft_answer": compiled.markdown if compiled.valid else "", "precheck": compiled.diagnostics,
            "raw_response": raw, "compiler": compiled.as_dict(), "fallback": not compiled.valid}


def synchronize_document(doc, parameters):
    """所有参数变更共用正文/研究同步；仅实际依赖字段的科学支持失效。"""
    if doc.get("writer_contract_version") != 3:
        return doc
    compiled = compile_document(doc["writer_content"], parameters, doc.get("requirements"), doc.get("evidence", []))
    from .stage4_research_projection import preserve_support
    previous = doc.get("research") or {}
    doc["research"] = preserve_support(previous, compiled.research, doc, parameters)
    old = doc.get("render_fingerprint")
    doc.update(content_hash=compiled.content_hash, render_fingerprint=compiled.render_fingerprint,
               compiler_version=COMPILER_VERSION, final_answer=compiled.markdown,
               design_structure={"version": 1, "headings": compiled.sections},
               compiled_design=compiled.as_dict(), precheck=compiled.diagnostics)
    doc["parameters"] = copy.deepcopy(parameters)
    doc["scheme_parameters"] = copy.deepcopy(parameters)
    if "repair_parameters" in doc:
        doc["repair_parameters"] = copy.deepcopy(parameters)
    if old and old != compiled.render_fingerprint:
        doc.setdefault("consistency_history", []).append(copy.deepcopy(doc.get("consistency_review")))
        doc["consistency_review"] = {"status": "stale", "input_fingerprint": old,
                                     "reason": "参数或正文已变化，当前一致性尚未复核"}
        doc["review_passed"] = False
        doc["review_status"] = "REVIEWED_WITH_ISSUES"
        if isinstance(doc.get("report_readiness"), dict):
            doc["report_readiness"]["category"] = "CONDITIONAL"
    return doc


def run_session(pipeline, question, plan=None, retrieval_seed=None, history_context="", *, resume=None):
    path = getattr(pipeline, "repair_checkpoint_path", None)
    if not path:
        raise ValueError("v3设计需课题目录检查点，不能无持久化调用")
    path = Path(path)
    if resume is not None:
        state = copy.deepcopy(resume)
        if state.get("writer_contract_version") != 3 or not isinstance(state.get("requests"), list) or not state.get("plan"):
            raise ValueError("v3检查点损坏，禁止回退v2重生成")
        plan = state["plan"]
        if state.get("phase") == "complete" and state.get("result"):
            if not isinstance(state["result"], dict) or state["result"].get("writer_contract_version") != 3:
                raise ValueError("v3完成检查点损坏，禁止回退旧输出")
            return copy.deepcopy(state["result"])
        for name in ("writer_parameters", "evidence", "retrieval", "content_revision", "repair_audit"):
            if name not in state:
                raise ValueError("v3检查点缺少" + name + "，禁止重生成修好损坏状态")
        if "writer_content" in state and (not isinstance(state["writer_content"], dict) or state["writer_content"].get("version") != 3):
            raise ValueError("v3当前内容树损坏，不能用Writer原稿覆盖有效修复")
    else:
        state = {"writer_contract_version": 3, "phase": "writer", "question": question, "plan": copy.deepcopy(plan),
            "checkpoint_identity": getattr(pipeline, "repair_checkpoint_identity", None), "requests": [],
            "retrieval": copy.deepcopy(retrieval_seed["retrieval"]), "evidence": copy.deepcopy(retrieval_seed["evidence"]),
            "retrieval_state": copy.deepcopy(retrieval_seed.get("retrieval_state")),
            "writer_parameters": copy.deepcopy((getattr(pipeline, "scheme_context", None) or {}).get("parameters", {})),
            "content_revision": 0, "repair_audit": []}
        planning_path = Path(path).with_name("stage4_planning_checkpoint.json")
        if planning_path.exists():
            planning = json.loads(planning_path.read_text(encoding="utf-8"))
            state["requests"] = copy.deepcopy(planning.get("requests", []))
    limits = bind_repair_options(pipeline, state)
    # 存量会话按已有请求保守补计，不能因为新增调度配额而重置旧尝试。
    final_used = sum(bool(row.get("reserved_final")) for row in state.get("repair_audit", []))
    state.setdefault("final_rounds", final_used)
    state.setdefault("regular_rounds", max(0, sum(r.get("role") == "repair" for r in state["requests"]) - final_used))
    requirements = plan.get("requirements") or {}
    def save():
        atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2))
        path.with_name("stage4_rebuild_requested.json").unlink(missing_ok=True)
    save()
    writer = pipeline.writer
    writer.contract_version = 3
    writer.current_parameters = state["writer_parameters"]
    writer.protocol_state = state
    writer.save_protocol = save
    from .adaptive_retrieval import options
    writer.context_options = options(pipeline)
    if "writer_output" not in state:
        from .adaptive_retrieval import select_context, options
        evidence = select_context(state, purpose="writer", config=options(pipeline))
        state["writer_output"] = writer.run(question=question, plan=plan, evidence=evidence,
                                           history_context=history_context, scheme=None)
        save()
    output = state["writer_output"]
    content = state.get("writer_content") or output.get("writer_content")
    parameters = state.get("parameters") or output.get("parameter_view") or state["writer_parameters"]
    if not content:
        state.update(phase="diagnostic", error=output.get("error", "Writer协议失败"))
        save()
        return {"writer_contract_version": 3, "final_answer": "", "plan": plan, "evidence": state["evidence"],
                "scheme": {}, "review_completed": False, "review_passed": False, "precheck": output.get("precheck", []),
                "reviewer_output": {"error_type": "structure", "confidence": "low", "issues": [state["error"]]}, "writer_output": output}
    if not isinstance(parameters, dict) or any(not isinstance(row, dict) for row in parameters.values()):
        raise ValueError("v3候选参数状态损坏，不恢复原稿参数")
    state.update(writer_content=content, parameters=parameters, phase="compile")
    doc = {"writer_contract_version": 3, "writer_content": content, "requirements": requirements,
           "target": requirements.get("source", question), "design_request": question,
           "evidence": state["evidence"], "parameters": parameters,
           "ledger_view": {"parameters": parameters}, "research": state.get("research", {}),
           "content_revision": state["content_revision"]}
    def compile_current():
        nonlocal content, parameters
        from .material_ids import canonical_parameters, assemble_components
        parameters = canonical_parameters(parameters)
        scheme = assemble_components({}, parameters)
        target = (parameters.get("requirements.target_energy_wh_kg") or {}).get("value")
        if target is not None:
            scheme["target_energy_wh_kg"] = target
        try:
            from auto_battery_research.simulation.geometry_contract import geometry_from_declared_parameters, geometry_hash
            # 程序生成的模型参考身份可复用，但显式null/非法声明不能被填补。
            declared_geometry = {p: row for p, row in parameters.items()
                                 if not (p.endswith(".R_s_um") and row.get("basis") == "model_reference")}
            candidate, geo = geometry_from_declared_parameters(declared_geometry, scheme, state["evidence"])
            scheme.update(geometry=geo, geometry_hash=geometry_hash(geo), initial_geometry_hash=geometry_hash(geo), design_revision=0)
            from .scheme_ledger import project_scheme_parameters
            parameters = project_scheme_parameters(scheme, parameters, "v3已声明几何及精确模型参考")
            for p, row in parameters.items():
                if row.get("basis") == "model_reference":
                    row["model_reference"] = copy.deepcopy(geo.get("parameter_sources", {}).get(p))
            state["geometry_candidate"] = candidate
            state.pop("geometry_error", None)
        except (ValueError, KeyError, TypeError) as exc:
            state["geometry_error"] = str(exc)
        doc.update(writer_content=copy.deepcopy(content), parameters=parameters, ledger_view={"parameters": parameters}, scheme=scheme)
        synchronize_document(doc, parameters)
        compiled = compile_document(content, parameters, requirements, state["evidence"])
        doc["precheck"] = compiled.diagnostics
        # 提案来源由编译后的实际叶节点记录，不从正文二次提取。
        for key, row in parameters.items():
            if row.get("reference_origin") == "writer_v3":
                item = next((leaf for leaf in compiled.leaves.values() if key in compiled.dependencies[leaf["id"]]["parameters"]), None)
                if item:
                    row["source_quote"] = item["rendered_text"]
        # 来源登记属于候选视图；审核只绑定登记完成后的指纹。
        synchronize_document(doc, parameters)
        compiled = compile_document(content, parameters, requirements, state["evidence"])
        state.update(writer_content=copy.deepcopy(content), parameters=parameters, compiled_design=compiled.as_dict(),
                     research=doc["research"], scheme=scheme)
        save()
        return compiled
    compiled = compile_current()
    # 来源由程序登记后进入最终指纹，全部审核绑定此候选参数视图。
    compiled = compile_current()
    if not compiled.valid or output.get("precheck") and any(d["severity"] == "error" for d in output["precheck"]):
        state.update(phase="diagnostic")
        save()
        return dict(doc, final_answer="", plan=plan, retrieval=state["retrieval"], writer_output=output,
                    review_completed=False, review_passed=False,
                    reviewer_output={"issues": [d["reason"] for d in compiled.diagnostics] or ["Writer协议或参数提案未通过"], "confidence": "low", "error_type": "structure"})
    from .adaptive_retrieval import select_context, options, after_review
    from .research_support import apply_review as apply_support, review_pending
    from .design_repair import review_verdict
    review = state.get("reviewer_output", {})
    def scientific_review():
        # 审核结论不参与输入身份；恢复已返回请求不能因新增support_review再发同稿。
        signature = compiled.render_fingerprint
        package = select_context(state, purpose="reviewer", text=compiled.markdown, config=options(pipeline))
        pipeline.reviewer.requirements = requirements
        pipeline.reviewer.research_draft = dict(doc, research=doc["research"])
        from .numeric_consistency import audit
        pipeline.reviewer.numeric_consistency = audit(parameters, compiled.markdown)
        def invoke():
            with single_attempt(pipeline.reviewer_llm):
                verdict = pipeline.reviewer.run(question=requirements.get("source", question), evidence=package,
                    draft_answer=compiled.markdown, history_context=history_context + "\n预检查：" + json.dumps(compiled.diagnostics, ensure_ascii=False), scheme=doc["scheme"],
                    claimed_energy=pipeline._extract_energy_claim(compiled.markdown))
            return verdict
        verdict = request(state, "scientific_review", signature, save, invoke)
        doc["research"] = apply_support(doc["research"], verdict.get("research_support_review"), doc, parameters, package)
        state.update(reviewer_output=verdict, research=doc["research"], scientific_input_fingerprint=compiled.render_fingerprint)
        doc["reviewer_output"] = verdict
        save()
        return verdict
    def repair_once(issues, *, final=False):
        nonlocal content, parameters, compiled
        permit = authorize(content, parameters, compiled, issues, requirements)
        if not permit["interfaces"]:
            return False
        def invoke():
            with single_attempt(pipeline.writer_llm):
                raw = pipeline.writer_llm.chat("只修程序授权内容树叶片段及未锁定参数；禁止全文替换、改标题/ID/行列/引用身份、删有效内容或改来源。",
                    json.dumps({"permit": permit, "content": content, "parameter_view": parameters, "full_markdown": compiled.markdown,
                        "requirements": requirements, "evidence": state["evidence"],
                        "response_shape": {"base_hash": permit["base_hash"], "transactions": [{"interface_ids": [], "replacements": [{"interface_id": "", "text": ""}], "parameters": {}, "additions": []}]}}, ensure_ascii=False),
                    temperature=.1, json_mode=True, trace_tag="writer_v3_patch")
            return {"raw_response": raw, "response_meta": copy.deepcopy(getattr(pipeline.writer_llm, "last_response", {}))}
        response = request(state, "repair", permit["base_hash"], save, invoke)
        before = digest({"content": content, "parameters": parameters})
        try:
            changed, fields, log = apply_patch(content, parameters, compiled, permit, decode_document(response["raw_response"]), requirements, state["evidence"])
            state["repair_audit"].append({"permit": permit, "response": response, "transactions": log, "reserved_final": final})
            if digest({"content": changed, "parameters": fields}) == before:
                save()
                return False
            candidate_compiled = compile_document(changed, fields, requirements, state["evidence"])
            verification_input = {"permit": permit, "before_markdown": compiled.markdown, "after_markdown": candidate_compiled.markdown,
                "before_parameters": parameters, "after_parameters": fields, "requirements": requirements, "evidence": state["evidence"]}
            def verify():
                with single_attempt(pipeline.reviewer_llm):
                    raw = pipeline.reviewer_llm.chat(
                        "只复核已授权事务：是否解决原问题、保留用户条件和科学边界、是否引入新矛盾。不要假定补丁正确。"
                        "返回JSON{base_hash:给定基准,interfaces:[{id:接口ID,resolved:布尔,reason:具体依据}],new_issues:[]}；"
                        "每个已提交接口恰好一次。new_issues每项含interface_id和reason。不能改写正文或参数。",
                        json.dumps(dict(verification_input, base_hash=permit["base_hash"]), ensure_ascii=False), temperature=.1, json_mode=True, trace_tag="repair_verify")
                return {"raw_response": raw, "response_meta": copy.deepcopy(getattr(pipeline.reviewer_llm, "last_response", {}))}
            response_verify = request(state, "repair_verify", digest(verification_input), save, verify)
            verified = decode_document(response_verify["raw_response"])
            submitted = {interface for row in log if row.get("status") == "APPLIED" for interface in row["interface_ids"]}
            rows = verified.get("interfaces")
            if (set(verified) != {"base_hash", "interfaces", "new_issues"} or verified["base_hash"] != permit["base_hash"]
                    or not isinstance(rows, list) or len(rows) != len(submitted) or any(not isinstance(v, dict) or set(v) != {"id", "resolved", "reason"}
                        or type(v["resolved"]) is not bool or not isinstance(v["reason"], str) or not v["reason"].strip() for v in rows)
                    or {v["id"] for v in rows} != submitted or not isinstance(verified.get("new_issues"), list)):
                raise ValueError("事务语义复核协议无效，撤销候选补丁")
            failed = {v["id"] for v in rows if not v["resolved"]}
            for issue in verified["new_issues"]:
                if not isinstance(issue, dict) or not isinstance(issue.get("interface_id"), str) or issue["interface_id"] not in submitted or not issue.get("reason"):
                    raise ValueError("语义复核新增问题未定位到授权事务")
                failed.add(issue["interface_id"])
            if failed:
                patch = decode_document(response["raw_response"])
                retained = [t for t in patch["transactions"] if not set(t["interface_ids"]) & failed]
                changed, fields, retained_log = apply_patch(content, parameters, compiled, permit,
                    {"base_hash": patch["base_hash"], "transactions": retained}, requirements, state["evidence"])
                state["repair_audit"][-1]["semantic_rejections"] = sorted(failed)
                state["repair_audit"][-1]["retained_transactions"] = retained_log
                if digest({"content": changed, "parameters": fields}) == before:
                    save()
                    return False
            state["repair_audit"][-1]["verification"] = verified
            content, parameters = changed, fields
            state["content_revision"] += 1
            doc["content_revision"] = state["content_revision"]
            compiled = compile_current()
            return True
        except (ValueError, KeyError, TypeError) as exc:
            state["repair_audit"].append({"permit": permit, "response": response, "status": "REJECTED", "reason": str(exc)})
            save()
            return False
    try:
        review = scientific_review()
        # 常规及终审轮数均由配置决定，终审修复所需机会提前预留。
        reserved = limits["v3_final_rounds"]
        while state["regular_rounds"] < limits["v3_regular_rounds"]:
            completed, passed = review_verdict(compiled.markdown, review, requirements)
            if passed or available(state, "repair") <= reserved or available(state, "scientific_review") <= reserved:
                break
            state["regular_rounds"] += 1
            save()
            # 复用已有检索窗口和策略，更新后必须重新绑定科学审核。
            after_review(pipeline, state, review, save)
            if state["evidence"] != doc["evidence"]:
                doc["evidence"] = state["evidence"]
                compiled = compile_current()
                review = scientific_review()
                continue
            if not repair_once(review.get("repair_issues", [])):
                break
            review = scientific_review()
    except Exception as exc:
        review = {"confidence": "low", "error_type": "reviewer", "issues": [str(exc)]}
    doc["reviewer_output"] = review
    state["phase"] = "consistency"
    save()
    final_review = audit_consistency(pipeline.reviewer_llm, state, doc, compiled, save)
    while state["final_rounds"] < limits["v3_final_rounds"]:
        if not (final_review["status"] == "issues" and available(state, "repair") > 0
                and available(state, "scientific_review") > 0 and available(state, "consistency") > 0):
            break
        state["final_rounds"] += 1
        save()
        try:
            if repair_once(final_review["issues"], final=True):
                review = scientific_review()
                doc["reviewer_output"] = review
                final_review = audit_consistency(pipeline.reviewer_llm, state, doc, compiled, save)
            else:
                break
        except Exception as exc:
            final_review = dict(final_review, repair_error=str(exc))
            break
    completed, passed = review_verdict(compiled.markdown, review, requirements)
    pending = review_pending(doc["research"], doc, parameters)
    from .requirement_review import normalize_review
    coverage = normalize_review(review, doc)
    coverage_ok = (coverage.get("source_checked") is True and not coverage.get("missing_quotes")
                   and not coverage.get("misread_ids") and not coverage.get("unanswered_ids") and not coverage.get("misread_diagnostics"))
    overall = passed and coverage_ok and not pending and final_review["status"] == "passed" and not state.get("geometry_error")
    rules = pipeline.relation_engine.check_scheme(doc["scheme"], claimed_energy=pipeline._extract_energy_claim(compiled.markdown), answer_text=compiled.markdown) if pipeline.relation_engine else {}
    rc = rules.get("rule_checks") or {}
    overall = overall and bool(rules) and not rc.get("violations") and not rc.get("rejects") and not rc.get("unverified") and rules.get("confidence") != "low"
    result = {**doc, "plan": plan, "retrieval": state["retrieval"], "retrieval_state": state.get("retrieval_state"),
        "final_answer": compiled.markdown, "writer_output": output, "writer_contract_version": 3,
        "reviewer_output": review, "review_completed": completed, "review_passed": overall,
        "consistency_review": final_review, "rule_checks": rules, "confidence": review.get("confidence", "low"),
        "geometry_candidate": state.get("geometry_candidate"), "geometry_error": state.get("geometry_error"),
        "repair_parameters": parameters, "scheme_parameters": parameters, "repair_audit": state["repair_audit"],
        "content_revision": state["content_revision"], "scientific_input_fingerprint": state.get("scientific_input_fingerprint"),
        "stage4_requests": copy.deepcopy(state["requests"])}
    state.update(result=copy.deepcopy(result), research=doc["research"], reviewer_output=review, consistency_review=final_review, phase="complete")
    save()
    return result


def document_errors(doc):
    """门禁从权威内容和当前参数重编译，不信任保存的投影。"""
    if doc.get("writer_contract_version") != 3:
        return []
    try:
        parameters = (doc.get("ledger_view") or {}).get("parameters", {})
        compiled = compile_document(doc["writer_content"], parameters, doc.get("requirements"), doc.get("evidence", []))
        errors = [d["reason"] for d in compiled.diagnostics if d["severity"] == "error"]
        for key, expected in (("content_hash", compiled.content_hash), ("render_fingerprint", compiled.render_fingerprint), ("final_answer", compiled.markdown)):
            if doc.get(key) != expected:
                errors.append("v3正文投影失效：" + key)
        for name in ("parameters", "scheme_parameters", "repair_parameters"):
            if name in doc and doc[name] != parameters:
                errors.append("v3当前参数投影与方案表不一致：" + name)
        from .research_support import projection
        for collection in ("answers", "candidates", "arguments"):
            actual = [projection(row) for row in (doc.get("research") or {}).get(collection, [])]
            expected = [projection(row) for row in compiled.research.get(collection, [])]
            if actual != expected:
                errors.append("v3研究投影失效：" + collection)
        if doc.get("review_status") == "APPROVED":
            consistency = doc.get("consistency_review") or {}
            if consistency.get("status") != "passed" or consistency.get("input_fingerprint") != compiled.render_fingerprint:
                errors.append("v3当前一致性终审缺失或陈旧")
            if doc.get("scientific_input_fingerprint") != compiled.render_fingerprint or not doc.get("review_passed"):
                errors.append("v3当前科学审核缺失或陈旧")
        return errors
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return ["v3状态损坏：" + str(exc)]


def stage5_final_review(task_dir, doc, client, *, repair_budget=None):
    """最终几何按配置配额只读复核，同一输入不重发，不恢复科学支持。"""
    if doc.get("writer_contract_version") != 3:
        return doc
    old = doc.get("consistency_review") or {}
    if old.get("status") != "stale":
        return doc
    path = Path(task_dir) / "stage4_repair_checkpoint.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("writer_contract_version") != 3:
        raise ValueError("Stage 5复核会话不匹配")
    from .runtime_budgets import checkpoint_repair_options
    state["repair_budget"] = checkpoint_repair_options(state, repair_budget)
    def save():
        atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2))
    compiled = compile_document(doc["writer_content"], doc["ledger_view"]["parameters"], doc.get("requirements"), doc.get("evidence", []))
    before = state.get("parameters") or {}
    differences = {k: {"before": before.get(k), "after": v} for k, v in doc["ledger_view"]["parameters"].items() if before.get(k) != v}
    if not differences and compiled.render_fingerprint == state.get("scientific_input_fingerprint"):
        return doc
    review = audit_consistency(client, state, doc, compiled, save, stage5=True, differences=differences)
    doc["consistency_review"] = review
    doc["stage4_requests"] = copy.deepcopy(state["requests"])
    doc["review_passed"] = False
    doc["review_status"] = "REVIEWED_WITH_ISSUES"
    from .stage4_publication import publish_document
    return publish_document(task_dir, doc, reason="Stage 5最终几何一致性复核")
