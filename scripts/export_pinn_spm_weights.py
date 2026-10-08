# -*- coding: utf-8 -*-
"""export_pinn_spm_weights.py — 一次性导出 SPM PINN 权重（TF SavedModel → npz）。

把 PINN-SPM-fast-prototyping 训练出的 Keras SavedModel（UNI SPM NE/PE，
Input(3)→BN→Dense(30,ELU)×2→Dense(1)，float64）导出为 pinn/models/ 下的 npz
权重，供 pinn/spm_runner.py 的纯 numpy 前向消费——主仓库因此零 TF 依赖。

在训练环境（conda pinn210，TF 2.10）手动运行：

    conda run -n pinn210 python scripts/export_pinn_spm_weights.py
    # 或显式指定路径：
    conda run -n pinn210 python scripts/export_pinn_spm_weights.py \
        --ne-model "<UNI SPM NE 目录>" --pe-model "<UNI SPM PE 目录>" \
        --system-dir pinn/models/systems/GrSi_NMC811

流程：按层抽取 BN(gamma/beta/moving_mean/moving_var + epsilon) 与 3 层
Dense(kernel/bias) → 写 npz → **自校验**：随机网格 + 包络极端点上 TF 前向 vs
numpy 前向 max|Δ| < 1e-9，npz 往返校验 → 写 golden_io.json 金样本（供仓库离线
测试持续锁定等价性）。源目录只读，不修改不删除。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pinn.spm_runner import network_forward, load_weights  # noqa: E402

DEFAULT_NE = ROOT / "pinn/PINN-SPM-fast-prototyping/train_universal/artifacts/models/UNI SPM NE"
DEFAULT_PE = ROOT / "pinn/PINN-SPM-fast-prototyping/train_universal/artifacts/models/UNI SPM PE"
DEFAULT_SYSTEM_DIR = ROOT / "pinn/models/systems/GrSi_NMC811"
TOL = 1e-9
N_CHECK, N_GOLDEN = 2048, 32


def extract_weights(model) -> Dict[str, np.ndarray]:
    """按层遍历抽取权重。键序与 pinn.spm_runner.network_forward 严格对应。"""
    import tensorflow as tf

    weights: Dict[str, np.ndarray] = {}
    dense_idx = 0
    for layer in model.layers:
        if isinstance(layer, tf.keras.layers.BatchNormalization):
            gamma, beta, moving_mean, moving_var = layer.get_weights()
            weights.update(
                bn_gamma=gamma, bn_beta=beta,
                bn_moving_mean=moving_mean, bn_moving_var=moving_var,
                bn_eps=np.float64(layer.epsilon),
            )
        elif isinstance(layer, tf.keras.layers.Dense):
            dense_idx += 1
            kernel, bias = layer.get_weights()
            weights[f"d{dense_idx}_kernel"] = kernel
            weights[f"d{dense_idx}_bias"] = bias
    if dense_idx != 3 or "bn_gamma" not in weights:
        raise RuntimeError(
            f"模型层结构与预期不符（期望 BN + 3×Dense，实得 {dense_idx}×Dense）；"
            "网络架构变更时需同步扩展 pinn/spm_runner.network_forward")
    return weights


def sample_inputs(n: int, seed: int) -> np.ndarray:
    """覆盖训练域的 (s, ρ, bc_norm) 采样：均匀网格 + 包络极端点。"""
    rng = np.random.default_rng(seed)
    X = np.column_stack([
        rng.uniform(0.0, 1.0, n),
        rng.uniform(0.01, 1.0, n),
        rng.uniform(0.0, 1.0, n),
    ])
    corners = np.array([
        [0.0, 0.01, 0.0], [1.0, 1.0, 0.0],
        [0.0, 1.0, 1.0], [1.0, 0.01, 1.0],
        [0.5, 0.5, 0.0], [0.5, 0.5, 1.0],
    ])
    return np.vstack([corners, X])


def tf_forward(model, X: np.ndarray) -> np.ndarray:
    import tensorflow as tf

    return np.asarray(model(tf.constant(X, tf.float64), training=False)).reshape(-1)


def export_side(tag: str, model_dir: Path, out_npz: Path) -> dict:
    """导出单侧电极模型并对拍校验，返回 golden 数据块。"""
    import tensorflow as tf

    tf.keras.backend.set_floatx("float64")
    model = tf.keras.models.load_model(str(model_dir))
    weights = extract_weights(model)

    rng = np.random.default_rng(0)
    X = sample_inputs(N_CHECK, seed=int(rng.integers(0, 2 ** 31)))
    y_tf = tf_forward(model, X)
    y_np = network_forward(weights, X)
    max_diff = float(np.max(np.abs(y_tf - y_np)))
    if not np.isfinite(y_tf).all() or max_diff >= TOL:
        raise RuntimeError(
            f"{tag}: numpy 前向与 TF 前向不一致（max|Δ|={max_diff:.3e} ≥ {TOL}）——"
            "不得写出 npz，请检查 network_forward 与模型架构是否同构")
    print(f"  {tag}: TF vs numpy max|Δ| = {max_diff:.3e} (< {TOL})")

    np.savez(str(out_npz), **weights)
    reloaded = load_weights(out_npz)
    y_round = network_forward(reloaded, X)
    round_diff = float(np.max(np.abs(y_tf - y_round)))
    if round_diff >= TOL:
        raise RuntimeError(f"{tag}: npz 往返校验失败（max|Δ|={round_diff:.3e}）——npz 键序可能写错")
    print(f"  {tag}: npz 往返校验通过 (max|Δ| = {round_diff:.3e})")

    X_golden = sample_inputs(N_GOLDEN, seed=20261005)
    return {
        "model_dir": str(model_dir),
        "inputs": [[float(v) for v in row] for row in X_golden],
        "outputs": [float(v) for v in tf_forward(model, X_golden)],
        "check_max_abs_diff": max_diff,
        "roundtrip_max_abs_diff": round_diff,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ne-model", type=Path, default=DEFAULT_NE)
    parser.add_argument("--pe-model", type=Path, default=DEFAULT_PE)
    parser.add_argument("--system-dir", type=Path, default=DEFAULT_SYSTEM_DIR)
    args = parser.parse_args()

    import tensorflow as tf

    system_dir = args.system_dir.resolve()
    weights_dir = system_dir / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)

    golden = {
        "schema_version": "1.0",
        "kind": "pinn_spm_golden_io",
        "generated_at": datetime.now().isoformat(),
        "tf_version": tf.__version__,
        "tolerance": TOL,
        "note": "inputs=(s,ρ,bc_norm)，outputs=网络原始输出 x̄（未加 stoi1）。离线测试断言 "
                "pinn.spm_runner.network_forward(npz 权重) 复现 outputs。",
        "sides": {},
    }
    for side, model_dir in (("ne", args.ne_model), ("pe", args.pe_model)):
        print(f"[{side}] 导出 {model_dir}")
        golden["sides"][side] = export_side(
            side.upper(), model_dir.resolve(), weights_dir / f"discharge_{side}.npz")

    golden_path = system_dir / "golden_io.json"
    with open(golden_path, "w", encoding="utf-8") as f:
        json.dump(golden, f, ensure_ascii=False, indent=2)
    print(f"金样本已写入 {golden_path}")
    print("导出完成。下一步：在 registry.json 确认体系条目指向这些权重文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
