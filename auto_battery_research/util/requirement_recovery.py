"""需求逐项恢复：通过项锁定，失败项保留原文与未知验证责任。"""
import copy
import re
from collections import Counter


def merge_requirement_patch(original, patch, accepted, goal=None):
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
    for key in ("task", "split_version", "hierarchy_version", "groups"):
        if (key not in merged or merged.get(key) is None) and key in patch:
            merged[key] = copy.deepcopy(patch[key])
    if "groups" in patch:
        merged["groups"] = copy.deepcopy(patch["groups"])
    if "query_links" in patch:
        merged["query_links"] = patch["query_links"]
    if isinstance(patch.get("task"), dict):
        from .requirement_sources import task_record
        previous = original.get("task") or {}
        candidate = copy.deepcopy(patch["task"])
        if isinstance(previous, dict):
            # 已有总任务描述不因修复覆盖而重写；附加原文允许扩充且不能撤下旧来源。
            candidate["description"] = previous.get("description") or candidate.get("description")
            old_quotes, new_quotes = previous.get("source_quotes"), candidate.get("source_quotes")
            if isinstance(old_quotes, list) and isinstance(new_quotes, list):
                candidate["source_quotes"] = list(dict.fromkeys(old_quotes + new_quotes))
        try:
            if goal is not None:
                task_record(candidate, goal)
            merged["task"] = candidate
        except (ValueError, TypeError):
            pass  # 非原文来源不覆盖既有总任务，后续恢复仍披露未覆盖内容。
    return merged


def recover_groups(raw, goal, items):
    """分别验收分组树和条目关联；单项待定不能清空有效树或留下悬空引用。"""
    from .requirement_structure import normalize_groups
    diagnostics, groups = [], []
    rows = raw.get("groups", [])
    rows = rows if isinstance(rows, list) else []
    counts = Counter(g.get("id") for g in rows if isinstance(g, dict) and isinstance(g.get("id"), str))
    for index, group in enumerate(rows):
        try:
            if not isinstance(group, dict) or not isinstance(group.get("id"), str) or counts[group["id"]] != 1:
                raise ValueError("要求分组id缺失或重复")
            node = normalize_groups({"groups": [dict(group, parent_id=None)]}, goal, [])[0]
            parent = group.get("parent_id")
            if parent is not None and not isinstance(parent, str):
                raise ValueError("要求分组父级须为字符串或null")
            node["parent_id"] = parent
            groups.append(node)
        except (ValueError, TypeError, KeyError) as exc:
            diagnostics.append(f"groups[{index}]：{exc}")
    by_id = {g["id"]: g for g in groups}
    invalid = set()
    for group in groups:
        parent, visited = group["parent_id"], {group["id"]}
        while parent is not None:
            if parent not in by_id or parent in visited:
                invalid.add(group["id"])
                diagnostics.append("要求分组父级未知或形成循环：" + group["id"])
                break
            visited.add(parent)
            parent = by_id[parent]["parent_id"]
    groups = [g for g in groups if g["id"] not in invalid]
    ids = {g["id"] for g in groups}
    pending = []
    for item in items:
        old = item.get("group_id")
        if old is not None and old not in ids:
            diagnostics.append("要求分组待恢复：" + item["id"] + "；原分组=" + str(old))
            item.pop("group_id", None)
        if raw.get("hierarchy_version") == 1 and item["kind"] != "background" and not item.get("group_id"):
            pending.append(item)
    if pending:
        # 待解析原文归入显式恢复组，不猜测其科研类别，也不授予已解析状态。
        from .requirement_sources import locate_quotes
        key = "recovery_pending"
        while key in ids:
            key += "_"
        quotes = list(dict.fromkeys(i["quote"] for i in pending))
        if any(goal.count(q) != 1 for q in quotes):
            quotes = [goal]
        groups.append({"id": key, "title": "待解析要求（原文保留）", "parent_id": None,
                       "source_quotes": quotes, "source_spans": locate_quotes(quotes, goal)})
        for item in pending:
            item["group_id"] = key
            diagnostics.append("要求所属分组待确认：" + item["id"])
    return groups, diagnostics


