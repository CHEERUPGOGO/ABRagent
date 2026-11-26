"""Stage 4收尾整合研究论证；固定科研输入，独立审核后由Stage 6只读呈现。"""
import copy
import json
import re
import unicodedata

from .scheme_ledger import digest, research_parameters
from .research_support import FIELDS, field_quotes, field_citations, is_summary, supported

VERSION = 1
MAX_ATTEMPTS = 2
GENERATION_PROMPT = '''你将已有科研记录组织为研报第二部分的连贯论证及第三部分的计算说明，只输出JSON：
{"sections":[{"id":"s1","title":"依据原始课题拟定的简短主题","paragraphs":[
{"id":"p1","text":"详细论证、比较表格或列表","requirement_ids":["原需求ID"],
 "source_ids":["sources中的ID"],"parameter_refs":["parameters中的字段"],
 "citations":["真实文献段落ID"],"limitation_ids":["在本段明确处理的limitation ID"]}]}],
 "calculation_notes":[{"id":"calc1","text":"完整Markdown计算说明表",
 "requirement_ids":["原需求ID"],"source_ids":["原稿计算及假设来源ID"],
 "parameter_refs":["已引用的参数字段"],"citations":[],"limitation_ids":[]}]}。
sections承载第二部分；calculation_notes承载第三部分第3.4节，复用相同内容单元和独立审核。
从原稿明确给出的预算与换算恢复简洁表格，列为“计算项目、原稿方法与代入、结果、假设与适用范围”。
计算项目随课题选择，可含配料、浓度、掺量、极片、注液或质量预算；没有已有计算时calculation_notes返回空数组。
计算说明同时绑定原计算段和假设说明，保留对象、面积、工况、分母、单位换算及舍入口径；已登记输入和结果使用参数占位符。
表格应简洁，正文只说明计算本身，不出现JSON、字段、占位符、来源ID或内部审核协议等实现说明。
代入与结果使用相容的精度；结果取自未舍入中间量时须写明，不能用舍入后的输入假装能精确得到原结果。
工程假设、缺失输入和适用范围必须明确。来源中错误或不完整的计算须指出问题，结果标为待核验或无法确认，不能新增无来源数值。
尤其不得将倍率参考比电流或面电流当作可逆比容量或面容量；区分局部孔体积与全电芯孔体积。
calculation_notes仅恢复设计核算及边界，具体制备与测试步骤仍由第五部分承载；计算表不重复放入sections。
原始prompt是组织主线，requirements用于验收覆盖，不能机械逐条复述要求。
根据研究问题组织“问题—依据—选择与取舍—预期与风险—验证边界”的自然论证，
章节主题、数量和篇幅由当前课题及证据决定，保留关键依据和比较细节；同一论点只讲一次，连接句也不得新增因果结论。
根据原设计与sources保留“为什么选、为什么不选、剂量或条件为何这样确定”的完整论证，包含相关定量对照、反证和适用边界。
多个候选、策略或文献工况需要横向比较时，优先使用Markdown比较表；有明确来源的剂量—性能关系可单列比较表并配解释段落。
表格按实际课题选择列，可包含候选、选择依据、文献体系与条件、结果及限制；参数缺失标明未报告或待确认，不为凑表补数。
同一论文的多个段落按source_papers中的doi/source识别同源，不能计成独立交叉证据；不同样品或测试条件分别说明。
原稿有科学争议时保留具体限制，科学正确性不能由原句可定位代替；已有计算或表述错误须说明问题，不能原样升级为结论。
第二部分只说明材料选择依据、机理、候选取舍、掺量依据与证据边界；具体实验方案统一放到第五部分。
不得在本章安排或诊断实验组编号、梯度点、投料、制备热程、装配、测试流程、仪器组合、重复数或放行阈值。
允许解释为何需要验证及现有证据不足，但用科学问题表述，不写操作指令；必要时仅指向第五部分。
当前候选组成、掺量口径、课题目标及解释证据适用性必需的文献原工况可保留；不重复第三部分全参数表。
若原课题要求掺量范围，正文仍须概括已有候选的具体系数/范围及计量口径，不能只说高/低剂量。
不得把组成成立的必要假设降为非关键；区分组成、工艺与协同的不同验证作用及失败后果。
没有定量来源的“阈值”不得自行补数或声称可直接执行；具体判据及其待定状态由第五部分承载。
所有非background要求至少关联一个段落；固定电池/电解液/窗口等条件可合并说明为评价边界。
每个paragraph是一项独立审核的内容单元，可以是论证段落、列表或完整表格；整表使用一个id。
每项关联支持其全部内容的source_ids与requirement_ids，不能靠填ID假装已回应；表格各行保留对应文献引用及适用条件。
sources提供既有原句、已审摘要和审核限制。优先概括通过审核的内容；对未通过/待审观点，
只能说明其问题、条件缺失或待验证性质，不能把有争议原文润色成肯定结论。
limitations中影响科学结论的内容须在相关段落保留实际含义，并用limitation_ids关联。
仅涉及实验编号、执行流程或操作参数的限制属于第五部分，不要求在第二部分复述；不得把原设计稿的操作缺陷写成本章科学结论。
同一限制兼有科学与操作内容时，只保留证据不足或结论适用边界；具体执行问题交第五部分，不据旧编号宣布当前实验方案无效。
文献支持仅限原体系/位点/倍率/温度/圈数，当前配方、协同、最优与实验完成均不得擅自宣称。
不得新增材料、配方、数值、实验结果、文献、研究排名；名义组成比不能称为实测晶格占位。
主方案参数用{{字段路径}}引用并列入parameter_refs；文献数字保留原研究对象和条件。
parameter_refs只需列出本段引用的字段，不要照抄来源记录的全部参数；参数的明确原句已随parameters提供。
若在不同段落再次提及固定窗口或倍率，该段也须绑定相应requirement来源或参数，不依赖其他段的来源。
section、paragraph和calculation_notes的id全文唯一，不可在每节重新从p1编号。不要罗列合成温度/时长等完整操作配方。
引用仅用本段来源已有的citations，正文可以用[段落ID]；不在正文输出内部字段ID。
标题不带章节编号；text允许自然段、空行分段、有序/无序列表及Markdown表格，不写标题、HTML、代码围栏或课题覆盖表。
表格独立成项，使用完整表头、分隔线和数据行，各行列数相同且以竖线开头和结尾；单元格中的竖线写成\\|，不在单元格内换行。
有序列表使用1.等Markdown编号，章节标题、编号与层级由程序生成；表格与解释段落之间留空行。
若给出repair_feedback，只修复被指出的整合问题，输入科研记录与参数始终只读。
'''

