"""需求误读的可定位证据契约；科学解释争议不拥有用户字段阻断权限。"""
import copy

VERSION = 1
INSTRUCTIONS = '''
需求审核与科学审核分开：misread_ids仅标用户原意/约束被错误理解，不能用于正文的科学解释、推理、文献适用性或摘要支持性争议。
材料和条件已与用户一致，但机理/复合表示/价态解释有疑问，放issues/repair_issues或research_support_review，不放misread_ids。
requirement_review额外返回misread_evidence数组，每个misread_id必须有一条证据：
{"requirement_id":"已有ID","scope":"user_binding|requirement_interpretation",
 "user_quote":"该要求用户原文逐字摘录","observed_quote":"当前正文中的错误解释逐字摘录",
 "reason":"具体原意差异，不把科研结论未验证写成要求误读",
 "path":"固定字段路径（user_binding必填）","expected_value":"用户真实指定值",
 "expected_unit":"规范单位","actual_value":"当前该字段值","actual_unit":"当前单位"}。
user_binding仅在对应固定字段的值或单位确实被误解时使用；期望与实际均须有原句和当前参数支持，不能用材料类别相同代替精确值相同。
requirement_interpretation只用于没有binding的要求，不拥有字段阻断权限；缺回答放unanswered_ids，科学错误放repair_issues。
同一条不能既说“用户固定值正确”又标user_binding误读。不确定时保留科学/语义待复核意见，不输出无证据的misread_id。
'''


def current_parameters(doc):
    from .scheme_ledger import flatten_scheme
    params = copy.deepcopy((doc.get("ledger_view") or {}).get("parameters", {}))
    params.update(doc.get("parameters") or {})
    params.update(doc.get("scheme_parameters") or {})
    for path, value in flatten_scheme(doc.get("scheme") or {}).items():
        if path.endswith(".material"):
            existing = params.get(path, {})
            from .research_contract import field_equal
            if not field_equal(path, existing.get("value"), value):
                params[path] = {"value": value, "unit": ""}
        else:
            params.setdefault(path, {"value": value, "unit": ""})
    return params


def _equal(path, left, right):
    from .research_contract import field_equal, normalized
    # 字面化学式不能仅因归一到同一材料族就视为相同。
    def formula(value):
        import re
        import unicodedata
        from decimal import Decimal
        if not isinstance(value, str):
            return None
        value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
        tokens = re.findall(r"([A-Z][a-z]?)(\d+(?:\.\d+)?)?", value)
        if not tokens or "".join(e + n for e, n in tokens) != value:
            return None
        result = {}
        for element, number in tokens:
            result[element] = result.get(element, Decimal(0)) + Decimal(number or "1")
        return result
    first, second = formula(left), formula(right)
    if first is not None and second is not None:
        return first == second
    return field_equal(path, left, right)


def _present(path, value, text):
    from .research_contract import normalized
    from .material_ids import source_matches
    if value is None:
        return False
    token = f"{value:g}" if type(value) in (int, float) else str(value)
    if type(value) in (int, float):
        import re
        return bool(re.search(r"(?<![\d.])" + re.escape(token) + r"(?![\d.])", normalized(text)))
    if isinstance(value, str):
        import re
        if re.search(r"\d", normalized(value)):
            return normalized(value) in normalized(text)
    return normalized(token) in normalized(text) or source_matches(path, value, text)


