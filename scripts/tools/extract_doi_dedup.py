#!/usr/bin/env python3
"""
多模式脚本：

模式一（默认）：从 PDF 提取 DOI，按 DOI 重命名复制到 pdf_doi/，文件名重复时自动跳过，
                  支持文件大小比较保留较大文件。

模式二（--check-existing）：提取 DOI 后检查 database/type/ 下是否已有同名 md 文件（仅报告）。

模式三（--scan-pdf-doi）：不提取 DOI，直接扫描 pdf_doi/ 下的 DOI 目录名，
                  与 database/type/ 下的 md 文件名比对，列出冲突（供手动删除）。

用法：
    # 模式一：提取 DOI + 去重
    python extract_doi_dedup.py --input papers/pdf/242
    python extract_doi_dedup.py --keep-larger
    python extract_doi_dedup.py --dry-run

    # 模式二：提取 DOI + 检查已入库（同 --check-existing）
    python extract_doi_dedup.py --input papers/pdf/242 --check-existing

    # 模式三：直接扫描 pdf_doi/ 检查冲突（不提取 DOI）
    python extract_doi_dedup.py --scan-pdf-doi --input papers/pdf_doi
    python extract_doi_dedup.py --scan-pdf-doi --input papers/pdf_doi --db-dir path/to/database
"""

import os
import re
import sys
import hashlib
import shutil
import logging
import argparse
from pathlib import Path
from typing import Optional, Set


try:
    import pdfplumber
except ImportError:
    print("请安装 pdfplumber: pip install pdfplumber")
    sys.exit(1)


# ==================== 默认配置（可在脚本顶部修改） ====================

DEFAULT_INPUT_DIR = "/home/ls/xiaoyue/LLM2/LMLLM/papers/pdf/242"
DEFAULT_OUTPUT_DIR = "/home/ls/xiaoyue/LLM2/LMLLM/papers/pdf_doi"
DEFAULT_NO_DOI_DIR = "/home/ls/xiaoyue/LLM2/LMLLM/papers/pdf_no_doi"
DEFAULT_DATABASE_DIR = "/home/ls/xiaoyue/LLM2/LMLLM/database/type/Lithium_Ion_Metal_Battery"

# ==================== 日志 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("extract_doi_dedup")


# ==================== DOI 正则 ====================

DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[^\s,;:\[\]<>]+", re.IGNORECASE)

DOI_DIR_PATTERN = re.compile(r"^10\.\d{4,9}_[^\s]+$")


# ==================== 函数 ====================


def sanitize_doi(doi: str) -> str:
    """将 DOI 中的 / 替换为 _，去除末尾标点，作为文件名"""
    doi = doi.strip().rstrip(".,;:()[]<>")
    return doi.replace("/", "_")


def extract_doi_from_pdf(pdf_path: str, max_pages: int = 5) -> Optional[str]:
    """从 PDF 前 N 页 + 最后 3 页提取 DOI"""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            scan_pages = set()
            scan_pages.update(range(min(max_pages, total)))
            scan_pages.update(range(max(0, total - 3), total))
            full_text = ""
            for i in sorted(scan_pages):
                page_text = pdf.pages[i].extract_text()
                if page_text:
                    full_text += page_text + "\n"
            if not full_text:
                return None
            clean_text = re.sub(r"\s+", " ", full_text)
            match = DOI_PATTERN.search(clean_text)
            if match:
                return match.group(0).rstrip(".,;:()[]<>")
    except Exception as e:
        logger.warning("读取 PDF 失败 %s: %s", Path(pdf_path).name, e)
    return None


def md5_of_file(filepath: str) -> str:
    """计算文件 MD5（用于无 DOI 时的 fallback 命名）"""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_doi_in_database(doi_sanitized: str, db_dir: Path) -> Optional[str]:
    """
    检查 database 下 anode/cathode/electrolyte 子目录中是否已有该 DOI 的 md 文件。
    """
    target_filename = doi_sanitized + ".md"
    for component in ["anode", "cathode", "electrolyte"]:
        comp_dir = db_dir / component
        if comp_dir.is_dir():
            md_path = comp_dir / target_filename
            if md_path.is_file():
                return str(md_path)
    return None


def collect_dois_from_pdf_doi(pdf_doi_dir: Path) -> Set[str]:
    """
    扫描 pdf_doi/ 目录，收集所有 sanitized DOI（子目录名）。
    支持两种结构：
        1. 扁平：pdf_doi/{DOI}.pdf
        2. 嵌套：pdf_doi/{子文件夹}/{DOI}/paper.pdf  （extract_doi_only.py 输出）
    """
    dois = set()
    for item in pdf_doi_dir.rglob("*"):
        if item.is_dir() and DOI_DIR_PATTERN.match(item.name):
            dois.add(item.name)
        elif item.suffix == ".pdf" and DOI_DIR_PATTERN.match(item.stem):
            dois.add(item.stem)
    return dois


