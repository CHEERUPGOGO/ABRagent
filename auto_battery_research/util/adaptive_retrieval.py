"""Stage 4有限证据改善：首次整体偏差才能重检索，检索不授予方案写权限。"""
from __future__ import annotations

import copy
import json
import math
import re
import unicodedata

from .scheme_ledger import digest
from .runtime_budgets import budget_options, checkpoint_repair_options

VERSION = 1
DEFAULTS = budget_options("stage4_retrieval")
DEFECTS = {"wrong_subject", "wrong_source", "systematic_filter"}
NON_RETRIEVAL = {"format", "protocol", "calculation", "binding", "user_decision", "measurement", "misreading"}
SUPPLEMENT_CAUSES = {"evidence_gap", "missing_conditions", "source_gap", "conflicting_evidence"}
CAUSES = NON_RETRIEVAL | SUPPLEMENT_CAUSES | {"context_omission"}
DIAGNOSTIC_PROMPT = '''
额外返回retrieval_diagnostics=[{"requirement_id":"原需求ID", "action":"reuse|supplement|rebuild|stop",
"cause":"evidence_gap|missing_conditions|source_gap|conflicting_evidence|context_omission|misreading|format|protocol|calculation|binding|user_decision|measurement",
"gap":"缺少的具体信息或证据", "reason":"动作理由", "evidence_ids":["已有相关段落ID，不表示足以回答缺口"],
"queries":["研究对象+缺失指标/条件，不能只是审核套话"],
"db_type":"literature|textbook|both", "focus_component":null或"cathode|anode|electrolyte",
"focus_labels":[], "use_label_filter":false}]。
逐问题诊断，混合写作错误不应掩盖独立证据缺口。已归档证据足以回答当前具体问题时reuse，即使此前没送入上下文；
已有单掺、异体系或部分条件等相关资料，但仍缺比较依据、直接证据或关键条件时supplement。
evidence_ids仅关联已有资料和同源查询锚点，填写它不会阻止supplement；不能把相关性当作充分性。
格式/单位换算/参数绑定/误读先修复，不查询。用户决策或本批材料实测缺口stop。
缺倍率/圈数/比例/工艺或独立来源时supplement；引用已知论文可查询同源条件/邻近段落；证据矛盾时查反证与条件。
retrieval_initial_defect=null或{"type":"wrong_subject|wrong_source|systematic_filter",
"affected_requirement_ids":[],"original_problem":"首次查询/路由/过滤的确切错误", "correction":"明确改法",
"archive_cannot_resolve":true,"evidence_ids":[],"queries":[],"db_type":"literature|textbook|both",
"focus_component":null,"focus_labels":[],"use_label_filter":false}。
rebuild只用于首次策略的严重整体错误，至少一半核心研究要求受影响且不少于两项。低置信度、单个候选无文献、
一次补检索/修复失败不是rebuild理由。rebuild窗口仅初始检索评估和首次有效审核开放，关闭后不能建议自动重建。
rebuild窗口关闭后，有剩余补检索预算时仍可针对新缺口supplement。低置信度源于证据不足时给出具体缺口与查询。
检索改善不允许修改原课题、用户锁定或替换材料方案。只使用已有库，不能新增网络文献采集。
仅输出一个完整JSON对象，不在对象外添加说明或另一个候选响应。
'''
GAIN_PROMPT = '''只评估本轮证据增益，不改正文和方案。输出JSON {"gains":[{"requirement_id":"目标ID",
"kind":"conditions|independent_source|conflict_explanation|direct_evidence|counter_evidence",
"passage_id":"候选ID","quote":"该段落逐字原句","reason":"它具体补齐什么/反驳什么"}]}。
仅有新段落不算改善，重复或不相关证据输出gains=[]。文献中的不同材料和工况不能冒充同体系直接实证。
可计入补齐条件、可比独立来源、解释矛盾或真实反证；同一篇文章的另一段不算independent_source。'''


def options(pipeline):
    return budget_options("stage4_retrieval", getattr(pipeline, "retrieval_options", None))


def channel_status(diagnostics, results):
    good = any(d.get("status") == "OK" for d in diagnostics)
    bad = any(d.get("status") in ("ERROR", "UNAVAILABLE") for d in diagnostics)
    if results:
        return "DEGRADED" if bad else "OK"
    return "EMPTY" if good or not bad else "ERROR"


def requirements(seed):
    contract = (seed.get("plan") or {}).get("requirements") or {}
    # 格式要求、固定值和用户偏好不计入核心证据需求分母。
    return {r["id"]: r for r in contract.get("items", []) if r.get("kind") in ("objective", "deliverable")
            and r.get("status") == "clear" and not r.get("binding") and r.get("evidence_needed", True)}


