"""验证真实 Writer 提示词在补检索、审核修订和末轮修订中保留冻结参数。"""

import json
from types import SimpleNamespace

import pytest

from auto_battery_research.rag import RAGPipeline
from lmllm.RAG.agents import WriterAgent


@pytest.mark.parametrize("structured", [True, False])
def test_all_writer_retries_preserve_frozen_context(monkeypatch, structured):
    question = "设计 NCM811 硅碳电池方案"
    candidate = {
        "cathode": {"L_um": 72.0, "porosity": 0.28, "R_s_um": 4.0, "D_s_m2s": None},
        "anode": {"L_um": 95.0, "porosity": 0.50, "R_s_um": 6.0, "D_s_m2s": None},
        "condition": {"c_rate": 0.5, "v_min": 2.8, "v_max": 4.3},
    }
    calls, retrieval_questions, review_questions = [], [], []

    class ScriptedLLM:
        available = True

        def chat(self, system_prompt, user_prompt, temperature=None, trace_tag=None):
            calls.append((trace_tag, user_prompt))
            if trace_tag == "geometry_design":
                return json.dumps(candidate)
            # 草稿刻意不复述参数，防止测试仅因旧草稿保留参数而误通过。
            return "\n".join(f"## {title}\n" + "需要进一步证据支持。" * 5
                             for title in ("目标与设计路线", "推荐组合", "预期关键指标", "可行性依据", "风险与数据缺口"))

    def retrieve(**kwargs):
        retrieval_questions.append(kwargs["question"])
        i = len(retrieval_questions)
        return {"db_type": "literature", "results": [
            {"passage_id": f"p{i}", "source": "测试文献", "text": f"证据{i}", "score": 1.0}
        ]}

    def review(**kwargs):
        review_questions.append(kwargs["question"])
        return {"confidence": "low", "issues": ["需要补充或修正文献依据"],
                "error_type": "retrieval" if len(review_questions) == 1 else "writing"}

    pipeline = RAGPipeline.__new__(RAGPipeline)
    pipeline.structured_geometry_enabled = structured
    pipeline._reranker_enabled = False
    pipeline.relation_engine = None
    pipeline.planner = SimpleNamespace(run=lambda *args: {
        "db_type": "literature", "retrieval_queries": [question], "answer_outline": ["方案"]})
    pipeline.writer_llm = ScriptedLLM()
    pipeline.writer = WriterAgent(pipeline.writer_llm)
    pipeline.reviewer = SimpleNamespace(run=review)
    pipeline.retrieve_context = retrieve
    pipeline._extract_scheme = lambda *args, **kwargs: {
        "cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce"}
    pipeline._extract_energy_claim = lambda *args: None
    pipeline._issues_to_queries = lambda issues: ["补充证据"]
    pipeline._build_reference_section = lambda evidence: ""
    monkeypatch.setattr("auto_battery_research.tools.materials_project.augment_mp_evidence",
                        lambda retrieval, plan, top_k: retrieval)

    result = pipeline.run(question)
    writing_calls = [(tag, prompt) for tag, prompt in calls if tag != "geometry_design"]
    assert [tag for tag, _ in writing_calls] == ["writer", "writer", "writer_revise"]
    # 完成审核不等于审核通过：末轮意见保留，不能伪装成已解决。
    assert result["review_completed"] is True
    assert result["review_passed"] is False
    assert result["confidence"] == "low"
    assert result["reviewer_output"]["error_type"] == "writing"
    assert retrieval_questions == [question, question]
    assert result["question"] == question
    assert result["geometry_error"] is None
    marker = "【已校验结构化几何，正文不得变更；工程候选不是用户实测值】\n"
    if structured:
        assert sum(tag == "geometry_design" for tag, _ in calls) == 1
        assert len(set(review_questions)) == 1
        frozen = review_questions[0].split(marker, 1)[1]
        parsed = json.loads(frozen)
        assert parsed["cathode"]["L_um"] == 72.0
        assert parsed["anode"]["R_s_um"] == 6.0
        assert parsed["condition"]["c_rate"] == 0.5
        assert parsed["parameter_sources"]["cathode.D_s_m2s"]["origin"] == "model_baseline"
        for _, prompt in writing_calls:
            assert marker + frozen in prompt
            assert prompt.count(marker) == 1
    else:
        assert review_questions == [question] * 3
        assert all(marker not in prompt for _, prompt in writing_calls)
    assert "证据2" in writing_calls[1][1]
    assert "审核反馈" in writing_calls[2][1]
