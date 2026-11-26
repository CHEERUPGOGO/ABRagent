"""Stage 4 生成和验收共用的正文完整性检查。"""

import re

REQUIRED_SECTIONS = [
    ("目标与设计路线", ["目标", "设计路线", "能量目标", "target", "路线"]),
    ("推荐组合", ["推荐材料组合", "推荐组合", "正极", "负极", "电解液", "combination", "配方"]),
    ("预期关键指标", ["预期指标", "关键指标", "能量密度", "比容量", "metrics", "性能指标"]),
    ("可行性依据", ["可行性依据", "文献证据", "机理支撑", "论证链与机理", "feasibility", "evidence", "依据"]),
    ("风险与数据缺口", ["风险与数据缺口", "风险分析", "数据缺口", "risks", "gaps", "风险评估"]),
]


def normalize_titles(text):
    """兼容旧调用，不再猜测改名；新稿在Writer出口按结构契约生成标题。"""
    return text


def design_sections(text: str) -> tuple[list, list, list]:
    headings = re.findall(r"^#{1,4}\s+(.+)$", text, re.MULTILINE)
    found, missing = [], []
    for name, words in REQUIRED_SECTIONS:
        matched = any(any(w.lower() in h.lower() for w in words) for h in headings)
        matched = matched or any(re.search(rf"\*\*\s*\d?\.?\s*({w})\s*\*\*", text, re.I) for w in words)
        (found if matched else missing).append(name)
    return headings, found, missing


BOLD_ERROR = "正文粗体标记未闭合，疑似输出截断"
FENCE_ERROR = "正文代码块未闭合，疑似输出截断"
FORMAT_ERROR = "正文存在可局部定位的粗体标记错误"


def _markdown_markers(text):
    """只在 Markdown 正文定位标记，保留原文行号供受限修复使用。"""
    odd_lines = []
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue
        visible = re.sub(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", "", line)
        if len(re.findall(r"(?<!\\)\*\*", visible)) % 2:
            odd_lines.append((number, line))
    return odd_lines, fence


def bold_repair_issue(text):
    """仅对可唯一定位且后续仍有正文的单行授予标记修改权限。"""
    odd, fence = _markdown_markers(text)
    if fence or len(odd) != 1:
        return None
    number, line = odd[0]
    if number >= len(text.rstrip().splitlines()) or text.count(line) != 1:
        return None
    issue = {"line": number, "quote": line, "reason": BOLD_ERROR,
             "suggestion": "只修正此行的粗体分隔符，保留全部文字、参数和引用", "parameter_paths": []}
    # 表格单元格末尾只有一个孤立闭标记，直接删除该标记可逐字保留全部内容。
    if line.lstrip().startswith("|") and "`" not in line and r"\|" not in line:
        match = re.search(r"(?<![\\*])\*\*(?=[ \t]*(?:\|[ \t]*)?$)", line)
        if match and "**" not in line[:match.start()].rsplit("|", 1)[-1]:
            issue["marker_offset"] = match.start()
    elif not line.lstrip().startswith("|") and "`" not in line and re.search(r"[。！？.!?；;][ \t]*$", line):
        # 唯一非末行且句子已结束，只补闭标记；半句话仍按疑似截断拦截。
        issue["insert_offset"] = len(line.rstrip())
    return issue


def classify_design_output(text: str, requirements=None):
    """区分内容完整性阻断和可逐字保留内容的局部格式问题。"""
    errors = design_output_issues(text, requirements)
    issue = bold_repair_issue(text) if FORMAT_ERROR in errors else None
    return {"blocking_issues": [e for e in errors if e != FORMAT_ERROR],
            "format_issues": [dict(issue, reason=FORMAT_ERROR)] if issue else []}


def design_output_issues(text: str, requirements=None) -> list[str]:
    from .design_structure import structure_issues
    errors = []
    if len(text.strip()) < 200:
        errors.append("设计正文不足200字符")
    missing = design_sections(text)[2] if (requirements or {}).get("scheme_required", True) else []
    if missing:
        errors.append("缺少五段式章节：" + "、".join(missing))
    errors.extend(structure_issues(text))
    odd, fence = _markdown_markers(text)
    if fence:
        errors.append(FENCE_ERROR)
    if len(odd) % 2:
        issue = bold_repair_issue(text)
        local = issue and ("marker_offset" in issue or "insert_offset" in issue)
        errors.append(FORMAT_ERROR if local else BOLD_ERROR)
    return errors
