"""
数据清洗模块 - 从 smart_chunking1.py 提取的清洗函数集合。

对学术文献 markdown 文本进行深度清洗：
- 过滤噪音行（图注、表注、参考文献、邮箱、URL等）
- 过滤 HTML 表格标签
- 去除地址/机构信息
- 按章节状态选择性保留内容
- 段落合并
- 特殊字符清洗

对外接口: clean_text(file_path) -> str
"""

import re
from typing import List, Optional
from bs4 import BeautifulSoup

# ==================== 配置常量 ====================

NOISE_KEYWORDS = [
    "graphical abstract",
    "entry for", "corresponding author", "author contributions",
    "received:", "revised:", "accepted:", "published:", "cite this",
    "read online", "access", "metrics & more", "article recommendations"
]

# 提取模式额外跳过的行（按段落级别过滤）
EXTRACT_SKIP_NOISE = ["keywords:", "highlights", "abstract", "toc graphic"]

# classify 模式不跳过任何章节（只按 END_SECTIONS 停止），extract 模式额外跳过
EXTRACT_SKIP_SECTIONS = ["abstract", "introduction", "intro", "background", "overview"]
KEEP_SECTIONS = [
    "experimental", "results", "discussion", "methods", "materials",
    "synthesis", "characterization", "electrochemical", "computation",
    "simulation", "supporting information", "supplementary",
]
END_SECTIONS = ["references", "bibliography", "acknowledgements", "acknowledgments",
                "author information", "author contributions", "corresponding author"]

# ==================== 内部工具函数 ====================

def is_noise_line(line: str, mode: str = "extract") -> bool:
    """判断是否为噪音行（图注、表注、参考文献、邮箱等）"""
    lower_line = line.lower().strip()
    stripped = line.strip()

    # HTML 注释
    if stripped.startswith('<!--') and stripped.endswith('-->'):
        return True

    # 1. 基础关键词
    for keyword in NOISE_KEYWORDS:
        if keyword in lower_line:
            return True
    if mode == "extract":
        for keyword in EXTRACT_SKIP_NOISE:
            if keyword in lower_line:
                return True

    # 2. 子图标记（单独的 A, B, C1, (a), (b) 等）
    if re.match(r'^\(?[a-zA-Z]\d?\)?$', stripped):
        return True

    # 3. 图注/表注
    if re.match(r'^(fig\.?|figure|tab\.?|table|scheme)\s*', lower_line):
        return True

    # 3. 图片标记 ![](...)
    if re.match(r'^!\[.*\]\(.*\)$', stripped):
        return True

    # 4. 表格分隔符
    if re.match(r'^[\s|:-]+$', stripped):
        return True

    # 5. 纯链接行
    if re.match(r'^https?://', stripped):
        return True

    # 6. 日期行
    if re.match(r'^(received|revised|accepted|published)\s*:', lower_line):
        return True

    # 7. 纯邮箱行
    if re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', stripped):
        return True

    # 8. 参考文献行识别（带编号且含年份或期刊特征）
    ref_start_pattern = r'^(\[\d+\]|\(\d+\)|\d+\.|\d+\))\s'
    if re.match(ref_start_pattern, stripped):
        has_year = re.search(r'20\d{2}', stripped)
        has_journal_hint = any(j in stripped for j in [
            'vol.', 'pp.', 'doi', 'lett.', 'mater.', 'chem.', 'sci.',
            'energy', 'nature', 'adv.', 'angew.', 'j.', 'trans.', 'comm.',
            'science', 'cell', 'pnas', 'acs', 'rsc', 'phys.', 'rev.', 'lett',
            'nanotech'
        ])
        if has_year or has_journal_hint:
            return True

    return False


def is_html_table_line(line: str) -> bool:
    """检测行内是否包含 HTML 表格标签"""
    html_tags = ['</table>', '<td>', '<tr>', '<tr>', '<th>', '<tbody>', '<thead>']
    lower_line = line.lower()
    return any(tag in lower_line for tag in html_tags)


def clean_urls_and_artifacts(text: str) -> str:
    """移除 URL 和子图标记"""
    text = re.sub(r'https?://[^\s]+', '', text)
    text = re.sub(r'(?:^|\s)([a-z]\.?\s){2,}', ' ', text, flags=re.IGNORECASE)
    tokens = text.split()
    cleaned = [t for t in tokens if not re.match(r'^\([a-z]\)[.,]?$', t, re.IGNORECASE)]
    return " ".join(cleaned).strip()


def is_address_line(line: str) -> bool:
    """判断是否为地址/机构行"""
    stripped = line.strip()
    if len(stripped) > 80:
        return False
    addr_keywords = [
        "department", "university", "institute", "college", "school",
        "republic of", "china", "usa", "korea", "email", "fax",
        "address", "laboratory", "center", "state key"
    ]
    lower_l = stripped.lower()
    if any(k in lower_l for k in addr_keywords):
        return True
    if re.match(r'^[A-Z]\.\s[A-Z]', stripped):
        return True
    if stripped.endswith(',') and len(stripped) < 50:
        return True
    return False


