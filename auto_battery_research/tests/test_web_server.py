"""FastAPI Web 监控大屏 (web/server.py) 离线单测.

只读数据层全部为磁盘纯函数 (不构造 StageManager, 不触发 Checker 级联),
此处直接覆盖纯函数 + monkeypatch TASKS_ROOT/LOG_DIR 指向 tmp 目录;
另含一个 httpx 可用时的 TestClient 冒烟测试 (无 httpx 则跳过, 不强依赖)。
"""

import json
import pytest

from auto_battery_research.web import server as web_server


@pytest.fixture()
def fake_env(tmp_path, monkeypatch):
    """构造隔离的 tasks/log/output 目录环境并指向 server 模块常量."""
    tasks_root = tmp_path / "output" / "tasks"
    tasks_root.mkdir(parents=True)
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    global_out = tmp_path / "output" / "auto_battery_research"
    global_out.mkdir(parents=True)
    monkeypatch.setattr(web_server, "TASKS_ROOT", tasks_root)
    monkeypatch.setattr(web_server, "LOG_DIR", log_dir)
    monkeypatch.setattr(web_server, "GLOBAL_OUT", global_out)
    monkeypatch.setattr(web_server, "ROOT_DIR", tmp_path)
    return type("FakeEnv", (), {
        "tasks_root": tasks_root, "log_dir": log_dir, "global_out": global_out, "tmp": tmp_path,
    })()


