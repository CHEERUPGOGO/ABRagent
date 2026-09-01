"""课题隔离与严格前缀验收回归测试.

锁定三处真实闭环可信度缺口 (修复对应 stage_manager / checkers):
1. 新哈希课题禁止读取全局 legacy 产物 —— Stage 3/4/6 门禁不再被
   output/auto_battery_research/ 下的全局陈旧产物穿透;
2. auto_detect_existing_progress 严格连续前缀验收 —— 中间阶段失败时,
   下游阶段不得因残留产物被自动认领为 PASSED (禁止跳步);
3. 历史存量课题 (被认领的无哈希旧目录) 的全局产物兼容读取承诺不回退。

全部测试通过 StageManager(workspace_root=tmp_path) 在临时工作区内运行，
不触碰真实仓库产物。
"""

import json
import re
import hashlib
from pathlib import Path

import pytest

from auto_battery_research.workflow.stage_manager import StageManager


# ────────────────────────── 合规产物构造工具 ──────────────────────────

VALID_REPORT_MD = """# 化学电池全生命周期研发与设计综合研报
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
""" * 2

VALID_SCHEME_MD = """# 设计方案
## 1. 目标与设计路线
设计400Wh/kg高比能电池路线，采用高镍正极与锂金属负极搭配局部高浓度电解液体系。

## 2. 推荐材料组合与关键配方
- 正极: NCM811
- 负极: 锂金属 (li_metal)
- 电解液: LHCE 局部高浓

## 3. 预期关键性能指标
- 能量密度: 410 Wh/kg
- 循环保持率: 88%

## 4. 可行性依据与机理
[ev_001] 前沿文献表明通过 LHCE 可稳定高镍界面与锂金属负极。

## 5. 风险与数据缺口
需要评估大规模产线水分控制与厚电极过电位。
"""

VALID_SCHEME_JSON = {
    "schema_version": "1.0",
    "target": "设计400Wh/kg高比能电池",
    "confidence": "high",
    "evidence": [
        {
            "passage_id": "ev_001",
            "source": "10.1038/s41467",
            "text": "锂金属 (lithium metal) 负极搭配高镍 NCM811 正极与 LHCE 局部高浓电解液的循环稳定性证据。",
        }
    ],
    "scheme": {"cathode": "NCM811", "anode": "li_metal", "electrolyte": "lhce"},
    "rule_checks": {"violations": [], "rejects": []},
}

VALID_CELL_JSON = {
    "materials": [{"canonical_id": "NCM811", "name": "NCM811"}],
    "cells": [
        {
            "cell_id": "cell_001",
            "cathode": "NCM811",
            "anode": "li_metal",
            "electrolyte": "lhce",
            "provenance": "10.1038/s41467",
        }
    ],
}

VALID_JOURNAL = [{"stage_id": 1, "notes": "文献入库完成", "deliverables": ["papers/merged/"]}]


def _task_dir_name(goal: str) -> str:
    """复现 get_task_output_dir 的哈希目录命名 (slug45 + md5 前 8 位)."""
    safe_slug = re.sub(r'[\\/:*?"<>|\s]+', '_', goal)[:45].strip('_') or "general_research_task"
    return f"{safe_slug}_{hashlib.md5(goal.encode('utf-8')).hexdigest()[:8]}"


