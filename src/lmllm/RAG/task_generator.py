"""设计任务生成器 — 阶段 2（数据工程的终点）

从数据资产自动生成四类设计任务，每个任务自带机器可校验的 ground truth：
  1. combination_recommendation  组合推荐（正样本）：候选表 × 能量目标 → 可行组合 + 能量估算
  2. constraint_check            约束验证（负样本）：constraints 排除规则 → 不可行组合 + 命中的规则 ID
  3. doping_design               掺杂设计：doping 关系对象 → 掺杂策略对比
  4. parameter_design            参数设计：performance 关系对象 → 材料-条件-性能

关系 schema → 任务模板的映射是自动的：每条关系对象就是一道题的原料。

用法：
  python -m src.lmllm.RAG.task_generator [--min-energy 350] [--output data/tasks/design_tasks.json]
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from typing import Dict, List

from .relation_engine import RelationEngine
from .energy_model import estimate_scheme_energy, DEFAULT_ACTIVE_RATIOS

DATA_DIR = Path(__file__).resolve().parent / "data"
SEED_PATH = DATA_DIR / "seed" / "relations_seed.json"
OUT_DIR = DATA_DIR / "tasks"

# 能量目标档位（生成组合推荐任务的标尺）
ENERGY_TARGETS = [300, 350, 400, 450]


def _load_candidates() -> Dict:
    return json.loads((DATA_DIR / "candidates.json").read_text(encoding="utf-8"))


def _load_seed() -> Dict:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def gen_combination_tasks(engine: RelationEngine, cands: Dict,
                          min_energy: float) -> List[Dict]:
    """组合推荐（正样本）：枚举可行组合，按能量目标生成任务。"""
    tasks = []
    cathode_ids = [m["id"] for m in cands.get("cathode", [])]
    anode_ids = [m["id"] for m in cands.get("anode", [])]
    electrolyte_ids = [m["id"] for m in cands.get("electrolyte", [])]
    for target in ENERGY_TARGETS:
        if target < min_energy:
            continue
        feasible = []
        for cath, an, ele in itertools.product(cathode_ids, anode_ids, electrolyte_ids):
            scheme = {"cathode": cath, "anode": an, "electrolyte": ele,
                      "target_energy": target}
            ev = engine.evaluate(scheme)
            if not ev["feasible"]:
                continue
            est = estimate_scheme_energy(scheme, cands)
            feasible.append({"scheme": scheme, "energy": est,
                             "inclusions": ev["inclusions"]})
        # 达标组合 → 正样本；无达标组合 → 缺口任务
        reached = [f for f in feasible if f["energy"] and f["energy"] >= target]
        if reached:
            best = max(reached, key=lambda f: f["energy"])
            q = f"设计一套能量密度不低于{target} Wh/kg的液态锂电池方案，给出正极/负极/电解液组合。"
            tasks.append({
                "task_type": "combination_recommendation",
                "question": q,
                "expected": {
                    "feasible": True,
                    "best_scheme": best["scheme"],
                    "energy_estimate": round(best["energy"], 1),
                    "n_feasible": len(feasible),
                    "required_inclusions": best["inclusions"],
                },
                "tag": {"relation": "compatibility", "verifiable": True,
                        "source": "candidates x constraints x energy_model"},
            })
        else:
            q = f"设计一套能量密度不低于{target} Wh/kg的液态锂电池方案。"
            tasks.append({
                "task_type": "combination_recommendation",
                "question": q,
                "expected": {"feasible": False,
                             "reason": f"候选表内无约束可行的组合达到 {target} Wh/kg"},
                "tag": {"relation": "compatibility", "verifiable": True,
                        "source": "energy_model 上限"},
            })
    return tasks


def gen_constraint_tasks(engine: RelationEngine, cands: Dict) -> List[Dict]:
    """约束验证（负样本）：每条 exclude 规则构造一个违反它的组合。"""
    tasks = []
    cathode_ids = [m["id"] for m in cands.get("cathode", [])]
    anode_ids = [m["id"] for m in cands.get("anode", [])]
    electrolyte_ids = [m["id"] for m in cands.get("electrolyte", [])]
    for rule in engine.constraints.get("rules", []):
        if rule.get("action") != "exclude":
            continue
        # 构造触发该规则的方案：用默认组合，检查哪条 violate
        made = False
        for cath, an, ele in itertools.product(cathode_ids, anode_ids, electrolyte_ids):
            scheme = {"cathode": cath, "anode": an, "electrolyte": ele}
            trig = rule.get("trigger", {})
            if trig.get("field") == "target_energy":
                scheme["target_energy"] = trig.get("value")
            ev = engine.evaluate(scheme)
            if any(v["rule_id"] == rule["id"] for v in ev["violations"]):
                tasks.append({
                    "task_type": "constraint_check",
                    "question": f"方案：正极 {cath} + 负极 {an} + 电解液 {ele}，不使用任何界面添加剂或表面改性。该组合可行吗？为什么？",
                    "expected": {"feasible": False,
                                 "violated_rules": [rule["id"]],
                                 "reason": rule.get("reason", ""),
                                 "scheme": scheme},
                    "tag": {"relation": "exclusion", "verifiable": True,
                            "source": f"constraints #{rule['id']}"},
                })
                made = True
                break
        if not made:
            print(f"  [warn] 规则 {rule['id']} 未能在候选表内构造出违规组合")
    return tasks


def gen_doping_tasks(seed: Dict) -> List[Dict]:
    """掺杂设计：从 doping 关系对象生成策略对比题。"""
    tasks = []
    for item in seed.get("doping", []):
        for rel in item.get("relations", []):
            val = rel.get("value")
            if not isinstance(val, dict) or not val.get("value"):
                continue
            dopants = ",".join(rel.get("dopants", []))
            q = (f"{rel.get('host', '材料')}经{dopants}掺杂后，"
                 f"{val.get('property', '性能')}达到多少？（条件：{rel.get('condition', {})}）")
            tasks.append({
                "task_type": "doping_design",
                "question": q,
                "expected": {"material": rel.get("result"),
                             "property": val.get("property"),
                             "value": val.get("value"),
                             "unit": val.get("unit", ""),
                             "condition": rel.get("condition", {})},
                "tag": {"relation": "doping", "verifiable": True,
                        "source_text": rel.get("source_text", "")},
            })
    return tasks


def gen_parameter_tasks(seed: Dict) -> List[Dict]:
    """参数设计：从 performance 关系对象生成材料-条件-性能题。"""
    tasks = []
    for item in seed.get("performance", []):
        for rel in item.get("relations", []):
            cond = rel.get("condition", {})
            cond_str = "、".join(f"{k}={v}" for k, v in cond.items()) or "无额外条件"
            q = (f"{rel.get('material')}在{cond_str}下的"
                 f"{rel.get('property')}是多少？")
            tasks.append({
                "task_type": "parameter_design",
                "question": q,
                "expected": {"material": rel.get("material"),
                             "property": rel.get("property"),
                             "value": rel.get("value"),
                             "unit": rel.get("unit", ""),
                             "condition": cond},
                "tag": {"relation": "performance", "verifiable": True,
                        "source_text": rel.get("source_text", "")},
            })
    return tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-energy", type=float, default=350)
    ap.add_argument("--output", default=str(OUT_DIR / "design_tasks.json"))
    args = ap.parse_args()

    engine = RelationEngine(DATA_DIR)
    cands = _load_candidates()
    seed = _load_seed()

    tasks = []
    tasks += gen_combination_tasks(engine, cands, args.min_energy)
    tasks += gen_constraint_tasks(engine, cands)
    tasks += gen_doping_tasks(seed)
    tasks += gen_parameter_tasks(seed)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.output)
    payload = {
        "version": "0.1",
        "updated": "2026-08-18",
        "n_tasks": len(tasks),
        "tasks": tasks,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 统计 ──
    print(f"任务生成器输出 → {out}")
    print(f"总任务数: {len(tasks)}")
    by_type: Dict[str, int] = {}
    for t in tasks:
        by_type[t["task_type"]] = by_type.get(t["task_type"], 0) + 1
    for k, v in sorted(by_type.items()):
        print(f"  {k:28s}: {v}")
    # 正负样本比例（constraint_check 为负样本）
    pos = sum(1 for t in tasks if t["task_type"] != "constraint_check")
    neg = sum(1 for t in tasks if t["task_type"] == "constraint_check")
    print(f"正样本 {pos} / 负样本 {neg}")
    # ground truth 完备性
    missing = sum(1 for t in tasks if not t["expected"])
    print(f"ground truth 缺失: {missing}")
    # 抽查
    print("\n抽查 3 条:")
    for t in tasks[:3]:
        print(f"  [{t['task_type']}] {t['question'][:60]}")
        print(f"      expected: {json.dumps(t['expected'], ensure_ascii=False)[:100]}")


if __name__ == "__main__":
    main()