REVIEW_PROMPT = '''你是科研研报Reviewer，审核整合论证和计算说明，不改写正文或科学方案。返回JSON：
{"input_fingerprint":"原值","narrative_fingerprint":"原值",
 "coverage":{"status":"supported|unsupported|needs_review","reason":"依据"},
 "paragraphs":[{"id":"原段落ID","status":"supported|unsupported|needs_review","reason":"依据"}],
 "limitations":[{"id":"原limitation ID","status":"supported|unsupported|needs_review","reason":"依据"}],
 "issues":["不能作为已确认结论展示的具体问题，注明对应段落ID"],"notes":["不阻止本稿展示的后续提醒或可选建议"]}。
必须逐个paragraph、逐个calculation_notes单元及逐个limitation返回，不遗漏；calculation_notes也按其id放入paragraphs审核数组。
paragraph可以是自然段、列表或完整表格，整表按其id审核。
calculation_notes属于第三部分第3.4节，允许设计核算表；sections属于第二部分，仍按下述材料论证职责审阅。
计算表逐项核对方法、输入来源、代入过程、算术、单位换算、样品、面积、工况、分母、舍入与假设。
计算项目按课题与本次整合任务选择，局部设计换算按表内声明的范围审核；覆盖检查关注已选项目的方法、输入、结果、必要中间步骤及假设是否完整。
材料排序、容量代价和性能外推的论证覆盖由sections承担；原稿没有计算则允许空数组。
缺少依据的计算可明确披露问题与缺项，不能补造输入；设计估算不得写成实测或仿真结果，倍率参考电流不能代替容量。
表格须核对表头、每行材料/样品、数值、单位、工况、比较基线和证据限制是否对应；把不同来源的值拼成同一样品或同工况时须指出。
同一论文的不同段落不视为独立证据；正文与表格中的推荐身份、量值和结论强度须一致，原稿错误不能因原句可定位而获得通过。
涉及数值范围、单位换算、计量或守恒关系的推论须独立核对：检查范围内外、增加/减少及正负收支是否与给定量值一致，不能仅因原设计也这样写就判supported。
详细论证、列表和比较表格属于允许的表达；审阅关键选材依据、剂量对照及反证是否保留，不因篇幅长于旧摘要目标而拒绝。
supported仅表示该整合忠实且条件清楚，不表示性能已验证。
核对原始prompt的每个要求是否实际得到论证，单纯挂requirement_id不算覆盖；检查标题是否夸大结论。
核对段落source_ids、文献及当前参数；禁止新增事实/数值、跨体系归因、把假设写成实证或独立发现。
对limitation_ids检查正文是否保留影响科学结论的问题与条件，不能只在元数据挂ID；不能用笼统“待验证”抹掉科学争议。
审阅论证是否连贯、去重而不丢关键依据；避免正文变成逐项问答或详细操作说明。
sections负责材料选择、机理、候选取舍、掺量依据及证据边界；第三节集中主方案参数与calculation_notes，具体实验方案统一由第五节提供。
检查本稿是否混入实验组编号、梯度点、制备热程、装配流程、测试安排、仪器组合或放行判据；这些执行内容应从第二部分移出。
涉及合成与测试的课题要求，在第二部分按可行性依据、科学问题与结论边界回应即可，不要求复述具体实验内容。
limitations仍须逐项审核：纯实验执行事项因属于第五部分可判supported，并在reason注明章节分工；需要提醒的写notes，不强制写入第二部分正文。
科学和操作问题混合时只要求正文保留科学限制，不因省略组号或操作细节否决；不得依据原设计稿编号断言当前第五部分实验无效。
但课题要求的核心组成、掺量范围、选择依据和科学边界仍须在第二部分实质说明，不能只挂ID。
保留已有来源的科学审核边界；缺文献或条件仍不足时标needs_review。不能通过未进入本包的文献。
对明显可改进的措辞给出具体原因和修改方向，issues可关联段落ID；不要重写材料方案。
issues仅保存影响本稿正确性、覆盖或来源的阻断问题；已在正文充分说明的限制、后续章节提醒和可选风格建议放notes，不要求把当前无关章节重写才能通过本稿。
审核以明显实质不一致为否决依据。来源可追溯且没有明确材料、数值、工况、否定或事实强度冲突时判supported；
已注明条件和待验证边界的工程建议或跨体系推断可以通过表述审核，不要求实验先完成。
不因语序、措辞、重复程度、来源ID分配不够精细等非实质问题拒绝；可改进事项写notes。
若程序提示覆盖或限制关联不完整，按实际正文检查：已充分表达则通过，确实遗漏关键要求/边界才拒绝。
“确定最优/验证效果”等研发目标可以用当前候选、现有依据、缺口和后续判定方法回应；
不得仅因实验尚未执行、梯度有待扩展或最优性尚未证实而判覆盖失败，已如实披露则把后续完善建议写notes。
来源中的审稿建议属于待办，不能当作当前已实施的措施；摘要不得借审核建议修改当前主方案或用户固定条件。
来源未报告倍率、温度或其它条件时，只能说明无法确认；不能据此认定“非1 C”等相反条件。沿用原稿的这类推断也须纠正。
'''


