"""
Meta 数据爬取模块 - 从 merged/markdown 文件夹中的 .md 文件提取元数据。

提取内容：
- 题目 (title)
- 作者 (authors)
- 出版时间 (publication_date)
- DOI (从内容正则匹配；匹配不到则用文件名去掉 .md)

特性：
- 支持递归扫描子目录（如 242/、example/ 等）
- 自动检测数据源：根据 config.yaml 的 auto_merge 决定优先使用 merged 还是 markdown
- 输出 JSON 到 miner/json/ 目录

用法：
    # 自动检测（优先 merged，不存在则 markdown）
    python -m miner.meta_extraction.extract_meta

    # 指定文件夹
    python -m miner.meta_extraction.extract_meta papers/merged/242
"""

import os
import re
import json
import yaml
from typing import Dict, Optional, List, Tuple
from pathlib import Path

# ==================== 路径配置 ====================

# 项目根目录（相对此文件：miner/meta_extraction/ -> ../../）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "preprocessing" / "config.yaml"
OUTPUT_DIR = PROJECT_ROOT / "miner" / "json"

# 默认的 merged / markdown 根目录
MERGED_ROOT = PROJECT_ROOT / "papers" / "merged"
MARKDOWN_ROOT = PROJECT_ROOT / "papers" / "markdown"


# ==================== 辅助函数 ====================

def load_config() -> dict:
    """加载 preprocessing/config.yaml 配置"""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"未找到配置文件 {CONFIG_PATH}，使用默认值")
        return {}
    except Exception as e:
        print(f"读取配置文件失败: {e}，使用默认值")
        return {}


def detect_input_source() -> Tuple[str, str]:
    """
    根据 config.yaml 自动检测数据源。

    Returns:
        (input_folder, source_label)  — 如 ("papers/merged", "merged (auto_merge=True)")
    """
    config = load_config()
    processing = config.get("processing", {})
    auto_merge = processing.get("auto_merge", False)
    paths_config = config.get("paths", {})

    # 使用固定的 papers/merged 和 papers/markdown 作为数据根目录
    # （config.yaml 中 paths.merged_root 可能指向子目录如 example，仅用于 merge 工具）
    merged_path = PROJECT_ROOT / "papers" / "merged"
    markdown_path = PROJECT_ROOT / "papers" / "markdown"

    merged_str = str(merged_path)
    markdown_str = str(markdown_path)

    merged_has = os.path.isdir(merged_str) and _has_md_files(merged_str)
    markdown_has = os.path.isdir(markdown_str) and _has_md_files(markdown_str)

    if auto_merge and merged_has:
        return merged_str, "merged (auto_merge=True)"
    if merged_has:
        return merged_str, "merged (文件存在)"
    if markdown_has:
        return markdown_str, "markdown (merged 为空)"
    if os.path.isdir(merged_str):
        return merged_str, "merged (空目录)"
    if os.path.isdir(markdown_str):
        return markdown_str, "markdown (空目录)"

    raise FileNotFoundError(
        f"未找到可用的文献数据。\n"
        f"  merged:   {merged_str}\n"
        f"  markdown: {markdown_str}\n"
        f"请先运行 pdf_to_markdown.py 转换文献。"
    )


def _has_md_files(folder: str) -> bool:
    """递归检查文件夹中是否有 .md 文件"""
    if not os.path.isdir(folder):
        return False
    for root, dirs, files in os.walk(folder):
        for f in files:
            if f.lower().endswith('.md'):
                return True
    return False


def find_md_files(input_folder: str, recursive: bool = True) -> List[str]:
    """查找文件夹中的 .md 文件（支持递归）"""
    md_files = []
    if recursive:
        for root, dirs, files in os.walk(input_folder):
            for f in files:
                if f.lower().endswith('.md'):
                    md_files.append(os.path.join(root, f))
    else:
        for f in os.listdir(input_folder):
            if f.lower().endswith('.md'):
                md_files.append(os.path.join(input_folder, f))
    return sorted(md_files)


# ==================== 提取函数 ====================

def extract_title(lines: List[str]) -> Optional[str]:
    """从 markdown 行列表中提取标题（跳过杂志推广噪音）"""
    skip_phrases = [
        'supporting information', 'supporting figures', 'supporting tables',
        'figure ', 'table ', 'fig.', 'appendix', 'supplementary',
        'as featured in', 'check for updates', 'read online',
        'metrics & more', 'article recommendations', 'cite this', 'access',
    ]
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('# '):
            heading = stripped[2:].strip()
            lower = heading.lower()
            if any(lower.startswith(p) for p in skip_phrases):
                continue
            return heading
    return None


