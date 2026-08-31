"""
电池组件分类 Agent — 对电池类型分类结果进一步细分主要研究组件。

将四类电池（Li-S, Li-air, Solid-state, Li-ion/Li-metal）下的文献，
按主要研究组件分为: 正极 (cathode)、负极 (anode)、电解质 (electrolyte)。

基于 LangChain Chain 架构，使用 DeepSeek 模型。

类别:
- cathode (正极)
- anode (负极)
- electrolyte (电解质)

输出结构:
    database/type/
    ├── Lithium_Sulfur_Battery/
    │   ├── cathode/
    │   ├── anode/
    │   └── electrolyte/
    ├── ...

用法:
    # 批量分类
    python component_type_agent.py -i database/battery_type -o database/type

    # 单篇分类
    python component_type_agent.py "论文标题" -c "论文内容"

    # 编程接口
    from miner.classification.component_type_agent import ComponentTypeAgent
    tc = TokenChecker(getattr(llm,"model_name",""))
    agent = ComponentTypeAgent.from_llm(llm, token_checker=tc)
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

# 电池类型 → 文件夹名映射（与 battery_type_agent 保持一致）
BATTERY_CATEGORIES = {
    "Li-S": "Lithium_Sulfur_Battery",
    "Li-air": "Lithium_Air_Battery",
    "Solid-state": "Solid_State_Lithium_Battery",
    "Li-ion/Li-metal": "Lithium_Ion_Metal_Battery",
}
BATTERY_KEYS = list(BATTERY_CATEGORIES.keys())

# 组件分类
COMPONENTS = ["cathode", "anode", "electrolyte"]

# 关键词降级备用
KEYWORDS: Dict[str, List[str]] = {
    "cathode": [
        "cathode", "positive electrode", "cathode material",
        "nmc", "lfp", "lco", "nca", "lmo",
        "sulfur cathode", "air cathode", "oxygen cathode",
        "cathode electrolyte interphase", "cei",
        "cathode stability", "cathode coating",
        "cathode-electrolyte", "high-voltage cathode",
        "layered oxide cathode", "spinel cathode",
        "polyanionic cathode", "conversion cathode",
    ],
    "anode": [
        "anode", "negative electrode", "anode material",
        "lithium metal anode", "li metal anode", "li-metal anode",
        "graphite anode", "silicon anode", "si anode",
        "lithium plating", "lithium stripping",
        "sei", "solid electrolyte interphase",
        "dendrite", "dendrite suppression",
        "hostless anode", "anode-free", "anodeless",
        "lithium alloy", "li alloy",
        "hard carbon anode", "carbon anode",
        "anode-electrolyte", "anode stability",
    ],
    "electrolyte": [
        "electrolyte", "electrolytes",
        "solid electrolyte", "solid-state electrolyte",
        "liquid electrolyte", "polymer electrolyte",
        "ceramic electrolyte", "sulfide electrolyte",
        "oxide electrolyte", "gel electrolyte",
        "composite electrolyte", "hybrid electrolyte",
        "catholyte", "anolyte",
        "separator", "membrane",
        "ionic conductivity", "ion transport",
        "li-ion conductivity", "lithium-ion conductivity",
        "electrolyte additive", "electrolyte engineering",
        "electrolyte-electrode", "electrolyte/electrode",
        "salt", "solvent", "carbonate electrolyte",
        "ether electrolyte", "ionic liquid electrolyte",
    ],
}

# ==================== Prompt ====================

COMPONENT_PROMPT_TEMPLATE = """你是一个电池材料研究专家。请根据以下论文的**标题**和**内容（前3000字）**，判断该论文的**主要研究组件**。

组件类别:
- cathode: 正极材料/正极侧（正极材料改性、表征、界面、DFT计算等主要关注正极的研究）
- anode: 负极材料/负极侧（负极材料、锂金属负极、SEI、枝晶等主要关注负极的研究）
- electrolyte: 电解质/隔膜（固态/液态/聚合物电解质、DFT计算、离子传导、电解质界面、隔膜等主要关注电解质的研究）

判断规则:
1. 仔细阅读标题和摘要，识别核心研究目标。
2. 如果论文同时涉及多个组件，选择**最核心、占比最大**的那个。
3. 如果论文主要研究电极-电解质界面但无明显侧重，根据标题关键词判断。
4. 如果内容不足（仅标题/图表引用），仅根据标题关键词推断。

输出严格按以下 JSON 格式（不要添加任何额外文字）：
{{"component": "cathode", "confidence": "high"}}

其中 component 必须是 cathode / anode / electrolyte 之一。
confidence 为 high（明确）/ medium（较明确）/ low（推断）。

--- 标题 ---
{title}

