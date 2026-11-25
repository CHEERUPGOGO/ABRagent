"""研报分文件发布：生成、重复编译、错版拦截与三文件失败回滚。"""
import json
import re
from types import SimpleNamespace

import pytest

from auto_battery_research.checkers.final_report_checker import FinalReportChecker
from auto_battery_research.tools.workflow_actions import run_synthesis_report
from auto_battery_research.util import report_bundle as bundle, report_record as records


def make_report(tmp_path, with_record, monkeypatch):
    if with_record:
        from auto_battery_research.tests.test_report_record import setup
        doc, _ = setup(tmp_path)
        (tmp_path / "stage_journals.json").write_text('[{"stage_id":5,"notes":"离线核对"}]', encoding="utf-8")
        stages = [SimpleNamespace(id=i, status="PASSED", skip=False, allow_skip=False) for i in range(1, 7)]
        mgr = SimpleNamespace(config={"paths": {"output_dir": str(tmp_path)}}, root_dir=tmp_path,
                              target_goal=doc["target"], stages=stages, is_legacy_task=False,
                              get_task_output_dir=lambda *a: tmp_path, get_stage_by_id=lambda i: stages[i - 1],
                              get_all_stage_journal=lambda: [{"stage_id": 5, "notes": "离线核对"}])
    else:
        from auto_battery_research.tests.test_final_design_reporting import report_fixture
        mgr, _, _, _ = report_fixture(tmp_path)
    monkeypatch.setattr("auto_battery_research.util.report_recipe_llm._llm_client", lambda *a: None)
    result = run_synthesis_report(stage_manager=mgr)
    assert result["success"], result
    checker = FinalReportChecker()
    checker.on_init(mgr, {"id": 6}, mgr.config)
    passed, diagnostic = checker.do_check(is_complete=True)
    assert passed, diagnostic
    return mgr, checker, result


@pytest.mark.parametrize("with_record", [False, True])
def test_regeneration_keeps_supplement_out_of_main(tmp_path, monkeypatch, with_record):
    mgr, checker, result = make_report(tmp_path, with_record, monkeypatch)
    original_inputs = {name: (tmp_path / name).read_bytes() for name in records.INPUTS if (tmp_path / name).exists()}
    for _ in range(2):
        main = (tmp_path / bundle.MAIN_FILENAME).read_text(encoding="utf-8")
        supplement = bundle.load_supplement(tmp_path / bundle.MAIN_FILENAME, main)
        assert main.split("## 6. 参考文献\n\n")[1].strip()
        assert "阶段执行履历" not in main and "### 阶段执行履历" in supplement
        assert not re.search(r"见[^。\n]{0,30}(?:附录|补充材料)|supplement\.md", main)
        assert set(result["deliverables"]) == {str(tmp_path / name) for name in (bundle.MAIN_FILENAME, bundle.SUPPLEMENT_FILENAME)}
        if with_record:
            record = json.loads((tmp_path / records.FILENAME).read_text(encoding="utf-8"))
            assert records.marker(record) in main and records.marker(record) in supplement
        result = run_synthesis_report(stage_manager=mgr)
        assert result["success"] and checker.do_check(is_complete=True)[0]
    assert original_inputs == {name: (tmp_path / name).read_bytes() for name in original_inputs}


@pytest.mark.parametrize("with_record", [False, True])
@pytest.mark.parametrize("tamper", ["missing", "stale", "journal", "tail", "move_core"])
def test_split_gate_rejects_missing_mixed_or_misplaced_content(tmp_path, monkeypatch, with_record, tamper):
    _, checker, _ = make_report(tmp_path, with_record, monkeypatch)
    path, sidecar = tmp_path / bundle.MAIN_FILENAME, tmp_path / bundle.SUPPLEMENT_FILENAME
    main, supplement = path.read_text(encoding="utf-8"), sidecar.read_text(encoding="utf-8")
    if tamper == "missing":
        sidecar.unlink()
    elif tamper == "stale":
        sidecar.write_text(re.sub(r"abr-report-identity:[^ ]+", "abr-report-identity:old", supplement), encoding="utf-8")
    elif tamper == "journal":
        sidecar.write_text(supplement.replace("### 阶段执行履历", "### 遗漏记录"), encoding="utf-8")
    elif tamper == "tail":
        path.write_text(main + "\n\n### 阶段执行履历\n被拼回的补充材料", encoding="utf-8")
    else:
        block = re.search(r"<!-- abr-final-design:start -->.*?<!-- abr-final-design:end -->", main, re.S).group()
        path.write_text(main.replace(block, ""), encoding="utf-8")
        sidecar.write_text(supplement + "\n" + block, encoding="utf-8")
    passed, diagnostic = checker.do_check(is_complete=True)
    assert not passed and diagnostic["error_code"] == "REPORT_FINAL_INPUT_MISMATCH", diagnostic


@pytest.mark.parametrize("existing_supplement", [False, True])
def test_record_publication_rolls_back_all_three_files(tmp_path, monkeypatch, existing_supplement):
    from auto_battery_research.tests.test_report_record import setup
    from auto_battery_research.util import report_consistency
    doc, _ = setup(tmp_path)
    record = records.capture(tmp_path, doc)
    previous = {records.FILENAME: b"old record", bundle.MAIN_FILENAME: b"old main"}
    if existing_supplement:
        previous[bundle.SUPPLEMENT_FILENAME] = b"old supplement"
    for name, data in previous.items():
        (tmp_path / name).write_bytes(data)
    monkeypatch.setattr(report_consistency, "check", lambda *a: [])
    write = records.atomic_write
    def fail_on_supplement(path, content):
        write(path, content)
        if path.name == bundle.SUPPLEMENT_FILENAME:
            raise OSError("模拟第三份文件替换后失败")
    monkeypatch.setattr(records, "atomic_write", fail_on_supplement)
    with pytest.raises(OSError):
        records.publish(tmp_path, record, "新正文", "新补充材料")
    assert previous == {name: (tmp_path / name).read_bytes() for name in previous}
    assert (tmp_path / bundle.SUPPLEMENT_FILENAME).exists() == existing_supplement


def test_legacy_audit_refresh_still_updates_inline_journal(tmp_path):
    from auto_battery_research.util.reports import publish_report_audit, _JOURNAL_TABLE_HEADER, _JOURNAL_TABLE_FOOTER
    path = tmp_path / bundle.MAIN_FILENAME
    path.write_text("# 旧报告\n- 阶段审计: 待审\n### 阶段执行履历\n" + _JOURNAL_TABLE_HEADER + "旧行" + _JOURNAL_TABLE_FOOTER, encoding="utf-8")
    publish_report_audit(path, [SimpleNamespace(id=6, status="PASSED")], [{"stage_id": 6, "duration_seconds": 9}])
    text = path.read_text(encoding="utf-8")
    assert "耗时 9.0s" in text and "旧行" not in text
    assert not (tmp_path / bundle.SUPPLEMENT_FILENAME).exists()
