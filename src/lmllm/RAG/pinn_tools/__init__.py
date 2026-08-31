# -*- coding: utf-8 -*-
"""pinn_tools — 项目内"PINN 工具协议"

RAG 的 LLM 通过 Reviewer JSON 字段 needs_pinn 自主决定何时调用；
管线用 executor.run_pinn_prediction 执行；换模型只改 registry + config。
"""
from .executor import build_spec_dict, run_pinn_prediction
from .registry import DEFAULT_BACKEND, WORKERS, backend_status, get_worker_cmd
from .protocol import PINN_TOOL_PROMPT
from .workers.base import DischargePrediction

__all__ = [
    "run_pinn_prediction", "build_spec_dict",
    "get_worker_cmd", "backend_status", "DEFAULT_BACKEND", "WORKERS",
    "PINN_TOOL_PROMPT", "DischargePrediction",
]
