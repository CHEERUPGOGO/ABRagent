#!/usr/bin/env python3
"""
PDF -> Markdown 转换工具（基于 MinerU 精准解析 API）

功能:
  - 批量将 PDF 文件通过 MinerU 精准解析 API 转换为 Markdown 格式
  - 使用批量文件上传接口 /api/v4/file-urls/batch + OSS 签名上传
  - 支持嵌套目录结构和扁平目录结构
  - 自动重试 + 指数退避 + 速率限制
  - GPU 加速优先（通过 vlm 模型版本）
  - 支持 OCR 语言、公式识别、表格识别等精细化解析参数
  - TOKEN 从环境变量或配置文件读取，防止泄露

用法:
  python pdf_to_markdown.py                           # 使用默认配置
  python pdf_to_markdown.py -c config.yaml            # 指定配置文件
  python pdf_to_markdown.py --pdf-root ./my_pdfs      # 覆盖 PDF 路径
  python pdf_to_markdown.py --batch-size 30           # 覆盖批次大小

API 参考: https://mineru.net/apiManage/docs
"""

import os
import sys
import time
import hashlib
import zipfile
import shutil
import random
import logging
import argparse
from pathlib import Path
from typing import List, Tuple, Dict, Optional

import requests

# ---------------------------------------------------------------------------
# 尝试加载 YAML 配置（如果没有 PyYAML 则回退到纯环境变量模式）
# ---------------------------------------------------------------------------
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
    logging.warning("PyYAML 未安装，仅支持环境变量配置。安装命令: pip install pyyaml")


# =============================================================================
# 配置加载
# =============================================================================

def load_config(config_path: str = "config.yaml") -> dict:
    """
    加载配置文件，优先级：
      1. 命令行参数（在 main 中覆盖）
      2. 环境变量
      3. 配置文件 config.yaml
      4. 内置默认值
    """
    config = {
        # --- 默认值 ---
        "mineru": {
            "token": "",
            "api_base_url": "https://mineru.net/api/v4",
            "model_version": "vlm",
            "use_gpu": True,
            # OCR 与解析参数
            "language": "ch",
            "is_ocr": False,
            "enable_formula": True,
            "enable_table": True,
            "page_range": "",
        },
        "paths": {
            "pdf_root": "./papers/pdf/test",
            "output_root": "./papers/markdown/test",
        },
        "processing": {
            "batch_size": 50,
            "poll_interval": 40,
            "max_retries": 5,
            "retry_delay": 10,
            "batch_delay": 30,
            "auto_merge": False,
        },
        "logging": {
            "level": "INFO",
        },
    }

    # 从 YAML 配置文件加载
    if HAS_YAML and os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f) or {}
        _deep_merge(config, yaml_config)

    # 环境变量覆盖（最高优先级）
    _env_override(config)

    return config


