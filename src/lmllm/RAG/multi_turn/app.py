"""多轮对话 Gradio 界面 — 高比能锂电池材料筛选 RAG(MVP 级)

增强:
- SQLite 对话历史落库(history_store.py)
- 会话管理(新建/切换/恢复)
- 重启回溯(自动恢复最近会话)
- logging 结构化日志

启动:
    cd /home/ls/xiaoyue/LLM2/LMLLM
    python -m src.lmllm.RAG.multi_turn.app
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import gradio as gr

from ..rag_pipeline import RAGPipeline
from ..baselines import BaselineA, BaselineB, run_comparison
from ..config import OLLAMA_MODEL, PLANNER_MODEL, WRITER_MODEL, REVIEWER_MODEL, RETRIEVAL_MODE, ensure_output_dir
from ..prompts import get_prompt_summary
from ..structured_output import normalize_latex, format_process_log, build_answer_markdown, save_markdown
from .history_store import HistoryStore

# ════════════════════════════════════════════════════════════
# 日志配置(结构化日志,自动写入文件)
# ════════════════════════════════════════════════════════════

LOG_DIR = ensure_output_dir() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"rag_multi_turn_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("rag_multi_turn")

# ════════════════════════════════════════════════════════════
# 全局:历史存储
# ════════════════════════════════════════════════════════════

history_store = HistoryStore()

# ════════════════════════════════════════════════════════════
# 辅助函数
# ════════════════════════════════════════════════════════════

def create_pipeline(
    planner_model: Optional[str] = None,
    writer_model: Optional[str] = None,
    reviewer_model: Optional[str] = None,
) -> RAGPipeline:
    return RAGPipeline(
        llm_backend="auto", retrieval_mode=RETRIEVAL_MODE,
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
        doi = item.get("doi", "")
        title = item.get("title", "")
        lines.append(f"**{i}. [{item['passage_id']}]** (score={item['score']})")
        if title:
            lines.append(f"> 标题: {title}")
        if doi:
            lines.append(f"> DOI: {doi}")
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

# ════════════════════════════════════════════════════════════
# 会话管理函数
# ════════════════════════════════════════════════════════════

def _session_label(s: dict) -> str:
    """为会话生成下拉标签."""
    msg_count = s.get("message_count", 0)
    title = s.get("title", "未命名")[:30]
    # 取 updated_at 的短时间
    updated = s.get("updated_at", "")
    if updated and len(updated) >= 16:
        updated = updated[5:16]  # "MM-DDTHH:MM"
    return f"[{msg_count}轮] {title} ({updated})"

def list_sessions_for_dropdown() -> Tuple[gr.update, List[Dict[str, Any]]]:
    """刷新会话下拉列表,返回 (dropdown_update, sessions_list)."""
    sessions = history_store.list_sessions(limit=50)
    choices = [(_session_label(s), s["id"]) for s in sessions]
    # 如果有会话,选中第一个(最新)
    value = sessions[0]["id"] if sessions else None
    logger.info(f"会话列表刷新: {len(sessions)} 个会话")
    return (
        gr.update(choices=choices, value=value),
        sessions,
    )

def load_last_display(session_id: str) -> Tuple[str, str]:
    """加载最后一条助手消息的 evidence_display 和 process_log.

    Returns:
        (evidence_md, log_md)，无数据时返回默认占位文本
    """
    if not session_id:
        return "等待提问...", "等待提问..."
    msgs = history_store.get_messages(session_id)
    for m in reversed(msgs):
        if m["role"] == "assistant":
            ev = m.get("evidence_display", "") or "等待提问..."
            pl = m.get("process_log", "") or "等待提问..."
            return ev, pl
    return "等待提问...", "等待提问..."


def load_session_messages(session_id: str) -> List[Tuple[str, str]]:
    """从 SQLite 加载会话历史为 (user, assistant) 元组格式."""
    if not session_id:
        logger.warning("load_session_messages: session_id 为空")
        return []
    msgs = history_store.get_messages(session_id)
    chat_history = []
    for m in msgs:
        if m["role"] == "user":
            chat_history.append((m["content"], None))
        elif m["role"] == "assistant" and chat_history and chat_history[-1][1] is None:
            last_user, _ = chat_history[-1]
            chat_history[-1] = (last_user, m["content"])
    logger.info(f"加载会话 {session_id[:16]}...: {len(msgs)} 条消息, {len(chat_history)} 轮")
    return chat_history

def switch_session(
    session_id: str,
    planner_model: str,
    writer_model: str,
    reviewer_model: str,
) -> Tuple[list, str, str, str]:
    """切换会话:加载历史消息+恢复上轮展示面板."""
    logger.info(f"切换会话: {session_id[:16]}...")
    chat_history = load_session_messages(session_id)
    session = history_store.get_session(session_id)
    title = session["title"] if session else "未知会话"
    evidence_md, log_md = load_last_display(session_id)
    return _to_gradio_msgs(chat_history), f"已切换到会话: {title}", evidence_md, log_md

def new_session_handler() -> Tuple[str, List[Tuple[str, str]], str, str, str, str, gr.update]:
    """新建会话:创建新的 SQLite 会话,清空 chatbot."""
    session_id = history_store.create_session()
    logger.info(f"新建会话: {session_id}")
    # 刷新下拉列表
    dd_update, _ = list_sessions_for_dropdown()
    return session_id, [], "新会话已创建", "等待提问...", "等待提问...", "等待提问...", dd_update

# ════════════════════════════════════════════════════════════
# 多轮对话处理(含落库 + 日志)
# ════════════════════════════════════════════════════════════

def _to_tuples(gradio_msgs: list) -> List[Tuple[str, str]]:
    """将 Gradio 6.x 新消息格式转为内部 (user, assistant) 元组格式."""
    result = []
    for m in gradio_msgs:
        if isinstance(m, (tuple, list)) and len(m) == 2 and not isinstance(m[0], dict):
            return gradio_msgs  # 已经是旧格式
        if isinstance(m, dict):
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, list):
                texts = [c.get("text", "") for c in content if isinstance(c, dict)]
                content = " ".join(texts)
            if role == "user":
                result.append((content, None))
            elif role == "assistant" and result and result[-1][1] is None:
                last_user, _ = result[-1]
                result[-1] = (last_user, content)
    return result

def _to_gradio_msgs(tuples: List[Tuple[str, str]]) -> list:
    """将内部 (user, assistant) 元组转为 Gradio 6.x 新消息格式."""
    result = []
    for user_msg, asst_msg in tuples:
        result.append({"role": "user", "content": [{"text": user_msg, "type": "text"}]})
        if asst_msg:
            result.append({"role": "assistant", "content": [{"text": asst_msg, "type": "text"}]})
    return result

def respond_multi_turn(
    message: str,
    chat_history: list,
    session_id: str,
    planner_model: str,
    writer_model: str,
    reviewer_model: str,
) -> Tuple[str, list, str, str, str, str, str, gr.update]:
    """多轮对话:RAG 问答 + 消息落库 + 日志 + 更新会话标题.

    Returns: (清空输入框, history, 证据, 日志, 运行状态, 会话状态, 下拉更新)
    """
    if not message.strip():
        return "", chat_history, session_id, "等待提问...", "等待提问...", "等待提问...", "等待提问...", gr.update()

    # ── Gradio 6.x 消息格式 → 内部 (user, asst) 元组 ──
    chat_tuples = _to_tuples(chat_history) if chat_history else []

    # ── 用户消息落库 ──
    history_store.add_message(session_id, "user", message)
    logger.info(f"[用户] 会话={session_id[:12]} 问题={message[:60]}...")

    # ── RAG 问答 ──
    pipeline = create_pipeline(planner_model, writer_model, reviewer_model)
    result = pipeline.chat(message, chat_history=chat_tuples)

    answer = result["final_answer"]
    evidence = result.get("evidence", [])
    evidence_md = _format_evidence_md(evidence)
    log_md = format_process_log(
        message,
        result["plan"],
        result["retrieval"],
        result["writer_output"],
        result["reviewer_output"],
    )
    runtime_md = pipeline.runtime_status()

    # ── 助手回答落库(含证据 + 展示用 Markdown) ──
    history_store.add_message(
        session_id, "assistant", answer,
        evidence=evidence,
        evidence_display=evidence_md,
        process_log=log_md,
    )
    logger.info(f"[助手] 会话={session_id[:12]} 回答长度={len(answer)} 证据数={len(evidence)}")

    # ── 更新会话标题(用第一轮的问题) ──
    msg_count = history_store.get_message_count(session_id)
    if msg_count <= 2:
        title = message[:40]
        history_store.update_session_title(session_id, title)
        logger.info(f"[会话] 标题更新为: {title}")

    # ── 更新 chatbot ──
    chat_tuples.append((message, answer))
    gradio_chat = _to_gradio_msgs(chat_tuples)

    # ── 刷新下拉列表(让会话标题更新) ──
    dd_update, _ = list_sessions_for_dropdown()

    session_info_text = f"会话进行中({len(chat_tuples)} 轮)"
    return "", gradio_chat, session_id, session_info_text, evidence_md, log_md, runtime_md, dd_update

def delete_session_handler(
    session_id: str,
    chat_history: List[Tuple[str, str]],
) -> Tuple[Any, str, str, str, str, gr.update]:
    """删除当前会话."""
    if not session_id:
        return gr.update(), "没有可删除的会话.", "等待提问...", "等待提问...", "等待提问...", gr.update()
    history_store.delete_session(session_id)
    logger.info(f"删除会话: {session_id[:16]}...")
    dd_update, sessions = list_sessions_for_dropdown()
    # 如果还有会话,切换到最新
    if sessions:
        new_id = sessions[0]["id"]
        chat_history = load_session_messages(new_id)
        return new_id, f"已删除会话,当前会话: {sessions[0]['title'][:30]}", "等待提问...", "等待提问...", "等待提问...", dd_update
    else:
        # 自动创建新会话
        new_id = history_store.create_session()
        dd_update, _ = list_sessions_for_dropdown()
        return new_id, "已删除所有会话,已自动创建新会话.", "等待提问...", "等待提问...", "等待提问...", dd_update

# ════════════════════════════════════════════════════════════
# Gradio 界面
# ════════════════════════════════════════════════════════════

def build_interface() -> gr.Blocks:
    # ── 在构建 UI 前初始化会话数据(替代不可靠的 demo.load) ──
    sessions = history_store.list_sessions(limit=50)
    if sessions:
        initial_session_id = sessions[0]["id"]
        initial_chat = load_session_messages(initial_session_id)
        initial_title = sessions[0].get("title", "已恢复")[:30]
        initial_info = f"已恢复最近会话: {initial_title}({len(initial_chat)} 轮)"
    else:
        initial_session_id = history_store.create_session("首次自动创建")
        initial_chat = []
        initial_info = "新会话已自动创建(首次启动)."
        sessions = history_store.list_sessions(limit=50)
    # 转为 Gradio 6.x 消息格式
    initial_gradio_chat = _to_gradio_msgs(initial_chat)
    initial_choices = [(_session_label(s), s["id"]) for s in sessions]

    with gr.Blocks(
        title="高比能锂电池材料筛选 RAG(多轮·MVP)",
        css="""body, .markdown, .prose, .chat-message {
    font-family: 'Noto Sans SC', 'Microsoft YaHei', 'PingFang SC', 'Segoe UI', Arial, sans-serif !important;
    font-size: 15px !important;
    line-height: 1.7 !important;
}"""
    ) as demo:
        gr.Markdown(
            "# ⚡ 高比能锂电池材料筛选 RAG — 多轮对话(MVP)\n"
            "多智能体协作:Planner → Retrieval → Writer → Reviewer\n\n"
            "**新增 MVP 特性**:\n"
            "- 对话历史自动落库(SQLite)→ 重启可回溯\n"
            "- 会话管理:新建 / 切换 / 删除\n"
            "- 结构化日志(自动写入文件)\n"
            "- 一键导出对话(Markdown)"
        )

        # ── 会话管理 ──
        with gr.Row():
            session_dd = gr.Dropdown(
                label="📁 当前会话", scale=4,
                choices=initial_choices, value=initial_session_id, interactive=True,
            )
            new_session_btn = gr.Button("➕ 新建会话", variant="primary", scale=1)
            del_session_btn = gr.Button("🗑️ 删除会话", variant="stop", scale=1)

        # ── 隐藏状态(存储当前 session_id) ──
        session_state = gr.State(value=initial_session_id)

        # ── 模型配置 ──
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

        # ── 对话面板 ──
        chatbot = gr.Chatbot(
            label="材料筛选对话", height=400,
            avatar_images=(None, "⚡"),
            value=initial_gradio_chat,
        )
        msg = gr.Textbox(
            label="输入材料筛选问题(支持追问)",
            placeholder="例如:NCM811和LRMO哪个能量密度更高？",
            lines=2,
        )

        with gr.Row():
            submit_btn = gr.Button("🚀 发送", variant="primary", size="lg")
            cancel_btn = gr.Button("⏹ 取消", variant="stop", size="lg")
            clear_btn = gr.Button("🗑️ 清空对话", variant="secondary", size="lg")
            export_btn = gr.Button("📥 导出对话", variant="secondary", size="sm")

        # ── 会话状态信息 ──
        session_info = gr.Markdown(value=initial_info)

        # ── 输出面板 ──
        with gr.Tabs():
            with gr.TabItem("📋 检索证据"):
                evidence_md = gr.Markdown(value="等待提问...")
            with gr.TabItem("📊 过程日志"):
                log_md = gr.Markdown(value="等待提问...")
            with gr.TabItem("🔍 运行状态"):
                runtime_md = gr.Markdown(value="等待提问...")
            with gr.TabItem("📜 Prompt 审计"):
                gr.Markdown(value=get_prompt_summary())
            with gr.TabItem("📥 对话导出"):
                export_md = gr.Markdown(value="点击下方「导出对话」按钮.")

        # ═══════════════════════════════════════════════════════
        # 事件绑定
        # ═══════════════════════════════════════════════════════

        # 发送消息
        submit_click_event = submit_btn.click(
            fn=respond_multi_turn,
            inputs=[msg, chatbot, session_state, planner_dd, writer_dd, reviewer_dd],
            outputs=[msg, chatbot, session_state, session_info, evidence_md, log_md, runtime_md, session_dd],
        ).then(
            fn=lambda sid: gr.update(value=sid),
            inputs=[session_state],
            outputs=[session_dd],
        )

        msg_submit_event = msg.submit(
            fn=respond_multi_turn,
            inputs=[msg, chatbot, session_state, planner_dd, writer_dd, reviewer_dd],
            outputs=[msg, chatbot, session_state, session_info, evidence_md, log_md, runtime_md, session_dd],
        )

        # 取消当前正在执行的请求
        cancel_btn.click(fn=lambda: None, cancels=[submit_click_event, msg_submit_event])

        # 切换会话
        session_dd.change(
            fn=switch_session,
            inputs=[session_dd, planner_dd, writer_dd, reviewer_dd],
            outputs=[chatbot, session_info, evidence_md, log_md],
        ).then(
            fn=lambda sid: gr.update(value=sid),
            inputs=[session_dd],
            outputs=[session_state],
        )

        # 新建会话
        new_session_btn.click(
            fn=new_session_handler,
            inputs=[],
            outputs=[session_state, chatbot, session_info, evidence_md, log_md, runtime_md, session_dd],
        )

        # 删除会话
        del_session_btn.click(
            fn=delete_session_handler,
            inputs=[session_state, chatbot],
            outputs=[session_state, session_info, evidence_md, log_md, runtime_md, session_dd],
        )

        # 清空当前会话(仅清 Gradio 端,SQLite 还在)
        def clear_chat_only(session_id: str) -> List[Tuple[str, str]]:
            if session_id:
                logger.info(f"清空展示会话: {session_id[:16]}...(数据仍在 SQLite 中)")
            return []

        clear_btn.click(
            fn=clear_chat_only,
            inputs=[session_state],
            outputs=[chatbot],
        ).then(
            fn=lambda: ("等待提问...", "等待提问...", "等待提问..."),
            outputs=[evidence_md, log_md, runtime_md],
        )

        # 导出对话
        def export_dialogue(session_id: str) -> str:
            if not session_id:
                return "没有可导出的会话."
            msgs = history_store.get_messages(session_id)
            session = history_store.get_session(session_id)
            title = session["title"] if session else "未命名"
            lines = [
                "# 高比能锂电池材料筛选 — 多轮对话导出",
                f"会话: {title}",
                f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"消息数: {len(msgs)}",
                "",
                "---",
                "",
            ]
            for i, m in enumerate(msgs):
                role = "🧑" if m["role"] == "user" else "⚡"
                lines.append(f"### {role} {m['role']} ({m['timestamp'][:19]})")
                lines.append(m["content"])
                lines.append("")
            md = "\n".join(lines)
            path = save_markdown(md, f"multi_turn_{session_id[:8]}")
            return f"对话已导出到: {path}\n\n---\n\n{md}"

        export_btn.click(
            fn=export_dialogue,
            inputs=[session_state],
            outputs=[export_md],
        )

    demo.queue()
    return demo

def _find_free_port(start: int = 7861, max_attempts: int = 10) -> int:
    """从 start 开始找第一个可用端口."""
    import socket
    for port in range(start, start + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"无法分配端口 ({start}–{start + max_attempts - 1} 均被占用)")

def main():
    logger.info("=" * 60)
    logger.info("RAG 多轮对话系统启动(MVP 版)")
    logger.info(f"日志文件: {LOG_FILE}")
    logger.info(f"数据库: {history_store.db_path}")
    logger.info("=" * 60)

    port = _find_free_port(7861)
    if port != 7861:
        logger.warning(f"端口 7861 被占用,自动切换至端口 {port}")

    build_interface().launch(
        server_name="0.0.0.0", server_port=port, share=False,
        theme="soft", show_error=True,
    )

if __name__ == "__main__":
    main()
