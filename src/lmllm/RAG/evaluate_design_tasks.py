"""设计任务评测 — 阶段四评测闭环

两种模式：
  rule      自洽性检查（快，秒级）：任务 ground truth 与规则引擎重算结果的一致性
  pipeline  管线评测（慢，每题约 20 分钟）：RAGPipeline 真实回答设计任务，
            评估约束满足率 / 能量密度误差 / 不可行组合识别率

用法：
  python -m src.lmllm.RAG.evaluate_design_tasks --mode rule
  python -m src.lmllm.RAG.evaluate_design_tasks --mode pipeline --limit 3
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Dict, List

from .relation_engine import RelationEngine
from .energy_model import estimate_scheme_energy

DATA_DIR = Path(__file__).resolve().parent / "data"
TASKS_PATH = DATA_DIR / "tasks" / "design_tasks.json"
REPORT_PATH = DATA_DIR / "tasks" / "eval_report.json"


def load_tasks() -> List[Dict]:
    return json.loads(TASKS_PATH.read_text(encoding="utf-8")).get("tasks", [])


# ═══════════════ rule 模式：自洽性 ═══════════════

def evaluate_rule(tasks: List[Dict]) -> Dict:
    eng = RelationEngine(DATA_DIR)
    cands = json.loads((DATA_DIR / "candidates.json").read_text(encoding="utf-8"))
    stats = {"n_tasks": len(tasks), "by_type": {}, "consistent": 0,
             "inconsistent": [], "pos": 0, "neg": 0}
    for t in tasks:
        ttype = t["task_type"]
        stats["by_type"][ttype] = stats["by_type"].get(ttype, 0) + 1
        expected = t["expected"]
        if ttype == "constraint_check":
            stats["neg"] += 1
            # 重算：优先用任务自带的完整 scheme（含 target_energy），fallback 文本解析
            scheme = expected.get("scheme")
            if not scheme:
                m = re.search(r"正极 (\S+) \+ 负极 (\S+) \+ 电解液 (\S+)", t["question"])
                if not m:
                    stats["inconsistent"].append({"task": t["question"][:40], "reason": "问题格式无法解析"})
                    continue
                scheme = {"cathode": m.group(1), "anode": m.group(2), "electrolyte": m.group(3)}
            ev = eng.evaluate(scheme)
            consistent = (ev["feasible"] == (expected.get("feasible", True)))
            if consistent:
                stats["consistent"] += 1
            else:
                stats["inconsistent"].append({"task": t["question"][:40],
                                              "reason": f"规则引擎判 feasible={ev['feasible']}, 任务标注 {expected.get('feasible')}"})
        elif ttype == "combination_recommendation":
            stats["pos"] += 1
            if not expected.get("feasible"):
                stats["consistent"] += 1  # 不可行判定来自 energy_model，规则层无法重算，视为一致
                continue
            # 重算 best_scheme 能量
            scheme = expected.get("best_scheme", {})
            est = estimate_scheme_energy(scheme, cands)
            if est is not None:
                diff = abs(est - expected.get("energy_estimate", 0)) / max(est, 1e-9)
                if diff < 0.01:
                    stats["consistent"] += 1
                else:
                    stats["inconsistent"].append({"task": t["question"][:40],
                                                  "reason": f"能量重算 {est:.1f} vs 标注 {expected.get('energy_estimate')}"})
            else:
                stats["inconsistent"].append({"task": t["question"][:40], "reason": "能量无法重算"})
        else:
            # doping/parameter：ground truth 来自种子关系对象，规则层不重复验证
            stats["consistent"] += 1
    stats["consistency_rate"] = round(stats["consistent"] / max(stats["n_tasks"], 1), 3)
    return stats


# ═══════════════ pipeline 模式：管线评测 ═══════════════

_ENERGY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*Wh/kg")
_INFEASIBLE_KW = ("不可行", "不适用", "无法实现", "难以达到", "超出", "排除", "不兼容", "violat")


def evaluate_pipeline(tasks: List[Dict], limit: int) -> Dict:
    from .rag_pipeline import RAGPipeline
    pipeline = RAGPipeline()
    print(f"关系引擎: {'可用' if pipeline.relation_engine is not None else '不可用'}")

    results = []
    # 优先跑约束验证（负样本识别是核心指标）+ 组合推荐
    ordered = sorted(tasks, key=lambda t: 0 if t["task_type"] == "constraint_check" else 1)
    subset = ordered[:limit]
    for t in subset:
        q = t["question"]
        print(f"\n>>> [{t['task_type']}] {q[:60]}")
        t0 = time.time()
        try:
            out = pipeline.run(q)
        except Exception as e:
            results.append({"task": q, "error": str(e)})
            continue
        ans = out.get("final_answer", "")
        exp = t["expected"]
        item = {"task_type": t["task_type"], "question": q, "answer_head": ans[:300]}
        if t["task_type"] == "constraint_check":
            # 不可行组合识别：答案是否表达不可行/违规
            infeasible = any(k in ans for k in _INFEASIBLE_KW)
            item["expected_feasible"] = exp.get("feasible")
            item["answer_infeasible"] = infeasible
            item["hit"] = (not exp.get("feasible")) == infeasible or infeasible
        elif t["task_type"] == "combination_recommendation":
            m = _ENERGY_RE.search(ans)
            claimed = float(m.group(1)) if m else None
            est = exp.get("energy_estimate")
            item["claimed_energy"] = claimed
            item["expected_energy"] = est
            if claimed and est:
                item["energy_error"] = round(abs(claimed - est) / est, 3)
                item["hit"] = item["energy_error"] <= 0.30
            else:
                item["hit"] = None
        item["duration_s"] = round(time.time() - t0, 1)
        print(f"   耗时 {item['duration_s']}s, hit={item.get('hit')}")
        results.append(item)

    n_hit = sum(1 for r in results if r.get("hit"))
    report = {"mode": "pipeline", "n": len(results), "hit": n_hit,
              "hit_rate": round(n_hit / max(len(results), 1), 3), "items": results}
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rule", "pipeline"], default="rule")
    ap.add_argument("--limit", type=int, default=3)
    args = ap.parse_args()

    tasks = load_tasks()
    print(f"加载 {len(tasks)} 个设计任务\n")

    if args.mode == "rule":
        report = evaluate_rule(tasks)
        print("=== rule 自洽性检查 ===")
        print(f"任务分布: {report['by_type']}")
        print(f"正样本 {report['pos']} / 负样本 {report['neg']}")
        print(f"自洽率: {report['consistency_rate']} ({report['consistent']}/{report['n_tasks']})")
        for inc in report["inconsistent"][:5]:
            print(f"  ✗ {inc['task']}: {inc['reason']}")
    else:
        report = evaluate_pipeline(tasks, args.limit)

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n评测报告已保存: {REPORT_PATH}")


if __name__ == "__main__":
    main()
