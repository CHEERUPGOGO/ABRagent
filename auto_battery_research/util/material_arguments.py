"""从 Stage 4 档案恢复选材论证，保留引用与候选边界，不重新调用模型。"""
import re

from .report_narrative import component_paragraph, describe, text, GROUPS

VERSION = 2
CATEGORIES = (
    ("cathode", "正极与改性路线", ("正极", "富锂", "NCM", "掺杂", "占位", "电荷", "半径", "畸变", "晶格", "氧释放", "氧化还原", "氧空位", "M–O", "redox", "支柱", "Na", "Zn", "Cu")),
    ("anode", "负极及容量匹配", ("负极", "锂金属", "锂库存", "硅碳", "SiOx", "Si/C", "碳宿主", "石墨", "预锂化", "膨胀", "SEI")),
    ("electrolyte", "电解液与界面匹配", ("电解液", "溶剂", "锂盐", "FEC", "VC", "LiPF", "LHCE", "CEI", "HF")),
    ("separator", "隔膜选择", ("隔膜", "Celgard", "separator")),
    ("current_collector", "集流体选择", ("集流体", "铜箔", "铝箔", "Al 箔", "Cu 箔", "PET-Cu")),
)
PARAMETER_SENTENCE = re.compile(
    r"(?:冻结几何|几何契约|硬性几何|面容量闭环|cathode\.|anode\.|condition\.|"
    r"厚度|孔隙率|面载|面容量|压实|\d[\d.\s/–—-]*(?:μm|µm|um)(?![A-Za-z])|"
    r"电压窗口|窗口.{0,8}\d|N/P|R_s|D_s|考核口径|冻结值|"
    r"化成|恒流|干燥|蒸干|预烧|主烧|煅烧|真空|涂布|缓冷|Li\s*过量|合成（借鉴|建议阈值|"
    r"E/C\s*(?:取|=)|注液量|(?:主选|对照组|本方案|本设计|本文取|目标|上限取).{0,45}\d|\d.{0,30}(?:主选|对照组))")
HEADING_TERMS = {
    "cathode": ("正极", "掺杂", "占位"), "anode": ("负极", "补锂"),
    "electrolyte": ("电解液", "溶剂", "添加剂"), "separator": ("隔膜",),
    "current_collector": ("集流体",),
}


def _citations(text, known):
    return [pid for pid in re.findall(r"\[([^\]\n]+)\]", text) if pid in known]


def _plain(text):
    return re.sub(r"^\s*(?:[-*+]\s+|\d+[.、]\s*)", "", text).strip()


def _units(text):
    """以段落或表行保存原论证，标题仅提供组件上下文，不进入正文。"""
    result, heading, paragraph = [], "", []
    table, table_heading = [], ""
    def flush():
        if paragraph:
            result.append({"heading": heading, "text": "\n".join(paragraph), "table": False})
            paragraph.clear()
    def flush_table():
        if table:
            result.append({"heading": table_heading, "rows": list(table), "table": True})
            table.clear()
    fence = False
    for line in text.splitlines() + [""]:
        if line.lstrip().startswith("```"):
            flush(); flush_table(); fence = not fence
            continue
        if fence:
            continue
        if line.startswith("#"):
            flush(); flush_table(); heading = line.lstrip("# ")
        elif line.lstrip().startswith("|"):
            flush()
            if not table:
                table_heading = heading
            table.append([v.strip() for v in re.split(r"(?<!\\)\|", line.strip().strip("|"))])
        elif not line.strip():
            flush(); flush_table()
        else:
            flush_table(); paragraph.append(line)
    return result


