"""六节研报的证据分析与实验路线，保留引用、不将初始提案当作最终参数。"""

import re
from pathlib import Path

from .report_contract import (
    PRECURSOR_LABELS,
    compute_cathode_pore_metrics,
    detect_synthesis_routes,
    display,
    format_scalar_or_range,
    parse_numeric_range,
)


def _classify_excerpt_anchors(para: str, scheme: dict) -> tuple[bool, bool, bool]:
    """判定归档段落是否属对照/备选路线，或与第 3 节锁定合成路线/主烧温度存在冲突。"""
    src = str(para or "")
    first_line = src.lstrip().splitlines()[0] if src.strip() else ""
    is_control = bool(
        re.match(r"^\*{0,2}(?:合成)?路线\s*[BC乙丙]\b|^\*{0,2}备选路线", first_line)
        or re.search(r"[（(]\s*(?:对照|备选)(?:路线)?\s*[)）]", first_line)
    )
    anchors = (scheme or {}).get("design_anchors") or {}
    if not isinstance(anchors, dict):
        return is_control, False, False
    locked_ptype = str(anchors.get("precursor_type") or "").strip()
    mentioned = detect_synthesis_routes(src)
    route_conflict = bool(
        locked_ptype
        and mentioned
        and (mentioned - {locked_ptype})
        and (locked_ptype not in mentioned or is_control)
    )
    temp_target = parse_numeric_range(anchors.get("calcination_temp_range_c") or anchors.get("calcination_temp_c"))
    temp_conflict = False
    if temp_target is not None:
        t_lo, t_hi = temp_target
        calc_ranges = []
        for clause_m in re.finditer(r"(?:主烧|烧结|煅烧)[^\n。；;]*", src):
            for tm in re.finditer(r"(?<![\d.])(\d{3,4})(?:\s*[~–—-]\s*(\d{3,4}))?\s*(?:°C|℃)", clause_m.group(0)):
                lo = float(tm.group(1))
                hi = float(tm.group(2)) if tm.group(2) else lo
                if lo > hi:
                    lo, hi = hi, lo
                calc_ranges.append((lo, hi))
        if calc_ranges and not any(lo <= (t_hi + 5.0) and hi >= (t_lo - 5.0) for lo, hi in calc_ranges):
            temp_conflict = True
    return is_control, route_conflict, temp_conflict


def _append_excerpt_caveats(
    lines: list[str],
    snippet: str,
    scheme: dict,
    ep_metrics: dict[str, float] | None,
    *,
    is_control: bool,
    route_conflict: bool,
    temp_conflict: bool,
) -> None:
    """对摘录中的路线冲突、E/P 孔体积误用、外推证据降级与幻象交叉引用追加确定性核对提示。"""
    anchors = (scheme or {}).get("design_anchors") or {}
    if is_control or route_conflict or temp_conflict:
        parts = []
        ptype = str(anchors.get("precursor_type") or "").strip()
        if ptype:
            parts.append(f"合成路线：{PRECURSOR_LABELS.get(ptype, ptype)}")
        temp_val = anchors.get("calcination_temp_range_c") or anchors.get("calcination_temp_c")
        if temp_val is not None and temp_val != "":
            parts.append(f"主烧温度：{format_scalar_or_range(temp_val)} °C")
        anchor_hint = f"（第 3 节锁定：{'，'.join(parts)}）" if parts else ""
        lines.append(
            f"**与第 3 节锚点对照提示**：上述摘录属于对照/备选路线或载有与当前锁定主工艺不同的参数{anchor_hint}，正式制备以第 3 节锁定锚点与第 5 节主路线为准。"
        )
    if ep_metrics and ep_metrics["ratio_min"] >= 3.0 and re.search(
        r"(?:贴近|接近|匹配)[^\n。；;]{0,16}孔体积", snippet
    ):
        vol_str = format_scalar_or_range(anchors.get("electrolyte_volume_uL"))
        lines.append(
            f"**注液量物理核对警示**：按第 3 节正极几何与极片面积核算，单片理论孔体积约 {ep_metrics['v_pore_uL']:.2f} μL，"
            f"设计注液量 {vol_str} μL 对应液孔比 E/P ≈ {ep_metrics['ratio_min']:.1f}×（≥ 3.0×，实为过量泛液测试基准而非贴近孔体积）；原文“贴近/匹配孔体积”表述不成立。"
        )
    if re.search(
        r"(?:α|alpha)\s*[=＝≈]\s*0\.5|(?:降级|外推|单元素)[^\n。；;]{0,16}(?:DFT|扩散|迁移|保持率|预期)",
        snippet,
        re.I,
    ):
        lines.append(
            "**证据等级核对提示**：上述摘录涉及理论代理、单元素外推或降级插值估算（如 α=0.5 近似），仅作定性趋势与筛选参考，不得直接升格为本体系实测定量预期。"
        )
    if re.search(r"(?:完整|原|上述)方案第[一二三四五六七八九十\d]+节(?:排行)?表", snippet):
        lines.append(
            "**交叉引用核对提示**：原文提及的方案内部表编号在综合研报中不对应同编号章节，请以 [设计方案原文](design_scheme.md) 及本文第 3/5 节为准。"
        )


