"""分析摘要的多段原文定位与审核绑定；科学支持性由现有Reviewer判定。"""
import copy
import re

from .scheme_ledger import digest

FIELDS = {"answers": ("response", "reason", "next_step"),
          "candidates": ("summary", "rationale", "risks"), "arguments": ("text",)}
STATUS = {"supported", "unsupported", "needs_review"}
INSTRUCTIONS = '''
研究记录使用source_quotes（1–12条正文逐字摘录）替代单一source_quote。
field_support按实际非空文本字段给来源下标数组（从0开始）：answers的response/reason/next_step，
candidates的summary/rationale/risks，arguments的text。每个字段可综合多段，但至少关联一条原句。
允许归纳、改写和连接已有观点；禁止新增事实、材料、数值、工况或夸大因果与验证层级。
同一参数出现于不同体系/倍率/温度不能混为同一结论。主方案数值用{{字段路径}}，其他文献数值保留原体系/条件。
citations只用被关联原句中已有的真实文献编号。来源编号和审核指纹由程序维护，不能自行声明support_review。
每个非background要求仅一条answers；多子问题用同一response综合并关联多条原句，不重复requirement_id。
材料候选名称与探索排序须来自原句，不能自己添加名次。用户指定材料不写成筛选得到的最优。
候选name仅填材料或样品名称；主推候选/备选候选等身份单列candidate_role，展示名可用display_name。
display_name可用同条来源中的简称和排行（如Rank 1 Zn–Na），不要求拼成完整材料名＋角色。display_name不能更换材料、名次或加入原文没有的科学结论。
candidate_role可选主推候选/备选候选/对照组/文献参照，不能自造“最优”角色；无明确角色时不填。
材料名和角色须出现在同一条source_quotes，不能跨不同候选拼接；角色不自动授予selected_candidate_id资格。
'''
REVIEW_INSTRUCTIONS = '''
同时核对待审研究记录的分析摘要，返回research_support_review数组，每项：
{"collection":"answers|candidates|arguments","id":"record_id","fingerprint":"输入中的support_fingerprint",
 "fields":{"response":{"status":"supported|unsupported|needs_review","reason":"简短依据"}}}。
answers的id必须使用原记录requirement_id，candidates和arguments使用原记录id，不能新编ID。
fields必须覆盖输入该记录的全部非空文本字段，候选使用summary/rationale/risks，论证使用text。
supported只表示该字段忠实综合所绑定原句和本包文献，不表示本课题性能已验证。
核对事实、材料、数值、原体系/位点/倍率/温度/圈数、否定和条件、因果强度及比较公平性。
不能因为原句可定位就认定其科学推断成立；有未解决文献误读的字段标needs_review或unsupported。
若字段依赖文献不在本次包中，标needs_review；不能借未读取的文献编号通过审核。
原文只说改善结构，摘要却说已抑制氧释放，或低倍率结果被写成1C性能，必须unsupported。
只返回逐字段审核，不改写摘要或方案；格式/指纹不匹配的返回将不予采信。
采用实质一致性标准：原文可追溯、数值与工况一致、没有明显矛盾时判supported。
措辞、语序、标点差异以及已明确标为待验证的工程建议/跨体系推断，不应仅因不够完美而否决。
只有材料或数量/条件不一致、无依据新增事实、否定关系反转、把推断写成已证等实质问题才标unsupported；
needs_review用于真实无法确定的关键事实，不能用于风格偏好或重复要求补充本文已披露的限制。
研究文本中的{{字段路径}}是合法主方案参数引用，展示时按parameters渲染，不是未解析错误；按当前值、单位和工况核对，不能要求改成写死数字。
status=partial/gap允许表示已给出推荐但科学最优性/效果仍待验证，不能单凭状态要求升级为answered。source_quotes/field_support权限仅修来源，参数引用、科研文本、验证状态和其他真实缺陷分别核验。
'''


def is_summary(row):
    return isinstance(row, dict) and "source_quotes" in row


def normalize_extracted_research(raw):
    """兼容模型返回的单来源格式；摘要须独立审核，不继承旧格式的逐字可信性。"""
    if not isinstance(raw, dict):
        raise ValueError("提取响应缺少有效research对象")
    result = copy.deepcopy(raw)
    for collection, fields in FIELDS.items():
        rows = result.get(collection, [])
        if not isinstance(rows, list):
            continue  # 集合形状仍由逐项验收器报告，不能影响其他集合。
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not is_summary(row) and isinstance(row.get("source_quote"), str) and row["source_quote"].strip():
                row["source_quotes"] = [row.pop("source_quote")]
                # 只绑定明确提供的这一原句；不从全文猜测其他依据。
                row["field_support"] = {field: [0] for field in fields if row.get(field)}
                row["extraction_compatibility"] = "single_source_pending_review"
            row.pop("support_review", None)
    return result


