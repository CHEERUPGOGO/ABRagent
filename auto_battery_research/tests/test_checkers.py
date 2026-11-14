"""Unit tests for deterministic checkers, sandboxing, and actions in auto_battery_research."""

import pytest
import json
from pathlib import Path
from auto_battery_research.checkers.ingestion_checker import IngestionChecker
from auto_battery_research.checkers.vector_db_checker import VectorDBChecker
from auto_battery_research.checkers.cell_assembly_checker import CellAssemblyChecker
from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
from auto_battery_research.checkers.pinn_physics_checker import PINNPhysicsChecker
from auto_battery_research.checkers.final_report_checker import FinalReportChecker
from auto_battery_research.tools.file_tools import validate_workspace_path, read_text_file, edit_text_file
from auto_battery_research.tools.knowledge_retriever import search_knowledge_base
from lmllm.RAG.relation_engine import RelationEngine


def test_pinn_checker_skip_logic():
    checker = PINNPhysicsChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 5, "skip": True, "skip_reason": "默认跳过"}, config={})
    passed, diag = checker.do_check()
    assert passed is True
    assert diag["observed"]["stage_status"] == "SKIPPED"


def test_ingestion_checker():
    checker = IngestionChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 1, "name": "文献解析"}, config={"paths": {"database_type_dir": "database/type", "papers_merged_dir": "papers/merged"}})
    passed, diag = checker.do_check()
    assert "check_pass" in diag
    assert "observed" in diag


def test_vector_db_checker_real_data():
    checker = VectorDBChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 2, "name": "语义标注"}, config={})
    passed, diag = checker.do_check()
    assert passed is True
    assert diag["observed"]["total_paragraphs"] > 0


def test_final_report_checker_schema(tmp_path):
    checker = FinalReportChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 6, "name": "研报生成"}, config={"paths": {"output_dir": str(tmp_path)}})
    passed, diag = checker.do_check()
    assert passed is False
    assert diag["error_code"] == "SYNTHESIS_REPORT_MISSING"
    assert "next_action" in diag

    # Positive test: valid final_research_report.md
    valid_report = tmp_path / "final_research_report.md"
    valid_report.write_text("""# 化学电池全生命周期研发与设计综合研报
## 1. 研发摘要
研发摘要内容... 包含详细的电化学背景与设计路线规划，面向用户高比能需求。
## 2. 文献与电芯数据概况
数据概况内容... 涵盖 5000+ 段落语义索引与 80+ 篇材料实体挖掘。
## 3. 电池体系设计方案
设计方案内容... 正极采用高镍单晶，负极采用金属锂，电解液采用局部高浓度体系。
## 4. 物理仿真与验证
物理仿真内容... Newman P2D 偏微分方程求解收敛，放电特性满足理论边界。
## 5. 实验配方与落地建议
实验配方内容... 首效与循环稳定性评估建议。
""" * 2, encoding="utf-8")

    (tmp_path / "stage_journals.json").write_text(json.dumps([{"stage_id": 1, "notes": "Literature completed"}]), encoding="utf-8")

    passed2, diag2 = checker.do_check()
    assert passed2 is True
    assert diag2["observed"]["report_length"] > 400


def test_workspace_path_sandbox():
    """测试 MCP 文件工具 Workspace 路径沙箱安全边界."""
    root = Path(__file__).resolve().parent.parent.parent
    
    # 1. 正常路径通过
    valid, safe_p, err = validate_workspace_path("auto_battery_research/setting.yaml", workspace_root=root)
    assert valid is True
    assert safe_p is not None
    assert err is None
    
    # 2. 路径遍历跨目录攻击拦截
    valid, safe_p, err = validate_workspace_path("../../etc/passwd", workspace_root=root)
    assert valid is False
    assert "安全拦截" in err
    
    # 3. 读取跨目录文件失败
    res = read_text_file("../../Windows/System32/drivers/etc/hosts", workspace_root=root)
    assert res["success"] is False
    assert "安全拦截" in res["error"]


def test_relation_engine_and_scheme():
    """测试底层 src/lmllm/RAG 关系引擎与材料约束核查."""
    re_engine = RelationEngine()
    
    # 实体归一化
    assert re_engine.normalize("NCM811") == "NCM811"
    assert re_engine.normalize("金属锂") == "li_metal"
    
    # 约束校验
    scheme = {"cathode": "NCM811", "anode": "li_metal", "electrolyte": "lhce"}
    check_res = re_engine.check_scheme(scheme, claimed_energy=400.0, answer_text="400 Wh/kg 循环测试 0.5C 2.8V 窗口")
    assert "rule_checks" in check_res
    assert check_res["confidence"] in ("high", "medium", "low")


def test_fortified_rag_design_checker(tmp_path):
    """测试强化版 RAGDesignChecker 对证据溯源、置信度与五段式的严格校验."""
    scheme_md = tmp_path / "design_scheme.md"
    scheme_json = tmp_path / "design_scheme.json"
    
    # 创建合规五段式与 JSON
    valid_md = """# 设计方案
## 1. 目标与设计路线
设计400Wh/kg高比能电池路线

## 2. 推荐组合与关键配方
- 正极: SC-NCM90
- 负极: 锂金属 (li_metal)
- 电解液: LHCE

## 3. 预期关键性能指标
- 能量密度: 410 Wh/kg
- 循环保持率: 88%

## 4. 可行性依据与机理
[ev_001] 前沿文献表明通过 LHCE 可稳定高镍界面。

## 5. 风险与数据缺口
需要评估大规模产线水分控制。
"""
    scheme_md.write_text(valid_md, encoding="utf-8")
    
    valid_json_payload = {
        "schema_version": "1.0",
        "target": "设计400Wh/kg高比能电池",
        "confidence": "high",
        "evidence": [{"passage_id": "ev_001", "source": "10.1038/s41467", "text": "evidence text"}],
        "scheme": {"cathode": "SC-NCM90", "anode": "li_metal", "electrolyte": "lhce",
                   "geometry": {
                       "schema_version": "1.1",
                       "cathode": {"L_um": 80.0, "porosity": 0.35, "R_s_um": 5.0, "D_s_m2s": 1e-14},
                       "anode": {"L_um": 50.0, "porosity": 0.2, "R_s_um": 20.0, "D_s_m2s": 1e-10},
                       "provenance": {
                           "cathode.L_um": "stage4_recipe", "cathode.porosity": "stage4_recipe",
                           "anode.L_um": "stage4_recipe", "anode.porosity": "stage4_recipe",
                           "cathode.R_s_um": "stage4_recipe", "cathode.D_s_m2s": "stage4_recipe",
                           "anode.R_s_um": "stage4_recipe", "anode.D_s_m2s": "stage4_recipe",
                       },
                       "condition": {},
                   }},
        "rule_checks": {"violations": [], "rejects": []},
    }
    scheme_json.write_text(json.dumps(valid_json_payload), encoding="utf-8")
    
    checker = RAGDesignChecker()
    checker.on_init(
        stage_manager=None,
        stage_info={"id": 4, "name": "方案设计"},
        config={"paths": {"output_dir": str(tmp_path)}},
    )
    passed, diag = checker.do_check()
    assert passed is True
    assert diag["observed"]["confidence"] == "high"


def test_rag_design_checker_fail_closed_zero_evidence(tmp_path):
    """测试无真实证据时，RAG 门禁严格拦截 (Fail-Closed)."""
    scheme_md = tmp_path / "design_scheme.md"
    scheme_json = tmp_path / "design_scheme.json"
    
    valid_md = """# 设计方案
## 1. 目标与设计路线
设计400Wh/kg高比能电池路线，采用单晶高镍正极与锂金属负极搭配局部高浓度电解液体系。

## 2. 推荐组合与关键配方
- 正极: SC-NCM90
- 负极: 锂金属 (li_metal)
- 电解液: LHCE

## 3. 预期关键性能指标
- 能量密度: 410 Wh/kg
- 循环保持率: 88%

## 4. 可行性依据与机理
前沿文献表明通过 LHCE 可稳定高镍界面。

## 5. 风险与数据缺口
需要评估大规模产线水分控制。
"""
    scheme_md.write_text(valid_md, encoding="utf-8")
    
    # 证据为空
    scheme_json.write_text(json.dumps({
        "schema_version": "1.0",
        "target": "设计测试",
        "confidence": "high",
        "evidence": [],
        "scheme": {"cathode": "NCM811", "anode": "li_metal", "electrolyte": "lhce"},
        "rule_checks": {"violations": []},
    }), encoding="utf-8")
    
    checker = RAGDesignChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 4, "name": "方案设计"}, config={"paths": {"output_dir": str(tmp_path)}})
    passed, diag = checker.do_check()
    assert passed is False
    assert diag["error_code"] == "EVIDENCE_MISSING"


