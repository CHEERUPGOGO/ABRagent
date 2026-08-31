"""BaseStage — 阶段实体封装 (AutoBatteryResearch Agent)."""

import time
from typing import Dict, Any, List, Optional
from auto_battery_research.checkers.base_checker import BaseChecker


class BaseStage:
    """单个工作流阶段对象."""

    def __init__(self, raw_cfg: Dict[str, Any], checkers: List[BaseChecker]):
        self.id: int = raw_cfg.get("id", 0)
        self.key: str = raw_cfg.get("key", f"stage_{self.id}")
        self.name: str = raw_cfg.get("name", "未命名阶段")
        self.description: str = raw_cfg.get("description", "")
        self.skip: bool = raw_cfg.get("skip", False)
        self.allow_skip: bool = raw_cfg.get("allow_skip", self.id == 5)
        self.skip_reason: str = raw_cfg.get("skip_reason", "")
        self.reference_files: List[str] = raw_cfg.get("reference_files", [])
        self.expected_outputs: List[str] = raw_cfg.get("expected_outputs", [])
        self.checkers: List[BaseChecker] = checkers
        self.status: str = "PENDING"  # PENDING | IN_PROGRESS | PASSED | SKIPPED | FAILED
        self.duration_seconds: float = 0.0
        self.start_timestamp: Optional[float] = None
        self.fail_count: int = 0

    def start_running(self) -> None:
        """开始运行计时."""
        self.status = "IN_PROGRESS"
        self.start_timestamp = time.time()

    def finish_running(self, status: str = "PASSED", duration: Optional[float] = None) -> None:
        """结束运行计时."""
        self.status = status
        if duration is not None:
            self.duration_seconds = duration
        elif self.start_timestamp:
            self.duration_seconds = time.time() - self.start_timestamp
        self.start_timestamp = None

    def get_current_duration(self) -> float:
        """获取当前阶段的实时耗时（秒）."""
        if self.status == "IN_PROGRESS" and self.start_timestamp:
            return round(time.time() - self.start_timestamp, 1)
        return round(self.duration_seconds, 1)

    def to_dict(self) -> Dict[str, Any]:
        """导出字典状态."""
        return {
            "id": self.id,
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "skip": self.skip,
            "skip_reason": self.skip_reason,
            "status": self.status,
            "duration_seconds": self.get_current_duration(),
            "fail_count": self.fail_count,
            "reference_files": self.reference_files,
            "expected_outputs": self.expected_outputs,
            "checkers": [c.name for c in self.checkers],
        }

    def get_tips(self) -> str:
        """获取当前阶段的详细任务指导与验收标准."""
        ref_list = "\n".join([f"  - `{f}`" for f in self.reference_files]) or "  - 无"
        out_list = "\n".join([f"  - `{f}`" for f in self.expected_outputs]) or "  - 无"
        check_list = "\n".join([f"  - `{c.name}`" for c in self.checkers])

        skip_info = f"\n**⚠️ 本阶段处于跳过 (SKIP) 模式**：{self.skip_reason}" if self.skip else ""

        tips = f"""### Stage {self.id}: {self.name} ({self.key})
{skip_info}

**【阶段任务描述】**
{self.description}

**【必须产出的交付物】**
{out_list}

**【参考指引文件】**
{ref_list}

**【绑定的门禁检查器 (Checkers)】**
{check_list}
"""
        return tips
