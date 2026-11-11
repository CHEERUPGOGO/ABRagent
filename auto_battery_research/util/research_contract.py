"""通用课题契约：语义由模型提出，原文定位、引用和主方案绑定由程序校验。"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata

VERSION = 1
KINDS = {"objective", "constraint", "preference", "deliverable", "background"}
STATES = {"clear", "ambiguous", "conflicting"}
RESPONSES = {"answered", "partial", "gap", "omitted"}
VERIFICATIONS = {"unverified", "literature", "calculation", "experiment", "not_applicable"}

PLANNER_INSTRUCTIONS = '''
同时输出 requirements 对象：{"task_type":"design|comparison|process|mechanism|experiment|query|other", "scheme_required":true或false,
"items":[{"id":"r1","quote":"用户原文逐字摘录", "start":可选原文起点,
"kind":"objective|constraint|preference|deliverable|background", "subject":"研究对象", "description":"单项要求",
"conditions":["条件原文"], "quantity":null或{"value":数值,"unit":"原文单位","operator":">等原文运算符"},
"related_ids":[], "status":"clear|ambiguous|conflicting", "verification_needed":true或false, "evidence_needed":true或false,
"binding":null或{"path":"electrolyte.salt等方案字段","value":"用户明确给定的值","unit":"规范单位"}}],
"query_links":[{"query_index":0,"requirement_ids":["r1"]}]}。
按语义识别所有要求，不以材料/指标词典限制范围；陌生材料、设备、工艺、成本、排除项和交付形式也须保留。
并列要求分别列出，共享条件通过conditions/related_ids绑定；背景文献结果不是用户目标。
区分必须、偏好、备选、对照和否定；只有用户明确指定的正向固定值可有binding，禁止给偏好或排除项绑定材料。
不把“显著改善”“低成本”等转成数值，不推定未给定的圈数、比例、阈值。条件须逐字来自原文。
quote必须可定位；同一quote可拆多项，id唯一。未确定语义标ambiguous，不忽略。研究对象、指标名称、描述均为开放文本。
查询可覆盖多个要求；2–4个查询只是常用建议，不限制要求条数。不强加比能量、材料排行或配方设计任务。
scheme_required只在用户要求设计或确定电芯/材料配方时为true；纯机理、数值查询、工艺比较不要求完整电芯。
evidence_needed说明该要求是否需要文献/理论证据；排版格式、文件交付和用户固定值为false，不计入检索覆盖分母。
'''

COLLECTION_INSTRUCTIONS = '''
本次有通用课题契约，另外输出 research={"answers":[],"candidates":[],"arguments":[],"selected_candidate_id":null}。
answers每项：requirement_id, status(answered/partial/gap/omitted), response, source_quote(正文逐字摘录),
verification(unverified/literature/calculation/experiment/not_applicable), citations[], reason, next_step, parameter_refs[]。
逐项回应非background要求。answered须有实质回答及正文原句；partial/gap须明确原因和后续验证/获取方法。
尚未执行的实验/本任务计算不能标experiment/calculation；文献仅证明所引研究对象，当前设计保持unverified。
candidates每项：id,name,rank(探索顺序或null),scope(candidate/control/literature),source_quote,
summary, rationale, risks, citations[], parameter_refs[]。仅选一个探索性主方案；它的完整材料、名义式、
改性元素/位点/掺量及口径、电解液组成须在parameters中声明，selected_candidate_id指向它。
主方案parameters包括cathode.nominal_formula、cathode.doping.*等开放字段；比例注明分母，添加剂候选不能进入主方案。
选中项不复制独立配方，只通过parameter_refs引用parameters或已有方案表；其他候选保留各自原文，禁止充当当前值。
arguments每项：requirement_ids[],title,text,source_quote,citations[],basis(literature/inference),parameter_refs[]。
保留有来源和适用条件的数值论证，按措施→机制→证据→验证组织。不恢复原文主观分数。
response/summary/rationale/risks/text/reason/next_step须摘自已审核正文，不创作新结论；主方案数值用{{字段路径}}引用。
非设计问题允许无候选、无选中项；没有证据时保留缺口，不填示例材料。'''


def normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold()


def field_equal(path, left, right):
    if normalized(left) == normalized(right):
        return True
    if path.endswith(".material") and left is not None and right is not None:
        from auto_battery_research.rag import RelationEngine
        engine = RelationEngine()
        a, b = engine.normalize(str(left)), engine.normalize(str(right))
        return a is not None and a == b
    return False


def _id(quote, description, start):
    raw = f"{start}:{quote}:{description}"
    return "req_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def fallback_requirements(goal, reason="语义解析未完成"):
    items = []
    # 分句只保留文本，不声称已判断约束强度或理解复杂条件。
    for match in re.finditer(r"[^。；;\n]+[。；;]?", goal):
        quote = match[0].strip()
        if quote:
            start = goal.index(quote, match.start())
            items.append({"id": _id(quote, quote, start), "quote": quote, "start": start,
                "end": start + len(quote), "kind": "deliverable", "subject": "",
                "description": quote, "conditions": [], "quantity": None, "related_ids": [],
                "status": "ambiguous", "verification_needed": False, "binding": None})
    return {"version": VERSION, "source": goal, "status": "unparsed", "items": items,
            "task_type": "other", "scheme_required": True, "query_links": [], "diagnostics": [reason]}


def normalize_requirements(raw, goal, queries=()):
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list) or not raw["items"]:
        raise ValueError("缺少通用课题要求items")
    if raw.get("task_type", "other") not in ("design", "comparison", "process", "mechanism", "experiment", "query", "other"):
        raise ValueError("未知任务类型应使用other并保留原文")
    if not isinstance(raw.get("scheme_required", True), bool):
        raise ValueError("scheme_required必须为布尔值")
    items, mapping = [], {}
    for row in raw["items"]:
        if not isinstance(row, dict):
            raise ValueError("要求必须为对象")
        row = copy.deepcopy(row)
        quote, description = row.get("quote"), row.get("description")
        if not isinstance(quote, str) or not quote or quote not in goal or not isinstance(description, str) or not description.strip():
            raise ValueError("要求缺少可定位的原文或单项描述")
        start = row.get("start")
        if not isinstance(start, int) or goal[start:start + len(quote)] != quote:
            if goal.count(quote) != 1:
                raise ValueError("重复原文必须提供正确start")
            start = goal.index(quote)
        if row.get("kind") not in KINDS or row.get("status", "clear") not in STATES:
            raise ValueError("要求类型或解析状态非法")
        conditions = row.get("conditions", [])
        if not isinstance(conditions, list) or any(not isinstance(c, str) or not c or c not in goal for c in conditions):
            raise ValueError("要求条件必须来自用户原文")
        quantity = row.get("quantity")
        if quantity is not None:
            if not isinstance(quantity, dict) or not all(str(quantity.get(k, "")) for k in ("value", "unit", "operator")):
                raise ValueError("数量声明缺少数值、单位或运算符")
            number = quantity["value"]
            number = f"{number:g}" if isinstance(number, (int, float)) and not isinstance(number, bool) else number
            if any(normalized(v) not in normalized(quote + " ".join(conditions)) for v in (number, quantity["unit"], quantity["operator"])):
                raise ValueError("数量及比较符号不能由模型补造")
        old_id = row.get("id")
        if not isinstance(old_id, str) or old_id in mapping:
            raise ValueError("要求id缺失或重复")
        stable = _id(quote, description, start)
        if stable in mapping.values():
            raise ValueError("重复要求")
        mapping[old_id] = stable
        binding = row.get("binding")
        if binding is not None:
            from .scheme_ledger import validate_field
            if row["kind"] != "constraint" or row.get("status", "clear") != "clear" or not isinstance(binding, dict):
                raise ValueError("只有明确的用户固定约束可以绑定字段")
            path = binding.get("path", "")
            raw_value = binding.get("value")
            token = f"{raw_value:g}" if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool) else str(raw_value)
            if raw_value is None or normalized(token) not in normalized(quote):
                raise ValueError("固定值必须直接出现在用户原句，不能用候选或推断值")
            validate_field(path, dict(binding, basis="user_requirement", source=quote))
        row.update(id=stable, start=start, end=start + len(quote), status=row.get("status", "clear"),
                   conditions=conditions, quantity=quantity, binding=binding,
                   verification_needed=row.get("verification_needed") is True)
        if "evidence_needed" in row and not isinstance(row["evidence_needed"], bool):
            raise ValueError("evidence_needed须为布尔值")
        items.append(row)
    for item in items:
        relations = item.get("related_ids", [])
        if not isinstance(relations, list) or any(r not in mapping for r in relations):
            raise ValueError("条件关联引用未知要求")
        item["related_ids"] = [mapping[r] for r in relations]
    links = []
    for link in raw.get("query_links", []):
        n, ids = link.get("query_index"), link.get("requirement_ids")
        if not isinstance(n, int) or not 0 <= n < len(queries) or not isinstance(ids, list) or any(i not in mapping for i in ids):
            raise ValueError("检索关联非法")
        links.append({"query_index": n, "requirement_ids": [mapping[i] for i in ids]})
    return {"version": VERSION, "source": goal, "status": "parsed", "items": items,
            "task_type": raw.get("task_type", "other"), "scheme_required": raw.get("scheme_required", True) is not False,
            "query_links": links, "diagnostics": []}


def attach_requirements(plan, goal, client=None):
    """复用Planner输出，结构错误最多局部修复一次，失败明确降级。"""
    try:
        plan["requirements"] = normalize_requirements(plan.get("requirements"), goal, plan.get("retrieval_queries", []))
        return plan
    except (ValueError, TypeError, KeyError) as exc:
        error = str(exc)
    if client is not None and getattr(client, "available", False):
        try:
            raw = client.chat("仅修复requirements JSON，其他规划没有修改权限。" + PLANNER_INSTRUCTIONS,
                json.dumps({"goal": goal, "queries": plan.get("retrieval_queries", []),
                            "requirements": plan.get("requirements"), "error": error}, ensure_ascii=False),
                temperature=0.1, trace_tag="requirements_repair")
            fixed = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            plan["requirements"] = normalize_requirements(fixed.get("requirements", fixed), goal, plan.get("retrieval_queries", []))
            return plan
        except Exception as exc:
            error += "; " + str(exc)
    plan["requirements"] = fallback_requirements(goal, error)
    return plan


def bindings(contract):
    result = {}
    for item in (contract or {}).get("items", []):
        b = item.get("binding")
        if b:
            field = {"value": b["value"], "unit": b.get("unit", ""), "basis": "user_requirement",
                     "source": "课题明确声明", "source_quote": item["quote"], "requirement_id": item["id"],
                     "locked": True, "meaning": item["description"], "conditions": item["conditions"]}
            if b["path"] in result and result[b["path"]]["value"] != field["value"]:
                raise ValueError("用户要求中的固定值冲突: " + b["path"])
            result[b["path"]] = field
    return result


def gap_answer(item, reason="尚未形成可采信的回答"):
    return {"requirement_id": item["id"], "status": "gap", "response": "待补充",
            "verification": "unverified", "source_quote": "", "citations": [], "parameter_refs": [],
            "reason": reason, "next_step": "围绕原始要求「" + item["quote"] + "」补充证据与审核；确认适用条件后再制定验证方法。"}


def fallback_research(contract, reason):
    return {"version": VERSION, "status": "partial", "answers": [gap_answer(i, reason)
        for i in contract["items"] if i["kind"] != "background"], "candidates": [], "arguments": [],
        "selected_candidate_id": None, "diagnostics": [reason]}


TOKEN = re.compile(r"\{\{([A-Za-z0-9_.-]+)\}\}")


def validate_research(raw, doc, parameters):
    """只接受可定位于已审核正文的摘录，选中项数值通过方案表引用。"""
    if not isinstance(raw, dict):
        raise ValueError("缺少research结构化课题回答")
    contract = doc["requirements"]
    required = {i["id"] for i in contract["items"] if i["kind"] != "background"}
    known = {str(e.get("passage_id")) for e in doc.get("evidence", [])}
    narrative = doc.get("final_answer", "")
    from .report_narrative import amount
    def check(row, fields):
        if not isinstance(row, dict):
            raise ValueError("研究记录必须为对象")
        quote = row.get("source_quote", "")
        if not quote or quote not in narrative:
            raise ValueError("研究记录缺少已审核正文中的source_quote")
        refs = row.get("parameter_refs", [])
        if not isinstance(refs, list) or any(p not in parameters or parameters[p].get("value") is None for p in refs):
            raise ValueError("研究记录引用未知主方案参数")
        for key in fields:
            value = row.get(key, "")
            if not isinstance(value, str):
                raise ValueError("研究记录文本非法")
            if set(TOKEN.findall(value)) - set(refs):
                raise ValueError("研究文本引用未声明的主方案字段")
            # 只允许摘录或参数替换；不允许维护模型另写新的科学结论。
            chunks = TOKEN.split(value)
            if any(part.strip() and normalized(part) not in normalized(quote) for part in chunks[::2]):
                raise ValueError("研究结论必须摘自支持原句: " + key)
            for path in chunks[1::2]:
                v = parameters[path]
                if normalized(v["value"]) not in normalized(quote) and normalized(amount(path, v)) not in normalized(quote):
                    raise ValueError("参数引用与支持原句不一致: " + path)
        cites = row.get("citations", [])
        if not isinstance(cites, list) or any(not isinstance(c, str) for c in cites) or set(cites) - known:
            raise ValueError("研究记录引用未归档文献")
        if any("[" + c + "]" not in quote for c in cites):
            raise ValueError("引用必须同时出现于支持原句，不能挪用其他证据")
        if set(re.findall(r"\[([\w:-]+)\]", quote)) - known - {"FSI", "TFSI", "PF6", "BF4"}:
            raise ValueError("支持原句含未知引用")
    answers = raw.get("answers")
    if not isinstance(answers, list) or any(not isinstance(a, dict) for a in answers) or len(answers) != len(required) or {a.get("requirement_id") for a in answers} != required:
        raise ValueError("研究回答遗漏或重复课题要求")
    for row in answers:
        if row.get("status") not in RESPONSES or row.get("verification") not in VERIFICATIONS:
            raise ValueError("回应与验证状态非法")
        check(row, ("response", "reason", "next_step"))
        if row["status"] == "answered" and len(row.get("response", "").strip()) < 8:
            raise ValueError("只有标题不能算实质回应")
        if row["status"] in ("gap", "partial") and not (row.get("reason") and row.get("next_step")):
            raise ValueError("缺口必须包含原因及获取/验证方法")
        if row["verification"] in ("calculation", "experiment"):
            raise ValueError("Stage 4 不得宣称本课题计算或实验已验证")
        if row["verification"] == "literature" and not row.get("citations"):
            raise ValueError("文献支持须引用归档证据")
    candidates, arguments = raw.get("candidates", []), raw.get("arguments", [])
    if not isinstance(candidates, list) or not isinstance(arguments, list):
        raise ValueError("候选与论证必须为数组")
    ids = set()
    for row in candidates:
        check(row, ("summary", "rationale", "risks"))
        if not isinstance(row.get("id"), str) or row["id"] in ids or row.get("scope") not in ("candidate", "control", "literature"):
            raise ValueError("候选身份或范围非法")
        ids.add(row["id"])
        if not row.get("name") or normalized(row["name"]) not in normalized(row["source_quote"]):
            raise ValueError("候选名称须来自支持原句")
        if row.get("rank") is not None and (not isinstance(row["rank"], int) or row["rank"] < 1 or str(row["rank"]) not in row["source_quote"]):
            raise ValueError("候选顺序须来自已审核正文")
    selected = raw.get("selected_candidate_id")
    if selected is not None:
        current = next((c for c in candidates if c["id"] == selected), None)
        if not current or current["scope"] != "candidate" or not current.get("parameter_refs"):
            raise ValueError("探索性首选必须引用当前主方案参数")
        identity = {p for p in parameters if p.endswith(".material") or p.startswith("electrolyte.")
                    or any(t in p for t in (".doping.", ".dopant", ".nominal_formula", ".composition", ".coating"))}
        if not identity.issubset(current["parameter_refs"]):
            raise ValueError("探索性首选没有引用完整主方案身份")
    for row in arguments:
        check(row, ("text",))
        if not row.get("requirement_ids") or set(row["requirement_ids"]) - required or row.get("basis") not in ("literature", "inference"):
            raise ValueError("论证缺少有效课题关联或依据类型")
        if row["basis"] == "literature" and not row.get("citations"):
            raise ValueError("文献论证缺少引用")
        if not isinstance(row.get("title", ""), str) or row.get("title") and normalized(row["title"]) not in normalized(row["source_quote"]):
            raise ValueError("论证标题须摘自支持原句或留空")
    result = copy.deepcopy(raw)
    identity = {p: copy.deepcopy(v) for p, v in parameters.items() if p.endswith(".material") or p.startswith("electrolyte.")
                or any(t in p for t in (".doping.", ".dopant", ".nominal_formula", ".composition", ".coating"))}
    result["bound_fields"] = {p: {"value": v.get("value"), "unit": v.get("unit", "")} for p, v in identity.items()}
    result.update(version=VERSION, status="complete" if all(a["status"] == "answered" for a in answers) else "partial", diagnostics=[])
    return result


def prepare_review(pipeline, seed):
    """结构化主方案先进入Reviewer上下文；适配器复用同一维护结果。"""
    requirements = (seed.get("plan") or {}).get("requirements")
    if not requirements:
        return
    from .scheme_ledger import collect_parameters, flatten_scheme
    draft_doc = {"target": requirements["source"], "requirements": requirements,
                 "final_answer": seed["draft"], "scheme": seed.get("scheme", {}),
                 "evidence": seed["evidence"], "ledger_view": copy.deepcopy(getattr(pipeline, "scheme_context", {}) or {}),
                 "reviewer_output": {}}
    if seed.get("retrieval_state"):
        from .adaptive_retrieval import select_context, options
        selected = select_context(seed, purpose="scheme_maintenance", text=seed["draft"], config=options(pipeline))
        draft_doc["evidence"] = selected
    collect_parameters(draft_doc, getattr(pipeline.writer, "llm", None))
    seed["research_draft"] = draft_doc
    params = copy.deepcopy(draft_doc.get("ledger_view", {}).get("parameters", {}))
    for path, value in flatten_scheme(seed.get("scheme", {})).items():
        params.setdefault(path, {"value": value, "unit": "", "basis": "engineering_proposal", "source": "初始设计"})
    params.update(draft_doc.get("scheme_parameters", {}))
    params.update(bindings(requirements))
    seed["parameters"] = params
    from auto_battery_research.rag import RelationEngine
    engine = RelationEngine()
    for comp in ("cathode", "anode", "electrolyte", "separator", "current_collector"):
        field = params.get(comp + ".material")
        if field:
            seed.setdefault("scheme", {})[comp] = engine.normalize(str(field["value"])) or field["value"]
    if "electrolyte.additives" in params:
        seed.setdefault("scheme", {})["additives"] = params["electrolyte.additives"]["value"]
    pipeline.reviewer.research_draft = {"requirements": requirements, "research": draft_doc.get("research"), "parameters": params}


def document_errors(doc):
    """不信任磁盘JSON形状，损坏契约返回诊断而不是绕过校验或崩溃。"""
    try:
        return _document_errors(doc)
    except (TypeError, KeyError, AttributeError, ValueError) as exc:
        return ["研究契约损坏：" + str(exc)]


def _document_errors(doc):
    if not doc.get("requirements"):
        return []
    errors = []
    contract = doc["requirements"]
    if not isinstance(contract, dict) or contract.get("version") != VERSION or not isinstance(contract.get("source"), str):
        return ["课题契约版本或结构无效"]
    if contract.get("source") != doc.get("target"):
        errors.append("课题契约原文与当前课题不一致")
    items = contract.get("items", [])
    if not isinstance(items, list) or not items:
        return ["课题要求清单为空或结构非法"]
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("start"), int) or not isinstance(item.get("end"), int) or not isinstance(item.get("quote"), str):
            return ["课题原文定位结构无效"]
        if contract["source"][item["start"]:item["end"]] != item["quote"]:
            errors.append("课题原文定位失效")
    expected = {i["id"] for i in items if i["kind"] != "background"}
    research = doc.get("research") or {}
    answers = research.get("answers", [])
    if not isinstance(answers, list) or any(not isinstance(a, dict) for a in answers):
        return errors + ["课题回应记录结构无效"]
    if len(answers) != len(expected) or {a.get("requirement_id") for a in answers} != expected:
        errors.append("课题回应记录缺失或重复")
    for a in answers:
        if a.get("status") not in RESPONSES or a.get("verification") not in VERIFICATIONS:
            errors.append("课题回应或验证状态非法")
        if a.get("status") == "omitted" or a.get("status") in ("gap", "partial") and not (a.get("reason") and a.get("next_step")):
            errors.append("课题要求未得到回答或完整缺口说明")
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    for path, row in bindings(contract).items():
        actual = params.get(path, {})
        if not field_equal(path, actual.get("value"), row["value"]):
            errors.append("用户固定要求与主方案不一致: " + path)
    for row in research.get("candidates", []) + research.get("arguments", []) + answers:
        if any(p not in params for p in row.get("parameter_refs", [])):
            errors.append("研究记录引用的方案字段不存在")
    if research.get("status") == "complete" and contract.get("status") != "parsed":
        errors.append("未解析的课题不能宣称覆盖完成")
    for path, bound in research.get("bound_fields", {}).items():
        actual = params.get(path, {})
        if not field_equal(path, bound.get("value"), actual.get("value")) or bound.get("unit", "") != actual.get("unit", ""):
            errors.append("研究论证基于旧主方案，须重新审核: " + path)
    selected = research.get("selected_candidate_id")
    if selected is not None and not any(c.get("id") == selected and c.get("scope") == "candidate" for c in research.get("candidates", [])):
        errors.append("主方案选中标识无效")
    return errors


def finalize_research(doc):
    """预算耗尽后保留真实缺口；独立审核发现的漏项仍进入最终覆盖表。"""
    contract = doc["requirements"]
    research = doc.setdefault("research", fallback_research(contract, "未形成结构化回答"))
    review = (doc.get("reviewer_output") or {}).get("requirement_review") or {}
    for quote in review.get("missing_quotes", []):
        if isinstance(quote, str) and quote and quote in contract["source"]:
            new = fallback_requirements(quote)["items"][0]
            new.update(quote=quote, description=quote, start=contract["source"].index(quote))
            new["end"] = new["start"] + len(quote)
            new["id"] = _id(quote, quote, new["start"])
            if not any(i["quote"] == quote for i in contract["items"]):
                contract["items"].append(new)
                research["answers"].append(gap_answer(new, "独立审核发现原需求清单遗漏，尚未完成针对性回答"))
    invalid = set(review.get("misread_ids", [])) | set(review.get("unanswered_ids", []))
    for n, answer in enumerate(research["answers"]):
        if answer["requirement_id"] in invalid or answer.get("status") == "omitted":
            item = next(i for i in contract["items"] if i["id"] == answer["requirement_id"])
            research["answers"][n] = gap_answer(item, "审核指出要求误读或回答不完整，修复预算已耗尽")
    issues = document_errors(doc)
    if issues:
        research.clear()
        research.update(fallback_research(contract, "；".join(issues)))
    reviewed = review.get("source_checked") is True and not review.get("missing_quotes") and not review.get("misread_ids") and not review.get("unanswered_ids")
    research["coverage_reviewed"] = reviewed
    if not reviewed:
        research.setdefault("diagnostics", []).append("原始课题与逐项回应尚未完成无遗漏审核，不能宣称覆盖已获确认。")
    if contract.get("status") != "parsed" or research.get("diagnostics") or any(a["status"] != "answered" for a in research["answers"]):
        state = doc.get("report_readiness")
        if state:
            if state["category"] == "COMPLETE":
                state["category"] = "CONDITIONAL"
            state["issues"].append("部分课题要求尚待证据补充或语义复核，逐项回应与获取路径见第1、2节。")
        doc["review_status"] = "REVIEWED_WITH_ISSUES"
    research["status"] = "complete" if reviewed and contract.get("status") == "parsed" and all(a["status"] == "answered" for a in research["answers"]) else "partial"


def requires_scheme(doc):
    contract = doc.get("requirements") or (doc.get("plan") or {}).get("requirements") or {}
    return contract.get("scheme_required", True)