def init_state(seed, *, fresh=False):
    if isinstance(seed.get("retrieval_state"), dict):
        state = seed["retrieval_state"]
        if state.get("version") != VERSION:
            raise ValueError("检索检查点版本不兼容，不能重置预算")
        for key in ("supplement_rounds", "rebuild_rounds", "service_retries"):
            if not isinstance(state.get(key), int) or isinstance(state[key], bool) or state[key] < 0:
                raise ValueError("检索检查点计数非法，不能重置预算")
        for key in ("attempted_queries", "working_ids", "preferred_ids", "rounds", "context_history", "pending_diagnostics"):
            if not isinstance(state.get(key), list):
                raise ValueError("检索检查点结构非法：" + key)
        if not isinstance(state.get("stopped_gaps"), dict) or not isinstance(state.get("window_open"), bool):
            raise ValueError("检索检查点窗口或停止记录非法")
        state.setdefault("gain_signatures", [])
        return state
    archive = seed.get("evidence", [])
    state = {"version": VERSION, "window_open": bool(fresh), "initial_assessed": False,
        "first_review_seen": False, "supplement_rounds": int(bool(seed.get("supplemented"))), "rebuild_rounds": 0,
        "service_retries": 0, "attempted_queries": [], "stopped_gaps": {}, "pending_diagnostics": [],
        "working_ids": [e.get("passage_id") for e in archive], "preferred_ids": [],
        "initial_strategy": strategy(seed), "rounds": [], "context_history": [], "diagnostics": [],
        "evidence_conflicts": [], "gain_signatures": [], "inflight": None}
    # 存量检查点不能借升级重新打开首次重检索窗口。
    if not fresh:
        state["diagnostics"].append("历史检查点按现有预算恢复，首次重检索窗口保持关闭")
    seed["retrieval_state"] = state
    for q in state["initial_strategy"]["queries"]:
        state["attempted_queries"].append(_query_signature(q, state["initial_strategy"]))
    return state


def strategy(seed):
    plan = seed.get("plan") or {}
    return {"queries": list(plan.get("retrieval_queries", [])), "db_type": seed.get("db_type", plan.get("db_type", "both")),
            "focus_component": plan.get("focus_component"), "focus_labels": plan.get("focus_labels", []),
            "use_label_filter": not plan.get("needs_reasoning", False)}


def _decode(raw):
    """读取唯一完整JSON，兼容说明文字和代码围栏，重复键及破损外层仍报错。"""
    from .writer_protocol import decode
    text = raw.strip()
    blocks = []
    if not text.startswith(("{", "[")):
        blocks = list(re.finditer(r"```(?:json)?\s*\n?(.*?)```", text, re.S | re.I))
    if blocks:
        if len(blocks) != 1:
            raise ValueError("检索诊断包含多份JSON，无法确定唯一响应")
        block = blocks[0]
        candidate = block[1].strip()
        surrounding = text[:block.start()] + text[block.end():]
    else:
        start = 0 if text.startswith(("{", "[")) else text.find("{")
        if start < 0:
            raise ValueError("检索诊断未包含完整JSON对象")
        candidate, surrounding = text[start:], text[:start]
    decoder = json.JSONDecoder()
    _, end = decoder.raw_decode(candidate)
    value = decode(candidate[:end])
    surrounding += "\n" + candidate[end:]
    for match in re.finditer(r"[\{\[]", surrounding):
        try:
            decoder.raw_decode(surrounding, match.start())
        except json.JSONDecodeError:
            continue
        raise ValueError("检索诊断包含多份JSON，无法确定唯一响应")
    if not isinstance(value, dict):
        raise ValueError("检索诊断须为对象")
    return value


def _tokens(value):
    value = unicodedata.normalize("NFKC", str(value)).casefold()
    words = re.findall(r"[a-z0-9][a-z0-9.+/-]*|[\u4e00-\u9fff]+", value)
    result = set()
    for word in words:
        if re.search(r"[\u4e00-\u9fff]", word):
            result.update(word[i:i+2] for i in range(len(word)-1))
        elif len(word) > 1:
            result.add(word)
    return result


