#!/usr/bin/env python3
"""增量管道协调器 — 全流程增量处理
每步只处理新增文献，已存在的跳过。
"""
import os, sys, hashlib, subprocess, argparse, glob
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PDF_DIR = ROOT / "papers" / "pdf"
MD_DIR = ROOT / "papers" / "markdown"
MRG_DIR = ROOT / "papers" / "merged"
DB_DIR = ROOT / "database" / "type"
CHROMA_DIR = ROOT / "miner" / "chroma" / "paragraphs_v3"
JSON_DIR = ROOT / "miner" / "json"

def _markdown_path(pdf):
    return MD_DIR / pdf.stem / f"{pdf.stem}.md"

def _merged_path(pdf):
    return MRG_DIR / f"{pdf.stem}.md"

def _is_classified(name):
    for root, _, files in os.walk(str(DB_DIR)):
        if any(f == f"{name}.md" for f in files):
            return True
    return False

def step_mineru(pdf):
    out = _markdown_path(pdf)
    if out.exists(): print(f"[skip] MinerU {pdf.name}"); return True
    print(f"[MinerU] {pdf.name}...")
    r = subprocess.run([sys.executable, str(ROOT/"preprocessing"/"pdf_to_markdown.py"),
                       "--pdf-root", str(pdf.parent), "--file", pdf.name],
                      cwd=str(ROOT), capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=3600)
    if r.returncode: print(f"  FAIL: {r.stderr[:200]}"); return False
    print(f"  OK"); return True

def step_merge(pdf):
    md_dir = MD_DIR / pdf.stem
    if not md_dir.exists(): return False
    out = _merged_path(pdf)
    if out.exists(): print(f"[skip] 合并 {pdf.name}"); return True
    print(f"[合并] {pdf.name}...")
    r = subprocess.run([sys.executable, str(ROOT/"preprocessing"/"merge_markdown.py"), str(md_dir)],
                      cwd=str(ROOT), capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=600)
    if r.returncode: print(f"  FAIL: {r.stderr[:200]}"); return False
    print(f"  OK"); return True

def step_classify(pdf):
    merged = _merged_path(pdf)
    if not merged.exists(): return False
    if _is_classified(pdf.stem): print(f"[skip] 分类 {pdf.name}"); return True
    print(f"[分类] {pdf.name}...")
    subprocess.run([sys.executable, str(ROOT/"miner"/"classification"/"run_battery_type.py"), "--input", str(merged)],
                  cwd=str(ROOT), capture_output=True, timeout=600)
    subprocess.run([sys.executable, str(ROOT/"miner"/"classification"/"run_component_type.py"), "--input", str(merged)],
                  cwd=str(ROOT), capture_output=True, timeout=600)
    if _is_classified(pdf.stem): print(f"  OK"); return True
    print(f"  WARN: 分类完成但未找到"); return True

def step_index():
    print(f"[入库] v4 --incremental ...")
    r = subprocess.run([sys.executable, "-m", "miner.paragraph_metadata_pipeline_v4",
                       "--input-root", str(DB_DIR), "--incremental"], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=3600)
    print(r.stdout[-300:])
    if r.returncode: print(f"  FAIL: {r.stderr[:200]}"); return False
    print(f"  OK"); return True

def step_extract():
    print(f"[挖掘] v6 --resume ...")
    r = subprocess.run([sys.executable, "-m", "miner.extraction_core.extraction_pipeline_v6",
                       "-i", str(DB_DIR), "-o", str(JSON_DIR), "--component", "all", "--resume", "--limit", "5"],
                      cwd=str(ROOT), capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=3600)
    print(r.stdout[-300:])
    if r.returncode: print(f"  FAIL: {r.stderr[:200]}"); return False
    print(f"  OK"); return True

def run_all():
    for pdf in sorted(PDF_DIR.rglob("*.pdf")):
        print(f"\n{'='*50}\n{pdf.relative_to(ROOT)}")
        step_mineru(pdf) and step_merge(pdf) and step_classify(pdf)
    step_index()
    step_extract()

def run_one(pdf_path):
    pdf = Path(pdf_path).resolve()
    if not pdf.exists(): print(f"Not found: {pdf_path}"); return
    step_mineru(pdf); step_merge(pdf); step_classify(pdf)
    step_index(); step_extract()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", help="单篇 PDF")
    ap.add_argument("--step", choices=["mineru","merge","classify","index","extract"], help="单步")
    a = ap.parse_args()
    if a.pdf: run_one(a.pdf)
    else: run_all()
