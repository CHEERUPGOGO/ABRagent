"""多产物发布日志与恢复：逐文件原子替换，门禁拒绝未完成或混合版本。"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from pathlib import Path

from .scheme_ledger import atomic_write, task_lock, digest, load, render

JOURNAL = "stage4_publication.json"


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None


def _read(path):
    return path.read_text(encoding="utf-8") if path.exists() else None


def publication_errors(task_dir):
    task = Path(task_dir)
    journal = task / JOURNAL
    if not journal.exists():
        return ["v3发布日志缺失"]
    try:
        data = json.loads(journal.read_text(encoding="utf-8"))
        if data.get("status") != "complete" or not isinstance(data.get("files"), dict) or not data["files"]:
            return ["v3多产物发布尚未完成"]
        errors = []
        for name, item in data["files"].items():
            path = (task / name).resolve()
            if not path.is_relative_to(task.resolve()):
                errors.append("发布路径越界")
            elif _hash(_read(path)) != item.get("new_hash"):
                errors.append("发布产物为混合版本或已被修改：" + name)
        return errors
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return ["发布日志损坏：" + str(exc)]


def _recover_locked(task, journal, *, rollback=False):
    """先检查所有目标仍等于旧版或本事务新版，任何外部写入均停止。"""
    data = json.loads(journal.read_text(encoding="utf-8"))
    if data.get("status") == "complete":
        return data
    if data.get("status") == "rolled_back":
        raise ValueError("发布事务已回滚，不能自动恢复为新版")
    entries = data.get("files")
    if not isinstance(entries, dict) or not entries:
        raise ValueError("发布事务日志损坏")
    for name, item in entries.items():
        path = (task / name).resolve()
        if not path.is_relative_to(task.resolve()) or _hash(_read(path)) not in (item["old_hash"], item["new_hash"]):
            raise ValueError("检测到事务外部写入，停止恢复：" + name)
        if _hash(item["old_text"]) != item["old_hash"] or _hash(item["new_text"]) != item["new_hash"]:
            raise ValueError("发布日志内容哈希损坏")
    choice = "old" if rollback else "new"
    for name, item in entries.items():
        path = task / name
        text = item[choice + "_text"]
        if text is None:
            path.unlink(missing_ok=True)
        elif _hash(_read(path)) != item[choice + "_hash"]:
            atomic_write(path, text)
        data["replaced"] = name
        atomic_write(journal, json.dumps(data, ensure_ascii=False, indent=2))
    for name, item in entries.items():
        if _hash(_read(task / name)) != item[choice + "_hash"]:
            raise ValueError("发布验收失败：" + name)
    data["status"] = "rolled_back" if rollback else "complete"
    atomic_write(journal, json.dumps(data, ensure_ascii=False, indent=2))
    return data


def recover_publication(task_dir, *, rollback=False):
    task = Path(task_dir)
    journal = task / JOURNAL
    with task_lock(task):
        return _recover_locked(task, journal, rollback=rollback) if journal.exists() else None


def publish_files(task_dir, files, *, expected=None):
    """不是整体原子事务：日志先写、逐个替换、全量验收、最后完成标记。"""
    task = Path(task_dir)
    with task_lock(task):
        return _publish_locked(task, files, expected=expected)


def _publish_locked(task, files, *, expected=None):
    journal = task / JOURNAL
    if journal.exists():
        previous = json.loads(journal.read_text(encoding="utf-8"))
        if previous.get("status") != "complete":
            raise ValueError("先恢复未完成发布事务，禁止覆盖日志")
        atomic_write(task / "stage4_publication_history" / (previous["transaction_id"] + ".json"), json.dumps(previous, ensure_ascii=False, indent=2))
    if expected:
        for name, fingerprint in expected.items():
            if _hash(_read(task / name)) != fingerprint:
                raise ValueError("发布期间目标已更新：" + name)
    entries = {}
    for name, text in files.items():
        path = (task / name).resolve()
        if not path.is_relative_to(task.resolve()) or path == journal.resolve() or not isinstance(text, str):
            raise ValueError("发布路径或内容非法")
        old = _read(path)
        entries[name] = {"old_hash": _hash(old), "new_hash": _hash(text), "old_text": old, "new_text": text}
    data = {"version": 1, "status": "prepared", "files": entries,
            "transaction_id": digest({name: item["new_hash"] for name, item in entries.items()})}
    atomic_write(journal, json.dumps(data, ensure_ascii=False, indent=2))
    return _recover_locked(task, journal)


def design_markdown(doc):
    from auto_battery_research.simulation import format_geometry_markdown
    from auto_battery_research.tools.geometry_revision import render_revision_log_markdown
    text = doc.get("final_answer", "")
    geometry = (doc.get("scheme") or {}).get("geometry")
    if geometry:
        text += "\n\n---\n\n" + format_geometry_markdown(geometry)
    revision = render_revision_log_markdown((doc.get("scheme") or {}).get("geometry_revisions") or [])
    if revision:
        text += "\n\n" + revision
    return text


def publish_document(task_dir, doc, *, commit=False, base_revision=None, raw=None, reason="Stage 4 v3一致发布", extra_files=None):
    """在隔离目录复用现有提交规则，真实方案表与正文在同一日志事务发布。"""
    from .stage4_document import compile_document
    from .stage4_session import synchronize_document
    task = Path(task_dir)
    with task_lock(task):
        ledger_text = _read(task / "scheme_ledger.json")
        current = load(task)
        prepared = copy.deepcopy(doc)
        files = {}
        existing_design = task / "design_scheme.json"
        if existing_design.exists() and reason != "Stage 4 v3一致发布":
            previous_doc = json.loads(existing_design.read_text(encoding="utf-8"))
            if (previous_doc.get("writer_contract_version") == 3 and
                    (prepared.get("content_hash") != previous_doc.get("content_hash")
                     or prepared.get("render_fingerprint") != previous_doc.get("render_fingerprint"))):
                raise ValueError("当前内容或参数视图在发布前已变化，拒绝覆盖并发版本")
        if not current:
            raise ValueError("v3发布前方案表必须存在")
        if base_revision is not None and current["revision"] != base_revision:
            raise ValueError("方案表在发布前已更新")
        if commit:
            from .scheme_ledger import commit_design
            with tempfile.TemporaryDirectory(prefix=".stage4_prepare_", dir=task) as staging:
                staged = Path(staging)
                atomic_write(staged / "scheme_ledger.json", ledger_text)
                commit_design(staged, prepared, reason=reason, base_revision=current["revision"])
                for path in staged.rglob("*"):
                    if path.is_file() and path.name != ".scheme_ledger.lock":
                        files[path.relative_to(staged).as_posix()] = path.read_text(encoding="utf-8")
        else:
            if prepared.get("ledger_hash") != digest(current):
                raise ValueError("正文更新基于旧方案表")
            prepared["ledger_view"] = copy.deepcopy(current)
        synchronize_document(prepared, prepared["ledger_view"]["parameters"])
        # Stage 5来源更新必须投影到当前参数；正文旧来源仅留历史，不作为冻结的第二份值。
        compiled = compile_document(prepared["writer_content"], prepared["ledger_view"]["parameters"], prepared.get("requirements"), prepared.get("evidence", []))
        if not compiled.valid:
            raise ValueError("发布前编译失败：" + "；".join(d["reason"] for d in compiled.diagnostics if d["severity"] == "error"))
        files.setdefault("scheme_ledger.json", ledger_text)
        files.setdefault("scheme_table.md", render(current))
        files["design_scheme.json"] = json.dumps(prepared, ensure_ascii=False, indent=2)
        files["design_scheme.md"] = design_markdown(prepared)
        raw_doc = copy.deepcopy(raw) if raw is not None else {"writer_contract_version": 3}
        if raw is None and (task / "rag_result.json").exists():
            raw_doc = json.loads((task / "rag_result.json").read_text(encoding="utf-8"))
        raw_doc.update({k: copy.deepcopy(prepared.get(k)) for k in ("final_answer", "scheme", "scheme_parameters", "writer_content", "content_hash", "render_fingerprint", "research", "consistency_review", "reviewer_output")})
        files["rag_result.json"] = json.dumps(raw_doc, ensure_ascii=False, indent=2)
        files["research_context.json"] = json.dumps({"target": prepared.get("target"), "provenance": prepared.get("provenance"),
            "content_hash": prepared["content_hash"], "render_fingerprint": prepared["render_fingerprint"]}, ensure_ascii=False, indent=2)
        geometry = prepared.get("scheme", {}).get("geometry", {})
        if geometry:
            revision = prepared["scheme"].get("design_revision", 0)
            name = "design_revisions/revision_" + str(revision) + "_" + prepared["scheme"].get("geometry_hash", prepared["content_hash"])[:12] + ".json"
            if not (task / name).exists():
                files[name] = json.dumps(prepared, ensure_ascii=False, indent=2)
        additions = extra_files(prepared) if callable(extra_files) else (extra_files or {})
        files.update(additions)
        # Stage 4正文事务不认领后续独立仿真写入；择优恢复事务显式纳入其物理文件。
        if prepared.get("experiment_contract"):
            # 实验绑定在隔离准备中核验，更新与设计同一次发布，不另写一份。
            from .experiment_design import load as load_experiment, validate as validate_experiment
            record = copy.deepcopy(load_experiment(task))
            if not record or record.get("plan_hash") != prepared.get("experiment_plan_hash"):
                raise ValueError("当前实验推荐与设计不匹配")
            bindings = validate_experiment(record, prepared)
            if bindings != record.get("bindings"):
                record["bindings"] = bindings
                record.setdefault("sync_history", []).append({"ledger_hash": prepared.get("ledger_hash"), "reason": reason})
            files["experiment_plan.json"] = json.dumps(record, ensure_ascii=False, indent=2)
        # 隔离准备期间仍持有课题锁；提交前再核验真实方案表。
        _publish_locked(task, files, expected={"scheme_ledger.json": _hash(ledger_text)})
        # 已完成发布是后续会话身份；旧调用档案保留原输入，恢复不能重复旧版本请求。
        checkpoint_path = task / "stage4_repair_checkpoint.json"
        if checkpoint_path.exists():
            state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if state.get("writer_contract_version") == 3:
                state["published_fingerprint"] = prepared["render_fingerprint"]
                state["published_ledger_hash"] = prepared["ledger_hash"]
                atomic_write(checkpoint_path, json.dumps(state, ensure_ascii=False, indent=2))
        doc.clear()
        doc.update(prepared)
    return doc
