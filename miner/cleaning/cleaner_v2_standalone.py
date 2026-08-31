#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献清洗工具 v2 — 独立版

基于 high_energy_rag/cleaner.py 修改，不依赖任何 miner/ 下的清洗代码。

新增规则：
  - 过滤图注段落（Fig./Figure 开头）
  - 过滤 # 标题行（Markdown heading）
  - 过滤 MinerU 图表碎片

用法:
  from miner.cleaning.cleaner_v2_standalone import clean_markdown
  cleaned = clean_markdown("database/type/run1/xxx/anode/10.1016_xxx.md")
"""

import re
from pathlib import Path
from typing import List, Optional

# ==================== 配置常量（从 clean_text1.py 内联） ====================

NOISE_KEYWORDS = [
    "graphical abstract", "entry for", "corresponding author", "author contributions",
    "received:", "revised:", "accepted:", "published:", "cite this",
    "read online", "access", "metrics & more", "article recommendations",
    "check for updates",
    "the author(s)", "conflict of interest", "competing interests",
    # ── 中文期刊 / 知网特有噪音 ──
    "收稿日期", "修回日期", "录用日期", "网络首发",
    "基金项目", "资助项目", "作者简介", "通信作者", "通讯作者",
    "中图分类号", "文献标志码", "文章编号", "引用本文", "版权声明",
    "国家自然科学基金", "国家重点研发计划",
]

MINERU_CHART_STARTS = (
    "line ", "bar ", "scatter ", "boxplot ",
    "textimage ", "naturalimage ", "chemical ",
)


def _is_mineru_chart_line(stripped: str) -> bool:
    """检测是否为 MinerU 图表转换的碎片数据行"""
    lower = stripped.lower()
    for marker in MINERU_CHART_STARTS:
        if lower.startswith(marker):
            return True
    if re.match(r"^(`{3,})\s*(mermaid|flowchart)\b", lower):
        return True
    return False


# ==================== 段落过滤规则 ====================

_REF_HEADER_RE = re.compile(r"^#+\s*(references|bibliography|参考文献)\s*[:：]?\s*", re.IGNORECASE)
# 中文文档的参考文献标题（知网 MinerU 输出不带 #，如 "参考文献" / "参考文献："）
_REF_HEADER_ZH_RE = re.compile(r"^(参考文献|References?)\s*[:：]?\s*$", re.IGNORECASE)
_PURE_IMAGE_RE = re.compile(r"^\s*!\[.*?\]\(.*?\)\s*$")
_DOI_ORCID_RE = re.compile(
    r"(orcid\s+identification|orcid\.org/|https?://doi\.org/"
    r"|creative commons|this is an open access"
    r"|complete contact information|pubs\.acs\.org)", re.IGNORECASE)
_CITE_INFO_RE = re.compile(
    r"^(received|revised|accepted|published|cite this|read online|"
    r"access|metrics|article recommendations|"
    r"orcid|©|copyright|creative commons|open access|"
    r"this is an open|https?://doi\.org|"
    r"the author(s)? declare|no competing|"
    r"收稿日期|修回日期|录用日期|刊出|网络首发|"
    r"文章编号|中图分类号|文献标志码)",
    re.IGNORECASE)
_ADDR_RE = re.compile(
    r"(department of|university of|college of|institute of|school of|"
    r"laboratory of|center for|state key|corresponding author|"
    r"e-mail|fax:|author contributions|"
    r"new energy|research institute|ORCID|"
    r"大学|学院|研究院|研究所|重点实验室|研究中心)",
    re.IGNORECASE)
_REF_LIST_RE = re.compile(
    r"[A-Z][a-z]+[\.,]\s+\d{4},\s+\d+", re.IGNORECASE)
# 中文参考文献条目行（GB/T 7714: "[1] 张三, 李四. ..."）
# 注意：开头必须紧跟中文字符，避免误伤英文文献 "[1] Smith, ..." 参考文献条目
_REF_CN_RE = re.compile(r"^\[\d+\]\s+[\u4e00-\u9fff]")

# ── v2 新增：图注过滤 ──
_FIGURE_CAPTION_RE = re.compile(
    r"^(fig\.?|figure|scheme|table\s*\d+|graphical\s+abstract|diagram|schematic|chart|"
    r"图\s*\d+|表\s*\d+|图\s*S\d+|表\s*\d+)\b",
    re.IGNORECASE)
# ── v2 新增：# 标题行和纯章节编号过滤 ──
_HEADING_RE = re.compile(r"^#+\s")
_SECTION_NUM_RE = re.compile(r'^[\d.]+\s+"?[A-Z]|^[\d.]+\s+[\u4e00-\u9fff]')  # 英文 "2.1. Electrolyte..." / 中文 "2.1 电解液..."

# MinerU <details> 标签
_DETAILS_BLOCK_RE = re.compile(
    r'<details>\s*<summary>[^<]*</summary>\s*(.*?)\s*</details>',
    re.DOTALL | re.IGNORECASE)

_TABLE_CAPTION_RE = re.compile(r'^(?:Table|表格)\s+\w', re.IGNORECASE)


# ==================== 核心过滤函数 ====================

def _expand_details(text: str) -> str:
    """展开 <details> 块：去掉图片转换的表格数据，保留图注/正文"""
    def _process(m: re.Match) -> str:
        inner = m.group(1)
        summary_match = re.search(r'<summary>([^<]*)</summary>', m.group(0))
        summary = summary_match.group(1).strip().lower() if summary_match else ''
        # MinerU 图表碎片（natural_image, text_image, line, bar, scatter, heatmap, chemical 等）直接丢弃
        if any(kw in summary for kw in ('naturalimage', 'natural_image', 'textimage', 'text_image',
                                         'line', 'bar', 'scatter', 'boxplot', 'heatmap', 'chemical')):
            return ''
        lines = []
        for line in inner.split('\n'):
            stripped = line.strip()
            if _PURE_IMAGE_RE.match(stripped):
                continue
            if re.match(r'^[\|\-\s:]+$', stripped):
                continue
            lines.append(line)
        if not lines:
            return ''
        pipe_count = sum(1 for l in lines if l.strip().startswith('|'))
        if pipe_count > 0 and pipe_count / len(lines) > 0.5:
            return ''
        result = '\n'.join(lines).strip()
        return result if result else ''
    return _DETAILS_BLOCK_RE.sub(_process, text)


def _is_table_line(text: str) -> bool:
    s = text.strip()
    return s.startswith('<table') or (s.startswith('|') and s.count('|') >= 3)


def _merge_standalone_equations(paragraphs: list) -> list:
    """将独立的 $$...$$ 公式段合并到前一段正文"""
    result = []
    for i, p in enumerate(paragraphs):
        s = p.strip()
        if s.startswith('$$') and s.endswith('$$') and result:
            result[-1] = result[-1] + '\n' + p
        else:
            result.append(p)
    return result


def _merge_table_captions(paragraphs: list) -> list:
    """将表注与紧邻的表格合并"""
    result = []
    i = 0
    while i < len(paragraphs):
        p = paragraphs[i]
        if _TABLE_CAPTION_RE.match(p.strip()) and i + 1 < len(paragraphs):
            nxt = paragraphs[i + 1].strip()
            if _is_table_line(nxt):
                result.append(p + '\n' + nxt)
                i += 2
                continue
        result.append(p)
        i += 1
    return result


def _is_figure_caption(text: str) -> bool:
    """判断是否为图注段落"""
    s = text.strip()
    # 短图注：直接过滤
    if len(s) < 300 and _FIGURE_CAPTION_RE.search(s):
        return True
    # 长图注：包含版权声明/多子图说明的，也过滤
    if _FIGURE_CAPTION_RE.search(s) and (
        "copyright" in s.lower() or "permission from" in s.lower()
        or "reproduced from" in s.lower()
        or re.search(r"\([a-d]\) ", s[:200])
    ):
        return True
    # Figure S 图注（SI 图注，前缀可能有噪音字符）
    if re.search(r'Figure\s+S\d+', s[:100]) and len(s) < 500:
        return True
    # Fig. X. (description) 图注（数字+句点开头，区别于 Fig. Xa 正文引用）
    if re.match(r'^.{0,10}Fig\.\s*\d+\.\s', s) and len(s) < 1000:
        return True
    return False


def _is_heading(text: str) -> bool:
    """判断是否为 # 标题行"""
    s = text.strip()
    return bool(_HEADING_RE.match(s))


