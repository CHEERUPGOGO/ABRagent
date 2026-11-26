"""课题驱动的六节报告视图；所有主方案值通过方案表绑定渲染。"""
import re

from .research_contract import TOKEN
from .report_narrative import amount, component_paragraph, component_identity_text, describe, numbered_detail
from .research_support import render_field, field_citations, selected_candidate, is_summary, supported, field_quotes
from .requirement_structure import ordered_items, group_title, item_bindings

STATUS = {"answered": "已回答", "partial": "部分回答", "gap": "明确缺口", "omitted": "遗漏"}
VERIFIED = {"unverified": "待验证", "literature": "文献支持（原研究对象）",
            "calculation": "计算支持", "experiment": "实验支持", "not_applicable": "无需性能验证"}


def cell(value):
    return str("—" if value is None or value == "" else value).replace("|", "\\|").replace("\n", " ")


def substitute(value, doc):
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    return TOKEN.sub(lambda m: amount(m[1], params[m[1]]) if m[1] in params else "待确定", str(value or ""))


def fixed_condition_response(item, doc):
    """固定条件按方案表验收；旧研究摘要失败不能把已锁定的用户条件显示成未知。"""
    from .research_contract import field_equal
    if doc.get("requirements", {}).get("hierarchy_version") != 1 or item["kind"] != "constraint":
        return None
    fixed = item_bindings(item)
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    if not fixed or any(not params.get(b["path"], {}).get("locked")
                        or params[b["path"]].get("basis") != "user_requirement"
                        or not field_equal(b["path"], params[b["path"]].get("value"), b["value"])
                        or params[b["path"]].get("unit", "") != b.get("unit", "") for b in fixed):
        return None
    return "主方案按用户指定固定：" + item["description"]


def selected_summary(doc):
    data = doc.get("ledger_view") or {"parameters": {}}
    if doc.get("requirements", {}).get("scheme_required") is False:
        return "本课题以研究分析为主，研究结论及适用条件见第2节。"
    selected = selected_candidate(doc)
    from .parameter_presentation import parameter_view, combined_rows, parameter_name, value_text
    rows = parameter_view(data)
    centers = [r for r in rows if r.get("value") is not None and r["canonical_key"].endswith(".center")]
    windows = combined_rows([r for r in rows if r.get("value") is not None
                             and any(w in r["canonical_key"] for w in ("doping.", "dopant", "coating"))])
    changes = [r for r in rows if r.get("value") is not None and r["canonical_key"].split(".")[-1] in ("coating", "dopant")]
    # 完整名义组成已由组件身份句显示，摘要不再重复同一公式。
    details = (centers if centers else [r for r in windows if "range_values" in r]) + changes
    parts = (["探索性首选：" + selected["name"] + "（尚未证明性能最优）。"] if selected else [])
    parts += [component_identity_text(data, g) for g in ("cathode", "anode", "electrolyte")]
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
    return ("".join(parts) + ("完整规格与条件见第3节。" if any(parts) and not details else "")) or "本课题尚未确定电芯主方案；按实际分析问题呈现结论，不补造配方。"


