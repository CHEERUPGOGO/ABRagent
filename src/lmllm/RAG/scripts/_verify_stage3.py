import sys
sys.path.insert(0, "/home/ls/xiaoyue/LLM2/LMLLM")
from src.lmllm.RAG.relation_engine import RelationEngine
from src.lmllm.RAG.agents import RetrievalAgent, ReviewerAgent
from src.lmllm.RAG.rag_pipeline import RAGPipeline

eng = RelationEngine()

# 1. RetrievalAgent 注入
ra = RetrievalAgent(kb=None, vector_store=None, relation_engine=eng)
print("[1] RetrievalAgent 注入 relation_engine OK:", ra.relation_engine is not None)

# 2. ReviewerAgent mock LLM + 错误方案（锂金属+碳酸酯 → C3 违规）
class FakeLLM:
    available = True
    def chat(self, system, user, temperature=0.1):
        # 模拟 LLM 误判为 high（规则必须否决）
        return '{"issues": [], "revised_answer": "该组合可行，能量密度约420 Wh/kg", "confidence": "high"}'

rv = ReviewerAgent(FakeLLM(), relation_engine=eng)
res = rv.run(
    question="锂金属负极配什么电解液？",
    evidence=[{"passage_id": "p1", "source": "x", "text": "锂金属与碳酸酯不兼容"}],
    draft_answer="推荐碳酸酯电解液，能量密度约420 Wh/kg",
    scheme={"cathode": "NCM811", "anode": "li_metal", "electrolyte": "carbonate_ec"},
    claimed_energy=420,
)
rule_conf = res.get("rule_checks", {}).get("confidence")
print(f"[2] 错误方案: rule_checks.confidence={rule_conf}, 最终 confidence={res['confidence']}")
assert res["confidence"] == "low", f"插桩 B 置信度否决失败: {res['confidence']}"
print("    插桩 B 置信度否决 OK（LLM 说 high，规则强制 low）")

# 3. 正确方案（锂金属+LHCE → 无违规）不应被误杀
res2 = rv.run(
    question="锂金属负极配什么电解液？",
    evidence=[{"passage_id": "p1", "source": "x", "text": "LHCE 适合锂金属负极"}],
    draft_answer="推荐 LHCE 电解液",
    scheme={"cathode": "NCM811", "anode": "li_metal", "electrolyte": "lhce"},
)
print(f"[3] 正确方案: violations={res2.get('rule_checks', {}).get('rule_checks', {}).get('violations')}, "
      f"最终 confidence={res2['confidence']}")
assert res2["confidence"] == "high", f"正确方案被误杀: {res2['confidence']}"
print("    正确方案放行 OK")

# 4. 不传 relation_engine（旧用法）向后兼容
rv_plain = ReviewerAgent(FakeLLM())
res3 = rv_plain.run(
    question="测试", evidence=[{"passage_id": "p1", "source": "x", "text": "x"}],
    draft_answer="答案",
)
print(f"[4] 无关系引擎(旧用法): confidence={res3['confidence']}, rule_checks={res3.get('rule_checks')}")
assert "rule_checks" not in res3
print("    向后兼容 OK")

# 5. rag_pipeline 辅助方法
print(f"[5] _extract_energy_claim('能量密度约420 Wh/kg') = "
      f"{RAGPipeline._extract_energy_claim('能量密度约420 Wh/kg')}")
print(f"    _extract_energy_claim('无数值答案') = {RAGPipeline._extract_energy_claim('无数值答案')}")

print("\n=== 阶段三插桩验证全部通过 ===")
