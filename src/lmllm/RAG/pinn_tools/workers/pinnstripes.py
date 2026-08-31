# -*- coding: utf-8 -*-
"""pinnstripes worker — 自包含独立脚本，封装 NREL PINNSTRIPES 的 SPM PINN 推理

用法:
  python pinnstripes.py --input spec.json --output pred.json \
      [--model-dir <modelFolder>] [--util-dir <utilFolder>]

模型目录缺省从环境变量读取:
  PINNSTRIPES_MODEL_DIR  # 含 best.weights.h5 + config.json（如 tests/Model_1）
  PINNSTRIPES_UTIL_DIR   # PINNSTRIPES 的 util 文件夹（pinn_spm_param/util）

依赖 tensorflow / keras / tf2jax（PINNSTRIPES 环境），全部隔离在本进程。
公共代码（executor / RAG）不 import 本脚本 —— 整个 PINNSTRIPES 包被替换时，
只需替换本脚本 + registry 一行。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict


def _write_result(path: str, result: Dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def main() -> None:
    ap = argparse.ArgumentParser(description="PINNSTRIPES SPM PINN worker")
    ap.add_argument("--input", required=True, help="spec.json 路径")
    ap.add_argument("--output", required=True, help="pred.json 输出路径")
    ap.add_argument("--model-dir", default=os.environ.get("PINNSTRIPES_MODEL_DIR", ""))
    ap.add_argument("--util-dir", default=os.environ.get("PINNSTRIPES_UTIL_DIR", ""))
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        spec = json.load(f)
    model_dir = Path(args.model_dir)
    util_dir = Path(args.util_dir)

    # ── 就绪性检查（模型未训练/路径未配置时给出明确错误）──
    if not model_dir.is_dir() or not (model_dir / "best.weights.h5").exists():
        _write_result(args.output, {
            "error": f"model-dir 无效或未训练: {model_dir}",
            "hint": "设置 PINNSTRIPES_MODEL_DIR 指向含 best.weights.h5 的文件夹"
                    "（如 PINNSTRIPES-main/pinn_spm_param/tests/Model_1）",
        })
        return
    if not util_dir.is_dir():
        _write_result(args.output, {
            "error": f"util-dir 无效: {util_dir}",
            "hint": "设置 PINNSTRIPES_UTIL_DIR 指向 PINNSTRIPES 的 util 文件夹"
                    "（如 PINNSTRIPES-main/pinn_spm_param/util）",
        })
        return

    # ── 加载 PINNSTRIPES 模型（TF 依赖仅在 worker 进程内）──
    try:
        sys.path.insert(0, str(util_dir))
        from load_pinn import load_model  # PINNSTRIPES util 下的加载器
        nn = load_model(str(util_dir), str(model_dir), str(util_dir))
    except Exception as e:
        _write_result(args.output, {
            "error": f"PINNSTRIPES 模型加载失败: {type(e).__name__}: {e}",
            "hint": "确认在 PINNSTRIPES 环境（tensorflow/keras/tf2jax/float64）运行",
        })
        return

    # ── 推理：参数向量 → 场 → 放电曲线标量 ──
    # TODO(模型就绪后补全): 
    #   1. 按 util/spm.py 的 rescale 定义，从 spec 构造 var_dict（t, r̃ 网格）
    #      + params_dict（deg_params 参数向量，如 [i0_a, ds_c]）；
    #   2. 调 util/forwardPass.py 的 pinn_pred(nn, var_dict, params_dict)，
    #      得到场 {phie, phis_c, cs_a, cs_c}；
    #   3. 用 util/uocp_cs.py 的 OCP 算 V(t) = phis_c - phie - U_ocp(cs_surf)；
    #   4. 积分得比容量/平均电压/能量密度，填充 DischargePrediction。
    _write_result(args.output, {
        "error": "PINNSTRIPES 推理后处理未实现（需按 SPM 场定义换算放电曲线）",
        "hint": "见本文件 TODO：场→曲线换算需对照 util/spm.py 的 rescale 定义",
        "model_loaded": True,
        "spec": spec,
    })


if __name__ == "__main__":
    main()
