"""Stage 4 生成和验收共用的正文完整性检查。"""

import re

REQUIRED_SECTIONS = [
    ("目标与设计路线", ["目标", "设计路线", "能量目标", "target", "路线"]),
    ("推荐组合", ["推荐材料组合", "推荐组合", "正极", "负极", "电解液", "combination", "配方"]),
    ("预期关键指标", ["预期指标", "关键指标", "能量密度", "比容量", "metrics", "性能指标"]),
    ("可行性依据", ["可行性依据", "文献证据", "机理支撑", "feasibility", "evidence", "依据"]),
    ("风险与数据缺口", ["风险与数据缺口", "风险分析", "数据缺口", "risks", "gaps", "风险评估"]),
]


def design_sections(text: str) -> tuple[list, list, list]:
    headings = re.findall(r"^#{1,4}\s+(.+)$", text, re.MULTILINE)
    found, missing = [], []
    for name, words in REQUIRED_SECTIONS:
        matched = any(any(w.lower() in h.lower() for w in words) for h in headings)
        matched = matched or any(re.search(rf"\*\*\s*\d?\.?\s*({w})\s*\*\*", text, re.I) for w in words)
        (found if matched else missing).append(name)
    return headings, found, missing


def design_output_issues(text: str) -> list[str]:
    errors = []
    if len(text.strip()) < 200:
        errors.append("设计正文不足200字符")
    missing = design_sections(text)[2]
    if missing:
        errors.append("缺少五段式章节：" + "、".join(missing))
    if len(re.findall(r"^\s*```", text, re.M)) % 2:
        errors.append("正文代码块未闭合，疑似输出截断")
    if len(re.findall(r"(?<!\\)\*\*", text)) % 2:
        errors.append("正文粗体标记未闭合，疑似输出截断")
    return errors
