# -*- coding: utf-8 -*-
"""PINN 执行器 — RAG 侧唯一入口（模型无关）

run_pinn_prediction(scheme, condition)：
  scheme(材料组合) + condition(工况) → spec dict → 查注册表 → subprocess 调 worker
  → DischargePrediction dict

任何失败返回 {"error": ...}，不抛异常（调用方降级策略）。
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .registry import DEFAULT_BACKEND, backend_status, get_worker_cmd

# spec dict 中允许透传的工况键（避免把不可序列化对象传给 worker）
_CONDITION_KEYS = (
    "c_rate", "voltage_min", "voltage_max", "temperature_C",
    "current_density", "cycle_number",
)


def build_spec_dict(
    scheme: Dict, condition: Optional[Dict] = None
) -> Dict[str, Any]:
    """scheme(材料组合) + condition(工况) → 扁平 spec dict（JSON 安全）。"""
    spec: Dict[str, Any] = {}
    for k, v in (scheme or {}).items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            spec[k] = v
    for key in _CONDITION_KEYS:
        if condition and condition.get(key) is not None:
            spec[key] = condition[key]
    return spec


def run_pinn_prediction(
    scheme: Dict,
    condition: Optional[Dict] = None,
    backend: Optional[str] = None,
    timeout_sec: float = 300.0,
) -> Dict[str, Any]:
    """执行 PINN 推理，返回 DischargePrediction dict；任何失败返回 {"error": ...}。"""
    cmd = get_worker_cmd(backend)
    if not cmd:
        return {
            "error": f"PINN backend 不可用: {backend or DEFAULT_BACKEND}",
            "available_backends": backend_status(),
        }

    spec = build_spec_dict(scheme, condition)
    try:
        with tempfile.TemporaryDirectory() as td:
            in_path = Path(td) / "spec.json"
            out_path = Path(td) / "pred.json"
            in_path.write_text(
                json.dumps(spec, ensure_ascii=False), encoding="utf-8"
            )
            proc = subprocess.run(
                cmd + ["--input", str(in_path), "--output", str(out_path)],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            if proc.returncode != 0:
                return {
                    "error": f"worker 退出码 {proc.returncode}: {proc.stderr[-500:]}"
                }
            if not out_path.exists():
                return {"error": f"worker 未产出输出文件: {proc.stderr[-500:]}"}
            result = json.loads(out_path.read_text(encoding="utf-8"))
            return result if isinstance(result, dict) else {
                "error": "worker 输出非 dict"
            }
    except subprocess.TimeoutExpired:
        return {"error": f"PINN worker 超时(>{timeout_sec}s)"}
    except Exception as e:
        return {"error": f"PINN 执行失败: {type(e).__name__}: {e}"}