def _claims(block, known):
    # 逐句筛选，避免混合段落中的旧几何、制备条件进入当前选型论证。
    fragments, pending, depth = [], [], 0
    for char in block:
        pending.append(char)
        if char in "（(":
            depth += 1
        elif char in "）)":
            depth = max(0, depth - 1)
        if char == "\n" or (depth == 0 and char in "。！？；"):
            fragments.append("".join(pending).strip())
            pending.clear()
    if pending:
        fragments.append("".join(pending).strip())
    claims = []
    for fragment in fragments:
        fragment = _plain(fragment)
        if not fragment or PARAMETER_SENTENCE.search(fragment):
            continue
        if re.fullmatch(r"\*\*[^*]+\*\*[:：]?", fragment):
            continue  # 仅有段首标签而无论证内容，不单独生成空泛段落。
        if fragment.startswith(("---", "database/", "/home/", "**验证优先级")):
            continue
        if not re.search(r"[\u4e00-\u9fff]", fragment):
            continue
        # 未归档引用不能进入证据链；配位式等方括号不当作文献引用。
        requested = re.findall(r"\[([A-Za-z0-9_-]+)\]", fragment)
        if any(pid not in known and pid not in ("FSI", "TFSI", "PF6", "BF4") for pid in requested):
            continue
        ids = _citations(fragment, known)
        if not ids and re.search(r"(?:mol/L|[\d.]\s*M\s*Li|EC\s*[:：/]\s*(?:DMC|DEC|EMC))", fragment):
            continue  # 未带文献的旧配方不再作为当前电解液推荐进入论证。
        explicit = any(word in fragment for word in ("推断", "假设", "候选", "需验证", "待验证", "无直接", "无实测", "风险", "应优先", "因此", "而不是", "补偿", "占位", "失配", "缺口"))
        if ids or explicit:
            claims.append({"text": fragment, "citations": ids, "inference": not bool(ids) or any(w in fragment for w in ("推断", "假设", "迁移至", "跨体系"))})
    return claims


def build_arguments(doc):
    known = {str(e.get("passage_id")): e for e in doc.get("evidence") or [] if e.get("passage_id")}
    blocks = _units(doc.get("final_answer") or "")
    result = {"version": VERSION, "components": {}, "comparisons": []}
    used = set()
    for comp, title, terms in CATEGORIES:
        claims = []
        for block in blocks:
            if block["table"]:
                if comp == "cathode" and block["rows"] and any(word in " ".join(block["rows"][0]) for word in ("排序", "排名", "组合", "候选")):
                    header = block["rows"][0]
                    name_index = next((i for i, h in enumerate(header) if any(w in h for w in ("组合", "掺杂对"))
                        or re.fullmatch(r"候选(?:材料|体系|路线|方案)?", h)), None)
                    formula_index = next((i for i, h in enumerate(header) if "化学式" in h), None)
                    if name_index is not None:
                        for row in block["rows"][1:]:
                            if not row or all(re.fullmatch(r"[:\s-]*", c) for c in row) or name_index >= len(row):
                                continue
                            item = {"name": row[name_index], "formula": row[formula_index] if formula_index is not None and formula_index < len(row) else None}
                            if PARAMETER_SENTENCE.search(item["name"]):
                                continue
                            if item not in result["comparisons"]:
                                result["comparisons"].append(item)
                continue
            for claim in _claims(block["text"], known):
                # 逐句识别组件，混合段落中的集流体与电解液论证不再互相吞并。
                scores = [(sum(term.casefold() in claim["text"].casefold() for term in ts), c) for c, _, ts in CATEGORIES]
                best_score = max(s for s, _ in scores)
                if not best_score:
                    scores = [(sum(term.casefold() in block["text"].casefold() for term in ts), c) for c, _, ts in CATEGORIES]
                    best_score = max(s for s, _ in scores)
                candidates = [c for s, c in scores if s == best_score]
                label = re.match(r"(?:（\d+）)?\*\*(.+?)\*\*", claim["text"])
                context = label.group(1) if label else block["heading"]
                explicit_component = next((c for c, words in HEADING_TERMS.items()
                                           if label and any(w in context for w in words)), None)
                assigned = explicit_component or next((c for c in candidates if any(w in context for w in HEADING_TERMS[c])), candidates[0])
                if not best_score or assigned != comp:
                    continue
                if claim["text"] in used:
                    continue
                used.add(claim["text"])
                claim["priority"] = int(any(w in block["heading"] for w in ("依据", "机理", "可行性", "取舍", "风险")))
                claims.append(claim)
        # 优先保留有引用的机理链，同时保留取舍与候选推断，避免开头的泛论占满配额。
        evidence_claims = sorted((claim for claim in claims if claim["citations"]), key=lambda c: -c["priority"])
        inference_claims = sorted((claim for claim in claims if not claim["citations"]), key=lambda c: -c["priority"])
        selected = evidence_claims[:10] + inference_claims[:10]
        result["components"][comp] = {"title": title, "claims": [claim for claim in claims if claim in selected]}
    result["review_issues"] = [str(v) for v in (doc.get("reviewer_output") or {}).get("issues", [])]
    return result


