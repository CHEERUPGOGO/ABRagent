"""从显式叶节点关联投影研究记录；定位不授予科学支持性。"""
from __future__ import annotations

import copy
import re

from .scheme_ledger import digest

VERSION = 1
FIELDS = {"answers": ("response", "reason", "next_step"),
          "candidates": ("summary", "rationale", "risks"), "arguments": ("text",)}


def _parameter(row):
    view = {k: copy.deepcopy(row.get(k)) for k in ("value", "unit", "quantity_kind", "denominator", "conditions", "scope", "group_id", "purpose", "basis", "source", "source_quote", "evidence", "model_reference", "original_value", "canonical_path")}
    view["locked"] = bool(row.get("locked", False))
    if not view["conditions"]:
        view["conditions"] = None
    return view


def field_fingerprint(row, field, doc, parameters):
    source = row.get("field_sources", {}).get(field, [])
    refs = {p for s in source for p in s.get("parameter_refs", [])}
    cites = {p for s in source for p in s.get("citations", [])}
    requirement_ids = row.get("requirement_ids", []) or [row.get("requirement_id")]
    items = [i for i in (doc.get("requirements") or {}).get("items", []) if i.get("id") in requirement_ids]
    return digest({"version": VERSION, "field": field, "text": row.get(field),
        "sources": [{k: s.get(k) for k in ("leaf_id", "quote", "source_text", "parameter_refs", "citations")} for s in source],
        "requirements": items, "parameters": {p: _parameter(parameters.get(p) or {}) for p in sorted(refs)},
        "evidence": sorted([e for e in doc.get("evidence", []) if e.get("passage_id") in cites], key=lambda e: e["passage_id"])})


def fingerprint(row, doc, parameters):
    collection = row.get("projection_collection")
    return digest({"version": VERSION, "identity": {k: row.get(k) for k in ("id", "requirement_id", "name", "scope", "candidate_role", "rank", "requirement_ids", "basis", "status", "verification")},
                   "fields": {f: field_fingerprint(row, f, doc, parameters) for f in FIELDS.get(collection, ()) if row.get(f)}})


def _fragment(item, start, end):
    """源片段通过已编译引用显示定位；禁止在引用标记中间截断。"""
    from .stage4_document import _escape
    source, parts, cursor = item["source_text"], [], start
    for ref in item.get("references", []):
        if ref["end"] <= start or ref["start"] >= end:
            continue
        if ref["start"] < start or ref["end"] > end:
            raise ValueError("正文摘录不能截断引用标记")
        parts += [_escape(source[cursor:ref["start"]], table=item["table"]), ref["display"]]
        cursor = ref["end"]
    parts.append(_escape(source[cursor:end], table=item["table"]))
    return "".join(parts)


