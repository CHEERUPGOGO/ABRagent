"""环境自检模块 (abr-cli --doctor) — 无网络强依赖的一次性体检.

检查项: Python 版本 / .env / LLM Key 与端点 / Ollama 与向量模型 / MinerU Token /
文献资产 / ReAct 运行时 / 可选依赖 / PINN 模型资产 / Materials Project MCP / 输出目录写权限。
全部离线可跑 (Ollama 探测失败仅降级为 WARN)。
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

# 可选依赖 extras: (展示名, 模块名, 安装提示) — pyproject optional-dependencies 的镜像
_OPTIONAL_EXTRAS: Tuple[Tuple[str, str, str], ...] = (
    ("Chroma 向量库 [rag]", "chromadb", "pip install -e '.[rag]'"),
    ("Ollama 客户端 [rag]", "ollama", "pip install -e '.[rag]'"),
    ("Textual TUI [ui]", "textual", "pip install -e '.[ui]'"),
    ("Gradio Web [ui]", "gradio", "pip install -e '.[ui]'"),
)


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


def _agent_runtime_row() -> Tuple[str, str, str, str]:
    """检查 ReAct 智能体运行时 (langchain>=1.0 / langgraph>=1.0，均为 base 依赖).

    backend._compile_agent_for_tools 的降级链为 create_agent → create_react_agent
    → 裸 bind_tools 模型。二者同时缺失时会落到第三级，而 invoke 仍按 LangGraph
    dict 协议传 {"messages": [...]}，裸模型只接受 str/list[BaseMessage] → 每阶段
    必抛 [LLM-Notice] Invalid input type <class 'dict'>，LLM 决策全部降级确定性
    调度。该项让该症状在自检阶段即可定位。
    """
    errors = []
    try:
        from langchain.agents import create_agent  # noqa: F401
    except Exception as e:
        errors.append(f"langchain.agents.create_agent 不可用 ({e})")
    try:
        from langgraph.prebuilt import create_react_agent  # noqa: F401
    except Exception as e:
        errors.append(f"langgraph.prebuilt.create_react_agent 不可用 ({e})")
    if not errors:
        return ("ReAct 运行时", OK, "langchain / langgraph 就绪", "")
    if len(errors) == 2:
        return ("ReAct 运行时", WARN, "langchain 与 langgraph 均缺失",
                "执行 pip install -e . 重装 (base 依赖已含二者)；缺失时 LLM 决策必失败并降级确定性调度 "
                "(日志特征: [LLM-Notice] Invalid input type <class 'dict'>)")
    return ("ReAct 运行时", WARN, errors[0],
            "将回退另一入口编译 ReAct，建议对齐锁定版本: pip install -e \".[all]\" -c requirements-lock.txt")


def _pinn_row(pinn_trigger: Optional[Dict] = None, models_dir: Optional[Path] = None) -> Tuple[str, str, str, str]:
    """检查 Stage 5 PINN 仿真环境 (纯 numpy SPM 前向，无 TF/GPU 依赖).

    numpy 是 base 依赖，缺失判 FAIL (PINN 完全无法运行)；registry.json 缺失/
    损坏/为空或某体系资产残缺 (params.json / discharge 权重 npz) 判 WARN ——
    运行期对未命中场景平滑回退参数提取-only，不视为致命。charge 权重为预留位
    (null) 不检查；pinn_trigger 总开关是有意配置，只反映在 detail 不影响状态。
    路径解析与运行期同源 (registry 内路径相对 MODELS_DIR)，优先复用
    pinn.spm_runner.MODELS_DIR 以兼容 wheel 布局；models_dir 可注入以便测试隔离。
    """
    trigger_on = bool((pinn_trigger or {}).get("enabled", False))

    # 1. numpy 可导入性 (base 依赖)
    try:
        import numpy  # noqa: F401
    except Exception as e:
        return ("PINN 仿真环境", FAIL, f"numpy 不可用: {e}",
                "执行 pip install -e '.' 重装 base 依赖 (PINN 前向完全依赖 numpy)")

    # 2. 模型目录: 与运行期同源，兼容源码仓与 wheel 两种布局
    if models_dir is None:
        try:
            from pinn.spm_runner import MODELS_DIR as _runtime_models_dir
            models_dir = Path(_runtime_models_dir)
        except Exception:
            models_dir = ROOT_DIR / "pinn" / "models"

    registry_file = models_dir / "registry.json"
    if not registry_file.exists():
        return ("PINN 仿真环境", WARN, f"未找到 {registry_file}",
                "模型资产随 wheel 分发: 重装 pip install -e '.[all]'，或检查 pinn/models/ 目录")
    try:
        import json
        registry = json.loads(registry_file.read_text(encoding="utf-8")) or {}
        systems = registry.get("systems") or []
    except Exception as e:
        return ("PINN 仿真环境", WARN, f"registry.json 解析失败: {e}",
                "修正 pinn/models/registry.json (Stage 5 将回退参数提取-only)")
    if not systems:
        return ("PINN 仿真环境", WARN, "registry.json 未声明任何已训练体系",
                "Stage 5 将对全部方案回退参数提取-only")

    # 3. 逐体系资产完整性 (提前暴露"模型目录只拷一半"，否则运行期匹配命中才失败)
    incomplete = []
    for sys_def in systems:
        sid = str(sys_def.get("system_id", "?"))
        missing = []
        params_rel = sys_def.get("params")
        if params_rel and not (models_dir / params_rel).exists():
            missing.append(str(params_rel))
        discharge = (sys_def.get("directions") or {}).get("discharge") or {}
        for side in ("ne", "pe"):
            w_rel = discharge.get(side)
            if w_rel and not (models_dir / w_rel).exists():
                missing.append(str(w_rel))
        if missing:
            incomplete.append(f"{sid} (缺 {'、'.join(missing)})")

    trigger_str = "trigger=开启" if trigger_on else "总开关关闭 (仅参数提取)"
    ids = ", ".join(str(s.get("system_id", "?")) for s in systems)
    if incomplete:
        return ("PINN 仿真环境", WARN,
                f"声明 {len(systems)} 个体系，资产残缺: {'; '.join(incomplete)}",
                "补齐缺失文件或从 registry.json 移除该体系")
    return ("PINN 仿真环境", OK, f"{len(systems)} 个已训练体系 ({ids}) · {trigger_str}", "")


def run_doctor_checks() -> List[Tuple[str, str, str, str]]:
    """执行全部自检，返回 (项目, 状态, 详情, 修复建议) 列表."""
    cfg = _load_setting_light()
    results: List[Tuple[str, str, str, str]] = []

    # 1. Python 版本 (Stage 5 PINN 为纯 numpy 前向，无 PyBaMM 时代的 <3.13 硬限制)
    py = sys.version_info
    if py < (3, 10):
        results.append(("Python 版本", FAIL, platform.python_version(), "需要 Python >= 3.10"))
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
    chroma_count = 0
    if chroma_dir.exists():
        sqlite_file = chroma_dir / "chroma.sqlite3"
        if sqlite_file.exists():
            try:
                import sqlite3
                with sqlite3.connect(str(sqlite_file)) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT count(*) FROM embeddings")
                    row = cur.fetchone()
                    chroma_count = int(row[0]) if row else 0
            except Exception:
                pass
    chroma_ok = chroma_count > 0
    if pdf_n or md_n or db_n or chroma_ok:
        chroma_str = f"✓ ({chroma_count:,} 条)" if chroma_ok else "✗ (0条/未索引)"
        detail = f"PDF {pdf_n} 篇 · 合并 MD {md_n} 篇 · 分类库 {db_n} 篇 · 向量库{chroma_str}"
        results.append(("文献资产", OK, detail, ""))
    else:
        results.append(("文献资产", WARN, "未检测到任何文献资产",
                        "放入 PDF 至 papers/pdf/ 并配置 MinerU Token 或导入向量资产；否则 Stage 1 将诚实失败"))

    # 8. ReAct 智能体运行时 (langchain / langgraph)
    results.append(_agent_runtime_row())

    # 9. 可选依赖 (extras)
    for label, module, extra in _OPTIONAL_EXTRAS:
        try:
            __import__(module)
            results.append((label, OK, "已安装", ""))
        except ImportError:
            results.append((label, WARN, "未安装", extra))

    # Materials Project 官方 MCP [mp]
    try:
        from auto_battery_research.tools.materials_project import MaterialsProjectClient
        mp_client = MaterialsProjectClient()
        if mp_client.enabled and mp_client.api_key:
            py_name = Path(mp_client.python).parent.name if Path(mp_client.python).parent.name != "Scripts" else Path(mp_client.python).parent.parent.name
            results.append(("Materials Project [mp]", OK, f"已就绪 (Key 已配置 · {py_name})", ""))
        elif mp_client.enabled:
            results.append(("Materials Project [mp]", WARN, "未配置 API Key", "在 .env 中设置 MP_API_KEY (从 materialsproject.org/api 获取)"))
        else:
            results.append(("Materials Project [mp]", OK, "已禁用 (MP_MCP_ENABLED=false)", ""))
    except Exception as e:
        results.append(("Materials Project [mp]", WARN, f"未就绪: {e}", "pip install -e '.[mp]'"))

    # PINN 仿真环境 (Stage 5 默认启用: 纯 numpy SPM 前向)
    results.append(_pinn_row(cfg.get("pinn_trigger") or {}))

    # 10. 输出目录写权限
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
