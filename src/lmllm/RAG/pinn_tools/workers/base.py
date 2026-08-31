# -*- coding: utf-8 -*-
"""PinnWorker 契约 — 放电预测输出定义（模型无关）

所有 PINN worker（dummy / pinnstripes / 未来任何包）输出同一结构。
字段只增不删：新模型若提供更丰富信息（置信区间、电解液浓度场等），
以可选字段追加，老字段保持兼容 —— 这是"换模型接口不变"的根基。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


@dataclass
class DischargePrediction:
    """统一放电预测输出。内部单位：mAh/g、V、Wh/kg（材料级）。"""

    v_curve: List[float] = field(default_factory=list)   # 放电电压 V(t) 采样点
    q_end_mAh_g: float = 0.0                             # 放电比容量
    v_mean: float = 0.0                                  # 平均放电电压
    energy_wh_kg: float = 0.0                            # 材料级能量密度
    confidence: str = "unknown"                          # high / medium / low / unknown
    data_gaps: List[str] = field(default_factory=list)   # 缺参数字段
    model: str = ""                                      # 产生本结果的 backend 名
    meta: Dict[str, Any] = field(default_factory=dict)   # 扩展信息（原始场、残差等）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DischargePrediction":
        """从 dict 构造；忽略未知字段（兼容未来扩展）。"""
        keys = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in d.items() if k in keys})
