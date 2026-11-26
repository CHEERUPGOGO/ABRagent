"""既有程序化实验产物的审核兼容读取，保留指纹和原判定。"""
from __future__ import annotations

import copy
import json

from .scheme_ledger import digest
from .experiment_slots import strict_json

CHECKS = ("fixed_conditions", "physical_consistency", "methods_and_controls", "requirement_coverage",
          "evidence_and_inference", "cross_section_consistency")


def subject(record, doc):
    """只绑定科研内容和当前输入，排除时间、调用计数及审核自身，避免循环指纹。"""
    from .experiment_design import effective_data
    from .research_experiments import required_tests
    content = {key: record.get(key) for key in ("parameters", "sections", "step_titles", "groups", "requirement_tests", "extensions",
               "citations", "pending_parameters", "pending_sections", "pending_groups", "pending_tests", "generation_pending", "route")}
    current = {key: {k: row.get(k) for k in ("value", "unit", "locked", "conditions", "scope", "purpose", "canonical_path",
                                          "basis", "source", "source_quote")}
               for key, row in effective_data(doc)["parameters"].items()}
    inputs = record.get("generation_inputs") or {}
    archived = {e.get("passage_id"): e for e in (doc.get("evidence") or []) + (doc.get("experiment_evidence") or [])}
    evidence = []
    for original in inputs.get("evidence", []):
        entry = archived.get(original.get("passage_id"))
        # 检索片段可以是原文的前缀，不能用同ID的另一段内容继承审核。
        if entry is None or not str(entry.get("text") or "").startswith(str(original.get("text") or "")):
            evidence.append({"passage_id": original.get("passage_id"), "stale": True})
        else:
            evidence.append(original)
    return {"content": content, "current_parameters": current, "target": doc.get("target"),
            "requirements": doc.get("requirements"), "required_tests": required_tests(doc), "evidence": evidence,
            "stage4_review": {k: doc.get(k) for k in ("review_completed", "review_passed", "reviewer_output")},
            "physics": doc.get("experiment_physics_context", doc.get("physics_context", inputs.get("physics", {})))}


def unit_ids(record):
    return (["parameters", "groups", "route", "extensions"]
            + ["sections." + key for key in record.get("sections", {})]
            + ["requirement_tests." + row["requirement_id"] for row in record.get("requirement_tests", [])])


def validate_reply(raw, expected_hash, record):
    value = strict_json(raw)
    if not isinstance(value, dict) or set(value) != {"base_hash", "checks", "requirement_ids", "issues"} or value["base_hash"] != expected_hash:
        raise ValueError("实验审核结构或基准指纹不一致")
    checks = value["checks"]
    if (not isinstance(checks, list) or len(checks) != len(CHECKS)
            or any(not isinstance(c, dict) or set(c) != {"id", "status", "reason"} for c in checks)
            or any(not isinstance(c["id"], str) for c in checks)
            or {c["id"] for c in checks} != set(CHECKS)):
        raise ValueError("实验审核未完整覆盖全部检查维度")
    for check in checks:
        if check["status"] not in ("supported", "needs_revision") or not isinstance(check["reason"], str) or not check["reason"].strip():
            raise ValueError("实验审核逐项状态或理由非法")
    expected = [row["requirement_id"] for row in record.get("requirement_tests", [])]
    covered = value["requirement_ids"]
    if not isinstance(covered, list) or any(not isinstance(v, str) for v in covered) or len(covered) != len(expected) or set(covered) != set(expected):
        raise ValueError("实验审核遗漏、重复或新增了研究目标")
    issues, units = value["issues"], set(unit_ids(record))
    if not isinstance(issues, list):
        raise ValueError("实验审核issues须为数组")
    for issue in issues:
        if (not isinstance(issue, dict) or set(issue) != {"check_id", "unit_id", "reason", "suggestion"}
                or any(not isinstance(v, str) or not v.strip() for v in issue.values())
                or issue["check_id"] not in CHECKS or issue["unit_id"] not in units):
            raise ValueError("实验审核问题须定位已存在的内容单元并给出原因和建议")
    failing = {c["id"] for c in checks if c["status"] == "needs_revision"}
    if failing != {i["check_id"] for i in issues}:
        raise ValueError("实验审核逐项结论与问题清单矛盾")
    return value




def status(record, doc=None):
    """旧产物不补授新审核；新产物的缺审、科学否决和输入变动均如实披露。"""
    if record.get("generation_contract") != 1:
        return "LEGACY", []
    review = record.get("scientific_review") or {}
    if doc is not None and review.get("subject_hash") != digest(subject(record, doc)):
        return "STALE", ["实验方案或输入已变化，原科学审核不再适用，须重新核对"]
    if review.get("status") in ("PASSED", "NEEDS_REVISION"):
        try:
            reply = {k: review.get(k) for k in ("checks", "requirement_ids", "issues")}
            reply["base_hash"] = review.get("subject_hash")
            validate_reply(json.dumps(reply, ensure_ascii=False), review.get("subject_hash"), record)
        except (ValueError, TypeError, KeyError):
            return "UNREVIEWED", ["实验科学审核记录不完整或结构非法，不能认定已通过"]
    if review.get("status") == "PASSED" and not review.get("issues"):
        return "PASSED", []
    if review.get("status") == "NEEDS_REVISION" and review.get("issues"):
        reasons = ["实验科学审核尚有未解决意见"]
        reasons.extend(i["reason"] for i in review.get("issues", []))
        return "NEEDS_REVISION", reasons
    return "UNREVIEWED", ["实验方案尚未完成有效的独立科学审核"]


def render_notice(record, doc):
    if record.get("generation_contract") != 1:
        return ""
    outcome, reasons = status(record, doc)
    if outcome == "PASSED":
        return "**实验科学审核：** 本版建议已完成独立全文审核；方法安排不代表条件齐全、实验已执行或性能已验证。"
    text = "**实验科学审核：** " + "；".join(reasons) + "。"
    if outcome == "NEEDS_REVISION":
        text += "\n\n" + "\n".join(
            f"- {i['unit_id']}：{i['reason']}；建议：{i['suggestion']}" for i in record["scientific_review"]["issues"])
    return text
