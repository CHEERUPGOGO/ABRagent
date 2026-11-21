"""要求层级与固定字段：只验证模型声明，不以领域词典猜测研究目标。"""
import copy
import math
import re
import unicodedata

from .requirement_sources import locate_quotes, source_segments, _unit

HIERARCHY_VERSION = 1


def normalize_rate_definition(item):
    """仅规范用户原句显式的 1 C 电流定义，不把测试倍率或容量推断成定义。"""
    result = copy.deepcopy(item)
    if result.get("kind") != "constraint" or result.get("status", "clear") != "clear":
        return result
    definitions = []
    for quote in source_segments(result):
        text = unicodedata.normalize("NFKC", quote)
        pattern = r"(?<![A-Za-z0-9_.])1\s*C\s*(?:=|定义为|对应)\s*(\d+(?:\.\d+)?)\s*mA\s*/\s*g(?![A-Za-z0-9/_])"
        for match in re.finditer(pattern, text):
            if re.search(r"不(?:采用|使用|按|得)|禁止|不应|并非|不是|假设|若|如果|例如|文献", text[:match.start()]):
                continue
            definitions.append((float(match[1]), quote))
    values = {v for v, _ in definitions}
    if not definitions:
        return result
    if len(values) != 1 or next(iter(values)) <= 0:
        raise ValueError("倍率定义冲突或非正数，不能绑定固定电流密度")
    value, quote = definitions[0]
    quantity = result.get("quantity")
    if quantity is not None:
        if (not isinstance(quantity, dict) or type(quantity.get("value")) not in (int, float)
                or quantity["value"] != value or _unit(quantity.get("unit", "")) != _unit("mA/g")):
            raise ValueError("倍率定义数量与用户原句不一致")
        operator = quantity.get("operator")
        if operator is not None and unicodedata.normalize("NFKC", str(operator)) not in ("=", "等于"):
            raise ValueError("倍率定义不能改成阈值比较")
    explicit_equal = "=" in unicodedata.normalize("NFKC", quote)
    result["quantity"] = dict(quantity or {}, value=value, unit="mA/g", operator="=" if explicit_equal else None)
    declared = item_bindings(result)
    paths = {b["path"] for b in declared}
    if "condition.C_rate_reference_current" not in paths:
        declared.append(dict(path="condition.C_rate_reference_current", value=value, unit="mA/g", source_quote=quote))
    result["bindings"] = declared
    result.pop("binding_status", None)
    result.pop("binding_reason", None)
    return result


def item_bindings(item):
    """旧单字段与新复合字段使用同一入口，重复路径须保持值和单位一致。"""
    rows = item.get("bindings", [])
    if not isinstance(rows, list):
        raise ValueError("固定字段bindings须为数组")
    rows = copy.deepcopy(rows)
    if item.get("binding") is not None:
        rows.insert(0, copy.deepcopy(item["binding"]))
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("固定字段须声明规范path")
        key = row["path"]
        if key in result and any(result[key].get(k) != row.get(k) for k in ("value", "unit")):
            raise ValueError("同一要求固定字段冲突: " + key)
        result.setdefault(key, row)
    return list(result.values())


def normalize_binding(binding, item, goal):
    from .research_contract import normalized
    from .scheme_ledger import validate_field
    from .material_ids import source_matches, canonical_field
    if item["kind"] != "constraint" or item.get("status", "clear") != "clear":
        raise ValueError("只有明确的用户固定约束可以绑定字段")
    row = copy.deepcopy(binding)
    path = row["path"]
    quote = row.get("source_quote", item["quote"])
    if not isinstance(quote, str) or not quote or quote not in goal or not any(quote in q for q in source_segments(item)):
        raise ValueError("固定字段来源必须属于该要求的用户原句")
    value = row.get("value")
    from .scheme_ledger import canonical_unit
    # 数值字段的JSON字符串可规范为数值，仍须逐字验证数值及单位来源。
    if canonical_unit(path) is not None and isinstance(value, str) and re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value.strip()):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("固定值必须是有限数值")
        row["value"] = value
    token = f"{value:g}" if type(value) in (int, float) else str(value)
    present = normalized(token) in normalized(quote) or source_matches(path, value, quote)
    if type(value) in (int, float):
        # 数量不能从化学式系数或其他物理量借来；区间两端共享原文单位。
        numeric = re.escape(token) + (r"(?:\.0+)?" if float(value).is_integer() else "")
        number = r"(?<![A-Za-z0-9_.])" + numeric + r"(?![\d.])"
        text = normalized(quote)
        present = bool(re.search(number, text))
        unit = row.get("unit")
        if unit:
            unit_pattern = re.escape(_unit(unit))
            text = _unit(quote)
            endpoint = r"(?<![A-Za-z0-9_.])" + numeric + r"(?![\d.])"
            suffix = r"(?:[–—~～-]\d+(?:\.\d+)?)?" + unit_pattern + r"(?![A-Za-z0-9/^_])"
            present = present and bool(re.search(endpoint + suffix, text))
    if value is None or not present:
        raise ValueError("固定值及单位必须直接来自用户原句，不能用候选或推断值")
    validate_field(path, dict(row, basis="user_requirement", source=quote))
    if "source_quote" in row:
        row["source_quote"] = quote
    return canonical_field(path, row)


