"""研究记录来源专用补丁：固定记录身份及科研内容，仅更新正文来源关联。"""
from __future__ import annotations

import copy
import re

from .scheme_ledger import digest

COLLECTIONS = ("answers", "candidates", "arguments")
ATTRIBUTES = ["source_quotes", "field_support"]


def records(doc):
    from .research_records import is_generated_gap
    research = (doc or {}).get("research") or {}
    result = {}
    for collection in COLLECTIONS:
        for row in research.get(collection, []):
            key = row.get("requirement_id" if collection == "answers" else "id")
            if key and not is_generated_gap(row) and row.get("projection_origin") != "writer_content_v3":
                result[collection, key] = row
    for error in research.get("record_errors", []):
        row = error.get("record")
        collection, key = error.get("collection"), error.get("id")
        if (collection in COLLECTIONS and isinstance(row, dict) and not is_generated_gap(row)
                and row.get("projection_origin") != "writer_content_v3"):
            result.setdefault((collection, key), row)
    return result


def source_problem(row, collection, doc, parameters):
    if not (row.get("source_quote") or row.get("source_quotes")):
        return None
    from .research_support import normalize
    from .research_records import validate_record
    try:
        if "source_quotes" in row:
            normalize(row, collection, doc, parameters)
        else:
            validate_record(row, collection, doc, parameters)
    except (ValueError, TypeError, KeyError) as exc:
        reason = str(exc)
        if any(word in reason for word in ("source_quote", "field_support", "来源", "原句", "支持原句")):
            return reason
    return None


def route_issues(issues, doc, text, parameters, *, excluded=()):
    """对已确认的来源失败签发记录级问题；不为无定位的科学意见制造正文权限。"""
    if not doc:
        return issues
    current = dict(doc, final_answer=text)
    available = records(current)
    excluded = {tuple(key) for key in excluded}
    failures = {key: error for key, row in available.items()
                if (error := source_problem(row, key[0], current, parameters))}
    result, represented = [], set()
    for item in issues:
        row = copy.deepcopy(item)
        requested = row.get("research_sources") or []
        explicit = {(r.get("collection"), r.get("id")) for r in requested if isinstance(r, dict)} if isinstance(requested, list) else set()
        reason = row.get("reason", "") + " " + row.get("suggestion", "")
        if not explicit and not row.get("parameter_paths") and any(w in reason for w in ("source_quote", "来源定位", "来源原句")):
            explicit = {key for key in failures if re.search(r"(?<![\w])" + re.escape(key[1]) + r"(?![\w])", reason)}
        targets = sorted(explicit & set(available))
        if targets and not row.get("parameter_paths"):
            row["research_sources"] = [{"collection": c, "id": key} for c, key in targets]
            row["source_only"] = True
            represented.update(targets)
        result.append(row)
    for (collection, key), reason in failures.items():
        if (collection, key) in represented or (collection, key) in excluded:
            continue
        result.append({"id": "source-" + digest([collection, key])[:16], "type": "research_source", "severity": "medium",
            "source_only": True, "research_sources": [{"collection": collection, "id": key}], "parameter_paths": [],
            "reason": f"{collection}[{key}] 当前来源待修复：{reason}",
            "suggestion": "只更新该记录的source_quotes/field_support，使各字段绑定当前正文唯一原句；科研文本、状态、参数引用及文献编号保留，由独立复核检查支持关系。"})
    return result


def permissions(issue, doc):
    available = records(doc)
    result = {}
    for target in issue.get("research_sources", []):
        key = target.get("collection"), target.get("id")
        row = available.get(key)
        if row is not None and (row.get("source_quote") or row.get("source_quotes")):
            token = key[0] + ":" + key[1]
            result[token] = {"collection": key[0], "id": key[1], "attributes": ATTRIBUTES,
                             "record_hash": digest(row), "record": copy.deepcopy(row)}
    return result


def _source_copy(row, quotes, support):
    result = copy.deepcopy(row)
    result.pop("source_quote", None)
    result.update(source_quotes=quotes, field_support=support)
    for key in ("support_review", "support_fingerprint", "source_locations"):
        result.pop(key, None)
    return result


