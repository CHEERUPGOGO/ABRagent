"""统一报告记录中的明确文字矛盾及全部章节版本核对。"""
from __future__ import annotations

import re


def wording_errors(text):
    errors = []
    for paragraph in text.splitlines():
        if (re.search(r"未(?:报告|提供|注明)[^。；]{0,20}(?:循环倍率|倍率)", paragraph)
                and re.search(r"(?:属|属于|是|仅有)\s*非\s*1\s*C", paragraph)):
            errors.append("文献倍率未报告，不能据此断言为非1 C数据")
    return list(dict.fromkeys(errors))


def check(text, record, supplement=None):
    from .report_record import attach, marker
    from .report_contract import report_context, validate_report_blocks
    from .research_reporting import render_summary, render_review_appendix
    from .report_readiness import notice
    from .report_analysis import render_materials
    doc = attach(record)
    from .report_bundle import validate_parts
    from .report_layout import remap_sections, section_count
    errors = validate_parts(text, supplement)
    audit_text = supplement if supplement is not None else text
    count = section_count(doc)
    for heading in range(1, count + 1):
        if len(re.findall(r"^## " + str(heading) + r"\. ", text, re.M)) != 1:
            errors.append("报告章节缺失或重复：" + str(heading))
    if len(re.findall(r"^## \d+\. ", text, re.M)) != count:
        errors.append("报告章节数量与当前布局不一致")
    if marker(record) not in text:
        errors.append("报告缺少统一当前记录标记")
    if doc.get("requirements"):
        expected = (remap_sections(render_summary(doc), doc), render_materials(doc), remap_sections(notice(doc), doc))
        for block in expected:
            if block not in text:
                errors.append("摘要、论证或附录未使用同一当前记录")
        if render_review_appendix(doc) not in audit_text:
            errors.append("完整审核附录未使用同一当前记录")
    from .research_original import render_review_appendix as render_original_review
    original_review = render_original_review(doc)
    if original_review and (supplement is None or original_review not in supplement):
        errors.append("补充材料缺少原稿审核保留意见")
    errors += wording_errors(text) + wording_errors(audit_text)
    return list(dict.fromkeys(errors))
