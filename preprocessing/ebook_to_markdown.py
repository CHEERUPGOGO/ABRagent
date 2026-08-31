#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电子书 PDF → Markdown 批量转换（基于 MinerU 精准解析 API batch 模式）

与 pdf_to_markdown.py 的区别:
  - 用 PyPDF2 精确定页，超过 200 页自动按 ≤200 页拆分
  - 每分片独立走 batch 上传+解析流程（与论文转换同款 API）
  - 同一电子书的分片集中在一个子文件夹，不保留图片
  - 文件名优先从 .md 内容中提取书名（fallback: PDF 文件名）
  - 配置独立：只复用 config.yaml 的 mineru 段，不影响论文转换

用法:
  python ebook_to_markdown.py
  python ebook_to_markdown.py --ebook-root ./papers/ebook/pdf --output-root ./papers/ebook/md
  python ebook_to_markdown.py --split 150 --max-mb 150
"""

import os, sys, re, time, hashlib, random, logging, argparse
from pathlib import Path
from typing import List

import requests

try:
    from PyPDF2 import PdfReader, PdfWriter
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ══════════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════════

def load_config(config_path: str = "config.yaml") -> dict:
    """只合并 mineru + processing 段，不使用 paths（避免跟论文转换路径冲突）"""
    config = {
        "mineru": {
            "token": "", "api_base_url": "https://mineru.net/api/v4",
            "model_version": "vlm", "language": "ch",
            "is_ocr": False, "enable_formula": True, "enable_table": True,
        },
        "ebook": {
            "pdf_root": "./papers/ebook/pdf",
            "output_root": "./papers/ebook/markdown",
        },
        "processing": {
            "max_pages_per_chunk": 200, "max_file_size_mb": 200,
            "poll_interval": 40, "max_retries": 5, "retry_delay": 10,
        },
        "logging": {"level": "INFO"},
    }
    if HAS_YAML and os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            yc = yaml.safe_load(f) or {}
        for sec in ["mineru", "processing"]:
            if sec in yc and isinstance(yc[sec], dict):
                config[sec].update(yc[sec])
    for env, (sec, key) in {
        "MINERU_TOKEN": ("mineru", "token"),
        "EBOOK_ROOT": ("ebook", "pdf_root"),
        "EBOOK_OUTPUT_ROOT": ("ebook", "output_root"),
    }.items():
        if os.environ.get(env):
            config[sec][key] = os.environ[env]
    for env, (sec, key) in {
        "MAX_PAGES_PER_CHUNK": ("processing", "max_pages_per_chunk"),
        "MAX_FILE_SIZE_MB": ("processing", "max_file_size_mb"),
    }.items():
        if os.environ.get(env):
            config[sec][key] = int(os.environ[env])
    return config


def setup_logging(level="INFO"):
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s [%(levelname)-7s] %(message)s", datefmt="%H:%M:%S")


def http_request(url, method="GET", headers=None, json=None, data=None,
                 max_retries=5, retry_delay=10.0, timeout=300):
    last = None
    for i in range(max_retries):
        try:
            if method == "GET":
                r = requests.get(url, headers=headers, timeout=timeout)
            elif method == "POST":
                r = requests.post(url, headers=headers, json=json, timeout=timeout)
            elif method == "PUT":
                r = requests.put(url, data=data, headers=headers, timeout=timeout)
            else:
                raise ValueError(f"unsupported method: {method}")
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", retry_delay)) + random.uniform(5, 15))
                continue
            return r
        except requests.exceptions.RequestException as e:
            last = e
            if i < max_retries - 1:
                time.sleep(retry_delay * (2 ** i) + random.uniform(0, 5))
    raise RuntimeError(f"HTTP retry exhausted") from last


# ══════════════════════════════════════════════════════════════════
# PDF 扫描
# ══════════════════════════════════════════════════════════════════

def count_pages(path: str) -> int:
    return len(PdfReader(path).pages)


def scan_pdfs(root: str, max_mb: int) -> List[str]:
    r = Path(root)
    ok, skip = [], 0
    for f in sorted(r.rglob("*.pdf")):
        mb = f.stat().st_size / 1048576
        if mb > max_mb:
            logging.warning("skip (>%dMB): %s (%.1fMB)", max_mb, f.name, mb)
            skip += 1
            continue
        ok.append(str(f))
    logging.info("found %d pdfs, %d skipped", len(ok), skip)
    return ok


# ══════════════════════════════════════════════════════════════════
# MinerU Batch Client（与 pdf_to_markdown.py 使用相同 API 流程）
# ══════════════════════════════════════════════════════════════════

class EbookClient:
    def __init__(self, cfg):
        m, p = cfg["mineru"], cfg["processing"]
        self.token = os.environ.get("MINERU_TOKEN") or m["token"]
        if not self.token:
            raise ValueError("No MINERU_TOKEN")
        self.api = m["api_base_url"]
        self.model = m["model_version"]
        self.lang = m.get("language", "ch")
        self.is_ocr = m.get("is_ocr", False)
        self.fml = m.get("enable_formula", True)
        self.tbl = m.get("enable_table", True)
        self.poll_int = p["poll_interval"]
        self.max_retry = p["max_retries"]
        self.retry_delay = p["retry_delay"]

    @property
    def _hdr(self):
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"}

    def submit_chunk(self, pdf: str, fname: str, sp: int, ep: int) -> str:
        """Upload file + create batch task for one page-range chunk. Returns batch_id."""
        did = hashlib.md5(f"{fname}_p{sp}-{ep}".encode()).hexdigest()
        pl = {
            "files": [{"name": fname, "data_id": did}],
            "model_version": self.model, "language": self.lang,
            "is_ocr": self.is_ocr, "enable_formula": self.fml, "enable_table": self.tbl,
            "page_range": f"{sp}-{ep}",
        }
        r = http_request(f"{self.api}/file-urls/batch", "POST", self._hdr, json=pl,
                         max_retries=self.max_retry, retry_delay=self.retry_delay)
        dat = r.json()
        if dat.get("code") != 0:
            raise RuntimeError(f"batch create failed: {dat.get('msg')}")
        bid = dat["data"]["batch_id"]
        urls = dat["data"]["file_urls"]
        time.sleep(random.uniform(3, 8))
        for u in urls:
            with open(pdf, "rb") as f:
                raw = f.read()
            pu = http_request(u, "PUT", data=raw, headers=None,
                              max_retries=self.max_retry, retry_delay=self.retry_delay, timeout=300)
            if not pu.ok:
                raise RuntimeError(f"upload failed HTTP {pu.status_code}")
            logging.info("uploaded %s (%d bytes)", fname, len(raw))
        return bid

    def poll_one(self, bid: str, label: str) -> dict:
        url = f"{self.api}/extract-results/batch/{bid}"
        h = {"Authorization": f"Bearer {self.token}"}
        while True:
            r = http_request(url, "GET", h, max_retries=self.max_retry, retry_delay=self.retry_delay)
            d = r.json()
            if d.get("code") != 0:
                raise RuntimeError(f"poll failed: {d.get('msg')}")
            items = d["data"]["extract_result"]
            if not items:
                raise RuntimeError("no extract_result")
            it = items[0]
            st = it.get("state", "?")
            if st == "done":
                logging.info("%s done", label)
                return it
            elif st == "failed":
                logging.error("%s failed: %s", label, it.get("err_msg", ""))
                return it
            logging.info("%s polling... %s", label, st)
            time.sleep(self.poll_int)

    def download_md(self, td: dict, out: Path) -> bool:
        zu = td.get("full_zip_url")
        if not zu:
            return False
        r = http_request(zu, "GET", max_retries=self.max_retry, retry_delay=self.retry_delay)
        import tempfile, zipfile
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as t:
            t.write(r.content)
            tp = t.name
        with zipfile.ZipFile(tp, "r") as z:
            md = [n for n in z.namelist() if n.endswith(".md")]
            if not md:
                os.unlink(tp)
                return False
            body = z.read(md[0]).decode("utf-8")
        os.unlink(tp)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        logging.info("saved %s (%d chars)", out.name, len(body))
        return True


# ══════════════════════════════════════════════════════════════════
# 书名提取
# ══════════════════════════════════════════════════════════════════

def book_title(md: str, fallback: str) -> str:
    for line in [l.strip() for l in md.split("\n") if l.strip() and not l.startswith("<")][:30]:
        if line.startswith("# "):
            t = line[2:].strip()
            if len(t) > 5:
                t = re.sub(r'[\\/:*?"<>|]', '_', t)
                t = re.sub(r'\s+', '_', t).strip('_')
                return t[:60]
    return Path(fallback).stem


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

def process_one(pdf: str, out_root: Path, cli: EbookClient, max_p: int, max_mb: int):
    nm = Path(pdf).name
    mb = Path(pdf).stat().st_size / 1048576
    logging.info("─" * 60)
    logging.info("processing: %s (%.1fMB)", nm, mb)
    if mb > max_mb:
        logging.warning("too big, skip")
        return
    total = count_pages(pdf)
    logging.info("total pages: %d", total)
    chunks = []
    for s in range(1, total + 1, max_p):
        e = min(s + max_p - 1, total)
        chunks.append((s, e))
    logging.info("%d chunks", len(chunks))

    # 物理拆分 PDF — MinerU 拒绝总页数>200的 PDF
    import tempfile
    with tempfile.TemporaryDirectory(prefix="ebook_split_") as tmp:
        tmp_dir = Path(tmp)
        chunk_paths = []
        for sp, ep in chunks:
            writer = PdfWriter()
            for i in range(sp - 1, ep):  # 0-indexed
                writer.add_page(PdfReader(pdf).pages[i])
            cp = tmp_dir / f"{Path(pdf).stem}_chunk_{sp}-{ep}.pdf"
            with open(cp, "wb") as f:
                writer.write(f)
            chunk_paths.append((sp, ep, str(cp)))
            logging.info("split: p%d-%d → %s (%.1fMB)", sp, ep, cp.name, cp.stat().st_size / 1_048_576)

        results = []
        for s, e, cpdf in chunk_paths:
            cname = Path(cpdf).name
            lb = f"{nm} p{s}-{e}"
            try:
                # 已拆分为小 PDF，不传 page_range 让 API 处理全部页
                bid = cli.submit_chunk(cpdf, cname, 1, e - s + 1)
                td = cli.poll_one(bid, lb)
                results.append((s, e, td))
            except Exception as ex:
                logging.error("chunk p%d-%d error: %s", s, e, ex)
                results.append((s, e, {"state": "failed", "err_msg": str(ex)}))
            time.sleep(3)

    # 书名提取
    first = next((td for _, _, td in results if td.get("state") == "done"), None)
    if first is None:
        logging.error("all chunks failed")
        return
    r = http_request(first["full_zip_url"], "GET", max_retries=cli.max_retry, retry_delay=cli.retry_delay)
    import tempfile, zipfile
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as t:
        t.write(r.content)
        tp = t.name
    with zipfile.ZipFile(tp, "r") as z:
        ml = [n for n in z.namelist() if n.endswith(".md")]
        title = book_title(z.read(ml[0]).decode("utf-8"), pdf) if ml else Path(pdf).stem
    os.unlink(tp)

    d = out_root / title
    d.mkdir(parents=True, exist_ok=True)
    logging.info("output dir: %s", d)
    for s, e, td in results:
        if td.get("state") != "done":
            logging.error("chunk p%d-%d failed, skip", s, e)
            continue
        cli.download_md(td, d / f"{title}_p{s:04d}-{e:04d}.md")
    logging.info("done: %s", nm)


def main():
    sd = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="eBook PDF → Markdown (MinerU batch)")
    ap.add_argument("-c", "--config", default=str(sd / "config.yaml"))
    ap.add_argument("--ebook-root", default=None)
    ap.add_argument("--output-root", default=None)
    ap.add_argument("--split", type=int, default=None, help="max pages/chunk (default 200)")
    ap.add_argument("--max-mb", type=int, default=None, help="max file size MB (default 200)")
    ap.add_argument("--log-level", default=None, choices=["DEBUG","INFO","WARNING","ERROR"])
    a = ap.parse_args()

    cfg = load_config(a.config)
    if a.ebook_root:  cfg["ebook"]["pdf_root"] = a.ebook_root
    if a.output_root: cfg["ebook"]["output_root"] = a.output_root
    if a.split:       cfg["processing"]["max_pages_per_chunk"] = a.split
    if a.max_mb:      cfg["processing"]["max_file_size_mb"] = a.max_mb
    if a.log_level:   cfg["logging"]["level"] = a.log_level

    setup_logging(cfg["logging"]["level"])
    proot = cfg["ebook"]["pdf_root"]
    oroot = Path(cfg["ebook"]["output_root"])
    mp = cfg["processing"]["max_pages_per_chunk"]
    mmb = cfg["processing"]["max_file_size_mb"]

    logging.info("=" * 54)
    logging.info("eBook PDF → Markdown")
    logging.info("pdf root:  %s", proot)
    logging.info("output:    %s", oroot)
    logging.info("chunk:     ≤%d pages", mp)
    logging.info("max size:  ≤%d MB", mmb)
    logging.info("=" * 54)

    if not HAS_PYPDF2:
        logging.error("need PyPDF2: pip install PyPDF2")
        sys.exit(1)
    try:
        cli = EbookClient(cfg)
    except ValueError as e:
        logging.error(str(e))
        sys.exit(1)

    pdfs = scan_pdfs(proot, mmb)
    if not pdfs:
        logging.warning("no valid pdfs"); return
    oroot.mkdir(parents=True, exist_ok=True)
    for p in pdfs:
        try:
            process_one(p, oroot, cli, mp, mmb)
        except Exception as e:
            logging.error("fail: %s — %s", Path(p).name, e)
    logging.info("done → %s", oroot)


if __name__ == "__main__":
    main()
