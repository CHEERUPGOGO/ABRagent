"""需求逐项恢复：通过项锁定，失败项保留原文与未知验证责任。"""
import copy
import re
from collections import Counter


def merge_requirement_patch(original, patch, accepted):
    """按原id或原句合并局部修复，模型不能在重试时重写通过项。"""
    original = original if isinstance(original, dict) else {}
    patch = patch if isinstance(patch, dict) else {}
    locked = {r["id"]: r for r in accepted}
    proposed = patch.get("items", [])
    proposed = proposed if isinstance(proposed, list) else []
    used, rows = set(), []
    for row in original.get("items", []) if isinstance(original.get("items"), list) else []:
        if isinstance(row, dict) and row.get("id") in locked:
            rows.append(copy.deepcopy(locked[row["id"]]))
            used.update(i for i, p in enumerate(proposed) if isinstance(p, dict) and p.get("id") == row["id"])
            continue
        replacement = next((i for i, p in enumerate(proposed) if i not in used and isinstance(p, dict)
            and isinstance(row, dict) and (row.get("id") is not None and p.get("id") == row.get("id")
                or row.get("quote") and p.get("quote") == row.get("quote"))), None)
        if replacement is None:
            rows.append(copy.deepcopy(row))
        else:
            rows.append(copy.deepcopy(proposed[replacement])); used.add(replacement)
    rows.extend(copy.deepcopy(p) for i, p in enumerate(proposed) if i not in used
                and (not isinstance(p, dict) or p.get("id") not in locked))
    merged = dict(original)
    for key in ("task_type", "scheme_required"):
        # 有效任务类型与是否需要方案不因某个数量字段错误而重置。
        valid = (isinstance(merged.get(key), bool) if key == "scheme_required" else
                 merged.get(key) in ("design", "comparison", "process", "mechanism", "experiment", "query", "other"))
        if not valid:
            merged[key] = patch.get(key, "other" if key == "task_type" else True)
    merged["items"] = rows
    if "query_links" in patch:
        merged["query_links"] = patch["query_links"]
    return merged


def recover_requirements(raw, goal, queries=(), reason=""):
    from .research_contract import normalize_requirements, fallback_requirements, _id
    raw = raw if isinstance(raw, dict) else {}
    rows = raw.get("items", [])
    rows = rows if isinstance(rows, list) else []
    counts = Counter(r.get("id") for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str))
    items, accepted, diagnostics, mapping = [], [], [], {}
    task_type = raw.get("task_type", "other")
    if task_type not in ("design", "comparison", "process", "mechanism", "experiment", "query", "other"):
        task_type = "other"; diagnostics.append("任务类型待复核")
    scheme_required = raw.get("scheme_required", True)
    if not isinstance(scheme_required, bool):
        scheme_required = True; diagnostics.append("是否需要主方案尚待复核")
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or counts[row["id"]] != 1:
                raise ValueError("要求id缺失或重复")
            single = copy.deepcopy(row)
            single["related_ids"] = []
            item = normalize_requirements({"items": [single], "scheme_required": scheme_required}, goal)["items"][0]
            if any(i["id"] == item["id"] for i in items):
                raise ValueError("重复要求")
            items.append(item); accepted.append(copy.deepcopy(row)); mapping[row["id"]] = item["id"]
        except (ValueError, TypeError, KeyError) as exc:
            diagnostics.append(f"items[{index}]：{exc}")
            quote = row.get("quote") if isinstance(row, dict) else None
            if isinstance(quote, str) and quote and quote in goal:
                start = row.get("start")
                if not isinstance(start, int) or goal[start:start+len(quote)] != quote:
                    if goal.count(quote) != 1:
                        continue
                    start = goal.index(quote)
                pending = fallback_requirements(quote)["items"][0]
                pending.update(quote=quote, description=quote, start=start, end=start+len(quote), id=_id(quote, quote, start))
                if not any(i["id"] == pending["id"] for i in items):
                    items.append(pending)
                if isinstance(row.get("id"), str) and counts[row["id"]] == 1:
                    mapping[row["id"]] = pending["id"]
    # 补回尚未覆盖的原句；仅定位，不猜测指标、数量或约束语义。
    covered = [False] * len(goal)
    for item in items:
        covered[item["start"]:item["end"]] = [True] * (item["end"]-item["start"])
    for match in re.finditer(r"[^。；;\n]+[。；;]?", goal):
        segments = []
        for position in range(match.start(), match.end()):
            if not covered[position]:
                if not segments or segments[-1][-1]+1 != position:
                    segments.append([])
                segments[-1].append(position)
        for segment in segments:
            quote = goal[segment[0]:segment[-1]+1].strip(" ，,。；;\n\t")
            if not quote or not re.search(r"[\w\u4e00-\u9fff]", quote):
                continue
            start = goal.index(quote, segment[0])
            pending = fallback_requirements(quote)["items"][0]
            pending.update(start=start, end=start+len(quote), id=_id(quote, quote, start))
            items.append(pending)
            diagnostics.append("未覆盖原文，保留待解析要求：" + quote)
    if not items:
        return fallback_requirements(goal, reason or "未能恢复课题要求"), accepted
    by_id = {item["id"]: item for item in items}
    locked = []
    for row in accepted:
        related = row.get("related_ids", [])
        if not isinstance(related, list) or any(not isinstance(r, str) or r not in mapping for r in related):
            diagnostics.append("要求关联尚待修复：" + row["id"])
        else:
            by_id[mapping[row["id"]]]["related_ids"] = [mapping[r] for r in related]
            locked.append(row)
    links = []
    for link in raw.get("query_links", []) if isinstance(raw.get("query_links", []), list) else []:
        if (isinstance(link, dict) and isinstance(link.get("query_index"), int)
                and 0 <= link["query_index"] < len(queries) and isinstance(link.get("requirement_ids"), list)
                and all(isinstance(i, str) and i in mapping for i in link["requirement_ids"])):
            links.append({"query_index": link["query_index"], "requirement_ids": [mapping[i] for i in link["requirement_ids"]]})
        else:
            diagnostics.append("检索关联尚待修复")
    return {"version": 1, "source": goal, "status": "parsed" if not diagnostics else "partial" if accepted else "unparsed",
            "items": sorted(items, key=lambda i: i["start"]), "task_type": task_type,
            "scheme_required": scheme_required, "query_links": links, "diagnostics": list(dict.fromkeys(diagnostics))}, locked