def _body(doc):
    return (doc.get("final_answer") or "").split("\n\n## 参考来源")[0].strip()


def source_context(doc):
    """构建可追溯来源目录；未审摘要只提供原句和具体限制，不能冒充已审观点。"""
    params = research_parameters(doc)
    research = doc.get("research") or {}
    sources, limitations = [], []
    for collection, fields in FIELDS.items():
        for row in research.get(collection, []):
            rid = row.get("requirement_id") or row.get("id")
            reqs = [rid] if collection == "answers" else row.get("requirement_ids", [])
            for field in fields:
                if not row.get(field):
                    continue
                quotes = field_quotes(row, field) if is_summary(row) else [row.get("source_quote", "")]
                quotes = [q for q in quotes if q and q in _body(doc)]
                if not quotes:
                    continue
                sid = f"{collection}:{rid}:{field}"
                status = ((row.get("support_review") or {}).get("fields", {}).get(field) or {})
                valid = supported(row, field, doc, params)
                source = {"id": sid, "requirement_ids": reqs, "source_quotes": quotes,
                    "text": row[field] if valid else "", "status": "supported" if valid else status.get("status", "needs_review"),
                    "reason": "" if valid else status.get("reason", "尚无与当前正文对应的支持性审核"),
                    "citations": field_citations(row, field), "parameter_refs": row.get("parameter_refs", []),
                    "verification": row.get("verification", row.get("basis", "unverified"))}
                sources.append(source)
                if not valid:
                    limitations.append({"id": sid, "reason": source["reason"], "source_ids": [sid]})
    for item in (doc.get("requirements") or {}).get("items", []):
        if item["kind"] == "background":
            continue
        sources.append({"id": "requirement:" + item["id"], "requirement_ids": [item["id"]],
            "source_quotes": [item["quote"]], "text": item["description"], "status": "user_requirement",
            "citations": [], "parameter_refs": [], "verification": "not_applicable"})
    # 完整原设计也是来源；局部研究提取失败不再截断整合可用的论证。
    from .research_sources import paragraphs
    from .numeric_bindings import occurrences
    known = {e.get("passage_id") for e in doc.get("evidence", [])}
    for block in paragraphs(_body(doc)):
        text = block["text"]
        if block["section_id"] == "legacy" or len(text) < 30:
            continue
        cites = set(re.findall(r"\[([\w:-]+)\]", text)) - {"FSI", "TFSI", "PF6", "BF4"}
        if cites - known:
            continue
        sources.append({"id": "design:" + block["paragraph_id"], "requirement_ids": [], "source_quotes": [text],
                        "text": text, "status": "archived", "reason": "原设计论证，需按本次一致性标准复核；工程假设不等于实测",
                        "citations": sorted(cites), "parameter_refs": list(dict.fromkeys(key for _, _, kind, key, _ in occurrences(text) if kind == "param")),
                        "verification": "unverified"})
    refs = {p for source in sources for p in source["parameter_refs"]}
    from .requirement_structure import item_bindings
    refs.update(b["path"] for i in (doc.get("requirements") or {}).get("items", []) for b in item_bindings(i))
    semantic = {p: {k: copy.deepcopy(params[p].get(k)) for k in
                   ("value", "unit", "quantity_kind", "denominator", "conditions", "meaning")}
                for p in sorted(refs) if p in params}
    from .report_narrative import amount
    for path, value in semantic.items():
        value["display_value"] = amount(path, params[path])
    prompt = (doc.get("requirements") or {}).get("source", doc.get("target", ""))
    for path, value in semantic.items():
        quote = params[path].get("source_quote")
        if isinstance(quote, str) and quote and (quote in _body(doc) or quote in prompt):
            value["source_quote"] = quote
            value["source_kind"] = "user_requirement" if quote in prompt else "design_statement"
        else:
            matches = [s["source_quotes"][0] for s in sources if s["id"].startswith("design:") and path in s["parameter_refs"]]
            if matches:
                value["source_quote"] = min(matches, key=len)
                value["source_kind"] = "design_statement"
    cites = {c for source in sources for c in source["citations"]}
    evidence = sorted([e for e in doc.get("evidence", []) if e.get("passage_id") in cites], key=lambda e: e["passage_id"])
    return {"prompt": prompt,
        "report_role": "第二部分：按课题组织材料论证。第三部分集中参数，第五部分提供操作和测试；不重复完整实验方法。",
        "requirements": doc.get("requirements"), "sources": sources, "limitations": limitations,
        "parameters": semantic, "narrative_body_hash": digest(_body(doc)), "evidence": evidence,
        "reviewer_issues": [str(s) for s in (doc.get("reviewer_output") or {}).get("issues", [])
                            if not str(s).startswith("研究摘要待核验：")]}