def projection(row):
    """仅科研内容参与局部重试的同一记录比较。"""
    return {k: v for k, v in row.items() if k not in ("support_fingerprint", "source_locations", "support_review")}


def review_payload(doc):
    """正文和证据已在Reviewer输入中提供，只携带待审记录与当前参数，避免复制整份任务档案。"""
    research = doc.get("research") or {}
    records = {collection: [{k: copy.deepcopy(v) for k, v in row.items() if k != "support_review"}
                           for row in research.get(collection, [])] for collection in FIELDS}
    records["selected_candidate_id"] = research.get("selected_candidate_id")
    return {"research": records,
            "parameters": copy.deepcopy(doc.get("parameters") or (doc.get("ledger_view") or {}).get("parameters", {})),
            "scheme": copy.deepcopy(doc.get("scheme") or {})}


def cited_evidence_ids(doc):
    """结构化引用直接传给证据选包；不要从含数组括号的JSON字符串猜引用。"""
    research = (doc or {}).get("research") or {}
    return list(dict.fromkeys(cite for collection in FIELDS for row in research.get(collection, [])
                              for cite in row.get("citations", []) if isinstance(cite, str)))


def _source_view(text):
    """仅去除成对加粗/行内代码标记，保留数字、否定、条件及原文坐标。"""
    hidden, wrappers = set(), []
    from .numeric_bindings import BOUND
    for match in BOUND.finditer(text):
        start, end = match.span()
        body_start, body_end = match.span(3)
        hidden.update(range(start, body_start))
        hidden.update(range(body_end, end))
        wrappers.append((start, end, body_start, body_end))
    for match in re.finditer(r"(?<!\\)(\*\*|`)(?=\S)(.+?)(?<=\S)\1", text):
        start, end = match.span()
        body_start, body_end = match.span(2)
        hidden.update(range(start, body_start))
        hidden.update(range(body_end, end))
        wrappers.append((start, end, body_start, body_end))
    offsets = [i for i in range(len(text)) if i not in hidden]
    return "".join(text[i] for i in offsets), offsets, wrappers


def locate_sources(row, doc):
    quotes = row.get("source_quotes")
    narrative = doc.get("final_answer") or ""
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= 12 or any(not isinstance(q, str) or not q.strip() for q in quotes):
        raise ValueError("source_quotes须包含1–12条正文原句")
    locations = []
    view = None
    for index, quote in enumerate(quotes):
        start = narrative.find(quote)
        end = start + len(quote)
        if start < 0 or narrative.find(quote, start + 1) >= 0:
            if view is None:
                view = _source_view(narrative)
            plain, offsets, wrappers = view
            target = _source_view(quote)[0]
            at = plain.find(target) if target.strip() else -1
            if at < 0 or plain.find(target, at + 1) >= 0:
                from .research_sources import locate
                previous = row.get("source_locations") or []
                try:
                    locations.append(locate(quote, narrative, previous[index] if index < len(previous) else None))
                    continue
                except ValueError as exc:
                    raise ValueError(f"多段来源原句不能在当前正文唯一定位：source_quotes[{index}]；{exc}") from exc
            start, end = offsets[at], offsets[at + len(target) - 1] + 1
            for opening, closing, body_start, body_end in wrappers:
                if start <= body_start and body_end <= end:
                    start, end = min(start, opening), max(end, closing)
        from .research_sources import paragraphs, plain as normalized_source
        block = next((b for b in paragraphs(narrative) if b["start"] <= start and end <= b["end"]), None)
        location = {"start": start, "end": end, "quote_hash": digest(narrative[start:end])}
        if block:
            location.update(section_id=block["section_id"], paragraph_id=block["paragraph_id"], paragraph_hash=digest(normalized_source(block["text"])[0]))
        locations.append(location)
    return locations


def field_quotes(row, field):
    support = row.get("field_support", {}).get(field, [])
    return [row["source_quotes"][i] for i in support]


