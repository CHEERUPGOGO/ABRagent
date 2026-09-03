"""FastAPI Web 监控大屏 (只读) — AutoBatteryResearch Agent 的主 Web 入口.

架构 follow UCAgent (D:\\positive\\UCAgent) 的成熟模式:
- FastAPI + uvicorn 提供自包含 HTML 页面 (无 Jinja / 无 npm 构建 / 无前端框架水合);
- 数据全走 REST API, 前端 vanilla JS 5 秒轮询刷新;
- markdown 研报由本地内嵌的 marked.js + highlight.js 渲染 (离线可用, 不引 CDN)。

核心设计约束 (与 Gradio 版的根本区别):
- **纯只读**: 所有数据直接从磁盘解析 (output/tasks/*/.stage_state.json 等),
  绝不构造 StageManager —— 其构造器会触发全量 Checker 级联与状态双写,
  与 TUI/CLI 主流程形成竞争 (见 CLAUDE.md 与 tools/stage_tools.py 的约定);
- **跨进程天然联动**: 状态文件为原子写 (os.replace), 本服务每次请求重读,
  TUI 在另一终端推进课题时, Web 端轮询即可在秒级"续上"最新进度;
- 课题在 URL 中以任务目录名标识 (唯一且文件系统安全), 不用原始 goal 文本。

启动: abr-cli --web  (Gradio 旧版保留为 --web-gradio 后备入口)
"""

from __future__ import annotations

import json
import re
import socket
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from starlette.staticfiles import StaticFiles

# -----------------------------------------------------------------------------
# 路径常量 (测试通过 monkeypatch 模块属性指向 tmp 目录, 处理器运行时读取)
# -----------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
TASKS_ROOT = ROOT_DIR / "output" / "tasks"
GLOBAL_OUT = ROOT_DIR / "output" / "auto_battery_research"  # 仅历史遗留课题的产物回退位置
LOG_DIR = ROOT_DIR / "log"
WORKFLOW_YAML = ROOT_DIR / "auto_battery_research" / "workflow" / "abr_workflow.yaml"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

# 终态集合与 StageManager.is_all_completed 保持一致 (PASSED/SKIPPED/FALLBACK 均视为"已结束")
_TERMINAL_OK = {"PASSED", "SKIPPED", "FALLBACK"}
# 任务目录名哈希后缀 (get_task_output_dir 的 slug45_md5[:8] 命名), 无后缀即历史遗留目录
_HASH_SUFFIX_RE = re.compile(r"_[0-9a-f]{8}$")
# 运行日志文件名清洗规则 (与 TUI/Gradio 入口的 init_file_logger 命名公式一致)
_LOG_CLEAN_RE = re.compile(r'[\/:*?"<>| ]+')
# 研报候选回退链 — 单一事实源在 util/reports.py (别名保持本模块内引用稳定)
from auto_battery_research.util.reports import (
    REPORT_CANDIDATES as _REPORT_CANDIDATES,
    SCHEME_CANDIDATE as _SCHEME_CANDIDATE,
    resolve_final_report,
)
_REPORT_MAX_BYTES = 2 * 1024 * 1024  # 研报响应体上限 (超出截断并标注)
_TIPS_MAX_CHARS = 8000  # 当前阶段 Tips (参考指南文档) 的截断长度

# 工作流阶段元数据缓存 (yaml 启动时解析一次; 供无状态文件/损坏状态的课题兜底展示)
_WORKFLOW_META: Optional[Dict[str, Any]] = None


# =============================================================================
# 纯函数层: 磁盘状态解析 (单测直接覆盖, 不经过 HTTP)
# =============================================================================

