"""实验推荐局部校验与补丁；有效参数和有效段落不可重写。"""
import copy
import json
import re

from .scheme_ledger import digest, atomic_write


def normalize_citations(record):
    record = copy.deepcopy(record)
    values = record.get("citations", [])
    if isinstance(values, list):
        record["citations"] = [v.get("passage_id") if isinstance(v, dict) else v for v in values]
    return record


def diagnose(record, doc):
    from .experiment_design import effective_data, overlap_key, TOKEN, SECTIONS, validate, plan_hash
    errors = []
    params = record.get("parameters")
    sections = record.get("sections")
    if not isinstance(params, dict) or not isinstance(sections, dict):
        return [{"path": ["parameters" if not isinstance(params, dict) else "sections"], "reason": "实验结构须为对象"}]
    current = effective_data(doc)["parameters"]
    bad = set()
    for key, row in params.items():
        reason = None
        if not isinstance(row, dict):
            reason = "参数须为对象"
        elif "ref" in row:
            if not isinstance(row["ref"], str) or (current.get(row["ref"]) or {}).get("value") is None:
                reason = "ref必须指向已有非空参数"
        else:
            declared = current.get(overlap_key(key)) or {}
            if declared.get("value") is not None and row.get("scope") != "control":
                if row.get("value") != declared["value"] or row.get("unit", "") != declared.get("unit", ""):
                    reason = "与当前方案冲突，应使用ref引用当前值"
            elif row.get("basis") not in ("engineering_proposal", "budget_assumption") or row.get("value") is None:
                reason = "操作建议须有值并标为engineering_proposal或budget_assumption；文献来源另行保留"
            elif isinstance(row.get("value"), float):
                import math
                if not math.isfinite(row["value"]):
                    reason = "推荐值必须有限"
        if reason:
            errors.append({"path": ["parameters", key], "reason": reason})
            bad.add(key)
    allowed = {e.get("passage_id") for e in (doc.get("evidence") or []) + (doc.get("experiment_evidence") or [])}
    cites = record.get("citations") or []
    if not isinstance(cites, list) or any(not isinstance(i, str) or i not in allowed for i in cites):
        errors.append({"path": ["citations"], "reason": "citations须为归档passage_id字符串列表"})
    good_params = {k: v for k, v in params.items() if k not in bad}
    for section in SECTIONS:
        paragraphs = sections.get(section)
        if not isinstance(paragraphs, list) or not paragraphs:
            errors.append({"path": ["sections", section], "reason": "缺少本节具体实验步骤"})
            continue
        for n, text in enumerate(paragraphs):
            if isinstance(text, str) and set(TOKEN.findall(text)) & bad:
                continue  # 等待对应参数修复，不扩大正文修改权限。
            probe = dict(record, parameters=good_params, citations=[c for c in cites if isinstance(c, str) and c in allowed] if isinstance(cites, list) else [],
                sections={s: ["保留当前方案并在小批验证中记录测量口径及实验条件。" * 6] for s in SECTIONS})
            probe["sections"][section] = [text]
            probe["plan_hash"] = plan_hash(probe)
            try:
                validate(probe, doc)
            except (ValueError, TypeError, KeyError) as exc:
                errors.append({"path": ["sections", section, n], "reason": str(exc)})
                if isinstance(text, str):
                    for missing in set(TOKEN.findall(text)) - set(params):
                        errors.append({"path": ["parameters", missing], "reason": "段落使用的参数尚未声明"})
    return errors


def apply_local(record, patch, errors):
    if not isinstance(patch, dict) or set(patch) != {"base_hash", "changes"} or patch["base_hash"] != digest(record):
        raise ValueError("实验修复必须提交匹配基准的changes补丁，不能重写完整方案")
    allowed = {tuple(e["path"]) for e in errors}
    updated, touched = copy.deepcopy(record), set()
    if not isinstance(patch["changes"], list):
        raise ValueError("changes须为数组")
    for change in patch["changes"]:
        if not isinstance(change, dict) or set(change) != {"path", "value"}:
            raise ValueError("补丁每项仅允许path和value")
        path = tuple(change["path"])
        if path not in allowed or path in touched:
            raise ValueError("实验修复越权或重复路径")
        parent = updated
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = copy.deepcopy(change["value"])
        touched.add(path)
    return updated


def repair_record(record, doc, client, task_dir, fingerprint):
    from .experiment_design import plan_hash, validate, overlap_key, effective_data
    original = copy.deepcopy(record)
    record = normalize_citations(record)
    errors = diagnose(record, doc)
    audit = {"input_fingerprint": fingerprint, "base_hash": digest(record), "errors": errors}
    atomic_write(task_dir / "experiment_draft.json", json.dumps({"audit": audit, "original": original, "record": record}, ensure_ascii=False, indent=2))
    if errors and client is not None and getattr(client, "available", False):
        try:
            raw = client.chat('仅返回局部JSON补丁 {"base_hash":"输入哈希","changes":[{"path":["parameters","字段路径"],"value":修复后的参数对象}]}。只能替换errors中列出的路径，保留其他所有参数和段落。修复引用、来源属性及缺失值；source用于来源描述，操作建议basis用engineering_proposal，文献出处仍保留。段落路径为["sections","节名",索引]。',
                json.dumps({"base_hash": digest(record), "record": record, "errors": errors,
                    "scheme": doc.get("scheme"), "ledger": doc.get("ledger_view"), "evidence": doc.get("evidence")}, ensure_ascii=False),
                temperature=0.1, trace_tag="experiment_repair")
            patch = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            record = apply_local(record, patch, errors)
            audit["patch"] = patch
        except Exception as exc:
            audit["repair_error"] = str(exc)
    remaining = diagnose(record, doc)
    audit["remaining"] = remaining
    # 修复失败只处理错误单元，保存详细方案中的其他已通过内容。
    current = effective_data(doc)["parameters"]
    pending = remaining
    for _ in range(2):
        for error in pending:
            path = error["path"]
            if path[0] == "parameters" and len(path) == 2:
                key = path[1]
                declared = current.get(overlap_key(key)) or {}
                if declared.get("value") is not None:
                    record["parameters"][key] = {"ref": overlap_key(key)}
                else:
                    record["parameters"][key] = {"value": "待确定，须核对原始文献或小批实验后定案", "unit": "",
                        "basis": "engineering_proposal", "source": "局部校验未通过，不能作为已确定操作值", "meaning": key, "role": "testing"}
            elif path[0] == "citations":
                allowed = {e.get("passage_id") for e in (doc.get("evidence") or []) + (doc.get("experiment_evidence") or [])}
                record["citations"] = [i for i in record.get("citations", []) if isinstance(i, str) and i in allowed]
            elif path[0] == "sections" and len(path) >= 2:
                text = "本项操作尚未定案：" + error["reason"] + "。保留当前材料及固定条件，先核对原始文献或开展小批对照后确定本项，不将未确认条件作为已验证工艺。"
                if len(path) == 3:
                    record["sections"][path[1]][path[2]] = text
                else:
                    record["sections"][path[1]] = [text]
        pending = diagnose(record, doc)
        if not pending:
            break
        remaining += [e for e in pending if e not in remaining]
    record["warnings"] = list(record.get("warnings") or []) + ["局部待确定：" + str(e) for e in remaining]
    record["repair_status"] = "REPAIRED" if not remaining else "PARTIAL"
    record["plan_hash"] = plan_hash(record)
    record["bindings"] = validate(record, doc)
    atomic_write(task_dir / "experiment_draft.json", json.dumps({"audit": audit, "original": original, "record": record}, ensure_ascii=False, indent=2))
    return record