def input_fingerprint(doc):
    return digest(source_context(doc))


def _numbers(text):
    text = re.sub(r"\[[\w:-]+\]|\{\{[^}]+\}\}", "", unicodedata.normalize("NFKC", text))
    # 列表序号是排版信息；小数、倍率和表格里的科学数值仍按来源核对。
    text = re.sub(r"(?m)^\s*\d+[.)][ \t]+", "", text)
    return set(re.findall(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text))


def _validate_markdown(text, pid):
    """允许段落、列表和比较表；章节层级继续由报告渲染器负责。"""
    if not isinstance(text, str) or len(text.strip()) < 16:
        raise ValueError("整合内容须包含实质论证或比较：" + pid)
    if (re.search(r"(?m)^[ \t]*(?:#{1,6}(?:\s|$)|`{3,}|~{3,}|>|(?:={3,}|-{3,}|\*{3,}|_{3,})[ \t]*$)", text)
            or re.search(r"<!--|<\s*/?\s*[A-Za-z][^>]*>|!\[", text)):
        raise ValueError("整合内容仅支持段落、列表和表格，不能声明标题、HTML、代码块或图片：" + pid)
    for block in re.split(r"\n[ \t]*\n", text.strip()):
        lines = block.splitlines()
        if not any(line.lstrip().startswith("|") for line in lines):
            continue
        if len(lines) < 3 or any(not line.strip().startswith("|") or not line.strip().endswith("|") for line in lines):
            raise ValueError("比较表须有表头、分隔线和数据行，并与其它正文空行分隔：" + pid)
        rows = [[cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]] for line in lines]
        if (len(rows[0]) < 2 or any(len(row) != len(rows[0]) for row in rows)
                or any(not re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1])):
            raise ValueError("比较表列数或分隔线无效；单元格中的竖线须转义：" + pid)


def _normalize_paragraph(row, context, sources, reqs, limits, seen):
    from .research_contract import TOKEN, normalized
    from .report_narrative import amount
    from .material_ids import source_matches
    required = {"id", "text", "requirement_ids", "source_ids", "parameter_refs", "citations", "limitation_ids"}
    if not isinstance(row, dict) or set(row) != required:
        raise ValueError("整合段落结构非法")
    pid, text = row["id"], row["text"]
    if not isinstance(pid, str) or not re.fullmatch(r"[\w-]{1,64}", pid) or pid in seen:
        raise ValueError("整合章节/段落ID重复或非法：" + str(pid))
    seen.add(pid)
    _validate_markdown(text, pid)
    for key, allowed in (("requirement_ids", reqs), ("source_ids", sources),
                         ("parameter_refs", context["parameters"]), ("limitation_ids", limits)):
        values = row[key]
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values) or set(values) - set(allowed):
            raise ValueError(f"{pid}引用未知对象：{key}")
    if not row["source_ids"] or not row["requirement_ids"]:
        raise ValueError("每段须关联原始要求及已有来源：" + pid)
    row = copy.deepcopy(row)
    # limitation_id本身是显式来源ID；补齐同一来源关联，不增加模型未引用的证据。
    row["source_ids"] = list(dict.fromkeys(row["source_ids"] + row["limitation_ids"]))
    related = [sources[s] for s in row["source_ids"]]
    errors = []
    if set(TOKEN.findall(text)) - set(row["parameter_refs"]):
        errors.append("整合参数引用与正文占位符不一致")
    cites = {c for s in related for c in s["citations"]}
    if (not isinstance(row["citations"], list) or any(not isinstance(c, str) for c in row["citations"])
            or set(row["citations"]) - cites):
        errors.append("整合段落引用不属于其关联来源")
    quote_text = "\n".join(q for s in related for q in s["source_quotes"])
    limits_text = "\n".join(s.get("reason", "") for s in related if s["id"] in row["limitation_ids"])
    parameter_text = "\n".join(context["parameters"][ref].get("source_quote", "") for ref in row["parameter_refs"])
    # 独立审核要求补回文献工况时，已关联的真实证据可直接支持该数字，无须再复制一份正文摘录。
    evidence_text = "\n".join(e.get("text", "") for e in context.get("evidence", [])
                              if e.get("passage_id") in row.get("citations", []))
    missing = _numbers(text) - _numbers(quote_text + "\n" + limits_text + "\n" + parameter_text + "\n" + evidence_text)
    if missing:
        errors.append("新增关联来源中不存在的数值：" + ",".join(sorted(missing)))
    if isinstance(row["citations"], list) and all(isinstance(c, str) for c in row["citations"]):
        if set(re.findall(r"\[([\w:-]+)\]", text)) - set(row["citations"]) - {"FSI", "TFSI", "PF6", "BF4"}:
            errors.append("整合正文含未绑定引用")
    # 当前参数值只读；其原文允许语义化表述，文本含义由Reviewer连同原句核验。
    for ref in TOKEN.findall(text):
        parameter = context["parameters"].get(ref, {})
        support_text = quote_text + "\n" + parameter.get("source_quote", "")
        value = parameter.get("value")
        if value is None:
            errors.append("参数值未声明：" + ref)
        elif not isinstance(value, str) or not parameter.get("source_quote"):
            display = amount(ref, parameter)
            numeric_support = (isinstance(value, (int, float)) and not isinstance(value, bool)
                               and bool(_numbers(display)) and _numbers(display) <= _numbers(support_text))
            if not numeric_support and not source_matches(ref, value, support_text) and normalized(display) not in normalized(support_text):
                errors.append("整合参数值缺少本段来源支持：" + ref)
    if errors:
        raise ValueError(pid + "：" + "；".join(errors))
    return copy.deepcopy(row)


