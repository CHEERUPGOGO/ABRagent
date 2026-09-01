"""Stage 1: IngestionChecker — 文献解析与分类门禁检查器."""

from pathlib import Path
from typing import Dict, Any, Tuple
from .base_checker import BaseChecker


class IngestionChecker(BaseChecker):
    """验证文献解析与组件分类产物.

    注: 文献语料 (papers/merged、database/type) 为全部课题共享的全局知识资产，
    不做课题隔离 —— 这与 Stage 3/4/6 的课题专属交付物不同，属设计内行为。
    """

    def do_check(self, is_complete: bool = False, **kwargs) -> Tuple[bool, Dict[str, Any]]:
        paths = self.config.get("paths", {})
        db_type_dir = paths.get("database_type_dir", "database/type")
        merged_dir = paths.get("papers_merged_dir", "papers/merged")
        
        db_path = self.resolve_path(db_type_dir)
        mrg_path = self.resolve_path(merged_dir)

        if not db_path.exists() and not mrg_path.exists():
            return False, self.build_diagnostic(
                passed=False,
                error_code="INGESTION_DIRECTORIES_MISSING",
                error_msg=f"未找到文献分类输出目录 ({db_type_dir}) 或合并目录 ({merged_dir})",
                observed={"db_type_exists": db_path.exists(), "merged_exists": mrg_path.exists()},
                expected="database/type/ 或 papers/merged/ 下存在分类/合并后的 .md 文献",
                next_action="请执行文献解析与分类脚本：python pipeline_incremental.py --step mineru 或 python -m miner.classification.battery_type_agent",
            )

        comp_counts = {"cathode": 0, "anode": 0, "electrolyte": 0}
        total_md_files = 0
        corrupt_files = []

        if db_path.exists():
            for comp in ["cathode", "anode", "electrolyte"]:
                found = list(db_path.rglob(f"**/{comp}/*.md"))
                comp_counts[comp] = len(found)
                total_md_files += len(found)
                for f in found:
                    if f.stat().st_size < 50:
                        corrupt_files.append(str(f))

        if total_md_files == 0 and mrg_path.exists():
            merged_files = list(mrg_path.rglob("*.md"))
            total_md_files = len(merged_files)
            for f in merged_files:
                if f.stat().st_size < 50:
                    corrupt_files.append(str(f))

        if total_md_files == 0:
            return False, self.build_diagnostic(
                passed=False,
                error_code="NO_MARKDOWN_PAPERS_FOUND",
                error_msg="未检测到任何有效的 Markdown 论文文件",
                observed={"total_md_count": 0, "comp_counts": comp_counts},
                expected="至少存在 1 篇以上非空的 .md 论文文件",
                next_action="请将 PDF 放入 papers/pdf 并运行转换脚本 python preprocessing/pdf_to_markdown.py",
            )

        if corrupt_files:
            return False, self.build_diagnostic(
                passed=False,
                error_code="EMPTY_OR_CORRUPT_MARKDOWN_FILES",
                error_msg=f"检测到 {len(corrupt_files)} 个异常过小的 Markdown 文件 (<50字节)",
                observed={"corrupt_files_count": len(corrupt_files), "samples": corrupt_files[:3]},
                expected="所有 .md 论文必须包含有效正文字符 (>50 字节)",
                next_action="请重新转换或清理损坏的文献文件",
            )

        # 严格门禁：必须确保正极、负极、电解质三大核心组件分类均具备有效文献
        missing_components = [c for c, count in comp_counts.items() if count == 0]
        if missing_components:
            return False, self.build_diagnostic(
                passed=False,
                error_code="INCOMPLETE_COMPONENT_CLASSIFICATION",
                error_msg=f"文献组件分类不完备，缺少以下组件文献: {', '.join(missing_components)}",
                observed={"comp_counts": comp_counts, "missing_components": missing_components},
                expected="正极 (cathode)、负极 (anode)、电解液 (electrolyte) 三大组件分类文献均至少存在 1 篇以上",
                next_action="补充缺失组件的学术文献并重新运行分类流水线：python -m miner.classification.battery_type_agent",
            )

        return True, self.build_diagnostic(
            passed=True,
            observed={
                "total_md_papers": total_md_files,
                "component_distribution": comp_counts,
            },
            expected="分类/合并文献就绪且正极/负极/电解液分类完备",
            details={"db_type_dir": str(db_path), "merged_dir": str(mrg_path)},
        )
