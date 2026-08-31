"""Unit tests for AutonomousLoopRunner end-to-end execution in auto_battery_research."""

import json
import pytest
from unittest.mock import patch
from auto_battery_research.workflow.stage_manager import StageManager
from auto_battery_research.backend.loop_runner import AutonomousLoopRunner


@pytest.mark.unit
def test_autonomous_loop_runner_dry_run(monkeypatch):
    """纯离线确定性单元测试：通过本地 Mock 快速验证全阶段状态机自主闭环推进."""
    mgr = StageManager(skip_pinn=True, target_goal="测试400Wh/kg锂金属电池设计")
    mgr.reset_workflow()

    def fake_rag_design(target_query: str = "", **kwargs):
        task_dir = mgr.get_task_output_dir(target_query)
        task_dir.mkdir(parents=True, exist_ok=True)
        
        md_file = task_dir / "design_scheme.md"
        json_file = task_dir / "design_scheme.json"
        
        md_file.write_text("""# 锂金属电池设计方案与工程规范

## 1. 目标与设计路线
本设计方案针对 400 Wh/kg 超高能量密度液态锂金属电池展开研发。路线采用高镍单晶正极与超薄锂金属负极搭配局部高浓电解液体系，实现宽温区稳定循环与高能量释放。

## 2. 推荐材料组合与微观配比
正极推荐采用单晶 NCM811 (LiNi0.8Co0.1Mn0.1O2) 材料；负极推荐采用超薄锂箔 (Li metal anode, 厚度 30~50 um)；电解液推荐采用高抗氧化、低氟化腐蚀的 LHCE 局部高浓度电解液 (1.2M LiFSI in DME/TTE) 配方。

## 3. 预期关键指标与热力学核算
预期比容量达到 220.0 mAh/g (0.1 C 放电)，平均放电平台电压为 3.75 V，单体电芯级能量密度稳定达到 400.0 Wh/kg，库仑效率大于 99.5%。

## 4. 可行性依据与文献证据链
文献 DOI:10.1038/s41467-024-54637-9 证实单晶 NCM811 在局部高浓度电解液下界面副反应显著降低，且与锂金属界面具有极佳的界面稳定性，实测首次库仑效率超过 90%。

## 5. 风险与数据缺口分析
潜在风险包括锂枝晶生长与高温界面阻抗上升。建议在后续工程化放大中增加微孔固态复合涂层隔膜并加强界面钝化监控。
""", encoding="utf-8")

        json_file.write_text(json.dumps({
            "scheme": {"cathode": "NCM811", "anode": "Li_metal", "electrolyte": "LHCE"},
            "evidence": [
                {
                    "passage_id": "p001",
                    "source": "10.1038/s41467-024-54637-9",
                    "text": "NCM811 cathode with Li metal anode and LHCE electrolyte achieves 400 Wh/kg and 220 mAh/g at 0.1C.",
                }
            ],
            "confidence": "high",
            "rule_checks": {"violations": [], "rejects": [], "energy_check": "passed"},
        }), encoding="utf-8")

        return {
            "success": True,
            "message": "Fake RAG Design Generated",
            "journal_notes": "生成真实测试方案",
            "deliverables": [str(md_file), str(json_file)],
            "key_findings": {"confidence": "high"},
        }

    def fake_literature_ingestion(**kwargs):
        return {
            "success": True,
            "message": "Fake literature ingestion (Mock)",
            "journal_notes": "文献资产就绪 (Mock，不触发真实 MinerU 解析)",
            "deliverables": ["database/type/"],
            "key_findings": {"total_md_papers": 17, "new_pdfs_detected": 0},
        }

    monkeypatch.setattr("auto_battery_research.tools.workflow_actions.run_literature_ingestion", fake_literature_ingestion)

    monkeypatch.setattr("auto_battery_research.tools.workflow_actions.run_rag_design", fake_rag_design)

    runner = AutonomousLoopRunner(manager=mgr, goal="测试400Wh/kg锂金属电池设计", max_stage_retries=1)
    res = runner.run()

    assert res["success"] is True
    assert "report_file" in res
    assert mgr.is_all_completed() is True


@pytest.mark.external
@pytest.mark.slow
def test_autonomous_loop_runner_live_external():
    """需要真实模型 API / 外部知识库的端到端大模型 RAG 闭环测试."""
    mgr = StageManager(skip_pinn=True, target_goal="测试400Wh/kg锂金属电池设计")
    mgr.reset_workflow()

    runner = AutonomousLoopRunner(manager=mgr, goal="测试400Wh/kg锂金属电池设计", max_stage_retries=1)
    res = runner.run()

    assert res["success"] is True
    assert "report_file" in res
    assert mgr.is_all_completed() is True

