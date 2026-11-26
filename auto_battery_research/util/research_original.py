"""五段稿只读直出，沿用原审核并用当前方案表刷新显式参数引用。"""
from __future__ import annotations

import copy
import re
import unicodedata

from .design_structure import START, END, ANCHOR, headings, markdown_lines, prose_lines, structure_issues
from .numeric_bindings import display, occurrences
from .parameter_names import checked_name, effective_parameters
from .scheme_ledger import digest


def available(doc):
    """结构化五段稿默认直出；v3继续使用自身内容编译器。"""
    text = doc.get("final_answer") or ""
    return (doc.get("writer_contract_version") != 3 and bool(doc.get("ledger_view"))
            and bool(doc.get("design_structure") or START in text or END in text))


def _normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _parameters(doc):
    """读取方案表唯一有效声明；旧字段仅提供身份，不得覆盖新值。"""
    from .numeric_consistency import identity
    current = effective_parameters(doc["ledger_view"].get("parameters", {}))
    previous = doc.get("scheme_parameters") or doc.get("parameters") or {}
    result = copy.deepcopy(current)
    for _, _, kind, path, _ in occurrences(doc["final_answer"]):
        if kind != "param":
            continue
        old = previous.get(path)
        try:
            if old:
                checked_name(path, old)
            if path in current:
                checked_name(path, current[path])
                if old and identity(path, old) != identity(path, current[path]):
                    result.pop(path, None)
                continue
            # 字段重命名只接受已登记的同身份别名，文献、对照及工况仍分别匹配。
            expected = identity(path, old or {})
            matches = []
            for key, row in current.items():
                try:
                    checked_name(key, row)
                except (ValueError, TypeError):
                    continue
                if identity(key, row) == expected:
                    matches.append(row)
            if matches and all(row.get("value") == matches[0].get("value")
                               and row.get("unit") == matches[0].get("unit") for row in matches):
                result[path] = copy.deepcopy(matches[0])
        except (ValueError, TypeError):
            result.pop(path, None)
    return result


def _refresh(body, parameters):
    """只替换程序签发的参数位置；手写数值与文献原值保留原文。"""
    from .research_reporting import cell
    changed, unresolved = [], []
    for start, end, kind, path, shown in reversed(occurrences(body)):
        if kind == "derived":
            value = shown + "（历史计算待复核）"
            unresolved.append(path)
        else:
            try:
                value = display(kind, path, parameters, effective=parameters)
                if parameters[path].get("value") is None:
                    unresolved.append(path)
            except (ValueError, KeyError, TypeError):
                value = "待核对（当前方案缺少同口径参数）"
                unresolved.append(path)
            if _normalized(value) != _normalized(shown):
                changed.append({"path": path, "before": shown, "after": value})
        line_start = body.rfind("\n", 0, start) + 1
        value = cell(value) if body[line_start:start].lstrip().startswith("|") else value.replace("\n", " ")
        body = body[:start] + value + body[end:]
    if any(re.search(r"<!-- abr-(?:param|derived):|<!-- /abr-value -->|\{\{(?:param|derived):", line)
           for _, line in prose_lines(body)):
        raise ValueError("原文直出存在不完整的参数引用标记")
    return body, list(reversed(changed)), sorted(set(unresolved))


