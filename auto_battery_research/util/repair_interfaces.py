"""问题接口暂存编辑建议；程序按修改范围及参数读写依赖组成事务。"""
import copy
import difflib
import json
import re

from .scheme_ledger import digest


def _overlap(left, right):
    """位置基于同一原稿；同点插入及插入落在替换边界时保守联动。"""
    a, b = left
    c, d = right
    return a <= c <= b if c == d else c <= a <= d if a == b else max(a, c) < min(b, d)


def _transactions(interfaces, footprints=None):
    groups = []
    for iid, item in interfaces.items():
        def related(other):
            if set(interfaces[other]["issue_ids"]) & set(item["issue_ids"]):
                return True  # 同一问题的多处声明不可只修一处。
            if footprints is None:
                return False
            left, right = footprints[iid], footprints[other]
            return (bool(left["writes"] & (right["reads"] | right["writes"]))
                    or bool(right["writes"] & left["reads"])
                    or bool(left["constraints"] & right["constraints"])
                    or other in left["source_dependencies"] or iid in right["source_dependencies"]
                    or item["block_id"] == interfaces[other]["block_id"] and any(
                        _overlap(a, b) for a in left["ranges"] for b in right["ranges"]))
        joined = [g for g in groups if any(related(other) for other in g)]
        union = {iid}
        for group in joined:
            union.update(group)
            groups.remove(group)
        groups.append(union)
    return {"group-" + digest(sorted(g))[:16]: sorted(g) for g in groups}


def _field_keys(path, parameters, scope=None):
    from .numeric_consistency import canonical, context
    row = parameters.get(path, {})
    actual_scope = tuple(scope or context(row))
    keys = {("quantity", canonical(path, row), *actual_scope, row.get("quantity_role", "value"))}
    if actual_scope == context(row):
        keys.add(("path", path))
    return keys


def _constraint_inputs(parameters, calculation):
    """共享硬等式/范围的数值联动须一起校验，来源措辞修改不借此扩为数值依赖。"""
    from .numeric_consistency import canonical, context
    result = []
    for check in calculation["checks"]:
        name, scope = check["id"], tuple(check.get("context", ("main", "main", "[]")))
        paths = []
        if name.endswith(".mass_fraction_sum"):
            comp = name.split(".")[0]
            paths = [comp + "." + part + "_fraction" for part in ("active", "conductive", "binder")]
        elif check.get("declared"):
            paths = check.get("paths", []) + check["declared"].get("paths", [])
        elif ":" in name and "<=" in name:
            quantity, roles = name.split(":", 1)
            paths = [path for path, row in parameters.items()
                     if canonical(path, row) == quantity and context(row) == scope
                     and row.get("quantity_role") in roles.split("<=")]
        if paths:
            result.append(set().union(*(_field_keys(path, parameters, scope) for path in paths)))
    # 电压窗口由审计直接检查，不在checks中；同工况上下限仍有硬依赖。
    windows = {}
    for path, row in parameters.items():
        quantity = canonical(path, row)
        if quantity in ("condition.v_min", "condition.v_max") and row.get("quantity_role", "value") == "value":
            windows.setdefault(context(row), {}).setdefault(quantity, set()).update(_field_keys(path, parameters))
    for endpoints in windows.values():
        if len(endpoints) == 2:
            result.append(set().union(*endpoints.values()))
    return result


def _reads(text, fields, parameters, calculation):
    from .numeric_bindings import occurrences
    keys = set()
    pending = set(fields)
    for _, _, kind, path, _ in occurrences(text):
        if kind == "param":
            pending.add(path)
        else:
            derived = calculation["derived"].get(path, {})
            paths = derived.get("paths") or derived.get("dependencies", [])
            # 未完成计算也保留所有明确替代公式的依赖，不能因缺值而拆开联动修改。
            if not derived.get("paths"):
                paths = list(paths) + [p for alt in derived.get("alternatives", []) for p in alt["dependencies"]]
            for dep in paths:
                keys.update(_field_keys(dep, parameters, derived.get("context")))
    pending.update(p for p in re.findall(r"\b(?:cathode|anode|electrolyte|separator|current_collector|packaging|process|condition)\.[\w.]+", text)
                   if p in parameters)
    visited = set()
    while pending:
        path = pending.pop()
        if not isinstance(path, str) or path in visited:
            continue
        visited.add(path)
        keys.update(_field_keys(path, parameters))
        pending.update(p for p in parameters.get(path, {}).get("ratio_inputs", []) if isinstance(p, str))
    return keys


def _compose_blocks(units, interfaces, supplied, ids):
    writes = {}
    for iid in ids:
        item = interfaces[iid]
        if item.get("kind") in ("research_source", "parameter_source"):
            continue
        writes.setdefault(item["block_id"], []).append((item, supplied[iid]["replacement"]))
    result = {}
    for bid, replacements in writes.items():
        value = units[bid]
        for item, replacement in sorted(replacements, key=lambda p: -p[0]["start"]):
            value = value[:item["start"]] + replacement + value[item["end"]:]
        result[bid] = value
    return result


