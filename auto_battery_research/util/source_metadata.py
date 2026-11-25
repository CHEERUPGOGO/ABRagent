"""正文来源的确定性同步与精确授权；来源更新不授予参数改值或科学通过。"""
from __future__ import annotations

import copy
import difflib

from .scheme_ledger import digest


def mutable_source(row):
    """用户条件和文献原文不是当前草稿的派生摘录，不随正文改写。"""
    return (isinstance(row, dict) and not row.get("locked")
            and row.get("basis") in (None, "engineering_proposal", "budget_assumption")
            and isinstance(row.get("source_quote"), str) and bool(row["source_quote"].strip()))


def references(quote, path):
    from .numeric_bindings import occurrences
    return any(kind == "param" and key == path for _, _, kind, key, _ in occurrences(quote))


def _map_span(old, new, start, end):
    """只按同一原稿的编辑位置投影；截断来源边界的替换不猜测新范围。"""
    if start == 0 and end == len(old):
        return new
    left = right = None
    for tag, a, b, c, d in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            if a <= start <= b:
                left = c + start - a if left is None else left
            if a <= end <= b:
                right = c + end - a
        else:
            if a < start < b or a < end < b:
                return None
            if start == a:
                left = c
            if end == b:
                right = d
    return new[left:right] if left is not None and right is not None and left < right else None


def rebase_quote(quote, old_text, new_text):
    """只有旧稿中唯一定位的原句可自动迁移，不用相似度修补已失效来源。"""
    if not quote or old_text.count(quote) != 1:
        return None
    from .design_repair import blocks
    old_parts, old_blocks = blocks(old_text)
    new_parts, new_blocks = blocks(new_text)
    if len(old_parts) != len(new_parts):
        return None
    owners = [(bid, body.index(quote)) for bid, body in old_blocks.items() if body.count(quote) == 1]
    if len(owners) != 1:
        return None
    bid, start = owners[0]
    if bid not in new_blocks:
        return None
    result = _map_span(old_blocks[bid], new_blocks[bid], start, start + len(quote))
    return result if result and new_text.count(result) == 1 else None


def parameter_permissions(text, parameters, block_ids):
    """只给授权段落中显式引用字段的source_quote属性，不扩大数值写权限。"""
    from .design_repair import blocks
    units = blocks(text)[1]
    result = {}
    for path, row in parameters.items():
        if not mutable_source(row):
            continue
        targets = [bid for bid in block_ids if bid in units and references(units[bid], path)]
        if targets:
            result[path] = {"attributes": ["source_quote"], "source_hash": digest(row["source_quote"]),
                            "blocks": targets}
    return result


def route_source_issues(issues, text, parameters, *, excluded=()):
    """旧来源不在正文时按现有参数身份定位，不为修来源授予正文改写权。"""
    from .design_repair import blocks
    from .numeric_bindings import binding_errors
    available = parameter_permissions(text, parameters, list(blocks(text)[1]))
    stale = {path for path in available if text.count(parameters[path]["source_quote"]) != 1
             or binding_errors(parameters[path]["source_quote"], parameters)}
    result, represented = [], set()
    for issue in issues:
        item = copy.deepcopy(issue)
        requested = item.get("parameter_source_paths")
        if requested is None and (item.get("type") == "source" or "source_quote" in (str(item.get("reason", "")) + str(item.get("suggestion", "")))):
            requested = item.get("parameter_paths", [])
        paths = [p for p in requested if isinstance(p, str) and p in available] if isinstance(requested, list) else []
        if paths and (item.get("parameter_source_paths") is not None or set(paths) & stale):
            item.update(parameter_source_paths=sorted(set(paths)), source_only=True)
            represented.update(paths)
        result.append(item)
    for path in sorted(stale - represented - set(excluded)):
        result.append({"id": "parameter-source-" + digest(path)[:16], "type": "parameter_source", "severity": "medium",
            "source_only": True, "parameter_source_paths": [path], "parameter_paths": [],
            "reason": f"{path} 的source_quote未对应当前正文或引用值", "suggestion": "仅重绑当前参数的正文来源，保留值、单位、来源说明、工况及锁定；新摘录须唯一定位并含该字段当前引用。"})
    return result


