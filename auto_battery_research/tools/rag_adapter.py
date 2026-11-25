"""AbrRagAdapter — 连接 AutoBatteryResearch 阶段编排与 src/lmllm/RAG 引擎的适配器.

职责分离:
- src/lmllm/RAG: 负责真实多智能体检索 (Hybrid/Chroma/BM25)、Planner、Writer、Reviewer 及 RelationEngine 规则核算。
- AbrRagAdapter: 负责将 RAGPipeline 的完整执行结果转化为 Stage 4 标准产物契约并持久化。
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

from auto_battery_research.util.logger import (
    log_tool_call,
    log_observation,
    log_thought,
    log_info,
    log_success,
    log_error,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


class AbrRagAdapter:
    """AutoBatteryResearch RAG 适配器."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, pipeline: Optional[Any] = None):
        self.config = config or {}
        if pipeline is not None:
            # 显式注入管线 (测试/复现场景): 跳过真实 Chroma/LLM 初始化，
            # 直接复用调用方构造好的 RAGPipeline 实例
            self.pipeline = pipeline
            self._init_error = None
            return
        self._init_pipeline()

    def _init_pipeline(self) -> None:
        """初始化底层 src.lmllm.RAG.RAGPipeline 实例."""
        self.pipeline = None
        self._init_error = None
        
        paths = self.config.get("paths", {})
        llm_cfg = self.config.get("llm", {})
        openai_cfg = self.config.get("openai", {})

        chroma_dir = str(ROOT_DIR / paths.get("chroma_dir", "miner/chroma/paragraphs_q"))
        chroma_col = paths.get("chroma_collection", "battery_paragraphs_q")
        
        api_key = openai_cfg.get("openai_api_key") or llm_cfg.get("api_key") or os.getenv("OPENAI_API_KEY")
        api_base = openai_cfg.get("openai_api_base") or llm_cfg.get("base_url") or os.getenv("OPENAI_API_BASE", "https://api.minimaxi.com/v1")
        model_name = llm_cfg.get("model") or openai_cfg.get("model_name") or os.getenv("OPENAI_MODEL", "MiniMax-M2.7-highspeed")
        planner_model = llm_cfg.get("planner_model", model_name)
        writer_model = llm_cfg.get("writer_model", model_name)
        reviewer_model = llm_cfg.get("reviewer_model", model_name)

        try:
            from auto_battery_research.rag import RAGPipeline
            self.pipeline = RAGPipeline(
                chroma_dir=chroma_dir,
                chroma_collection=chroma_col,
                retrieval_mode="hybrid",
                llm_backend="auto",
                llm_model=model_name,
                planner_model=planner_model,
                writer_model=writer_model,
                reviewer_model=reviewer_model,
                reranker_enabled=False,
                api_key=api_key,
                api_base=api_base,
                max_tokens=int(os.getenv("LLM_MAX_TOKENS", llm_cfg.get("max_tokens", 16384))),
                output_max_tokens=int(llm_cfg.get("output_max_tokens", 32768)),
            )
        except Exception as e:
            self._init_error = str(e)
            log_error(f"RAGPipeline 初始化受阻: {e}")

    def run_rag_design(self, target_query: str, task_dir: Path, task_goal: Optional[str] = None) -> Dict[str, Any]:
        """执行 Stage 4 多智能体 RAG 研发并生成标准契约文件 (仅写入课题专属目录)."""
        if self.pipeline is None:
            err_msg = f"RAGPipeline 未就绪: {self._init_error}。请安装完整 RAG 依赖 (pip install 'auto-battery-research[rag]')"
            log_error(err_msg)
            return {"success": False, "error": err_msg}

        task_dir.mkdir(parents=True, exist_ok=True)
        publication_path = task_dir / "stage4_publication.json"
        if publication_path.exists() and not (task_dir / "stage4_rebuild_requested.json").exists():
            from auto_battery_research.util.stage4_publication import recover_publication
            try:
                recover_publication(task_dir)
            except (ValueError, OSError) as exc:
                return {"success": False, "error_code": "PUBLICATION_RECOVERY_BLOCKED", "error": str(exc)}
        from auto_battery_research.util.scheme_ledger import ensure
        ledger = ensure(task_dir, task_goal or target_query)
        from auto_battery_research.util.scheme_ledger import digest
        self.pipeline.repair_checkpoint_path = task_dir / "stage4_repair_checkpoint.json"
        repair_provenance = self._build_provenance()
        from auto_battery_research.util.research_contract import VERSION as RESEARCH_VERSION
        repair_provenance["research_contract_version"] = RESEARCH_VERSION
        from auto_battery_research.util.adaptive_retrieval import VERSION as RETRIEVAL_VERSION
        repair_provenance["adaptive_retrieval_version"] = RETRIEVAL_VERSION
        repair_provenance.pop("recorded_at", None)
        from auto_battery_research.util.material_ids import checkpoint_identity
        self.pipeline.repair_checkpoint_identity = checkpoint_identity(task_goal or target_query, ledger, repair_provenance)
        def persist_requirements(requirements):
            nonlocal ledger
            from auto_battery_research.util.scheme_ledger import persist_requirement_bindings
            ledger = persist_requirement_bindings(task_dir, requirements, ledger["revision"])
            self.pipeline.scheme_context = ledger
            self.pipeline.repair_checkpoint_identity = checkpoint_identity(task_goal or target_query, ledger, repair_provenance)
            return ledger
        self.pipeline.persist_requirements = persist_requirements
        self.pipeline.repair_resume = None
        self.pipeline.retrieval_resume = None
        self.pipeline.retrieval_options = (self.config.get("runtime_options") or {}).get("stage4_retrieval", {})
        from auto_battery_research.util.runtime_budgets import repair_options, budget_options
        self.pipeline.stage4_repair_options = repair_options((self.config.get("runtime_options") or {}).get("stage4_repair"))
        self.pipeline.retrieval_options = budget_options("stage4_retrieval", self.pipeline.retrieval_options)
        self.pipeline.report_first = (self.config.get("runtime_options") or {}).get("report_first", True)
        rebuilding = (task_dir / "stage4_rebuild_requested.json").exists()
        ready_file = task_dir / "design_scheme.json"
        configured = ((self.config.get("runtime_options") or {}).get("stage4_writer") or {}).get("contract_version", 2)
        if type(configured) is not int or configured not in (2, 3):
            return {"success": False, "error_code": "WRITER_CONTRACT_INVALID", "error": "stage4_writer.contract_version只允许2或3"}
        self.pipeline.writer_contract_version = configured
        # 已有会话版本固定，配置切换不能自动迁移历史设计或检查点。
        if not rebuilding:
            for session_file in (self.pipeline.repair_checkpoint_path, ready_file, task_dir / "stage4_retrieval_checkpoint.json", task_dir / "stage4_planning_checkpoint.json"):
                if session_file.exists():
                    try:
                        saved_version = json.loads(session_file.read_text(encoding="utf-8"))
                        self.pipeline.writer_contract_version = saved_version.get("writer_contract_version", 2)
                    except (OSError, ValueError):
                        return {"success": False, "error_code": "REPAIR_CHECKPOINT_INVALID", "error": "设计会话损坏，禁止自动切换契约重新生成"}
                    break
            if self.pipeline.writer_contract_version == 3:
                from auto_battery_research.util.stage4_publication import recover_publication, publication_errors
                try:
                    recover_publication(task_dir)
                except (ValueError, OSError) as exc:
                    return {"success": False, "error_code": "PUBLICATION_RECOVERY_BLOCKED", "error": str(exc)}
                if ready_file.exists():
                    ready = json.loads(ready_file.read_text(encoding="utf-8"))
                    from auto_battery_research.util.stage4_session import document_errors
                    from auto_battery_research.util.scheme_ledger import projection_errors
                    stale = publication_errors(task_dir) + document_errors(ready) + projection_errors(task_dir, ready)
                    if stale:
                        return {"success": False, "error_code": "V3_PUBLICATION_INVALID", "error": "；".join(stale)}
                    return {"success": True, "review_status": ready["review_status"], "retryable": False,
                            "message": "当前v3设计已发布，保留原审核状态并继续下游阶段"}
        if ready_file.exists() and not rebuilding:
            try:
                ready = json.loads(ready_file.read_text(encoding="utf-8"))
                from auto_battery_research.util.report_readiness import check
                if isinstance(ready, dict) and ready.get("delivery_contract") == 1 and not check(task_dir, ready):
                    from auto_battery_research.util.adaptive_retrieval import has_pending, cache_resume_matches
                    saved = json.loads(self.pipeline.repair_checkpoint_path.read_text(encoding="utf-8")) if self.pipeline.repair_checkpoint_path.exists() else {}
                    from auto_battery_research.util.design_repair import structure_recovery_available
                    structure_pending = structure_recovery_available(task_dir, ready, saved, budget=self.pipeline.stage4_repair_options)
                    if structure_pending or has_pending(saved, retrieval_options=self.pipeline.retrieval_options,
                                                        repair_options=self.pipeline.stage4_repair_options):
                        if not cache_resume_matches(saved, ready, repair_provenance):
                            if structure_pending:
                                return {"success": False, "error_code": "REPAIR_CHECKPOINT_STALE",
                                        "error": "正文修复检查点与已归档方案或证据版本不一致"}
                            return {"success": False, "error_code": "RETRIEVAL_CHECKPOINT_STALE", "error": "待补检索检查点与当前方案或证据版本不一致，不能复用"}
                        self.pipeline.repair_resume = saved
                    else:
                        return {"success": True, "review_status": ready["review_status"], "retryable": False,
                            "message": "已有可交付方案且无有效待执行补检索，继续 Stage 5", "report_readiness": ready["report_readiness"]}
            except (OSError, ValueError):
                pass
        if self.pipeline.repair_checkpoint_path.exists() and not rebuilding and not self.pipeline.repair_resume:
            try:
                saved = json.loads(self.pipeline.repair_checkpoint_path.read_text(encoding="utf-8"))
                if saved.get("checkpoint_identity") == self.pipeline.repair_checkpoint_identity:
                    self.pipeline.repair_resume = saved
                else:
                    return {"success": False, "error_code": "REPAIR_CHECKPOINT_STALE",
                            "error": "修复检查点与当前目标、方案表或语料版本不一致，请明确重新设计，不能自动全文重跑"}
            except (ValueError, OSError) as exc:
                return {"success": False, "error_code": "REPAIR_CHECKPOINT_INVALID", "error": str(exc)}

        retrieval_path = self.pipeline.repair_checkpoint_path.with_name("stage4_retrieval_checkpoint.json")
        if retrieval_path.exists() and not rebuilding and not self.pipeline.repair_resume:
            try:
                saved = json.loads(retrieval_path.read_text(encoding="utf-8"))
                if saved.get("checkpoint_identity") != self.pipeline.repair_checkpoint_identity:
                    return {"success": False, "error_code": "RETRIEVAL_CHECKPOINT_STALE", "error": "检索检查点与课题、方案或语料版本不一致，请显式重新设计"}
                self.pipeline.retrieval_resume = saved
            except (ValueError, OSError):
                return {"success": False, "error_code": "RETRIEVAL_CHECKPOINT_INVALID", "error": "检索检查点损坏，不自动重置预算"}

        scheme_md_file = task_dir / "design_scheme.md"
        scheme_json_file = task_dir / "design_scheme.json"
        raw_rag_file = task_dir / "rag_result.json"
        if not rebuilding and not self.pipeline.repair_resume and scheme_json_file.exists():
            try:
                existing = json.loads(scheme_json_file.read_text(encoding="utf-8"))
                if existing.get("target") != (task_goal or target_query) or not existing.get("evidence"):
                    return {"success": False, "error_code": "REPAIR_BASE_UNAVAILABLE", "error": "历史设计目标或证据不匹配，请明确重新设计"}
                from auto_battery_research.util.scheme_ledger import projection_errors
                if projection_errors(task_dir, existing):
                    return {"success": False, "error_code": "REPAIR_CHECKPOINT_STALE", "error": "历史设计方案表版本失效，请明确重新设计"}
                raw = json.loads(raw_rag_file.read_text(encoding="utf-8")) if raw_rag_file.exists() else {}
                historical_text = existing.get("final_answer", "").split("\n\n## 参考来源")[0]
                self.pipeline.repair_resume = {"draft": historical_text,
                    "scheme": existing.get("scheme", {}), "evidence": existing["evidence"],
                    "plan": existing.get("plan", {}), "retrieval": existing.get("retrieval", {}),
                    "geometry_candidate": raw.get("geometry_candidate"), "geometry_error": raw.get("geometry_error"),
                    "checkpoint_identity": self.pipeline.repair_checkpoint_identity}
            except (ValueError, OSError) as exc:
                return {"success": False, "error_code": "REPAIR_BASE_UNAVAILABLE", "error": str(exc)}

        log_thought(f"调度 RAGPipeline (Planner -> Retrieval -> Writer -> Reviewer+RelationEngine) 执行 '{target_query}'...")
        log_tool_call("RAGPipeline", f"query='{target_query}', mode='hybrid'")

        # 1. 运行真正的 RAG 引擎流水线
        self.pipeline.structured_geometry_enabled = True
        self.pipeline.original_design_request = getattr(self, "original_design_request", target_query)
        self.pipeline.requirement_source = task_goal or target_query
        self.pipeline.scheme_context = ledger
        self.pipeline.inherited_design = None
        if scheme_json_file.exists():
            try:
                existing = json.loads(scheme_json_file.read_text(encoding="utf-8"))
                from auto_battery_research.simulation.geometry_contract import validate_geometry_contract
                geo = existing.get("scheme", {}).get("geometry", {})
                declared = ledger["parameters"]
                if geo and not validate_geometry_contract(geo, existing["scheme"], existing.get("evidence", [])) and all(
                    (declared.get(f"{c}.{f}") or {}).get("value") == (geo.get(c) or {}).get(f)
                    for c in ("cathode", "anode") for f in ("L_um", "porosity", "R_s_um", "D_s_m2s")):
                    self.pipeline.inherited_design = existing["scheme"]
            except (ValueError, KeyError, TypeError):
                pass
        if self.pipeline.inherited_design is None:
            try:
                from auto_battery_research.simulation.geometry_contract import build_geometry_contract
                params = ledger["parameters"]
                from auto_battery_research.util.material_ids import assemble_components
                materials = assemble_components({}, params)
                if all(materials.get(k) for k in ("cathode", "anode", "electrolyte")):
                    candidate = {c: {f: (params.get(f"{c}.{f}") or {}).get("value") for f in ("L_um", "porosity", "R_s_um")} for c in ("cathode", "anode")}
                    candidate["condition"] = {k: (params.get(f"condition.{k}") or {}).get("value") for k in ("v_min", "v_max", "c_rate")}
                    geo = build_geometry_contract(candidate, materials, [])
                    if all((params.get(f"{c}.D_s_m2s") or {}).get("value") in (None, geo[c]["D_s_m2s"]) for c in ("cathode", "anode")):
                        self.pipeline.inherited_design = dict(materials, geometry=geo)
            except (ValueError, TypeError, KeyError):
                pass
        rag_raw = self.pipeline.run(target_query)

        if rag_raw.get("writer_contract_version") == 3:
            return self._publish_v3(task_dir, ledger, rag_raw, task_goal or target_query, target_query, rebuilding)

        final_answer = rag_raw.get("final_answer", "")
        plan = rag_raw.get("plan", {})
        retrieval = rag_raw.get("retrieval", {})
        evidence = rag_raw.get("evidence", [])
        reviewer_output = rag_raw.get("reviewer_output", {})
        rule_checks = rag_raw.get("rule_checks", {})
        confidence = rag_raw.get("confidence", "high")
        if plan.get("requirements"):
            # 注入/恢复管线同样登记；候选提交失败不能撤销经原文核验的用户条件。
            persist_requirements(plan["requirements"])
        from auto_battery_research.util.material_ids import canonical_parameters, assemble_components, component_signature, declared_anode_parameters, VERSION as MATERIAL_ID_VERSION
        accepted_parameters = canonical_parameters({**((rag_raw.get("research_draft") or {}).get("scheme_parameters") or {}),
                                                    **(rag_raw.get("repair_parameters") or {})})
        accepted_parameters.update(declared_anode_parameters({"final_answer": final_answer}, {**ledger["parameters"], **accepted_parameters}))
        scheme = assemble_components(rag_raw.get("scheme", {}), {**ledger["parameters"], **accepted_parameters}, strict=False)
        if component_signature(scheme) != component_signature(rag_raw.get("scheme")):
            engine = getattr(self.pipeline, "relation_engine", None)
            # 投影改变后不得复用旧材料上的通过结果；无引擎时保持 fail-closed。
            energy_parser = getattr(self.pipeline, "_extract_energy_claim", None)
            rule_checks = engine.check_scheme(scheme, claimed_energy=energy_parser(final_answer) if callable(energy_parser) else None,
                                              answer_text=final_answer) if engine else {}
        rag_raw["scheme"] = scheme

        log_observation(
            f"RAGPipeline 执行完成: 召回 {len(evidence)} 条证据, 综合置信度 [{confidence}], Reviewer [{reviewer_output.get('confidence', '未提供')}], "
            f"抽取配方: {scheme.get('cathode', 'N/A')} + {scheme.get('anode', 'N/A')} + {scheme.get('electrolyte', 'N/A')}"
        )

        # 2. 严格 Fail-Closed 门禁校验 (先校验后落盘，结果以 review_status 固化进契约)
        errors = []
        errors.extend(scheme.get("component_identity_errors") or [])
        from auto_battery_research.util.design_sections import design_output_issues
        errors.extend(design_output_issues(final_answer, plan.get("requirements")))
        if len(evidence) < 1:
            errors.append("未召回任何有效文献证据 (evidence_count == 0)")
        if rag_raw.get("review_completed", rag_raw.get("review_passed")) is False:
            errors.append("正文结构不完整，尚未完成审核" if reviewer_output.get("error_type") == "structure" else "Reviewer 审核未完成，草稿未经审核")
        reviewer = rag_raw.get("reviewer_output") or {}
        if reviewer.get("error_type") == "reviewer":
            errors.append("Reviewer 响应失败 (空输出/JSON 无效/调用异常)")
        needs_scheme = (plan.get("requirements") or {}).get("scheme_required", True)
        if needs_scheme:
            if not isinstance(scheme, dict) or not scheme.get("cathode") or not scheme.get("anode") or not scheme.get("electrolyte"):
                errors.append(f"结构化材料配方不完整 (缺少必要组件): {scheme}")
            violations = rule_checks.get("rule_checks", {}).get("violations") or rule_checks.get("violations", [])
            rejects = rule_checks.get("rule_checks", {}).get("rejects") or rule_checks.get("rejects", [])
            if violations or rejects:
                errors.append(f"RelationEngine 规则校验不通过: {violations or rejects}")
            unverified = rule_checks.get("rule_checks", {}).get("unverified") or rule_checks.get("unverified", [])
            if not rule_checks or unverified or rule_checks.get("energy_check") == "energy_mismatch" or rule_checks.get("confidence") == "low":
                errors.append("RelationEngine 校验缺失、存在未知材料或能量核算不一致")
        review_status = "REJECTED" if errors else "APPROVED"

        # 2.5 几何声明块归一化 (schema_version="1.1" / contract_version="2.0"):
        # scheme.geometry 必须显式给出 (逐字段值 + provenance: stage4_recipe/model_baseline)。
        # 归一化结果同步进 scheme (design_scheme.json 与 rag_result.json 共用同一 dict)。
        geometry_md = ""
        if needs_scheme and final_answer.strip():
            try:
                from pinn.input_spec import complete_geometry_block, format_geometry_markdown
                from auto_battery_research.simulation.geometry_contract import (
                    build_geometry_contract, geometry_hash, GEOMETRY_SYSTEM_PROMPT, validate_geometry_contract,
                )
                from auto_battery_research.tools.geometry_revision import resolve_input_ranges
                from lmllm.RAG.llm_client import safe_json_loads
                candidate = rag_raw.get("geometry_candidate")
                inherited = getattr(self.pipeline, "inherited_design", None)
                can_inherit = inherited and all(inherited.get(k) == scheme.get(k) for k in ("cathode", "anode", "electrolyte"))
                if rag_raw.get("geometry_error") and not can_inherit:
                    raise ValueError(rag_raw["geometry_error"])
                generated_here = candidate is None and not can_inherit
                if generated_here and self.pipeline.repair_resume:
                    from auto_battery_research.simulation.geometry_contract import geometry_from_declared_parameters
                    candidate, recovered = geometry_from_declared_parameters(
                        {**ledger["parameters"], **accepted_parameters}, scheme, evidence)
                    # 只继承本次验证的声明，不将恢复视为新的几何设计机会。
                    inherited = dict(scheme, geometry=recovered)
                    can_inherit, generated_here = True, False
                    rag_raw["geometry_candidate"] = candidate
                if generated_here:
                    llm = getattr(self.pipeline, "writer_llm", None)
                    if llm is None or not llm.available:
                        raise ValueError("结构化几何候选缺失且 LLM 不可用；请提供 geometry_candidate")
                    from auto_battery_research.util.physical_parameters import (
                        INSTRUCTIONS, fixed_geometry, fill_missing_candidate, record_geometry_sources,
                    )
                    fixed = fixed_geometry({**ledger["parameters"], **accepted_parameters})
                    request = {
                        "fixed_geometry": fixed,
                        "design_request": target_query,
                        "materials": {k: scheme.get(k) for k in ("cathode", "anode", "electrolyte")},
                        "ranges_SI": resolve_input_ranges(scheme.get("cathode"), scheme.get("anode"), scheme=scheme),
                        "evidence": evidence[:25],
                    }
                    raw = llm.chat(GEOMETRY_SYSTEM_PROMPT + "\n" + INSTRUCTIONS, json.dumps(request, ensure_ascii=False),
                                   temperature=0.1, json_mode=True, trace_tag="geometry_design")
                    from auto_battery_research.simulation.geometry_contract import constrain_generated_np
                    candidate = fill_missing_candidate(constrain_generated_np(safe_json_loads(raw), self.pipeline.original_design_request), fixed)
                normalized = json.loads(json.dumps(inherited["geometry"])) if can_inherit else build_geometry_contract(candidate, scheme, evidence)
                if generated_here:
                    record_geometry_sources(normalized, fixed)
                elif not can_inherit and rag_raw.get("scheme", {}).get("geometry", {}).get("contract_version") == "2.0":
                    from auto_battery_research.util.physical_parameters import fixed_geometry, record_geometry_sources
                    record_geometry_sources(normalized, fixed_geometry(ledger["parameters"]))
                contract_errors = validate_geometry_contract(normalized, scheme, evidence)
                if contract_errors:
                    raise ValueError("; ".join(contract_errors))
                if normalized is not None:
                    scheme["geometry"] = normalized
                    scheme["design_revision"] = 0
                    scheme["geometry_hash"] = geometry_hash(normalized)
                    scheme["initial_geometry_hash"] = scheme["geometry_hash"]
                    geometry_md = format_geometry_markdown(normalized)
                    rag_raw["geometry_candidate"] = candidate
                    rag_raw["scheme"] = scheme
            except Exception as geo_err:
                # 新任务禁止在结构化生成失败后静默采用旧正则/基准结果。
                scheme.pop("geometry", None)
                errors.append(f"结构化几何验证失败: {geo_err}")
                log_observation(errors[-1])
        review_status = "REJECTED" if errors else "APPROVED"
        if not errors and (rag_raw.get("review_passed") is False or reviewer_output.get("issues") or reviewer_output.get("confidence") == "low"):
            review_status = "REVIEWED_WITH_ISSUES"

        # 3. 规范化 Stage 4 标准输出契约 (含知识资产溯源，供科研可复现性回溯)
        report_first = (self.config.get("runtime_options") or {}).get("report_first", True)
        delivery_warnings = list(errors)
        if report_first:
            errors = []
        provenance = self._build_provenance()
        contract_payload = {
            "schema_version": "1.1",
            "target": task_goal or target_query,
            "design_request": target_query,
            "review_status": review_status,
            "review_passed": rag_raw.get("review_passed"),
            "review_completed": rag_raw.get("review_completed", rag_raw.get("review_passed")),
            "final_answer": final_answer,
            "design_structure": rag_raw.get("design_structure"),
            "numeric_contract": rag_raw.get("numeric_contract"),
            "plan": plan,
            "retrieval": {
                "db_type": retrieval.get("db_type", "hybrid"),
                "evidence_count": len(evidence),
                "sources": [e.get("source_display") or e.get("source") or e.get("doi") for e in evidence if isinstance(e, dict)],
            },
            "evidence": evidence,
            "reviewer_output": reviewer_output,
            "repair_audit": rag_raw.get("repair_audit", []),
            "repair_diagnoses": rag_raw.get("repair_diagnoses", []),
            "retrieval_state": rag_raw.get("retrieval_state"),
            "rule_checks": rule_checks,
            "confidence": confidence,
            "scheme": scheme,
            "scheme_parameters": accepted_parameters,
            "provenance": provenance,
            "artifacts": {
                "markdown": "design_scheme.md",
                "json": "design_scheme.json",
                "raw_rag": "rag_result.json",
                "research_context": "research_context.json",
            },
            "generated_at": datetime.now().isoformat(),
            "material_id_version": MATERIAL_ID_VERSION,
        }
        if plan.get("requirements"):
            import copy
            contract_payload["requirements"] = copy.deepcopy(plan["requirements"])
            contract_payload["ledger_view"] = copy.deepcopy(ledger)

        # 4. 持久化文件 (仅课题目录；无论验收通过与否均落盘，REJECTED 产物保留供诊断回溯)
        if report_first:
            from auto_battery_research.util.report_readiness import prepare
            prepare(contract_payload, ledger, delivery_warnings, rag_raw.get("geometry_candidate"))
            scheme = contract_payload["scheme"]
            review_status = contract_payload["review_status"]
            if delivery_warnings:
                geometry_md = ""
        if not errors:
            from auto_battery_research.util.scheme_ledger import commit_design, collect_parameters, restore_research_cache
            try:
                cached = rag_raw.get("research_draft")
                restored = restore_research_cache(contract_payload, cached)
                used = (cached or {}).get("maintenance_calls", 0)
                if final_answer.strip() and (not restored or (contract_payload.get("research") or {}).get("record_errors")):
                    collect_parameters(contract_payload, getattr(self.pipeline, "writer_llm", None) or getattr(self.pipeline, "llm", None),
                                       max_attempts=max(0, 2 - used))
                    contract_payload["maintenance_calls"] = used + contract_payload.get("maintenance_calls", 0)
                for key, field in (rag_raw.get("repair_parameters") or {}).items():
                    if field != ledger["parameters"].get(key) and field.get("source_quote"):
                        contract_payload.setdefault("scheme_parameters", {})[key] = field
                contract_payload["scheme_parameters"] = canonical_parameters(contract_payload.get("scheme_parameters"))
                if contract_payload.get("requirements"):
                    from auto_battery_research.util.research_contract import bindings
                    fixed = bindings(contract_payload["requirements"])
                    from auto_battery_research.util.requirement_review import normalize_review
                    requirement_review = normalize_review(reviewer_output, contract_payload)
                    if requirement_review["blocked_fields"]:
                        raise ValueError("用户固定字段误读证据已核验，禁止提交对应字段：" + ", ".join(requirement_review["blocked_fields"]))
                    extras = contract_payload.setdefault("scheme_parameters", {})
                    # 主方案只由已定位声明和用户固定值组成，候选全文提及不补写配方。
                    from auto_battery_research.util.research_contract import field_equal
                    for path, field in fixed.items():
                        previous = ledger["parameters"].get(path, {})
                        if previous.get("locked") and not field_equal(path, previous.get("value"), field["value"]):
                            raise ValueError("课题与已锁定字段冲突: " + path)
                    extras.update(fixed)
                # 无 requirements 的旧路径也不能在维护后复用旧材料上的约束结果。
                projected = assemble_components(scheme, {**ledger["parameters"], **contract_payload["scheme_parameters"]})
                if component_signature(projected) != component_signature(scheme):
                    engine = getattr(self.pipeline, "relation_engine", None)
                    energy_parser = getattr(self.pipeline, "_extract_energy_claim", None)
                    current_rules = engine.check_scheme(projected, claimed_energy=energy_parser(final_answer) if callable(energy_parser) else None,
                                                       answer_text=final_answer) if engine else {}
                    rule_errors = current_rules.get("rule_checks") or {}
                    if not current_rules or rule_errors.get("violations") or rule_errors.get("rejects") or current_rules.get("confidence") == "low":
                        raise ValueError("组件身份更新后材料约束未通过或尚未验证")
                    contract_payload["rule_checks"] = current_rules
                    if projected.get("geometry", {}).get("contract_version") == "2.0":
                        from auto_battery_research.simulation.geometry_contract import validate_geometry_contract
                        geometry_errors = validate_geometry_contract(projected["geometry"], projected, evidence)
                        if geometry_errors:
                            raise ValueError("; ".join(geometry_errors))
                scheme.clear()
                scheme.update(projected)
                if contract_payload.get("requirements"):
                    if "electrolyte.additives" in extras:
                        scheme["additives"] = extras["electrolyte.additives"]["value"]
                    else:
                        scheme.pop("additives", None)
                if report_first:
                    import copy
                    from auto_battery_research.util.scheme_ledger import calculate, project_scheme_parameters
                    preview = copy.deepcopy(ledger)
                    preview["parameters"].update(contract_payload.get("scheme_parameters") or {})
                    preview["parameters"] = project_scheme_parameters(scheme, preview["parameters"])
                    _, conflicts = calculate(preview)
                    if conflicts:
                        raise ValueError("; ".join(conflicts))
                commit_design(task_dir, contract_payload, base_revision=ledger["revision"])
                scheme = contract_payload["scheme"]
                if contract_payload.get("requirements"):
                    from auto_battery_research.util.research_contract import finalize_research
                    finalize_research(contract_payload)
                    review_status = contract_payload["review_status"]
            except ValueError as exc:
                if not report_first:
                    return {"success": False, "error_code": "SCHEME_LEDGER_CONFLICT", "error": str(exc)}
                from auto_battery_research.util.report_readiness import restore_diagnostic
                restore_diagnostic(contract_payload, ledger, str(exc))
                scheme = contract_payload["scheme"]
                final_answer = contract_payload["final_answer"]
                review_status = contract_payload["review_status"]
            from auto_battery_research.tools.workflow_actions import _atomic_write_text
            contract_payload["stage5_contract"] = 2
            snapshot = task_dir / "design_revisions" / f"revision_0_{scheme.get('geometry_hash', 'report')[:12]}.json"
            if not errors and not snapshot.exists():
                _atomic_write_text(snapshot, json.dumps(contract_payload, ensure_ascii=False, indent=2))
        # Markdown (final_answer 五段式 + 设计点几何声明表)
        with open(scheme_md_file, "w", encoding="utf-8") as f:
            f.write(final_answer if not geometry_md else final_answer + "\n\n---\n\n" + geometry_md + "\n")

        # JSON 契约
        with open(scheme_json_file, "w", encoding="utf-8") as f:
            json.dump(contract_payload, f, ensure_ascii=False, indent=2)

        # 原始 RAG 结果
        rag_raw["scheme"] = scheme
        rag_raw["rule_checks"] = contract_payload["rule_checks"]
        with open(raw_rag_file, "w", encoding="utf-8") as f:
            json.dump(rag_raw, f, ensure_ascii=False, indent=2)

        # 知识资产版本快照 (corpus 清单哈希 / 向量库指纹 / 规则版本)
        with open(task_dir / "research_context.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "target": task_goal or target_query,
                    "generated_at": contract_payload["generated_at"],
                    "review_status": review_status,
                    "provenance": provenance,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        if errors:
            log_error(f"Stage 4 未通过验收标准 (review_status=REJECTED): {'; '.join(errors)}")
            return {
                "success": False,
                "error": "; ".join(errors),
                "review_status": review_status,
                "scheme_file": str(scheme_md_file),
                "scheme_json": str(scheme_json_file),
                "raw_rag_file": str(raw_rag_file),
                "message": f"Stage 4 方案未通过验收: {'; '.join(errors)}",
                "deliverables": [str(scheme_md_file), str(scheme_json_file), str(raw_rag_file)],
                "key_findings": {
                    "target_query": target_query,
                    "evidence_count": len(evidence),
                    "confidence": confidence,
                    "scheme": scheme,
                    "rule_checks": rule_checks,
                    "errors": errors,
                },
            }

        log_success(f"Stage 4 契约产物已生成 (review_status={review_status}): {scheme_md_file} & {scheme_json_file}")
        return {
            "success": True,
            "review_status": review_status,
            "scheme_file": str(scheme_md_file),
            "scheme_json": str(scheme_json_file),
            "raw_rag_file": str(raw_rag_file),
            "message": ("Stage 4 仅生成诊断交付，Writer正文或学术审核未完成" if not contract_payload.get("review_completed") else
                        "Stage 4 已生成附条件候选，未解决审核与规则意见见报告" if review_status == "REVIEWED_WITH_ISSUES" else
                        "多智能体 RAG 方案设计完成并已通过规则校验") + (
                "；审核仍有未解决意见，仅作为待验证候选推进，不能称为审核通过" if review_status == "REVIEWED_WITH_ISSUES" else ""),
            "journal_notes": (
                f"基于 RAGPipeline ({len(evidence)} 证据段落, 置信度 {confidence}) "
                + ("生成诊断交付，设计正文或学术审核尚未完成。" if not contract_payload.get("review_completed") else
                   "生成附条件候选，未解决意见随报告保留。" if review_status == "REVIEWED_WITH_ISSUES" else
                   "完成多智能体方案设计，RelationEngine 规则审查通过。")
            ),
            "deliverables": [str(scheme_md_file), str(scheme_json_file), str(raw_rag_file)],
            "key_findings": {
                "target_query": target_query,
                "review_completed": rag_raw.get("review_completed", rag_raw.get("review_passed")),
                "review_passed": rag_raw.get("review_passed"),
                "unresolved_review_issues": reviewer_output.get("issues", []),
                "evidence_count": len(evidence),
                "confidence": confidence,
                "scheme": scheme,
                "rule_checks": rule_checks,
            },
        }

    def _publish_v3(self, task_dir, ledger, result, goal, query, rebuilding=False):
        """新契约直接发布审核内容与研究投影，撤下二次提取及整合改写。"""
        import copy
        from auto_battery_research.util.scheme_ledger import digest
        from auto_battery_research.util.stage4_publication import publish_document
        doc = copy.deepcopy(result)
        doc.update(schema_version="1.1", target=goal, design_request=query,
                   stage5_contract=2, ledger_view=copy.deepcopy(ledger), ledger_revision=ledger["revision"], ledger_hash=digest(ledger),
                   provenance=self._build_provenance(), artifacts={"markdown": "design_scheme.md", "json": "design_scheme.json", "raw_rag": "rag_result.json", "research_context": "research_context.json"},
                   generated_at=datetime.now().isoformat())
        if not doc.get("writer_content") or not doc.get("final_answer"):
            # 不可编译稿仅归档原稿/诊断，不替代当前已接受方案。
            from auto_battery_research.util.scheme_ledger import atomic_write
            atomic_write(task_dir / "stage4_v3_diagnostic.json", json.dumps(doc, ensure_ascii=False, indent=2))
            return {"success": False, "error_code": "V3_CONTENT_INVALID", "error": "正文无法编译；原始响应和诊断已归档", "deliverables": [str(task_dir / "stage4_v3_diagnostic.json")]}
        warnings = [str(i) for i in (doc.get("reviewer_output") or {}).get("issues", [])]
        warnings += [d["reason"] for d in doc.get("precheck", []) if d.get("severity") == "error"]
        if doc.get("geometry_error"):
            warnings.append("物理输入待确认：" + doc["geometry_error"])
        consistency = doc.get("consistency_review") or {}
        if consistency.get("status") != "passed":
            warnings.append("当前一致性终审未通过或未完成")
            warnings += [i["reason"] for i in consistency.get("issues", [])]
        if not doc.get("review_passed"):
            warnings.append("当前候选尚有科学支持或规则缺口，不能认定审核通过")
        doc["review_status"] = "APPROVED" if not warnings and doc.get("review_passed") else "REVIEWED_WITH_ISSUES"
        doc["delivery_contract"] = 1
        doc["report_readiness"] = {"category": "COMPLETE" if doc["review_status"] == "APPROVED" else "CONDITIONAL",
            "issues": list(dict.fromkeys(warnings)), "review_status": "REVIEWED" if doc.get("review_completed") else "UNREVIEWED",
            "physics_status": "NOT_EVALUATED", "retryable": False}
        try:
            # 基准必须重读核验；隔离准备和真实发布由公共服务完成。
            publish_document(task_dir, doc, commit=True, base_revision=ledger["revision"], raw=result)
        except (ValueError, OSError) as exc:
            from auto_battery_research.util.scheme_ledger import atomic_write
            atomic_write(task_dir / "stage4_v3_diagnostic.json", json.dumps(dict(doc, publication_error=str(exc)), ensure_ascii=False, indent=2))
            return {"success": False, "error_code": "V3_PUBLICATION_FAILED", "error": str(exc)}
        return {"success": True, "review_status": doc["review_status"], "review_completed": doc.get("review_completed"),
            "review_passed": doc.get("review_passed"), "scheme_file": str(task_dir / "design_scheme.md"), "scheme_json": str(task_dir / "design_scheme.json"),
            "message": "v3科研内容已一致发布；审核及限制保留原状态", "deliverables": [str(task_dir / name) for name in ("design_scheme.json", "design_scheme.md", "rag_result.json", "research_context.json")],
            "key_findings": {"consistency_review": doc.get("consistency_review"), "review_passed": doc.get("review_passed"), "scheme": doc.get("scheme")}}

    # ────────────────────────── 知识资产溯源 ──────────────────────────

    @staticmethod
    def _dir_fingerprint(root: Path) -> Dict[str, Any]:
        """目录指纹: 相对路径 + 文件大小的有序清单 MD5 (轻量、跨平台、免读全文)."""
        if not root.exists():
            return {"exists": False, "files": 0, "hash": None}
        entries = []
        for p in sorted(root.rglob("*")):
            if p.is_file():
                try:
                    entries.append(f"{p.relative_to(root).as_posix()}:{p.stat().st_size}")
                except OSError:
                    continue
        import hashlib
        digest = hashlib.md5("\n".join(entries).encode("utf-8")).hexdigest() if entries else None
        return {"exists": True, "files": len(entries), "hash": digest}

    def _build_provenance(self) -> Dict[str, Any]:
        """固化 corpus / 向量库 / 规则版本指纹，写入每份方案以支持复现性审计."""
        import hashlib
        paths_cfg = self.config.get("paths", {})

        corpus_parts = {}
        for key, rel, default in (
            ("database_type", "database_type_dir", "database/type"),
            ("papers_merged", "papers_merged_dir", "papers/merged"),
            ("papers_text_merged_legacy", None, "papers/text_merged"),
        ):
            rel_path = rel and paths_cfg.get(rel) or default
            fp = self._dir_fingerprint(ROOT_DIR / rel_path)
            corpus_parts[key] = {"dir": rel_path, **fp}

        manifest_hash = hashlib.md5(
            json.dumps(corpus_parts, sort_keys=True).encode("utf-8")
        ).hexdigest()

        try:
            from auto_battery_research.rag import RULES_VERSION
            rules_version: str = RULES_VERSION
        except Exception:
            rules_version = "unknown"

        return {
            "corpus": {"manifest_hash": manifest_hash, "parts": corpus_parts},
            "vector_index": {
                "dir": paths_cfg.get("chroma_dir", "miner/chroma/paragraphs_q"),
                **self._dir_fingerprint(ROOT_DIR / paths_cfg.get("chroma_dir", "miner/chroma/paragraphs_q")),
            },
            "rules_version": rules_version,
            "recorded_at": datetime.now().isoformat(),
        }