def _footprints(interfaces, supplied, parameters, issues, units):
    from .numeric_consistency import audit
    calculation = audit(parameters)
    constraints = _constraint_inputs(parameters, calculation)
    issue_map = {item["id"]: item for item in issues}
    result = {}
    for iid, item in interfaces.items():
        proposal = supplied.get(iid, {})
        if item.get("kind") == "parameter_source":
            path = item["path"]
            quote = proposal.get("source_quote")
            quotes = [quote] if isinstance(quote, str) and quote else []
            result[iid] = {"reads": _reads("\n".join(quotes), [path], parameters, calculation),
                "writes": {("source", path)}, "ranges": [], "quotes": quotes, "source_dependencies": set(), "constraints": set()}
            continue
        if item.get("kind") == "research_source":
            grant = item["research_source"]
            quotes = proposal.get("source_quotes", [])
            quotes = [q for q in quotes if isinstance(q, str)] if isinstance(quotes, list) else []
            result[iid] = {"reads": _reads("\n".join(quotes), grant["record"].get("parameter_refs", []), parameters, calculation),
                "writes": {("research_source", grant["collection"], grant["id"])}, "ranges": [], "quotes": quotes,
                "source_dependencies": set(), "constraints": set()}
            continue
        replacement = proposal.get("replacement")
        replacement = replacement if isinstance(replacement, str) else item["original"]
        changes = proposal.get("parameter_changes", [])
        changes = changes if isinstance(changes, list) else []
        writes, numeric_writes = set(), set()
        quotes = []
        for change in changes:
            if not isinstance(change, dict) or change.get("path") not in item["fields"]:
                continue  # 越权内容只拒绝所属事务，不允许伪造依赖牵连其他事务。
            path = change["path"]
            field = change.get("field")
            if field is None:
                field = {k: v for k, v in change.items() if k not in ("path", "issue_id")}
            effective = dict(parameters.get(path, {}), **field) if isinstance(field, dict) else None
            if effective is not None and effective != parameters.get(path):
                writes.update(_field_keys(path, parameters))
                if any(effective.get(k) != parameters.get(path, {}).get(k)
                       for k in ("value", "unit", "basis", "conditions", "evidence")):
                    numeric_writes.update(_field_keys(path, parameters))
                if isinstance(effective.get("source_quote"), str) and effective["source_quote"]:
                    quotes.append(effective["source_quote"])
        source_changes = proposal.get("source_changes", [])
        for change in source_changes if isinstance(source_changes, list) else []:
            if isinstance(change, dict) and change.get("path") in item.get("source_fields", {}):
                writes.add(("source", change["path"]))
                if isinstance(change.get("source_quote"), str) and change["source_quote"]:
                    quotes.append(change["source_quote"])
        fields = item["fields"] + [p for i in item["issue_ids"] for p in issue_map[i].get("parameter_paths", [])]
        ranges = [(item["start"] + a, item["start"] + b)
                  for tag, a, b, _, _ in difflib.SequenceMatcher(None, item["original"], replacement, autojunk=False).get_opcodes()
                  if tag != "equal"]
        result[iid] = {"reads": _reads("\n".join([item["original"], replacement, *quotes]), fields, parameters, calculation),
                       "writes": writes, "ranges": ranges, "quotes": quotes, "source_dependencies": set(),
                       "constraints": {i for i, keys in enumerate(constraints) if keys & numeric_writes}}
        # 同记录的正文与摘要或来源修改共用事务，不能分别提交相互覆盖的副本。
        result[iid]["writes"].update(("research_source", g["collection"], g["id"])
                                    for g in item.get("research_text_permissions", {}).values())
    # 参数原句若依赖另一接口的新文字，两者须一并提交；仅同段不构成依赖。
    valid = {i: row for i, row in supplied.items() if isinstance(row.get("replacement"), str)}
    preview = _compose_blocks(units, interfaces, valid, valid)
    for iid, footprint in result.items():
        for quote in footprint["quotes"]:
            for bid, body in units.items():
                for match in re.finditer(re.escape(quote), body):
                    footprint["source_dependencies"].update(other for other, item in interfaces.items()
                        if item["block_id"] == bid and any(_overlap(match.span(), span) for span in result[other]["ranges"]))
            for bid, body in preview.items():
                for match in re.finditer(re.escape(quote), body):
                    shift = 0
                    for other, item in sorted(((i, interfaces[i]) for i in valid if interfaces[i]["block_id"] == bid),
                                              key=lambda pair: pair[1]["start"]):
                        new = valid[other]["replacement"]
                        for tag, _, _, start, end in difflib.SequenceMatcher(None, item["original"], new, autojunk=False).get_opcodes():
                            if tag != "equal" and _overlap((match.start(), match.end()), (item["start"] + shift + start, item["start"] + shift + end)):
                                footprint["source_dependencies"].add(other)
                        shift += len(new) - len(item["original"])
    return result


