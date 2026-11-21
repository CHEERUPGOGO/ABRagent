"""Stage 4 设计稿结构契约：模型填内容，程序生成标题、编号和内部引用。"""
import json
import re

from .design_sections import REQUIRED_SECTIONS

VERSION = 1
SECTION_IDS = ("goals", "recommendations", "metrics", "feasibility", "risks")
SECTION_TITLES = dict(zip(SECTION_IDS, (name for name, _ in REQUIRED_SECTIONS)))
START = "<!-- abr-design:v1 -->"
END = "<!-- /abr-design -->"
ANCHOR = re.compile(r'<a id="design-([a-z][a-z0-9_-]{0,63})"></a>')
REFERENCE = re.compile(r"\{\{section:([a-z][a-z0-9_-]{0,63})\}\}")
NUMBER_REFERENCE = re.compile(r"(?:见\s*(?:第\s*)?(\d+(?:\.\d+)+)|第\s*(\d+(?:\.\d+)*)\s*[节章])")


def markdown_lines(text):
    """返回代码围栏外的行及字符坐标，保留原换行和原文供受限修复。"""
    fence, offset = None, 0
    for line in text.splitlines(keepends=True):
        value = line.rstrip("\r\n")
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", value)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
        elif marker:
            fence = marker[1]
        else:
            yield offset, value
        offset += len(line)


