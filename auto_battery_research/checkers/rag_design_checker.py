"""Stage 4: RAGDesignChecker — 多智能体 RAG 方案设计门禁检查器 (全面对标 RAG 契约与 RelationEngine 审计)."""

import json
from pathlib import Path
from typing import Dict, Any, Tuple, List
from .base_checker import BaseChecker

REQUIRED_SECTIONS = [
    ("目标与设计路线", ["目标", "设计路线", "能量目标", "target", "路线"]),
    ("推荐组合", ["推荐材料组合", "推荐组合", "正极", "负极", "电解液", "combination", "配方"]),
    ("预期关键指标", ["预期指标", "关键指标", "能量密度", "比容量", "metrics", "性能指标"]),
    ("可行性依据", ["可行性依据", "文献证据", "机理支撑", "feasibility", "evidence", "依据"]),
    ("风险与数据缺口", ["风险与数据缺口", "风险分析", "数据缺口", "risks", "gaps", "风险评估"]),
]


class RAGDesignChecker(BaseChecker):
    """验证多智能体 RAG 输出的设计方案完整性、证据溯源、结构化配方及 RelationEngine 规则合规性."""

    def do_check(self, is_complete: bool = False, **kwargs) -> Tuple[bool, Dict[str, Any]]:
        import re
        paths = self.config.get("paths", {})
        output_agent_dir = self.resolve_path(paths.get("output_dir", "output/auto_battery_research"))

        # 新哈希课题: 仅认课题目录内产物，缺失即失败 —— 全局目录与
        # src/lmllm/RAG/output 共享目录 (取最新 md，跨课题泄漏通道) 仅对
        # 历史存量课题 (is_legacy_task) / Checker 独立使用场景保留回退
        if self.stage_manager and not self.allow_global_legacy_fallback:
            task_dir = self.stage_manager.get_task_output_dir()
            scheme_json_file = task_dir / "design_scheme.json"
            scheme_md_file = task_dir / "design_scheme.md"
        else:
            scheme_json_file = output_agent_dir / "design_scheme.json"
            scheme_md_file = output_agent_dir / "design_scheme.md"

            if self.stage_manager:
                task_dir = self.stage_manager.get_task_output_dir()
                candidate_md = task_dir / "design_scheme.md"
                candidate_json = task_dir / "design_scheme.json"
                if candidate_md.exists():
                    scheme_md_file = candidate_md
                if candidate_json.exists():
                    scheme_json_file = candidate_json

            rag_output_dir = self.resolve_path("src/lmllm/RAG/output")
            if not scheme_md_file.exists() and rag_output_dir.exists():
                rag_mds = sorted(list(rag_output_dir.glob("*.md")), key=lambda x: x.stat().st_mtime, reverse=True)
                if rag_mds:
                    scheme_md_file = rag_mds[0]

        # 1. 存在性检查
        if not scheme_json_file.exists() and not scheme_md_file.exists():
            return False, self.build_diagnostic(
                passed=False,
                error_code="DESIGN_SCHEME_FILE_MISSING",
                error_msg=f"未找到 RAG 生成的设计方案文件 ({scheme_json_file} 或 {scheme_md_file})",
                observed={"json_exists": scheme_json_file.exists(), "md_exists": scheme_md_file.exists()},
                expected="存在完整的电池设计方案 Markdown 报告与结构化 JSON",
                next_action="调用 RAG 设计工具生成方案：RunRAGDesign(target_query='设计400Wh/kg高比能锂金属电池')",
            )

        # 2. Markdown 正文与真正的标题层级五段式结构检查
        md_content = ""
        if scheme_md_file.exists():
            try:
                with open(scheme_md_file, "r", encoding="utf-8") as f:
                    md_content = f.read()
            except Exception as e:
                return False, self.build_diagnostic(
                    passed=False,
                    error_code="DESIGN_SCHEME_MD_READ_ERROR",
                    error_msg=f"读取设计方案 Markdown 失败: {str(e)}",
                    next_action="请修复或重新导出设计方案 Markdown 文件",
                )

        if len(md_content.strip()) < 200:
            return False, self.build_diagnostic(
                passed=False,
                error_code="DESIGN_SCHEME_MD_TOO_SHORT",
                error_msg=f"设计方案 Markdown 长度不足 ({len(md_content)} < 200 字符)，内容不完整",
                observed={"content_length": len(md_content)},
                expected="包含五段式完整方案（不少于 200 字符）",
                next_action="重新运行 RAG Pipeline 生成详尽的设计方案报告",
            )

        # 提取真正的 Markdown 标题行进行结构树校验 (AST/Heading-level parsing)
        headings = re.findall(r"^#{1,4}\s+(.+)$", md_content, re.MULTILINE)
        missing_sections = []
        found_sections = []
        for sec_name, keywords in REQUIRED_SECTIONS:
            # 要求关键词必须命中真实的标题行或顶级粗体章节
            matched = any(any(kw.lower() in h.lower() for kw in keywords) for h in headings)
            if not matched:
                # 兼容一级/二级带序号的加粗标题
                matched = any(re.search(rf"\*\*\s*\d?\.?\s*({kw})\s*\*\*", md_content, re.IGNORECASE) for kw in keywords)
            
            if matched:
                found_sections.append(sec_name)
            else:
                missing_sections.append(sec_name)

        if len(missing_sections) >= 1:
            return False, self.build_diagnostic(
                passed=False,
                error_code="DESIGN_SCHEME_SECTIONS_INCOMPLETE",
                error_msg=f"设计方案缺少关键五段式章节结构: {', '.join(missing_sections)} (已识别章节标题: {headings})",
                observed={"found_sections": found_sections, "missing_sections": missing_sections, "parsed_headings": headings},
                expected="必须包含五段式全部完整标题结构（目标路线、推荐组合、预期指标、可行性依据、风险缺口）",
                next_action="在方案 Markdown 中使用明确的二级标题 (##) 标明五段式章节",
            )

        # 3. 结构化 JSON 契约深度检查 (证据溯源、相关性、Reviewer 置信度与 RelationEngine 规则)
        if not scheme_json_file.exists():
            return False, self.build_diagnostic(
                passed=False,
                error_code="DESIGN_SCHEME_JSON_MISSING",
                error_msg=f"未找到设计方案结构化契约文件 ({scheme_json_file})",
                observed={"md_exists": scheme_md_file.exists(), "json_exists": False},
                expected="Stage 4 产物必须同时包含 Markdown 报告与结构化 design_scheme.json",
                next_action="重新执行 Stage 4 生成结构化契约 JSON 文件",
            )

        json_data, err = self.load_json_safe(str(scheme_json_file))
        if err or not isinstance(json_data, dict):
            return False, self.build_diagnostic(
                passed=False,
                error_code="DESIGN_SCHEME_JSON_CORRUPTED",
                error_msg=f"设计方案 JSON 文件解析异常: {err}",
                next_action="重新执行 Stage 4 生成结构化契约 JSON",
            )

        # 3.1 证据与段落溯源及化学体系相关性检查
        evidence_list = json_data.get("evidence", [])
        valid_evidence_count = 0
        relevant_evidence_count = 0
        mismatched_evidence = []

        scheme = json_data.get("scheme", {})
        scheme_str = (
            f"{scheme.get('cathode', '')} {scheme.get('anode', '')} {scheme.get('electrolyte', '')} "
            f"{self.stage_manager.target_goal if self.stage_manager else ''}"
        ).lower()
        is_lithium_system = any(kw in scheme_str for kw in ["li", "锂", "lithium", "ncm", "lfp", "lhce", "lp40"])
        foreign_chemistry_kws = ["钠离子", "na-ion", "sodium-ion", "硬碳钠电", "mose2", "na3v2", "钾离子", "k-ion"]

        for ev in evidence_list:
            if not isinstance(ev, dict):
                continue
            ev_text = str(ev.get("text", "")).lower()
            ev_source = str(ev.get("source", "")).lower()
            ev_combined = f"{ev_source} {ev_text}"
            
            if (ev.get("passage_id") or ev.get("source")) and ev.get("text"):
                valid_evidence_count += 1
                
                # 检查异质体系冲突 (例如锂电方案中混入纯钠电/钾电异质文献)
                if is_lithium_system and any(fkw in ev_combined for fkw in foreign_chemistry_kws):
                    if not any(lkw in ev_combined for lkw in ["li", "锂", "lithium"]):
                        mismatched_evidence.append({
                            "source": ev.get("source"),
                            "reason": "检测到异质电池体系文献 (钠/钾电文献)，与当前锂电设计体系不匹配",
                        })
                        continue
                
                relevant_evidence_count += 1

        if valid_evidence_count < 1:
            return False, self.build_diagnostic(
                passed=False,
                error_code="EVIDENCE_MISSING",
                error_msg=f"设计方案缺少有效文献证据支撑 (有效证据数 {valid_evidence_count} < 1)，未达成真实 RAG 溯源要求",
                observed={"valid_evidence_count": valid_evidence_count, "evidence_list": evidence_list},
                expected="至少包含 1 条具备 passage_id、source 与 text 的真实文献证据",
                next_action="检查知识库检索通道并优化检索关键词以召回真实文献证据",
            )

        if mismatched_evidence and relevant_evidence_count == 0:
            return False, self.build_diagnostic(
                passed=False,
                error_code="EVIDENCE_CHEMISTRY_MISMATCH",
                error_msg=f"文献证据与当前设计体系冲突: {mismatched_evidence[0]['reason']}",
                observed={"mismatched_evidence": mismatched_evidence, "valid_count": valid_evidence_count},
                expected="文献证据必须与目标化学体系 (正负极/电解液) 真实相关且自洽",
                next_action="优化检索 Query，排除非目标体系关键词 (如排除 Na-ion/MoSe2 异质文献)",
            )

        # 3.2 Reviewer 置信度强制检查
        confidence = json_data.get("confidence", "high")
        if str(confidence).lower() == "low":
            return False, self.build_diagnostic(
                passed=False,
                error_code="REVIEWER_CONFIDENCE_TOO_LOW",
                error_msg=f"Reviewer 审核置信度过低 (confidence='low')，存在未解决的学术/证据冲突",
                observed={"confidence": confidence, "issues": json_data.get("reviewer_output", {}).get("issues", [])},
                expected="Reviewer 审核置信度达到 medium 或 high",
                next_action="检查文献知识库或优化提问以补足证据链条",
            )

        # 3.3 结构化配方 (Scheme) 完整性强制检查
        if not isinstance(scheme, dict) or not scheme.get("cathode") or not scheme.get("anode") or not scheme.get("electrolyte"):
            return False, self.build_diagnostic(
                passed=False,
                error_code="STRUCTURED_SCHEME_INCOMPLETE",
                error_msg=f"结构化材料配方 (scheme) 不完整: {scheme}，缺少必要组件 (cathode/anode/electrolyte)",
                observed={"scheme": scheme},
                expected="scheme 必须包含明确的 cathode, anode, electrolyte 键值对",
                next_action="在方案生成与抽取阶段确保正极、负极、电解液材料均被明确定义",
            )

        # 3.4 规则引擎 (RelationEngine) 校验
        rule_checks = json_data.get("rule_checks", {})
        if not rule_checks or not isinstance(rule_checks, dict):
            return False, self.build_diagnostic(
                passed=False,
                error_code="RULE_CHECKS_MISSING",
                error_msg="缺少 RelationEngine 规则引擎审查报告",
                expected="必须包含由 RelationEngine 审查生成的 rule_checks 字典",
                next_action="确保 RAGPipeline 运行了 RelationEngine.check_scheme()",
            )

        violations = rule_checks.get("rule_checks", {}).get("violations") or rule_checks.get("violations", [])
        rejects = rule_checks.get("rule_checks", {}).get("rejects") or rule_checks.get("rejects", [])
        unverified = rule_checks.get("rule_checks", {}).get("unverified") or rule_checks.get("unverified", [])
        energy_check = rule_checks.get("energy_check")
        if violations or rejects or unverified or energy_check == "energy_mismatch":
            return False, self.build_diagnostic(
                passed=False,
                error_code="RELATION_ENGINE_VIOLATION",
                error_msg=f"RelationEngine 硬约束校验失败: violations={violations}, rejects={rejects}, unverified={unverified}, energy_check={energy_check}",
                observed={"rule_checks": rule_checks, "unverified": unverified},
                expected="材料组合符合热力学硬约束、无未知材料违规且能量密度核算相符",
                next_action="调整正极/负极/电解液搭配，排除不相容或未经验证的组分",
            )

        return True, self.build_diagnostic(
            passed=True,
            observed={
                "sections_found": found_sections,
                "scheme_md_file": str(scheme_md_file),
                "scheme_json_file": str(scheme_json_file),
                "evidence_count": valid_evidence_count,
                "confidence": json_data.get("confidence", "high"),
                "extracted_scheme": json_data.get("scheme", {}),
                "rule_checks_status": "VALIDATED",
            },
            expected="设计方案符合五段式规范，证据溯源完备，经由 RelationEngine 审查通过",
            details={
                "scheme": json_data.get("scheme", {}),
                "rule_checks": json_data.get("rule_checks", {}),
            },
        )

