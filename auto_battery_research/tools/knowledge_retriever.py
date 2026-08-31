"""Knowledge retriever module for AutoBatteryResearch Agent.

Performs dynamic multi-lingual chemistry retrieval across 5,471+ real academic paragraphs
stored in the project's literature database.
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

# 电化学与电池材料跨语言概念映射表
CHEMISTRY_KEYWORD_MAP = {
    # 核心元素与材料体系
    "镍": ["ni", "nickel", "ncm", "nca", "high-ni", "ni-rich", "lini"],
    "高镍": ["high-ni", "ni-rich", "ncm811", "ncm90", "lini", "ncm83"],
    "超高镍": ["ultrahigh-ni", "ncm90", "ncm95", "ni90"],
    "钴": ["co", "cobalt", "ncm", "lco"],
    "低钴": ["low-co", "co-free", "low cobalt", "cobalt-free"],
    "无钴": ["co-free", "cobalt-free", "lnmo", "nmx"],
    "锰": ["mn", "manganese", "ncm", "lnmo", "spinel"],
    "富锂锰基": ["lrich", "lithium-rich", "li-rich manganese", "llo"],
    "锂": ["li", "lithium", "li-metal", "lfp", "lco"],
    "金属锂": ["lithium metal", "li metal", "li anode", "dendrite", "lithium stripping"],
    "铁锂": ["lfp", "lifepo4", "iron phosphate"],
    "磷酸铁锂": ["lfp", "lifepo4", "iron phosphate"],
    "三元": ["ncm", "nca", "ternary", "layered oxide"],
    "单晶": ["single crystal", "single-crystal", "sc-ncm"],
    "硅": ["silicon", "si", "sio", "si/c", "silicon-based"],
    "硅碳": ["silicon carbon", "si/c", "silicon-graphite"],
    "石墨": ["graphite", "carbon", "gr"],
    "铅": ["lead", "pb", "lead-acid"],
    "铅酸": ["lead-acid", "lead dioxide", "pbso4"],
    "钠": ["sodium", "na", "sib", "sodium-ion"],
    "固态": ["solid-state", "solid electrolyte", "garnet", "sulfide", "llzo", "lps"],
    "全固态": ["all-solid-state", "assb", "solid-state battery"],
    
    # 电池组件
    "正极": ["cathode", "positive electrode", "ncm", "lfp"],
    "负极": ["anode", "negative electrode", "lithium metal", "graphite", "silicon"],
    "电解液": ["electrolyte", "lhce", "hce", "solvent", "lifsi", "lipf6", "dme", "tte", "ec", "emc", "fec"],
    "高压电解液": ["high voltage electrolyte", "fluorinated", "lhce", "oxidative stability"],
    "局域高浓": ["lhce", "locally concentrated", "diluent", "tte", "btfe"],
    "添加剂": ["additive", "fec", "vc", "lidfob", "dto", "film-forming"],
    "成膜添加剂": ["film-forming additive", "fec", "vc", "lidfob", "dtd"],
    "隔膜": ["separator", "pp/pe", "coating", "ceramic-coated"],
    "集流体": ["current collector", "copper foil", "aluminum foil", "3d current collector"],
    
    # 电化学特性与机理
    "能量密度": ["energy density", "wh/kg", "wh/l", "specific energy"],
    "比容量": ["specific capacity", "mah/g", "capacity"],
    "配比": ["ratio", "stoichiometry", "composition", "proportion", "content"],
    "比例": ["ratio", "fraction", "proportion", "stoichiometry"],
    "改性": ["modification", "coating", "doping", "surface", "al2o3", "zr"],
    "包覆": ["coating", "surface modification", "al2o3", "tio2", "lbo"],
    "掺杂": ["doping", "zr", "ti", "mg", "al", "dopant", "al-doped"],
    "循环": ["cycling", "cycle life", "retention", "degradation"],
    "首次库仑效率": ["ice", "initial coulombic efficiency", "first cycle efficiency"],
    "库仑效率": ["coulombic efficiency", "ce"],
    "电压": ["voltage", "high voltage", "cutoff voltage", "4.5v", "4.8v"],
    "安全": ["safety", "thermal stability", "flammability", "runaway"],
    "枝晶": ["dendrite", "dead lithium", "seeding", "dendrite growth"],
    "界面": ["interface", "interphase", "sei", "cei", "passivation"],
    "过电位": ["overpotential", "polarization"],
    "快充": ["fast charging", "rate capability", "c-rate", "4c", "6c"],
    "经济性": ["cost", "economic", "cost-effective", "low-cost", "inexpensive", "price"],
    "成本": ["cost", "low-cost", "economic", "cheap", "raw material cost"],
}


def extract_query_keywords(query: str) -> List[str]:
    """从多语言查询中提取电化学与材料英文检索词."""
    q_lower = query.lower()
    search_terms = set()
    
    # 1. 匹配中文字词映射
    for cn_kw, en_terms in CHEMISTRY_KEYWORD_MAP.items():
        if cn_kw in q_lower:
            search_terms.update(en_terms)
            
    # 2. 匹配直接输入的英文字词 (过滤无意义停用词)
    raw_en_tokens = re.findall(r"[a-zA-Z0-9\-\.\+]{2,}", q_lower)
    stopwords = {"the", "and", "for", "with", "what", "how", "best", "should", "make", "battery"}
    for tok in raw_en_tokens:
        if tok not in stopwords:
            search_terms.add(tok)
            
    return list(search_terms)


def search_knowledge_base(query: str, top_k: int = 4, root_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """在 5,471 条真实学术文献段落库中执行动态电化学语义检索."""
    root = root_dir or Path(__file__).resolve().parent.parent.parent
    
    candidates = [
        root / "miner" / "json" / "100" / "paragraph_metadata_v4.json",
        root / "miner" / "json" / "100" / "paragraph_metadata_v4_20260622_155323.json",
    ]
    
    json_path = next((p for p in candidates if p.exists()), None)
    if not json_path:
        return []
        
    try:
        with open(json_path, "r", encoding="utf-8", errors="ignore") as f:
            paragraphs = json.load(f)
    except Exception:
        return []
        
    search_terms = extract_query_keywords(query)
    if not search_terms:
        return []
        
    scored_results = []
    for p in paragraphs:
        text = p.get("paragraph_context", "").lower()
        if len(text) < 40:
            continue
            
        score = 0
        matched_terms = set()
        
        for term in search_terms:
            t_lower = term.lower()
            if len(t_lower) <= 2:
                # 短化学缩写需匹配边界单词
                matches = len(re.findall(r"\b" + re.escape(t_lower) + r"\b", text))
            else:
                matches = text.count(t_lower)
                
            if matches > 0:
                score += min(matches, 4) * (3 if t_lower in ("nickel", "high-ni", "ncm", "cobalt", "electrolyte", "lithium", "lead", "anode", "cathode", "cost") else 1)
                matched_terms.add(term)
                
        # 命中多维度概念组合（如材料+性能+工艺）给予协同加权
        if len(matched_terms) >= 2:
            score += len(matched_terms) * 4
            
        if score > 0 and len(matched_terms) > 0:
            scored_results.append({
                "score": score,
                "title": p.get("title") or p.get("source_paper", "Academic Research Paper"),
                "paper": p.get("source_paper", "DOI Unknown"),
                "component": p.get("component", "General"),
                "label": p.get("label", "General"),
                "snippet": p.get("paragraph_context", "").strip(),
                "matched_terms": list(matched_terms),
            })
            
    scored_results.sort(key=lambda x: x["score"], reverse=True)
    return scored_results[:top_k]
