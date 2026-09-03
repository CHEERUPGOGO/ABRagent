#!/usr/bin/env python3
"""增量管道核心模块 — 全流程增量处理 (AutoBatteryResearch Agent).

包含文献 MinerU 解析、主附录合并、组件分类、语义向量入库及实体挖掘的核心增量方法。
每步只处理新增文献，已存在的自动跳过（幂等安全）。
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path
from typing import Optional, Union

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def get_paths(root: Optional[Path] = None):
    r = root or ROOT_DIR
    return {
        "root": r,
        "pdf_dir": r / "papers" / "pdf",
        "md_dir": r / "papers" / "markdown",
        "mrg_dir": r / "papers" / "merged",
        "db_dir": r / "database" / "type",
        "chroma_dir": r / "miner" / "chroma" / "paragraphs_v3",
        "json_dir": r / "miner" / "json",
    }


def _markdown_path(pdf: Path, md_dir: Optional[Path] = None) -> Path:
    base = md_dir or (ROOT_DIR / "papers" / "markdown")
    return base / pdf.stem / f"{pdf.stem}.md"


def _merged_path(pdf: Path, mrg_dir: Optional[Path] = None) -> Path:
    base = mrg_dir or (ROOT_DIR / "papers" / "merged")
    return base / f"{pdf.stem}.md"


def is_classified(name: str, db_dir: Optional[Path] = None) -> bool:
    base = db_dir or (ROOT_DIR / "database" / "type")
    if not base.exists():
        return False
    for _, _, files in os.walk(str(base)):
        if any(f == f"{name}.md" for f in files):
            return True
    return False


def step_mineru(pdf: Union[str, Path], root: Optional[Path] = None) -> bool:
    """Stage 1.1: MinerU PDF 转 Markdown."""
    r = Path(root) if root else ROOT_DIR
    pdf_p = Path(pdf).resolve()
    paths = get_paths(r)

    out = _markdown_path(pdf_p, paths["md_dir"])
    if out.exists():
        print(f"[skip] MinerU {pdf_p.name}")
        return True
    print(f"[MinerU] {pdf_p.name}...")
    res = subprocess.run(
        [
            sys.executable,
            str(r / "preprocessing" / "pdf_to_markdown.py"),
            "--pdf-root",
            str(pdf_p.parent),
            "--file",
            pdf_p.name,
        ],
        cwd=str(r),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
    )
    if res.returncode:
        print(f"  FAIL: {res.stderr[:200]}")
        return False
    print("  OK")
    return True


def step_merge(pdf: Union[str, Path], root: Optional[Path] = None) -> bool:
    """Stage 1.2: 合并正文与 SI 补充材料."""
    r = Path(root) if root else ROOT_DIR
    pdf_p = Path(pdf).resolve()
    paths = get_paths(r)

    md_dir = paths["md_dir"] / pdf_p.stem
    if not md_dir.exists():
        return False
    out = _merged_path(pdf_p, paths["mrg_dir"])
    if out.exists():
        print(f"[skip] 合并 {pdf_p.name}")
        return True
    print(f"[合并] {pdf_p.name}...")
    res = subprocess.run(
        [
            sys.executable,
            str(r / "preprocessing" / "merge_markdown.py"),
            str(md_dir),
        ],
        cwd=str(r),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if res.returncode:
        print(f"  FAIL: {res.stderr[:200]}")
        return False
    print("  OK")
    return True


def step_classify(pdf: Union[str, Path], root: Optional[Path] = None) -> bool:
    """Stage 1.3: 分类入库至电池体系与组件目录 (正极/负极/电解液)."""
    r = Path(root) if root else ROOT_DIR
    pdf_p = Path(pdf).resolve()
    paths = get_paths(r)

    merged = _merged_path(pdf_p, paths["mrg_dir"])
    if not merged.exists():
        return False
    if is_classified(pdf_p.stem, paths["db_dir"]):
        print(f"[skip] 分类 {pdf_p.name}")
        return True
    print(f"[分类] {pdf_p.name}...")
    subprocess.run(
        [
            sys.executable,
            str(r / "miner" / "classification" / "run_battery_type.py"),
            "--input",
            str(merged),
        ],
        cwd=str(r),
        capture_output=True,
        timeout=600,
    )
    subprocess.run(
        [
            sys.executable,
            str(r / "miner" / "classification" / "run_component_type.py"),
            "--input",
            str(merged),
        ],
        cwd=str(r),
        capture_output=True,
        timeout=600,
    )
    if is_classified(pdf_p.stem, paths["db_dir"]):
        print("  OK")
        return True
    print("  WARN: 分类完成但未在组件目录下找到")
    return True


def step_index(root: Optional[Path] = None) -> bool:
    """Stage 2: 向量化入库."""
    r = Path(root) if root else ROOT_DIR
    paths = get_paths(r)
    print("[入库] v4 --incremental ...")
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "miner.paragraph_metadata_pipeline_v4",
            "--input-root",
            str(paths["db_dir"]),
            "--incremental",
        ],
        cwd=str(r),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
    )
    print(res.stdout[-300:])
    if res.returncode:
        print(f"  FAIL: {res.stderr[:200]}")
        return False
    print("  OK")
    return True


def step_extract(root: Optional[Path] = None) -> bool:
    """Stage 3: 实体挖掘."""
    r = Path(root) if root else ROOT_DIR
    paths = get_paths(r)
    print("[挖掘] v6 --resume ...")
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "miner.extraction_core.extraction_pipeline_v6",
            "-i",
            str(paths["db_dir"]),
            "-o",
            str(paths["json_dir"]),
            "--component",
            "all",
            "--resume",
            "--limit",
            "5",
        ],
        cwd=str(r),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
    )
    print(res.stdout[-300:])
    if res.returncode:
        print(f"  FAIL: {res.stderr[:200]}")
        return False
    print("  OK")
    return True


def run_all(root: Optional[Path] = None):
    r = Path(root) if root else ROOT_DIR
    paths = get_paths(r)
    for pdf in sorted(paths["pdf_dir"].rglob("*.pdf")):
        print(f"\n{'='*50}\n{pdf.relative_to(r)}")
        if step_mineru(pdf, r) and step_merge(pdf, r):
            step_classify(pdf, r)
    step_index(r)
    step_extract(r)


def run_one(pdf_path: Union[str, Path], root: Optional[Path] = None):
    r = Path(root) if root else ROOT_DIR
    pdf = Path(pdf_path).resolve()
    if not pdf.exists():
        print(f"Not found: {pdf_path}")
        return
    step_mineru(pdf, r)
    step_merge(pdf, r)
    step_classify(pdf, r)
    step_index(r)
    step_extract(r)