def collect_md_dois_from_database(db_dir: Path) -> Set[str]:
    """收集 database 下 anode/cathode/electrolyte 中所有 md 文件的 DOI（去后缀 .md）"""
    dois = set()
    for component in ["anode", "cathode", "electrolyte"]:
        comp_dir = db_dir / component
        if comp_dir.is_dir():
            for md_file in comp_dir.glob("*.md"):
                dois.add(md_file.stem)
    return dois


# ==================== 模式三：扫描 pdf_doi 检查冲突 ====================


def run_scan_pdf_doi(args) -> None:
    """
    模式三：直接扫描 pdf_doi/ 下的 DOI 目录名，与 database 比对。
    不提取 DOI，不复制任何文件。
    """
    pdf_doi_dir = Path(args.input)
    db_dir = Path(args.db_dir)

    if not pdf_doi_dir.is_dir():
        logger.error("pdf_doi 目录不存在: %s", pdf_doi_dir)
        sys.exit(1)

    if not db_dir.is_dir():
        logger.error("database 目录不存在: %s", db_dir)
        sys.exit(1)

    logger.info("扫描 pdf_doi 目录: %s", pdf_doi_dir)
    logger.info("database 目录: %s", db_dir)

    pdf_dois = collect_dois_from_pdf_doi(pdf_doi_dir)
    db_dois = collect_md_dois_from_database(db_dir)

    logger.info("pdf_doi 中找到 %d 个 DOI", len(pdf_dois))
    logger.info("database 中找到 %d 个 md 文件", len(db_dois))

    conflicts = sorted(pdf_dois & db_dois)

    if conflicts:
        logger.info("-" * 50)
        logger.info("以下 %d 个 DOI 在 database 中已有同名 md 文件，请手动删除 pdf_doi 中对应的目录：",
                    len(conflicts))
        logger.info("")
        for doi in conflicts:
            found_paths = []
            for item in pdf_doi_dir.rglob(doi):
                if item.is_dir():
                    found_paths.append(str(item))
            if found_paths:
                for p in found_paths:
                    logger.info("  %s", os.path.relpath(p, pdf_doi_dir))
            else:
                logger.info("  %s  (仅 .pdf 文件)", doi)
        logger.info("")
        logger.info("删除命令（预览）：")
        for doi in conflicts:
            paths = list(pdf_doi_dir.rglob(doi))
            if paths:
                logger.info("  rm -rf %s", paths[0])
    else:
        logger.info("未发现冲突。pdf_doi 中的所有 DOI 在 database 中均不存在同名 md 文件。")

    logger.info("=" * 50)
    logger.info("检查完成")
    logger.info("  pdf_doi 中 DOI 数: %d", len(pdf_dois))
    logger.info("  database 中 md 数: %d", len(db_dois))
    logger.info("  冲突数:            %d", len(conflicts))
    logger.info("=" * 50)


# ==================== 模式一 / 二：提取 DOI + 复制 ====================


