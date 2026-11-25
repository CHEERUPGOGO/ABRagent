"""正文纠错派生的研究字段权限；同一事务内同步，不授予身份或科学状态。"""
from __future__ import annotations

import copy

from .scheme_ledger import digest


def permissions(doc, text, interface):
    """仅签发原绑定来源与精确正文授权片段相交的非空字段。"""
    from .design_repair import blocks
    from .research_source_metadata import records
    from .research_support import FIELDS
    if interface.get("kind"):
        return {}
    units = blocks(text)[1]
    body = units[interface["block_id"]]
    result = {}
    for (collection, key), row in records(doc).items():
        quotes = row.get("source_quotes") or [row.get("source_quote")]
        if not isinstance(quotes, list):
            continue
        support = row.get("field_support")
        if support is None and "source_quote" in row:
            support = {f: [0] for f in FIELDS[collection] if row.get(f)}
        if not isinstance(support, dict):
            continue
        fields = {}
        for field in FIELDS[collection]:
            value, indices = row.get(field), support.get(field)
            if not isinstance(value, str) or not value.strip() or not isinstance(indices, list) or not indices:
                continue
            if any(type(i) is not int or not 0 <= i < len(quotes) or not isinstance(quotes[i], str) for i in indices):
                continue
            bound = list(dict.fromkeys(quotes[i] for i in indices))
            affected = [q for q in bound if q and text.count(q) == 1 and body.count(q) == 1
                        and max(body.index(q), interface["start"]) < min(body.index(q) + len(q), interface["end"])]
            if affected:
                fields[field] = {"old": value, "field_hash": digest(value), "source_quotes": bound,
                                 "affected_quotes": affected}
        if fields:
            result[collection + ":" + key] = {"collection": collection, "id": key,
                "record_hash": digest(row), "fields": fields}
    return result


def merge_permissions(interfaces):
    result = {}
    for interface in interfaces:
        for token, grant in interface.get("research_text_permissions", {}).items():
            previous = result.setdefault(token, copy.deepcopy(grant))
            if previous["record_hash"] != grant["record_hash"]:
                raise ValueError("研究字段权限基准冲突")
            for field, rule in grant["fields"].items():
                if field not in previous["fields"]:
                    previous["fields"][field] = copy.deepcopy(rule)
                else:
                    target = previous["fields"][field]
                    if target["old"] != rule["old"] or target["source_quotes"] != rule["source_quotes"]:
                        raise ValueError("研究字段来源权限冲突")
                    target["affected_quotes"] = list(dict.fromkeys(target["affected_quotes"] + rule["affected_quotes"]))
    return result


def apply_changes(doc, candidate, old_text, new_text, parameters, changes, grants, updates):
    """原值、精确字段、实际正文变更及完整记录全部验收后返回审计项。"""
    from .research_contract import TOKEN
    from .research_records import validate_record
    from .research_source_metadata import records, _source_copy
    from .research_support import FIELDS, normalize
    from .source_metadata import rebase_quote
    available, seen, audit = records(doc), set(), {}
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"collection", "id", "record_hash", "fields"}:
            raise ValueError("研究文本补丁只能提交记录身份、基准和字段补丁")
        collection, key = change["collection"], change["id"]
        if not isinstance(collection, str) or not isinstance(key, str):
            raise ValueError("研究文本记录身份非法")
        identity, token = (collection, key), collection + ":" + key
        grant, original = grants.get(token), available.get((collection, key))
        if not grant or original is None:
            raise ValueError("研究文本记录越权")
        if change["record_hash"] != grant["record_hash"] or digest(original) != grant["record_hash"]:
            raise ValueError("研究文本记录基准已过期")
        fields = change["fields"]
        if not isinstance(fields, list) or not fields:
            raise ValueError("研究文本字段补丁须为非空数组；无需改写时省略research_changes")
        row = copy.deepcopy(updates.get(identity, original))
        quotes = row.get("source_quotes") or [row.get("source_quote", "")]
        support = row.get("field_support") or {f: [0] for f in FIELDS[collection] if row.get(f)}
        row = _source_copy(row, copy.deepcopy(quotes), copy.deepcopy(support))
        changed = audit.setdefault(identity, {})
        for patch in fields:
            if not isinstance(patch, dict) or set(patch) != {"field", "old", "new", "source_quotes"}:
                raise ValueError("研究文本字段补丁只允许field/old/new/source_quotes")
            field = patch["field"]
            if not isinstance(field, str) or field not in grant["fields"] or (collection, key, field) in seen:
                raise ValueError("研究文本字段越权或重复提交")
            rule = grant["fields"][field]
            if patch["old"] != original[field] or digest(patch["old"]) != rule["field_hash"]:
                raise ValueError("研究文本字段原值已过期")
            value = patch["new"]
            if not isinstance(value, str) or not value.strip() or value == patch["old"]:
                raise ValueError("研究文本字段须有实质修改且禁止清空")
            if set(TOKEN.findall(value)) != set(TOKEN.findall(patch["old"])):
                raise ValueError("研究文本补丁不得改绑主方案参数引用")
            residual = TOKEN.sub("", value)
            if "{{" in residual or "}}" in residual:
                raise ValueError("研究文本含非法参数模板引用")
            mapped = {q: rebase_quote(q, old_text, new_text) for q in rule["source_quotes"]}
            affected = {mapped[q] for q in rule["affected_quotes"] if mapped.get(q) and mapped[q] != q}
            requested = patch["source_quotes"]
            if (not isinstance(requested, list) or not 1 <= len(requested) <= 12
                    or any(not isinstance(q, str) or not q.strip() or new_text.count(q) != 1 for q in requested)
                    or not set(requested).issubset({q for q in mapped.values() if q})
                    or not set(requested) & affected):
                raise ValueError("研究文本来源须来自实际修改的获准原句及原绑定依据")
            row[field] = value
            indices = []
            for quote in requested:
                if quote not in row["source_quotes"]:
                    row["source_quotes"].append(quote)
                indices.append(row["source_quotes"].index(quote))
            row["field_support"][field] = indices
            changed[field] = {"before": patch["old"], "after": value, "source_quotes": copy.deepcopy(requested),
                              "authorized_source_quotes": copy.deepcopy(rule["affected_quotes"])}
            seen.add((collection, key, field))
        normalized = normalize(row, collection, candidate, parameters)
        if normalized["source_quotes"] != row["source_quotes"] or normalized["field_support"] != row["field_support"]:
            raise ValueError("研究文本补丁须显式完整列出字段依据，不能自动补源放行")
        validate_record(normalized, collection, candidate, parameters)
        updates[identity] = normalized
    return audit


def review_targets(updates):
    """复核必须逐条绑定本次实际变化，不复用旧支持结论。"""
    return [{"collection": row["collection"], "id": row["id"], "field": field,
             "change_hash": digest([row["collection"], row["id"], field, change]), **copy.deepcopy(change)}
            for row in updates for field, change in row.get("changed_fields", {}).items()]
