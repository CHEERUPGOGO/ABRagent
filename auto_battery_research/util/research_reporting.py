"""课题驱动的六节报告视图；所有主方案值通过方案表绑定渲染。"""
import re

from .research_contract import TOKEN
from .report_narrative import amount, component_paragraph, describe, numbered_detail

STATUS = {"answered": "已回答", "partial": "部分回答", "gap": "明确缺口", "omitted": "遗漏"}
VERIFIED = {"unverified": "待验证", "literature": "文献支持（原研究对象）",
            "calculation": "计算支持", "experiment": "实验支持", "not_applicable": "无需性能验证"}


def cell(value):
    return str("—" if value is None or value == "" else value).replace("|", "\\|").replace("\n", " ")


def substitute(value, doc):
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    return TOKEN.sub(lambda m: amount(m[1], params[m[1]]) if m[1] in params else "待确定", str(value or ""))


def selected_summary(doc):
    data = doc.get("ledger_view") or {"parameters": {}}
    if doc.get("requirements", {}).get("scheme_required") is False:
        return "本课题以研究分析为主，具体结论和适用条件见逐项回应；不推定完整电芯主方案。"
    research = doc.get("research") or {}
    selected = next((c for c in research.get("candidates", []) if c["id"] == research.get("selected_candidate_id")), None)
    values = data["parameters"]
    from .parameter_presentation import parameter_view, combined_rows, parameter_name, value_text
    rows = parameter_view(data)
    formula = next((r for r in rows if r["canonical_key"].endswith("nominal_formula")), None)
    centers = [r for r in rows if r.get("value") is not None and r["canonical_key"].endswith(".center")]
    windows = combined_rows([r for r in rows if r.get("value") is not None
                             and any(w in r["canonical_key"] for w in ("doping.", "dopant", "coating"))])
    changes = [r for r in rows if r.get("value") is not None and r["canonical_key"].split(".")[-1] in ("coating", "dopant")]
    details = ([formula] if formula else []) + (centers if centers else [r for r in windows if "range_values" in r]) + changes
    parts = (["探索性首选：" + selected["name"] + "（尚未证明性能最优）。"] if selected else [])
    parts += [component_paragraph(data, g, material_only=True) for g in ("cathode", "anode", "electrolyte")
              if values.get(g + ".material", {}).get("value") is not None]
    if details:
        brief = []
        for row in details[:3]:
            key = row["canonical_key"]
            if row.get("range_values"):
                lo, hi = row["range_values"]
                value = f"{lo:g}–{hi:g}（计量口径见第3节）"
            else:
                value = value_text(key, row)
            brief.append(parameter_name(key, row) + "：" + value)
        parts.append("；".join(brief) + "。完整规格与条件见第3节。")
    return "".join(parts) or "本课题尚未确定电芯主方案；按实际分析问题呈现结论，不补造配方。"


def render_summary(doc):
    requirements, research = doc["requirements"], doc.get("research") or {}
    answers = {a["requirement_id"]: a for a in research.get("answers", [])}
    lines = ["**（1）当前推荐与研究结论。** " + selected_summary(doc),
             "回应状态表示课题内容是否得到回答；验证状态表示证据层级，两者分别判断。"]
    if requirements.get("status") != "parsed":
        lines.append("**需求解析尚待复核。** 以下保留用户原始分句，不能据此宣称课题已完整理解。")
    lines += ["\n**（2）课题要求覆盖。**\n", "| 序号 | 课题要求 | 当前回答 | 回应 / 验证状态 | 详细位置 |", "|---|---|---|---|---|"]
    for index, item in enumerate((i for i in requirements["items"] if i["kind"] != "background"), 1):
        answer = answers.get(item["id"], {})
        response = substitute(answer.get("response", "尚未回答"), doc)
        if answer.get("status") in ("gap", "partial"):
            response += "；" + substitute(answer.get("reason", ""), doc)
        state = STATUS.get(answer.get("status"), "遗漏") + " / " + VERIFIED.get(answer.get("verification"), "待验证")
        lines.append(f"| {index} | {cell(item['description'])} | {cell(response)} | {state} | 第2节·课题回应{index} |")
    lines.append("\n**（3）下一步。** 按第5节对应课题目标开展验证；未确定项先按第2节列出的获取路径补齐。")
    return "\n\n".join(lines[:2]) + "\n\n" + "\n".join(lines[2:])


