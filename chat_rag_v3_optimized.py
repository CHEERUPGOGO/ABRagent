#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAG 问答前端 — v3 优化版：组件过滤 + top-15 + 标签加权排序"""

import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import re
from typing import List, Optional, Dict
from langchain_core.prompts import PromptTemplate
from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma
from miner.config import create_llm

CHROMA_DIR = "/home/ls/xiaoyue/LLM2/LMLLM/miner/chroma/paragraphs_v5_test"
COLLECTION = "battery_paragraphs_v5_test"
TOP_K = 10
SEARCH_K = 15


def normalize_latex(text):
    text = re.sub(r"\$\$(.*?)\$\$", r" \1 ", text, flags=re.DOTALL)
    text = re.sub(r"\$(.*?)\$", r" \1 ", text)
    for cmd in ["mathrm", "text", "mathbf", "mathcal", "mathsf", "mathit"]:
        text = re.sub(r"\\" + cmd + r"\{([^}]+)\}", r"\1", text)
    text = re.sub(r"\\([a-zA-Z*]+)", r" ", text)
    text = re.sub(r"[_{}|]", "", text)
    text = re.sub(r" +", " ", text).strip()
    return text


ANSWER_PROMPT = PromptTemplate.from_template(
    """你是锂电池领域专家。请基于以下文献段落回答问题，每条结论标注出处。

回答规则：
- 优先使用提供的文献段落中的信息，不要编造
- 如果段落不足以回答，说"文献中未找到相关数据"
- 数值要标注单位和条件
- 不要使用 LaTeX 公式格式
- 如果问题没有指定具体的材料或组件，请先基于提供的段落内容识别最相关的材料体系，再针对性回答
- 回答时优先提炼并呈现所有可量化的具体数值（电压、容量、电流密度、循环圈数、衰减率、百分比等）
- **回答末尾必须另起一行输出 [置信度: 高/中/低] 及简短理由**

相关文献段落：
{context}

用户问题：
{question}

回答：
"""
)


LABEL_KEYWORDS = {
    "电化学性能": ["性能", "容量", "能量密度", "循环", "倍率", "库仑", "电压", "capacity", "cycle", "rate capability", "coulombic", "energy density", "polarization", "eis", "cv"],
    "理化性质": ["电导率", "扩散系数", "电化学窗口", "迁移数", "模量", "带隙", "孔隙率", "conductivity", "diffusion coefficient", "electrochemical window", "modulus", "band gap"],
    "结构表征": ["xrd", "sem", "tem", "xps", "raman", "ftir", "晶格", "形貌", "衍射", "sei", "morphology"],
    "材料制备": ["合成", "制备", "烧结", "退火", "涂覆", "掺杂", "synthesized", "prepared", "calcined", "annealed"],
    "机理/模拟": ["dft", "第一性原理", "分子动力学", "机理", "模拟", "枝晶", "成核", "mechanism", "nucleation"],
}


def _detect_labels(question):
    q = question.lower()
    scores = {}
    for label, kws in LABEL_KEYWORDS.items():
        s = sum(1 for kw in kws if kw.lower() in q)
        if s > 0:
            scores[label] = s
    return sorted(scores, key=scores.get, reverse=True) if scores else []


def _detect_component(question):
    q = question.lower()
    if any(w in q for w in ["正极", "cathode", "阴极", "ncm", "nca", "lfp", "lrmo", "富锂"]):
        return "cathode"
    if any(w in q for w in ["负极", "anode", "阳极", "锂金属", "dendrite", "枝晶", "石墨", "硅基"]):
        return "anode"
    if any(w in q for w in ["电解质", "电解液", "electrolyte", "固态", "llzo", "latp", "硫化物"]):
        return "electrolyte"
    return None


