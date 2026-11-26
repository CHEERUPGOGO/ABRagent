"""第二部分恢复论证，同时排除旧规格与伪造引用。"""
import copy

from auto_battery_research.util.report_analysis import render_materials
from auto_battery_research.util.report_contract import report_identity
from auto_battery_research.tests.test_report_narrative import example


def fixture():
    return {"scheme": {"cathode": "NCM811"}, "ledger_view": example(), "evidence": [
        {"passage_id": "p1", "source": "paper1", "text": "包覆可减缓界面副反应"},
        {"passage_id": "p2", "source": "paper2", "text": "含钴富锂体系中的 Na 层间作用"}],
        "final_answer": """## 推荐组合
| 排序 | 组合 | 建议化学式 | 总分 |
|---|---|---|---|
| 1 | Zn–Na | Li1.18Na0.02Mn0.6Ni0.18Zn0.02O2 | 8.6 |
| 2 | Cu–Na | Cu 替代 Zn | 8.2 |

**正极**：单晶包覆用于减缓高镍正极的界面副反应 [p1]。

正极厚度 999 μm、孔隙率 0.9 为初始提案 [p1]。

Na 的层间支柱作用见含 Co 体系，不能直接证明当前无钴体系性能 [p2]。

Zn–Na 比 Cu–Na 的成本更低，排序为候选推断，尚无直接验证。

**电解液**：EC 基碳酸酯存在高压氧化风险，添加剂选择需与正极界面匹配 [p1]。

**负极**：锂金属需要关注死锂和 SEI 稳定性 [p1]。
""", "reviewer_output": {"issues": ["Zn–Na 排名没有直接实验支撑，须标为候选。"]}}


def test_restores_reasoning_comparison_and_citations():
    doc = fixture()
    before = copy.deepcopy(doc)
    body = render_materials(doc)
    assert "单晶包覆" in body and "[p1]" in body and "[p2]" in body
    assert "Zn–Na" in body and "Cu–Na" in body and "Li1.18" in body
    assert "含 Co" in body and "不能直接证明" in body
    assert "未解决" in body and "没有直接实验支撑" in body
    assert "999" not in body and "8.6" not in body and "|" not in body
    assert "正极采用NCM811" in body
    assert doc == before


def test_unknown_citations_and_code_blocks_not_used():
    doc = fixture()
    doc["final_answer"] += "\n\n正极掺杂已被证实更好 [unknown]。\n\n```json\n{\"正极厚度\":999}\n```"
    body = render_materials(doc)
    assert "unknown" not in body and "999" not in body and "```" not in body


def test_missing_sources_are_not_replaced_with_generic_evidence():
    doc = fixture()
    doc["final_answer"] = ""
    body = render_materials(doc)
    assert "没有可定位" in body and "[p1]" not in body


def test_component_labels_and_centralized_review_issues():
    from auto_battery_research.util.material_arguments import build_arguments
    from auto_battery_research.util.report_contract import render_selected_design, report_context
    from auto_battery_research.util.report_readiness import notice
    doc = fixture()
    issue = doc["reviewer_output"]["issues"][0]
    doc["report_readiness"] = {"category": "CONDITIONAL", "issues": [issue, issue], "physics_status": "PASSED"}
    doc["final_answer"] += "\n\n**负极**：石墨与碳宿主的选择需结合 FEC 和电解液配方验证 [p1]。"
    arguments = build_arguments(doc)
    assert any("石墨与碳宿主" in c["text"] for c in arguments["components"]["anode"]["claims"])
    assert not any("石墨与碳宿主" in c["text"] for c in arguments["components"]["electrolyte"]["claims"])
    body = render_materials(doc)
    assert body.count(issue) == 1 and "**文献依据与机理。**" in body
    assert issue not in notice(doc)
    assert issue not in render_selected_design(report_context(doc, None))


def test_material_headings_and_indented_reasoning():
    import pytest
    doc = fixture()
    body = render_materials(doc)
    assert "### 2.1 正极：NCM811" in body
    assert "### 2.2 负极：锂金属" in body
    assert "### 2.4 隔膜：具体材料待确定" in body
    assert "> **选型结论。** 正极采用NCM811" in body
    assert "- **文献依据与机理。**\n\n  " in body
    assert "### 2.1 正极：Zn" not in body
    parser = pytest.importorskip("markdown_it").MarkdownIt()
    html = parser.render(body)
    assert "<h3>2.1 正极：NCM811</h3>" in html
    assert "<blockquote>" in html and "<ul>" in html and "<pre>" not in html


def test_argument_input_changes_report_identity():
    doc = fixture()
    before = report_identity(doc, None, None)
    doc["final_answer"] += "\n新的取舍理由。"
    assert report_identity(doc, None, None) != before


def test_archived_operating_conditions_do_not_override_scheme():
    doc = fixture()
    doc["final_answer"] += """

上限取冻结值 4.7 V，应另设 4.8 V 加严对照组 [p1]。

电解液 EC:DMC 主选 1:1，增设 2% FEC 对照组。

Li 过量 5 mol% 补偿挥发并缓冷至 400 ℃ [p1]。

正极界面保护用于减少氧释放和副反应 [p1]。
"""
    body = render_materials(doc)
    assert all(value not in body for value in ("4.7 V", "4.8 V", "1:1", "400 ℃"))
    assert "正极界面保护" in body


def test_chinese_headings_disambiguate_components_and_keep_inference():
    from auto_battery_research.util.material_arguments import build_arguments
    doc = fixture()
    doc["final_answer"] = """## 集流体选择
Al 箔有助导电，其高压界面仍需关注 HF 风险 [p1]。

## 正极改性机理
Na 支柱作用来自含 Co 体系，迁移至无钴体系属推断 [p2]。
"""
    arguments = build_arguments(doc)
    assert arguments["components"]["current_collector"]["claims"][0]["text"].startswith("Al 箔")
    assert not arguments["components"]["electrolyte"]["claims"]
    assert arguments["components"]["cathode"]["claims"][0]["inference"]


def test_evidence_input_changes_report_identity():
    doc = fixture()
    before = report_identity(doc, None, None)
    doc["evidence"][0]["text"] = "更正后的证据原文"
    assert report_identity(doc, None, None) != before


def test_recipe_table_is_not_misread_as_cathode_ranking():
    from auto_battery_research.util.material_arguments import build_arguments
    doc = fixture()
    doc["final_answer"] = """| 部件 | 建议配方/工艺（候选） | 依据 |
|---|---|---|
| 负极 | 压实 1.5 g/cm³、面载 11 mg/cm² | [p1] |
| 化成 | 0.05C 充电 | [p1] |

粘结剂有助缓冲硅碳负极膨胀 [p1]。

粘结剂是70 μm厚硅碳极片的关键 [p1]。
"""
    assert not build_arguments(doc)["comparisons"]
    body = render_materials(doc)
    assert "缓冲硅碳负极膨胀" in body
    assert "70 μm" not in body and "0.05C" not in body and "11 mg" not in body