def evidence_package(evidence, *, text="", issues=(), requirement_items=(), preferred_ids=(), working_ids=None,
                     limit=None, char_limit=None):
    """引用/新增证据优先，按要求轮转选取；不足容量记录未纳入项，不改归档原文。"""
    defaults = budget_options("stage4_retrieval")
    limit = defaults["context_passages"] if limit is None else limit
    char_limit = defaults["context_chars"] if char_limit is None else char_limit
    archive = {e.get("passage_id"): e for e in evidence if isinstance(e, dict) and e.get("passage_id")}
    cited = re.findall(r"\[([^\]\n]+)\]", text)
    issue_text = " ".join(str(i.get(k, "")) for i in issues if isinstance(i, dict) for k in ("quote", "reason", "suggestion", "gap"))
    explicit = [p for i in issues if isinstance(i, dict) for p in i.get("evidence_ids", [])]
    pinned = list(dict.fromkeys(explicit + re.findall(r"\[([^\]\n]+)\]", issue_text) + list(preferred_ids) + cited))
    eligible = set(working_ids) if working_ids is not None else set(archive)
    targets = [issue_text] if issue_text.strip() else []
    targets += [r.get("description", "") + " " + " ".join(r.get("conditions", [])) for r in requirement_items]
    def score(e, target):
        return len(_tokens(target) & _tokens(e.get("text", "") + " " + str(e.get("source", ""))))
    rankings = [sorted((e for pid, e in archive.items() if pid in eligible), key=lambda e: -score(e, t)) for t in targets]
    order = [archive[p] for p in pinned if p in archive]
    if rankings:
        for n in range(max(map(len, rankings), default=0)):
            order.extend(r[n] for r in rankings if n < len(r))
    else:
        order.extend(e for pid, e in archive.items() if pid in eligible)
    selected, seen, chars = [], set(), 0
    for e in order:
        pid = e["passage_id"]
        if pid in seen:
            continue
        seen.add(pid)
        if len(selected) >= limit or chars + len(e.get("text", "")) > char_limit:
            continue
        selected.append(e)
        chars += len(e.get("text", ""))
    used = [e["passage_id"] for e in selected]
    return selected, {"selected_ids": used, "omitted_pinned_ids": [p for p in pinned if p in archive and p not in used],
                      "chars": chars, "limit": limit, "char_limit": char_limit}


def select_context(seed, *, purpose, text="", issues=(), config=None):
    state = init_state(seed)
    config = config or DEFAULTS
    preferred_ids = state["preferred_ids"]
    if purpose in ("reviewer", "final_review"):
        from .research_support import cited_evidence_ids
        preferred_ids = list(dict.fromkeys(cited_evidence_ids(seed.get("research_draft")) + preferred_ids))
    pack, selection = evidence_package(seed.get("evidence", []), text=text, issues=issues,
        requirement_items=list(requirements(seed).values()), preferred_ids=preferred_ids,
        working_ids=state["working_ids"], limit=config["context_passages"], char_limit=config["context_chars"])
    selection["purpose"] = purpose
    state["context_history"].append(selection)
    return pack


def _validated_diagnostics(review, seed):
    known = {e.get("passage_id") for e in seed.get("evidence", [])}
    core = requirements(seed)
    raw = review.get("retrieval_diagnostics", [])
    if not isinstance(raw, list):
        return []
    result = []
    for d in raw:
        if not isinstance(d, dict) or d.get("requirement_id") not in core or d.get("action") not in ("reuse", "supplement", "rebuild", "stop") or d.get("cause") not in CAUSES:
            continue
        ids = d.get("evidence_ids", [])
        if not isinstance(ids, list) or any(not isinstance(p, str) or p not in known for p in ids):
            continue
        if not isinstance(d.get("gap"), str) or not d["gap"].strip() or not isinstance(d.get("reason"), str) or not d["reason"].strip():
            continue
        item = copy.deepcopy(d)
        item["evidence_ids"] = ids
        result.append(item)
    return result


def _query_signature(query, spec):
    query = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", query)).strip().casefold()
    return digest([query, spec.get("db_type"), spec.get("focus_component"), sorted(spec.get("focus_labels", [])), spec.get("use_label_filter", False)])


def _search_spec(raw, seed):
    result = {"db_type": raw.get("db_type", seed.get("db_type", "both")),
              "focus_component": raw.get("focus_component"), "focus_labels": raw.get("focus_labels", []),
              "use_label_filter": raw.get("use_label_filter", False)}
    if result["db_type"] not in ("literature", "textbook", "both") or result["focus_component"] not in (None, "cathode", "anode", "electrolyte"):
        raise ValueError("检索源或组件非法，只能使用已有库")
    if not isinstance(result["focus_labels"], list) or any(not isinstance(x, str) for x in result["focus_labels"]) or not isinstance(result["use_label_filter"], bool):
        raise ValueError("检索过滤条件非法")
    return result


def query_list(raw, state, spec, limit):
    if limit == 0:
        return []
    values = raw if isinstance(raw, list) else []
    result = []
    for q in values:
        if not isinstance(q, str) or not 4 <= len(q.strip()) <= 400:
            continue
        # 必须是真实检索问题，不能只有证据不足等审核提示。
        if not _tokens(re.sub(r"覆盖|性能达标|证据|不足|缺少|缺乏|文献|数据|研究|未找到|检索", "", q)):
            continue
        signature = _query_signature(q, spec)
        if signature in state["attempted_queries"] or any(_query_signature(q, spec) == _query_signature(p, spec) for p in result):
            continue
        result.append(q.strip())
        if len(result) == limit:
            break
    return result


