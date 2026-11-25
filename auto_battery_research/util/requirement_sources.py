"""要求原文与数量校验：保留书写差异，不凭空添加比较关系或换算物理量。"""
import copy
import math
import re
import unicodedata

SPLIT_VERSION = 2


def locate_quotes(quotes, goal):
    if not isinstance(quotes, list) or not quotes or any(not isinstance(q, str) or not q for q in quotes):
        raise ValueError("原文来源须为非空字符串数组")
    spans = []
    for quote in quotes:
        if goal.count(quote) != 1:
            raise ValueError("附加原文必须可唯一定位；重复原句使用主quote及start定位")
        start = goal.index(quote)
        spans.append({"quote": quote, "start": start, "end": start + len(quote)})
    return spans


def task_record(raw, goal):
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("description"), str) or not raw["description"].strip():
        raise ValueError("总任务缺少描述")
    spans = locate_quotes(raw.get("source_quotes"), goal)
    return {"description": raw["description"].strip(), "source_quotes": [s["quote"] for s in spans], "source_spans": spans}


def source_segments(row):
    return list(dict.fromkeys([row["quote"]] + row.get("source_quotes", []) + row.get("conditions", [])))


def coverage_gaps(items, task, goal):
    covered = [False] * len(goal)
    spans = list((task or {}).get("source_spans", []))
    for row in items:
        spans.append({"start": row["start"], "end": row["end"]})
        spans.extend(row.get("source_spans", []))
        for text in row.get("conditions", []):
            for match in re.finditer(re.escape(text), goal):
                spans.append({"start": match.start(), "end": match.end()})
    for span in spans:
        covered[span["start"]:span["end"]] = [True] * (span["end"] - span["start"])
    return [match[0] for match in re.finditer(r"[^\W_]+", "".join(c if not covered[n] else " " for n, c in enumerate(goal)), re.UNICODE)
            if meaningful_gap(match[0])]


def meaningful_gap(text):
    """仅忽略孤立连接词；否定、条件及研究内容仍交给语义审核。"""
    return bool(text.strip()) and text.strip() not in {"并", "及", "与", "和", "以及", "并且", "且", "及其", "以", "为目标", "and"}


def _unit(text):
    text = unicodedata.normalize("NFKC", text).replace("−", "-").replace("·", "")
    # 兼容目录形式的比值单位；不做数值或量纲换算。
    return re.sub(r"\s+", "", text).replace("_", "/").replace("^", "").casefold()


OPERATORS = {">=": (">=", "≥", "不低于", "至少", "不少于"), "<=": ("<=", "≤", "不超过", "至多", "不高于"),
             ">": (">", "大于", "超过"), "<": ("<", "小于", "低于"), "=": ("=", "等于"), "approx": ("约", "大约", "≈")}


def quantity_record(raw, row):
    if raw is None:
        return None
    value = raw.get("value") if isinstance(raw, dict) else None
    unit = raw.get("unit") if isinstance(raw, dict) else None
    if type(value) not in (int, float) or not math.isfinite(value) or not isinstance(unit, str) or not unit.strip():
        raise ValueError("数量声明缺少有限数值或真实单位")
    wanted = _unit(unit)
    matches = []
    number_re = r"(?<![\d.])([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*"
    for segment in source_segments(row):
        text = unicodedata.normalize("NFKC", segment)
        for match in re.finditer(number_re, text):
            if not math.isclose(float(match[1]), value, rel_tol=1e-12, abs_tol=0):
                continue
            remainder = text[match.end():]
            for end in range(1, len(remainder) + 1):
                if _unit(remainder[:end]) == wanted:
                    # 单位必须完整匹配，不能将mAh/g误读为mA。
                    if end < len(remainder) and re.match(r"[A-Za-z0-9/^_]", remainder[end]):
                        continue
                    matches.append((segment, remainder[:end].strip(), match.start(), match.end()+end))
                    break
    if not matches:
        raise ValueError("数量及比较符号不能由模型补造；数值与单位须在同一原句联合定位")
    operator = raw.get("operator") or None
    if isinstance(operator, str):
        operator = unicodedata.normalize("NFKC", operator).strip()
    canonical = None
    if operator is not None:
        canonical = next((key for key, aliases in OPERATORS.items() if operator == key or operator in aliases), None)
        if canonical is None:
            raise ValueError("未知数量比较关系")
        # 比较关系必须邻接该数量或在同一数量短语中，不能借用其他指标的符号。
        found = False
        for segment, original_unit, start, end in matches:
            prefix = unicodedata.normalize("NFKC", segment)[max(0, start-12):start]
            for alias in OPERATORS[canonical]:
                if re.search(re.escape(alias) + r"\s*$", prefix):
                    if alias in ("<", ">", "=") and prefix.rstrip().endswith(("<=", ">=")):
                        continue
                    found = True
        if not found:
            raise ValueError("数量及比较符号不能由模型补造")
    elif any(re.search(re.escape(alias) + r"\s*$", unicodedata.normalize("NFKC", s)[max(0, start-12):start])
             for s, _, start, _ in matches for aliases in OPERATORS.values() for alias in aliases):
        raise ValueError("原文明确的数量比较关系不得删除")
    result = copy.deepcopy(raw)
    result.update(value=value, unit=unit.strip(), operator=canonical, original_unit=matches[0][1])
    return result
