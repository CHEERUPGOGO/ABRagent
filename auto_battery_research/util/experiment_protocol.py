"""实验方案的只读方法视图：分级方法标题、顺序步骤及分组一致性提示。"""
from __future__ import annotations

import copy
import math
import re

from .experiment_titles import paragraph_title
from .report_narrative import amount, substitute_parameters


STYLE_INSTRUCTIONS = '''
实验方案采用专业实验方法格式：材料制备→极片制作→电池装配→化成/电化学测试→相关材料表征→数据判定。
仅保留本课题实际需要的电池类型和表征，不为凑齐模板添加软包、中试或额外仪器。
sections中的每段描述一个实验方法或同一方法的连续操作；step_titles写方法名称。同一方法分段时使用相同主题。
按操作先后写清对象、动作、已确定条件和需记录的数据；长段分为简短操作句，引用及适用限制与操作放在一起。
所有已有配方及数值用参数引用，禁止在{{字段}}后再复制“=数值”；主组和对照组不能共用代表不同取值的引用。
有groups结构时，sections.groups仅写实验目的、平行样和共同条件，分组组成和变量只在groups中声明，正文用组号指代。
化成、活化与循环的倍率/电压/圈数分别说明；容量保持率写明基线，文献结果不能写成本次实测结果。
待确认条件单列于gaps，尚未解决的主方案冲突不得在实验正文中自行决定采用另一套配方。
'''

ASSIGNMENT = re.compile(r"\{\{([A-Za-z0-9_.-]+)\}\}\s*[=＝]\s*([^，。；;、\s]+)")


def assignment_issues(paragraph, bindings):
    """识别参数引用后的另一份赋值；不能让主组引用渲染成“0.03=0”。"""
    issues = []
    for match in ASSIGNMENT.finditer(paragraph):
        row = bindings.get(match[1], {})
        value, literal = row.get("value"), match[2]
        if value is None:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                equal = math.isclose(float(literal), value, rel_tol=1e-9, abs_tol=1e-12)
            except ValueError:
                equal = False
        else:
            equal = str(value).strip() == literal
        if not equal:
            issues.append("参数引用值为" + amount(match[1], row) + "，正文却另写为" + literal)
    return issues


def split_operations(paragraph):
    """只在明确标点/顺序动作边界排版；括号、引用、比例、条件句保持完整。"""
    # 条件或否定限制作用于整句时，不把后半句拆成无条件操作。
    def is_conditional(start):
        end = paragraph.find("。", start)
        return re.search(r"若|如果|仅当|只有|否则|不得|不能|尚未|未定|待确认", paragraph[start:end if end >= 0 else len(paragraph)])
    conditional = is_conditional(0)
    openings = {"（": "）", "(": ")", "[": "]", "{": "}", "“": "”", "‘": "’"}
    closing = set(openings.values())
    stack, parts, start = [], [], 0
    for index, char in enumerate(paragraph):
        if char in openings:
            stack.append(openings[char])
        elif char in closing and stack and stack[-1] == char:
            stack.pop()
        if stack:
            continue
        boundary = char in "\n。" or (char == "；" and not conditional)
        if char == "，" and not conditional:
            # 不拆“在某温度下”“升温速率”等限定，不以逗号普遍切碎参数条件。
            boundary = (not paragraph[start:index].endswith(("后", "时", "前", "下")) and
                        bool(re.match(r"(?:先以|再以|随后|然后|接着|球磨|压片|涂于|冲成|再按|先\{\{|再\{\{)", paragraph[index+1:])))
        if boundary:
            fragment = paragraph[start:index+1].strip()
            if fragment:
                parts.append(fragment)
            start = index+1
            if char in "\n。":
                conditional = is_conditional(start)
    tail = paragraph[start:].strip()
    if tail:
        parts.append(tail)
    # 句末之后的引用仍跟随其原句，不成为独立操作。
    joined = []
    for part in parts:
        if joined and re.fullmatch(r"(?:\[[\w:-]+\]\s*)+[。；]?", part):
            joined[-1] += part
        else:
            joined.append(part)
    return joined


def ordered_steps(parts):
    """采用Word示例的悬挂编号语义，避免加粗标题代替实际步骤。"""
    lines = []
    n = 0
    for part in parts:
        # 来源和限制从操作编号中分出，作为前一步的方法说明保留。
        note = re.search(r"^(?:合成路线依据|配比与混料方式参照|装配流程参照|.*?参数沿用|"
                         r"煅烧制度为工程|封口与静置为工程|该判据是|.*?在证据库中无直接数据)", part)
        if note and lines:
            lines.append("\n" + " " * len(f"{n}. ") + "**依据与说明：** " + part)
            continue
        n += 1
        prefix = f"{n}. "
        body = re.sub(r"^\s*\d+[.、]\s+", "", part)
        if body.endswith(("，", "；")):
            body = body[:-1] + "。"
        lines.append(prefix + body.replace("\n", "\n" + " " * len(prefix)))
    return "\n".join(lines)


def protocol_parameters(bindings):
    """只读换算极片直径的显示单位，电极涂层厚度仍按微米呈现。"""
    result = copy.deepcopy(bindings)
    for key, row in result.items():
        if ("diameter" in key and row.get("unit") in ("um", "μm", "µm")
                and isinstance(row.get("value"), (int, float)) and row["value"] >= 1000):
            row["value"] /= 1000
            row["unit"] = "mm"
    return result