def build_interfaces(text, parameters, issues, permit, research_doc=None):
    from .design_repair import blocks, issue_quotes
    units = blocks(text)[1]
    spans, unresolved, metadata = {}, [], {}
    for issue in issues:
        grant = permit["grants"].get(issue["id"])
        if issue.get("source_only"):
            if grant and grant.get("parameter_source_only"):
                for path, source in grant["source_fields"].items():
                    token = "parameters:" + path
                    item = metadata.setdefault(token, {"kind": "parameter_source", "block_id": "", "start": 0, "end": 0,
                        "original": "", "fields": [], "source_fields": {path: source}, "issue_ids": [], "path": path})
                    item["issue_ids"].append(issue["id"])
            elif not grant or not grant.get("research_sources"):
                unresolved.append(issue["id"])
            else:
                for token, source in grant["research_sources"].items():
                    item = metadata.setdefault(token, {"kind": "research_source", "block_id": "", "start": 0, "end": 0,
                        "original": "", "fields": [], "source_fields": {}, "issue_ids": [], "permission": token, "research_source": source})
                    item["issue_ids"].append(issue["id"])
            continue
        quotes = issue_quotes(issue)
        matches = [[(bid, value.index(quote)) for bid, value in units.items() if value.count(quote) == 1] for quote in quotes]
        if not grant or not quotes or any(len(m) != 1 for m in matches):
            unresolved.append(issue["id"])
            continue
        primary = set()
        for quote, found in zip(quotes, matches):
            bid, start = found[0]
            primary.add(bid)
            spans.setdefault(bid, []).append({"start": start, "end": start + len(quote),
                "issue_ids": [issue["id"]], "fields": grant["fields"], "source_fields": grant.get("source_fields", {})})
        for dep in grant["blocks"]:
            if dep not in primary:
                # 显式字段引用的依赖段落纳入同一事务；不自动扩展到其他章节。
                spans.setdefault(dep, []).append({"start": 0, "end": len(units[dep]),
                    "issue_ids": [issue["id"]], "fields": grant["fields"], "source_fields": grant.get("source_fields", {})})
    interfaces = {}
    for bid, entries in spans.items():
        merged = []
        for span in sorted(entries, key=lambda s: (s["start"], s["end"])):
            if merged and span["start"] < merged[-1]["end"]:
                prev = merged[-1]
                prev["end"] = max(prev["end"], span["end"])
                prev["issue_ids"] = sorted(set(prev["issue_ids"] + span["issue_ids"]))
                prev["fields"] = sorted(set(prev["fields"] + span["fields"]))
                from .source_metadata import merge_permissions
                prev["source_fields"] = merge_permissions([prev, span])
            else:
                merged.append(copy.deepcopy(span))
        for span in merged:
            iid = "interface-" + digest([permit["base_hash"], bid, span])[:16]
            interfaces[iid] = dict(span, block_id=bid, original=units[bid][span["start"]:span["end"]])
    for token, item in metadata.items():
        iid = "source-interface-" + digest([permit["base_hash"], token, item])[:16]
        interfaces[iid] = item
    if research_doc:
        from .research_text_repair import permissions
        for item in interfaces.values():
            grants = permissions(research_doc, text, item)
            if grants:
                item["research_text_permissions"] = grants
    return {"schema_version": 3, "base_hash": permit["base_hash"], "parameters_hash": permit["parameters_hash"],
        "interfaces": interfaces, "transactions": _transactions(interfaces), "unresolved": unresolved,
        "transaction_policy": "同一问题或重叠范围原子提交；补丁返回后按实际修改范围、参数读写及来源依赖重分组，同段独立修改可分别接受"}


def normalize_submissions(patch, contract, text):
    """旧整段补丁只在目标唯一且没有修改授权原句之外文字时转换。"""
    if not isinstance(patch, dict):
        raise ValueError("PATCH_FORMAT_INVALID: 需要JSON对象")
    if set(patch) == {"format"}:
        patch = patch["format"]
    if not isinstance(patch, dict):
        raise ValueError("PATCH_FORMAT_INVALID: format包装中必须为对象")
    if patch.get("base_hash") != contract["base_hash"]:
        raise ValueError("补丁基准版本过期或缺失")
    if patch.get("parameters_hash", contract["parameters_hash"]) != contract["parameters_hash"]:
        raise ValueError("参数基准版本过期")
    if "submissions" in patch:
        if set(patch) - {"base_hash", "parameters_hash", "submissions"}:
            raise ValueError("补丁包含未授权顶层字段")
        if not isinstance(patch["submissions"], list):
            raise ValueError("PATCH_FORMAT_INVALID: submissions须为数组")
        return patch["submissions"]
    if set(patch) - {"base_hash", "parameters_hash", "edits", "parameters"}:
        raise ValueError("不兼容的旧补丁")
    from .design_repair import blocks
    units = blocks(text)[1]
    converted = []
    assigned = set()
    for edit in patch.get("edits", []):
        matches = [(iid, item) for iid, item in contract["interfaces"].items()
                   if item["block_id"] == edit.get("block_id") and edit.get("issue_id") in item["issue_ids"]]
        if len(matches) != 1:
            raise ValueError("旧整段补丁不能唯一对应接口")
        iid, item = matches[0]
        if iid in assigned:
            raise ValueError("旧补丁重复整段版本不可猜测合并")
        assigned.add(iid)
        old, new = units[item["block_id"]], edit.get("text", "")
        before, after = old[:item["start"]], old[item["end"]:]
        if not isinstance(new, str) or not new.startswith(before) or (after and not new.endswith(after)) or len(new) < len(before) + len(after):
            raise ValueError("旧整段补丁改变接口范围外文字")
        changes = [c for c in patch.get("parameters", []) if c.get("issue_id") == edit.get("issue_id")]
        converted.append({"interface_id": iid, "replacement": new[len(before):len(new)-len(after) if after else None], "parameter_changes": changes})
    if any(not any(c in s["parameter_changes"] for s in converted) for c in patch.get("parameters", [])):
        raise ValueError("旧参数补丁没有对应正文接口")
    return converted