def normalize(raw, doc):
    """程序只核对覆盖、来源、数值与字段，科学支持性仍交Reviewer。"""
    context = source_context(doc)
    sources = {s["id"]: s for s in context["sources"]}
    reqs = {i["id"] for i in doc["requirements"]["items"] if i["kind"] != "background"}
    limits = {s["id"] for s in context["limitations"]}
    if not isinstance(raw, dict) or "sections" not in raw or set(raw) - {"sections", "calculation_notes"}:
        raise ValueError("整合稿仅允许sections和calculation_notes，不得改写研究记录或参数")
    sections = raw["sections"]
    if not isinstance(sections, list) or not 1 <= len(sections) <= 8:
        raise ValueError("整合稿须包含1–8个主题章节")
    result, seen, covered, retained, errors = [], set(), set(), set(), []
    for section in sections:
        if not isinstance(section, dict) or set(section) != {"id", "title", "paragraphs"}:
            raise ValueError("整合章节结构非法")
        title, sid = section["title"], section["id"]
        if not isinstance(sid, str) or not re.fullmatch(r"[\w-]{1,64}", sid) or sid in seen:
            raise ValueError("整合章节/段落ID重复或非法")
        seen.add(sid)
        if not isinstance(title, str) or not 2 <= len(title) <= 80 or re.search(r"[\n#|]", title):
            raise ValueError("整合章节须有简短主题标题")
        if not isinstance(section["paragraphs"], list) or not 1 <= len(section["paragraphs"]) <= 6:
            raise ValueError("每个整合章节须包含1–6项论证、列表或表格")
        paragraphs = []
        for row in section["paragraphs"]:
            try:
                paragraph = _normalize_paragraph(row, context, sources, reqs, limits, seen)
                covered.update(paragraph["requirement_ids"])
                retained.update(paragraph["limitation_ids"])
                paragraphs.append(paragraph)
            except ValueError as exc:
                errors.append(str(exc))
        body = " ".join(p["text"] for p in paragraphs)
        if _numbers(title) - _numbers(body):
            errors.append("整合标题新增段落中不存在的数值：" + sid)
        result.append({"id": sid, "title": title, "paragraphs": paragraphs})
    calculations = []
    if "calculation_notes" in raw:
        if not isinstance(raw["calculation_notes"], list):
            raise ValueError("calculation_notes必须为内容单元数组")
        for row in raw["calculation_notes"]:
            try:
                paragraph = _normalize_paragraph(row, context, sources, reqs, limits, seen)
                covered.update(paragraph["requirement_ids"])
                retained.update(paragraph["limitation_ids"])
                calculations.append(paragraph)
            except ValueError as exc:
                errors.append(str(exc))
    coverage_notes = []
    if reqs - covered:
        coverage_notes.append("要求关联待审核实际正文覆盖：" + ",".join(sorted(reqs - covered)))
    if limits - retained:
        coverage_notes.append("来源限制关联待审核实际表述：" + ",".join(sorted(limits - retained)))
    if errors:
        raise ValueError("\n".join(errors))
    fp = input_fingerprint(doc)
    content = {"sections": result}
    # 可选字段缺省时保留旧指纹形状，旧稿继续按原审核验收。
    if "calculation_notes" in raw:
        content["calculation_notes"] = calculations
    return {"version": VERSION, "input_fingerprint": fp, **content, "coverage_notes": coverage_notes,
            "narrative_fingerprint": digest({"input": fp, **content}), "status": "pending"}


def content_units(narrative):
    """共用内容单元遍历，计算说明与论证使用同一逐项审核协议。"""
    for section in narrative["sections"]:
        for paragraph in section["paragraphs"]:
            yield section["id"], paragraph
    for paragraph in narrative.get("calculation_notes", []):
        yield "", paragraph


def _content(narrative):
    return {key: narrative[key] for key in ("sections", "calculation_notes") if key in narrative}


class ReviewProtocolError(ValueError):
    """审核格式失败不能当作内容否决并触发全文重写。"""

    def __init__(self, message, raw_response=None):
        super().__init__(message)
        self.raw_response = raw_response


def validate_review_response(verdict, narrative, doc):
    if (not isinstance(verdict, dict) or verdict.get("input_fingerprint") != narrative["input_fingerprint"]
            or verdict.get("narrative_fingerprint") != narrative["narrative_fingerprint"]):
        raise ReviewProtocolError("整合审核对象或双指纹不匹配")
    def valid(row):
        return (isinstance(row, dict) and row.get("status") in ("supported", "unsupported", "needs_review")
                and isinstance(row.get("reason"), str) and bool(row["reason"].strip()))
    if not valid(verdict.get("coverage")):
        raise ReviewProtocolError("整合审核缺少有效coverage")
    for key, ids in (("paragraphs", [p["id"] for _, p in content_units(narrative)]),
                     ("limitations", [l["id"] for l in source_context(doc)["limitations"]])):
        rows = verdict.get(key)
        if (not isinstance(rows, list) or len(rows) != len(ids)
                or any(not valid(r) or not isinstance(r.get("id"), str) for r in rows)
                or {r["id"] for r in rows} != set(ids)):
            raise ReviewProtocolError("整合审核逐项结构缺失、重复或包含未知ID：" + key)
    if not isinstance(verdict.get("issues"), list) or any(not isinstance(i, str) for i in verdict["issues"]):
        raise ReviewProtocolError("整合审核issues必须为字符串数组")


