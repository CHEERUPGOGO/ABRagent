"""只审Stage 5与当前主方案的实质一致性；两次核对及一次局部修订留档。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .scheme_ledger import atomic_write, digest

PROMPT = '''你只核对实验方案与当前主方案的实质一致性，不做新的材料设计或科学批准。
只返回JSON {"subject":"原subject","status":"consistent|conflict","issues":[{"path":["sections","节名",索引],"quote":"该字段中的精确原句","reason":"明确矛盾","suggestion":"依据主方案局部改法"}]}。
issues可定位sections具体段落、requirement_tests具体字符串字段或extensions段落；数组位置必须是从0开始的整数索引，例如["requirement_tests",0,"conditions"]，不得把requirement_id作为数组下标。禁止定位整表或修改主方案。
主方案execution_conditions优先：继承材料、组成、单位、工况、主/备选身份及已定状态。inheritance已提供整体承接，不要求文字重复所有字段。
检查旧“未定案”与当前已定值冲突、备选变主路线、主组引用变对照、同一实验的条件组合错误、跨部分重复声明矛盾及主方案工艺步骤遗漏。
工况宏{{profile:ID}}引用完整已声明条件，不因宏缺少未声明字段否决。不同配方分别配料，不可写成共用一份不同组成的前驱体或浆料。
主方案未给出的操作细节允许工程建议。等价单位、同义表述、已说明待实测的设计值、实验尚未完成、暂不开展备选/后续优化均不算不一致。
对照替换元素后，“总阳离子”等通用计量分母按该组完整化学式计算；主组meaning中举出的元素求和不是要求对照仍计入已被替换的元素。分母类型与数值未变时，不因没有重复列元素清单而否决。
只报告有明确主方案声明或明确文本矛盾支持的问题；不因缺少文献、仪器或新阈值新增要求，不用科学风险推翻主方案。
已解决旧问题以resolved_issues为准；原文档科学争议保留，不转成新的数值冲突。无实质不一致返回consistent及空issues。
gap_kind=validation表示推荐已交付、最终效果或最优性留待实验；核对recommendation_refs确实对应该目标的当前主推荐。
不能将缺推荐值、缺必要执行条件或尚未明确的验证责任当成单纯待实验验证；推荐通过不表示最优性已经证实。
'''


def subject(record, doc):
    from .experiment_alignment import execution_conditions
    from .issue_resolution import resolved
    from .experiment_consistency import preflight
    return digest({"conditions": execution_conditions(doc), "requirements": doc.get("requirements"),
        "consistency": preflight(doc),
        "resolved_issues": sorted(resolved(doc)), "plan": {k: record.get(k) for k in
        ("parameters", "sections", "groups", "requirement_tests", "extensions", "execution_contract")}})


def current(record, doc):
    row = record.get("alignment_review") or {}
    return row.get("subject") == subject(record, doc) and row.get("status") == "consistent" and row.get("issues") == []


def _decode(raw):
    from .writer_protocol import decode
    return decode(raw)


def _validate(value, expected, record):
    from .experiment_alignment import text_entries
    if not isinstance(value, dict) or value.get("subject") != expected or value.get("status") not in ("consistent", "conflict") or not isinstance(value.get("issues"), list):
        raise ValueError("一致性审核对象、状态或问题列表无效")
    texts = {tuple(p): s for p, s in text_entries(record)}
    for row in value["issues"]:
        if not isinstance(row, dict) or not isinstance(row.get("path"), list):
            raise ValueError("一致性问题缺少具体路径")
        path = row["path"]
        if len(path) == 3 and path[0] == "requirement_tests" and isinstance(path[1], str):
            indices = [i for i, t in enumerate(record.get("requirement_tests", [])) if t.get("requirement_id") == path[1]]
            if len(indices) == 1:
                row["path"] = [path[0], indices[0], path[2]]
        if any(not isinstance(p, (str, int)) or isinstance(p, bool) for p in row["path"]):
            raise ValueError("一致性问题路径格式非法")
        text = texts.get(tuple(row["path"]))
        if text is None or not isinstance(row.get("quote"), str) or not row["quote"] or row["quote"] not in text or not row.get("reason") or not row.get("suggestion"):
            raise ValueError("一致性问题须定位具体原句并说明依据")
    if (value["status"] == "consistent") != (not value["issues"]):
        raise ValueError("一致性审核状态与问题列表矛盾")


def review(record, doc, client, task_dir):
    if not record.get("execution_contract") or current(record, doc):
        return record
    if client is None or not getattr(client, "available", False):
        record["alignment_review"] = {"subject": subject(record, doc), "status": "pending", "issues": [], "reason": "主方案文字一致性尚未审核"}
        return record
    from .experiment_alignment import execution_conditions, issues
    from .issue_resolution import resolved
    from .experiment_repair import apply_local
    from .experiment_design import plan_hash, validate
    from .experiment_consistency import stamp
    root_subject = subject(record, doc)
    path = Path(task_dir) / "experiment_alignment_history" / (root_subject + ".json")
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"subject": root_subject, "calls": [], "result": copy.deepcopy(record)}
    def save():
        atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2))
    def invoke(kind, system, payload):
        sig = digest({"kind": kind, "system": system, "payload": payload})
        previous = next((r for r in state["calls"] if r["signature"] == sig), None)
        if previous:
            if "response" not in previous:
                raise ValueError("一致性调用已中断，不自动重发")
            return previous["response"]
        if sum(r["kind"] == kind for r in state["calls"]) >= (2 if kind == "review" else 1):
            raise ValueError("一致性局部审核预算已用尽")
        entry = {"signature": sig, "kind": kind, "system": system, "input": payload}
        state["calls"].append(entry)
        save()
        had_single, previous_single = hasattr(client, "single_attempt"), getattr(client, "single_attempt", None)
        client.single_attempt = True
        try:
            entry["response"] = client.chat(system, json.dumps(payload, ensure_ascii=False), temperature=0.1,
                                            json_mode=True, trace_tag="experiment_alignment_" + kind)
        finally:
            if had_single:
                client.single_attempt = previous_single
            else:
                delattr(client, "single_attempt")
        save()
        return entry["response"]
    # 复用审核内容，不能用旧缓存覆盖本次输入指纹、来源版本或生成元数据。
    record = copy.deepcopy(record)
    for key in ("sections", "requirement_tests", "extensions", "step_titles", "alignment_review"):
        if key in state["result"]:
            record[key] = copy.deepcopy(state["result"][key])
    try:
        for attempt in range(2):
            expected = subject(record, doc)
            payload = {"subject": expected, "execution_conditions": execution_conditions(doc),
                       "requirements": doc.get("requirements"), "resolved_issues": sorted(resolved(doc)),
                       "plan": {k: record.get(k) for k in ("parameters", "sections", "groups", "requirement_tests", "extensions", "execution_contract")}}
            verdict = _decode(invoke("review", PROMPT, payload))
            _validate(verdict, expected, record)
            record["alignment_review"] = verdict
            state["result"] = copy.deepcopy(record)
            save()
            if verdict["status"] == "consistent" or attempt:
                break
            errors = [{"path": r["path"], "reason": r["reason"], "suggestion": r["suggestion"]} for r in verdict["issues"]]
            patch = _decode(invoke("patch", "只返回局部JSON补丁{\"base_hash\":\"原值\",\"changes\":[{\"path\":[\"sections\",\"节名\",0],\"value\":\"修订段落\"}]}。只能替换issues中的具体字符串字段；继承主方案，不改参数、组别和其它正文。", {
                "base_hash": digest(record), "record": record, "issues": errors, "execution_conditions": execution_conditions(doc)}))
            candidate = apply_local(record, patch, errors)
            if issues(candidate, doc):
                raise ValueError("局部文字补丁仍有主方案一致性冲突")
            stamp(candidate, doc)
            candidate["plan_hash"] = plan_hash(candidate)
            validate(candidate, doc, delivery=True)
            record = candidate
            state["result"] = copy.deepcopy(record)
            save()
    except (ValueError, TypeError, KeyError) as exc:
        record["alignment_review"] = {"subject": subject(record, doc), "status": "pending", "issues": [], "reason": str(exc)}
        state["error"] = str(exc)
    state["result"] = copy.deepcopy(record)
    save()
    record["plan_hash"] = plan_hash(record)
    record["bindings"] = validate(record, doc, delivery=True)
    return record
