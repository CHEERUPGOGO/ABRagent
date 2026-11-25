"""独立只读终审、持久调用预算与内容树受限修复。"""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager

from .scheme_ledger import digest
from .stage4_document import decode_document, compile_document, TOKEN
from .runtime_budgets import checkpoint_repair_options

VERSION = 1
DIMENSIONS = ("user_conditions", "identities", "quantities", "cross_sections", "citations", "limitations")


@contextmanager
def single_attempt(client):
    """禁用客户端隐藏完整重试，所有机会由编排层占用。"""
    prior = getattr(client, "single_attempt", None)
    client.single_attempt = True
    try:
        yield
    finally:
        if prior is None:
            delattr(client, "single_attempt")
        else:
            client.single_attempt = prior


def request(state, role, signature, save, invoke):
    """先持久化占用，再请求；中断、失败和拒绝不退还或重发。"""
    records = state.setdefault("requests", [])
    existing = next((r for r in records if r["role"] == role and r["signature"] == signature), None)
    if existing:
        if existing.get("status") == "returned":
            return copy.deepcopy(existing["response"])
        raise ValueError(role + "请求已占用，不能自动重发：" + existing["status"])
    used = sum(r["role"] == role for r in records)
    if used >= checkpoint_repair_options(state)["v3_requests"][role]:
        raise ValueError(role + "调用预算已耗尽")
    row = {"role": role, "signature": signature, "attempt": used + 1, "status": "requested"}
    records.append(row)
    save()
    try:
        value = invoke()
        row.update(status="returned", response=copy.deepcopy(value))
        save()
        return value
    except Exception as exc:
        row.update(status="failed", error=str(exc))
        if hasattr(exc, "raw_response"):
            row.update(raw_response=exc.raw_response, response_meta=copy.deepcopy(getattr(exc, "response_meta", {})))
        save()
        raise


def available(state, role):
    return max(0, checkpoint_repair_options(state)["v3_requests"][role]
               - sum(r.get("role") == role for r in state.get("requests", [])))


def consistency_prompt(doc, compiled, differences=None):
    return ("独立只读一致性终审。不得改正文、参数、研究记录或覆盖科学否决。程序仅保证表达和数据关系，"
        "不证明选材、机理和预测正确。逐项检查六维：用户条件及解释、主方案/候选/对照身份、"
        "参数/工况/量纲/分母/派生量、跨节声明及关联、引用归属/证据适用、结论强度及限制。"
        "未绑定数值须明确核对对象，不能把倍率参考电流当比容量、以厚度证明N/P或从浓度猜LHCE。"
        "返回一个JSON对象{version:1,dimensions:{六维id:passed|issues|unreviewed},issues:["
        "{dimension,leaf_id,quote,reason,suggestion,parameter_paths:[]}]}；"
        "quote须为指定叶的源文本唯一逐字片段。不可定位的问题可保留leaf_id=null，不获得写权限。"
        "六维id为" + json.dumps(DIMENSIONS) + "。没有科学证据也不得宣称性能已验证。\n"
        + json.dumps({"original_requirements": doc.get("requirements"), "design_request": doc.get("design_request", doc.get("target")),
            "full_markdown": compiled.markdown, "leaves": compiled.leaves,
            "parameters": (doc.get("ledger_view") or {}).get("parameters", doc.get("parameters", {})),
            "research": compiled.research, "precheck": compiled.diagnostics,
            "scientific_review": doc.get("reviewer_output"), "evidence": doc.get("evidence"), "differences": differences}, ensure_ascii=False))


def parse_consistency(raw, signature):
    data = decode_document(raw)
    if (set(data) != {"version", "dimensions", "issues"} or type(data["version"]) is not int or data["version"] != VERSION
            or not isinstance(data["dimensions"], dict) or set(data["dimensions"]) != set(DIMENSIONS)
            or any(v not in ("passed", "issues", "unreviewed") for v in data["dimensions"].values())
            or not isinstance(data["issues"], list)):
        raise ValueError("终审响应须完整返回六维状态与问题清单")
    for issue in data["issues"]:
        if (not isinstance(issue, dict) or set(issue) - {"dimension", "leaf_id", "quote", "reason", "suggestion", "parameter_paths", "requirement_id", "section_id"}
                or issue.get("dimension") not in DIMENSIONS or not isinstance(issue.get("reason"), str) or not issue["reason"].strip()
                or not isinstance(issue.get("suggestion"), str) or not isinstance(issue.get("parameter_paths", []), list)):
            raise ValueError("终审问题结构不完整")
    if data["issues"] and all(v == "passed" for v in data["dimensions"].values()):
        raise ValueError("终审问题与维度状态矛盾")
    return dict(data, input_fingerprint=signature, status="passed" if not data["issues"] and all(v == "passed" for v in data["dimensions"].values()) else "issues", raw_response=raw)


