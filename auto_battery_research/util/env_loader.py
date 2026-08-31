"""极简 .env 加载器 — 零第三方依赖 (AutoBatteryResearch Agent).

优先级约定 (与 setting.yaml 的 `$(ENV_VAR:默认值)` 插值配合):
    已有系统环境变量 > .env 文件 > setting.yaml 内置默认值

即 .env 不覆盖已导出的环境变量；文件不存在时静默跳过，不影响离线运行。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

_loaded_paths = set()


def load_env(env_file: Optional[Path] = None, override: bool = False) -> bool:
    """解析 .env 并注入 os.environ (幂等；默认不覆盖已有环境变量).

    Returns:
        bool: 本次是否实际读取并注入了变量。
    """
    global _loaded_paths
    path = Path(env_file) if env_file else ROOT_DIR / ".env"
    if not path.exists() or path in _loaded_paths:
        return False

    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception:
        return False

    injected = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if val[:1] in ("'", '"'):
            # 带引号值：取首个配对引号内部，其后内容 (含行内注释) 一并忽略
            end = val.find(val[0], 1)
            if end != -1:
                val = val[1:end]
        elif " #" in val:
            # 无引号值：剥离行内注释
            val = val.split(" #", 1)[0].rstrip()
        if override or key not in os.environ:
            os.environ[key] = val
            injected = True

    _loaded_paths.add(path)
    return injected
