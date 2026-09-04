"""课题目标同义对齐 (resolve_effective_goal) 与管理器收敛回归测试.

锁定 "默认课题收敛单一事实源" 的解析语义，防止 LLM 措辞差异
(大小写/空格/标点/子串/能量密度写法) 造成课题分叉:
1. resolve_effective_goal 纯函数: 同义对齐回活跃课题，真异课题保留显式输入;
2. get_stage_manager_for_goal: 同义课题复用全局单例，不再新建 per-goal 管理器
   (避免全量 Checker 级联与状态双写，避免 task_dir 与 manager 归属不一致)。
"""

import pytest

from auto_battery_research.tools import stage_tools
from auto_battery_research.tools.stage_tools import (
    resolve_effective_goal,
    get_stage_manager_for_goal,
    set_stage_manager,
)
from auto_battery_research.workflow.stage_manager import StageManager


ACTIVE_GOAL = "设计400Wh/kg高比能液态锂金属电池方案"


@pytest.fixture(autouse=True)
def restore_manager_state():
    """快照并恢复模块级单例与按课题缓存，防止测试间互相污染."""
    saved_global = stage_tools._GLOBAL_MANAGER
    saved_cache = dict(stage_tools._GOAL_MANAGER_CACHE)
    yield
    stage_tools._GLOBAL_MANAGER = saved_global
    stage_tools._GOAL_MANAGER_CACHE.clear()
    stage_tools._GOAL_MANAGER_CACHE.update(saved_cache)


# ────────────────────────── resolve_effective_goal 纯函数 ──────────────────────────


def test_empty_explicit_goal_falls_back_to_active():
    """显式课题为空 → 回退活跃课题；双空 → 通用兜底键."""
    assert resolve_effective_goal("", ACTIVE_GOAL) == ACTIVE_GOAL
    assert resolve_effective_goal(None, ACTIVE_GOAL) == ACTIVE_GOAL
    assert resolve_effective_goal("   ", ACTIVE_GOAL) == ACTIVE_GOAL
    assert resolve_effective_goal("", "") == "general_research_task"


def test_empty_active_goal_preserves_explicit():
    """无活跃课题 → 显式课题原样保留."""
    assert resolve_effective_goal("设计500Wh/kg固态电池", "") == "设计500Wh/kg固态电池"


def test_normalized_equivalent_aligns_to_active():
    """大小写/空格/下划线/中英文标点差异视为同义，对齐回活跃课题原文."""
    variants = [
        "设计400wh/kg高比能液态锂金属电池方案",
        "设计 400Wh/kg 高比能液态锂金属电池方案",
        "设计400Wh/kg_高比能液态锂金属电池方案",
        "设计400Wh/kg高比能液态锂金属电池方案。",
        "设计400Wh/kg高比能液态锂金属电池方案，",
    ]
    for v in variants:
        assert resolve_effective_goal(v, ACTIVE_GOAL) == ACTIVE_GOAL


def test_substring_paraphrase_aligns_to_active():
    """省略前后缀的子串措辞 (双向) 对齐回活跃课题."""
    # 显式省略了"设计/方案"包装词
    assert resolve_effective_goal("400Wh/kg高比能液态锂金属电池", ACTIVE_GOAL) == ACTIVE_GOAL
    # 显式在活跃课题上追加了后缀说明
    assert resolve_effective_goal(ACTIVE_GOAL + "（迭代二）", ACTIVE_GOAL) == ACTIVE_GOAL


def test_same_energy_density_aligns_to_active():
    """同能量密度的措辞差异对齐回活跃课题 (锁定当前设计意图:
    LLM 常把 400Wh/kg 课题改写成 "设计400wh/kg 锂金属电池" 等短句)."""
    assert resolve_effective_goal("400wh/kg 锂金属电池", ACTIVE_GOAL) == ACTIVE_GOAL
    assert resolve_effective_goal("高比能400Wh/kg电池方案", ACTIVE_GOAL) == ACTIVE_GOAL


def test_different_energy_density_preserves_explicit():
    """能量密度数值不同 → 判为不同课题，显式输入保留."""
    g = "设计500Wh/kg高比能液态锂金属电池方案"
    assert resolve_effective_goal(g, ACTIVE_GOAL) == g


def test_genuinely_different_goal_preserves_explicit():
    """无子串关系且密度不同的真异课题 → 显式输入保留 (不吞并)."""
    g = "硫化物固态电池电解质筛选与界面优化研究"
    assert resolve_effective_goal(g, ACTIVE_GOAL) == g


def test_short_strings_skip_fuzzy_rules():
    """归一化后长度 <6 的短串不触发子串/密度模糊规则，显式输入保留
    (防止 "固态电池" 之类短词被任意吸并)."""
    g = "固态电池研究"
    assert resolve_effective_goal(g, "固态电池") == g


# ────────────────────────── get_stage_manager_for_goal 收敛 ──────────────────────────


def test_synonymous_goal_reuses_global_singleton():
    """同义课题必须复用全局单例 (同一对象)，不得新建 per-goal 管理器."""
    mgr = StageManager(target_goal=ACTIVE_GOAL)
    set_stage_manager(mgr)

    # 大小写/空格差异、子串措辞、同密度短句 —— 三类同义输入都命中单例
    assert get_stage_manager_for_goal("设计400wh/kg 高比能液态锂金属电池方案") is mgr
    assert get_stage_manager_for_goal("400Wh/kg高比能液态锂金属电池") is mgr
    assert get_stage_manager_for_goal("400wh/kg 锂金属电池") is mgr
    # 显式留空同样回落单例
    assert get_stage_manager_for_goal("") is mgr
    assert get_stage_manager_for_goal(None) is mgr

    # 同义请求不产生按课题缓存条目
    assert all(k == ACTIVE_GOAL for k in stage_tools._GOAL_MANAGER_CACHE)


def test_different_goal_gets_independent_manager():
    """真异课题返回独立管理器 (不与全局单例混用)，且按课题缓存复用."""
    mgr = StageManager(target_goal=ACTIVE_GOAL)
    set_stage_manager(mgr)

    other_goal = "钠离子电池正极材料筛选研究"
    other = get_stage_manager_for_goal(other_goal)
    assert other is not mgr
    assert other.target_goal == other_goal
    # 二次请求命中缓存，同一实例
    assert get_stage_manager_for_goal(other_goal) is other


def test_no_global_manager_creates_per_goal_cache_entry():
    """无全局单例时按课题缓存创建，且不隐式污染全局单例位."""
    # 套件中其他测试可能已设置全局单例，此处显式清空以模拟全新进程状态
    # (autouse fixture restore_manager_state 负责测试后恢复)
    stage_tools._GLOBAL_MANAGER = None
    goal = "无全局单例_按课题缓存创建验证"
    mgr = get_stage_manager_for_goal(goal)
    assert mgr.target_goal == goal
    assert stage_tools._GLOBAL_MANAGER is None
    assert stage_tools._GOAL_MANAGER_CACHE.get(goal) is mgr