def render_material_arguments(doc):
    from .report_readiness import unresolved_issues
    data = doc["ledger_view"]
    arguments = build_arguments(doc)
    parts = ["本节说明材料选择的文献依据、机理和取舍。当前采用的材料及规格以第 3 节为准；文献数值仅属于所引研究对象，跨体系迁移与无直接证据的判断均为待验证假设。"]
    issues = unresolved_issues(doc)
    if issues:
        parts.append("归档论证仍有未解决的审核意见（见本节末尾），以下摘录保留原有研究思路，不表示这些判断已通过审核。")
    for index, (comp, title, _) in enumerate(CATEGORIES, 1):
        material = (data.get("parameters", {}).get(comp + ".material") or {}).get("value")
        # 标题仅使用当前方案表的材料，不能把历史候选提升为推荐结论。
        material_name = text(material) if material is not None else "具体材料待确定"
        material_name = re.sub(r"([\\`*_[\]<>#])", r"\\\1", material_name)
        parts += [f"### 2.{index} {GROUPS[comp]}：{material_name}",
                  "> **选型结论。** " + component_paragraph(data, comp, material_only=True)]
        # 掺杂或包覆已有定案时优先呈现，不能由历史候选替代。
        current_details = [(k, v) for k, v in data.get("parameters", {}).items() if k.startswith(comp + ".")
            and v.get("value") is not None and any(w in k for w in ("dopant", "doping", "coating", "composition", "formula"))]
        if current_details:
            parts.append("> **已定改性规格。** " + "；".join(describe(k, v) for k, v in current_details) + "。")
        if comp == "cathode" and arguments["comparisons"]:
            parts.append("**备选路线。** 以下顺序表示归档研究优先级，未经实验验证；原文评分不作为定量性能依据，候选不能替代当前定案配方。")
            parts.append("\n".join(f"{i}. **{v['name']}**" + (f"：归档建议式为 {v['formula']}。" if v["formula"] else "。")
                for i, v in enumerate(arguments["comparisons"][:12], 1)))
        claims = arguments["components"][comp]["claims"]
        if claims:
            buckets = {}
            seen = set()
            for claim in claims:
                content = claim["text"]
                normalized = re.sub(r"[\s*；;。]", "", content)
                if normalized in seen:
                    continue
                seen.add(normalized)
                category = ("推断与验证边界" if claim["inference"] else
                            "取舍与适用边界" if any(w in content for w in ("风险", "不足", "缺少", "未标", "不能", "相比", "对照")) else
                            "文献依据与机理")
                buckets.setdefault(category, []).append(content)
            for category in ("文献依据与机理", "取舍与适用边界", "推断与验证边界"):
                if category in buckets:
                    entries = buckets[category]
                    # 成组呈现短句；较长原句独立保留，避免破坏引用与适用条件。
                    paragraph, paragraphs = "", []
                    for entry in entries:
                        if paragraph and len(paragraph) + len(entry) > 260:
                            paragraphs.append(paragraph); paragraph = ""
                        paragraph += entry.rstrip("；;") + ("。" if entry.endswith(("；", ";")) else "")
                    if paragraph:
                        paragraphs.append(paragraph)
                    # 列表缩进由 Markdown 语义表达，避免四空格被预览器当作代码块。
                    parts.append(f"- **{category}。**\n\n" + "\n\n".join(
                        "  " + p.replace("\n", "\n  ") for p in paragraphs))
        else:
            parts.append("归档中没有可定位且可与当前规格分开的该组件选型论证，不以通用优点补写文献结论。具体工艺与对照安排见实验章节。")
    if issues:
        parts += ["### 2.6 风险与待验证事项", "以下意见尚未解决，涉及的配方、排名或机理不能视为已审核确认："]
        parts.append("\n".join(f"{i}. {issue}" for i, issue in enumerate(issues, 1)))
    return "\n\n".join(parts)
