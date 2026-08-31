"""公开数据集抓取 — 高比能液态锂电池设计方案系统（阶段 0）

三类来源，统一 provenance（source/url/fetched_at/sha256/license）落盘 data/raw/：

  mp         Materials Project API 快照（chemsys 化学体系查询；需 MP_API_KEY）
  betterbat  BetterBat Cell Database（TUM xlsx，校准 energy_model 经验区间）
  cdx        ChemDataExtractor 电池文献库（Cambridge repository，静态下载）

用法：
  python scripts/fetch_datasets.py mp        # 抓 MP 快照
  python scripts/fetch_datasets.py betterbat # 下载 BetterBat
  python scripts/fetch_datasets.py all       # 全部

环境变量：
  MP_API_KEY   Materials Project API key（mp 子命令必需）

说明：MP 的 formula 查询对多元素化学式匹配不稳定（NCM811 等返回 0 条），
改用 chemsys（元素集合）查询 + _fields 指定返回字段，实测可用。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# MP 查询清单：(chemsys 化学体系, 标签)。对应 candidates.json 的候选材料。
MP_QUERIES = [
    ("Li-Ni-Co-Mn-O", "NCM 高镍体系（NCM811 等）"),
    ("Li-Ni-Mn-O", "NCM 无钴 / LNMO 体系"),
    ("Li-Mn-O", "富锂锰基 / 锰酸锂体系"),
    ("C", "石墨 / 碳负极"),
    ("Si", "硅负极"),
    ("Si-O", "SiOx 负极"),
    ("Li", "锂金属"),
]

# summary 端点默认只返回 material_id，用 _fields 指定需要的性质字段
MP_FIELDS = (
    "material_id,formula_pretty,energy_above_hull,"
    "formation_energy_per_atom,band_gap,symmetry"
)

BETTERBAT_URL = (
    "https://raw.githubusercontent.com/TUMFTM/TechnoEconomicCellSelection/"
    "main/inputs/CellDatabase_v6.xlsx"
)
BETTERBAT_LICENSE = "MIT（TUMFTM 仓库协议，请以仓库 LICENSE 为准）"

MP_LICENSE = "CC-BY-4.0"
MP_BASE_URL = "https://api.materialsproject.org"


def _provenance(source: str, url: str, sha256: str, license_: str,
                version: str = "") -> dict:
    return {
        "source": source,
        "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": sha256,
        "license": license_,
        "version": version,
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch(url: str, timeout: int = 60, headers: dict = None) -> bytes:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "deepseek-tui"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_mp_snapshot(api_key: str, queries: list = None,
                      base_url: str = MP_BASE_URL, out_dir: Path = RAW_DIR) -> Path:
    """按 chemsys 清单查询 MP 材料摘要，落盘快照（带 provenance）。

    端点: {base}/materials/summary/?chemsys=...&_fields=...（next-gen API）。
    若 404/401，请核对 API key 与端点版本。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    records, errors = [], []
    for chemsys, label in (queries or MP_QUERIES):
        params = urllib.parse.urlencode({"chemsys": chemsys, "_fields": MP_FIELDS})
        url = f"{base_url}/materials/summary/?{params}"
        try:
            data = _fetch(url, headers={"X-API-KEY": api_key, "User-Agent": "deepseek-tui"})
            resp = json.loads(data)
            records.append({"chemsys": chemsys, "label": label, "response": resp})
            print(f"[mp] {chemsys:16s} ({label}): {len(resp.get('data', []))} 条记录")
        except Exception as e:
            errors.append({"chemsys": chemsys, "error": str(e)})
            print(f"[mp] {chemsys}: 失败 {e}")
        time.sleep(0.5)  # 限速
    stamp = datetime.now().strftime("%Y-%m-%d")
    payload = {
        "provenance": _provenance("materials-project", MP_BASE_URL,
                                  _sha256(json.dumps(records).encode()), MP_LICENSE,
                                  version=f"snapshot-{stamp}"),
        "queries": [{"chemsys": c, "label": l} for c, l in (queries or MP_QUERIES)],
        "fields": MP_FIELDS,
        "errors": errors,
        "records": records,
    }
    out = out_dir / f"mp_snapshot_{stamp}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[mp] 快照已保存: {out}")
    return out


def fetch_betterbat(out_dir: Path = RAW_DIR) -> Path:
    """下载 BetterBat Cell Database xlsx，记录 provenance。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    data = _fetch(BETTERBAT_URL)
    out = out_dir / "CellDatabase_v6.xlsx"
    out.write_bytes(data)
    prov = _provenance("betterbat", BETTERBAT_URL, _sha256(data), BETTERBAT_LICENSE)
    (out_dir / "CellDatabase_v6.provenance.json").write_text(
        json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[betterbat] 已下载 {len(data)} bytes → {out}")
    return out


def fetch_cdx(url: str = None, out_dir: Path = RAW_DIR) -> Path:
    """下载 ChemDataExtractor 电池文献库（静态发布，URL 可配置）。

    数据集发布在 Cambridge repository（repository.cam.ac.uk）。
    需人工确认发布页的最新下载链接后传入 --url。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if not url:
        print("[cdx] 未提供下载 URL。请在 Cambridge repository 定位 "
              "ChemDataExtractor battery database 的发布文件后使用 --url。")
        sys.exit(1)
    data = _fetch(url, timeout=300)
    fname = Path(urllib.parse.urlparse(url).path).name or "cdx_battery.zip"
    out = out_dir / fname
    out.write_bytes(data)
    prov = _provenance("chemdataextractor-battery", url, _sha256(data), "待确认")
    (out_dir / f"{out.stem}.provenance.json").write_text(
        json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[cdx] 已下载 {len(data)} bytes → {out}")
    return out


def main():
    p = argparse.ArgumentParser(description="阶段0 公开数据集抓取")
    p.add_argument("task", choices=["mp", "betterbat", "cdx", "all"])
    p.add_argument("--url", help="cdx 任务: 数据集下载直链")
    p.add_argument("--out-dir", default=str(RAW_DIR))
    args = p.parse_args()
    out_dir = Path(args.out_dir)

    if args.task in ("mp", "all"):
        key = os.environ.get("MP_API_KEY")
        if not key:
            print("[mp] 缺少 MP_API_KEY 环境变量，跳过。"
                  "注册: https://materialsproject.org/api")
        else:
            fetch_mp_snapshot(key, out_dir=out_dir)
    if args.task in ("betterbat", "all"):
        try:
            fetch_betterbat(out_dir)
        except Exception as e:
            print(f"[betterbat] 下载失败: {e}")
    if args.task in ("cdx", "all"):
        fetch_cdx(args.url, out_dir)


if __name__ == "__main__":
    main()
