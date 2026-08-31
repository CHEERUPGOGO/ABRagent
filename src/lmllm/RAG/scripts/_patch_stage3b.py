"""阶段三插桩 patch (part 2) — rag_pipeline.py

1. import RelationEngine（try/except 降级）
2. __init__ 创建 relation_engine 并注入 retriever / reviewer
3. run() 提取 scheme/claimed_energy 传给 reviewer.run
4. 新增辅助方法 _extract_scheme / _extract_energy_claim
"""

from pathlib import Path

p = Path("/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG/rag_pipeline.py")
s = p.read_text(encoding="utf-8")

# 1. import
old = "from .prompts import get_prompt_summary"
new = old + """

try:
    from .relation_engine import RelationEngine
except Exception:
    RelationEngine = None  # 关系引擎不可用时降级为纯 RAG"""
assert old in s, "import anchor"
s = s.replace(old, new)

# 2. __init__ 创建 relation_engine + 注入两个 agent
old = """        # 智能体
        self.planner = PlannerAgent(self.planner_llm)
        self.retriever = RetrievalAgent(
            self.kb,
            vector_store=self.vector_store,
        )
        self.writer = WriterAgent(self.writer_llm)
        self.reviewer = ReviewerAgent(self.reviewer_llm)"""
new = """        # 智能体
        # 关系引擎（阶段3插桩: 约束过滤 + 规则校验; 不可用时降级为纯 RAG）
        self.relation_engine = None
        if RelationEngine is not None:
            try:
                self.relation_engine = RelationEngine()
            except Exception as e:
                print(f"[RAGPipeline] 关系引擎初始化失败(降级为纯RAG): {e}")

        self.planner = PlannerAgent(self.planner_llm)
        self.retriever = RetrievalAgent(
            self.kb,
            vector_store=self.vector_store,
            relation_engine=self.relation_engine,
        )
        self.writer = WriterAgent(self.writer_llm)
        self.reviewer = ReviewerAgent(self.reviewer_llm, relation_engine=self.relation_engine)"""
assert old in s, "init agents anchor"
s = s.replace(old, new)

# 3. run(): reviewer 调用前提取 scheme / claimed_energy
old = """            reviewer_output = self.reviewer.run(
                question=question,
                evidence=_current_evidence,
                draft_answer=_draft,
                history_context=history_context,
            )"""
new = """            _scheme = self._extract_scheme(question, _current_plan)
            _claimed_energy = self._extract_energy_claim(_draft)
            reviewer_output = self.reviewer.run(
                question=question,
                evidence=_current_evidence,
                draft_answer=_draft,
                history_context=history_context,
                scheme=_scheme,
                claimed_energy=_claimed_energy,
            )"""
assert old in s, "reviewer call anchor"
s = s.replace(old, new)

# 4. 辅助方法（插在 _issues_to_queries 前）
old = """    @staticmethod
    def _issues_to_queries(issues: List[str]) -> List[str]:"""
new = """    def _extract_scheme(self, question: str, plan: Dict) -> Optional[Dict[str, Any]]:
        \"\"\"从问题+规划中提取材料组合方案（供 Reviewer 硬规则校验）。\"\"\"
        if self.relation_engine is None:
            return None
        try:
            entities = self.relation_engine.extract_entities(question)
            scheme = {}
            for cat in ("cathode", "anode", "electrolyte"):
                ids = entities.get(cat) or []
                if ids:
                    scheme[cat] = ids[0]
            return scheme or None
        except Exception:
            return None

    @staticmethod
    def _extract_energy_claim(text: str) -> Optional[float]:
        \"\"\"从答案文本提取声称的能量密度数值（Wh/kg）。\"\"\"
        if not text:
            return None
        m = re.search(r"(\\d+(?:\\.\\d+)?)\\s*Wh/kg", text)
        return float(m.group(1)) if m else None

    @staticmethod
    def _issues_to_queries(issues: List[str]) -> List[str]:"""
assert old in s, "helper methods anchor"
s = s.replace(old, new)

p.write_text(s, encoding="utf-8")
print("rag_pipeline.py: relation_engine 注入 + scheme 提取已写入")