def source_permissions(issue, text, parameters):
    from .design_repair import blocks
    available = parameter_permissions(text, parameters, list(blocks(text)[1]))
    return {path: available[path] for path in issue.get("parameter_source_paths", []) if path in available}


def merge_permissions(grants):
    result = {}
    for grant in grants:
        for path, item in grant.get("source_fields", {}).items():
            if path in result and result[path]["source_hash"] != item["source_hash"]:
                raise ValueError("来源权限基准冲突")
            previous = result.setdefault(path, copy.deepcopy(item))
            previous["blocks"] = sorted(set(previous["blocks"]) | set(item["blocks"]))
    return result


def synchronize_parameters(old_text, new_text, before, after):
    """将已接受正文的唯一摘录映射到新稿；保留未获准字段的值、单位及来源说明。"""
    from .numeric_bindings import binding_errors
    for path, old in before.items():
        row = after.get(path)
        if not mutable_source(old) or not isinstance(row, dict):
            continue
        quote = old["source_quote"]
        # 显式来源补丁另行核验，不能被自动同步覆盖。
        if row.get("source_quote") != quote or not references(quote, path):
            continue
        replacement = rebase_quote(quote, old_text, new_text)
        if (replacement and replacement != quote and references(replacement, path)
                and not binding_errors(replacement, after)):
            row["source_quote"] = replacement
    # 获授权的参数补丁可引用旧显示值，沿用程序渲染完成同次改值后的精确摘录。
    from .numeric_bindings import refresh
    for path, row in after.items():
        if not mutable_source(row) or not references(row["source_quote"], path):
            continue
        try:
            refreshed = refresh(row["source_quote"], after)
        except ValueError:
            continue  # 历史失效字段留待精确来源补丁，不阻断无关正文事务。
        if refreshed != row["source_quote"] and new_text.count(refreshed) == 1:
            row["source_quote"] = refreshed


def apply_parameter_sources(text, before, after, grants, changes):
    """模型仅提交获准字段的新原句；当前唯一位置和字段引用均须匹配。"""
    from .design_repair import blocks
    from .numeric_bindings import binding_errors
    units = blocks(text)[1]
    seen = set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"issue_id", "path", "source_quote"}:
            raise ValueError("来源补丁只允许issue_id、path和source_quote")
        path = change["path"]
        grant = grants.get(change["issue_id"], {}).get("source_fields", {}).get(path)
        original = before.get(path, {})
        if path in seen or not grant or not mutable_source(original):
            raise ValueError("来源元数据越权、重复或用户锁定")
        if grant.get("attributes") != ["source_quote"] or grant.get("source_hash") != digest(original["source_quote"]):
            raise ValueError("来源元数据基准已过期")
        quote = change["source_quote"]
        if (not isinstance(quote, str) or not quote.strip() or text.count(quote) != 1
                or not any(quote in units.get(bid, "") for bid in grant["blocks"])
                or not references(quote, path) or binding_errors(quote, after)):
            raise ValueError("来源须唯一定位于授权段落并保留该字段的当前参数引用")
        after[path]["source_quote"] = quote
        seen.add(path)


def parameter_updates(before, after):
    return [{"path": path, "before": row.get("source_quote"), "after": after[path].get("source_quote")}
            for path, row in before.items() if path in after and row.get("source_quote") != after[path].get("source_quote")]


def changed_parameter_fields(before, after):
    """来源位置同步不是物理输入变更；其他属性仍走原参数与几何检查。"""
    return {path for path in set(before) | set(after)
            if {k: v for k, v in before.get(path, {}).items() if k != "source_quote"}
            != {k: v for k, v in after.get(path, {}).items() if k != "source_quote"}}
