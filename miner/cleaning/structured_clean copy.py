# -*- coding: utf-8 -*-
"""结构化清洗 — 将 markdown 按文本/表格/图片分类输出"""

import re, os, tempfile
from typing import List, Optional, Dict
from dataclasses import dataclass, field
from bs4 import BeautifulSoup

from miner.cleaning.clean_text import (
    is_noise_line, is_html_table_line, is_address_line,
    get_section_status, clean_urls_and_artifacts, _trim_si_section,
    EXTRACT_SKIP_NOISE, EXTRACT_SKIP_SECTIONS,
    NOISE_KEYWORDS, KEEP_SECTIONS, END_SECTIONS,
)


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
    """判断是否为表注/表标题行"""
    return bool(re.match(r'^table\s', line.strip().lower()))

def is_table_start(line: str) -> bool:
    s = line.strip().lower()
    return '<table' in s or '<tr>' in s or '<td>' in s or '<th>' in s or bool(re.match(r'^\|.+\|$', s))

def html_table_to_text(raw: str) -> str:
    """将含表注+HTML表格的文本转为纯文本表格"""
    caption = ""
    html = raw
    # 提取表注（<table 之前的第一行非空行）
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
    doc.meta["file_name"] = os.path.basename(file_path)
    doc.meta["file_stem"] = os.path.splitext(doc.meta["file_name"])[0]
    doc.meta["doi"] = doc.meta["file_stem"].replace("_", "/")

    current_buffer = ""
    current_status = 'skip' if mode == 'extract' else 'keep'
    hit_keep_section = False
    in_table = False
    table_buf = ""
    figure_buf = ""

    for line in lines:
        s = line.strip()

        # 空行
        if not s:
            if in_table:
                if table_buf.strip():
                    doc.tables.append(html_table_to_text(table_buf) if '<table' in table_buf.lower() else table_buf)
                table_buf = ""; in_table = False
            elif figure_buf:
                doc.figures.append(figure_buf.strip())
                figure_buf = ""
            elif current_buffer and len(current_buffer) >= 800:
                doc.texts.append(current_buffer.strip())
                current_buffer = ""
            continue

        # 图片/图注
        if is_figure_line(s):
            figure_buf += s + "\n"; continue
        if is_figure_caption(s):
            figure_buf += s + "\n"; continue

        # 表注（归入表格缓冲区，作为表头）
        if is_table_caption(s):
            table_buf += s + "\n"
            continue

        # 表格
        if is_table_start(s) or (in_table and is_html_table_line(s)):
            in_table = True; table_buf += s + "\n"; continue
        if in_table:
            if s.startswith('|') or re.match(r'^[\s|:-]+$', s):
                table_buf += s + "\n"; continue
            else:
                if table_buf.strip():
                    doc.tables.append(html_table_to_text(table_buf) if '<table' in table_buf.lower() else table_buf)
                table_buf = ""; in_table = False

        # 噪音
        if is_noise_line(s, mode=mode) or is_address_line(s):
            continue

        # 标题
        if s.startswith('#'):
            if current_buffer:
                doc.texts.append(current_buffer.strip())
                current_buffer = ""
            if figure_buf:
                doc.figures.append(figure_buf.strip())
                figure_buf = ""

            hdr = s.replace('#', '').strip()
            st = get_section_status(hdr, mode=mode)

            if mode == 'extract' and st is None:
                if current_status != 'keep': current_status = 'skip'
            elif st == 'end':
                current_status = 'end'
            elif st == 'keep':
                current_status = 'keep'; hit_keep_section = True
                if mode == 'extract': current_buffer = s + " "
            elif st == 'skip':
                current_status = 'skip'
            else:
                if mode == 'classify':
                    current_status = 'keep'
                    current_buffer += s + " "

            if not doc.meta.get("title") and mode == 'classify':
                doc.meta["title"] = hdr[:200]
            continue

        if current_status in ('end', 'skip'):
            continue

        cleaned = clean_urls_and_artifacts(s)
        if not cleaned.strip(): continue

        if not current_buffer:
            current_buffer = cleaned
        else:
            prev = current_buffer
            ends_punct = prev.rstrip()[-1] in ('.', '!', '?') if prev.rstrip() else False
            starts_lower = cleaned[0].islower() if cleaned else False
            if starts_lower or not ends_punct:
                current_buffer += " " + cleaned
            else:
                doc.texts.append(current_buffer.strip())
                current_buffer = cleaned

    # 收尾
    if current_buffer: doc.texts.append(current_buffer.strip())
    if table_buf.strip():
        doc.tables.append(html_table_to_text(table_buf) if '<table' in table_buf.lower() else table_buf)
    if figure_buf: doc.figures.append(figure_buf.strip())

    # Fallback
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