def merge_exact_duplicates(raw):
    """只合并全部语义字段相同的重复声明；同义改写由Planner及Reviewer判断。"""
    import json
    raw = copy.deepcopy(raw)
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        return raw
    rows, seen, aliases = [], {}, {}
    from collections import Counter
    counts = Counter(r.get("id") for r in raw["items"] if isinstance(r, dict) and isinstance(r.get("id"), str))
    keys = ("kind", "subject", "description", "conditions", "quantity", "status", "verification_needed", "evidence_needed", "binding", "bindings", "group_id", "binding_status", "binding_reason")
    for row in raw["items"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or counts[row["id"]] != 1:
            rows.append(row); continue
        signature = json.dumps({k: row.get(k) for k in keys}, sort_keys=True, ensure_ascii=False)
        # 来源工况不同即使模型给出相同描述也不能合并，只允许孤立前置连接词差异。
        quote = str(row.get("quote", "")).strip()
        quote = re.sub(r"^(?:并且|以及|并|及|且)(?=[^，,。；;])", "", quote)
        signature += "\n" + quote
        prior = seen.get(signature)
        if prior is None or prior["id"] == row["id"]:
            rows.append(row); seen[signature] = row; continue
        aliases[row["id"]] = prior["id"]
        extra = prior.get("source_quotes", []) + [row.get("quote", "")] + row.get("source_quotes", [])
        prior["source_quotes"] = list(dict.fromkeys(q for q in extra if q and q != prior.get("quote")))
        prior["related_ids"] = list(dict.fromkeys(prior.get("related_ids", []) + row.get("related_ids", [])))
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("related_ids", []), list):
            row["related_ids"] = list(dict.fromkeys(aliases.get(k, k) for k in row.get("related_ids", []) if aliases.get(k, k) != row.get("id")))
    for link in raw.get("query_links", []) if isinstance(raw.get("query_links", []), list) else []:
        if isinstance(link, dict) and isinstance(link.get("requirement_ids"), list):
            link["requirement_ids"] = list(dict.fromkeys(aliases.get(k, k) for k in link["requirement_ids"]))
    raw["items"] = rows
    return raw


def normalize_groups(raw, goal, items):
    """分组只组织目标，不生成回答/实验责任；父子结构必须无环且原句可定位。"""
    groups = raw.get("groups", [])
    if raw.get("hierarchy_version", 0) not in (0, HIERARCHY_VERSION):
        raise ValueError("未知要求层级版本")
    if not isinstance(groups, list):
        raise ValueError("要求分组groups须为数组")
    result, ids = [], set()
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("id"), str) or not group["id"] or group["id"] in ids:
            raise ValueError("要求分组id缺失或重复")
        if not isinstance(group.get("title"), str) or not group["title"].strip():
            raise ValueError("要求分组缺少标题")
        ids.add(group["id"])
        spans = locate_quotes(group.get("source_quotes"), goal)
        result.append({"id": group["id"], "title": group["title"].strip(), "parent_id": group.get("parent_id"),
                       "source_quotes": [s["quote"] for s in spans], "source_spans": spans})
    by_id = {g["id"]: g for g in result}
    for group in result:
        parent, visited = group["parent_id"], {group["id"]}
        while parent is not None:
            if parent not in by_id or parent in visited:
                raise ValueError("要求分组父级未知或形成循环")
            visited.add(parent)
            parent = by_id[parent]["parent_id"]
    for item in items:
        if item.get("group_id") is not None and item["group_id"] not in ids:
            raise ValueError("要求引用未知分组")
        if raw.get("hierarchy_version") == HIERARCHY_VERSION and item["kind"] != "background" and not item.get("group_id"):
            raise ValueError("层级要求必须声明所属分组")
    return result


def ordered_items(contract):
    """报告共享顺序：按分组树展示，旧无层级清单保持原序。"""
    rows = [r for r in contract.get("items", []) if r["kind"] != "background"]
    groups = contract.get("groups", [])
    ordered = []
    visited = set()
    def visit(parent):
        for group in groups:
            if group.get("parent_id") == parent and group["id"] not in visited:
                visited.add(group["id"])
                ordered.extend(r for r in rows if r.get("group_id") == group["id"])
                visit(group["id"])
    visit(None)
    known = {r["id"] for r in ordered}
    return ordered + [r for r in rows if r["id"] not in known]


def group_title(contract, item):
    groups = {g["id"]: g for g in contract.get("groups", [])}
    titles, key, visited = [], item.get("group_id"), set()
    while key in groups and key not in visited:
        visited.add(key)
        titles.insert(0, groups[key]["title"])
        key = groups[key].get("parent_id")
    return " / ".join(titles)
