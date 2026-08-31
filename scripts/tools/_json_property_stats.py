#!/usr/bin/env python3
"""统计 miner/json/100/ 中 extraction JSON 的属性频次"""
import json, os, sys
from collections import Counter

JSON_DIR = sys.argv[1] if len(sys.argv) > 1 else "/home/ls/xiaoyue/LLM2/LMLLM/miner/json/100"

property_counter = Counter()
prop_files = {}

json_files = sorted(f for f in os.listdir(JSON_DIR) if f.endswith("_extracted.json") and not f.startswith("_"))

for fname in json_files:
    fp = os.path.join(JSON_DIR, fname)
    with open(fp, encoding="utf-8") as f:
        data = json.load(f)

    props_in_file = set()
    for item in data.get("items", []):
        for ptype in item.get("property_types", []):
            props_in_file.add(ptype)
        for pname in item.get("extracted_info", {}):
            props_in_file.add(pname)
        for pname in item.get("performance_info", {}):
            props_in_file.add(pname)

    for p in props_in_file:
        property_counter[p] += 1
        prop_files.setdefault(p, []).append(fname)

print(f"来源: {len(json_files)} 个 extraction JSON")
print(f"属性种类: {len(property_counter)}")
print()
print(f"{'属性':<35} {'出现频次':>8} {'文件覆盖率':>8}")
print("-" * 55)

for prop, count in property_counter.most_common():
    n_files = len(prop_files.get(prop, []))
    pct = n_files / len(json_files) * 100 if json_files else 0
    print(f"{prop:<35} {count:>8} {n_files:>6}/{len(json_files)} ({pct:.0f}%)")