def _make_goal_dir(env, dir_name, goal=None, stages=None, current_idx=0, report=None, scheme=False, updated="2026-09-01T10:00:00"):
    """生成一个带 .stage_state.json 的课题目录 (stages 缺省为 6 阶段全 PENDING)."""
    d = env.tasks_root / dir_name
    d.mkdir(parents=True, exist_ok=True)
    state = {
        "version": "1.0.0",
        "target_goal": goal or dir_name,
        "updated_at": updated,
        "current_stage_idx": current_idx,
        "stages": stages if stages is not None else [
            {"id": i, "key": f"stage_{i}", "name": f"Stage {i}", "status": "PENDING", "skip": False}
            for i in range(1, 7)
        ],
    }
    (d / ".stage_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    if report:
        (d / report).write_text(f"# 研报\n\n{goal or dir_name} 的综合研报内容。", encoding="utf-8")
    if scheme:
        (d / "design_scheme.md").write_text("### Stage 4 设计方案\n\nNCM811 + 锂金属。", encoding="utf-8")
    return d


# =============================================================================
# 课题列表扫描
# =============================================================================

class TestScanGoals:
    def test_empty_root_returns_zero(self, fake_env):
        result = web_server.scan_goals()
        assert result["count"] == 0 and result["goals"] == []

    def test_listing_sorted_by_updated_at_desc(self, fake_env):
        _make_goal_dir(fake_env, "课题a_aaaaaaaa", goal="课题a", updated="2026-09-01T10:00:00")
        _make_goal_dir(fake_env, "课题b_bbbbbbbb", goal="课题b", updated="2026-09-02T09:00:00")
        result = web_server.scan_goals()
        assert result["count"] == 2
        assert result["goals"][0]["goal"] == "课题b"  # 最新的在前
        assert result["goals"][1]["goal"] == "课题a"

    def test_progress_and_flags(self, fake_env):
        stages = [
            {"id": 1, "key": "k1", "name": "S1", "status": "PASSED", "skip": False},
            {"id": 2, "key": "k2", "name": "S2", "status": "PASSED", "skip": False},
            {"id": 3, "key": "k3", "name": "S3", "status": "SKIPPED", "skip": True},
            {"id": 4, "key": "k4", "name": "S4", "status": "IN_PROGRESS", "skip": False},
            {"id": 5, "key": "k5", "name": "S5", "status": "PENDING", "skip": True},
            {"id": 6, "key": "k6", "name": "S6", "status": "PENDING", "skip": False},
        ]
        _make_goal_dir(fake_env, "课题x_c0ffee00", goal="课题x", stages=stages,
                       current_idx=3, report="final_research_report.md", scheme=True)
        g = web_server.scan_goals()["goals"][0]
        assert g["progress"] == "3/6"          # PASSED+PASSED+SKIPPED
        assert g["current_stage_id"] == 4       # current_stage_idx 0-based → 4
        assert g["has_report"] is True and g["has_scheme"] is True
        assert g["is_all_completed"] is False
        assert g["is_legacy"] is False           # 带哈希后缀 → 非遗留

    def test_corrupted_state_does_not_crash(self, fake_env):
        d = fake_env.tasks_root / "损坏课题_deadbeef"
        d.mkdir()
        (d / ".stage_state.json").write_text("{ not valid json !!", encoding="utf-8")
        g = web_server.scan_goals()["goals"][0]
        assert g["state_error"] is True
        assert g["state_found"] is False
        assert g["goal"] == "损坏课题_deadbeef"   # 回退用目录名
        assert all(s["status"] == "PENDING" for s in g["stages"])

    def test_legacy_unhashed_dir_detected(self, fake_env):
        _make_goal_dir(fake_env, "旧课题无哈希", goal="旧课题")
        g = web_server.scan_goals()["goals"][0]
        assert g["is_legacy"] is True

    def test_empty_dir_skipped(self, fake_env):
        (fake_env.tasks_root / "空目录_00000000").mkdir()
        assert web_server.scan_goals()["count"] == 0


# =============================================================================
# 目录安全
# =============================================================================

class TestSafeTaskDir:
    def test_traversal_rejected(self, fake_env):
        for bad in ("../evil", "..\\evil", "a/b", "a\\b", "..", ".hidden"):
            assert web_server._safe_task_dir(bad) is None, bad

    def test_valid_dir_resolved_inside_root(self, fake_env):
        _make_goal_dir(fake_env, "正常课题_1234abcd")
        p = web_server._safe_task_dir("正常课题_1234abcd")
        assert p is not None and p.is_dir()

    def test_missing_dir_name_rejected(self, fake_env):
        assert web_server._safe_task_dir("") is None


# =============================================================================
# 研报回退链
# =============================================================================

class TestReportFallback:
    def test_prefers_final_report(self, fake_env):
        d = _make_goal_dir(fake_env, "课题r_11111111", goal="课题r", report="final_research_report.md", scheme=True)
        path, source = web_server._resolve_report(d, is_legacy=False)
        assert path is not None and path.name == "final_research_report.md"

    def test_falls_back_to_design_scheme(self, fake_env):
        d = _make_goal_dir(fake_env, "课题s_22222222", goal="课题s", scheme=True)
        path, source = web_server._resolve_report(d, is_legacy=False)
        assert path is not None and path.name == "design_scheme.md"

    def test_legacy_goal_uses_global_fallback(self, fake_env):
        d = _make_goal_dir(fake_env, "旧课题", goal="旧课题")  # 无任何产物
        (fake_env.global_out / "final_research_report.md").write_text("# 全局旧研报", encoding="utf-8")
        path, source = web_server._resolve_report(d, is_legacy=True)
        assert path is not None and path.parent == fake_env.global_out

    def test_non_legacy_never_reads_global(self, fake_env):
        d = _make_goal_dir(fake_env, "课题n_33333333", goal="课题n")  # 无产物
        (fake_env.global_out / "final_research_report.md").write_text("# 全局旧研报", encoding="utf-8")
        path, _ = web_server._resolve_report(d, is_legacy=False)
        assert path is None

    def test_read_report_text_truncation_marker(self, fake_env):
        big = fake_env.tmp / "big.md"
        big.write_text("x" * (web_server._REPORT_MAX_BYTES + 100), encoding="utf-8")
        text, nbytes = web_server._read_report_text(big)
        assert nbytes == web_server._REPORT_MAX_BYTES + 100
        assert "仅展示前 2MB" in text


# =============================================================================
# 运行日志尾部
# =============================================================================

class TestTailLog:
    def test_log_found_and_tailed(self, fake_env):
        (fake_env.log_dir / "清洗后课题名.log").write_text(
            "\n".join(f"line-{i}" for i in range(1, 501)), encoding="utf-8")
        result = web_server._tail_log("清洗后课题名", tail=100)
        assert result["found"] is True
        assert result["lines"] == 100
        assert "line-501" not in result["text"] and "line-500" in result["text"]

    def test_log_missing(self, fake_env):
        result = web_server._tail_log("不存在的课题", tail=100)
        assert result["found"] is False and result["text"] == ""

    def test_clean_log_name_matches_tui_formula(self):
        # 与 TUI/Gradio 入口 init_file_logger 的命名公式一致: 非法字符→_、截断 40
        assert web_server._clean_log_name('设计400Wh/kg 高比能:液态"锂"金属方案') == \
            "设计400Wh_kg_高比能_液态_锂_金属方案.log"
        assert len(web_server._clean_log_name("长" * 80)) == 44  # 40 字符 + ".log"


# =============================================================================
# 阶段矩阵合并
# =============================================================================

class TestStageMatrix:
    def test_state_missing_falls_back_to_yaml_meta(self):
        matrix = web_server._stage_matrix(None)
        assert len(matrix) >= 6
        assert all(s["status"] == "PENDING" for s in matrix)
        assert matrix[0]["key"] == "literature_ingestion"  # yaml 元数据兜底

    def test_state_partial_stages_merged(self):
        matrix = web_server._stage_matrix({
            "stages": [{"id": 3, "key": "k", "name": "S3", "status": "PASSED", "skip": False}]
        })
        by_id = {s["id"]: s for s in matrix}
        assert by_id[3]["status"] == "PASSED"
        assert by_id[1]["status"] == "PENDING"
        assert by_id[1]["name"]  # yaml 名称兜底


# =============================================================================
# TestClient 冒烟 (httpx 存在时才跑, 不强依赖)
# =============================================================================

class TestHttpSmoke:
    def test_api_routes_via_testclient(self, fake_env):
        httpx = pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        _make_goal_dir(fake_env, "课题http_44444444", goal="课题http", report="final_research_report.md")

        client = TestClient(web_server.app)
        r = client.get("/api/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"

        r = client.get("/api/goals")
        assert r.status_code == 200 and r.json()["count"] == 1

        r = client.get("/api/goal/%E8%AF%BE%E9%A2%98http_44444444/status")  # URL 编码中文目录名
        assert r.status_code == 200
        assert r.json()["goal"] == "课题http"

        r = client.get("/api/goal/..%2F..%2Fetc%2Fpasswd/status")
        # 路径穿越必须被拒: 路由层不解码 %2F 时按多段路径处理 → 404;
        # 解码后进入处理器时 _safe_task_dir 拒绝 → 200 + error JSON。两者皆为安全行为。
        assert r.status_code == 404 or (r.status_code == 200 and "error" in r.json())
