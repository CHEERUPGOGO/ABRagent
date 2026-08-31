#!/usr/bin/env python3
"""组件分类适配器 — 支持自定义路径"""
import sys, os, shutil, json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
os.chdir(str(_PROJECT_ROOT))

BATTERY_IN = sys.argv[1] if len(sys.argv) > 1 else "database/battery_type/100"
COMPONENT_OUT = sys.argv[2] if len(sys.argv) > 2 else "database/type/100"
INCREMENTAL = "--incremental" in sys.argv

INPUT = str(_PROJECT_ROOT / BATTERY_IN)
OUTPUT = str(_PROJECT_ROOT / COMPONENT_OUT)

from miner.config import create_llm
from miner.extraction_core.pricing import TokenChecker
from miner.classification.component_type_agent import ComponentTypeAgent
from miner.cleaning.clean_text import clean_text

llm = create_llm("classification")
tc = TokenChecker(getattr(llm, "model_name", ""))
agent = ComponentTypeAgent.from_llm(llm, token_checker=tc)

all_tasks = []
for root, dirs, files in os.walk(INPUT):
    for f in files:
        if f.lower().endswith(".md"):
            all_tasks.append((root, f))

# 增量模式：读取处理记录（含被过滤的文献）
PROCESSED_LOG = os.path.join(OUTPUT, "._processed.json")
done = set()
if INCREMENTAL:
    try:
        with open(PROCESSED_LOG) as pf:
            done = set(json.load(pf))
    except: pass
    if done:
        print(f"[incremental] 已有 {len(done)} 篇已处理，跳过它们")

print(f"docs {len(all_tasks)} 篇文献")
for i, (root, fname) in enumerate(all_tasks, 1):
    if fname in done:
        print(f"  [{i}/{len(all_tasks)}] {fname} ⏭️ skip")
        continue
    fp = os.path.join(root, fname)
    battery_folder = os.path.basename(root)
    print(f"  [{i}/{len(all_tasks)}] {fname} [{battery_folder}]...", end=" ")
    cleaned = clean_text(fp, min_text_len=100, mode="extract")
    if cleaned is None:
        print("skip empty"); continue
    title = ""
    with open(fp, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.startswith("#") and not s.startswith("# "):
                title = s.lstrip("#").strip(); break
    result = agent.invoke({"title": title, "content": cleaned})
    out = result["output"]
    done.add(fname)  # 记录已处理
    component = out["component"]
    conf = out.get("confidence", 0)
    print(f"✅ {component} (conf={conf})")
    dest_dir = os.path.join(OUTPUT, battery_folder, component)
    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(fp, os.path.join(dest_dir, fname))
    if INCREMENTAL and i % 50 == 0:
        with open(PROCESSED_LOG, "w") as pf:
            json.dump(list(done), pf)
    base_name = os.path.splitext(fname)[0]
    img_src = os.path.join(os.path.dirname(fp), base_name + "images")
    if os.path.isdir(img_src):
        shutil.copytree(img_src, os.path.join(dest_dir, base_name + "images"), dirs_exist_ok=True)

print(f"\noutput: {OUTPUT}")
