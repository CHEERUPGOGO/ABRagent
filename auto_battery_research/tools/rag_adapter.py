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

    def run_rag_design(self, target_query: str, task_dir: Path) -> Dict[str, Any]:
        """执行 Stage 4 多智能体 RAG 研发并生成标准契约文件 (仅写入课题专属目录)."""
        if self.pipeline is None:
            err_msg = f"RAGPipeline 未就绪: {self._init_error}。请安装完整 RAG 依赖 (pip install 'auto-battery-research[rag]')"
            log_error(err_msg)
            return {"success": False, "error": err_msg}

        task_dir.mkdir(parents=True, exist_ok=True)

        scheme_md_file = task_dir / "design_scheme.md"
        scheme_json_file = task_dir / "design_scheme.json"
        raw_rag_file = task_dir / "rag_result.json"

        log_thought(f"调度 RAGPipeline (Planner -> Retrieval -> Writer -> Reviewer+RelationEngine) 执行 '{target_query}'...")
        log_tool_call("RAGPipeline", f"query='{target_query}', mode='hybrid'")

        # 1. 运行真正的 RAG 引擎流水线
        self.pipeline.structured_geometry_enabled = True
        self.pipeline.original_design_request = getattr(self, "original_design_request", target_query)
        rag_raw = self.pipeline.run(target_query)

        final_answer = rag_raw.get("final_answer", "")
        plan = rag_raw.get("plan", {})
        retrieval = rag_raw.get("retrieval", {})
        evidence = rag_raw.get("evidence", [])
        reviewer_output = rag_raw.get("reviewer_output", {})
        rule_checks = rag_raw.get("rule_checks", {})
        confidence = rag_raw.get("confidence", "high")
        scheme = rag_raw.get("scheme", {})

        log_observation(
            f"RAGPipeline 执行完成: 召回 {len(evidence)} 条证据, 综合置信度 [{confidence}], Reviewer [{reviewer_output.get('confidence', '未提供')}], "
            f"抽取配方: {scheme.get('cathode', 'N/A')} + {scheme.get('anode', 'N/A')} + {scheme.get('electrolyte', 'N/A')}"
        )

        # 2. 严格 Fail-Closed 门禁校验 (先校验后落盘，结果以 review_status 固化进契约)
        errors = []
        from auto_battery_research.util.design_sections import design_output_issues
        errors.extend(design_output_issues(final_answer))
        if len(evidence) < 1:
            errors.append("未召回任何有效文献证据 (evidence_count == 0)")
        if rag_raw.get("review_completed", rag_raw.get("review_passed")) is False:
            errors.append("Reviewer 审核响应失败，草稿未经审核")
        reviewer = rag_raw.get("reviewer_output") or {}
        if reviewer.get("error_type") == "reviewer":
            errors.append("Reviewer 响应失败 (空输出/JSON 无效/调用异常)")
        if not isinstance(scheme, dict) or not scheme.get("cathode") or not scheme.get("anode") or not scheme.get("electrolyte"):
            errors.append(f"结构化材料配方不完整 (缺少必要组件): {scheme}")
        violations = rule_checks.get("rule_checks", {}).get("violations") or rule_checks.get("violations", [])
        rejects = rule_checks.get("rule_checks", {}).get("rejects") or rule_checks.get("rejects", [])
        if violations or rejects:
            errors.append(f"RelationEngine 硬约束校验不通过: {violations or rejects}")
        unverified = rule_checks.get("rule_checks", {}).get("unverified") or rule_checks.get("unverified", [])
        if not rule_checks or unverified or rule_checks.get("energy_check") == "energy_mismatch" or rule_checks.get("confidence") == "low":
            errors.append("RelationEngine 校验缺失、存在未知材料或能量核算不一致")

        review_status = "REJECTED" if errors else "APPROVED"

        # 2.5 几何声明块归一化 (产物契约 v1.1): scheme.geometry 从叙事稀疏提取
        # 升级为显式完整声明 (逐字段值 + provenance: stage4_recipe/params_baseline)。
        # 归一化结果同步进 scheme (design_scheme.json 与 rag_result.json 共用同一 dict)。
        geometry_md = ""
        try:
            from pinn.input_spec import complete_geometry_block, format_geometry_markdown
            from auto_battery_research.simulation.geometry_contract import (
                build_geometry_contract, geometry_hash, GEOMETRY_SYSTEM_PROMPT, validate_geometry_contract,
            )
            from auto_battery_research.tools.geometry_revision import resolve_input_ranges
            from lmllm.RAG.llm_client import safe_json_loads
            candidate = rag_raw.get("geometry_candidate")
            if rag_raw.get("geometry_error"):
                raise ValueError(rag_raw["geometry_error"])
            if candidate is None:
                llm = getattr(self.pipeline, "writer_llm", None)
                if llm is None or not llm.available:
                    raise ValueError("结构化几何候选缺失且 LLM 不可用；请提供 geometry_candidate")
                request = {
                    "design_request": target_query,
                    "materials": {k: scheme.get(k) for k in ("cathode", "anode", "electrolyte")},
                    "ranges_SI": resolve_input_ranges(scheme.get("cathode"), scheme.get("anode")),
                    "evidence": evidence[:25],
                }
                raw = llm.chat(GEOMETRY_SYSTEM_PROMPT, json.dumps(request, ensure_ascii=False),
                               temperature=0.1, trace_tag="geometry_design")
                from auto_battery_research.simulation.geometry_contract import constrain_generated_np
                candidate = constrain_generated_np(safe_json_loads(raw), self.pipeline.original_design_request)
            normalized = build_geometry_contract(candidate, scheme, evidence)
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
        provenance = self._build_provenance()
        contract_payload = {
            "schema_version": "1.1",
            "target": target_query,
            "review_status": review_status,
            "review_passed": rag_raw.get("review_passed"),
            "review_completed": rag_raw.get("review_completed", rag_raw.get("review_passed")),
            "final_answer": final_answer,
            "plan": plan,
            "retrieval": {
                "db_type": retrieval.get("db_type", "hybrid"),
                "evidence_count": len(evidence),
                "sources": [e.get("source_display") or e.get("source") or e.get("doi") for e in evidence if isinstance(e, dict)],
            },
            "evidence": evidence,
            "reviewer_output": reviewer_output,
            "rule_checks": rule_checks,
            "confidence": confidence,
            "scheme": scheme,
            "provenance": provenance,
            "artifacts": {
                "markdown": "design_scheme.md",
                "json": "design_scheme.json",
                "raw_rag": "rag_result.json",
                "research_context": "research_context.json",
            },
            "generated_at": datetime.now().isoformat(),
        }

        # 4. 持久化文件 (仅课题目录；无论验收通过与否均落盘，REJECTED 产物保留供诊断回溯)
        if not errors:
            from auto_battery_research.tools.workflow_actions import _atomic_write_text
            snapshot = task_dir / "design_revisions" / f"revision_0_{scheme['geometry_hash'][:12]}.json"
            if not snapshot.exists():
                _atomic_write_text(snapshot, json.dumps(contract_payload, ensure_ascii=False, indent=2))
        # Markdown (final_answer 五段式 + 设计点几何声明表)
        with open(scheme_md_file, "w", encoding="utf-8") as f:
            f.write(final_answer if not geometry_md else final_answer + "\n\n---\n\n" + geometry_md + "\n")

        # JSON 契约
        with open(scheme_json_file, "w", encoding="utf-8") as f:
            json.dump(contract_payload, f, ensure_ascii=False, indent=2)

        # 原始 RAG 结果
        with open(raw_rag_file, "w", encoding="utf-8") as f:
            json.dump(rag_raw, f, ensure_ascii=False, indent=2)

        # 知识资产版本快照 (corpus 清单哈希 / 向量库指纹 / 规则版本)
        with open(task_dir / "research_context.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "target": target_query,
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
            "message": "多智能体 RAG 方案设计完成并已通过规则校验" + (
                "；审核仍有未解决意见，仅作为待验证候选推进，不能称为审核通过" if review_status == "REVIEWED_WITH_ISSUES" else ""),
            "journal_notes": (
                f"基于 RAGPipeline ({len(evidence)} 证据段落, 置信度 {confidence}) "
                f"完成多智能体方案设计，RelationEngine 规则审查通过。"
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

