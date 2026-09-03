"""Stage 6: FinalReportChecker — 综合研发报告门禁检查器."""

from pathlib import Path
from typing import Dict, Any, Tuple
from .base_checker import BaseChecker

REPORT_REQUIRED_SECTIONS = [
    ("研发摘要", ["研发摘要", "执行摘要", "总体概览", "summary"]),
    ("文献与电芯数据概况", ["文献", "数据概况", "电芯挖掘", "literature", "mining", "stage journal", "阶段履历"]),
    ("电池体系设计方案", ["体系设计方案", "设计方案", "正极", "负极", "电解液", "design", "方案"]),
    ("物理仿真与验证", ["物理仿真", "pinn", "pybamm", "仿真验证", "跳过说明", "simulation", "物理"]),
    ("实验配方与落地建议", ["实验配方", "落地建议", "制备工艺", "研发路线", "recipe", "roadmap", "后续建议", "结论"]),
]


class FinalReportChecker(BaseChecker):
    """验证最终化学电池综合研报的完整性、结构与数据可信度."""

    def do_check(self, is_complete: bool = False, **kwargs) -> Tuple[bool, Dict[str, Any]]:
        import re
        paths = self.config.get("paths", {})
        output_agent_dir = self.resolve_path(paths.get("output_dir", "output/auto_battery_research"))
        explicit_report = paths.get("final_report_file")
        # 课题专属目录优先：全局 legacy 路径仅对历史存量课题 (is_legacy_task)
        # 保留读取回退；新哈希课题必须自包含研报，防止其他课题/全局陈旧研报
        # 穿透本课题门禁 (Stage 6 假通过)
        if self.stage_manager:
            task_report = self.stage_manager.get_task_output_dir() / "final_research_report.md"
            if task_report.exists():
                report_file = task_report
            elif self.allow_global_legacy_fallback and explicit_report:
                report_file = self.resolve_path(explicit_report)
            elif self.allow_global_legacy_fallback:
                report_file = output_agent_dir / "final_research_report.md"
            else:
                report_file = task_report  # 新课题: 仅认课题目录，缺失即失败并给出明确路径
        elif explicit_report:
            report_file = self.resolve_path(explicit_report)
        else:
            report_file = output_agent_dir / "final_research_report.md"

        journal_file = self.resolve_path(paths.get("journal_file", output_agent_dir / "abr_agent_journal.json"))
        if self.stage_manager:
            task_journal = self.stage_manager.get_task_output_dir() / "stage_journals.json"
            if task_journal.exists():
                journal_file = task_journal
            elif not self.allow_global_legacy_fallback:
                journal_file = task_journal  # 新课题: 仅认课题目录日志
            elif (output_agent_dir / "stage_journals.json").exists():
                journal_file = output_agent_dir / "stage_journals.json"
        elif (output_agent_dir / "stage_journals.json").exists():
            journal_file = output_agent_dir / "stage_journals.json"

        if not report_file.exists():
            current_goal = self.stage_manager.target_goal if self.stage_manager else ""
            call_hint = f"SynthesizeResearchReport(target_goal='{current_goal}')" if current_goal else "SynthesizeResearchReport()"
            return False, self.build_diagnostic(
                passed=False,
                error_code="SYNTHESIS_REPORT_MISSING",
                error_msg=f"未检测到最终综合研发报告文件: {report_file}",
                observed={"report_exists": False},
                expected="存在完整的 final_research_report.md",
                next_action=f"生成综合研报：{call_hint}",
            )

        try:
            with open(report_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            return False, self.build_diagnostic(
                passed=False,
                error_code="SYNTHESIS_REPORT_READ_ERROR",
                error_msg=f"读取综合研报失败: {str(e)}",
                next_action="检查文件编码并重新生成报告",
            )

        if len(content.strip()) < 400:
            return False, self.build_diagnostic(
                passed=False,
                error_code="SYNTHESIS_REPORT_TOO_SHORT",
                error_msg=f"综合研报字数过短 ({len(content)} < 400 字符)，内容不完备",
                observed={"content_length": len(content)},
                expected="报告篇幅需包含各阶段完整总结与建议 (>400 字符)",
                next_action="重新汇总全流程各阶段成果，生成详细报告",
            )

        headings = re.findall(r"^#{1,4}\s+(.+)$", content, re.MULTILINE)
        found_sections = []
        missing_sections = []
        for sec_name, keywords in REPORT_REQUIRED_SECTIONS:
            # 优先校验标题行层级
            matched = any(any(kw.lower() in h.lower() for kw in keywords) for h in headings)
            if not matched:
                # 兼容加粗大标题 (如 **1. 研发摘要**)
                matched = any(re.search(rf"\*\*\s*\d?\.?\s*({kw})\s*\*\*", content, re.IGNORECASE) for kw in keywords)

            if matched:
                found_sections.append(sec_name)
            else:
                missing_sections.append(sec_name)

        if len(missing_sections) >= 1:
            return False, self.build_diagnostic(
                passed=False,
                error_code="SYNTHESIS_REPORT_SECTIONS_INCOMPLETE",
                error_msg=f"综合研报缺少核心章节标题: {', '.join(missing_sections)} (已识别标题: {headings})",
                observed={"found_sections": found_sections, "missing_sections": missing_sections, "parsed_headings": headings},
                expected="必须包含研发摘要、文献与电芯数据概况、电池体系设计方案、物理仿真与验证、实验配方与落地建议全部 5 大核心章节",
                next_action="在最终报告中使用明确的二级标题 (##) 标明各核心章节并重新生成",
            )

        # 严格门禁：必须确保阶段日志已同步持久化且非空
        journal_exists = journal_file.exists() and journal_file.stat().st_size > 10
        if not journal_exists:
            return False, self.build_diagnostic(
                passed=False,
                error_code="STAGE_JOURNAL_MISSING",
                error_msg=f"未检测到有效的阶段研发日志 ({journal_file})",
                observed={"journal_file_exists": False},
                expected="必须包含记录全流程执行履历的 stage_journals.json / abr_agent_journal.json",
                next_action="在各阶段推进前调用 SetStageJournal 记录研发日志",
            )

        return True, self.build_diagnostic(
            passed=True,
            observed={
                "report_file": str(report_file),
                "report_length": len(content),
                "sections_covered": found_sections,
                "journal_file_exists": journal_exists,
            },
            expected="全阶段工作流完成，综合研报与阶段日志验收合格",
            details={"output_path": str(report_file)},
        )
