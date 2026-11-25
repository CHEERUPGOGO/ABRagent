"""六节研报共享当前记录的摘要与附录；不把旧研究回答当作最新执行安排。"""
from __future__ import annotations


def summary(doc):
    from .research_reporting import selected_summary
    record = doc["_report_record"]
    view = record["view"]
    boundary = "材料论证、方案参数与实验安排来自同一当前记录；设计建议及文献推断不代表性能已经验证。"
    narrative = view.get("narrative")
    if narrative and narrative.get("status") != "approved":
        boundary += "当前材料论证仍有待复核内容，见第2节对应段落。"
    if (doc.get("requirements") or {}).get("status") != "parsed":
        boundary += "课题要求仍有明确待核对项。"
    return "**（1）主要结论。** " + selected_summary(doc) + "\n\n**（2）证据边界。** " + boundary + "\n\n**（3）下一步。** 按第5节当前实验计划推进；具体条件、判据与尚待确定项以本版记录为准。"


def coverage(doc):
    from .research_reporting import cell, group_title
    rows = doc["_report_record"]["view"]["coverage"]
    lines = ["### 当前课题要求覆盖", "以下逐项对应本版第2节论证、第3节方案和第5节实验；历史提案不作为当前安排。",
             "| 课题要求 | 当前回应与位置 | 当前实验状态 |", "|---|---|---|"]
    for row in rows:
        item = row["requirement"]
        refs = list(dict.fromkeys(p["location"] for p in row["paragraphs"]))
        fixed = row.get("fixed_response")
        answer = fixed + "；第3节" if fixed else "论证见" + "、".join(refs) if refs else "当前论证未明确覆盖，需补充"
        test = row.get("experiment")
        if test:
            state = {"planned": "已有方法安排", "gap": "已有方法及待补项", "not_applicable": "本轮不适用"}.get(test.get("status"), "状态待确认")
            if test.get("status") == "gap" and test.get("gap_kind") == "validation":
                from .experiment_delivery import validation_gap_errors
                if not validation_gap_errors(test, doc["_report_record"]["experiment"], doc):
                    state = "推荐已给出，待实验验证"
            state += "；第5节"
        else:
            state = "固定条件一致" if fixed else "未登记本轮测试安排"
        category = group_title(doc["requirements"], item)
        lines.append("| " + cell((category + "／" if category else "") + item["description"]) + " | " + cell(answer) + " | " + cell(state) + " |")
    return "\n\n".join(lines[:2]) + "\n\n" + "\n".join(lines[2:])


def appendix(doc):
    from .research_reporting import cell
    from .parameter_presentation import render_parameter_appendix
    from .numeric_consistency import document_audit, render_audit
    from .adaptive_retrieval import render_audit as retrieval_audit
    from .research_experiments import render_groups
    record = doc["_report_record"]
    view, recipe = record["view"], record["experiment"]
    parts = [coverage(doc)]
    if view.get("narrative"):
        # 当前排序和取舍只复用本版论证，避免旧候选摘要重新引入不同强度的科学陈述。
        parts += ["### 当前候选与论证索引", "候选排序、选择理由与适用边界见本版第2节；以下位置直接对应当前论证：",
                  "\n".join(f"- 第2.{i}节：{section['title']}" for i, section in enumerate(view["narrative"]["sections"], 1))]
    if recipe.get("groups"):
        parts += ["### 当前实验分组索引", "与第五部分使用同一分组记录，未在此登记的措施不视为本轮已安排。", render_groups(recipe, doc)]
    parts.append("### 当前限制与待核对项")
    parts.append("\n".join(f"{i}. {s}" for i, s in enumerate(view["active_issues"], 1)) or "当前记录未检出待核对项；这不表示实验性能已经验证。")
    from .experiment_delivery import assessment
    validation = assessment(recipe, doc)["validation_notes"]
    if validation:
        parts += ["### 后续验证事项（不阻断本轮交付）",
                  "\n".join(f"- {row['reason']} 后续：{row['next_step']}" for row in validation)]
    rules = doc.get("rule_checks") or {}
    recommendations = rules.get("recommendations") or rules.get("rule_checks", {}).get("recommendations", [])
    if recommendations:
        parts += ["### 当前规则建议（非阻断）",
                  "以下建议用于提示风险和可选措施，未采纳本身不否决方案，也不表示措施已实施或性能已验证。"]
        parts.append("\n".join(f"- **{row['rule_id']}**：{row['reason']}" for row in recommendations))
    if doc.get("writer_contract_version") == 3:
        from .stage4_reporting import audit, details
        parts += [details(doc, "### 当前研究关联"), audit(doc)]
    narrative = view.get("narrative")
    if narrative:
        parts += ["### 当前论证审核", "表述审核状态：" + ("通过" if narrative["status"] == "approved" else "仍有待复核项") + "。该状态只说明论证与所用依据的关系，不替代科学和实验验证。"]
        notes = narrative.get("review", {}).get("notes") or []
        if notes:
            parts += ["逐段审阅的可选建议保留在当前记录的审核档案中；其中针对来源稿的实验建议不作为本版待办，实际安排见第五部分。"]
    if doc.get("ledger_view"):
        parts += [render_parameter_appendix(doc["ledger_view"])]
    if doc.get("numeric_contract"):
        parts.append(render_audit(document_audit(doc)))
    if doc.get("retrieval_state"):
        parts.append(retrieval_audit(doc))
    parts += ["### 历史资料与本版依据", "本版使用同一份[当前报告记录](report_record.json)。原Stage 4回答、原稿和历次审核保留于其中的来源档案；它们记录当时的提案与问题，不作为本版当前安排。科学保留意见未因重新编排而获批准。",
              "[Stage 4原设计](design_scheme.md) · [当前实验计划](experiment_plan.json) · [统一方案表](scheme_ledger.json)"]
    return "\n\n".join(p for p in parts if p)
