"""小批量抽取测试 — 阶段 1 质量确认（冒烟测试后的第二步）

从 candidate_sentences.json（miner 真实文献句）挑句子，三类关系各跑一批，
统计 parsability 率 + 抽查关系对象质量，结果落盘供人工核对。

用法：
  python scripts/batch_extract.py [--limit 6] [--sleep 1.0]

输出：
  data/seed/batch_extract_test.json  （全部结果，含 raw 截断）
  终端统计 + 抽查展示
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent))

from src.lmllm.RAG.extractor import RelationExtractor  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
CANDIDATES = BASE / "data" / "seed" / "candidate_sentences.json"
SEED = BASE / "data" / "seed" / "relations_seed.json"
OUT = BASE / "data" / "seed" / "batch_extract_test.json"

# 每类抽取句数
PER_TYPE_LIMIT = 6

# 候选句中的明显噪声（HTML/LaTeX 残留/方法描述）
NOISE_PATTERNS = ("</details>", "<details>", "line</summary>", "DFT", "Perdew",
                  "PBE functional", "XPS analysis was conducted", "GGA")


def get_api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    # fallback: miner/config.yaml
    try:
        import yaml
        cfg = yaml.safe_load(open(Path(__file__).resolve().parent.parent.parent.parent
                                  / "miner" / "config.yaml", encoding="utf-8"))
        return cfg.get("llm", {}).get("api_key", "")
    except Exception:
        return ""


def pick_sentences(limit: int) -> list:
    """三类句子：performance/doping 从候选句挑（滤噪声），compatibility 从种子教科书句挑。"""
    cands = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    picked = []
    # performance / doping：真实文献句（滤噪声）
    for rtype in ("performance", "doping"):
        n = 0
        for it in cands.get(rtype, []):
            if n >= limit:
                break
            s = it["sentence"]
            if any(p in s for p in NOISE_PATTERNS):
                continue
            picked.append({"type": rtype, "text": s, "doi": it.get("doi", "")})
            n += 1
    # compatibility：种子里的中文教科书句（miner 提取无兼容性内容）
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    n = 0
    for item in seed.get("compatibility", []):
        if n >= limit:
            break
        picked.append({"type": "compatibility", "text": item["text"],
                       "doi": "textbook-consensus"})
        n += 1
    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=PER_TYPE_LIMIT)
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()

    key = get_api_key()
    if not key:
        print("缺少 DEEPSEEK_API_KEY（环境变量或 miner/config.yaml）")
        sys.exit(1)
    ex = RelationExtractor(api_key=key)

    sentences = pick_sentences(args.limit)
    print(f"共 {len(sentences)} 句："
          f"performance {sum(1 for s in sentences if s['type']=='performance')}，"
          f"doping {sum(1 for s in sentences if s['type']=='doping')}，"
          f"compatibility {sum(1 for s in sentences if s['type']=='compatibility')}")

    results = []
    for i, item in enumerate(sentences):
        try:
            res = ex.extract(item["text"], item["type"])
        except Exception as e:
            res = {"relations": [], "parsable": False,
                   "raw": f"EXC: {e}", "errors": [str(e)]}
        results.append({
            "index": i,
            "type": item["type"],
            "text": item["text"],
            "doi": item["doi"],
            "parsable": res["parsable"],
            "n_relations": len(res["relations"]),
            "relations": res["relations"],
            "errors": res["errors"][:5],
            "raw_truncated": res.get("raw", "")[:400],
        })
        status = "OK" if res["parsable"] else "EMPTY/FAIL"
        print(f"[{i:2d}] {item['type']:13s} {status:10s} "
              f"rels={len(res['relations'])}  {item['text'][:60]}")
        time.sleep(args.sleep)

    BASE.joinpath("data", "seed").mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 统计 ──
    print("\n=== 统计 ===")
    total, ok = len(results), sum(1 for r in results if r["parsable"])
    print(f"总 parsability: {ok}/{total} = {ok/total*100:.0f}%")
    for rtype in ("performance", "doping", "compatibility"):
        sub = [r for r in results if r["type"] == rtype]
        if sub:
            sub_ok = sum(1 for r in sub if r["parsable"])
            print(f"  {rtype:13s}: {sub_ok}/{len(sub)} parsable，"
                  f"平均关系数 {sum(r['n_relations'] for r in sub)/len(sub):.1f}")
    # 空结果分布（区分"合法空"与"解析失败"）
    empty = [r for r in results if r["n_relations"] == 0]
    legit_empty = [r for r in empty if not r["errors"]]
    if legit_empty:
        print(f"\n注意: {len(legit_empty)} 句返回空数组且无解析错误")
        for r in legit_empty[:5]:
            print(f"  - [{r['type']}] {r['text'][:70]}")
    print(f"\n完整结果: {OUT}")