def _load_workflow_meta() -> Dict[str, Any]:
    """解析 abr_workflow.yaml 的 mission 与 stages 元数据 (进程内缓存一次)."""
    global _WORKFLOW_META
    if _WORKFLOW_META is not None:
        return _WORKFLOW_META
    meta: Dict[str, Any] = {"mission": {}, "stages": []}
    try:
        import yaml
        with open(WORKFLOW_YAML, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        meta["mission"] = data.get("mission", {}) or {}
        meta["stages"] = [
            {
                "id": int(s.get("id", i + 1)),
                "key": s.get("key", ""),
                "name": s.get("name", ""),
                "description": (s.get("description") or "").strip(),
                "reference_file": (s.get("reference_files") or [""])[0],
            }
            for i, s in enumerate(data.get("stages", []) or [])
        ]
    except Exception:
        # yaml 缺失/损坏时降级为空元数据, 接口仍可用 (阶段名退化为状态文件自带字段)
        pass
    _WORKFLOW_META = meta
    return meta


def _read_task_state(task_dir: Path) -> Tuple[Optional[Dict[str, Any]], bool]:
    """读取课题的 .stage_state.json.

    返回 (state, is_error): 文件不存在 → (None, False); 存在但损坏 → (None, True)。
    原子写 (os.replace) 保证读到的永远是完整的新旧版本之一, 无需加锁。
    """
    state_file = task_dir / ".stage_state.json"
    if not state_file.exists():
        return None, False
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f), False
    except Exception:
        return None, True


def _safe_task_dir(dir_name: str) -> Optional[Path]:
    """校验课题目录名合法且落在 TASKS_ROOT 内 (防路径穿越), 非法返回 None."""
    if not dir_name or dir_name.startswith("."):
        return None
    if "/" in dir_name or "\\" in dir_name or ".." in dir_name:
        return None
    candidate = (TASKS_ROOT / dir_name).resolve()
    try:
        candidate.relative_to(TASKS_ROOT.resolve())
    except ValueError:
        return None
    return candidate


def _clean_log_name(goal: str) -> str:
    """按 TUI/CLI 的落盘命名公式生成日志文件名 (log/<清洗后课题名[:40]>.log)."""
    clean = _LOG_CLEAN_RE.sub("_", (goal or "").strip())[:40].strip("_") or "agent"
    return f"{clean}.log"


