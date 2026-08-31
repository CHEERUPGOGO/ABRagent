#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gradio 双栏融合模式对比 — weighted vs RRF

启动:
    python compare_fusion_frontend.py          # 默认 7873 端口
    GRADIO_SERVER_PORT=7890 python compare_fusion_frontend.py
"""

import os, sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import gradio as gr
from src.lmllm.RAG.rag_pipeline import RAGPipeline

PORT = int(os.environ.get("GRADIO_SERVER_PORT", 7873))

# 预热两个 pipeline
print("Initializing weighted pipeline...")
pipeline_w = RAGPipeline(fusion_mode="weighted")
print("Initializing RRF pipeline...")
pipeline_r = RAGPipeline(fusion_mode="rrf")


def run_both(message):
    pipeline_w._last_evidence = None
    pipeline_r._last_evidence = None
    try:
        rw = pipeline_w.run(message)
    except Exception as e:
        rw = {"final_answer": f"❌ {e}", "evidence": []}
    try:
        rr = pipeline_r.run(message)
    except Exception as e:
        rr = {"final_answer": f"❌ {e}", "evidence": []}
    return rw, rr


def fmt_result(res, mode_name):
    text = res.get("final_answer", "")
    ev = res.get("evidence", [])
    info = f"\n\n**检索信息**\n模式: {mode_name} | 召回 {len(ev)} 段"
    if ev:
        info += "\n\n**来源 (Top-5):**"
        for i, item in enumerate(ev[:5], 1):
            meta = item.get("metadata", {})
            doi = item.get("doi", meta.get("source_paper", "?"))
            label = meta.get("label", "?")
            txt = item.get("text", "")[:120]
            info += f"\n- [{label}] {doi}\n  _{txt}_\n"
    return text + info


def respond_wrapper(msg, hist_w, hist_r):
    rw, rr = run_both(msg)

    ew = rw.get("evidence", [])
    er = rr.get("evidence", [])
    ids_w = {e.get("passage_id", "") for e in ew}
    ids_r = {e.get("passage_id", "") for e in er}
    olap = len(ids_w & ids_r)
    tot = len(ids_w | ids_r)
    pct = olap * 100 // tot if tot else 0

    text_w = fmt_result(rw, "weighted") + f"\n\n---\n📊 overlap: {olap}/{tot} ({pct}%)"
    text_r = fmt_result(rr, "RRF")

    if hist_w is None: hist_w = []
    if hist_r is None: hist_r = []
    hist_w.append({"role": "user", "content": msg})
    hist_w.append({"role": "assistant", "content": text_w})
    hist_r.append({"role": "user", "content": msg})
    hist_r.append({"role": "assistant", "content": text_r})
    return (msg, hist_w, hist_r)


with gr.Blocks(title="Fusion Mode 对比 — weighted vs RRF", css="""
    footer { visibility: hidden; }
""") as demo:

    gr.Markdown(
        "# ⚖️ Chroma+BM25 融合模式对比\n"
        "同一问题分别走 **加权融合 (weighted)** 和 **RRF 融合**，对比召回与回答差异。"
    )

    with gr.Row():
        cw = gr.Chatbot(label="加权融合 (weighted) — Chroma×0.6 + BM25×0.4", height=550)
        cr = gr.Chatbot(label="RRF 融合 — 按排名倒数 1/(60+rank) 融合", height=550)

    with gr.Row():
        msg = gr.Textbox(label="输入问题", placeholder="例如：NCM811 和 LRMO 哪个能量密度更高？", scale=4)
        btn = gr.Button("发送", scale=1, variant="primary")

    gr.Examples(
        examples=[
            ["NCM811 和 LRMO 哪个能量密度更高"],
            ["高电压正极材料的容量衰减机理"],
            ["固态电解质的离子电导率对比"],
            ["高能量密度锂电池材料筛选方案"],
            ["锂金属负极的枝晶抑制策略"],
        ],
        inputs=msg,
    )

    btn.click(respond_wrapper, inputs=[msg, cw, cr], outputs=[msg, cw, cr])
    msg.submit(respond_wrapper, inputs=[msg, cw, cr], outputs=[msg, cw, cr])


if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=PORT, share=False)