def rebuild_allowed(defect, seed, state, config):
    if not state["window_open"] or state["rebuild_rounds"] >= config["rebuild_rounds"]:
        return False, "首次重检索窗口已关闭或预算耗尽"
    if not isinstance(defect, dict) or defect.get("type") not in DEFECTS or defect.get("archive_cannot_resolve") is not True:
        return False, "未证明首次策略存在整体性缺陷"
    known = {e.get("passage_id") for e in seed.get("evidence", [])}
    ids = defect.get("evidence_ids", [])
    if not isinstance(ids, list) or any(not isinstance(p, str) or p not in known for p in ids):
        return False, "首次缺陷引用未归档证据"
    if (seed.get("retrieval") or {}).get("status") == "ERROR":
        return False, "首次检索服务失败不是整体策略错误，应按原策略有限重试"
    core = requirements(seed)
    affected = defect.get("affected_requirement_ids", [])
    if not isinstance(affected, list) or any(r not in core for r in affected):
        return False, "受影响需求引用无效"
    count = len(set(affected))
    threshold = max(2, math.ceil(len(core) / 2)) if len(core) > 1 else 1
    if not core or count < threshold:
        return False, "首次偏差未影响至少一半且不少于两项核心需求"
    if len(core) == 1 and defect.get("type") != "wrong_subject":
        return False, "单项课题只有研究对象整体错误才能重检索"
    if not isinstance(defect.get("original_problem"), str) or not defect["original_problem"].strip() or not isinstance(defect.get("correction"), str) or not defect["correction"].strip():
        return False, "缺少首次策略错误及明确改法"
    if defect.get("type") == "wrong_source" and defect.get("db_type") == state["initial_strategy"]["db_type"]:
        return False, "数据源纠正没有实际改变"
    if defect.get("type") == "wrong_subject" and not any(q not in state["initial_strategy"]["queries"] for q in defect.get("queries", []) if isinstance(q, str)):
        return False, "研究对象纠正没有新的查询"
    if defect.get("type") == "systematic_filter" and not any(defect.get(k) != state["initial_strategy"].get(k) for k in ("focus_component", "focus_labels", "use_label_filter")):
        return False, "过滤纠正没有实际改变"
    return True, "首次策略整体失效且存在明确纠正方案"


def _gap_key(d):
    return digest([d["requirement_id"], re.sub(r"\s+", "", d["gap"]).casefold()])


def next_action(review, seed, *, phase, config):
    state = init_state(seed)
    if phase not in ("initial", "first_review"):
        state["window_open"] = False
    ds = _validated_diagnostics(review, seed)
    state["pending_diagnostics"] = ds
    if (seed.get("retrieval") or {}).get("status") == "ERROR":
        for d in ds:
            state["stopped_gaps"][_gap_key(d)] = "检索通道不可用，需恢复服务；不能当作无文献"
        return {"action": "stop", "reason": "服务失败不重建策略，也不反复补检索", "diagnostics": ds}
    reusable = [d for d in ds if d["action"] == "reuse" or d.get("cause") in ("context_omission", "misreading")]
    state["preferred_ids"] = list(dict.fromkeys([p for d in reusable for p in d["evidence_ids"]] + state["preferred_ids"]))
    defect = review.get("retrieval_initial_defect")
    if any(d.get("cause") in ("context_omission", "misreading") and d["evidence_ids"] for d in ds):
        defect = None  # 现存证据有答案时先核验，不重新构建整个策略。
    if defect:
        allowed, reason = rebuild_allowed(defect, seed, state, config)
        if allowed:
            try:
                spec = _search_spec(defect, seed)
                qs = query_list(defect.get("queries"), state, spec, config["queries_per_round"])
            except ValueError:
                qs = []
            if qs:
                return {"action": "rebuild", "reason": reason, "queries": qs, "spec": spec,
                        "requirement_ids": list(set(defect["affected_requirement_ids"])), "diagnostics": ds,
                        "defect": copy.deepcopy(defect)}
            reason = "首次纠正方案没有有效新查询"
        state["diagnostics"].append("拒绝重检索：" + reason)
    for d in ds:
        gap = _gap_key(d)
        if d["action"] == "stop" or d.get("cause") in NON_RETRIEVAL:
            state["stopped_gaps"][gap] = d["reason"]
            continue
        if d["action"] != "supplement" or d["cause"] not in SUPPLEMENT_CAUSES or gap in state["stopped_gaps"]:
            continue
        if d["evidence_ids"]:
            # 相关资料可继续供修复/比较使用；显式证据缺口仍按建议补检索。
            state["preferred_ids"] = list(dict.fromkeys(d["evidence_ids"] + state["preferred_ids"]))
        if state["supplement_rounds"] >= config["supplement_rounds"]:
            state["stopped_gaps"][gap] = "针对性补检索预算耗尽"
            continue
        try:
            spec = _search_spec(d, seed)
            qs = query_list(d.get("queries"), state, spec, config["queries_per_round"])
        except ValueError as exc:
            state["stopped_gaps"][gap] = str(exc)
            continue
        if qs:
            chosen = [d]
            # 同路由的独立缺口可共享一批最多3个查询，避免两轮预算只能覆盖两个要求。
            for other in ds:
                if other is d or other["action"] != "supplement" or other["cause"] not in SUPPLEMENT_CAUSES or _gap_key(other) in state["stopped_gaps"]:
                    continue
                try:
                    other_spec = _search_spec(other, seed)
                except ValueError:
                    continue
                if other_spec != spec:
                    continue
                if not isinstance(other.get("queries", []), list):
                    continue
                new_queries = query_list(qs + other.get("queries", []), state, spec, config["queries_per_round"])
                if new_queries != qs:
                    chosen.append(other)
                    qs = new_queries
            return {"action": "supplement", "reason": "；".join(c["reason"] for c in chosen), "queries": qs, "spec": spec,
                    "requirement_ids": list(dict.fromkeys(c["requirement_id"] for c in chosen)), "diagnostics": chosen,
                    "gap_keys": [_gap_key(c) for c in chosen]}
        state["stopped_gaps"][gap] = "没有有效新查询，已尝试的策略不重复执行"
    return {"action": "reuse" if reusable else "stop", "reason": "已有证据优先复核" if reusable else "没有满足条件的检索动作", "diagnostics": ds}


