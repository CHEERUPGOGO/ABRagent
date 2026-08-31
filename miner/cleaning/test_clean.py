#!/usr/bin/env python3
"""
清洗结果查看工具 — 对指定 .md 文件运行 clean_text1 并输出清洗结果。

用法:
  python miner/cleaning/test_clean.py                                    # 默认测试目录
  python miner/cleaning/test_clean.py -f papers/merged/test/anode.md    # 单个文件
  python miner/cleaning/test_clean.py -i database/type/test -o out      # 目录+保存
  python miner/cleaning/test_clean.py --raw                              # 直接输出清洗后的 markdown 文本
  python miner/cleaning/test_clean.py --no-skip                         # 保留摘要/引言
"""

import os, sys, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from miner.cleaning.clean_text1 import clean_text


def print_header(text: str, width: int = 66):
    print(f"\n {'='*width} ")
    print(f"  {text}")
    print(f" {'='*width} ")


def inspect_file(file_path: str, mode: str = "extract", save_to: str = None):
    fname = os.path.basename(file_path)
    print_header(f"输入: {file_path}")
    raw_size = os.path.getsize(file_path)
    print(f"  原始文件: {raw_size:,} 字节")
    text = clean_text(file_path, min_text_len=50, mode=mode)
    if text is None:
        print("  ⚠ 清洗后文本低于阈值，返回 None\n")
        return
    all_paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    paras = [p for p in all_paras if len(p) > 50 or p.startswith("#")]
    print(f"  清洗后: {len(text):,} 字符, {len(paras)} 段落（含标题）\n")
    for i, p in enumerate(paras):
        short = p[:200].replace("\n", " ")
        if len(p) > 200: short += "..."
        tag = "[H]" if p.startswith("#") else "   "
        print(f"  {tag} [{i:02d}/{len(paras):02d}] ({len(p):>5}字) {short}")
    if save_to:
        os.makedirs(os.path.dirname(save_to) or ".", exist_ok=True)
        with open(save_to, "w", encoding="utf-8") as f: f.write(text)
        print(f"\n  -> 已保存: {save_to}")


def scan_dir(input_dir: str, mode: str = "extract", save_root: str = None):
    md_files = []
    for root, dirs, files in os.walk(input_dir):
        for f in files:
            if f.endswith(".md"): md_files.append(os.path.join(root, f))
    md_files.sort()
    print_header(f"扫描目录: {input_dir}")
    print(f"  发现 {len(md_files)} 个 .md 文件\n")
    for fp in md_files:
        rel = os.path.relpath(fp, input_dir)
        sp = os.path.join(save_root, rel) if save_root else None
        text = clean_text(fp, min_text_len=50, mode=mode)
        if text is None:
            print(f"  {rel} -> None"); continue
        paras = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 50 or p.strip().startswith("#")]
        print(f"  {rel} -> {len(text):,} 字, {len(paras)} 段（含标题）")
        if sp:
            os.makedirs(os.path.dirname(sp), exist_ok=True)
            with open(sp, "w", encoding="utf-8") as f: f.write(text)


def main():
    p = argparse.ArgumentParser(description="查看 clean_text1 清洗结果")
    p.add_argument("-f", "--file", help="单个 .md 文件")
    p.add_argument("-i", "--input-dir", default="database/type/test",
                   help="输入目录（默认: database/type/test）")
    p.add_argument("-o", "--output-dir", help="保存清洗结果到此目录")
    p.add_argument("--raw", action="store_true",
                   help="直接输出清洗后的 markdown 文本到 stdout")
    p.add_argument("--no-skip", action="store_true",
                   help="classify 模式（不跳过摘要/引言）")
    args = p.parse_args()
    mode = "classify" if args.no_skip else "extract"
    if args.file:
        if args.raw:
            print(clean_text(args.file, min_text_len=50, mode=mode) or "")
        else:
            inspect_file(args.file, mode=mode, save_to=args.output_dir)
    else:
        if args.raw:
            md_files = []
            for root, dirs, files in os.walk(args.input_dir):
                for f in files:
                    if f.endswith(".md"): md_files.append(os.path.join(root, f))
            md_files.sort()
            for fp in md_files:
                rel = os.path.relpath(fp, args.input_dir)
                text = clean_text(fp, min_text_len=50, mode=mode)
                if text:
                    print(f"# ==== {rel} ====\n")
                    print(text)
                    print()
        else:
            scan_dir(args.input_dir, mode=mode, save_root=args.output_dir)


if __name__ == "__main__":
    main()
