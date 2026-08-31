"""agent/entity_register.py — LLM 辅助注册判定（新实体注册闭环的判定环节）

消费 pipeline 输出的未命中队列（_unmatched_all.json 材料 + _unmatched_labels_all.json 属性），
先用当前词表/标签自动过滤（词表扩充后已能命中的自动解决），
再用 DeepSeek 对真正未命中的候选给出判定：

- merge   : 归并到已有实体/标准标签（候选是它的别名/变体写法）→ 给出 target_id
- create  : 真实新材料/新标签 → 给出建议 id
- discard : 噪音（集流体/隔膜/包装/无法判断）→ 丢弃

输出建议 JSON（供人工确认后更新 candidates.json / alias_map.json / format1 标签）。
本脚本只给建议，不自动改词表——归并/新建必须人工确认。

用法：
  python agent/entity_register.py \
      --materials results/tok2000normtest/tok2000/_unmatched_all.json \
      --labels results/tok2000normtest/tok2000/_unmatched_labels_all.json \
      --out results/tok2000normtest/tok2000

依赖：DEEPSEEK_API_KEY（环境变量或 miner/config.yaml）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.material_norm import MaterialNormalizer
from agent.label_norm import LabelNormalizer


def _api_config() -> Dict:
    """API key/base_url：环境变量优先，回退 miner/config.yaml（与 pipeline._llm 一致）。"""
    key = os.getenv("DEEPSEEK_API_KEY", "")
    base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
    if not key:
        try:
            from miner.config import load_config
            llm = load_config().get("llm", {})
            key = llm.get("api_key", "")
            base = os.getenv("DEEPSEEK_API_BASE", llm.get("base_url", base))
        except Exception:
            pass
    return {"key": key, "base": base.rstrip("/")}


def _call_llm(system: str, user: str, temperature: float = 0.1, max_tokens: int = 3000) -> str:
    cfg = _api_config()
    if not cfg["key"]:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY（环境变量或 miner/config.yaml）")
    payload = json.dumps({
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg['base']}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['key']}"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp["choices"][0]["message"]["content"]


def _parse_json_array(raw: str) -> List[Dict]:
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if m:
        text = m.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []


# ────────────────────────── 材料判定 ──────────────────────────

def build_material_context(norm: MaterialNormalizer) -> str:
    lines = []
    for cid in sorted(norm.known_ids()):
        al = norm.alias_map.get(cid, [])
        lines.append(f"  {cid}: {', '.join(al[:6])}")
    return "\n".join(lines)


MATERIAL_SYSTEM = (
    "你是锂电池材料领域专家，负责判定文献抽取中的\"未注册材料候选\"。"
    "候选不在现有词表中。对每个候选判断动作：\n"
    "- merge: 它是某个已有实体的别名/变体写法（中英/缩写/化学式变体/同一配方体系）→ 必须给 target_id\n"
    "- create: 它是真实的新材料/新配方体系 → target_id 给建议的规范 id（可空）\n"
    "- discard: 它不是电池材料（集流体/隔膜/包装/纸/器具等）或无法判断 → target_id 空\n"
    "只输出 JSON 数组，不要输出其他内容。"
)

MATERIAL_USER = """已有实体（id: 别名摘录）：
{context}

候选（index: name | formula）：
{items}

输出 JSON 数组：[{{"index": 0, "action": "merge|create|discard", "target_id": "", "reason": "简短理由", "confidence": "high|medium|low"}}]"""


# ────────────────────────── 属性判定 ──────────────────────────

def build_label_context(ln: LabelNormalizer) -> str:
    lines = []
    for comp in ("cathode", "anode", "electrolyte"):
        idx = ln._index.get(comp, {})
        if idx:
            lines.append(f"  [{comp}] " + ", ".join(sorted(set(idx.values()))))
    return "\n".join(lines)


LABEL_SYSTEM = (
    "你是锂电池领域属性命名专家，负责判定文献抽取中的\"未注册属性名\"。"
    "候选不在标准标签集中。对每个候选判断动作：\n"
    "- merge: 它是某个标准标签的别名/同义写法 → 必须给 target_id（标准标签名）\n"
    "- create: 它是真实存在但标准标签集缺失的新属性 → target_id 给建议的 Snake_Case 标签名\n"
    "- discard: 它不是电池属性/无法判断 → target_id 空\n"
    "注意：merge 必须严格——只有含义明确等同才归并；模糊的宁可 create 或 discard。\n"
    "只输出 JSON 数组，不要输出其他内容。"
)

LABEL_USER = """标准标签（按组件）：
{context}