def stage_groups(text, parameters, permit, contract, submissions, research_doc=None):
    from .design_repair import blocks, apply_patch, issue_quotes
    from .numeric_consistency import audit
    units = blocks(text)[1]
    calculation = audit(parameters)
    supplied, duplicates = {}, set()
    for row in submissions:
        if not isinstance(row, dict) or row.get("interface_id") not in contract["interfaces"]:
            raise ValueError("未知或伪造修复接口")
        iid = row["interface_id"]
        if iid in supplied:
            duplicates.add(iid)
        supplied[iid] = row
    footprints = _footprints(contract["interfaces"], supplied, parameters, contract["issues"], units)
    contract["transactions"] = _transactions(contract["interfaces"], footprints)
    contract["modification_footprints"] = {iid: {
        "ranges": item["ranges"], "read_parameters": sorted(key[1] for key in item["reads"] if key[0] == "path"),
        "written_parameters": sorted(key[1] for key in item["writes"] if key[0] == "path"),
        "source_dependencies": sorted(item["source_dependencies"]), "numeric_constraints": sorted(item["constraints"])}
        for iid, item in footprints.items()}
    candidates, diagnostics = {}, {}
    issue_map = {i["id"]: i for i in contract["issues"]}
    for gid, ids in contract["transactions"].items():
        try:
            if any(i not in supplied or i in duplicates for i in ids):
                raise ValueError("事务接口未收齐或重复提交")
            # 未授权问题只保护其实际范围与字段依赖，不冻结整段的无关修改。
            for unresolved in contract["unresolved"]:
                item = issue_map[unresolved]
                reads = _reads("\n".join(issue_quotes(item)), item.get("parameter_paths", []), parameters, calculation)
                if any(footprints[i]["writes"] & reads or any(
                        _overlap(span, match.span()) for span in footprints[i]["ranges"]
                        for quote in issue_quotes(item) for match in re.finditer(re.escape(quote), units.get(contract["interfaces"][i]["block_id"], "")))
                       for i in ids):
                    raise ValueError("修改涉及未定位或未授权问题的范围或参数依赖")
            writes, changes, field_values, sources, source_values = {}, [], {}, [], {}
            research_changes, research_grants = [], {}
            research_text_changes = []
            from .research_text_repair import merge_permissions as merge_research_permissions
            research_text_grants = merge_research_permissions(contract["interfaces"][i] for i in ids)
            for iid in ids:
                interface, row = contract["interfaces"][iid], supplied[iid]
                if interface.get("kind") == "parameter_source":
                    if set(row) != {"interface_id", "source_quote"}:
                        raise ValueError("参数来源接口只能提交source_quote，不能修改正文或参数其他属性")
                    key = interface["path"]
                    if key in source_values and source_values[key] != row["source_quote"]:
                        raise ValueError("同一来源字段提交冲突摘录")
                    if key not in source_values:
                        source_values[key] = row["source_quote"]
                        sources.append({"issue_id": gid, "path": key, "source_quote": row["source_quote"]})
                    continue
                if interface.get("kind") == "research_source":
                    if set(row) != {"interface_id", "source_quotes", "field_support"}:
                        raise ValueError("研究来源接口只能提交source_quotes和field_support，不能修改正文或科研字段")
                    token = interface["permission"]
                    research_grants[token] = interface["research_source"]
                    research_changes.append({"permission": token, "source_quotes": row["source_quotes"], "field_support": row["field_support"]})
                    continue
                if set(row) - {"interface_id", "replacement", "parameter_changes", "source_changes", "research_changes"}:
                    raise ValueError("接口不能修改位置或授权信息")
                replacement = row.get("replacement")
                if not isinstance(replacement, str) or not replacement.strip():
                    raise ValueError("接口替换为空，禁止删除")
                writes.setdefault(interface["block_id"], []).append((interface, replacement))
                summary_changes = row.get("research_changes", [])
                if not isinstance(summary_changes, list):
                    raise ValueError("研究文本补丁须为数组")
                # 每条提交仅使用本接口签发的字段，不能借同事务其他接口扩权。
                local_grants = interface.get("research_text_permissions", {})
                for change in summary_changes:
                    token = (str(change.get("collection")) + ":" + str(change.get("id"))) if isinstance(change, dict) else ""
                    grant = local_grants.get(token)
                    if not grant or not isinstance(change.get("fields"), list) or any(
                            not isinstance(f, dict) or not isinstance(f.get("field"), str) or f["field"] not in grant["fields"]
                            for f in change["fields"]):
                        raise ValueError("研究文本补丁超出所属正文接口字段权限")
                research_text_changes.extend(summary_changes)
                for change in row.get("parameter_changes", []):
                    if change.get("path") not in interface["fields"]:
                        raise ValueError("接口参数越权")
                    key = change["path"]
                    field = change.get("field")
                    if field is None:
                        field = {k: v for k, v in change.items() if k not in ("path", "issue_id")}
                    if key in field_values and field_values[key] != field:
                        raise ValueError("同一参数提交冲突值")
                    if key not in field_values:
                        field_values[key] = field
                        changes.append({"issue_id": gid, "path": key, "field": field})
                source_changes = row.get("source_changes", [])
                if not isinstance(source_changes, list):
                    raise ValueError("来源补丁须为数组")
                for change in source_changes:
                    if (not isinstance(change, dict) or set(change) != {"path", "source_quote"}
                            or change.get("path") not in interface.get("source_fields", {})):
                        raise ValueError("来源补丁只能修改获准字段的source_quote")
                    key = change["path"]
                    if key in source_values and source_values[key] != change["source_quote"]:
                        raise ValueError("同一来源字段提交冲突摘录")
                    if key not in source_values:
                        source_values[key] = change["source_quote"]
                        sources.append(dict(change, issue_id=gid))
            group_fields = sorted({f for i in ids for f in contract["interfaces"][i]["fields"]})
            from .source_metadata import merge_permissions
            synthetic_permit = dict(permit, grants={gid: {"blocks": list(writes), "fields": group_fields,
                "source_fields": merge_permissions(contract["interfaces"][i] for i in ids)}})
            edits = []
            for bid, replacements in writes.items():
                value = units[bid]
                for item, replacement in sorted(replacements, key=lambda p: -p[0]["start"]):
                    value = value[:item["start"]] + replacement + value[item["end"]:]
                edits.append({"issue_id": gid, "block_id": bid, "text": value})
            patch = {"base_hash": permit["base_hash"], "edits": edits, "parameters": changes, "sources": sources}
            merged, fields = apply_patch(text, parameters, synthetic_permit, patch)
            from .research_source_metadata import prepare
            proposed_research, research_updates = prepare(research_doc, text, merged, fields, research_changes, research_grants,
                text_changes=research_text_changes, text_grants=research_text_grants)
            candidates[gid] = {"patch": patch, "permit": synthetic_permit, "text": merged, "parameters": fields, "interface_ids": ids,
                "issue_ids": sorted({issue for i in ids for issue in contract["interfaces"][i]["issue_ids"]}),
                "research_doc": proposed_research, "research_updates": research_updates,
                "research_changes": research_changes, "research_grants": research_grants,
                "research_text_changes": research_text_changes, "research_text_grants": research_text_grants,
                "blocks": {bid: value for bid, value in blocks(merged)[1].items() if units.get(bid) != value}}
        except (ValueError, TypeError, KeyError) as exc:
            diagnostics[gid] = {"status": "REJECTED", "reason": str(exc)}
    return candidates, diagnostics