def recover_requirements(raw, goal, queries=(), reason=""):
    from .research_contract import normalize_requirements, fallback_requirements, _id
    raw = raw if isinstance(raw, dict) else {}
    rows = raw.get("items", [])
    rows = rows if isinstance(rows, list) else []
    counts = Counter(r.get("id") for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str))
    items, accepted, diagnostics, mapping = [], [], [], {}
    task_type = raw.get("task_type", "other")
    from .requirement_sources import task_record, SPLIT_VERSION, locate_quotes, meaningful_gap
    task = None
    try:
        task = task_record(raw.get("task"), goal)
        if raw.get("split_version") == SPLIT_VERSION and task is None:
            raise ValueError("新版拆分缺少总任务原文")
    except ValueError as exc:
        diagnostics.append(str(exc))
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
            single.pop("group_id", None)  # 逐项验收时层级引用留到全集校验。
            item = normalize_requirements({"items": [single], "scheme_required": scheme_required}, goal)["items"][0]
            if row.get("group_id"):
                item["group_id"] = row["group_id"]
            if raw.get("hierarchy_version") == 1 and item["kind"] == "constraint":
                from .requirement_structure import item_bindings
                if not item_bindings(item) and item.get("binding_status") != "pending":
                    raise ValueError("固定条件必须绑定规范字段或明确披露待绑定原因")
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
                # 数量错误不删除其他已定位的语义/条件；错误数量本身留空。
                if "数量" in str(exc) and isinstance(row, dict):
                    pending.update({k: copy.deepcopy(row[k]) for k in ("kind", "subject", "description", "conditions", "evidence_needed") if k in row})
                    pending["quantity"] = None
                    try:
                        pending["source_quotes"] = row.get("source_quotes", [])
                        if pending["source_quotes"]:
                            pending["source_spans"] = locate_quotes(pending["source_quotes"], goal)
                    except ValueError:
                        pending.pop("source_quotes", None)
                pending.update(quote=quote, start=start, end=start+len(quote), id=_id(quote, pending["description"], start))
                if isinstance(row, dict) and row.get("group_id"):
                    pending["group_id"] = row["group_id"]
                if raw.get("hierarchy_version") == 1 and row.get("kind") == "constraint":
                    pending.update(kind="constraint", subject=row.get("subject", ""), description=row.get("description", quote))
                    pending.update(binding_status="pending", binding_reason="固定字段解析失败，须复核用户原句：" + str(exc))
                if not any(i["id"] == pending["id"] for i in items):
                    items.append(pending)
                if isinstance(row.get("id"), str) and counts[row["id"]] == 1:
                    mapping[row["id"]] = pending["id"]
    # 补回尚未覆盖的原句；仅定位，不猜测指标、数量或约束语义。
    covered = [False] * len(goal)
    for item in items:
        covered[item["start"]:item["end"]] = [True] * (item["end"]-item["start"])
        for span in item.get("source_spans", []):
            covered[span["start"]:span["end"]] = [True] * (span["end"]-span["start"])
        for condition in item.get("conditions", []):
            for match in re.finditer(re.escape(condition), goal):
                covered[match.start():match.end()] = [True] * (match.end()-match.start())
    for span in (task or {}).get("source_spans", []):
        covered[span["start"]:span["end"]] = [True] * (span["end"]-span["start"])
    for match in re.finditer(r"[^。；;\n]+[。；;]?", goal):
        segments = []
        for position in range(match.start(), match.end()):
            if not covered[position]:
                if not segments or segments[-1][-1]+1 != position:
                    segments.append([])
                segments[-1].append(position)
        for segment in segments:
            # Unicode空白与标点一并去除，再定位实际保留的原句。
            quote = re.sub(r"^[\s，,。；;]+|[\s，,。；;]+$", "", goal[segment[0]:segment[-1]+1])
            if not meaningful_gap(quote) or not re.search(r"[\w\u4e00-\u9fff]", quote):
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
    groups, group_errors = recover_groups(raw, goal, items)
    diagnostics.extend(group_errors)
    diagnostics.extend("固定条件待绑定：" + r["description"] + "；" + r.get("binding_reason", "原因待补") for r in items if r.get("binding_status") == "pending")
    return {"version": 1, "split_version": raw.get("split_version", 1), "hierarchy_version": raw.get("hierarchy_version", 0), "groups": groups, "task": task, "source": goal, "status": "parsed" if not diagnostics else "partial" if accepted else "unparsed",
            "items": sorted(items, key=lambda i: i["start"]), "task_type": task_type,
            "scheme_required": scheme_required, "query_links": links, "diagnostics": list(dict.fromkeys(diagnostics))}, locked