def field_citations(row, field):
    if not is_summary(row):
        return row.get("citations", [])
    quotes = "\n".join(field_quotes(row, field))
    return [c for c in row.get("citations", []) if "[" + c + "]" in quotes]


def fingerprint(row, doc, parameters, *, _legacy_conditions=None, _legacy_numbers=False):
    if row.get("projection_origin") == "writer_content_v3":
        from .stage4_research_projection import fingerprint as projection_fingerprint
        return projection_fingerprint(row, doc, parameters)
    refs = row.get("parameter_refs", [])
    cited = set(row.get("citations", []))
    narrative = (doc.get("final_answer") or "").split("\n\n## 参考来源")[0].strip()
    def parameter_view(path):
        view = {k: (parameters.get(path) or {}).get(k) for k in ("value", "unit", "quantity_kind", "denominator", "conditions")}
        if not _legacy_numbers and isinstance(view["value"], float) and view["value"].is_integer():
            view["value"] = int(view["value"])  # 写表后的1.0与原声明1具有同一数值身份。
        # 写表时补的空容器不改变科学条件；非空条件仍逐字参与身份。
        if _legacy_conditions == "raw":
            return view
        if _legacy_conditions == "writer_default":
            if view["conditions"] == {} and (parameters.get(path) or {}).get("reference_origin") == "writer_v2":
                view["conditions"] = None
            return view
        if view["conditions"] in ((None, {}) if _legacy_conditions == "dict_default" else (None, {}, [])):
            view["conditions"] = None
        return view
    from .research_sources import context_identity
    requirements = doc.get("requirements") or {}
    related = set(row.get("requirement_ids", [])) | {row.get("requirement_id")}
    requirement_view = {"source": requirements.get("source"), "items": [r for r in requirements.get("items", []) if r.get("id") in related]}
    source_hash = digest(narrative) if _legacy_conditions is not None else digest([context_identity(q, narrative) for q in row.get("source_quotes", [])])
    return digest({"record": projection(row), "narrative_hash": source_hash,
        "parameters": {p: parameter_view(p) for p in refs}, "requirements": doc.get("requirements") if _legacy_conditions is not None else requirement_view,
        "evidence": sorted([e for e in doc.get("evidence", []) if e.get("passage_id") in cited],
                           key=lambda e: str(e.get("passage_id")))})


def review_fingerprint_matches(row, doc, parameters):
    """旧审核仅兼容空条件表示变化；正文、参数值、证据等其余输入仍完整匹配。"""
    recorded = (row.get("support_review") or {}).get("fingerprint")
    return bool(recorded) and any(recorded == fingerprint(row, doc, parameters, _legacy_conditions=mode, _legacy_numbers=numbers)
                                 for mode in (None, "raw", "dict_default", "writer_default") for numbers in (False, True))