def render_summary(doc):
    """摘要只保留结论、证据边界与下一步，完整要求覆盖另存附录。"""
    if doc.get("_report_record"):
        from .report_current_view import summary
        return summary(doc)
    if doc.get("writer_contract_version") == 3:
        return ("**当前方案。** " + selected_summary(doc) + "\n\n"
                "**研究依据与边界。** 第2节直接呈现当前科研正文，保留原顺序、引用与适用条件；待审字段及一致性意见随相关段落保留。"
                "\n\n**下一步。** 按第5节开展实验验证；程序定位与一致性通过不代表本课题性能已经验证。")
    from .research_narrative import display_narrative, content_units
    from .research_original import view as original_view
    requirements = doc["requirements"]
    original = original_view(doc)
    narrative = None if original else display_narrative(doc)
    blocked = {rid for _, p in content_units(narrative)
               if narrative["paragraph_reviews"][p["id"]] for rid in p["requirement_ids"]} if narrative else set()
    answers = {a["requirement_id"]: a for a in (doc.get("research") or {}).get("answers", [])}
    conclusion = selected_summary(doc)
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    # 只摘取一条完整且简短的有效回答，不截断句子或去掉适用条件，不从失败摘要提炼结论。
    for item in ordered_items(requirements):
        answer = answers.get(item["id"], {})
        if item["kind"] in ("constraint", "background") or item["id"] in blocked or not answer.get("response"):
            continue
        if not supported(answer, "response", doc, params):
            continue
        text = render_field(answer, "response", doc)
        if len(text) <= 240:
            conclusion += " " + text
            break
    boundary = "文献结论仅适用于原研究对象和已注明条件；候选方案、跨体系推断与待验证假设不能作为本课题已完成的性能验证。"
    if original:
        boundary += "第2节沿用原设计审核状态，参数更新后的复核项随原文呈现。"
    elif narrative and narrative["status"] != "approved":
        pending = sum(bool(r) for r in narrative["paragraph_reviews"].values())
        location = "第2节及第3.4节" if narrative.get("calculation_notes") else "第2节"
        boundary += (f"整合论证有{pending}段待复核，具体问题已在{location}标注。" if pending
                     else f"整合论证的整体审核仍待复核，具体说明见{location}。")
    elif doc.get("research_narrative") and narrative is None:
        boundary += "整合稿尚无与当前输入匹配的有效审核。"
    if requirements.get("status") != "parsed":
        boundary += "需求解析仍待复核。"
    return ("**（1）主要结论。** " + conclusion + "\n\n**（2）证据边界。** " + boundary
            + "\n\n**（3）下一步。** 优先核对待复核事项，按第5节开展相应验证。")


def render_coverage(doc):
    """完整覆盖表属于附录，保留原回答、来源边界及章节位置。"""
    if doc.get("_report_record"):
        from .report_current_view import coverage
        return coverage(doc)
    requirements, research = doc["requirements"], doc.get("research") or {}
    answers = {a["requirement_id"]: a for a in research.get("answers", [])}
    lines = ["### 课题要求覆盖表", "回应状态表示课题内容是否得到回答；验证状态表示证据层级，两者分别判断。"]
    if requirements.get("task"):
        lines.append("**总任务：** " + requirements["task"]["description"].rstrip("。") + "。以下仅列可独立检查的要求。")
    if requirements.get("status") != "parsed":
        lines.append("**需求解析尚待复核。** 以下保留用户原始分句，不能据此宣称课题已完整理解。")
    grouped = bool(requirements.get("groups"))
    if grouped:
        lines += ["上层目标用于组织内容，以下检查点分别验收；固定条件检查执行一致性。",
                  "| 序号 | 所属目标 / 类别 | 课题要求 | 当前回答 | 回应 / 验证状态 | 详细位置 |", "|---|---|---|---|---|---|"]
    else:
        lines += ["| 序号 | 课题要求 | 当前回答 | 回应 / 验证状态 | 详细位置 |", "|---|---|---|---|---|"]
    from .research_narrative import requirement_locations
    locations = requirement_locations(doc)
    for index, item in enumerate(ordered_items(requirements), 1):
        answer = answers.get(item["id"], {})
        if doc.get("writer_contract_version") == 3:
            from .stage4_reporting import reference
            response = reference(answer, "response") if answer else "尚未回答"
        else:
            response = render_field(answer, "response", doc) if answer else "尚未回答"
        if answer.get("status") in ("gap", "partial"):
            response += "；" + render_field(answer, "reason", doc)
        state = STATUS.get(answer.get("status"), "遗漏") + " / " + VERIFIED.get(answer.get("verification"), "待验证")
        fixed_response = fixed_condition_response(item, doc)
        if fixed_response:
            response = fixed_response
            state = "方案已固定 / 条件一致"
        category = cell(group_title(requirements, item)) + " | " if grouped else ""
        location = locations.get(item["id"], f"第6节·课题回应{index}")
        lines.append(f"| {index} | {category}{cell(item['description'])} | {cell(response)} | {state} | {cell(location)} |")
    return "\n\n".join(lines[:2]) + "\n\n" + "\n".join(lines[2:])


