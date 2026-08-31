"""单轮问答 Gradio 界面 — 高比能锂电池材料筛选 RAG"""

from typing import Dict, List, Optional, Tuple

import gradio as gr

from ..rag_pipeline import RAGPipeline
from ..baselines import BaselineA, BaselineB, run_comparison
from ..config import OLLAMA_MODEL, PLANNER_MODEL, WRITER_MODEL, REVIEWER_MODEL
from ..prompts import get_prompt_summary
from ..structured_output import normalize_latex, format_process_log, build_answer_markdown, save_markdown

def create_pipeline(
    planner_model: Optional[str] = None,
    writer_model: Optional[str] = None,
    reviewer_model: Optional[str] = None,
) -> RAGPipeline:
    return RAGPipeline(
        llm_backend="auto", retrieval_mode="chroma",
        planner_model=planner_model or None,
        writer_model=writer_model or None,
        reviewer_model=reviewer_model or None,
    )

def _format_evidence_md(results: List[Dict[str, Any]]) -> str:
    if not results:
        return "未检索到相关证据."
    lines = [f"## 检索证据(共 {len(results)} 条)", ""]
    for i, item in enumerate(results[:10], 1):
        snippet = normalize_latex(item.get("text", ""))[:200]
        lines.append(f"**{i}. [{item['passage_id']}]** (score={item['score']})")
        lines.append(f"> 来源: {item['source']}")
        lines.append(f"> {snippet}")
        lines.append("")
    return "\n".join(lines)