def merge_archive(seed, results):
    state = init_state(seed)
    archive = seed.setdefault("evidence", [])
    by_id = {e["passage_id"]: e for e in archive}
    added, candidates = [], []
    for original in results:
        if not isinstance(original, dict) or not original.get("passage_id") or not isinstance(original.get("text"), str) or not original["text"].strip():
            continue
        e = copy.deepcopy(original)
        pid = e["passage_id"]
        if pid in by_id and (by_id[pid].get("text") != e["text"] or by_id[pid].get("source") != e.get("source")):
            state["evidence_conflicts"].append({"passage_id": pid, "archived": copy.deepcopy(by_id[pid]), "incoming": e,
                                                 "reason": "同一段落ID原文或来源变化，拒绝覆盖"})
            continue
        candidates.append(e)
        if pid not in by_id:
            archive.append(e)
            by_id[pid] = e
            added.append(pid)
    return candidates, list(dict.fromkeys(added))


def _source(e):
    from .report_analysis import source_identity
    return source_identity(e)[0]


def validate_gains(raw, action, seed, candidates):
    if not isinstance(raw, dict) or not isinstance(raw.get("gains"), list):
        raise ValueError("证据增益格式无效")
    by_id = {e["passage_id"]: e for e in candidates}
    accepted, seen = [], set()
    for g in raw["gains"]:
        if not isinstance(g, dict) or g.get("requirement_id") not in action["requirement_ids"] or g.get("kind") not in ("conditions", "independent_source", "conflict_explanation", "direct_evidence", "counter_evidence"):
            continue
        e = by_id.get(g.get("passage_id"))
        if not e or not isinstance(g.get("quote"), str) or not g["quote"].strip() or g["quote"] not in e["text"] or not g.get("reason"):
            continue
        if g["kind"] == "independent_source":
            old_ids = [p for d in action["diagnostics"] for p in d["evidence_ids"]]
            sources = {_source(old) for old in seed["evidence"] if old["passage_id"] in old_ids}
            if not sources or _source(e) in sources:
                continue
        signature = digest([g["requirement_id"], g["kind"], g["passage_id"], g["quote"]])
        if signature in init_state(seed).get("gain_signatures", []) or signature in seen:
            continue
        seen.add(signature)
        accepted.append(copy.deepcopy(g))
    return accepted


