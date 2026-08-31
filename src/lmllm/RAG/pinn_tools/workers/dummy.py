# -*- coding: utf-8 -*-
"""dummy worker — 自包含独立脚本，链路验证用（假数据）

用法:
  python dummy.py --input spec.json --output pred.json

不 import pinn_tools 任何模块；输入输出均为 JSON 文件。
"""
from __future__ import annotations

import argparse
import json
from typing import Dict


def predict(spec: Dict) -> Dict:
    """按材料给假标称值（仅演示链路，非物理计算）。"""
    cathode = (spec.get("cathode") or "").lower()
    table = {
        "ncm811": (200.0, 3.7),
        "ncm": (200.0, 3.7),
        "ni96": (240.0, 3.7),
        "lrmo": (260.0, 3.5),
        "lnmo": (130.0, 4.7),
        "lfp": (160.0, 3.3),
        "li_metal": (0.0, 0.0),   # 负极材料不直接给正极容量
    }
    cap, v = table.get(cathode, (180.0, 3.6))
    return {
        "v_curve": [4.2, 4.0, 3.9, 3.8, 3.7, 3.6, 3.4, 3.2],
        "q_end_mAh_g": cap,
        "v_mean": v,
        "energy_wh_kg": round(cap * v, 1),
        "confidence": "low",
        "data_gaps": ["dummy 假数据：未接入真实 PINN 模型"],
        "model": "dummy",
        "meta": {"note": "链路验证用，数值无物理意义"},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="dummy PINN worker")
    ap.add_argument("--input", required=True, help="spec.json 路径")
    ap.add_argument("--output", required=True, help="pred.json 输出路径")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        spec = json.load(f)
    result = predict(spec)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[dummy] q={result['q_end_mAh_g']} mAh/g, "
          f"E={result['energy_wh_kg']} Wh/kg  (cathode={spec.get('cathode')})")


if __name__ == "__main__":
    main()