def _current_choice_record(doc):
    """只读关联当前名义组成的选材记录，不恢复首选标识或候选排名。"""
    selected = selected_candidate(doc)
    if selected:
        return selected
    from .research_records import validate_record
    from .research_contract import field_equal
    from .parameter_presentation import normalized
    from .material_ids import source_matches
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    research = doc.get("research") or {}
    formulas = {key: row["value"] for key, row in params.items()
                if key.endswith(".nominal_formula") and row.get("value") is not None}
    matched = []
    for candidate in research.get("candidates", []):
        if candidate.get("scope") != "candidate":
            continue
        refs = candidate.get("parameter_refs", [])
        summary_quotes = field_quotes(candidate, "summary")
        name_pattern = r"(?<!\w)" + re.escape(normalized(candidate.get("name"))) + r"(?!\w)"
        # C31 等样品编号可通过已声明的公式引用及其逐字来源唯一关联。
        # 只用于展示选材理由，不恢复首选或排名；来源仍走 validate_record。
        if not any(key in refs and (
                normalized(candidate.get("name")) == normalized(value)
                or key in TOKEN.findall(candidate.get("summary", ""))
                   and any(source_matches(key, value, q) for q in summary_quotes)
                   and any(re.search(name_pattern, normalized(q)) for q in summary_quotes))
                   for key, value in formulas.items()):
            continue
        bound = research.get("bound_fields") or {}
        if any(key in bound and (not field_equal(key, bound[key].get("value"), params.get(key, {}).get("value"))
               or bound[key].get("unit", "") != params.get(key, {}).get("unit", "")) for key in refs):
            continue
        try:
            validate_record(candidate, "candidates", doc, params)
        except (ValueError, TypeError, KeyError):
            continue
        matched.append(candidate)
    return matched[0] if len(matched) == 1 else None


def _choice_field(row, field, doc):
    """选材正文区分归档依据与通过审核的表达，核验原因紧随具体依据。"""
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    if not is_summary(row) or supported(row, field, doc, params):
        value = render_field(row, field, doc)
        cites = [c for c in field_citations(row, field) if f"[{c}]" not in value]
        return value + (" " + " ".join(f"[{c}]" for c in cites) if cites else "")
    quotes = list(dict.fromkeys(field_quotes(row, field)))
    reason = ((row.get("support_review") or {}).get("fields", {}).get(field) or {}).get("reason")
    if not reason:
        reason = "该表述尚未取得与当前方案和文献对应的有效审核。"
    return "以下保留原设计的选材依据，其科学适用性仍待核验：\n\n" + "\n\n".join(
        "> " + q.replace("\n", "\n> ") for q in quotes) + "\n\n**待核验事项：** " + reason


def _render_current_choice(doc):
    if doc["requirements"].get("scheme_required") is False:
        return "本课题未确定材料主方案，按研究分析结论交付；归档中的材料举例不视为选型。"
    data = doc.get("ledger_view") or {"parameters": {}}
    composition = "".join(component_identity_text(data, g, emphasize=True) for g in ("cathode", "anode", "electrolyte"))
    if not composition:
        return "当前尚未确定材料组成，需先明确研究方案，再说明选材依据与验证边界。"
    parts = ["> **当前方案。** " + composition + "完整配方、占位、掺量及适用条件见第3节。"]
    choice = _current_choice_record(doc)
    if choice:
        why = _choice_field(choice, "rationale", doc) if choice.get("rationale") else "归档未提供该方案的选择理由，需补充文献依据及其适用条件。"
        parts.append(numbered_detail(1, "选材考虑", why))
        risk = _choice_field(choice, "risks", doc) if choice.get("risks") else "归档未提供该方案的具体风险，仍需补充验证。"
        parts.append(numbered_detail(2, "取舍与验证边界", risk))
    else:
        parts.append(numbered_detail(1, "选材考虑", "尚未归档可明确对应当前组成的选择理由，需补充选材依据和适用条件；已有文献分析见第2.3节。"))
    parts.append("当前组成是用于后续验证的研究方案，尚不能据此认定性能最优。" +
                 ("候选比较见第2.2节，优劣仍需同条件对照验证。" if len((doc.get("research") or {}).get("candidates", [])) > 1
                  else "尚无其他候选的同条件比较，不能宣称最优。") + "本课题目标的测试方法与判据见第5节。")
    return "\n\n".join(parts)


