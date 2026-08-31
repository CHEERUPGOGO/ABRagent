# -*- coding: utf-8 -*-
"""cell_spec_schema.py — 电芯方案参数字典（Cell Spec Dict，阶段 A）

三方对齐契约：
  1. P2D / PyBaMM 输入参数（物理层）
  2. miner / agent 提取的 JSON 字段（文献层）
  3. 数据库字段（BetterBat / Materials Project / PyBaMM 参数集）

设计原则
--------
- 内部统一 SI 单位（m、mol/m³、m²/s、S/m、A/m²、kg/m²、V、K），
  外部单位在转换函数里换算，绝不把"带单位的裸字符串"往物理层传。
- 所有数值字段可选（None = 文献/数据库未报告），缺省由 ``fill_missing``
  从缺省参数表补全，并打上 provenance（来源 + 置信度）。
- 每个关键字段都有 provenance，杜绝"不知道哪来的数"。
- 纯标准库实现（dataclasses / typing / math / json），无 pydantic / pybamm
  依赖——阶段 B 才引入 PyBaMM，本模块只定义契约。

点 vs 曲线的桥梁
----------------
miner 挖到的是"标量点"（0.1C 下 200 mAh/g 等），P2D/PINN 输出的是"曲线"
（放电 V(Q)）。两者通过积分守恒连接：

    PINN 曲线  --积分-->  放电比容量 / 平均电压 / 能量密度（标量点）
                              ↑
                        与 miner JSON 的点对齐比较（PerformanceAnchor）

本模块只负责"标量点"这一侧的字段定义与转换；曲线的积分后处理在阶段 B 完成。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any

# ══════════════════════════════════════════════════════════════
# 1. Provenance —— 每个字段的来源追踪
# ══════════════════════════════════════════════════════════════

@dataclass
class Provenance:
    """字段来源与置信度。source 取值：
    miner / agent / pybamm_default / betterbat / mp /
    seed_manual / engineering / literature / unknown
    """
    source: str = "unknown"
    confidence: str = "unknown"   # high / medium / low / unknown
    ref: str = ""                 # 文献 DOI / 文件名 / 规则 id


def prov(source: str, confidence: str = "medium", ref: str = "") -> Provenance:
    """便捷构造 Provenance。"""
    return Provenance(source=source, confidence=confidence, ref=ref)


# ══════════════════════════════════════════════════════════════
# 2. 规范 dataclass（Cell Spec Dict 的骨架）
# ══════════════════════════════════════════════════════════════

@dataclass
class MaterialSpec:
    """活性物质参数（正极或负极）。内部统一 SI 单位。"""
    name: str = ""                          # 规范化名（对齐 candidates.json 的 id）
    formula: str = ""
    component: str = ""                     # cathode / anode
    # 锂金属负极在 P2D 里通常作为 Li 源边界（对电极），不按多孔电极处理
    model: str = "porous_electrode"         # porous_electrode / li_metal_boundary

    # ── 热力学 / 容量 ──
    c_max: Optional[float] = None           # mol/m³ 最大锂浓度
    theoretical_capacity: Optional[float] = None  # Ah/kg（1 mAh/g == 1 Ah/kg）
    stoich_min: Optional[float] = None      # 嵌锂下限（化学计量比，如 0.4）
    stoich_max: Optional[float] = None      # 嵌锂上限（如 0.98）

    # ── 动力学 ──
    D_s: Optional[float] = None             # m²/s 固相扩散系数（25°C 参考）
    k_ref: Optional[float] = None           # m/s 反应速率常数（25°C 参考）
    Ea_Ds: Optional[float] = None           # J/mol 扩散活化能
    Ea_k: Optional[float] = None            # J/mol 反应活化能

    # ── 结构 ──
    R_p: Optional[float] = None             # m 颗粒半径
    sigma: Optional[float] = None           # S/m 电子电导率

    # ── 电压 ──
    avg_voltage: Optional[float] = None     # V 平均放电电压（0 阶估算用）
    voltage_limit: Optional[float] = None   # V 充电截止电压
    U_ocp: Optional[str] = None             # 开路电压曲线引用（PyBaMM 参数名 / 插值表路径）

    weakness: str = ""


@dataclass
class ElectrodeSpec:
    """电极级参数（活性物质 + 电极几何）。"""
    component: str = ""                     # cathode / anode
    material: MaterialSpec = field(default_factory=MaterialSpec)

    L: Optional[float] = None               # m 电极厚度
    epsilon: Optional[float] = None         # 孔隙率（电解液相体积分数）
    epsilon_s: Optional[float] = None       # 活性物质体积分数
    epsilon_f: Optional[float] = None       # 导电剂体积分数
    epsilon_b: Optional[float] = None       # 粘结剂体积分数
    mass_loading: Optional[float] = None    # kg/m² 活性物质面载量
    area: Optional[float] = None            # m² 电极面积
    N_P_ratio: Optional[float] = None       # N/P 比（负极容量 / 正极容量）


@dataclass
class ElectrolyteSpec:
    """电解液参数。"""
    name: str = ""                          # 规范化名（对齐 candidates.json 的 id）
    composition: str = ""                   # 配方描述（如 "1M LiPF6 in EC/DMC"）

    c_e0: Optional[float] = None            # mol/m³ 初始锂盐浓度（1M = 1000）
    D_e: Optional[float] = None             # m²/s 锂盐扩散系数
    t_plus: Optional[float] = None          # 阳离子迁移数
    kappa: Optional[float] = None           # S/m 离子电导率
    oxidation_window: Optional[float] = None    # V 氧化窗口（对 Li/Li+）
    reduction_stability: Optional[float] = None  # V 还原稳定性（对 Li/Li+）

    note: str = ""


@dataclass
class SeparatorSpec:
    """隔膜参数。"""
    name: str = ""
    L: Optional[float] = None               # m 厚度
    epsilon: Optional[float] = None         # 孔隙率


@dataclass
class CellDesignSpec:
    """电芯级设计参数（长宽高 / N-P / 电解液量 / 集流体）。"""
    cell_format: str = ""                   # pouch / coin / cylindrical / prismatic
    cell_dimensions: str = ""               # 长宽高描述（如 "60mm × 40mm × 5mm"）
    area: Optional[float] = None            # m² 有效电极面积
    EC_ratio: Optional[float] = None        # g/Ah 电解液 / 容量比
    current_collector_pos: str = "Al"       # 正极集流体
    current_collector_neg: str = "Cu"       # 负极集流体
    target_energy_density: Optional[float] = None  # Wh/kg 设计目标


@dataclass
class TestCondition:
    """测试 / 运行条件（来自 miner phase1 的条件提取）。"""
    scenario: str = ""                      # half_cell_test / full_cell_test / synthesis / ...
    temperature_C: Optional[float] = None   # °C
    c_rate: Optional[float] = None          # 1/h（0.1C → 0.1）
    current_density: Optional[float] = None # A/m²
    voltage_min: Optional[float] = None     # V
    voltage_max: Optional[float] = None     # V
    cycle_number: Optional[int] = None
    electrolyte_desc: str = ""
    separator: str = ""
    counter_electrode: str = ""


@dataclass
class PerformanceAnchor:
    """文献挖到的标量锚点（PINN 积分后对比用）。"""
    property_name: str = ""                 # 归一化后的 canonical 名（见 PROPERTY_ALIASES）
    raw_name: str = ""                      # miner 原始 property_name
    value: Optional[float] = None
    unit: str = ""
    component: str = ""                     # cathode / anode / electrolyte / full_cell
    material_id: str = ""
    condition: TestCondition = field(default_factory=TestCondition)
    source_text: str = ""
    provenance: Provenance = field(default_factory=Provenance)


@dataclass
class CellSpec:
    """顶层电芯方案（契约的根对象）。"""
    scheme_id: str = ""
    cathode: ElectrodeSpec = field(default_factory=ElectrodeSpec)
    anode: ElectrodeSpec = field(default_factory=ElectrodeSpec)
    electrolyte: ElectrolyteSpec = field(default_factory=ElectrolyteSpec)
    separator: SeparatorSpec = field(default_factory=SeparatorSpec)
    design: CellDesignSpec = field(default_factory=CellDesignSpec)
    condition: TestCondition = field(default_factory=TestCondition)
    anchors: List[PerformanceAnchor] = field(default_factory=list)
    # 字段路径 → 来源（如 "cathode.material.D_s" → Provenance）
    provenance: Dict[str, Provenance] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def set_prov(self, path: str, p: Provenance) -> None:
        self.provenance[path] = p


# ══════════════════════════════════════════════════════════════
# 3. 单位换算（外部单位 → 内部 SI）
# ══════════════════════════════════════════════════════════════

def mah_g_to_ah_kg(v_mah_g: float) -> float:
    """mAh/g → Ah/kg。恒等（1 mAh/g = 1 Ah/kg）。"""
    return v_mah_g


def ah_kg_to_mah_g(v_ah_kg: float) -> float:
    """Ah/kg → mAh/g。恒等。"""
    return v_ah_kg


def mg_cm2_to_kg_m2(v_mg_cm2: float) -> float:
    """mg/cm² → kg/m²。1 mg/cm² = 0.01 kg/m²。"""
    return v_mg_cm2 * 0.01


def kg_m2_to_mg_cm2(v_kg_m2: float) -> float:
    """kg/m² → mg/cm²。1 kg/m² = 100 mg/cm²。"""
    return v_kg_m2 * 100.0


def c_rate_to_current_density(
    c_rate: float,
    specific_capacity_ah_kg: float,
    mass_loading_kg_m2: float,
) -> float:
    """C-rate + 比容量 + 面载量 → 电流密度 (A/m²)。

    I (A/m²) = C_rate (1/h) × Q_s (Ah/kg) × L (kg/m²)
    """
    return c_rate * specific_capacity_ah_kg * mass_loading_kg_m2


def current_density_to_c_rate(
    current_density_a_m2: float,
    specific_capacity_ah_kg: float,
    mass_loading_kg_m2: float,
) -> float:
    """电流密度 → C-rate。C = I / (Q_s × L)。"""
    if not specific_capacity_ah_kg or not mass_loading_kg_m2:
        return 0.0
    return current_density_a_m2 / (specific_capacity_ah_kg * mass_loading_kg_m2)


def celsius_to_kelvin(t_c: float) -> float:
    return t_c + 273.15


def scm_to_sm(v_scm: float) -> float:
    """S/cm → S/m。"""
    return v_scm * 100.0


def cm2s_to_m2s(v_cm2s: float) -> float:
    """cm²/s → m²/s。"""
    return v_cm2s * 1e-4


# ══════════════════════════════════════════════════════════════
# 4. 缺省参数表（对齐 candidates.json 材料体系）
#
# 来源标注：这些是「工程经验 + 教科书 + 常见文献」的初值，置信度 low~medium。
# 阶段 B 用 PyBaMM 参数集（Chen2020 / Marquis2019 等）替换/校准，
# 阶段 C 用 miner 挖到的点反推标定 D_s / k。
# ══════════════════════════════════════════════════════════════

DEFAULT_MATERIALS: Dict[str, Dict[str, Any]] = {
    # ── 正极 ──
    "NCM811": {
        "component": "cathode",
        "formula": "LiNi0.8Co0.1Mn0.1O2",
        "theoretical_capacity": 275.0,      # Ah/kg（实际 ~200）
        "c_max": 49000.0,                    # mol/m³
        "D_s": 1e-14,                        # m²/s
        "k_ref": 1e-11,                      # m/s
        "R_p": 5e-6,                         # 5 μm
        "sigma": 0.5,                        # S/m
        "epsilon_s": 0.58,
        "epsilon": 0.30,
        "L": 70e-6,                          # 70 μm
        "mass_loading": 0.02,                # kg/m²（2 mg/cm² ≈ 0.02）
        "avg_voltage": 3.8,
        "voltage_limit": 4.3,
        "stoich_min": 0.4,
        "stoich_max": 0.98,
        "weakness": "高电压下表面副反应加剧，热稳定性差",
    },
    "LRMO": {
        "component": "cathode",
        "formula": "Li1.2Ni0.2Mn0.6O2",
        "theoretical_capacity": 380.0,      # Ah/kg（实际 ~260）
        "c_max": 43000.0,
        "D_s": 5e-15,
        "k_ref": 5e-12,
        "R_p": 5e-6,
        "sigma": 0.1,
        "epsilon_s": 0.55,
        "epsilon": 0.32,
        "L": 75e-6,
        "mass_loading": 0.02,
        "avg_voltage": 3.6,
        "voltage_limit": 4.6,
        "stoich_min": 0.1,
        "stoich_max": 1.0,
        "weakness": "电压衰减、首圈不可逆容量大、倍率性能差",
    },
    "LNMO": {
        "component": "cathode",
        "formula": "LiNi0.5Mn1.5O4",
        "theoretical_capacity": 147.0,      # Ah/kg（实际 ~130）
        "c_max": 24000.0,
        "D_s": 1e-14,
        "k_ref": 1e-11,
        "R_p": 5e-6,
        "sigma": 1e-4,                       # 尖晶石电导低
        "epsilon_s": 0.55,
        "epsilon": 0.32,
        "L": 70e-6,
        "mass_loading": 0.02,
        "avg_voltage": 4.7,
        "voltage_limit": 4.9,
        "stoich_min": 0.0,
        "stoich_max": 1.0,
        "weakness": "Mn 溶解、常规电解液氧化分解",
    },
    "Ni96": {
        "component": "cathode",
        "formula": "LiNi0.96Co0.02Mn0.02O2",   # 超高镍（近似）
        "theoretical_capacity": 240.0,        # Ah/kg（实际 ~240 mAh/g）
        "c_max": 70000.0,
        "D_s": 1e-14,
        "k_ref": 1e-11,
        "R_p": 5e-6,
        "sigma": 0.5,
        "epsilon_s": 0.58,
        "epsilon": 0.30,
        "L": 70e-6,
        "mass_loading": 0.02,
        "avg_voltage": 3.9,
        "voltage_limit": 4.4,
        "stoich_min": 0.2,
        "stoich_max": 0.98,
        "weakness": "超高镍：容量高但热稳定性差、表面副反应敏感",
    },
    # ── 负极 ──
    "graphite": {
        "component": "anode",
        "formula": "C",
        "theoretical_capacity": 372.0,
        "c_max": 31368.0,
        "D_s": 3.9e-14,
        "k_ref": 2e-11,
        "R_p": 10e-6,
        "sigma": 100.0,
        "epsilon_s": 0.58,
        "epsilon": 0.35,
        "L": 80e-6,
        "mass_loading": 0.012,               # 按 N/P≈1.1 配比
        "avg_voltage": 0.1,
        "voltage_limit": None,
        "stoich_min": 0.0,
        "stoich_max": 1.0,
        "weakness": "容量上限 ~372 Ah/kg，倍率受限",
    },
    "si_base": {
        "component": "anode",
        "formula": "Si/SiOx",
        "theoretical_capacity": 1500.0,     # 工程实际值（理论 3579）
        "c_max": 50000.0,
        "D_s": 1e-16,
        "k_ref": 1e-11,
        "R_p": 5e-6,
        "sigma": 10.0,
        "epsilon_s": 0.45,
        "epsilon": 0.40,                     # 预留膨胀空间
        "L": 60e-6,
        "mass_loading": 0.003,               # 高容量 → 低载量
        "avg_voltage": 0.4,
        "voltage_limit": None,
        "stoich_min": 0.0,
        "stoich_max": 1.0,
        "weakness": "体积膨胀 ~300%，需 FEC 类添加剂稳定 SEI",
    },
    "li_metal": {
        "component": "anode",
        "formula": "Li",
        "model": "li_metal_boundary",        # P2D 里作为 Li 源边界，不按多孔电极
        "theoretical_capacity": 3860.0,
        "c_max": None,
        "D_s": None,
        "k_ref": None,
        "R_p": None,
        "sigma": None,
        "epsilon_s": None,
        "epsilon": None,
        "L": None,                           # 锂箔厚度（如 20 μm）需单独给定
        "mass_loading": None,
        "avg_voltage": 0.0,
        "voltage_limit": None,
        "stoich_min": None,
        "stoich_max": None,
        "weakness": "枝晶、死锂、库仑效率低；液态下需高浓/LHCE 电解液",
    },
}

DEFAULT_ELECTROLYTES: Dict[str, Dict[str, Any]] = {
    "carbonate_ec": {
        "composition": "1M LiPF6 in EC/DMC/EMC",
        "c_e0": 1000.0,
        "D_e": 3e-10,
        "t_plus": 0.4,
        "kappa": 1.1,
        "oxidation_window": 4.3,
        "reduction_stability": 0.6,
        "note": "常规碳酸酯",
    },
    "fluorinated": {
        "composition": "FEC/FEMC 体系",
        "c_e0": 1000.0,
        "D_e": 2e-10,
        "t_plus": 0.4,
        "kappa": 0.8,
        "oxidation_window": 4.8,
        "reduction_stability": 0.5,
        "note": "含氟溶剂",
    },
    "high_concentration": {
        "composition": ">3M LiFSI 体系",
        "c_e0": 3000.0,
        "D_e": 1e-10,
        "t_plus": 0.7,
        "kappa": 0.6,
        "oxidation_window": 5.0,
        "reduction_stability": 0.1,
        "note": "高浓电解液，适合锂金属负极与高压正极",
    },
    "lhce": {
        "composition": "高浓 + 稀释剂（局部高浓）",
        "c_e0": 2500.0,
        "D_e": 1.5e-10,
        "t_plus": 0.65,
        "kappa": 0.5,
        "oxidation_window": 5.0,
        "reduction_stability": 0.1,
        "note": "局部高浓，兼顾粘度与稳定性",
    },
    "dilute_aqueous": {
        "composition": "1M Li2SO4 / LiTFSI 水溶液",
        "c_e0": 1000.0,
        "D_e": 1e-9,
        "t_plus": 0.4,
        "kappa": 5.0,
        "oxidation_window": 1.23,
        "reduction_stability": -0.0,
        "note": "水系，热力学窗口约 1.23V",
    },
    "water_in_salt": {
        "composition": ">20m LiTFSI 水溶液",
        "c_e0": 20000.0,
        "D_e": 3e-10,
        "t_plus": 0.5,
        "kappa": 1.0,
        "oxidation_window": 3.0,
        "reduction_stability": -0.3,
        "note": "water-in-salt 高浓水系",
    },
}

# 负极体系 → 电芯级折算系数（工程经验初值，与 RAG energy_model 一致）
DEFAULT_ACTIVE_RATIOS: Dict[str, float] = {
    "graphite": 0.50,
    "si_base": 0.42,
    "li_metal": 0.35,
    "default": 0.45,
}


# ══════════════════════════════════════════════════════════════
# 5. property 别名归一化（miner format1 的 PascalCase ↔ RAG 的 snake_case）
# ══════════════════════════════════════════════════════════════

# 归一化到 canonical key（P2D 可验证的标量）
PROPERTY_ALIASES: Dict[str, str] = {
    # 比容量
    "Discharge_Specific_Capacity": "specific_capacity",
    "discharge_capacity": "specific_capacity",
    "Specific_Capacity": "specific_capacity",
    "specific_capacity": "specific_capacity",
    "Charge_Specific_Capacity": "specific_capacity",
    # 能量密度
    "energy_density": "energy_density",
    "Energy_Density": "energy_density",
    "gravimetric_energy_density": "energy_density",
    "Gravimetric_Energy_Density": "energy_density",
    # 保持率 / 效率
    "capacity_retention": "capacity_retention",
    "Capacity_Retention": "capacity_retention",
    "capacity_retention_rate": "capacity_retention",
    "coulombic_efficiency": "coulombic_efficiency",
    "Coulombic_Efficiency": "coulombic_efficiency",
    "ice": "initial_coulombic_efficiency",
    "Initial_Coulombic_Efficiency": "initial_coulombic_efficiency",
    "initial_coulombic_efficiency": "initial_coulombic_efficiency",
    # 电压
    "avg_voltage": "avg_voltage",
    "Average_Voltage": "avg_voltage",
    "voltage": "avg_voltage",
    "working_voltage": "avg_voltage",
    # 电导率 / 扩散
    "ionic_conductivity": "ionic_conductivity",
    "Ionic_Conductivity": "ionic_conductivity",
    "diffusion_coefficient": "diffusion_coefficient",
    "Diffusion_Coefficient": "diffusion_coefficient",
    "Lithium_Ion_Diffusion_Coefficient": "diffusion_coefficient",
    # 规格参数
    "mass_loading": "mass_loading",
    "Mass_Loading": "mass_loading",
    "electrode_thickness": "electrode_thickness",
    "Electrode_Thickness": "electrode_thickness",
    "porosity": "porosity",
    "Porosity": "porosity",
    "N_P_ratio": "N_P_ratio",
    "NP_ratio": "N_P_ratio",
    # 面容量
    "areal_capacity": "areal_capacity",
    "Areal_Capacity": "areal_capacity",
    # ── agent/miner 实际输出命名（_all_conditioned_data.csv 盘点）──
    "Discharge_Specific_Capacity_Initial": "specific_capacity",
    "Capacity_Retention_Ratio": "capacity_retention",
    "Capacity_Retention_at_Nth_Cycle": "capacity_retention",
    "Average_Coulombic_Efficiency_Stable_Cycling": "coulombic_efficiency",
    "Gravimetric_Energy_Density": "energy_density",
    "Energy_Density_gravimetric": "energy_density",
    "Energy_Density_volumetric": "volumetric_energy_density",
    "Mass_Loading_min": "mass_loading",
    "Mass_Loading_max": "mass_loading",
    "Charge_Transfer_Resistance": "charge_transfer_resistance",
    "Rate_Performance": "rate_performance",
    "Rate_Capability": "rate_performance",
    "Chemical_Diffusion_Coefficient_of_Li_GITT": "diffusion_coefficient",
    "Cycle_Life_80_Percent_Capacity_Retention": "cycle_life",
    "Plateau_Overpotential": "overpotential",
    "Li_Nucleation_Overpotential": "overpotential",
}


def normalize_property_name(raw: str) -> str:
    """原始 property_name → canonical key（未知时原样返回）。"""
    return PROPERTY_ALIASES.get(raw.strip(), raw.strip())


# ══════════════════════════════════════════════════════════════
# 6. 转换函数
# ══════════════════════════════════════════════════════════════

def _build_material(name: str, component: str) -> MaterialSpec:
    """按材料 id + 组件从缺省表构造 MaterialSpec。"""
    params = DEFAULT_MATERIALS.get(name, {})
    m = MaterialSpec(
        name=name,
        component=component or params.get("component", component),
        formula=params.get("formula", ""),
        model=params.get("model", "porous_electrode"),
        c_max=params.get("c_max"),
        theoretical_capacity=params.get("theoretical_capacity"),
        stoich_min=params.get("stoich_min"),
        stoich_max=params.get("stoich_max"),
        D_s=params.get("D_s"),
        k_ref=params.get("k_ref"),
        Ea_Ds=params.get("Ea_Ds"),
        Ea_k=params.get("Ea_k"),
        R_p=params.get("R_p"),
        sigma=params.get("sigma"),
        avg_voltage=params.get("avg_voltage"),
        voltage_limit=params.get("voltage_limit"),
        U_ocp=params.get("U_ocp"),
        weakness=params.get("weakness", ""),
    )
    return m


def _build_electrode(name: str, component: str) -> ElectrodeSpec:
    """按材料 id 构造 ElectrodeSpec（含缺省电极几何）。"""
    params = DEFAULT_MATERIALS.get(name, {})
    mat = _build_material(name, component)
    return ElectrodeSpec(
        component=component or params.get("component", ""),
        material=mat,
        L=params.get("L"),
        epsilon=params.get("epsilon"),
        epsilon_s=params.get("epsilon_s"),
        epsilon_f=None,
        epsilon_b=None,
        mass_loading=params.get("mass_loading"),
        area=None,
        N_P_ratio=None,
    )


def _build_electrolyte(name: str) -> ElectrolyteSpec:
    params = DEFAULT_ELECTROLYTES.get(name, {})
    return ElectrolyteSpec(
        name=name,
        composition=params.get("composition", ""),
        c_e0=params.get("c_e0"),
        D_e=params.get("D_e"),
        t_plus=params.get("t_plus"),
        kappa=params.get("kappa"),
        oxidation_window=params.get("oxidation_window"),
        reduction_stability=params.get("reduction_stability"),
        note=params.get("note", ""),
    )


def candidates_scheme_to_cell_spec(
    scheme: Dict[str, Any],
    candidates: Optional[Dict[str, Any]] = None,
    scheme_id: str = "",
) -> CellSpec:
    """RAG 方案 dict + candidates.json → CellSpec。

    Args:
        scheme: {"cathode": "NCM811", "anode": "li_metal",
                 "electrolyte": "lhce", "target_energy": 400, ...}
        candidates: candidates.json 内容（可选，未给时用缺省表）。
        scheme_id: 方案标识。

    说明：
        candidates.json 提供 id / formula / capacity / avg_voltage / voltage_limit，
        这些是"材料级"信息；本函数补上缺省表的物理参数（D_s/k/孔隙率等）。
        若 candidates 提供的值与缺省表冲突，以 candidates 为准（它更接近用户方案）。
    """
    spec = CellSpec(scheme_id=scheme_id)

    cathode_id = scheme.get("cathode") or ""
    anode_id = scheme.get("anode") or ""
    elec_id = scheme.get("electrolyte") or ""

    spec.cathode = _build_electrode(cathode_id, "cathode")
    spec.anode = _build_electrode(anode_id, "anode")
    spec.electrolyte = _build_electrolyte(elec_id)

    # candidates.json 覆盖（材料级电压/容量更权威）
    if candidates:
        for cat, field_name in (("cathode", "cathode"), ("anode", "anode")):
            for m in candidates.get(cat, []):
                if m.get("id") != scheme.get(field_name):
                    continue
                electrode = getattr(spec, field_name)
                if m.get("capacity", {}).get("value") is not None:
                    electrode.material.theoretical_capacity = float(
                        m["capacity"]["value"])
                if m.get("avg_voltage") is not None:
                    electrode.material.avg_voltage = float(m["avg_voltage"])
                if m.get("voltage_limit") is not None:
                    electrode.material.voltage_limit = float(m["voltage_limit"])
                if m.get("formula"):
                    electrode.material.formula = m["formula"]
                if m.get("weakness"):
                    electrode.material.weakness = m["weakness"]
                setattr(spec, field_name, electrode)

        for e in candidates.get("electrolyte", []):
            if e.get("id") != elec_id:
                continue
            if e.get("oxidation_window") is not None:
                spec.electrolyte.oxidation_window = float(e["oxidation_window"])
            if e.get("note"):
                spec.electrolyte.note = e["note"]
            if e.get("formula"):
                spec.electrolyte.composition = e["formula"]

    # 设计目标
    if scheme.get("target_energy") is not None:
        spec.design.target_energy_density = float(scheme["target_energy"])

    # provenance
    spec.set_prov("scheme", prov("seed_manual", "high", "scheme dict"))
    for path, src in (
        ("cathode.material", "seed_manual+mp"),
        ("anode.material", "seed_manual+mp"),
        ("electrolyte", "seed_manual"),
    ):
        spec.set_prov(path, prov(src, "medium"))
    spec.set_prov("cathode.material.D_s", prov("engineering", "low", "缺省初值"))
    spec.set_prov("anode.material.D_s", prov("engineering", "low", "缺省初值"))
    spec.set_prov("cathode.epsilon", prov("engineering", "low", "缺省初值"))

    return spec


def miner_records_to_cell_spec(
    miner_json: Dict[str, Any],
    scheme: Optional[Dict[str, Any]] = None,
    scheme_id: str = "",
) -> CellSpec:
    """miner/agent 提取的 JSON → CellSpec。

    Args:
        miner_json: phase0/phase12 输出的 JSON，含
            materials / conditions / conditioned_properties / intrinsic_properties。
        scheme: 可选，RAG 方案 dict（提供正极/负极/电解液 id），
            若未给则从 miner 的 materials 里推断。
        scheme_id: 方案标识。

    挖到的性能点进入 ``anchors``（验证锚点），材料/条件进入对应字段。
    """
    spec = candidates_scheme_to_cell_spec(scheme or {}, None, scheme_id) \
        if scheme else CellSpec(scheme_id=scheme_id)

    # 材料
    for m in miner_json.get("materials", []):
        name = m.get("name", "") or m.get("short_name", "")
        # 只认缺省表里有的材料，避免把电解液配方误判为正极
        if name in DEFAULT_MATERIALS:
            comp = DEFAULT_MATERIALS[name]["component"]
            electrode = _build_electrode(name, comp)
            if comp == "cathode":
                spec.cathode = electrode
            elif comp == "anode":
                spec.anode = electrode
            spec.set_prov(f"{comp}.material", prov("miner", "medium", name))

    # 条件
    conds = miner_json.get("conditions", []) or miner_json.get("new_conditions", [])
    if conds:
        c0 = conds[0].get("condition", {})
        sc = conds[0].get("scenario", "")
        spec.condition = TestCondition(
            scenario=sc,
            temperature_C=_num(c0, "temperature"),
            c_rate=_num(c0, "c_rate"),
            current_density=_num(c0, "current_density"),
            voltage_min=_min(c0, "voltage_range"),
            voltage_max=_max(c0, "voltage_range"),
            electrolyte_desc=_str(c0, "electrolyte"),
            separator=_str(c0, "separator"),
            counter_electrode=_str(c0, "counter_electrode"),
        )

    # 性能锚点
    anchors: List[PerformanceAnchor] = []
    props = list(miner_json.get("conditioned_properties", []))
    props += list(miner_json.get("intrinsic_properties", []))
    for p in props:
        raw_name = p.get("property_name", "")
        canon = normalize_property_name(raw_name)
        val = p.get("value", {})
        num = val.get("value") if isinstance(val, dict) else val
        unit = val.get("unit", "") if isinstance(val, dict) else ""
        if num is None:
            continue
        try:
            num = float(num)
        except (TypeError, ValueError):
            continue
        anchors.append(PerformanceAnchor(
            property_name=canon,
            raw_name=raw_name,
            value=num,
            unit=unit,
            component=p.get("component", ""),
            material_id=p.get("material_id", ""),
            condition=spec.condition,
            source_text=p.get("source_text", ""),
            provenance=prov("miner", "medium", p.get("source_text", "")[:80]),
        ))
    spec.anchors = anchors

    return spec


def fill_missing(spec: CellSpec) -> CellSpec:
    """补全缺省字段（文献/数据库没报告的，从缺省表 + 工程经验补）。

    已存在的字段不动；只补 None。补全后打 provenance。
    """
    for comp, electrode in (("cathode", spec.cathode), ("anode", spec.anode)):
        params = DEFAULT_MATERIALS.get(electrode.material.name, {})
        if not params:
            continue
        m = electrode.material
        for f in ("theoretical_capacity", "c_max", "D_s", "k_ref", "R_p",
                  "sigma", "avg_voltage", "voltage_limit", "stoich_min", "stoich_max"):
            if getattr(m, f) is None and params.get(f) is not None:
                setattr(m, f, params[f])
                spec.set_prov(f"{comp}.material.{f}",
                              prov("engineering", "low", "fill_missing 缺省"))
        for f in ("L", "epsilon", "epsilon_s", "mass_loading"):
            if getattr(electrode, f) is None and params.get(f) is not None:
                setattr(electrode, f, params[f])
                spec.set_prov(f"{comp}.{f}",
                              prov("engineering", "low", "fill_missing 缺省"))

    ep = spec.electrolyte
    if ep.name and ep.name in DEFAULT_ELECTROLYTES:
        params = DEFAULT_ELECTROLYTES[ep.name]
        for f in ("c_e0", "D_e", "t_plus", "kappa", "oxidation_window",
                  "reduction_stability"):
            if getattr(ep, f) is None and params.get(f) is not None:
                setattr(ep, f, params[f])
                spec.set_prov(f"electrolyte.{f}",
                              prov("engineering", "low", "fill_missing 缺省"))

    # 隔膜缺省（工程常见值：Celgard 2325，25 μm，孔隙率 0.4）
    if spec.separator.L is None:
        spec.separator.L = 25e-6
        spec.separator.epsilon = 0.4
        spec.separator.name = "Celgard 2325 (default)"
        spec.set_prov("separator", prov("engineering", "low", "fill_missing 缺省"))

    return spec


def to_pybamm_dict(spec: CellSpec) -> Dict[str, float]:
    """CellSpec → PyBaMM 参数字典（阶段 B 直接喂给 pybamm.ParameterValues）。

    key 命名对齐 PyBaMM 标准 parameter set（Chen2020 等）。
    锂金属负极（li_metal_boundary）跳过负极多孔电极参数。
    """
    out: Dict[str, float] = {}
    c, a = spec.cathode, spec.anode
    e = spec.electrolyte
    sep = spec.separator

    if c.material.theoretical_capacity is not None:
        out["Positive electrode capacity [A.h.kg-1]"] = c.material.theoretical_capacity
    if c.material.c_max is not None:
        out["Maximum concentration in positive electrode [mol.m-3]"] = c.material.c_max
    if c.material.D_s is not None:
        out["Positive particle diffusivity [m2.s-1]"] = c.material.D_s
    if c.material.k_ref is not None:
        out["Positive electrode reaction rate [m.s-1]"] = c.material.k_ref
    if c.material.R_p is not None:
        out["Positive particle radius [m]"] = c.material.R_p
    if c.material.sigma is not None:
        out["Positive electrode conductivity [S.m-1]"] = c.material.sigma
    if c.L is not None:
        out["Positive electrode thickness [m]"] = c.L
    if c.epsilon is not None:
        out["Positive electrode porosity"] = c.epsilon
    if c.epsilon_s is not None:
        out["Positive electrode active material volume fraction"] = c.epsilon_s

    # 负极：多孔电极才给这些参数
    if a.material.model != "li_metal_boundary":
        if a.material.theoretical_capacity is not None:
            out["Negative electrode capacity [A.h.kg-1]"] = a.material.theoretical_capacity
        if a.material.c_max is not None:
            out["Maximum concentration in negative electrode [mol.m-3]"] = a.material.c_max
        if a.material.D_s is not None:
            out["Negative particle diffusivity [m2.s-1]"] = a.material.D_s
        if a.material.k_ref is not None:
            out["Negative electrode reaction rate [m.s-1]"] = a.material.k_ref
        if a.material.R_p is not None:
            out["Negative particle radius [m]"] = a.material.R_p
        if a.material.sigma is not None:
            out["Negative electrode conductivity [S.m-1]"] = a.material.sigma
        if a.L is not None:
            out["Negative electrode thickness [m]"] = a.L
        if a.epsilon is not None:
            out["Negative electrode porosity"] = a.epsilon
        if a.epsilon_s is not None:
            out["Negative electrode active material volume fraction"] = a.epsilon_s

    if e.c_e0 is not None:
        out["Initial concentration in electrolyte [mol.m-3]"] = e.c_e0
        out["Typical electrolyte concentration [mol.m-3]"] = e.c_e0
    if e.D_e is not None:
        out["Electrolyte diffusivity [m2.s-1]"] = e.D_e
    if e.t_plus is not None:
        out["Cation transference number"] = e.t_plus
    if e.kappa is not None:
        out["Electrolyte conductivity [S.m-1]"] = e.kappa
    if sep.L is not None:
        out["Separator thickness [m]"] = sep.L
    if sep.epsilon is not None:
        out["Separator porosity"] = sep.epsilon

    return out


# ══════════════════════════════════════════════════════════════
# 7. 0 阶能量估算（复用 RAG energy_model 的逻辑，作 PINN 校准前初值）
# ══════════════════════════════════════════════════════════════

def estimate_material_energy(avg_voltage: float, capacity_ah_kg: float) -> float:
    """材料级比能量 (Wh/kg 活性物质) = V × Ah/kg。"""
    return avg_voltage * capacity_ah_kg


def estimate_cell_energy(
    avg_voltage: float,
    capacity_ah_kg: float,
    active_ratio: float = 0.45,
) -> float:
    """电芯级比能量一级估算 (Wh/kg)。"""
    return estimate_material_energy(avg_voltage, capacity_ah_kg) * active_ratio


def estimate_scheme_energy(spec: CellSpec) -> Optional[float]:
    """给定 CellSpec 估算电芯级能量密度 (Wh/kg)。

    用正极 avg_voltage × 实际比容量 × 负极体系折算系数。
    若正极缺电压或容量，返回 None。
    """
    cathode = spec.cathode.material
    anode_id = spec.anode.material.name
    if not cathode.avg_voltage or not cathode.theoretical_capacity:
        return None
    ratio = DEFAULT_ACTIVE_RATIOS.get(anode_id, DEFAULT_ACTIVE_RATIOS["default"])
    return estimate_cell_energy(cathode.avg_voltage, cathode.theoretical_capacity, ratio)


# ══════════════════════════════════════════════════════════════
# 8. 校验
# ══════════════════════════════════════════════════════════════

def validate(spec: CellSpec) -> List[str]:
    """校验物理合理性，返回问题列表（空 = 通过）。"""
    issues: List[str] = []

    def chk_range(v, lo, hi, name):
        if v is not None and not (lo <= v <= hi):
            issues.append(f"{name}={v} 超出合理范围 [{lo}, {hi}]")

    for comp, electrode in (("cathode", spec.cathode), ("anode", spec.anode)):
        if electrode.epsilon is not None:
            chk_range(electrode.epsilon, 0.0, 1.0, f"{comp}.epsilon 孔隙率")
        if electrode.epsilon_s is not None:
            chk_range(electrode.epsilon_s, 0.0, 1.0, f"{comp}.epsilon_s 活性体积分数")
        if electrode.material.c_max is not None and electrode.material.c_max <= 0:
            issues.append(f"{comp}.c_max 非正")
        if electrode.L is not None and electrode.L <= 0:
            issues.append(f"{comp}.L 电极厚度非正")
        if electrode.mass_loading is not None and electrode.mass_loading <= 0:
            issues.append(f"{comp}.mass_loading 面载量非正")

    if spec.electrolyte.c_e0 is not None and spec.electrolyte.c_e0 <= 0:
        issues.append("electrolyte.c_e0 非正")
    if spec.electrolyte.oxidation_window is not None:
        if spec.cathode.material.voltage_limit is not None and \
                spec.electrolyte.oxidation_window < spec.cathode.material.voltage_limit:
            issues.append(
                f"电解液氧化窗口 {spec.electrolyte.oxidation_window}V "
                f"低于正极截止电压 {spec.cathode.material.voltage_limit}V → 氧化分解风险")

    if spec.condition.voltage_min is not None and spec.condition.voltage_max is not None:
        if spec.condition.voltage_min >= spec.condition.voltage_max:
            issues.append("电压窗口 min >= max")

    return issues


# ══════════════════════════════════════════════════════════════
# 内部小工具
# ══════════════════════════════════════════════════════════════

def _num(cond: Dict[str, Any], key: str) -> Optional[float]:
    v = cond.get(key)
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _str(cond: Dict[str, Any], key: str) -> str:
    v = cond.get(key)
    return str(v) if v else ""


def _min(cond: Dict[str, Any], key: str) -> Optional[float]:
    v = cond.get(key)
    if isinstance(v, dict):
        return v.get("min")
    return None


def _max(cond: Dict[str, Any], key: str) -> Optional[float]:
    v = cond.get(key)
    if isinstance(v, dict):
        return v.get("max")
    return None


# ══════════════════════════════════════════════════════════════
# 9. 自测
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 示例方案：NCM811 | 锂金属 | LHCE，目标 400 Wh/kg
    scheme = {
        "cathode": "NCM811",
        "anode": "li_metal",
        "electrolyte": "lhce",
        "target_energy": 400,
    }
    spec = candidates_scheme_to_cell_spec(scheme, scheme_id="demo-001")
    fill_missing(spec)

    print("=== CellSpec (demo) ===")
    print(f"正极: {spec.cathode.material.name} "
          f"V_avg={spec.cathode.material.avg_voltage}V "
          f"Q={spec.cathode.material.theoretical_capacity} Ah/kg")
    print(f"负极: {spec.anode.material.name} "
          f"(model={spec.anode.material.model})")
    print(f"电解液: {spec.electrolyte.name} "
          f"窗口={spec.electrolyte.oxidation_window}V")
    print(f"能量估算(0阶): {estimate_scheme_energy(spec):.1f} Wh/kg "
          f"(目标 {spec.design.target_energy_density} Wh/kg)")

    issues = validate(spec)
    print(f"校验: {'通过' if not issues else issues}")

    print("\n=== PyBaMM 参数映射 (前 12 项) ===")
    pb = to_pybamm_dict(spec)
    for i, (k, v) in enumerate(pb.items()):
        if i >= 12:
            break
        print(f"  {k} = {v}")

    print("\n=== miner JSON → anchors 示例 ===")
    miner_demo = {
        "materials": [{"name": "NCM811", "role": "novel", "short_name": "NCM811"}],
        "conditions": [{
            "condition_id": "C001", "scenario": "half_cell_test",
            "condition": {"c_rate": {"value": 0.1, "unit": "C"},
                          "voltage_range": {"min": 2.8, "max": 4.3, "unit": "V"}},
        }],
        "conditioned_properties": [
            {"condition_id": "C001", "component": "cathode", "material_id": "NCM811",
             "property_type": "electrochemical_performance",
             "property_name": "Discharge_Specific_Capacity",
             "value": {"value": 200, "unit": "mAh/g"},
             "source_text": "NCM811 delivers 200 mAh/g at 0.1C"},
        ],
        "intrinsic_properties": [],
    }
    spec2 = miner_records_to_cell_spec(miner_demo, scheme, scheme_id="demo-002")
    print(f"  锚点数: {len(spec2.anchors)}")
    for a in spec2.anchors:
        print(f"    {a.raw_name} → {a.property_name} = {a.value} {a.unit} "
              f"@ {a.condition.c_rate}C")
