"""FileTools — 安全文本与文件操作工具库 (带 Workspace 路径沙箱隔离)."""

import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

_DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent


def validate_workspace_path(
    filepath: str,
    workspace_root: Optional[Path] = None
) -> Tuple[bool, Optional[Path], Optional[str]]:
    """验证目标文件路径是否严格处于指定 workspace 沙箱边界内，彻底杜绝跨目录路径遍历攻击."""
    root = (workspace_root or _DEFAULT_WORKSPACE_ROOT).resolve()
    
    try:
        p = Path(filepath)
        if not p.is_absolute():
            resolved = (root / p).resolve()
        else:
            resolved = p.resolve()
            
        # 严格校验 resolved 路径是否位于 root 内部或为其自身
        try:
            resolved.relative_to(root)
            return True, resolved, None
        except ValueError:
            return False, None, f"安全拦截：目标路径 '{filepath}' 超出 Workspace 沙箱边界 '{root}'"
    except Exception as e:
        return False, None, f"路径解析异常: {str(e)}"


def read_text_file(
    filepath: str,
    count: int = -1,
    start_line: int = 1,
    workspace_root: Optional[Path] = None
) -> Dict[str, Any]:
    """安全读取指定文本文件内容 (受沙箱约束)."""
    valid, safe_path, err = validate_workspace_path(filepath, workspace_root)
    if not valid:
        return {"success": False, "error": err}
        
    p = safe_path
    if not p.exists():
        return {"success": False, "error": f"文件不存在: {p}"}
    if not p.is_file():
        return {"success": False, "error": f"路径不是文件: {p}"}
        
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        if start_line > total_lines:
            return {"success": True, "content": "", "total_lines": total_lines, "lines_read": 0}

        start_idx = max(0, start_line - 1)
        if count == 0:
            return {"success": True, "content": "[登记已读，未加载正文]", "total_lines": total_lines, "lines_read": 0}
        elif count > 0:
            selected = lines[start_idx : start_idx + count]
        else:
            selected = lines[start_idx:]

        return {
            "success": True,
            "filepath": str(p),
            "total_lines": total_lines,
            "lines_read": len(selected),
            "content": "".join(selected),
        }
    except Exception as e:
        return {"success": False, "error": f"读取文件失败 ({p}): {str(e)}"}


def edit_text_file(
    filepath: str,
    content: str,
    append: bool = False,
    workspace_root: Optional[Path] = None
) -> Dict[str, Any]:
    """安全创建、覆盖或追加写入文本文件 (受沙箱约束)."""
    valid, safe_path, err = validate_workspace_path(filepath, workspace_root)
    if not valid:
        return {"success": False, "error": err}
        
    p = safe_path
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(p, mode, encoding="utf-8") as f:
            f.write(content)
        return {
            "success": True,
            "filepath": str(p),
            "bytes_written": len(content.encode("utf-8")),
            "mode": "append" if append else "overwrite",
        }
    except Exception as e:
        return {"success": False, "error": f"写入文件失败 ({p}): {str(e)}"}


def replace_string_in_file(
    filepath: str,
    old_string: str,
    new_string: str,
    workspace_root: Optional[Path] = None
) -> Dict[str, Any]:
    """在文本文件中精确替换字符串 (受沙箱约束)."""
    valid, safe_path, err = validate_workspace_path(filepath, workspace_root)
    if not valid:
        return {"success": False, "error": err}
        
    p = safe_path
    if not p.exists():
        return {"success": False, "error": f"文件不存在: {p}"}
    try:
        with open(p, "r", encoding="utf-8") as f:
            content = f.read()
        
        if old_string not in content:
            return {"success": False, "error": f"未在文件内找到待替换目标字符串: {old_string[:100]}"}

        count = content.count(old_string)
        new_content = content.replace(old_string, new_string)
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_content)

        return {
            "success": True,
            "filepath": str(p),
            "replaced_count": count,
        }
    except Exception as e:
        return {"success": False, "error": f"替换文件内容失败 ({p}): {str(e)}"}