--- 内容（前3000字）---
{content}
"""


# ==================== Agent ====================

class ComponentTypeAgent(Chain):
    """
    电池组件分类 Agent — 判断论文主要研究正极、负极还是电解质。

    属性:
        classify_chain: Any — 执行分类的链
        input_key: str — 输入键
        output_key: str — 输出键
    """

    classify_chain: Any
    input_key: str = "content"
    token_checker: Any = None
    output_key: str = "output"

    @property
    def input_keys(self) -> List[str]:
        return [self.input_key]

    @property
    def output_keys(self) -> List[str]:
        return [self.output_key]

    # ---- 工具方法 ----

    @staticmethod
    def _setup_logger() -> logging.Logger:
        logger_obj = logging.getLogger("ComponentTypeAgent")
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
        """关键词降级分类，返回 component 或 None"""
        text_lower = text.lower()
        scores: Dict[str, int] = {}
        for comp, kw_list in KEYWORDS.items():
            count = sum(1 for kw in kw_list if kw.lower() in text_lower)
            if count > 0:
                scores[comp] = count

        if not scores:
            return None

        # 返回命中关键词最多的组件
        return max(scores, key=scores.get)  # type: ignore[arg-type]

    # ---- 输出解析 ----

    def _parse_output(self, output: str, title: str = "") -> Dict[str, Any]:
        """解析 LLM 输出，提取 JSON 分类结果"""
        log = self._setup_logger()

        if not output or not isinstance(output, str):
            log.warning("Empty or invalid LLM output")
            return self._keyword_fallback(title, "")

        cleaned = output.replace("```JSON", "").replace("```json", "").replace("```", "").strip()

        json_match = re.search(r'\{[^{}]*\}', cleaned, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                component = data.get("component", "").lower()
                if component not in COMPONENTS:
                    component = (self.classify_by_keyword(title) or "electrolyte")
                return {
                    "component": component,
                    "confidence": data.get("confidence", "medium"),
                    "source": "LLM",
                }
            except json.JSONDecodeError:
                log.warning("JSON 解析失败，降级到关键词")

        return self._keyword_fallback(title, "")

    def _keyword_fallback(self, title: str, content: str) -> Dict[str, Any]:
        """关键词降级方案"""
        full_text = title + " " + content
        component = self.classify_by_keyword(full_text)
        return {
            "component": component or "electrolyte",
            "confidence": "low",
            "source": "keyword_fallback",
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
        输出: {"output": {"component": ..., "confidence": ..., "source": ...}}
        """
        _run_manager = run_manager or CallbackManagerForChainRun.get_noop_manager()
        callbacks = _run_manager.get_child()
        log = self._setup_logger()

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
                f"组件分类: {title[:60]}... → {result['component']} "
                f"(confidence={result['confidence']}, src={result['source']})"
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
    ) -> "ComponentTypeAgent":
        """从 LLM 实例创建 ComponentTypeAgent"""
        prompt = PromptTemplate(
            template=COMPONENT_PROMPT_TEMPLATE,
            input_variables=["title", "content"],
        )
        classify_chain = prompt | llm | StrOutputParser()
        inst = cls(classify_chain=classify_chain, **kwargs)
        inst.token_checker = token_checker
        return inst


# ==================== 文件复制工具 ====================

def copy_md_to_component(
    src_file: str,
    dest_root: str,
    battery_folder: str,
    component: str,
):
    """
    将 markdown 文件及其 images 文件夹复制到组件分类目录。

    Args:
        src_file: 源 .md 文件路径
        dest_root: 输出根目录（如 database/type）
        battery_folder: 电池类型文件夹名 (Lithium_Sulfur_Battery 等)
        component: 组件名 (cathode / anode / electrolyte)
    """
    dest_dir = os.path.join(dest_root, battery_folder, component)
    os.makedirs(dest_dir, exist_ok=True)

    # 复制 .md 文件
    dest_md = os.path.join(dest_dir, os.path.basename(src_file))
    shutil.copy2(src_file, dest_md)

    # 复制 images 文件夹
    base_name = os.path.splitext(os.path.basename(src_file))[0]
    src_dir = os.path.dirname(src_file)
    images_folder = base_name + "images"
    src_images = os.path.join(src_dir, images_folder)

    if os.path.isdir(src_images):
        dest_images = os.path.join(dest_dir, images_folder)
        shutil.copytree(src_images, dest_images, dirs_exist_ok=True)


# ==================== 批量处理 ====================

