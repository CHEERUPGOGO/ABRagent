"""Stage 4 实验推荐的离线契约测试。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.util import experiment_design as recipes
from auto_battery_research.util import scheme_ledger as ledger


def recipe_payload():
    def proposal(value, unit, role):
        return {"value": value, "unit": unit, "role": role, "basis": "engineering_proposal",
                "source": "工程推荐", "meaning": "离线测试参数"}
    return {"parameters": {
        "cathode.material": {"ref": "cathode.material"}, "cathode.L_um": {"ref": "cathode.L_um"},
        "cathode.active_fraction": proposal(.96, "", "recipe"),
        "process.drying_temperature_K": proposal(350, "K", "processing"),
        "process.electrode_diameter_um": proposal(12000, "um", "assembly"),
        "process.formation_c_rate": proposal(.1, "C", "formation"),
        "process.test_cycles": proposal(100, "", "testing")},
        "sections": {"groups": ["设主推荐组和不加改性处理的基准组，保持其余条件一致。"],
            "fabrication": ["以{{cathode.material}}为正极，活性物占比为{{cathode.active_fraction}}；干燥温度为{{process.drying_temperature_K}}，涂层厚度按{{cathode.L_um}}验收。"],
            "assembly": ["电极直径采用{{process.electrode_diameter_um}}，以{{process.formation_c_rate}}化成。"],
            "testing": ["循环测试先进行{{process.test_cycles}}轮，并记录首效及阻抗。"],
            "criteria": ["以同批基准比较可逆容量与界面阻抗，再决定下一轮优化。"],
            "gaps": ["本征物性尚需独立测量，不用工程建议代替实测。"]}, "citations": []}


def scripted_client():
    return SimpleNamespace(available=True, chat=lambda *a, **kw: json.dumps(recipe_payload(), ensure_ascii=False))


def setup_design(tmp_path):
    ledger.ensure(tmp_path)
    doc = {"target": "实验方案测试", "scheme": {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce",
        "geometry": {"cathode": {"L_um": 60}}}, "evidence": [], "final_answer": "离线测试分析"}
    ledger.commit_design(tmp_path, doc)
    recipe = recipes.generate(tmp_path, doc, scripted_client(), doc["final_answer"])
    doc.update(experiment_contract=1, experiment_plan_hash=recipe["plan_hash"])
    return doc, recipe


def test_stage4_generation_and_stage5_sync(tmp_path):
    doc, recipe = setup_design(tmp_path)
    body = recipes.render(recipe, doc)
    assert "60 um" in body or "60 μm" in body
    doc["scheme"]["geometry"]["cathode"]["L_um"] = 70
    recipes.sync(tmp_path, doc, dry_run=True)
    ledger.commit_design(tmp_path, doc, reason="修订")
    recipes.sync(tmp_path, doc)
    saved = recipes.load(tmp_path)
    assert saved["plan_hash"] == recipe["plan_hash"]
    assert saved["bindings"]["cathode.L_um"]["value"] == 70
    assert saved["sync_history"]


def test_invalid_or_missing_plan_rejected(tmp_path):
    doc, recipe = setup_design(tmp_path)
    for mutate in (lambda r: r["parameters"].pop("process.formation_c_rate"),
                   lambda r: r["citations"].append("fabricated"),
                   lambda r: r["sections"].update(assembly=[])):
        broken = copy.deepcopy(recipe)
        mutate(broken)
        broken["plan_hash"] = recipes.plan_hash(broken)
        with pytest.raises(ValueError):
            recipes.validate(broken, doc)
    with pytest.raises(ValueError):
        recipes.validate(None, doc)


def test_failure_does_not_replace_valid_recipe(tmp_path):
    doc, recipe = setup_design(tmp_path)
    before = (tmp_path / recipes.FILENAME).read_bytes()
    with pytest.raises(ValueError):
        recipes.generate(tmp_path, doc, None, "改变分析导致指纹失效")
    assert (tmp_path / recipes.FILENAME).read_bytes() == before


def test_stage6_only_projects_no_llm(tmp_path, monkeypatch):
    from auto_battery_research.util import report_recipe_llm as report
    from auto_battery_research.util.report_contract import report_context
    doc, recipe = setup_design(tmp_path)
    monkeypatch.setattr(report, "_llm_client", lambda mgr: pytest.fail("Stage 6 不应调用模型"))
    before = (tmp_path / recipes.FILENAME).read_bytes()
    plan = report.generate_experiment_plan(None, tmp_path, doc, report_context(doc, {}), {}, {}, "", None, 5)
    assert plan["source"] == "stage4_projection"
    assert (tmp_path / recipes.FILENAME).read_bytes() == before
    assert (tmp_path / recipes.REPORT_FILENAME).exists()


def test_report_steps_preserve_recipe_and_separate_suggestions(tmp_path):
    doc, recipe = setup_design(tmp_path)
    before = copy.deepcopy(recipe)
    body = recipes.render(recipe, doc)
    assert "1. **" in body and "76.85 ℃" in body and "96%" in body
    assert "**方案绑定。**" in body and "首轮实验建议" in body
    assert "本轮验证对象：" not in body and "{{" not in body
    assert "先进行100轮" in body and recipe == before
    import re
    assert not re.search(r"[：。]\*\*[^\s]", body)


def test_material_change_requires_regeneration(tmp_path):
    doc, recipe = setup_design(tmp_path)
    doc["scheme"]["electrolyte"] = "other_electrolyte"
    with pytest.raises(ValueError, match="固定条件"):
        recipes.sync(tmp_path, doc, dry_run=True)


def test_fabrication_namespace_is_valid_experiment_parameter(tmp_path):
    doc, recipe = setup_design(tmp_path)
    old_key = "process.drying_temperature_K"
    new_key = "fabrication.cathode_mixing_temperature_K"
    recipe["parameters"][new_key] = recipe["parameters"].pop(old_key)
    recipe["sections"]["fabrication"] = [p.replace(old_key, new_key) for p in recipe["sections"]["fabrication"]]
    recipe["plan_hash"] = recipes.plan_hash(recipe)
    bindings = recipes.validate(recipe, doc)
    assert bindings[new_key]["value"] == 350
    assert new_key not in ledger.load(tmp_path)["parameters"]
    recipe["parameters"][new_key]["unit"] = "invalid_unit"
    recipe["plan_hash"] = recipes.plan_hash(recipe)
    recipes.validate(recipe, doc)  # 非重叠工程参数不再硬性拒绝单位建议。


def test_geometry_rollback_restores_binding(tmp_path):
    doc, recipe = setup_design(tmp_path)
    original = copy.deepcopy(doc["scheme"]["geometry"])
    doc["scheme"]["geometry"]["cathode"]["L_um"] = 70
    ledger.commit_design(tmp_path, doc, reason="几何修订")
    recipes.sync(tmp_path, doc)
    doc["scheme"]["geometry"] = original
    ledger.commit_design(tmp_path, doc, reason="择优回滚")
    recipes.sync(tmp_path, doc)
    saved = recipes.load(tmp_path)
    assert saved["bindings"]["cathode.L_um"]["value"] == 60
    assert len(saved["sync_history"]) == 2
    assert saved["plan_hash"] == recipe["plan_hash"]
