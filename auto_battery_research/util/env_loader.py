"""极简 .env 加载器 — 零第三方依赖 (AutoBatteryResearch Agent).

优先级约定 (与 setting.yaml 的 `$(ENV_VAR:默认值)` 插值配合):
    已有系统环境变量 > .env 文件 > setting.yaml 内置默认值

即 .env 不覆盖已导出的环境变量；文件不存在时静默跳过，不影响离线运行。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

_loaded_paths = set()


def load_env(env_file: Optional[Path] = None, override: bool = False) -> bool:
    """解析 .env 并注入 os.environ (幂等；默认不覆盖已有环境变量).

    Returns:
        bool: 本次是否实际读取并注入了变量。
    """
    global _loaded_paths
    path = Path(env_file) if env_file else ROOT_DIR / ".env"
    if not path.exists() or path in _loaded_paths:
        return False

    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception:
        return False

    injected = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if val[:1] in ("'", '"'):
            # 带引号值：取首个配对引号内部，其后内容 (含行内注释) 一并忽略
            end = val.find(val[0], 1)
            if end != -1:
                val = val[1:end]
        elif " #" in val:
            # 无引号值：剥离行内注释
            val = val.split(" #", 1)[0].rstrip()
        if override or key not in os.environ:
            os.environ[key] = val
            injected = True

    # 若在 WSL 环境下运行且 localhost:11434 不通，自动桥接至 Windows 宿主机 Ollama 端口
    wsl_ip = resolve_wsl_host_ip()
    if wsl_ip:
        curr_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        if curr_url in ("http://localhost:11434", "http://127.0.0.1:11434"):
            import urllib.request
            try:
                with urllib.request.urlopen(f"{curr_url}/api/tags", timeout=0.4) as _:
                    pass
            except Exception:
                try:
                    with urllib.request.urlopen(f"http://{wsl_ip}:11434/api/tags", timeout=0.8) as _:
                        os.environ["OLLAMA_BASE_URL"] = f"http://{wsl_ip}:11434"
                except Exception:
                    pass

    _loaded_paths.add(path)
    return injected


def resolve_wsl_host_ip() -> Optional[str]:
    """若在 WSL 环境下运行，自动探测 Windows 宿主机的网关 IP (nameserver)."""
    try:
        if os.path.exists("/proc/version"):
            with open("/proc/version", "r", encoding="utf-8", errors="ignore") as f:
                if "microsoft" in f.read().lower():
                    if os.path.exists("/etc/resolv.conf"):
                        with open("/etc/resolv.conf", "r", encoding="utf-8", errors="ignore") as rf:
                            for line in rf:
                                if line.startswith("nameserver"):
                                    return line.split()[1].strip()
    except Exception:
        pass
    return None
