#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电子书 Markdown 分片合并工具

功能:
  - 扫描 ebook_to_markdown.py 输出目录，将每个子文件夹内的分片 .md 按页码顺序合并
  - 输出单个完整的 .md 文件到指定目录
  - 保留分片间的页码标记，不复制图片

合并规则:
  - 文件名模式: {书名}_p{起始页}-{结束页}.md
    例: Lithium_Batteries_p0001-0200.md, ..., Lithium_Batteries_p0801-0850.md
  - 按起始页码升序合并，确保原文顺序
  - 每段之间插入 <!-- 分片标记 --> 用以追溯来源

用法:
  python ebook_merge.py                                              # 默认配置
  python ebook_merge.py --input ./ebooks/markdown --output ./ebooks/merged
"""

import os, re, sys, logging, argparse
from pathlib import Path
from typing import List, Tuple

# ══════════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════════

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def load_config(config_path: str = "config.yaml") -> dict:
    config = {
        "paths": {
            "input_root": "./papers/ebook/markdown",
            "output_root": "./papers/ebook/merged",
        },
    }
    if HAS_YAML and os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            yaml_cfg = yaml.safe_load(f) or {}
        if "paths" in yaml_cfg:
            paths = yaml_cfg["paths"]
            if "ebook_md_root" in paths:
                config["paths"]["input_root"] = paths["ebook_md_root"]
            if "ebook_merged_root" in paths:
                config["paths"]["output_root"] = paths["ebook_merged_root"]
    for env_var, key in [("EBOOK_MD_INPUT", "input_root"),
                           ("EBOOK_MERGED_OUTPUT", "output_root")]:
        val = os.environ.get(env_var)
        if val:
            config["paths"][key] = val
    return config


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S",
    )


# ══════════════════════════════════════════════════════════════════
# 分片文件名解析
# ══════════════════════════════════════════════════════════════════

PAGE_RANGE_RE = re.compile(r'_p(\d{4})-(\d{4})\.md$')


def parse_page_range(filename: str) -> Tuple[int, int]:
    """从文件名提取起始页和结束页，返回 (start, end)。"""
    m = PAGE_RANGE_RE.search(filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    return (0, 0)


def collect_ebook_folders(input_root: Path) -> List[Path]:
    """收集所有含有分片 .md 文件的子文件夹。"""
    folders = []
    for d in sorted(input_root.iterdir()):
        if not d.is_dir():
            continue
        md_files = [f for f in d.iterdir() if PAGE_RANGE_RE.search(f.name)]
        if len(md_files) >= 1:
            folders.append(d)
    return folders


# ══════════════════════════════════════════════════════════════════
# 合并逻辑
# ══════════════════════════════════════════════════════════════════

def merge_ebook_folder(folder: Path, output_root: Path) -> int:
    """合并一个电子书文件夹内的所有分片 .md 文件。

    返回合并的文件数。
    """
    folder_name = folder.name

    # 收集所有分片 .md 文件
    md_files = sorted(f for f in folder.iterdir()
                      if f.is_file() and f.suffix == ".md"
                      and PAGE_RANGE_RE.search(f.name))

    if len(md_files) == 0:
        return 0

    # 按起始页码排序
    md_files.sort(key=lambda f: parse_page_range(f.name)[0])

    parts = []
    parts.append(f"<!-- ====== 电子书: {folder_name} ====== -->\n")
    parts.append(f"<!-- 共 {len(md_files)} 个分片 -->\n\n")

    prev_end = 0
    for i, f in enumerate(md_files, 1):
        start, end = parse_page_range(f.name)
        gap = ""
        if prev_end > 0 and start > prev_end + 1:
            gap = f" (注意：与前一片之间有 {start - prev_end - 1} 页缺口)"
        parts.append(f"<!-- ====== 分片 {i}/{len(md_files)}: p{start}-{end}{gap} ====== -->\n")
        content = f.read_text(encoding="utf-8").rstrip()
        parts.append(content)
        parts.append("\n\n")
        prev_end = end

    output_file = output_root / f"{folder_name}_merged.md"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("".join(parts), encoding="utf-8")

    total_bytes = output_file.stat().st_size
    mb = total_bytes / (1024 * 1024)
    logging.info("合并: %s → %s (%d 分片, %.1f MB)",
                 folder_name, output_file.name, len(md_files), mb)

    return len(md_files)


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

def run_merge(input_root: str, output_root: str) -> Tuple[int, int]:
    """外部可调用入口。返回 (folders_processed, files_merged)。"""
    in_path = Path(input_root)
    out_path = Path(output_root)
    return _do_merge(in_path, out_path)


def _do_merge(input_root: Path, output_root: Path) -> Tuple[int, int]:
    folders = collect_ebook_folders(input_root)
    if not folders:
        logging.warning("未找到包含分片 .md 文件的文件夹。")
        return (0, 0)

    output_root.mkdir(parents=True, exist_ok=True)
    total_files = 0
    for folder in folders:
        n = merge_ebook_folder(folder, output_root)
        total_files += n

    logging.info("合并完成: %d 个文件夹, %d 个分片 → %s",
                 len(folders), total_files, output_root)
    return (len(folders), total_files)


def main():
    script_dir = Path(__file__).resolve().parent
    default_config = str(script_dir / "config.yaml")

    parser = argparse.ArgumentParser(
        description="电子书 Markdown 分片 → 按页码顺序合并",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ebook_merge.py
  python ebook_merge.py --input ./ebooks/md --output ./ebooks/merged
        """,
    )
    parser.add_argument("-c", "--config", default=default_config, help="配置文件路径")
    parser.add_argument("--input", help="ebook_to_markdown.py 的输出目录")
    parser.add_argument("--output", help="合并后的 .md 输出目录")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.input:
        config["paths"]["input_root"] = args.input
    if args.output:
        config["paths"]["output_root"] = args.output

    setup_logging()

    in_root = config["paths"]["input_root"]
    out_root = config["paths"]["output_root"]

    logging.info("=" * 54)
    logging.info("电子书 Markdown 分片合并")
    logging.info("输入: %s", in_root)
    logging.info("输出: %s", out_root)
    logging.info("=" * 54)

    run_merge(in_root, out_root)


if __name__ == "__main__":
    main()