def _stage_matrix(state: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """状态文件 stages[] 与 yaml 元数据合并 → 6 阶段展示矩阵.

    状态优先 (status/skip 来自落盘进度); 缺失字段由 yaml 元数据兜底;
    两者都缺时显示 PENDING (绝不编造进度)。
    """
    meta = _load_workflow_meta()
    meta_by_id = {s["id"]: s for s in meta["stages"]}
    saved = {int(s["id"]): s for s in (state or {}).get("stages", []) if isinstance(s, dict) and "id" in s}

    ids = sorted(set(meta_by_id) | set(saved)) or list(range(1, 7))
    matrix = []
    for sid in ids:
        m = meta_by_id.get(sid, {})
        sv = saved.get(sid, {})
        matrix.append({
            "id": sid,
            "key": sv.get("key") or m.get("key", ""),
            "name": sv.get("name") or m.get("name", f"Stage {sid}"),
            "description": m.get("description", ""),
            "status": sv.get("status", "PENDING"),
            "skip": bool(sv.get("skip", False)),
        })
    return matrix


def _goal_summary(dir_name: str, task_dir: Path) -> Optional[Dict[str, Any]]:
    """单个课题目录 → 列表页摘要对象; 目录为空壳 (无状态无产物) 返回 None 跳过."""
    state, state_error = _read_task_state(task_dir)

    try:
        has_artifacts = any(task_dir.iterdir())
    except OSError:
        return None
    if state is None and not state_error and not has_artifacts:
        return None  # 完全空目录, 不进列表

    goal = (state or {}).get("target_goal") or dir_name
    stages = _stage_matrix(state)
    done = sum(1 for s in stages if s["status"] in _TERMINAL_OK)
    current_idx = (state or {}).get("current_stage_idx")
    current_stage_id = (
        min(int(current_idx) + 1, max((s["id"] for s in stages), default=6))
        if isinstance(current_idx, int) and current_idx >= 0 else 1
    )
    try:
        fallback_time = datetime.fromtimestamp(task_dir.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        fallback_time = ""
    updated_at = (state or {}).get("updated_at") or fallback_time

    return {
        "dir": dir_name,
        "goal": goal,
        "updated_at": updated_at,
        "current_stage_id": current_stage_id,
        "progress": f"{done}/{len(stages)}",
        "is_all_completed": all(s["status"] in _TERMINAL_OK for s in stages),
        "is_legacy": bool(_HASH_SUFFIX_RE.search(dir_name)) is False,
        "state_found": state is not None,
        "state_error": state_error,
        "has_report": any((task_dir / c).exists() for c in _REPORT_CANDIDATES),
        "has_scheme": (task_dir / _SCHEME_CANDIDATE).exists(),
        "stages": stages,
    }


def scan_goals() -> Dict[str, Any]:
    """扫描 TASKS_ROOT → 课题摘要列表 (按 updated_at 倒序, 最新课题在前)."""
    goals: List[Dict[str, Any]] = []
    try:
        entries = sorted(TASKS_ROOT.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        summary = _goal_summary(entry.name, entry)
        if summary is not None:
            goals.append(summary)
    goals.sort(key=lambda g: str(g.get("updated_at") or ""), reverse=True)
    return {"count": len(goals), "goals": goals}


def _resolve_report(task_dir: Path, is_legacy: bool) -> Tuple[Optional[Path], str]:
    """研报回退链: 委托 util/reports.resolve_final_report (单一事实源);
    历史遗留课题再回退到全局 output/auto_battery_research/."""
    p = resolve_final_report(task_dir, is_legacy=is_legacy, scheme_fallback=True, global_dir=GLOBAL_OUT)
    if p is None:
        return None, ""
    name = p.name
    if p.parent == GLOBAL_OUT:
        name = f"(全局回退) {name}"
    return p, name


def _read_report_text(path: Path) -> Tuple[str, int]:
    """读取研报文本 (UTF-8), 超过 _REPORT_MAX_BYTES 截断并显式标注, 绝不静默丢尾."""
    data = path.read_bytes()
    truncated = len(data) > _REPORT_MAX_BYTES
    text = data[:_REPORT_MAX_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += "\n\n---\n\n> ⚠️ 研报超过 2MB, 此处仅展示前 2MB 内容, 完整内容请查看原始文件。"
    return text, len(data)


def _tail_log(goal: str, tail: int) -> Dict[str, Any]:
    """读取 log/<课题清洗名>.log 的尾部 N 行 (TUI/CLI 运行时的实时执行日志)."""
    log_file = LOG_DIR / _clean_log_name(goal)
    if not log_file.exists():
        return {"found": False, "path": "", "lines": 0, "text": ""}
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return {"found": False, "path": "", "lines": 0, "text": ""}
    tail_lines = lines[-max(1, min(tail, 2000)):]
    return {
        "found": True,
        "path": str(log_file.relative_to(ROOT_DIR)) if LOG_DIR.is_relative_to(ROOT_DIR) else str(log_file),
        "lines": len(tail_lines),
        "text": "".join(tail_lines),
    }


def _tips_md(current_stage_id: int) -> str:
    """当前阶段的任务指南 (参考文档 excerpt); 文档缺失时退化为 yaml 描述."""
    meta = _load_workflow_meta()
    for s in meta["stages"]:
        if s["id"] == current_stage_id:
            ref = ROOT_DIR / s["reference_file"] if s["reference_file"] else None
            if ref and ref.exists():
                try:
                    text = ref.read_text(encoding="utf-8")
                    return text[:_TIPS_MAX_CHARS] + ("…" if len(text) > _TIPS_MAX_CHARS else "")
                except OSError:
                    pass
            return s.get("description", "")
    return ""


def _asset_stats() -> Dict[str, Any]:
    """文献资产统计条 (只读 glob 计数; 逻辑对齐 workflow_actions._merged_literature_dirs:
    合并文献 = 规范 papers/merged + 遗留 papers/text_merged 双目录合并计数)."""
    import glob as _glob

    def _count(pattern: str) -> int:
        return len(_glob.glob(str(ROOT_DIR / pattern), recursive=True))

    merged = _count("papers/merged/**/*.md") + _count("papers/text_merged/**/*.md")
    meta_len = 0
    for cand in ("miner/json/meta_merged.json", "miner/json/metadata/meta_merged.json"):
        p = ROOT_DIR / cand
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    meta_len = len(json.load(f))
                break
            except Exception:
                pass
    return {
        "pdf_papers": _count("papers/pdf/**/*.pdf"),
        "merged_markdown": merged,
        "classified_database": _count("database/type/**/*.md"),
        "extracted_cells": _count("miner/json/100/*.json"),
        "metadata_index": meta_len,
    }


# =============================================================================
# FastAPI 应用与只读路由
# =============================================================================

app = FastAPI(
    title="AutoBatteryResearch Agent Web Monitor",
    description="只读监控大屏: 课题进度 / 综合研报 / 阶段日志 / 运行日志 (数据直读磁盘, 与 TUI/CLI 天然联动)",
    version="1.0.0",
)

if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse, summary="监控大屏页面")
def index():
    html_path = _TEMPLATES_DIR / "index.html"
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(
            "<h2>AutoBatteryResearch Agent Web Monitor</h2>"
            "<p>页面模板缺失, 请检查 auto_battery_research/web/templates/index.html</p>"
            "<p>API 文档: <a href='/docs'>/docs</a></p>",
            status_code=500,
        )


@app.get("/api/health", summary="健康检查")
def health():
    return {"status": "ok", "service": "abr-web-monitor", "time": datetime.now().isoformat(timespec="seconds")}


@app.get("/api/goals", summary="课题列表 (按更新时间倒序)")
def api_goals():
    return scan_goals()


@app.get("/api/goal/{dir_name}/status", summary="单课题详情: 阶段矩阵 + 当前阶段 Tips")
def api_goal_status(dir_name: str):
    task_dir = _safe_task_dir(dir_name)
    if task_dir is None or not task_dir.is_dir():
        return {"error": f"课题目录不存在或非法: {dir_name}"}
    summary = _goal_summary(dir_name, task_dir)
    if summary is None:
        return {"error": f"课题目录为空: {dir_name}"}
    summary["mission"] = _load_workflow_meta().get("mission", {})
    summary["tips_md"] = _tips_md(summary["current_stage_id"])
    return summary


@app.get("/api/goal/{dir_name}/report", summary="综合研报内容 (markdown, 带回退链)")
def api_goal_report(dir_name: str, download: int = Query(0, description="1=以附件形式下载原文")):
    task_dir = _safe_task_dir(dir_name)
    if task_dir is None or not task_dir.is_dir():
        return {"found": False, "error": f"课题目录不存在或非法: {dir_name}"}
    summary = _goal_summary(dir_name, task_dir)
    if summary is None:
        return {"found": False, "error": f"课题目录为空: {dir_name}"}
    path, source = _resolve_report(task_dir, summary["is_legacy"])
    if path is None:
        return {"found": False, "goal": summary["goal"], "source": "", "markdown": ""}
    text, nbytes = _read_report_text(path)
    if download:
        filename = path.name
        return PlainTextResponse(
            content=text,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )
    return {"found": True, "goal": summary["goal"], "source": source, "bytes": nbytes, "markdown": text}


@app.get("/api/goal/{dir_name}/journals", summary="阶段研发日志 (stage_journals.json)")
def api_goal_journals(dir_name: str):
    task_dir = _safe_task_dir(dir_name)
    if task_dir is None or not task_dir.is_dir():
        return {"found": False, "journals": []}
    jf = task_dir / "stage_journals.json"
    if not jf.exists():
        return {"found": False, "journals": []}
    try:
        with open(jf, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"found": True, "journals": data if isinstance(data, list) else [data]}
    except Exception:
        return {"found": False, "journals": [], "error": "stage_journals.json 解析失败"}


@app.get("/api/goal/{dir_name}/log", summary="运行日志尾部 (TUI/CLI 实时执行日志)")
def api_goal_log(dir_name: str, tail: int = Query(200, ge=1, le=2000)):
    task_dir = _safe_task_dir(dir_name)
    if task_dir is None or not task_dir.is_dir():
        return {"found": False, "lines": 0, "text": ""}
    state, _ = _read_task_state(task_dir)
    goal = (state or {}).get("target_goal") or dir_name
    result = _tail_log(goal, tail)
    result["goal"] = goal
    return result


@app.get("/api/goal/{dir_name}/files", summary="课题产物文件清单")
def api_goal_files(dir_name: str):
    task_dir = _safe_task_dir(dir_name)
    if task_dir is None or not task_dir.is_dir():
        return {"files": []}
    files = []
    try:
        for entry in sorted(task_dir.iterdir(), key=lambda e: e.name):
            try:
                st = entry.stat()
                files.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size": st.st_size if entry.is_file() else None,
                    "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                })
            except OSError:
                continue
    except OSError:
        pass
    return {"dir": dir_name, "files": files}


@app.get("/api/stats", summary="文献资产统计")
def api_stats():
    return _asset_stats()


# =============================================================================
# 启动器 (端口占用自动回退, 逻辑对齐 Gradio 版 app.py 的探测方式)
# =============================================================================

def _port_free(check_host: str, check_port: int) -> bool:
    # 注意不要设置 SO_REUSEADDR: Windows 下它允许重复绑定, 会误判端口空闲
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((check_host, check_port))
            return True
    except OSError:
        return False


def probe_monitor_health(host: str = "127.0.0.1", port: int = 7865, timeout: float = 2.0) -> bool:
    """探测目标端口上是否已有本监控服务健康实例 (供 TUI/CLI 复用而非重复启动)."""
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=timeout) as r:
            return "abr-web-monitor" in r.read().decode("utf-8", "replace")
    except Exception:
        return False


def launch_fastapi_web_server(
    manager: Optional[Any] = None,
    host: str = "127.0.0.1",
    port: int = 7865,
    share: bool = False,
) -> None:
    """启动只读 Web 监控大屏.

    - manager 参数仅为兼容 TUI/CLI 旧调用签名而保留: 本服务纯读磁盘、
      不触碰任何 StageManager 运行态 (构造它会触发 Checker 级联与状态双写);
    - 默认仅监听本地回环 (局域网访问需显式传 host="0.0.0.0");
    - 端口被残留进程占用时自动顺延探测空闲端口 (port+1 .. port+10)。
    """
    import sys
    import uvicorn

    # Windows 控制台默认 GBK: 入口统一重配 UTF-8 (沿用 cli.py 的模式, 带防护)
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    actual_port = port
    if not _port_free(host, port):
        print(f"⚠️ 端口 {port} 已被占用 (大概率有上一次 Web 会话的残留进程未退出)...")
        for cand in range(port + 1, port + 11):
            if _port_free(host, cand):
                actual_port = cand
                break
        if actual_port == port:
            print(f"❌ 端口 {port}~{port + 10} 均被占用：请关闭残留进程后重试，或用 --port 指定其他端口。")
            return
        print(f"👉 已自动切换到空闲端口: {actual_port} (请以新地址访问，旧标签页可能已失效)")

    display_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    print("\n🌐 启动 AutoBatteryResearch Agent Web 监控大屏 (FastAPI 只读版)...")
    print(f"👉 本地访问地址: http://{display_host}:{actual_port}")
    print("   · 课题进度 / 综合研报 / 阶段日志 / 运行日志 均直读磁盘, TUI/CLI 运行中可实时联动")
    print("   · API 文档: http://%s:%s/docs" % (display_host, actual_port))
    uvicorn.run(app, host=host, port=actual_port, log_level="warning")