def _combine(text, permit, contract, submissions, candidates):
    """同段获准片段基于原始坐标一次合成，禁止拼接多个完整段落版本。"""
    from .design_repair import blocks
    combined_permit = dict(permit, grants={})
    combined = {"base_hash": permit["base_hash"], "edits": [], "parameters": [], "sources": []}
    ids = [iid for candidate in candidates for iid in candidate["interface_ids"]]
    supplied = {row["interface_id"]: row for row in submissions}
    writes = _compose_blocks(blocks(text)[1], contract["interfaces"], supplied, ids)
    for candidate in candidates:
        combined_permit["grants"].update(candidate["permit"]["grants"])
        combined["parameters"] += candidate["patch"]["parameters"]
        combined["sources"] += candidate["patch"].get("sources", [])
    for bid, value in writes.items():
        owners = [candidate for candidate in candidates if any(contract["interfaces"][i]["block_id"] == bid for i in candidate["interface_ids"])]
        grant_id = "merge-" + digest([bid, [c["interface_ids"] for c in owners]])[:16]
        fields = sorted({f for candidate in owners for i in candidate["interface_ids"] for f in contract["interfaces"][i]["fields"]})
        combined_permit["grants"][grant_id] = {"blocks": [bid], "fields": fields}
        combined["edits"].append({"issue_id": grant_id, "block_id": bid, "text": value})
    return combined_permit, combined