def audit_consistency(client, state, doc, compiled, save, *, stage5=False, differences=None):
    signature = compiled.render_fingerprint
    role = "stage5_consistency" if stage5 else "consistency"
    def invoke():
        if not client or not client.available:
            raise ValueError("LLM不可用，不能授予一致性通过")
        with single_attempt(client):
            raw = client.chat("你是独立只读一致性审核员。", consistency_prompt(doc, compiled, differences), temperature=.1, json_mode=True, trace_tag=role)
        return {"raw_response": raw, "response_meta": copy.deepcopy(getattr(client, "last_response", {}))}
    try:
        output = request(state, role, signature, save, invoke)
        try:
            return parse_consistency(output["raw_response"], signature)
        except ValueError:
            if stage5:
                raise
            # 每稿只有一次协议重审，原稿不变；总共至多两稿。
            output = request(state, "consistency_retry", signature, save, invoke)
            return parse_consistency(output["raw_response"], signature)
    except Exception as exc:
        return {"version": VERSION, "status": "unreviewed", "input_fingerprint": signature,
                "dimensions": {d: "unreviewed" for d in DIMENSIONS}, "issues": [], "error": str(exc)}


def authorize(content, parameters, compiled, issues, requirements):
    """将审核原句映射到稳定叶；无精确位置的问题不扩大权限。"""
    permits = []
    known = {i["id"] for i in (requirements or {}).get("items", [])}
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        ids = [issue.get("leaf_id")] if issue.get("leaf_id") else []
        quote = issue.get("quote")
        if not ids and isinstance(quote, str) and quote:
            ids = [k for k, v in compiled.leaves.items() if v["source_text"].count(quote) == 1 or v["rendered_text"].count(quote) == 1]
        if len(ids) != 1 or ids[0] not in compiled.leaves:
            # 只有已核验遗漏要求、指定章节和明确来源清单才能授权追加。
            rid, sid = issue.get("requirement_id"), issue.get("section_id")
            if rid in known and sid in {s["id"] for s in compiled.sections} and issue.get("missing_requirement") is True:
                permits.append({"id": "append_" + digest(issue)[:12], "operation": "append", "section_id": sid,
                    "requirement_id": rid, "required_citations": issue.get("required_citations", []), "issue": copy.deepcopy(issue)})
            continue
        item = compiled.leaves[ids[0]]
        source = item["source_text"]
        if not isinstance(quote, str) or not quote or source.count(quote) != 1:
            # 渲染原句仅允许完整叶的等值定位，不能猜测半句与引用的边界。
            if quote != item["rendered_text"]:
                continue
            quote = source
        paths = [p for p in issue.get("parameter_paths", []) if p in parameters and not parameters[p].get("locked")]
        # 来源/引用身份不改变，但同参数的全部依赖须纳入联合接口权限。
        dependent_ids = {k for k, deps in compiled.dependencies.items() if set(paths) & set(deps["parameters"])}
        permits.append({"id": "leaf_" + digest({"leaf": item["id"], "quote": quote})[:12], "operation": "replace",
            "leaf_id": item["id"], "quote": quote, "parameter_paths": paths, "dependent_leaf_ids": sorted(dependent_ids), "issue": copy.deepcopy(issue)})
    unique = {}
    for permit in permits:
        if permit["id"] not in unique:
            unique[permit["id"]] = permit
        else:
            old = unique[permit["id"]]
            old["parameter_paths"] = sorted(set(old.get("parameter_paths", [])) | set(permit.get("parameter_paths", [])))
            old["dependent_leaf_ids"] = sorted(set(old.get("dependent_leaf_ids", [])) | set(permit.get("dependent_leaf_ids", [])))
            old.setdefault("related_issues", []).append(permit["issue"])
    permits = list(unique.values())
    # 同叶重叠原句或同参数问题必须按同一依赖组提交。
    groups = []
    for permit in permits:
        dependencies = {"leaf:" + str(permit.get("leaf_id"))} | {"param:" + p for p in permit.get("parameter_paths", [])}
        overlaps = [g for g in groups if g["dependencies"] & dependencies]
        if overlaps:
            current = overlaps[0]
            current["dependencies"] |= dependencies
            current["ids"].append(permit["id"])
            for other in overlaps[1:]:
                current["dependencies"] |= other["dependencies"]
                current["ids"] += other["ids"]
                groups.remove(other)
        else:
            groups.append({"dependencies": dependencies, "ids": [permit["id"]]})
    return {"base_hash": digest({"content": content, "parameters": parameters}), "interfaces": permits,
            "transaction_groups": [g["ids"] for g in groups]}


