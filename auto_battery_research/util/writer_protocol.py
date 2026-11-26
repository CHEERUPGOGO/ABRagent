"""Writer协议恢复：兼容等价书写，按剩余错误逐轮修补，不改科研内容。"""
import copy
import json
import re

from .scheme_ledger import digest, DOMAINS, canonical_unit
from .runtime_budgets import repair_options


def single_closure_repair(raw, *, require_design=True):
    """只补解析器报错处唯一缺失的内部闭括号；不补截断尾部、不改字符串。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        decode(text)
        return None
    except json.JSONDecodeError as exc:
        offset = exc.pos
    except (ValueError, TypeError):
        return None
    if offset >= len(text) or text[offset] not in "}]":
        return None
    stack, quoted, escaped = [], False, False
    for char in text[:offset]:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or stack.pop() != {"}": "{", "]": "["}[char]:
                return None
    if quoted or len(stack) < 2:
        return None
    closing = {"{": "}", "[": "]"}[stack[-1]]
    if closing == text[offset] or {"{": "}", "[": "]"}[stack[-2]] != text[offset]:
        return None
    repaired = text[:offset] + closing + text[offset:]
    try:
        data = decode(repaired)
        from .design_structure import SECTION_IDS
        if not isinstance(data, dict) or require_design and set(data.get("sections", {})) != set(SECTION_IDS):
            return None
    except (ValueError, TypeError):
        return None
    return {"base_hash": digest(raw), "offset": offset, "insert": closing,
            "repaired_hash": digest(repaired), "repaired_response": repaired}


def syntax_recovery_available(seed):
    state = seed.get("writer_protocol") or {}
    raw = state.get("completion_raw_response", state.get("raw_response", ""))
    return (not seed.get("draft") and bool(seed.get("writer_request"))
            and not state.get("syntax_repair") and bool(single_closure_repair(raw)))


def incomplete_json(raw):
    """识别JSON在字符串/容器内结束；完整但协议非法的输出不触发重新生成。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    if not text.startswith("{"):
        return False
    try:
        json.loads(text)
        return False
    except json.JSONDecodeError:
        pass
    stack, quoted, escaped = [], False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or stack.pop() != {"}": "{", "]": "["}[char]:
                return False
    return bool(stack or quoted)


def complete_response(client, system, user, state, save):
    """网关误报stop的截断只用尚未使用的完整输出重试机会，原响应始终保留。"""
    raw = state.get("raw_response", "")
    if "completion_raw_response" in state:
        return state["completion_raw_response"]
    meta = state.get("response_meta") or {}
    if (not incomplete_json(raw) or meta.get("attempt") != 1 or state.get("repair_attempts", 0)
            or not getattr(client, "available", False)):
        return raw
    if state.get("completion_attempted"):
        raise ValueError("Writer完整输出重试已占用且未返回，不能自动重发")
    state["completion_attempted"] = True
    save()
    old_budget = getattr(client, "max_tokens", None)
    had_single = hasattr(client, "single_attempt")
    old_single = getattr(client, "single_attempt", False)
    try:
        if isinstance(old_budget, int):
            client.max_tokens = min(old_budget * 2, getattr(client, "output_max_tokens", old_budget * 2))
        client.single_attempt = True
        response = client.chat(system, user + "\n\n上次JSON在中途结束。请精简推理和重复元数据，"
            "保证全部五个主章节及JSON完整；正文不超过2500中文字。继续严格遵守既有参数和用户锁定，禁止省略结尾。",
            temperature=.2, json_mode=True, trace_tag="writer_completion_retry")
        state["completion_raw_response"] = response
        state["completion_response_meta"] = copy.deepcopy(getattr(client, "last_response", {}))
        save()
        return response
    finally:
        if isinstance(old_budget, int):
            client.max_tokens = old_budget
        if had_single:
            client.single_attempt = old_single
        else:
            delattr(client, "single_attempt")


def completion_recovery_available(seed):
    """已完成诊断但尚有一次完整输出机会时，允许在原检查点恢复。"""
    state = seed.get("writer_protocol") or {}
    return (not seed.get("draft") and bool(seed.get("writer_request"))
            and (state.get("response_meta") or {}).get("attempt") == 1
            and not state.get("repair_attempts", 0) and not state.get("completion_attempted")
            and incomplete_json(state.get("raw_response", "")))


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Writer JSON重复字段：" + key)
            result[key] = value
        return result
    return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()), object_pairs_hook=unique)