def extract_authors(lines: List[str], title: Optional[str] = None) -> Optional[str]:
    """从 markdown 行列表中提取作者信息"""
    title_found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if title and stripped.startswith('# ') and title in stripped:
            title_found = True
            continue
        if title_found or (not title and i > 0 and not stripped.startswith('#')):
            if len(stripped) > 10 and len(stripped) < 300:
                lower = stripped.lower()
                noise_markers = [
                    'http', 'doi', 'fig.', 'table', 'abstract', 'introduction',
                    'received', 'published', 'email', 'corresponding',
                    'cite this', 'read online', 'supporting', 'graphical',
                    'department', 'university', 'institute'
                ]
                if not any(m in lower for m in noise_markers):
                    if ',' in stripped or ' and ' in lower:
                        return stripped
            if not title:
                continue
    return None


def extract_publication_date(content: str) -> Optional[str]:
    """从文本内容中提取出版时间"""
    # 方法1：查找 "Published:" 行
    pub_match = re.search(r'Published:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
    if pub_match:
        return pub_match.group(1).strip()
    # 方法2：查找 "Cite This:" 行中的年份
    cite_match = re.search(r'Cite This:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
    if cite_match:
        year_match = re.search(r'(20\d{2})', cite_match.group(1))
        if year_match:
            return year_match.group(1)
    # 方法3：文件前2000字符中最早的20xx年份
    years = re.findall(r'\b(20\d{2})\b', content[:2000])
    if years:
        return years[0]
    return None


def extract_doi(content: str, file_name: str) -> Optional[str]:
    """从内容 / 文件名中提取 DOI"""
    # 修复被换行/空格断开的DOI（如 "10.1149/1945-7111/ acb0b9"）
    content = re.sub(r'(10\.\d{4,9}/[^\s]*/)\s+', r'\1', content)
    # 也修复直接在第一个 / 后断开的情况（如 "10.3390/ pr10081573"）
    content = re.sub(r'(10\.\d{4,9}/)\s+', r'\1', content)
 
    # 方法1：正则匹配标准 DOI
    matches = re.findall(r'\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b', content, re.IGNORECASE)
    if matches:
        return matches[0].rstrip('.,;:')
    # 方法2：匹配 doi.org/ 链接
    url_matches = re.findall(r'doi\.org/(10\.\d{4,9}/[-._;()/:A-Z0-9]+)', content, re.IGNORECASE)
    if url_matches:
        return url_matches[0].rstrip('.,;:')
    # 方法3：文件名去掉 .md，将 _ 替换为 / 作为 DOI
    name_wo_ext = re.sub(r'\.md$', '', file_name, flags=re.IGNORECASE)
    name_as_doi = name_wo_ext.replace('_', '/')
    if re.match(r'^10\.\d{4,9}/', name_as_doi):
        return name_as_doi
    # 方法4：文件名中正则匹配 DOI（同样将 _ 替换为 /）
    name_match = re.search(
        r'(10\.\d{4,9}/[-._;()/:A-Z0-9]+)',
        file_name.replace('_', '/'), re.IGNORECASE
    )
    if name_match:
        return name_match.group(1).rstrip('.,;:')
    return None


# ==================== 主函数 ====================

def extract_meta_from_file(file_path: str) -> Dict[str, Optional[str]]:
    """
    从单个 markdown 文件中提取元数据。
    """
    file_name = os.path.basename(file_path)
    abs_path = os.path.abspath(file_path)

    # 记录所属子文件夹（相对于 merged/markdown 根目录）
    subfolder = ""
    for candidate in [str(MERGED_ROOT), str(MARKDOWN_ROOT)]:
        root_abs = os.path.abspath(candidate)
        if abs_path.startswith(root_abs):
            rel = os.path.relpath(os.path.dirname(abs_path), root_abs)
            subfolder = rel if rel != "." else ""
            break

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"  读取失败 {file_name}: {e}")
        return {
            "title": None, "authors": None,
            "publication_date": None, "doi": None,
            "file_path": file_path, "subfolder": subfolder,
        }

    lines = content.split('\n')
    title = extract_title(lines)
    authors = extract_authors(lines, title)
    pub_date = extract_publication_date(content)
    doi = extract_doi(content, file_name)

    return {
        "title": title,
        "authors": authors,
        "publication_date": pub_date,
        "doi": doi,
        "file_path": file_path,
        "subfolder": subfolder,
    }


def extract_meta_from_folder(
    input_folder: str,
    output_json: Optional[str] = None,
    recursive: bool = True,
    incremental: bool = False,
    existing_paths: Optional[set] = None,
) -> List[Dict]:
    """
    递归扫描文件夹中的 .md 文件，提取元数据。

    Args:
        input_folder: 包含 .md 文件的文件夹路径
        output_json: 输出 JSON 文件路径（可选）
        recursive: 是否递归扫描子目录
    """
    if not os.path.exists(input_folder):
        raise FileNotFoundError(f"输入文件夹不存在: {input_folder}")

    md_files = find_md_files(input_folder, recursive=recursive)
    if incremental and existing_paths:
        old_count = len(md_files)
        md_files = [f for f in md_files if f not in existing_paths]
        print(f"增量模式: 已有 {len(existing_paths)} 条, 跳过 {old_count - len(md_files)} 个旧文件, 新增 {len(md_files)} 个")
    print(f"在 {input_folder} 中发现 {len(md_files)} 个 .md 文件")

    results = []
    for i, file_path in enumerate(md_files, 1):
        file_name = os.path.basename(file_path)
        subdir = os.path.relpath(os.path.dirname(file_path), input_folder)
        label = f"{subdir}/{file_name}" if subdir != "." else file_name
        print(f"[{i}/{len(md_files)}] {label}")
        meta = extract_meta_from_file(file_path)
        results.append(meta)

    # 统计
    doi_count = sum(1 for r in results if r["doi"])
    title_count = sum(1 for r in results if r["title"])
    authors_count = sum(1 for r in results if r["authors"])
    print(f"\n提取完成: {len(results)} 篇文献")
    print(f"  - DOI:    {doi_count}/{len(results)}")
    print(f"  - 标题:   {title_count}/{len(results)}")
    print(f"  - 作者:   {authors_count}/{len(results)}")

    if output_json:
        output_path = Path(output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 增量模式：读取旧数据合并
        final_results = results
        if incremental and existing_paths and output_path.exists():
            try:
                with open(output_path, encoding="utf-8") as f:
                    old_results = json.load(f)
                seen_paths = {r.get("file_path", "") for r in old_results}
                merged = list(old_results)
                for r in results:
                    if r.get("file_path", "") not in seen_paths:
                        merged.append(r)
                        seen_paths.add(r.get("file_path", ""))
                final_results = merged
                print(f"  - 增量合并: {len(old_results)} 旧 + {len(results)} 新 = {len(merged)} 条")
            except Exception as e:
                print(f"  - 合并失败，使用新结果: {e}")
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(final_results, f, ensure_ascii=False, indent=2)
        print(f"  - 结果已保存至: {output_json}")

    return results


# ==================== 命令行入口 ====================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="从 markdown 文献文件中提取元数据（题目、作者、出版时间、DOI）"
    )
    parser.add_argument(
        "input_folder",
        nargs="?",
        default=None,
        help="包含 .md 文件的文件夹路径。不指定则自动检测（优先 merged，其次 markdown）"
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="输出 JSON 文件路径（默认: miner/json/meta_<source>.json）"
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="不递归扫描子目录"
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="增量模式：只处理新增的 .md 文件，追加到已有 JSON"
    )
    args = parser.parse_args()

    # 确定输入源
    if args.input_folder:
        input_folder = args.input_folder
        source_label = "manual"
    else:
        input_folder, source_label = detect_input_source()
        print(f"自动检测数据源: {source_label}")
        print(f"  {input_folder}")

    # 确定输出路径
    if args.output:
        output_json = args.output
    else:
        source_name = os.path.basename(input_folder.rstrip('/'))
        output_json = str(OUTPUT_DIR / f"meta_{source_name}.json")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    existing_paths = set()
    if args.incremental and os.path.exists(output_json):
        try:
            with open(output_json, encoding="utf-8") as f:
                existing_data = json.load(f)
            for item in existing_data:
                fp = item.get("file_path", "")
                if fp:
                    existing_paths.add(fp)
            print(f"[incremental] 已有 {len(existing_paths)} 条元数据，只处理新增文件")
        except Exception as e:
            print(f"[warn] 无法读取已有 JSON: {e}")
    
    extract_meta_from_folder(input_folder, output_json, recursive=not args.no_recursive,
                             incremental=args.incremental, existing_paths=existing_paths)
    
    # 如果是增量模式，合并新旧数据
    if args.incremental and os.path.exists(output_json):
        try:
            with open(output_json, encoding="utf-8") as f:
                all_data = json.load(f)
            print(f"[incremental] 最终 {len(all_data)} 条元数据")
        except Exception as e:
            print(f"[warn] 读取结果失败: {e}")