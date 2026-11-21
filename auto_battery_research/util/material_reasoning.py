"""只读组织归档选材逻辑；不增加科学结论、配方或审核通过声明。"""
import re


STEPS = (
    ("研究对象与筛选动机", ("req_", "动机", "策略", "瓶颈", "需求")),
    ("文献如何支持材料选择", ("为什么选", "选择依据", "核心机理", "第一顺位", "强键", "支柱效应")),
    ("备选比较与取舍", ("Cu（", "Zn（", "证据强度", "局限", "排行", "排序", "探索", "边际收益")),
    ("适用条件与验证路径", ("待验证", "预测", "归因", "第一轮", "第二轮", "验证", "可行性", "必须声明", "缺口")),
)


def clean_claim(value):
    """移除内部需求ID标签；原句的科学内容、限定和引用完整保留。"""
    return re.sub(r"^\*\*req_[A-Za-z0-9_]+（([^\n]+?)）\*\*[:：]\s*",
                  lambda m: "**" + m[1] + "：** ", value.strip())


def reasoning_group(claim):
    content = claim["text"]
    # 明确选材理由先于一般机制/风险；不以引用存在推定直接实证。
    if any(w in content for w in ("为什么选", "选择依据", "核心机理", "路径 1", "路径 2")):
        return STEPS[1][0]
    if any(w in content for w in ("Cu（", "Zn（", "证据强度", "局限", "排行", "排序", "边际收益")):
        return STEPS[2][0]
    for title, words in STEPS:
        if any(w in content for w in words):
            return title
    return STEPS[1][0] if claim.get("citations") else STEPS[3][0]


def render_claim_chain(claims):
    """按决策环节归组原论证；每条证据只展示一次，不反复生成通用小标题。"""
    groups, seen = {}, set()
    for claim in claims:
        content = clean_claim(claim["text"])
        signature = re.sub(r"\s|\*", "", content)
        if not content or signature in seen:
            continue
        seen.add(signature)
        groups.setdefault(reasoning_group(claim), []).append(content)
    if not groups:
        return ""
    parts = []
    active = [(title, groups[title]) for title, _ in STEPS if groups.get(title)]
    connectors = {
        "研究对象与筛选动机": "先明确研究对象和需要解决的问题，再确定材料应承担的作用。",
        "文献如何支持材料选择": "接着核对所提出的材料作用在什么体系、位点和工况下得到支持；以下原文说明证据如何影响选择。",
        "备选比较与取舍": "在上述材料依据之外，再比较备选的证据成熟度与局限，区分优先验证路线和探索对照。",
        "适用条件与验证路径": "最后把材料选择转成可检验假设，说明哪些性能仍须在本课题工况下测量。",
    }
    for n, (title, entries) in enumerate(active, 1):
        # 单个组件只有一条旁证时，不重复一套通用阶段标题。
        if len(active) > 1:
            parts.append(f"**{n}. {title}。** " + connectors[title])
        parts.extend(entries)
    return "\n\n".join(parts)


def archived_decision_chain(doc):
    """提取正文显式决策链，省去工艺/几何枝节；不自行推导材料排名。"""
    from .material_arguments import _units, PARAMETER_SENTENCE
    from .report_readiness import unresolved_issues
    known = {str(e.get("passage_id")) for e in doc.get("evidence", []) if e.get("passage_id")}
    for block in _units(doc.get("final_answer") or ""):
        if block["table"] or not any(t in block["heading"] for t in ("论证链", "决策链", "筛选逻辑")):
            continue
        content = block["text"]
        if content.count("→") < 3:
            continue
        requested = set(re.findall(r"\[([\w:-]+)\]", content))
        if requested - known - {"FSI", "TFSI", "PF6", "BF4"}:
            continue
        depth, start, clauses = 0, 0, []
        for index, char in enumerate(content):
            if char in "（(":
                depth += 1
            elif char in "）)":
                depth = max(0, depth - 1)
            elif char == "→" and depth == 0:
                clauses.append(content[start:index].strip()); start = index + 1
        clauses.append(content[start:].strip())
        steps = [c for c in clauses if c and not PARAMETER_SENTENCE.search(c)
                 and not re.match(r"(?:合成|扣电验证|电芯组装|实验步骤|掺量约束)", c)]
        if len(steps) < 3:
            continue
        parts = ["> **归档中的选材决策链。** 以下为设计原文的推理路径，展示材料选择如何形成；其中的最优、优先级及跨体系推断均须结合审核意见理解，不能视为已验证结果。"]
        for index, step in enumerate(steps, 1):
            parts.append(f"{index}. " + clean_claim(step))
        issues = unresolved_issues(doc)
        involved = set(re.findall(r"\[([\w:-]+)\]", " ".join(steps)))
        if any(involved & set(re.findall(r"\[([\w:-]+)\]", issue)) for issue in issues):
            parts.append("**该链条仍有证据条件或体系适用性的审核争议。** 引用可定位不等于论证成立，具体误读与待核对事项见第6节附录；本段保留的是归档思路。")
        return "\n\n".join(parts)
    return ""


def render_structured_chain(arguments, doc):
    from .research_support import render_field, field_citations
    claims = []
    for argument in arguments:
        content = render_field(argument, "text", doc)
        ids = field_citations(argument, "text")
        absent = [p for p in ids if f"[{p}]" not in content]
        if absent:
            content += " " + " ".join(f"[{p}]" for p in absent)
        # 验证层级随原段落保留，标题按已有论证主题组织。
        if argument.get("basis") == "inference":
            content += "（机制推断，待验证）"
        claims.append({"text": content, "citations": ids})
    return render_claim_chain(claims)