def _is_noise_paragraph(text: str, is_zh: bool = False) -> bool:
    """是否是噪音段落（应被过滤）"""
    s = text.strip()
    if not s:
        return True
    # ── 噪音关键词匹配 ──
    lower = s.lower()
    for kw in NOISE_KEYWORDS:
        if kw in lower:
            return True
    # ── v2 新增：图注 ──
    if _is_figure_caption(s):
        return True
    # ── 图表碎片：多行 | 段落中首行首单词重复率 > 50% ──
    if "|" in s:
        raw_lines = s.split("\n")
        pipe_lines = [l for l in raw_lines if l.strip().startswith("|")]
        if len(pipe_lines) >= 4:
            first_words = []
            for line in pipe_lines:
                cells = [p.strip() for p in line.strip().split("|") if p.strip()]
                if cells:
                    first_word = cells[0].split()[0] if cells[0].split() else ""
                    first_words.append(first_word)
            if first_words:
                from collections import Counter
                most_common, cnt = Counter(first_words).most_common(1)[0]
                if cnt / len(first_words) > 0.5:
                    return True
    # ── v2 新增：# 标题 ──
    if _is_heading(s):
        return True
    # ── v2 新增：纯章节编号标题（"2.1. Electrolyte Bulk..."） ──
    lines = s.split("\n")
    section_lines = sum(1 for line in lines if line.strip() and _SECTION_NUM_RE.match(line.strip()))
    if len(s) < 120 and _SECTION_NUM_RE.match(s):
        return True
    # 多行块全由章节标题组成（如 "6.2.2. ...\n6.2.3. ...\n6.2.4. ..."）
    if len(lines) >= 2 and section_lines == sum(1 for line in lines if line.strip()):
        return True
    # HTML 注释块
    if s.startswith("<!--") and ("-->" in s or len(s) < 120):
        return True
    # 纯图片段落
    if _PURE_IMAGE_RE.match(s):
        return True
    # 引用/出版信息
    if _CITE_INFO_RE.match(s):
        return True
    # ORCID / DOI / contact info
    m_doi_orcid = _DOI_ORCID_RE.search(s)
    if m_doi_orcid:
        matched = m_doi_orcid.group()
        if "orcid.org" in matched or "contact" in matched.lower() or len(s) < 200:
            return True
    # Inline 引用列表：≥3 个引用模式 + 引用占比 > 30%（长段）或 < 5000 字（短段）
    ref_matches = _REF_LIST_RE.findall(s)
    if len(ref_matches) >= 3:
        estimated_ref_chars = len(ref_matches) * 100
        if len(s) < 5000 or (estimated_ref_chars / max(len(s), 1)) > 0.3:
            return True
    # 中文参考文献条目行（整段为一条 [n] 开头引用）
    if _REF_CN_RE.match(s) and len(s) < 800:
        return True
    # MinerU 图表碎片
    if _is_mineru_chart_line(s[:60]):
        return True
    # 短地址行
    if len(s) < 120 and _ADDR_RE.search(s):
        return True
    # 纯数据行
    if len(s) < 60 and re.match(r"^[\d\s.,%()\-]+$", s):
        return True
    # ── 邮箱/联系方式行 ──
    if len(s) < 120 and re.search(r"@[a-zA-Z0-9.-]+\.(?:com|org|edu|gov|cn|net)", s):
        return True
    # ── 作者名单段：先去掉中间名缩写中的点号、上标数字和特殊符号，再判纯作者行 ──
    clean_s = re.sub(r"\b([A-Z])\.", r"\1", s)  # "V." → "V"
    clean_s = re.sub(r"[0-9]", "", clean_s)       # 去掉上标数字
    clean_s = re.sub(r"[ ✉✝†‡*&,]", " ", clean_s)  # 去掉特殊符号和逗号
    clean_s = re.sub(r"\s+", " ", clean_s).strip()

    # 匹配英文人名模式: "First[-Name] [M.] LastName[-Name]"
    name_pattern = r"[A-Z][a-z]+(?:-[A-Z][a-z]+)?(?:\.[A-Z]\.)?\s+(?:[A-Z](?:\.?\s+))?[A-Z][a-z]+(?:-[A-Z][a-z]+)?"
    name_matches = re.findall(name_pattern, clean_s)
    name_count = len(name_matches)

    # 统计总词数，计算人名密度
    words = clean_s.split()
    total_words = len(words) if words else 0
    name_ratio = name_count / total_words if total_words > 0 else 0

    # 检测是否为完整句子：在 clean_s 上检测句号+空格（已去掉了中间名缩写的点号，不会被 V. 误导）
    has_sentence_end = bool(re.search(r"[.!?]\s", clean_s))
    is_long = len(s) > 250

    # 纯作者行：人名密度高（3+ 人名且没有句子结尾）
    if name_count >= 3 and not has_sentence_end:
        return True
    if name_count >= 2 and name_ratio >= 0.6 and not has_sentence_end:
        return True
    # 短段 + 高人名密度 + 无动词/冠词结构
    if len(s) < 150 and name_count >= 2 and name_ratio >= 0.5 and not is_long:
        return True

    # ── 缩略名作者行：J. Meng, W. Hu, L. Xu... ──
    abbrev_pattern = r"[A-Z]\.\s+[A-Z][a-z]+(?:-[A-Z][a-z]+)?"
    abbrev_matches = re.findall(abbrev_pattern, s)
    if len(abbrev_matches) >= 3 and not has_sentence_end and len(s) < 150:
        return True

    # ── 中文期刊格式纯作者行：ALL_CAPS 姓 + 名 (中文名) + 数字编号 ──
    # 形如 "HUANG Shao-zhen(黄绍祯) 1, 2 , HE Pan(贺盼) 1 , ..."
    cn_author_pat = r"[A-Z][A-Z\s-]+[A-Z][a-z]+(?:-[a-z]+)?\([^)]+\)\s*[\d,\s]+"
    cn_authors = re.findall(cn_author_pat, s)
    if len(cn_authors) >= 3:
        return True
    # ══════════════════════════════════════════════════════
    if len(s) < 20 and not re.search(r'[a-zA-Z]{4,}', s):
        # 中文短段保护：仅当含中文、单行、且（长度≥4 或含中文标点）时跳过过滤。
        # 多行残片（如 "H \n一 \n¬" 页面排版噪声）与单字残片（"一"）仍按噪音过滤。
        has_cn = re.search(r'[\u4e00-\u9fff]', s)
        if not has_cn or not ("\n" not in s and (len(s) >= 4 or re.search(r'[。，；：！？、]', s))):
            return True
    # ── MinerU 图号标签残片：短文本且只含(括弧+字母数字+空格)或无实质词 ──
    if len(s) < 60 and re.match(r'^[\s\(\)（）""a-zA-Z0-9,.:;+\-]+\s*$', s):
        words = re.findall(r'[a-zA-Z]{3,}', s)
        if len(words) < 3 and not any(w.lower() in ('the', 'and', 'for', 'with') for w in words):
            return True
    # ── 长段中残片主导：大量图号标签 + 少量实质内容 → 噪音 ──
    # 统计 "图号行"（(a)、(c）、Discharge state: 等）
    label_lines = [
        l for l in s.split('\n') if re.match(r'^\s*[\(（][a-z][\)）]?\s*$', l.strip())
        or re.match(r'^\s*(discharge|charge)\s+state:\s*$', l.strip(), re.IGNORECASE)
    ]
    if len(label_lines) >= 3 and len(label_lines) / max(len(s.split('\n')), 1) > 0.3:
        return True

    # ── 极短的补充信息声明行（<100字，否则可能包含正文数据） ──
    if len(s) < 100 and re.search(r"(supplementary|supporting)\s+information", s, re.IGNORECASE):
        return True

    # ── 出版元数据行 ──
    if len(s) < 200 and re.match(r"^\s*(received|revised|accepted|published)\s*(online)?\s*:", s, re.IGNORECASE):
        return True
    if re.match(r"^\s*Published\s+online\s*:\s*\d+", s, re.IGNORECASE):
        return True

    # Keywords/关键词 行 → 噪音段过滤
    if re.match(r'^(Keywords|关键词)\s*[:：]', s.strip()):
        return True
    # 中文摘要（"摘要" / "摘要：" / 知网 "摘 要" 字间空格形式）
    if re.match(r'^(摘[\s　]*要|摘要|提要)\s*[:：]?', s.strip()):
        return True
    if is_zh:
        # ── 知网中文文献特有噪音（仅中文文档启用，不影响英文文献）──
        # 1) 英文摘要（与中文摘要内容重复，知网文献通常中英双份）
        if re.match(r"^(Abstract|Summary)\s*[:：]", s.strip()):
            return True
        # 2) doi 行（知网小写 "doi：10.19799/..."）
        if re.match(r"^doi\s*[：:]\s*10\.", s.strip()):
            return True
        # 3) 参考文献条目（GB/T 7714，含英文条目 "[1] ECE/..."、变音符号 "[13] ŠKORO"、数字 "[3] 2022"）
        if re.match(r"^\[\d+\]\s*[　\s]*\S", s) and len(s) < 800:
            return True
        # 4) 表格 HTML 单行（知网 MinerU 输出 <table>...</table>）
        if s.startswith("<table"):
            return True
        # 5) 图注碎片段：含 ≥2 个 (a)(b)(c) 图号标签的短段
        label_count = len(re.findall(r"[\(（][a-zA-Z0-9]+[\)）]", s[:200]))
        if label_count >= 2 and len(s) < 600:
            return True
    return False


