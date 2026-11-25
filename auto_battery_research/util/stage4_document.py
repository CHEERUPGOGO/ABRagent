"""Stage 4 v3 纯编译器：正文树是唯一内容源，参数和来源由程序解析。"""
from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field

from .design_structure import SECTION_IDS, SECTION_TITLES, START, END, _content
from .scheme_ledger import digest
from .writer_titles import INSTRUCTIONS as TITLE_INSTRUCTIONS, issues as title_issues

VERSION = 3
COMPILER_VERSION = 5
ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")
TOKEN = re.compile(r"\{\{(param|derived|section|cite|component|candidate):([^{}\s]+)\}\}")


def diagnostic(code, location, reason, parameters=(), *, severity="error", repair_class="protocol"):
    return {"code": code, "severity": severity, "source_location": location,
            "related_parameters": list(parameters), "reason": reason, "repair_class": repair_class}


def decode_document(raw):
    """只读完整外层对象；重复键和非有限数不能被解析器静默接受。"""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON重复字段：" + key)
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError("JSON非有限数：" + value)
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("JSON非有限数：" + value)
        return number
    data = json.loads(raw.strip(), object_pairs_hook=unique, parse_constant=nonfinite, parse_float=finite_float)
    if not isinstance(data, dict):
        raise ValueError("Writer必须返回完整JSON对象")
    return data


def current_content(data):
    """参数提案仅留在调用档案；当前内容不持有第二套可写参数。"""
    return copy.deepcopy({key: data[key] for key in ("version", "sections", "research_links") if key in data})


@dataclass
class CompiledDesign:
    markdown: str = ""
    sections: list = field(default_factory=list)
    leaves: dict = field(default_factory=dict)
    dependencies: dict = field(default_factory=dict)
    research: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)
    content_hash: str = ""
    render_fingerprint: str = ""
    unbound_mentions: list = field(default_factory=list)

    @property
    def valid(self):
        return not any(d["severity"] == "error" for d in self.diagnostics)

    def as_dict(self):
        return copy.deepcopy(self.__dict__)


def candidate_parameters(proposed, current):
    """复用已有量值身份、别名及锁定规则，汇总全部提案错误。"""
    from .numeric_bindings import declarations
    result, issues = copy.deepcopy(current or {}), []
    if not isinstance(proposed, dict):
        return result, [diagnostic("PARAMETERS_INVALID", "parameters", "参数提案必须是对象")]
    for path, original in proposed.items():
        try:
            row = copy.deepcopy(original)
            if not isinstance(row, dict):
                raise ValueError("参数必须是对象")
            evidence = row.pop("evidence", None)
            if evidence is not None and not path.endswith(".D_s_m2s"):
                raise ValueError("扩散证据只能绑定D_s")
            declared = declarations({"parameters": {path: row}}, result)[path]
            if path in current and declared == current[path]:
                continue  # 同值冗余声明沿用已有来源，不标成Writer新提案。
            declared["reference_origin"] = "writer_v3"
            if evidence is not None:
                declared["evidence"] = evidence
            result[path] = declared
        except (ValueError, TypeError, KeyError) as exc:
            issues.append(diagnostic("PARAMETER_INVALID", ["parameters", path], str(exc), [path], repair_class="parameter"))
    return result, issues


def _escape(text, *, table=False):
    # 只转义格式字符，不删除化学式、数学符号和普通标点。
    text = text.replace("\\", "\\\\")
    text = re.sub(r"([`*\[\]<>#])", r"\\\1", text)
    text = re.sub(r"(?<!\w)_(?=\S)|(?<=\S)_(?!\w)", r"\\_", text)
    text = re.sub(r"(?m)^(\s*)([-+]|\d+\.)(?=\s)", r"\1\\\2", text)
    text = re.sub(r"(?m)^(\s*)([-=~]{3,})(\s*)$", lambda m: m[1] + "\\" + m[2] + m[3], text)
    if table:
        return text.replace("|", "\\|").replace("\n", "<br>")
    return text


