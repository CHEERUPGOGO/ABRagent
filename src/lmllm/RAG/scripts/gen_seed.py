"""从 miner 提取结果生成候选句子 — 阶段 1 种子标注取材

读取 miner/json/*_extracted_v4.json 的段落文本，分句后按三类关系关键词
过滤，输出候选句子清单（带来源 DOI）到 data/seed/candidate_sentences.json。
人工从候选中挑选标注种子（30-50 条）。

用法：
  python scripts/gen_seed.py [--max-per-type 20]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "seed"

EXTRACT_FILES = [
    "cathode_cathode_extracted_v4.json",
    "anode_anode_extracted_v4.json",
    "electrolyte_electrolyte_extracted_v4.json",
]

# 关系类型 → 关键词（英文/中文）
TYPE_KEYWORDS = {
    "doping": ["dop", "doped", "substitut", "掺杂", "共掺", "co-dop"],
    "compatibility": ["incompatible", "compatible", "不适用", "适用于", "oxidation",
                      "decompos", "dendrite", "窗口", "corrosion", "不可行"],
    "performance": ["mAh/g", "mAh g", "Wh/kg", "Wh kg", "retains", "retention",
                    "capacity of", "conductivity", "S/cm", "coulombic efficiency"],
}


def find_miner_dir() -> Path:
    """从脚本位置向上搜索 miner/json 目录（健壮，不依赖固定层级）。"""
    p = Path(__file__).resolve().parent
    while p != p.parent:
        cand = p / "miner" / "json"
        if cand.exists():
            return cand
        p = p.parent
    raise FileNotFoundError("未找到 miner/json 目录")


def split_sentences(text: str) -> list:
    """按句号/分号切分段落，去噪声，过滤长度。"""
    text = re.sub(r"\s+", " ", text)
    parts = re.split(r"(?<=[.;])\s+", text)
    out = []
    for p in parts:
        p = p.strip()
        if 30 <= len(p) <= 600 and not re.search(r"Figure|Fig\.|Table|Scheme|^\d", p):
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-per-type", type=int, default=20)
    args = ap.parse_args()

    miner_dir = find_miner_dir()
    candidates = {t: [] for t in TYPE_KEYWORDS}
    n_files = 0
    for fname in EXTRACT_FILES:
        fp = miner_dir / fname
        if not fp.exists():
            print(f"[gen_seed] 跳过（不存在）: {fname}")
            continue
        data = json.loads(fp.read_text(encoding="utf-8"))
        n_files += 1
        doi = data.get("paper", {}).get("doi", "")
        for mat in data.get("materials", []):
            for item in mat.get("items", []):
                para = item.get("paragraph", "")
                for sent in split_sentences(para):
                    low = sent.lower()
                    for t, kws in TYPE_KEYWORDS.items():
                        if any(k.lower() in low for k in kws):
                            candidates[t].append({
                                "doi": doi,
                                "material": mat.get("name", ""),
                                "sentence": sent,
                            })
                            break  # 一句归一类（首个命中）

    # 去重 + 限额
    seen = set()
    result = {}
    for t, items in candidates.items():
        uniq = []
        for it in items:
            key = it["sentence"][:120]
            if key in seen:
                continue
            seen.add(key)
            uniq.append(it)
        result[t] = uniq[: args.max_per_type]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "candidate_sentences.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[gen_seed] 处理 {n_files} 个提取文件 → {out}")
    for t, items in result.items():
        print(f"  {t:12s}: {len(items)} 条候选句子")
    print("\n下一步：从候选中挑选标注种子（30-50 条）")


if __name__ == "__main__":
    main()
