"""实验段落主题：首稿绑定、局部修复失效及旧记录的只读回退。"""
from __future__ import annotations

import re

from .scheme_ledger import digest


INSTRUCTIONS = '''
同时输出可选 step_titles，键与 sections 相同，值为与本节段落逐项对应的短中文主题字符串数组。
每段一个主题，建议2至24字，例如“粉体合成”“极片加工”“结构与成分”“抑氧验证”“排序与取舍”。
标题仅概括本段已经写出的对象与操作，优先沿用本段开头明确的主题；不能新增配方、数值结果或已成功、已达标、机理已证实等结论。
各节标题不能统一套用“性能验证”“判定与迭代”“数据缺口与获取方法”等通用标签。同一主题确实分段时允许重名，不为去重虚构差异。
sections仍是原有段落字符串数组，保留所有参数引用；step_titles中不写参数占位符、Markdown或段落哈希，哈希由程序绑定。
'''


def checked_title(value, paragraph):
    """只接受简短主题和正文已有术语；不能把标题当作额外科学结论。"""
    if not isinstance(value, str) or not isinstance(paragraph, str):
        return None
    title = value.strip()
    if not 2 <= len(title) <= 24 or not re.search(r"[\u4e00-\u9fff]", title):
        return None
    if re.search(r"[\r\n{}\[\]<>`*#|：:。；;，,！？!?=]", title):
        return None
    if re.search(r"已|达标|达成|成功|最优|最佳|证明|证实|揭示|显著|有效抑制|大幅|提升|提高|降低|改善", title):
        return None
    if re.search(r"\d\s*(?:%|℃|°|K\b|V\b|mAh|Wh|mg|[μµu]m|mol|轮|圈|小时)", title, re.I):
        return None
    # 新材料名、仪器名或数字不能仅在标题出现。正文的参数占位符不算实际术语。
    source = re.sub(r"\{\{[^{}]*\}\}", "", paragraph).casefold()
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9_+/-]*", title)
    if any(term.casefold() not in source for term in terms):
        return None
    # 允许简短意译，但至少要有正文支持的主题词，不能只凭“验证”等通用后缀。
    topic = re.sub(r"验证|测试|测量|表征|核对|确认|安排|方案|步骤|方法|条件|工艺|获取|与|及|的", "", title)
    anchors = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9_+/-]*", topic)
    if not any(anchor.casefold() in source or any(anchor[i:i+2] in source for i in range(len(anchor)-1))
               for anchor in anchors):
        return None
    return title


def normalize_step_titles(record, *, bind_unbound=False):
    """仅首稿允许绑定裸标题；修复后丢弃陈旧绑定，不影响实验正文验收。"""
    if "step_titles" not in record:
        return []
    metadata = record.get("step_titles")
    sections = record.get("sections")
    diagnostics, normalized = [], {}
    if isinstance(metadata, dict) and isinstance(sections, dict):
        for section, paragraphs in sections.items():
            rows = metadata.get(section)
            if rows is None:
                continue
            if not isinstance(paragraphs, list) or not isinstance(rows, list) or len(rows) != len(paragraphs):
                diagnostics.append(f"实验主题标题回退：{section}标题与段落数量不匹配")
                continue
            entries = []
            for index, (paragraph, row) in enumerate(zip(paragraphs, rows)):
                title = None
                if isinstance(paragraph, str):
                    if isinstance(row, dict) and row.get("paragraph_hash") == digest(paragraph):
                        title = checked_title(row.get("title"), paragraph)
                    elif bind_unbound and isinstance(row, str):
                        title = checked_title(row, paragraph)
                entries.append({"title": title, "paragraph_hash": digest(paragraph)} if title else None)
                if row is not None and title is None:
                    diagnostics.append(f"实验主题标题回退：{section}第{index+1}段标题无效或正文已变更")
            if any(entries):
                normalized[section] = entries
    else:
        diagnostics.append("实验主题标题回退：step_titles结构无效")
    if normalized:
        record["step_titles"] = normalized
    else:
        record.pop("step_titles", None)
    return diagnostics


def lead_title(paragraph):
    """旧记录只读回退：明确的开头主题或动作，不扫描正文关键词。"""
    text = paragraph.strip()
    # 英文冒号两侧均为字母/数字时是配方比例，不能截成主题。
    match = re.match(r"([^\n。；;，,：:]{2,24})[：:]", text)
    if match and not (text[match.end()-1] == ":" and re.search(r"[A-Za-z0-9]:[A-Za-z0-9]", text[match.end()-2:match.end()+1])):
        title = checked_title(match[1], paragraph)
        if title:
            return title
    # 已明确写在句首的动作可沿用；跨任务的陌生方法仍由显式主题或中性编号承接。
    starts = (
        (r"合成(?:采用|路线|方法)", "合成路线"),
        (r"极片(?:按|加工|制备)", "极片加工"),
        (r"(?:首轮)?(?:设|设置).*?主组.*?对照", "主组与对照"),
        (r"每组平行", "平行样"),
        (r"对照[^。；]*同批同工艺", "对照工艺"),
        (r"(?:CR\d+|扣式电池|扣电)[^。；]*装配", "装配"),
        (r"电解液(?:为|配制)", "电解液配制"),
        (r"电解液[^。；]*(?:无|缺少).*先例", "电解液先例核对"),
        (r"(?:配浆|浆料配制)", "配浆"),
        (r"涂布", "涂布"),
        (r"(?:干燥|真空干燥)", "干燥"),
        (r"辊压", "辊压"),
        (r"注液", "注液"),
        (r"(?:化成与循环|化成|循环测试)", None),
        (r"(?:循环(?:前后|前|后))?(?:原位|非原位)?[A-Za-z][A-Za-z0-9/+\-]{1,15}(?:表征|分析|测试|测量|检测)", None),
    )
    for pattern, title in starts:
        match = re.match(pattern, text)
        if match:
            candidate = checked_title(title or match[0], paragraph)
            if candidate:
                return candidate
    return None


def paragraph_title(record, section, index, paragraph):
    """渲染不改写旧记录；编号为1起始，绑定失效时不重新绑定旧标题。"""
    metadata = record.get("step_titles")
    rows = metadata.get(section) if isinstance(metadata, dict) else None
    paragraphs = record.get("sections", {}).get(section)
    if isinstance(rows, list) and isinstance(paragraphs, list) and len(rows) == len(paragraphs):
        row = rows[index-1]
        if isinstance(row, dict) and row.get("paragraph_hash") == digest(paragraph):
            title = checked_title(row.get("title"), paragraph)
            if title:
                return title
    return lead_title(paragraph) or (f"待确认事项{index}" if section == "gaps" else f"步骤{index}")
