"""agent/material_norm.py — 材料实体归一化层

把 LLM 抽取出的材料名/化学式映射到规范 id（candidates.json 的 id），
与 src/lmllm/RAG/relation_engine.py 共享同一份词表数据
（src/lmllm/RAG/data/ 下的 candidates.json + alias_map.json，数据单一来源）。

匹配策略（按优先级）：
1. 别名精确匹配（小写 + 去空格/连字符/斜杠变体）
2. 化学式元素计数匹配（LiNi0.8Co0.1Mn0.1O2 vs LiNi0.8Mn0.1Co0.1O2 → 同一材料）
3. 别名包含匹配（长别名优先；配方串输入跳过，防误命中）

定位：只做"LLM 已抽出名称 → 规范 id"的映射，不做全文扫描；
全文实体发现（含未命中候选收集）由 ner_dict 承担。
未命中的材料收集为 unmatched 列表，作为"新实体注册"队列的种子。

用法：
    from agent.material_norm import MaterialNormalizer
    norm = MaterialNormalizer()
    r = norm.normalize("NCM 811")             # -> canonical_id="NCM811", method="alias"
    r = norm.normalize("LiNi0.8Mn0.1Co0.1O2") # -> canonical_id="NCM811", method="formula"
    unmatched = norm.normalize_materials(mats)  # 就地补 material_id/norm_method，返回未命中
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "lmllm" / "RAG" / "data"

# 锂电/材料领域常用元素符号（含全部过渡金属与常见主族）。
# 用于化学式解析时校验合法性，防止 "EC" -> {E:1, C:1} 这类误解析。
_ELEMENTS = set("""
H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn
Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce
Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn
Fr Ra Ac Th Pa U
""".split())

_ELEMENT_RE = re.compile(r"([A-Z][a-z]?)(\d*\.?\d*)")
# 配方串特征：浓度（1M）、介词 in、比例（+ / %）、括号 → 视为整体配方，不做别名包含匹配
_FORMULATION_RE = re.compile(r"\d\s*[Mm]\b|\s\+|\d+\s*%|\(|\)")
# 配方串的盐段："1M LiPF6" / "1M LiPF6 + 0.2M LiDFOB" / "1.15 M LiTFSI"
_FORM_SALT_RE = re.compile(r"([\d.]+\s*[Mm])\s+([A-Za-z][A-Za-z0-9]*)")


def _names_from_dict_field(x) -> List[str]:
    """结构化 formula（dict）字段提取 name 列表（兼容单 dict 和 list）。"""
    if isinstance(x, dict):
        x = [x]
    return [i.get("name", "").strip() for i in (x or [])
            if isinstance(i, dict) and i.get("name")]


def _formula_id(salts: List[str], solvents: List[str], additives: List[str],
                diluents: List[str], concs: List[str]) -> Optional[str]:
    """配方唯一 ID：{盐}_{溶剂}[_{添加剂}][_dil-{稀释剂}]，非常规浓度前缀。

    例: LiPF6 + EC/DMC + 1%VC       -> LiPF6_EC-DMC_VC
        1.2M LiPF6 in EC/DMC       -> 1.2M_LiPF6_EC-DMC
        1M LiPF6 + 0.2M LiDFOB in FEC/EMC -> LiPF6-LiDFOB_FEC-EMC
    盐/溶剂/添加剂各自按字母序去重，保证同一体系唯一 id。
    """
    if not salts or not solvents:
        return None
    salts_s = "-".join(sorted({s.upper() for s in salts}))
    solv_s = "-".join(sorted({s.upper() for s in solvents}))
    add_s = "-".join(sorted({s.upper() for s in additives}))
    dil_s = "-".join(sorted({s.upper() for s in diluents}))
    base = f"{salts_s}_{solv_s}"
    if add_s:
        base += f"_{add_s}"
    if dil_s:
        base += f"_dil-{dil_s}"
    # 非常规浓度（仅单盐且 ≠1M）标注，1M 省略（1.0M/1 M 等同 1M）
    if len(salts) == 1 and concs:
        c0 = re.sub(r"\s+", "", concs[0])
        cm = re.match(r"^([\d.]+)m$", c0, re.I)
        if not (cm and abs(float(cm.group(1)) - 1.0) < 1e-6):
            base = c0 + "_" + base
    return base


def parse_formulation(name: str, formula=None) -> Optional[str]:
    """配方串 → 唯一 id（LiPF6_EC-DMC_VC 模式）。无法解析返回 None。

    formula 为结构化 dict（phase0 输出）时直接取字段；
    否则对 name/公式字符串用正则拆盐/溶剂/添加剂。
    """
    # 1) 结构化 dict
    if isinstance(formula, dict):
        salts = _names_from_dict_field(formula.get("salt") or formula.get("salts"))
        solv = _names_from_dict_field(formula.get("solvents"))
        add = _names_from_dict_field(formula.get("additives"))
        dil = _names_from_dict_field(formula.get("diluents"))
        concs = []
        sf = formula.get("salt")
        if isinstance(sf, dict):
            sf = [sf]
        for s in (sf or []):
            if isinstance(s, dict) and s.get("concentration"):
                concs.append(str(s["concentration"]))
        fid = _formula_id(salts, solv, add, dil, concs)
        if fid:
            return fid
        # dict 解析失败退回字符串路径（formula 字段是 dict 时 name 用不上，直接 None）
        return None

def _parse_composition_text(text: str):
    """解析单个配方字符串 → (salts, solvents, additives, diluents, concs) | None。"""
    text = (text or "").strip()
    m = re.search(r"\s+in\s+", text, re.I)
    if m:
        head, tail = text[:m.start()].strip(), text[m.end():].strip()
    else:
        # 无 "in" 的写法："1.15M LiPF6 EC/DEC (v/v=1/1)"
        m2 = re.match(r"^([\d.]+\s*[Mm]\s+[A-Za-z][A-Za-z0-9]*)\s+(.*)$", text)
        if not m2:
            return None
        head, tail = m2.group(1), m2.group(2)
    salt_ms = _FORM_SALT_RE.findall(head)
    if not salt_ms:
        return None
    salts = [s for _, s in salt_ms]
    concs = [c for c, _ in salt_ms]
    # 尾部分拆：+ 前为主体溶剂，+ 后为添加剂
    parts = re.split(r"\s*\+\s*", tail)
    # 去"比例括号"（含 : / v/v / vol / wt）与无括号比例段（1:4），保留名称括号如 Di-(CF3)-EC
    main = re.sub(r"\([^)]*(?:v/v|vol|wt|:)[^)]*\)", " ", parts[0])
    main = re.sub(r"\d+\s*:\s*\d+", " ", main)
    solv = [s.strip().strip("()") for s in re.split(r"[/:,，、]", main)
            if s.strip().strip("()") and
            re.fullmatch(r"[A-Za-z0-9\-()]*[A-Za-z][A-Za-z0-9\-()]*", s.strip().strip("()"))]
    adds = []
    for p in parts[1:]:
        p = re.sub(r"\([^)]*(?:v/v|vol|wt|:)[^)]*\)", " ", p)
        # 数字+单位前缀只清一次（count=1），避免误删 CF3-EC 的 "3"
        p = re.sub(r"^[\d.]+\s*(?:wt\.?%|vol\.?%|v/v|%|wt|mM|M)?\s*", "", p, count=1)
        p = p.strip().strip("()").rstrip(";,")
        if p and re.fullmatch(r"[A-Za-z0-9\-()]*[A-Za-z][A-Za-z0-9\-()]*", p):
            adds.append(p)
    return salts, solv, adds, [], concs


def parse_formulation(name: str, formula=None) -> Optional[str]:
    """配方串 → 唯一 id（LiPF6_EC-DMC_VC 模式）。无法解析返回 None。

    formula 为结构化 dict（phase0 输出）时直接取字段；
    否则对 name/公式字符串用正则拆盐/溶剂/添加剂。
    """
    # 1) 结构化 dict
    if isinstance(formula, dict):
        salts = _names_from_dict_field(formula.get("salt") or formula.get("salts"))
        solv = _names_from_dict_field(formula.get("solvents"))
        add = _names_from_dict_field(formula.get("additives"))
        dil = _names_from_dict_field(formula.get("diluents"))
        concs = []
        sf = formula.get("salt")
        if isinstance(sf, dict):
            sf = [sf]
        for s in (sf or []):
            if isinstance(s, dict) and s.get("concentration"):
                concs.append(str(s["concentration"]))
        fid = _formula_id(salts, solv, add, dil, concs)
        if fid:
            return fid
        # dict 解析失败退回字符串路径（formula 字段是 dict 时 name 用不上，直接 None）
        return None

    # 2) 字符串（name 或 formula 字段）
    for text in ([name] if isinstance(name, str) else []) + \
               ([formula] if isinstance(formula, str) else []):
        comp = _parse_composition_text(text)
        if comp:
            return _formula_id(*comp)
    return None


def parse_formulation_structured(name: str, formula=None):
    """配方串 → (唯一 id, 结构化组成 {salts, solvents, additives, diluents, concentration})。"""
    if isinstance(formula, dict):
        salts = _names_from_dict_field(formula.get("salt") or formula.get("salts"))
        solv = _names_from_dict_field(formula.get("solvents"))
        add = _names_from_dict_field(formula.get("additives"))
        dil = _names_from_dict_field(formula.get("diluents"))
        concs = []
        sf = formula.get("salt")
        if isinstance(sf, dict):
            sf = [sf]
        for s in (sf or []):
            if isinstance(s, dict) and s.get("concentration"):
                concs.append(str(s["concentration"]))
        fid = _formula_id(salts, solv, add, dil, concs)
        if fid:
            return fid, {"salts": salts, "solvents": solv, "additives": add,
                         "diluents": dil, "concentration": concs}
        return None, None
    for text in ([name] if isinstance(name, str) else []) + \
               ([formula] if isinstance(formula, str) else []):
        comp = _parse_composition_text(text)
        if comp:
            salts, solv, add, dil, concs = comp
            fid = _formula_id(salts, solv, add, dil, concs)
            if fid:
                return fid, {"salts": salts, "solvents": solv, "additives": add,
                             "diluents": dil, "concentration": concs}
    return None, None


# ────────────────────────── 电极改性剥离（分层归一化） ──────────────────────────

_MOD_COATING_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9\-()]*)\s*(?:-coated|-wrapped|coated|wrapped|encapsulated)\b", re.I)
_MOD_DOPANT_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9\-()]*)\s*(?:-doped|-substituted|doped|substituted)\b", re.I)
_MORPH_WORDS = ["nanoparticles", "nanowires", "nanosheets", "nanofibers", "nanoparticle",
                "nanowire", "nanosheet", "nanofiber", "microspheres", "microsphere",
                "nanocrystals", "nanocrystal", "single-crystal", "single crystal",
                "nanostructured", "porous", "hollow", "nano-structured", "nano"]
_TREAT_WORDS = ["prelithiated", "pre-lithiated", "prelithiation", "pre-lithiation",
                "activated", "pre-treated", "pretreated"]


def parse_modifications(name: str):
    """电极材料名 → (base_text, mods)。

    "Al2O3-coated NCM811"      -> ("NCM811", {coating: [Al2O3]})
    "Nd-doped NCM955"          -> ("NCM955", {dopants: [Nd]})
    "N,S co-doped hard carbon" -> ("hard carbon", {dopants: [N, S]})
    "graphene-wrapped Si nanoparticles" -> ("graphene Si", {coating: [graphene], morphology: [nanoparticles]})
    "prelithiated Si-Gr anode" -> ("Si-Gr anode", {treatment: [prelithiated]})
    """
    mods: Dict[str, List[str]] = {}
    s = name.strip()
    if "@" in s:  # "NCM811@Al2O3"
        left, right = s.split("@", 1)
        mods.setdefault("coating", []).append(right.strip().strip("()"))
        s = left.strip()
    m = _MOD_COATING_RE.search(s)
    if m:
        mods.setdefault("coating", []).append(m.group(1).rstrip("-"))
        s = (s[:m.start()] + " " + s[m.end():]).strip()
    m = re.search(r"([A-Za-z](?:\s*,\s*[A-Za-z])*)\s+co-doped\b", s, re.I)
    if m:  # 先处理 co-doped，避免被单元素 doped 正则抢先吃掉 "co-"
        mods.setdefault("dopants", []).extend(
            [x.strip() for x in re.split(r"[,\s]+", m.group(1)) if x.strip()])
        s = (s[:m.start()] + " " + s[m.end():]).strip()
    m = _MOD_DOPANT_RE.search(s)
    if m:
        mods.setdefault("dopants", []).append(m.group(1).rstrip("-"))
        s = (s[:m.start()] + " " + s[m.end():]).strip()
    m = re.search(r"([A-Za-z][A-Za-z0-9\-()]*)\s*(?:-based|based)\b", s, re.I)
    if m:  # "Si-based" -> 保留前缀 "Si"
        s = (s[:m.start()] + m.group(1).rstrip("-") + " " + s[m.end():]).strip()
    for w in _MORPH_WORDS:
        if re.search(rf"\b{re.escape(w)}\b", s, re.I):
            mods.setdefault("morphology", []).append(w.lower())
            s = re.sub(rf"\b{re.escape(w)}\b", " ", s, flags=re.I)
    for w in _TREAT_WORDS:
        if re.search(rf"\b{re.escape(w)}\b", s, re.I):
            mods.setdefault("treatment", []).append(w.lower())
            s = re.sub(rf"\b{re.escape(w)}\b", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(), mods


def variant_id(base: str, mods: Optional[Dict[str, List[str]]]) -> str:
    """材料变体 id：base + 改性后缀。

    NCM811 + coating Al2O3     -> "NCM811_Al2O3-coated"
    NCM955 + dopants Nd        -> "NCM955_Nd-doped"
    si_base + treatment prelithiated -> "si_base_prelithiated"
    无改性 -> base（与旧行为一致）
    """
    if not mods:
        return base
    suffix = {"coating": "coated", "dopants": "doped", "composite": "comp"}
    parts = [base]
    for typ, vals in mods.items():
        for v in vals:
            parts.append(f"{v}-{suffix[typ]}" if typ in suffix else v)
    return "_".join(parts)


def _norm_token(s: str) -> str:
    """归一化匹配 token：小写 + 去掉空白/连字符/下划线/斜杠/点。"""
    return re.sub(r"[\s\-_/.,]+", "", s).lower()


def formula_counts(formula: str) -> Optional[Dict[str, float]]:
    """解析化学式 -> {元素: 计数}；含括号/变量系数/非法元素时返回 None。

    例: "LiNi0.8Co0.1Mn0.1O2" -> {Li:1.0, Ni:0.8, Co:0.1, Mn:0.1, O:2.0}
    "EC/DMC"、"SiOx"、"1M LiPF6 in EC/DEC" -> None（不强行解析）
    """
    if not formula or not isinstance(formula, str):
        return None  # phase0 的 formula 可能是结构化 dict（电解液配方），非 str 跳过
    f = formula.strip()
    if not f:
        return None
    matches = _ELEMENT_RE.findall(f)
    if not matches:
        return None
    # 校验：所有字符都被解析消费（残留 / x 变量系数等 → 拒绝，
    # 防 "SiOx" 误解析成 {Si:1,O:1}、"EC/DMC" 解析出非法元素）
    if "".join(sym + num for sym, num in matches) != f:
        return None
    counts: Dict[str, float] = {}
    for sym, num in matches:
        if sym not in _ELEMENTS:
            return None
        n = float(num) if num else 1.0
        counts[sym] = counts.get(sym, 0.0) + n
    if not counts:
        return None
    return counts


def counts_key(counts: Dict[str, float]) -> str:
    """元素计数 -> 规范化字符串键（元素按字母序，格式 :g 统一 0.8/0.80）。"""
    return "".join(f"{s}{counts[s]:g}" for s in sorted(counts))


@dataclass
class NormResult:
    canonical_id: Optional[str]
    method: str            # "alias" | "formula" | "alias-contains" | "formulation" | "none"
    matched_alias: str = ""
    base_id: Optional[str] = None   # 电极基础材料 id / 电解液配方 id
    mods: Optional[Dict] = None     # 电极 {coating,dopants,morphology,treatment} / 电解液 {salts,solvents,...}


class MaterialNormalizer:
    """材料归一化器：LLM 抽取名称 -> 规范 id。"""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.alias_map: Dict[str, List[str]] = {}
        self.candidates: Dict = {}
        self._alias_index: Dict[str, str] = {}    # norm_token -> canonical
        self._alias_items: List[Tuple[str, str]] = []  # (norm_token, canonical)，含匹配用
        self._formula_index: Dict[str, str] = {}  # counts_key -> canonical
        self._id_category: Dict[str, str] = {}    # canonical -> cathode/anode/...
        self._load()

    # ────────────────────────── 加载（与 relation_engine 同源） ──────────────────────────

    def _load(self) -> None:
        try:
            self.alias_map = json.loads(
                (self.data_dir / "alias_map.json").read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[material_norm] alias_map 加载失败: {e}")
        try:
            self.candidates = json.loads(
                (self.data_dir / "candidates.json").read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[material_norm] candidates 加载失败: {e}")
        self._build_indexes()

    def _build_indexes(self) -> None:
        for canonical, aliases in self.alias_map.items():
            if not isinstance(aliases, list):
                continue  # 顶层 version/updated/note 等元数据字段
            self._alias_index.setdefault(_norm_token(canonical), canonical)
            for a in aliases:
                a = a.strip()
                if not a:
                    continue
                self._alias_index.setdefault(_norm_token(a), canonical)
                c = formula_counts(a)
                if c:
                    self._formula_index.setdefault(counts_key(c), canonical)
        for cat, items in self.candidates.items():
            if not isinstance(items, list):
                continue  # 顶层 version/updated/note 等元数据字段
            for it in items:
                if not isinstance(it, dict):
                    continue
                cid = it.get("id")
                if not cid:
                    continue
                self._id_category[cid] = cat
                self._alias_index.setdefault(_norm_token(cid), cid)
                c = formula_counts(it.get("formula") or "")
                if c:
                    self._formula_index.setdefault(counts_key(c), cid)
        # 含匹配用：按 token 长度降序，长别名优先（避免 "C" 误命中；<3 字符只做精确匹配）
        self._alias_items = sorted(
            ((t, c) for t, c in self._alias_index.items() if len(t) >= 3),
            key=lambda kv: -len(kv[0]),
        )

    # ────────────────────────── 归一化 ──────────────────────────

    def normalize(self, name: str, formula: str = "", base_hint: str = "",
                  mods_hint: Optional[Dict] = None) -> NormResult:
        """单个材料名 -> 规范 id（分层：电极 base+mods，电解液配方 id）。

        优先级：配方串（电解液）> LLM base_hint > 电极改性剥离 > 别名精确 > formula > 化学式 > 包含。
        base_hint/mods_hint 来自 phase0 的 LLM 结构化输出（有全文语境，优先于规则剥离）。
        """
        if not isinstance(name, str):
            name = str(name) if name is not None else ""
        name = name.strip()
        if not name:
            return NormResult(None, "none")

        # 1. 配方串（电解液）：结构化组成 + 配方 id
        if _FORMULATION_RE.search(name) or re.search(r"\s+in\s+", name, re.I):
            fid, comp = parse_formulation_structured(name, formula)
            if fid and fid in self._id_category:
                return NormResult(fid, "formulation", name, base_id=fid, mods=comp)
            return NormResult(None, "none")  # 配方无法 id 化 → 未命中（防误命中，进注册队列）

        # 2. LLM base_hint（phase0 结构化输出，语义判断优先）
        mods: Dict[str, List[str]] = dict(mods_hint or {})
        if base_hint:
            hb = base_hint.strip()
            if hb:
                r_hint = self._resolve_base(hb, formula)
                if r_hint[0]:
                    vid = variant_id(r_hint[0], mods or None)
                    return NormResult(vid, f"{r_hint[1]}+llm-base", name,
                                      base_id=r_hint[0], mods=mods or None)

        # 3. 电极改性剥离：base_text + mods（规则，与 LLM mods 合并：LLM 优先，规则补缺）
        base_text, rule_mods = parse_modifications(name)
        for k, v in rule_mods.items():
            mods.setdefault(k, [])
            for x in v:
                if x not in mods[k]:
                    mods[k].append(x)
        bt = base_text.strip()
        if not bt:
            return NormResult(None, "none")

        cid, method = self._resolve_base(bt, formula)
        if cid:
            vid = variant_id(cid, mods or None)
            return NormResult(vid, method, name, base_id=cid, mods=mods or None)
        return NormResult(None, "none")

    def _resolve_base(self, base_text: str, formula: str = "") -> tuple:
        """基础材料文本 -> (canonical_id, method)。别名精确 > formula > 化学式 > 包含。"""
        tok = _norm_token(base_text)
        cid = self._alias_index.get(tok)
        method = "alias"
        if not cid and formula:
            c = formula_counts(formula)
            if c:
                cid = self._formula_index.get(counts_key(c))
                method = "formula"
        if not cid:
            c = formula_counts(base_text)
            if c:
                cid = self._formula_index.get(counts_key(c))
                method = "formula"
        if not cid:
            for alias_tok, canonical in self._alias_items:
                if alias_tok in tok:
                    cid = canonical
                    method = "alias-contains"
                    break
        return cid, method

    def normalize_materials(self, mats: List[Dict]) -> List[Dict]:
        """就地增强 phase0 输出的 materials 列表。

        命中: 写回 material_id=规范 id（phase3 归组直接用），加 norm_method/matched_alias。
        未命中: 保留原 material_id，加 norm_method="none"，并收集进返回列表（注册队列种子）。
        """
        unmatched: List[Dict] = []
        for m in mats or []:
            if not isinstance(m, dict):
                continue
            r = self.normalize(m.get("name", ""), m.get("formula", ""),
                               base_hint=m.get("base_material", ""), mods_hint=m.get("mods"))
            m["norm_method"] = r.method
            if r.canonical_id:
                m["material_id"] = r.canonical_id          # 变体 id（电极 base+mods / 电解液配方 id）
                m["base_id"] = r.base_id or r.canonical_id  # 基础材料 id
                m["material_mods"] = r.mods or {}           # 改性维度（分层）
                m["matched_alias"] = r.matched_alias
            else:
                m.setdefault("material_id", m.get("name", ""))
                # 未命中也保留 LLM 分层信息（注册流程参考）
                if m.get("base_material") or m.get("mods"):
                    m["base_id"] = m.get("base_material", "")
                    m["material_mods"] = m.get("mods") or {}
                unmatched.append({
                    "name": m.get("name", ""),
                    "formula": m.get("formula", ""),
                    "material_id": m.get("material_id", ""),
                    "component": m.get("role", ""),
                })
        return unmatched

    def category_of(self, canonical_id: str) -> str:
        return self._id_category.get(canonical_id, "unknown")

    def known_ids(self) -> List[str]:
        return list(self._id_category.keys())

    def normalize_condition_components(self, conds: List[Dict]) -> None:
        """条件里的组件字段归一化（cell 配置规范化）。

        electrolyte -> 配方 id（electrolyte_id）；counter_electrode -> 材料 id（counter_electrode_id）。
        保留原文，新增 *_id 字段（不破坏现有输出）。"""
        for c in conds or []:
            if not isinstance(c, dict):
                continue
            cond = c.get("condition")
            if not isinstance(cond, dict):
                continue
            e = cond.get("electrolyte")
            if isinstance(e, str) and e.strip() and not cond.get("electrolyte_id"):
                fid = parse_formulation(e)
                if fid:
                    cond["electrolyte_id"] = fid
            ce = cond.get("counter_electrode")
            if isinstance(ce, str) and ce.strip() and not cond.get("counter_electrode_id"):
                r = self.normalize(ce)
                if r.canonical_id:
                    cond["counter_electrode_id"] = r.canonical_id


# ══════════════════════════════════════════════════════════════
# 自测（不依赖 API）：python agent/material_norm.py
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    norm = MaterialNormalizer()
    print(f"词表: {len(norm.known_ids())} 个规范 id: {sorted(norm.known_ids())}")
    print()

    cases = [
        # (name, formula, 期望 canonical, 期望 method)
        ("NCM811", "", "NCM811", "alias"),
        ("NCM 811", "", "NCM811", "alias"),
        # 以下化学式在 alias_map 中是显式别名 → alias 命中（结果相同）
        ("LiNi0.8Co0.1Mn0.1O2", "", "NCM811", "alias"),
        ("LiNi0.8Mn0.1Co0.1O2", "", "NCM811", "alias"),      # 元素顺序不同
        ("LiNi0.80Co0.10Mn0.10O2", "", "NCM811", "formula"),  # 小数点变体，纯 formula 路径
        ("graphite", "", "graphite", "alias"),
        ("石墨", "", "graphite", "alias"),                       # 中文别名
        ("Li metal", "", "li_metal", "alias"),
        ("EC/DMC", "", "carbonate_ec", "alias"),
        ("EC/DMC-based electrolyte", "", "carbonate_ec", "alias-contains"),
        ("SiOx", "", "si_base", "alias"),
        ("LiNi0.5Mn1.5O4", "", "LNMO", "alias"),   # alias_map 显式别名
        ("富锂锰基", "", "LRMO", "alias"),
        ("Li1.2Ni0.2Mn0.6O2", "", "LRMO", "alias"),  # alias_map 显式别名
        ("C", "", "graphite", "alias"),
        ("LiPO2F2", "", "LiPO2F2", "alias"),
        # 配方串/新材料：不误命中、进注册队列
        ("1M LiPF6 in EC/DEC", "", "LIPF6_DEC-EC", "formulation"),  # 配方 ID 化命中（词表 0.4 注册）
        ("E-PFPN (1M LiTFSI in DME + 20% HFE + 5% PFPN)", "", None, "none"),
        ("novel additive X", "", None, "none"),
        # 类型防护：phase0 的 formula 可能是 dict（电解液配方），name 可能是非 str
        ("NCM811", {"salt": {"name": "LiTFSI"}, "solvents": [{"name": "DME"}]}, "NCM811", "alias"),
        ({"nested": "name"}, "", None, "none"),
        # 电极改性分层：base + mods -> 变体 id
        ("Al2O3-coated NCM811", "", "NCM811_Al2O3-coated", "alias"),
        ("Nd-doped NCM955", "", "NCM955_Nd-doped", "alias"),
        ("N,S co-doped hard carbon", "", "hard_carbon_N-doped_S-doped", "alias"),
        ("prelithiated Si-Gr anode", "", "si_base_prelithiated", "alias-contains"),
        ("NCM811@Al2O3", "", "NCM811_Al2O3-coated", "alias"),
        ("Si-based anode", "", "si_base", "alias"),
        ("LiCoO2 cathode", "", "LCO", "alias"),
    ]
    fails = 0
    for name, formula, want_id, want_method in cases:
        r = norm.normalize(name, formula)
        ok = (r.canonical_id == want_id) and (r.method == want_method)
        if not ok:
            fails += 1
        tag = "OK" if ok else "FAIL"
        got = r.canonical_id if r.canonical_id else "(none)"
        print(f"  [{tag}] {name!r} -> {got} (method={r.method}, want={want_id}/{want_method})")

    # 批量增强自测
    print()
    mats = [
        {"name": "NCM811", "formula": "LiNi0.8Co0.1Mn0.1O2", "role": "cathode"},
        {"name": "E-PFPN (1M LiTFSI in DME + 20% HFE + 5% PFPN)", "formula": "", "role": "electrolyte",
         "material_id": "E-PFPN (1M LiTFSI in DME + 20% HFE + 5% PFPN)"},
    ]
    unmatched = norm.normalize_materials(mats)
    print(f"批量: mats[0].material_id={mats[0]['material_id']} (norm_method={mats[0]['norm_method']})")
    print(f"批量: mats[1].material_id={mats[1]['material_id']} (norm_method={mats[1]['norm_method']})")
    print(f"批量: unmatched={unmatched}")
    ok_batch = mats[0]["material_id"] == "NCM811" and len(unmatched) == 1
    if not ok_batch:
        fails += 1

    print()
    print(f"结果: {'全部通过' if fails == 0 else f'{fails} 个用例失败'}")
    raise SystemExit(1 if fails else 0)
