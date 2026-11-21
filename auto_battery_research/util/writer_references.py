"""Writer正文引用的受限补丁：精确定位、等值替换及独立语义复核。"""
import copy
import json
import math
import re
import unicodedata

from .design_structure import prose_lines
from .numeric_bindings import TOKEN, declarations, display
from .scheme_ledger import digest

NUMBER = re.compile(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*)", re.S)


def body_entries(data):
    """仅枚举结构化正文，标题、参数说明和来源均没有替换权限。"""
    def visit(node, path):
        if not isinstance(node, dict):
            return
        if isinstance(node.get("body"), str):
            yield path + ["body"], node["body"]
        children = node.get("subsections", [])
        if isinstance(children, list):
            for index, child in enumerate(children):
                yield from visit(child, path + ["subsections", index])
    sections = data.get("sections", {}) if isinstance(data, dict) else {}
    if isinstance(sections, dict):
        for key, node in sections.items():
            yield from visit(node, ["sections", key])


def visible(body):
    """坐标不变地屏蔽代码、HTML、引用及已有模板标记。"""
    chars = [" "] * len(body)
    for offset, line in prose_lines(body):
        chars[offset:offset + len(line)] = line
    text = "".join(chars)
    for match in re.finditer(r"<!--.*?-->|<[^>]*>|\{\{.*?\}\}|\[[^\]]*\](?:\([^)]*\))?|\$[^$]*\$|\\\(.*?\\\)", text, re.S):
        chars[match.start():match.end()] = " " * len(match[0])
    return "".join(chars)


def collect_errors(data, current, first=None):
    """独立扫描字段、所有正文引用与未绑定声明，不因首个错误停止。"""
    issues = []
    def add(code, path, reason):
        item = {"code": code, "path": path, "reason": str(reason)}
        if item not in issues:
            issues.append(item)
    if not isinstance(data, dict):
        return [{"code": "PROTOCOL", "path": [], "reason": str(first or "Writer输出必须为JSON对象")}]
    params = copy.deepcopy(current or {})
    proposed = data.get("parameters")
    if not isinstance(proposed, dict):
        add("PARAMETERS", ["parameters"], "parameters必须是字段到参数的映射")
        proposed = {}
    for key, row in proposed.items():
        try:
            params = declarations({"parameters": {key: row}}, params)
        except (ValueError, TypeError, KeyError) as exc:
            add("PARAMETER", ["parameters", key], exc)
    used = set()
    from .numeric_consistency import audit
    calculation = audit(params)
    for path, body in body_entries(data):
        for _, line in prose_lines(body):
            for token in TOKEN.finditer(line):
                kind, key = token[1], token[2]
                if kind == "param":
                    used.add(key)
                elif key.startswith("calculated."):
                    used.add(key[len("calculated."):])
                try:
                    display(kind, key, params, calculation)
                except (ValueError, TypeError, KeyError) as exc:
                    add("REFERENCE", path, exc)
            if re.search(r"\{\{(?:param|derived):", TOKEN.sub("", line)):
                add("REFERENCE", path, "数值引用语法无效")
    for key in proposed:
        if key not in used:
            add("UNBOUND_PARAMETER", ["parameters", key], "工程提案未在正文中采用：" + key)
    if first is not None:
        details = getattr(first, "issues", None)
        if details:
            for item in details:
                add(item.get("code", "PROTOCOL"), item["path"], item["reason"])
        elif str(first) not in [item["reason"] for item in issues]:
            add("PROTOCOL", [], first)
    return issues


