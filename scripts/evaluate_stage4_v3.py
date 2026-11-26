"""显式真实模型对照：六类课题各三次、v2/v3隔离输出，结果不自动授予上线。"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CASES = (
    ("fixed", "设计NCM811正极、硅基负极和LHCE电解液电池，正极厚度70 μm、孔隙率0.31；负极厚度110 μm、孔隙率0.56，工作电压2.8–4.2 V。"),
    ("selection", "设计350 Wh/kg级液态锂电池，提出材料组合，说明选材依据、比较对象和未验证边界。"),
    ("doping", "比较Zn–Na与Cu–Na的富锂正极共掺杂路线，提出多个候选名义式和分母口径，说明氧释放风险及证据适用范围。"),
    ("half_cell", "设计富锂正极半电池评价，采用金属锂箔对电极，明确用户条件、锂箔致密几何及验证边界，不推定N/P匹配。"),
    ("unmatched", "设计LiFePO4与硬碳的研究候选电池，文献未给的物性保持缺失；不借用其他PINN体系的参数或性能。"),
    ("controversy", "评价富锂正极低剂量共掺杂是否协同抑制氧释放，区分单元素证据、高浓DFT与共掺杂实证；证据不足须留缺口及验证路径。"),
)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-external", action="store_true", help="显式运行真实LLM/检索服务；默认只列任务")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "stage4_v3_evaluation")
    parser.add_argument("--settings", type=Path, help="v2/v3共用同一配置和证据库")
    args = parser.parse_args(argv)
    if not args.run_external:
        print(json.dumps({"cases": CASES, "repeats": 3, "contracts": [2, 3], "status": "尚未运行真实模型验收"}, ensure_ascii=False, indent=2))
        return 0
    from auto_battery_research.util.config import ABRConfigLoader
    from auto_battery_research.tools.rag_adapter import AbrRagAdapter
    from auto_battery_research.rag import LLMClient
    from auto_battery_research.util.scheme_ledger import atomic_write
    config = ABRConfigLoader(workspace_root=str(ROOT), setting_file=str(args.settings) if args.settings else None).load(persist_snapshot=False)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    original = LLMClient.chat
    results = []
    try:
        for case, goal in CASES:
            for repeat in range(1, 4):
                for version in (2, 3):
                    task = output / case / str(repeat) / ("v" + str(version))
                    if (task / "evaluation.json").exists():
                        results.append(json.loads((task / "evaluation.json").read_text(encoding="utf-8")))
                        continue
                    calls = []
                    def measured(client, system, user, **kwargs):
                        started = time.monotonic()
                        record = {"role": kwargs.get("trace_tag"), "status": "requested"}
                        calls.append(record)
                        atomic_write(task / "evaluation_calls.json", json.dumps(calls, ensure_ascii=False, indent=2))
                        try:
                            text = original(client, system, user, **kwargs)
                            record.update(status="returned", response_meta=copy.deepcopy(client.last_response))
                            return text
                        except Exception as exc:
                            record.update(status="failed", error=str(exc))
                            raise
                        finally:
                            record["seconds"] = time.monotonic() - started
                            atomic_write(task / "evaluation_calls.json", json.dumps(calls, ensure_ascii=False, indent=2))
                    LLMClient.chat = measured
                    selected = copy.deepcopy(config)
                    selected.setdefault("runtime_options", {}).setdefault("stage4_writer", {})["contract_version"] = version
                    started = time.monotonic()
                    try:
                        outcome = AbrRagAdapter(config=selected).run_rag_design(goal, task, task_goal=goal)
                    except Exception as exc:
                        outcome = {"success": False, "error": str(exc)}
                    design_path = task / "design_scheme.json"
                    doc = json.loads(design_path.read_text(encoding="utf-8")) if design_path.exists() else {}
                    checkpoint = task / "stage4_repair_checkpoint.json"
                    state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
                    writer_output = state.get("writer_output") or doc.get("writer_output") or {}
                    row = {"case": case, "repeat": repeat, "version": version, "seconds": time.monotonic()-started,
                        "outcome": outcome, "calls": calls, "first_compile_pass": bool(writer_output.get("draft_answer")) and not any(
                            d.get("severity") == "error" for d in writer_output.get("precheck", [])) and not any(r.get("role") == "protocol_patch" for r in state.get("requests", [])),
                        "review_status": doc.get("review_status"), "research": doc.get("research"), "consistency_review": doc.get("consistency_review"),
                        "manual_review": {"hard_conflicts_intercepted": None, "false_positives": None, "requirement_coverage": None,
                                          "argument_completeness": None, "evidence_applicability": None, "scientific_strength": None}}
                    atomic_write(task / "evaluation.json", json.dumps(row, ensure_ascii=False, indent=2))
                    results.append(row)
                    atomic_write(output / "comparison.json", json.dumps({"status": "待人工核验，不自动启用v3", "results": results}, ensure_ascii=False, indent=2))
    finally:
        LLMClient.chat = original
    print("对照产物已保存：" + str(output / "comparison.json") + "；人工确认前默认仍为v2。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
