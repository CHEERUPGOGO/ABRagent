"""clean_rag token 版 — CMAX=2000 tokens / CTRIG=3000 tokens（DeepSeek 真实 tokenizer，对比字符版 rag_clean CMAX=2000 用）"""
import re
from typing import List, Optional
from miner.cleaning.cleaner_v2_standalone import clean_markdown as _v2_clean
from miner.pricing import estimate_tokens as _tok
MIN = 100
CMAX = 2000   # tokens
CTRIG = 3000  # tokens
_REF = re.compile(r"^\d+\.\s+[A-Z]")
_SENT = re.compile(r"(?<=[。.．])\s*")  # 中英文句号后切句
def _chunk(text: str) -> List[str]:
    if _tok(text) <= CTRIG:
        return [text]
    ss = [s for s in _SENT.split(text.replace("\n", " ")) if s.strip()]
    r, buf = [], ""
    for s in ss:
        cand = (buf + s).strip()
        if _tok(cand) > CMAX and buf:
            r.append(buf.strip())
            buf = s
        else:
            buf = cand
    if buf:
        r.append(buf.strip())
    return r
def _split_long(t: str) -> List[str]:
    if _tok(t) <= 3000:
        return [t]
    parts = re.split(r"(?=\d+\.\s+[A-Z])", t)
    return [p.strip() for p in parts if p.strip()] if len(parts) > 1 else [t]
def _merge(ps: List[str]) -> List[str]:
    if not ps:
        return ps
    m = [ps[0]]
    for p in ps[1:]:
        pv = m[-1]
        if pv.rstrip().endswith("$$"):
            cs = p.lstrip()
            st = cs[0] if cs else ""
            if st == '(':
                m[-1] = pv + " " + p
            else:
                m.append(p)
            continue
        pe = pv.rstrip()[-1] if pv.rstrip() else ""
        es = pe in (".", "!", "?")
        cs = p.lstrip()
        st = cs[0] if cs else ""
        bul = bool(re.match(r"^[\-\*•]\s", cs))
        num = bool(re.match(r"^\d+[\.\)]\s", cs))
        low = st.islower() if st else False
        tab = cs.startswith("<table") or cs.startswith("(Continued)")
        ptab = pv.lstrip().startswith("<table")
        ref = bool(_REF.match(cs))
        if tab or ptab:
            m.append(p)
        elif ref:
            m.append(p)
        elif re.match(r'^(ABSTRACT|摘要|Keywords|关键词)\s*', cs):
            m.append(p)
        elif not es or bul or num or low:
            m[-1] = pv + " " + p
        else:
            m.append(p)
    return m
def clean(fp: str) -> Optional[List[str]]:
    c = _v2_clean(fp, min_len=0)
    if not c:
        return None
    ps = []
    for ch in re.split(r"\n\s*\n+", c):
        p = re.sub(r"[ \t]+", " ", ch.strip())
        p = re.sub(r"\n+", " ", p)
        if len(p) >= MIN:
            ps.extend(_split_long(p))
    ps = _merge(ps)
    result = []
    for p in ps:
        result.extend(_chunk(p))
    return result or None
def clean_text(t: str) -> Optional[List[str]]:
    import tempfile
    from pathlib import Path
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(t)
        tmp = f.name
    try:
        return clean(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)