候选（index: property_name | 出现次数）：
{items}

输出 JSON 数组：[{{"index": 0, "action": "merge|create|discard", "target_id": "", "reason": "简短理由", "confidence": "high|medium|low"}}]"""


# ────────────────────────── 主流程 ──────────────────────────

def judge(items: List[Dict], system: str, user_tpl: str, context: str, max_items: int = 40) -> List[Dict]:
    """分批判定。返回 [{"index":..., "action":..., "target_id":..., "reason":..., "confidence":...}]。"""
    results: List[Dict] = []
    for i in range(0, len(items), max_items):
        batch = items[i:i + max_items]
        lines = [f"[{j}] {it['display']}" for j, it in enumerate(batch)]
        prompt = user_tpl.format(context=context, items="\n".join(lines))
        try:
            raw = _call_llm(system, prompt)
            verdicts = _parse_json_array(raw)
            for v in verdicts:
                if isinstance(v, dict) and isinstance(v.get("index"), int):
                    idx = v["index"]
                    if 0 <= idx < len(batch):
                        v["candidate"] = batch[idx]
                        results.append(v)
        except Exception as e:
            print(f"  批次 {i // max_items} 判定失败: {e}")
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM 辅助注册判定")
    ap.add_argument("--materials", default=None, help="材料未命中队列（_unmatched_all.json）")
    ap.add_argument("--labels", default=None, help="属性未命中队列（_unmatched_labels_all.json）")
    ap.add_argument("--out", default="results/tok2000normtest/tok2000", help="输出目录")
    ap.add_argument("--batch", type=int, default=40, help="每批判定数")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    suggestions = {"materials": [], "labels": [], "resolved_materials": [], "resolved_labels": []}

    # ── 材料 ──
    if args.materials and Path(args.materials).exists():
        norm = MaterialNormalizer()
        queue = json.loads(Path(args.materials).read_text(encoding="utf-8"))
        pending, resolved = [], []
        for it in queue:
            name = it.get("name", "")
            formula = it.get("formula", "")
            r = norm.normalize(name, formula)
            if r.canonical_id:
                resolved.append({"name": name, "resolved_to": r.canonical_id, "method": r.method})
            else:
                f_disp = formula if isinstance(formula, str) else json.dumps(formula, ensure_ascii=False)
                pending.append({"display": f"{name} | {f_disp[:70]}", "raw": it})
        suggestions["resolved_materials"] = resolved
        print(f"材料候选: {len(queue)} -> 已自动解决 {len(resolved)} / 待判定 {len(pending)}")
        if pending:
            context = build_material_context(norm)
            for v in judge(pending, MATERIAL_SYSTEM, MATERIAL_USER, context, args.batch):
                v["candidate_raw"] = v.pop("candidate", {}).get("raw")
                suggestions["materials"].append(v)
    else:
        print("跳过材料判定（未提供 --materials）")

    # ── 属性 ──
    if args.labels and Path(args.labels).exists():
        ln = LabelNormalizer()
        queue = json.loads(Path(args.labels).read_text(encoding="utf-8"))
        pending, resolved = [], []
        for it in queue:
            pn = it.get("property_name", "")
            r = None
            for comp in ("cathode", "anode", "electrolyte"):
                cand = ln.check(comp, pn)
                if cand.standard_label:
                    r = cand
                    break
            if r:
                resolved.append({"property_name": pn, "resolved_to": r.standard_label})
            else:
                pending.append({"display": f"{pn} | 出现 {it.get('count', 1)} 次", "raw": it})
        suggestions["resolved_labels"] = resolved
        print(f"属性候选: {len(queue)} -> 已自动解决 {len(resolved)} / 待判定 {len(pending)}")
        if pending:
            context = build_label_context(ln)
            for v in judge(pending, LABEL_SYSTEM, LABEL_USER, context, args.batch):
                v["candidate_raw"] = v.pop("candidate", {}).get("raw")
                suggestions["labels"].append(v)
    else:
        print("跳过属性判定（未提供 --labels）")

    out_path = out_dir / "_register_suggestions.json"
    out_path.write_text(json.dumps(suggestions, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n建议已写入: {out_path}")
    print(f"  材料建议 {len(suggestions['materials'])} 条, 属性建议 {len(suggestions['labels'])} 条")


if __name__ == "__main__":
    main()