def source_identity(e):
    meta = e.get("metadata") or {}
    source = str(e.get("source") or meta.get("source_file") or "")
    source = re.sub(r"^(?:.*?[/\\])?(?=(?:database|papers)[/\\])", "", source).replace("\\", "/")
    doi = e.get("doi") or meta.get("doi") or meta.get("source_paper") or ""
    if not str(doi).startswith("10."):
        match = re.search(r"(10\.\d{4,9})[_/]([^/\\]+?)(?:\.md)?$", source)
        doi = f"{match[1]}/{match[2]}" if match else ""
    title = str(e.get("title") or "").strip() or str(meta.get("title") or "").strip() or "题名未提供"
    key = str(doi).lower() or source.replace("\\", "/").split("/")[-1] or str(e.get("passage_id"))
    return key, str(doi), title, source


def render_sources(doc):
    groups = {}
    for e in doc.get("evidence") or []:
        key, doi, title, source = source_identity(e)
        group = groups.setdefault(key, {"doi": doi, "title": title, "paths": [], "ids": []})
        if group["title"] == "题名未提供" and title and title != "题名未提供":
            group["title"] = title
        if source not in group["paths"]:
            group["paths"].append(source)
        if e.get("passage_id") not in group["ids"]:
            group["ids"].append(e.get("passage_id"))
    lines = [f"共归档 {len(doc.get('evidence') or [])} 个证据段落，按 DOI 或来源文件归并为 {len(groups)} 个来源。",
             "同一论文的不同段落不视为独立重复验证；没有 DOI 的来源按文件名归并，独立性仍需人工核实。", ""]
    for i, g in enumerate(groups.values(), 1):
        lines += [f"### 文献 {i}：{display(g['title'])}", f"- DOI：{display(g['doi'])}",
                  "- 段落 ID：" + ", ".join(f"[{pid}]" for pid in g["ids"] if pid)]
        lines.extend(f"- 原始来源：`{p}`" for p in g["paths"])
    return "\n".join(lines)


def _leading_label(para):
    """段首加粗小标题 (如 **正极（主选 NCM811）**)；无标题时返回空串。"""
    match = re.match(r"\s*\*\*(.+?)\*\*", para)
    return match.group(1) if match else ""


_SKIP_HEADING_RE = re.compile(r"风险|数据缺口|预期关键指标|与用户约束|基于文献推断|参考来源")
_SKIP_BOLD_BLOCK_RE = re.compile(r"^\s*\*\*(?:主要)?风险|^\s*\*\*(?:关键)?数据缺口")
_SKIP_RISK_ITEM_RE = re.compile(
    r"^\s*\d+[\.\、]\s*\*\*(?:掺杂偏析|杂相|电解液副反应|半电池固有偏差|缺失参数|验证闭环)"
)


def _resolve_component_value(scheme: dict, key: str):
    """从 scheme 或锁定 design_anchors 解析组件选型，避免与第 3 节锚点矛盾。"""
    if not isinstance(scheme, dict):
        return None
    val = scheme.get(key)
    if val:
        return val
    anchors = scheme.get("design_anchors") or {}
    if not isinstance(anchors, dict):
        return None
    if key == "separator" and anchors.get("separator"):
        return anchors["separator"]
    if key == "current_collector":
        if anchors.get("current_collector"):
            return anchors["current_collector"]
        al = anchors.get("al_foil_um")
        if al is not None and al != "":
            if isinstance(al, (int, float)) and not isinstance(al, bool):
                al_str = f"{float(al):g}"
            else:
                al_str = re.sub(r"\s*[~–—-]\s*", "–", str(al).strip())
            return f"Al 箔 ({al_str} μm)"
    return None


