# -*- coding: utf-8 -*-
"""结构化清洗 — 批量处理 markdown 文件，按文本/表格/图片分类输出

输出四个字段：
- texts:   清洗后的正文段落列表
- tables:  提取的表格（HTML 表格自动转纯文本）
- figures: 图片/图注
- meta:    元数据（通过 extract_meta 提取）

用法：
    # 单文件
    doc = structured_clean("papers/merged/10.1021_xxx.md")
    # 批量
    results = structured_clean_batch("papers/merged")
"""

import re, os, tempfile
from pathlib import Path
from typing import List, Optional, Dict
from dataclasses import dataclass, field
from bs4 import BeautifulSoup

from miner.cleaning.clean_text import (
    is_noise_line, is_html_table_line, is_address_line,
    get_section_status, clean_urls_and_artifacts, _trim_si_section,
)
from miner.meta_extraction.extract_meta import extract_meta_from_file


@dataclass
class CleanedDocument:
    source_file: str = ""
    mode: str = "extract"
    texts: List[str] = field(default_factory=list)
    tables: List[str] = field(default_factory=list)
    figures: List[str] = field(default_factory=list)
    meta: Dict[str, str] = field(default_factory=dict)

    @property
    def clean_text(self) -> str:
        return "\n\n".join(self.texts)


def is_figure_line(line: str) -> bool:
    return bool(re.match(r'^!\[.*\]\(.*\)$', line.strip()))

def is_figure_caption(line: str) -> bool:
    return bool(re.match(r'^(fig\.?|figure|scheme)\s', line.strip().lower()))

def is_table_caption(line: str) -> bool:
    return bool(re.match(r'^table\s', line.strip().lower()))

def is_table_start(line: str) -> bool:
    s = line.strip().lower()
    return '<table' in s or '<tr>' in s or '<td>' in s or '<th>' in s or bool(re.match(r'^\|.+\|$', s))

def html_table_to_text(raw: str) -> str:
    caption = ""
    for line in raw.split('\n'):
        s = line.strip()
        if s and '<table' not in s.lower() and '<tr>' not in s.lower():
            caption = s
        else:
            break
    soup = BeautifulSoup(raw, "html.parser")
    rows = []
    for tr in soup.find_all('tr'):
        cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
        if cells: rows.append(" | ".join(cells))
    result = "\n".join(rows)
    if caption:
        result = caption + "\n" + result
    return result


def structured_clean(file_path: str, min_text_len: int = 200, mode: str = "extract") -> Optional[CleanedDocument]:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"读取失败 {file_path}: {e}")
        return None

    lines = _trim_si_section(lines)
    doc = CleanedDocument(source_file=file_path, mode=mode)

    try:
        doc.meta = extract_meta_from_file(file_path)
    except Exception:
        fname = os.path.basename(file_path)
        doc.meta = {
            "title": "", "authors": "", "publication_date": "",
            "doi": os.path.splitext(fname)[0].replace("_", "/"),
            "file_path": file_path, "subfolder": ""
        }

    current_buffer = ""
    current_status = 'skip' if mode == 'extract' else 'keep'
    hit_keep_section = False
    in_table = False
    table_buf = ""
    figure_buf = ""
    # 无标题内容累积计数器：超过阈值时空行回退为段落边界
    _headless_chars = 0
    HEADLESS_THRESHOLD = 10000

    for line in lines:
        s = line.strip()

        if not s:
            if in_table:
                if table_buf.strip():
                    doc.tables.append(html_table_to_text(table_buf) if '<table' in table_buf.lower() else table_buf)
                table_buf = ""; in_table = False
            elif figure_buf:
                doc.figures.append(figure_buf.strip())
                figure_buf = ""
            elif current_buffer and _headless_chars >= HEADLESS_THRESHOLD:
                # 连续大段无标题 → 空行回退为段落边界
                doc.texts.append(current_buffer.strip())
                current_buffer = ""
            continue

        if is_figure_line(s):
            figure_buf += s + "\n"; continue
        if is_figure_caption(s):
            figure_buf += s + "\n"; continue

        if is_table_caption(s):
            table_buf += s + "\n"
            continue

        if is_table_start(s) or (in_table and is_html_table_line(s)):
            in_table = True; table_buf += s + "\n"; continue
        if in_table:
            if s.startswith('|') or re.match(r'^[\s|:-]+$', s):
                table_buf += s + "\n"; continue
            else:
                if table_buf.strip():
                    doc.tables.append(html_table_to_text(table_buf) if '<table' in table_buf.lower() else table_buf)
                table_buf = ""; in_table = False

        if is_noise_line(s, mode=mode) or is_address_line(s):
            continue

        if s.startswith('#'):
            if current_buffer:
                doc.texts.append(current_buffer.strip())
                current_buffer = ""
            if figure_buf:
                doc.figures.append(figure_buf.strip())
                figure_buf = ""
            _headless_chars = 0

            hdr = s.replace('#', '').strip()
            st = get_section_status(hdr, mode=mode)

            if mode == 'extract' and st is None:
                if current_status != 'keep': current_status = 'skip'
            elif st == 'end':
                if mode == 'classify':
                    current_buffer += s + " "
                current_status = 'end'
            elif st == 'keep':
                current_status = 'keep'; hit_keep_section = True
                current_buffer += s + " "
            elif st == 'skip':
                current_status = 'skip'
            else:
                if mode == 'classify':
                    if current_status != 'end':
                        current_status = 'keep'
                        current_buffer += s + " "
                    else:
                        current_buffer += s + " "

            if not doc.meta.get("title") and mode == 'classify':
                doc.meta["title"] = hdr[:200]
            continue

        if current_status in ('end', 'skip'):
            continue

        cleaned = clean_urls_and_artifacts(s)
        if not cleaned.strip(): continue

        # 同一个小标题下：所有内容合并为一个段落
        _headless_chars += len(cleaned)
        if not current_buffer:
            # 空行新段落：检查是否与上一段落为断句（上段无句号 + 本段小写开头）
            if doc.texts:
                prev = doc.texts[-1]
                ends_punct = prev.rstrip()[-1] in ('.', '!', '?') if prev.rstrip() else False
                starts_lower = cleaned[0].islower() if cleaned else False
                if starts_lower or not ends_punct:
                    doc.texts[-1] += " " + cleaned
                    continue
            current_buffer = cleaned
        else:
            current_buffer += " " + cleaned

    if current_buffer: doc.texts.append(current_buffer.strip())
    if table_buf.strip():
        doc.tables.append(html_table_to_text(table_buf) if '<table' in table_buf.lower() else table_buf)
    if figure_buf: doc.figures.append(figure_buf.strip())

    total_text_len = sum(len(t) for t in doc.texts)
    if mode == 'extract' and (not hit_keep_section or total_text_len < 1000):
        fb = []
        for line in lines:
            s = line.strip()
            if not s or is_noise_line(s, mode='classify') or is_figure_line(s) or is_figure_caption(s): continue
            if s.startswith('#'):
                if get_section_status(s.replace('#','').strip(), mode='classify') == 'end': break
                continue
            if re.match(r'^(abstract|keywords)\b', s, re.IGNORECASE): continue
            c = clean_urls_and_artifacts(s)
            if c.strip(): fb.append(c.strip())
        doc.texts = fb

    if min_text_len and sum(len(t) for t in doc.texts) < min_text_len:
        return None
    return doc


