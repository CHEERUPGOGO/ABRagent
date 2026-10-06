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
        "scheme": {"cathode": "SC-NCM90", "anode": "li_metal", "electrolyte": "lhce"},
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
    assert ev_c8["feasible"] is False
    assert any(r["rule_id"] == "C8" for r in ev_c8["rejects"])