class RAG:
    def __init__(self):
        self.embeddings = OllamaEmbeddings(model="bge-m3", base_url="http://localhost:11434")
        self.store = Chroma(collection_name=COLLECTION, embedding_function=self.embeddings, persist_directory=CHROMA_DIR)
        self.llm = create_llm("classification")

    def _search(self, question, k, comp):
        filter_dict = {"component": comp} if comp else {}
        return self.store.similarity_search_with_score(question, k=k, **({"filter": filter_dict} if filter_dict else {}))

    def ask(self, question, top_k=TOP_K):
        import hashlib

        comp = _detect_component(question)
        expected_labels = _detect_labels(question)

        # 第1轮：组件过滤，搜 25 段，加权排序
        all_docs = self._search(question, 25, comp)
        seen_contents = set()

        # 递进回退：如果加权后缺少某个标签，为该标签单独搜 5 段
        scanned_labels = set()
        for item in all_docs[:10]:
            doc = item[0] if isinstance(item, tuple) else item
            scanned_labels.add(doc.metadata.get("label", ""))
        if comp:
            for el in expected_labels:
                if el not in scanned_labels:
                    extra = self._search(question, 5, comp)
                    all_docs.extend(extra)

        # 兜底搜：合并全局搜索结果，防止标签/组件过滤遗漏
        import re as _re
        extra_docs = self.store.similarity_search_with_score(question, k=10)
        # 给额外搜到的段落一个标签匹配之外的加分机会（限制在 extra 结果中）
        extra_ids = set()
        for item in extra_docs:
            doc = item[0] if isinstance(item, tuple) else item
            extra_ids.add(hashlib.md5(doc.page_content.encode()).hexdigest())
        all_docs.extend(extra_docs)

        # 去重 + 余弦置信度 + 标签加分重排
        scored = []
        for item in all_docs:
            d, cos_score = (item[0], item[1]) if isinstance(item, tuple) and len(item) == 2 else (item, 0.5)
            h = hashlib.md5(d.page_content.encode()).hexdigest()
            if h in seen_contents:
                continue
            seen_contents.add(h)
            # 检索置信度：cosine distance 归一化 (0.0~1.0, 越高越相关)
            conf_retrieval = cos_score / 2.0  # cosine 距离 [0,2], 归一化到 [0,1]
            score = conf_retrieval * 2.0
            lbl = d.metadata.get("label", "")
            for i, el in enumerate(expected_labels):
                if lbl == el:
                    score += 0.15 * (1 / (i + 1))
            if h in extra_ids:
                score += 0.15
            num_hits = len(_re.findall(r"\d+(?:\.\d+)?\s*(mAh|mA|mV|V|Wh|W|mS|S|°C|℃|%)", d.page_content))
            score += 0.04 * min(num_hits, 5)
            scored.append((score, d))

        # 同文献压制：同一篇文献超过2段，后续段落降权20%，防止扎堆
        paper_count = {}
        for i, (score, d) in enumerate(scored):
            paper = d.metadata.get("source_paper", "")
            paper_count[paper] = paper_count.get(paper, 0) + 1
            if paper_count[paper] > 2:
                scored[i] = (score * 0.8, d)
        scored.sort(key=lambda x: -x[0])
        docs = [d for _, d in scored[:top_k]]

        sources, context_parts = [], []
        for i, d in enumerate(docs, 1):
            meta = d.metadata
            doi = meta.get("source_paper", "?")
            comp_name = meta.get("component", "?")
            lbl = meta.get("label", "?")
            snippet = normalize_latex(d.page_content)
            context_parts.append(f"[{i}] (DOI: {doi}, {comp_name}/{lbl}) {snippet}")
            sources.append({"doi": doi, "component": comp_name, "label": lbl, "snippet": snippet})

        prompt = ANSWER_PROMPT.format(context="\n\n".join(context_parts), question=question)
        answer = self.llm.invoke(prompt).content.strip()

        max_conf = max(s[0] for s in scored[:top_k]) if scored[:top_k] else 0
        conf_pct = round(min(max_conf / 2.0 * 100, 100), 1)
        info = f"[优化 | 组件={comp or '无'} | 标签={'/'.join(expected_labels) if expected_labels else '无'} | 召回{len(docs)}/{len(scored)}段 | 检索置信度:{conf_pct}%]"
        return {"answer": answer, "sources": sources, "info": info}


rag = RAG()


def respond(message, history):
    result = rag.ask(message)
    answer = result["answer"] + f"\n\n**检索**: {result['info']}"
    sources = result.get("sources", [])
    if sources:
        answer += "\n\n**来源:**"
        for s in sources[:5]:
            snippet = (s.get("snippet") or "").strip()
            if snippet:
                answer += f"\n  [{s.get('label','?')}] {s.get('doi','?')}\n    {snippet}\n"
    return answer


import gradio as gr

demo = gr.ChatInterface(respond,
    title="锂电池文献 RAG (v3优化·标签加权排序)",
    description="top-15 搜索 + 标签加权排序 vs v3(硬过滤) vs v2",
    examples=["NCM811的首次放电容量", "下一代锂电池发展方向", "高电压正极材料的容量衰减", "锂金属负极的界面稳定性"])

if __name__ == "__main__":
    import os
    port = int(os.environ.get("GRADIO_SERVER_PORT", 7865))
    demo.launch(server_name="127.0.0.1", server_port=port)