def decode_review_response(raw, narrative, doc):
    """只读取完整外层；唯一内部闭括号可离线恢复，原响应与补丁留档。"""
    from .writer_protocol import decode, single_closure_repair
    syntax = None
    try:
        try:
            verdict = decode(raw)
        except json.JSONDecodeError:
            syntax = single_closure_repair(raw, require_design=False)
            if not syntax:
                raise
            verdict = decode(syntax["repaired_response"])
        validate_review_response(verdict, narrative, doc)
    except (ValueError, TypeError, KeyError) as exc:
        raise ReviewProtocolError(str(exc), raw) from exc
    verdict["protocol_record"] = {"raw_response": raw, "syntax_repair": syntax}
    return verdict


def apply_review(narrative, verdict, doc, evidence):
    """只有当前输入和整合稿双指纹匹配、逐段及覆盖审核齐全时才允许展示。"""
    available = {e.get("passage_id") for e in evidence}
    errors = []
    matched = (isinstance(verdict, dict) and verdict.get("input_fingerprint") == narrative["input_fingerprint"]
               and verdict.get("narrative_fingerprint") == narrative["narrative_fingerprint"])
    def approved(value):
        return isinstance(value, dict) and value.get("status") == "supported" and isinstance(value.get("reason"), str) and bool(value["reason"].strip())
    if not matched or not approved(verdict.get("coverage") if matched else None):
        errors.append("整合稿缺少与当前指纹对应的完整覆盖审核")
    for key, ids in (("paragraphs", [p["id"] for _, p in content_units(narrative)]),
                     ("limitations", [l["id"] for l in source_context(doc)["limitations"]])):
        rows = verdict.get(key, []) if matched else []
        rows = rows if isinstance(rows, list) else []
        for rid in ids:
            found = [r for r in rows if isinstance(r, dict) and r.get("id") == rid]
            if len(found) != 1 or not approved(found[0]):
                reason = found[0].get("reason") if len(found) == 1 else "缺少唯一逐项审核"
                errors.append(f"{key}[{rid}]：{reason}")
    required = {c for _, p in content_units(narrative) for c in p["citations"]}
    if required - available:
        errors.append("整合稿依赖的引用未进入本次审核包：" + ",".join(sorted(required - available)))
    if matched and verdict.get("issues"):
        errors.extend(str(s) for s in verdict["issues"])
    narrative["review"] = copy.deepcopy(verdict) if isinstance(verdict, dict) else {}
    narrative["reviewed_evidence_ids"] = sorted(available)
    narrative["diagnostics"] = list(dict.fromkeys(errors))
    narrative["status"] = "approved" if not errors and input_fingerprint(doc) == narrative["input_fingerprint"] else "rejected"
    return narrative


def _display_parameters_match(narrative, doc):
    """方案表实际显示值须与审核参数一致，拦截旧扩展声明遮蔽方案表更新。"""
    reviewed = research_parameters(doc)
    displayed = (doc.get("ledger_view") or {}).get("parameters", {})
    refs = {ref for _, row in content_units(narrative) for ref in row["parameter_refs"]}
    for ref in refs:
        expected, actual = reviewed.get(ref), displayed.get(ref)
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            return False
        if expected.get("value") != actual.get("value"):
            return False
        for key in ("unit", "quantity_kind", "denominator", "conditions", "meaning"):
            if (expected.get(key) or None) != (actual.get(key) or None):
                return False
    return True


def current_narrative(doc):
    """展示时重验，不信任磁盘上的approved字符串或旧输入哈希。"""
    row = doc.get("research_narrative")
    if not isinstance(row, dict) or row.get("version") != VERSION or row.get("status") != "approved":
        return None
    try:
        checked = normalize(_content(row), doc)
        if (checked["narrative_fingerprint"] != row.get("narrative_fingerprint")
                or not _display_parameters_match(checked, doc)):
            return None
        ids = set(row.get("reviewed_evidence_ids", []))
        package = [e for e in doc.get("evidence", []) if e.get("passage_id") in ids]
        apply_review(checked, row.get("review"), doc, package)
        return checked if checked["status"] == "approved" else None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _review_entry(review, key, rid):
    rows = review.get(key, [])
    matches = [r for r in rows if isinstance(r, dict) and r.get("id") == rid] if isinstance(rows, list) else []
    if len(matches) != 1:
        return {"status": "needs_review", "reason": "缺少唯一逐项审核"}
    row = matches[0]
    if row.get("status") not in ("supported", "needs_review", "unsupported") or not isinstance(row.get("reason"), str) or not row["reason"].strip():
        return {"status": "needs_review", "reason": "缺少有效的审核状态与原因"}
    return row


