# -*- coding: utf-8 -*-
"""规则筛选与 include 兜底 — 决定段落/表格块的处理方式

旧版是每个段落都走 include 再决定是否 extract，LLM 调用次数多。
新版改用规则预判 + include 灰区兜底：

  1. extract: 高置信段落（含明确材料名、性能关键词等）→ 直接走 unified agent
  2. include: 低置信但疑似相关 → 先调 fast LLM 确认
  3. skip: 明显无关 → 直接跳过

rule_screening 核心逻辑：
  - screen_extraction_unit(): 规则筛选主函数，返回 ScreeningDecision
  - llm_include_fallback(): 当规则不确定时，用 fast LLM 兜底判断
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Any

logger = logging.getLogger("RuleScreening")

# ==================== 组件关键词 ====================

_COMPONENT_KEYWORDS = {
    "cathode": [
        "cathode", "正极", "positive electrode", "NCM", "NCA", "LCO", "LFP",
        "LMO", "LNMO", "LiCoO", "LiNi", "LiMn", "LiFePO", "layer.*oxide",
        "spinel", "olivine", "cathode material", "anode-free",
    ],
    "anode": [
        "anode", "负极", "negative electrode", "graphite", "silicon", "Si",
        "lithium metal", "Li metal", "LTO", "Li4Ti5O", "hard carbon",
        "soft carbon", "tin", "Sb", "alloy", "dendrite", "SEI", "anode.*material",
        "plating", "stripping", "Li_", "nucleation",
    ],
    "electrolyte": [
        "electrolyte", "电解液", "solid electrolyte", "liquid electrolyte",
        "polymer electrolyte", "gel electrolyte", "ionic liquid", "salt",
        "solvent", "additive", "conductivity", "transference number",
        "LiPF6", "LiTFSI", "LiFSI", "EC", "DMC", "DEC", "FEC", "VC",
        "ionic conductivity", "electrochemical window", "SEI",
    ],
}

# ==================== 排除关键词 ====================

_SKIP_KEYWORDS = [
    "abstract", "introduction", "background", "overview",
    "references", "bibliography", "acknowledgement",
    "author information", "author contribution", "corresponding author",
    "graphical abstract", "table of contents", "supplementary information",
    "supporting information", "data availability", "code availability",
    "conflict of interest", "ethical statement", "consent",
    "©", "copyright", "license",
]

# ==================== 电化学/材料性能关键词 ====================

_PERFORMANCE_KEYWORDS = [
    "capacity", "mAh g", "C rate", "current density", "voltage",
    "impedance", "EIS", "CV", "cycle", "rate capability",
    "coulombic efficiency", "energy density", "power density",
    "retention", "conductivity", "specific capacity",
    "overpotential", "polarization", "Rct", "Rsei",
    "diffusion coefficient", "activation energy",
    "S/cm", "S cm", "ohm", "mA g", "A g", "mW h",
]

_MATERIAL_KEYWORDS = [
    "XRD", "XPS", "SEM", "TEM", "X-ray", "Raman", "FTIR",
    "NMR", "lattice", "crystal", "band gap", "diffraction",
    "particle size", "crystallite size", "grain size",
    "morphology", "surface area", "precursor", "synthesis",
    "TGA", "DSC", "DTA",
]

_CONDITION_KEYWORDS = [
    "tested at", "measured at", "cycled at", "temperature",
    "°C", "°F", "C", "V vs", "mA cm", "mV s", "A g",
    "half-cell", "full-cell", "coin cell", "pouch cell",
    "2032", "2025", "Swagelok",
    "current collector", "separator", "electrolyte",
]

# ==================== 判定结果 ====================


@dataclass
class ScreeningDecision:
    action: str = "skip"  # "extract", "include", "skip"
    confidence: float = 0.0
    focus_tasks: List[str] = field(default_factory=lambda: ["all"])
    reason: str = ""


# ==================== 规则评分 ====================


def _score_keyword_hits(text: str, keywords: List[str]) -> int:
    """统计文本中匹配的关键词数量"""
    lower = text.lower()
    count = 0
    for kw in keywords:
        if re.search(kw, lower):
            count += 1
    return count


def _has_number_with_unit(text: str) -> bool:
    """检测是否含数值+单位模式"""
    return bool(
        re.search(r"\d+[\.\,]?\d*\s*[μmnmcmmMkKA°VCΩSgW%]", text)
        or re.search(r"\d+[\.\,]?\d*\s*(?:mAh|mW|mA|mg|mS|mV)", text)
    )


def _screen_cathode(text: str) -> ScreeningDecision:
    t = text.lower()
    comp_score = _score_keyword_hits(t, _COMPONENT_KEYWORDS["cathode"])
    perf_score = _score_keyword_hits(t, _PERFORMANCE_KEYWORDS)
    mat_score = _score_keyword_hits(t, _MATERIAL_KEYWORDS)
    cond_score = _score_keyword_hits(t, _CONDITION_KEYWORDS)
    has_num = _has_number_with_unit(text)
    total = comp_score * 3 + perf_score * 2 + mat_score + cond_score

    if has_num and comp_score >= 1:
        return ScreeningDecision("extract", min(1.0, total / 6), ["all"])
    if total >= 3 and comp_score >= 2:
        return ScreeningDecision("extract", min(1.0, total / 8), ["all"])
    # 材料表征+数字 → 直接 extract（不受组件关键词限制）
    if has_num and mat_score >= 1:
        return ScreeningDecision("extract", min(1.0, total / 6), ["all"])
    if total >= 1 or has_num:
        return ScreeningDecision("include", 0.4, ["all"])

    skip_match = _score_keyword_hits(t, _SKIP_KEYWORDS)
    if skip_match >= 1:
        return ScreeningDecision("skip", 0.0, ["all"])
    if len(t) < 100:
        return ScreeningDecision("skip", 0.0, ["all"])

    return ScreeningDecision("include", 0.2, ["all"])


def _screen_anode(text: str) -> ScreeningDecision:
    t = text.lower()
    comp_score = _score_keyword_hits(t, _COMPONENT_KEYWORDS["anode"])
    perf_score = _score_keyword_hits(t, _PERFORMANCE_KEYWORDS)
    mat_score = _score_keyword_hits(t, _MATERIAL_KEYWORDS)
    cond_score = _score_keyword_hits(t, _CONDITION_KEYWORDS)
    has_num = _has_number_with_unit(text)
    total = comp_score * 3 + perf_score * 2 + mat_score + cond_score

    if has_num and comp_score >= 1:
        return ScreeningDecision("extract", min(1.0, total / 6), ["all"])
    if total >= 3 and comp_score >= 1:
        return ScreeningDecision("extract", min(1.0, total / 8), ["all"])
    # 材料表征+数字 → 直接 extract（不受组件关键词限制）
    if has_num and mat_score >= 1:
        return ScreeningDecision("extract", min(1.0, total / 6), ["all"])
    if total >= 2 or has_num:
        return ScreeningDecision("include", 0.4, ["all"])

    skip_match = _score_keyword_hits(t, _SKIP_KEYWORDS)
    if skip_match >= 1:
        return ScreeningDecision("skip", 0.0, ["all"])
    if len(t) < 100:
        return ScreeningDecision("skip", 0.0, ["all"])

    return ScreeningDecision("include", 0.2, ["all"])


def _screen_electrolyte(text: str) -> ScreeningDecision:
    t = text.lower()
    comp_score = _score_keyword_hits(t, _COMPONENT_KEYWORDS["electrolyte"])
    perf_score = _score_keyword_hits(t, _PERFORMANCE_KEYWORDS)
    mat_score = _score_keyword_hits(t, _MATERIAL_KEYWORDS)
    cond_score = _score_keyword_hits(t, _CONDITION_KEYWORDS)
    has_num = _has_number_with_unit(text)
    total = comp_score * 3 + perf_score * 1 + mat_score * 1 + cond_score

    if has_num and comp_score >= 1:
        return ScreeningDecision("extract", min(1.0, total / 6), ["all"])
    if total >= 3 and comp_score >= 1:
        return ScreeningDecision("extract", min(1.0, total / 8), ["all"])
    # 材料表征+数字 → 直接 extract（不受组件关键词限制）
    if has_num and mat_score >= 1:
        return ScreeningDecision("extract", min(1.0, total / 6), ["all"])
    if total >= 1 or has_num:
        return ScreeningDecision("include", 0.4, ["all"])

    skip_match = _score_keyword_hits(t, _SKIP_KEYWORDS)
    if skip_match >= 1:
        return ScreeningDecision("skip", 0.0, ["all"])
    if len(t) < 100:
        return ScreeningDecision("skip", 0.0, ["all"])

    return ScreeningDecision("skip", 0.0, ["all"])


# ==================== 表格块的筛选（默认 extract） ====================


def _screen_table_block(text: str, component: str) -> ScreeningDecision:
    """表格块默认较高置信度，但检查是否完全无关"""
    t = text.lower()
    comp_keywords = _COMPONENT_KEYWORDS.get(component, [])
    comp_score = _score_keyword_hits(t, comp_keywords)
    if comp_score == 0 and "TABLE DATA BLOCK" in t:
        # 表格块即使没有组件关键词，也可能有数据
        has_num = _has_number_with_unit(text)
        if has_num:
            return ScreeningDecision("extract", 0.6, ["all"])
        return ScreeningDecision("include", 0.4, ["all"])
    return ScreeningDecision("extract", 0.8, ["all"])


# ==================== 主入口 ====================


def screen_extraction_unit(
    text: str, component: str = "cathode"
) -> ScreeningDecision:
    """规则筛选主函数：判断一个段落/表格块是 extract / include / skip

    Args:
        text: 文本（普通段落或 TABLE DATA BLOCK）
        component: 组件类型 (cathode / anode / electrolyte)

    Returns:
        ScreeningDecision 包含 action / confidence / focus_tasks / reason
    """
    if not text or len(text.strip()) < 50:
        return ScreeningDecision("skip", 0.0, ["all"], "text too short")

    # 检测是否为表格块
    if "TABLE DATA BLOCK" in text:
        return _screen_table_block(text, component)

    # 组件相关筛选
    if component == "cathode":
        return _screen_cathode(text)
    elif component == "anode":
        return _screen_anode(text)
    elif component == "electrolyte":
        return _screen_electrolyte(text)
    else:
        # 通用规则
        has_num = _has_number_with_unit(text)
        if has_num:
            return ScreeningDecision("extract", 0.5, ["all"])
        return ScreeningDecision("skip", 0.0, ["all"])


# ==================== include 兜底 ====================


def llm_include_fallback(
    llm: Any,
    text: str,
    component: str,
    decision: ScreeningDecision,
    token_checker: Optional[Any] = None,
) -> ScreeningDecision:
    """当规则判断为 include 时，用 fast LLM 做一次确认

    Args:
        llm: ChatOpenAI 实例（fast model）
        text: 段落文本
        component: 组件类型
        decision: 规则筛选的初步判断
        token_checker: Token 计数器（可选）

    Returns:
        更新后的 ScreeningDecision
    """
    prompt = (
        f"你是一个锂电池 {component} 文献筛选助手。判断以下段落是否包含与 {component} "
        f"相关的制备条件、材料性质、表征数据、测试条件或电化学性能。\n\n"
        f"如果包含相关内容，请只回复 YES。\n"
        f"如果不包含或不确定，请只回复 NO。\n\n"
        f"段落内容：\n\n{text[:2000]}"
    )

    try:
        response = llm.invoke(prompt)
        answer = response.content.strip().upper() if hasattr(response, "content") else str(response).strip().upper()

        if token_checker:
            token_checker.record(f"include-{component}", prompt, answer, "include")

        if answer.startswith("YES"):
            return ScreeningDecision("extract", 0.7, ["all"], "include fallback: YES")
        else:
            return ScreeningDecision("skip", 0.0, ["all"], "include fallback: NO")
    except Exception as e:
        logger.warning(f"Include LLM fallback failed: {type(e).__name__}: {e}")
        # fallback: keep the original include decision
        return ScreeningDecision("extract", 0.5, ["all"], "include fallback error -> extract")