def run_extract(args) -> None:
    """模式一/二：从 PDF 提取 DOI，复制到 pdf_doi/，支持去重和入库检查。"""
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    no_doi_dir = Path(args.no_doi)
    db_dir = Path(args.db_dir)

    if not input_dir.is_dir():
        logger.error("输入目录不存在: %s", input_dir)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    no_doi_dir.mkdir(parents=True, exist_ok=True)

    if args.check_existing and not db_dir.is_dir():
        logger.warning("database 目录不存在: %s，将不执行入库检查", db_dir)
        args.check_existing = False

    pdfs = sorted(input_dir.rglob("*.pdf"))
    if not pdfs:
        logger.warning("未在 %s 中找到 PDF 文件", input_dir)
        return

    logger.info("共发现 %d 个 PDF，输入目录: %s", len(pdfs), input_dir)
    logger.info("输出目录: %s", output_dir)
    logger.info("无 DOI 目录: %s", no_doi_dir)
    if args.check_existing:
        logger.info("入库检查: %s", db_dir)

    success = 0
    skipped_dup = 0
    skipped_replaced = 0
    failed = 0
    db_duplicates = []

    for pdf_path in pdfs:
        rel = pdf_path.relative_to(input_dir.parent if input_dir.is_absolute() else input_dir)
        doi = extract_doi_from_pdf(str(pdf_path))

        if not doi:
            dest_name = md5_of_file(str(pdf_path)) + ".pdf"
            dest = no_doi_dir / dest_name
            if dest.exists():
                logger.info("[无DOI-跳过] %s → %s (已存在)", pdf_path.name, dest_name)
                skipped_dup += 1
            else:
                if args.dry_run:
                    logger.info("[dry-run] 无DOI: %s → %s", pdf_path.name, dest_name)
                else:
                    shutil.copy2(str(pdf_path), str(dest))
                    logger.info("[无DOI] %s → %s", pdf_path.name, dest_name)
                failed += 1
            continue

        doi_clean = sanitize_doi(doi)
        dest_name = doi_clean + ".pdf"
        dest = output_dir / dest_name

        if args.check_existing:
            existing_md = check_doi_in_database(doi_clean, db_dir)
            if existing_md:
                rel_db = os.path.relpath(existing_md, db_dir.parent)
                logger.info("[已入库] %s (DOI: %s) → 同名 md 存在于: %s",
                            pdf_path.name, doi, rel_db)
                db_duplicates.append((doi, doi_clean, str(rel_db)))

        if dest.exists():
            if args.keep_larger:
                src_size = pdf_path.stat().st_size
                dst_size = dest.stat().st_size
                if src_size > dst_size:
                    if args.dry_run:
                        logger.info("[替换] %s (%d bytes) → 替换 %s (%d bytes)",
                                     pdf_path.name, src_size, dest_name, dst_size)
                    else:
                        shutil.copy2(str(pdf_path), str(dest))
                        logger.info("[替换] %s (%d bytes) → 覆盖 %s (原 %d bytes)",
                                     pdf_path.name, src_size, dest_name, dst_size)
                    skipped_replaced += 1
                else:
                    logger.info("[跳过] %s → %s (已存在，大小 %d ≤ %d)",
                                 pdf_path.name, dest_name, src_size, dst_size)
                    skipped_dup += 1
            else:
                logger.info("[跳过] %s → %s (已存在)", pdf_path.name, dest_name)
                skipped_dup += 1
        else:
            if args.dry_run:
                logger.info("[dry-run] %s → %s", pdf_path.name, dest_name)
            else:
                shutil.copy2(str(pdf_path), str(dest))
                logger.info("[成功] %s (DOI: %s) → %s", pdf_path.name, doi, dest_name)
            success += 1

    logger.info("=" * 50)
    logger.info("处理完成")
    logger.info("  成功:      %d", success)
    logger.info("  重复跳过:   %d", skipped_dup)
    if args.keep_larger:
        logger.info("  重复替换:   %d", skipped_replaced)
    logger.info("  无DOI:     %d", failed)
    total = success + skipped_dup + skipped_replaced + failed
    logger.info("  合计:      %d", total)

    if args.check_existing and db_duplicates:
        logger.info("-" * 50)
        logger.info("以下 %d 个论文的 DOI 已在 database 中存在同名 md 文件：", len(db_duplicates))
        for doi, doi_clean, db_path in sorted(db_duplicates, key=lambda x: x[0]):
            pdf_path_in_doi = output_dir / (doi_clean + ".pdf")
            pdf_exists = pdf_path_in_doi.exists()
            exists_tag = "✓ 已复制" if pdf_exists else " 未复制"
            logger.info("  %s%s → %s", doi, exists_tag, db_path)
        logger.info("=" * 50)
    elif args.check_existing and not db_duplicates:
        logger.info("  已入库重复: 0 个（全部是新论文）")

    logger.info("=" * 50)


# ==================== 主函数 ====================


def main():
    parser = argparse.ArgumentParser(
        description="多模式：提取 DOI + 去重 / 检查已入库 / 扫描 pdf_doi 检查冲突"
    )
    parser.add_argument("--input", default=DEFAULT_INPUT_DIR, help="输入目录")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR, help="成功输出目录 (pdf_doi)")
    parser.add_argument("--no-doi", default=DEFAULT_NO_DOI_DIR, help="提取失败输出目录 (pdf_no_doi)")
    parser.add_argument(
        "--keep-larger",
        action="store_true",
        help="当 pdf_doi 中已有同名文件时，比较大小保留较大的那个",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际复制")
    parser.add_argument(
        "--check-existing",
        action="store_true",
        help="提取 DOI 时检查 database 中是否已存在（仅报告，不跳过）",
    )
    parser.add_argument(
        "--scan-pdf-doi",
        action="store_true",
        help="不提取 DOI，直接扫描 --input 目录下的 DOI 目录名，与 database 比对冲突",
    )
    parser.add_argument(
        "--db-dir",
        default=DEFAULT_DATABASE_DIR,
        help="database 目录路径（默认: database/type/Lithium_Ion_Metal_Battery）",
    )
    args = parser.parse_args()

    if args.scan_pdf_doi:
        run_scan_pdf_doi(args)
    else:
        run_extract(args)


if __name__ == "__main__":
    main()