def headings(text):
    return [(offset, line, len(match[1]), match[2]) for offset, line in markdown_lines(text)
            if (match := re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$", line))]


def prose_lines(text):
    """章节引用只在可见正文中解析，围栏和行内代码保持原样。"""
    for offset, line in markdown_lines(text):
        yield offset, re.sub(r"(`+).*?\1", lambda m: " " * len(m[0]), line)


def protected_structure(text):
    """正文补丁不能修改任何层级标题、程序锚点或结构边界。"""
    return [line for _, line in markdown_lines(text)
            if re.match(r"^ {0,3}#{1,6}(?:\s|$)", line) or line in (START, END)
            or ANCHOR.fullmatch(line)]


def _content(body):
    visible = re.sub(r"<!--.*?-->|<a\b[^>]*>.*?</a>|\[.*?\]\([^)]*\)", "", body, flags=re.S)
    visible = re.sub(r"[\s\W_]+", "", visible)
    return bool(visible) and visible.lower() not in {"待补充", "待填写", "暂无", "无", "todo", "tbd", "null"}


def _body(value):
    if not isinstance(value, str):
        raise ValueError("章节正文必须是字符串")
    visible = "\n".join(line for _, line in markdown_lines(value))
    if (headings(value) or re.search(r"<h[1-6]\b", visible, re.I)
            or re.search(r"(?m)^\S[^\n]*\n {0,3}(?:={3,}|-{3,})\s*$", visible)):
        raise ValueError("正文不能自行生成标题；请使用subsections声明子节")
    if "abr-design" in visible or "design-" in visible and "<a" in visible:
        raise ValueError("正文不能声明程序结构标记或锚点")
    return value


def writer_instructions():
    example = {"version": 2, "parameters": {}, "sections": {
        key: {"body": "本节正文或缺口原因与获取路径，保留[passage_id]引用", "subsections": []}
        for key in SECTION_IDS}}
    return ("\n【程序生成章节契约，取代自行输出Markdown标题】\n"
            "本次只返回一个JSON对象，不加代码围栏。固定五节的ID、顺序与名称为："
            + json.dumps(SECTION_TITLES, ensure_ascii=False) + "。\n"
            "每节必须提供实质正文，或通过subsections提供内容；缺证据须写明原因和验证路径，不能只写待补充。"
            "body可以含表格、列表、段落和文献引用，但不能含Markdown/HTML标题。"
            "可选subsections为数组，每项含id、title、body及可选subsections（最多三层子节）。"
            "子节id使用全篇唯一的英文小写字母开头、字母数字下划线或连字符；title只写名称，不加编号或Markdown。"
            "主标题、子节编号、层级由程序生成。跨节引用使用{{section:目标id}}，例如见{{section:risks}}；"
            "不能自行写见5.2或第2节。正文与表格内容仍须满足全部科研写作要求。\n返回形状："
            + json.dumps(example, ensure_ascii=False))


def parse_writer_output(raw, current_parameters=None):
    """重复键也视作协议错误，不能让JSON解析静默覆盖章节。"""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("章节JSON包含重复字段：" + key)
            result[key] = value
        return result
    data = json.loads(raw, object_pairs_hook=unique)
    text, contract = render_design(data, current_parameters=current_parameters)
    return data, text, contract


def render_design(data, current_parameters=None):
    numeric = isinstance(data, dict) and data.get("version") == 2
    keys = {"version", "sections", "parameters"} if numeric else {"version", "sections"}
    if (not isinstance(data, dict) or set(data) != keys
            or type(data.get("version")) is not int or data["version"] not in (VERSION, 2)
            or not isinstance(data.get("sections"), dict) or set(data["sections"]) != set(SECTION_IDS)):
        raise ValueError("设计稿须声明version=2、parameters及完整且唯一的五个sections字段（旧v1仅用于读取）")
    nodes, seen = [], set()

    def visit(node, key, number, title, depth):
        if not isinstance(node, dict) or set(node) - ({"body", "subsections"} if depth == 0 else {"id", "title", "body", "subsections"}):
            raise ValueError("章节包含未授权字段")
        if depth > 3 or not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) or key in seen:
            raise ValueError("章节ID重复、非法或层级过深")
        if depth and (key in SECTION_IDS or not isinstance(title, str) or not title.strip() or len(title) > 100
                      or re.search(r"[\n\r#<>\[\]{}*_`]|^\s*(?:\d+[.、\s]|[一二三四五六七八九十]+[、.])", title)):
            raise ValueError("子节标题必须是无编号的纯文本，ID不能占用主章节")
        seen.add(key)
        body = _body(node.get("body", ""))
        # 模型输出时只接受稳定ID引用，编号只能由渲染器决定。
        if any(NUMBER_REFERENCE.search(line) for _, line in prose_lines(body)):
            raise ValueError("跨节引用须使用{{section:id}}，不能手写章节编号")
        children = node.get("subsections", [])
        if not isinstance(children, list) or not children and not _content(body):
            raise ValueError("章节缺少实质正文或缺口说明：" + key)
        nodes.append({"id": key, "number": number, "title": title, "level": depth + 2, "body": body})
        for index, child in enumerate(children, 1):
            if not isinstance(child, dict):
                raise ValueError("子节必须是对象")
            visit(child, child.get("id"), number + "." + str(index), child.get("title"), depth + 1)

    for index, key in enumerate(SECTION_IDS, 1):
        visit(data["sections"][key], key, str(index), SECTION_TITLES[key], 0)
    lookup = {node["id"]: node["number"] for node in nodes}
    def reference(match):
        if match[1] not in lookup:
            raise ValueError("跨节引用目标不存在：" + match[1])
        return f"[第 {lookup[match[1]]} 节](#design-{match[1]})"
    parts = [START]
    for node in nodes:
        body = node["body"]
        changes = [(offset + m.start(), offset + m.end(), reference(m))
                   for offset, line in prose_lines(body) for m in REFERENCE.finditer(line)]
        for start, end, value in reversed(changes):
            body = body[:start] + value + body[end:]
        if any("{{section:" in line for _, line in prose_lines(body)):
            raise ValueError("跨节引用语法无效")
        parts += [f'<a id="design-{node["id"]}"></a>',
                  f'{"#" * node["level"]} {node["number"]}. {node["title"]}' if node["level"] == 2
                  else f'{"#" * node["level"]} {node["number"]} {node["title"]}']
        if body.strip():
            parts.append(body.strip())
    parts.append(END)
    text = "\n\n".join(parts)
    if numeric:
        from .numeric_bindings import declarations, render
        text = render(text, declarations(data, current_parameters))
    contract = {"version": VERSION, "headings": [{k: n[k] for k in ("id", "number", "title", "level")} for n in nodes]}
    errors = structure_issues(text, contract)
    if errors:
        raise ValueError("；".join(errors))
    return text, contract


def structure_issues(text, contract=None):
    """新稿验证固定结构；旧稿沿用旧验收，恢复必须走显式入口。"""
    if START not in text and END not in text and not contract:
        return []
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) >= text.index(END):
        return ["设计稿结构边界缺失或重复"]
    region = text.split(START, 1)[1].split(END, 1)[0]
    found = headings(region)
    anchors = [(p, ANCHOR.fullmatch(l)[1]) for p, l in markdown_lines(region) if ANCHOR.fullmatch(l)]
    if len(found) != len(anchors) or not found:
        return ["设计稿标题与程序锚点不对应"]
    errors, manifest, counters, ids = [], [], [], set()
    for i, ((pos, line, level, title), (anchor_pos, key)) in enumerate(zip(found, anchors)):
        numbered = re.fullmatch(r"(\d+(?:\.\d+)*)(?:\.)?\s+(.+)", title)
        if (not numbered or level < 2 or level > 5 or anchor_pos >= pos
                or region[anchor_pos:pos].strip() != f'<a id="design-{key}"></a>'):
            errors.append("设计稿标题编号、层级或锚点非法")
            continue
        depth = level - 2
        if depth > len(counters):
            errors.append("设计稿子节层级跳跃")
            continue
        counters = counters[:depth + 1]
        if len(counters) <= depth:
            counters.append(0)
        counters[depth] += 1
        number = ".".join(map(str, counters))
        if numbered[1] != number:
            errors.append("设计稿章节编号不连续或不属于当前父章节")
        if key in ids:
            errors.append("设计稿章节ID重复")
        ids.add(key)
        if depth == 0 and (counters[0] > 5 or key != SECTION_IDS[counters[0] - 1]
                           or numbered[2] != SECTION_TITLES.get(key)):
            errors.append("设计稿主标题名称或顺序不符合固定五节")
        manifest.append({"id": key, "number": numbered[1], "title": numbered[2], "level": level})
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(region)
        body = region[pos + len(line):end]
        try:
            _body(body)
        except ValueError as exc:
            errors.append(str(exc))
        # 父节可由子节承载内容；叶节不能只有标题或占位文字。
        if i == len(found) - 1 or found[i + 1][2] <= level:
            if not _content(body):
                errors.append("设计稿章节缺少实质正文：" + key)
    if [n["id"] for n in manifest if n["level"] == 2] != list(SECTION_IDS):
        errors.append("设计稿缺少固定主章节")
    if contract is not None and contract != {"version": VERSION, "headings": manifest}:
        errors.append("设计稿标题与已登记结构不一致")
    lookup = {n["id"]: n["number"] for n in manifest}
    visible = "\n".join(line for _, line in prose_lines(region))
    for label, key in re.findall(r"\[([^\]]*)\]\(#design-([^)]*)\)", visible):
        if key not in lookup or label != f"第 {lookup[key]} 节":
            errors.append("设计稿跨节引用不存在或编号失效：" + key)
    if "{{section:" in visible:
        errors.append("设计稿存在未解析的章节引用")
    for match in NUMBER_REFERENCE.finditer(visible):
        if (match[1] or match[2]) not in lookup.values():
            errors.append("设计稿引用了不存在的章节：" + match[0])
    return list(dict.fromkeys(errors))
