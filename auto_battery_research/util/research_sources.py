"""研究来源按章节/段落定位；书写与解释性增补可兼容，关键事实变化仍要求重审。"""
import difflib
import re
import unicodedata
from functools import lru_cache

from .scheme_ledger import digest


@lru_cache(maxsize=512)
def plain(text):
    from .research_support import _source_view
    view, offsets, _ = _source_view(text)
    chars, positions = [], []
    for c, offset in zip(view, offsets):
        for normalized in unicodedata.normalize("NFKC", c):
            if not normalized.isspace():
                chars.append(normalized)
                positions.append(offset)
    return "".join(chars), positions


def paragraphs(text):
    """程序章节ID与段序号为定位锚点，内容哈希独立核对。"""
    section, ordinal = "legacy", 0
    for match in re.finditer(r"[^\n]+(?:\n(?!\s*\n)[^\n]+)*", text):
        body = match[0]
        anchor = re.search(r'<a id="design-([^"<>]+)"></a>', body)
        if anchor:
            section, ordinal = anchor[1], 0
            continue
        if body.lstrip().startswith(("#", "<!--")) and not re.search(r"<!-- abr-(?:param|derived):", body):
            continue
        ordinal += 1
        yield {"section_id": section, "paragraph_id": f"{section}:p{ordinal}",
               "start": match.start(), "end": match.end(), "text": body}


def _critical(text):
    # 数字、英文材料/单位、否定和验证程度不可由相似度豁免。
    return re.findall(r"[A-Za-z][A-Za-z0-9_.+/-]*|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|"
                      r"未(?:经|被|能|获|完成|验证)?|不(?:能|得|可|是|含|采用|使用)?|无|禁止|"
                      r"仅|只|必须|可能|有望|尚|假设|推断|已(?:经|验证|证实|证明|达到)|对照|主组|候选", text)


def compatible_quote(quote, candidate):
    """近似只用于定位，无权授予科学审核；事实符号和条件变化不兼容。"""
    left, right = plain(quote)[0], plain(candidate)[0]
    if not left or len(left) < 12:
        return False
    if left == right:
        return True
    if 2 * min(len(left), len(right)) / (len(left) + len(right)) < .86:
        return False
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    if matcher.ratio() < .86:
        return False
    for op, a, b, c, d in matcher.get_opcodes():
        if op == "equal":
            continue
        old, new = left[a:b], right[c:d]
        if old and re.search(r"[\w\u4e00-\u9fff]", old):
            return False  # 替换材料/谓词不能靠字符相似度当作同义改写。
        if _critical(old) or _critical(new) or re.search(r"[<>≤≥=/%℃]", old + new):
            return False
    return True


def locate(quote, text, anchor=None):
    target, _ = plain(quote)
    matches = []
    for block in paragraphs(text):
        if anchor and anchor.get("section_id") and block["section_id"] != anchor["section_id"]:
            continue
        content, positions = plain(block["text"])
        at = content.find(target) if target else -1
        if at >= 0 and content.find(target, at + 1) < 0:
            start, end = block["start"] + positions[at], block["start"] + positions[at + len(target) - 1] + 1
            matches.append((start, end, block, "normalized"))
        elif compatible_quote(quote, block["text"]):
            matches.append((block["start"], block["end"], block, "compatible_revision"))
        else:
            # 列表项/表格行独立定位，邻行不能因相似内容被拼成当前依据。
            offset = block["start"]
            for line in block["text"].splitlines(keepends=True):
                if compatible_quote(quote, line.strip()):
                    start = offset + len(line) - len(line.lstrip())
                    matches.append((start, offset + len(line.rstrip()), block, "compatible_revision"))
                offset += len(line)
    unique = {(a, b): (a, b, block, mode) for a, b, block, mode in matches}
    if len(unique) != 1:
        raise ValueError("来源不能唯一定位或存在材料/数值/条件变化")
    start, end, block, mode = next(iter(unique.values()))
    return {"start": start, "end": end, "quote_hash": digest(text[start:end]), "section_id": block["section_id"],
            "paragraph_id": block["paragraph_id"], "paragraph_hash": digest(plain(block["text"])[0]), "match_mode": mode}


@lru_cache(maxsize=512)
def context_identity(quote, text):
    """只绑定实际引用段落；同段新增限制会失效，无关段落不牵连。"""
    try:
        found = locate(quote, text)
        return found["paragraph_hash"]
    except ValueError:
        return "unlocated:" + digest(quote)