def prepare(doc, old_text, new_text, parameters, changes=(), grants=None, *, text_changes=(), text_grants=None):
    """构造候选研究视图，来源变更清除旧支持标记，独立审核后才可发布。"""
    if not doc:
        if changes or text_changes:
            raise ValueError("缺少研究来源基准")
        return None, []
    from .research_support import FIELDS, normalize
    from .research_records import collect_research
    from .source_metadata import rebase_quote
    available = records(doc)
    candidate = copy.deepcopy(doc)
    candidate.update(final_answer=new_text, parameters=copy.deepcopy(parameters), ledger_view={"parameters": copy.deepcopy(parameters)})
    if isinstance(candidate.get("scheme_parameters"), dict):
        candidate["scheme_parameters"] = {k: copy.deepcopy(parameters[k]) for k in candidate["scheme_parameters"] if k in parameters}
    from .material_ids import assemble_components
    candidate["scheme"] = assemble_components(doc.get("scheme"), parameters, strict=False)
    updates = {}
    for key, row in available.items():
        quotes = row.get("source_quotes") or [row.get("source_quote", "")]
        if not isinstance(quotes, list) or any(not isinstance(q, str) for q in quotes):
            continue  # 坏格式来源保留原诊断，不能阻塞无关正文的候选构造。
        mapped = [rebase_quote(q, old_text, new_text) or q for q in quotes]
        if mapped != quotes:
            support = row.get("field_support") or {f: [0] for f in FIELDS[key[0]] if row.get(f)}
            updates[key] = _source_copy(row, mapped, copy.deepcopy(support))
    seen = set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"permission", "source_quotes", "field_support"}:
            raise ValueError("研究来源补丁只能选择权限并提交source_quotes/field_support")
        token = change["permission"]
        grant = (grants or {}).get(token)
        if not grant or token in seen or grant.get("attributes") != ATTRIBUTES:
            raise ValueError("研究来源元数据越权或重复提交")
        key = grant["collection"], grant["id"]
        original = available.get(key)
        if original is None or digest(original) != grant["record_hash"]:
            raise ValueError("研究来源记录基准已过期")
        quotes = change["source_quotes"]
        if (not isinstance(quotes, list) or not 1 <= len(quotes) <= 12
                or any(not isinstance(q, str) or not q.strip() or new_text.count(q) != 1 for q in quotes)):
            raise ValueError("研究来源必须逐条唯一定位于候选正文")
        row = _source_copy(original, copy.deepcopy(quotes), copy.deepcopy(change["field_support"]))
        normalized = normalize(row, key[0], candidate, parameters)
        # 授权仅重绑模型明确给出的来源，不靠normalize另补其他原句放行。
        if normalized["source_quotes"] != quotes or normalized["field_support"] != change["field_support"]:
            raise ValueError("研究来源补丁须完整列出所需原句及字段关联")
        # 完整记录在collect_research逐项验收；无关旧错误仍保留，来源补丁不能改状态绕过。
        if (normalized["source_quotes"] != original.get("source_quotes")
                or normalized["field_support"] != original.get("field_support")):
            updates[key] = normalized
        seen.add(token)
    from .research_text_repair import apply_changes
    text_audit = apply_changes(doc, candidate, old_text, new_text, parameters, text_changes, text_grants or {}, updates)
    if not updates:
        return candidate, []
    previous = copy.deepcopy(doc.get("research") or {})
    raw = {c: [] for c in COLLECTIONS}
    audit = []
    for (collection, key), row in updates.items():
        id_field = "requirement_id" if collection == "answers" else "id"
        previous[collection] = [r for r in previous.get(collection, []) if r.get(id_field) != key]
        raw[collection].append(row)
        audit.append({"collection": collection, "id": key, "before": copy.deepcopy(available[collection, key]),
                      "after": copy.deepcopy(row), "reason": "body_summary_correction" if (collection, key) in text_audit
                      else "explicit_source_patch" if collection + ":" + key in seen else "accepted_body_edit"})
        if (collection, key) in text_audit:
            audit[-1]["changed_fields"] = text_audit[collection, key]
    candidate["research"] = collect_research(raw, candidate, parameters, previous=previous)
    for (collection, key) in text_audit:
        id_field = "requirement_id" if collection == "answers" else "id"
        accepted = [r for r in candidate["research"].get(collection, []) if r.get(id_field) == key]
        if (len(accepted) != 1 or any(e.get("collection") == collection and e.get("id") == key
                                    for e in candidate["research"].get("record_errors", []))
                or any(accepted[0].get(f) != updates[collection, key][f] for f in text_audit[collection, key])):
            raise ValueError("研究文本同步记录未通过完整验收，禁止隔离降级后提交")
    return candidate, audit
