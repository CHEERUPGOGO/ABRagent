"""
用法：

    # 输出单个文件的清洗结果
    python miner/cleaning/test_clean.py -f database/type/test/Lithium_Ion_Metal_Battery/anode/anode.md --raw
    
    # 输出整个目录的清洗结果（每段 \n\n 分隔）
    python miner/cleaning/test_clean.py -i database/type/test/Lithium_Ion_Metal_Battery --raw
数据清洗模块 v1 — 从 clean_text.py 复制并修改

与原版 clean_text.py 的区别：
  1. 标题行独立成段，不与后续正文合并（标题作为独立段落输出）
  2. 标题下的多个正文段落合并为一个段落（空行不打断合并）
  3. 段落 \n\n 分隔符不会被擦除（逐段 extra_clean_text）
  4. 过滤 MinerU <details> 图表碎片块和 line/bar/scatter 等碎片行
  5. 当正文无 keep 标题但 SI 触发 keep 时，自动补充正文内容

对外接口: clean_text(file_path) -> str （与 clean_text.py 相同）
"""

import re
from typing import List, Optional
from bs4 import BeautifulSoup

NOISE_KEYWORDS = [
    "graphical abstract", "entry for", "corresponding author", "author contributions",
    "received:", "revised:", "accepted:", "published:", "cite this",
    "read online", "access", "metrics & more", "article recommendations"
]
EXTRACT_SKIP_NOISE = ["keywords:", "highlights", "abstract", "toc graphic"]
EXTRACT_SKIP_SECTIONS = ["abstract", "introduction", "intro", "background", "overview"]
KEEP_SECTIONS = [
    "experimental", "results", "discussion", "methods", "materials",
    "synthesis", "characterization", "electrochemical", "computation",
    "simulation", "supporting information", "supplementary",
]
END_SECTIONS = ["references", "bibliography", "acknowledgements", "acknowledgments",
                "author information", "author contributions", "corresponding author"]


MINERU_CHART_STARTS = (
    "line ", "bar ", "scatter ", "boxplot ",
    "textimage ", "naturalimage ", "chemical ",
)


def _is_mineru_chart_line(stripped: str) -> bool:
    """检测是否为 MinerU 图表转换的碎片数据行（如 line/bar/scatter 开头的坐标序列）"""
    lower = stripped.lower()
    for marker in MINERU_CHART_STARTS:
        if lower.startswith(marker):
            return True
    # mermaid / flowchart 代码块标记
    if re.match(r"^(`{3,})\s*(mermaid|flowchart)\b", lower):
        return True
    return False


def is_noise_line(line: str, mode: str = "extract") -> bool:
    lower_line, stripped = line.lower().strip(), line.strip()
    if stripped.startswith("<!--") and stripped.endswith("-->"): return True
    for kw in NOISE_KEYWORDS:
        if kw in lower_line: return True
    if mode == "extract":
        for kw in EXTRACT_SKIP_NOISE:
            if kw in lower_line: return True
    if re.match(r"^\(?[a-zA-Z]\d?\)?$", stripped): return True
    if re.match(r"^(fig\.?|figure|tab\.?|table|scheme)\s*", lower_line): return True
    if re.match(r"^!\[.*\]\(.*\)$", stripped): return True
    if re.match(r"^[\s|:-]+$", stripped): return True
    if re.match(r"^https?://", stripped): return True
    if re.match(r"^(received|revised|accepted|published)\s*:", lower_line): return True
    if re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", stripped): return True
    if re.match(r"^(\[\d+\]|\(\d+\)|\d+\.|\d+\))\s", stripped):
        has_year = re.search(r"20\d{2}", stripped)
        has_journal = any(j in stripped for j in [
            "vol.", "pp.", "doi", "lett.", "mater.", "chem.", "sci.",
            "energy", "nature", "adv.", "angew.", "j.", "trans.", "comm.",
            "science", "cell", "pnas", "acs", "rsc", "phys.", "rev.", "lett", "nanotech"])
        if has_year or has_journal: return True
    # MinerU 图表碎片
    if re.match(r"^\s*</?(?:details|summary)>\s*$", stripped, re.IGNORECASE): return True
    if _is_mineru_chart_line(stripped): return True
    return False


def is_html_table_line(line: str) -> bool:
    lower_line = line.lower()
    return any(tag in lower_line for tag in ["</table>", "<td>", "<tr>", "<th>", "<tbody>", "<thead>"])


def clean_urls_and_artifacts(text: str) -> str:
    text = re.sub(r"https?://[^\s]+", "", text)
    text = re.sub(r"(?:^|\s)([a-z]\.?\s){2,}", " ", text, flags=re.IGNORECASE)
    tokens = text.split()
    cleaned = [t for t in tokens if not re.match(r"^\([a-z]\)[.,]?$", t, re.IGNORECASE)]
    return " ".join(cleaned).strip()


