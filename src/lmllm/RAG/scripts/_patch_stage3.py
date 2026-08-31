"""阶段三插桩 patch — 把关系引擎接进 RAG 管线

改动（全部向后兼容，relation_engine 默认 None 时行为不变）：
  1. agents.py：
     - RetrievalAgent：注入 relation_engine；run() 末尾加约束过滤（插桩 A）
     - ReviewerAgent：注入 relation_engine；run() 加规则校验（插桩 B）
  2. rag_pipeline.py：
     - __init__ 创建 relation_engine 并注入两个 agent
     - run() 提取 scheme/claimed_energy 传给 reviewer
"""

from pathlib import Path

base = Path("/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG")

# ════════════════════ 1. agents.py ════════════════════
p = base / "agents.py"
s = p.read_text(encoding="utf-8")

# 1a. import
old = """from .prompts import (
    PLANNER_SYSTEM_PROMPT,
    WRITER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
)"""
new = old + """

try:
    from .relation_engine import RelationEngine
except Exception:
    RelationEngine = None  # 关系引擎不可用时降级为纯 RAG"""
assert old in s, "agents import anchor"
s = s.replace(old, new)

# 1b. RetrievalAgent.__init__
old = """    def __init__(self, kb, vector_store=None):
        self.kb = kb
        self.vector_store = vector_store"""
new = """    def __init__(self, kb, vector_store=None, relation_engine=None):
        self.kb = kb
        self.vector_store = vector_store
        self.relation_engine = relation_engine"""
assert old in s, "retrieval init anchor"
s = s.replace(old, new)

# 1c. RetrievalAgent.run 末尾加约束过滤（results_list 排序后、return 前）
old = """        results_list = sorted(merged.values(), key=lambda x: x["score"], reverse=True)

        return {
            "queries": queries,
            "results": results_list,
            "search_logs": search_logs,
        }"""
new = """        results_list = sorted(merged.values(), key=lambda x: x["score"], reverse=True)

        # ── 插桩 A: 约束过滤（关系引擎可用时）──
        constraint_log = None
        if self.relation_engine is not None:
            try:
                entities = self.relation_engine.extract_entities(question)
                scheme = {}
                for cat in ("cathode", "anode", "electrolyte"):
                    ids = entities.get(cat) or []
                    if ids:
                        scheme[cat] = ids[0]
                if scheme:
                    mods = self.relation_engine.query_modifiers(scheme)
                    exclude_terms, boost_terms = mods["exclude_terms"], mods["boost_terms"]
                    filtered, boosted = [], []
                    for r in results_list:
                        text = (r.get("text", "") + " " + r.get("source", "")).lower()
                        if any(t.lower() in text for t in exclude_terms):
                            r["score"] -= 0.5  # 违反约束的段落降权（保留可追溯）
                            filtered.append(r.get("passage_id"))
                        if any(t.lower() in text for t in boost_terms):
                            r["score"] += 0.15
                            boosted.append(r.get("passage_id"))
                    results_list.sort(key=lambda x: x["score"], reverse=True)
                    constraint_log = {
                        "exclude_terms": exclude_terms[:8],
                        "boost_terms": boost_terms[:8],
                        "downgraded": filtered[:10],
                        "boosted": boosted[:10],
                    }
            except Exception as e:
                print(f"[RetrievalAgent] 约束过滤失败(降级): {type(e).__name__}: {e}")

        result = {
            "queries": queries,
            "results": results_list,
            "search_logs": search_logs,
        }
        if constraint_log:
            result["constraint_log"] = constraint_log
        return result"""
assert old in s, "retrieval run anchor"
s = s.replace(old, new)

# 1d. ReviewerAgent.__init__
old = """    def __init__(self, llm_client):
        self.llm = llm_client"""
new = """    def __init__(self, llm_client, relation_engine=None):
        self.llm = llm_client
        self.relation_engine = relation_engine"""
assert old in s, "reviewer init anchor"
s = s.replace(old, new)

# 1e. ReviewerAgent.run 签名 + 规则校验
old = """    def run(
        self,
        question: str,
        evidence: List[Dict[str, Any]],
        draft_answer: str,
        history_context: str = "",
    ) -> Dict[str, Any]:
        \"\"\"审核草稿答案,修正幻觉,生成最终答案.\"\"\""""