# ── 剥离段落开头的作者信息前缀 ──
_LEADING_AUTHORS_RE = re.compile(
    r"""^
    (?:[A-Z][a-z]+(?:-[A-Z][a-z]+)?(?:\.[A-Z]\.)?\s+)  # 名 姓
    [A-Z][a-z]+(?:-[A-Z][a-z]+)?                         # 姓
    (?:[,&]?\s*(?:[0-9,\s]+)?[ ✉✝†‡*]*\s*)+             # 上标数字/符号
    (?=[A-Z][a-z])                                        # 后面紧跟正常正文
    """,
    re.VERBOSE,
)


def _strip_leading_authors(paragraph: str) -> str:
    """如果段落以作者名单开头，剥离这部分"""
    m = _LEADING_AUTHORS_RE.match(paragraph)
    if m:
        stripped = paragraph[m.end():]
        if len(stripped) > 40:
            return stripped.lstrip(", ")
    # 中文期刊格式作者列表：LAST_NAME First-name(中文名) 1, 2, ...
    # 形如 "HUANG Shao-zhen(黄绍祯) 1, 2 , HE Pan(贺盼) 1 , ... * Abstract: ..."
    if re.match(r'^[A-Z][A-Z\s-]+[A-Z][a-z]+(?:-[a-z]+)?\([^)]+\)\s*\d', paragraph):
        abstract_match = re.search(r'(Abstract|摘要)\s*[:：]\s*', paragraph)
        if abstract_match:
            clean = paragraph[abstract_match.end():]
            if len(clean) > 40:
                return clean
    return paragraph