def _term_pattern(term: str) -> re.Pattern[str]:
    """为缩写/英文术语补充边界约束，防止子串误匹配（如 NCA 命中 encapsulated）。"""
    escaped = re.escape(term)
    prefix = r"(?<![A-Za-z0-9])" if term[0].isascii() and term[0].isalnum() else ""
    lower = term.lower()
    if lower in ("ncm", "celgard"):
        suffix = r"(?![A-Za-z])"
    elif lower == "li1.2":
        suffix = r"(?![0-9])"
    elif lower in ("separator", "current collector"):
        suffix = ""
    elif term[-1].isascii() and term[-1].isalnum():
        suffix = r"(?![A-Za-z0-9])"
    else:
        suffix = ""
    return re.compile(prefix + escaped + suffix, re.I)


def _extract_relevant_clauses(decision: str, term_patterns: list[re.Pattern[str]], resolved: list[str]) -> str | None:
    """从多组件共现段落中提取匹配当前组件的句子，避免跨小节整段原文重复。"""
    if "\n- 或 " in decision:
        return None
    clauses = [
        c.strip()
        for c in re.findall(r"[^。；]+(?:[。；]+(?:\s*\[[^\]]+\])*|$)", decision.strip())
        if c.strip()
    ]
    if len(clauses) > 1:
        matched = [c for c in clauses if any(pat.search(c) for pat in term_patterns)]
        if matched and len(matched) < len(clauses):
            joined = " ".join(matched) if any(re.search(r"\]\s*$", c) for c in matched[:-1]) else "".join(matched)
            if resolved and not any(f"[{pid}]" in joined for pid in resolved):
                cites = "".join(f"[{pid}]" for pid in resolved)
                if joined.endswith(("。", "；")):
                    joined = joined[:-1].rstrip() + f" {cites}。"
                else:
                    joined = f"{joined} {cites}。"
            elif joined.endswith("；"):
                joined = joined[:-1] + "。"
            return joined
    if "\n- " in decision:
        lines = [ln.strip() for ln in decision.splitlines() if ln.strip()]
        content_lines = [ln for ln in lines if ln.startswith("- ") or not re.match(r"^\*\*[^*]+\*\*[：:]?$", ln)]
        if len(content_lines) > 1:
            matched_lines = [ln for ln in content_lines if any(pat.search(ln) for pat in term_patterns)]
            if matched_lines and len(matched_lines) < len(content_lines):
                joined = "\n".join(matched_lines)
                if resolved and not any(f"[{pid}]" in joined for pid in resolved):
                    cites = "".join(f"[{pid}]" for pid in resolved)
                    joined = f"{joined} {cites}"
                return joined
    return None


def _split_material_paragraphs(text: str) -> list[str]:
    """按空行与编号条目拆分正文，并跟踪 Markdown 标题层级以排除风险/缺口/预期指标小节。"""
    heading_stack: dict[int, str] = {}
    paragraphs: list[str] = []
    for raw_block in re.split(r"\n\s*\n", text):
        if not raw_block.strip():
            continue
        sub_blocks: list[str] = []
        cur_lines: list[str] = []
        for line in raw_block.splitlines():
            if re.match(r"^\s*#{1,6}\s+\S", line):
                if cur_lines:
                    sub_blocks.append("\n".join(cur_lines))
                    cur_lines = []
                sub_blocks.append(line)
            else:
                cur_lines.append(line)
        if cur_lines:
            sub_blocks.append("\n".join(cur_lines))

        for sub in sub_blocks:
            hm = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", sub)
            if hm:
                lvl = len(hm.group(1))
                for k in [k for k in heading_stack if k >= lvl]:
                    del heading_stack[k]
                heading_stack[lvl] = hm.group(2)
                continue
            if any(_SKIP_HEADING_RE.search(h) for h in heading_stack.values()):
                continue
            if _SKIP_BOLD_BLOCK_RE.match(sub):
                continue
            for part in re.split(r"\n(?=\s*(?:\*\*\s*(?:\(\d+\)|（\d+）|\d+[\.\、])|(?:\(\d+\)|（\d+）|\d+[\.\、])\s*\*\*))", sub):
                part_s = part.strip()
                if not part_s or _SKIP_RISK_ITEM_RE.match(part_s):
                    continue
                paragraphs.append(part_s)
    return paragraphs


