#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gradio 三栏问答对比前端 — 三粒度 Chroma 库多轮问答对比

每个 DB 独立对话，可切换对比三种 chunk 策略的召回回答差异。

用法:
  python compare_qa_frontend.py            # 默认端口 7872
  GRADIO_SERVER_PORT=7890 python compare_qa_frontend.py
"""

import os, sys, re, hashlib
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from typing import Optional
from collections import Counter

from langchain_core.prompts import PromptTemplate
from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma
from miner.config import create_llm

# ── 三 Chroma 配置 ──────────────────────────────────────────────

BASE = _PROJECT_ROOT / "miner" / "chroma"
COLLECTION = "battery_paragraphs_q"
MODEL = "qwen3-embedding:8b"
OLLAMA_URL = "http://localhost:11434"
TOP_K = 10
SEARCH_K = 15

CHROMA_CFG = [
    {"label": "7500/750 (粗粒度)",  "dir": str(BASE / "paragraphs_q"),   "color": "#4A90D9"},
    {"label": "2000/200 (细粒度)",  "dir": str(BASE / "paragraphs_q_1"), "color": "#7EC850"},
    {"label": "3000/300 (中粒度)",  "dir": str(BASE / "paragraphs_q_2"), "color": "#EAA935"},
]

# ── 标签关键词（用于标签感知加权排序） ─────────────────────────

LABEL_KEYWORDS = {
    "电化学性能": ["性能", "容量", "能量密度", "循环", "倍率", "库仑", "电压",
                   "capacity", "cycle", "rate capability", "coulombic", "energy density",
                   "polarization", "eis", "cv"],
    "材料属性与表征": ["xrd", "sem", "tem", "xps", "raman", "ftir", "晶格", "形貌",
                       "衍射", "sei", "morphology", "conductivity", "diffusion"],
    "材料制备": ["合成", "制备", "烧结", "退火", "涂覆", "掺杂",
                 "synthesized", "prepared", "calcined", "annealed"],
    "机理/模拟": ["dft", "第一性原理", "分子动力学", "机理", "模拟", "枝晶",
                   "成核", "mechanism", "nucleation"],
    "概述": ["综述", "应用", "前景", "挑战", "overview", "review", "challenge"],
}

ANSWER_TEMPLATE = PromptTemplate.from_template(
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


# ── 工具函数 ────────────────────────────────────────────────────

def normalize_latex(text):
    text = re.sub(r"\$\$(.*?)\$\$", r" \1 ", text, flags=re.DOTALL)
    text = re.sub(r"\$(.*?)\$", r" \1 ", text)
    for cmd in ["mathrm", "text", "mathbf", "mathcal", "mathsf", "mathit"]:
        text = re.sub(r"\\" + cmd + r"\{([^}]+)\}", r"\1", text)
    text = re.sub(r"\\([a-zA-Z*]+)", r" ", text)
    text = re.sub(r"[_{}|]", "", text)
    text = re.sub(r" +", " ", text).strip()
    return text


def detect_component(question):
    q = question.lower()
    if any(w in q for w in ["正极", "cathode", "阴极", "ncm", "nca", "lfp", "lrmo", "富锂"]):
        return "cathode"
    if any(w in q for w in ["负极", "anode", "阳极", "锂金属", "dendrite", "枝晶", "石墨", "硅基"]):
        return "anode"
    if any(w in q for w in ["电解质", "电解液", "electrolyte", "固态", "llzo", "latp", "硫化物"]):
        return "electrolyte"
    return None


def detect_labels(question):
    q = question.lower()
    scores = {}
    for label, kws in LABEL_KEYWORDS.items():
        s = sum(1 for kw in kws if kw.lower() in q)
        if s > 0:
            scores[label] = s
    return sorted(scores, key=scores.get, reverse=True) if scores else []


# ── RAG 实例（每个 Chroma 一个） ────────────────────────────────

class RAGInstance:
    def __init__(self, chroma_dir: str, label: str, color: str):
        self.label = label
        self.color = color
        self.embeddings = OllamaEmbeddings(model=MODEL, base_url=OLLAMA_URL)
        self.store = Chroma(
            collection_name=COLLECTION,
            embedding_function=self.embeddings,
            persist_directory=chroma_dir,
        )
        self.llm = create_llm("classification")

    def _search(self, question, k, comp):
        filter_dict = {"component": comp} if comp else {}
        return self.store.similarity_search_with_score(
            question, k=k,
            **({"filter": filter_dict} if filter_dict else {})
        )

    def ask(self, question, top_k=TOP_K):
        comp = detect_component(question)
        expected_labels = detect_labels(question)

        all_docs = self._search(question, SEARCH_K, comp)
        seen_contents = set()

        # 递进回退：缺少某标签时补搜
        scanned_labels = set()
        for item in all_docs[:10]:
            d = item[0] if isinstance(item, tuple) else item
            scanned_labels.add(d.metadata.get("label", ""))
        if comp:
            for el in expected_labels:
                if el not in scanned_labels:
                    extra = self._search(question, 5, comp)
                    all_docs.extend(extra)

        # 兜底搜
        extra_docs = self.store.similarity_search_with_score(question, k=10)
        extra_ids = set()
        for item in extra_docs:
            d = item[0] if isinstance(item, tuple) else item
            extra_ids.add(hashlib.md5(d.page_content.encode()).hexdigest())
        all_docs.extend(extra_docs)

        # 去重 + 加权排序
        scored = []
        for item in all_docs:
            d, cos_score = (
                (item[0], item[1]) if isinstance(item, tuple) and len(item) == 2
                else (item, 0.5)
            )
            h = hashlib.md5(d.page_content.encode()).hexdigest()
            if h in seen_contents:
                continue
            seen_contents.add(h)
            conf_retrieval = cos_score / 2.0
            score = conf_retrieval * 2.0
            lbl = d.metadata.get("label", "")
            for i, el in enumerate(expected_labels):
                if lbl == el:
                    score += 0.15 * (1 / (i + 1))
            if h in extra_ids:
                score += 0.15
            num_hits = len(re.findall(
                r"\d+(?:\.\d+)?\s*(mAh|mA|mV|V|Wh|W|mS|S|°C|℃|%)",
                d.page_content
            ))
            score += 0.04 * min(num_hits, 5)
            scored.append((score, d))

        # 同文献压制
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

        prompt = ANSWER_TEMPLATE.format(
            context="\n\n".join(context_parts), question=question
        )
        answer = self.llm.invoke(prompt).content.strip()

        max_conf = max(s[0] for s in scored[:top_k]) if scored[:top_k] else 0
        conf_pct = round(min(max_conf / 2.0 * 100, 100), 1)

        info = (f"[组件={comp or '无'} | 标签={'/'.join(expected_labels) if expected_labels else '无'}"
                f" | 召回{len(docs)}/{len(scored)}段 | 置信度:{conf_pct}%]")
        return {
            "answer": answer,
            "sources": sources,
            "info": info,
            "n_chunks": len(docs),
            "n_papers": len(set(s["doi"] for s in sources)),
        }


# 初始化三个实例
RAGS = [RAGInstance(**cfg) for cfg in CHROMA_CFG]


# ── Gradio 前端 ─────────────────────────────────────────────────

def respond(message, history):
    """三栏并行问答，返回三条对话回复"""
    results = [rag.ask(message) for rag in RAGS]
    replies = []
    for result, rag in zip(results, RAGS):
        answer = result["answer"]
        info = result["info"]
        sources = result.get("sources", [])

        text = answer + f"\n\n**检索信息**\n{info}"

        if sources:
            text += "\n\n**来源段落 (Top-5):**"
            for s in sources[:5]:
                snippet = (s.get("snippet") or "").strip()[:200]
                if snippet:
                    text += f"\n- [{s['label']}] {s['doi']}\n  _{snippet}_\n"

        replies.append(text)

    return tuple(replies)


import gradio as gr

# 每个栏对应的实例
with gr.Blocks(title="三粒度 RAG 问答对比", css="""
    .rag-block { border-radius: 8px; padding: 4px 10px; margin-bottom: 4px; }
    .rag-label { font-weight: bold; font-size: 1.1em; margin-bottom: 6px; }
    footer { visibility: hidden; }