def render_requirement_details(doc, heading="### 2.1 课题逐项回应"):
    if doc.get("writer_contract_version") == 3:
        from .stage4_reporting import details
        return details(doc, heading)
    research = doc.get("research") or {}
    answers = {a["requirement_id"]: a for a in research.get("answers", [])}
    parts = [heading, "文献结论仅适用于所引研究对象；工程选择与探索性排序仍需验证。"]
    previous_group = None
    for n, item in enumerate(ordered_items(doc["requirements"]), 1):
        category = group_title(doc["requirements"], item)
        if category and category != previous_group:
            parts.append("#### " + category)
        previous_group = category
        answer = answers.get(item["id"], {})
        details = []
        if item.get("conditions"):
            details.append(("用户指定条件", "；".join(item["conditions"])))
        if field_citations(answer, "response"):
            details.append(("文献依据", " ".join(f"[{c}]" for c in field_citations(answer, "response"))))
        if answer.get("reason"):
            details.append(("边界与原因", render_field(answer, "reason", doc)))
        if answer.get("next_step"):
            details.append(("获取或验证方法", render_field(answer, "next_step", doc)))
        status = STATUS.get(answer.get("status"), "遗漏")
        fixed_response = fixed_condition_response(item, doc)
        if fixed_response:
            status = "方案已固定"
            details = [d for d in details if d[0] == "用户指定条件"]
            details.append(("执行边界", "当前方案值与用户原文一致并已锁定；这表示方案声明符合条件，实验实施仍按第5节执行。"))
        body = "**课题要求：** " + item["description"] + "\n\n" + (fixed_response or (render_field(answer, "response", doc) if answer else "尚未形成回答。"))
        parts.append(numbered_detail(n, f"课题回应{n}（{status}）", body, details))
    return "\n\n".join(parts)


def render_candidate_comparison(doc, heading="### 2.2 候选比较与取舍"):
    if doc.get("writer_contract_version") == 3:
        return heading + "\n\n候选身份、比较、取舍及风险按原顺序保留于第2节；逐字段来源和支持状态见本附录的研究关联档案。角色不自动授予首选或最优排序。"
    research = doc.get("research") or {}
    candidates = research.get("candidates", [])
    parts = [heading]
    if candidates:
        parts += ["排序表示探索优先级，不能视为实测性能排名。未核验的摘要显示原文依据，不能替代第3节主方案。",
                  "| 顺序 / 身份 | 候选 | 依据与取舍 | 风险与边界 |",
                  "|---|---|---|---|"]
        for c in candidates:
            identity = "当前探索性首选" if selected_candidate(doc) == c else {"control": "对照", "literature": "文献旁证"}.get(c["scope"], "候选")
            field = "rationale" if c.get("rationale") else "summary"
            why = render_field(c, field, doc)
            why += " " + " ".join(f"[{p}]" for p in field_citations(c, field) if f"[{p}]" not in why)
            parts.append(f"| {cell(c.get('rank'))} / {identity} | {cell(c['name'])} | {cell(why)} | {cell(render_field(c, 'risks', doc))} |")
    else:
        parts.append("当前没有通过提取校验的结构化候选比较。设计原文可能含备选思路，保留在原设计档案中；不据此恢复已验证排名。")
    return re.sub(r"(\|[^\n]*\|)\n\n(?=\|)", r"\1\n", "\n\n".join(parts))


