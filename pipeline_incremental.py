#!/usr/bin/env python3
"""增量管道协调器 (向下兼容入口包装) — 全流程增量处理.

核心实现已沉淀至 `auto_battery_research.pipeline.incremental`。
本入口保留以保证历史命令 `python pipeline_incremental.py` 与外部引用的 100% 兼容。
"""

import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auto_battery_research.pipeline.incremental import (
    step_mineru,
    step_merge,
    step_classify,
    step_index,
    step_extract,
    run_all,
    run_one,
)

PDF_DIR = ROOT / "papers" / "pdf"
MD_DIR = ROOT / "papers" / "markdown"
MRG_DIR = ROOT / "papers" / "merged"
DB_DIR = ROOT / "database" / "type"
CHROMA_DIR = ROOT / "miner" / "chroma" / "paragraphs_v3"
JSON_DIR = ROOT / "miner" / "json"

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AutoBatteryResearch 增量处理流水线 (兼容包装入口)")
    ap.add_argument("--pdf", help="单篇 PDF 路径")
    ap.add_argument("--step", choices=["mineru", "merge", "classify", "index", "extract"], help="单步执行")
    a = ap.parse_args()

    if a.pdf:
        if a.step == "mineru":
            step_mineru(a.pdf)
        elif a.step == "merge":
            step_merge(a.pdf)
        elif a.step == "classify":
            step_classify(a.pdf)
        else:
            run_one(a.pdf)
    elif a.step == "index":
        step_index()
    elif a.step == "extract":
        step_extract()
    else:
        run_all()
