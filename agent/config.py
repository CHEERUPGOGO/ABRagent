"""agent 配置"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── LLM ──
EXTRACTION_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
EXTRACTION_TEMPERATURE = 0.1
MERGE_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
MERGE_TEMPERATURE = 0.1

# ── 路径 ──
DEFAULT_INPUT_ROOT = PROJECT_ROOT / "database" / "type"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results"
META_JSON_PATH = PROJECT_ROOT / "miner" / "json" / "meta_merged.json"

# ── 并行度 ──
MAX_WORKERS = 8

# ── 段落过滤 ──
MIN_PARAGRAPH_LEN = 100

# ── 数据挖掘中文过滤（仅影响 agent 数据挖掘线，入库线不受影响）──
# 数据挖掘暂不处理中文文献：扫描时跳过中文字符占比 >= ZH_RATIO_THRESHOLD 的文档。
# 阈值 5% 依据实测：双语期刊（英文正文+中文摘要）占比 0.6~1.2% 不会被误判；
# 知网中文文献占比 >=13.2% 会被正确跳过；纯英文文献 <=0.55%。
SKIP_ZH_DOCS = True          # 想开始挖掘中文时改为 False
ZH_RATIO_THRESHOLD = 0.05    # 中文字符占比阈值


def is_zh_doc(file_path) -> bool:
    """判断文献是否为中文文档（全文读取，按中文字符占比）。"""
    import re
    try:
        text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    if not text:
        return False
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    return cn / len(text) >= ZH_RATIO_THRESHOLD

# ── 条件字段（用于 canonical hash） ──
CONDITION_HASH_FIELDS = [
    "temperature", "c_rate", "current_density",
    "voltage_range", "electrolyte", "electrode_config",
    "mass_loading", "test_method",
]
