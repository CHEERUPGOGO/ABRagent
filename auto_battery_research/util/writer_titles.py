"""Writer 小标题契约：逐项定位格式错误，仅授权失败标题的局部补丁。"""
import copy
import json
import re

from .scheme_ledger import digest

VERSION = 1
INSTRUCTIONS = (
    '【小标题固定格式】subsections每项的title只写单行纯文本名称字符串，'
    'title 长度为1–100字符，无首尾空白，不加章节编号、Markdown、HTML、模板引用或换行。'
    '编号和标题层级由程序生成，不能写“1. 推荐组合”“5.1 已知风险”“（一）选材依据”或“### 推荐组合”。'
    '科学量值属于名称内容，可以写“1 C 口径与目标判据”“0.5 C 倍率性能”“25 ℃ 循环条件”'
    '和“保持率 >74% 的判据”；不要为去编号而删除或改动科学数值。'
)
MARKUP = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029#\[\]{}*_`]|~~|^\s*(?:>|[-+])[ \t]+|<\s*(?:/?[A-Za-z]|!)[^>]*>")
# 裸数字加空格不是编号；小数点不能作为编号分隔符。层级号后接量值单位时保留。
EXPLICIT_NUMBERING = (
    r"^\s*(?:\d+[、．。)）]|\d+\.(?!\d)|[一二三四五六七八九十百]+[、.．)）]"
    r"|[（(](?:\d+(?:\.\d+)*|[一二三四五六七八九十百]+)[）)]"
    r"|第[一二三四五六七八九十百\d]+[章节、])\s*"
)
NUMBERING = re.compile(EXPLICIT_NUMBERING + r"|^\s*\d+(?:\.\d+)+[ \t]+(?=[\u4e00-\u9fff])")


class TitleFormatError(ValueError):
    """保留机器可读路径和逐项原因，供协议修复器签发权限。"""
    def __init__(self, issues):
        self.issues = issues
        super().__init__("；".join(f"{json.dumps(i['path'], ensure_ascii=False)}：{i['reason']}" for i in issues))


def entries(data):
    """只枚举原始内容树中的子节，主标题与正文不获得修改权限。"""
    def visit(node, path, depth):
        if not isinstance(node, dict) or depth > 3:
            return
        children = node.get("subsections")
        if not isinstance(children, list):
            return
        for index, child in enumerate(children):
            if isinstance(child, dict):
                child_path = path + ["subsections", index]
                yield child_path + ["title"], child
                yield from visit(child, child_path, depth + 1)
    sections = data.get("sections", {}) if isinstance(data, dict) else {}
    if isinstance(sections, dict):
        for key, node in sections.items():
            yield from visit(node, ["sections", key], 1)


def issues(data):
    """一次汇总所有失败小标题，返回原值、精确路径、错误类型与修复约束。"""
    result = []
    for path, node in entries(data):
        title = node.get("title")
        errors = []
        if not isinstance(title, str):
            errors.append(("TITLE_TYPE", "title必须是字符串"))
        elif not title.strip():
            errors.append(("TITLE_EMPTY", "小标题不能为空，须根据本节已有正文给出名称"))
        else:
            if len(title) > 100:
                errors.append(("TITLE_LENGTH", f"小标题长度{len(title)}超过100字符"))
            if title != title.strip():
                errors.append(("TITLE_WHITESPACE", "小标题不能含首尾空白"))
            if MARKUP.search(title):
                errors.append(("TITLE_MARKUP", "小标题须为单行纯文本，不能含Markdown、HTML、模板标记或控制字符"))
            if NUMBERING.match(title):
                errors.append(("TITLE_NUMBERING", "小标题不能自行添加章节编号；编号由程序生成，科学量值必须保留"))
        for code, reason in errors:
            result.append(dict(code=code, path=path, reason=reason, title=title,
                               node_id=node.get("id"), expected=INSTRUCTIONS))
    return result


def _plain(title):
    """仅比较明确格式包装；不对科学数字、词语、否定或大小写做等价猜测。"""
    if not isinstance(title, str):
        return None
    value = re.sub(r"^[#]+\s*", "", title.strip()).strip()
    while True:
        prior = value
        wrapper = re.fullmatch(r"<\s*(h[1-6]|strong|em|b|i|u|span)(?:\s+[^<>]*)?>\s*(.*?)\s*</\s*\1\s*>",
                               value, re.I | re.S)
        if wrapper:
            value = wrapper[2].strip()
        for marker in ("**", "__", "~~", "`", "*", "_"):
            if value.startswith(marker) and value.endswith(marker) and len(value) > 2 * len(marker):
                value = value[len(marker):-len(marker)].strip()
                break
        if value == prior:
            break
    # 不删变量名内部符号或未知标签；小数与层级号有歧义，删除前缀必须复核。
    value = re.sub(EXPLICIT_NUMBERING, "", value, count=1)
    return re.sub(r"\s+", " ", value).strip()