def validate_misread(record, item, doc, parameters):
    from .research_contract import normalized
    if not isinstance(record, dict) or record.get("requirement_id") != item["id"]:
        raise ValueError("误读证据未关联要求")
    user, observed = record.get("user_quote"), record.get("observed_quote")
    sources = [item["quote"]] + item.get("source_quotes", []) + item.get("conditions", [])
    if not isinstance(user, str) or not user or user not in doc["requirements"]["source"] or not any(user in q for q in sources):
        raise ValueError("误读证据的用户原句不属于该要求")
    if not isinstance(observed, str) or not observed or observed not in (doc.get("final_answer") or ""):
        raise ValueError("误读证据的实际解释不能定位于当前正文")
    if not isinstance(record.get("reason"), str) or not record["reason"].strip():
        raise ValueError("误读证据缺少具体差异原因")
    from .requirement_structure import item_bindings
    fixed = item_bindings(item)
    if not fixed:
        if record.get("scope") != "requirement_interpretation":
            raise ValueError("没有固定绑定的要求不能阻断用户字段")
        return None
    binding = next((b for b in fixed if b["path"] == record.get("path")), None)
    if record.get("scope") != "user_binding" or binding is None:
        raise ValueError("固定要求误读必须指向其绑定字段，科学解释争议不阻断绑定")
    path = binding["path"]
    actual = parameters.get(path)
    if not isinstance(actual, dict) or actual.get("value") is None:
        raise ValueError("实际字段缺失须走缺失/未回答诊断，不能伪造误读值")
    expected = record.get("expected_value")
    if not _present(path, expected, user):
        raise ValueError("期望值缺少用户原句支持")
    binding_value = binding.get("original_value", binding.get("value"))
    if not _equal(path, expected, binding_value):
        raise ValueError("期望值与该要求的原始固定值不一致，须先修复需求解析")
    if not _equal(path, record.get("actual_value"), actual["value"]) or record.get("actual_unit") != actual.get("unit", ""):
        raise ValueError("误读证据的实际值/单位与当前参数不符")
    original = actual.get("original_value", actual["value"])
    if not _present(path, original, observed):
        raise ValueError("实际字段值缺少当前正文支持")
    expected_unit = record.get("expected_unit")
    if not isinstance(expected_unit, str) or expected_unit != binding.get("unit", ""):
        raise ValueError("期望单位缺少用户原句或绑定支持")
    if _equal(path, expected, original) and expected_unit == actual.get("unit", ""):
        raise ValueError("用户期望值与当前字段一致；只能审核科学解释或摘要支持性")
    return path


def normalize_review(verdict, doc):
    """保留原判定和待复核理由；只给证据通过的固定误读授予字段阻断权限。"""
    raw = verdict.get("requirement_review") or {}
    if not isinstance(raw, dict):
        raw = {}
    review = copy.deepcopy(raw)
    ids = raw.get("reported_misread_ids", raw.get("misread_ids", []))
    ids = ids if isinstance(ids, list) else []
    evidence = raw.get("misread_evidence", [])
    evidence = evidence if isinstance(evidence, list) else []
    items = {i["id"]: i for i in doc["requirements"]["items"]}
    accepted, blocked, diagnostics = [], [], []
    if any(not isinstance(i, str) for i in ids):
        diagnostics.append({"requirement_id": "$", "reason": "误读ID须为字符串", "evidence": []})
    parameters = current_parameters(doc)
    for requirement_id in dict.fromkeys(i for i in ids if isinstance(i, str)):
        matches = [e for e in evidence if isinstance(e, dict) and e.get("requirement_id") == requirement_id]
        try:
            if requirement_id not in items or len(matches) != 1:
                raise ValueError("每项误读须有唯一的可定位证据")
            path = validate_misread(matches[0], items[requirement_id], doc, parameters)
            accepted.append(requirement_id)
            if path:
                blocked.append(path)
        except ValueError as exc:
            diagnostics.append({"requirement_id": requirement_id, "reason": str(exc), "evidence": matches})
    review.update(contract_version=VERSION, reported_misread_ids=copy.deepcopy(ids), misread_ids=accepted,
                  blocked_fields=list(dict.fromkeys(blocked)), misread_diagnostics=diagnostics)
    verdict["requirement_review"] = review
    verdict["issues"] = [issue for issue in verdict.get("issues", [])
                         if not str(issue).startswith("需求误读证据待复核（不阻断字段）：")]
    for diagnostic in diagnostics:
        message = "需求误读证据待复核（不阻断字段）：" + diagnostic["requirement_id"] + "；" + diagnostic["reason"]
        if message not in verdict.setdefault("issues", []):
            verdict["issues"].append(message)
    return review


def blocking_fields(doc):
    """Adapter使用当前输入重验，不信任缓存/模型自报的blocked_fields。"""
    verdict = copy.deepcopy(doc.get("reviewer_output") or {})
    return normalize_review(verdict, doc)["blocked_fields"]
