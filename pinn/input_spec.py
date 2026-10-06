# -*- coding: utf-8 -*-
"""input_spec.py — pinn_input_spec.json 的 cell_spec 构建器（无缺省表回填）.

Stage 5 参数提取的 spec 组装契约：只填充**真实数据来源**的字段——
  1. Stage 4 方案 (scheme)：材料 id、添加剂、设计目标、面载量；
  2. candidates.json 材料级参数：formula / capacity / avg_voltage /
     voltage_limit / weakness / oxidation_window（含其自带 provenance）；
  3. 测试条件：c_rate / 温度（由调用方传入）。

物理动力学字段 (c_max / D_s / R_p / k_ref / 孔隙率 / 厚度 / 电解液输运参数等)
一律保持 null——它们由 PINN 体系注册表的 params.json 提供 (训练基准)，或由
spec 显式输入 (见 pinn/spm_runner.py 的 spec 驱动语义)。不做任何通用缺省表
回填，杜绝"工程估值冒充体系数据"。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# extraction_summary 的统计口径 (None/空串视为"未提取到")
MATERIAL_PARAM_FIELDS = (
    "formula", "c_max", "theoretical_capacity", "stoich_min", "stoich_max",
    "D_s", "k_ref", "Ea_Ds", "Ea_k", "R_p", "sigma", "avg_voltage",
    "voltage_limit", "U_ocp",
)
ELECTROLYTE_PARAM_FIELDS = (
    "composition", "c_e0", "D_e", "t_plus", "kappa",
    "oxidation_window", "reduction_stability",
)
ELECTRODE_GEOMETRY_FIELDS = ("L", "epsilon", "epsilon_s", "mass_loading")

# mg/cm² → kg/m²（面载量换算，1 mg/cm² = 0.01 kg/m²）
_MG_CM2_TO_KG_M2 = 0.01


def _material(name: str, component: str, candidates_entry: Optional[dict]) -> dict:
    """材料块：candidates 提供材料级数据，物理动力学字段保持 null。"""
    mat = {
        "name": name or "",
        "formula": "", "component": component, "model": None,
        "c_max": None, "theoretical_capacity": None,
        "stoich_min": None, "stoich_max": None,
        "D_s": None, "k_ref": None, "Ea_Ds": None, "Ea_k": None,
        "R_p": None, "sigma": None,
        "avg_voltage": None, "voltage_limit": None, "U_ocp": None,
        "weakness": "",
    }
    provenance = {"source": "scheme", "confidence": "high", "ref": "scheme.cathode/anode"}
    if candidates_entry:
        if candidates_entry.get("formula"):
            mat["formula"] = candidates_entry["formula"]
        cap = candidates_entry.get("capacity") or {}
        if cap.get("value") is not None:
            mat["theoretical_capacity"] = float(cap["value"])  # Ah/kg ≡ mAh/g
        for key in ("avg_voltage", "voltage_limit"):
            if candidates_entry.get(key) is not None:
                mat[key] = float(candidates_entry[key])
        if candidates_entry.get("weakness"):
            mat["weakness"] = candidates_entry["weakness"]
        prov = candidates_entry.get("provenance") or {}
        provenance = {
            "source": prov.get("source") or "candidates",
            "confidence": prov.get("confidence") or "medium",
            "ref": f"candidates.json:{name}",
        }
    return mat, provenance


def _electrode(component: str, name: str, candidates_entry: Optional[dict],
               scheme: dict) -> Tuple[dict, Dict[str, dict]]:
    """电极块：材料 + 电极几何（几何仅 scheme 显式给出的面载量）。"""
    mat, mat_prov = _material(name, component, candidates_entry)
    electrode = {
        "component": component, "material": mat,
        "L": None, "epsilon": None, "epsilon_s": None,
        "epsilon_f": None, "epsilon_b": None,
        "mass_loading": None, "area": None, "N_P_ratio": None,
    }
    provenance = {f"{component}.material": mat_prov}
    loading = scheme.get("loading_mg_cm2")
    if isinstance(loading, (int, float)):
        electrode["mass_loading"] = float(loading) * _MG_CM2_TO_KG_M2
        provenance[f"{component}.mass_loading"] = {
            "source": "scheme", "confidence": "high", "ref": "scheme.loading_mg_cm2"}
    return electrode, provenance


# ══════════════════════════════════════════════════════════════
# Stage 4 产物契约 v1.1: 显式完整几何声明块 (design_scheme.json scheme.geometry)
# ══════════════════════════════════════════════════════════════

def complete_geometry_block(
    raw_geometry: Optional[Dict[str, Any]],
    cathode_id: Optional[str],
    anode_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """把叙事正则提取的稀疏 geometry 归一化为显式完整声明块（产物契约 v1.1）.

    每个 PINN 输入字段逐字段给出值与来源 provenance:
      - stage4_recipe:   叙事正则提取（含载量/压实换算厚度）；
      - params_baseline: 叙事未写, 体系 params.json/registry 训练基准兜底;
      - unmatched:       体系未匹配, 无基准可兜底（字段保持 null, 不虚构）。

    Returns:
        完整 geometry 块；体系未匹配且叙事无任何几何值时返回 None（调用方保持原样）。
    """
    from pinn.spm_runner import MODELS_DIR, load_system_params, match_pinn_system

    raw = raw_geometry if isinstance(raw_geometry, dict) else {}
    raw_has_values = any(
        isinstance(raw.get(comp), dict) and raw.get(comp)
        for comp in ("cathode", "anode")
    ) or bool(raw.get("n_p_ratio")) or bool(raw.get("condition"))
    system = match_pinn_system(cathode_id or "", anode_id or "") if (cathode_id or anode_id) else None
    if system is None and not raw_has_values:
        return None

    base_params: Optional[dict] = None
    if system is not None:
        try:
            base_params = load_system_params(system)
        except Exception:
            base_params = None
    post = (system or {}).get("post_processing") or {}

    def baseline(side: str) -> Dict[str, Any]:
        if base_params is None:
            return {}
        key = "negativeElectrode" if side == "ne" else "positiveElectrode"
        e = base_params.get(key) or {}
        am = (e.get("active_materials") or [{}])[0] if e.get("active_materials") else {}
        l_m = (e.get("thickness") or {}).get("value")
        r_m = (am.get("particleRadius") or {}).get("value")
        return {
            "L_um": float(l_m) * 1e6 if isinstance(l_m, (int, float)) else None,      # m → μm
            "porosity": (e.get("porosity") or {}).get("value"),
            "R_s_um": float(r_m) * 1e6 if isinstance(r_m, (int, float)) else None,    # m → μm
            "D_s_m2s": (am.get("diffusionConstant") or {}).get("value"),
        }

    def num(value: Any) -> Optional[float]:
        return float(value) if isinstance(value, (int, float)) else None

    block: Dict[str, Any] = {
        "schema_version": "1.1",
        "cathode": {}, "anode": {},
        "provenance": {},
    }
    for comp, side in (("cathode", "pe"), ("anode", "ne")):
        raw_side = raw.get(comp) or {}
        raw_side = raw_side if isinstance(raw_side, dict) else {}
        b = baseline(side)
        out: Dict[str, Any] = {}

        # 厚度: 叙事显式厚度 > 载量/压实换算 > 体系基准
        t_um = num(raw_side.get("thickness_um"))
        t_source = "stage4_recipe" if t_um is not None else None
        if t_um is None:
            loading, comp_d = num(raw_side.get("loading_mg_cm2")), num(raw_side.get("compaction_g_cm3"))
            if loading is not None and comp_d:
                t_um = loading / comp_d * 10.0  # mg/cm² ÷ g/cm³ = 1e-3 cm = 10 μm
                t_source = "stage4_recipe"
        if t_um is None and b.get("L_um") is not None:
            t_um = num(b.get("L_um"))  # baseline 已归一为 μm
            t_source = "params_baseline"
        out["L_um"] = t_um
        block["provenance"][f"{comp}.L_um"] = t_source or "unmatched"

        # 孔隙率: 叙事 % → 小数 > 体系基准
        por = num(raw_side.get("porosity_pct"))
        por_source = "stage4_recipe" if por is not None else None
        por = por / 100.0 if por is not None else None
        if por is None and b.get("porosity") is not None:
            por = num(b.get("porosity"))
            por_source = "params_baseline"
        out["porosity"] = por
        block["provenance"][f"{comp}.porosity"] = por_source or "unmatched"

        # R_s / D_s: 暂无叙事来源, 体系基准
        for field in ("R_s_um", "D_s_m2s"):
            v = num(b.get(field))
            out[field] = v
            block["provenance"][f"{comp}.{field}"] = "params_baseline" if v is not None else "unmatched"

        # 信息性原值 (叙事原样, 不参与仿真)
        out["loading_mg_cm2"] = num(raw_side.get("loading_mg_cm2"))
        out["compaction_g_cm3"] = num(raw_side.get("compaction_g_cm3"))
        block[comp] = out

    n_p = num(raw.get("n_p_ratio")) if raw.get("n_p_ratio") is not None else None
    block["n_p_ratio"] = n_p
    block["provenance"]["n_p_ratio"] = "stage4_recipe" if n_p is not None else "unmatched"

    cond_raw = raw.get("condition") if isinstance(raw.get("condition"), dict) else {}
    v_min = num(cond_raw.get("v_min"))
    v_max = num(cond_raw.get("v_max"))
    if v_min is None and post.get("v_min_cutoff_V") is not None:
        v_min = num(post.get("v_min_cutoff_V"))
        block["provenance"]["condition.v_min"] = "params_baseline"
    else:
        block["provenance"]["condition.v_min"] = "stage4_recipe" if v_min is not None else "unmatched"
    block["provenance"]["condition.v_max"] = "stage4_recipe" if v_max is not None else "unmatched"
    block["condition"] = {"v_min": v_min, "v_max": v_max}
    return block


def format_geometry_markdown(geometry: Dict[str, Any]) -> str:
    """把显式完整几何块渲染为 design_scheme.md 尾部的声明表（报告第 3 节随用随取）。"""
    if not isinstance(geometry, dict) or not geometry.get("provenance"):
        return ""
    prov = geometry.get("provenance") or {}
    src_cn = {"stage4_recipe": "叙事提取", "params_baseline": "体系基准", "unmatched": "无基准"}

    def cell(value: Any, spec: str = "{:.4g}") -> str:
        return spec.format(float(value)) if isinstance(value, (int, float)) else "—"

    lines = [
        "**设计点几何声明**（Stage 4 显式给出，Stage 5 PINN 仿真输入）：",
        "",
        "| 电极 | 厚度 L (μm) | 孔隙率 | R_s (μm) | D_s (m²/s) | 面载量 (mg/cm²) | 压实 (g/cm³) | 来源 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name_cn, comp in (("正极", "cathode"), ("负极", "anode")):
        d = geometry.get(comp) or {}
        fields = ("L_um", "porosity", "R_s_um", "D_s_m2s")
        sources = sorted({src_cn.get(prov.get(f"{comp}.{f}"), prov.get(f"{comp}.{f}", "—")) for f in fields})
        lines.append(
            f"| {name_cn} | {cell(d.get('L_um'))} | {cell(d.get('porosity'))} | {cell(d.get('R_s_um'))} "
            f"| {cell(d.get('D_s_m2s'), '{:.2e}')} | {cell(d.get('loading_mg_cm2'))} "
            f"| {cell(d.get('compaction_g_cm3'))} | {'/'.join(sources)} |"
        )
    n_p = geometry.get("n_p_ratio")
    cond = geometry.get("condition") or {}
    window = "—"
    if isinstance(cond.get("v_min"), (int, float)) and isinstance(cond.get("v_max"), (int, float)):
        window = f"{cond['v_min']:g}–{cond['v_max']:g} V"
    elif isinstance(cond.get("v_min"), (int, float)):
        window = f"截止 {cond['v_min']:g} V"
    lines.append("")
    lines.append(f"N/P 比：{cell(n_p)}；电压窗口：{window}。来源口径：叙事提取=方案文本明确给出的设计值；体系基准=该 PINN 体系训练参数（params.json）。")
    return "\n".join(lines)


def _apply_recipe_geometry(cell_spec: Dict[str, Any], geometry: Dict[str, Any]) -> None:
    """Stage 4 配方几何 → cell_spec 显式输入.

    优先消费 v1.1 显式 schema (L_um/porosity/R_s_um/D_s_m2s/condition.*)，
    旧键 (thickness_um/porosity_pct/载量+压实) 保留兼容；provenance 逐字段
    取自 geometry.provenance，流入 PINN 结果的 spec_inputs 供报告标注来源。
    缺失字段不写（保持 None, 由 PINN 逐字段回退体系训练参数）。
    """
    prov_block = geometry.get("provenance") if isinstance(geometry.get("provenance"), dict) else {}

    def src(comp: str, field: str, default: str = "stage4_recipe") -> Dict[str, str]:
        return {"source": prov_block.get(f"{comp}.{field}", default),
                "confidence": "medium",
                "ref": "design_scheme.json scheme.geometry"}

    for comp in ("cathode", "anode"):
        g = geometry.get(comp) or {}
        if not isinstance(g, dict):
            continue
        electrode = cell_spec.get(comp) or {}
        material = electrode.setdefault("material", {})
        # v1.1: 厚度/孔隙率 (直接值)
        if isinstance(g.get("L_um"), (int, float)) and g["L_um"] > 0:
            electrode["L"] = float(g["L_um"]) * 1e-6
            cell_spec["provenance"][f"{comp}.L"] = src(comp, "L_um")
        if isinstance(g.get("porosity"), (int, float)) and 0 < float(g["porosity"]) < 1:
            electrode["epsilon"] = float(g["porosity"])
            cell_spec["provenance"][f"{comp}.epsilon"] = src(comp, "porosity")
        # v1.1: R_s / D_s
        if isinstance(g.get("R_s_um"), (int, float)) and g["R_s_um"] > 0:
            material["R_p"] = float(g["R_s_um"]) * 1e-6
            cell_spec["provenance"][f"{comp}.material.R_p"] = src(comp, "R_s_um")
        if isinstance(g.get("D_s_m2s"), (int, float)) and g["D_s_m2s"] > 0:
            material["D_s"] = float(g["D_s_m2s"])
            cell_spec["provenance"][f"{comp}.material.D_s"] = src(comp, "D_s_m2s")
        # 旧 schema 兼容: 显式厚度 > 载量/压实换算 > 孔隙率 %
        if electrode.get("L") is None and isinstance(g.get("thickness_um"), (int, float)) and g["thickness_um"] > 0:
            electrode["L"] = float(g["thickness_um"]) * 1e-6
            cell_spec["provenance"][f"{comp}.L"] = src(comp, "thickness_um")
        if electrode.get("L") is None and isinstance(g.get("loading_mg_cm2"), (int, float)) \
                and isinstance(g.get("compaction_g_cm3"), (int, float)) and g["compaction_g_cm3"] > 0:
            electrode["L"] = float(g["loading_mg_cm2"]) / float(g["compaction_g_cm3"]) * 1e-5
            cell_spec["provenance"][f"{comp}.L"] = src(comp, "loading_mg_cm2")
        if electrode.get("epsilon") is None and isinstance(g.get("porosity_pct"), (int, float)) \
                and 0 < float(g["porosity_pct"]) < 100:
            electrode["epsilon"] = float(g["porosity_pct"]) / 100.0
            cell_spec["provenance"][f"{comp}.epsilon"] = src(comp, "porosity_pct")
        # 面载量 (两 schema 同名): mg/cm² → kg/m²
        if isinstance(g.get("loading_mg_cm2"), (int, float)) and g["loading_mg_cm2"] > 0:
            electrode["mass_loading"] = float(g["loading_mg_cm2"]) * _MG_CM2_TO_KG_M2
            cell_spec["provenance"][f"{comp}.mass_loading"] = src(comp, "loading_mg_cm2")

    # N/P 比
    n_p = geometry.get("n_p_ratio")
    if isinstance(n_p, (int, float)) and n_p > 0:
        cell_spec.setdefault("anode", {})["N_P_ratio"] = float(n_p)
        cell_spec["provenance"]["anode.N_P_ratio"] = src("anode", "n_p_ratio")

    # 电压窗口 → 测试条件 (runner 的截止电压优先读 condition.voltage_min)
    cond = geometry.get("condition") if isinstance(geometry.get("condition"), dict) else {}
    if isinstance(cond.get("v_min"), (int, float)) and cond["v_min"] > 0:
        cell_spec.setdefault("condition", {})["voltage_min"] = float(cond["v_min"])
        cell_spec["provenance"]["condition.voltage_min"] = src("condition", "v_min")
    if isinstance(cond.get("v_max"), (int, float)) and cond["v_max"] > 0:
        cell_spec.setdefault("condition", {})["voltage_max"] = float(cond["v_max"])
        cell_spec["provenance"]["condition.voltage_max"] = src("condition", "v_max")


def build_cell_spec(
    scheme: Dict[str, Any],
    candidates: Optional[Dict[str, Any]],
    c_rate: float = 0.5,
    ambient_temp_k: float = 298.15,
) -> Dict[str, Any]:
    """Stage 4 方案 + candidates.json → cell_spec dict（无缺省表回填）。

    Args:
        scheme: design_scheme.json 的 scheme 块（cathode/anode/electrolyte/
            additives/target_energy_wh_kg/loading_mg_cm2）。
        candidates: candidates.json 全量内容（可 None）。
        c_rate: 放电倍率（提取清单记录值）。
        ambient_temp_k: 环境温度 [K]（记录为 ℃）。

    Returns:
        与 pinn_input_spec.json 契约同构的 cell_spec dict。
    """
    scheme = scheme or {}
    candidates = candidates or {}

    def _entry(category: str, item_id: str) -> Optional[dict]:
        for entry in candidates.get(category, []) or []:
            if entry.get("id") == item_id:
                return entry
        return None

    cathode_id = str(scheme.get("cathode") or "")
    anode_id = str(scheme.get("anode") or "")
    electrolyte_id = str(scheme.get("electrolyte") or "")

    cathode, provenance = _electrode("cathode", cathode_id, _entry("cathode", cathode_id), scheme)
    anode, prov = _electrode("anode", anode_id, _entry("anode", anode_id), scheme)
    provenance.update(prov)

    elec_entry = _entry("electrolyte", electrolyte_id)
    electrolyte = {
        "name": electrolyte_id, "composition": "",
        "c_e0": None, "D_e": None, "t_plus": None, "kappa": None,
        "oxidation_window": None, "reduction_stability": None, "note": "",
    }
    if elec_entry:
        if elec_entry.get("formula"):
            electrolyte["composition"] = elec_entry["formula"]
        if elec_entry.get("oxidation_window") is not None:
            electrolyte["oxidation_window"] = float(elec_entry["oxidation_window"])
        if elec_entry.get("note"):
            electrolyte["note"] = elec_entry["note"]
        prov = elec_entry.get("provenance") or {}
        provenance["electrolyte"] = {
            "source": prov.get("source") or "candidates",
            "confidence": prov.get("confidence") or "medium",
            "ref": f"candidates.json:{electrolyte_id}",
        }
    else:
        provenance["electrolyte"] = {"source": "scheme", "confidence": "high",
                                     "ref": "scheme.electrolyte"}

    target = scheme.get("target_energy_wh_kg") or scheme.get("target_energy")
    cell_spec = {
        "scheme_id": "",
        "cathode": cathode,
        "anode": anode,
        "electrolyte": electrolyte,
        "separator": {"name": "", "L": None, "epsilon": None},
        "design": {
            "cell_format": "", "cell_dimensions": "", "area": None,
            "EC_ratio": None,
            "current_collector_pos": "Al", "current_collector_neg": "Cu",
            "target_energy_density": float(target) if isinstance(target, (int, float)) else None,
        },
        "condition": {
            "scenario": "", "temperature_C": float(ambient_temp_k) - 273.15,
            "c_rate": float(c_rate), "current_density": None,
            "voltage_min": None, "voltage_max": None,
            "cycle_number": None, "electrolyte_desc": "", "separator": "",
            "counter_electrode": "",
        },
        "anchors": [],
        "provenance": provenance,
    }
    # Stage 4 配方几何 → 显式输入（有则提取、无则跳过, PINN 侧逐字段回退训练参数）
    geometry = scheme.get("geometry") if isinstance(scheme, dict) else None
    if isinstance(geometry, dict) and geometry:
        _apply_recipe_geometry(cell_spec, geometry)
    return cell_spec


def summarize_param_extraction(spec_obj: Any, fields: Tuple[str, ...]) -> Tuple[List[str], List[str]]:
    """按字段清单统计已提取 / 未提取 (None 或空串) 的物理量。"""
    filled: List[str] = []
    missing: List[str] = []
    for name in fields:
        value = spec_obj.get(name) if isinstance(spec_obj, dict) else getattr(spec_obj, name, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
        else:
            filled.append(name)
    return filled, missing
