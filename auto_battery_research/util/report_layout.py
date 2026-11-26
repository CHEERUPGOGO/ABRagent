"""完整五段稿报告的五节布局及程序生成文字的章节编号映射。"""
from __future__ import annotations

import re

from .research_original import available as original_available


def section_count(doc):
    return 5 if original_available(doc) else 6


def _number(value):
    main, dot, sub = value.partition(".")
    if main == "3":
        return "2"
    return {"4": "3", "5": "4", "6": "5"}.get(main, main) + dot + sub


def remap_sections(text, doc):
    """只用于报告模板及程序投影，原科研正文与历史审核原句不经过此映射。"""
    if not original_available(doc):
        return text
    from .design_structure import markdown_lines

    def reference(match):
        numbers = re.sub(r"\b[1-6](?:\.\d+)*", lambda m: _number(m[0]), match[1])
        return "第" + numbers + "节"

    for offset, line in reversed(list(markdown_lines(text))):
        updated = re.sub(r"^(#{2,6}\s+)([4-6])(?=\.(?:\d|\s))",
                         lambda m: m[1] + str(int(m[2]) - 1), line)
        updated = re.sub(r"第\s*([1-6](?:\.\d+)*(?:\s*[、和]\s*[1-6](?:\.\d+)*)*)\s*节", reference, updated)
        updated = re.sub(r"第([三四五六])部分", lambda m: "第" + dict(三="二", 四="三", 五="四", 六="五")[m[1]] + "部分", updated)
        updated = re.sub(r"表\s*([4-6])(?=[-－.]\d)", lambda m: "表" + str(int(m[1]) - 1), updated)
        text = text[:offset] + updated + text[offset + len(line):]
    return text


def compile_main(text, doc):
    """移除重复方案章节，保留第二部分原文及其稳定锚点。"""
    if not original_available(doc):
        return text
    from .design_structure import headings
    positions = {title: offset for offset, _, level, title in headings(text) if level == 2}
    materials = positions["2. 材料选择与文献依据"]
    design = positions["3. 电芯总体方案与关键匹配"]
    physics = positions["4. 仿真与优化分析"]
    sources = positions["6. 参考文献"]
    text = (remap_sections(text[:materials], doc) + text[materials:design]
            + remap_sections(text[physics:sources], doc)
            + text[sources:].replace("## 6. 参考文献", "## 5. 参考文献", 1))
    return text.replace("<!-- abr-report-layout:6 -->", "<!-- abr-report-layout:5 -->", 1)
