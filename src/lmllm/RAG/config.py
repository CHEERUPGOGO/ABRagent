"""RAG 模块统一配置 — 高比能锂电池材料筛选场景

为"高比能锂电池材料筛选"提供:
- 多 LLM 后端(OpenAI/MiniMax API + DeepSeek API + Ollama 本地),支持为不同 Agent 绑定不同模型
- 标签体系与关键词映射(聚焦高比能材料筛选)
- 路径与模型配置
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Any

# ── 项目根 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# ════════════════════════════════════════════════════════════
# LLM 配置 — 每个 Agent 可独立指定模型 (优先读取 setting.yaml 与环境变量)
# ════════════════════════════════════════════════════════════

def _expand_placeholders(val: Any) -> Any:
    """递归展开配置中的环境变量占位符 $(VAR:default) 或 ${VAR:-default}."""
    import re
    if isinstance(val, str):
        s = val.strip()
        m = re.match(r"^\$[\({]([A-Za-z0-9_]+)(?::|-)?(.*)[\)}]$", s)
        if m:
            var_name, default_val = m.group(1), m.group(2)
            env_val = os.getenv(var_name)
            return env_val if (env_val is not None and env_val != "") else default_val.strip()
        return val
    elif isinstance(val, dict):
        return {k: _expand_placeholders(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_expand_placeholders(v) for v in val]
    return val

def _load_yaml_configs() -> dict:
    import yaml
    configs = {}
    candidates = [
        PROJECT_ROOT / "auto_battery_research" / "setting.yaml",
        PROJECT_ROOT / "miner" / "config.yaml",
    ]
    for cp in candidates:
        if cp.exists():
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        configs.update(data)
            except Exception:
                pass
    return _expand_placeholders(configs)

_local_configs = _load_yaml_configs()
_openai_cfg = _local_configs.get("openai", {})
_llm_cfg = _local_configs.get("llm", {})

# ── OpenAI / MiniMax API ──
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "") or _openai_cfg.get("openai_api_key", "") or _llm_cfg.get("api_key", "")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "") or _openai_cfg.get("openai_api_base", "") or _llm_cfg.get("base_url", "https://api.minimaxi.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "") or _openai_cfg.get("model_name", "") or _llm_cfg.get("model", "MiniMax-M2.7-highspeed")

# ── DeepSeek API ──
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "") or _local_configs.get("deepseek", {}).get("api_key", "")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
DEEPSEEK_CLASSIFICATION_MODEL = os.getenv("DEEPSEEK_CLASSIFICATION_MODEL", "deepseek-v4-flash")
DEEPSEEK_EXTRACTION_MODEL = os.getenv("DEEPSEEK_EXTRACTION_MODEL", "deepseek-v4-pro")

# ── Ollama 本地 ──
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "240"))

LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))

LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "8192"))
LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "180"))

# ── Agent 绑定模型默认值 ──
PLANNER_MODEL = os.getenv("PLANNER_MODEL", OPENAI_MODEL or "MiniMax-M2.7-highspeed")
WRITER_MODEL = os.getenv("WRITER_MODEL", OPENAI_MODEL or "MiniMax-M2.7-highspeed")
REVIEWER_MODEL = os.getenv("REVIEWER_MODEL", OPENAI_MODEL or "MiniMax-M2.7-highspeed")

# ════════════════════════════════════════════════════════════
# 检索配置
# ════════════════════════════════════════════════════════════
DEFAULT_TOP_K = 50
SEARCH_K = 20
RETRIEVAL_TOP_K_PER_QUERY = 40

# ── 向量检索 (Chroma) ──
CHROMA_DIR = os.getenv("CHROMA_DIR", str(PROJECT_ROOT / "miner" / "chroma" / "paragraphs_q"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "battery_paragraphs_q")

# ── 电子书 Chroma 配置 ──
EBOOK_CHROMA_DIR = os.getenv("EBOOK_CHROMA_DIR", str(PROJECT_ROOT / "miner" / "chroma" / "ebooks"))
EBOOK_COLLECTION_NAME = os.getenv("EBOOK_COLLECTION_NAME", "ebook_chunks")

# 嵌入模型
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:8b")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "http://localhost:11434")

# 检索模式: "chroma" | "hybrid" | "tfidf"
RETRIEVAL_MODE = os.getenv("RETRIEVAL_MODE", "hybrid")

CHROMA_TOP_K = 15
CHROMA_SEARCH_K = 50

# ── 元数据 ──
META_JSON_PATH = PROJECT_ROOT / "miner" / "json" / "metadata" / "meta_merged.json"

# ── 输出 ──
RAG_OUTPUT_DIR = PROJECT_ROOT / "src" / "lmllm" / "RAG" / "output"

# ── Reranker 配置 ──
RERANKER_MODEL_PATH = os.getenv("RERANKER_MODEL_PATH", "/home/ls/xiaoyue/models/Qwen3-Reranker-4B")
RERANKER_ENABLED = os.getenv("RERANKER_ENABLED", "false").lower() == "true"
RERANKER_TOP_K = int(os.getenv("RERANKER_TOP_K", "25"))
RERANKER_BATCH_SIZE = int(os.getenv("RERANKER_BATCH_SIZE", "2"))
RERANKER_ALPHA = float(os.getenv("RERANKER_ALPHA", "0.7"))

# ════════════════════════════════════════════════════════════
# 标签体系 — 高比能锂电池材料筛选场景
# ════════════════════════════════════════════════════════════

PRIMARY_LABELS = {"电化学性能", "材料属性与表征", "材料制备", "机理/模拟", "概述", "非正文"}

# 组件 → 关键词映射
COMPONENT_KEYWORDS: dict[str, list[str]] = {
    "cathode": [
        "cathode", "positive electrode", "ncm", "nca", "lfp", "lco", "lmo",
        "lrmo", "lnmo", "lifepo4", "licoo2", "limn2o4", "high nickel",
        "正极", "正极材料", "高镍", "三元", "磷酸铁锂", "钴酸锂", "锰酸锂",
        "富锂锰基", "富锂", "单晶", "单晶正极",
    ],
    "anode": [
        "anode", "negative electrode", "graphite", "silicon", "si/c", "sio",
        "lithium metal", "li metal", "hard carbon", "soft carbon", "lto",
        "负极", "负极材料", "石墨", "金属锂", "锂金属", "硅负极", "硅碳",
        "硬碳", "软碳", "钛酸锂",
    ],
    "electrolyte": [
        "electrolyte", "solvent", "salt", "additive", "lipf6", "lifsi", "litfsi",
        "ec", "dec", "dmc", "emc", "fec", "vc", "lhce", "hce", "ionic liquid",
        "solid state electrolyte", "sse", "garnet", "sulfide", "polymer",
        "电解液", "溶剂", "溶质", "添加剂", "高浓度电解液", "局部高浓度",
        "固态电解质", "硫化物", "氧化物", "聚合物", "成膜添加剂",
    ],
}

LABEL_KEYWORDS: dict[str, list[str]] = {

    "电化学性能": [
        "capacity", "voltage", "current", "cycle", "coulombic efficiency",
        "retention", "energy density", "power density", "discharge",
        "charge", "rate capability", "overpotential", "impedance", "eis",
        "fade", "decay", "initial coulombic efficiency", "ice",
        "容量", "电压", "电流", "循环", "库仑效率", "保持率",
        "能量密度", "功率密度", "放电", "充电", "倍率", "过电位",
        "阻抗", "衰减", "首次库仑效率", "首效", "循环寿命",
    ],
    "材料属性与表征": [
        "sem", "tem", "xrd", "xps", "afm", "bet", "raman",
        "structure", "morphology", "crystal", "phase", "lattice",
        "conductivity", "ionic conductivity", "electronic conductivity",
        "thickness", "porosity", "viscosity", "density",
        "形貌", "结构", "晶体", "相变", "晶格", "电导率",
        "离子电导率", "电子电导率", "厚度", "孔隙率", "粘度",
        "密度", "表征", "微观结构", "颗粒尺寸", "比表面积",
    ],
    "材料制备": [
        "synthesis", "synthesize", "prepare", "preparation", "fabricate",
        "fabrication", "coating", "coat", "doping", "dope",
        "calcination", "calcinate", "sintering", "sinter", "anneal",
        "hydrothermal", "sol-gel", "ball milling", "mixing",
        "slurry", "electrode preparation", "cell assembly",
        "制备", "合成", "包覆", "掺杂", "煅烧", "烧结",
        "退火", "水热", "溶胶凝胶", "球磨", "混合", "涂布",
        "极片制备", "电芯组装", "配方", "工艺",
    ],
    "机理/模拟": [
        "mechanism", "dft", "md", "molecular dynamics", "simulation",
        "density functional theory", "reaction pathway", "interphase",
        "sei", "cei", "interface", "adsorption", "diffusion barrier",
        "activation energy", "electron transfer", "degradation mechanism",
        "机理", "模拟", "分子动力学", "反应路径", "界面",
        "固体电解质界面", "吸附", "扩散势垒", "活化能",
        "电子转移", "衰减机理", "枝晶生长", "相变机理",
    ],
    "概述": [
        "review", "introduction", "overview", "progress", "challenge",
        "perspective", "future", "summary", "background",
        "综述", "引言", "概述", "进展", "挑战", "展望", "总结", "背景",
    ],
    "非正文": [
        "acknowledgment", "reference", "references", "author", "conflict",
        "supporting information", "supplementary",
        "致谢", "参考文献", "作者", "利益冲突", "补充材料",
    ],
}

QTYPE_KEYWORDS: dict[str, list[str]] = {
    "screening": ["筛选", "候选", "哪种", "对比", "比较", "vs", "优缺点", "区别", "优劣"],
    "numeric": ["多少", "容量", "电压", "能量密度", "电导率", "电流", "效率", "温度", "循环"],
    "experiment": ["怎么做", "如何做", "制备", "合成", "测试", "表征"],
    "trend": ["发展方向", "趋势", "前景", "下一代", "前沿", "最新"],
    "definition": ["什么是", "定义", "概念", "是什么意思"],
}

PLANNER_SYSTEM_PROMPT = None
WRITER_SYSTEM_PROMPT = None
REVIEWER_SYSTEM_PROMPT = None

# ════════════════════════════════════════════════════════════
# LLM 工厂函数
# ════════════════════════════════════════════════════════════

def create_openai_llm(
    model_type: str = "classification",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    model_name: Optional[str] = None,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
) -> Any:
    """创建 OpenAI / MiniMax API (langchain_openai) 实例."""
    key = api_key or OPENAI_API_KEY
    if not key:
        raise ValueError("未设置 OpenAI/MiniMax API Key. 请设置环境变量 OPENAI_API_KEY 或配置 setting.yaml")

    base = api_base or OPENAI_API_BASE
    model = model_name or OPENAI_MODEL
    temp = temperature if temperature is not None else LLM_TEMPERATURE
    mt = max_tokens if max_tokens is not None else LLM_MAX_TOKENS

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        api_key=key,
        base_url=base,
        model=model,
        temperature=temp,
        max_tokens=mt,
        request_timeout=LLM_REQUEST_TIMEOUT,
    )


def create_deepseek_llm(
    model_type: str = "classification",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    model_name: Optional[str] = None,
) -> Any:
    """创建 DeepSeek / OpenAI-compatible API 实例."""
    if DEEPSEEK_API_KEY:
        api_key = DEEPSEEK_API_KEY
        api_base = DEEPSEEK_API_BASE
        model = model_name or (
            DEEPSEEK_CLASSIFICATION_MODEL if model_type == "classification" else DEEPSEEK_EXTRACTION_MODEL
        )
    elif OPENAI_API_KEY:
        return create_openai_llm(
            model_type=model_type,
            temperature=temperature,
            max_tokens=max_tokens,
            model_name=model_name,
        )
    else:
        raise ValueError("未设置 API Key. 请设置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY")

    temp = temperature if temperature is not None else LLM_TEMPERATURE
    mt = max_tokens if max_tokens is not None else LLM_MAX_TOKENS

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        api_key=api_key,
        base_url=api_base,
        model=model,
        temperature=temp,
        max_tokens=mt,
        request_timeout=LLM_REQUEST_TIMEOUT,
    )


def ensure_output_dir() -> Path:
    """确保输出目录存在"""
    RAG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return RAG_OUTPUT_DIR
