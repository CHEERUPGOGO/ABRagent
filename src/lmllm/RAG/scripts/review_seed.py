"""种子标注审核工具 — 阶段 1 人工审核辅助

把 relations_seed.json 转成可读对照表 + 自动一致性检查（宽容匹配）：
  1. source_text 溯源：归一化（去空白/标点）后是否为句子子串
  2. 数值一致性：标注的 value 是否出现在句子文本中
  3. 条件一致性：condition 的数值锚点是否在句子中被提及（容忍 ">4.3V" ↔ "超过4.3V"）

用法：
  python scripts/review_seed.py            # 终端摘要
  python scripts/review_seed.py --md       # 输出 Markdown 审核报告
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed" / "relations_seed.json"

RELATION_LABELS = {
    "doping": "掺杂",
    "compatibility": "兼容性",
    "performance": "性能",
}


def norm(s: str) -> str:
    """归一化：小写 + 去空白 + 去标点（用于宽容子串匹配）。"""
    return re.sub(r"[\s,，。.;；:：'\"'\"()（）\[\]{}]+", "", s).lower()


def num_in_text(value, text: str) -> bool:
    """数值是否出现在句子中。"""
    if value is None:
        return True
    s = str(value)
    if s in text:
        return True
    return bool(re.search(re.escape(s) + r"\b", text))


def condition_in_text(cond: dict, text: str) -> bool:
    """条件一致性：以数值为锚点（容忍 '>4.3V' ↔ '超过4.3V'）。"""
    if not cond:
        return True
    for k, v in cond.items():
        if v is None:
            continue
        vs = str(v)
        if vs in text:
            continue
        # 数值锚点：condition 中的数字必须出现在句子中
        nums = re.findall(r"\d+(?:\.\d+)?", vs)
        if not nums:
            continue  # 无语义锚点的条件值（如 SEI工程）不做机械检查
        if not all(n in text for n in nums):
            return False
    return True


def check_relation(rel: dict, text: str) -> list:
    flags = []
    st = rel.get("source_text", "")
    if st and norm(st) not in norm(text):
        flags.append("source_text 与句子不一致（归一化后仍不匹配）")
    val = rel.get("value")
    if isinstance(val, dict):
        if not num_in_text(val.get("value"), text):
            flags.append(f"value={val.get('value')} 不在句子中")
    elif val is not None and not num_in_text(val, text):
        flags.append(f"value={val} 不在句子中")
    cond = rel.get("condition")
    if not condition_in_text(cond or {}, text):
        flags.append(f"condition={cond} 未在句子中提及")
    if rel.get("type") == "compatibility" and rel.get("relation") not in ("compatible", "incompatible", "improved_by", "conditionally_compatible"):
        flags.append(f"relation 值非法: {rel.get('relation')}")
    return flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true", help="输出 Markdown 报告")
    args = ap.parse_args()

    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    total_rels, flagged = 0, 0
    lines = []

    for rtype, items in seed.items():
        if not isinstance(items, list):
            continue
        lines.append(f"\n## {rtype}（{RELATION_LABELS.get(rtype, rtype)}，{len(items)} 条）\n")
        for i, item in enumerate(items):
            text = item.get("text", "")
            rels = item.get("relations", [])
            total_rels += len(rels)
            flags = []
            for rel in rels:
                flags.extend(check_relation(rel, text))
            if rtype == "doping":
                summary = "; ".join(
                    f"host={r.get('host')} dopants={r.get('dopants')} "
                    f"value={r.get('value', {}).get('value', '-') if isinstance(r.get('value'), dict) else '-'}"
                    for r in rels)
            elif rtype == "compatibility":
                summary = "; ".join(
                    f"{r.get('subject', {}).get('material')} {r.get('relation')} "
                    f"{r.get('object', {}).get('material')}"
                    for r in rels)
            else:
                summary = "; ".join(
                    f"{r.get('material')} {r.get('property')}={r.get('value')}{r.get('unit', '')}"
                    for r in rels)
            tag = "⚠️" if flags else "✅"
            if flags:
                flagged += 1
            short = text[:110] + ("..." if len(text) > 110 else "")
            lines.append(f"{tag} {i+1}. {short}")
            lines.append(f"   → {summary}")
            for f in flags:
                lines.append(f"   ⚠ {f}")

    report = "\n".join(lines)
    if args.md:
        out = SEED_PATH.parent / "review_report.md"
        out.write_text(f"# 种子审核报告\n\n共 {total_rels} 个关系对象，{flagged} 条有疑点。\n" + report,
                       encoding="utf-8")
        print(f"报告已保存: {out}")
    else:
        print(f"共 {total_rels} 个关系对象，{flagged} 条有疑点\n")
        print(report)


if __name__ == "__main__":
    main()
