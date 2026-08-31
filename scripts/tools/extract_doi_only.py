#!/usr/bin/env python3
"""从 PDF 提取 DOI，按 DOI 重命名复制到 pdf_doi/，保留原始子文件夹结构

用法：
    python extract_doi_only.py                         # 默认：无DOI文件 → pdf_no_doi/
    python extract_doi_only.py --skip-no-doi            # 无DOI文件原地跳过，手动处理

输入：papers/pdf/ 下的子文件夹（如 242, chan, za 等）
输出：
    成功 → pdf_doi/{子文件夹名}/{DOI}/原文件名.pdf
    --skip-no-doi 时 → 无 DOI 的文件原地跳过，日志标记
    默认 → pdf_no_doi/{子文件夹名}/原文件名.pdf

示例：
    papers/pdf/242/xxx/paper.pdf → pdf_doi/242/10.1002_adma.202107326/paper.pdf
"""
import os, re, shutil, logging, sys, argparse
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    print("请安装 pdfplumber: pip install pdfplumber")
    sys.exit(1)

DOI_PATTERN = re.compile(r'\b10\.\d{4,9}/[^\s,;:\[\]<>]+', re.IGNORECASE)

# 输入：papers/pdf 根目录，将遍历其下所有子文件夹
INPUT_DIR = "/home/ls/xiaoyue/LLM2/LMLLM/papers/pdf"
# 输出：保留子文件夹名
OUTPUT_DIR = "/home/ls/xiaoyue/LLM2/LMLLM/papers/pdf_doi"
NO_DOI_DIR = "/home/ls/xiaoyue/LLM2/LMLLM/papers/pdf_no_doi"

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def extract_doi(pdf_path: str) -> str | None:
    try:
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            scan_pages = set()
            scan_pages.update(range(min(5, total)))
            scan_pages.update(range(max(0, total - 3), total))
            full_text = ""
            for i in sorted(scan_pages):
                t = pdf.pages[i].extract_text()
                if t: full_text += t + "\n"
            if not full_text:
                return None
            clean = re.sub(r'\s+', ' ', full_text)

            match = DOI_PATTERN.search(clean)
            if match:
                return match.group(0).rstrip('.,;:()[]<>')
            return None
    except Exception as e:
        logger.warning(f"读取失败 {Path(pdf_path).name}: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description="从 PDF 提取 DOI，按 DOI 重命名，保留原始子文件夹结构"
    )
    parser.add_argument("--input", default=INPUT_DIR, help=f"PDF 源目录（默认: {INPUT_DIR}）")
    parser.add_argument("--output", default=OUTPUT_DIR, help=f"成功输出目录（默认: {OUTPUT_DIR}）")
    parser.add_argument("--no-doi", default=NO_DOI_DIR, help=f"失败输出目录（默认: {NO_DOI_DIR}）")
    parser.add_argument(
        "--skip-no-doi", action="store_true",
        help="对无 DOI 的文件不复制不移动，仅日志标记，留给手动处理"
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)
    no_doi_root = Path(args.no_doi)

    if not input_path.exists():
        logger.error(f"目录不存在: {input_path}")
        return

    # 如果输入直接是 papers/pdf/{子文件夹}，子文件夹名直接取 input_path.name
    if input_path.parent.name == "pdf" and input_path.name != "pdf":
        subdir_fixed = input_path.name  # 如 --input papers/pdf/242 → "242"
        logger.info(f"输入子文件夹: {subdir_fixed}")
    else:
        subdir_fixed = None  # 从每个文件的相对路径中动态提取

    pdfs = sorted(input_path.rglob("*.pdf"))
    logger.info(f"共发现 {len(pdfs)} 个 PDF")

    success = 0
    skipped = 0
    failed = 0
    for pdf in pdfs:
        # 计算子文件夹名：优先用固定值，否则从相对路径提取第一级
        if subdir_fixed:
            subdir = subdir_fixed
        else:
            rel = pdf.relative_to(input_path)
            subdir = rel.parts[0] if len(rel.parts) > 1 else ""

        doi = extract_doi(str(pdf))
        if doi:
            doi_clean = doi.replace("/", "_")
            # 输出：pdf_doi/{子文件夹}/{DOI}/原文件名.pdf
            dst_dir = output_dir / subdir / doi_clean
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / pdf.name
            shutil.copy2(str(pdf), str(dst))
            logger.info(f"✅ [{subdir}] {doi}  →  {subdir}/{doi_clean}/{pdf.name}")
            success += 1
        elif args.skip_no_doi:
            # 跳过，留给手动处理
            logger.warning(f"⚠️ [{subdir}] 跳过-待手动处理: {rel}")
            skipped += 1
        else:
            # 输出：pdf_no_doi/{子文件夹}/原文件名.pdf
            no_doi_path = no_doi_root / subdir
            no_doi_path.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(pdf), str(no_doi_path / pdf.name))
            logger.warning(f"❌ [{subdir}] 未找到 DOI: {pdf.name}")
            failed += 1

    logger.info(f"完成: 成功={success}, 跳过-待手动处理={skipped}, 失败={failed}")


if __name__ == "__main__":
    main()