def execute_action(pipeline, seed, action, save, config):
    """查询签名和预算先持久化；重启不隐式重发已发请求。"""
    state = init_state(seed)
    name = action["action"]
    if name not in ("supplement", "rebuild"):
        return False
    counter = "supplement_rounds" if name == "supplement" else "rebuild_rounds"
    if state[counter] >= config[counter] or not action.get("queries") or len(action["queries"]) > config["queries_per_round"]:
        state["diagnostics"].append("拒绝检索：动作预算或查询数非法")
        save()
        return False
    if name == "rebuild":
        allowed, reason = rebuild_allowed(action.get("defect"), seed, state, config)
        if not allowed:
            state["diagnostics"].append("拒绝重检索：" + reason)
            save()
            return False
    if query_list(action["queries"], state, action["spec"], config["queries_per_round"]) != action["queries"]:
        state["diagnostics"].append("拒绝检索：查询重复或无效")
        save()
        return False
    state["window_open"] = False
    state[counter] += 1
    state["attempted_queries"].extend(_query_signature(q, action["spec"]) for q in action["queries"])
    event = copy.deepcopy(action)
    event.update(status="RUNNING", result_ids=[], added_ids=[], gains=[])
    state["rounds"].append(event)
    state["inflight"] = len(state["rounds"]) - 1
    save()
    try:
        retriever = getattr(pipeline, "retriever", None)
        old_inference = getattr(retriever, "disable_component_inference", False)
        old_extra = getattr(retriever, "skip_extra_query", False)
        if retriever:
            retriever.disable_component_inference = action["spec"]["focus_component"] is None
            retriever.skip_extra_query = True
        response = pipeline.retrieve_context(question=" ".join(action["queries"]), queries=action["queries"],
            previous_evidence=None, top_k=config["results_per_round"], **action["spec"])
        if response.get("status") == "ERROR":
            raise RuntimeError(str(response.get("channel_diagnostics") or response.get("error") or "检索通道失败"))
    except Exception as exc:
        event.update(status="SERVICE_ERROR", reason=str(exc))
        if state["service_retries"] < config["service_retries"]:
            state["service_retries"] += 1
            event["service_retry"] = True
            save()
            try:
                response = pipeline.retrieve_context(question=" ".join(action["queries"]), queries=action["queries"], previous_evidence=None, top_k=config["results_per_round"], **action["spec"])
                if response.get("status") == "ERROR":
                    raise RuntimeError("重试后检索通道仍失败")
            except Exception as retry_error:
                event["reason"] += "; " + str(retry_error)
                response = None
        else:
            response = None
        if response is None:
            for d in action["diagnostics"]:
                state["stopped_gaps"][_gap_key(d)] = "检索服务不可用，不能当作无文献"
            state["inflight"] = None
            if retriever:
                retriever.disable_component_inference = old_inference
                retriever.skip_extra_query = old_extra
            save()
            return False
    if retriever:
        retriever.disable_component_inference = old_inference
        retriever.skip_extra_query = old_extra
    event["channel_diagnostics"] = copy.deepcopy(response.get("channel_diagnostics", []))
    event["query_logs"] = copy.deepcopy(response.get("search_logs", []))
    before_ids = {e["passage_id"] for e in seed["evidence"]}
    results = list(response.get("results", []))
    if name == "supplement" and any(d.get("cause") == "missing_conditions" and d["evidence_ids"] for d in action["diagnostics"]):
        neighbours = source_neighbours(pipeline, seed, action)
        results = neighbours + results
        event["same_source_ids"] = [e["passage_id"] for e in neighbours]
    candidates, added = merge_archive(seed, results)
    event.update(result_ids=[e["passage_id"] for e in candidates], added_ids=added)
    save()
    gains = []
    client = getattr(getattr(pipeline, "reviewer", None), "llm", None)
    if candidates and client is not None and getattr(client, "available", False):
        try:
            old_pack, _ = evidence_package([e for e in seed["evidence"] if e["passage_id"] in before_ids],
                issues=action["diagnostics"], limit=config["gain_previous_passages"], char_limit=config["context_chars"])
            gain_pack, gain_context = evidence_package(candidates, preferred_ids=added,
                requirement_items=[requirements(seed)[r] for r in action["requirement_ids"]],
                limit=config["context_passages"], char_limit=config["context_chars"])
            event["gain_context"] = gain_context
            raw = client.chat(GAIN_PROMPT, json.dumps({"goal": seed.get("question"), "requirements": {r: requirements(seed)[r] for r in action["requirement_ids"]},
                "gaps": action["diagnostics"], "previous_evidence": old_pack, "candidates": gain_pack}, ensure_ascii=False), temperature=0.1, json_mode=True, trace_tag="retrieval_gain")
            gains = validate_gains(_decode(raw), action, seed, gain_pack)
        except Exception as exc:
            event["gain_error"] = str(exc)
    event["gains"] = gains
    state.setdefault("gain_signatures", []).extend(digest([g["requirement_id"], g["kind"], g["passage_id"], g["quote"]]) for g in gains)
    useful = list(dict.fromkeys(g["passage_id"] for g in gains))
    if name == "rebuild":
        # 保留旧档案，当前工作证据只用纠正后的召回；仍需通过增益审核。
        state["working_ids"] = event["result_ids"] if useful else []
        state["preferred_ids"] = []
    else:
        state["working_ids"] = list(dict.fromkeys(state["working_ids"] + useful))
    state["preferred_ids"] = list(dict.fromkeys(useful + state["preferred_ids"]))
    event.update(status="IMPROVED" if useful else "NO_GAIN", stop_reason=None if useful else "未补齐条件、独立来源、直接证据或矛盾解释")
    if not useful:
        for d in action["diagnostics"]:
            state["stopped_gaps"][_gap_key(d)] = event["stop_reason"]
    state["inflight"] = None
    save()
    return bool(useful)