def test_rag_design_checker_fail_closed_incomplete_scheme(tmp_path):
    """测试材料配方缺漏时，RAG 门禁严格拦截 (Fail-Closed)."""
    scheme_md = tmp_path / "design_scheme.md"
    scheme_json = tmp_path / "design_scheme.json"
    
    valid_md = """# 设计方案
## 1. 目标与设计路线
设计400Wh/kg高比能电池路线，采用单晶高镍正极与锂金属负极搭配局部高浓度电解液体系。

## 2. 推荐组合与关键配方
- 正极: SC-NCM90
- 负极: 锂金属 (li_metal)
- 电解液: LHCE

## 3. 预期关键性能指标
- 能量密度: 410 Wh/kg
- 循环保持率: 88%

## 4. 可行性依据与机理
前沿文献表明通过 LHCE 可稳定高镍界面。

## 5. 风险与数据缺口
需要评估大规模产线水分控制。
"""
    scheme_md.write_text(valid_md, encoding="utf-8")
    
    # 缺少 electrolyte
    scheme_json.write_text(json.dumps({
        "schema_version": "1.0",
        "target": "设计测试",
        "confidence": "high",
        "evidence": [{"passage_id": "ev_01", "source": "test", "text": "evidence text"}],
        "scheme": {"cathode": "NCM811", "anode": "li_metal"},
        "rule_checks": {"violations": []},
    }), encoding="utf-8")
    
    checker = RAGDesignChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 4, "name": "方案设计"}, config={"paths": {"output_dir": str(tmp_path)}})
    passed, diag = checker.do_check()
    assert passed is False
    assert diag["error_code"] == "STRUCTURED_SCHEME_INCOMPLETE"


def test_ingestion_checker_fail_closed_missing_component(tmp_path):
    """测试文献分类不全时，IngestionChecker 严格判定失败."""
    db_dir = tmp_path / "database" / "type"
    (db_dir / "cathode").mkdir(parents=True)
    (db_dir / "anode").mkdir(parents=True)
    # 缺少 electrolyte
    (db_dir / "cathode" / "paper1.md").write_text("# Cathode Paper\n" * 10, encoding="utf-8")
    (db_dir / "anode" / "paper2.md").write_text("# Anode Paper\n" * 10, encoding="utf-8")

    checker = IngestionChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 1, "name": "文献解析"}, config={"paths": {"database_type_dir": str(db_dir)}})
    passed, diag = checker.do_check()
    assert passed is False
    assert diag["error_code"] == "INCOMPLETE_COMPONENT_CLASSIFICATION"
    assert "electrolyte" in diag["observed"]["missing_components"]


def test_cell_assembly_checker_fail_closed(tmp_path):
    """测试材料挖掘为空时，CellAssemblyChecker 严格判定失败."""
    cell_dir = tmp_path / "cell_assembly"
    cell_dir.mkdir(parents=True)
    
    empty_cell_file = cell_dir / "sample_assembled_cell_extracted.json"
    empty_cell_file.write_text(json.dumps({
        "materials": [],
        "cells": [],
    }), encoding="utf-8")

    checker = CellAssemblyChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 3, "name": "材料挖掘"}, config={"paths": {"output_dir": str(tmp_path)}})
    passed, diag = checker.do_check()
    assert passed is False
    assert diag["error_code"] == "NO_MATERIALS_EXTRACTED"


def test_stage4_to_5_schema_integration(tmp_path):
    """测试 Stage 4 方案 -> Stage 5 SPM PINN 仿真 -> PINNPhysicsChecker 契约贯通."""
    from pinn.spm_runner import run_pinn_discharge, match_pinn_system
    from pinn.input_spec import build_cell_spec

    system = match_pinn_system("NCM811", "si_base")
    assert system is not None, "GrSi_NMC811 体系应已注册"
    scheme = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce",
              "target_energy_wh_kg": 500.0}
    cell_spec = build_cell_spec(scheme, None, c_rate=0.5)
    # 输入合规 (校准到体系训练参考值)，保证走通完整仿真链路
    cell_spec["anode"].update({"L": 110e-6, "epsilon": 0.56})
    cell_spec["anode"]["material"].update({"R_p": 8.21e-6, "D_s": 4e-14})
    cell_spec["cathode"].update({"L": 70e-6, "epsilon": 0.31})
    cell_spec["cathode"]["material"].update({"R_p": 5.34e-6, "D_s": 1e-14})

    sim_result = run_pinn_discharge(cell_spec, scheme=scheme, system=system)
    assert sim_result["status"] == "CONVERGED", sim_result.get("error")
    assert "specific_capacity_mAh_g" in sim_result
    assert "average_voltage_V" in sim_result
    assert "energy_wh_kg" in sim_result
    assert "discharge_curve" in sim_result
    assert "capacity" in sim_result["discharge_curve"]
    assert "voltage" in sim_result["discharge_curve"]

    sim_file = tmp_path / "pinn_simulation_result.json"
    sim_file.write_text(json.dumps(sim_result, ensure_ascii=False), encoding="utf-8")

    checker = PINNPhysicsChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 5, "name": "物理仿真", "skip": False}, config={"paths": {"output_dir": str(tmp_path)}})
    passed, diag = checker.do_check()
    assert passed is True
    assert diag["check_pass"] is True


def test_mcp_stdio_logger_redirection(capsys):
    """测试 MCP stdio 模式下日志强制流向 stderr，绝不污染 stdout."""
    import sys
    from auto_battery_research.util.logger import set_mcp_stdio_mode, log_raw, log_info

    # 激活 MCP 隔离模式
    set_mcp_stdio_mode(True)
    try:
        log_info("This is an internal agent log in MCP mode")
        captured = capsys.readouterr()
        # stdout 必须完全纯净 (空)
        assert captured.out == ""
        # stderr 必须包含日志内容
        assert "This is an internal agent log in MCP mode" in captured.err
    finally:
        set_mcp_stdio_mode(False)


def test_checker_workspace_root_resolution():
    """测试 Checker 的 workspace_root 与 resolve_path 杜绝相对路径漂移."""
    checker = IngestionChecker()
    resolved = checker.resolve_path("database/type")
    assert resolved.is_absolute()
    assert str(resolved).endswith(str(Path("database/type")))


def test_rag_design_checker_missing_json_no_crash(tmp_path):
    """测试仅有 Markdown 缺少 JSON 时，RAGDesignChecker 明确返回错误码而不抛出 UnboundLocalError."""
    md_file = tmp_path / "design_scheme.md"
    md_file.write_text("""# 设计方案
## 1. 目标与设计路线
设计路线内容...
## 2. 推荐材料组合
正极负极电解液...
## 3. 预期关键指标
能量密度400Wh/kg...
## 4. 可行性依据
文献依据...
## 5. 风险与数据缺口
风险评估...
""" * 3, encoding="utf-8")

    checker = RAGDesignChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 4, "name": "方案设计"}, config={"paths": {"output_dir": str(tmp_path)}})
    passed, diag = checker.do_check()
    assert passed is False
    assert diag["error_code"] == "DESIGN_SCHEME_JSON_MISSING"


def test_rag_design_checker_mismatched_foreign_evidence(tmp_path):
    """测试锂电方案搭配纯钠电/MoSe2 异质文献证据时，触发 EVIDENCE_CHEMISTRY_MISMATCH 拦截."""
    md_file = tmp_path / "design_scheme.md"
    md_file.write_text("""# 锂金属电池设计方案
## 1. 目标与设计路线
设计400Wh/kg高比能锂金属电池
## 2. 推荐材料组合
推荐组合：正极 NCM811，负极 li_metal，电解液 LHCE
## 3. 预期关键指标
预期比容量 220 mAh/g
## 4. 可行性依据
文献证据支撑
## 5. 风险与数据缺口
数据缺口分析
""" * 2, encoding="utf-8")

    json_file = tmp_path / "design_scheme.json"
    json_file.write_text(json.dumps({
        "scheme": {
            "cathode": "NCM811",
            "anode": "li_metal",
            "electrolyte": "LHCE",
        },
        "evidence": [
            {
                "passage_id": "doc_na_001",
                "source": "10.1016/j.ensm.2021.05.022",
                "text": "MoSe2 纳米花作为钠离子电池 (Na-ion battery) 负极材料展现出优异的倍率性能与硬碳钠电兼容性，循环1000次容量无明显衰减。",
            }
        ],
        "confidence": "high",
        "rule_checks": {
            "violations": [],
            "rejects": [],
            "energy_check": "passed",
        }
    }), encoding="utf-8")

    checker = RAGDesignChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 4, "name": "方案设计"}, config={"paths": {"output_dir": str(tmp_path)}})
    passed, diag = checker.do_check()
    assert passed is False
    assert diag["error_code"] == "EVIDENCE_CHEMISTRY_MISMATCH"


