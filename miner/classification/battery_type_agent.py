"""
锂电池类型分类 Agent — 基于 LangChain Chain 的文献分类器。

参考 propertybaseqwendelsfh.py 的 Chain 架构，使用 DeepSeek 模型对
锂电池文献进行自动分类。

类别:
- Li-S (锂硫电池)
- Li-air (锂空气电池)
- Solid-state (固态锂电池)
- Li-ion/Li-metal (锂离子/锂金属电池)

同时判断: 是否为综述、是否属于锂电池相关。

用法:
    # 单篇分类
    python battery_type_agent.py "论文标题"

    # 批量分类（输出到 database/battery_type/，含 images 复制）
    python battery_type_agent.py -i papers/cleaned_markdown/242/ -o database/battery_type

    # 编程接口
    from miner.classification.battery_type_agent import BatteryTypeAgent
    tc = TokenChecker(getattr(llm,"model_name",""))
    agent = BatteryTypeAgent.from_llm(llm, token_checker=tc)
    result = agent.invoke({"title": "...", "content": "..."})
"""

import json
import os
import re
import shutil
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.language_models.base import BaseLanguageModel
from langchain_classic.chains.base import Chain
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.callbacks.manager import CallbackManagerForChainRun

logger = logging.getLogger(__name__)

# ==================== 常量 ====================

CATEGORIES = {
    "Li-S": "Lithium_Sulfur_Battery",
    "Li-air": "Lithium_Air_Battery",
    "Solid-state": "Solid_State_Lithium_Battery",
    "Li-ion/Li-metal": "Lithium_Ion_Metal_Battery",
}
ALL_CATS = list(CATEGORIES.keys())

# 关键词降级备用
KEYWORDS = {
    "Li-ion/Li-metal": [
        "lithium ion", "li-ion", "li ion", "lithium metal", "li metal",
        "li-metal", "intercalation", "graphite anode", "lithiation",
        "lib", "lithium-ion battery", "lithium-metal battery",
    ],
    "Li-S": [
        "lithium sulfur", "li-s", "li s", "lithium-sulfur",
        "sulfur cathode", "polysulfide", "li–s",
    ],
    "Li-air": [
        "lithium air", "li-air", "li air", "lithium oxygen",
        "li-o2", "li o2", "lithium–air", "li–air", "li–o2",
    ],
    "Solid-state": [
        "solid state", "solid-state", "solid electrolyte",
        "sulfide electrolyte", "oxide electrolyte", "polymer electrolyte",
        "all-solid-state", "ceramic electrolyte", "in situ polymerized",
    ],
}

REVIEW_KEYWORDS = [
    "review", "overview", "survey", "perspective", "state of the art", "reflection",
    "opinion", "comment", "viewpoint",
    "critical review", "comprehensive review", "mini review",
    "recent progress", "recent advance", "current status", "future direction",
]

RECYCLING_KEYWORDS = [
    "recycl", "recovery", "leaching", "regeneration",
    "spent battery", "spent cathode", "spent lithium",
    "circular economy", "urban mining",
    "second life", "second-life", "end-of-life",
    "battery waste", "decommission",
    "回收", "再生", "浸出", "退役",
]

FLEXIBLE_BATTERY_KEYWORDS = [
    "flexible battery", "flexible batteries", "flexible lithium",
    "flexible energy", "foldable battery", "stretchable battery",
    "wearable battery", "flexible electrode", "bendable",
    "flexible lib", "flexible li-ion", "flexible li-ion battery",
    "bidirectional deformat", "rigid-supple", "bidirectionally flexible",
]

# ==================== Prompt ====================