def project_research(links, compiled, parameters, requirements, evidence):
    from .stage4_document import diagnostic, ID
    from .research_contract import RESPONSES, VERIFICATIONS
    from .research_records import identity_bindings, validate_selection
    research = {"version": 1, "projection_version": VERSION, "projection_origin": "writer_content_v3",
                "answers": [], "candidates": [], "arguments": [], "diagnostics": [], "record_errors": [],
                "bound_fields": identity_bindings(parameters)}
    issues = []
    required = {i.get("id") for i in requirements.get("items", []) if i.get("kind") != "background"}
    if set(links) - {"answers", "candidates", "arguments", "selected_candidate_id"}:
        issues.append(diagnostic("RESEARCH_LINK_FIELDS", "research_links", "研究关联含未知字段"))
    for collection, fields in FIELDS.items():
        entries = links.get(collection, [])
        if not isinstance(entries, list):
            issues.append(diagnostic("RESEARCH_LINK_INVALID", ["research_links", collection], "研究关联须为数组"))
            continue
        seen = set()
        metadata = {"answers": {"requirement_id", "status", "verification"},
                    "candidates": {"id", "name", "scope", "candidate_role", "rank"},
                    "arguments": {"id", "requirement_ids", "basis"}}[collection]
        for index, original in enumerate(entries):
            location = ["research_links", collection, index]
            row = copy.deepcopy(original) if isinstance(original, dict) else {}
            key = row.get("requirement_id" if collection == "answers" else "id")
            errors = []
            if not isinstance(original, dict) or set(row) - metadata - set(fields):
                errors.append("研究关联含非法字段；文本字段只允许正文引用")
            if not isinstance(key, str) or key in seen or collection != "answers" and not ID.fullmatch(key):
                errors.append("研究记录ID重复或非法")
            else:
                seen.add(key)
            if collection == "answers":
                if key not in required or row.get("status") not in RESPONSES or row.get("verification") not in VERIFICATIONS:
                    errors.append("要求ID、回应或验证状态非法")
                if row.get("verification") in ("experiment", "calculation"):
                    errors.append("Stage 4不能宣称本课题实验或计算已验证")
            elif collection == "candidates":
                if not isinstance(row.get("name"), str) or not row["name"].strip() or row.get("scope") not in ("candidate", "control", "literature"):
                    errors.append("候选名称或scope非法")
            else:
                ids = row.get("requirement_ids")
                if not isinstance(ids, list) or not ids or any(i not in required for i in ids) or row.get("basis") not in ("literature", "inference"):
                    errors.append("论证要求关联或依据类型非法")
            row.update(projection_origin="writer_content_v3", projection_collection=collection, projection_version=VERSION,
                       field_sources={}, field_support={}, source_quotes=[], source_locations=[], parameter_refs=[], citations=[])
            for name in fields:
                references = original.get(name, []) if isinstance(original, dict) else []
                if not isinstance(references, list) or len(references) > 12:
                    errors.append(name + "必须为至多12个正文引用")
                    references = []
                texts, sources, indices = [], [], []
                for ref in references:
                    try:
                        if not isinstance(ref, dict) or set(ref) - {"leaf_id", "quote"} or not isinstance(ref.get("leaf_id"), str):
                            raise ValueError("引用必须是{leaf_id,quote?}")
                        item = compiled.leaves.get(ref["leaf_id"])
                        if not item:
                            raise ValueError("引用叶节点不存在：" + str(ref.get("leaf_id")))
                        source = item["source_text"]
                        quote = ref.get("quote", source)
                        if not isinstance(quote, str) or not quote.strip() or "quote" in ref and source.count(quote) != 1:
                            raise ValueError("quote必须在指定叶节点唯一逐字定位")
                        start = source.index(quote) if "quote" in ref else 0
                        end = start + len(quote)
                        rendered = _fragment(item, start, end)
                        offset = len(_fragment(item, 0, start))
                        absolute = item["start"] + offset
                        if item["start"] < 0 or compiled.markdown[absolute:absolute + len(rendered)] != rendered:
                            raise ValueError("正文真实坐标无法定位")
                        used = [r for r in item["references"] if start <= r["start"] and r["end"] <= end]
                        params = sorted({p for r in used for p in r["parameters"]})
                        cites = sorted({r["target"] for r in used if r["kind"] == "cite"})
                        sources.append({"leaf_id": item["id"], "section_id": item["section_id"], "quote": quote, "source_text": quote,
                            "source_hash": digest(quote), "start": absolute, "end": absolute + len(rendered),
                            "parameter_refs": params, "citations": cites})
                        indices.append(len(row["source_quotes"]))
                        row["source_quotes"].append(rendered)
                        row["source_locations"].append({"start": absolute, "end": absolute + len(rendered), "leaf_id": item["id"]})
                        row["parameter_refs"] += params
                        row["citations"] += cites
                        texts.append(rendered)
                    except (ValueError, KeyError, TypeError) as exc:
                        errors.append(name + "：" + str(exc))
                row[name] = "\n".join(texts)
                row["field_sources"][name] = sources
                row["field_support"][name] = indices
            row["parameter_refs"] = sorted(set(row["parameter_refs"]))
            row["citations"] = sorted(set(row["citations"]))
            if collection == "answers":
                if row.get("status") == "answered" and len(row["response"].strip()) < 8:
                    errors.append("回应缺少实质正文")
                if row.get("status") in ("partial", "gap", "omitted") and (not row["reason"] or not row["next_step"]):
                    errors.append("缺口必须关联原因和下一步")
                if row.get("verification") == "literature" and not row["citations"]:
                    errors.append("文献验证须关联真实引用")
            if collection == "candidates":
                source_text = "\n".join(row["source_quotes"])
                if row.get("name") and row["name"] not in source_text:
                    errors.append("候选名称未出现在关联正文")
                role = row.get("candidate_role")
                if role is not None:
                    from .research_records import CANDIDATE_ROLES
                    if (not isinstance(role, str) or CANDIDATE_ROLES.get(role) != row.get("scope")
                            or not any(row.get("name", "") in q and role in q for q in row["source_quotes"])):
                        errors.append("候选角色须为合法身份且与名称同句有据")
                rank = row.get("rank")
                if rank is not None and (type(rank) is not int or rank < 1 or str(rank) not in source_text):
                    errors.append("候选排序缺少明确正文依据")
            if collection == "arguments" and (not row["text"] or row.get("basis") == "literature" and not row["citations"]):
                errors.append("论证正文或文献来源缺失")
            doc = {"requirements": requirements, "evidence": evidence}
            row["field_fingerprints"] = {f: field_fingerprint(row, f, doc, parameters) for f in fields if row.get(f)}
            row["support_fingerprint"] = fingerprint(row, doc, parameters)
            research[collection].append(row)
            for reason in errors:
                issues.append(diagnostic("RESEARCH_LINK_INVALID", location, reason))
                research["record_errors"].append({"collection": collection, "id": key, "reason": reason})
        if collection == "answers":
            for missing in required - seen:
                issues.append(diagnostic("REQUIREMENT_UNLINKED", "research_links.answers", "要求缺少正文关联：" + missing))
                research["diagnostics"].append("要求缺少正文关联：" + missing)
    selected = links.get("selected_candidate_id")
    research["selected_candidate_id"] = selected
    try:
        validate_selection(selected, research["candidates"], parameters)
    except (ValueError, KeyError, TypeError) as exc:
        issues.append(diagnostic("CANDIDATE_SELECTION", "research_links.selected_candidate_id", str(exc)))
    research["status"] = "partial" if issues or any(a.get("status") != "answered" for a in research["answers"]) else "complete"
    return research, issues


def preserve_support(previous, current, doc, parameters):
    """实际关联字段变更才失效；无关段落和来源坐标移动不撤销支持。"""
    for collection, fields in FIELDS.items():
        old = {r.get("requirement_id" if collection == "answers" else "id"): r for r in previous.get(collection, [])}
        for row in current.get(collection, []):
            prior = old.get(row.get("requirement_id" if collection == "answers" else "id"), {})
            review = prior.get("support_review") or {}
            kept = {}
            for name in fields:
                value = review.get("fields", {}).get(name) or {}
                fp = field_fingerprint(row, name, doc, parameters)
                if value.get("fingerprint") == fp:
                    kept[name] = copy.deepcopy(value)
                elif row.get(name):
                    kept[name] = {"status": "needs_review", "reason": "关联正文、参数或证据已变化，原科学支持失效", "fingerprint": fp}
            row["support_review"] = {"fingerprint": row["support_fingerprint"], "fields": kept}
    return current