def refresh_model_list() -> Tuple[gr.update, gr.update, gr.update]:
    try:
        import urllib.request, json
        base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        req = urllib.request.Request(f"{base_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ollama_models = [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        ollama_models = []
    default_p = PLANNER_MODEL or OLLAMA_MODEL
    default_w = WRITER_MODEL or OLLAMA_MODEL
    default_r = REVIEWER_MODEL or OLLAMA_MODEL
    choices = ollama_models or ["qwen3:8b"]
    return (
        gr.update(choices=choices, value=default_p if default_p in choices else choices[0]),
        gr.update(choices=choices, value=default_w if default_w in choices else choices[0]),
        gr.update(choices=choices, value=default_r if default_r in choices else choices[0]),
    )

def answer_question(
    question: str, planner_model: str, writer_model: str, reviewer_model: str,
) -> Tuple[str, str, str, str]:
    """单轮问答,调用 RAGPipeline.run() 触发 Planner -> Retrieval -> Writer -> Reviewer 四阶段流水线."""
    if not question.strip():
        return "请输入高比能锂电池材料筛选相关问题.", "", "", ""
    pipeline = create_pipeline(planner_model, writer_model, reviewer_model)
    result = pipeline.run(question)

    # 自动留存:导出结构化 Markdown 到 output/
    try:
        md = build_answer_markdown(
            question=question,
            final_answer=result["final_answer"],
            plan=result["plan"],
            evidence=result["evidence"],
            reviewer_output=result["reviewer_output"],
            include_process_log=True,
        )
        save_markdown(md, "single_turn_auto")
    except Exception as e:
        print(f"[auto-save] 留存失败: {e}")

    return (
        result["final_answer"],
        _format_evidence_md(result["evidence"]),
        format_process_log(question, result["plan"], result["retrieval"], result["writer_output"], result["reviewer_output"]),
        pipeline.runtime_status(),
    )

def run_baseline_comparison(
    question: str, planner_model: str, writer_model: str, reviewer_model: str,
) -> str:
    if not question.strip():
        return "请输入问题."
    pipeline = create_pipeline(planner_model, writer_model, reviewer_model)
    baseline_a = BaselineA(pipeline.llm)
    baseline_b = BaselineB(pipeline.kb)
    try:
        result = run_comparison(question, baseline_a, baseline_b, pipeline, output_dir=None)
        return result["comparison_markdown"]
    except Exception as e:
        return f"对比实验失败: {e}"

def build_interface() -> gr.Blocks:
    with gr.Blocks(
        title="高比能锂电池材料筛选 RAG(单轮)",
        css="""body, .markdown, .prose, .chat-message {
    font-family: 'Noto Sans SC', 'Microsoft YaHei', 'PingFang SC', 'Segoe UI', Arial, sans-serif !important;
    font-size: 15px !important;
    line-height: 1.7 !important;
}"""
    ) as demo:
        gr.Markdown(
            "# ⚡ 高比能锂电池材料筛选 RAG — 单轮问答\n"
            "多智能体协作:Planner → Retrieval → Writer → Reviewer\n\n"
            "**对标**:  — 知识图谱课程 RAG 系统\n\n"
            "**场景**: 每次一问一答,独立处理,不保留上下文."
        )

        with gr.Accordion("⚙️ 模型配置", open=False):
            with gr.Row():
                planner_dd = gr.Dropdown(
                    label="Planner 模型", choices=["deepseek-v4-flash", "deepseek-v4-pro", "qwen3:8b"],
                    value=PLANNER_MODEL or OLLAMA_MODEL, allow_custom_value=True,
                )
                writer_dd = gr.Dropdown(
                    label="Writer 模型", choices=["deepseek-v4-pro", "deepseek-v4-flash", "qwen3:8b"],
                    value=WRITER_MODEL or OLLAMA_MODEL, allow_custom_value=True,
                )
                reviewer_dd = gr.Dropdown(
                    label="Reviewer 模型", choices=["deepseek-v4-pro", "deepseek-v4-flash", "qwen3:8b"],
                    value=REVIEWER_MODEL or OLLAMA_MODEL, allow_custom_value=True,
                )
            refresh_btn = gr.Button("🔄 刷新本地模型", variant="secondary", size="sm")
            refresh_btn.click(fn=refresh_model_list, outputs=[planner_dd, writer_dd, reviewer_dd])

        question_box = gr.Textbox(
            label="输入材料筛选问题", placeholder="例如:NCM811和LRMO哪个能量密度更高？", lines=2,
        )
        submit_btn = gr.Button("🚀 提交问题", variant="primary", size="lg")

        with gr.Tabs():
            with gr.TabItem("📝 回答"):
                answer_md = gr.Markdown(value="等待提问...")
            with gr.TabItem("📋 检索证据"):
                evidence_md = gr.Markdown(value="等待提问...")
            with gr.TabItem("📊 过程日志"):
                log_md = gr.Markdown(value="等待提问...")
            with gr.TabItem("🔍 运行状态"):
                runtime_md = gr.Markdown(value="等待提问...")
            with gr.TabItem("📜 Prompt 审计"):
                gr.Markdown(value=get_prompt_summary())

        with gr.Accordion("📊 基线对比(Baseline A / B / Ours)", open=False):
            comp_input = gr.Textbox(label="对比问题", placeholder="例如:高电压正极材料的容量衰减", lines=1)
            comp_btn = gr.Button("📊 运行对比", variant="secondary")
            comp_output = gr.Markdown()

        submit_btn.click(
            fn=answer_question, inputs=[question_box, planner_dd, writer_dd, reviewer_dd],
            outputs=[answer_md, evidence_md, log_md, runtime_md],
        )
        question_box.submit(
            fn=answer_question, inputs=[question_box, planner_dd, writer_dd, reviewer_dd],
            outputs=[answer_md, evidence_md, log_md, runtime_md],
        )
        comp_btn.click(
            fn=run_baseline_comparison, inputs=[comp_input, planner_dd, writer_dd, reviewer_dd],
            outputs=[comp_output],
        )
    return demo

def main():
    build_interface().launch(
        server_name="0.0.0.0", server_port=7860, share=False,
        theme="soft",
    )

if __name__ == "__main__":
    main()