def normalize(row, collection, doc, parameters):
    if row.get("projection_origin") == "writer_content_v3":
        # v3来源由编译器定位；重复句子仍按稳定叶ID定位，不扫描全文猜测。
        result = copy.deepcopy(row)
        for source, location in zip(result.get("source_quotes", []), result.get("source_locations", [])):
            if doc.get("final_answer", "")[location["start"]:location["end"]] != source:
                raise ValueError("v3研究来源坐标失效")
        result["support_fingerprint"] = fingerprint(result, doc, parameters)
        return result
    from .research_contract import TOKEN, normalized
    from .report_narrative import amount
    result = copy.deepcopy(projection(row))
    locations = locate_sources(row, doc)
    # 保存当前正文中的真实摘录；格式归一不授予摘要科学支持性。
    result["source_quotes"] = [doc["final_answer"][p["start"]:p["end"]] for p in locations]
    requested = set(re.findall(r"\[([\w:-]+)\]", "\n".join(result["source_quotes"]))) - {"FSI", "TFSI", "PF6", "BF4"}
    known = {e.get("passage_id") for e in doc.get("evidence", [])}
    if requested - known:
        raise ValueError("多段来源含未归档引用")
    support = result.get("field_support")
    if not isinstance(support, dict) or set(support) - set(FIELDS[collection]):
        raise ValueError("field_support字段与研究记录类型不一致")
    for field in FIELDS[collection]:
        value = result.get(field, "")
        if not isinstance(value, str):
            raise ValueError("研究摘要文本必须为字符串")
        if not value.strip():
            continue
        indices = support.get(field)
        if (not isinstance(indices, list) or not indices or any(isinstance(i, bool) or not isinstance(i, int)
            or not 0 <= i < len(result["source_quotes"]) for i in indices)):
            raise ValueError("摘要每个非空字段须关联有效来源下标：" + field)
        source = "\n".join(field_quotes(result, field))
        for ref in TOKEN.findall(value):
            parameter = parameters.get(ref) or {}
            if parameter.get("value") is None:
                raise ValueError("摘要引用未知主方案参数：" + ref)
            from .material_ids import source_matches
            if not source_matches(ref, parameter["value"], source) and normalized(amount(ref, parameter)) not in normalized(source):
                # 参数已经有程序签发的正文引用时，补上对应真实原句；不用同值数字猜绑定。
                from .numeric_bindings import occurrences, binding_errors
                from .research_sources import paragraphs
                candidates = [b["text"] for b in paragraphs(doc.get("final_answer", ""))
                              if any(kind == "param" and key == ref for _, _, kind, key, _ in occurrences(b["text"]))
                              and not binding_errors(b["text"], parameters)]
                if not candidates:
                    raise ValueError("参数引用与该字段多段依据不一致：" + ref)
                added = min(candidates, key=len)
                if added not in result["source_quotes"]:
                    if len(result["source_quotes"]) >= 12:
                        raise ValueError("参数来源补充超过单项上限：" + ref)
                    result["source_quotes"].append(added)
                index = result["source_quotes"].index(added)
                if index not in support[field]:
                    support[field].append(index)
                source = "\n".join(field_quotes(result, field))
        # 数值仅做必要事实检查；条件和材料含义还须Reviewer核对。
        def numeric_tokens(text):
            text = re.sub(r"\[[\w:-]+\]|\{\{[^}]+\}\}", "", text)
            return set(re.findall(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text))
        if numeric_tokens(value) - numeric_tokens(source):
            raise ValueError("摘要含所绑定原句中没有的数值：" + field)
        requested = set(re.findall(r"\[([\w:-]+)\]", value)) - {"FSI", "TFSI", "PF6", "BF4"}
        if requested - set(field_citations(result, field)):
            raise ValueError("摘要字段引用不属于其绑定的原句：" + field)
    result["source_locations"] = locate_sources(result, doc)
    result["support_fingerprint"] = fingerprint(result, doc, parameters)
    return result


def supported(row, field, doc, parameters):
    if row.get("projection_origin") == "writer_content_v3":
        from .stage4_research_projection import field_fingerprint as content_field_fingerprint
        verdict = (row.get("support_review") or {}).get("fields", {}).get(field, {})
        return verdict.get("status") == "supported" and verdict.get("fingerprint") == content_field_fingerprint(row, field, doc, parameters)
    if not is_summary(row):
        return True
    review = row.get("support_review") or {}
    entry = review.get("fields", {}).get(field, {})
    if entry.get("field_fingerprint"):
        return entry.get("status") == "supported" and any(entry["field_fingerprint"] == field_fingerprint(row, field, doc, parameters, _legacy_numbers=old)
                                                           for old in (False, True))
    return (review_fingerprint_matches(row, doc, parameters)
            and review.get("fields", {}).get(field, {}).get("status") == "supported")


def field_fingerprint(row, field, doc, parameters, *, _legacy_numbers=False):
    """字段只绑定本字段来源及实际使用参数，其余字段变化不撤销当前结论。"""
    from .research_contract import TOKEN
    fragment = {k: copy.deepcopy(row[k]) for k in ("id", "requirement_id", "requirement_ids", "verification", "basis", "scope", "status") if k in row}
    quotes = field_quotes(row, field)
    fragment.update(source_quotes=quotes, field_support={field: list(range(len(quotes)))},
                    parameter_refs=list(dict.fromkeys(TOKEN.findall(row.get(field, "")))), citations=field_citations(row, field))
    fragment[field] = row.get(field, "")
    return fingerprint(fragment, doc, parameters, _legacy_numbers=_legacy_numbers)