def display_narrative(doc):
    """逐段构建只读展示视图；完整通过与局部可展示分别判断，不提升科研审核状态。"""
    row = doc.get("research_narrative")
    if not isinstance(row, dict) or row.get("version") != VERSION or row.get("status") not in ("approved", "rejected"):
        return None
    try:
        checked = normalize(_content(row), doc)
        review = row.get("review") or {}
        if (checked["narrative_fingerprint"] != row.get("narrative_fingerprint")
                or not _display_parameters_match(checked, doc)
                or not isinstance(review, dict)
                or review.get("input_fingerprint") != checked["input_fingerprint"]
                or review.get("narrative_fingerprint") != checked["narrative_fingerprint"]):
            return None
        ids = set(row.get("reviewed_evidence_ids", []))
        package = [e for e in doc.get("evidence", []) if e.get("passage_id") in ids]
        apply_review(checked, review, doc, package)
        available = {e.get("passage_id") for e in package}
        issues = review.get("issues") or []
        issues = [str(i) for i in issues] if isinstance(issues, list) else [str(issues)]
        paragraph_reviews = {}
        scoped_issues = set()
        for section_id, paragraph in content_units(checked):
            result = _review_entry(review, "paragraphs", paragraph["id"])
            reasons = [] if result["status"] == "supported" else [result["reason"]]
            for lid in paragraph["limitation_ids"]:
                limitation = _review_entry(review, "limitations", lid)
                if limitation["status"] != "supported":
                    reasons.append("来源限制尚未完整保留：" + limitation["reason"])
            if set(paragraph["citations"]) - available:
                reasons.append("本段依赖的文献未进入本次审核包")
            for issue in issues:
                if any(re.search(r"(?<![\w-])" + re.escape(rid) + r"(?![\w-])", issue)
                       for rid in (paragraph["id"], section_id) if rid):
                    if not reasons:
                        reasons.append(issue)
                    scoped_issues.add(issue)
            paragraph_reviews[paragraph["id"]] = list(dict.fromkeys(reasons))
        checked["paragraph_reviews"] = paragraph_reviews
        checked["unscoped_issues"] = [i for i in issues if i not in scoped_issues]
        return checked
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _render_paragraph(paragraph, narrative, doc):
    """论证和计算表共用参数替换、表格转义及逐项审核提示。"""
    from .research_reporting import substitute, cell
    from .research_contract import TOKEN
    lines = []
    for line in paragraph["text"].splitlines():
        if line.lstrip().startswith("|"):
            line = TOKEN.sub(lambda match: cell(substitute(match[0], doc)), line)
        else:
            line = substitute(line, doc)
        lines.append(line)
    text = "\n".join(lines)
    cites = [c for c in paragraph["citations"] if f"[{c}]" not in text]
    if cites:
        # 多行内容的引用独立成段，避免落到表格尾行或最后一个列表项。
        separator = "\n\n" if "\n" in text else " "
        text += separator + " ".join(f"[{c}]" for c in cites)
    reasons = narrative["paragraph_reviews"][paragraph["id"]]
    if reasons:
        return ("> **本段待复核，以下为原稿。** " + "；".join(reasons).replace("\n", " ")
                + "\n>\n> " + text.replace("\n", "\n> "))
    return text


def render(doc):
    narrative = display_narrative(doc)
    if narrative is None:
        return None
    parts = []
    if narrative["status"] != "approved":
        reviews = [narrative["paragraph_reviews"][p["id"]] for s in narrative["sections"] for p in s["paragraphs"]]
        pending = sum(bool(reasons) for reasons in reviews)
        passed = len(reviews) - pending
        label = "部分通过" if passed else "尚未通过"
        parts.append(f"> **论证审核：{label}。** {passed}段通过当前来源与表述审核，{pending}段待复核；通过审核不代表性能已经实验验证。")
        coverage = narrative["review"].get("coverage") or {}
        coverage = coverage if isinstance(coverage, dict) else {}
        if coverage.get("status") != "supported":
            parts.append("> **整体覆盖待复核。** " + str(coverage.get("reason") or "尚缺有效的完整覆盖审核。"))
        for issue in narrative["unscoped_issues"]:
            parts.append("> **整体待复核事项。** " + issue.replace("\n", " "))
        parts.append("待复核段落以引用块保留原稿及问题，不能作为已确认结论。")
    for number, section in enumerate(narrative["sections"], 1):
        parts.append(f"### 2.{number} {section['title']}")
        for paragraph in section["paragraphs"]:
            parts.append(_render_paragraph(paragraph, narrative, doc))
    return "\n\n".join(parts)


def render_calculation_notes(doc):
    """失效计算只提示复核，避免用新参数替换旧公式后仍展示旧结果。"""
    archived = doc.get("research_narrative") or {}
    if not isinstance(archived, dict) or not archived.get("calculation_notes"):
        return None
    narrative = display_narrative(doc)
    if narrative is None:
        return "> **计算说明待复核。** 输入、计算内容或审核已失效，须基于当前参数重新整合并审核。"
    parts = ["**已归档的局部设计换算。** 以下保留原稿的计算方法、输入与适用范围，工程估算仍需实测核验。"]
    parts.extend(_render_paragraph(p, narrative, doc) for p in narrative["calculation_notes"])
    for issue in narrative["unscoped_issues"]:
        parts.append("> **整合审核待复核。** " + issue.replace("\n", " "))
    return "\n\n".join(parts)