def section_nodes(data):
    def visit(node):
        if not isinstance(node, dict):
            raise ValueError("章节必须是对象")
        yield node
        for child in node.get("subsections", []):
            yield from visit(child)
    for node in data.get("sections", {}).values():
        yield from visit(node)


def can_complete_c_rate_unit(path, row):
    """仅接受规范倍率身份及原声明的明确倍率含义，不补电流/容量单位或锁定缺项。"""
    import math
    from .numeric_consistency import canonical
    if (row.get("locked") or row.get("unit") not in (None, "")
            or canonical(path, row) != "condition.c_rate"
            or type(row.get("value")) not in (int, float)
            or not math.isfinite(row["value"]) or row["value"] <= 0
            or row.get("quantity_kind") not in (None, "physical", "other")):
        return False
    meaning = str(row.get("meaning") or "")
    if re.search(r"不是|并非|非倍率|不代表|不适用|待确认|待确定|未知|电流|容量|温度|摄氏|库仑|\bnot\b|\bunknown\b|\bTBD\b|current|capacity|temperature|coulomb", meaning, re.I):
        return False
    return bool(re.search(r"倍率|\bC[- ]?rate\b|\b(?:cycling|charge|discharge) rate\b", meaning, re.I))


def normalize(data, *, normalize_units=True, normalize_metadata=True):
    """规范ID、等价单位和通用类型标签，不换算值、不猜材料身份。"""
    from .design_structure import SECTION_IDS
    from .numeric_consistency import canonical, UNITS
    result = copy.deepcopy(data)
    if not isinstance(result, dict):
        raise ValueError("Writer输出必须为JSON对象")
    mapping, seen = {}, set(SECTION_IDS)
    for node in section_nodes(result):
        if "id" not in node:
            continue
        old = node["id"]
        if not isinstance(old, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", old):
            raise ValueError("章节ID格式非法：" + str(old))
        new = old.lower()
        if new in seen:
            raise ValueError("章节ID规范化后重复：" + new)
        seen.add(new)
        mapping[old] = new
        node["id"] = new
    for node in section_nodes(result):
        if isinstance(node.get("body"), str):
            node["body"] = re.sub(r"\{\{section:([^}]+)\}\}", lambda m: "{{section:" + mapping.get(m[1], m[1]) + "}}", node["body"])
    for path, row in (result.get("parameters") or {}).items():
        if normalize_metadata and isinstance(row, dict):
            from .numeric_bindings import normalize_declaration
            row = result["parameters"][path] = normalize_declaration(row)
        if normalize_units and isinstance(row, dict) and can_complete_c_rate_unit(path, row):
            row["unit"] = "C"
        if isinstance(row, dict) and row.get("unit") in ("1", "fraction", None) and (
                canonical_unit(path) == "" or UNITS.get(canonical(path, row)) == ""):
            row["unit"] = ""
        elif normalize_units and isinstance(row, dict) and isinstance(row.get("unit"), str):
            from .numeric_consistency import unit
            expected = canonical_unit(path)
            if expected is None:
                expected = UNITS.get(canonical(path, row))
            # 只规范同尺度书写：μm/µm→um、m²/s→m2/s；mm→um需换值，不能在这里执行。
            if expected is not None and unit(row["unit"]) == unit(expected):
                row["unit"] = expected
    return result


def without_writer_lock_claims(data):
    """只去掉模型无权设置的布尔锁定，不能借此扩大单位补全或其他协议修复权限。"""
    result = copy.deepcopy(data)
    for row in (result.get("parameters") or {}).values():
        if isinstance(row, dict) and type(row.get("locked")) is bool:
            row.pop("locked")
    return result


def parse(data, current):
    from .design_structure import render_design
    from .numeric_bindings import declarations, source_parameters, normalize_material_references
    if data.get("version") != 2:
        raise ValueError("新设计须使用version=2参数引用契约")
    data = without_writer_lock_claims(data)
    text, contract = render_design(data, current_parameters=current)
    text, parameters = normalize_material_references(text, source_parameters(text, declarations(data, current)))
    return {"draft_answer": text, "structured_sections": data, "design_structure": contract,
            "numeric_contract": 1, "calculation_policy": "model", "numeric_parameters": parameters, "fallback": False}


def diagnostics(data, current, first):
    from .writer_references import collect_errors
    return collect_errors(without_writer_lock_claims(data) if isinstance(data, dict) else data, current, first)


def parameter_path(path):
    """参数路径兼容点分字符串和JSON路径数组。"""
    if isinstance(path, list):
        parts = path[1:] if path[:1] == ["parameters"] else path
        if not parts or any(not isinstance(part, str) for part in parts):
            raise ValueError("参数路径须为字段字符串或字符串数组")
        path = ".".join(parts)
    if not isinstance(path, str):
        raise ValueError("参数路径须为字段字符串或字符串数组")
    return path


def metadata_patch(data, patch):
    """仅重命名参数路径、改协议元数据；所有值和正文逐字保留（引用路径随唯一映射替换）。"""
    if not isinstance(patch, dict) or set(patch) != {"base_hash", "parameters"} or patch["base_hash"] != digest(data):
        raise ValueError("协议补丁基准不一致或越权")
    if not isinstance(patch["parameters"], list):
        raise ValueError("协议补丁parameters必须是数组")
    result, mapping, used = copy.deepcopy(data), {}, set()
    params = result.get("parameters", {})
    allowed = {"unit", "quantity_kind", "denominator"}
    for change in patch["parameters"]:
        if not isinstance(change, dict) or set(change) - {"path", "new_path", "metadata"}:
            raise ValueError("协议修复只能修改字段路径与列明元数据")
        old = parameter_path(change.get("path"))
        new = parameter_path(change.get("new_path", old))
        if old not in params or old in used or new.split(".")[0] not in DOMAINS:
            raise ValueError("协议修复路径重复、未知或命名空间非法")
        if old.split(".")[0] in DOMAINS and new != old:
            raise ValueError("已有规范路径不能通过协议修复改绑：" + old)
        if old.split(".")[0] not in DOMAINS:
            prefix, _, tail = old.partition(".")
            expected = {"doping": "cathode.doping", "synthesis": "process", "test": "condition"}.get(prefix)
            if expected is None or new != expected + "." + tail:
                raise ValueError("未知字段只允许明确命名空间映射，不能猜测物理含义：" + old)
        used.add(old)
        mapping[old] = new
        meta = change.get("metadata", {})
        if not isinstance(meta, dict) or set(meta) - allowed:
            raise ValueError("协议修复不能更改数值、来源、组别、条件或锁定")
        from .numeric_bindings import normalize_declaration
        before = params[old] = normalize_declaration(params[old])
        # 已有语义不能重定义；只能补缺元数据。单位等价书写由程序处理。
        for key, value in meta.items():
            complete_rate = key == "unit" and value == "C" and can_complete_c_rate_unit(new, before)
            if key == "denominator" and not before.get(key) and value:
                support = " ".join(str(before.get(k) or "") for k in ("unit", "meaning", "source"))
                if not isinstance(value, str) or value not in support:
                    raise ValueError("补充分母必须逐字来自原单位或原声明，不能推断：" + old)
            if key == "unit" and before.get("unit") is None and value and not complete_rate:
                raise ValueError("原声明没有单位，协议修复不能补造单位：" + old)
            if before.get(key) is not None and before[key] != value:
                from .numeric_consistency import unit
                from .parameter_presentation import QUANTITY_KINDS
                equivalent = key == "unit" and (complete_rate or unit(before[key]) == unit(value) or before[key] in ("1", "fraction") and value == "")
                if key == "quantity_kind":
                    equivalent = (isinstance(value, str) and value in QUANTITY_KINDS
                                  and (not isinstance(before[key], str) or before[key] not in QUANTITY_KINDS))
                if not equivalent:
                    raise ValueError("协议修复不能改变已有计量口径：" + old + "." + key)
        before.update(meta)
    mapped = {}
    for old, row in params.items():
        new = mapping.get(old, old)
        if new in mapped:
            raise ValueError("参数路径映射冲突")
        mapped[new] = row
    result["parameters"] = mapped
    for node in section_nodes(result):
        if isinstance(node.get("body"), str):
            node["body"] = re.sub(r"\{\{param:([^}]+)\}\}", lambda m: "{{param:" + mapping.get(m[1], m[1]) + "}}", node["body"])
    for row in mapped.values():
        if isinstance(row.get("ratio_inputs"), list):
            row["ratio_inputs"] = [mapping.get(path, path) for path in row["ratio_inputs"]]
    return result


def protocol_patch(data, patch, current, client, state, save, *, grants=None, evidence=(), context=""):
    """旧元数据补丁兼容；正文改动只能经过精确引用替换与独立复核。"""
    if (not isinstance(patch, dict) or set(patch) - {"base_hash", "parameters", "references", "corrections", "titles"}
            or not {"base_hash", "parameters"} <= patch.keys() or patch["base_hash"] != digest(data)):
        raise ValueError("协议补丁基准不一致或越权")
    from .writer_titles import apply as apply_titles, verify as verify_titles
    from .writer_protocol_advice import apply_corrections, verify
    corrected, corrected_records = apply_corrections(data, patch.get("corrections", []), current, grants or {})
    touched = {path for r in corrected_records for path in (r["path"], r.get("new_path", r["path"]))}
    if not isinstance(patch["parameters"], list) or any(
            not isinstance(change, dict) or parameter_path(change.get("path")) in touched
            for change in patch["parameters"]):
        raise ValueError("签发的协议转换不能同时叠加任意元数据修改")
    metadata = normalize(metadata_patch(corrected, {"base_hash": digest(corrected), "parameters": patch["parameters"]}))
    from .writer_references import apply_references, verify_references
    repaired, records = apply_references(metadata, patch.get("references", []), current)
    verify_references(client, metadata, repaired, records, current, state, save)
    repaired, title_records = apply_titles(repaired, patch.get("titles", []))
    verify_titles(client, title_records, state, save)
    # 引用复核和参数转换分别留档；转换通过不等于方案已获科学审核支持。
    verify(client, data, repaired, corrected_records, current, evidence, context, state, save)
    return repaired


def produce(client, system, user, current, state, save, *, budget=None, evidence=()):
    """初次生成及协议修复调用前占用机会，重启不重发，不占正文/学术审核预算。"""
    from .writer_protocol_advice import AUTHORITY_VERSION
    limits = repair_options(budget)
    state["repair_budget"] = limits
    from .writer_titles import VERSION as TITLE_VERSION, INSTRUCTIONS as TITLE_INSTRUCTIONS
    state["title_contract_version"] = TITLE_VERSION
    signature = digest(current)
    if state.get("parameters_hash", signature) != signature:
        return {"draft_answer": "", "raw_response": state.get("raw_response", ""), "fallback": True,
                "error": "Writer协议检查点的参数基准已变化，拒绝恢复旧响应", "protocol_state": copy.deepcopy(state)}
    state.setdefault("parameters_hash", signature)
    if "raw_response" not in state:
        if state.get("generation_attempted"):
            return {"draft_answer": "", "fallback": True, "error": "Writer生成中断，原调用未返回；须显式恢复或重建", "protocol_state": state}
        state["generation_attempted"] = True
        save()
        try:
            state["raw_response"] = client.chat(system, user, temperature=.2, json_mode=True, trace_tag="writer")
            state["response_meta"] = copy.deepcopy(getattr(client, "last_response", {}))
        except Exception as exc:
            state["raw_response"], state["error"] = "", str(exc)
        save()
    raw = state["raw_response"]
    data = None
    try:
        raw = complete_response(client, system, user, state, save)
        syntax = single_closure_repair(raw)
        if state.get("syntax_repair") and state["syntax_repair"] != syntax:
            raise ValueError("Writer单字符语法恢复与原始响应不一致")
        if syntax:
            state["syntax_repair"] = syntax
            save()
        legacy_data = normalize(decode(syntax["repaired_response"] if syntax else raw),
                                normalize_units=False, normalize_metadata=False)
        data = normalize(legacy_data)
        ignored_locks = [{"path": path, "requested_locked": row["locked"]}
                         for path, row in (legacy_data.get("parameters") or {}).items()
                         if isinstance(row, dict) and type(row.get("locked")) is bool]
        if ignored_locks:
            state["ignored_writer_lock_claims"] = {"raw_hash": digest(legacy_data), "fields": ignored_locks}
            save()
        repair_keys = ("repair_raw_response", "accepted_patch", "patch_audit", "reference_review", "title_review",
                       "parameter_advice", "parameter_review")
        history = state.setdefault("repair_history", [])
        if not history and state.get("repair_attempts"):
            history.append({key: copy.deepcopy(state[key]) for key in repair_keys if key in state})
        cursor, last_error = 0, None
        while True:
            try:
                result = parse(data, current)
                break
            except (ValueError, TypeError, KeyError) as first:
                parse_error = first
                state["error"] = str(last_error or first)
                state["diagnostics"] = diagnostics(data, current, last_error or first)
                save()
                request = {"base_hash": digest(data), "errors": state["diagnostics"], "document": data,
                           "current_parameters": current, "allowed_domains": sorted(DOMAINS),
                           "title_format": TITLE_INSTRUCTIONS}
                fresh = cursor == len(history)
                if fresh:
                    if state.get("repair_attempts", 0) >= limits["protocol_rounds"] or not getattr(client, "available", False):
                        raise last_error or first
                    history.append({"authority_version": AUTHORITY_VERSION, "title_contract_version": TITLE_VERSION})
                    state["repair_attempts"] = state.get("repair_attempts", 0) + 1
                attempt = history[cursor]
                cursor += 1

            def save_attempt():
                # 顶层保留最新一轮的旧接口；每轮复核响应分别保存，恢复不重发。
                for key in repair_keys:
                    if key in attempt:
                        state[key] = attempt[key]
                    else:
                        state.pop(key, None)
                save()

            grants = {}
            if attempt.get("authority_version", 0) >= 1:
                from .writer_protocol_advice import advise
                grants = advise(client, data, current, state["diagnostics"], list(evidence), user,
                                attempt, save_attempt, raw_data=legacy_data,
                                allow_scope=attempt["authority_version"] >= AUTHORITY_VERSION)
                request["allowed_corrections"] = grants
            if fresh:
                save_attempt()
                had_single = hasattr(client, "single_attempt")
                old_single = getattr(client, "single_attempt", False)
                try:
                    client.single_attempt = True
                    attempt["repair_raw_response"] = client.chat(
                '只修Writer输出协议，不重写科学正文，不改数值、公式、来源、组别或条件。返回JSON {"base_hash":"原哈希",'
                '"parameters":[{"path":"原路径字符串，也兼容[parameters,字段]数组","new_path":"规范路径",'
                '"metadata":{"quantity_kind":"缺失或非法标签可改为text/physical/ratio等",'
                '"unit":"等价书写；规范c_rate且原meaning明确倍率时可将空单位补C，不能改其他单位","denominator":"仅补缺失分母"}}],'
                '"corrections":["allowed_corrections中程序签发的转换编号，无此权限则为空数组"],'
                '"titles":[{"path":["sections","goals","subsections",0,"title"],"old":"原标题","new":"修正后的纯文本名称"}],'
                '"references":[{"field":"未绑定字段的规范路径","body_path":["sections","goals","body"],'
                '"quote":"该body内唯一定位的上下文原句","value_quote":"原句内唯一且完整的值及单位或文字声明"}]}。'
                '字段重命名只允许doping.*→cathode.doping.*、synthesis.*→process.*、test.*→condition.*且保持末尾字段不变；'
                '已有合法路径不得改绑。references仅补errors中的UNBOUND_PARAMETER：程序将value_quote替换为{{param:field}}，'
                '你不能提交替换正文。body_path可经subsections及数组索引定位，但不能指向title/参数/来源。'
                'titles是独立的小标题格式补丁，只能修改本轮errors中TITLE_开头错误的准确path，old必须等于原值；'
                '逐项根据原因改为title_format规定格式，保持原意及科学量值，不能改有效标题、ID、顺序、正文或主标题。'
                '名称含义变化须独立复核，无法修复的保留；其他协议补丁权限不扩大。'
                '引用必须同样品、同工况、同用途和分母；不引用文献值、其他候选/对照值，不改已有引用或锁定值。'
                '文字允许原句与原值的等价表达，但quote/body须明确包含原值的完整含义；'
                '占位关系等可由同一正文上下文明确声明，程序将送独立语义复核，不能用参数说明或常识补缺失关系。'
                'corrections是受控例外：程序已按只读模型建议签发具体字段及前后等值表示/待验证采用，'
                '你只能选择编号，不能自填value/basis/source；原值与来源仍归档，并须另作独立语义复核。'
                'scope_relocation编号已包含独立new_path和原canonical_path，选择编号会由程序同步正文引用与依赖；'
                '不能再通过parameters自填重命名，也不能删改条件、样品或用途。'
                '不得对同一个corrections字段再提交metadata。禁止改写路线或由化学式推导比例。仅修有明确依据的项；'
                '按本轮errors修复全部有依据的项；已修复部分保持不变。无法修复的项保留，不补造值；无可改项可返回空数组。',
                        json.dumps(request, ensure_ascii=False), temperature=.0, json_mode=True, trace_tag="writer_protocol_repair")
                finally:
                    if had_single:
                        client.single_attempt = old_single
                    else:
                        delattr(client, "single_attempt")
                save_attempt()
            if "repair_raw_response" not in attempt:
                raise ValueError("Writer协议修复已占用但未返回，保留原调用，不自动重发")
            unchanged = False
            try:
                patch = attempt.get("accepted_patch") or decode(attempt["repair_raw_response"])
                # 旧补丁兼容单位及元数据规范前的原始签名。
                base = legacy_data if (cursor == 1 and isinstance(patch, dict)
                                       and patch.get("base_hash") == digest(legacy_data)) else data
                repaired = protocol_patch(base, patch, current, client, attempt, save_attempt,
                                          grants=grants, evidence=list(evidence), context=user)
                attempt["accepted_patch"] = patch
                attempt["patch_audit"] = {"before_hash": patch["base_hash"], "after_hash": digest(repaired),
                                          "reference_count": len(patch.get("references", []))}
                save_attempt()
                unchanged = repaired == data
                if unchanged:
                    # 旧接口无法修title的空补丁只重放，不占新调用；剩余预算允许使用新接口。
                    from .writer_titles import issues as title_issues
                    if attempt.get("title_contract_version", 0) < TITLE_VERSION and title_issues(data):
                        continue
                    if attempt.get("authority_upgrade_pending"):
                        continue
                    # 按旧轮次权限重放；新增作用域权限只在剩余预算中新开一轮。
                    # 当前版本空补丁仍立即停止，不能以升级为由循环重发旧请求。
                    if attempt.get("authority_version", 0) < AUTHORITY_VERSION and cursor == len(history):
                        from .writer_protocol_advice import candidates
                        choices = candidates(data, current, state["diagnostics"], evidence)
                        upgrade = (any('scope_relocation' in c['actions'] for c in choices.values())
                                   if attempt.get("authority_version") else
                                   candidates(legacy_data, current, state["diagnostics"], evidence))
                        if upgrade:
                            attempt["authority_upgrade_pending"] = True
                            save_attempt()
                            continue
                    raise parse_error
                data, last_error = repaired, None
            except (ValueError, TypeError, KeyError) as exc:
                last_error = exc
                attempt["error"] = str(exc)
                save_attempt()
                review = attempt.get("reference_review") or {}
                if (review.get("attempted") or (attempt.get("parameter_review") or {}).get("attempted")
                        or (attempt.get("title_review") or {}).get("attempted") or unchanged):
                    raise
        state.update(status="READY", error=None)
        state["diagnostics"] = []
        result.update(raw_response=raw, protocol_state=copy.deepcopy(state))
        save()
        return result
    except Exception as exc:
        state.update(status="INVALID", error=str(exc))
        state["diagnostics"] = diagnostics(data, current, exc)
        state["error"] = "；".join(dict.fromkeys(
            (json.dumps(item["path"], ensure_ascii=False) + "：" + item["reason"]
             if item["code"].startswith("TITLE_") else item["reason"])
            for item in state["diagnostics"]))
        save()
        return {"draft_answer": "", "raw_response": raw, "fallback": True, "error": state["error"],
                "diagnostics": copy.deepcopy(state["diagnostics"]), "protocol_state": copy.deepcopy(state)}
