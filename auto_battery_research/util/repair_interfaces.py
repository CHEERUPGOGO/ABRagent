"""问题接口暂存编辑建议；程序按段落及参数依赖组成事务。"""
import copy
import json
import re

from .scheme_ledger import digest


def build_interfaces(text, parameters, issues, permit):
    from .design_repair import blocks
    units = blocks(text)[1]
    spans, unresolved = {}, []
    for issue in issues:
        grant = permit["grants"].get(issue["id"])
        quote = issue.get("quote")
        matches = [(bid, value.index(quote)) for bid, value in units.items()
                   if isinstance(quote, str) and quote and value.count(quote) == 1]
        if not grant or len(matches) != 1:
            unresolved.append(issue["id"])
            continue
        bid, start = matches[0]
        spans.setdefault(bid, []).append({"start": start, "end": start + len(quote),
            "issue_ids": [issue["id"]], "fields": grant["fields"]})
        for dep in grant["blocks"]:
            if dep != bid:
                # 显式字段引用的依赖段落纳入同一事务；不自动扩展到其他章节。
                spans.setdefault(dep, []).append({"start": 0, "end": len(units[dep]),
                    "issue_ids": [issue["id"]], "fields": grant["fields"]})
    interfaces = {}
    for bid, entries in spans.items():
        merged = []
        for span in sorted(entries, key=lambda s: (s["start"], s["end"])):
            if merged and span["start"] < merged[-1]["end"]:
                prev = merged[-1]
                prev["end"] = max(prev["end"], span["end"])
                prev["issue_ids"] = sorted(set(prev["issue_ids"] + span["issue_ids"]))
                prev["fields"] = sorted(set(prev["fields"] + span["fields"]))
            else:
                merged.append(copy.deepcopy(span))
        for span in merged:
            iid = "interface-" + digest([permit["base_hash"], bid, span])[:16]
            interfaces[iid] = dict(span, block_id=bid, original=units[bid][span["start"]:span["end"]])
    groups = []
    for iid, item in interfaces.items():
        joined = [g for g in groups if any(
            interfaces[other]["block_id"] == item["block_id"]
            or set(interfaces[other]["fields"]) & set(item["fields"])
            or set(interfaces[other]["issue_ids"]) & set(item["issue_ids"])
            for other in g)]
        union = {iid}
        for g in joined:
            union.update(g)
            groups.remove(g)
        groups.append(union)
    transactions = {"group-" + digest(sorted(g))[:16]: sorted(g) for g in groups}
    return {"schema_version": 1, "base_hash": permit["base_hash"], "parameters_hash": permit["parameters_hash"],
        "interfaces": interfaces, "transactions": transactions, "unresolved": unresolved}


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


def stage_groups(text, parameters, permit, contract, submissions):
    from .design_repair import blocks, apply_patch
    units = blocks(text)[1]
    supplied, duplicates = {}, set()
    for row in submissions:
        if not isinstance(row, dict) or row.get("interface_id") not in contract["interfaces"]:
            raise ValueError("未知或伪造修复接口")
        iid = row["interface_id"]
        if iid in supplied:
            duplicates.add(iid)
        supplied[iid] = row
    candidates, diagnostics = {}, {}
    issue_map = {i["id"]: i for i in contract["issues"]}
    for gid, ids in contract["transactions"].items():
        try:
            if any(i not in supplied or i in duplicates for i in ids):
                raise ValueError("事务接口未收齐或重复提交")
            affected = {contract["interfaces"][i]["block_id"] for i in ids}
            # 可定位到本段但未能签发接口的问题，同样阻止提交该段。
            if any(issue_map[i].get("quote") and any(issue_map[i]["quote"] in units[b] for b in affected) for i in contract["unresolved"]):
                raise ValueError("该段仍有未定位或未授权问题")
            writes, changes, field_values = {}, [], {}
            for iid in ids:
                interface, row = contract["interfaces"][iid], supplied[iid]
                if set(row) - {"interface_id", "replacement", "parameter_changes"}:
                    raise ValueError("接口不能修改位置或授权信息")
                replacement = row.get("replacement")
                if not isinstance(replacement, str) or not replacement.strip():
                    raise ValueError("接口替换为空，禁止删除")
                writes.setdefault(interface["block_id"], []).append((interface, replacement))
                for change in row.get("parameter_changes", []):
                    if change.get("path") not in interface["fields"]:
                        raise ValueError("接口参数越权")
                    key, field = change["path"], change.get("field")
                    if key in field_values and field_values[key] != field:
                        raise ValueError("同一参数提交冲突值")
                    if key not in field_values:
                        field_values[key] = field
                        changes.append({"issue_id": gid, "path": key, "field": field})
            group_fields = sorted({f for i in ids for f in contract["interfaces"][i]["fields"]})
            synthetic_permit = dict(permit, grants={gid: {"blocks": list(writes), "fields": group_fields}})
            edits = []
            for bid, replacements in writes.items():
                value = units[bid]
                for item, replacement in sorted(replacements, key=lambda p: -p[0]["start"]):
                    value = value[:item["start"]] + replacement + value[item["end"]:]
                edits.append({"issue_id": gid, "block_id": bid, "text": value})
            patch = {"base_hash": permit["base_hash"], "edits": edits, "parameters": changes}
            merged, fields = apply_patch(text, parameters, synthetic_permit, patch)
            candidates[gid] = {"patch": patch, "permit": synthetic_permit, "text": merged, "parameters": fields,
                "issue_ids": sorted({issue for i in ids for issue in contract["interfaces"][i]["issue_ids"]}),
                "blocks": {e["block_id"]: e["text"] for e in edits}}
        except (ValueError, TypeError, KeyError) as exc:
            diagnostics[gid] = {"status": "REJECTED", "reason": str(exc)}
    return candidates, diagnostics