def _at(content, path):
    value = content
    for key in path:
        value = value[key]
    return value


def apply_patch(content, parameters, compiled, permit, patch, requirements, evidence):
    """独立事务可部分成功；合并后全量编译失败撤销全部修改。"""
    if (not isinstance(patch, dict) or set(patch) != {"base_hash", "transactions"}
            or patch["base_hash"] != permit["base_hash"] or permit["base_hash"] != digest({"content": content, "parameters": parameters})
            or not isinstance(patch["transactions"], list)):
        raise ValueError("补丁基准失效或形状非法")
    allowed = {p["id"]: p for p in permit["interfaces"]}
    accepted_content, accepted_parameters = copy.deepcopy(content), copy.deepcopy(parameters)
    log, touched = [], set()
    for transaction in patch["transactions"]:
        next_content, next_parameters = copy.deepcopy(accepted_content), copy.deepcopy(accepted_parameters)
        try:
            if not isinstance(transaction, dict) or set(transaction) != {"interface_ids", "replacements", "parameters", "additions"}:
                raise ValueError("事务只能包含interface_ids/replacements/parameters/additions")
            ids = transaction["interface_ids"]
            if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids) or any(k not in allowed for k in ids):
                raise ValueError("接口ID未授权或重复")
            if any(set(ids) & set(group) and not set(group).issubset(ids) for group in permit.get("transaction_groups", [])):
                raise ValueError("同段或同参数的问题必须在同一事务完整解决")
            grants = [allowed[k] for k in ids]
            changed_leaves, changed_parameters = set(), set()
            if not isinstance(transaction["replacements"], list) or not isinstance(transaction["parameters"], dict) or not isinstance(transaction["additions"], list):
                raise ValueError("事务字段形状非法")
            for replacement in transaction["replacements"]:
                if not isinstance(replacement, dict) or set(replacement) != {"interface_id", "text"}:
                    raise ValueError("替换只能指定已授权接口和文本")
                grant = allowed.get(replacement["interface_id"])
                if not grant or grant not in grants or grant["operation"] != "replace":
                    raise ValueError("叶节点替换越权")
                item = compiled.leaves[grant["leaf_id"]]
                source = _at(next_content, item["source_location"])
                text = replacement["text"]
                if not isinstance(text, str) or not text.strip() or source.count(grant["quote"]) != 1:
                    raise ValueError("替换片段失效或删除有效正文")
                before = TOKEN.findall(grant["quote"])
                if before != TOKEN.findall(text):
                    raise ValueError("不能更改引用身份、数量或顺序")
                parent = _at(next_content, item["source_location"][:-1])
                parent[item["source_location"][-1]] = source.replace(grant["quote"], text, 1)
                # 程序同步明确引用的被修片段，不让模型重写关联身份或独立摘要。
                from .stage4_research_projection import FIELDS
                for collection, names in FIELDS.items():
                    for row in next_content["research_links"].get(collection, []):
                        for name in names:
                            for ref in row.get(name, []):
                                quoted = ref.get("quote")
                                if ref.get("leaf_id") != item["id"] or quoted is None:
                                    continue
                                if quoted == grant["quote"]:
                                    ref["quote"] = text
                                elif grant["quote"] in quoted:
                                    ref["quote"] = quoted.replace(grant["quote"], text, 1)
                                elif quoted in grant["quote"] and quoted not in text:
                                    raise ValueError("补丁删除关联片段，不能猜测新的quote边界")
                changed_leaves.add(item["id"])
            paths = {p for g in grants for p in g.get("parameter_paths", [])}
            from .scheme_ledger import validate_field
            for key, value in transaction["parameters"].items():
                if key not in paths or next_parameters[key].get("locked"):
                    raise ValueError("参数修改越权或用户锁定")
                if next_parameters[key].get("basis") in ("user_requirement", "model_reference"):
                    raise ValueError("不能把用户条件或模型参考身份改写为工程提案")
                if not isinstance(value, dict) or set(value) - {"value", "unit", "meaning"}:
                    raise ValueError("不能修改参数来源身份和工况")
                row = dict(next_parameters[key], **value)
                validate_field(key, row)
                next_parameters[key] = row
                changed_parameters.add(key)
            for addition in transaction["additions"]:
                if not isinstance(addition, dict) or set(addition) != {"interface_id", "block", "answer"}:
                    raise ValueError("追加必须绑定遗漏要求的指定接口")
                grant = allowed.get(addition["interface_id"])
                if not grant or grant not in grants or grant["operation"] != "append":
                    raise ValueError("追加内容越权")
                answer = addition["answer"]
                block = addition["block"]
                if (not isinstance(answer, dict) or answer.get("requirement_id") != grant["requirement_id"]
                        or not isinstance(block, dict) or block.get("type") != "paragraph" or len(block.get("text", "")) > 2400):
                    raise ValueError("追加须为受限段落及指定要求关联")
                if not set(grant["required_citations"]).issubset({r[1] for r in TOKEN.findall(block["text"]) if r[0] == "cite"}):
                    raise ValueError("追加内容缺少授权要求所需来源")
                section = next(s for s in compiled.sections if s["id"] == grant["section_id"])
                # 主节及子节均使用已编译节点路径，不能另建标题。
                def find(nodes, key):
                    for sid, node in nodes.items():
                        if sid == key:
                            return node
                        stack = list(node.get("subsections", []))
                        while stack:
                            child = stack.pop()
                            if child["id"] == key:
                                return child
                            stack += child.get("subsections", [])
                find(next_content["sections"], section["id"]).setdefault("blocks", []).append(block)
                answers = next_content["research_links"].setdefault("answers", [])
                if any(a["requirement_id"] == grant["requirement_id"] for a in answers):
                    raise ValueError("不能覆盖既有有效研究关联")
                answers.append(answer)
                changed_leaves.add(block.get("id"))
            impact = changed_leaves | {"param:" + p for p in changed_parameters}
            if impact & touched:
                raise ValueError("同段或同参数的依赖问题须在一个事务联动修改")
            # 同参数实际引用的其他叶必须一同授权，但引用显示不由模型改写。
            for p in changed_parameters:
                dependent = {k for k, d in compiled.dependencies.items() if p in d["parameters"]}
                granted_leaves = {g.get("leaf_id") for g in grants} | {k for g in grants for k in g.get("dependent_leaf_ids", [])}
                if not dependent.issubset(granted_leaves):
                    raise ValueError("参数的全部正文依赖未获联合授权")
            tested = compile_document(next_content, next_parameters, requirements, evidence)
            old_errors = {(d["code"], str(d["source_location"]), d["reason"]) for d in compiled.diagnostics if d["severity"] == "error"}
            new_errors = {(d["code"], str(d["source_location"]), d["reason"]) for d in tested.diagnostics if d["severity"] == "error"}
            if new_errors - old_errors:
                raise ValueError("事务引入新的全量校验错误")
            accepted_content, accepted_parameters = next_content, next_parameters
            touched |= impact
            log.append({"status": "APPLIED", "interface_ids": ids, "after_hash": digest({"content": next_content, "parameters": next_parameters})})
        except (ValueError, TypeError, KeyError, StopIteration) as exc:
            log.append({"status": "REJECTED", "transaction": copy.deepcopy(transaction), "reason": str(exc)})
    tested = compile_document(accepted_content, accepted_parameters, requirements, evidence)
    if not tested.valid:
        log.append({"status": "ROLLED_BACK", "reason": "合并后全量校验未通过"})
        return copy.deepcopy(content), copy.deepcopy(parameters), log
    return accepted_content, accepted_parameters, log
