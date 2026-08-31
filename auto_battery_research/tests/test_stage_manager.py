"""Unit tests for StageManager lifecycle and skip mechanisms in auto_battery_research."""

import pytest
from pathlib import Path
from auto_battery_research.workflow.stage_manager import StageManager


def test_stage_manager_init():
    mgr = StageManager()
    assert len(mgr.stages) == 6
    assert mgr.stages[0].key == "literature_ingestion"
    assert mgr.stages[4].key == "pinn_physics_simulation"
    assert mgr.stages[4].skip is True  # Stage 5 默认必须为 skip


def test_stage_skip_override():
    # 测试 CLI 覆盖 PINN skip 为 False
    mgr = StageManager(skip_pinn=False)
    assert mgr.stages[4].skip is False

    # 测试动态跳过与激活
    mgr.set_stage_skip(5, skip=True, reason="测试跳过")
    assert mgr.stages[4].skip is True
    assert mgr.stages[4].status == "SKIPPED"

    mgr.set_stage_skip(5, skip=False)
    assert mgr.stages[4].skip is False


def test_stage_journal():
    mgr = StageManager()
    res = mgr.set_stage_journal(
        stage_id=1,
        notes="Stage 1 测试完成",
        deliverables=["database/type/"],
        key_findings={"papers_count": 10},
    )
    assert res["status"] == "success"

    journals = mgr.get_all_stage_journal()
    assert len(journals) >= 1
    assert any(j["stage_id"] == 1 for j in journals)


def test_stage_check_and_status():
    mgr = StageManager()
    status = mgr.get_status()
    assert "mission_name" in status
    assert status["current_stage_id"] >= 1
    assert "progress" in status


def test_stage_ordering_enforcement():
    """测试状态机严格顺序执行，禁止越级推进未来的阶段."""
    mgr = StageManager()
    mgr.reset_workflow()
    assert mgr.get_current_stage().id == 1

    # 尝试直接完成 Stage 4 (应当被拦截)
    ok, res = mgr.complete_stage(stage_id=4)
    assert ok is False
    assert "无法跨阶段推进" in res.get("error", "")


def test_stage_skip_restrictions():
    """测试仅允许跳过支持 skip 的阶段，核心阶段禁止跳过."""
    mgr = StageManager()
    # 尝试跳过 Stage 1、4、6 (核心必跑阶段)
    assert mgr.set_stage_skip(1, skip=True) is False
    assert mgr.set_stage_skip(4, skip=True) is False
    assert mgr.set_stage_skip(6, skip=True) is False

    # Stage 5 支持跳过
    assert mgr.set_stage_skip(5, skip=True) is True


def test_checker_strict_semantics():
    """测试 non-strict (strict=False) 检查器失败不阻断阶段推进."""
    from auto_battery_research.checkers.base_checker import BaseChecker
    from auto_battery_research.stage.base_stage import BaseStage

    class DummyWarningChecker(BaseChecker):
        def do_check(self, is_complete=False, **kwargs):
            return False, self.build_diagnostic(passed=False, error_code="WARN", error_msg="Warning only")

    class DummyPassingChecker(BaseChecker):
        def do_check(self, is_complete=False, **kwargs):
            return True, self.build_diagnostic(passed=True)

    mgr = StageManager()
    warn_checker = DummyWarningChecker(name="WarnCheck", strict=False)
    pass_checker = DummyPassingChecker(name="PassCheck", strict=True)
    warn_checker.on_init(stage_manager=mgr, stage_info={}, config={})
    pass_checker.on_init(stage_manager=mgr, stage_info={}, config={})

    stage = BaseStage({"id": 99, "name": "TestStage", "allow_skip": False}, [pass_checker, warn_checker])
    mgr.stages.append(stage)

    passed, res = mgr.check_stage(stage_id=99)
    assert passed is True  # strict=False 失败不影响 all_passed
    assert len(res["diagnostics"]) == 2
    assert res["diagnostics"][1].get("warning_only") is True


def test_dynamic_synthesis_report_generation(tmp_path):
    """测试综合研报生成时，准确动态反映 Stage 5 跳过与各阶段真实状态."""
    from auto_battery_research.tools.workflow_actions import run_generate_synthesis_report
    
    mgr = StageManager(skip_pinn=True, target_goal="测试动态研报生成")
    res = run_generate_synthesis_report(target_query="测试动态研报生成", stage_manager=mgr)
    assert res["success"] is True
    
    report_text = Path(res["report_file"]).read_text(encoding="utf-8")
    assert "Stage 5 (PINN 物理仿真): SKIPPED" in report_text
    assert "必检阶段门禁全部通过" in report_text or "全流程" in report_text


def test_stage_manager_atomic_journal_concurrency(tmp_path):
    """测试高并发下 StageManager 日志原子写入安全无冲突."""
    import threading
    mgr = StageManager(target_goal="并发日志写入压力测试")
    
    errors = []
    def worker(stage_id):
        try:
            mgr.set_stage_journal(
                stage_id=stage_id,
                notes=f"Concurrent note from thread {stage_id}",
                deliverables=[f"file_{stage_id}.json"],
                key_findings={"thread_id": stage_id},
            )
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, 6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    all_j = mgr.get_all_stage_journal()
    assert len(all_j) >= 1