def test_cell_assembly_checker_empty_cells_fail_closed(tmp_path):
    """测试仅有材料列表但 cells 为空时，CellAssemblyChecker 必须拦截报错 NO_CELLS_ASSEMBLED."""
    cell_dir = tmp_path / "cell_assembly"
    cell_dir.mkdir(parents=True, exist_ok=True)
    sample_file = cell_dir / "sample_extracted.json"
    sample_file.write_text(json.dumps({
        "materials": [{"canonical_id": "NCM811", "name": "NCM811"}],
        "cells": [],
    }), encoding="utf-8")

    checker = CellAssemblyChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 3, "name": "材料挖掘组装"}, config={"paths": {"output_dir": str(tmp_path)}})
    passed, diag = checker.do_check()
    assert passed is False
    assert diag["error_code"] == "NO_CELLS_ASSEMBLED"


def test_cell_assembly_checker_incomplete_cell_fields_fail_closed(tmp_path):
    """测试电芯实体缺少必要组件引用 (如缺电解液或缺 provenance) 时，触发 CELL_SPEC_INCOMPLETE 拦截."""
    cell_dir = tmp_path / "cell_assembly"
    cell_dir.mkdir(parents=True, exist_ok=True)
    sample_file = cell_dir / "sample_extracted.json"
    sample_file.write_text(json.dumps({
        "materials": [{"canonical_id": "NCM811", "name": "NCM811"}],
        "cells": [
            {
                "cell_id": "cell_001",
                "cathode": "NCM811",
                "anode": "Li_metal",
                # missing electrolyte and provenance
            }
        ],
    }), encoding="utf-8")

    checker = CellAssemblyChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 3, "name": "材料挖掘组装"}, config={"paths": {"output_dir": str(tmp_path)}})
    passed, diag = checker.do_check()
    assert passed is False
    assert diag["error_code"] == "CELL_SPEC_INCOMPLETE"


def test_unknown_materials_and_c7_c8_fail_closed():
    """测试 RelationEngine 对未知材料的 Fail-Closed 拒绝机制与 C7/C8 target_energy 拦截."""
    from lmllm.RAG.relation_engine import RelationEngine
    engine = RelationEngine()

    # 1. 全未知材料：必须触发 C0_UNVERIFIED_MATERIAL 拒绝 (Fail-Closed)
    unknown_scheme = {
        "cathode": "Unverified_Magic_Cathode_XYZ",
        "anode": "Hypothetical_Anode_ABC",
        "electrolyte": "Alien_Solvent_123",
    }
    ev_unknown = engine.evaluate(unknown_scheme)
    assert ev_unknown["feasible"] is False
    assert any(r["rule_id"] == "C0_UNVERIFIED_MATERIAL" for r in ev_unknown["rejects"])

    # 2. C7 规则: target_energy >= 400 Wh/kg 用石墨负极必须被 exclude 拦截
    c7_scheme = {
        "cathode": "NCM811",
        "anode": "graphite",
        "electrolyte": "carbonate_ec",
        "target_energy_density": 420.0,
    }
    ev_c7 = engine.evaluate(c7_scheme)
    assert ev_c7["feasible"] is False
    assert any(v["rule_id"] == "C7" for v in ev_c7["violations"])

    # 3. C8 规则: target_energy >= 500 Wh/kg 液态体系必须被直接 reject
    c8_scheme = {
        "cathode": "NCM811",
        "anode": "li_metal",
        "electrolyte": "lhce",
        "target_energy": 520.0,
    }
    ev_c8 = engine.evaluate(c8_scheme)
    assert ev_c8["feasible"] is True
    assert any(r["rule_id"] == "C8" for r in ev_c8["frontier_flags"])

    # 4. C4 规则已永久移除
    assert not any(r.get("id") == "C4" for r in engine.constraints.get("rules", []))

    # 5. C1 与 C3 自洽性软质询：声明 FEC/单晶等对策即放行，纯裸体系无对策则拦截
    mitigated_scheme = {
        "cathode": "NCM811",
        "anode": "li_metal",
        "electrolyte": "carbonate_ec",
        "additives": ["FEC", "VC"],
    }
    ev_mit = engine.evaluate(mitigated_scheme)
    assert ev_mit["feasible"] is True
    assert any(c["rule_id"] == "C3" and c["status"] == "passed" for c in ev_mit["challenges"])

    # 6. C1/C2 裸碳酸酯无改性拦截，声明对策后放行
    ev_c1_bare = engine.evaluate({"cathode": "LRMO", "anode": "graphite", "electrolyte": "carbonate_ec"})
    assert ev_c1_bare["feasible"] is False
    assert any(v["rule_id"] == "C1" for v in ev_c1_bare["violations"])

    ev_c1_mit = engine.evaluate({"cathode": "LRMO", "anode": "graphite", "electrolyte": "carbonate_ec", "additives": ["FEC"]})
    assert ev_c1_mit["feasible"] is True
    assert any(c["rule_id"] == "C1" and c["status"] == "passed" for c in ev_c1_mit["challenges"])

    ev_c2_bare = engine.evaluate({"cathode": "LNMO", "anode": "graphite", "electrolyte": "carbonate_ec"})
    assert ev_c2_bare["feasible"] is False
    assert any(v["rule_id"] == "C2" for v in ev_c2_bare["violations"])

    # 7. target_energy_wh_kg（RAGPipeline 输出字段名）能正确触发 C7/C8
    ev_wh_c7 = engine.evaluate({"cathode": "NCM811", "anode": "graphite", "electrolyte": "carbonate_ec", "target_energy_wh_kg": 400.0})
    assert ev_wh_c7["feasible"] is False
    assert any(v["rule_id"] == "C7" for v in ev_wh_c7["violations"])

    ev_wh_c8 = engine.evaluate({"cathode": "LRMO", "anode": "li_metal", "electrolyte": "lhce", "target_energy_wh_kg": 520.0})
    assert ev_wh_c8["feasible"] is True
    assert any(f["rule_id"] == "C8" for f in ev_wh_c8["frontier_flags"])

    # 8. query_modifiers 在 v2.1 规则下正常产出 exclude/boost 词且过滤单字符别名（如 "C"）
    mods = engine.query_modifiers({"cathode": "LRMO", "anode": "li_metal", "target_energy_wh_kg": 420.0})
    assert "carbonate_ec" in mods["exclude_terms"]
    assert "C" not in mods["exclude_terms"]
    assert any(t in mods["boost_terms"] for t in ("FEC", "lhce", "LiNO3"))

    mods_no_anode = engine.query_modifiers({"cathode": "LRMO", "target_energy_wh_kg": 420.0})
    assert "graphite" in mods_no_anode["exclude_terms"]
    assert "C" not in mods_no_anode["exclude_terms"]
    assert "li_metal" in mods_no_anode["boost_terms"]


def test_rag_pipeline_scheme_energy_wh_kg_triggers_c7():
    """验证 RAGPipeline._extract_scheme 产出的 target_energy_wh_kg 能端到端触发 RelationEngine C7 与指标优先提取。"""
    from lmllm.RAG.rag_pipeline import RAGPipeline
    from lmllm.RAG.relation_engine import RelationEngine

    pipe = RAGPipeline.__new__(RAGPipeline)
    pipe.relation_engine = RelationEngine()

    draft = (
        "## 1. 目标与设计路线\n常规石墨体系能量密度仅约 250 Wh/kg。\n"
        "## 3. 预期关键性能指标\n- 单体质量能量密度: 410 Wh/kg\n"
    )
    assert pipe._extract_energy_claim(draft) == 410.0

    scheme = pipe._extract_scheme("设计400Wh/kg高比能锂电池（正极 NCM811，负极 石墨，电解液 常规碳酸酯）", {}, draft_text=draft)
    assert scheme is not None and scheme.get("target_energy_wh_kg") == 400.0
    chk = pipe.relation_engine.check_scheme(scheme, claimed_energy=pipe._extract_energy_claim(draft), answer_text=draft)
    assert any(v["rule_id"] == "C7" for v in chk["rule_checks"]["violations"])
    assert chk["confidence"] == "low"







# ────────────────────────── Stage 4 geometry 声明门禁 ──────────────────────────