def get_section_status(header_text: str, mode: str = "extract") -> Optional[str]:
    """根据标题文本判断章节状态：'keep', 'skip', 'end', 或 None。
    classify 模式只检查 END_SECTIONS（不跳任何章节，保留引言做分类判断）。
    extract 模式额外检查 EXTRACT_SKIP_SECTIONS 跳过摘要/引言等。"""
    lower_h = header_text.lower()
    clean_h = re.sub(r'[^\w\s]', '', lower_h)
    for end_tag in END_SECTIONS:
        if end_tag in clean_h:
            return 'end'
    for keep_tag in KEEP_SECTIONS:
        if keep_tag in clean_h:
            return 'keep'
    if mode == "extract":
        for skip_tag in EXTRACT_SKIP_SECTIONS:
            if skip_tag in clean_h:
                return 'skip'
    # classify 模式：未识别的章节默认保留（包括 introduction）
    return None


# ==================== 核心清洗函数 ====================

def clean_and_merge_lines(lines: List[str], mode: str = "extract") -> List[str]:
    """
    清洗并合并行列表，返回段落列表。

    mode="classify": 保留标题/作者/摘要/引言，用于电池类型/组件类型分类
    mode="extract":  只保留实验/结果/讨论正文，用于材料/性能/条件提取
    """
    cleaned_paragraphs = []
    current_buffer = ""
    # extract 模式从 skip 开始（直到命中第一个 KEEP 章节），classify 从 keep 开始
    current_status = 'skip' if mode == 'extract' else 'keep'
    in_header_noise = True
    address_buffer = []
    hit_keep_section = False  # 是否命中过 KEEP 章节

    for line in lines:
        stripped = line.strip()
        if not stripped:
            # 空行：只在缓冲区积累足够（300+字符）时才作为段落边界
            if current_buffer and len(current_buffer) >= 500:
                cleaned_paragraphs.append(current_buffer)
                current_buffer = ""
            continue

        # 过滤确定噪音
        if is_noise_line(stripped, mode=mode) or is_html_table_line(stripped):
            if address_buffer:
                address_buffer = []
            continue

        # 标题行处理
        if stripped.startswith('#'):
            if current_buffer:
                cleaned_paragraphs.append(current_buffer)
                current_buffer = ""
            if address_buffer:
                address_buffer = []

            header_text = stripped.replace('#', '').strip()
            status = get_section_status(header_text, mode=mode)

            if mode == "extract" and status is None:
                # 提取模式：未识别的标题——如果还没进入正文则跳过，如果已在正文中则保持当前状态
                if current_status != 'keep':
                    current_status = 'skip'
                # 否则保持 keep（不覆盖已建立的正文状态）
            elif status == 'end':
                current_status = 'end'
            elif status == 'keep':
                current_status = 'keep'
                hit_keep_section = True
                if mode == "extract":
                    cleaned_paragraphs.append(stripped)
            elif status == 'skip':
                current_status = 'skip'
            else:
                if mode == "classify":
                    current_status = 'keep'
                    cleaned_paragraphs.append(stripped)

            in_header_noise = False
            continue

        if current_status in ('end', 'skip'):
            continue

        # 头部噪音区（作者单位等）处理
        if in_header_noise:
            if len(stripped) > 80 and not is_address_line(stripped):
                in_header_noise = False
                address_buffer = []
                current_buffer = stripped
            else:
                if is_address_line(stripped):
                    address_buffer.append(stripped)
                continue
        else:
            # 正文处理
            if is_address_line(stripped):
                if current_buffer:
                    cleaned_paragraphs.append(current_buffer)
                    current_buffer = ""
                address_buffer = []
                continue

            curr_text = clean_urls_and_artifacts(stripped)
            if not curr_text.strip():
                continue

            # 段落合并逻辑
            if not current_buffer:
                current_buffer = curr_text
            else:
                prev_text = current_buffer
                ends_with_punct = prev_text.rstrip()[-1] in ('.', '!', '?')
                starts_with_lower = curr_text[0].islower()
                if starts_with_lower or not ends_with_punct:
                    current_buffer += " " + curr_text
                else:
                    cleaned_paragraphs.append(current_buffer)
                    current_buffer = curr_text

    if current_buffer:
        cleaned_paragraphs.append(current_buffer)

    # Fallback: extract 模式下如果结果过短（<1000字符，说明正文被误跳过），保留正文
    total_len = sum(len(p) for p in cleaned_paragraphs)
    if mode == "extract" and (not hit_keep_section or total_len < 1000):
        # 用 classify 模式重新处理，然后去掉摘要/关键词段落
        fallback = []
        fb_status = 'keep'
        fb_header = True
        for line in lines:
            stripped = line.strip()
            if not stripped: continue
            if is_noise_line(stripped, mode="classify") or is_html_table_line(stripped): continue
            if stripped.startswith('#'):
                hdr = stripped.replace('#', '').strip()
                st = get_section_status(hdr, mode="classify")
                if st == 'end': fb_status = 'end'
                elif st == 'keep': fb_status = 'keep'
                else: fb_status = 'keep'
                if fb_status == 'keep':
                    fallback.append(stripped)
                fb_header = False
                continue
            if fb_status in ('end', 'skip'): continue
            # 跳过 ABSTRACT / KEYWORDS 段落
            if fb_header and re.match(r'^(abstract|keywords)\b', stripped, re.IGNORECASE):
                continue
            cleaned = clean_urls_and_artifacts(stripped)
            if cleaned.strip():
                fallback.append(cleaned)
            fb_header = False
        return fallback

    return cleaned_paragraphs


