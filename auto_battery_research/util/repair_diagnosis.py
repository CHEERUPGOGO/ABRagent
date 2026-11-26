"""同一问题连续两轮未修复时，独立审查原因并给出下一步，不直接改稿或授予权限。"""
import copy
import json
import re

from .scheme_ledger import digest


def issue_key(issue):
    from .design_repair import issue_quotes
    quotes = [re.sub(r"\s+", "", q) for q in issue_quotes(issue)]
    reason = re.sub(r"\s+", "", issue.get("reason", ""))
    signature = [issue.get("type"), sorted(quotes), sorted(issue.get("parameter_paths", [])), reason]
    if issue.get("research_sources"):
        signature.append(issue["research_sources"])
    return digest(signature)[:20]


def unresolved(entry):
    if "remaining_issues" in entry:
        return entry["remaining_issues"]
    issues = (entry.get("interfaces") or {}).get("issues", [])
    resolved = set(entry.get("resolved_issue_ids", []))
    return [i for i in issues if i.get("id") not in resolved]


def failed_cases(issues, audit):
    rounds = [a for a in audit if a.get("budget") != "format" and
              ("remaining_issues" in a or (a.get("interfaces") or {}).get("issues"))]
    if len(rounds) < 2:
        return []
    repeated = set(map(issue_key, unresolved(rounds[-1]))) & set(map(issue_key, unresolved(rounds[-2])))
    return [i for i in issues if issue_key(i) in repeated]


def diagnose(client, text, parameters, issues, audit, state, save, evidence=(), question=""):
    """每个连续失败问题只诊断一次，原响应及占用记录随检查点保存。"""
    cases = failed_cases(issues, audit)
    if not cases:
        return []
    records = state.setdefault("repair_diagnoses", [])
    keys = {issue_key(i) for i in cases}
    previous = [r for r in records if keys & set(r["issue_keys"])]
    diagnosed = {k for r in previous for k in r["issue_keys"]}
    fresh = [i for i in cases if issue_key(i) not in diagnosed]
    if fresh and client is not None and callable(getattr(client, "chat", None)) and getattr(client, "available", True):
        from .design_repair import blocks, authorize
        from .numeric_consistency import audit as numeric_audit
        units = blocks(text)[1]
        payload_cases = []
        for item in fresh:
            key = issue_key(item)
            attempts = []
            rounds = [a for a in audit if a.get("budget") != "format" and
                      ("remaining_issues" in a or (a.get("interfaces") or {}).get("issues"))]
            for old in rounds[-2:]:
                contract = old.get("interfaces") or {}
                matching = {i["id"] for i in unresolved(old) if issue_key(i) == key}
                interfaces = {iid for iid, row in contract.get("interfaces", {}).items()
                              if matching & set(row["issue_ids"])}
                attempts.append({"status": old.get("status"), "reason": old.get("reason"),
                    "transactions": {gid: old.get("transactions", {}).get(gid) for gid, ids in contract.get("transactions", {}).items()
                                     if interfaces & set(ids)},
                    "submissions": [s for s in old.get("submissions", []) if s.get("interface_id") in interfaces]})
            permit = authorize(text, [item], parameters, state.get("research_draft"))
            payload_cases.append({"issue_key": key, "issue": item, "previous_attempts": attempts,
                                  "current_permission": permit["grants"].get(item["id"], {})})
        relevant = {p for i in fresh for p in i.get("parameter_paths", [])}
        numerical = numeric_audit(parameters, text)
        payload = {"question": question, "cases": payload_cases, "parameters": parameters,
                   "current_paragraphs": {bid: body for bid, body in units.items() if any(
                       q in body for i in fresh for q in i.get("quotes", [i.get("quote", "")]) if q)},
                   "numeric_checks": [c for c in numerical["checks"] if c["status"] != "NOT_APPLICABLE" and
                                      (relevant & set(c.get("paths", [])) or "abr-derived:" + c["id"] in text)],
                   "numeric_issues": numerical["issues"], "evidence": list(evidence)}
        record = {"input_hash": digest(payload), "issue_keys": [issue_key(i) for i in fresh], "status": "ATTEMPTED"}
        records.append(record)
        save()
        had_single, old_single = hasattr(client, "single_attempt"), getattr(client, "single_attempt", False)
        try:
            client.single_attempt = True
            record["raw_response"] = client.chat(
                '你独立审查连续两轮未修复的问题。先看原问题是否成立，再区分科学/证据缺口、程序计算路径、'
                '接口格式或参数引用限制与措辞误判；不要原样重复前两轮建议。检查可用替代公式、厚度与计量基准。'
                '基线未使用添加剂与另设候选对照可以同时成立，不因没有固定套话就否决。'
                '返回JSON {"diagnoses":[{"issue_key":"原编号","cause":"calculation/interface/evidence/scientific/wording/unclear",'
                '"action":"repair/recheck/defer","reason":"结合失败记录的具体原因","suggestion":"可执行的下一步"}]}。'
                'repair给后续补丁策略：原始参数变化用{path,field:{value,unit,basis,source,source_quote}}，保留abr-param引用。'
                '预算由模型直接写清公式、输入、代入和结果；授权原句内可移除旧abr-derived占位，不能要求补固定字段或让程序重算。'
                'current_permission.source_fields仅允许source_changes更新source_quote；research_sources提供记录级source_quotes/field_support权限，不允许改科研文本或状态。'
                '程序会按接受的正文编辑同步可定位来源，复核候选已含更新；来源问题优先使用实际签发的元数据权限，不要求改无关正文或提交越权参数。'
                '若原问题可能误判或已被新计算解决用recheck，须重新交科学Reviewer判定，不能自行宣称通过；'
                '缺实验/证据或需改程序且现接口无法处理用defer。禁止改稿、编造参数、覆盖用户锁定或扩大写权限。',
                json.dumps(payload, ensure_ascii=False), temperature=0, json_mode=True, trace_tag="repair_case_review")
            save()
            from .writer_protocol import decode
            rows = decode(record["raw_response"])["diagnoses"]
            if (not isinstance(rows, list) or len(rows) != len(fresh)
                    or {r.get("issue_key") for r in rows} != set(record["issue_keys"])):
                raise ValueError("连续失败审查须逐项返回当前问题编号")
            for row in rows:
                if (row.get("action") not in ("repair", "recheck", "defer")
                        or not isinstance(row.get("reason"), str) or not row["reason"].strip()
                        or not isinstance(row.get("suggestion"), str) or not row["suggestion"].strip()):
                    raise ValueError("连续失败审查缺少有效行动、原因或建议")
            record.update(status="REVIEWED", diagnoses=rows)
        except Exception as exc:
            record.update(status="FAILED", error=str(exc))
        finally:
            if had_single:
                client.single_attempt = old_single
            else:
                delattr(client, "single_attempt")
            save()
        previous.append(record)
    return [copy.deepcopy(d) for r in previous for d in r.get("diagnoses", []) if d["issue_key"] in keys]