def is_address_line(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) > 80: return False
    addr_kw = ["department", "university", "institute", "college", "school",
               "republic of", "china", "usa", "korea", "email", "fax",
               "address", "laboratory", "center", "state key"]
    lower_l = stripped.lower()
    if any(k in lower_l for k in addr_kw): return True
    if re.match(r"^[A-Z]\.\s[A-Z]", stripped): return True
    if stripped.endswith(",") and len(stripped) < 50: return True
    return False


def get_section_status(header_text: str, mode: str = "extract") -> Optional[str]:
    lower_h, clean_h = header_text.lower(), re.sub(r"[^\w\s]", "", header_text.lower())
    for tag in END_SECTIONS:
        if tag in clean_h: return "end"
    for tag in KEEP_SECTIONS:
        if tag in clean_h:
            return "end" if mode == "classify" else "keep"
    if mode == "extract":
        for tag in EXTRACT_SKIP_SECTIONS:
            if tag in clean_h: return "skip"
    return None


# ==================== 核心清洗（修复版） ====================

def clean_and_merge_lines(lines: List[str], mode: str = "extract") -> List[str]:
    cleaned_paragraphs, current_buffer = [], ""
    current_status = "skip" if mode == "extract" else "keep"
    in_header_noise, address_buffer = True, []
    hit_keep_section, is_first_heading = False, True

    for line in lines:
        stripped = line.strip()
        if not stripped:
            # 空行不打断段落合并 —— 标题下的多个段落合并为一个
            continue
        if is_noise_line(stripped, mode=mode) or is_html_table_line(stripped):
            if address_buffer: address_buffer = []
            continue

        if stripped.startswith("#"):
            # 标题出现时，先 flush 当前 buffer（前一个标题下的正文）
            if current_buffer:
                cleaned_paragraphs.append(current_buffer)
                current_buffer = ""
            address_buffer = []
            header_text = stripped.replace("#", "").strip()
            status = None if is_first_heading else get_section_status(header_text, mode=mode)
            is_first_heading = False

            if mode == "extract" and status is None:
                if current_status != "keep": current_status = "skip"
            elif status == "end": current_status = "end"
            elif status == "keep":
                current_status, hit_keep_section = "keep", True
                if mode == "extract": cleaned_paragraphs.append(stripped)  # 标题独立成段
            elif status == "skip": current_status = "skip"
            elif mode == "classify" and current_status != "end":
                current_status = "keep"
                if not re.match(r"^# \d", stripped): cleaned_paragraphs.append(stripped)

            in_header_noise = False
            continue

        if current_status in ("end", "skip"): continue

        if in_header_noise:
            if len(stripped) > 80 and not is_address_line(stripped):
                in_header_noise, address_buffer = False, []
                current_buffer = stripped
            else:
                if is_address_line(stripped): address_buffer.append(stripped)
            continue

        if is_address_line(stripped):
            if current_buffer: cleaned_paragraphs.append(current_buffer)
            current_buffer, address_buffer = "", []
            continue

        curr_text = clean_urls_and_artifacts(stripped)
        if not curr_text.strip(): continue

        if not current_buffer:
            if cleaned_paragraphs and not cleaned_paragraphs[-1].startswith("#"):
                # 前一个段落是正文（非标题），检查是否需要合并
                prev = cleaned_paragraphs[-1]
                ends_punct = prev.rstrip()[-1] in (".", "!", "?") if prev.rstrip() else False
                starts_lower = curr_text[0].islower() if curr_text else False
                if starts_lower or not ends_punct:
                    cleaned_paragraphs[-1] += " " + curr_text
                else:
                    current_buffer = curr_text
            else:
                # 前一个是标题或 cleaned_paragraphs 为空，开始新段落
                current_buffer = curr_text
        else:
            ends_punct = current_buffer.rstrip()[-1] in (".", "!", "?")
            starts_lower = curr_text[0].islower() if curr_text else False
            if starts_lower or not ends_punct:
                current_buffer += " " + curr_text
            else:
                cleaned_paragraphs.append(current_buffer)
                current_buffer = curr_text

    if current_buffer: cleaned_paragraphs.append(current_buffer)

    total_len = sum(len(p) for p in cleaned_paragraphs)
    if mode == "extract" and (not hit_keep_section or total_len < 1000):
        fallback = []
        fb_status, fb_header = "keep", True
        for line in lines:
            s = line.strip()
            if not s: continue
            if is_noise_line(s, mode="classify") or is_html_table_line(s): continue
            if s.startswith("#"):
                st = get_section_status(s.replace("#", "").strip(), mode="classify")
                fb_status = "end" if st == "end" else "keep"
                if fb_status == "keep": fallback.append(s)
                fb_header = False
                continue
            if fb_status in ("end", "skip"): continue
            if fb_header and re.match(r"^(abstract|keywords)\b", s, re.IGNORECASE): continue
            c = clean_urls_and_artifacts(s)
            if c.strip(): fallback.append(c)
            fb_header = False
        return fallback

    return cleaned_paragraphs


