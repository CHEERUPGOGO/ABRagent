"""研究记录逐项验收与恢复；一个坏引用不能清空其他已通过的回答。"""
import copy
import re
from collections import Counter


from .candidate_metadata import ROLES as CANDIDATE_ROLES


def normalize_candidate_label(row):
    """展示简称可兼容；材料、角色及排序仍使用同条来源核验。"""
    from .candidate_metadata import normalize_label
    return normalize_label(row)


def is_generated_gap(row):
    """程序占位不是模型研究回答，不可用其空来源覆盖真实提取失败记录。"""
    return (isinstance(row, dict) and row.get("status") == "gap" and row.get("response") == "待补充"
            and not (row.get("source_quote") or row.get("source_quotes"))
            and row.get("verification") == "unverified" and not row.get("citations") and not row.get("parameter_refs"))


def validate_record(row, collection, doc, parameters):
    if isinstance(row, dict) and row.get("projection_origin") == "writer_content_v3":
        from .stage4_document import compile_document
        from .research_support import projection
        compiled = compile_document(doc.get("writer_content"), parameters, doc.get("requirements"), doc.get("evidence", []))
        key = "requirement_id" if collection == "answers" else "id"
        matching = [r for r in compiled.research.get(collection, []) if r.get(key) == row.get(key)]
        if len(matching) != 1 or projection(matching[0]) != projection(row):
            raise ValueError("v3研究记录与权威正文关联不一致")
        if any(d["severity"] == "error" for d in compiled.diagnostics):
            raise ValueError("v3编译或研究关联未通过")
        return
    from .research_contract import TOKEN, RESPONSES, VERIFICATIONS, normalized
    from .report_narrative import amount
    if not isinstance(row, dict):
        raise ValueError("研究记录必须为对象")
    if collection == "candidates":
        row = normalize_candidate_label(row)
    from .research_support import is_summary, normalize, field_quotes
    summary = is_summary(row)
    if summary:
        row = normalize(row, collection, doc, parameters)
    quote = "\n".join(row.get("source_quotes", [])) if summary and isinstance(row.get("source_quotes"), list) else row.get("source_quote", "")
    if not summary and (not isinstance(quote, str) or not quote or quote not in doc.get("final_answer", "")):
        raise ValueError("研究记录缺少已审核正文中的source_quote")
    refs = row.get("parameter_refs", [])
    if not isinstance(refs, list) or any(not isinstance(p, str) or p not in parameters or parameters[p].get("value") is None for p in refs):
        raise ValueError("研究记录引用未知主方案参数")
    fields = {"answers": ("response", "reason", "next_step"), "candidates": ("summary", "rationale", "risks"), "arguments": ("text",)}[collection]
    for key in fields:
        value = row.get(key, "")
        if not isinstance(value, str):
            raise ValueError("研究记录文本非法")
        if set(TOKEN.findall(value)) - set(refs):
            raise ValueError("研究文本引用未声明的主方案字段")
        chunks = TOKEN.split(value)
        source = "\n".join(field_quotes(row, key)) if summary else quote
        if not summary and any(part.strip() and normalized(part) not in normalized(quote) for part in chunks[::2]):
            raise ValueError("研究结论必须摘自支持原句: " + key)
        for path in chunks[1::2]:
            row_value = parameters[path]
            from .material_ids import source_matches
            if not source_matches(path, row_value["value"], source) and normalized(amount(path, row_value)) not in normalized(source):
                raise ValueError("参数引用与支持原句不一致: " + path)
    known = {str(e.get("passage_id")) for e in doc.get("evidence", [])}
    cites = row.get("citations", [])
    if not isinstance(cites, list) or any(not isinstance(c, str) for c in cites) or set(cites) - known:
        raise ValueError("研究记录引用未归档文献")
    if any("[" + c + "]" not in quote for c in cites):
        raise ValueError("引用必须同时出现于支持原句，不能挪用其他证据")
    if set(re.findall(r"\[([\w:-]+)\]", quote)) - known - {"FSI", "TFSI", "PF6", "BF4"}:
        raise ValueError("支持原句含未知引用")
    required = {i["id"] for i in doc["requirements"]["items"] if i["kind"] != "background"}
    if collection == "answers":
        if row.get("requirement_id") not in required:
            raise ValueError("回答引用未知课题要求")
        if row.get("status") not in RESPONSES or row.get("verification") not in VERIFICATIONS:
            raise ValueError("回应与验证状态非法")
        if row["status"] == "answered" and len(row.get("response", "").strip()) < 8:
            raise ValueError("只有标题不能算实质回应")
        if row["status"] in ("gap", "partial") and not (row.get("reason") and row.get("next_step")):
            raise ValueError("缺口必须包含原因及获取/验证方法")
        if row["verification"] in ("calculation", "experiment"):
            raise ValueError("Stage 4 不得宣称本课题计算或实验已验证")
        if row["verification"] == "literature" and not cites:
            raise ValueError("文献支持须引用归档证据")
    elif collection == "candidates":
        if not isinstance(row.get("id"), str) or not row["id"] or row.get("scope") not in ("candidate", "control", "literature"):
            raise ValueError("候选身份或范围非法")
        if not row.get("name") or normalized(row["name"]) not in normalized(quote):
            raise ValueError("候选名称须来自支持原句")
        from .candidate_metadata import rank_supported
        if row.get("rank") is not None and (type(row["rank"]) is not int or row["rank"] < 1
                or not rank_supported(row["rank"], row["name"], row.get("source_quotes") or [quote])):
            raise ValueError("候选顺序须来自已审核正文")
    else:
        ids = row.get("requirement_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or set(ids) - required or row.get("basis") not in ("literature", "inference"):
            raise ValueError("论证缺少有效课题关联或依据类型")
        if row["basis"] == "literature" and not cites:
            raise ValueError("文献论证缺少引用")
        if not isinstance(row.get("title", ""), str) or not summary and row.get("title") and normalized(row["title"]) not in normalized(quote):
            raise ValueError("论证标题须摘自支持原句或留空")


def identity_bindings(parameters):
    from .material_ids import identity_path
    return {p: {"value": copy.deepcopy(v.get("value")), "unit": v.get("unit", "")} for p, v in parameters.items()
            if identity_path(p, v) or p.startswith("electrolyte.")
            or any(t in p for t in (".doping.", ".dopant", ".nominal_formula", ".composition", ".coating"))}


def validate_selection(selected, candidates, parameters):
    if selected is None:
        return
    current = next((c for c in candidates if c["id"] == selected), None)
    if not current or current["scope"] != "candidate" or not current.get("parameter_refs"):
        raise ValueError("探索性首选必须引用当前主方案参数")
    if not set(identity_bindings(parameters)).issubset(current["parameter_refs"]):
        raise ValueError("探索性首选没有引用完整主方案身份")


def record_key(collection, row, index):
    if not isinstance(row, dict):
        return f"invalid_{index}"
    value = row.get("requirement_id" if collection == "answers" else "id")
    return value if isinstance(value, str) and value else f"argument_{index}" if collection == "arguments" else f"invalid_{index}"


def collect_research(raw, doc, parameters, previous=None):
    """只重试失败记录；已验收记录在同一轮维护中保持不变。"""
    from .research_contract import gap_answer, field_equal
    raw = raw if isinstance(raw, dict) else {}
    previous = previous if isinstance(previous, dict) else {}
    accepted = {key: {} for key in ("answers", "candidates", "arguments")}
    prior_errors = previous.get("record_errors") or []
    prior_errors = prior_errors if isinstance(prior_errors, list) else []
    errors = {(e["collection"], e["id"]): copy.deepcopy(e) for e in prior_errors
              if isinstance(e, dict) and isinstance(e.get("collection"), str) and isinstance(e.get("id"), str)}
    prior_bindings = previous.get("bound_fields") or raw.get("bound_fields") or {}
    prior_bindings = prior_bindings if isinstance(prior_bindings, dict) else {}
    stale = {p for p, bound in prior_bindings.items() if isinstance(bound, dict) and (
             not field_equal(p, bound.get("value"), parameters.get(p, {}).get("value"))
             or bound.get("unit", "") != parameters.get(p, {}).get("unit", ""))}
    def accept(collection, row, index, locked=False, check_bindings=False):
        from .research_support import is_summary, normalize, projection
        key = record_key(collection, row, index)
        value = copy.deepcopy(row)
        if locked and collection == "answers" and is_generated_gap(value):
            return
        if collection == "answers" and is_generated_gap(value):
            if (collection, key) not in errors:
                errors[collection, key] = {"collection": collection, "id": key,
                    "reason": value.get("reason") or "尚未返回该要求的有效回答", "record": value}
            return
        try:
            if isinstance(value, dict) and collection == "candidates":
                value = normalize_candidate_label(value)
            if isinstance(value, dict) and collection == "arguments":
                value.setdefault("id", key)
            if check_bindings and stale and isinstance(value, dict) and (set(value.get("parameter_refs", [])) & stale or collection != "answers" and not value.get("parameter_refs")):
                raise ValueError("该研究记录基于旧主方案，须重新审核")
            validate_record(value, collection, doc, parameters)
            trusted = accepted[collection].get(key) or (value if locked else {})
            if is_summary(value):
                value = normalize(value, collection, doc, parameters)
            if key in accepted[collection] and projection(accepted[collection][key]) != projection(value):
                raise ValueError("已通过研究记录禁止在局部重试中修改")
            if is_summary(value):
                from .research_support import review_fingerprint_matches
                if (projection(trusted) == projection(value) and review_fingerprint_matches(trusted, doc, parameters)):
                    value["support_review"] = copy.deepcopy(trusted["support_review"])
                    value["support_review"]["fingerprint"] = value["support_fingerprint"]
            accepted[collection][key] = value
            errors.pop((collection, key), None)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            if not locked or isinstance(value, dict) and (value.get("source_quote") or value.get("source_quotes")):
                errors[collection, key] = {"collection": collection, "id": key, "reason": str(exc), "record": value}
    submitted = set()
    for collection in accepted:
        for index, row in enumerate(previous.get(collection, []) or []):
            accept(collection, row, index, locked=True, check_bindings=True)
        rows = raw.get(collection, [])
        if not isinstance(rows, list):
            errors[collection, "$"] = {"collection": collection, "id": "$", "reason": "研究记录须为数组"}
            continue
        if collection in raw:
            errors.pop((collection, "$"), None)
        counts = Counter(record_key(collection, row, n) for n, row in enumerate(rows))
        for index, row in enumerate(rows):
            key = record_key(collection, row, index)
            submitted.add((collection, key))
            if counts[key] != 1:
                errors[collection, key] = {"collection": collection, "id": key, "reason": "研究记录身份重复", "record": row}
                continue
            accept(collection, row, index, check_bindings=bool(raw.get("bound_fields")))
    # 缓存刷新不能只重验占位记录；保留原失败记录，按当前正文/参数重新定位。
    for error in list(errors.values()):
        collection, key = error["collection"], error["id"]
        row = error.get("record")
        if collection in accepted and (collection, key) not in submitted and key not in accepted[collection] and isinstance(row, dict):
            accept(collection, row, 0, check_bindings=True)
    items = [i for i in doc["requirements"]["items"] if i["kind"] != "background"]
    answers = []
    for item in items:
        if item["id"] in accepted["answers"]:
            answers.append(accepted["answers"][item["id"]])
        else:
            error = errors.setdefault(("answers", item["id"]), {"collection": "answers", "id": item["id"], "reason": "尚未返回该要求的有效回答"})
            answers.append(gap_answer(item, "该项结构化回答待修复：" + error["reason"]))
    selected = (raw.get("selected_candidate_id") if "selected_candidate_id" in raw
                else previous.get("selected_candidate_id"))
    if selected is None and "selected_candidate_id" not in raw:
        # 仅恢复因候选格式被隔离的原显式引用；不能按Rank、角色或当前材料猜首选。
        selection_error = errors.get(("selected_candidate_id", "$"), {})
        prior_selected = selection_error.get("record")
        if (selection_error.get("reason") == "探索性首选必须引用当前主方案参数"
                and isinstance(prior_selected, str) and prior_selected in accepted["candidates"]):
            selected = prior_selected
    try:
        validate_selection(selected, list(accepted["candidates"].values()), parameters)
        if "selected_candidate_id" in raw or selected is not None:
            errors.pop(("selected_candidate_id", "$"), None)
    except (ValueError, TypeError) as exc:
        errors["selected_candidate_id", "$"] = {"collection": "selected_candidate_id", "id": "$", "reason": str(exc), "record": copy.deepcopy(selected)}
        selected = None
    diagnostics = [f"{e['collection']}[{e['id']}]：{e['reason']}" for e in errors.values()]
    return {"version": 1, "status": "complete" if not errors and doc["requirements"].get("status") == "parsed"
            and all(a["status"] == "answered" for a in answers) else "partial",
            "answers": answers, "candidates": list(accepted["candidates"].values()),
            "arguments": list(accepted["arguments"].values()), "selected_candidate_id": selected,
            "record_errors": list(errors.values()), "diagnostics": diagnostics, "bound_fields": identity_bindings(parameters)}
