"""最终报告的确定性数据视图：只消费选定设计和与之匹配的仿真产物。"""

import json
import hashlib
from typing import Any


ENERGY_SCOPE = (
    "电芯比能量采用材料级比能量乘固定体系系数折算，未逐项计算集流体、隔膜、"
    "电解液和封装质量；当前 PINN 使用训练体系固定电解液，未验证方案电解液/添加剂。"
    "本次最佳仅指已评估候选，不代表物理上限，不能据此证明非活性质量优化收益。"
)


def report_identity(doc: dict, result: dict | None, spec: dict | None) -> str:
    """报告输入指纹包含完整结构化方案与实际结果，变更后必须重新编译。"""
    payload = {"scheme": doc.get("scheme") or {}, "reviewer_output": doc.get("reviewer_output") or {},
               "result": result or {}, "spec": spec or {}}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    return f"<!-- abr-report-identity:{digest} -->"


def validate_report_blocks(text: str, context: dict, target: float | None, tolerance: float) -> list[str]:
    """关键数值正文由相同视图重算验收，禁止旧值混入权威块。

    final-design / energy 必须始终存在; final-recipe 仅在回退渲染路径出现,
    存在时按同一视图重算, 缺失时交给实验方案生成记录单独校验。
    """
    errors = []
    required = {"final-design": render_selected_design(context), "energy": render_energy(context, target, tolerance)}
    for key, value in required.items():
        if f"<!-- abr-{key}:start -->\n{value}\n<!-- abr-{key}:end -->" not in text:
            errors.append(f"{key} 与最终设计/仿真不一致")
    if "<!-- abr-final-recipe:start -->" in text:
        if f"<!-- abr-final-recipe:start -->\n{render_recipe(context)}\n<!-- abr-final-recipe:end -->" not in text:
            errors.append("final-recipe 与最终设计/仿真不一致")
    return errors


def display(value: Any) -> str:
    if value is None or value == "":
        return "未提供"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def report_context(doc: dict, result: dict | None, spec: dict | None = None) -> dict:
    scheme = doc.get("scheme") or {}
    result = result or {}
    accounting = dict(result.get("energy_accounting") or {})
    # 历史结果可从已保存的材料/电芯能量反推本轮折算系数，不套用现版模型默认值。
    material = result.get("material_energy_Wh_kg")
    energy = result.get("energy_wh_kg")
    if not accounting and isinstance(material, (float, int)) and material > 0 and isinstance(energy, (float, int)):
        accounting = {"method": "fixed_material_to_cell_ratio", "material_energy_wh_kg": material,
                      "cell_energy_wh_kg": energy, "cell_energy_ratio": energy / material}
    return {"scheme": scheme, "geometry": scheme.get("geometry") or {},
            "effective": (result.get("pinn") or {}).get("design") or {},
            "condition": ((spec or {}).get("cell_spec") or {}).get("condition") or {},
            "result": result, "energy_accounting": accounting,
            "reviewer_output": doc.get("reviewer_output") or {}}


def render_selected_design(context: dict) -> str:
    """正文只渲染最终版本；初始提案链接留档，不能混入当前配方。"""
    s, g = context["scheme"], context["geometry"]
    if not s:
        return "*本课题尚未生成独立的 Stage 4 电池体系设计方案 (design_scheme.json)。缺失参数保持空白。*"
    lines = [f"**选定设计版本：{display(s.get('design_revision'))}**；几何哈希：`{display(s.get('geometry_hash'))}`。",
             "", "| 组件 | 最终选型 |", "|---|---|",
             f"| 正极 | {display(s.get('cathode'))} |", f"| 负极 | {display(s.get('anode'))} |",
             f"| 电解液 | {display(s.get('electrolyte'))} |",
             f"| 添加剂 | {display(', '.join(s.get('additives') or []))} |", "",
             "| 参数 | 正极声明值 | 负极声明值 |", "|---|---|---|"]
    for key, name in (("L_um", "厚度 μm"), ("porosity", "孔隙率"), ("R_s_um", "颗粒半径 R_s μm"), ("D_s_m2s", "固相扩散系数 m²/s")):
        lines.append(f"| {name} | {display((g.get('cathode') or {}).get(key))} | {display((g.get('anode') or {}).get(key))} |")
    cond = g.get("condition") or {}
    lines += ["", f"设计测试条件：{display(cond.get('c_rate'))} C，{display(cond.get('v_min'))}–{display(cond.get('v_max'))} V。",
              (f"显式 N/P：{display(g['n_p_ratio'])}；实际厚度以仿真有效输入表为准。" if g.get("n_p_ratio") is not None else
               "N/P 未提供，不进行容量比厚度校准，正负极分别使用声明厚度。"),
              "工程设计值不是实测值；模型基准 D_s 不代表文献测量结果。", ""]
    for path, source in (g.get("parameter_sources") or {}).items():
        lines.append(f"- {display(path)}：{display(source.get('origin'))} / {display(source.get('basis'))}")
    lines += ["", "原始文献分析与初始提案见 [RAG 档案](rag_result.json)，最终结构化方案见 [设计契约](design_scheme.json)。",
              "初始提案的数值和计算未经本轮重新验证，不作为最终设计或实验参数。"]
    review = context.get("reviewer_output") or {}
    if review.get("issues"):
        lines += ["", "**尚未解决的审核意见（不代表审核通过）**"]
        lines.extend(f"- {display(issue)}" for issue in review["issues"])
    return "\n".join(lines)