def _write_rag_artifacts(tmp_path, scheme, extra_config=None):
    """写入合规五段式 md + 带指定 scheme 的 design_scheme.json, 返回 do_check 结果。"""
    scheme_md = tmp_path / "design_scheme.md"
    scheme_json = tmp_path / "design_scheme.json"
    scheme_md.write_text("""# 设计方案
## 1. 目标与设计路线
设计400Wh/kg高比能电池路线

## 2. 推荐组合与关键配方
- 正极: NCM811
- 负极: si_base

## 3. 预期关键性能指标
- 能量密度: 410 Wh/kg

## 4. 可行性依据与机理
[ev_001] LHCE 稳定高镍界面。

## 5. 风险与数据缺口
风险评估。
""" * 2, encoding="utf-8")
    payload = {
        "schema_version": "1.0",
        "target": "设计400Wh/kg高比能电池",
        "confidence": "high",
        "evidence": [{"passage_id": "ev_001", "source": "10.1038/s41467", "text": "evidence text"}],
        "scheme": scheme,
        "rule_checks": {"violations": [], "rejects": []},
    }
    scheme_json.write_text(json.dumps(payload), encoding="utf-8")
    checker = RAGDesignChecker()
    checker.on_init(stage_manager=None, stage_info={"id": 4, "name": "方案设计"},
                    config={"paths": {"output_dir": str(tmp_path)}, **(extra_config or {})})
    return checker.do_check()


GEOMETRY_V11 = {
    "schema_version": "1.1",
    "cathode": {"L_um": 80.0, "porosity": 0.35, "R_s_um": 5.34, "D_s_m2s": 1e-14},
    "anode": {"L_um": 110.0, "porosity": 0.45, "R_s_um": 8.21, "D_s_m2s": 4e-14},
    "provenance": {
        "cathode.L_um": "stage4_recipe", "cathode.porosity": "stage4_recipe",
        "anode.L_um": "stage4_recipe", "anode.porosity": "stage4_recipe",
        "cathode.R_s_um": "stage4_recipe", "cathode.D_s_m2s": "stage4_recipe",
        "anode.R_s_um": "stage4_recipe", "anode.D_s_m2s": "stage4_recipe",
        "condition.v_min": "params_baseline",
    },
    "n_p_ratio": 1.2,
    "condition": {"v_min": 2.8, "c_rate": 0.5},
}


def test_rag_geometry_gate_pass(tmp_path):
    """8 硬值显式 (stage4_recipe) + 可选字段合法 → 门禁通过."""
    scheme = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce", "geometry": GEOMETRY_V11}
    passed, diag = _write_rag_artifacts(tmp_path, scheme)
    assert passed is True, f"{diag.get('error_code')}: {diag.get('error')}"


def test_rag_geometry_gate_missing_block(tmp_path):
    """scheme.geometry 缺失 → GEOMETRY_BLOCK_MISSING."""
    scheme = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce"}
    passed, diag = _write_rag_artifacts(tmp_path, scheme)
    assert passed is False and diag["error_code"] == "GEOMETRY_BLOCK_MISSING"


def test_rag_geometry_gate_incomplete(tmp_path):
    """硬值缺失 (anode.L_um) → GEOMETRY_DECLARATION_INCOMPLETE."""
    import json as _json
    geo = _json.loads(_json.dumps(GEOMETRY_V11))
    geo["anode"].pop("L_um")
    geo["provenance"].pop("anode.L_um")
    scheme = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce", "geometry": geo}
    passed, diag = _write_rag_artifacts(tmp_path, scheme)
    assert passed is False and diag["error_code"] == "GEOMETRY_DECLARATION_INCOMPLETE"


def test_rag_geometry_gate_rs_ds_missing(tmp_path):
    """R_s/D_s 与厚度/孔隙率同级硬性: 两极粒径/扩散系数缺失 → GEOMETRY_DECLARATION_INCOMPLETE."""
    import json as _json
    geo = _json.loads(_json.dumps(GEOMETRY_V11))
    for comp in ("cathode", "anode"):
        geo[comp].pop("R_s_um")
        geo[comp].pop("D_s_m2s")
        geo["provenance"].pop(f"{comp}.R_s_um")
        geo["provenance"].pop(f"{comp}.D_s_m2s")
    scheme = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce", "geometry": geo}
    passed, diag = _write_rag_artifacts(tmp_path, scheme)
    assert passed is False and diag["error_code"] == "GEOMETRY_DECLARATION_INCOMPLETE"
    joined = "; ".join(diag.get("observed", {}).get("missing", []))
    assert "cathode.R_s_um" in joined and "anode.D_s_m2s" in joined


def test_rag_geometry_gate_stage5_revision_accepted(tmp_path):
    """Stage 5 反馈修订后的几何 (provenance=stage5_revision) 在 Stage 4 重检时同样放行."""
    import json as _json
    geo = _json.loads(_json.dumps(GEOMETRY_V11))
    geo["cathode"]["L_um"] = 85.0
    geo["provenance"]["cathode.L_um"] = "stage5_revision"
    geo["anode"]["D_s_m2s"] = 5e-14
    geo["provenance"]["anode.D_s_m2s"] = "stage5_revision"
    scheme = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce", "geometry": geo}
    passed, diag = _write_rag_artifacts(tmp_path, scheme)
    assert passed is True, f"{diag.get('error_code')}: {diag.get('error')}"


def test_rag_geometry_gate_baseline_not_explicit(tmp_path):
    """厚度来自体系基准 (params_baseline) 而非显式声明 → 门禁拒绝."""
    import json as _json
    geo = _json.loads(_json.dumps(GEOMETRY_V11))
    geo["provenance"]["cathode.L_um"] = "params_baseline"
    scheme = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce", "geometry": geo}
    passed, diag = _write_rag_artifacts(tmp_path, scheme)
    assert passed is False and diag["error_code"] == "GEOMETRY_DECLARATION_INCOMPLETE"


def test_rag_geometry_gate_rs_out_of_registry_range(tmp_path):
    """粒径超出 registry input_ranges (pe R_s 上限 10.68 μm) → GEOMETRY_VALUE_ILLEGAL."""
    import json as _json
    geo = _json.loads(_json.dumps(GEOMETRY_V11))
    geo["cathode"]["R_s_um"] = 50.0
    scheme = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce", "geometry": geo}
    passed, diag = _write_rag_artifacts(tmp_path, scheme)
    assert passed is False and diag["error_code"] == "GEOMETRY_VALUE_ILLEGAL"


def test_rag_geometry_gate_illegal_value(tmp_path):
    """提供非法值 (负厚度/孔隙率越界) → GEOMETRY_VALUE_ILLEGAL."""
    import json as _json
    geo = _json.loads(_json.dumps(GEOMETRY_V11))
    geo["cathode"]["L_um"] = -10.0
    geo["cathode"]["porosity"] = 1.2
    scheme = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce", "geometry": geo}
    passed, diag = _write_rag_artifacts(tmp_path, scheme)
    assert passed is False and diag["error_code"] == "GEOMETRY_VALUE_ILLEGAL"