def _make_stage12_corpus(root: Path) -> None:
    """构造能让 Stage 1/2 门禁通过的共享语料产物."""
    for comp in ("cathode", "anode", "electrolyte"):
        d = root / "database" / "type" / "Lithium_Ion_Metal_Battery" / comp
        d.mkdir(parents=True, exist_ok=True)
        (d / f"paper_{comp}.md").write_text("# 锂电池文献\n" + "NCM811 锂金属 LHCE 放电容量 220 mAh/g 内容。" * 5, encoding="utf-8")

    meta_dir = root / "miner" / "json" / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "meta_merged.json").write_text(
        json.dumps([{"title": "Lithium metal battery", "doi": "10.1038/s41467"}]), encoding="utf-8"
    )
    para_dir = root / "miner" / "json" / "100"
    para_dir.mkdir(parents=True, exist_ok=True)
    (para_dir / "paragraph_metadata_v4.json").write_text(
        json.dumps(
            [
                {"paragraph_context": "NCM811 正极 220 mAh/g 放电比容量测试。", "label": "电化学性能"},
                {"paragraph_context": "LHCE 局部高浓电解液在锂金属表面形成 LiF 钝化层。", "label": "材料属性与表征"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _make_valid_legacy_global_artifacts(root: Path) -> None:
    """在全局 legacy 目录放置完全合规的旧产物 (模拟历史课题交付物)."""
    g = root / "output" / "auto_battery_research"
    (g / "cell_assembly").mkdir(parents=True, exist_ok=True)
    (g / "final_research_report.md").write_text(VALID_REPORT_MD, encoding="utf-8")
    (g / "stage_journals.json").write_text(json.dumps(VALID_JOURNAL, ensure_ascii=False), encoding="utf-8")
    (g / "abr_agent_journal.json").write_text(json.dumps(VALID_JOURNAL, ensure_ascii=False), encoding="utf-8")
    (g / "design_scheme.md").write_text(VALID_SCHEME_MD, encoding="utf-8")
    (g / "design_scheme.json").write_text(json.dumps(VALID_SCHEME_JSON, ensure_ascii=False), encoding="utf-8")
    (g / "cell_assembly" / "sample_assembled_cell_extracted.json").write_text(
        json.dumps(VALID_CELL_JSON, ensure_ascii=False), encoding="utf-8"
    )


def _make_valid_task_stage4_artifacts(task_dir: Path) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "design_scheme.md").write_text(VALID_SCHEME_MD, encoding="utf-8")
    (task_dir / "design_scheme.json").write_text(json.dumps(VALID_SCHEME_JSON, ensure_ascii=False), encoding="utf-8")


def _make_valid_task_stage6_artifacts(task_dir: Path) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "final_research_report.md").write_text(VALID_REPORT_MD, encoding="utf-8")
    (task_dir / "stage_journals.json").write_text(json.dumps(VALID_JOURNAL, ensure_ascii=False), encoding="utf-8")


# ────────────────────────── 测试 1: 新课题全局产物隔离 ──────────────────────────


def test_new_task_blocks_legacy_global_artifacts(tmp_path):
    """新哈希课题: 即使全局 legacy 目录存在完全合规的 Stage 3/4/6 产物，
    门禁也必须失败 —— 不得穿透课题隔离 (旧缺陷: Stage 6 假通过)。"""
    _make_valid_legacy_global_artifacts(tmp_path)

    goal = "黄金回归_新课题隔离验证_全合规全局产物不得穿透"
    mgr = StageManager(target_goal=goal, workspace_root=str(tmp_path))

    assert mgr.is_legacy_task is False

    # Stage 4: 课题目录无方案 → 明确失败 (而非读到全局旧 design_scheme 报 RULE_CHECKS_MISSING)
    ok4, res4 = mgr.check_stage(4)
    assert ok4 is False
    assert res4["failure_summary"]["error_code"] == "DESIGN_SCHEME_FILE_MISSING"

    # Stage 6: 课题目录无研报 → 失败 (旧缺陷: 回退读全局旧报告而通过)
    ok6, res6 = mgr.check_stage(6)
    assert ok6 is False
    assert res6["failure_summary"]["error_code"] == "SYNTHESIS_REPORT_MISSING"

    # Stage 3: 课题目录无组装产物 → 失败 (不得聚合全局 cell_assembly)
    ok3, res3 = mgr.check_stage(3)
    assert ok3 is False
    assert res3["failure_summary"]["error_code"] == "NO_EXTRACTED_DATA_FOUND"

    # 状态文件记录课题目录形态为 hashed
    state = json.loads((mgr.get_task_output_dir() / ".stage_state.json").read_text(encoding="utf-8"))
    assert state["task_dir_schema"] == "hashed"


def test_new_task_blocks_rag_shared_output_dir(tmp_path):
    """新课题: src/lmllm/RAG/output 共享目录的最新 md 也不得作为 Stage 4 产物回退."""
    rag_out = tmp_path / "src" / "lmllm" / "RAG" / "output"
    rag_out.mkdir(parents=True, exist_ok=True)
    (rag_out / "rag_materials_screening_20260101.md").write_text(VALID_SCHEME_MD, encoding="utf-8")

    goal = "黄金回归_RAG共享输出目录不得回退"
    mgr = StageManager(target_goal=goal, workspace_root=str(tmp_path))

    ok4, res4 = mgr.check_stage(4)
    assert ok4 is False
    assert res4["failure_summary"]["error_code"] == "DESIGN_SCHEME_FILE_MISSING"


# ────────────────────────── 测试 2: 历史课题兼容读取 ──────────────────────────


def test_legacy_adopted_task_reads_global_artifacts(tmp_path):
    """被认领的无哈希旧目录课题 (is_legacy_task=True): 全局产物读取回退保持兼容。"""
    _make_valid_legacy_global_artifacts(tmp_path)

    goal = "黄金回归_历史课题认领验证"
    safe_slug = re.sub(r'[\\/:*?"<>|\s]+', '_', goal)[:45].strip('_')
    legacy_dir = tmp_path / "output" / "tasks" / safe_slug
    legacy_dir.mkdir(parents=True, exist_ok=True)
    # 状态文件 target_goal 与本课题一致 → 认领为历史课题目录
    (legacy_dir / ".stage_state.json").write_text(json.dumps({"target_goal": goal}), encoding="utf-8")

    mgr = StageManager(target_goal=goal, workspace_root=str(tmp_path))

    assert mgr.get_task_output_dir() == legacy_dir
    assert mgr.is_legacy_task is True

    ok4, res4 = mgr.check_stage(4)
    assert ok4 is True, f"历史课题应能读全局 design_scheme: {res4.get('failure_summary')}"

    ok6, res6 = mgr.check_stage(6)
    assert ok6 is True, f"历史课题应能读全局研报: {res6.get('failure_summary')}"

    ok3, res3 = mgr.check_stage(3)
    assert ok3 is True, f"历史课题应能读全局 cell_assembly: {res3.get('failure_summary')}"

    state = json.loads((legacy_dir / ".stage_state.json").read_text(encoding="utf-8"))
    assert state["task_dir_schema"] == "legacy"


def test_legacy_adoption_rejects_mismatched_goal(tmp_path):
    """无哈希目录的状态文件 target_goal 不匹配 (前缀撞名的他课题) 时不得认领。"""
    goal = "黄金回归_撞名课题不得认领_目标A"
    other_goal = "黄金回归_撞名课题不得认领_目标B_完全不同"
    safe_slug = re.sub(r'[\\/:*?"<>|\s]+', '_', goal)[:45].strip('_')
    legacy_dir = tmp_path / "output" / "tasks" / safe_slug
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / ".stage_state.json").write_text(json.dumps({"target_goal": other_goal}), encoding="utf-8")

    mgr = StageManager(target_goal=goal, workspace_root=str(tmp_path))

    assert mgr.get_task_output_dir() != legacy_dir
    assert mgr.is_legacy_task is False


# ────────────────────────── 测试 3: 严格连续前缀验收 ──────────────────────────


def test_auto_detect_strict_prefix(tmp_path):
    """Stage 1/2 产物齐备、课题目录有合规 Stage 4/6 产物但缺 Stage 3 时:
    仅 1/2 被自动认领，3 失败，4/6 必须保持 PENDING —— 禁止跳步认领下游。"""
    _make_stage12_corpus(tmp_path)

    goal = "黄金回归_严格前缀验收_中间缺Stage3"
    task_dir = tmp_path / "output" / "tasks" / _task_dir_name(goal)
    task_dir.mkdir(parents=True, exist_ok=True)
    _make_valid_task_stage4_artifacts(task_dir)
    _make_valid_task_stage6_artifacts(task_dir)

    mgr = StageManager(target_goal=goal, workspace_root=str(tmp_path))

    status_by_id = {s.id: s.status for s in mgr.stages}
    assert status_by_id[1] == "PASSED"
    assert status_by_id[2] == "PASSED"
    assert status_by_id[3] == "FAILED"      # 首个未定论阶段: 尝试验收后失败
    assert status_by_id[4] == "PENDING"     # ★ 旧缺陷: 会被独立验收为 PASSED
    assert status_by_id[5] == "SKIPPED"     # 默认跳过 (初始化设定，不受前缀影响)
    assert status_by_id[6] == "PENDING"     # ★ 旧缺陷: 会被独立验收为 PASSED

    # 指针停在首个未完成阶段 (Stage 3, 0-based idx=2)
    assert mgr.current_stage_idx == 2
    assert mgr.get_current_stage().id == 3

    # 进度不显示虚假的 6/6
    progress = mgr.get_status()["progress"]
    assert progress == "3/6"


def test_auto_detect_honors_loaded_prefix(tmp_path):
    """持久化前缀 1-3 已 PASSED 且课题目录存在合规 Stage 4 产物时:
    Stage 4 被正常认领 (前缀连续时不误伤正常续跑)。"""
    _make_stage12_corpus(tmp_path)

    goal = "黄金回归_前缀连续_Stage4正常认领"
    task_dir = tmp_path / "output" / "tasks" / _task_dir_name(goal)
    task_dir.mkdir(parents=True, exist_ok=True)
    _make_valid_task_stage4_artifacts(task_dir)

    # 预置已完成前缀的状态文件 (Stage 1-3 PASSED, Stage 5 默认跳过)
    state = {
        "version": "1.0.0",
        "target_goal": goal,
        "current_stage_idx": 3,
        "stages": [
            {"id": 1, "status": "PASSED"},
            {"id": 2, "status": "PASSED"},
            {"id": 3, "status": "PASSED"},
            {"id": 4, "status": "PENDING"},
            {"id": 5, "status": "SKIPPED", "skip": True},
            {"id": 6, "status": "PENDING"},
        ],
    }
    (task_dir / ".stage_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    mgr = StageManager(target_goal=goal, workspace_root=str(tmp_path))

    status_by_id = {s.id: s.status for s in mgr.stages}
    assert status_by_id[1] == "PASSED"
    assert status_by_id[2] == "PASSED"
    assert status_by_id[3] == "PASSED"
    assert status_by_id[4] == "PASSED"   # 前缀连续 + 课题产物齐备 → 正常认领
    assert status_by_id[5] == "SKIPPED"
    assert status_by_id[6] == "FAILED"   # 无研报产物: 尝试验收后失败 (指针停留处)
    assert mgr.get_current_stage().id == 6


def test_report_synthesis_task_isolation_never_leaks_global_scheme(tmp_path):
    """新哈希课题生成 Stage 6 研报时，即使全局 output/auto_battery_research/
    下残留了旧课题方案，研报中也绝不泄漏旧课题方案，保持严格审计隔离。"""
    from auto_battery_research.tools.workflow_actions import run_synthesis_report

    # 在全局 legacy 目录放置带有明显特征的旧课题方案
    global_dir = tmp_path / "output" / "auto_battery_research"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "design_scheme.md").write_text("# 遗留旧方案: FOREIGN_LEGACY_SCHEME_XYZ", encoding="utf-8")
    (global_dir / "design_scheme.json").write_text(json.dumps({"scheme": {"cathode": "FOREIGN_CATHODE_XYZ"}}), encoding="utf-8")

    new_goal = "新课题_严格隔离研报生成测试"
    mgr = StageManager(target_goal=new_goal, workspace_root=str(tmp_path))
    assert mgr.is_legacy_task is False

    res = run_synthesis_report(target_query=new_goal, stage_manager=mgr)
    assert res["success"] is True

    report_content = Path(res["report_file"]).read_text(encoding="utf-8")
    # 必须绝不包含全局旧课题的特征字符串
    assert "FOREIGN_LEGACY_SCHEME_XYZ" not in report_content
    assert "FOREIGN_CATHODE_XYZ" not in report_content
    assert "尚未生成独立的 Stage 4 电池体系设计方案" in report_content
