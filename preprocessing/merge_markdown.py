#!/usr/bin/env python3
"""
合并 pdf_to_markdown.py 生成的 Markdown 文件。

功能:
  - 递归扫描输出目录，处理每个子文件夹内的 .md 文件（多个合并，单个直接输出）
  - 自动识别源文献（主文献）和 SI 补充信息，源文献在前、SI 在后
  - 以最后一层子文件夹名作为合并后的文件名
  - 自动复制图片文件夹到输出目录
  - 支持从 config.yaml 读取路径，也可命令行覆盖

用法:
  python merge_markdown.py                           # 使用默认配置
  python merge_markdown.py -c config.yaml            # 指定配置文件
  python merge_markdown.py --input ./papers/markdown --output ./papers/merged

合并规则:
  - 每个子文件夹中: 所有主文献 .md（按名排序）→ 所有 SI .md（按名排序）
  - SI 判断: 文件名含 _si_ / supplement / supporting 等关键词
  - 输出文件名: {子文件夹名}.md
  - 图片文件夹: 原样复制到输出目录
"""

import os
import sys
import shutil
import logging
import argparse
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------------
# YAML 配置（可选）
# ---------------------------------------------------------------------------
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# =============================================================================
# 配置加载
# =============================================================================

def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置，优先级：命令行 > 环境变量 > 配置文件 > 默认值。

    与 pdf_to_markdown.py 共享 config.yaml:
      - merge 的输入 = config.yaml 中 paths.output_root
      - merge 的输出 = config.yaml 中 paths.merged_root
    """
    config = {
        "paths": {
            "input_root": "./papers/markdown",
            "output_root": "./papers/merged",
        },
    }

    if HAS_YAML and os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f) or {}
        if "paths" in yaml_config:
            yaml_paths = yaml_config["paths"]
            # 从共享 config 读取：merge 输入 = pdf_to_markdown 输出
            if "output_root" in yaml_paths:
                config["paths"]["input_root"] = yaml_paths["output_root"]
            if "merged_root" in yaml_paths:
                config["paths"]["output_root"] = yaml_paths["merged_root"]

    # 环境变量
    for env_var, key in [("MERGE_INPUT_ROOT", "input_root"),
                          ("MERGE_OUTPUT_ROOT", "output_root")]:
        val = os.environ.get(env_var)
        if val:
            config["paths"][key] = val

    return config


# =============================================================================
# 日志
# =============================================================================

def setup_logging():
    fmt = "%(asctime)s [%(levelname)-7s] %(message)s"
    datefmt = "%H:%M:%S"
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt=datefmt)


# =============================================================================
# SI 文件判断
# =============================================================================

# SI 文件常见命名特征（不区分大小写）
SI_PATTERNS = [
    "_si_", "_si.", "-si_", "-si.",
    "supplement", "supporting",
    "_suppl", "_supp.",
    "appendix", "appendices",
    "_sm_", "_sm.",           # Supporting Material
    "_esi_", "_esi.",         # Electronic Supplementary Information
]


def is_si_file(filename: str) -> bool:
    """判断文件名是否属于 SI 补充信息。"""
    name_lower = filename.lower()
    return any(p in name_lower for p in SI_PATTERNS)


# =============================================================================
# 合并逻辑
# =============================================================================

def collect_image_dirs(folder: Path) -> List[Path]:
    """收集文件夹下所有图片目录（名称含 'images' 的目录）。"""
    image_dirs = []
    for item in sorted(folder.iterdir()):
        if item.is_dir() and "images" in item.name.lower():
            image_dirs.append(item)
    return image_dirs


def merge_subfolder(folder: Path, output_root: Path) -> int:
    """
    合并一个子文件夹中的 markdown 文件。

    返回合并的文件数（0 表示无文件可处理）。
    """
    # 收集 .md 文件（排除 full.md 和已是合并产物的文件）
    md_files = sorted([
        f for f in folder.iterdir()
        if f.is_file() and f.suffix == ".md"
        and f.name != "full.md"
        and not f.name.startswith("merged_")   # 排除自身产物
    ])

    if len(md_files) == 0:
        return 0

    # 分组：主文献 vs SI
    main_files = [f for f in md_files if not is_si_file(f.name)]
    si_files = [f for f in md_files if is_si_file(f.name)]

    if not main_files and not si_files:
        return 0

    # 合并时记录来源注释
    merged_parts = []

    if main_files:
        merged_parts.append(f"<!-- ====== 源文献 ({len(main_files)} 篇) ====== -->\n")
        for f in main_files:
            merged_parts.append(f"<!-- File: {f.name} -->\n")
            merged_parts.append(f.read_text(encoding="utf-8").rstrip() + "\n\n")

    if si_files:
        merged_parts.append(f"<!-- ====== 补充信息 SI ({len(si_files)} 篇) ====== -->\n")
        for f in si_files:
            merged_parts.append(f"<!-- File: {f.name} -->\n")
            merged_parts.append(f.read_text(encoding="utf-8").rstrip() + "\n\n")

    # 输出文件名：最后一层子文件夹名
    folder_name = folder.name
    output_file = output_root / f"{folder_name}.md"
    output_file.write_text("".join(merged_parts), encoding="utf-8")

    # 计算相对路径用于日志
    try:
        rel = folder.relative_to(folder.parents[2])
    except (ValueError, IndexError):
        rel = folder

    logging.info("合并: %s → %s (主文献=%d, SI=%d)",
                 rel, output_file.name, len(main_files), len(si_files))

    # 复制图片目录
    image_dirs = collect_image_dirs(folder)
    for img_dir in image_dirs:
        target = output_root / img_dir.name
        if not target.exists():
            shutil.copytree(img_dir, target)
            logging.info("  复制图片目录: %s → %s", img_dir.name, target)
        else:
            # 合并：只复制新文件
            new_count = 0
            for item in img_dir.rglob("*"):
                rel_item = item.relative_to(img_dir)
                dest = target / rel_item
                if item.is_file() and not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest)
                    new_count += 1
            if new_count > 0:
                logging.info("  合并图片目录: %s (+%d 新文件)", img_dir.name, new_count)

    return len(md_files)


# =============================================================================
# 核心函数（可被 pdf_to_markdown.py 导入调用）
# =============================================================================

def run_merge(input_root: str, output_root: str) -> Tuple[int, int]:
    """执行合并，返回 (合并的文件夹数, 合并的文件总数)。

    供 pdf_to_markdown.py 联动调用，也可被 main() 使用。
    """
    input_path = Path(input_root)
    output_path = Path(output_root)

    if not input_path.is_dir():
        logging.error("输入路径不存在: %s", input_path)
        return 0, 0

    output_path.mkdir(parents=True, exist_ok=True)

    total_merged = 0
    total_files = 0

    for root, dirs, files in os.walk(input_path):
        root_path = Path(root)
        md_count = sum(1 for f in files if f.endswith(".md") and f != "full.md" and not f.startswith("merged_"))

        if md_count >= 1:
            n = merge_subfolder(root_path, output_path)
            if n > 0:
                total_merged += 1
                total_files += n

    return total_merged, total_files


# =============================================================================
# 主函数
# =============================================================================

def main():
    _script_dir = Path(__file__).resolve().parent
    _default_config = str(_script_dir / "config.yaml")

    parser = argparse.ArgumentParser(
        description="合并 pdf_to_markdown.py 生成的 Markdown 文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-c", "--config", default=_default_config,
                        help="配置文件路径 (默认: 脚本同目录下的 config.yaml)")
    parser.add_argument("--input", "-i", help="Markdown 源文件夹（覆盖配置文件）")
    parser.add_argument("--output", "-o", help="合并后输出文件夹（覆盖配置文件）")
    args = parser.parse_args()

    setup_logging()
    config = load_config(args.config)

    input_root = args.input or config["paths"]["input_root"]
    output_root = args.output or config["paths"]["output_root"]

    logging.info("=" * 50)
    logging.info("Markdown 合并工具")
    logging.info("输入路径:   %s", input_root)
    logging.info("输出路径:   %s", output_root)
    logging.info("合并规则:   源文献在前, SI 补充信息在后")
    logging.info("=" * 50)

    total_merged, total_files = run_merge(input_root, output_root)

    if total_merged == 0:
        logging.warning("未找到需要合并的文件夹（每个文件夹至少需要 1 个 .md 文件）")
    else:
        logging.info("=" * 50)
        logging.info("完成: 合并了 %d 个文件夹, 共 %d 个文件", total_merged, total_files)
        logging.info("输出目录: %s", output_root)


if __name__ == "__main__":
    main()