def recovery_available(seed, *, budget=None):
    """旧标题误判/无标题权限的检查点可重验一次，保留原响应及已用协议次数。"""
    state = seed.get("writer_protocol") or {}
    if seed.get("draft") or not seed.get("writer_request") or state.get("title_contract_version") == VERSION:
        return False
    error = str(state.get("error") or (seed.get("writer_output") or {}).get("error") or "")
    if "子节标题" not in error and "小标题" not in error:
        return False
    from .writer_protocol import decode, normalize
    from .runtime_budgets import repair_options
    try:
        data = normalize(decode(state.get("completion_raw_response", state.get("raw_response", ""))))
    except (ValueError, TypeError, KeyError):
        return False
    return not issues(data) or state.get("repair_attempts", 0) < repair_options(budget)["protocol_rounds"]


def apply(data, changes):
    """补丁只能改本轮明确报错的title；原值、路径和新格式必须逐项匹配。"""
    if not isinstance(changes, list):
        raise ValueError("titles必须是小标题补丁数组")
    allowed = {tuple(i["path"]) for i in issues(data)}
    nodes = {tuple(path): node for path, node in entries(data)}
    result, records, seen = copy.deepcopy(data), [], set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"path", "old", "new"}:
            raise ValueError("小标题补丁只能包含path、old、new")
        path = change["path"]
        if (not isinstance(path, list) or any(type(k) not in (str, int) for k in path)
                or tuple(path) not in allowed or tuple(path) in seen):
            raise ValueError("小标题补丁只能唯一定位本轮报错的title，不能修改有效标题、正文、ID或主章节")
        seen.add(tuple(path))
        node = nodes[tuple(path)]
        if type(change["old"]) is not type(node.get("title")) or change["old"] != node.get("title"):
            raise ValueError("小标题补丁的原标题已变化")
        new = change["new"]
        probe = {"sections": {"probe": {"subsections": [{"title": new}]}}}
        if issues(probe):
            raise ValueError("修改后的小标题仍不符合格式：" + "；".join(i["reason"] for i in issues(probe)))
        target = result
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = new
        records.append(dict(index=len(records), path=path, before=node.get("title"), after=new,
                            body=node.get("body", {k: v for k, v in node.items() if k in ("blocks", "subsections")}),
                            format_only=_plain(node.get("title")) == new))
    return result, records


def verify(client, records, state, save):
    """纯格式等价无需调用；名称发生变化时独立核对原意及正文依据，调用占用后不重发。"""
    semantic = [dict(r, index=i) for i, r in enumerate(r for r in records if not r["format_only"])]
    if not semantic:
        return
    from .writer_protocol import decode
    signature = digest(semantic)
    saved = state.setdefault("title_review", {})
    if saved.get("input_hash", signature) != signature:
        raise ValueError("小标题复核基准已变化")
    saved.setdefault("input_hash", signature)
    if "raw_response" not in saved:
        if saved.get("attempted") or not getattr(client, "available", False):
            raise ValueError("小标题语义复核未完成，不能离线放行或重发原调用")
        saved["attempted"] = True
        save()
        had_single, old_single = hasattr(client, "single_attempt"), getattr(client, "single_attempt", False)
        try:
            client.single_attempt = True
            saved["raw_response"] = client.chat(
                '只审核局部小标题改名，不修正文或科学内容。新标题须保持原标题的材料、数值、单位、条件、'
                '肯定/否定和证据边界，不得新增性能结论；原标题缺失或过长时须以给定body为依据。'
                '无依据或改变原意则supported=false。返回JSON '
                '{"input_hash":"给定哈希","titles":[{"index":0,"supported":true或false,"reason":"具体依据"}]}，每项恰好一次。',
                json.dumps(dict(input_hash=signature, titles=semantic), ensure_ascii=False),
                temperature=0, json_mode=True, trace_tag="writer_title_verify")
            save()
        finally:
            if had_single:
                client.single_attempt = old_single
            else:
                delattr(client, "single_attempt")
    review = decode(saved["raw_response"])
    rows = review.get("titles") if isinstance(review, dict) else None
    if (not isinstance(review, dict) or review.get("input_hash") != signature or not isinstance(rows, list)
            or len(rows) != len(semantic) or any(not isinstance(r, dict) or type(r.get("index")) is not int for r in rows)
            or {r["index"] for r in rows} != set(range(len(semantic)))):
        raise ValueError("小标题复核缺项、重复或基准不匹配")
    rejected = [str(r.get("reason") or "缺少依据") for r in rows
                if r.get("supported") is not True or not isinstance(r.get("reason"), str) or not r["reason"].strip()]
    saved["status"] = "REJECTED" if rejected else "ACCEPTED"
    save()
    if rejected:
        raise ValueError("小标题修改未通过语义复核：" + "；".join(rejected))
