"""BaseChecker — 确定性门禁检查器基类 (AutoBatteryResearch Agent).

所有 Stage 的 Checker 继承此类，实现 do_check()，
返回结构化可操作诊断，杜绝模糊报错。
"""

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List


class BaseChecker(ABC):
    """阶段门禁检查器基类."""

    def __init__(self, name: Optional[str] = None, strict: bool = True):
        self.name = name or self.__class__.__name__
        self.strict = strict
        self.stage_manager = None
        self.config: Dict[str, Any] = {}
        self.stage_info: Dict[str, Any] = {}

    def on_init(self, stage_manager, stage_info: Dict[str, Any], config: Dict[str, Any]):
        """生命周期初始化."""
        self.stage_manager = stage_manager
        self.stage_info = stage_info
        self.config = config

    @property
    def workspace_root(self) -> Path:
        """获取绝对工作区根目录，杜绝从外部 CWD 调用时的相对路径漂移."""
        if self.stage_manager and hasattr(self.stage_manager, "root_dir"):
            return Path(self.stage_manager.root_dir).resolve()
        return Path(__file__).resolve().parent.parent.parent

    def resolve_path(self, path_val: Any) -> Path:
        """将路径安全解析为基于 workspace_root 的绝对路径."""
        p = Path(path_val)
        if p.is_absolute():
            return p
        return (self.workspace_root / p).resolve()

    @abstractmethod
    def do_check(self, is_complete: bool = False, **kwargs) -> Tuple[bool, Dict[str, Any]]:
        """执行检查.
        
        Args:
            is_complete: 是否是在 Complete 推进动作时触发（True 时执行完整终审）
            **kwargs: 额外传入参数
            
        Returns:
            Tuple[bool, Dict[str, Any]]: 
                - bool: True 表示通过门禁，False 表示未通过
                - dict: 结构化诊断信息 (必须包含 check_pass, error_code, error, observed, expected, next_action)
        """
        pass

    def build_diagnostic(
        self,
        passed: bool,
        error_code: Optional[str] = None,
        error_msg: Optional[str] = None,
        observed: Any = None,
        expected: Any = None,
        next_action: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """构建统一格式的结构化诊断报告."""
        return {
            "check_pass": passed,
            "checker_name": self.name,
            "stage_id": self.stage_info.get("id"),
            "stage_key": self.stage_info.get("key"),
            "stage_name": self.stage_info.get("name"),
            "error_code": error_code if not passed else None,
            "error": error_msg if not passed else None,
            "observed": observed,
            "expected": expected,
            "next_action": next_action if not passed else "阶段产物合格，允许推进到下一阶段。",
            "details": details or {},
        }

    # --- 常用确定性辅助校验方法 ---

    def check_file_exists(self, filepath: str, min_size_bytes: int = 1) -> Tuple[bool, Optional[str]]:
        """检查文件是否存在且大小不小于指定字节数."""
        p = Path(filepath)
        if not p.exists():
            return False, f"文件不存在: {filepath}"
        if not p.is_file():
            return False, f"路径不是有效文件: {filepath}"
        if p.stat().st_size < min_size_bytes:
            return False, f"文件大小小于最小要求 ({p.stat().st_size} < {min_size_bytes} 字节): {filepath}"
        return True, None

    def check_dir_has_files(self, dirpath: str, pattern: str = "*", min_count: int = 1) -> Tuple[bool, int, Optional[str]]:
        """检查目录是否存在且包含指定数量的文件."""
        p = Path(dirpath)
        if not p.exists() or not p.is_dir():
            return False, 0, f"目录不存在或不是文件夹: {dirpath}"
        files = list(p.glob(pattern))
        count = len(files)
        if count < min_count:
            return False, count, f"目录下匹配 '{pattern}' 的文件数量不足 ({count} < {min_count}): {dirpath}"
        return True, count, None

    def load_json_safe(self, filepath: str) -> Tuple[Optional[Any], Optional[str]]:
        """安全加载 JSON 文件."""
        p = Path(filepath)
        if not p.exists():
            return None, f"JSON 文件不存在: {filepath}"
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data, None
        except Exception as e:
            return None, f"JSON 解析失败 ({filepath}): {str(e)}"