def test_rag_geometry_gate_warn_mode(tmp_path):
    """warn 灰度模式: 缺 geometry 不阻断, 但诊断带 warning_only 标记."""
    scheme = {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce"}
    passed, diag = _write_rag_artifacts(
        tmp_path, scheme,
        extra_config={"runtime_options": {"geometry_gate": "warn"}})
    assert passed is True and diag.get("warning_only") is True


def test_user_locked_electrolyte_and_modification_mitigation():
    """课题显式锁定碳酸酯电解液且带正极改性时，锁定位次不被覆盖，RelationEngine 放行并附审计注记；裸组合仍拦截。"""
    from lmllm.RAG.rag_pipeline import RAGPipeline
    from lmllm.RAG.agents import WriterAgent

    pipe = RAGPipeline.__new__(RAGPipeline)
    pipe.relation_engine = RelationEngine()

    question = (
        "双位点共掺杂改进无钴富锂 Li1.2Mn0.6Ni0.2O2 正极，评估 Zn–Na、Cu–Na 组合，"
        "匹配金属锂负极和 1 mol/L LiPF6/EC+DMC。"
    )
    draft_with_lhce = "### 电解液\n主推荐：局部高浓电解液 LHCE (LiFSI 3.0 M in EC/DMC + HFE/TTE)。"
    scheme = pipe._extract_scheme(question, {}, draft_text=draft_with_lhce)
    assert scheme is not None
    assert scheme["electrolyte"] == "carbonate_ec"
    assert "electrolyte" in scheme.get("user_locked_components", [])
    assert scheme.get("modifications")

    # 锁定基线 + 改性（无外部添加剂）放行并记录审计注记
    ev_locked = pipe.relation_engine.evaluate({
        "cathode": "LRMO",
        "anode": "li_metal",
        "electrolyte": "carbonate_ec",
        "user_locked_components": ["electrolyte"],
        "modifications": ["共掺杂", "zn-na"],
    })
    assert ev_locked["feasible"] is True
    assert not ev_locked["violations"]
    assert any("常规碳酸酯" in n["reason"] for n in ev_locked["audit_notes"])

    # 未锁定且无缓解的裸碳酸酯方案仍被 C1/C2/C3 拦截
    ev_bare_lrmo = pipe.relation_engine.evaluate({"cathode": "LRMO", "anode": "li_metal", "electrolyte": "carbonate_ec"})
    assert ev_bare_lrmo["feasible"] is False
    assert {v["rule_id"] for v in ev_bare_lrmo["violations"]} >= {"C1", "C3"}

    ev_bare_lnmo = pipe.relation_engine.evaluate({"cathode": "LNMO", "anode": "li_metal", "electrolyte": "carbonate_ec"})
    assert ev_bare_lnmo["feasible"] is False
    assert {v["rule_id"] for v in ev_bare_lnmo["violations"]} >= {"C1", "C2", "C3"}

    # WriterAgent.run 注入用户锁定电解液约束提示
    captured = {}

    class _StubLLM:
        available = True

        def chat(self, system, user, **kwargs):
            captured["user"] = user
            return "stub draft"

    writer = WriterAgent(_StubLLM(), relation_engine=pipe.relation_engine)
    writer.run(
        question=question,
        plan={"answer_outline": ["目标与设计路线"]},
        evidence=[{"passage_id": "p1", "text": "LRMO 掺杂文献", "source": "paper"}],
        scheme=scheme,
    )
    assert "【用户锁定电解液约束】" in captured["user"]
    assert "carbonate_ec" in captured["user"]


def test_li_metal_dense_geometry_contract_and_physical_validation(tmp_path):
    """li_metal 致密锂箔契约允许 porosity=0.0 与 null R_s/D_s，同时拒绝非物理压实密度与面载量。"""
    from auto_battery_research.simulation.geometry_contract import (
        build_geometry_contract, geometry_hash, validate_geometry_contract,
    )
    from auto_battery_research.tools.geometry_revision import collect_geometry_violations, resolve_input_ranges
    from pinn.input_spec import build_cell_spec

    scheme_li = {"cathode": "LRMO", "anode": "li_metal", "electrolyte": "lhce"}
    geo_li = build_geometry_contract({
        "cathode": {"L_um": 20.0, "porosity": 0.32, "R_s_um": 5.0, "D_s_m2s": None},
        "anode": {"L_um": 20.0, "porosity": None, "R_s_um": None, "D_s_m2s": None},
        "condition": {"v_min": 2.0, "v_max": 4.8, "c_rate": 1.0},
    }, scheme_li, [])
    assert geo_li["anode"]["porosity"] == 0.0
    assert geo_li["parameter_sources"]["anode.porosity"]["basis"] == "dense_metal_foil"
    assert geo_li["parameter_sources"]["anode.R_s_um"]["origin"] == "not_applicable"
    assert geo_li["parameter_sources"]["anode.D_s_m2s"]["origin"] == "not_applicable"
    assert validate_geometry_contract(geo_li, scheme_li, []) == []

    spec = build_cell_spec({**scheme_li, "geometry": geo_li}, {})
    assert spec["anode"]["epsilon"] == 0.0

    # v2.0 致密锂契约通过 RAGDesignChecker
    passed, diag = _write_rag_artifacts(
        tmp_path,
        {**scheme_li, "geometry": geo_li, "geometry_hash": geometry_hash(geo_li)},
    )
    assert passed is True, diag

    # 非 li_metal 负极 (si_base) 仍然禁止 porosity=0.0
    with pytest.raises(ValueError):
        build_geometry_contract({
            "cathode": {"L_um": 70.0, "porosity": 0.31, "R_s_um": 5.0, "D_s_m2s": None},
            "anode": {"L_um": 110.0, "porosity": 0.0, "R_s_um": 8.0, "D_s_m2s": None},
        }, {"cathode": "NCM811", "anode": "si_base", "electrolyte": "lhce"}, [])

    # 致密锂理论参数 (20 μm -> 1.068 mg/cm², 0.534 g/cm³) 合规
    ranges = resolve_input_ranges("LRMO", "li_metal")
    geo_phys = {
        "schema_version": "1.1",
        "cathode": {"L_um": 20.0, "porosity": 0.32, "R_s_um": 5.0, "D_s_m2s": 1e-14, "loading_mg_cm2": 5.5, "compaction_g_cm3": 2.8},
        "anode": {"L_um": 20.0, "porosity": 0.0, "R_s_um": None, "D_s_m2s": None, "loading_mg_cm2": 1.068, "compaction_g_cm3": 0.534},
        "provenance": {
            "cathode.L_um": "stage4_recipe", "cathode.porosity": "stage4_recipe",
            "cathode.R_s_um": "stage4_recipe", "cathode.D_s_m2s": "stage4_recipe",
            "anode.L_um": "stage4_recipe", "anode.porosity": "stage4_recipe",
        },
        "condition": {"v_min": 2.0, "v_max": 4.8, "c_rate": 1.0},
    }
    assert collect_geometry_violations(geo_phys, ranges, anode_id="li_metal") == []
    passed_phys, diag_phys = _write_rag_artifacts(tmp_path, {**scheme_li, "geometry": geo_phys})
    assert passed_phys is True, diag_phys

    # 非物理压实 2.8 g/cm³ 与虚高面载 5.5 mg/cm² 均被拦截为 GEOMETRY_VALUE_ILLEGAL
    geo_nonphys = json.loads(json.dumps(geo_phys))
    geo_nonphys["anode"]["compaction_g_cm3"] = 2.8
    geo_nonphys["anode"]["loading_mg_cm2"] = 5.5
    viols = collect_geometry_violations(geo_nonphys, ranges, anode_id="li_metal")
    assert any("compaction_g_cm3" in v for v in viols)
    assert any("loading_mg_cm2" in v for v in viols)
    passed_bad, diag_bad = _write_rag_artifacts(tmp_path, {**scheme_li, "geometry": geo_nonphys})
    assert passed_bad is False and diag_bad["error_code"] == "GEOMETRY_VALUE_ILLEGAL"

    # v2.0 契约下追加非物理压实同样应归类为 GEOMETRY_VALUE_ILLEGAL
    geo_v2_bad = json.loads(json.dumps(geo_li))
    geo_v2_bad["anode"]["compaction_g_cm3"] = 2.8
    passed_v2_bad, diag_v2_bad = _write_rag_artifacts(
        tmp_path,
        {**scheme_li, "geometry": geo_v2_bad, "geometry_hash": geometry_hash(geo_v2_bad)},
    )
    assert passed_v2_bad is False and diag_v2_bad["error_code"] == "GEOMETRY_VALUE_ILLEGAL"


def test_extract_geometry_inline_anchor_separation_and_li_metal_guard():
    """_extract_geometry 应在同节内按内联锚点分属正负极，忽略对比/匹配提及并跳过非物理锂压实。"""
    from lmllm.RAG.rag_pipeline import RAGPipeline

    text = (
        "### 门禁与工程参数\n"
        "- 正极单面涂敷厚度 **20.0 μm**，正极孔隙率 **32%**，正极面载量 **5.5 mg/cm²**，正极压实密度 **2.8 g/cm³**\n"
        "- 金属锂负极厚度 **20.0 μm**，负极孔隙率 **0%**，负极面载量 **1.068 mg/cm²**，负极压实密度 **2.8 g/cm³**\n"
        "- 匹配正极与对比负极的测试条件：电压窗口 **2.0–4.8 V**，测试倍率 **1.0 C**\n"
    )
    geo = RAGPipeline._extract_geometry(text)
    assert geo["cathode"]["thickness_um"] == pytest.approx(20.0)
    assert geo["cathode"]["porosity_pct"] == pytest.approx(32.0)
    assert geo["cathode"]["loading_mg_cm2"] == pytest.approx(5.5)
    assert geo["cathode"]["compaction_g_cm3"] == pytest.approx(2.8)
    assert geo["anode"]["thickness_um"] == pytest.approx(20.0)
    assert geo["anode"]["porosity_pct"] == pytest.approx(0.0)
    assert geo["anode"]["loading_mg_cm2"] == pytest.approx(1.068)
    assert "compaction_g_cm3" not in geo["anode"]


def test_component_aware_retrieval_and_metadata_normalization():
    """验证 RetrievalAgent、MultiRetrieval 与 RAGPipeline.retrieve_context(db_type='both') 的组件隔离、约束保留、路径规范化及同论文题名回填."""
    from types import SimpleNamespace
    from lmllm.RAG.agents import RetrievalAgent
    from lmllm.RAG.bm25_kb import BM25KnowledgeBase, normalize_source_path
    from lmllm.RAG.multi_retrieval import MultiRetrieval
    from lmllm.RAG.rag_pipeline import RAGPipeline
    from lmllm.RAG.structured_output import build_answer_markdown, format_evidence_display

    # 1. normalize_source_path 边界行为：仅剥离 database/ 或 papers/ 之前的前缀，保留无该目录的绝对/相对路径
    assert normalize_source_path("/home/ls/xiaoyue/LLM2/LMLLM/database/type/cathode/a.md") == "database/type/cathode/a.md"
    assert normalize_source_path(r"C:\work\repo\papers\merged\b.md") == "papers/merged/b.md"
    assert normalize_source_path("/old/10.1021_test.md") == "/old/10.1021_test.md"

    # 2. 构建含同论文空题名/有题名 sibling chunks、通用无组件段落及跨组件干扰段落的 BM25 知识库
    kb = BM25KnowledgeBase()
    cath_path_legacy = "/home/ls/xiaoyue/LLM2/LMLLM/database/type/cathode/10.1021_ncm.md"
    cath_path_clean = "database/type/cathode/10.1021_ncm.md"

    kb.add_passage(
        "p_cath_1",
        "NCM811 cathode delivers 210 mAh/g with surface coating stabilization.",
        source=cath_path_legacy,
        metadata={
            "passage_id": "p_cath_1",
            "source_file": cath_path_legacy,
            "source_paper": "10.1021/ncm",
            "title": "",
            "component": "cathode",
            "label": "电化学性能",
        },
    )
    kb.add_passage(
        "p_cath_2",
        "Single-crystal NCM811 cathode synthesis suppresses microcracking.",
        source=cath_path_clean,
        metadata={
            "passage_id": "p_cath_2",
            "source_file": cath_path_clean,
            "source_paper": "10.1021/ncm",
            "title": "Single-Crystal NCM811 Cathode Stabilization",
            "component": "cathode",
            "label": "材料制备",
        },
    )
    kb.add_passage(
        "p_generic",
        "Full-cell NCM811 high-energy design guidelines using LHCE electrolyte and LiNO3.",
        source=r"D:\repo\papers\merged\review_cell.md",
        metadata={
            "passage_id": "p_generic",
            "source_file": r"D:\repo\papers\merged\review_cell.md",
            "source_paper": "10.1016/review",
            "title": "High-Energy Lithium Battery Review",
            "label": "概述",
        },
    )
    kb.add_passage(
        "p_anode",
        "Graphite anode vs silicon anode slurry for high energy cathode cells.",
        source="/home/ls/xiaoyue/LLM2/LMLLM/database/type/anode/10.1002_graphite.md",
        metadata={
            "passage_id": "p_anode",
            "source_file": "/home/ls/xiaoyue/LLM2/LMLLM/database/type/anode/10.1002_graphite.md",
            "source_paper": "10.1002/graphite",
            "title": "Graphite Anode Study",
            "component": "anode",
            "label": "材料制备",
        },
    )
    kb.add_passage(
        "p_elec",
        "Carbonate electrolyte additive optimization for cathode stability.",
        source="/old/10.1021_test.md",
        metadata={
            "passage_id": "p_elec",
            "source_file": "/old/10.1021_test.md",
            "source_paper": "10.1021/test",
            "title": "Electrolyte Formulation",
            "component": "electrolyte",
            "label": "材料制备",
        },
    )
    kb.build_index()

    doc_cath_1 = SimpleNamespace(
        page_content="NCM811 cathode delivers 210 mAh/g with surface coating stabilization.",
        metadata={
            "passage_id": "p_cath_1",
            "source_file": cath_path_legacy,
            "source_paper": "10.1021/ncm",
            "title": "",
            "component": "cathode",
        },
    )
    doc_cath_2 = SimpleNamespace(
        page_content="Single-crystal NCM811 cathode synthesis suppresses microcracking.",
        metadata={
            "passage_id": "p_cath_2",
            "source_file": cath_path_clean,
            "source_paper": "10.1021/ncm",
            "title": "Single-Crystal NCM811 Cathode Stabilization",
            "component": "cathode",
        },
    )
    doc_generic = SimpleNamespace(
        page_content="Full-cell NCM811 high-energy design guidelines using LHCE electrolyte and LiNO3.",
        metadata={
            "passage_id": "p_generic",
            "source_file": r"D:\repo\papers\merged\review_cell.md",
            "source_paper": "10.1016/review",
            "title": "High-Energy Lithium Battery Review",
        },
    )
    doc_anode = SimpleNamespace(
        page_content="Graphite anode vs silicon anode slurry for high energy cathode cells.",
        metadata={
            "passage_id": "p_anode",
            "source_file": "/home/ls/xiaoyue/LLM2/LMLLM/database/type/anode/10.1002_graphite.md",
            "source_paper": "10.1002/graphite",
            "title": "Graphite Anode Study",
            "component": "anode",
        },
    )
    doc_elec = SimpleNamespace(
        page_content="Carbonate electrolyte additive optimization for cathode stability.",
        metadata={
            "passage_id": "p_elec",
            "source_file": "/old/10.1021_test.md",
            "source_paper": "10.1021/test",
            "title": "Electrolyte Formulation",
            "component": "electrolyte",
        },
    )

    # 3. 测试不接受 filter 参数的 mock vector_store（触发 TypeError 回退 + 后过滤）
    class _NoFilterVectorStore:
        def similarity_search_with_score(self, query, k=10):
            return [(doc_anode, 0.05), (doc_cath_1, 0.1), (doc_generic, 0.15), (doc_cath_2, 0.2)]

    agent_nofilt = RetrievalAgent(kb=kb, vector_store=_NoFilterVectorStore(), relation_engine=RelationEngine())
    single_hits = agent_nofilt._run_single_query(
        query="NCM811 cathode stabilization",
        component="cathode",
        expected_labels=["电化学性能"],
        top_k=5,
        question="设计 420 Wh/kg NCM811 正极电池",
    )
    single_pids = [h["passage_id"] for h in single_hits]
    assert "p_cath_1" in single_pids and "p_generic" in single_pids
    assert "p_anode" not in single_pids and "p_elec" not in single_pids
    cath1_hit = next(h for h in single_hits if h["passage_id"] == "p_cath_1")
    assert cath1_hit["source"] == cath_path_clean
    assert cath1_hit["title"] == "Single-Crystal NCM811 Cathode Stabilization"

    # 合并时不以空 title 覆盖非空 title
    merged_bucket = {"p_cath_1": dict(cath1_hit, score=0.4)}
    agent_nofilt._merge_entry_keep_title(
        merged_bucket,
        "p_cath_1",
        {"passage_id": "p_cath_1", "title": "", "score": 0.9, "metadata": {"title": "", "source_paper": "10.1021/ncm"}},
        prefer_higher_score=True,
    )
    assert merged_bucket["p_cath_1"]["title"] == "Single-Crystal NCM811 Cathode Stabilization"

    # 4. 测试支持 filter 的 mock vector_store + Round 2 跨组件降权 + MultiRetrieval 组件过滤
    class _FilterVectorStore:
        def similarity_search_with_score(self, query, k=10, filter=None):
            all_docs = [(doc_anode, 0.05), (doc_elec, 0.08), (doc_cath_1, 0.1), (doc_generic, 0.15), (doc_cath_2, 0.2)]
            if not filter:
                return all_docs
            comp = None
            if "component" in filter:
                comp = filter["component"]
            elif "$and" in filter:
                for part in filter["$and"]:
                    if "component" in part:
                        comp = part["component"]
            if comp:
                return [(d, s) for d, s in all_docs if d.metadata.get("component") == comp]
            return all_docs

    agent_filt = RetrievalAgent(kb=kb, vector_store=_FilterVectorStore(), relation_engine=RelationEngine())
    mr = MultiRetrieval.__new__(MultiRetrieval)
    mr.lit_store = _FilterVectorStore()
    mr.lit_bm25 = kb
    mr.ebook_store = None
    mr.ebook_bm25 = None
    mr._ebook_available = True
    mr.lit_weight = 0.7
    mr.ebook_weight = 0.3
    mr.search_ebook = lambda q, top_k=10: [
        {
            "passage_id": "ebk_1",
            "source": "/home/ls/xiaoyue/LLM2/LMLLM/papers/merged/electrochem_book.md",
            "title": "Electrochemical Principles",
            "score": 0.5,
            "text": "Solid-state diffusion governs rate capability.",
            "metadata": {"source_file": "/home/ls/xiaoyue/LLM2/LMLLM/papers/merged/electrochem_book.md"},
        }
    ]

    # MultiRetrieval.search_literature / search 也应保持组件隔离与路径/题名规范化
    mr_hits = mr.search("NCM811 cathode capacity", top_k_lit=10, top_k_ebook=5, top_k_final=10, component="cathode")
    mr_pids = [h["passage_id"] for h in mr_hits]
    assert "p_cath_1" in mr_pids and "p_cath_2" in mr_pids
    assert "p_anode" not in mr_pids and "p_elec" not in mr_pids
    mr_cath1 = next(h for h in mr_hits if h["passage_id"] == "p_cath_1")
    assert mr_cath1["source"] == cath_path_clean
    assert mr_cath1["title"] == "Single-Crystal NCM811 Cathode Stabilization"

    # 不支持 filter 的 MultiRetrieval 同样生效
    mr_nofilt = MultiRetrieval.__new__(MultiRetrieval)
    mr_nofilt.lit_store = _NoFilterVectorStore()
    mr_nofilt.lit_bm25 = kb
    mr_nofilt.ebook_store = None
    mr_nofilt.ebook_bm25 = None
    mr_nofilt._ebook_available = False
    mr_nofilt.lit_weight = 0.7
    mr_nofilt.ebook_weight = 0.3
    mr_nf_hits = mr_nofilt.search_literature("NCM811 cathode", top_k=10, component="cathode")
    assert all((h.get("metadata") or {}).get("component") in (None, "", "cathode") for h in mr_nf_hits)

    # 5. 测试 RAGPipeline.retrieve_context(db_type="both", focus_component="cathode")
    pipe = RAGPipeline.__new__(RAGPipeline)
    pipe.retriever = agent_filt
    pipe.multi_retriever = mr
    pipe.relation_engine = agent_filt.relation_engine

    ctx_both = pipe.retrieve_context(
        db_type="both",
        question="设计 420 Wh/kg NCM811 正极与金属锂电池",
        queries=["NCM811 cathode stabilization", "NCM811 high energy design"],
        focus_component="cathode",
        top_k=10,
    )
    assert ctx_both["db_type"] == "both"
    assert ctx_both.get("constraint_log")
    both_results = ctx_both["results"]
    by_pid = {r["passage_id"]: r for r in both_results}
    assert "p_cath_1" in by_pid and "p_cath_2" in by_pid and "p_generic" in by_pid and "ebk_1" in by_pid
    # Round 2 跨组件条目即便兜底召回也显著低于主组件及无标签通用段落
    if "p_elec" in by_pid:
        assert by_pid["p_elec"]["score"] < by_pid["p_cath_1"]["score"]
    if "p_anode" in by_pid:
        assert by_pid["p_anode"]["score"] < by_pid["p_cath_1"]["score"]
    assert by_pid["p_cath_1"]["source"] == cath_path_clean
    assert by_pid["p_cath_1"]["title"] == "Single-Crystal NCM811 Cathode Stabilization"
    assert by_pid["p_generic"]["source"] == "papers/merged/review_cell.md"
    assert by_pid["ebk_1"]["source"] == "papers/merged/electrochem_book.md"

    # 6. structured_output 展示输出同样规范化路径并回填空题名
    md_out = build_answer_markdown("NCM811 正极筛选", "推荐单晶 NCM811 [p_cath_1]。", {}, both_results)
    disp_out = format_evidence_display(ctx_both)
    assert "/home/ls/xiaoyue" not in md_out and "/home/ls/xiaoyue" not in disp_out
    assert "Single-Crystal NCM811 Cathode Stabilization" in md_out
    assert "Single-Crystal NCM811 Cathode Stabilization" in disp_out

    # 7. 半电池锂金属对电极不翻转组件、正极化学式识别与 Chroma sibling chunk 题名回填
    q_half_cell = "CR2032锂金属半电池中Li1.2Mn0.6Ni0.2O2极片配方、首圈库伦效率、0.1C活化、1C循环100周"
    assert agent_filt._detect_component(q_half_cell) == "cathode"
    assert not agent_filt._has_component_keyword(q_half_cell.lower(), "anode")
    assert agent_filt._detect_component("高容量硅碳负极与锂金属负极界面调控") == "anode"

    q4_res = agent_filt.run(
        question="双位点共掺杂改进无钴富锂 Li1.2Mn0.6Ni0.2O2 正极",
        queries=[q_half_cell],
        top_k_per_query=5,
    )
    round1_pids = [h["passage_id"] for h in q4_res["search_logs"][0]["hits"]]
    assert "p_anode" not in round1_pids
    q4_by_pid = {r["passage_id"]: r for r in q4_res["results"]}
    if "p_anode" in q4_by_pid:
        assert q4_by_pid["p_anode"]["score"] < q4_by_pid["p_cath_1"]["score"]

    # Chroma sibling chunk 回填：当 kb.paper_title_map 缺某篇 DOI 题名时，通过 vector_store._collection.get 回填
    class _CollectionTitleLookupVectorStore:
        class _FakeCollection:
            def get(self, where=None, include=None, limit=None):
                if where == {"source_paper": "10.1016/j.jcis.2024.02.213"}:
                    return {
                        "metadatas": [
                            {"source_paper": "10.1016/j.jcis.2024.02.213", "title": ""},
                            {
                                "source_paper": "10.1016/j.jcis.2024.02.213",
                                "title": "A multifunctional dual cation doping strategy to stabilize high-voltage cathode",
                            },
                        ]
                    }
                return {"metadatas": []}

        def __init__(self):
            self._collection = self._FakeCollection()

    agent_title = RetrievalAgent(kb=kb, vector_store=_CollectionTitleLookupVectorStore(), relation_engine=None)
    untitled_doc = SimpleNamespace(
        page_content="Dual cation doping suppresses oxygen release.",
        metadata={"passage_id": "p_jcis", "source_paper": "10.1016/j.jcis.2024.02.213", "title": ""},
    )
    resolved_titles = agent_title._build_title_map([(untitled_doc, 0.1)], [])
    assert (
        resolved_titles.get("10.1016/j.jcis.2024.02.213")
        == "A multifunctional dual cation doping strategy to stabilize high-voltage cathode"
    )

    # 8. set_stage_journal 在阶段 PASSED 时清理先前误写的工具授权缺口阻塞日志
    import tempfile
    from auto_battery_research.workflow.stage_manager import StageManager

    with tempfile.TemporaryDirectory() as tmpdir:
        jmgr = StageManager(workspace_root=tmpdir, target_goal="测试日志清洗")
        jmgr.set_stage_journal(
            6,
            notes="Stage 6 执行受阻记录（工具授权缺口）：SynthesizeResearchReport 未挂载",
            key_findings={"status": "BLOCKED", "error": "tool_missing"},
        )
        res_clean = jmgr.set_stage_journal(
            6,
            notes="Stage 6 (综合研发报告生成) 顺利完成，通过确定性门禁质检 (耗时: 12.3s)",
            key_findings={"status": "PASSED", "duration_seconds": 12.3},
        )
        recorded = res_clean["recorded_entry"]
        assert "工具授权缺口" not in recorded["notes"]
        assert recorded["key_findings"]["status"] == "PASSED"
        assert "error" not in recorded["key_findings"]


def test_final_report_checker_passes_with_open_review_caveats_clarified(tmp_path):
    """Fix 6: Stage 4/6 门禁 PASSED 与审稿保留意见并存时应明确区分契约通过与实验室闭环验证。"""
    from types import SimpleNamespace
    from auto_battery_research.simulation import build_cell_spec, run_pinn_discharge
    from auto_battery_research.simulation.geometry_contract import build_geometry_contract, geometry_hash
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.util.hashing import canonical_hash, stage5_scheme_projection

    s = {
        "cathode": "NCM811",
        "anode": "si_base",
        "electrolyte": "high_concentration",
        "design_revision": 1,
        "user_locked_components": ["cathode", "anode", "electrolyte"],
        "additives": ["FEC"],
    }
    cond = {
        "cathode": {"L_um": 170.0, "porosity": 0.15, "R_s_um": 2.7, "D_s_m2s": None},
        "anode": {"L_um": 145.0, "porosity": 0.18, "R_s_um": 4.11, "D_s_m2s": None},
        "condition": {"v_min": 2.5, "v_max": 4.2, "c_rate": 0.5},
    }
    s["geometry"] = build_geometry_contract(cond, s, [])
    s["geometry_hash"] = geometry_hash(s["geometry"])
    spec = {"cell_spec": build_cell_spec(s, {}), "scheme": stage5_scheme_projection(s)}
    spec["cell_spec_hash"] = canonical_hash(spec["cell_spec"])
    result = run_pinn_discharge(spec["cell_spec"], scheme=s)
    result["pinn"].update(spec_hash=spec["cell_spec_hash"], scheme_hash=canonical_hash(spec["scheme"]), run_id="r_caveat")

    doc = {
        "target": "测试350Wh/kg电池",
        "review_status": "REVIEWED_WITH_ISSUES",
        "scheme": s,
        "evidence": [{"passage_id": "p1", "source": "paper", "text": "NCM811"}],
        "reviewer_output": {"issues": ["尚缺 45 °C 高温长循环产气与首效实验验证"]},
    }
    for filename, obj in (("design_scheme.json", doc), ("pinn_input_spec.json", spec), ("pinn_simulation_result.json", result)):
        (tmp_path / filename).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "design_scheme.md").write_text("完整设计背景", encoding="utf-8")
    (tmp_path / "stage_journals.json").write_text('[{"stage_id": 5, "notes": "done"}]', encoding="utf-8")
    stages = [SimpleNamespace(id=i, status="PASSED", skip=False, skip_reason="", allow_skip=False) for i in range(1, 7)]
    mgr = SimpleNamespace(
        config={"paths": {"output_dir": str(tmp_path)}},
        root_dir=tmp_path,
        target_goal=doc["target"],
        stages=stages,
        is_legacy_task=False,
        get_task_output_dir=lambda *a, **kw: tmp_path,
        get_stage_by_id=lambda i: stages[i - 1],
        get_all_stage_journal=lambda: [{"stage_id": 5, "notes": "done", "duration_seconds": 5}],
    )

    out = run_synthesis_report(target_query=mgr.target_goal, stage_manager=mgr)
    assert out["success"]
    report_text = (tmp_path / "final_research_report.md").read_text(encoding="utf-8")
    assert "尚未解决的审核意见（不代表审核通过）" not in report_text
    assert "**审稿保留意见（非流程阻塞项；需实验室阶段闭环验证）**" in report_text
    assert "非性能/实验定论" in report_text
    assert "REVIEWED_WITH_ISSUES" in report_text

    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    ok, diag = checker.do_check(is_complete=True)
    assert ok is True, diag
    assert diag["observed"]["review_status"] == "REVIEWED_WITH_ISSUES"
    assert diag["observed"]["open_review_caveats"] == 1
    assert "不等同于科学审稿保留项或实验室实证闭环" in diag["expected"]