def render_research(doc):
    from .research_original import render as render_original
    original = render_original(doc)
    if original is not None:
        return original
    if doc.get("writer_contract_version") == 3:
        from .stage4_reporting import render
        return render(doc)
    from .research_narrative import render as render_narrative
    integrated = render_narrative(doc)
    if integrated:
        return integrated
    research = doc.get("research") or {}
    parts = ["### 2.1 研究依据与当前边界",
             "当前尚无与输入和来源匹配的完整整合审核。以下保留归档论证及当前方案说明。"]
    parts += ["### 2.2 候选比较与取舍",
              "探索优先级不能视为实测性能排名。"
              if research.get("candidates") else "当前没有通过提取校验的结构化候选比较，原始备选思路仍保留在设计档案中。"]
    if research.get("arguments"):
        from .material_reasoning import render_structured_chain
        parts += ["### 2.3 从文献到材料选择的分析链",
                  "以下沿研究问题、材料机制、候选取舍和验证边界组织已归档论证；文献支持仅适用于原研究对象。",
                  render_structured_chain(research["arguments"], doc)]
        # 部分提取成功不能阻止展示其余原设计论证；补充仍明确标作待核验摘录。
        from .research_sources import paragraphs
        bound = "\n".join(q for r in research["arguments"] for q in r.get("source_quotes", []))
        rationale_sections = {h["id"] for h in (doc.get("design_structure") or {}).get("headings", [])
                              if str(h.get("number", "")).split(".")[0] == "4"}
        extra = []
        for block in paragraphs(doc.get("final_answer", "")):
            if block["section_id"] in ("legacy", "goals", "metrics", "recommendations", "risks"):
                continue
            text = block["text"]
            if ((block["section_id"] in rationale_sections or block["section_id"].startswith(("why-", "feasibility", "synthesis-consistency", "electrolyte-compat")))
                    and len(text) >= 45 and text not in bound and not text.lstrip().startswith("|")):
                extra.append("> " + text.replace("\n", "\n> "))
        if extra:
            parts += ["#### 原设计补充论证（待核验）", "以下保留未被上述结构化记录覆盖的原设计论证。", "\n\n".join(extra)]
    else:
        parts.append(render_archived_arguments(doc))
    parts.append("### 2.4 主方案选择与理由")
    parts.append(_render_current_choice(doc))
    parts += ["### 2.5 关键限制", "尚未解决的问题不能被表述为已验证结论。"]
    # 表格行必须连续，段落之间保持空行。
    return re.sub(r"(\|[^\n]*\|)\n\n(?=\|)", r"\1\n", "\n\n".join(parts))


def render_archived_arguments(doc):
    """结构化摘要缺失时只读摘录原论证，不修复状态或重建主方案。"""
    from .material_arguments import build_arguments, CATEGORIES
    arguments = build_arguments(doc, rationale_only=True)
    from .material_reasoning import render_claim_chain, archived_decision_chain
    parts = ["### 2.3 从文献到材料选择的分析链（归档摘录）",
             "以下按研究对象与动机、材料依据、候选取舍和验证路径重组设计原文，说明文献如何影响选材。结构化论证尚未形成有效记录，引用可定位不等于结论已验证；原文中的主选、排序和推荐均为归档观点，不能替代第3节当前方案。"]
    decision = archived_decision_chain(doc)
    if decision:
        parts.append(decision)
    for comp, title, _ in CATEGORIES:
        claims = arguments["components"][comp]["claims"]
        if not claims:
            continue
        parts.append("#### " + title)
        if any(claim["inference"] for claim in claims):
            parts.append("无直接数据、跨体系迁移及机理推断仍为待验证判断；以下按其在选材决策中的作用组织原文。")
        parts.append(render_claim_chain(claims))
    if not any(v["claims"] for v in arguments["components"].values()):
        parts.append("归档中没有可定位且可与当前规格分开的选材论证。需补充材料与研究目标的关联、比较对象、文献依据和适用条件；不以通用优点补造理由。")
    return "\n\n".join(parts)


