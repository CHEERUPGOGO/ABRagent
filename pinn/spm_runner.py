# -*- coding: utf-8 -*-
"""spm_runner.py — 万能族 SPM PINN 推理运行器（纯 numpy，零 TF/scipy 依赖）。

Stage 5 的 PINN 仿真入口。模型资产位于 pinn/models/（registry.json + systems/），
权重由 scripts/export_pinn_spm_weights.py 从 TF SavedModel 导出为 npz，本模块用
纯 numpy 复现同构前向（BN 推理式 + Dense ELU×2 + 线性头，float64）：

    TF 前向 vs numpy 前向 max|Δ| < 1e-9（导出时对拍锁定）；
    仓库内 golden_io.json 金样本测试持续锁定（test_stage5_pinn_inference.py）。

物理链路忠实移植 PINN-SPM-fast-prototyping/train_universal/predict_uni.py：
  容量/bc 解析映射 → 训练包络检查 → 网络前向 x = x̄ + stoi1 → OCV + Butler–Volmer
  装配 V(t)。与原型唯一的有意差异：BV 过电位中的电极厚度用生效厚度（原型用
  params 默认厚度 L0，与覆盖厚度时物理不一致；默认参数下两者相等，零差异）。

参数策略（spec 驱动的显式外部输入）：R_s、D_s、电极厚度、孔隙率、C-rate 五个
设计参数从 pinn_input_spec.json 的 cell_spec 显式提取输入（spec 未提供的字段
回退体系 params.json 训练基准值）。每个 PINN 体系在 registry `input_ranges`
中显式声明各输入参数的有效范围，调用网络前逐参数校验，越界直接拦截
（OUT_OF_RANGE，不执行仿真）；组合约束由训练包络 (|bc|/|bc_ref|) 第二层拦截
（OUT_OF_ENVELOPE）——两层都在网络前向之前完成。
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

MODELS_DIR = Path(__file__).resolve().parent / "models"

# 与 reference_spm_uni.py 一致的常量（registry envelope 缺省值）
T_WINDOW_1C = 3900.0          # 默认电极 1C 放电窗口 [s]
DEFAULT_BC_LO, DEFAULT_BC_HI, DEFAULT_BAND_MARGIN = 0.02, 15.0, 1.1
AMBIENT_T_K = 298.15          # 原型硬编码训练/推理温度
DEFAULT_C_RATE = 0.5

# ══════════════════════════════════════════════════════════════
# registry 与权重缓存（进程级；Gradio/Web 线程池调用，需加锁）
# ══════════════════════════════════════════════════════════════

_registry_cache: Dict[str, Tuple[float, dict]] = {}
_weights_cache: Dict[str, Tuple[float, Dict[str, np.ndarray]]] = {}
_params_cache: Dict[str, Tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()


def _cached_json(path: Path, cache: Dict[str, Tuple[float, dict]]) -> dict:
    key = str(path)
    mtime = path.stat().st_mtime
    with _CACHE_LOCK:
        hit = cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    with _CACHE_LOCK:
        cache[key] = (mtime, data)
    return data


def load_registry(models_dir: Optional[Path] = None) -> dict:
    """读模型注册表（按 mtime 缓存）。"""
    path = (models_dir or MODELS_DIR) / "registry.json"
    return _cached_json(path, _registry_cache)


def match_pinn_system(
    cathode_id: str,
    anode_id: str,
    models_dir: Optional[Path] = None,
) -> Optional[dict]:
    """按 (cathode, anode) id 精确匹配注册表体系；电解液不参与匹配。

    Returns:
        命中的 system 条目 dict；未命中返回 None。
    """
    if not cathode_id or not anode_id:
        return None
    for system in load_registry(models_dir).get("systems", []):
        m = system.get("match", {})
        if cathode_id in (m.get("cathode") or []) and anode_id in (m.get("anode") or []):
            return system
    return None


def load_weights(npz_path: Path) -> Dict[str, np.ndarray]:
    """加载 npz 权重（按 mtime 缓存）。"""
    npz_path = Path(npz_path)
    key = str(npz_path)
    mtime = npz_path.stat().st_mtime
    with _CACHE_LOCK:
        hit = _weights_cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    with np.load(npz_path) as z:
        weights = {k: np.asarray(z[k], dtype=np.float64) for k in z.files}
    with _CACHE_LOCK:
        _weights_cache[key] = (mtime, weights)
    return weights


def load_system_params(system: dict, models_dir: Optional[Path] = None) -> dict:
    """加载体系 params.json（按 mtime 缓存）。"""
    path = (models_dir or MODELS_DIR) / system["params"]
    return _cached_json(path, _params_cache)


# ══════════════════════════════════════════════════════════════
# 网络前向（numpy，与 TF SavedModel 同构）
# ══════════════════════════════════════════════════════════════

def network_forward(weights: Dict[str, np.ndarray], X: np.ndarray) -> np.ndarray:
    """万能族网络前向：X (N,3) = [s, ρ, bc_norm] → x̄ (N,)。

    结构：BN(推理式) → Dense(30, ELU) → Dense(30, ELU) → Dense(1, linear)。
    必须与 scripts/export_pinn_spm_weights.py 的导出键序严格对应。
    """
    X = np.asarray(X, dtype=np.float64)
    eps = float(weights["bn_eps"])
    z = (X - weights["bn_moving_mean"]) / np.sqrt(weights["bn_moving_var"] + eps)
    z = z * weights["bn_gamma"] + weights["bn_beta"]
    for prefix in ("d1", "d2"):
        z = z @ weights[f"{prefix}_kernel"] + weights[f"{prefix}_bias"]
        # ELU(alpha=1)：x>0 取 x，否则 exp(x)-1；minimum 防 z>0 时 expm1 溢出告警
        z = np.where(z > 0.0, z, np.expm1(np.minimum(z, 0.0)))
    return (z @ weights["d3_kernel"] + weights["d3_bias"])[:, 0]


# ══════════════════════════════════════════════════════════════
# 电极参数（移植 reference_spm_uni.Electrode 的解析映射）
# ══════════════════════════════════════════════════════════════

def _electrode_constants(params: dict, side: str) -> dict:
    """从体系 params.json 提取单电极解析常数（参考参数，训练包络的基准）。"""
    key = "negativeElectrode" if side == "ne" else "positiveElectrode"
    e = params[key]
    am = e["active_materials"][0]
    eps0 = float(e["porosity"]["value"])
    eps_s0 = float(am["volFrac_active"]["value"])
    L0 = float(e["thickness"]["value"])
    A = float(e["area"]["value"])
    R_s = float(am["particleRadius"]["value"])
    D_s = float(am["diffusionConstant"]["value"])
    c_max = float(am["maximumConcentration"]["value"])
    stoi1 = float(am["stoichiometry1"]["value"])
    stoi0 = float(am["stoichiometry0"]["value"])
    k0 = float(am["kineticConstant"]["value"])
    F = float(params["constants"]["F"]["value"])
    R = float(params["constants"]["R"]["value"])
    c_e0 = float(params["electrolyte"]["initialConcentration"]["value"])
    frac_inert = 1.0 - eps0 - eps_s0
    # Q0 用参考几何容量（Coulombs），与 Electrode._capacity(self.L0, self.eps_s0) 一致；
    # bc_ref 与 Electrode.bc_ref() 一致 = bc_value(Q0/3600, L0, eps0)，符号每电极固定
    Q0 = A * F * L0 * eps_s0 * c_max * abs(stoi1 - stoi0)
    bc_ref = (-1.0 if side == "ne" else 1.0) * \
        R_s ** 2 * (Q0 / 3600.0) / (D_s * 3 * eps_s0 * L0 * c_max * F * A)
    return {
        "side": side, "sign": -1.0 if side == "ne" else 1.0,
        "L0": L0, "A": A, "eps0": eps0, "eps_s0": eps_s0,
        "frac_inert": frac_inert, "R_s": R_s, "D_s": D_s,
        "c_max": c_max, "stoi1": stoi1, "stoi0": stoi0,
        "k0": k0, "F": F, "R": R, "c_e0": c_e0,
        "Q0": Q0, "bc_ref": bc_ref,
        "tau_ref": T_WINDOW_1C * D_s / R_s ** 2,
    }


def _interp_extrap(x: np.ndarray, xp: np.ndarray, fp: np.ndarray) -> np.ndarray:
    """线性插值 + 线性外推（与原型 scipy interp1d 默认 kind='linear' +
    fill_value='extrapolate' 逐位一致）。"""
    x = np.asarray(x, dtype=np.float64)
    y = np.interp(x, xp, fp)
    if x.size and (x[0] < xp[0] or x[-1] > xp[-1]):
        left = x < xp[0]
        if left.any():
            slope = (fp[1] - fp[0]) / (xp[1] - xp[0])
            y[left] = fp[0] + slope * (x[left] - xp[0])
        right = x > xp[-1]
        if right.any():
            slope = (fp[-1] - fp[-2]) / (xp[-1] - xp[-2])
            y[right] = fp[-1] + slope * (x[right] - xp[-1])
    return y


def _load_ocv(params: dict, side: str, models_dir: Path, system: dict) -> Tuple[np.ndarray, np.ndarray]:
    """读电极 pOCV 曲线 (x [-], U [V])。"""
    key = "negativeElectrode" if side == "ne" else "positiveElectrode"
    fname = params[key]["active_materials"][0]["openCircuitPotential"]["value"]
    path = (models_dir or MODELS_DIR) / Path(system["params"]).parent / fname
    data = np.loadtxt(path)
    # pOCV 文件可能是降序排列（PE 曲线），插值要求升序（原型 scipy interp1d 亦会排序）
    order = np.argsort(data[:, 0])
    return data[order, 0], data[order, 1]


# ══════════════════════════════════════════════════════════════
# cell_spec 读取与覆盖门控
# ══════════════════════════════════════════════════════════════

def _spec_get(cell_spec: Any, path: str) -> Any:
    """按 'a.b.c' 路径读 cell_spec（支持 CellSpec 对象与 to_dict() 后的 dict）。"""
    if hasattr(cell_spec, "to_dict"):
        cell_spec = cell_spec.to_dict()
    cur: Any = cell_spec
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _spec_design_inputs(cell_spec: Any) -> Tuple[Dict[str, Dict[str, Optional[float]]], Dict[str, Dict[str, str]]]:
    """提取 spec 显式设计输入：R_s / D_s / 厚度 / 孔隙率（PINN 的外部输入）。

    cell_spec 有值即采用（不做 provenance 门控），无值 (None) 回退体系训练参数。
    cell_spec.cathode → pe（正极），cell_spec.anode → ne（负极）。

    Returns:
        (inputs, sources)：inputs[side][field] 为 spec 值或 None；
        sources[side][field] 为该字段 provenance 来源（未标注则 "unspecified"）。
    """
    provenance = _spec_get(cell_spec, "provenance") or {}
    inputs: Dict[str, Dict[str, Optional[float]]] = {}
    sources: Dict[str, Dict[str, str]] = {}
    for side, comp in (("ne", "anode"), ("pe", "cathode")):
        vals: Dict[str, Optional[float]] = {}
        srcs: Dict[str, str] = {}
        for field, path in (
            ("thickness", f"{comp}.L"),
            ("porosity", f"{comp}.epsilon"),
            ("R_s", f"{comp}.material.R_p"),
            ("D_s", f"{comp}.material.D_s"),
        ):
            value = _spec_get(cell_spec, path)
            vals[field] = float(value) if isinstance(value, (int, float)) else None
            srcs[field] = (provenance.get(path) or {}).get("source") or "unspecified"
        inputs[side] = vals
        sources[side] = srcs
    return inputs, sources


def _resolve_c_rate(cell_spec: Any, c_rate: Optional[float]) -> Tuple[float, str]:
    """C-rate 来源解析：spec condition.c_rate 显式输入优先，其次调用参数，缺省 0.5。"""
    spec_c_rate = _spec_get(cell_spec, "condition.c_rate")
    if isinstance(spec_c_rate, (int, float)) and spec_c_rate > 0:
        return float(spec_c_rate), "spec"
    if isinstance(c_rate, (int, float)) and c_rate > 0:
        return float(c_rate), "argument"
    return DEFAULT_C_RATE, "default"


def _range_violations(
    eff: Dict[str, Dict[str, float]],
    c_rate: float,
    system_id: str,
    input_ranges: Any,
) -> List[str]:
    """逐参数有效范围校验（registry input_ranges，调用网络前的第一层拦截）。"""
    violations: List[str] = []
    if not isinstance(input_ranges, dict):
        return violations

    def _in_range(v: float, lo: Any, hi: Any) -> bool:
        # 含相对容差的闭区间: 换算/浮点表示可能让恰在边界上的值偏离 1 ULP
        tol = 1e-9
        return (lo * (1 - tol) <= v <= hi * (1 + tol))

    for side in ("ne", "pe"):
        ranges = input_ranges.get(side) or {}
        for field in ("thickness", "porosity", "R_s", "D_s"):
            r = ranges.get(field)
            # registry 声明用 thickness，eff 内部键为 L（电极厚度）
            value = eff[side]["L" if field == "thickness" else field]
            if not isinstance(r, dict) or value is None:
                continue
            lo, hi = r.get("min"), r.get("max")
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) \
                    and not _in_range(value, lo, hi):
                unit = f" {r['unit']}" if r.get("unit") else ""
                violations.append(
                    f"{side}.{field}={value:g} 超出体系 {system_id} 有效范围 [{lo:g}, {hi:g}]{unit}；"
                    f"请修正 pinn_input_spec.json 的该字段或更新注册表范围")
    rc = input_ranges.get("c_rate")
    if isinstance(rc, dict):
        lo, hi = rc.get("min"), rc.get("max")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) \
                and not (lo <= c_rate <= hi):
            unit = f" {rc['unit']}" if rc.get("unit") else ""
            violations.append(
                f"c_rate={c_rate:g} 超出体系 {system_id} 有效范围 [{lo:g}, {hi:g}]{unit}")
    return violations


# ══════════════════════════════════════════════════════════════
# 放电仿真主入口
# ══════════════════════════════════════════════════════════════

def run_pinn_discharge(
    cell_spec: Any,
    scheme: Optional[Dict[str, Any]] = None,
    c_rate: Optional[float] = None,
    system: Optional[dict] = None,
    models_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """对匹配体系执行 SPM PINN 放电仿真。

    Args:
        cell_spec: pinn_input_spec.json 的 cell_spec 块（dict 或 CellSpec 对象）。
            显式外部输入：厚度 L、孔隙率 ε、R_p(→R_s)、D_s、condition.c_rate
            （有值即采用，无值回退体系训练参数）。
        scheme: Stage 4 方案 dict（{"cathode","anode",...}，用于 id 与折算系数）。
        c_rate: 放电倍率回退值（spec condition.c_rate 缺失时使用；再缺省 0.5）。
        system: registry 命中的体系条目；None 时按 cell_spec 材料名自匹配。
        models_dir: 模型资产目录（测试可注入临时目录）。

    Returns:
        PINN 仿真结果 dict (扁平标量契约 + pinn 诊断块)。
        status: CONVERGED | OUT_OF_RANGE | OUT_OF_ENVELOPE |
        NO_MATCH | ERROR（两类越界均在网络前向之前拦截，不产出仿真结果）。
    """
    models_dir = Path(models_dir or MODELS_DIR)
    scheme = scheme or {}
    try:
        if system is None:
            cathode_id = scheme.get("cathode") or _spec_get(cell_spec, "cathode.material.name") or ""
            anode_id = scheme.get("anode") or _spec_get(cell_spec, "anode.material.name") or ""
            system = match_pinn_system(cathode_id, anode_id, models_dir)
            if system is None:
                return {"status": "NO_MATCH", "error": "cell_spec 材料体系未匹配任何注册表 PINN"}
        params = load_system_params(system, models_dir)
        envelope = system.get("envelope", {})
        bc_lo = float(envelope.get("bc_lo", DEFAULT_BC_LO))
        bc_hi = float(envelope.get("bc_hi", DEFAULT_BC_HI))
        band_margin = float(envelope.get("band_margin", DEFAULT_BAND_MARGIN))

        ref = {s: _electrode_constants(params, s) for s in ("ne", "pe")}
        spec_inputs, spec_sources = _spec_design_inputs(cell_spec)
        c_rate_eff, c_rate_source = _resolve_c_rate(cell_spec, c_rate)
        eff: Dict[str, Dict[str, float]] = {}
        for side in ("ne", "pe"):
            base, si = ref[side], spec_inputs[side]
            por = si["porosity"] if si["porosity"] is not None else base["eps0"]
            eps_s = 1.0 - base["frac_inert"] - por
            if eps_s <= 0:
                return {"status": "ERROR", "error": f"{side} 孔隙率输入导致活性体积分数 <= 0"}
            eff[side] = {
                "L": si["thickness"] if si["thickness"] is not None else base["L0"],
                "porosity": por,
                "eps_s": eps_s,
                "R_s": si["R_s"] if si["R_s"] is not None else base["R_s"],
                "D_s": si["D_s"] if si["D_s"] is not None else base["D_s"],
            }

        # N/P 一致性: 声明 n_p_ratio 时, 负极厚度按容量比 Q_n/Q_p = N/P 推导
        # （声明几何与 N/P 自相矛盾时以 N/P 为准——系统级约束; 调整记录进 warnings）
        sim_warnings: List[str] = []
        n_p_declared = _spec_get(cell_spec, "anode.N_P_ratio")
        if isinstance(n_p_declared, (int, float)) and n_p_declared > 0:
            F0 = ref["ne"]["F"]
            q_ne = (ref["ne"]["A"] * F0 * eff["ne"]["L"] * eff["ne"]["eps_s"]
                    * ref["ne"]["c_max"] * abs(ref["ne"]["stoi1"] - ref["ne"]["stoi0"]))
            q_pe = (ref["pe"]["A"] * F0 * eff["pe"]["L"] * eff["pe"]["eps_s"]
                    * ref["pe"]["c_max"] * abs(ref["pe"]["stoi1"] - ref["pe"]["stoi0"]))
            if q_ne > 0 and q_pe > 0:
                implied = q_ne / q_pe
                if abs(implied - float(n_p_declared)) / float(n_p_declared) > 0.05:
                    eff["ne"]["L"] = eff["ne"]["L"] * (float(n_p_declared) / implied)
                    sim_warnings.append(
                        f"声明几何的隐含 N/P={implied:.2f} 与声明 N/P={float(n_p_declared):g} 不一致, "
                        f"负极厚度已按 N/P 调整为 {eff['ne']['L'] * 1e6:.1f} μm (系统级约束优先)")

        # 容量、电流、bc（容量与 R_s/D_s 无关）
        F = ref["ne"]["F"]
        Q = {}
        for side in ("ne", "pe"):
            b, e = ref[side], eff[side]
            Q[side] = b["A"] * F * e["L"] * e["eps_s"] * b["c_max"] * abs(b["stoi1"] - b["stoi0"])
        I = min(Q["ne"], Q["pe"]) / 3600.0 * c_rate_eff
        limiting = "ne" if Q["ne"] <= Q["pe"] else "pe"
        bc = {}
        for side in ("ne", "pe"):
            b, e = ref[side], eff[side]
            bc[side] = b["sign"] * e["R_s"] ** 2 * I / (
                e["D_s"] * 3 * e["eps_s"] * e["L"] * b["c_max"] * F * b["A"])

        # 工作点 u（对照参考电极固定 bc_ref）——两层拦截都要用，先于拦截计算
        u = {side: abs(bc[side]) / abs(ref[side]["bc_ref"]) for side in ("ne", "pe")}

        def _intercept_payload(status: str, violations: List[str]) -> Dict[str, Any]:
            """网络前向之前的拦截产物（越界不执行仿真，诊断回显辅助定位）。"""
            return {
                "status": status,
                "error": "；".join(violations),
                "pinn": {
                    "system_id": system["system_id"], "direction": "discharge",
                    "envelope_ok": False, "violations": violations,
                    "u_n": u["ne"], "u_p": u["pe"], "bc_n": float(bc["ne"]), "bc_p": float(bc["pe"]),
                    "c_rate": c_rate_eff, "c_rate_source": c_rate_source,
                    "spec_inputs": {
                        side: {f: {"value": spec_inputs[side][f], "source": spec_sources[side][f]}
                               for f in spec_inputs[side] if spec_inputs[side][f] is not None}
                        for side in ("ne", "pe")
                    },
                    "design": {s: dict(eff[s]) for s in ("ne", "pe")},
                },
            }

        # 第一层拦截：逐参数有效范围（registry input_ranges 显式声明）
        range_violations = _range_violations(eff, c_rate_eff, system["system_id"],
                                             system.get("input_ranges"))
        if range_violations:
            return _intercept_payload("OUT_OF_RANGE", range_violations)

        # 第二层拦截：训练包络组合约束（防"单参数合规、组合越界"逃过第一层；
        # 含相对容差，边界值不因浮点表示漂移而误判）
        violations = []
        env_tol = 1e-9
        for side in ("ne", "pe"):
            if not (bc_lo * (1 - env_tol) <= u[side] <= bc_hi * (1 + env_tol)):
                violations.append(
                    f"{side}: |bc|/|bc_ref| = {u[side]:.4g} 超出训练包络 [{bc_lo}, {bc_hi}]（外推无精度保证）")
        if violations:
            return _intercept_payload("OUT_OF_ENVELOPE", violations)

        # 每电极时间带 → 表面化学计量（网络前向 ρ=1）
        surf: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        t_ends: Dict[str, float] = {}
        n_band = 100
        for side in ("ne", "pe"):
            b, e = ref[side], eff[side]
            h = u[side]
            s = np.linspace(0.0, 1.0, n_band)
            t_side = s * band_margin / h * b["tau_ref"] * e["R_s"] ** 2 / e["D_s"]
            X = np.stack([s, np.ones(n_band), np.full(n_band, (u[side] - bc_lo) / (bc_hi - bc_lo))], 1)
            weights = load_weights(models_dir / system["directions"]["discharge"][side])
            xs = network_forward(weights, X) + b["stoi1"]
            surf[side] = (t_side, xs)
            t_ends[side] = float(t_side[-1])

        t = np.linspace(0.0, min(t_ends.values()), n_band)
        xs_n = np.interp(t, *surf["ne"])
        xs_p = np.interp(t, *surf["pe"])

        # 电压：pOCV + Butler–Volmer（生效厚度；默认参数下与原型逐位一致）
        ocv_x = {s: _load_ocv(params, s, models_dir, system) for s in ("ne", "pe")}

        def eta_of(side: str, xs: np.ndarray, sign: float) -> np.ndarray:
            b, e = ref[side], eff[side]
            with np.errstate(divide="ignore", invalid="ignore"):
                return 2 * b["R"] * AMBIENT_T_K / F * np.arcsinh(
                    sign * e["R_s"] * I / (
                        2 * b["k0"] * math.sqrt(b["c_e0"]) * 3 * e["eps_s"] * e["L"] *
                        b["c_max"] * F * b["A"] *
                        np.sqrt(np.maximum(xs * (1.0 - xs), 0.0))))

        eta_n = eta_of("ne", xs_n, +1.0)
        eta_p = eta_of("pe", xs_p, -1.0)
        V = (_interp_extrap(xs_p, *ocv_x["pe"])
             - _interp_extrap(xs_n, *ocv_x["ne"])
             + eta_p - eta_n)
        valid = (xs_n > 0) & (xs_n < 1) & (xs_p > 0) & (xs_p < 1)
        V = np.where(valid, V, np.nan)

        # 截止：电压下限（v_min）或化学计量耗尽（NaN）
        # 来源优先级: spec condition.voltage_min → 体系 registry post_processing → 2.5V
        v_min = _spec_get(cell_spec, "condition.voltage_min")
        if not isinstance(v_min, (int, float)):
            v_min = (system.get("post_processing") or {}).get("v_min_cutoff_V", 2.5)
        v_min = float(v_min)
        t_end, termination = t[-1], "curve_end"
        for i in range(1, t.size):
            if not np.isfinite(V[i]) or V[i] < v_min:
                if np.isfinite(V[i]) and np.isfinite(V[i - 1]):
                    t_end = t[i - 1] + (t[i] - t[i - 1]) * (v_min - V[i - 1]) / (V[i] - V[i - 1])
                    termination = "voltage_cutoff"
                else:
                    t_end = t[i - 1]
                    termination = "stoichiometry_exhausted"
                break
        mask = np.isfinite(V) & (t <= t_end)
        if not mask.any() or t_end <= 0:
            return {"status": "ERROR", "error": "放电曲线全程无效（参数远离训练域）"}

        # 标量（Ah/kg 与 mAh/g 数值相等；1 mAh/g = 1 Ah/kg）
        avg_V = float(np.mean(V[mask]))
        q_end_ah = I * float(t_end) / 3600.0
        e_pe, b_pe = eff["pe"], ref["pe"]
        # 后处理常数与体系绑定（registry post_processing）：正极真密度、能量折算系数
        post = system.get("post_processing") or {}
        rho = float(post.get("rho_cathode_kg_m3", 4900.0))
        m_active_kg = b_pe["A"] * e_pe["L"] * e_pe["eps_s"] * rho
        specific_capacity = q_end_ah / m_active_kg          # mAh/g
        material_energy = specific_capacity * avg_V          # Wh/kg（材料级）
        active_ratio = float(post.get("cell_energy_ratio", 0.45))
        cell_energy = material_energy * active_ratio         # Wh/kg（电芯级折算）

        # 训练报告参考解 RMS → pde_residual_loss（对齐 legacy 契约的残差指标）
        residual = None
        for side in ("ne", "pe"):
            report_path = models_dir / Path(system["params"]).parent / f"train_report_{side}.json"
            if report_path.exists():
                report = _cached_json(report_path, _params_cache)
                rms = (report.get("ref_check") or {}).get("rms")
                if isinstance(rms, (int, float)):
                    residual = rms if residual is None else max(residual, rms)

        return {
            "status": "CONVERGED",
            "model": "pinn_spm_universal",
            "solver": "pinn_spm",
            "system_id": system["system_id"],
            "direction": "discharge",
            "c_rate": c_rate_eff,
            "c_rate_source": c_rate_source,
            "convergence": "Converged",
            "is_fallback": False,
            "specific_capacity_mAh_g": specific_capacity,
            "q_end_mAh_g": specific_capacity,
            "average_voltage_V": avg_V,
            "v_mean": avg_V,
            "energy_wh_kg": cell_energy,
            "calculated_cell_energy_wh_kg": cell_energy,
            "material_energy_Wh_kg": material_energy,
            "q_end_Ah": q_end_ah,
            "current_A": I,
            "limiting_electrode": limiting,
            "termination": termination,
            "v_min_cutoff_V": v_min,
            "t_end_s": float(t_end),
            "pde_residual_loss": residual,
            "envelope_ok": True,
            "discharge_curve": {
                # 比容量口径 (mAh/g) = I·t/3600 / m_active，与标量 specific_capacity 同单位
                "capacity": [float(c) for c in (I * t[mask] / 3600.0 / m_active_kg)],
                "voltage": [float(v) for v in V[mask]],
            },
            "pinn": {
                "system_id": system["system_id"],
                "direction": "discharge",
                "envelope_ok": True,
                "violations": [],
                "u_n": u["ne"], "u_p": u["pe"],
                "bc_n": float(bc["ne"]), "bc_p": float(bc["pe"]),
                "c_rate": c_rate_eff, "c_rate_source": c_rate_source,
                "spec_inputs": {
                    side: {f: {"value": spec_inputs[side][f], "source": spec_sources[side][f]}
                           for f in spec_inputs[side] if spec_inputs[side][f] is not None}
                    for side in ("ne", "pe")
                },
                "design": {s: dict(eff[s]) for s in ("ne", "pe")},
                "capacity_Ah": {"ne": Q["ne"] / 3600.0, "pe": Q["pe"] / 3600.0},
                "weights": {s: system["directions"]["discharge"][s] for s in ("ne", "pe")},
                "temperature_K": AMBIENT_T_K,
                "t": [float(x) for x in t[mask]],
                "x_n_surface": [float(x) for x in xs_n[mask]],
                "x_p_surface": [float(x) for x in xs_p[mask]],
            },
            "warnings": sim_warnings,
        }
    except Exception as e:  # fail-closed：任何推理异常都不得让工作流崩溃
        return {"status": "ERROR", "error": f"{type(e).__name__}: {e}"}


def run_pinn_charge(
    cell_spec: Any,
    scheme: Optional[Dict[str, Any]] = None,
    c_rate: float = 0.5,
    system: Optional[dict] = None,
    models_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """充电方向占位：registry 的 directions.charge 配对位就绪后即可启用。

    充电模型训练导出（weights/charge_{ne,pe}.npz + registry 增条目）后，
    本函数按放电同构链路实现；当前恒返回 CHARGE_NOT_AVAILABLE。
    """
    models_dir = Path(models_dir or MODELS_DIR)
    if system is None:
        scheme = scheme or {}
        cathode_id = scheme.get("cathode") or _spec_get(cell_spec, "cathode.material.name") or ""
        anode_id = scheme.get("anode") or _spec_get(cell_spec, "anode.material.name") or ""
        system = match_pinn_system(cathode_id, anode_id, models_dir)
    if system is None:
        return {"status": "NO_MATCH", "error": "cell_spec 材料体系未匹配任何注册表 PINN"}
    if not system.get("directions", {}).get("charge"):
        return {
            "status": "CHARGE_NOT_AVAILABLE",
            "error": f"体系 {system['system_id']} 尚无成对充电模型（registry directions.charge 为空）",
            "system_id": system["system_id"],
        }
    return {"status": "ERROR", "error": "充电模型已注册但推理链路尚未实现（待充电模型接入）"}
