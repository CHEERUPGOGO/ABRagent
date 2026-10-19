"""第五部分实验方案：大模型综合完整方案/证据/仿真，程序侧做引用与一致性验收。

输入优先级: 最终结构化设计 + 实际仿真参数 > 完整 design_scheme.md 分析背景 >
检索证据原文 > 模型提出的实验建议。模型正文属于待验证建议, 不能改写最终参数,
不能把跨体系文献数值写成本体系已验证结果, 也不能宣称性能目标已达成。
"""

import json
import re
from datetime import datetime
from pathlib import Path

from .hashing import canonical_hash

PLAN_FILENAME = "experiment_plan.json"
PLAN_START = "<!-- abr-experiment-plan:start -->"
PLAN_END = "<!-- abr-experiment-plan:end -->"
# 送入模型的设计方案上限；超出时显式告知, 不做静默截断。
_MAX_SCHEME_CHARS = 40000
_MAX_EVIDENCE_CHARS = 60000
_FABRICATION_PATTERNS = (
    r"已(?:经)?通过实验验证",
    r"实验(?:已)?证明.{0,8}达标",
    r"实测(?:已)?(?:达到|证实).{0,8}\d+\s*Wh",
    r"已(?:经)?达成\s*\d+\s*Wh",
)

PLAN_SYSTEM_PROMPT = """你是电池实验方案设计专家。基于给定的最终设计、完整方案分析、检索证据与仿真结果,
输出可执行的实验方案章节, 使用 Markdown 三级标题 (### 5.1 ... ### 5.6)。

硬性规则:
1. 最终设计数值 (材料体系、电极厚度、孔隙率、颗粒半径、电压窗口、倍率) 以"最终结构化设计"为准;
   完整方案 Markdown 中的数值若与最终设计或实际生效参数不一致, 必须说明差异并以最终值为验证对象。
2. 引用文献时保留原文段落 ID 形式 [passage_id]; 只能引用"可用证据"中真实存在的 ID, 不得虚构。
3. 文献数值必须注明材料体系与测试条件, 并标明是原文研究对象, 不得表述为本设计已实现。
4. 仿真仅覆盖单次放电, 不得推断循环寿命、首效、安全等已通过; 电芯比能量为材料级值乘固定系数折算。
5. 未确定的配比、温度、注液量、化成制度等给出"如何确定"的实验途径, 不填无依据的固定值。
6. 建议分组与验证路线 (基准组/推荐组/对照), 说明哪些变量保持一致、哪些做梯度实验。
7. 输出正文控制在 3500 中文字以内, 必须完整, 不得截断。

章节: 5.1 实验目标与分组 / 5.2 材料制备与极片加工 / 5.3 电芯组装与化成 /
5.4 性能测试与模型对照 / 5.5 结果判定与下一轮迭代 / 5.6 数据缺口与确定方法。"""


def input_fingerprint(doc, context, result, spec, scheme_md):
    """第五部分输入指纹：方案文本、证据、最终设计、仿真与测试条件。"""
    return canonical_hash({
        "scheme_md": scheme_md,
        "scheme": doc.get("scheme") or {},
        "reviewer_output": doc.get("reviewer_output") or {},
        "evidence": sorted(str(e.get("passage_id")) for e in doc.get("evidence") or []),
        "result": result or {},
        "spec": spec or {},
        "effective": context.get("effective") or {},
        "condition": context.get("condition") or {},
    })


def load_plan_record(task_dir):
    path = Path(task_dir) / PLAN_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("text") else None


_MATERIAL_ALIASES = {
    "li_metal": ("锂金属", "li metal", "lithium metal", "金属锂"),
    "si_base": ("硅碳", "硅基", "si-c", "si/c", "si@", "硅", "silicon"),
    "graphite": ("石墨", "graphite"),
    "ncm811": ("ncm811", "ncm 811", "高镍ncm811"),
    "high_concentration": ("高浓度", "高浓", "high concentration", "high_concentration"),
    "lhce": ("lhce", "局部高浓", "局部高浓度"),
}


def _material_named(value, text):
    token = str(value or "").strip()
    if not token:
        return True
    lowered = text.lower()
    if token.lower() in lowered:
        return True
    return any(alias.lower() in lowered for alias in _MATERIAL_ALIASES.get(token.lower(), ()))


