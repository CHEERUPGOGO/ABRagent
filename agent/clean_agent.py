"""clean_agent: 短段落清洗，适合 LLM 提取
策略: clean_markdown + 空行切分 + 超长句号切分（同v6）"""
import re
from typing import List, Optional
from miner.cleaning.cleaner_v2_standalone import clean_markdown as _v2_clean
MIN = 100
CMAX = 2000
CTRIG = 3000
def _chunk(text: str) -> List[str]:
    if len(text) <= CTRIG:
        return [text]
    ss = text.replace("\n", " ").split(". ")
    r, buf = [], ""
    for s in ss:
        cand = (buf + ". " + s).strip()
        if len(cand) > CMAX and buf:
            r.append(buf.strip())
            buf = s
        else:
            buf = cand
    if buf:
        r.append(buf.strip())
    return r
def clean(fp: str) -> Optional[List[str]]:
    c = _v2_clean(fp, min_len=0)
    if not c:
        return None
    r = []
    for p in c.split("\n\n"):
        p = re.sub(r"[ \t]+", " ", p.strip())
        if len(p) > MIN:
            r.extend(_chunk(p))
    return r or None
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