def process_battery_type_folders(
    input_root: str,
    output_root: str,
    agent: ComponentTypeAgent,
    min_text_len: int = 500,
) -> List[Dict[str, Any]]:
    """
    遍历 database/battery_type/ 下的四类电池文件夹，对每篇论文进行组件分类。

    Args:
        input_root: 输入根目录（如 database/battery_type）
        output_root: 输出根目录（如 database/type）
        agent: ComponentTypeAgent 实例
        min_text_len: 清洗后最小文本长度

    Returns:
        处理结果列表
    """
    from miner.cleaning.clean_text import clean_text
    from miner.meta_extraction.extract_meta import extract_meta_from_file

    if not os.path.exists(input_root):
        raise FileNotFoundError(f"输入文件夹不存在: {input_root}")

    # 收集所有待处理的 .md 文件及其电池类型
    all_tasks: List[Tuple[str, str, str]] = []  # (file_path, filename, battery_folder)
    for battery_key, battery_folder in BATTERY_CATEGORIES.items():
        batt_dir = os.path.join(input_root, battery_folder)
        if not os.path.isdir(batt_dir):
            continue
        for f in sorted(os.listdir(batt_dir)):
            if f.lower().endswith('.md'):
                all_tasks.append((
                    os.path.join(batt_dir, f),
                    f,
                    battery_folder,
                ))

    if not all_tasks:
        print("📂 未找到任何 .md 文件")
        return []

    print(f"📂 发现 {len(all_tasks)} 篇文献（分布在 {len(BATTERY_CATEGORIES)} 类电池中）")
    print(f"📁 输出目录: {output_root}")

    results = []
    for i, (file_path, filename, battery_folder) in enumerate(all_tasks, 1):
        print(f"  [{i}/{len(all_tasks)}] [{battery_folder}] {filename}...", end=" ")

        # 清洗文本
        cleaned_text = clean_text(file_path, min_text_len=min_text_len, mode="classify")
        if cleaned_text is None:
            print("⏭️  跳过（文本太短）")
            results.append({
                "file": filename, "battery_type": battery_folder,
                "component": None, "source": "skip_too_short",
            })
            continue

        meta = extract_meta_from_file(file_path)
        title = meta.get("title") or ""

        # 使用 Agent 分类
        result = agent.invoke({"title": title, "content": cleaned_text})
        out = result["output"]
        component = out["component"]

        print(f"✅ → {component}  (置信度: {out['confidence']}, 来源: {out['source']})")

        # 复制到组件分类目录
        copy_md_to_component(file_path, output_root, battery_folder, component)
        results.append({
            "file": filename, "battery_type": battery_folder,
            "component": component, "source": out["source"],
        })

    # 统计
    classified = sum(1 for r in results if r["component"])
    print(f"\n📊 完成: {classified}/{len(all_tasks)} 篇已分类")

    # 分类别统计
    comp_counts: Dict[str, int] = {}
    for r in results:
        comp = r.get("component")
        if comp:
            comp_counts[comp] = comp_counts.get(comp, 0) + 1
    for comp in COMPONENTS:
        print(f"   {comp}: {comp_counts.get(comp, 0)} 篇")

    return results
    # 保存分类结果到JSON文件
    #results_file = os.path.join(output_root, "classification_results.json")
    #with open(results_file, 'w', encoding='utf-8') as f:
        #json.dump(results, f, ensure_ascii=False, indent=2)
    #print(f"📝 分类结果已保存到: {results_file}")


# ==================== 命令行入口 ====================

if __name__ == "__main__":
    import sys
    from pathlib import Path
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(_PROJECT_ROOT))

    import argparse
    from miner.config import create_llm
    from miner.extraction_core.pricing import TokenChecker

    parser = argparse.ArgumentParser(
        description="电池组件分类 Agent — 判断主要研究正极/负极/电解质"
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
    parser.add_argument(
        "-i", "--input",
        default="database/battery_type",
        help="输入根目录，扫描其下四类电池文件夹（默认: database/battery_type）"
    )
    parser.add_argument(
        "-o", "--output",
        default="database/type",
        help="输出根目录（默认: database/type）"
    )
    parser.add_argument(
        "--model",
        default=None,
        help="DeepSeek 模型名（默认从 config.yaml 读取）"
    )
    args = parser.parse_args()

    # 创建 LLM 实例
    try:
        llm = create_llm(model_type="classification")
    except ValueError as e:
        print(f"错误: {e}")
        print("请在 miner/config.yaml 的 llm.api_key 中填写你的 DeepSeek API Key")
        exit(1)

    if args.model:
        llm.model_name = args.model

    tc = TokenChecker(getattr(llm,"model_name",""))
    agent = ComponentTypeAgent.from_llm(llm, token_checker=tc)

    # ---- 单篇模式 ----
    if args.title:
        title = args.title
        result = agent.invoke({"title": title, "content": args.content})
        out = result["output"]
        print(f"\n标题: {title[:80]}...")
        print(f"组件: {out['component']}")
        print(f"置信度: {out['confidence']}")
        print(f"判断来源: {out['source']}")

    # ---- 批量模式 ----
    else:
        process_battery_type_folders(args.input, args.output, agent)