def render_field(row, field, doc):
    """缺审核的字段只呈现真实摘录，不将自由摘要提升为结论。"""
    from .research_reporting import substitute
    if field == "response" and row.get("status") == "gap" and not (row.get("source_quote") or row.get("source_quotes")):
        # 摘要被拒后仍能展示经独立定位的原句；绝不使用失败摘要或信任旧缓存位置。
        errors = (doc.get("research") or {}).get("record_errors", [])
        error = next((e for e in errors if e.get("collection") == "answers" and e.get("id") == row.get("requirement_id")), None)
        rejected = (error or {}).get("record") or {}
        quotes = rejected.get("source_quotes") or [rejected.get("source_quote", "")]
        indices = (rejected.get("field_support") or {}).get(field)
        if isinstance(indices, list) and indices and all(type(i) is int and 0 <= i < len(quotes) for i in indices):
            quotes = [quotes[i] for i in indices]
        known = {e.get("passage_id") for e in doc.get("evidence", [])}
        valid = [q for q in quotes if isinstance(q, str) and q and q in (doc.get("final_answer") or "")
                 and not (set(re.findall(r"\[([\w:-]+)\]", q)) - known - {"FSI", "TFSI", "PF6", "BF4"})]
        if valid:
            return "归档原文（研究回答待核验）：" + "；".join(dict.fromkeys(valid)) + "；提取失败原因：" + error["reason"]
    if not is_summary(row) or not row.get(field):
        return substitute(row.get(field), doc)
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    if supported(row, field, doc, params):
        return substitute(row[field], doc)
    quotes = field_quotes(row, field)
    status = (row.get("support_review") or {}).get("fields", {}).get(field, {})
    reason = status.get("reason", "尚未取得当前输入的支持性审核")
    return "归档原文（分析摘要待核验）：" + "；".join(quotes) + "；核验说明：" + reason


def apply_review(research, verdicts, doc, parameters, reviewed_evidence=None):
    """程序才可写入审核标记；逐字段未知/缺失项保留待审。"""
    rows = verdicts if isinstance(verdicts, list) else []
    for collection in FIELDS:
        for row in research.get(collection, []):
            if not is_summary(row):
                continue
            key = row.get("requirement_id") if collection == "answers" else row.get("id")
            fp = fingerprint(row, doc, parameters)
            matches = [v for v in rows if isinstance(v, dict) and v.get("collection") == collection
                       and v.get("id") == key and v.get("fingerprint") == fp]
            fields = {}
            verdict = matches[0] if len(matches) == 1 else {}
            for field in FIELDS[collection]:
                if not row.get(field):
                    continue
                value = verdict.get("fields", {}).get(field, {}) if isinstance(verdict.get("fields"), dict) else {}
                status = value.get("status") if isinstance(value, dict) else None
                reason = value.get("reason") if isinstance(value, dict) else None
                if not isinstance(status, str) or status not in STATUS or not isinstance(reason, str) or not reason.strip():
                    status, reason = "needs_review", "缺少匹配指纹的有效摘要支持性审核"
                available = {e.get("passage_id") for e in reviewed_evidence} if reviewed_evidence is not None else None
                if status == "supported" and available is not None and set(field_citations(row, field)) - available:
                    status, reason = "needs_review", "该字段依赖的文献未进入本次审核证据包"
                fields[field] = {"status": status, "reason": reason}
                if row.get("projection_origin") == "writer_content_v3":
                    from .stage4_research_projection import field_fingerprint as content_field_fingerprint
                    fields[field]["fingerprint"] = content_field_fingerprint(row, field, doc, parameters)
                else:
                    fields[field]["field_fingerprint"] = field_fingerprint(row, field, doc, parameters)
            row["support_review"] = {"fingerprint": fp, "fields": fields}
    return research


def review_pending(research, doc, parameters):
    return [(collection, row, field) for collection, fields in FIELDS.items() for row in research.get(collection, [])
            if is_summary(row) for field in fields if row.get(field) and not supported(row, field, doc, parameters)]


def review_signature(doc, parameters):
    """审核版本独立于补丁复核版本；补丁通过不能冒充研究摘要完成审核。"""
    research = doc.get("research") or {}
    return digest({"narrative": doc.get("final_answer"), "requirements": doc.get("requirements"),
        "records": {c: [fingerprint(row, doc, parameters) for row in research.get(c, [])] for c in FIELDS},
        "record_errors": research.get("record_errors", [])})


def missing_support_fields(research, doc, parameters):
    """只补漏返字段，科学否决和证据包缺失不当作格式遗漏。"""
    return [{"collection": collection, "id": row.get("requirement_id") or row.get("id"),
             "fingerprint": row.get("support_fingerprint"), "field": field}
            for collection, row, field in review_pending(research, doc, parameters)
            if ((row.get("support_review") or {}).get("fields", {}).get(field) or {}).get("reason")
            == "缺少匹配指纹的有效摘要支持性审核"]