def test_final_report_checker_rejects_and_sanitizer_cleans_false_pinn_journal(tmp_path):
    """PINN 未触发且无仿真结果时，stage_journals 不得宣称已完成放电仿真或交付 pinn_simulation_result.json。"""
    import json
    from types import SimpleNamespace
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    from auto_battery_research.tools.workflow_actions import sanitize_stage_journals_for_pinn_state

    (tmp_path / "pinn_input_spec.json").write_text(
        json.dumps({"trigger": {"enabled": True, "triggered": False, "reasons": ["lrmo_not_in_pinn_registry"]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    filler = "本研究围绕高容量无钴富锂正极的双位点共掺杂改性与界面稳定性验证展开实验设计。" * 10
    (tmp_path / "final_research_report.md").write_text(
        f"## 1. 研发摘要\n{filler}\n## 2. 材料选择与文献依据\n{filler}\n## 3. 最终设计方案\n{filler}\n"
        f"## 4. 仿真与优化分析\n{filler}\n## 5. 实验路线与验证计划\n{filler}\n## 6. 参考来源附录\n{filler}\n",
        encoding="utf-8",
    )
    false_journals = [
        {
            "stage_id": 5,
            "status": "SKIPPED",
            "notes": "跳过 Stage 5；完成放电仿真校验，指标已核验",
            "key_findings": {"status": "SKIPPED", "note": "未执行放电仿真"},
            "deliverables": ["pinn_input_spec.json", "pinn_simulation_result.json"],
        },
        {
            "stage_id": 6,
            "status": "PASSED",
            "notes": "汇总前序阶段输出并完成放电仿真校验，依据 Stage5 SPM PINN 放电仿真校验生成报告",
            "key_findings": {"status": "PASSED", "sim_note": "已完成 SPM PINN 放电仿真校验"},
            "deliverables": ["final_research_report.md"],
        },
    ]
    (tmp_path / "stage_journals.json").write_text(
        json.dumps(false_journals, ensure_ascii=False), encoding="utf-8"
    )
    stages = [SimpleNamespace(id=i, status="PASSED", skip=False, skip_reason="", allow_skip=False) for i in range(1, 7)]
    mgr = SimpleNamespace(
        config={"paths": {"output_dir": str(tmp_path)}},
        root_dir=tmp_path,
        target_goal="LRMO 测试",
        stages=stages,
        is_legacy_task=False,
        get_task_output_dir=lambda *a, **kw: tmp_path,
        get_stage_by_id=lambda i: stages[i - 1],
    )
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)

    ok, diag = checker.do_check(is_complete=True)
    assert ok is False
    assert diag["error_code"] == "STAGE_JOURNAL_PINN_MISMATCH"

    # 仅有 notes 中的 "Stage5 SPM PINN 放电仿真校验" 与 dict key_findings 时也不得抛出 TypeError，且必须精准拦截
    (tmp_path / "stage_journals.json").write_text(
        json.dumps(
            [
                {
                    "stage_id": 6,
                    "status": "PASSED",
                    "notes": "依据 Stage5 SPM PINN 放电仿真校验整理正文",
                    "key_findings": {"status": "PASSED", "note": "常规校验"},
                    "deliverables": ["final_research_report.md"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ok2, diag2 = checker.do_check(is_complete=True)
    assert ok2 is False
    assert diag2["error_code"] == "STAGE_JOURNAL_PINN_MISMATCH"

    # 恢复包含 deliverables + notes + dict key_findings 的完整脏日志，验证 sanitize_stage_journals_for_pinn_state 一次性清洗并放行
    (tmp_path / "stage_journals.json").write_text(
        json.dumps(false_journals, ensure_ascii=False), encoding="utf-8"
    )
    cleaned = sanitize_stage_journals_for_pinn_state(tmp_path, false_journals)
    assert "pinn_simulation_result.json" not in cleaned[0]["deliverables"]
    assert "未执行" in cleaned[0]["notes"]
    assert "未执行" in cleaned[1]["notes"]
    assert "未执行" in cleaned[1]["key_findings"]["sim_note"]
    ok3, diag3 = checker.do_check(is_complete=True)
    assert ok3 is True, diag3

    # 即使 stage_journals.json 已干净，stage_journals.md 交付物栏若仍虚列 pinn_simulation_result.json 亦应被 checker 拦截并在清洗时独立修复，同时保留正文“不产生 pinn_simulation_result.json”
    md_dirty = (
        "# 课题研发阶段日志与审计表 (Task Stage Journal)\n\n"
        "| Stage | 阶段名称 | 状态 | 耗时 | 交付物 | 关键发现与所得 (Key Findings) |\n"
        "|:---|:---|:---:|:---:|:---|:---|\n"
        "| Stage 5 | PINN 物理仿真校验 | 完成 | 17.2s | `pinn_input_spec.json, pinn_simulation_result.json` | 未运行 SPM PINN 放电仿真，故本轮不产生 pinn_simulation_result.json，也不触发 ReviseGeometry。 |\n"
    )
    (tmp_path / "stage_journals.md").write_text(md_dirty, encoding="utf-8")
    ok4, diag4 = checker.do_check(is_complete=True)
    assert ok4 is False
    assert diag4["error_code"] == "STAGE_JOURNAL_PINN_MISMATCH"

    sanitize_stage_journals_for_pinn_state(tmp_path)
    cleaned_md = (tmp_path / "stage_journals.md").read_text(encoding="utf-8")
    assert "| `pinn_input_spec.json` |" in cleaned_md
    assert "pinn_input_spec.json, pinn_simulation_result.json" not in cleaned_md
    assert "故本轮不产生 pinn_simulation_result.json" in cleaned_md
    ok5, diag5 = checker.do_check(is_complete=True)
    assert ok5 is True, diag5