_ORPHAN_BOLD_NUM_RE = re.compile(r"^(\*\*\s*)(?:\(\d+\)|（\d+）|\d+[\.\、])\s*")
_ORPHAN_PLAIN_NUM_RE = re.compile(r"^(?:\(\d+\)|（\d+）)\s*")
_ORPHAN_LIST_NUM_RE = re.compile(r"^\d+[\.\、]\s*(?=\*\*)")
_SUBSCRIPT_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _clean_excerpt_text(text: str) -> str:
    """规范化 LiDFOB 大小写并剥离拆段后遗留的孤立条目序号（如 **(7) 标题**）。"""
    s = re.sub(r"\bLIDFOB\b", "LiDFOB", str(text or "").strip())
    s = _ORPHAN_BOLD_NUM_RE.sub(r"\1", s)
    s = _ORPHAN_PLAIN_NUM_RE.sub("", s)
    s = _ORPHAN_LIST_NUM_RE.sub("", s)
    return s.strip()


def _normalize_match_text(text: str) -> str:
    s = str(text or "").translate(_SUBSCRIPT_MAP)
    s = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰⁺⁻]+", "", s)
    return s.lower()


def _material_sort_key(item: tuple, name: str, scheme: dict) -> tuple:
    para = item[0]
    conflict = any(_classify_excerpt_anchors(para, scheme))
    if name != "正极":
        return (conflict,)
    anchors = scheme.get("design_anchors") if isinstance(scheme.get("design_anchors"), dict) else {}
    pf_norm = _normalize_match_text(anchors.get("primary_formula") or "")
    para_norm = _normalize_match_text(para)
    has_primary = bool(pf_norm and pf_norm in para_norm)
    has_rationale = any(
        k in para
        for k in (
            "主推方案", "主推候选", "主推荐", "Rank #1", "名义化学式", "改性方案",
            "双位点", "共掺杂", "掺量寻优", "理论依据", "晶格", "氧释放", "电压衰减",
        )
    )
    head = para[:40]
    is_feasibility_only = any(k in head for k in ("合成可行性", "测试可行性")) and not has_primary
    return (conflict, not has_primary, not has_rationale, is_feasibility_only)


def _render_missing_material_contract(name: str, scheme: dict, doc: dict) -> str | None:
    """当组件缺少带编号文献分析段时，为有契约声明的关键组件输出严格忠于 design_scheme.json 的结构化说明。"""
    from auto_battery_research.util.report_contract import _is_additives_locked
    anchors = scheme.get("design_anchors") if isinstance(scheme.get("design_anchors"), dict) else {}
    locked_lower = {str(x).strip().lower() for x in (scheme.get("user_locked_components") or []) if str(x).strip()}
    if name == "正极":
        pf = anchors.get("primary_formula")
        cathode = scheme.get("cathode")
        if not pf and not cathode:
            return None
        pf_text = f"主推候选名义化学式为 `{pf}`（Rank #1，待实验验证），" if pf else ""
        return (
            f"- 归档提案正文中未提供带文献编号的正极专题段落；依结构化设计契约（`design_scheme.json`），"
            f"当前正极体系为 `{cathode or '未指定'}`，{pf_text}具体界面/结构机制与循寿命指标由第 3 节与第 5 节实验矩阵闭环验证（不补造未引用文献依据）。"
        )
    if name == "负极" and scheme.get("anode"):
        anode_name = str(scheme.get("anode")).strip()
        anode_geo = (scheme.get("geometry") or {}).get("anode") or {}
        l_um = anode_geo.get("L_um")
        locked_flag = "（用户硬锁定组件）" if anode_name.lower() in locked_lower else ""
        geo_note = f"，几何参数厚度 {l_um} μm" if l_um is not None else ""
        if anode_name.lower() in {"li_metal", "li", "lithium", "锂金属", "金属锂"}:
            extra = "半电池测试中金属锂片仅作过量储锂对电极（非全电池商业负极匹配验证），事后锂过量倍数及低 N/P 全电池映射须以第 3 节边界与第 5 节实验为准"
        else:
            extra = "其首效、膨胀与倍率匹配须结合第 5 节实验与第 3 节边界条件共同解读"
        return (
            f"- 归档提案正文中未提供带文献编号的负极专题段落；依结构化设计契约（`design_scheme.json`），"
            f"当前选定负极为 `{anode_name}`{locked_flag}{geo_note}。{extra}（不补造未引用文献依据）。"
        )
    if name == "电解液与添加剂" and (scheme.get("electrolyte") or scheme.get("additives")):
        elec_name = re.sub(r"\bLIDFOB\b", "LiDFOB", str(scheme.get("electrolyte") or "未指定").strip())
        additives = [re.sub(r"\bLIDFOB\b", "LiDFOB", str(a).strip()) for a in (scheme.get("additives") or []) if str(a).strip()]
        vol = anchors.get("electrolyte_volume_uL")
        vol_note = f"，扣电设计注液量 {vol} μL" if vol not in (None, "") else ""
        if additives:
            if _is_additives_locked(scheme, anchors=anchors, target=str((doc or {}).get("target") or "")):
                add_clause = f"并已锁定引入添加剂 `{', '.join(additives)}`，最优剂量（0 vs x wt%）与化成匹配待第 5 节梯度实验确定"
            else:
                add_clause = (
                    f"作为第 5 节 `G0–G6` 无添加剂基础电解液（`E0`）；"
                    f"候选界面添加剂 `{', '.join(additives)}` 不预混入 `G0/G1` 基础电解液 `E0`，而纳入第 5 节 `G7` 与 5.3 正交/单因素筛选矩阵（0 vs 1/2 wt% 及化成匹配）以验证高压成膜与抑制产气效果"
                )
            return (
                f"- 归档提案正文中未提供带文献编号的电解液专题段落；依结构化设计契约（`design_scheme.json`），"
                f"当前基础电解液为 `{elec_name}`{vol_note}，{add_clause}（不补造未引用文献依据）。"
            )
        return (
            f"- 归档提案正文中未提供带文献编号的电解液专题段落；依结构化设计契约（`design_scheme.json`），"
            f"当前选定基础电解液为 `{elec_name}`{vol_note}，其高压界面成膜与长循环稳定性须由第 5 节实验验证（不补造未引用文献依据）。"
        )
    return None