def compile_document(content, parameter_view, requirements=None, evidence=()):
    """无模型、无文件写入、无输入修改；一次汇总结构、引用、来源和数值诊断。"""
    from .numeric_consistency import audit
    from .numeric_bindings import display, dependencies
    from .material_ids import assemble_components
    result = CompiledDesign()
    issues = result.diagnostics
    parameters = copy.deepcopy(parameter_view or {})
    if not isinstance(content, dict):
        issues.append(diagnostic("CONTENT_INVALID", "writer_content", "正文必须是对象"))
        return result
    if not isinstance(parameter_view, dict) or any(not isinstance(row, dict) for row in parameters.values()):
        issues.append(diagnostic("PARAMETER_VIEW_INVALID", "parameters", "参数视图须为字段到对象的映射"))
        return result
    if requirements is not None and (not isinstance(requirements, dict)
            or not isinstance(requirements.get("items", []), list)
            or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in requirements.get("items", []))):
        issues.append(diagnostic("REQUIREMENTS_INVALID", "requirements", "要求契约须为对象"))
        return result
    try:
        result.content_hash = digest(content)
        calculation = audit(parameters)
    except (ValueError, TypeError, OverflowError) as exc:
        issues.append(diagnostic("NONFINITE_VALUE", "writer_content", str(exc)))
        return result
    for error in calculation.get("issues", []):
        issues.append(diagnostic("NUMERIC_RELATION", "parameters", error.get("message", str(error)), error.get("paths", []), repair_class="parameter"))
    sections = content.get("sections")
    if (content.get("version") != VERSION or type(content.get("version")) is not int
            or set(content) - {"version", "sections", "research_links"}
            or not isinstance(sections, dict) or set(sections) != set(SECTION_IDS)):
        issues.append(diagnostic("CONTENT_CONTRACT", "sections", "须声明version=3及完整唯一的五节内容树"))
    if not isinstance(sections, dict):
        return result
    for item in title_issues(content):
        issues.append(dict(diagnostic(item["code"], item["path"], item["reason"]),
                           title=item["title"], node_id=item["node_id"], expected=item["expected"]))
    seen, nodes, leaves = set(), [], result.leaves
    def register(key, location, derived=False):
        valid = isinstance(key, str) and (ID.fullmatch(key) or derived and re.fullmatch(r"[a-z][a-z0-9_-]{0,195}", key))
        if not valid or key in seen:
            issues.append(diagnostic("ID_INVALID", location, "ID重复或非法：" + str(key)))
            return False
        seen.add(key)
        return True
    def leaf(key, value, location, section_id, table=False, derived=False):
        if not register(key, location, derived):
            return
        if not isinstance(value, str) or not _content(value):
            issues.append(diagnostic("EMPTY_LEAF", location, "叶节点须有实质正文或具体缺口说明"))
            return
        if re.search(r"(?m)^\s*(?:#{1,6}\s|```|~~~|\|?\s*:?-{3,}:?\s*\|)|<!--|</?[a-zA-Z][^>]*>", value):
            issues.append(diagnostic("MODEL_MARKUP", location, "正文不能自行生成标题、表格分隔线、围栏或HTML"))
        from .design_structure import NUMBER_REFERENCE
        if NUMBER_REFERENCE.search(value):
            issues.append(diagnostic("SECTION_NUMBER_REFERENCE", location, "跨节引用须用{{section:id}}，编号由程序生成"))
        leaves[key] = {"id": key, "source_text": value, "source_location": location,
                       "section_id": section_id, "table": table}
    def visit(node, key, number, title, depth, location):
        if not register(key, location) or not isinstance(node, dict):
            if not isinstance(node, dict):
                issues.append(diagnostic("NODE_INVALID", location, "章节必须是对象"))
            return
        allowed = {"blocks", "subsections"} | ({"id", "title"} if depth else set())
        if depth > 3 or set(node) - allowed:
            issues.append(diagnostic("NODE_INVALID", location, "章节层级过深或含未授权字段"))
            return
        # 标题错误已逐项登记；继续扫描科研叶节点，避免误报参数未绑定或研究来源丢失。
        blocks, children = node.get("blocks", []), node.get("subsections", [])
        if not isinstance(blocks, list) or not isinstance(children, list):
            issues.append(diagnostic("NODE_INVALID", location, "blocks和subsections须为数组"))
            return
        if not blocks and not children:
            issues.append(diagnostic("EMPTY_SECTION", location, "章节不能为空"))
        entry = {"id": key, "number": number, "title": title, "level": depth + 2, "blocks": []}
        nodes.append(entry)
        for i, block in enumerate(blocks):
            loc = location + ["blocks", i]
            if not isinstance(block, dict):
                issues.append(diagnostic("BLOCK_INVALID", loc, "内容块必须是对象"))
                continue
            bid, kind = block.get("id"), block.get("type")
            before = set(leaves)
            if kind == "paragraph" and set(block) == {"id", "type", "text"}:
                leaf(bid, block["text"], loc + ["text"], key)
            elif kind == "list" and not set(block) - {"id", "type", "ordered", "items"}:
                if not register(bid, loc) or type(block.get("ordered", False)) is not bool or not isinstance(block.get("items"), list) or not block["items"]:
                    issues.append(diagnostic("LIST_INVALID", loc, "列表须有稳定ID条目及布尔ordered"))
                    continue
                for j, item in enumerate(block["items"]):
                    if not isinstance(item, dict) or set(item) != {"id", "text"}:
                        issues.append(diagnostic("LIST_INVALID", loc + ["items", j], "列表条目须含id、text"))
                    else:
                        leaf(item["id"], item["text"], loc + ["items", j, "text"], key, True)
            elif kind == "table" and set(block) == {"id", "type", "columns", "rows"}:
                if not register(bid, loc):
                    continue
                cols, rows = block["columns"], block["rows"]
                if not isinstance(cols, list) or not cols or not isinstance(rows, list) or not rows:
                    issues.append(diagnostic("TABLE_INVALID", loc, "表格须有列和行"))
                    continue
                col_ids, row_ids = [], set()
                for col in cols:
                    if (not isinstance(col, dict) or set(col) != {"id", "name"} or not isinstance(col.get("id"), str)
                            or not ID.fullmatch(col["id"]) or col["id"] in col_ids or not isinstance(col.get("name"), str)
                            or not col["name"].strip() or TOKEN.search(col["name"])):
                        issues.append(diagnostic("TABLE_COLUMN", loc, "表格列ID重复、非法或列名无效"))
                    else:
                        col_ids.append(col["id"])
                for j, row in enumerate(rows):
                    if (not isinstance(row, dict) or set(row) != {"id", "cells"} or not isinstance(row.get("id"), str)
                            or not ID.fullmatch(row["id"]) or row["id"] in row_ids or not isinstance(row.get("cells"), dict)
                            or set(row["cells"]) != set(col_ids)):
                        issues.append(diagnostic("TABLE_ROW", loc + ["rows", j], "行ID重复或单元格列数不一致"))
                        continue
                    row_ids.add(row["id"])
                    for col in col_ids:
                        # 稳定坐标含表格ID，避免不同表的同行列名相撞。
                        cell_id = bid + "__" + row["id"] + "__" + col
                        leaf(cell_id, row["cells"][col], loc + ["rows", j, "cells", col], key, True, True)
            else:
                issues.append(diagnostic("BLOCK_INVALID", loc, "只允许paragraph、list、table及其已知字段"))
                continue
            entry["blocks"].append({"block": block, "leaf_ids": [p for p in leaves if p not in before]})
        for i, child in enumerate(children, 1):
            if isinstance(child, dict):
                visit(child, child.get("id"), number + "." + str(i), child.get("title"), depth + 1, location + ["subsections", i-1])
            else:
                issues.append(diagnostic("NODE_INVALID", location, "子节必须是对象"))
    for index, key in enumerate(SECTION_IDS, 1):
        if key in sections:
            visit(sections[key], key, str(index), SECTION_TITLES[key], 0, ["sections", key])
    result.sections = [{k: n[k] for k in ("id", "number", "title", "level")} for n in nodes]
    lookup = {n["id"]: n["number"] for n in nodes}
    known_evidence = {str(e.get("passage_id")): e for e in evidence if isinstance(e, dict) and e.get("passage_id") and e.get("text")}
    links = content.get("research_links")
    if not isinstance(links, dict):
        issues.append(diagnostic("RESEARCH_LINKS_INVALID", "research_links", "须提供显式研究关联"))
        links = {}
    candidates = {c.get("id"): c.get("name") for c in links.get("candidates", []) if isinstance(c, dict)} if isinstance(links.get("candidates", []), list) else {}
    try:
        components = assemble_components({}, parameters)
    except (ValueError, TypeError, KeyError) as exc:
        components = {}
        issues.append(diagnostic("COMPONENT_IDENTITY", "parameters", str(exc), repair_class="parameter"))
    if (requirements or {}).get("scheme_required") is True and not all(components.get(c) for c in ("cathode", "anode", "electrolyte")):
        issues.append(diagnostic("COMPONENTS_INCOMPLETE", "parameters", "设计主方案身份无法定案，不能生成替代配方", repair_class="parameter"))
    from .physical_parameters import physical_declarations
    _, physical_errors = physical_declarations(parameters)
    for reason in physical_errors:
        issues.append(diagnostic("PHYSICAL_DECLARATION_CONFLICT", "parameters", reason, repair_class="parameter"))
    if components.get("cathode") and components.get("anode"):
        from auto_battery_research.tools.geometry_revision import collect_geometry_violations, resolve_input_ranges
        from .numeric_consistency import context
        geometry = {"cathode": {}, "anode": {}, "condition": {}}
        for path, row in parameters.items():
            canonical = row.get("canonical_path") or path
            comp, _, name = canonical.partition(".")
            if comp in geometry and name in ("L_um", "porosity", "R_s_um", "D_s_m2s", "v_min", "v_max", "c_rate") and context(row) == ("main", "main", "[]"):
                geometry[comp][name] = row.get("value")
        for reason in collect_geometry_violations(geometry, resolve_input_ranges(components["cathode"], components["anode"], scheme=components), components["anode"]):
            issues.append(diagnostic("GEOMETRY_VALUE_ILLEGAL", "parameters", reason, repair_class="parameter"))
    # 所有入口复用当前参数预检；来源缺口可披露，明确非法扩散值不得进入方案表。
    from .scheme_ledger import validate_field
    from .research_contract import bindings, field_equal
    for path, row in parameters.items():
        try:
            validate_field(path, row)
        except (ValueError, TypeError) as exc:
            issues.append(diagnostic("PARAMETER_VIEW_INVALID", ["parameters", path], str(exc), [path], repair_class="parameter"))
        canonical = row.get("canonical_path") or path
        if canonical.endswith(".D_s_m2s") and row.get("value") is not None and row.get("basis") != "model_reference":
            try:
                from auto_battery_research.simulation.geometry_contract import validate_diffusion_evidence
                comp = canonical.split(".")[0]
                if row.get("unit") != "m2/s":
                    raise ValueError("D_s须为规范单位m2/s")
                validate_diffusion_evidence(row.get("evidence"), row["value"], str(components.get(comp) or ""), list(evidence))
            except (ValueError, TypeError, KeyError) as exc:
                issues.append(diagnostic("DIFFUSION_EVIDENCE_INVALID", ["parameters", path], str(exc), [path], repair_class="parameter"))
        if canonical.endswith((".R_s_um", ".D_s_m2s")) and row.get("value") is not None and row.get("basis") == "model_reference":
            try:
                import hashlib
                from auto_battery_research.simulation import match_pinn_system, complete_geometry_block, load_system_params
                from .material_ids import pinn_material_ids
                model_ids = pinn_material_ids(components)
                system = match_pinn_system(model_ids["cathode"], model_ids["anode"])
                if not system:
                    raise ValueError("模型参考来源无精确材料体系匹配")
                # 当前registry对应的完整基准来源含参数文件哈希，不能自称model_reference。
                baseline = complete_geometry_block({}, model_ids["cathode"], model_ids["anode"])
                comp, name = canonical.split(".")
                source = {"origin": "model_baseline", "material_id": components[comp], "system_id": system["system_id"],
                          "params_hash": hashlib.sha256(json.dumps(load_system_params(system), sort_keys=True).encode()).hexdigest()}
                declared_source = row.get("model_reference") or {}
                if row["value"] != baseline[comp][name] or any(declared_source.get(k) != source.get(k) for k in ("origin", "material_id", "system_id", "params_hash")):
                    raise ValueError("模型参考数值或来源指纹失配")
            except (ValueError, TypeError, KeyError) as exc:
                issues.append(diagnostic("MODEL_REFERENCE_INVALID", ["parameters", path], str(exc), [path], repair_class="blocked"))
    try:
        for path, expected in bindings(requirements or {}).items():
            actual = parameters.get(path) or {}
            if (not field_equal(path, expected.get("value"), actual.get("value"))
                    or expected.get("unit", "") != actual.get("unit", "") or not actual.get("locked")):
                issues.append(diagnostic("USER_BINDING_CONFLICT", ["parameters", path], "用户固定值、单位或锁定与当前视图不一致：" + path, [path], repair_class="blocked"))
    except (ValueError, TypeError, KeyError) as exc:
        issues.append(diagnostic("REQUIREMENTS_INVALID", "requirements", str(exc)))
    for key, item in leaves.items():
        source = item["source_text"]
        refs, rendered, cursor = [], [], 0
        for match in TOKEN.finditer(source):
            rendered.append(_escape(source[cursor:match.start()], table=item["table"]))
            kind, target = match[1], match[2]
            paths, value = [], ""
            try:
                if kind in ("param", "derived"):
                    if kind == "param" and target not in parameters:
                        if target in {f"{c}.{f}" for c in ("cathode", "anode") for f in ("L_um", "porosity", "R_s_um", "D_s_m2s")}:
                            value = "未确定（" + target + "缺少声明）"
                        else:
                            raise ValueError("引用未登记参数：" + target)
                    else:
                        value = display(kind, target, parameters, calculation)
                    paths = sorted(dependencies(kind, target, parameters))
                    value = f"<!-- abr-{kind}:{target} -->" + _escape(value, table=item["table"]) + "<!-- /abr-value -->"
                elif kind == "section":
                    if target not in lookup:
                        raise ValueError("章节目标不存在：" + target)
                    value = f"[第 {lookup[target]} 节](#design-{target})"
                elif kind == "cite":
                    if target not in known_evidence or not re.fullmatch(r"[\w:-]+", target):
                        raise ValueError("文献目标未归档：" + target)
                    value = "[" + target + "]"
                elif kind == "component":
                    if not components.get(target) or target not in ("cathode", "anode", "electrolyte", "separator", "current_collector"):
                        raise ValueError("组成身份未确定：" + target)
                    value = _escape(str(components[target]), table=item["table"])
                    paths = list((components.get("component_identities", {}).get(target) or {}).get("parameter_refs", []))
                elif kind == "candidate":
                    if not isinstance(candidates.get(target), str) or not ID.fullmatch(target):
                        raise ValueError("候选目标不存在：" + target)
                    value = _escape(candidates[target], table=item["table"])
                refs.append({"kind": kind, "target": target, "start": match.start(), "end": match.end(), "parameters": paths, "display": value})
            except (ValueError, TypeError, KeyError) as exc:
                issues.append(diagnostic("REFERENCE_INVALID", item["source_location"], str(exc), [target] if kind == "param" else []))
                value = _escape(match[0], table=item["table"])
            rendered.append(value)
            cursor = match.end()
        rendered.append(_escape(source[cursor:], table=item["table"]))
        remaining = TOKEN.sub("", source)
        if "{{" in remaining or "}}" in remaining:
            issues.append(diagnostic("REFERENCE_SYNTAX", item["source_location"], "引用语法无效"))
        # 手写文献编号不授予来源身份；普通化学式方括号由转义器保留。
        raw_cites = set(re.findall(r"\[([\w:-]+)\]", remaining))
        if raw_cites - {"FSI", "TFSI", "PF6", "BF4"}:
            issues.append(diagnostic("UNBOUND_CITATION", item["source_location"], "文献须使用{{cite:passage_id}}，原样编号不授予来源身份"))
        item.update(rendered_text="".join(rendered), references=refs, source_hash=digest(source))
        result.dependencies[key] = {"parameters": sorted({p for r in refs for p in r["parameters"]}),
                                    "citations": sorted({r["target"] for r in refs if r["kind"] == "cite"}),
                                    "sections": sorted({r["target"] for r in refs if r["kind"] == "section"})}
    used = {p for d in result.dependencies.values() for p in d["parameters"]}
    for path, row in parameters.items():
        if row.get("reference_origin") == "writer_v3" and path not in used:
            issues.append(diagnostic("UNBOUND_PARAMETER", ["parameters", path], "新提案未在适用正文中使用：" + path, [path]))
    parts = [START]
    length = len(START)
    def append(value, offsets=None):
        nonlocal length
        start = length + 2
        for leaf_id, relative in (offsets or {}).items():
            leaves[leaf_id]["start"] = start + relative
            leaves[leaf_id]["end"] = start + relative + len(leaves[leaf_id]["rendered_text"])
        parts.append(value)
        length = start + len(value)
    for node in nodes:
        append(f'<a id="design-{node["id"]}"></a>')
        append(f'{"#" * node["level"]} {node["number"]}{"." if node["level"] == 2 else ""} {node["title"]}')
        for group in node["blocks"]:
            block, keys = group["block"], group["leaf_ids"]
            if block["type"] == "paragraph":
                for key in keys:
                    append(leaves[key]["rendered_text"], {key: 0})
            elif block["type"] == "list":
                lines, offsets, local = [], {}, 0
                for i, key in enumerate(keys, 1):
                    prefix = f"{i}. " if block.get("ordered") else "- "
                    offsets[key] = local + len(prefix)
                    line = prefix + leaves[key]["rendered_text"]
                    lines.append(line)
                    local += len(line) + 1
                append("\n".join(lines), offsets)
            else:
                cols = [col for col in block["columns"] if isinstance(col, dict) and isinstance(col.get("name"), str)]
                lines = ["| " + " | ".join(_escape(c["name"], table=True) for c in cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
                offsets = {}
                for row in block["rows"]:
                    if isinstance(row, dict):
                        cells = [leaves.get(block["id"] + "__" + str(row.get("id")) + "__" + str(c.get("id")), {}).get("rendered_text", "") for c in cols]
                        local = sum(len(line) + 1 for line in lines) + 2
                        for col, cell in zip(cols, cells):
                            key = block["id"] + "__" + str(row.get("id")) + "__" + str(col.get("id"))
                            if key in leaves:
                                offsets[key] = local
                            local += len(cell) + 3
                        lines.append("| " + " | ".join(cells) + " |")
                append("\n".join(lines), offsets)
    append(END)
    result.markdown = "\n\n".join(parts)
    from .numeric_consistency import unbound_mentions
    result.unbound_mentions = unbound_mentions(result.markdown)
    for mention in result.unbound_mentions:
        issues.append(diagnostic("UNCLASSIFIED_QUANTITY", {"start": mention["start"], "end": mention["end"]},
            "未绑定量值须由Reviewer核对对象、工况及用途：" + mention["quote"], severity="warning", repair_class="scientific"))
    for item in leaves.values():
        if "start" not in item:
            item.update(start=-1, end=-1)
    from .stage4_research_projection import project_research
    result.research, projection_issues = project_research(links, result, parameters, requirements or {}, list(evidence))
    issues.extend(projection_issues)
    from .stage4_research_projection import _parameter
    result.render_fingerprint = digest({"compiler_version": COMPILER_VERSION, "content": content,
        "parameters": {p: _parameter(row) for p, row in parameters.items()}, "requirements": requirements,
        "evidence": list(evidence), "markdown": result.markdown})
    return result


def writer_instructions():
    example = {"version": 3, "parameters": {}, "sections": {key: {"blocks": [
        {"type": "paragraph", "id": key + "_p", "text": "本节完整科研正文；无证据写明原因和验证路径。"}]} for key in SECTION_IDS},
        "research_links": {"answers": [], "candidates": [], "arguments": []}}
    return ("\n【v3完整科研内容树，取代自由大纲、Markdown及version=2契约】\n"
        "只返回一个JSON对象，一次生成完整五节及研究关联。五节ID固定为" + json.dumps(SECTION_TITLES, ensure_ascii=False) + "。"
        "章节含blocks及可选subsections（子节含id/title/blocks/subsections，最多三层）。所有ID全篇唯一、英文小写开头。"
        + TITLE_INSTRUCTIONS +
        "blocks仅支持paragraph={id,type,text}；list={id,type,ordered,items:[{id,text}]}；"
        "table={id,type,columns:[{id,name}],rows:[{id,cells:{列id:文本}}]}。表格叶ID为表id__行id__列id。"
        "正文用普通中文和Unicode科学符号，禁止Markdown标题、表格分隔线、代码围栏、HTML；格式由程序生成。"
        "引用用{{param:字段路径}}、{{section:节id}}、{{cite:passage_id}}、{{component:cathode}}、{{candidate:候选id}}；不使用derived。"
        "已登记的输入参数和文献编号使用引用；计算过程与结果由模型直接写明方法、代入值、单位和适用口径。已有参数只能引用，未锁定空字段可登记工程提案，来源、口径、用途必须明确；"
        "参数不得自称user_requirement/model_reference或加锁。D_s非空必须给evidence并符合现有精确扩散证据契约。"
        "每个提案须在所属样品/工况的正文实际使用；候选、对照和文献参数使用独立路径及canonical_path/scope/group_id/conditions/purpose。"
        "research_links的answers每个非background requirement一项，含requirement_id/status/verification和response/reason/next_step；"
        "candidates含id/name/scope及可选candidate_role/rank，summary/rationale/risks；"
        "arguments含id/requirement_ids/basis和text。所有非空文本字段只能返回正文引用数组[{leaf_id,quote?}]，"
        "不得另写摘要。quote须在该叶源文本逐字唯一定位，省略即引用整叶。候选名/角色/排序仍须有正文依据，不推定最优。"
        "缺口回应必须关联具体原因及下一步。模型预算不得声称已运行PINN或完成实验验证。返回形状：" + json.dumps(example, ensure_ascii=False))


def recover_references(data, replacements, parameters, compiled):
    """等值引用恢复仅绑定未采用的新提案；复用已有单位/分母核验，不改正文含义。"""
    from .writer_references import equivalent
    if not isinstance(replacements, list):
        raise ValueError("references须为数组")
    result, records, used_spans = copy.deepcopy(data), [], {}
    unbound = {d["related_parameters"][0] for d in compiled.diagnostics if d["code"] == "UNBOUND_PARAMETER"}
    for item in replacements:
        if not isinstance(item, dict) or set(item) != {"field", "leaf_id", "quote", "value_quote"}:
            raise ValueError("引用恢复只能指定field/leaf_id/quote/value_quote")
        key, lid, quote, value = item["field"], item["leaf_id"], item["quote"], item["value_quote"]
        if key not in unbound or lid not in compiled.leaves or parameters[key].get("locked"):
            raise ValueError("仅可绑定未采用的新提案与已有叶节点")
        leaf = compiled.leaves[lid]
        text = leaf["source_text"]
        if not isinstance(quote, str) or not quote or not isinstance(value, str) or not value or text.count(quote) != 1 or quote.count(value) != 1:
            raise ValueError("恢复原句及完整量值须唯一定位")
        start = text.index(quote) + quote.index(value)
        end = start + len(value)
        if any(start < ref["end"] and end > ref["start"] for ref in leaf["references"]):
            raise ValueError("不能替换已有引用")
        spans = used_spans.setdefault(lid, [])
        if any(start < e and end > s for s, e in spans):
            raise ValueError("等值引用恢复范围重叠")
        if re.search(r"[A-Za-z0-9_.+\-/]$", text[:start]) or re.match(r"[A-Za-z0-9_.%/+\-~～–—⁰¹²³⁴⁵⁶⁷⁸⁹⁻℃°μµ]", text[end:].lstrip()):
            raise ValueError("必须绑定完整量值，不能截取公式/范围/复合单位")
        if not equivalent(key, parameters[key], value, text):
            raise ValueError("原文与提案值、单位或分母不等价")
        spans.append((start, end))
        records.append(dict(item, index=len(records), parameter=copy.deepcopy(parameters[key])))
    for lid, spans in used_spans.items():
        leaf = compiled.leaves[lid]
        path = leaf["source_location"]
        node = result
        for part in path[:-1]:
            node = node[part]
        text = node[path[-1]]
        edits = []
        for record in records:
            if record["leaf_id"] == lid:
                start = text.index(record["quote"]) + record["quote"].index(record["value_quote"])
                edits.append((start, start + len(record["value_quote"]), "{{param:" + record["field"] + "}}"))
        for start, end, value in sorted(edits, reverse=True):
            old = text[start:end]
            text = text[:start] + value + text[end:]
            from .stage4_research_projection import FIELDS
            for collection, names in FIELDS.items():
                for row in result["research_links"].get(collection, []):
                    for name in names:
                        for reference in row.get(name, []):
                            if reference.get("leaf_id") == lid and isinstance(reference.get("quote"), str) and old in reference["quote"]:
                                reference["quote"] = reference["quote"].replace(old, value, 1)
        node[path[-1]] = text
    return result, records
