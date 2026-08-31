"""结构化输出生成器 — 高比能锂电池材料筛选场景

为高比能锂电池材料筛选 RAG 问答提供结构化的 Markdown 输出.

功能:
- classify_question: 问题类型分类(材料筛选类型优先)
- build_answer_markdown: 生成带章节的结构化回答
- format_evidence_display: 格式化证据展示
- format_process_log: 格式化多智能体过程日志
- extract_performance_data: 从回答中提取材料筛选的定量数据摘要
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import QTYPE_KEYWORDS, LABEL_KEYWORDS, COMPONENT_KEYWORDS

# ── 问题类型分类(材料筛选专用)──

def classify_question(question: str) -> str:
    """将问题分为:screening / numeric / experiment / trend / definition / general

    材料筛选场景优先检测 screening 类型.
    """
    q = question.strip()
    for t, kws in QTYPE_KEYWORDS.items():
        if any(kw in q for kw in kws):
            return t
    return "general"

# ── 归一化 ──

def normalize_latex(text: str) -> str:
    """去除 LaTeX 格式标记,使文本更适合终端显示"""
    text = re.sub(r"\$\$(.*?)\$\$", r" \1 ", text, flags=re.DOTALL)
    text = re.sub(r"\$(.*?)\$", r" \1 ", text)
    for cmd in ["mathrm", "text", "mathbf", "mathcal", "mathsf", "mathit"]:
        text = re.sub(r"\\" + cmd + r"\{([^}]+)\}", r"\1", text)
    text = re.sub(r"\\([a-zA-Z*]+)", r" ", text)
    text = re.sub(r"[_{}|]", "", text)
    text = re.sub(r" +", " ", text).strip()
    return text

# ── 材料筛选定量数据提取 ──

def extract_materials_screening_data(answer_text: str, evidence: List[Dict[str, Any]]) -> str:
    """从回答和证据中提取材料筛选相关的定量数据摘要.

    适配材料筛选场景.
    """
    text_pool = answer_text + "\n" + "\n".join([e.get("text", "") for e in evidence])

    lines: List[str] = []
    data_sections: Dict[str, List[str]] = {
        "比容量": [],
        "能量密度": [],
        "电压": [],
        "离子电导率": [],
        "容量保持率": [],
        "循环寿命": [],
    }

    numeric_patterns = [
        (r"(\d+(?:\.\d+)?)\s*(mAh/g)", "比容量"),
        (r"(\d+(?:\.\d+)?)\s*(mAh/cm²)", "比容量"),
        (r"(\d+(?:\.\d+)?)\s*(Wh/kg)", "能量密度"),
        (r"(\d+(?:\.\d+)?)\s*(Wh/L)", "能量密度"),
        (r"(\d+(?:\.\d+)?)\s*%(?:\s*(?:capacity|容量).*?(?:retention|保持))", "容量保持率"),
        (r"(\d+(?:\.\d+)?)\s*(mS/cm)", "离子电导率"),
        (r"(\d+(?:\.\d+)?)\s*(S/cm)", "离子电导率"),
        (r"(\d+(?:\.\d+)?)\s*(V(?:\s*vs)?)", "电压"),
        (r"(\d+(?:\.\d+)?)\s*(°C|℃)", "温度"),
        (r"(\d+(?:\.\d+)?)\s*(C-rate|C\b)", "倍率"),
        (r"(\d+)\s*(圈|次|cycles)", "循环寿命"),
    ]

    found_any = False
    for pattern, label in numeric_patterns:
        matches = re.findall(pattern, answer_text, re.IGNORECASE)
        if matches:
            # 去重取前3
            unique_vals = list(set(f"{m[0]} {m[1]}" for m in matches))[:3]
            if label in data_sections:
                data_sections[label].extend(unique_vals)
            found_any = True

    # 输出有数据的部分
    for section, values in data_sections.items():
        if values:
            lines.append(f"- **{section}**:{'/'.join(values[:3])}")

    if found_any:
        return "## 定量数据摘要(材料筛选)\n" + "\n".join(lines)
    return ""

# ── 结构化 Markdown 生成 ──

def build_answer_markdown(
    question: str,
    final_answer: str,
    plan: Dict[str, Any],
    evidence: List[Dict[str, Any]],
    reviewer_output: Optional[Dict[str, Any]] = None,
    include_process_log: bool = False,
) -> str:
    """生成结构化的最终回答 Markdown(材料筛选格式)."""
    qtype = classify_question(question)

    lines: List[str] = [
        f"# 高比能锂电池材料筛选 RAG 问答",
        "",
        "## 问题",
        question,
        "",
        f"*问题类型: {qtype} | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        "",
        "---",
        "",
        "## 材料筛选回答",
        final_answer.strip(),
    ]

    # 定量数据摘要
    quant_summary = extract_materials_screening_data(final_answer, evidence)
    if quant_summary:
        lines.extend(["", "---", "", quant_summary])

    # 置信度与审核发现
    if reviewer_output:
        confidence = reviewer_output.get("confidence", "unknown")
        issues = reviewer_output.get("issues", [])
        lines.extend([
            "",
            "---",
            "",
            "## 质量评估",
            f"- 置信度: **{confidence}**",
        ])
        if issues:
            lines.append("- 审核发现:")
            for issue in issues:
                lines.append(f"  - {issue}")

        if reviewer_output.get("fallback"):
            lines.append("- ⚠️ 当前为规则回退模式,答案未经 LLM 生成/审核.")

    # 证据引用
    lines.extend(["", "---", "", "## 参考来源(材料筛选证据)"])
    if evidence:
        seen_sources = set()
        for i, e in enumerate(evidence[:8], 1):
            pid = e.get("passage_id", f"E{i}")
            source = e.get("source", "未知")
            text_preview = normalize_latex(e.get("text", ""))[:150]
            lines.append(f"{i}. **[{pid}]** `{source}`")
            lines.append(f"   > {text_preview}...")
            lines.append("")
            seen_sources.add(source)
    else:
        lines.append("*未检索到相关证据*")

    # 过程日志(可选)
    if include_process_log:
        lines.extend([
            "",
            "---",
            "",
            "## 多智能体过程记录",
            "",
            "### Planner 规划(材料筛选视角)",
            f"- 任务理解: {plan.get('task_understanding', 'N/A')}",
            f"- 子检索问题:",
        ])
        for q in plan.get("retrieval_queries", []):
            lines.append(f"  - {q}")
        lines.append(f"- 回答大纲: {plan.get('answer_outline', [])}")
        lines.append(f"- 聚焦标签: {plan.get('focus_labels', [])}")
        lines.append(f"- 聚焦组件: {plan.get('focus_component', '无')}")
        lines.append(f"- Planner 模式: {'规则回退' if plan.get('fallback') else 'LLM 生成'}")

    return "\n".join(lines)

# ── 证据展示格式化 ──

def format_evidence_display(retrieval: Dict[str, Any]) -> str:
    """格式化检索证据展示."""
    results = retrieval.get("results", [])
    search_logs = retrieval.get("search_logs", [])

    if not results:
        return "未检索到相关证据."

    lines: List[str] = [
        "## 检索证据",
        f"共检索到 {len(results)} 条证据",
        "",
        "### 子检索日志",
    ]

    for q in search_logs:
        lines.append(f"#### 子问题: {q['query']}")
        if not q.get("hits"):
            lines.append("- 无命中")
        else:
            for hit in q["hits"]:
                lines.append(f"- 命中: {hit['passage_id']} | {hit['source']} | score={hit['score']}")

    lines.append("")
    lines.append("### 证据详情")

    for idx, item in enumerate(results[:10], start=1):
        snippet = normalize_latex(item.get("text", ""))[:300]
        lines.append(f"**证据 {idx}** [{item['passage_id']}]")
        lines.append(f"- 来源: {item['source']}")
        lines.append(f"- 评分: {item['score']}")
        lines.append(f"- 内容: {snippet}...")

    return "\n".join(lines)

# ── 过程日志格式化 ──

def format_process_log(
    question: str,
    plan: Dict[str, Any],
    retrieval: Dict[str, Any],
    writer_output: Dict[str, Any],
    review_output: Dict[str, Any],
) -> str:
    """格式化多智能体协作过程日志(材料筛选版本)."""
    issues = review_output.get("issues", [])

    lines = [
        "## 协作过程日志",
        "### 1. 用户输入",
        question,
        "### 2. Planner Agent 发言(材料筛选规划)",
        f"- 任务理解:{plan.get('task_understanding', '')}",
        f"- 子检索问题:{plan.get('retrieval_queries', [])}",
        f"- 回答结构:{plan.get('answer_outline', [])}",
        f"- 聚焦标签:{plan.get('focus_labels', [])}",
        f"- 聚焦组件:{plan.get('focus_component', '无')}",
        "### 3. Retrieval Agent 工具调用",
        f"- 数据源类型: {retrieval.get('db_type', plan.get('db_type', '未知'))}",
        f"- 共检索 {len(plan.get('retrieval_queries', [question]))} 个子问题",
        f"- 合并去重后共召回 {len(retrieval.get('results', []))} 条证据",
        "### 4. Writer Agent 决策",
        f"- 生成草稿答案,长度:{len(writer_output.get('draft_answer', ''))} 字符",
        "### 5. Reviewer Agent 反馈",
    ]

    if issues:
        for issue in issues:
            lines.append(f"- {issue}")
    else:
        lines.append("- 无额外问题")

    pinn_result = review_output.get("pinn_result")
    if pinn_result:
        lines.append("### 5.1 PINN 数值验证（独立物理计算）")
        if "error" in pinn_result:
            lines.append(f"- 计算不可用: {pinn_result['error']}")
        else:
            lines.append(
                f"- 模型: {pinn_result.get('model', '?')} | "
                f"置信度: {pinn_result.get('confidence', '?')}"
            )
            lines.append(
                f"- 放电比容量: {pinn_result.get('q_end_mAh_g', '?')} mAh/g | "
                f"平均电压: {pinn_result.get('v_mean', '?')} V"
            )
            lines.append(f"- 能量密度: {pinn_result.get('energy_wh_kg', '?')} Wh/kg")
            if pinn_result.get("data_gaps"):
                lines.append(f"- 数据缺口: {'; '.join(pinn_result['data_gaps'])}")
        lines.append("")

    if plan.get("fallback") or writer_output.get("fallback") or review_output.get("fallback"):
        lines.append("### 6. 运行说明")
        lines.append("- 当前有部分步骤处于回退模式.请确认 LLM 后端已正确配置.")

    return "\n".join(lines)

# ── 文件保存 ──

def save_markdown(md_content: str, prefix: str = "rag_materials_screening",
                  output_dir: Optional[Path] = None) -> str:
    """保存 Markdown 到文件并返回路径."""
    from .config import ensure_output_dir
    out_dir = output_dir or ensure_output_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{prefix}_{timestamp}.md"
    path.write_text(md_content, encoding="utf-8")
    print(f"[save] 结果已保存到: {path}")
    return str(path)

# ── 兼容旧接口 ──
extract_quantitative_summary = extract_materials_screening_data