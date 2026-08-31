# -*- coding: utf-8 -*-
"""PINN worker 注册表 — backend 名 → 可执行命令

换模型（包括整个 PINNSTRIPES 包被替换）只改这里 + 配置：
  1. 新包写一个自包含 worker 脚本（--input spec.json --output pred.json）
  2. 本表加一行
  3. PINN_BACKEND 环境变量指向新 backend 名

worker 是独立进程（subprocess），公共代码不 import 任何 PINN 包。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

_WORKERS_DIR = Path(__file__).resolve().parent / "workers"

# cmd 用列表形式（避免 shell 注入）；env 为附加环境变量
WORKERS: Dict[str, Dict] = {
    "dummy": {
        "cmd": [sys.executable, str(_WORKERS_DIR / "dummy.py")],
        "env": {},
        "enabled": True,
    },
    # PINNSTRIPES：模型权重就绪后 export PINNSTRIPES_MODEL_DIR / PINNSTRIPES_UTIL_DIR
    "pinnstripes": {
        "cmd": [sys.executable, str(_WORKERS_DIR / "pinnstripes.py")],
        "env": {
            "PINNSTRIPES_MODEL_DIR": os.environ.get("PINNSTRIPES_MODEL_DIR", ""),
            "PINNSTRIPES_UTIL_DIR": os.environ.get("PINNSTRIPES_UTIL_DIR", ""),
        },
        "enabled": bool(os.environ.get("PINNSTRIPES_MODEL_DIR")),
    },
}

DEFAULT_BACKEND = os.environ.get("PINN_BACKEND", "dummy")


def get_worker_cmd(backend: Optional[str] = None) -> Optional[List[str]]:
    """返回 backend 对应的 worker 命令；未启用/未知返回 None。"""
    name = backend or DEFAULT_BACKEND
    entry = WORKERS.get(name)
    if not entry or not entry.get("enabled"):
        return None
    return entry["cmd"]


def backend_status() -> Dict[str, bool]:
    """各 backend 是否可用（用于错误信息与日志）。"""
    return {name: bool(e.get("enabled")) for name, e in WORKERS.items()}