def _research_preview(doc, text, merged, parameters, candidates):
    from .research_source_metadata import prepare
    changes = [r for c in candidates for r in c.get("research_changes", [])]
    grants = {k: v for c in candidates for k, v in c.get("research_grants", {}).items()}
    text_changes = [r for c in candidates for r in c.get("research_text_changes", [])]
    from .research_text_repair import merge_permissions
    text_grants = merge_permissions({"research_text_permissions": c.get("research_text_grants", {})} for c in candidates)
    return prepare(doc, text, merged, parameters, changes, grants, text_changes=text_changes, text_grants=text_grants)


def execute(writer, verifier, text, parameters, permit, issues, evidence, context="", geometry_validator=None, feedback=None, evidence_selection=None, research_doc=None):
    contract = build_interfaces(text, parameters, issues, permit, research_doc)
    contract["issues"] = issues
    if not contract["interfaces"]:
        return text, parameters, {"status": "SCOPE_UNRESOLVED", "interfaces": contract}
    from .design_repair import blocks
    from .adaptive_retrieval import evidence_package
    package, evidence_selection = (evidence, evidence_selection) if evidence_selection is not None else evidence_package(evidence, text=text, issues=issues)
    prompt = {"read_only_context": context, "draft_blocks": blocks(text)[1], "previous_failures": feedback or [],
        "permission": permit, "interface_contract": contract, "parameters": parameters, "evidence": package, "evidence_context": evidence_selection,
        "parameter_change_format": {"path": "只能取接口fields中的字段", "field": {
            "value": "按问题和证据填写，数值须用JSON数字", "unit": "沿用规范单位", "basis": "engineering_proposal或literature",
            "source": "真实来源描述", "source_quote": "合成后的正文中逐字存在的声明", "passage_id": "literature必须为已归档段落ID"}},
        "source_change_format": {"path": "只能取接口source_fields中的字段", "source_quote": "授权blocks中唯一存在且含该字段abr-param引用的当前原句"},
        "source_instruction": "正文改写后，程序按已定位编辑同步受影响的工程参数source_quote，并将同步后的候选参数交复核。旧来源已经失效时用source_changes精确重绑；此权限只能改source_quote，不能改value/unit/source/basis/conditions/锁定。没有来源问题且未受正文修改影响的字段保留原记录。",
        "parameter_source_instruction": "kind=parameter_source按已有参数身份签发，不依赖旧来源在当前正文可定位。只提交interface_id和source_quote，不提交replacement/parameter_changes/source_changes。目标字段由接口path固定，新摘录须位于source_fields.blocks且含该字段abr-param引用。",
        "research_source_instruction": "kind=research_source是记录来源专用接口，只提交interface_id、source_quotes、field_support。来源必须逐条唯一定位于当前或同事务候选正文；保留原记录的科研文本、状态、参数引用与文献编号。不能改正文、改参数、添加记录或授予support_review；没有充分来源就保留问题。",
        "research_text_instruction": "正文接口若带research_text_permissions，在同条submission的research_changes中同步纠正与本次实际修改原句关联的摘要。正文纠错导致旧摘要的计算或科学表述不一致时须同步修正，不能只更新来源而保留错误摘要。仅修改授权记录/字段，保留原参数引用、文献、身份、名称、排序、状态和验证程度，不添加记录。source_quotes只用原绑定依据在候选正文中的逐字迁移原句，至少一条须被本事务实际修改；不新增事实或科学支持。无需修改摘要时省略research_changes。正文与摘要同次逐字段独立复核，任一失败整组回滚。",
        "research_change_format": {"collection": "权限中的集合", "id": "原记录ID", "record_hash": "权限中的record_hash",
            "fields": [{"field": "仅授权文本字段", "old": "权限中的old原值", "new": "纠正后的摘要文本",
                        "source_quotes": ["候选正文中完整的原绑定迁移原句，不能自行扩展来源"]}]},
        "numeric_binding_instruction": "abr-param标记及显示值仍受保护；原参数改值只提交已授权parameter_changes。计算方法、代入过程、结果和单位由模型填写并由Reviewer复核，不补固定派生字段、不请求程序重算。授权范围内的旧abr-derived可改为完整计算说明，禁止新增derived标记。文献数字不自动改。",
        "return_object_example_do_not_wrap": {"base_hash": contract["base_hash"], "submissions": [
            {"interface_id": "使用接口清单中的编号", "replacement": "仅替换接口original对应的文字", "parameter_changes": []}]}}
    from .runtime_budgets import repair_options
    attempts = repair_options(getattr(writer, "repair_options", None))["patch_attempts"]
    if not attempts:
        return text, parameters, {"status": "REJECTED", "reason": "补丁生成预算为0", "interfaces": contract}
    for attempt in range(attempts):
        try:
            raw = writer.llm.chat("只返回JSON问题接口补丁。每个接口只提交一次；联合接口须同时解决全部issue_ids。不能提交整段正文或修改范围。", json.dumps(prompt, ensure_ascii=False), temperature=0.1, json_mode=True, trace_tag="writer_patch")
            patch = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            submissions = normalize_submissions(patch, contract, text)
            staged, diagnostics = stage_groups(text, parameters, permit, contract, submissions, research_doc)
            break
        except Exception as exc:
            protocol = isinstance(exc, json.JSONDecodeError) or "PATCH_FORMAT_INVALID" in str(exc)
            if attempt + 1 < attempts and protocol:
                prompt["format_error"] = str(exc)
                continue
            return text, parameters, {"status": "REJECTED", "reason": str(exc), "interfaces": contract, "error_type": "protocol" if protocol else "validation"}
    if staged:
        from .design_repair import apply_patch, blocks
        preview_permit, preview_patch = _combine(text, permit, contract, submissions, list(staged.values()))
        try:
            preview_text, preview_parameters = apply_patch(text, parameters, preview_permit, preview_patch)
            preview_research, _ = _research_preview(research_doc, text, preview_text, preview_parameters, list(staged.values()))
            preview = {"paragraphs": {bid: value for bid, value in blocks(preview_text)[1].items()
                                       if prompt["draft_blocks"].get(bid) != value}, "parameters": preview_parameters,
                       "research": (preview_research or {}).get("research")}
        except (ValueError, TypeError, KeyError) as exc:
            preview = {"validation_error": str(exc)}
        staged_issue_ids = {issue_id for candidate in staged.values() for issue_id in candidate["issue_ids"]}
        request = {"issues": [issue for issue in issues if issue["id"] in staged_issue_ids],
            "transactions": {g: {"issue_ids": c["issue_ids"], "paragraphs": c["blocks"], "parameter_changes": c["patch"]["parameters"],
                "source_changes": c["patch"].get("sources", []),
                "parameters": {path: row for path, row in c["parameters"].items() if row != parameters.get(path)},
                "parameters_scope": "本事务实际变化字段；其他字段沿用original_parameters"} for g, c in staged.items()},
            "combined_preview": preview, "parameters": preview.get("parameters", parameters), "original_parameters": parameters,
            "original_blocks": prompt["draft_blocks"], "evidence": package, "evidence_context": evidence_selection, "context": context}
        from .source_metadata import parameter_updates
        from .research_text_repair import review_targets
        for gid, candidate in staged.items():
            request["transactions"][gid]["research_source_updates"] = candidate["research_updates"]
            request["transactions"][gid]["research_text_changes"] = review_targets(candidate["research_updates"])
            request["transactions"][gid]["source_updates"] = parameter_updates(parameters, candidate["parameters"])
        try:
            from .model_calculations import REVIEW_INSTRUCTIONS
            raw = verifier.chat(REVIEW_INSTRUCTIONS + '只复核transactions列出的候选事务，不返回其他问题编号。逐项核对是否解决问题以及是否产生新矛盾，不修改正文。'
                '同段独立事务可分别通过；同时检查combined_preview中的合并效果，跨事务的新矛盾须在new_issues中分别标记所有涉及的group_id。'
                '原有且不受本事务修改影响的问题留给所属事务，不得仅因同段其他问题未解决而否决独立修改。'
                'transactions.parameters及combined_preview.parameters是实际拟提交记录（含程序同步来源），original_parameters仅用于比较修改前状态。'
                '核验当前来源与候选正文、字段对象及工况是否匹配；不能用旧来源否定已同步的新记录。来源修复不授予科学支持，也不修改文献原文或用户条件。'
                '仅来源专用事务（research_text_changes为空）逐字段核对原科研文本是否由新source_quotes及field_support忠实支持，包含材料、数字、工况、否定、文献和验证边界；只定位成功不算支持。旧记录其他非来源问题独立保留，不能要求来源专用接口改写科研文本或状态。'
                '若事务含research_text_changes，以after和combined_preview.research为实际候选，before仅作原稿比较，不得用已纠正的旧摘要否定当前字段。须对每个实际修改字段返回research_verdicts，每项含group_id、collection、id、field、change_hash（照抄该变更）、supported布尔值和reason。逐字段对照before/after、原/新正文及来源，核对计算、材料、数值、工况、因果、否定和验证程度；不得仅因正文原句存在而通过。遗漏或不支持则正文和摘要整组拒绝。此处通过不授予研究记录support_review，正式科学审核仍须重新进行。'
                '研究记录的{{字段路径}}是合法参数引用，由报告在展示时渲染；按parameters的当前值理解，不因保留该绑定而否决，不要求写死数值。记录原status=partial/gap可表达尚待实验验证，来源修复不改状态。'
                'parameters中的locked条件不能改写、否定、换材料或转为仅对照；重复提到同一材料可以，但须核对完整声明仍与锁定值一致。'
                '输出JSON {"verdicts":[{"group_id":"事务编号","issue_id":"问题编号","resolved":true,"reason":"依据"}],"research_verdicts":[],"new_issues":[]}。new_issues每项含group_id和reason。', json.dumps(request, ensure_ascii=False), temperature=0.1, json_mode=True, trace_tag="repair_verify")
            verification = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            verdicts = verification["verdicts"]
            if not isinstance(verdicts, list) or not isinstance(verification.get("new_issues"), list):
                raise ValueError("复核JSON结构无效")
            seen = set()
            for v in verdicts:
                pair = (v["group_id"], v["issue_id"])
                if pair in seen or pair[0] not in staged or pair[1] not in staged[pair[0]]["issue_ids"] or not isinstance(v.get("resolved"), bool) or not v.get("reason"):
                    raise ValueError("复核返回重复或未知问题编号")
                seen.add(pair)
            if any(i.get("group_id") not in staged or not i.get("reason") for i in verification["new_issues"]):
                raise ValueError("新问题缺少有效事务定位")
            expected = {(gid, target["collection"], target["id"], target["field"], target["change_hash"])
                        for gid, transaction in request["transactions"].items() for target in transaction["research_text_changes"]}
            research_verdicts = verification.get("research_verdicts", [])
            if not isinstance(research_verdicts, list):
                raise ValueError("研究文本逐字段复核结构无效")
            research_results = {}
            for result in research_verdicts:
                if not isinstance(result, dict):
                    raise ValueError("研究文本逐字段复核须为对象")
                key = tuple(result.get(k) for k in ("group_id", "collection", "id", "field", "change_hash"))
                if (any(not isinstance(k, str) for k in key) or key not in expected or key in research_results
                        or not isinstance(result.get("supported"), bool) or not isinstance(result.get("reason"), str)
                        or not result["reason"].strip()):
                    raise ValueError("研究文本逐字段复核重复、越权或基准不符")
                research_results[key] = result["supported"]
        except Exception as exc:
            verification = {"error": str(exc)}
            for gid in staged:
                diagnostics[gid] = {"status": "REJECTED", "reason": "针对性复核未完成：" + str(exc)}
        else:
            for gid, candidate in staged.items():
                failed = [i for i in candidate["issue_ids"] if not any(v["group_id"] == gid and v["issue_id"] == i and v["resolved"] for v in verdicts)]
                new = [i for i in verification["new_issues"] if i["group_id"] == gid]
                try:
                    if failed or new:
                        raise ValueError("问题未全部解决或引入新矛盾：" + json.dumps({"failed": failed, "new": new}, ensure_ascii=False))
                    if any(not research_results.get(key, False) for key in expected if key[0] == gid):
                        raise ValueError("研究文本字段未全部获得当前变更的独立支持复核")
                    if geometry_validator:
                        geometry_validator(candidate["parameters"])
                    diagnostics[gid] = {"status": "ACCEPTED"}
                except (ValueError, TypeError, KeyError) as exc:
                    diagnostics[gid] = {"status": "REJECTED", "reason": str(exc)}
    else:
        verification = {"skipped": "没有通过程序校验的事务"}
    selected = [staged[g] for g in staged if diagnostics.get(g, {}).get("status") == "ACCEPTED"]
    from .design_repair import apply_patch
    combined_permit, combined = _combine(text, permit, contract, submissions, selected)
    resolved = {iid for candidate in selected for iid in candidate["issue_ids"]}
    try:
        merged, fields = apply_patch(text, parameters, combined_permit, combined)
        source_research, research_updates = _research_preview(research_doc, text, merged, fields, selected)
        if geometry_validator and fields != parameters:
            geometry_validator(fields)
    except (ValueError, TypeError, KeyError) as exc:
        for value in diagnostics.values():
            if value["status"] == "ACCEPTED":
                value.update(status="ROLLED_BACK", reason=str(exc))
        return text, parameters, {"status": "REJECTED", "reason": "整份一致性检查失败：" + str(exc), "interfaces": contract, "transactions": diagnostics, "verification": verification}
    for value in diagnostics.values():
        if value["status"] == "ACCEPTED":
            value["status"] = "COMMITTED"
    remaining = [i for i in issues if i["id"] not in resolved]
    from .source_metadata import parameter_updates
    return merged, fields, {"status": "APPLIED" if selected and (merged != text or fields != parameters or research_updates) else ("REJECTED" if diagnostics and not selected else "NO_CHANGE"),
        "evidence_context": evidence_selection,
        "permission": permit, "interfaces": contract, "transactions": diagnostics, "verification": verification,
        "submissions": submissions, "remaining_issues": remaining, "resolved_issue_ids": sorted(resolved),
        "before": digest(text), "after": digest(merged), "patch": combined, "source_updates": parameter_updates(parameters, fields),
        "research_source_updates": research_updates, "_research_draft": source_research}