def render_materials(doc):
    evidence = {str(e.get("passage_id")): e for e in doc.get("evidence") or []}
    text = doc.get("final_answer") or ""
    # (显示名, 段首标题关键词, 正文关键词, 通用选择准则)
    scheme = doc.get("scheme") or {}
    anchors = scheme.get("design_anchors") if isinstance(scheme.get("design_anchors"), dict) else {}
    extra_cathode_terms: tuple[str, ...] = ()
    if anchors.get("primary_formula"):
        pf_plain = _normalize_match_text(anchors["primary_formula"])
        extra_cathode_terms = (str(anchors["primary_formula"]), pf_plain)
    categories = [
        ("正极", ("正极", "主推方案", "主推荐", "主推候选", "Rank #1"), ("正极", "主推方案", "主推荐", "主推候选", "Rank #1", "名义化学式", "改性方案", "双位点", "共掺杂", "掺杂", "NCM", "单晶", "LRMO", "LNMO", "LFP", "LCO", "NCA", "富锂", "尖晶石", "层状", "Li1.2") + extra_cathode_terms, "正极选择需要同时核对容量、电压窗口和结构稳定性。单晶化、掺杂或包覆应分别列为候选路线，不能把不同材料的优势叠加为本方案已验证性能。"),
        ("负极", ("负极",), ("负极", "硅", "碳宿主", "宿主", "锂金属", "金属锂", "锂箔", "li foil", "li_metal", "石墨", "硬碳"), "负极选择应区分材料半电池表现与全电池表现。首效、膨胀和循环保持率必须连同对电极、倍率和质量归一口径解读；良好的半电池结果不直接证明全电池目标达成。"),
        ("电解液与添加剂", ("电解液",), ("电解液", "LiFSI", "LiPF6", "LiPF₆", "EC+DMC", "EC/DMC", "LHCE", "FEC", "VC", "DTD", "LiDFOB", "LIDFOB", "添加剂"), "电解液候选需要同时匹配正负极界面与工作电压。其他负极体系中的高压循环结果只能作为筛选线索，不能证明当前负极的成膜、产气和寿命表现。PINN 使用的训练电解液也不等于候选配方已被模拟。"),
        ("隔膜", ("隔膜",), ("隔膜", "separator", "Celgard"), "隔膜的选择依据应覆盖厚度、孔结构、润湿与机械完整性。来自其他体系的薄膜规格仅作对照，不能直接写入最终电芯；结构化契约没有声明的规格保持待确定。"),
        ("集流体", ("集流体",), ("集流体", "PET-Cu", "铜箔", "铝箔", "Al 箔", "Al箔", "Cu 箔", "Cu箔", "涂碳铝箔", "current collector"), "集流体需同时评估导电、附着、机械缓冲及质量贡献。轻质复合集流体在邻近体系的循环收益属于迁移假设，须用当前活性材料和最终极片几何开展对照验证。"),
    ]
    labels = {"si_base": "硅基/硅碳负极（si_base）", "high_concentration": "高浓度电解液（high_concentration）",
              "lhce": "局部高浓度电解液（lhce）", "li_metal": "锂金属负极（li_metal）",
              "carbonate_ec": "常规碳酸酯电解液（carbonate_ec）", "lrmo": "无钴富锂锰基正极（lrmo）",
              "lnmo": "高电压尖晶石正极（lnmo）"}
    keys = ("cathode", "anode", "electrolyte", "separator", "current_collector")
    # 仅转录有引用的分析段落；排除几何声明、性能表、主观评分及风险/缺口段落。
    paragraphs = _split_material_paragraphs(text)
    lines = ["**选型结论先行**：以下区分已选材料体系和待验证的具体路线。材料体系取自最终 JSON；形貌、制备和具体配方只有在契约明确声明时才算定案。",
             "", "| 组件 | 当前采用的材料体系 | 决策状态 |", "|---|---|---|"]
    for (name, _, _, _), key in zip(categories, keys):
        value = _resolve_component_value(scheme, key)
        lines.append(f"| {name} | {display(labels.get(str(value).lower(), value))} | {'体系已选定，具体规格见下文' if value else '尚未定案，下面给出候选与选择条件'} |")
    lines += ["", "文献数值仅对应原文研究对象，不能替代第 3 节的最终设计参数；邻近体系证据仅用于确定验证优先级。"]
    used_primaries: set[str] = set()
    used_excerpts: set[str] = set()
    ep_metrics = compute_cathode_pore_metrics(scheme)
    for index, (name, label_keys, terms, rationale) in enumerate(categories, 1):
        value = _resolve_component_value(scheme, keys[index - 1])
        lines += ["", f"### 2.{index} {name}：选型结论与取舍",
                  f"**当前决定**：{'采用 ' + display(labels.get(str(value).lower(), value)) + ' 材料体系。' if value else '最终契约尚未确定该组件，不将文献候选冒充最终选型。'}", rationale, ""]
        term_patterns = [_term_pattern(term) for term in terms]
        label_hits, term_hits = [], []
        for para in paragraphs:
            if para.lstrip().startswith(("|", "#")) or any(w in para for w in ("冻结几何", "推荐指数", "硬性几何")):
                continue
            ids = [pid for pid in re.findall(r"\[([^\]]+)\]", para)]
            resolved = [pid for pid in ids if pid in evidence]
            if not resolved:
                continue  # 无可定位引用的段落不作为选型依据
            unresolved = [pid for pid in ids if pid not in evidence]
            entry = (para, resolved, unresolved)
            if any(key in _leading_label(para) for key in label_keys):
                label_hits.append(entry)
            elif any(pat.search(para) for pat in term_patterns):
                term_hits.append(entry)
        # 段首标题命中优先；没有标题命中时才回退到正文关键词命中；组内优先非对照且与锚点一致的段落。
        raw_selected = label_hits or term_hits
        selected = sorted(
            raw_selected,
            key=lambda item: _material_sort_key(item, name, scheme),
        )
        emitted_entries = []
        if not selected:
            contract_line = _render_missing_material_contract(name, scheme, doc)
            if contract_line:
                lines.append(contract_line)
            else:
                lines.append("当前归档提案中没有可直接引用的该组件分析段落；不补造选型依据。")
        else:
            used_all = used_primaries | used_excerpts
            primary_entry = selected[0]
            if primary_entry[0] in used_all:
                for cand in selected[1:]:
                    if cand[0] not in used_all:
                        primary_entry = cand
                        break
            decision, resolved, unresolved = primary_entry
            orig_decision = decision
            if decision in used_all:
                extracted = _extract_relevant_clauses(decision, term_patterns, resolved)
                if extracted and extracted not in used_all:
                    decision = extracted
                    extracted_ids = [pid for pid in re.findall(r"\[([^\]]+)\]", decision)]
                    resolved = [pid for pid in extracted_ids if pid in evidence] or resolved
                    unresolved = [pid for pid in extracted_ids if pid not in evidence]

            if decision in used_all:
                lines.append("当前归档提案中没有该组件的独立非重复分析段落（相关交叉论述已在前序小节摘录）；不重复引述或补造选型依据。")
            else:
                primary, sep, alternative = decision.partition('\n- 或 ')
                primary_clean = _clean_excerpt_text(primary)
                alternative_clean = _clean_excerpt_text(alternative)
                is_ctrl, route_conf, temp_conf = _classify_excerpt_anchors(decision, scheme)
                if name == "正极" and anchors.get("primary_formula"):
                    pf_norm = _normalize_match_text(anchors["primary_formula"])
                    if pf_norm and pf_norm not in _normalize_match_text(primary_clean):
                        lines.append(
                            f"- **主推候选契约摘要（Rank #1，待实验验证）**：当前设计采用主推候选名义化学式 `{anchors['primary_formula']}`，"
                            f"其掺杂位点与可行性论证见下文归档摘录。"
                        )
                lines += ["**具体路线建议（来自已有提案，尚待验证）**：", primary_clean,
                          "**采用条件**：先核对该路线与最终材料体系及实际电极几何一致，再以相同工况验证；文献中的工艺数值不自动成为当前规格。"]
                if sep:
                    lines += ["**备选路线**：" + alternative_clean,
                              "**取舍原则**：优先比较同体系全电池证据；若备选仅有半电池或邻近体系证据，则先作为对照，不直接替换主路线。"]
                _append_excerpt_caveats(
                    lines,
                    decision,
                    scheme,
                    ep_metrics,
                    is_control=is_ctrl,
                    route_conflict=route_conf,
                    temp_conflict=temp_conf,
                )
                if unresolved:
                    lines.append("**引用核对提示**：" + "、".join(f"[{pid}]" for pid in unresolved) +
                                 " 未在本次归档证据中定位，涉及这些引用的表述不作为选型依据，需回到原文核实。")
                used_primaries.add(decision)
                emitted_entries.append((decision, resolved, unresolved))

                extra_count = 0
                for para, p_res, p_unres in selected:
                    if para == orig_decision or para == decision or para in used_primaries or para in used_excerpts:
                        continue
                    ex_ctrl, ex_route_conf, ex_temp_conf = _classify_excerpt_anchors(para, scheme)
                    heading = (
                        "**对照/备选路线摘录（非当前锁定主路线，仅作对照参考）**：\n"
                        if (ex_ctrl or ex_route_conf)
                        else "**归档论证摘录（候选/待验证）**：\n"
                    )
                    para_clean = _clean_excerpt_text(para)
                    lines.append(heading + "\n".join("> " + line for line in para_clean.splitlines()) + "\n")
                    _append_excerpt_caveats(
                        lines,
                        para,
                        scheme,
                        ep_metrics,
                        is_control=ex_ctrl,
                        route_conflict=ex_route_conf,
                        temp_conflict=ex_temp_conf,
                    )
                    used_excerpts.add(para)
                    emitted_entries.append((para, p_res, p_unres))
                    extra_count += 1
                    if extra_count >= 2:
                        break
        ids = {pid for _, res, _ in emitted_entries for pid in res if pid in evidence}
        sources = {source_identity(evidence[pid])[0] for pid in ids}
        lines.append(f"本小节引用 {len(ids)} 个段落、{len(sources)} 个归并来源。该数量只表示来源覆盖，不证明对当前体系的独立交叉验证。")
        lines.append("**取舍与验证边界**：采用与当前材料和测试条件一致的证据作为优先依据；跨体系证据保留为对照假设。未声明的配比、工况和尺寸不从上述摘录自动补入最终设计。")
    return "\n".join(lines)