def view(doc):
    """报告与终审共用当前投影，原稿、参数表及审核档案均只读。"""
    if not available(doc):
        return None
    text = doc["final_answer"]
    errors = structure_issues(text, doc.get("design_structure"))
    if errors:
        raise ValueError("五段稿原文结构无效：" + "；".join(errors))
    region = text.split(START, 1)[1].split(END, 1)[0].strip()
    anchors = [(offset, ANCHOR.fullmatch(line)[1]) for offset, line in markdown_lines(region)
               if ANCHOR.fullmatch(line)]
    titles = headings(region)
    parameters = _parameters(doc)
    lookup = {key: "2." + re.match(r"\d+(?:\.\d+)*", title)[0]
              for (_, key), (_, _, _, title) in zip(anchors, titles)}
    requirements = (doc.get("requirements") or {}).get("items", [])
    from .research_support import FIELDS, field_quotes, is_summary
    quotes = {}
    for collection, fields in FIELDS.items():
        for row in (doc.get("research") or {}).get(collection, []):
            ids = [row.get("requirement_id")] if collection == "answers" else row.get("requirement_ids", [])
            for field in fields:
                for quote in field_quotes(row, field) if is_summary(row) else [row.get("source_quote", "")]:
                    if quote:
                        for rid in ids:
                            quotes.setdefault(rid, []).append(quote)
    review = doc.get("reviewer_output") or {}
    sections, changes, diagnostics = [], [], []
    for index, ((_, sid), (offset, heading, level, title)) in enumerate(zip(anchors, titles)):
        end = anchors[index + 1][0] if index + 1 < len(anchors) else len(region)
        source = region[offset + len(heading):end].strip()
        body, updates, unresolved = _refresh(source, parameters)
        links = [(pos + m.start(), pos + m.end(), m[1]) for pos, line in prose_lines(body)
                 for m in re.finditer(r"\[[^\]]*\]\(#design-([^)]*)\)", line)]
        for start, stop, key in reversed(links):
            body = body[:start] + f"[第 {lookup[key]} 节](#report-design-{key})" + body[stop:]
        related = [item["id"] for item in requirements if item["id"] in source
                   or any(quote in source for quote in quotes.get(item["id"], []))]
        notes = []
        if updates:
            notes.append("本节参数已按当前方案表更新，相关手算结果、比较结论与适用条件仍需随输入复核。")
        if unresolved:
            notes.append("本节存在缺少当前声明或口径不一致的参数及历史计算，相关结果待核对。")
        for issue in review.get("repair_issues", []):
            quote = issue.get("quote") if isinstance(issue, dict) else None
            if quote and quote in source and issue.get("reason"):
                notes.append(str(issue["reason"]))
        notes = list(dict.fromkeys(notes))
        location = f"第{lookup[sid]}节·" + re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", title)
        sections.append({"id": sid, "number": lookup[sid], "title": location.split("·", 1)[1],
                         "level": level + 1, "text": body, "requirement_ids": related,
                         "location": location, "issues": notes})
        changes.extend(dict(update, section_id=sid) for update in updates)
        diagnostics.extend(location + "：" + note for note in notes)
    status = ("reviewed" if doc.get("review_passed") else "reviewed_with_issues") if doc.get("review_completed") else "unreviewed"
    return {"mode": "original", "source_hash": digest(text), "status": status,
            "sections": sections, "parameter_changes": changes, "diagnostics": diagnostics,
            "review_issues": list(dict.fromkeys(str(issue) for issue in review.get("issues", [])))}


def render(doc):
    data = view(doc)
    if data is None:
        return None
    status = {"reviewed": "原稿已完成科学审核。", "reviewed_with_issues": "原稿已完成科学审核，仍有保留意见。",
              "unreviewed": "原稿尚未完成有效科学审核。"}[data["status"]]
    parts = ["以下保留完整设计原文，已绑定的参数引用采用当前方案表。" + status
             + "原稿中的实验提案与本轮执行安排请结合第4节核对。"]
    if data["parameter_changes"]:
        parts.append("> **参数版本已更新。** 原文中的计算过程、未绑定的中间量及科学推论需按本次参数变化复核。")
    for section in data["sections"]:
        parts.append(f'<a id="report-design-{section["id"]}"></a>\n\n'
                     + "#" * section["level"] + f' {section["number"]} {section["title"]}')
        if section["issues"]:
            parts.append("> **本节复核说明。** " + "；".join(section["issues"]).replace("\n", " "))
        if section["text"]:
            parts.append(section["text"])
    return "\n\n".join(parts)


def render_review_appendix(doc):
    """原稿完整审核意见集中放入补充材料，原审核状态继续沿用。"""
    data = view(doc)
    if data is None or not data["review_issues"]:
        return ""
    return "### 原稿审核保留意见\n\n" + "\n".join(
        f"- {issue.replace(chr(10), ' ')}" for issue in data["review_issues"])