def _deep_merge(base: dict, override: dict) -> None:
    """递归合并 override 到 base 中（原地修改 base）。"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _env_override(config: dict) -> None:
    """用环境变量覆盖配置值。"""
    # 布尔类型需要特殊处理的 key
    _bool_keys = {"use_gpu", "is_ocr", "enable_formula", "enable_table"}
    _int_keys = {"batch_size", "poll_interval", "max_retries", "retry_delay", "batch_delay"}

    env_map = {
        # MinerU 核心
        "MINERU_TOKEN":            ("mineru", "token"),
        "MINERU_API_BASE_URL":     ("mineru", "api_base_url"),
        "MINERU_MODEL_VERSION":    ("mineru", "model_version"),
        "MINERU_USE_GPU":          ("mineru", "use_gpu"),
        # OCR 与解析参数
        "MINERU_LANGUAGE":         ("mineru", "language"),
        "MINERU_IS_OCR":           ("mineru", "is_ocr"),
        "MINERU_ENABLE_FORMULA":   ("mineru", "enable_formula"),
        "MINERU_ENABLE_TABLE":     ("mineru", "enable_table"),
        "MINERU_PAGE_RANGE":       ("mineru", "page_range"),
        # 路径
        "PDF_ROOT":                ("paths", "pdf_root"),
        "OUTPUT_ROOT":             ("paths", "output_root"),
    }

    for env_var, (section, key) in env_map.items():
        val = os.environ.get(env_var)
        if val is not None:
            if key in _bool_keys:
                val = val.lower() in ("true", "1", "yes")
            elif key in _int_keys:
                val = int(val)
            config[section][key] = val


# =============================================================================
# 日志设置
# =============================================================================

def setup_logging(level: str = "INFO") -> None:
    """配置结构化日志。"""
    fmt = "%(asctime)s [%(levelname)-7s] %(message)s"
    datefmt = "%H:%M:%S"
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=fmt, datefmt=datefmt)


# =============================================================================
# HTTP 请求（带重试 + 指数退避）
# =============================================================================

def http_request(
    url: str,
    method: str = "GET",
    headers: Optional[Dict] = None,
    json: Optional[Dict] = None,
    data: Optional[bytes] = None,
    max_retries: int = 5,
    retry_delay: float = 10.0,
    timeout: int = 60,
) -> requests.Response:
    """
    带重试机制的 HTTP 请求。
    - 遇到 429 自动等待 Retry-After + 随机延迟
    - 网络错误使用指数退避重试
    """
    last_exception = None

    for attempt in range(max_retries):
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, timeout=timeout)
            elif method == "POST":
                resp = requests.post(url, headers=headers, json=json, timeout=timeout)
            elif method == "PUT":
                resp = requests.put(url, data=data, headers=headers, timeout=timeout)
            else:
                raise ValueError(f"不支持的 HTTP 方法: {method}")

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", retry_delay))
                jitter = random.uniform(5, 15)
                wait = retry_after + jitter
                logging.warning("收到 429 限流，等待 %.1f 秒后重试...", wait)
                time.sleep(wait)
                continue

            return resp

        except requests.exceptions.RequestException as e:
            last_exception = e
            logging.error("请求失败 (attempt %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                delay = retry_delay * (2 ** attempt) + random.uniform(0, 5)
                logging.info("等待 %.1f 秒后重试...", delay)
                time.sleep(delay)

    raise RuntimeError(f"HTTP 请求失败，已重试 {max_retries} 次") from last_exception


# =============================================================================
# PDF 扫描
# =============================================================================

def scan_pdfs(pdf_root: str) -> List[Tuple[str, str]]:
    """
    递归扫描所有 PDF 文件，返回 [(pdf_path, relative_dir), ...]。
    支持任意深度的嵌套结构。
    - relative_dir: PDF 文件所在目录的相对路径（相对于 pdf_root）
    - 如果 PDF 直接在根目录下，relative_dir 为空字符串
    示例:
      pdf_root/1110/242/10.1002_xxx/file.pdf  -> rel = "1110/242/10.1002_xxx"
      pdf_root/file.pdf                       -> rel = ""
    """
    results: List[Tuple[str, str]] = []
    root = Path(pdf_root)

    if not root.is_dir():
        logging.error("PDF 根目录不存在: %s", pdf_root)
        return results

    for pdf_file in sorted(root.rglob("*.pdf")):
        rel_dir = str(pdf_file.parent.relative_to(root))
        if rel_dir == ".":
            rel_dir = ""
        results.append((str(pdf_file), rel_dir))

    if not results:
        logging.warning("在 %s 中未找到任何 PDF 文件", pdf_root)
    else:
        depths = set(len(Path(r[1]).parts) for r in results if r[1])
        if not depths:
            logging.info("扁平目录结构，找到 %d 个 PDF", len(results))
        else:
            logging.info("嵌套目录结构（最深 %d 层），找到 %d 个 PDF", max(depths), len(results))

    return results


# =============================================================================
# MinerU API 交互
# =============================================================================

class MinerUClient:
    """MinerU 精准解析 API 客户端封装。

    支持两种解析模式：
      - 批量模式 (默认): 通过 /api/v4/file-urls/batch 批量上传，轮询批量结果
      - 单文件模式: 通过 /api/v4/extract/task 逐个提交（需文件已有公开 URL）
    """

    def __init__(self, config: dict):
        mineru = config["mineru"]
        proc = config["processing"]

        # TOKEN 优先级: 环境变量 > 配置文件
        self.token = os.environ.get("MINERU_TOKEN") or mineru.get("token", "")
        if not self.token:
            raise ValueError(
                "未设置 MinerU TOKEN！请通过以下方式之一设置:\n"
                "  1. 环境变量: export MINERU_TOKEN='your_token'\n"
                "  2. 配置文件: 在 config.yaml 中填写 mineru.token"
            )

        self.api_base = mineru["api_base_url"]
        self.model_version = mineru["model_version"]
        self.use_gpu = mineru.get("use_gpu", True)

        # OCR 与解析参数（精准解析 API 支持）
        self.language = mineru.get("language", "ch")
        self.is_ocr = mineru.get("is_ocr", False)
        self.enable_formula = mineru.get("enable_formula", True)
        self.enable_table = mineru.get("enable_table", True)
        self.page_range = mineru.get("page_range", "")

        self.batch_size = proc["batch_size"]
        self.poll_interval = proc["poll_interval"]
        self.max_retries = proc["max_retries"]
        self.retry_delay = proc["retry_delay"]
        self.batch_delay = proc["batch_delay"]

        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

        if self.use_gpu:
            logging.info("GPU 加速模式已启用 (model_version=%s)", self.model_version)
        else:
            logging.info("CPU 模式 (model_version=%s)", self.model_version)
        logging.info(
            "解析参数: language=%s, is_ocr=%s, enable_formula=%s, enable_table=%s, page_range=%s",
            self.language, self.is_ocr, self.enable_formula, self.enable_table,
            self.page_range or "(全部)"
        )

    # ------------------------------------------------------------------
    # 批量模式：批量上传 + 批量轮询
    # ------------------------------------------------------------------

    def _build_batch_payload(self, files_param: list) -> dict:
        """构建批量上传请求体，包含所有解析参数。"""
        payload = {
            "files": files_param,
            "model_version": self.model_version,
            "language": self.language,
            "is_ocr": self.is_ocr,
            "enable_formula": self.enable_formula,
            "enable_table": self.enable_table,
        }
        if self.page_range:
            payload["page_range"] = self.page_range
        return payload

    def batch_upload(self, pdf_info_list: List[Tuple[str, str]]) -> Tuple[str, Dict[str, Tuple[str, str]]]:
        """
        批量获取上传 URL 并上传 PDF 文件。

        流程:
          1. POST /api/v4/file-urls/batch  获取 batch_id + OSS 签名上传 URL
          2. PUT 每个 PDF 文件到对应的 OSS URL
          3. 上传完成后 MinerU 自动开始解析

        返回: (batch_id, file_map)
        """
        url = f"{self.api_base}/file-urls/batch"

        files_param = []
        file_map: Dict[str, Tuple[str, str]] = {}

        for pdf_path, relative_dir in pdf_info_list:
            file_name = Path(pdf_path).name
            # 用 MD5 生成稳定的 data_id，便于追踪和去重
            hash_source = f"{relative_dir}_{file_name}".encode("utf-8")
            data_id = hashlib.md5(hash_source).hexdigest()

            files_param.append({"name": file_name, "data_id": data_id})
            file_map[data_id] = (pdf_path, relative_dir)

        payload = self._build_batch_payload(files_param)

        logging.info("提交批量上传请求: %d 个文件", len(files_param))
        resp = http_request(
            url, method="POST", headers=self._headers, json=payload,
            max_retries=self.max_retries, retry_delay=self.retry_delay
        )
        result = resp.json()

        if result.get("code") != 0:
            raise RuntimeError(f"获取上传 URL 失败: {result.get('msg', 'unknown')}")

        batch_id = result["data"]["batch_id"]
        upload_urls = result["data"]["file_urls"]
        logging.info("批量任务创建成功: batch_id=%s, 文件数=%d", batch_id, len(upload_urls))

        # 上传文件到 OSS
        time.sleep(random.uniform(5, 10))  # 短暂冷却，避免限流

        for idx, upload_url in enumerate(upload_urls):
            data_id = files_param[idx]["data_id"]
            pdf_path, _ = file_map[data_id]
            try:
                with open(pdf_path, "rb") as f:
                    file_data = f.read()
                # OSS 预签名 URL 对请求头敏感，不传额外 headers
                put_resp = http_request(
                    upload_url, method="PUT", data=file_data,
                    headers=None,
                    max_retries=self.max_retries, retry_delay=self.retry_delay,
                    timeout=120,
                )
                if not put_resp.ok:
                    logging.error("上传失败 [HTTP %d]: %s — %s",
                                  put_resp.status_code, Path(pdf_path).name,
                                  put_resp.text[:200])
                    continue
                logging.info("上传成功: %s (%d bytes)", Path(pdf_path).name, len(file_data))

                # 每上传 5 个文件暂停一下
                if (idx + 1) % 5 == 0:
                    time.sleep(random.uniform(5, 10))

            except Exception as e:
                logging.error("上传失败: %s — %s", Path(pdf_path).name, e)

        return batch_id, file_map

    # ------------------------------------------------------------------
    # 批量轮询
    # ------------------------------------------------------------------

    def poll_status(self, batch_id: str) -> List[dict]:
        """轮询批次解析状态，直到全部完成或失败。

        调用 GET /api/v4/extract-results/batch/{batch_id}
        返回 extract_result 列表，每项包含: data_id, state, full_zip_url, err_msg 等

        状态值包括: waiting-file, running, done, failed 等
        """
        url = f"{self.api_base}/extract-results/batch/{batch_id}"
        headers = {"Authorization": f"Bearer {self.token}"}

        while True:
            resp = http_request(
                url, method="GET", headers=headers,
                max_retries=self.max_retries, retry_delay=self.retry_delay
            )
            result = resp.json()

            if result.get("code") != 0:
                raise RuntimeError(f"查询状态失败: {result.get('msg', 'unknown')}")

            extract_results = result["data"]["extract_result"]
            total = len(extract_results)

            # 按状态分组统计
            from collections import Counter
            state_counts = Counter(r["state"] for r in extract_results)

            done_count = state_counts.get("done", 0)
            failed_count = state_counts.get("failed", 0)

            if all(r["state"] in ("done", "failed") for r in extract_results):
                logging.info("全部解析完成: done=%d, failed=%d, total=%d",
                             done_count, failed_count, total)
                # 打印失败文件的错误信息，便于排查
                for r in extract_results:
                    if r["state"] == "failed":
                        logging.warning("  失败文件 data_id=%s: %s",
                                        r.get("data_id"), r.get("err_msg", ""))
                return extract_results

            # 显示所有状态分布
            status_str = ", ".join(f"{st}={cnt}" for st, cnt in state_counts.most_common())
            logging.info("解析中... total=%d | %s", total, status_str)
            time.sleep(self.poll_interval)

    # ------------------------------------------------------------------
    # 单文件模式：通过 /api/v4/extract/task 提交（需文件已有公开 URL）
    # ------------------------------------------------------------------

    def submit_single_file(self, file_url: str, file_name: str = "") -> str:
        """通过单文件 API 提交一个解析任务。

        适用场景:
          - 文件已托管在可公开访问的 URL（OSS / CDN / 对象存储）
          - 批量上传中个别文件失败后，单独重试

        参数:
          file_url: 文件的公开访问 URL
          file_name: 文件名（仅用于日志）

        返回: task_id
        """
        url = f"{self.api_base}/extract/task"
        payload = {
            "url": file_url,
            "model_version": self.model_version,
            "language": self.language,
            "is_ocr": self.is_ocr,
            "enable_formula": self.enable_formula,
            "enable_table": self.enable_table,
        }
        if self.page_range:
            payload["page_range"] = self.page_range

        logging.info("提交单文件解析: %s", file_name or file_url)
        resp = http_request(
            url, method="POST", headers=self._headers, json=payload,
            max_retries=self.max_retries, retry_delay=self.retry_delay
        )
        result = resp.json()

        if result.get("code") != 0:
            raise RuntimeError(f"提交单文件解析失败: {result.get('msg', 'unknown')}")

        task_id = result["data"]["task_id"]
        logging.info("单文件任务已创建: task_id=%s", task_id)
        return task_id

    def poll_single_task(self, task_id: str) -> dict:
        """轮询单个解析任务直到完成。

        调用 GET /api/v4/extract/task/{task_id}
        返回 data 字段，包含: state, full_zip_url, err_msg 等
        """
        url = f"{self.api_base}/extract/task/{task_id}"
        headers = {"Authorization": f"Bearer {self.token}"}

        while True:
            resp = http_request(
                url, method="GET", headers=headers,
                max_retries=self.max_retries, retry_delay=self.retry_delay
            )
            result = resp.json()

            if result.get("code") != 0:
                raise RuntimeError(f"查询单文件状态失败: {result.get('msg', 'unknown')}")

            task_data = result["data"]
            state = task_data.get("state", "unknown")

            if state == "done":
                logging.info("单文件解析完成: task_id=%s", task_id)
                return task_data
            elif state == "failed":
                logging.error("单文件解析失败: task_id=%s, err_msg=%s",
                              task_id, task_data.get("err_msg", ""))
                return task_data

            logging.info("单文件解析中... task_id=%s, state=%s", task_id, state)
            time.sleep(self.poll_interval)

    # ------------------------------------------------------------------
    # 下载与保存
    # ------------------------------------------------------------------

    def download_and_save(self, extract_results: List[dict],
                          file_map: Dict[str, Tuple[str, str]],
                          output_root: str) -> None:
        """下载 ZIP 包并解压为 Markdown + 图片。

        对每个解析完成的文件:
          1. 下载 full_zip_url 指向的 ZIP 包
          2. 解压提取 .md 文件（Markdown 正文）
          3. 提取图片并替换 Markdown 中的图片路径为本地相对路径
          4. 清理临时 ZIP 文件
        """
        Path(output_root).mkdir(parents=True, exist_ok=True)

        img_exts = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

        for res in extract_results:
            data_id = res["data_id"]
            state = res["state"]
            full_zip_url = res.get("full_zip_url")
            err_msg = res.get("err_msg", "")

            if state != "done" or not full_zip_url:
                logging.warning("跳过失败文件: data_id=%s, reason=%s", data_id, err_msg)
                continue

            entry = file_map.get(data_id)
            if not entry:
                logging.warning("未找到 data_id 映射: %s", data_id)
                continue

            pdf_path, relative_dir = entry
            file_name = Path(pdf_path).name
            base_name = Path(pdf_path).stem

            # 确定保存路径
            if relative_dir:
                md_save_dir = Path(output_root) / relative_dir
                # 图片文件夹: 最后一级目录名 + "images"
                images_dir_name = f"{Path(relative_dir).name}images"
            else:
                md_save_dir = Path(output_root)
                images_dir_name = f"{base_name}_images"

            md_save_dir.mkdir(parents=True, exist_ok=True)
            images_dir = md_save_dir / images_dir_name
            images_dir.mkdir(parents=True, exist_ok=True)

            md_save_path = md_save_dir / f"{base_name}.md"

            try:
                timestamp = int(time.time())
                temp_zip = md_save_dir / f"temp_{base_name}_{timestamp}.zip"

                # 下载 ZIP
                resp = http_request(full_zip_url, method="GET",
                                    max_retries=self.max_retries,
                                    retry_delay=self.retry_delay)
                temp_zip.write_bytes(resp.content)

                with zipfile.ZipFile(temp_zip, "r") as zf:
                    # 查找 Markdown 文件
                    md_files = [f for f in zf.namelist() if f.endswith(".md")]
                    if not md_files:
                        logging.warning("压缩包中无 .md 文件: %s", full_zip_url)
                        temp_zip.unlink()
                        continue

                    md_content = zf.read(md_files[0]).decode("utf-8")

                    # 提取图片并替换路径
                    img_count = 0
                    for img_entry in zf.namelist():
                        if not img_entry.lower().endswith(img_exts):
                            continue
                        img_name = Path(img_entry).name
                        img_data = zf.read(img_entry)
                        img_path = images_dir / img_name
                        img_path.write_bytes(img_data)

                        # 替换 Markdown 中的图片路径
                        new_rel = f"{images_dir_name}/{img_name}".replace("\\", "/")
                        md_content = md_content.replace(img_entry, new_rel)
                        md_content = md_content.replace(f"./{img_entry}", new_rel)
                        img_count += 1

                    md_save_path.write_text(md_content, encoding="utf-8")

                temp_zip.unlink()
                logging.info("处理成功 [%d 张图片]: %s", img_count, md_save_path)

            except Exception as e:
                logging.error("处理失败 [%s]: %s", file_name, e)
                if temp_zip.exists():
                    temp_zip.unlink()

    def download_and_save_single(self, task_data: dict, file_name: str,
                                 output_dir: str) -> Optional[str]:
        """下载单个解析任务的 ZIP 并保存为 Markdown。

        适用于 submit_single_file + poll_single_task 的工作流。

        返回: 保存的 .md 文件路径，失败则返回 None
        """
        full_zip_url = task_data.get("full_zip_url")
        if not full_zip_url:
            logging.error("单文件结果中无下载链接: %s", file_name)
            return None

        base_name = Path(file_name).stem
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        md_path = out_dir / f"{base_name}.md"
        img_dir = out_dir / f"{base_name}_images"
        img_dir.mkdir(parents=True, exist_ok=True)
        img_exts = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

        try:
            timestamp = int(time.time())
            temp_zip = out_dir / f"temp_{base_name}_{timestamp}.zip"

            resp = http_request(full_zip_url, method="GET",
                                max_retries=self.max_retries,
                                retry_delay=self.retry_delay)
            temp_zip.write_bytes(resp.content)

            with zipfile.ZipFile(temp_zip, "r") as zf:
                md_files = [f for f in zf.namelist() if f.endswith(".md")]
                if not md_files:
                    logging.warning("压缩包中无 .md 文件")
                    temp_zip.unlink()
                    return None

                md_content = zf.read(md_files[0]).decode("utf-8")

                img_dir_name = f"{base_name}_images"
                for img_entry in zf.namelist():
                    if not img_entry.lower().endswith(img_exts):
                        continue
                    img_name = Path(img_entry).name
                    img_path = img_dir / img_name
                    img_path.write_bytes(zf.read(img_entry))
                    new_rel = f"{img_dir_name}/{img_name}".replace("\\", "/")
                    md_content = md_content.replace(img_entry, new_rel)
                    md_content = md_content.replace(f"./{img_entry}", new_rel)

                md_path.write_text(md_content, encoding="utf-8")

            temp_zip.unlink()
            logging.info("单文件处理成功: %s", md_path)
            return str(md_path)

        except Exception as e:
            logging.error("单文件处理失败 [%s]: %s", file_name, e)
            if temp_zip.exists():
                temp_zip.unlink()
            return None


# =============================================================================
# 主函数
# =============================================================================

def main():
    # 脚本所在目录，用于定位默认配置文件
    _script_dir = Path(__file__).resolve().parent
    _default_config = str(_script_dir / "config.yaml")

    parser = argparse.ArgumentParser(
        description="PDF -> Markdown 批量转换工具 (MinerU 精准解析 API)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python pdf_to_markdown.py
  python pdf_to_markdown.py -c config.yaml
  python pdf_to_markdown.py --pdf-root ./my_pdfs --output-root ./my_md
  MINERU_TOKEN=xxx python pdf_to_markdown.py
        """,
    )
    parser.add_argument("-c", "--config", default=_default_config,
                        help="配置文件路径 (默认: 脚本同目录下的 config.yaml)")
    parser.add_argument("--pdf-root", help="PDF 源文件夹（覆盖配置文件）")
    parser.add_argument("--output-root", help="Markdown 输出文件夹（覆盖配置文件）")
    parser.add_argument("--batch-size", type=int, help="每批处理的文件数")
    parser.add_argument("--language", default=None,
                        help="OCR 语言代码，如 ch/en（覆盖配置文件）")
    parser.add_argument("--merge", dest="auto_merge", action="store_true", default=None,
                        help="转换完成后自动合并 markdown（覆盖配置文件）")
    parser.add_argument("--no-merge", dest="auto_merge", action="store_false", default=None,
                        help="转换完成后不合并 markdown（覆盖配置文件）")
    parser.add_argument("--log-level", default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="日志级别")
    parser.add_argument("--daily-limit", type=int, default=None,
                        help="今日最大处理文件数（自动分批，第二天续跑）")
    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)

    # 命令行参数覆盖
    if args.pdf_root:
        config["paths"]["pdf_root"] = args.pdf_root
    if args.output_root:
        config["paths"]["output_root"] = args.output_root
    if args.batch_size:
        config["processing"]["batch_size"] = args.batch_size
    if args.language:
        config["mineru"]["language"] = args.language
    if args.auto_merge is not None:
        config["processing"]["auto_merge"] = args.auto_merge
    if args.log_level:
        config["logging"]["level"] = args.log_level

    # 初始化
    setup_logging(config["logging"]["level"])

    pdf_root = config["paths"]["pdf_root"]
    output_root = config["paths"]["output_root"]
    batch_delay = config["processing"]["batch_delay"]

    logging.info("=" * 54)
    logging.info("PDF -> Markdown 转换工具 (MinerU 精准解析 API)")
    logging.info("API 文档:   https://mineru.net/apiManage/docs")
    logging.info("PDF 源:     %s", pdf_root)
    logging.info("输出路径:   %s", output_root)
    logging.info("GPU 加速:   %s", "启用" if config["mineru"]["use_gpu"] else "未启用")
    logging.info("=" * 54)

    try:
        client = MinerUClient(config)
    except ValueError as e:
        logging.error(str(e))
        sys.exit(1)

    # 扫描 PDF
    pdf_info_list = scan_pdfs(pdf_root)
    if args.daily_limit and len(pdf_info_list) > args.daily_limit:
        pdf_info_list = pdf_info_list[:args.daily_limit]
        logging.info("今日限额: %d 篇，剩余 %d 篇下次处理",
                     args.daily_limit, len(pdf_info_list))
    if not pdf_info_list:
        logging.warning("未找到 PDF 文件，退出。")
        return

    total = len(pdf_info_list)
    total_batches = (total + client.batch_size - 1) // client.batch_size

    # 分批处理
    for batch_idx in range(total_batches):
        start = batch_idx * client.batch_size
        end = min(start + client.batch_size, total)
        batch = pdf_info_list[start:end]

        logging.info("")
        logging.info("批次 %d/%d (%d 个文件)", batch_idx + 1, total_batches, len(batch))

        batch_id, file_map = client.batch_upload(batch)
        extract_results = client.poll_status(batch_id)
        client.download_and_save(extract_results, file_map, output_root)

        # 每批次完成后立即归档该批次的 PDF，支持断点续传
        archive_dir = Path(pdf_root).parent / "pdf_processed"
        archive_dir.mkdir(parents=True, exist_ok=True)
        for pdf_path, _ in batch:
            src = Path(pdf_path)
            parent_dir = src.parent
            if parent_dir.exists() and parent_dir != Path(pdf_root):
                doi_name = parent_dir.name
                dst = archive_dir / doi_name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.move(str(parent_dir), str(dst))
        logging.info("已归档批次 %d 的 %d 个 PDF", batch_idx + 1, len(batch))

        if batch_idx < total_batches - 1:
            logging.info("等待 %d 秒后处理下一批...", batch_delay)
            time.sleep(batch_delay)

    logging.info("")
    logging.info("全部完成！Markdown 文件已保存至: %s", output_root)
    # --- 自动合并 ---
    auto_merge = config["processing"].get("auto_merge", False)
    if auto_merge:
        merged_root = config["paths"].get("merged_root", "")
        if not merged_root:
            merged_root = str(Path(output_root).parent / "merged")
        logging.info("")
        logging.info("=" * 54)
        logging.info("自动合并: %s -> %s", output_root, merged_root)
        try:
            from merge_markdown import run_merge
            m_count, f_count = run_merge(output_root, merged_root)
            if m_count > 0:
                logging.info("合并完成: %d 个文件夹, %d 个文件 -> %s",
                             m_count, f_count, merged_root)
            else:
                logging.info("无需合并（每个子文件夹至少需要 2 个 .md 文件）")
        except ImportError:
            logging.warning("无法导入 merge_markdown 模块，跳过合并。"
                          "请确保 merge_markdown.py 在同一目录。")
        except Exception as e:
            logging.error("合并失败: %s", e)

    logging.info("=" * 54)


if __name__ == "__main__":
    main()