def requirement_locations(doc):
    from .research_original import view as original_view
    original = original_view(doc)
    if original:
        locations = {}
        for section in original["sections"]:
            for rid in section["requirement_ids"]:
                locations.setdefault(rid, []).append(section["location"])
        return {rid: "、".join(values) for rid, values in locations.items()}
    narrative = display_narrative(doc)
    if narrative is None:
        return {}
    result = {}
    for number, section in enumerate(narrative["sections"], 1):
        for paragraph in section["paragraphs"]:
            for rid in paragraph["requirement_ids"]:
                location = f"第2.{number}节·{section['title']}"
                pending = bool(narrative["paragraph_reviews"][paragraph["id"]])
                if rid not in result or "（待复核）" in result[rid] and not pending:
                    result[rid] = location + ("（待复核）" if pending else "")
    for paragraph in narrative.get("calculation_notes", []):
        pending = bool(narrative["paragraph_reviews"][paragraph["id"]])
        for rid in paragraph["requirement_ids"]:
            location = "第3.4节·设计计算说明" + ("（待复核）" if pending else "")
            result[rid] = result[rid] + "、" + location if rid in result else location
    return result


def integrate(doc, client, reviewer, state=None, save=None):
    """最多两次整合与两次独立审核；调用前占用持久预算，中断后不重发同次请求。"""
    state = state if state is not None else {}
    save = save or (lambda: None)
    fp = input_fingerprint(doc)
    if current_narrative(doc):
        return doc["research_narrative"]
    if state.get("input_fingerprint") not in (None, fp):
        doc["research_narrative"] = {"version": VERSION, "status": "stale", "diagnostics": ["研究输入已改变，旧整合稿与预算不能自动复用"]}
        save()
        return doc["research_narrative"]
    state["input_fingerprint"] = fp
    review_fn = getattr(reviewer, "review_research_narrative", None)
    if client is None or not getattr(client, "available", False) or not callable(review_fn):
        return None
    context = source_context(doc)
    feedback = list(state.get("repair_feedback") or [])
    proposal = None
    if state.get("phase") in ("proposal", "review_retry") and state.get("submitted_draft"):
        try:
            proposal = normalize(state["submitted_draft"], doc)
        except (ValueError, TypeError, KeyError) as exc:
            feedback = [str(exc)]
    while state.get("review_calls", 0) < MAX_ATTEMPTS and (proposal is not None or state.get("generation_calls", 0) < MAX_ATTEMPTS):
        if state.get("phase") in ("generating", "reviewing", "interrupted"):
            state["phase"] = "interrupted"
            state["diagnostic"] = "上次整合调用中断，已占用预算；不重复该请求"
            save()
            return None
        if proposal is not None:
            draft, proposal = proposal, None
        else:
            state["generation_calls"] = state.get("generation_calls", 0) + 1
            state["phase"] = "generating"
            save()
            try:
                payload = {k: v for k, v in context.items() if k != "evidence"}
                payload["source_papers"] = [{k: e.get(k) for k in ("passage_id", "doi", "source", "title")}
                                           for e in context["evidence"]]
                if feedback:
                    payload.update(repair_feedback=feedback, previous_draft=state.get("submitted_draft") or state.get("draft"))
                raw = client.chat(GENERATION_PROMPT, json.dumps(payload, ensure_ascii=False), temperature=0.1, json_mode=True, trace_tag="research_narrative")
                raw = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
                state["submitted_draft"] = raw
                draft = normalize(raw, doc)
            except Exception as exc:
                feedback = [str(exc)]
                state["repair_feedback"] = feedback
                state.setdefault("attempts", []).append({"phase": "generation", "errors": feedback})
                state["phase"] = "generation_failed"
                save()
                continue
        state["draft"] = draft
        from .adaptive_retrieval import evidence_package
        citations = list(dict.fromkeys(c for _, p in content_units(draft) for c in p["citations"]))
        # Reviewer同时核对来源及限制；整合稿省略某引用不应把该来源文献挤出审核包。
        citations = list(dict.fromkeys(citations + [c for source in context["sources"] for c in source["citations"]]))
        package, selection = evidence_package(doc.get("evidence", []), preferred_ids=citations,
            requirement_items=doc["requirements"]["items"])
        state["review_calls"] = state.get("review_calls", 0) + 1
        state["phase"] = "reviewing"
        state["evidence_selection"] = selection
        save()
        protocol_failure = False
        try:
            verdict = review_fn(doc=doc, narrative=draft, evidence=package)
            validate_review_response(verdict, draft, doc)
            apply_review(draft, verdict, doc, package)
        except ReviewProtocolError as exc:
            protocol_failure = True
            state.setdefault("review_protocol_errors", []).append({"attempt": state["review_calls"],
                "reason": str(exc), "raw_response": exc.raw_response})
            draft.update(status="rejected", diagnostics=["整合审核协议失败：" + str(exc)])
        except Exception as exc:
            draft.update(status="rejected", diagnostics=[str(exc)])
        state.setdefault("attempts", []).append(copy.deepcopy(draft))
        state["phase"] = "review_retry" if protocol_failure else "reviewed"
        doc["research_narrative"] = draft
        feedback = draft.get("diagnostics", [])
        state["repair_feedback"] = feedback
        save()
        if protocol_failure:
            # 共用原有两次审核预算，只重审已验证稿，不消耗新生成机会。
            proposal = normalize(state["submitted_draft"], doc)
            continue
        if draft["status"] == "approved":
            return draft
    if not doc.get("research_narrative"):
        doc["research_narrative"] = {"version": VERSION, "status": "failed", "input_fingerprint": fp, "diagnostics": feedback}
    state["phase"] = "exhausted"
    save()
    return doc["research_narrative"]