def initial_assessment(pipeline, seed, save):
    state = init_state(seed, fresh=True)
    state["budget"] = options(pipeline)
    if state["inflight"] is not None:
        state["rounds"][state["inflight"]]["status"] = "INTERRUPTED"
        state["inflight"] = None
        state["window_open"] = False
        state["diagnostics"].append("检索评估恢复前已中断，保留预算，不自动重发")
        save()
    if state["initial_assessed"]:
        return
    state["initial_assessed"] = True
    save()
    initial = seed.get("retrieval") or {}
    if initial.get("status") == "ERROR":
        # 原策略服务重试不占补检索预算，不视为新策略；共享配置中的服务重试配额。
        if state["service_retries"] < options(pipeline)["service_retries"]:
            state["service_retries"] += 1
            save()
            try:
                st = state["initial_strategy"]
                retry = pipeline.retrieve_context(question=seed.get("question", ""), queries=st["queries"],
                    db_type=st["db_type"], focus_component=st["focus_component"], focus_labels=st["focus_labels"],
                    use_label_filter=st["use_label_filter"], previous_evidence=None, top_k=options(pipeline)["initial_retry_results"])
                seed["retrieval"] = retry
                if retry.get("status") != "ERROR":
                    _, added = merge_archive(seed, retry.get("results", []))
                    state["working_ids"] = list(dict.fromkeys(state["working_ids"] + added))
                state["initial_service_retry"] = {"status": retry.get("status"), "channels": retry.get("channel_diagnostics", [])}
            except Exception as exc:
                state["initial_service_retry"] = {"status": "ERROR", "error": str(exc)}
        if (seed.get("retrieval") or {}).get("status") == "ERROR":
            state["window_open"] = False
            state["diagnostics"].append("首次检索通道不可用，停止自动检索；不能推断文献不存在")
            save()
            return
    client = getattr(getattr(pipeline, "reviewer", None), "llm", None)
    if not requirements(seed) or client is None or not getattr(client, "available", False):
        state["diagnostics"].append("无明确核心需求或语义评估不可用，首次检索不自动重建")
        save()
        return
    try:
        pack = select_context(seed, purpose="initial_assessment", config=options(pipeline))
        raw = client.chat("只评估首次检索，不编写方案。默认reuse，只有严重整体策略错误才rebuild。" + DIAGNOSTIC_PROMPT,
            json.dumps({"goal": seed.get("question"), "requirements": list(requirements(seed).values()), "strategy": state["initial_strategy"],
                        "channels": (seed.get("retrieval") or {}).get("channel_diagnostics", []), "evidence": pack,
                        "archive_manifest": [{"passage_id": e["passage_id"], "source": e.get("source"), "preview": e.get("text", "")[:300]} for e in seed["evidence"]]}, ensure_ascii=False),
            temperature=0.1, json_mode=True, trace_tag="retrieval_assess")
        review = _decode(raw)
        state["initial_assessment"] = review
        action = next_action(review, seed, phase="initial", config=options(pipeline))
        execute_action(pipeline, seed, action, save, options(pipeline))
    except Exception as exc:
        state["diagnostics"].append("首次证据评估未完成：" + str(exc))
    save()


def after_review(pipeline, seed, review, save):
    state = init_state(seed)
    state["budget"] = options(pipeline)
    if state["inflight"] is not None:
        state["rounds"][state["inflight"]]["status"] = "INTERRUPTED"
        state["diagnostics"].append("前次检索中断，已消耗预算保留，不自动重发")
        state["inflight"] = None
    phase = "first_review" if not state["first_review_seen"] else "later_review"
    state["first_review_seen"] = True
    action = next_action(review, seed, phase=phase, config=options(pipeline))
    # 首次有效审核之后，无论是否检索，都永久关闭重建窗口。
    save()
    if action["action"] in ("supplement", "rebuild"):
        changed = execute_action(pipeline, seed, action, save, options(pipeline))
        state["window_open"] = False
        save()
        return changed
    state["window_open"] = False
    save()
    return False


