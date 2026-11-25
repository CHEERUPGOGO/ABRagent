"""Stage 6只读呈现v3完整科研内容，保持顺序、引用和未解决科学边界。"""
from .stage4_document import compile_document
from .stage4_research_projection import FIELDS
from .research_support import supported


def render(doc):
    parameters = doc.get("ledger_view", {}).get("parameters", {})
    compiled = compile_document(doc.get("writer_content"), parameters, doc.get("requirements"), doc.get("evidence", []))
    if not compiled.valid:
        raise ValueError("当前v3正文无法编译，不能生成替代科研内容")
    from .stage4_session import document_errors
    errors = document_errors(doc)
    if errors:
        raise ValueError("；".join(errors))
    pending = {}
    for collection, names in FIELDS.items():
        for row in doc.get("research", {}).get(collection, []):
            for name in names:
                if not row.get(name) or supported(row, name, doc, parameters):
                    continue
                verdict = row.get("support_review", {}).get("fields", {}).get(name, {})
                reason = verdict.get("reason", "尚未取得当前依赖的科学支持性审核")
                for source in row.get("field_sources", {}).get(name, []):
                    pending.setdefault(source["leaf_id"], []).append(reason)
    text = compiled.markdown
    # 待审提示按叶身份附在所在内容块之后，不将同一科学片段重复列为结论。
    for key in reversed(list(compiled.leaves)):
        if key not in pending:
            continue
        item = compiled.leaves[key]
        if item["table"]:
            continue  # 表格待审逐单元格位置统一在本节末提示，保持表格结构。
        if item["start"] >= 0:
            end = item["end"]
            note = "\n\n> 此处科学支持待复核：" + "；".join(dict.fromkeys(pending[key]))
            text = text[:end] + note + text[end:]
    # 从深层标题开始，避免父级匹配到子标题尾部；叶原文保持不变。
    for section in reversed(compiled.sections):
        original = f'{"#" * section["level"]} {section["number"]}{"." if section["level"] == 2 else ""} {section["title"]}'
        heading = f'{"#" * min(6, section["level"] + 1)} 2.{section["number"]} {section["title"]}'
        text = text.replace("\n" + original + "\n", "\n" + heading + "\n", 1)
    table_notes = [(key, values) for key, values in pending.items() if compiled.leaves[key]["table"]]
    if table_notes:
        text += "\n\n**表格内容待复核：**\n\n" + "\n\n".join(
            f"- {key}：" + "；".join(dict.fromkeys(values)) for key, values in table_notes)
    review = doc.get("consistency_review") or {}
    if review.get("status") != "passed" or review.get("input_fingerprint") != compiled.render_fingerprint:
        text += "\n\n> 当前全文一致性未完成有效复核。"
    return text


def reference(row, name):
    sources = row.get("field_sources", {}).get(name, [])
    return "、".join(dict.fromkeys(f"[正文 {s['leaf_id']}](#design-{s.get('section_id', '')})" if s.get("section_id") else "正文 " + s["leaf_id"] for s in sources)) or "关联缺失"


def details(doc, heading):
    parameters = doc.get("ledger_view", {}).get("parameters", {})
    parts = [heading]
    for collection, names in FIELDS.items():
        for row in doc.get("research", {}).get(collection, []):
            key = row.get("requirement_id" if collection == "answers" else "id")
            parts.append("**" + str(key) + "**" + ("：" + row["name"] if row.get("name") else ""))
            for name in names:
                if row.get(name):
                    verdict = row.get("support_review", {}).get("fields", {}).get(name, {})
                    state = "审核支持" if supported(row, name, doc, parameters) else "待复核：" + verdict.get("reason", "缺少当前支持性审核")
                    parts.append(f"- {name}：{reference(row, name)}；{state}")
    return "\n\n".join(parts)


def audit(doc):
    review = doc.get("consistency_review") or {}
    parts = ["### 独立一致性终审", "一致性结论只表示当前表述和数据关系的复核状态，不覆盖科学否决或证明性能可行。",
             "当前状态：" + str(review.get("status", "unreviewed"))]
    parts += [f"- {key}：{value}" for key, value in review.get("dimensions", {}).items()]
    parts += ["- " + i["reason"] for i in review.get("issues", [])]
    if review.get("error"):
        parts.append("请求失败：" + review["error"])
    parts += ["### 调用与修复档案", "| 角色 | 次数 | 状态 |", "|---|---|---|"]
    parts += [f"| {r['role']} | {r['attempt']} | {r['status']} |" for r in doc.get("stage4_requests", [])]
    return "\n\n".join(parts[:3]) + "\n\n" + "\n".join(parts[3:])
