"""Constants — 全局共享常量 (AutoBatteryResearch Agent).

任何需要引用默认研究课题的模块一律从此处导入，禁止散落复制硬编码。
"""

from __future__ import annotations

# 默认研究课题 —— CLI `--goal` 的默认值，也是直接实例化 StageManager / ABRAgent
# 未显式传 goal 时的兜底课题，README / CLAUDE.md 的使用示例与它对齐。
# ⚠️ 修改该值会使默认课题在 output/tasks/<slug>/ 下的既有状态目录"孤儿化"
# （旧进度不再被接续，多课题并存不受影响），变更前务必评估兼容性。
DEFAULT_GOAL = "设计400Wh/kg高比能液态锂金属电池方案"