# ==================== 主清洗函数 ====================

def clean_markdown(file_path: str, min_len: int = 200) -> Optional[str]:
    """清洗一篇 markdown 文献"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        print(f"[cleaner_v2] 读取失败 {file_path}: {e}")
        return None

    text = _expand_details(text)
    # 文档级中文检测：中文字符数 >= 200 才启用知网中文特有过滤规则。
    # 英文文献即使混入少量中文（如引用中文标题，实测最多 47 字）也不会被误判；
    # 知网中文文献实测最少 2382 字，阈值留足余量。
    is_zh = len(re.findall(r'[\u4e00-\u9fff]', text)) >= 200
    # 删除图片引用 + 其前面的 MinerU 图号标签（单字母 + 空格/换行）
    text = re.sub(r'''(?<![a-zA-Z0-9])[a-zA-Z]\s*\n?\s*!\[.*?\]\([^)]*\)\s*''', '', text)
    text = re.sub(r'!\[.*?\]\([^)]*\)\s*', '', text)


    paragraphs = text.split("\n\n")

    ref_idx = len(paragraphs)
    for i, para in enumerate(paragraphs):
        first_line = para.strip().split("\n")[0]
        if _REF_HEADER_RE.match(first_line) or (is_zh and _REF_HEADER_ZH_RE.match(first_line)):
            ref_idx = i
            break
    paragraphs = paragraphs[:ref_idx]

    cleaned = []
    for para in paragraphs:
        s = para.strip()
        if not s:
            continue
        if _is_noise_paragraph(s, is_zh):
            continue
        s = _strip_leading_authors(s)
        cleaned.append(s)

    cleaned = _merge_standalone_equations(cleaned)
    cleaned = _merge_table_captions(cleaned)

    keep_start = 0
    for i, p in enumerate(cleaned):
        first_line = p.split("\n")[0].strip()
        if len(p) > 80:
            intro_check = first_line.replace("#", "").strip()
            if re.match(r"^\d+\.?\s*(introduction|background|引言|前言|绪论)", intro_check, re.IGNORECASE):
                continue
            keep_start = i
            break
    cleaned = cleaned[keep_start:]

    if not cleaned:
        return None

    result = "\n\n".join(cleaned)
    return result if len(result) >= min_len else None


def clean_markdown_content(content: str, min_len: int = 200) -> Optional[str]:
    """直接清洗文本内容"""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                     delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp_path = f.name
    try:
        return clean_markdown(tmp_path, min_len=min_len)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ==================== 命令行测试入口 ====================

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="清洗单篇文献（独立版 v2）")
    p.add_argument("file", help="markdown 文件路径")
    p.add_argument("--min-len", type=int, default=200, help="最短字符数阈值（缺省200）")
    p.add_argument("-o", "--output", help="保存清洗结果到文件")
    args = p.parse_args()
    result = clean_markdown(args.file, min_len=args.min_len)
    if result:
        print(f"清洗后 {len(result)} 字符, {result.count(chr(10)+chr(10))+1} 段")
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(result)
            print(f"已保存: {args.output}")
        else:
            print("---前400字---")
            print(result[:400])
    else:
        print("清洗后无有效内容")