def structured_clean_from_content(content: str, min_text_len: int = 200, mode: str = "extract") -> Optional[CleanedDocument]:
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
        f.write(content); tmp = f.name
    result = structured_clean(tmp, min_text_len=min_text_len, mode=mode)
    os.unlink(tmp)
    return result


def structured_clean_batch(
    input_folder: str,
    output_json: Optional[str] = None,
    output_dir: Optional[str] = None,
    recursive: bool = True,
    min_text_len: int = 200,
    mode: str = "extract",
) -> List[Dict]:
    if not os.path.exists(input_folder):
        raise FileNotFoundError(f"输入文件夹不存在: {input_folder}")

    md_files = _find_md_files(input_folder, recursive=recursive)
    print(f"在 {input_folder} 中发现 {len(md_files)} 个 .md 文件")

    results = []
    for i, file_path in enumerate(md_files, 1):
        file_name = os.path.basename(file_path)
        subdir = os.path.relpath(os.path.dirname(file_path), input_folder)
        label = f"{subdir}/{file_name}" if subdir != "." else file_name
        print(f"[{i}/{len(md_files)}] {label}")

        doc = structured_clean(file_path, min_text_len=min_text_len, mode=mode)
        if doc:
            result = {
                "file_path": file_path,
                "texts": doc.texts,
                "tables": doc.tables,
                "figures": doc.figures,
                "meta": doc.meta,
            }
            if output_dir:
                result["output_file"] = _write_cleaned_document(doc, input_folder, output_dir)
            results.append(result)
        else:
            results.append({
                "file_path": file_path,
                "texts": [], "tables": [], "figures": [],
                "meta": {},
                "error": "文本过短或提取失败",
            })

    ok_count = sum(1 for r in results if "error" not in r)
    print(f"\n清洗完成: {ok_count}/{len(results)} 篇文献成功")

    if output_json:
        import json as _json
        output_path = Path(output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            _json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"  - 结果已保存至: {output_json}")

    if output_dir:
        print(f"  - Cleaned documents written to: {output_dir}")

    return results


def _write_cleaned_document(doc: CleanedDocument, input_folder: str, output_dir: str) -> str:
    input_root = Path(input_folder).resolve()
    source_path = Path(doc.source_file).resolve()
    try:
        relative_path = source_path.relative_to(input_root)
    except ValueError:
        relative_path = Path(source_path.name)

    output_path = Path(output_dir) / relative_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(doc.clean_text.strip() + "\n", encoding="utf-8")
    return str(output_path)


def _find_md_files(folder: str, recursive: bool = True) -> List[str]:
    md_files = []
    if recursive:
        for root, dirs, files in os.walk(folder):
            for f in files:
                if f.lower().endswith('.md'):
                    md_files.append(os.path.join(root, f))
    else:
        for f in os.listdir(folder):
            if f.lower().endswith('.md'):
                md_files.append(os.path.join(folder, f))
    return sorted(md_files)


if __name__ == "__main__":
    import argparse, json as _json
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="批量结构化清洗 markdown 文献文件"
    )
    parser.add_argument(
        "input_folder",
        nargs="?",
        default="papers/merged",
        help="包含 .md 文件的文件夹路径（默认: papers/merged）"
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="输出 JSON 文件路径"
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for cleaned markdown files"
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="不递归扫描子目录"
    )
    parser.add_argument(
        "-m", "--mode",
        default="extract",
        choices=["classify", "extract"],
        help="清洗模式（默认: extract）"
    )
    args = parser.parse_args()

    structured_clean_batch(
        args.input_folder,
        output_json=args.output,
        output_dir=args.output_dir,
        recursive=not args.no_recursive,
        mode=args.mode,
    )
