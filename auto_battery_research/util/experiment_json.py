"""Stage 5B JSON语法恢复：一次受限标点补丁，原稿与调用机会持久留档。"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .scheme_ledger import atomic_write, digest, task_lock

PROMPT = '''你只修复输入JSON的语法，不修改实验设计。
仅返回合法JSON对象 {"base_hash":"输入原值","changes":[{"offset":整数,"delete":"原字符","insert":"替换字符"}]}。
offset是raw_response中的从0开始的Unicode字符位置，所有位置都基于原文。
最多8处小修改，每处删除和插入各不超过8字符；只能修改字符串外的括号、逗号、冒号和空白。
禁止改变引号、字段名、数值、正文、材料、单位、引用或记录顺序，禁止删掉内容来通过解析。
parse_error及error_context提供定位；如无法仅修标点完成，返回空changes，不猜补截断内容。
不要返回整份实验方案。'''


def decode(raw):
    """严格解析单个对象；重复键和非标准常量不能悄悄吞掉内容。"""
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("实验JSON重复字段：" + key)
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError("实验JSON含非法常量：" + value)

    value = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("实验JSON顶层必须为对象")
    return value


def _tokens(text):
    """记录所有不可修改的词元及字符串边界，避免标点补丁合并数值或侵入正文。"""
    decoder = json.JSONDecoder()
    tokens, strings, pos = [], [], 0
    while pos < len(text):
        char = text[pos]
        if char in "{}[]:, \t\r\n":
            pos += 1
        elif char == '"':
            _, end = decoder.raw_decode(text, pos)
            tokens.append(text[pos:end])
            strings.append((pos, end))
            pos = end
        else:
            end = pos + 1
            while end < len(text) and text[end] not in '{}[]:," \t\r\n':
                end += 1
            tokens.append(text[pos:end])
            pos = end
    return tokens, strings


def apply_patch(text, patch):
    """只执行基准匹配、互不重叠且不改任何词元的标点修改。"""
    if set(patch) != {"base_hash", "changes"} or patch["base_hash"] != digest(text):
        raise ValueError("JSON格式补丁基准不匹配")
    changes = patch["changes"]
    if not isinstance(changes, list) or not 1 <= len(changes) <= 8:
        raise ValueError("JSON格式补丁须含1至8处标点修改")
    tokens, strings = _tokens(text)
    edits = []
    for row in changes:
        if not isinstance(row, dict) or set(row) != {"offset", "delete", "insert"}:
            raise ValueError("JSON格式补丁字段非法")
        pos, old, new = row["offset"], row["delete"], row["insert"]
        if (type(pos) is not int or not 0 <= pos < len(text)
                or not isinstance(old, str) or not isinstance(new, str)
                or len(old) > 8 or len(new) > 8 or old == new
                or any(c not in "{}[]:, \t\r\n" for c in old + new)
                or text[pos:pos + len(old)] != old):
            raise ValueError("JSON格式补丁只允许原位标点修改")
        end = pos + len(old)
        if any(start < pos < stop or pos < stop and end > start for start, stop in strings):
            raise ValueError("JSON格式补丁不得修改字符串内部")
        edits.append((pos, end, new))
    edits.sort()
    for previous, current in zip(edits, edits[1:]):
        if current[0] == previous[0] or current[0] < previous[1]:
            raise ValueError("JSON格式补丁位置重叠")
    repaired, shift = text, 0
    for pos, end, new in edits:
        # 每处修改必须对应当下的解析错误，不能借修格式改写已合法的层级或数组类型。
        try:
            decode(repaired)
        except json.JSONDecodeError as exc:
            error_pos = exc.pos
        else:
            raise ValueError("JSON已经合法，拒绝额外格式修改")
        current_pos = pos + shift
        trailing_comma = (text[pos:end] == "," and not new and current_pos < error_pos
                          and not repaired[current_pos + 1:error_pos].strip())
        if current_pos != error_pos and not trailing_comma:
            raise ValueError("JSON格式补丁必须定位当前解析错误")
        repaired = repaired[:current_pos] + new + repaired[end + shift:]
        shift += len(new) - (end - pos)
    if _tokens(repaired)[0] != tokens:
        raise ValueError("JSON格式补丁改变了字段、数值或正文")
    return repaired, decode(repaired)


def parse_with_repair(raw, client, task_dir, fingerprint, audit, save_audit, check_source):
    """解析失败最多调用一次格式修复；调用前占用，恢复不重发，成功后仍需业务验收。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        return decode(text)
    except json.JSONDecodeError as exc:
        error = {"message": exc.msg, "line": exc.lineno, "column": exc.colno, "offset": exc.pos}
    # 格式补丁不能补造截断内容，也不猜改不完整字符串。
    if not text.startswith("{") or not text.endswith("}"):
        raise ValueError("实验JSON未形成完整对象，保留原稿，不用标点补丁补写截断内容")
    try:
        _tokens(text)
    except json.JSONDecodeError as exc:
        raise ValueError("实验JSON字符串不完整或转义非法，不自动修改正文") from exc
    base_hash = digest(text)
    identity = digest({"input_fingerprint": fingerprint, "base_hash": base_hash})
    path = Path(task_dir) / "experiment_json_history" / (identity + ".json")

    def save():
        atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2))
        audit["json_syntax"] = copy.deepcopy(state)
        save_audit()

    with task_lock(task_dir):
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("input_fingerprint") != fingerprint or state.get("base_hash") != base_hash:
                raise ValueError("JSON格式修复记录身份不匹配")
            audit["json_syntax"] = copy.deepcopy(state)
            save_audit()
            if "raw_response" not in state:
                raise ValueError("JSON格式修复机会已占用且未取得响应，不自动重发")
            send = False
        else:
            check_source()
            state = {"version": 1, "input_fingerprint": fingerprint, "base_hash": base_hash,
                     "original_response": raw, "parse_error": error, "status": "requesting"}
            save()
            send = True
    if send:
        had_single = hasattr(client, "single_attempt")
        previous_single = getattr(client, "single_attempt", None)
        client.single_attempt = True
        try:
            offset = error["offset"]
            state["raw_response"] = client.chat(PROMPT, json.dumps({
                "base_hash": base_hash, "raw_response": text, "parse_error": error,
                "error_context": {"offset": max(0, offset - 120),
                                  "text": text[max(0, offset - 120):offset + 120]}},
                ensure_ascii=False), temperature=0, json_mode=True, trace_tag="experiment_json_repair")
            state["response_meta"] = copy.deepcopy(getattr(client, "last_response", {}))
            state["status"] = "received"
            save()
        except Exception as exc:
            state.update(status="failed", error=str(exc))
            if hasattr(exc, "raw_response"):
                state["failed_raw_response"] = exc.raw_response
            save()
            raise ValueError("实验JSON格式修复请求失败：" + str(exc)) from exc
        finally:
            if had_single:
                client.single_attempt = previous_single
            else:
                delattr(client, "single_attempt")
    try:
        patch = decode(state["raw_response"])
        repaired, value = apply_patch(text, patch)
        check_source()
        state.update(status="repaired", patch=patch, repaired_response=repaired, repaired_hash=digest(repaired))
        state.pop("error", None)
        save()
        return value
    except (ValueError, TypeError, KeyError) as exc:
        state.update(status="failed", error=str(exc))
        save()
        raise ValueError("实验JSON格式修复未通过（最多一次）：" + str(exc)) from exc