def render_review_appendix(doc):
    if doc.get("_report_record"):
        from .report_current_view import appendix
        return appendix(doc)
    from .report_readiness import unresolved_issues
    issues = unresolved_issues(doc)
    diagnostics = (doc.get("requirements") or {}).get("diagnostics", []) + (doc.get("research") or {}).get("diagnostics", [])
    items = list(dict.fromkeys(issues + diagnostics))
    body = render_coverage(doc)
    body += "\n\n" + render_requirement_details(doc, "### 课题逐项回应档案")
    body += "\n\n" + render_candidate_comparison(doc, "### 候选比较档案")
    body += "\n\n### 完整审核与解析记录\n\n" + ("\n\n".join(numbered_detail(n, "待复核事项", s) for n, s in enumerate(items, 1)) if items else "没有尚未解决的审核意见；这不等于性能已通过实验验证。")
    if doc.get("writer_contract_version") == 3:
        from .stage4_reporting import audit
        body += "\n\n" + audit(doc)
    from .research_narrative import display_narrative
    from .research_original import available as original_available
    direct = original_available(doc)
    integrated = None if direct else display_narrative(doc)
    if direct:
        body += "\n\n### 原文与参数版本\n\n第2节直接呈现归档五段稿，显式参数引用读取当前方案表。原稿科学审核及保留意见继续沿用。"
    elif integrated and integrated["status"] == "approved":
        body += "\n\n### 连贯论证审核\n\n整合稿已按原始课题、来源、参数及未解决限制通过Reviewer审核；这表示表述与依据相符，不表示本课题性能已验证。"
    elif doc.get("research_narrative"):
        row = doc["research_narrative"]
        body += "\n\n### 连贯论证审核\n\n" + ("整合稿尚未整体通过；论证与计算说明按逐项审核展示，待复核部分保留原稿并标明具体原因。"
            if integrated else "整合稿尚无与当前输入匹配的有效审核或输入已变化；旧稿不作为当前结论，逐项回应保留于本附录。")
        body += "\n\n" + "\n\n".join(str(s) for s in row.get("diagnostics", []))
        if not integrated and row.get("sections"):
            body += "\n\n### 历史整合原稿（当前审核无效）\n\n"
            body += "\n\n".join("> " + str(p.get("text", "")).replace("\n", "\n> ")
                                  for s in row["sections"] if isinstance(s, dict)
                                  for p in s.get("paragraphs", []) if isinstance(p, dict))
            notes = row.get("calculation_notes") or []
            if isinstance(notes, list):
                body += "\n\n" + "\n\n".join("> " + str(p.get("text", "")).replace("\n", "\n> ")
                                              for p in notes if isinstance(p, dict))
    review = {} if direct else (doc.get("research_narrative") or {}).get("review") or {}
    if isinstance(review, dict):
        coverage = review.get("coverage") or {}
        if isinstance(coverage, dict) and coverage.get("reason"):
            body += "\n\n**整体覆盖审核：** " + str(coverage["reason"])
        notes = review.get("notes") or []
        if isinstance(notes, list) and notes:
            body += "\n\n**论证审核补充提醒：**\n\n" + "\n\n".join(numbered_detail(i, "提醒", str(note)) for i, note in enumerate(notes, 1))
    if doc.get("experiment_extensions"):
        body += "\n\n### 后续实验与采购补充\n\n" + "\n\n".join(numbered_detail(n, "扩展安排", s) for n, s in enumerate(doc["experiment_extensions"], 1))
    if doc.get("ledger_view"):
        from .parameter_presentation import render_parameter_appendix
        body += "\n\n" + render_parameter_appendix(doc["ledger_view"])
    from .adaptive_retrieval import render_audit
    audit = render_audit(doc)
    if audit:
        body += "\n\n" + audit
    if doc.get("numeric_contract"):
        from .numeric_consistency import document_audit, render_audit as render_numeric
        body += "\n\n" + render_numeric(document_audit(doc))
    return body