def extra_clean_text(text: str, min_length: int = 500) -> Optional[str]:
    """
    对已清洗合并的文本进行额外清洗：
    - 去除残留 HTML
    - 去除特殊标点符号
    - 合并多余空格
    - 替换无用模式
    """
    if not text:
        return None

    soup = BeautifulSoup(text, "html.parser")
    text = soup.get_text(separator=' ')

    import string
    stop = [item for item in string.punctuation if item not in (':', '/', '.', '-', ',', '%')]
    extra = ['', '', '', '', '', '', '', '', '', '', '', '', '', '�']
    stop = list(set(stop + extra))
    trans_table = str.maketrans('', '', ''.join(stop))
    text = text.translate(trans_table)

    text = re.sub(r'\s+', ' ', text)
    text = text.replace(' ac.', '~').replace(' a.c.', '~').replace(' a.c', '~')
    text = text.replace('', '').replace('', '')
    text = text.strip()

    if len(text) < min_length:
        return None
    return text


# ==================== SI 裁剪 ====================

SI_START_THRESHOLD = 10  # 检查前 N 行是否为 SI


def _trim_si_section(lines: List[str]) -> List[str]:
    """
    如果文件开头是 Supporting Information，裁剪到正文标题。
    返回裁剪后的行列表。
    """
    si_index = None
    next_title_index = None

    for i, line in enumerate(lines[:SI_START_THRESHOLD]):
        if line.strip().lower().startswith('# supporting information'):
            si_index = i
            for j in range(i + 1, len(lines)):
                stripped_j = lines[j].strip()
                if stripped_j.startswith('#'):
                    lower_j = stripped_j.lower()
                    skip_phrases = [
                        'supporting information', 'supporting figures',
                        'supporting tables', 'figure ', 'table '
                    ]
                    if not any(lower_j.startswith(p) for p in skip_phrases):
                        next_title_index = j
                        break
            break

    if si_index is not None and next_title_index is not None:
        return lines[next_title_index:]
    return lines


# ==================== 对外接口 ====================

def clean_text(file_path: str, min_text_len: int = 500, mode: str = "extract") -> Optional[str]:
    """
    读取 .md 文件，应用完整清洗流程。

    Args:
        file_path: markdown 文件路径
        min_text_len: 清洗后文本的最小长度，低于此值返回 None
        mode: "classify"(保留标题/作者/摘要/引言，用于分类Agent)
              或 "extract"(只保留实验/结果正文，用于提取Agent)

    Returns:
        清洗后的文本，或 None
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"读取文件失败 {file_path}: {e}")
        return None

    lines = _trim_si_section(lines)
    merged_paragraphs = clean_and_merge_lines(lines, mode=mode)
    full_text = "\n\n".join(merged_paragraphs)
    return extra_clean_text(full_text, min_length=min_text_len)


def clean_text_from_content(content: str, min_text_len: int = 500, mode: str = "extract") -> Optional[str]:
    lines = content.split('\n')
    lines = _trim_si_section(lines)
    merged_paragraphs = clean_and_merge_lines(lines, mode=mode)
    full_text = "\n\n".join(merged_paragraphs)
    return extra_clean_text(full_text, min_length=min_text_len)


def extract_title_from_markdown(file_path: str, fallback_text: str = "") -> str:
    """
    从 markdown 文件中提取标题（第一个 # 开头的非噪音行）。

    Args:
        file_path: markdown 文件路径
        fallback_text: 提取失败时的回退文本

    Returns:
        提取的标题字符串
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            raw = f.read()
        lines = raw.split('\n')
        skip_phrases = [
            'supporting information', 'supporting figures', 'supporting tables',
            'figure ', 'table ', 'fig.', 'appendix', 'supplementary'
        ]
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('# '):
                lower = stripped.lower()
                if any(lower.startswith(p) for p in skip_phrases):
                    continue
                return stripped[2:].strip()
        return fallback_text[:100] if fallback_text else ""
    except Exception:
        return fallback_text[:100] if fallback_text else ""


def extract_doi_from_content(content: str) -> Optional[str]:
    """
    从文本内容中提取 DOI。

    Args:
        content: 文本内容

    Returns:
        DOI 字符串，或 None
    """
    doi_pattern = r'\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b'
    matches = re.findall(doi_pattern, content, re.IGNORECASE)
    if matches:
        # 清理末尾标点
        doi = matches[0].rstrip('.,;:')
        return doi

    url_pattern = r'doi\.org/(10\.\d{4,9}/[-._;()/:A-Z0-9]+)'
    url_matches = re.findall(url_pattern, content, re.IGNORECASE)
    if url_matches:
        return url_matches[0].rstrip('.,;:')

    return None