def render_energy(context: dict, target: float | None, tolerance: float) -> str:
    r, a = context["result"], context["energy_accounting"]
    energy = r.get("energy_wh_kg")
    if not isinstance(energy, (int, float)):
        return "**目标判定：未完成有效仿真，不能判定达标。**\n\n" + ENERGY_SCOPE
    threshold = target * (1 - tolerance / 100) if target is not None else None
    verdict = "无数值目标" if threshold is None else ("达到容差阈值" if energy >= threshold else "未达标")
    lines = [f"**目标判定：{verdict}。当前选定候选折算比能量 {energy:.1f} Wh/kg。**"]
    if threshold is not None:
        lines.append(f"目标 {target:g} Wh/kg；容差 {tolerance:g}%；下限 {threshold:.1f} Wh/kg；距下限缺口 {max(0, threshold-energy):.1f} Wh/kg。")
    lines += ["", "**能量计算口径**", "",
              f"材料级 {display(a.get('material_energy_wh_kg'))} Wh/kg × 固定折算系数 {display(a.get('cell_energy_ratio'))} = 电芯折算 {energy:.1f} Wh/kg。",
              ENERGY_SCOPE]
    return "\n".join(lines)


def render_recipe(context: dict) -> str:
    """确定性实验参数基线（第五部分回退路径使用）; 未知制备条件留空, 不引入固定数值配方。"""
    g, eff, s = context["geometry"], context["effective"], context["scheme"]
    cond = context["condition"]
    raw_cond = g.get("condition") or {}
    lo, hi = cond.get("voltage_min", raw_cond.get("v_min")), cond.get("voltage_max", raw_cond.get("v_max"))
    rate = context["result"].get("c_rate", raw_cond.get("c_rate"))
    lines = ["#### 设计规格基线", f"- 正极：{display(s.get('cathode'))}；负极：{display(s.get('anode'))}；电解液：{display(s.get('electrolyte'))}。",
             "- 材料纯度、残碱、水分、粒径分布及配方质量比：当前结构化契约未提供，待证据提取或实验确定。",
             "- R_s 为模型颗粒半径，不等同于 D50；禁止把半径直接当作采购直径。", "", "#### 有效几何验收点"]
    for comp, side, title in (("cathode", "pe", "正极"), ("anode", "ne", "负极")):
        actual = eff.get(side) or {}
        declared = g.get(comp) or {}
        thickness = actual["L"] * 1e6 if actual.get("L") is not None else declared.get("L_um")
        radius = actual["R_s"] * 1e6 if actual.get("R_s") is not None else declared.get("R_s_um")
        porosity = actual.get("porosity", declared.get("porosity"))
        lines.append(f"- {title}：厚度 {display(thickness)} μm，孔隙率 {display(porosity)}，模型半径 R_s {display(radius)} μm；以实际生效值作为本轮制备验证目标。")
    lines += ["- 浆料配比、固含量、面载量、干燥温度及辊压制度：待验证，不从通用模板补数值。", "",
              "#### 已声明条件边界", f"- 已声明电压窗口：{display(lo)}–{display(hi)} V。实验不得擅自扩展该上限。",
              "- 注液量、静置时间、化成电流与预锂化剂量：当前契约未提供，保持待验证。",
              f"- 仿真对照条件：{display(rate)} C、{display(cond.get('temperature_C'))} °C。",
              "- 首效、循环寿命、倍率与界面稳定性属于待测项目；不得把单次放电仿真当作这些指标已通过。",
              "- 如需提高电芯级比能量，应补充逐项质量模型后评估，不从固定系数折算结果推断物理上限。"]
    return "\n".join(lines)


def render_evidence(doc: dict) -> str:
    rows = ["### 文献与数据来源", "| 段落 ID | 来源 |", "|---|---|"]
    for e in doc.get("evidence") or []:
        rows.append(f"| {display(e.get('passage_id'))} | {display(e.get('source'))} |")
    rows.append("证据条数表示已归档段落数量，不等于每项设计参数已有实验验证。")
    return "\n".join(rows)
