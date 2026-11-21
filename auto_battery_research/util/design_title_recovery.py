"""用归档原稿和已接受补丁证明旧标题映射，不猜测丢失的编号或替换科研正文。"""
import re

from .design_sections import REQUIRED_SECTIONS
from .design_structure import SECTION_IDS, SECTION_TITLES, START, END, headings, structure_issues
from .scheme_ledger import digest


def _old_normalize(text):
    """仅重现旧错误用于核对历史哈希，不能用于新稿或当前正文。"""
    def replace(match):
        hits = [name for name, aliases in REQUIRED_SECTIONS if any(a.lower() in match[2].lower() for a in aliases)]
        return f"## {hits[0]}" if len(hits) == 1 else match[0]
    return re.sub(r"^(#{1,2})\s+([^\n]+)$", replace, text, flags=re.M)


def restore_titles(seed):
    """只替换已证明对应的标题行；已接受正文补丁与其余字节保持不变。"""
    from .design_repair import blocks
    draft = seed.get("draft")
    archived = (seed.get("writer_output") or {}).get("draft_answer")
    if not isinstance(draft, str) or not isinstance(archived, str) or not archived.strip():
        raise ValueError("缺少当前草稿或归档Writer原稿")
    if seed.get("design_structure") or START in draft:
        raise ValueError("当前稿件已有程序章节契约，无需旧标题恢复")
    replay = _old_normalize(archived)
    for entry in seed.get("repair_audit", []):
        if entry.get("status") != "APPLIED":
            continue
        patch = entry.get("patch") or {}
        if entry.get("before") != digest(replay) or patch.get("base_hash") != digest(replay):
            raise ValueError("历史补丁基准无法与归档Writer原稿核对")
        if patch.get("append_sections"):
            raise ValueError("历史补丁改变了章节集合，不能自动恢复标题")
        parts, units = blocks(replay)
        used = set()
        for edit in patch.get("edits", []):
            key, value = edit.get("block_id"), edit.get("text")
            if key in used or key not in units or not isinstance(value, str):
                raise ValueError("历史补丁包含非法段落")
            if [h[1:] for h in headings(value)] != [h[1:] for h in headings(units[key])]:
                raise ValueError("历史补丁改变了标题，需人工确认对应关系")
            parts[int(key[1:])] = value
            used.add(key)
        replay = "".join(parts)
        if entry.get("after") != digest(replay):
            raise ValueError("历史补丁结果哈希不一致")
    if replay != draft:
        raise ValueError("当前正文不是归档原稿加已接受补丁，拒绝按位置猜测恢复")
    original_headings, current_headings = headings(archived), headings(draft)
    if len(original_headings) != len(current_headings) or not original_headings:
        raise ValueError("归档与当前标题数量不一致")
    root_level = min(h[2] for h in original_headings)
    tops = [h for h in original_headings if h[2] == root_level]
    if len(tops) != 5 or archived[:original_headings[0][0]].strip():
        raise ValueError("归档主章节边界不明确，不能恢复固定五节")
    replacements, manifest, top = [], [], 0
    for old, current in zip(original_headings, current_headings):
        _, old_line, level, title = old
        pos, line, _, _ = current
        if _old_normalize(old_line) != line:
            raise ValueError("标题不是旧规范化的确定结果")
        new_level = level - root_level + 2
        if level == root_level:
            top += 1
            key, number = SECTION_IDS[top - 1], str(top)
            hits = [name for name, aliases in REQUIRED_SECTIONS if any(a.lower() in title.lower() for a in aliases)]
            if hits != [SECTION_TITLES[key]]:
                raise ValueError("归档主章节语义与固定五节顺序不一致")
            title = SECTION_TITLES[key]
            heading = f"## {number}. {title}"
        else:
            match = re.fullmatch(r"(\d+(?:\.\d+)+)\s+(.+)", title)
            if not match or match[1].split(".")[0] != str(top):
                raise ValueError("归档子节没有唯一且属于当前父节的编号")
            number, title = match.groups()
            key = "legacy-" + number.replace(".", "-")
            heading = "#" * new_level + f" {number} {title}"
        manifest.append({"id": key, "number": number, "title": title, "level": new_level})
        replacements.append({"start": pos, "end": pos + len(line), "before": line,
                             "after": f'<a id="design-{key}"></a>\n\n' + heading})
    restored = draft
    for item in reversed(replacements):
        restored = restored[:item["start"]] + item["after"] + restored[item["end"]:]
    restored = START + "\n\n" + restored + "\n\n" + END
    contract = {"version": 1, "headings": manifest}
    errors = structure_issues(restored, contract)
    if errors:
        raise ValueError("恢复标题后结构仍无效：" + "；".join(errors))
    return restored, contract, {"kind": "title_recovery", "status": "APPLIED", "before": digest(draft),
        "after": digest(restored), "writer_hash": digest(archived), "headings": replacements,
        "body_preserved": True, "reason": "归档标题和完整成功补丁链已核对，仅恢复标题层级与编号"}
