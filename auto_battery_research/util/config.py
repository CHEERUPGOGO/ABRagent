"""Config Engine — 分层配置加载、环境变量解析与运行时快照生成 (AutoBatteryResearch Agent).

1. 全局配置 (setting.yaml)
2. 工作流定义 (abr_workflow.yaml)
3. 用户与环境覆盖 ($(ENV_VAR: default) 解析)
4. 模板变量动态渲染 ({WORKSPACE}, {OUT}, {TARGET_GOAL}, {MODEL})
5. 非机密运行时快照持久化 (.abr_agent/runtime_config.json)
"""

import os
import re
import sys
import json
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, Union, List


ENV_PATTERN = re.compile(r"\$\(([\w]+)(?:\s*:\s*([^)]*))?\)")
TEMPLATE_PATTERN = re.compile(r"\{([A-Z_]+)\}")

SECRET_KEYS = {
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "auth",
    "credential",
    "openai_api_key",
    "deepseek_api_key",
}


def resolve_env_vars(val: Any) -> Any:
    """递归解析配置字符串中的 $(VAR_NAME: default_value) 语法."""
    if isinstance(val, str):
        def repl(match):
            var_name = match.group(1)
            default_val = match.group(2) if match.group(2) is not None else ""
            default_val = default_val.strip()
            return os.getenv(var_name, default_val)

        return ENV_PATTERN.sub(repl, val)
    elif isinstance(val, dict):
        return {k: resolve_env_vars(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [resolve_env_vars(item) for item in val]
    return val


def render_templates(val: Any, context: Dict[str, Any]) -> Any:
    """递归替换配置中的 {WORKSPACE}, {OUT}, {TARGET_GOAL} 模板变量."""
    if isinstance(val, str):
        for k, v in context.items():
            val = val.replace(f"{{{k}}}", str(v))
        return val
    elif isinstance(val, dict):
        return {k: render_templates(v, context) for k, v in val.items()}
    elif isinstance(val, list):
        return [render_templates(item, context) for item in val]
    return val


def sanitize_secrets(val: Any) -> Any:
    """递归脱敏配置字典中的 API Keys 与机密字段，供快照持久化使用."""
    if isinstance(val, dict):
        clean = {}
        for k, v in val.items():
            if any(sk in k.lower() for sk in SECRET_KEYS):
                clean[k] = "******" if v else ""
            else:
                clean[k] = sanitize_secrets(v)
        return clean
    elif isinstance(val, list):
        return [sanitize_secrets(item) for item in val]
    return val


class ABRConfigLoader:
    """分层配置加载与运行时快照管理器."""

    def __init__(
        self,
        workspace_root: Optional[str] = None,
        setting_file: Optional[str] = None,
        workflow_file: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ):
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path(__file__).resolve().parent.parent.parent
        self.setting_file = Path(setting_file) if setting_file else self.workspace_root / "auto_battery_research" / "setting.yaml"
        self.workflow_file = Path(workflow_file) if workflow_file else self.workspace_root / "auto_battery_research" / "workflow" / "abr_workflow.yaml"
        self.overrides = overrides or {}
        
        self.raw_setting: Dict[str, Any] = {}
        self.raw_workflow: Dict[str, Any] = {}
        self.resolved_config: Dict[str, Any] = {}

    def load(self, target_goal: str = "设计400Wh/kg高比能液态锂金属电池方案") -> Dict[str, Any]:
        """执行完整分层加载与模板渲染流水线."""
        # 1. 加载 setting.yaml
        if self.setting_file.exists():
            with open(self.setting_file, "r", encoding="utf-8") as f:
                self.raw_setting = yaml.safe_load(f) or {}

        # 2. 加载 workflow.yaml
        if self.workflow_file.exists():
            with open(self.workflow_file, "r", encoding="utf-8") as f:
                self.raw_workflow = yaml.safe_load(f) or {}

        # 3. 合并配置
        merged = {}
        merged.update(self.raw_setting)
        merged["mission"] = self.raw_workflow.get("mission", {})
        merged["stages"] = self.raw_workflow.get("stages", [])

        # 4. 合并显式覆盖项
        if self.overrides:
            merged.update(self.overrides)

        # 5. 解析 $(ENV: default)
        merged = resolve_env_vars(merged)

        # 6. 构造模板上下文并渲染
        out_dir = merged.get("paths", {}).get("output_dir", "output/auto_battery_research")
        context = {
            "WORKSPACE": str(self.workspace_root),
            "OUT": str(self.workspace_root / out_dir),
            "TARGET_GOAL": target_goal,
            "PYTHON": sys.executable,
        }
        self.resolved_config = render_templates(merged, context)

        # 7. 保存非机密运行时快照
        self.save_runtime_snapshot()

        return self.resolved_config

    def save_runtime_snapshot(self):
        """保存非机密运行时快照至 .abr_agent/runtime_config.json."""
        snap_dir = self.workspace_root / ".abr_agent"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_file = snap_dir / "runtime_config.json"

        snapshot_data = {
            "version": "1.0.0",
            "agent_name": "AutoBatteryResearch Agent",
            "python_executable": sys.executable,
            "python_version": sys.version.split()[0],
            "workspace_root": str(self.workspace_root),
            "package_path": str(self.workspace_root / "auto_battery_research"),
            "config": sanitize_secrets(self.resolved_config),
        }

        try:
            with open(snap_file, "w", encoding="utf-8") as f:
                json.dump(snapshot_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ABRConfigLoader] 警告: 无法保存运行时快照: {e}")


def load_runtime_config(workspace_dir: Optional[str] = None) -> Dict[str, Any]:
    """外部工具与脚本读取当前运行时快照."""
    ws = Path(workspace_dir).resolve() if workspace_dir else Path.cwd()
    snap_file = ws / ".abr_agent" / "runtime_config.json"
    if snap_file.exists():
        try:
            with open(snap_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}
