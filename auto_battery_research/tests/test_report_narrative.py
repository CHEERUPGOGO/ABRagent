"""报告正文与内部参数表分离，并验证方法型候选及版本缓存。"""
import copy
import json

from auto_battery_research.util import scheme_ledger as ledger
from auto_battery_research.util import report_narrative as prose
from auto_battery_research.util import report_recipe_llm as plans
from auto_battery_research.util.report_contract import report_context, render_selected_design


def example():
    def row(value, unit=""):
        return {"value": value, "unit": unit, "basis": "engineering_proposal", "source": "测试设计"}
    return {"revision": 3, "parameters": {"cathode.material": row("NCM811"), "anode.material": row("li_metal"),
        "electrolyte.material": row("lhce"), "cathode.L_um": row(60, "um"), "cathode.porosity": row(.28),
        "cathode.D_s_m2s": row(None, "m2/s"), "process.drying_temperature_K": row(350, "K"),
        "condition.c_rate": row(.5, "C")}, "derived": {}, "candidates": [], "issues": []}


def test_internal_table_and_report_prose_are_separate():
    data = example()
    before = copy.deepcopy(data)
    body = prose.render_design(data)
    assert "正极采用NCM811" in body and "60 μm" in body and "28%" in body
    assert "cathode." not in body and "|" not in body and "```" not in body
    assert "| 字段" in ledger.render(data)
    assert data == before
    doc = {"scheme": {"cathode": "NCM811"}, "ledger_view": data}
    assert render_selected_design(report_context(doc, None)) == body
    recipe = ledger.render_experiment(data, {})
    assert "76.85 ℃" in recipe and "未完成有效放电仿真" in recipe
    assert "process" not in recipe and "|" not in recipe


def test_method_only_suggestions_keep_value_empty():
    suggestions = prose.missing_suggestions(example())
    body = prose.render_suggestions(suggestions)
    assert suggestions["cathode.D_s_m2s"]["value"] is None
    assert "固相扩散系数" in body and "间歇滴定" in body
    assert "可靠数值" in body and "```" not in body and "cathode." not in body
    old = {"process.drying_temperature_K": {"value": 350, "unit": "K", "basis": "engineering_proposal"}}
    assert "76.85 ℃" in prose.render_suggestions(old) and "小批" in prose.render_suggestions(old)


def test_offline_generation_and_old_cache_rejected(tmp_path, monkeypatch):
    doc = {"ledger_view": example(), "scheme": {"cathode": "NCM811"}, "ledger_hash": "固定版本"}
    context = report_context(doc, None)
    monkeypatch.setattr(plans, "_llm_client", lambda mgr: None)
    (tmp_path / plans.PLAN_FILENAME).write_text(json.dumps({"fingerprint": "旧版", "text": "旧表格", "source": "scheme_ledger"}), encoding="utf-8")
    output = plans.generate_experiment_plan(None, tmp_path, doc, context, {}, {}, "", None, 5)
    assert "旧表格" not in output["text"] and "间歇滴定" in output["text"]
    saved = plans.load_plan_record(tmp_path)
    assert saved["render_version"] == prose.RENDER_VERSION
    assert "|" not in saved["text"] and "```" not in saved["text"]
    assert "process.drying_temperature_K" not in saved["suggestions"]


def test_existing_value_cannot_be_suggested_as_missing():
    assert plans._validate_suggestions({"cathode.L_um": {"value": 70, "unit": "um",
        "basis": "engineering_proposal", "source": "LLM候选"}}, example())


def test_model_geometry_is_prose():
    data = example()
    selected = {"cathode": {"L_um": 60}}
    body = prose.render_geometry_comparison({"design": {"pe": {"L": 60e-6}}}, selected, {"cathode": {"L_um": 50}})
    assert "50 μm" in body and "60 μm" in body and "模型实际采用" in body
    assert "|" not in body and "cathode." not in body


def test_units_and_parameter_layers_preserve_inputs():
    data = example()
    before = copy.deepcopy(data)
    body = prose.render_design(data)
    assert "**正极配置。**" in body and "容量匹配尚未闭合" in body
    assert "固相扩散系数" not in body.split("### 3.2")[0]
    assert "具体设计中" not in body
    assert prose.amount("process.ph", {"value": 8, "unit": "1"}) == "8"
    assert prose.amount("process.count", {"value": 3, "unit": "cells"}) == "3 只"
    assert prose.readable_units("25 K，298.15 K，1e-14 m²/s [p1]") == "-248.15 ℃，25 ℃，1e-14 m²/s [p1]"
    assert data == before


def test_bold_labels_have_markdown_closing_boundaries():
    import re
    from auto_battery_research.util.report_readiness import notice
    body = prose.render_design(example()) + "\n\n" + notice({"report_readiness": {"category": "CONDITIONAL", "issues": ["待核查"]}})
    assert not re.search(r"[：。]\*\*[^\s]", body)
    # 与 VS Code 同源的 Markdown-it 解析器实际验证加粗，而非只检查星号。
    import pytest
    parser = pytest.importorskip("markdown_it").MarkdownIt()
    html = parser.render(body)
    assert "<strong>正极配置。</strong>" in html
    assert "<strong>交付状态：</strong>" in html


def test_integrated_design_keeps_scopes_and_moves_model_details():
    data = example()
    data["parameters"].update({
        "electrolyte.salt": {"value": "LiPF6", "conditions": "基线配方，LHCE 冲突待核对"},
        "electrolyte.conc_M": {"value": 1, "unit": "mol/L", "conditions": "基线配方，LHCE 冲突待核对"},
        "anode.n_p_ratio": {"value": None}, "anode.loading_mg_cm2": {"value": None},
        "anode.R_s_um": {"value": 4.8, "unit": "um"}})
    before = copy.deepcopy(data)
    body = prose.render_design(data)
    assert all(f"### 3.{i}" in body for i in range(1, 5))
    assert "**60 μm**" in body and "容量匹配尚未闭合" in body
    assert body.count("基线配方，LHCE 冲突待核对") == 1 and "统一配方" in body
    assert "隔膜、集流体、封装" in body
    assert "4.8 μm" not in body and "4.8 μm" in prose.render_model_parameters(data)
    assert data == before
    data["parameters"]["anode.n_p_ratio"]["value"] = 1.1
    body = prose.render_design(data)
    assert "容量匹配尚未闭合" not in body and "实测核验" in body


def test_pore_budget_does_not_imply_energy_feasibility():
    data = example()
    data["derived"] = {"cathode.pore_volume_uL_cm2": {"value": 2.52, "unit": "uL/cm2"}}
    body = prose.render_design(data)
    assert "2.52 μL/cm²" in body and "不能单独证明" in body
