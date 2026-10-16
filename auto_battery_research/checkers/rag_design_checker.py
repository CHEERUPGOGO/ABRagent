"""Stage 4: RAGDesignChecker — 多智能体 RAG 方案设计门禁检查器 (全面对标 RAG 契约与 RelationEngine 审计)."""

import json
from pathlib import Path
from typing import Dict, Any, Tuple, List
from .base_checker import BaseChecker

from auto_battery_research.util.design_sections import REQUIRED_SECTIONS


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
            current_goal = self.stage_manager.target_goal if self.stage_manager else ""
            call_hint = f"RunRAGDesign(target_goal='{current_goal}')" if current_goal else "RunRAGDesign()"
            return False, self.build_diagnostic(
                passed=False,
                error_code="DESIGN_SCHEME_FILE_MISSING",
                error_msg=f"未找到 RAG 生成的设计方案文件 ({scheme_json_file} 或 {scheme_md_file})",
                observed={"json_exists": scheme_json_file.exists(), "md_exists": scheme_md_file.exists()},
                expected="存在完整的电池设计方案 Markdown 报告与结构化 JSON",
                next_action=f"调用 RAG 设计工具生成方案：{call_hint}",
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
        review = json_data.get("reviewer_output") or {}
        # 仅审核"响应失败"才否决; 审核已执行但给出意见属正常结果, 由后续规则/结构门禁处理。
        if review.get("error_type") == "reviewer" or json_data.get("review_completed", json_data.get("review_passed")) is False:
            return False, self.build_diagnostic(
                passed=False, error_code="REVIEWER_RESPONSE_FAILED", error_msg="Reviewer 审核响应失败，草稿未经审核",
                observed={"reviewer_error_type": review.get("error_type"), "issues": review.get("issues")},
                next_action="重跑 RunRAGDesign 获得有效审核响应；规则通过不能替代 Reviewer 审核")

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
        if violations or rejects or unverified or energy_check == "energy_mismatch" or rule_checks.get("confidence") == "low":
            return False, self.build_diagnostic(
                passed=False,
                error_code="RELATION_ENGINE_VIOLATION",
                error_msg=f"RelationEngine 硬约束校验失败: violations={violations}, rejects={rejects}, unverified={unverified}, energy_check={energy_check}",
                observed={"rule_checks": rule_checks, "unverified": unverified},
                expected="材料组合符合热力学硬约束、无未知材料违规且能量密度核算相符",
                next_action="调整正极/负极/电解液搭配，排除不相容或未经验证的组分",
            )

        # 3.5 几何声明门禁 (产物契约 v1.1): 正/负极厚度/孔隙率/粒径/扩散系数 8 个设计值
        #     必须由方案显式给出 (provenance ∈ {stage4_recipe, stage5_revision}), 全部
        #     提供值经物理/registry 范围校验 —— 杜绝"静默回退训练基准冒充设计点"。
        #     runtime_options.geometry_gate: enforce (默认, 硬失败) | warn (灰度, 降级为警告不阻断)。
        gate_mode = str((self.config.get("runtime_options") or {}).get("geometry_gate", "enforce")).lower()

        def _geo_fail(code: str, msg: str, observed: dict, expected: str, next_action: str):
            diag = self.build_diagnostic(
                passed=False, error_code=code, error_msg=msg,
                observed=observed, expected=expected, next_action=next_action)
            if gate_mode == "warn":
                diag["check_pass"] = True
                diag["error_code"] = None
                diag["error"] = None
                diag["warning_only"] = True
                return True, diag
            return False, diag

        geo = scheme.get("geometry") if isinstance(scheme.get("geometry"), dict) else None
        if geo is None:
            return _geo_fail(
                "GEOMETRY_BLOCK_MISSING",
                "design_scheme.json 的 scheme.geometry 缺失 —— Stage 4 必须显式声明电极几何设计值 "
                "(契约 v1.1: 正/负极厚度/孔隙率/颗粒粒径/固相扩散系数)",
                {"scheme_keys": sorted(scheme.keys())},
                "scheme.geometry 存在且为 v1.1 归一化声明块",
                "重新执行 RunRAGDesign: 方案正/负极章节须明确给出电极涂敷厚度 (μm)、孔隙率 (%)、"
                "颗粒粒径 (μm) 与固相扩散系数 (m²/s)")
        if geo.get("schema_version") != "1.1":
            return _geo_fail(
                "GEOMETRY_DECLARATION_INCOMPLETE",
                f"scheme.geometry 不是 v1.1 归一化声明块 (schema_version={geo.get('schema_version')!r})",
                {"geometry": geo},
                "v1.1 归一化块 (由 RunRAGDesign 归一化写入 design_scheme.json)",
                "重新执行 RunRAGDesign 生成 v1.1 几何声明块")

        geo_prov = geo.get("provenance") if isinstance(geo.get("provenance"), dict) else {}
        if geo.get("contract_version") == "2.0":
            from auto_battery_research.simulation.geometry_contract import validate_geometry_contract, geometry_hash
            contract_errors = validate_geometry_contract(geo, scheme, evidence_list)
            try:
                actual_hash = geometry_hash(geo)
            except (ValueError, TypeError):
                actual_hash = None
            if actual_hash is None or scheme.get("geometry_hash") != actual_hash:
                contract_errors.append("geometry_hash 与实际内容不一致")
            if contract_errors:
                return _geo_fail("GEOMETRY_CONTRACT_INVALID", "; ".join(contract_errors),
                                 {"errors": contract_errors}, "结构化几何来源与输入身份可验证", "修复指定字段的结构化契约")
        # 硬字段 8 项: 两电极各自的厚度/孔隙率/粒径/扩散系数; 显式声明 provenance
        # 接受 stage4_recipe (Stage 4 叙事提取) 与 stage5_revision (Stage 5 反馈修订)
        from auto_battery_research.tools.geometry_revision import EXPLICIT_PROVENANCE_TAGS
        hard_fields = tuple(
            (comp, field) for comp in ("cathode", "anode")
            for field in ("L_um", "porosity", "R_s_um", "D_s_m2s")
        )
        missing, not_explicit, illegal = [], [], []
        for comp, field in hard_fields:
            comp_block = geo.get(comp) if isinstance(geo.get(comp), dict) else {}
            val = comp_block.get(field)
            path = f"{comp}.{field}"
            structured = geo.get("contract_version") == "2.0"
            source = (geo.get("parameter_sources") or {}).get(path, {})
            if structured and field in ("R_s_um", "D_s_m2s"):
                if source.get("origin") == "model_baseline" and geo_prov.get(path) == "params_baseline":
                    continue
                if val is None and source.get("origin") == "missing":
                    continue
                if field == "D_s_m2s":
                    try:
                        from auto_battery_research.simulation.geometry_contract import validate_diffusion_evidence
                        validate_diffusion_evidence(source.get("evidence"), val, str(scheme.get(comp)), evidence_list)
                    except (ValueError, TypeError) as exc:
                        illegal.append(f"{path}: {exc}")
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                missing.append(path)
                continue
            src_tag = geo_prov.get(path)
            if src_tag == "invalid":
                illegal.append(f"{path}={val} (非法值)")
            elif src_tag not in EXPLICIT_PROVENANCE_TAGS:
                not_explicit.append(f"{path}={val} (来源={src_tag or '未标注'})")
        if missing or not_explicit:
            return _geo_fail(
                "GEOMETRY_DECLARATION_INCOMPLETE",
                "几何硬性设计值缺失或非显式声明: " + "; ".join(missing + not_explicit),
                {"missing": missing, "not_explicit": not_explicit},
                "正/负极厚度、孔隙率、颗粒粒径与固相扩散系数共 8 个值必须由方案显式给出 "
                f"(provenance ∈ {list(EXPLICIT_PROVENANCE_TAGS)})",
                "在方案正/负极章节明确给出电极涂敷厚度 (μm)、孔隙率 (%)、颗粒粒径 (μm) 与"
                "固相扩散系数 (m²/s) 后重新执行 RunRAGDesign")

        # 全部提供值的范围校验 (registry input_ranges 对齐, 未匹配体系退物理常识;
        # 范围逻辑统一承载于 geometry_revision, 与 ReviseGeometry 共用避免双份漂移)
        from auto_battery_research.tools.geometry_revision import collect_geometry_violations, resolve_input_ranges
        ranges = resolve_input_ranges(scheme.get("cathode"), scheme.get("anode"))
        illegal = list(dict.fromkeys(illegal + collect_geometry_violations(geo, ranges)))
        if illegal:
            return _geo_fail(
                "GEOMETRY_VALUE_ILLEGAL",
                "scheme.geometry 提供了非法/越界值: " + "; ".join(illegal),
                {"illegal": illegal},
                "全部提供值在物理与 registry input_ranges 范围内 (未匹配体系按物理常识)",
                "修正方案中的非法几何值 (或删除该值改用体系默认) 后重新执行 RunRAGDesign")

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
                "geometry_gate": "PASSED (v1.1 声明块, 正/负极厚度/孔隙率/粒径/扩散系数显式)",
            },
            expected="设计方案符合五段式规范，证据溯源完备，经由 RelationEngine 审查通过",
            details={
                "scheme": json_data.get("scheme", {}),
                "rule_checks": json_data.get("rule_checks", {}),
            },
        )