def validate_plan_text(text, doc, context, target, tolerance):
    """程序侧验收：引用真实、内容完整、不改写最终参数、不虚构达标。"""
    errors = []
    scheme = doc.get("scheme") or {}
    if not isinstance(text, str) or len(text.strip()) < 200:
        return ["实验方案正文缺失或过短"]
    if len(re.findall(r"^\s*```", text, re.M)) % 2 or len(re.findall(r"(?<!\\)\*\*", text)) % 2:
        errors.append("实验方案代码块或粗体标记未闭合，疑似截断")
    for title in ("5.1", "5.2", "5.3", "5.4"):
        if not re.search(rf"^###\s*{re.escape(title)}", text, re.M):
            errors.append(f"缺少实验方案小节 {title}")
    evidence = {str(e.get("passage_id")) for e in doc.get("evidence") or []}
    cited = set(re.findall(r"\[([A-Za-z0-9_\-]+)\]", text))
    unknown = sorted(c for c in cited if c not in evidence)
    if unknown:
        errors.append("引用未在本次归档证据中定位: " + ", ".join(f"[{c}]" for c in unknown))
    for material_key in ("cathode", "anode"):
        if not _material_named(scheme.get(material_key), text):
            errors.append(f"实验方案未体现最终{('正极' if material_key == 'cathode' else '负极')}材料 {scheme.get(material_key)}")
    geometry = (scheme.get("geometry") or {})
    effective = context.get("effective") or {}
    allowed = []
    for comp, side in (("cathode", "pe"), ("anode", "ne")):
        declared = (geometry.get(comp) or {}).get("L_um")
        actual = (effective.get(side) or {}).get("L")
        allowed += [v for v in (declared, actual * 1e6 if actual else None) if v]
    stated = re.findall(r"(?:厚度|L)[^\d]{0,6}(\d+(?:\.\d+)?)\s*(?:μm|um)", text)
    if allowed and stated and any(
            not any(abs(float(s) - a) / a < 0.02 for a in allowed) for s in stated):
        errors.append("厚度表述与最终声明及实际生效值不一致")
    if target is not None:
        threshold = target * (1 - tolerance / 100)
        energy = (context.get("result") or {}).get("energy_wh_kg")
        if isinstance(energy, (int, float)) and energy < threshold:
            for pattern in _FABRICATION_PATTERNS:
                if re.search(pattern, text):
                    errors.append("实验方案宣称目标已达成，但仿真未达标，属未经证实的结论")
    return errors


def _assemble_user_prompt(task_goal, scheme_md, context, doc, result, spec):
    scheme = doc.get("scheme") or {}
    geometry = scheme.get("geometry") or {}
    effective = context.get("effective") or {}
    lines = [f"课题目标: {task_goal}", "", "## 最终结构化设计 (数值以此为准)", "",
             f"- 正极材料: {scheme.get('cathode')}；负极材料: {scheme.get('anode')}；电解液: {scheme.get('electrolyte')}",
             f"- 设计版本: {scheme.get('design_revision')}",
             f"- 声明几何: {json.dumps(geometry, ensure_ascii=False)}"]
    if effective:
        lines.append(f"- 实际生效几何(仿真输入): {json.dumps(effective, ensure_ascii=False)}")
    if result:
        lines += ["", "## 仿真结果摘要", "",
                  f"- 状态: {result.get('status')}；体系: {result.get('system_id')}；倍率: {result.get('c_rate')} C",
                  f"- 比容量: {result.get('specific_capacity_mAh_g')} mAh/g；平均电压: {result.get('average_voltage_V')} V",
                  f"- 电芯折算比能量: {result.get('energy_wh_kg')} Wh/kg",
                  f"- 能量口径: {json.dumps(result.get('energy_accounting') or {}, ensure_ascii=False)}",
                  f"- 终止模式: {result.get('termination')}；限制电极: {result.get('limiting_electrode')}"]
    if spec:
        condition = ((spec.get("cell_spec") or {}).get("condition")) or {}
        lines.append(f"- 测试条件: {json.dumps(condition, ensure_ascii=False)}")
    issues = (doc.get("reviewer_output") or {}).get("issues") or []
    if issues:
        lines += ["", "## 尚未解决的审核意见 (不得再次写成确定结论)", ""] + [f"- {i}" for i in issues]
    lines += ["", "## 完整设计方案 Markdown", ""]
    if len(scheme_md) > _MAX_SCHEME_CHARS:
        lines += [f"(方案全文约 {len(scheme_md)} 字符，超出单次上限，以下为前 {_MAX_SCHEME_CHARS} 字符，"
                  "其余部分请依据最终结构化设计补充，不要臆造未给出内容)", "", scheme_md[:_MAX_SCHEME_CHARS]]
    else:
        lines.append(scheme_md or "(方案 Markdown 缺失，仅依据结构化设计生成)")
    lines += ["", "## 可用证据 (只能引用下列 passage_id)", ""]
    used = 0
    for e in doc.get("evidence") or []:
        block = f"[{e.get('passage_id')}] 来源:{e.get('source')}\n内容:{e.get('text')}\n"
        if used + len(block) > _MAX_EVIDENCE_CHARS:
            lines.append(f"(证据过长已截断，仍可引用的段落 ID: {', '.join('[' + str(x.get('passage_id')) + ']' for x in (doc.get('evidence') or []))})")
            break
        lines.append(block)
        used += len(block)
    lines += ["", "请输出完整实验方案章节。"]
    return "\n".join(lines)


