"""结构化摘要降级时保留选材依据，但不提升档案结论或改变当前方案。"""
import copy

from auto_battery_research.tests.test_research_contract import research_doc
from auto_battery_research.util.report_analysis import render_materials


def archived_doc():
    doc = research_doc()
    doc["research"].update(candidates=[], arguments=[], selected_candidate_id=None,
                           status="partial", diagnostics=["研究结论必须摘自支持原句: response"])
    doc["evidence"] = [{"passage_id": "na", "text": "Na 的层间作用"},
                       {"passage_id": "cu", "text": "Cu 共掺的钠电先例"}]
    doc["final_answer"] = """## 正极选择依据
Na 的层间支柱作用为正极选择提供依据 [na]。

Cu 有共掺先例，但属于钠电体系，不能直接证明当前锂电性能 [cu]。

Zn–Na 为探索候选，缺少直接证据，需验证占位与容量损失。

正极厚度 999 μm 为旧规格 [na]。

正极材料已被证明最优 [unknown]。
"""
    return doc


def test_missing_structured_research_preserves_archived_rationale_without_promotion():
    doc = archived_doc()
    before = copy.deepcopy(doc)
    body = render_materials(doc)
    assert "Na 的层间支柱作用" in body and "[na]" in body
    assert "钠电体系" in body and "[cu]" in body
    assert "Zn–Na 为探索候选" in body and "推断与待验证判断" in body
    assert "引用可定位不等于结论已验证" in body and "第6节附录" in body
    assert "尚无通过结构化校验的首选" in body
    assert "999" not in body and "unknown" not in body
    assert doc == before


def test_selected_reason_and_risk_are_explicit_and_parameter_bound():
    doc = research_doc()
    chosen = doc["research"]["candidates"][0]
    chosen["rationale"] = "采用{{cathode.material}}用于对照验证"
    chosen["citations"] = ["p1"]
    body = render_materials(doc)
    choice = body.split("### 2.4", 1)[1]
    assert "为什么优先选择" in choice and "采用配方A用于对照验证" in choice
    assert "[p1]" in choice and "暂不能确定最优" in choice
    assert "尚无其他候选的同条件比较" in choice
    assert "{{" not in choice


def test_missing_evidence_stays_a_gap_and_analysis_does_not_force_selection():
    doc = archived_doc()
    doc["requirements"]["scheme_required"] = False
    doc["final_answer"] = ""
    body = render_materials(doc)
    assert "没有可定位" in body and "不以通用优点补造理由" in body
    assert "材料举例不视为选型" in body
    assert "[na]" not in body and "主选为" not in body


def test_valid_structured_arguments_keep_citations_without_duplicate_fallback():
    doc = archived_doc()
    doc["research"]["arguments"] = [{"title": "层间作用", "text": "Na 的层间作用仍需验证。",
        "basis": "inference", "citations": ["na"]}]
    body = render_materials(doc)
    assert "机制推断，待验证" in body and "[na]" in body
    assert "归档摘录" not in body and "Cu 有共掺先例" not in body


def test_archived_reason_retains_same_paragraph_system_limitations():
    doc = archived_doc()
    paragraph = "Na 在 LiCoO2 中的支柱作用不利于循环性能。作者因此建议探索多元共掺 [na]。"
    doc["final_answer"] = "## 正极选择依据\n\n" + paragraph
    body = render_materials(doc)
    assert paragraph in body


def test_material_names_and_assembly_conditions_are_not_selection_reasons():
    doc = archived_doc()
    doc["final_answer"] = """## 正极候选
候选 1：Li1.17Na0.03Mn0.6Ni0.19Cu0.01O2

## 电池装配
- 壳体：CR2032 [na]。
- 对电极：金属锂片（用户锁定） [na]。
- 风险控制：Li₂CO₃ 过量 3–5% 补偿高温损失 [na]。
"""
    body = render_materials(doc)
    assert "Li1.17" not in body and "CR2032" not in body and "3–5%" not in body
    assert "#### 负极及容量匹配" in body and "用户锁定" in body
    assert "#### 电解液与界面匹配" not in body