def render_experiments(doc):
    lines = ["### 5.5 有来源的制备路线与迁移边界",
             "以下摘录来自归档证据原文，只是原论文的制备参考，不能直接作为本任务工艺指令。原文温度、配比等参数必须连同材料体系理解；引用中未给出的条件保持待确定。"]
    seen = set()
    for e in doc.get("evidence") or []:
        meta = e.get("metadata") or {}
        text = str(e.get("text") or "")
        if not (meta.get("label") == "材料制备" or re.search(r"preparation|fabrication|synthesis|制备|合成", text, re.I)):
            continue
        key = source_identity(e)[0]
        if key in seen:
            continue
        seen.add(key)
        lines += ["", f"**文献制备参考 [{e.get('passage_id')}]**（{display(source_identity(e)[2])}）",
                  "> " + text[:1200].replace("\n", "\n> ") + (" …（节选，完整段落见归档证据）" if len(text) > 1200 else ""),
                  "迁移验证：核对原文材料组成、设备与气氛，并先验证产物结构和批间一致性，再评估是否适用于最终选定电极。"]
        if len(seen) == 4:
            break
    if not seen:
        lines.append("当前证据未提供明确的制备段落，制备温度、时间与气氛均待确定。")
    lines += ["", "### 5.6 分阶段实验与判定路线",
              "**材料与界面筛选**：对最终选定材料开展组成、形貌和粒径表征，区分一次颗粒与团聚体；以未改性材料为对照逐项验证包覆、掺杂或碳宿主路线，避免同时改变多个因素而无法归因。采用同一测试窗口比较容量、首效与阻抗，缺失窗口先通过小样稳定性测试确定。",
              "**极片制备窗口**：以第 3、4 节的最终声明和实际生效厚度、孔隙率为验证点。通过涂层称重、截面测量和密度测定建立面载量—厚度—孔隙率关系；浆料固含量、粘结剂比例、干燥和辊压条件分别做梯度试验，以附着、开裂、电子导通及浸润共同筛选。未知配方不填默认数值。",
              "**电解液、隔膜与集流体对照**：保持活性材料和几何一致，分别比较候选与基准电解液、隔膜和集流体。记录润湿、界面阻抗、循环后形貌和产气，检验跨体系证据的可迁移性；不能把模型固定电解液的仿真结果当成新配方已通过。",
              "**装配与化成**：注液量通过孔隙体积估算、吸液称重和浸润实验确定；静置时间以浸润与阻抗趋稳为依据。化成倍率、温度、阶梯和预锂化剂量均待实验确定，试验设计不得擅自扩展已声明电压窗口。N/P 未提供时不把补齐该值列为仿真前置条件。",
              "**电化学与模型比对**：先在仿真实际倍率和温度下获取放电曲线，统一容量归一口径，比较平台、电压终点与可逆容量。再独立测试首效、循环、膨胀和温度依赖；模型仅预测单次放电，不预先给出这些实验的合格结果。",
              "**电芯能量确认**：分别称量正负极涂层、集流体、隔膜、电解液及包装，积分实际放电能量并除以同一口径的质量。将逐项质量结果与固定系数折算并列，解释差异后再判断性能目标，而不是把材料级比能量直接当电芯级指标。",
              "**反馈与停止条件**：若实测偏差来自材料参数，补充对应材料与条件下的证据后修订；D_s 必须有明确证据，不能为了提高输出而调整。若偏差主要来自非活性质量或制造因素，应补充质量与工艺模型。最终结论区分实验支持、模型支持和仍未验证的部分。"]
    return "\n\n".join(lines)