def execute(writer, verifier, text, parameters, permit, issues, evidence, context="", geometry_validator=None, feedback=None):
    contract = build_interfaces(text, parameters, issues, permit)
    contract["issues"] = issues
    if not contract["interfaces"]:
        return text, parameters, {"status": "SCOPE_UNRESOLVED", "interfaces": contract}
    from .design_repair import blocks
    prompt = {"read_only_context": context, "draft_blocks": blocks(text)[1], "previous_failures": feedback or [],
        "permission": permit, "interface_contract": contract, "parameters": parameters, "evidence": evidence[:25],
        "parameter_change_format": {"path": "只能取接口fields中的字段", "field": {
            "value": "按问题和证据填写，数值须用JSON数字", "unit": "沿用规范单位", "basis": "engineering_proposal或literature",
            "source": "真实来源描述", "source_quote": "合成后的正文中逐字存在的声明", "passage_id": "literature必须为已归档段落ID"}},
        "return_object_example_do_not_wrap": {"base_hash": contract["base_hash"], "submissions": [
            {"interface_id": "使用接口清单中的编号", "replacement": "仅替换接口original对应的文字", "parameter_changes": []}]}}
    for attempt in range(2):
        try:
            raw = writer.llm.chat("只返回JSON问题接口补丁。每个接口只提交一次；联合接口须同时解决全部issue_ids。不能提交整段正文或修改范围。", json.dumps(prompt, ensure_ascii=False), temperature=0.1, trace_tag="writer_patch")
            patch = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            submissions = normalize_submissions(patch, contract, text)
            staged, diagnostics = stage_groups(text, parameters, permit, contract, submissions)
            break
        except Exception as exc:
            protocol = isinstance(exc, json.JSONDecodeError) or "PATCH_FORMAT_INVALID" in str(exc)
            if attempt == 0 and protocol:
                prompt["format_error"] = str(exc)
                continue
            return text, parameters, {"status": "REJECTED", "reason": str(exc), "interfaces": contract, "error_type": "protocol" if protocol else "validation"}
    if staged:
        request = {"issues": issues, "transactions": {g: {"issue_ids": c["issue_ids"], "paragraphs": c["blocks"], "parameter_changes": c["patch"]["parameters"]} for g, c in staged.items()},
            "parameters": parameters, "original_blocks": prompt["draft_blocks"], "evidence": evidence[:25], "context": context}
        try:
            raw = verifier.chat('只复核本批问题是否在候选段落中解决以及是否产生新矛盾，不修改正文。输出JSON {"verdicts":[{"group_id":"事务编号","issue_id":"问题编号","resolved":true,"reason":"依据"}],"new_issues":[]}。new_issues每项含group_id和reason。', json.dumps(request, ensure_ascii=False), temperature=0.1, trace_tag="repair_verify")
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
                    if geometry_validator:
                        geometry_validator(candidate["parameters"])
                    diagnostics[gid] = {"status": "ACCEPTED"}
                except (ValueError, TypeError, KeyError) as exc:
                    diagnostics[gid] = {"status": "REJECTED", "reason": str(exc)}
    else:
        verification = {"skipped": "没有通过程序校验的事务"}
    selected = [staged[g] for g in staged if diagnostics.get(g, {}).get("status") == "ACCEPTED"]
    from .design_repair import apply_patch
    combined_permit = dict(permit, grants={})
    combined = {"base_hash": permit["base_hash"], "edits": [], "parameters": []}
    resolved = set()
    for gid, candidate in staged.items():
        if diagnostics[gid]["status"] != "ACCEPTED":
            continue
        combined_permit["grants"].update(candidate["permit"]["grants"])
        combined["edits"] += candidate["patch"]["edits"]
        combined["parameters"] += candidate["patch"]["parameters"]
        resolved.update(candidate["issue_ids"])
    try:
        merged, fields = apply_patch(text, parameters, combined_permit, combined)
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
    return merged, fields, {"status": "APPLIED" if selected and (merged != text or fields != parameters) else ("REJECTED" if diagnostics and not selected else "NO_CHANGE"),
        "permission": permit, "interfaces": contract, "transactions": diagnostics, "verification": verification,
        "submissions": submissions, "remaining_issues": remaining, "resolved_issue_ids": sorted(resolved),
        "before": digest(text), "after": digest(merged), "patch": combined}
