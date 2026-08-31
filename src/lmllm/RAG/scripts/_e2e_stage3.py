"""阶段三端到端验证 — 完整 RAGPipeline.run() 验证插桩

验证点：
  1. 关系引擎初始化成功（RAGPipeline.relation_engine）
  2. 插桩 A：retrieval.constraint_log 出现（约束过滤生效）
  3. 插桩 B：reviewer_output.rule_checks 出现（规则校验生效）
  4. 答案可生成（无回归）

用法：
  DEEPSEEK_API_KEY=<key> python scripts/_e2e_stage3.py
"""

import json
import os
import sys
import time

sys.path.insert(0, "/home/ls/xiaoyue/LLM2/LMLLM")
from src.lmllm.RAG import RAGPipeline

QUESTIONS = [
    ("设计类(C7触发)", "设计一套能量密度不低于400 Wh/kg的液态锂电池方案，给出正极/负极/电解液组合。"),
    ("约束类(C3/C4触发)", "锂金属负极配什么电解液？对比几种方案。"),
]

def main():
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("缺少 DEEPSEEK_API_KEY")
        return
    print("初始化 RAGPipeline ...")
    pipeline = RAGPipeline()
    print(f"  关系引擎: {'可用' if pipeline.relation_engine is not None else '未加载(降级)'}")

    for label, q in QUESTIONS:
        print(f"\n{'='*60}\n[{label}] {q}\n{'='*60}")
        t0 = time.time()
        try:
            result = pipeline.run(q)
        except Exception as e:
            print(f"  !!! 运行异常: {type(e).__name__}: {e}")
            continue
        dt = time.time() - t0
        print(f"  耗时 {dt:.1f}s")

        # 插桩 A 检查
        retrieval = result.get("retrieval", {})
        clog = retrieval.get("constraint_log")
        if clog:
            print(f"  [插桩A] constraint_log: exclude={clog['exclude_terms'][:4]} "
                  f"降权段落={len(clog['downgraded'])} boost段落={len(clog['boosted'])}")
        else:
            print("  [插桩A] 未触发 constraint_log（问题未命中约束或实体未识别）")

        # 插桩 B 检查
        rv = result.get("reviewer_output", {})
        rcheck = rv.get("rule_checks")
        if rcheck:
            print(f"  [插桩B] rule_checks: confidence={rcheck.get('confidence')} "
                  f"energy={rcheck.get('energy_check')} "
                  f"violations={rcheck.get('rule_checks', {}).get('violations')}")
        else:
            print("  [插桩B] 未触发 rule_checks")

        print(f"  最终置信度: {rv.get('confidence')}")
        ans = result.get("final_answer", "")
        print(f"  答案前 250 字:\n{ans[:250]}")

    print("\n=== 端到端验证完成 ===")

if __name__ == "__main__":
    main()
