"""环境自检模块 (abr-cli --doctor) — 无网络强依赖的一次性体检.

检查项: Python 版本 / .env / LLM Key 与端点 / Ollama 与向量模型 / MinerU Token /
文献资产 / 可选依赖 / 输出目录写权限。全部离线可跑 (Ollama 探测失败仅降级为 WARN)。
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.request import urlopen
from urllib.error import URLError

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_ICONS = {OK: "[green]✅[/green]", WARN: "[yellow]⚠️ [/yellow]", FAIL: "[red]❌[/red]"}


def _load_setting_light() -> Dict:
    """轻量读取 setting.yaml (含 `$(VAR:default)` 插值)，不触发 StageManager 的 Checker 级联."""
    import yaml

    cfg_path = ROOT_DIR / "auto_battery_research" / "setting.yaml"
    if not cfg_path.exists():
        return {}
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

    def _interp(v):
        if isinstance(v, str) and v.startswith("$(") and v.endswith(")"):
            inner = v[2:-1]
            if ":" in inner:
                var, _, default = inner.partition(":")
                return os.environ.get(var.strip(), default.strip())
            return os.environ.get(inner.strip(), "")
        return v

    def _walk(node):
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(x) for x in node]
        return _interp(node)

    return _walk(raw)


def _mask(secret: str) -> str:
    if not secret:
        return "(空)"
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:6]}...{secret[-4:]} (已隐藏)"


def _count(pattern: str) -> int:
    import glob as _glob
    return len(_glob.glob(pattern, recursive=True))


def run_doctor_checks() -> List[Tuple[str, str, str, str]]:
    """执行全部自检，返回 (项目, 状态, 详情, 修复建议) 列表."""
    cfg = _load_setting_light()
    results: List[Tuple[str, str, str, str]] = []

    # 1. Python 版本
    py = sys.version_info
    if py < (3, 10):
        results.append(("Python 版本", FAIL, platform.python_version(), "需要 Python >= 3.10"))
    elif py >= (3, 13):
        results.append((
            "Python 版本", WARN, platform.python_version(),
            ">= 3.13 无法安装 PyBaMM，Stage 5 物理仿真不可用 (其余功能正常)",
        ))
    else:
        results.append(("Python 版本", OK, platform.python_version(), ""))

    # 2. .env 文件
    env_path = ROOT_DIR / ".env"
    if env_path.exists():
        results.append((".env 配置", OK, f"{env_path.name} 已存在 (变量已注入)", ""))
    else:
        results.append((".env 配置", WARN, "未创建 (依赖系统环境变量或 setting.yaml 默认值)",
                        "可复制 .env.example 为 .env 填写密钥"))

    # 3. LLM API Key
    key = (os.environ.get("OPENAI_API_KEY")
           or (cfg.get("openai") or {}).get("openai_api_key")
           or (cfg.get("llm") or {}).get("api_key")
           or "")
    key = str(key).strip()
    if key and key not in ("dummy_key",):
        results.append(("LLM API Key", OK, f"{_mask(key)} · 来源: {'环境变量' if os.environ.get('OPENAI_API_KEY') else 'setting.yaml'}", ""))
    else:
        results.append(("LLM API Key", WARN, "未配置", "将进入确定性离线流水线模式 (门禁仍可推进)；配置 OPENAI_API_KEY 启用 ReAct 主控"))

    # 4. LLM 端点与模型
    base = (os.environ.get("OPENAI_API_BASE") or (cfg.get("llm") or {}).get("base_url")
            or (cfg.get("openai") or {}).get("openai_api_base") or "https://api.minimaxi.com/v1")
    model = (os.environ.get("OPENAI_MODEL") or (cfg.get("llm") or {}).get("model")
             or (cfg.get("openai") or {}).get("model_name") or "MiniMax-M2.7-highspeed")
    results.append(("LLM 端点", OK if key else WARN, f"{model} @ {base}", ""))

    # 5. Ollama 向量服务
    emb_cfg = cfg.get("embedding") or {}
    ollama_base = str(emb_cfg.get("ollama_base_url", "http://localhost:11434")).rstrip("/")
    emb_model = str(emb_cfg.get("model", "qwen3-embedding:8b"))
    try:
        with urlopen(f"{ollama_base}/api/tags", timeout=2.5) as resp:
            tags = json_loads_safe(resp.read().decode("utf-8", "replace"))
        names = [m.get("name", "") for m in (tags.get("models") or [])]
        if any(n.startswith(emb_model.split(":")[0]) for n in names):
            results.append(("Ollama 向量服务", OK, f"{ollama_base} · 已就绪，含 {emb_model}", ""))
        else:
            results.append(("Ollama 向量服务", WARN, f"{ollama_base} 在线但缺少 {emb_model} (现有: {', '.join(names[:5]) or '无'})",
                            f"执行: ollama pull {emb_model}"))
    except (URLError, OSError, TimeoutError):
        results.append(("Ollama 向量服务", WARN, f"{ollama_base} 不可达", "Stage 2/4 检索将降级 TF-IDF/BM25；启动: ollama serve"))
    except Exception as e:
        results.append(("Ollama 向量服务", WARN, f"探测异常: {e}", ""))

    # 6. MinerU Token (仅 Stage 1 新增 PDF 解析需要)
    mineru_token = os.environ.get("MINERU_TOKEN", "")
    if not mineru_token:
        pre_cfg = ROOT_DIR / "preprocessing" / "config.yaml"
        if pre_cfg.exists():
            try:
                import yaml
                mc = yaml.safe_load(pre_cfg.read_text(encoding="utf-8")) or {}
                mineru_token = str(((mc.get("mineru") or {}).get("token")) or "")
            except Exception:
                pass
    if mineru_token:
        results.append(("MinerU 云解析 Token", OK, _mask(mineru_token), ""))
    else:
        results.append(("MinerU 云解析 Token", WARN, "未配置", "仅新增 PDF 解析需要 (已有文献资产不触发)；设置 MINERU_TOKEN 或 preprocessing/config.yaml mineru.token"))

    # 7. 文献资产
    pdf_n = _count(str(ROOT_DIR / "papers/pdf/**/*.pdf"))
    merged_dirs = [ROOT_DIR / "papers/merged", ROOT_DIR / "papers/text_merged"]
    md_n = sum(_count(str(d / "**/*.md")) for d in merged_dirs)
    db_n = _count(str(ROOT_DIR / "database/type/**/*.md"))
    chroma_dir = ROOT_DIR / "miner/chroma/paragraphs_q"
    chroma_ok = chroma_dir.exists() and any(chroma_dir.iterdir())
    if pdf_n or md_n or db_n:
        detail = f"PDF {pdf_n} 篇 · 合并 MD {md_n} 篇 · 分类库 {db_n} 篇 · 向量库{'✓' if chroma_ok else '✗'}"
        results.append(("文献资产", OK, detail, ""))
    else:
        results.append(("文献资产", WARN, "未检测到任何文献资产",
                        "放入 PDF 至 papers/pdf/ 并配置 MinerU Token；否则 Stage 1 将诚实失败"))

    # 8. 可选依赖 (extras)
    for label, module, extra in (
        ("Chroma 向量库 [rag]", "chromadb", "pip install -e '.[rag]'"),
        ("Ollama 客户端 [rag]", "ollama", "pip install -e '.[rag]'"),
        ("Textual TUI [ui]", "textual", "pip install -e '.[ui]'"),
        ("Gradio Web [ui]", "gradio", "pip install -e '.[ui]'"),
        ("PyBaMM 物理 [physics]", "pybamm", "pip install -e '.[physics]' (需 Python < 3.13)"),
    ):
        try:
            __import__(module)
            results.append((label, OK, "已安装", ""))
        except ImportError:
            results.append((label, WARN, "未安装", extra))

    # 9. 输出目录写权限
    try:
        out_dir = ROOT_DIR / "output" / "tasks"
        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=out_dir, delete=True, suffix=".doctor"):
            pass
        results.append(("输出目录写权限", OK, str(out_dir.relative_to(ROOT_DIR)), ""))
    except Exception as e:
        results.append(("输出目录写权限", FAIL, f"不可写: {e}", "检查 output/ 目录权限"))

    return results


def json_loads_safe(text: str) -> Dict:
    import json
    return json.loads(text) if text else {}


def print_doctor_report() -> int:
    """渲染自检报告，返回 FAIL 项数量."""
    results = run_doctor_checks()
    n_fail = sum(1 for _, s, _, _ in results if s == FAIL)
    n_warn = sum(1 for _, s, _, _ in results if s == WARN)

    try:
        from rich.console import Console
        from rich.table import Table
        table = Table(title="🩺 AutoBatteryResearch Agent 环境自检 (--doctor)", show_lines=False)
        table.add_column("检查项", style="bold")
        table.add_column("状态", justify="center")
        table.add_column("详情")
        table.add_column("修复建议", style="dim")
        for name, st, detail, hint in results:
            table.add_row(name, _ICONS[st], detail, hint or "-")
        console = Console()
        console.print(table)
        summary = f"共 {len(results)} 项: ✅ {len(results) - n_warn - n_fail} 通过 · ⚠️  {n_warn} 提示 · ❌ {n_fail} 失败"
        console.print(f"[bold]{summary}[/bold]")
        if n_warn:
            console.print("[dim]提示项不阻塞运行 (均有降级路径)；失败项需要处理后才能正常工作。[/dim]")
    except ImportError:
        print("=" * 70)
        print("AutoBatteryResearch Agent 环境自检 (--doctor)")
        print("=" * 70)
        for name, st, detail, hint in results:
            icon = {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}[st]
            print(f"{icon} {name}: {detail}" + (f"  -> {hint}" if hint else ""))
        print(f"共 {len(results)} 项: {len(results) - n_warn - n_fail} 通过 / {n_warn} 提示 / {n_fail} 失败")

    return n_fail
