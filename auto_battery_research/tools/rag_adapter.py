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
            from src.lmllm.RAG import RAGPipeline
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
            f"RAGPipeline 执行完成: 召回 {len(evidence)} 条证据, Reviewer 置信度 [{confidence}], "
            f"抽取配方: {scheme.get('cathode', 'N/A')} + {scheme.get('anode', 'N/A')} + {scheme.get('electrolyte', 'N/A')}"
        )

        # 2. 严格 Fail-Closed 门禁校验 (先校验后落盘，结果以 review_status 固化进契约)
        errors = []
        if len(evidence) < 1:
            errors.append("未召回任何有效文献证据 (evidence_count == 0)")
        if str(confidence).lower() == "low":
            errors.append("Reviewer 审核置信度过低 (confidence == 'low')")
        if not isinstance(scheme, dict) or not scheme.get("cathode") or not scheme.get("anode") or not scheme.get("electrolyte"):
            errors.append(f"结构化材料配方不完整 (缺少必要组件): {scheme}")
        violations = rule_checks.get("rule_checks", {}).get("violations") or rule_checks.get("violations", [])
        rejects = rule_checks.get("rule_checks", {}).get("rejects") or rule_checks.get("rejects", [])
        if violations or rejects:
            errors.append(f"RelationEngine 硬约束校验不通过: {violations or rejects}")

        review_status = "REJECTED" if errors else "APPROVED"

        # 3. 规范化 Stage 4 标准输出契约 (含知识资产溯源，供科研可复现性回溯)
        provenance = self._build_provenance()
        contract_payload = {
            "schema_version": "1.1",
            "target": target_query,
            "review_status": review_status,
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
        # Markdown
        with open(scheme_md_file, "w", encoding="utf-8") as f:
            f.write(final_answer)

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

        log_success(f"Stage 4 契约产物已生成 (review_status=APPROVED): {scheme_md_file} & {scheme_json_file}")
        return {
            "success": True,
            "review_status": review_status,
            "scheme_file": str(scheme_md_file),
            "scheme_json": str(scheme_json_file),
            "raw_rag_file": str(raw_rag_file),
            "message": "多智能体 RAG 方案设计完成并已通过规则校验",
            "journal_notes": (
                f"基于 RAGPipeline ({len(evidence)} 证据段落, 置信度 {confidence}) "
                f"完成多智能体方案设计，RelationEngine 规则审查通过。"
            ),
            "deliverables": [str(scheme_md_file), str(scheme_json_file), str(raw_rag_file)],
            "key_findings": {
                "target_query": target_query,
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
            from src.lmllm.RAG.relation_engine import RULES_VERSION
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

