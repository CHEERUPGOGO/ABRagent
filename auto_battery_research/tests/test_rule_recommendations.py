"""C1/C3建议保留风险信息，但不拥有规则否决或检索降权权限。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.rag import RelationEngine, ReviewerAgent, WriterAgent


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("declarations", [
    {},
    {"notes": "未使用FEC或人工SEI，保持原始基线。"},
    {"user_locked_components": ["electrolyte"], "modifications": ["共掺杂"]},
    {"additives": ["FEC"]},
])
def test_c1_c3_only_recommend_without_changing_scheme(declarations):
    scheme = {"cathode": "LRMO", "anode": "li_metal", "electrolyte": "carbonate_ec", **declarations}
    before = copy.deepcopy(scheme)
    engine = RelationEngine()
    result = engine.check_scheme(scheme)
    assert scheme == before
    assert result["confidence"] == "high"
    assert not result["rule_checks"]["violations"]
    assert not result["rule_checks"]["rejects"]
    assert not result["challenges"]
    assert {r["rule_id"] for r in result["recommendations"]} == {"C1", "C3"}
    assert all(r["reason"] for r in result["recommendations"])
    modifiers = engine.query_modifiers(scheme)
    assert modifiers["exclude_terms"] == []
    assert {"FEC", "lhce"} <= set(modifiers["boost_terms"])


@pytest.mark.parametrize("issues", [[], ["正文声称循环性能已经验证，但引用仅给出待验证假设。"]])
def test_reviewer_receives_advice_and_keeps_independent_scientific_verdict(issues):
    prompts = []

    def chat(system, user, **kwargs):
        prompts.append(user)
        return json.dumps({"issues": issues, "confidence": "high"}, ensure_ascii=False)

    reviewer = ReviewerAgent(SimpleNamespace(available=True, chat=chat), RelationEngine())
    result = reviewer.run(question="核查共掺杂方案", draft_answer="循环性能仍需同条件验证。",
        evidence=[{"passage_id": "p1", "source": "离线测试证据", "text": "性能为待验证假设。"}],
        scheme={"cathode": "LRMO", "anode": "li_metal", "electrolyte": "carbonate_ec"})
    assert result["issues"] == issues
    assert result["confidence"] == ("low" if issues else "high")
    assert result["rule_checks"]["confidence"] == "high"
    assert "recommendations:" in prompts[0]
    assert "C1" in prompts[0] and "C3" in prompts[0]
    assert "不得仅因命中C1/C3或未采纳建议" in prompts[0]


def test_writer_receives_advice_as_optional_measures():
    prompts = []

    def chat(system, user, **kwargs):
        prompts.append(user)
        return "界面稳定性仍需验证。"

    writer = WriterAgent(SimpleNamespace(available=True, chat=chat), RelationEngine())
    writer.run(question="分析界面", plan={"requirements": {"scheme_required": False}},
        evidence=[{"passage_id": "p1", "source": "离线测试证据", "text": "界面稳定性待验证。"}],
        scheme={"cathode": "LRMO", "anode": "li_metal", "electrolyte": "carbonate_ec"})
    assert "recommendations(非阻断建议):" in prompts[0]
    assert "C1" in prompts[0] and "C3" in prompts[0]
    assert "未采纳不等于方案被否决" in prompts[0]
