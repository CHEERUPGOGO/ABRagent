import sys
sys.path.insert(0, "/home/ls/xiaoyue/LLM2/LMLLM")
from src.lmllm.RAG.relation_engine import RelationEngine
from src.lmllm.RAG.agents import WriterAgent

eng = RelationEngine()

# mock LLM：捕获 user_prompt，返回占位答案
class CaptureLLM:
    available = True
    last_prompt = ""
    def chat(self, system, user, temperature=0.2):
        self.last_prompt = user
        return "（占位答案）"

# 1. 设计类问题 + 违规方案（锂金属+碳酸酯）→ 应注入五段式指示 + 约束评估
llm1 = CaptureLLM()
w1 = WriterAgent(llm1, relation_engine=eng)
w1.run(
    question="设计一套能量密度不低于400 Wh/kg的液态锂电池方案。",
    plan={"answer_outline": ["方案", "数据缺口"]},
    evidence=[{"passage_id": "p1", "source": "x", "text": "文献内容"}],
    scheme={"cathode": "NCM811", "anode": "li_metal", "electrolyte": "carbonate_ec"},
)
p1 = llm1.last_prompt
print("[1] 设计类+违规方案")
print("    五段式指示:", "设计任务输出结构" in p1 or "五段式" in p1)
print("    约束评估注入:", "约束引擎评估" in p1)
print("    violations 内容:", "violations" in p1)
assert "五段式" in p1, "缺少五段式指示"
assert "约束引擎评估" in p1, "缺少约束评估"
assert "C3" in p1 or "C1" in p1 or "C6" in p1, "violations 应含规则 ID"

# 2. 普通问题 + 无 scheme → 无额外注入（向后兼容）
llm2 = CaptureLLM()
w2 = WriterAgent(llm2, relation_engine=eng)
w2.run(
    question="NCM811的首次放电容量是多少？",
    plan={"answer_outline": ["数据"]},
    evidence=[{"passage_id": "p1", "source": "x", "text": "内容"}],
)
p2 = llm2.last_prompt
print("\n[2] 普通查询")
print("    无五段式指示:", "五段式" not in p2)
print("    无约束注入:", "约束引擎评估" not in p2)
assert "五段式" not in p2 and "约束引擎评估" not in p2

# 3. 设计类问题但无 scheme（实体未识别）→ 有五段式但无约束评估
llm3 = CaptureLLM()
w3 = WriterAgent(llm3, relation_engine=eng)
w3.run(
    question="推荐一套高比能方案。",
    plan={"answer_outline": ["方案"]},
    evidence=[{"passage_id": "p1", "source": "x", "text": "内容"}],
)
p3 = llm3.last_prompt
print("\n[3] 设计类但无 scheme")
print("    五段式指示:", "五段式" in p3)
print("    无约束评估:", "约束引擎评估" not in p3)
assert "五段式" in p3 and "约束引擎评估" not in p3

print("\n=== Writer 方案输出改造验证全部通过 ===")