def render_research(doc):
    research = doc.get("research") or {}
    answers = {a["requirement_id"]: a for a in research.get("answers", [])}
    parts = ["### 2.1 课题逐项回应", "文献结论仅适用于所引研究对象；工程选择与探索性排序仍需验证。"]
    for n, item in enumerate((i for i in doc["requirements"]["items"] if i["kind"] != "background"), 1):
        answer = answers.get(item["id"], {})
        parts += [f"#### 2.1.{n} 课题回应{n}：{item['description']}"]
        details = []
        if item.get("conditions"):
            details.append(("用户指定条件", "；".join(item["conditions"])))
        if answer.get("citations"):
            details.append(("文献依据", " ".join(f"[{c}]" for c in answer["citations"])))
        if answer.get("reason"):
            details.append(("边界与原因", substitute(answer["reason"], doc)))
        if answer.get("next_step"):
            details.append(("获取或验证方法", substitute(answer["next_step"], doc)))
        parts.append(numbered_detail(1, "当前回答", substitute(answer.get("response", "尚未形成回答。"), doc), details))
    candidates = research.get("candidates", [])
    if candidates:
        parts += ["### 2.2 候选比较与取舍", "排序表示探索优先级，不能视为实测性能排名。仅标为当前首选的项进入第3节主方案。",
                  "| 顺序 / 身份 | 候选 | 依据与取舍 | 风险与边界 |",
                  "|---|---|---|---|"]
        for c in candidates:
            identity = "当前探索性首选" if c["id"] == research.get("selected_candidate_id") else {"control": "对照", "literature": "文献旁证"}.get(c["scope"], "候选")
            why = substitute(c.get("rationale") or c.get("summary"), doc)
            why += " " + " ".join(f"[{p}]" for p in c.get("citations", []))
            parts.append(f"| {cell(c.get('rank'))} / {identity} | {cell(c['name'])} | {cell(why)} | {cell(substitute(c.get('risks'), doc))} |")
    if research.get("arguments"):
        parts.append("### 2.3 论证链与适用条件")
        for index, argument in enumerate(research["arguments"], 1):
            basis = "文献依据" if argument["basis"] == "literature" else "机制推断，待验证"
            details = [("归档引用", " ".join(f"[{p}]" for p in argument.get("citations", [])))] if argument.get("citations") else []
            parts.append(numbered_detail(index, f"{argument.get('title') or '研究论证'}（{basis}）", substitute(argument["text"], doc), details))
    else:
        parts.append(render_archived_arguments(doc))
    selected = next((c for c in candidates if c["id"] == research.get("selected_candidate_id")), None)
    parts.append("### 2.4 主方案选择与理由")
    if selected:
        parts.append("> **当前探索性首选。** " + selected["name"] + "。完整组成、计量口径及固定条件集中见第3节。")
        rationale = substitute(selected.get("rationale"), doc)
        parts.append(numbered_detail(1, "为什么优先选择", rationale or "归档未提供选择理由，不能仅凭候选顺序判断优劣。",
            [("归档引用", " ".join(f"[{p}]" for p in selected["citations"]))] if selected.get("citations") else []))
        parts.append(numbered_detail(2, "取舍与验证边界", substitute(selected.get("risks"), doc)
            or "归档未提供该首选的风险与适用边界，仍需补充同条件比较。"))
        parts.append("以上是归档的选择理由，引用只支持原研究对象；探索性首选不等于已证明最优。候选间的具体差异见第2.2节。"
                     if len(candidates) > 1 else "以上是归档的选择理由，引用只支持原研究对象；尚无其他候选的同条件比较，不能宣称最优。")
    elif doc["requirements"].get("scheme_required") is False:
        parts.append("本课题未确定材料主方案，按研究分析结论交付；归档中的材料举例不视为选型。")
    else:
        parts.append("当前材料组成以第3节方案表为准，但尚无通过结构化校验的首选及其选择理由。第2.3节的归档论证仅供追溯，不能据此恢复候选排名或更换当前材料。")
    parts += ["### 2.5 关键限制", "完整审核记录见第6节附录；尚未解决的问题不能被表述为已验证结论。"]
    # 表格行必须连续，段落之间保持空行。
    return re.sub(r"(\|[^\n]*\|)\n\n(?=\|)", r"\1\n", "\n\n".join(parts))


def render_archived_arguments(doc):
    """结构化摘要缺失时只读摘录原论证，不修复状态或重建主方案。"""
    from .material_arguments import build_arguments, CATEGORIES
    arguments = build_arguments(doc, rationale_only=True)
    parts = ["### 2.3 选材理由与证据边界（归档摘录）",
             "结构化论证尚未形成有效记录，以下从设计原文保留可定位的论证。引用可定位不等于结论已验证；原文中的主选、排序和推荐均为归档观点，不能替代第3节当前方案。未解决的审核意见仍见第6节附录。"]
    for comp, title, _ in CATEGORIES:
        claims = arguments["components"][comp]["claims"]
        if not claims:
            continue
        parts.append("#### " + title)
        for index, claim in enumerate(claims, 1):
            basis = "推断与待验证判断" if claim["inference"] else "原文论证（引用可定位）"
            parts.append(numbered_detail(index, basis, claim["text"]))
    if not any(v["claims"] for v in arguments["components"].values()):
        parts.append("归档中没有可定位且可与当前规格分开的选材论证。需补充材料与研究目标的关联、比较对象、文献依据和适用条件；不以通用优点补造理由。")
    return "\n\n".join(parts)


def render_review_appendix(doc):
    from .report_readiness import unresolved_issues
    issues = unresolved_issues(doc)
    diagnostics = (doc.get("requirements") or {}).get("diagnostics", []) + (doc.get("research") or {}).get("diagnostics", [])
    items = list(dict.fromkeys(issues + diagnostics))
    body = "### 完整审核与解析记录\n\n" + ("\n\n".join(numbered_detail(n, "待复核事项", s) for n, s in enumerate(items, 1)) if items else "没有尚未解决的审核意见；这不等于性能已通过实验验证。")
    if doc.get("experiment_extensions"):
        body += "\n\n### 后续实验与采购补充\n\n" + "\n\n".join(numbered_detail(n, "扩展安排", s) for n, s in enumerate(doc["experiment_extensions"], 1))
    if doc.get("ledger_view"):
        from .parameter_presentation import render_parameter_appendix
        body += "\n\n" + render_parameter_appendix(doc["ledger_view"])
    from .adaptive_retrieval import render_audit
    audit = render_audit(doc)
    if audit:
        body += "\n\n" + audit
    return body