def _clean_single_paragraph(text: str) -> Optional[str]:
    if not text: return None
    soup = BeautifulSoup(text, "html.parser")
    text = soup.get_text(separator=" ")
    import string
    stop = [c for c in string.punctuation if c not in (":", "/", ".", "-", ",", "%", "#")]
    stop += ["", "", "", "", "", "", "", "", "", "", "", "", "", "�"]
    text = text.translate(str.maketrans("", "", "".join(set(stop))))
    text = re.sub(r"\s+", " ", text)
    text = text.replace(" ac.", "~").replace(" a.c.", "~").replace(" a.c", "~")
    text = text.replace("", "").replace("", "")
    # 修复 MinerU LaTeX 转换中被拆散的数值："1 0 0 0" → "1000", "2 . 9 4" → "2.94"
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    text = re.sub(r"(\d)\s*\.\s*(?=\d)", r"\1.", text)
    text = text.strip()
    return text if text else None


SI_START_THRESHOLD = 10


def _remove_details_blocks(lines: List[str]) -> List[str]:
    """移除所有 <details>...</details> 块（MinerU 图表碎片容器）"""
    result = []
    depth = 0
    for line in lines:
        if re.match(r"^\s*<details", line, re.IGNORECASE):
            depth += 1
            continue
        if re.match(r"^\s*</details>\s*$", line, re.IGNORECASE):
            depth = max(0, depth - 1)
            continue
        if depth > 0:
            continue
        result.append(line)
    return result


def _trim_si_section(lines: List[str]) -> List[str]:
    si_idx = None
    for i, line in enumerate(lines[:SI_START_THRESHOLD]):
        if line.strip().lower().startswith("# supporting information"):
            si_idx = i
            for j in range(i + 1, len(lines)):
                sj = lines[j].strip()
                if sj.startswith("#"):
                    lj = sj.lower()
                    if not any(lj.startswith(p) for p in [
                        "supporting information", "supporting figures",
                        "supporting tables", "figure ", "table "]): return lines[j:]
            break
    return lines


def _detect_si_first_keep(lines: List[str]) -> bool:
    """检测第一个 keep 标题是否来自 Supporting Information"""
    found_any = False
    for line in lines:
        if line.startswith("#"):
            h = line.replace("#", "").strip().lower()
            st = get_section_status(h, mode="extract")
            if st == "keep" and not found_any:
                found_any = True
                if any(kw in h for kw in ["supporting", "supplementary"]):
                    return True
    return False


def clean_text(file_path: str, min_text_len: int = 500, mode: str = "extract") -> Optional[str]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"读取文件失败 {file_path}: {e}"); return None

    lines = _trim_si_section(lines)
    lines = _remove_details_blocks(lines)

    merged = clean_and_merge_lines(lines, mode=mode)

    # 当正文无 keep 标题但 SI 触发 keep 时，用 classify 模式补充正文
    if mode == "extract" and merged and _detect_si_first_keep(lines):
        body_paras = clean_and_merge_lines(lines, mode="classify")
        if body_paras:
            # classify 模式不检查 EXTRACT_SKIP_NOISE，此处补做过滤
            body_paras = [
                p for p in body_paras
                if not any(kw in p.lower() for kw in EXTRACT_SKIP_NOISE)
            ]
            # body_paras（正文，到 SI 结束） + merged（SI 正文，从 SI 开始）
            # 去重合并：classify 以 SI 标题为 end，extract 以 SI 标题为 start
            seen = set(body_paras)
            for p in merged:
                if p not in seen:
                    seen.add(p)
                    body_paras.append(p)
            merged = body_paras

    if not merged: return None

    cleaned_paras = []
    for p in merged:
        cp = _clean_single_paragraph(p)
        if cp and (len(cp) > 100 or cp.startswith("#")): cleaned_paras.append(cp)

    if not cleaned_paras or sum(len(p) for p in cleaned_paras) < min_text_len: return None
    return "\n\n".join(cleaned_paras)


def clean_text_from_content(content: str, min_text_len: int = 500, mode: str = "extract") -> Optional[str]:
    lines = content.split("\n")
    lines = _trim_si_section(lines)
    lines = _remove_details_blocks(lines)

    merged = clean_and_merge_lines(lines, mode=mode)

    # 同 clean_text 的 SI fallback 逻辑
    if mode == "extract" and merged and _detect_si_first_keep(lines):
        body_paras = clean_and_merge_lines(lines, mode="classify")
        if body_paras:
            # classify 模式不检查 EXTRACT_SKIP_NOISE，此处补做过滤
            body_paras = [
                p for p in body_paras
                if not any(kw in p.lower() for kw in EXTRACT_SKIP_NOISE)
            ]
            seen = set(body_paras)
            for p in merged:
                if p not in seen:
                    seen.add(p)
                    body_paras.append(p)
            merged = body_paras

    if not merged: return None

    cleaned_paras = []
    for p in merged:
        cp = _clean_single_paragraph(p)
        if cp and (len(cp) > 100 or cp.startswith("#")): cleaned_paras.append(cp)

    if not cleaned_paras or sum(len(p) for p in cleaned_paras) < min_text_len: return None
    return "\n\n".join(cleaned_paras)