def render_methods(record, key, section_number, bindings, doc=None):
    """连续同主题段落归于一个方法，保留原操作顺序及参数绑定。"""
    methods = []
    for index, paragraph in enumerate(record["sections"].get(key) or [], 1):
        title = paragraph_title(record, key, index, paragraph)
        from .experiment_alignment import expand_profiles
        paragraph = expand_profiles(paragraph, record)
        if re.fullmatch(r"(?:步骤|待确认事项)\d+", title):
            title = "实验操作" if key not in ("criteria", "gaps") else "判定说明" if key == "criteria" else "待确认事项"
        if methods and methods[-1][0] == title:
            methods[-1][1].append(paragraph)
        else:
            methods.append((title, [paragraph]))
    result = []
    display_bindings = protocol_parameters(bindings)
    for index, (title, paragraphs) in enumerate(methods, 1):
        result.append(f"#### {section_number}.{index} {title}")
        from .experiment_values import substitute_pending
        from .experiment_consistency import paragraph_pending
        pending = record.get("consistency", {}).get("pending_fields") and any(
            paragraph_pending(p, key, doc or {}, record["consistency"]) for p in paragraphs)
        if pending or key in ("fabrication", "assembly", "testing") and affected_by_conflict(paragraphs, doc or {}):
            result.append("> **条件待确认。** 本方法涉及已披露的方案冲突，以下为归档建议；先统一配方，再执行相关配料和装配步骤。")
        units = []
        for paragraph in paragraphs:
            issues = assignment_issues(paragraph, bindings)
            if issues:
                units.append("本项条件待确认：" + "；".join(issues) + "。先核对该样品的独立参数，原建议保留在实验方案档案中。")
                continue
            units.extend(substitute_parameters(substitute_pending(p.replace("}}{{", "}} {{"), record), display_bindings) for p in split_operations(paragraph))
        result.append(ordered_steps(units))
    return "\n\n".join(result) or "本节安排待补充。"


def render_group_notes(record, bindings):
    """有结构化分组时只由组表给组成，旧分组段落不能重复改写主组引用。"""
    from .research_reporting import cell
    registered = {g["id"] for g in record.get("groups", [])}
    group_pattern = "(?:" + "|".join([re.escape(g) for g in sorted(registered, key=len, reverse=True)] + [r"G\d+"]) + ")"
    mentions = re.compile(r"(?<![A-Za-z0-9_])" + group_pattern + r"(?![A-Za-z0-9_])")
    declared = re.compile(r"^\s*(?:\*\*)?" + group_pattern + r"(?![A-Za-z0-9_])")
    notes, pending = [], []
    for paragraph in record["sections"].get("groups") or []:
        from .experiment_alignment import expand_profiles
        paragraph = expand_profiles(paragraph, record)
        identifiers = set(mentions.findall(paragraph))
        if identifiers:
            unknown = sorted(identifiers - registered)
            if unknown:
                # 只摘录原有分组名，不展示未经登记的另一套组成。
                label = paragraph.split("：", 1)[0].split(":", 1)[0].strip()
                if len(label) > 60 or "{{" in label:
                    label = "、".join(unknown)
                pending.append(label)
            if declared.match(paragraph):
                continue
        issues = assignment_issues(paragraph, bindings)
        if issues:
            notes.append("分组条件待确认：" + "；".join(issues) + "。")
        else:
            from .experiment_values import substitute_pending
            notes.append(substitute_parameters(substitute_pending(paragraph, record), bindings))
    if pending:
        notes.append("**待登记对照。** 下列名称来自原实验草稿，具体组成和变量尚未通过分组登记；补齐后再投料，不能把主组参数直接用于这些对照。")
        table = ["| 对照名称 | 当前状态 |", "|---|---|"]
        table += [f"| {cell(label)} | 组成与独立参数待确认 |" for label in dict.fromkeys(pending)]
        notes.append("\n".join(table))
    return "\n\n".join(notes)


def _conflicts(doc):
    from .report_readiness import unresolved_issues
    # 论证的表述审核保留在第二部分及附录；操作冲突由主方案及实验快照提供。
    return [issue for issue in unresolved_issues(doc) if not issue.startswith(("实验交付限制：", "当前论证："))
            and any(w in issue for w in ("冲突", "错绑", "固定条件不一致"))]


def affected_by_conflict(paragraphs, doc):
    """只按审核已指明的方案字段标注相关方法，不在渲染阶段推断新冲突。"""
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    for issue in _conflicts(doc):
        fields = re.findall(r"[a-z_]+(?:\.[A-Za-z0-9_]+)+", issue)
        for field in fields:
            if field in params and any("{{" + field.partition(".")[0] + "." in p for p in paragraphs):
                return True
    return False


def consistency_notice(doc, snapshot=None):
    """把已有方案冲突移到操作前提示，不在排版时擅改科学方案。"""
    conflicts = _conflicts(doc)
    if snapshot:
        conflicts += [issue["path"] + "：" + issue["reason"] for issue in snapshot.get("issues", []) if issue.get("source") != "review"]
    if not conflicts:
        return ""
    from .report_narrative import GROUPS, label
    params = (doc.get("ledger_view") or {}).get("parameters", {})
    def readable(issue):
        def replace(match):
            key = match[0]
            if key not in params:
                return key
            name = label(key, params[key])
            group = GROUPS.get(key.partition(".")[0], "")
            return name if name.startswith(group) else group + name
        return re.sub(r"[a-z_]+(?:\.[A-Za-z0-9_]+)+", replace, issue).replace("\n", " ")
    return "> **实施前核对。** 下列方案冲突尚未解决，相关配料与装配条件须先定案；本节保留的步骤为待核对实验建议。\n>\n" + "\n".join(
        "> - " + readable(issue) for issue in conflicts)
