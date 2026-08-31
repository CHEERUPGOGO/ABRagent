"""Writer 输出改造 patch — 答案 → 方案五段式

1. prompts.py：WRITER_SYSTEM_PROMPT 加"设计任务输出结构"节
2. agents.py：WriterAgent.run 加 scheme 参数，注入约束评估 + 五段式要求
3. rag_pipeline.py：Writer 调用前提取 scheme 并传递
"""

from pathlib import Path

base = Path("/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG")

# ── 1. prompts.py ──
p = base / "prompts.py"
s = p.read_text(encoding="utf-8")
old = """- 文献引用格式:[passage_id]
\"\"\""""
new = """- 文献引用格式:[passage_id]

# 设计任务输出结构（当用户要求"设计/推荐电池方案"时,按以下五段式组织答案）
如果用户问题是"设计一套方案/推荐材料组合"类(题目含 设计/推荐/方案 等词),在遵守 answer_outline 的前提下,**必须**按以下五段式组织:
1. **目标**:明确能量密度目标(如 ≥400 Wh/kg)与约束范围
2. **推荐组合**:正极 + 负极 + 电解液 + 添加剂(如有)
3. **预期指标**:估算能量密度/电压窗口/库仑效率预期,并标注计算口径(材料级还是电芯级)
4. **可行性依据**:每条推荐对应约束规则(如"碳酸酯氧化窗口 4.3V < 4.6V 充电截止")或文献证据,用 [passage_id] 引用
5. **风险与数据缺口**:哪些参数文献缺失、需要实验验证;不确定处明确标注"需实验验证"
--- 五段式内容之间用 "---" 分隔,确保读者清楚区分实证结论与方案建议。
\"\"\""""
assert old in s, "prompts anchor"
s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("prompts.py: 五段式输出结构已加入 WRITER_SYSTEM_PROMPT")

# ── 2. agents.py WriterAgent.run ──
p = base / "agents.py"
s = p.read_text(encoding="utf-8")

old = """    def run(
        self,
        question: str,
        plan: Dict[str, Any],
        evidence: List[Dict[str, Any]],
        history_context: str = "",
    ) -> Dict[str, Any]:
        \"\"\"基于证据生成答案.\"\"\""""
new = """    def run(
        self,
        question: str,
        plan: Dict[str, Any],
        evidence: List[Dict[str, Any]],
        history_context: str = "",
        scheme: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        \"\"\"基于证据生成答案. 若提供 scheme,注入约束评估并启用方案五段式输出.\"\"\""""
assert old in s, "writer run signature anchor"
s = s.replace(old, new)

old = """        user_prompt = (
            f"用户问题:{question}\\n\\n"
            f"计划输出结构:{str(outline)}\\n\\n"
            f"可用证据如下:\\n{evidence_text}\\n\\n"
            f"请依据证据生成一个适合材料筛选场景的结构化中文回答.\\n"
            f"必须在关键结论后保留 [passage_id] 引用.\\n"
            f"回答应以材料筛选为导向:对比候选材料/标注测试条件/给出数据缺口."
        )

        raw = self.llm.chat(WRITER_SYSTEM_PROMPT, user_prompt, temperature=0.2)"""
new = """        user_prompt = (
            f"用户问题:{question}\\n\\n"
            f"计划输出结构:{str(outline)}\\n\\n"
            f"可用证据如下:\\n{evidence_text}\\n\\n"
            f"请依据证据生成一个适合材料筛选场景的结构化中文回答.\\n"
            f"必须在关键结论后保留 [passage_id] 引用.\\n"
            f"回答应以材料筛选为导向:对比候选材料/标注测试条件/给出数据缺口."
        )

        # ── 方案模式: 注入约束评估 + 五段式指示 ──
        design_mode = any(k in question for k in ("设计", "推荐", "方案"))
        if scheme and self.relation_engine is not None:
            try:
                ev = self.relation_engine.evaluate(scheme)
                if ev["violations"] or ev["rejects"] or ev["inclusions"]:
                    scheme_note = (
                        "\\n\\n【约束引擎评估(纯规则,供写作参考,不可违反)】\\n"
                        f"方案: {scheme}\\n"
                        f"violations(违规): {ev['violations']}\\n"
                        f"rejects(拒绝): {ev['rejects']}\\n"
                        f"inclusions(需补充): {ev['inclusions']}\\n"
                        "若 violations/rejects 非空,该组合不可行,回答中必须指出并给出替代方向."
                    )
                    user_prompt += scheme_note
            except Exception as e:
                print(f"[WriterAgent] 约束评估失败(降级): {type(e).__name__}: {e}")
        if design_mode or scheme:
            user_prompt += (
                "\\n\\n【输出要求】这是设计/方案类问题,请按 WRITER_SYSTEM_PROMPT 中"
                "『设计任务输出结构』的五段式组织答案(目标/推荐组合/预期指标/可行性依据/风险与数据缺口),"
                "段间用 --- 分隔."
            )

        raw = self.llm.chat(WRITER_SYSTEM_PROMPT, user_prompt, temperature=0.2)"""
assert old in s, "writer user_prompt anchor"
s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("agents.py: WriterAgent 方案模式已加入")

# ── 3. rag_pipeline.py Writer 调用传 scheme ──
p = base / "rag_pipeline.py"
s = p.read_text(encoding="utf-8")
old = """        print("  [3/4] Writer 生成中...")
        writer_output = self.writer.run(
            question=question,
            plan=plan,
            evidence=evidence,
            history_context=history_context,
        )"""
new = """        print("  [3/4] Writer 生成中...")
        _writer_scheme = self._extract_scheme(question, plan)
        writer_output = self.writer.run(
            question=question,
            plan=plan,
            evidence=evidence,
            history_context=history_context,
            scheme=_writer_scheme,
        )"""
assert old in s, "writer call anchor"
s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("rag_pipeline.py: Writer 调用传入 scheme")