def prune_resolved_review_diagnostics(doc):
    """归档已经补齐的漏审诊断；科学争议不因记录恢复而删除。"""
    research = doc.get("research") or {}
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    records = {(c, row.get("requirement_id") or row.get("id")): row
               for c in FIELDS for row in research.get(c, [])}
    archived = []
    for container in (doc.get("reviewer_output"), doc.get("report_readiness")):
        if not isinstance(container, dict):
            continue
        retained = []
        for issue in container.get("issues", []):
            match = re.fullmatch(r"研究摘要待核验：(answers|candidates|arguments)\[([^\]]+)\]\.(\w+)：缺少匹配指纹的有效摘要支持性审核", str(issue))
            row = records.get((match[1], match[2])) if match else None
            review = (row or {}).get("support_review") or {}
            field = review.get("fields", {}).get(match[3], {}) if match else {}
            if (row and review_fingerprint_matches(row, doc, params)
                    and field.get("status") in STATUS and field.get("reason")
                    and field["reason"] != "缺少匹配指纹的有效摘要支持性审核"):
                archived.append(issue)
            else:
                retained.append(issue)
        container["issues"] = list(dict.fromkeys(retained))
    if archived:
        doc["research_diagnostic_history"] = list(dict.fromkeys(doc.get("research_diagnostic_history", []) + archived))


def refresh_for_review(pipeline, seed, text, parameters, evidence, *, repair_records=True):
    """已有维护预算内刷新受正文修改影响的摘要，不能沿用旧审核标记。"""
    from .research_records import collect_research
    from .scheme_ledger import collect_parameters
    prior = seed.get("research_draft")
    if not prior:
        return None
    doc = copy.deepcopy(prior)
    doc.update(final_answer=text, evidence=evidence, ledger_view={"parameters": parameters}, parameters=parameters)
    if isinstance(doc.get("scheme_parameters"), dict):
        doc["scheme_parameters"] = {k: copy.deepcopy(parameters[k]) for k in doc["scheme_parameters"] if k in parameters}
    from .material_ids import assemble_components
    doc["scheme"] = assemble_components(seed.get("scheme") or prior.get("scheme"), parameters, strict=False)
    # 正文、文献、参数任一变化均逐项重验；收尾前保留一次提取预算。
    doc["research"] = collect_research({}, doc, parameters, previous=prior.get("research"))
    used = seed.get("maintenance_calls", prior.get("maintenance_calls", 0))
    remaining = max(0, 2 - used)
    if repair_records and doc["research"].get("record_errors") and remaining:
        doc["research_only"] = True
        collect_parameters(doc, getattr(pipeline.writer, "llm", None), max_attempts=remaining)
        doc.pop("research_only", None)
        seed["maintenance_calls"] = used + doc.get("maintenance_calls", 0)
        doc["maintenance_calls"] = seed["maintenance_calls"]
    seed["research_draft"] = doc
    pipeline.reviewer.research_draft = doc
    return doc


def finish_review(pipeline, seed, verdict, parameters, evidence, reviewed_evidence=None):
    doc = seed.get("research_draft")
    if not doc:
        return
    doc["evidence"] = evidence
    from .requirement_review import normalize_review
    normalize_review(verdict, dict(doc, parameters=parameters))
    apply_review(doc["research"], verdict.get("research_support_review"), doc, parameters, reviewed_evidence)
    if verdict.get("error_type") != "reviewer":
        seed["research_reviewed_signature"] = review_signature(doc, parameters)
    pipeline.reviewer.research_draft = doc
    # 记录层审核问题只授权研究记录修复，不伪造正文quote或申请改配方权限。
    pending = review_pending(doc["research"], doc, parameters)
    for collection, row, field in pending:
        status = (row.get("support_review") or {}).get("fields", {}).get(field, {})
        key = row.get("requirement_id") if collection == "answers" else row.get("id")
        message = f"研究摘要待核验：{collection}[{key}].{field}：{status.get('reason', '尚未完成支持性审核')}"
        if message not in verdict.setdefault("issues", []):
            verdict["issues"].append(message)
    if pending:
        verdict["confidence"] = "low"


def selected_candidate(doc):
    """未核验的选择理由不能据此成为报告首选。"""
    research = doc.get("research") or {}
    row = next((c for c in research.get("candidates", []) if c.get("id") == research.get("selected_candidate_id")), None)
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    return row if row and (not is_summary(row) or all(supported(row, f, doc, params) for f in FIELDS["candidates"] if row.get(f))) else None
