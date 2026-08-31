#!/usr/bin/env python3
"""battery_type_agent 适配器 — 用 extract 模式清洗后分类"""
import sys, os, json
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from miner.config import create_llm
from miner.extraction_core.pricing import TokenChecker
from miner.classification.battery_type_agent import BatteryTypeAgent, CATEGORIES, copy_md_to_category, REVIEW_KEYWORDS
from miner.cleaning.clean_text import clean_text

import argparse; parser=argparse.ArgumentParser(); parser.add_argument("--input",default="papers/markdown/run1"); parser.add_argument("--output",default="database/battery_type/run1"); parser.add_argument("--incremental",action="store_true",help="跳过已处理的文献"); args=parser.parse_args()
INPUT = str(_PROJECT_ROOT / args.input)
OUTPUT = str(_PROJECT_ROOT / args.output)
os.makedirs(OUTPUT, exist_ok=True)

llm = create_llm("classification")
tc = TokenChecker(getattr(llm,"model_name",""))
agent = BatteryTypeAgent.from_llm(llm, token_checker=tc)

files = sorted([f for f in os.listdir(INPUT) if f.lower().endswith(".md")])
# 增量模式：读取处理记录（含被过滤的文献）
PROCESSED_LOG = os.path.join(OUTPUT, "._processed.json")
done = set()
if args.incremental:
    try:
        with open(PROCESSED_LOG) as pf:
            done = set(json.load(pf))
    except: pass
    if done:
        print(f"[incremental] 已有 {len(done)} 篇已处理，跳过它们")
print(f"📂 {len(files)} 篇文献")
for i, fname in enumerate(files, 1):
    if fname in done:
        print(f"  [{i}/{len(files)}] {fname} ⏭️  跳过（已处理）")
        continue
    fp = os.path.join(INPUT, fname)
    print(f"  [{i}/{len(files)}] {fname}...", end=" ")
    cleaned = clean_text(fp, min_text_len=100, mode="extract")
    if cleaned is None:
        print("⏭️ 空文本"); continue
    title = ""
    with open(fp, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.startswith("#") and "Supplementary" not in s:
                text = s.lstrip("#").strip()
                if text and not text.startswith(" ") and not text[0].isdigit():
                    title = text; break
    result = agent.invoke({"title": title, "content": cleaned})
    out = result["output"]
    done.add(fname)  # 记录已处理（含被过滤的）
    # 综述兜底：LLM判断 + 标题关键词检测 + 内容摘要关键词检测
    is_review = out.get("is_review", False)
    if not is_review:
        text_lower = (title + " " + cleaned[:5000]).lower()
        if any(kw in text_lower for kw in REVIEW_KEYWORDS):
            is_review = True
    subtype = out.get("subtype", "unclassified")
    if not subtype or subtype == "none":
        subtype = "unclassified"
    if is_review:
        print(f"⏭️  跳过（综述文章, src={out.get('source','?')})")
        continue
    if out.get("is_recycling", False):
        print(f"⏭️  跳过（电池回收文章, src={out.get('source','?')})")
        continue
    if out.get("is_flexible_battery", False):
        print(f"⏭️  跳过（柔性/可穿戴电池文章, src={out.get('source','?')})")
        continue
    CATS = ["Li-ion/Li-metal"]
    if subtype not in CATS:
        print(f"⏭️  跳过（非Li-ion/Li-metal电池: {subtype}）")
        continue
    print(f"✅ {subtype} (src={out.get('source','?')})")
    copy_md_to_category(fp, OUTPUT, subtype)
    # 每处理一篇就原子写入处理记录
    if args.incremental:
        tmp = PROCESSED_LOG + ".tmp"
        with open(tmp, "w") as pf:
            json.dump(list(done), pf)
        os.replace(tmp, PROCESSED_LOG)

print(f"\n📁 输出: {OUTPUT}")
for cat in CATEGORIES.values():
    d = os.path.join(OUTPUT, cat)
    if os.path.isdir(d):
        print(f"  {cat}: {os.listdir(d)}")