def _first_valid(*values):
    for v in values:
        s = str(v or "").strip()
        if s and s not in ("dummy_key", "none", "None") and not s.startswith("$("):
            return s
    return ""


def _llm_client(mgr):
    cfg = (mgr.config if mgr else {}) or {}
    llm_cfg = cfg.get("llm") or {}
    openai_cfg = cfg.get("openai") or {}
    # 未配置 llm/openai 段落时判定为确定性环境 (单测/离线), 不回退环境变量,
    # 避免测试或离线运行误连真实大模型。
    if not llm_cfg and not openai_cfg:
        return None
    if not (cfg.get("runtime_options") or {}).get("experiment_plan_llm", True):
        return None
    import os
    api_key = _first_valid(openai_cfg.get("openai_api_key"), llm_cfg.get("api_key"), os.getenv("OPENAI_API_KEY"))
    api_base = _first_valid(openai_cfg.get("openai_api_base"), llm_cfg.get("base_url"), os.getenv("OPENAI_API_BASE"))
    model_name = _first_valid(llm_cfg.get("writer_model"), llm_cfg.get("model"),
                              openai_cfg.get("model_name"), os.getenv("OPENAI_MODEL"))
    if not api_key:
        return None
    from auto_battery_research.rag import LLMClient
    client = LLMClient(model_name=model_name or None, api_key=api_key, api_base=api_base or None, temperature=0.2)
    return client if client.available else None


def generate_experiment_plan(mgr, task_dir, doc, context, result, spec, scheme_md, target, tolerance):
    """生成并验收第五部分；成功返回 {text, marker, source}，失败返回 None (调用方回退)。"""
    fingerprint = input_fingerprint(doc, context, result, spec, scheme_md)
    cached = load_plan_record(task_dir)
    if cached and cached.get("fingerprint") == fingerprint and not validate_plan_text(
            cached.get("text", ""), doc, context, target, tolerance):
        return {"text": cached["text"], "marker": (PLAN_START, PLAN_END), "source": "cached"}
    client = _llm_client(mgr)
    if client is None:
        return None
    user_prompt = _assemble_user_prompt(getattr(mgr, "target_goal", "") or "", scheme_md, context, doc, result, spec)
    last_error = ""
    for _attempt in range(2):
        prompt = user_prompt + (f"\n\n上次生成未通过验收: {last_error}\n请针对性修正后重新输出完整章节。" if last_error else "")
        try:
            text = client.chat(PLAN_SYSTEM_PROMPT, prompt, temperature=0.2, trace_tag="report_recipe")
        except Exception as exc:
            last_error = str(exc)
            continue
        errors = validate_plan_text(text, doc, context, target, tolerance)
        if errors:
            last_error = "; ".join(errors)
            continue
        record = {"fingerprint": fingerprint, "generated_at": datetime.now().isoformat(),
                  "model": getattr(client, "_model_name", ""), "attempts": _attempt + 1,
                  "citations": sorted(set(re.findall(r"\[([A-Za-z0-9_\-]+)\]", text))), "text": text}
        Path(task_dir, PLAN_FILENAME).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"text": text, "marker": (PLAN_START, PLAN_END), "source": "llm"}
    return None


def plan_fingerprint_marker(fingerprint):
    return f"<!-- abr-experiment-plan-id:{fingerprint} -->"


def validate_plan_record(task_dir, doc, context, result, spec, scheme_md, target, tolerance, content):
    """Stage 6 验收：报告第五部分与保存记录、当前输入三者一致。"""
    if PLAN_START not in content:
        return ["报告缺少实验方案生成标记"]
    record = load_plan_record(task_dir)
    if not record:
        return ["未找到实验方案生成记录 experiment_plan.json"]
    current = input_fingerprint(doc, context, result, spec, scheme_md)
    if record.get("fingerprint") != current or plan_fingerprint_marker(current) not in content:
        return ["实验方案输入指纹与当前设计/证据/仿真不一致，需重新生成"]
    errors = validate_plan_text(record.get("text", ""), doc, context, target, tolerance)
    expected_block = f"{PLAN_START}\n{plan_fingerprint_marker(current)}\n{record.get('text','')}\n{PLAN_END}"
    if expected_block not in content:
        errors.append("报告中的实验方案与保存记录不一致")
    return errors
