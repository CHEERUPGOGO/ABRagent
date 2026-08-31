# -*- coding: utf-8 -*-
"""表格上下文提取 — 在清洗前从原始 Markdown 中提取表格

支持两类表格：
  1. 标准 Markdown 表格（| header | header | + |---| ---| + 行数据）
  2. HTML <table> 标签表格

输出格式：每个表格转换为一个 TABLE DATA BLOCK 格式的字符串，
包含附近文本、caption、表头、行数据和按列展开的数据。
清洗可能会去掉表格（例如 HTML table），所以这一步需要在清洗前执行。
"""

import re
import logging
from typing import List, Optional
from bs4 import BeautifulSoup

logger = logging.getLogger("TableContext")

# ==================== Markdown 表格 ====================

_MD_TABLE_RE = re.compile(
    r"^(\|.+\|)\s*\n"          # header row: | a | b |
    r"^(\|[\s:-]+\|)\s*\n"     # separator: |---|---|
    r"((?:^\|.+\|\s*\n)*)",     # data rows: | 1 | 2 |
    re.MULTILINE
)


def _extract_markdown_tables(text: str) -> List[dict]:
    """提取标准 Markdown 表格"""
    tables = []
    for m in _MD_TABLE_RE.finditer(text):
        header_line = m.group(1).strip()
        # data lines
        data_block = m.group(3).strip()
        data_lines = [l.strip() for l in data_block.split("\n") if l.strip()]

        headers = [h.strip() for h in header_line.strip("|").split("|")]
        rows = []
        for line in data_lines:
            cells = [c.strip() for c in line.strip("|").split("|")]
            rows.append(cells)

        tables.append({
            "type": "markdown",
            "headers": headers,
            "rows": rows,
            "raw": m.group(0),
            "start": m.start(),
            "end": m.end(),
        })
    return tables


# ==================== HTML 表格 ====================


def _extract_html_tables(html_fragment: str) -> List[dict]:
    """提取 HTML <table> 表格"""
    tables = []
    soup = BeautifulSoup(html_fragment, "html.parser")
    for table_tag in soup.find_all("table"):
        headers = []
        rows = []

        # thead / th
        thead = table_tag.find("thead")
        if thead:
            ths = thead.find_all("th")
            if ths:
                headers = [th.get_text(strip=True) for th in ths]

        # tr → td
        for tr in table_tag.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if len(tds) > 1 or (
                len(tds) == 1 and headers and len(tds[0].get_text(strip=True)) > 0
            ):
                cells = [td.get_text(strip=True) for td in tds]
                rows.append(cells)

        # fallback: 从第一个 tr 提取 header
        if not headers and table_tag.find_all("tr"):
            first_tr = table_tag.find_all("tr")[0]
            ths = first_tr.find_all("th")
            if ths:
                headers = [th.get_text(strip=True) for th in ths]
            elif len(first_tr.find_all("td")) >= 2:
                # 第一行可能是表头
                headers = [td.get_text(strip=True) for td in first_tr.find_all("td")]
                if rows and rows[0] == headers:
                    rows = rows[1:]

        if headers or rows:
            tables.append({
                "type": "html",
                "headers": headers,
                "rows": rows,
                "raw": str(table_tag),
                "start": -1,
                "end": -1,
            })
    return tables


# ==================== 附近文本提取 ====================


def _get_surrounding_text(text: str, pos: int, window_chars: int = 400) -> str:
    """获取表格位置附近的上下文文本"""
    start = max(0, pos - window_chars)
    end = min(len(text), pos + window_chars)
    before = text[start:pos].strip()
    after = text[pos:end].strip()
    ctx = ""
    if before:
        ctx += before[-window_chars:] + "\n"
    ctx += f"[TABLE at pos {pos}]\n"
    if after:
        ctx += after[:window_chars]
    return ctx.strip()


# ==================== 构建 TABLE DATA BLOCK ====================


def _format_table_block(
    table: dict, surrounding: str, caption: str = ""
) -> str:
    """将表格格式化为 TABLE DATA BLOCK 文本"""
    lines = ["TABLE DATA BLOCK"]
    if caption:
        lines.append(f"Caption or title: {caption}")
    if surrounding:
        lines.append(f"Nearby text: {surrounding[:300]}")
    if table["headers"]:
        lines.append(f"Headers: {'; '.join(table['headers'])}")
    lines.append("Rows:")
    for i, row in enumerate(table["rows"], 1):
        if table["headers"] and len(table["headers"]) == len(row):
            pairs = []
            for h, c in zip(table["headers"], row):
                pairs.append(f"{h} = {c}")
            lines.append(f"Row {i}: {'; '.join(pairs)}")
        else:
            lines.append(f"Row {i}: {'; '.join(row)}")
    # 按列展开
    if table["headers"] and table["rows"]:
        lines.append("Column data:")
        for ci, header in enumerate(table["headers"]):
            col_vals = []
            for row in table["rows"]:
                if ci < len(row):
                    col_vals.append(row[ci])
            lines.append(f"  {header}: {'; '.join(col_vals)}")
    return "\n".join(lines)


def _find_nearest_caption(text: str, table_start: int, max_lookback: int = 800) -> str:
    """在表格前面找最近的表题（Table X. / Table SX. 等）"""
    before = text[max(0, table_start - max_lookback):table_start]
    m = re.search(
        r"(?:Table|TABLE|表)\s+(?:S\d+[\.\s]|\d+[\.\s])[^\n]{,200}",
        before, re.IGNORECASE
    )
    if m:
        return m.group(0).strip()
    # fallback: 找带 "table" 关键词的行
    for line in before.split("\n"):
        if re.search(r"table", line, re.IGNORECASE):
            return line.strip()
    return ""


# ==================== 主入口 ====================


def extract_table_contexts(file_path: str) -> List[str]:
    """从 Markdown 文件中提取所有表格，返回 TABLE DATA BLOCK 列表

    Args:
        file_path: Markdown 文件路径

    Returns:
        表格块字符串列表，每个字符串是一个完整 TABLE DATA BLOCK
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        logger.warning(f"读取文件失败 {file_path}: {e}")
        return []

    if not text.strip():
        return []

    blocks = []

    # 1. Markdown 表格
    md_tables = _extract_markdown_tables(text)
    for tbl in md_tables:
        caption = _find_nearest_caption(text, tbl["start"])
        surrounding = _get_surrounding_text(text, tbl["start"])
        block = _format_table_block(tbl, surrounding, caption)
        blocks.append(block)

    # 2. HTML 表格
    html_tables = _extract_html_tables(text)
    for tbl in html_tables:
        # HTML 表格没有准确位置，跳过附近文本
        caption = ""
        block = _format_table_block(tbl, "", caption)
        blocks.append(block)

    # 去重（同一表格可能被两种方式重复提取）
    seen = set()
    unique_blocks = []
    for b in blocks:
        # 用表头和行数去重
        sig = re.sub(r"\s+", " ", b[:200])
        if sig not in seen:
            seen.add(sig)
            unique_blocks.append(b)

    if unique_blocks:
        logger.info(f"提取 {len(unique_blocks)} 个表格块")
    return unique_blocks