CLASSIFY_PROMPT_TEMPLATE = """你是一个电池文献分类专家。请根据以下论文的**标题**和**内容（前3000字）** 判断其类别。

分类规则：
1. 判断这篇论文是否属于锂电池相关研究（包括锂离子电池、锂金属电池、锂硫电池、锂空气电池、固态锂电池等）。**特别注意：铝离子电池、钠离子电池、钾离子电池、锌离子电池、镁离子电池等非锂电池体系，is_li_battery 为 false。** 如果不是，is_li_battery 为 false，is_review 为 false，subtype 可设为空字符串。
2. 判断是否为综述/观点/评论类文章（非原创研究论文）：如果标题或内容表现出综述性质（使用了 "review", "overview", "perspective", "survey", "opinion", "comment", "viewpoint" 等词），或系统性总结评述某一领域而未给出新的实验数据，或为观点性/展望性文章（perspective, outlook, opinion），则 is_review 为 true。**仅当论文有明确的新实验数据/新材料/新表征结果时，才判为 false。**
3. 确定锂电池子类（subtype），按以下规则判断，重点关注论文中实际使用的电解质类型：
   - 如果论文研究的是**全固态电解质**（无液态成分，如无机固态电解质、纯聚合物固态电解质），子类为"Solid-state"。
   - 对于**非全固态**（半固态/类固态/液态电解质，如 SLE、MOF+离子液体、凝胶、液态电解质等），子类**不是 Solid-state**，需进一步根据电池体系判断：
        若属于锂硫电池体系（Li-S，围绕硫正极、多硫化物穿梭效应等），子类为 "Li-S"。
        若属于锂空气/锂氧气电池体系（Li-air / Li-O₂），子类为 "Li-air"。
        若以上均不是，则子类为 "Li-ion/Li-metal"。

请特别注意：
- 只要论文中明确采用了固态电解质，就必须归为 Solid-state，哪怕论文同时提及锂硫或锂空气概念。
- 分类时务必依据论文的真实实验描述，而非仅凭标题中的个别词汇。

4. 判断是否为电池回收/再生论文。如果论文主要研究废旧锂电池的金属回收、材料再生、湿法冶金、放电拆除、正极材料浸出萃取等（关键词：recycling、spent battery、leaching、regeneration、second-life 等），is_recycling 为 true。否则为 false。

5. 判断是否为柔性/可穿戴电池论文。如果论文主要研究柔性电池的结构设计、可弯折电池、可拉伸电池、可穿戴电池等，关注的是电池整体的力学结构而非特定电极材料的化学改性（关键词：flexible battery、foldable battery、stretchable battery、wearable battery、bidirectional deformability、bendable battery 等），is_flexible_battery 为 true。否则为 false。

输出严格按以下 JSON 格式（不要添加任何额外文字）：
{{"is_review": false, "is_li_battery": true, "is_recycling": false, "is_flexible_battery": false, "subtype": "Li-ion/Li-metal"}}

--- 标题 ---
{title}

--- 内容（前3000字）---
{content}
"""


# ==================== Agent ====================