def _text(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def equivalent(field, row, quote, document):
    """仅同值文字、明确单位换算和有显式分母的比例可替换；不猜公式或路线同义词。"""
    value = row.get("value")
    if isinstance(value, str):
        return row.get("quantity_kind") == "text" and _text(quote) == _text(display("param", field, {field: row}))
    if type(value) not in (int, float) or not math.isfinite(value):
        return False
    match = NUMBER.fullmatch(unicodedata.normalize("NFKC", quote.strip()))
    if not match:
        return False
    number, raw_unit = float(match[1]), match[2].strip()
    from .numeric_consistency import normalized_number, unit
    target = copy.deepcopy(row)
    literal = dict(row, value=number, unit=raw_unit)
    # 百分数只能在原声明已有明确比例分母时解释；mol%不等同质量/体积分数。
    if raw_unit in ("%", "mol%", "at%", "wt%", "vol%"):
        kind = row.get("quantity_kind")
        allowed = {"%": {"site_fraction", "mass_fraction", "volume_fraction", "ratio"},
                   "mol%": {"site_fraction"}, "at%": {"site_fraction"},
                   "wt%": {"mass_fraction"}, "vol%": {"volume_fraction"}}
        denominator = row.get("denominator")
        if kind not in allowed[raw_unit] or not isinstance(denominator, str) or not denominator or _text(denominator) not in _text(document):
            return False
        literal["unit"] = "%"
    # 带语义的比例单位不能通过相同字符串回退绕过分母校验。
    if row.get("quantity_kind") in ("site_fraction", "mass_fraction", "volume_fraction") and unit(row.get("unit")) not in ("", "1", "fraction", "%"):
        return False
    aliases = {"圈": "cycles", "cycle": "cycles", "cycles": "cycles", "°C": "℃"}
    target["unit"] = aliases.get(target.get("unit"), target.get("unit"))
    literal["unit"] = aliases.get(literal["unit"], literal["unit"])
    # 摄氏显示是现有渲染器的明确投影，不把温差/升温速率当绝对温度。
    if target.get("unit") == "K" and literal["unit"] == "℃":
        return _text(quote) == _text(display("param", field, {field: row}))
    try:
        left, left_unit = normalized_number(field, target)
        right, right_unit = normalized_number(field, literal)
        return left_unit == right_unit and math.isclose(left, right, rel_tol=1e-12, abs_tol=0)
    except (ValueError, TypeError):
        return False


def apply_references(data, changes, current):
    """补丁只能将唯一原文中的一个完整量值替换为程序生成的param引用。"""
    if not isinstance(changes, list):
        raise ValueError("正文引用补丁references必须是数组")
    if not changes:
        return copy.deepcopy(data), []
    parameters = declarations(data, current)
    eligible = {issue["path"][1] for issue in collect_errors(data, current) if issue["code"] == "UNBOUND_PARAMETER"}
    bodies = {tuple(path): body for path, body in body_entries(data)}
    document = "\n".join(visible(body) for body in bodies.values())
    edits, records = {}, []
    for index, change in enumerate(changes):
        if not isinstance(change, dict) or set(change) != {"field", "body_path", "quote", "value_quote"}:
            raise ValueError("引用补丁只能声明field、body_path、quote和value_quote")
        field, path = change["field"], change["body_path"]
        if not isinstance(field, str) or field not in eligible or parameters[field].get("locked"):
            raise ValueError("只能绑定尚未采用的新声明，不能覆盖已有/锁定字段：" + str(field))
        if not isinstance(path, list) or any(type(p) not in (str, int) for p in path) or tuple(path) not in bodies:
            raise ValueError("引用补丁只能定位既有sections正文")
        body = bodies[tuple(path)]
        quote, value_quote = change["quote"], change["value_quote"]
        if (not isinstance(quote, str) or not quote or not isinstance(value_quote, str) or not value_quote
                or body.count(quote) != 1 or quote.count(value_quote) != 1):
            raise ValueError("引用补丁的上下文和量值必须逐字唯一定位：" + field)
        start = body.index(quote) + quote.index(value_quote)
        end = start + len(value_quote)
        if visible(body)[start:end] != value_quote:
            raise ValueError("不能改写代码、来源、公式标记或已有引用：" + field)
        # 数字不得取化学式、区间、指数或复合单位中的一个子串。
        if type(parameters[field].get("value")) in (int, float):
            tail = body[end:].lstrip(" \t")
            if (start and re.match(r"[A-Za-z0-9_.+\-~～–—/]", body[start - 1])
                    or re.match(r"[A-Za-z0-9_.%/+\-~～–—⁰¹²³⁴⁵⁶⁷⁸⁹⁻℃°μµ]", tail)):
                raise ValueError("须引用完整量值及单位，不能截取公式/范围/单位：" + field)
        elif (value_quote[0].isascii() and value_quote[0].isalnum() and start and body[start - 1].isascii() and body[start - 1].isalnum()
              or value_quote[-1].isascii() and value_quote[-1].isalnum() and end < len(body) and body[end].isascii() and body[end].isalnum()):
            raise ValueError("文字声明不能截取其他材料/路线名称的子串：" + field)
        if not equivalent(field, parameters[field], value_quote, document):
            raise ValueError("原文与参数值、单位或计量分母不能确认等价：" + field)
        spans = edits.setdefault(tuple(path), [])
        if any(start < e and end > s for s, e, _ in spans):
            raise ValueError("引用替换范围重叠，不能复用同一量值")
        replacement = "{{param:" + field + "}}"
        spans.append((start, end, replacement))
        records.append(dict(index=index, field=field, parameter=parameters[field], body_path=path,
                            body=body, start=start, end=end, quote=quote, value_quote=value_quote, replacement=replacement))
    result = copy.deepcopy(data)
    for path, spans in edits.items():
        node = result
        for key in path[:-1]:
            node = node[key]
        body = node[path[-1]]
        for start, end, replacement in sorted(spans, reverse=True):
            body = body[:start] + replacement + body[end:]
        node[path[-1]] = body
    return result, records


def verify_references(client, data, repaired, records, current, state, save):
    """独立核对样品、工况、用途、分母及文献/主方案归属；中断和否决不重发。"""
    if not records:
        return
    from .writer_protocol import decode
    request = {"before": data, "after": repaired, "bindings": records, "current_parameters": current}
    signature = digest(request)
    saved = state.setdefault("reference_review", {})
    if saved.get("input_hash", signature) != signature:
        raise ValueError("引用语义复核基准已变化，拒绝旧审核")
    saved.setdefault("input_hash", signature)
    if "raw_response" not in saved:
        if saved.get("attempted") or not getattr(client, "available", False):
            raise ValueError("引用语义复核未完成，不能自动重发或离线放行")
        saved["attempted"] = True
        save()
        had_single, old_single = hasattr(client, "single_attempt"), getattr(client, "single_attempt", False)
        try:
            client.single_attempt = True
            saved["raw_response"] = client.chat(
                '你独立审核正文引用绑定，只核对绑定语义，不修科学内容，不假定补丁正确。逐项检查同一材料/样品、'
                '工况、主值/目标/上限/实验封顶、比例分母及步骤；文献原值不能绑定主方案，候选/对照不能冒充主组。'
                '同值但语义不明、缺失条件、否定/假设或范围关系被破坏必须supported=false。'
                '返回JSON {"input_hash":"给定哈希","bindings":[{"index":0,"supported":true或false,"reason":"具体原文依据或歧义"}]}，'
                '每个index必须出现一次，不能遗漏。',
                json.dumps(dict(request, input_hash=signature), ensure_ascii=False), temperature=0,
                trace_tag="writer_reference_verify")
            save()
        finally:
            if had_single:
                client.single_attempt = old_single
            else:
                delattr(client, "single_attempt")
    review = decode(saved["raw_response"])
    rows = review.get("bindings") if isinstance(review, dict) else None
    if (not isinstance(review, dict) or review.get("input_hash") != signature or not isinstance(rows, list)
            or len(rows) != len(records) or any(not isinstance(r, dict) or type(r.get("index")) is not int for r in rows)
            or {r["index"] for r in rows} != set(range(len(records)))):
        raise ValueError("引用语义复核缺项、重复或基准不匹配")
    rejected = [records[r["index"]]["field"] + "：" + str(r.get("reason") or "无有效判定依据")
                for r in rows if r.get("supported") is not True or not isinstance(r.get("reason"), str) or not r["reason"].strip()]
    saved["status"] = "REJECTED" if rejected else "ACCEPTED"
    save()
    if rejected:
        raise ValueError("引用语义未通过：" + "；".join(rejected))