""") as demo:

    gr.Markdown(
        "# 🔬 三粒度 Chroma RAG 问答对比\n"
        "同一问题对比 **7500/750** (粗)、**2000/200** (细)、**3000/300** (中) 三种 chunk 策略的召回与回答效果。"
    )

    with gr.Row():
        chatboxes = []
        for rag in RAGS:
            color = rag.color
            with gr.Column(scale=1, min_width=320):
                gr.HTML(
                    f'<div class="rag-block" style="border-left: 4px solid {color}; background: {color}10;">'
                    f'<span class="rag-label" style="color: {color};">{rag.label}</span>'
                    f'<span style="font-size:0.8em;color:#888;"> ({rag.store._client.list_collections()})</span>'
                    f'</div>'
                )
                cb = gr.Chatbot(label=rag.label, height=500)
                chatboxes.append(cb)

    with gr.Row():
        msg = gr.Textbox(
            label="输入问题",
            placeholder="例如：NCM811 的首次放电容量",
            scale=4,
        )
        btn = gr.Button("发送", scale=1, variant="primary")

    examples = gr.Examples(
        examples=[
            ["NCM811 的首次放电容量和倍率性能"],
            ["锂金属负极的枝晶抑制策略"],
            ["高电压正极材料的容量衰减机理"],
            ["固态电解质的离子电导率对比"],
            ["Li-S 电池中多硫化物穿梭效应的解决途径"],
            ["硅基负极的体积膨胀问题"],
        ],
        inputs=msg,
    )

    def respond_wrapper(msg, *history_list):
        results = [rag.ask(msg) for rag in RAGS]
        replies = []
        for result, rag, hist in zip(results, RAGS, history_list):
            answer = result["answer"]
            info = result["info"]
            sources = result.get("sources", [])

            text = answer + f"\n\n**检索信息**\n{info}"
            if sources:
                text += "\n\n**来源段落 (Top-5):**"
                for s in sources[:5]:
                    snippet = (s.get("snippet") or "").strip()[:200]
                    if snippet:
                        text += f"\n- [{s['label']}] {s['doi']}\n  _{snippet}_\n"

            if hist is None:
                hist = []
            hist.append((msg, text))
            replies.append(hist)
        return [msg] + replies

    btn.click(
        respond_wrapper,
        inputs=[msg] + chatboxes,
        outputs=[msg] + chatboxes,
    )
    msg.submit(
        respond_wrapper,
        inputs=[msg] + chatboxes,
        outputs=[msg] + chatboxes,
    )

if __name__ == "__main__":
    port = int(os.environ.get("GRADIO_SERVER_PORT", 7872))
    demo.launch(server_name="127.0.0.1", server_port=port, share=False)