class BatteryTypeAgent(Chain):
    """
    锂电池类型分类 Agent。

    属性:
        classify_chain: Any — 执行分类的链
        input_key: str — 输入键
        output_key: str — 输出键
    """

    classify_chain: Any
    input_key: str = "content"
    token_checker: Any = None
    output_key: str = "output"

    # Chain 需要的输入/输出键声明
    @property
    def input_keys(self) -> List[str]:
        return [self.input_key]

    @property
    def output_keys(self) -> List[str]:
        return [self.output_key]

    # ---- 工具方法 ----

    @staticmethod
    def _setup_logger() -> logging.Logger:
        logger_obj = logging.getLogger("BatteryTypeAgent")
        if not logger_obj.handlers:
            logger_obj.setLevel(logging.INFO)
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            ch = logging.StreamHandler()
            ch.setFormatter(formatter)
            logger_obj.addHandler(ch)
        return logger_obj

    @staticmethod
    def classify_by_keyword(text: str) -> Optional[str]:
        """关键词降级分类"""
        text_lower = text.lower()
        for cat, kw_list in KEYWORDS.items():
            for kw in kw_list:
                if kw.lower() in text_lower:
                    return cat
        if re.search(r'\blithium\b|\bli\b', text_lower):
            return "Li-ion/Li-metal"
        return None

    @staticmethod
    def is_review_by_keyword(text: str) -> bool:
        """关键词判断是否为综述"""
        return any(kw in text.lower() for kw in REVIEW_KEYWORDS)

    @staticmethod
    def is_recycling_by_keyword(text: str) -> bool:
        """关键词判断是否为回收论文"""
        return any(kw in text.lower() for kw in RECYCLING_KEYWORDS)

    @staticmethod
    def is_flexible_battery_by_keyword(text: str) -> bool:
        """关键词判断是否为柔性电池论文"""
        return any(kw in text.lower() for kw in FLEXIBLE_BATTERY_KEYWORDS)

    @staticmethod
    def fallback_subtype_by_title(title: str) -> str:
        """根据标题关键词推断子类"""
        t = title.lower()
        if any(k in t for k in ["solid-state", "solid state", "in situ polymerized"]):
            return "Solid-state"
        if any(k in t for k in ["li-s", "lithium sulfur", "li–s"]):
            return "Li-S"
        if any(k in t for k in ["li-air", "lithium air", "li–air", "li-o2"]):
            return "Li-air"
        return "Li-ion/Li-metal"

    # ---- 输出解析 ----

    def _parse_output(self, output: str, title: str = "") -> Dict[str, Any]:
        """
        解析 LLM 输出，提取 JSON 分类结果。
        """
        log = self._setup_logger()

        if not output or not isinstance(output, str):
            log.warning("Empty or invalid LLM output")
            return self._keyword_fallback(title, "")

        cleaned = output.replace("```JSON", "").replace("```json", "").replace("```", "").strip()

        json_match = re.search(r'\{[^{}]*\}', cleaned, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                subtype = data.get("subtype", "Li-ion/Li-metal")
                if subtype not in ALL_CATS:
                    subtype = self.fallback_subtype_by_title(title)
                return {
                    "is_review": data.get("is_review", False),
                    "is_li_battery": data.get("is_li_battery", True),
                    "is_recycling": data.get("is_recycling", False),
                    "is_flexible_battery": data.get("is_flexible_battery", False),
                    "subtype": subtype,
                    "source": "LLM",
                }
            except json.JSONDecodeError:
                log.warning("JSON 解析失败，降级到关键词")

        return self._keyword_fallback(title, "")

    def _keyword_fallback(self, title: str, content: str) -> Dict[str, Any]:
        """关键词降级方案"""
        full_text = title + " " + content
        is_review = self.is_review_by_keyword(full_text)
        is_recycling = self.is_recycling_by_keyword(full_text)
        is_flexible_battery = self.is_flexible_battery_by_keyword(full_text)
        subtype = self.classify_by_keyword(full_text)
        return {
            "is_review": is_review,
            "is_li_battery": subtype is not None,
            "is_recycling": is_recycling,
            "subtype": subtype or "Li-ion/Li-metal",
            "source": "keyword_fallback",
            "is_flexible_battery": is_flexible_battery,
        }

    # ---- 核心执行 ----

    def _call(
        self,
        inputs: Dict[str, Any],
        run_manager: Optional[CallbackManagerForChainRun] = None,
    ) -> Dict[str, Any]:
        """
        执行分类。

        输入: {"content": {"title": "...", "content": "..."}} 或 {"title": "...", "content": "..."}
        输出: {"output": {"is_review": ..., "is_li_battery": ..., "subtype": ..., "source": ...}}
        """
        _run_manager = run_manager or CallbackManagerForChainRun.get_noop_manager()
        callbacks = _run_manager.get_child()
        log = self._setup_logger()

        # 提取输入
        raw = inputs.get(self.input_key, inputs)
        if isinstance(raw, dict):
            title = raw.get("title", "")
            content = raw.get("content", "")
        else:
            title = inputs.get("title", "")
            content = str(raw)

        truncated = content[:3000]

        try:
            if self.token_checker: self.token_checker.check_include(json.dumps({"title":title,"content":truncated}))
            llm_output = self.classify_chain.invoke(
                {"title": title, "content": truncated},
                {"callbacks": callbacks},
            )
            result = self._parse_output(llm_output, title)
            log.info(
                f"分类: {title[:60]}... → {result['subtype']} "
                f"(review={result['is_review']}, recycling={result.get('is_recycling', False)}, flexible={result.get('is_flexible_battery', False)}, src={result['source']})"
            )
        except Exception as e:
            log.warning(f"LLM 调用失败: {e}，使用关键词降级")
            result = self._keyword_fallback(title, content)

        return {self.output_key: result}

    # ---- 工厂方法 ----

    @classmethod
    def from_llm(
        cls,
        llm: BaseLanguageModel,
        token_checker: Any = None,
        **kwargs,
    ) -> "BatteryTypeAgent":
        """
        从 LLM 实例创建 BatteryTypeAgent。

        Args:
            llm: LangChain 兼容的语言模型
            **kwargs: 传递给 Chain 的额外参数

        Returns:
            BatteryTypeAgent 实例
        """
        prompt = PromptTemplate(
            template=CLASSIFY_PROMPT_TEMPLATE,
            input_variables=["title", "content"],
        )
        classify_chain = prompt | llm | StrOutputParser()
        inst = cls(classify_chain=classify_chain, **kwargs)
        inst.token_checker = token_checker
        return inst


# ==================== 文件复制工具 ====================

def copy_md_to_category(
    src_file: str,
    dest_root: str,
    category_key: str,
):
    """
    将 markdown 文件及其 images 文件夹复制到分类目录。

    仿照 classify_papers.py 的输出方式。

    Args:
        src_file: 源 .md 文件路径
        dest_root: 输出根目录（如 database/battery_type）
        category_key: 分类键（Li-S / Li-air / Solid-state / Li-ion/Li-metal）
    """
    dest_dir = os.path.join(dest_root, CATEGORIES[category_key])
    os.makedirs(dest_dir, exist_ok=True)

    # 复制 .md 文件
    dest_md = os.path.join(dest_dir, os.path.basename(src_file))
    shutil.copy2(src_file, dest_md)

    # 复制 images 文件夹（与 .md 同名的 images 目录）
    base_name = os.path.splitext(os.path.basename(src_file))[0]
    src_dir = os.path.dirname(src_file)
    images_folder = base_name + "images"
    src_images = os.path.join(src_dir, images_folder)

    if os.path.isdir(src_images):
        dest_images = os.path.join(dest_dir, images_folder)
        shutil.copytree(src_images, dest_images, dirs_exist_ok=True)


# ==================== 批量处理 ====================

def process_markdown_files(
    input_folder: str,
    output_root: str,
    agent: BatteryTypeAgent,
    min_text_len: int = 500,
) -> List[Dict[str, Any]]:
    """
    批量处理文件夹中的 markdown 文件：清洗 → 分类 → 复制。

    Args:
        input_folder: 包含 .md 文件的输入文件夹
        output_root: 输出根目录（如 database/battery_type）
        agent: BatteryTypeAgent 实例
        min_text_len: 清洗后最小文本长度

    Returns:
        处理结果列表
    """
    from miner.cleaning.clean_text import clean_text
    from miner.meta_extraction.extract_meta import extract_meta_from_file

    if not os.path.exists(input_folder):
        raise FileNotFoundError(f"输入文件夹不存在: {input_folder}")

    files = sorted([
        f for f in os.listdir(input_folder)
        if f.lower().endswith('.md')
    ])

    print(f"📂 发现 {len(files)} 篇文献")
    print(f"📁 输出目录: {output_root}")

    results = []
    for i, filename in enumerate(files, 1):
        file_path = os.path.join(input_folder, filename)
        print(f"  [{i}/{len(files)}] 处理 {filename}...", end=" ")

        # 清洗文本
        cleaned_text = clean_text(file_path, min_text_len=min_text_len, mode="classify")
        if cleaned_text is None:
            print("⏭️  跳过（文本太短）")
            results.append({"file": filename, "category": None, "source": "skip_too_short"})
            continue

        meta = extract_meta_from_file(file_path)
        title = meta.get("title") or ""

        # 使用 Agent 分类
        result = agent.invoke({"title": title, "content": cleaned_text})
        out = result["output"]

        # 跳过综述文章（只研究研究型论文）
        if out.get("is_review", False):
            print(f"⏭️  跳过（综述文章，来源: {out['source']}）")
            results.append({"file": filename, "category": None, "source": "is_review"})
            continue

        if not out.get("is_li_battery", True):
            print(f"⏭️  跳过（非锂电池，来源: {out['source']}）")
            results.append({"file": filename, "category": None, "source": "not_li_battery"})
            continue

        subtype = out["subtype"]
        print(f"✅ → {subtype}  (来源: {out['source']})")

        # 复制到分类目录
        copy_md_to_category(file_path, output_root, subtype)
        results.append({"file": filename, "category": subtype, "source": out["source"]})

    # 统计
    classified = sum(1 for r in results if r["category"])
    print(f"\n📊 完成: {classified}/{len(files)} 篇已分类")

    return results


# ==================== 命令行入口 ====================

if __name__ == "__main__":
    import sys
    from pathlib import Path
    # 将项目根目录加入 sys.path，确保 miner 包可导入
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(_PROJECT_ROOT))

    import argparse
    from miner.config import create_llm, load_config
    from miner.extraction_core.pricing import TokenChecker

    parser = argparse.ArgumentParser(
        description="锂电池类型分类 Agent — 使用 DeepSeek 模型"
    )
    parser.add_argument(
        "title",
        nargs="?",
        default=None,
        help="文献标题（单篇模式）"
    )
    parser.add_argument(
        "-c", "--content",
        default="",
        help="文献内容，配合 title 使用（可选）"
    )
    # 从配置文件读取默认路径，并转为相对于项目根的绝对路径
    _cfg = load_config()
    _bt_cfg = _cfg.get("battery_type", {})
    _default_input = _bt_cfg.get("input_dir", None)
    _default_output = _bt_cfg.get("output_dir", "database/battery_type")

    # 相对路径 → 绝对路径（以项目根为基准），避免依赖当前工作目录
    if _default_input and not os.path.isabs(_default_input):
        _default_input = str(_PROJECT_ROOT / _default_input)
    if _default_output and not os.path.isabs(_default_output):
        _default_output = str(_PROJECT_ROOT / _default_output)

    parser.add_argument(
        "-i", "--input",
        default=_default_input,
        help="输入文件夹路径（批量模式，默认从 config.yaml 读取）"
    )
    parser.add_argument(
        "-o", "--output",
        default=_default_output,
        help="输出根目录（默认从 config.yaml 读取）"
    )
    parser.add_argument(
        "--model",
        default=None,
        help="DeepSeek 模型名（默认从 config.yaml 读取）"
    )
    args = parser.parse_args()

    # 从 miner/config.yaml 创建 LLM 实例
    try:
        llm = create_llm(model_type="classification")
    except ValueError as e:
        print(f"错误: {e}")
        print("请在 miner/config.yaml 的 llm.api_key 中填写你的 DeepSeek API Key")
        exit(1)

    if args.model:
        llm.model_name = args.model

    tc = TokenChecker(getattr(llm,"model_name",""))
    agent = BatteryTypeAgent.from_llm(llm, token_checker=tc)

    # ---- 批量模式 ----
    if args.input:
        process_markdown_files(args.input, args.output, agent)

    # ---- 单篇模式 ----
    else:
        title = args.title or "Solid-state electrolyte for lithium metal batteries"
        result = agent.invoke({"title": title, "content": args.content})
        out = result["output"]
        print(f"\n标题: {title[:80]}...")
        print(f"分类: {out['subtype']}")
        print(f"是否综述: {out['is_review']}")
        print(f"是否锂电池: {out['is_li_battery']}")
        print(f"是否回收: {out.get('is_recycling', False)}")
        print(f"是否柔性电池: {out.get('is_flexible_battery', False)}")
        print(f"判断来源: {out['source']}")