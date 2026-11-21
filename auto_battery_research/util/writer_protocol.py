"""Writer协议有限恢复：先保存原始响应，只允许一次不改科研内容的结构补丁。"""
import copy
import json
import re

from .scheme_ledger import digest, DOMAINS, canonical_unit

MAX_REPAIRS = 1


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
            temperature=.2, trace_tag="writer_completion_retry")
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


def normalize(data):
    """仅修唯一ID大小写和明确无量纲单位，不改值、不猜章节或材料身份。"""
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
        if isinstance(row, dict) and row.get("unit") in ("1", "fraction", None) and (
                canonical_unit(path) == "" or UNITS.get(canonical(path, row)) == ""):
            row["unit"] = ""
    return result


def parse(data, current):
    from .design_structure import render_design
    from .numeric_bindings import declarations, source_parameters
    if data.get("version") != 2:
        raise ValueError("新设计须使用version=2参数引用契约")
    text, contract = render_design(data, current_parameters=current)
    return {"draft_answer": text, "structured_sections": data, "design_structure": contract,
            "numeric_contract": 1, "numeric_parameters": source_parameters(text, declarations(data, current)), "fallback": False}


def diagnostics(data, current, first):
    from .writer_references import collect_errors
    return collect_errors(data, current, first)


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
        old, new = change.get("path"), change.get("new_path", change.get("path"))
        if old not in params or old in used or not isinstance(new, str) or new.split(".")[0] not in DOMAINS:
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
        before = params[old]
        # 已有语义不能重定义；只能补缺元数据。单位等价书写由程序处理。
        for key, value in meta.items():
            if key == "denominator" and not before.get(key) and value:
                support = " ".join(str(before.get(k) or "") for k in ("unit", "meaning", "source"))
                if not isinstance(value, str) or value not in support:
                    raise ValueError("补充分母必须逐字来自原单位或原声明，不能推断：" + old)
            if key == "unit" and before.get("unit") is None and value:
                raise ValueError("原声明没有单位，协议修复不能补造单位：" + old)
            if before.get(key) is not None and before[key] != value:
                from .numeric_consistency import unit
                equivalent = key == "unit" and (unit(before[key]) == unit(value) or before[key] in ("1", "fraction") and value == "")
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


def protocol_patch(data, patch, current, client, state, save):
    """旧元数据补丁兼容；正文改动只能经过精确引用替换与独立复核。"""
    if (not isinstance(patch, dict) or set(patch) - {"base_hash", "parameters", "references"}
            or not {"base_hash", "parameters"} <= patch.keys() or patch["base_hash"] != digest(data)):
        raise ValueError("协议补丁基准不一致或越权")
    metadata = metadata_patch(data, {k: patch[k] for k in ("base_hash", "parameters")})
    from .writer_references import apply_references, verify_references
    repaired, records = apply_references(metadata, patch.get("references", []), current)
    verify_references(client, metadata, repaired, records, current, state, save)
    return repaired


def produce(client, system, user, current, state, save):
    """初次生成及协议修复调用前占用机会，重启不重发，不占正文/学术审核预算。"""
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
            state["raw_response"] = client.chat(system, user, temperature=.2, trace_tag="writer")
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
        data = normalize(decode(syntax["repaired_response"] if syntax else raw))
        if state.get("accepted_patch"):
            data = protocol_patch(data, state["accepted_patch"], current, client, state, save)
        try:
            result = parse(data, current)
        except (ValueError, TypeError, KeyError) as first:
            state["error"] = str(first)
            save()
            request = {"base_hash": digest(data), "errors": diagnostics(data, current, first), "document": data,
                       "current_parameters": current, "allowed_domains": sorted(DOMAINS)}
            state["diagnostics"] = request["errors"]
            save()
            if state.get("accepted_patch"):
                raise  # 已接受的局部补丁仍有剩余问题，只披露，不重新占用协议机会。
            if "repair_raw_response" in state:
                reply = state["repair_raw_response"]
            else:
                if state.get("repair_attempts", 0) >= MAX_REPAIRS or not getattr(client, "available", False):
                    raise
                state["repair_attempts"] = state.get("repair_attempts", 0) + 1
                save()
                had_single = hasattr(client, "single_attempt")
                old_single = getattr(client, "single_attempt", False)
                try:
                    client.single_attempt = True
                    reply = client.chat(
                '只修Writer输出协议，不重写科学正文，不改数值、公式、来源、组别或条件。返回JSON {"base_hash":"原哈希",'
                '"parameters":[{"path":"原路径","new_path":"规范路径","metadata":{"quantity_kind":"缺失时可补text/physical等",'
                '"unit":"只允许等价书写","denominator":"仅补缺失分母"}}],'
                '"references":[{"field":"未绑定字段的规范路径","body_path":["sections","goals","body"],'
                '"quote":"该body内唯一定位的上下文原句","value_quote":"原句内唯一且完整的值及单位或文字声明"}]}。'
                '字段重命名只允许doping.*→cathode.doping.*、synthesis.*→process.*、test.*→condition.*且保持末尾字段不变；'
                '已有合法路径不得改绑。references仅补errors中的UNBOUND_PARAMETER：程序将value_quote替换为{{param:field}}，'
                '你不能提交替换正文。body_path可经subsections及数组索引定位，但不能指向title/参数/来源。'
                '引用必须同样品、同工况、同用途和分母；不引用文献值、其他候选/对照值，不改已有引用或锁定值。'
                '文字只允许原值等价书写，禁止改写路线或由化学式推导比例。仅修有明确依据的项；'
                '无法安全修复的项保留，不删声明、不补造值，可返回空数组。',
                        json.dumps(request, ensure_ascii=False), temperature=.0, trace_tag="writer_protocol_repair")
                finally:
                    if had_single:
                        client.single_attempt = old_single
                    else:
                        delattr(client, "single_attempt")
            state["repair_raw_response"] = reply
            save()
            patch = decode(reply)
            repaired = protocol_patch(data, patch, current, client, state, save)
            state["accepted_patch"] = patch
            state["patch_audit"] = {"before_hash": digest(data), "after_hash": digest(repaired),
                                    "reference_count": len(patch.get("references", []))}
            data = repaired
            save()
            result = parse(data, current)
        state.update(status="READY", error=None)
        state["diagnostics"] = []
        result.update(raw_response=raw, protocol_state=copy.deepcopy(state))
        save()
        return result
    except Exception as exc:
        state.update(status="INVALID", error=str(exc))
        state["diagnostics"] = diagnostics(data, current, exc)
        state["error"] = "；".join(dict.fromkeys(item["reason"] for item in state["diagnostics"]))
        save()
        return {"draft_answer": "", "raw_response": raw, "fallback": True, "error": state["error"],
                "diagnostics": copy.deepcopy(state["diagnostics"]), "protocol_state": copy.deepcopy(state)}