def source_neighbours(pipeline, seed, action):
    """利用已加载段落库查同源附近条件，不联网、不刷新或创建索引。"""
    anchor_ids = {p for d in action["diagnostics"] for p in d["evidence_ids"]}
    anchors = [e for e in seed["evidence"] if e["passage_id"] in anchor_ids]
    source_keys = {_source(e) for e in anchors}
    collections = [getattr(getattr(pipeline, "retriever", None), "kb", None)]
    multi = getattr(pipeline, "multi_retriever", None)
    collections += [getattr(multi, "lit_bm25", None), getattr(multi, "ebook_bm25", None)]
    seen, rows = set(), []
    words = _tokens(" ".join(action["queries"]))
    for kb in collections:
        for p in getattr(kb, "passages", []):
            if p.passage_id in seen or p.passage_id in anchor_ids:
                continue
            e = {"passage_id": p.passage_id, "source": p.source, "text": p.text, "metadata": p.metadata}
            if _source(e) not in source_keys:
                continue
            seen.add(p.passage_id)
            score = len(words & _tokens(p.text))
            index = (p.metadata or {}).get("paragraph_index")
            for a in anchors:
                previous = (a.get("metadata") or {}).get("paragraph_index")
                if isinstance(index, int) and isinstance(previous, int) and abs(index - previous) <= 2:
                    score += 4
            rows.append((score, e))
    return [e for _, e in sorted(rows, key=lambda r: -r[0])[:options(pipeline)["source_neighbours"]]]


def has_pending(seed, *, retrieval_options=None, repair_options=None):
    # 正文生成失败时，补证据不能恢复正文；保留预算与诊断，避免空跑耗尽阶段重试。
    if not (seed.get("draft") or "").strip() and (seed.get("writer_output") or {}).get("error"):
        return False
    state = seed.get("retrieval_state")
    if not isinstance(state, dict) or state.get("version") != VERSION or state.get("inflight") is not None:
        return False
    limits = checkpoint_repair_options(seed, repair_options)
    if (seed.get("repair_rounds", 0) >= limits["content_rounds"]
            or seed.get("review_rounds", 0) >= limits["review_rounds"] or seed.get("protocol_exhausted")):
        return False
    shadow = copy.deepcopy(seed)
    review = {"retrieval_diagnostics": state.get("pending_diagnostics", [])}
    try:
        config = budget_options("stage4_retrieval", retrieval_options if retrieval_options is not None else state.get("budget"))
        return next_action(review, shadow, phase="later_review", config=config)["action"] == "supplement"
    except (ValueError, TypeError, KeyError):
        return False


def cache_resume_matches(seed, doc, provenance):
    """已定案缓存恢复须同时匹配文本、证据、方案表和语料，不能仅因预算未用完就复跑。"""
    if digest(seed.get("retrieval_state")) != digest(doc.get("retrieval_state")):
        return False
    if digest(seed.get("evidence", [])) != digest(doc.get("evidence", [])):
        return False
    if seed.get("draft") is not None and seed["draft"].strip() != re.split(r"\n+## 参考来源(?:\r?\n|$)", doc.get("final_answer") or "", maxsplit=1)[0].strip():
        return False
    ledger = doc.get("ledger_view") or {}
    from .research_contract import field_equal
    for key, field in seed.get("parameters", {}).items():
        if not field_equal(key, ledger.get("parameters", {}).get(key, {}).get("value"), field.get("value")):
            return False
    old = doc.get("provenance") or {}
    return all(digest(old.get(k)) == digest(provenance.get(k)) for k in ("corpus", "vector_index", "rules_version"))


def render_audit(doc):
    state = doc.get("retrieval_state")
    if not state:
        return ""
    lines = ["### Stage 4证据改善记录", "重检索仅用于首次策略整体失效；未检到只表示当前库与本次查询范围内未找到。",
             f"针对性补检索{state.get('supplement_rounds', 0)}轮，重建检索{state.get('rebuild_rounds', 0)}轮。"]
    for n, event in enumerate(state.get("rounds", []), 1):
        lines += [f"{n}. **{'针对性补检索' if event['action'] == 'supplement' else '首次策略重建'}：{event.get('status')}**",
                  "   - 原因：" + str(event.get("reason", "")), "   - 查询：" + "；".join(event.get("queries", [])),
                  "   - 证据增益：" + ("；".join(g["reason"] for g in event.get("gains", [])) or str(event.get("stop_reason") or "评估未完成"))]
    lines.extend("- " + s for s in list(dict.fromkeys(state.get("stopped_gaps", {}).values())) + list(dict.fromkeys(state.get("diagnostics", []))))
    return "\n\n".join(lines)