def render_history(task_dir):
    import json
    path = Path(task_dir) / "pinn_simulation_history.json"
    if not path.exists():
        return "### 仿真与优化历史\n未保存逐轮仿真历史，不能重建优化趋势。"
    history = json.loads(path.read_text(encoding="utf-8"))
    lines = ["### 逐轮候选比较", "| 仿真轮 | 状态 | 倍率 C | 比能量 Wh/kg | 较上一有效轮变化 |", "|---|---|---|---|---|"]
    previous = None
    for r in history.get("rounds", []):
        energy = r.get("energy_wh_kg")
        delta = energy - previous if isinstance(energy, (int, float)) and previous is not None else None
        lines.append(f"| {r.get('round')} | {display(r.get('status'))} | {display(r.get('c_rate'))} | {display(energy)} | {display(delta)} |")
        if isinstance(energy, (int, float)):
            previous = energy
    lines += ["", "### 各轮有效参数与结果解释"]
    for r in history.get("rounds", []):
        lines.append(f"\n**第 {r.get('round')} 轮**：{display(r.get('status'))}。")
        name = r.get("result_file")
        sidecar = Path(task_dir) / str(name or "")
        if not name or Path(name).name != name or not sidecar.is_file():
            lines.append("未保存本轮完整结果恢复件；不能推断有效几何或具体失败原因，状态以历史索引为准。")
            continue
        result = json.loads(sidecar.read_text(encoding="utf-8"))
        effective = (result.get("pinn") or {}).get("design") or {}
        for side, title in (("pe", "正极"), ("ne", "负极")):
            g = effective.get(side) or {}
            lines.append(f"- {title}实际厚度 {display(g['L']*1e6 if g.get('L') is not None else None)} μm；孔隙率 {display(g.get('porosity'))}；R_s {display(g['R_s']*1e6 if g.get('R_s') is not None else None)} μm。")
        lines.append(f"- 终止模式：{display(result.get('termination'))}；限制电极：{display(result.get('limiting_electrode'))}；平均电压：{display(result.get('average_voltage_V'))} V。")
        for warning in result.get("warnings") or []:
            lines.append(f"- 运行诊断：{display(warning)}")
    lines.append("\n修订记录中的理由是当时的优化假设；能量升降是本轮观察，二者不能直接等同为因果证明。多参数同时修改时，需单变量对照进一步辨别敏感性。有限候选最优不代表物理上限。")
    return "\n".join(lines)