new = """    def run(
        self,
        question: str,
        evidence: List[Dict[str, Any]],
        draft_answer: str,
        history_context: str = "",
        scheme: Optional[Dict[str, Any]] = None,
        claimed_energy: Optional[float] = None,
    ) -> Dict[str, Any]:
        \"\"\"审核草稿答案,修正幻觉,生成最终答案.

        插桩 B: 若注入 relation_engine 且提供 scheme,先执行硬规则校验,
        规则结论注入 LLM 审核 prompt,并具有 confidence 否决权.
        \"\"\"

        # ── 插桩 B: 硬规则校验（LLM 审核之前,纯规则,可解释）──
        rule_result = None
        if self.relation_engine is not None and scheme:
            try:
                rule_result = self.relation_engine.check_scheme(
                    scheme,
                    claimed_energy=claimed_energy,
                    answer_text=draft_answer,
                )
            except Exception as e:
                print(f"[ReviewerAgent] 规则校验失败(降级): {type(e).__name__}: {e}")"""
assert old in s, "reviewer run signature anchor"
s = s.replace(old, new)

# 1f. ReviewerAgent.run: user_prompt 注入规则结论
old = """        user_prompt += (
            f"用户问题:{question}\\n\\n"
            f"证据:\\n{evidence_text}\\n\\n"
            f"草稿答案:\\n{draft_answer}\\n\\n"
            f"请严格按 JSON 格式输出.\\n"
            f"审核关注:材料筛选数值的准确性/条件完整性/比较公平性.\\n"
            f"如果草稿中存在无证据支持内容,你必须删除这些内容,并重新生成 revised_answer.\\n"
            f"revised_answer 必须是最终可直接展示给用户的答案."
        )"""
new = """        user_prompt += (
            f"用户问题:{question}\\n\\n"
            f"证据:\\n{evidence_text}\\n\\n"
            f"草稿答案:\\n{draft_answer}\\n\\n"
            f"请严格按 JSON 格式输出.\\n"
            f"审核关注:材料筛选数值的准确性/条件完整性/比较公平性.\\n"
            f"如果草稿中存在无证据支持内容,你必须删除这些内容,并重新生成 revised_answer.\\n"
            f"revised_answer 必须是最终可直接展示给用户的答案."
        )
        if rule_result is not None:
            rule_note = (
                "\\n\\n【硬规则检查结果（来自约束表+能量模型，不可推翻，仅可引用）】\\n"
                f"violations: {rule_result['rule_checks'].get('violations', [])}\\n"
                f"rejects: {rule_result['rule_checks'].get('rejects', [])}\\n"
                f"energy_check: {rule_result.get('energy_check')}\\n"
                f"condition_missing: {rule_result.get('condition_missing')}\\n"
                "若 violations/rejects 非空或 energy_check=energy_mismatch，"
                "revised_answer 必须指出该方案不可行，confidence 必须为 low。"
            )
            user_prompt += rule_note"""
assert old in s, "reviewer prompt anchor"
s = s.replace(old, new)

# 1g. ReviewerAgent.run: 返回附 rule_checks + confidence 否决
old = """        data["fallback"] = False
        return data"""
new = """        # ── 插桩 B 收尾: rule_checks 附到输出, confidence 规则否决 ──
        if rule_result is not None:
            data["rule_checks"] = rule_result
            hard_fail = (
                not rule_result["rule_checks"].get("violations", []) == []
                or not rule_result["rule_checks"].get("rejects", []) == []
                or rule_result.get("energy_check") == "energy_mismatch"
            )
            if hard_fail:
                data["confidence"] = "low"
                data["issues"] = list(data.get("issues", [])) + [
                    f"硬规则拦截: {rule_result['rule_checks'].get('violations', [])}"
                ]
            elif rule_result.get("condition_missing") and data.get("confidence") == "high":
                data["confidence"] = "medium"

        data["fallback"] = False
        return data"""
assert old in s, "reviewer return anchor"
s = s.replace(old, new)

p.write_text(s, encoding="utf-8")
print("agents.py: 插桩 A + 插桩 B 已写入")
