"""最终报告的确定性数据视图：只消费选定设计和与之匹配的仿真产物。"""

import hashlib
import json
import math
import re
from typing import Any
from .report_narrative import RENDER_VERSION


ENERGY_SCOPE = (
    "电芯比能量采用材料级比能量乘固定体系系数折算，未逐项计算集流体、隔膜、"
    "电解液和封装质量；当前 PINN 使用训练体系固定电解液，未验证方案电解液/添加剂。"
    "本次最佳仅指已评估候选，不代表物理上限，不能据此证明非活性质量优化收益。"
)


def report_identity(doc: dict, result: dict | None, spec: dict | None) -> str:
    """报告输入指纹包含完整结构化方案与实际结果，变更后必须重新编译。"""
    payload = {"render_version": RENDER_VERSION, "experiment_plan_hash": doc.get("experiment_plan_hash"), "scheme": doc.get("scheme") or {}, "ledger_hash": doc.get("ledger_hash"), "reviewer_output": doc.get("reviewer_output") or {},
               "result": result or {}, "spec": spec or {},
               "materials_input": {"analysis": doc.get("final_answer"), "evidence": doc.get("evidence")},
               "requirements": doc.get("requirements"), "research": doc.get("research"),
               "research_narrative": doc.get("research_narrative"),
               "retrieval_state": doc.get("retrieval_state"),
               "numeric_contract": doc.get("numeric_contract"),
               "rule_material_matches": (doc.get("rule_checks") or {}).get("material_matches"),
               "experiment_extensions": doc.get("experiment_extensions"),
               "physics_input_diagnostic": doc.get("physics_input_diagnostic"),
               "pinn_attempt_summary": doc.get("pinn_attempt_summary"),
               "report_readiness": doc.get("report_readiness")}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    return f"<!-- abr-report-identity:{digest} -->"


def validate_report_blocks(text: str, context: dict, target: float | None, tolerance: float) -> list[str]:
    """关键数值正文由相同视图重算验收，禁止旧值混入权威块。

    final-design / energy 必须始终存在; final-recipe 仅在回退渲染路径出现,
    存在时按同一视图重算, 缺失时交给实验方案生成记录单独校验。
    """
    errors = []
    numeric_doc = context.get("research_doc") or {}
    if numeric_doc.get("numeric_contract"):
        from .numeric_consistency import document_audit, render_audit as render_numeric
        if render_numeric(document_audit(numeric_doc)) not in text:
            errors.append("数值一致性核对附录与当前参数/正文不一致")
    if context.get("requirements"):
        from .research_reporting import render_summary, render_review_appendix
        from .research_contract import document_errors
        doc = context["research_doc"]
        research_errors = document_errors(doc)
        if doc.get("report_readiness", {}).get("category") == "DIAGNOSTIC":
            research_errors = [e for e in research_errors if "用户固定要求与主方案不一致" not in e]
        errors.extend(research_errors)
        from .parameter_presentation import presentation_conflicts
        conflicts = presentation_conflicts(doc.get("ledger_view") or {})
        if doc.get("report_readiness", {}).get("category") != "DIAGNOSTIC":
            errors.extend(conflicts)
        for expected in (render_summary(doc), render_review_appendix(doc)):
            if expected not in text:
                errors.append("课题回应摘要或完整审核附录与归档不一致")
    required = {"final-design": render_selected_design(context), "energy": render_energy(context, target, tolerance)}
    from auto_battery_research.simulation.geometry_contract import render_input_diagnostic
    diagnostic = render_input_diagnostic(context.get("research_doc") or {})
    if diagnostic and not context.get("result") and diagnostic not in text:
        errors.append("报告缺少当前材料匹配与物理输入阻断说明")
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


_CANONICAL_ADDITIVE_MAP = {
    "lidfob": "LiDFOB",
    "libob": "LiBOB",
    "fec": "FEC",
    "vc": "VC",
    "dtd": "DTD",
    "ps": "PS",
    "pes": "PES",
    "tmsb": "TMSB",
    "tmsp": "TMSP",
    "lifsi": "LiFSI",
    "lipf6": "LiPF6",
    "litfsi": "LiTFSI",
}


def _canonical_additive_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    return _CANONICAL_ADDITIVE_MAP.get(text.lower(), text)


def _normalize_lidfob_text(text: str) -> str:
    import re
    return re.sub(r"\bLIDFOB\b", "LiDFOB", str(text or ""))


def _is_additives_locked(scheme: dict, anchors: dict | None = None, target: str = "") -> bool:
    """判定添加剂是否真被用户/契约锁定到基础配方，缺失 additive_status 时保守降级为候选筛选。"""
    anchors = anchors if isinstance(anchors, dict) else {}
    status = str(anchors.get("additive_status") or "").strip().lower()
    if status == "locked":
        return True
    if status:
        return False
    locked_set = {str(x).strip().lower() for x in (scheme.get("user_locked_components") or []) if str(x).strip()}
    additives = [_canonical_additive_name(a) for a in (scheme.get("additives") or []) if str(a).strip()]
    explicit_locked = bool(locked_set & {"additives", "additive"} or any(a.lower() in locked_set for a in additives))
    if not explicit_locked:
        return False
    goal = str(target or "").strip()
    if goal and scheme.get("electrolyte"):
        import re
        goal_lower = goal.lower()
        mentioned = ("添加剂" in goal) or any(
            re.search(rf"(?<![A-Za-z0-9]){re.escape(a.lower())}(?![A-Za-z0-9])", goal_lower)
            for a in additives
        )
        if not mentioned:
            return False
    return True


def _extract_candidate_ranking_table(final_answer: str) -> str:
    """从 Stage 4 final_answer 中提取正式候选排序 Markdown 表格并规范化 LiDFOB 拼写。"""
    import re
    text = str(final_answer or "")
    if not text.strip():
        return ""
    lines = text.splitlines()
    n = len(lines)
    i = 0
    best_block: list[str] = []
    sep_cell_re = re.compile(r"^:?-{2,}:?$")
    rank_keys = ("排序", "rank", "排名", "推荐序", "掺杂组合", "候选组合", "a/b 元素", "a/b位")
    detail_keys = ("化学式", "formula", "元素", "评分", "rating", "score", "保持率", "综合", "容量")
    while i < n:
        if lines[i].lstrip().startswith("|"):
            j = i
            block = []
            while j < n and lines[j].lstrip().startswith("|"):
                block.append(lines[j].strip())
                j += 1
            if len(block) >= 3:
                sep_cells = [c.strip() for c in block[1].strip("|").split("|")]
                if sep_cells and all(sep_cell_re.match(c) for c in sep_cells):
                    header_lower = block[0].lower()
                    if any(k in header_lower for k in rank_keys) and any(k in header_lower for k in detail_keys):
                        best_block = [_normalize_lidfob_text(row) for row in block]
                        break
            i = j
        else:
            i += 1
    return "\n".join(best_block)


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
            "requirements": doc.get("requirements"), "research_doc": doc,
            "ledger_view": doc.get("ledger_view"),
            "effective": (result.get("pinn") or {}).get("design") or {},
            "condition": ((spec or {}).get("cell_spec") or {}).get("condition") or {},
            "result": result, "energy_accounting": accounting,
            "reviewer_output": doc.get("reviewer_output") or {},
            "target": doc.get("target") or "",
            "review_status": doc.get("review_status") or "",
            "final_answer": doc.get("final_answer") or ""}


PRECURSOR_LABELS = {
    "solid_state": "固相法",
    "carbonate_coprecipitation": "碳酸盐共沉淀",
    "hydroxide_coprecipitation": "氢氧化物共沉淀",
    "sol_gel": "溶胶-凝胶法",
}
_PRECURSOR_LABELS = PRECURSOR_LABELS

ROUTE_SPECS = {
    "solid_state": (
        "固相法",
        r"(?:高温)?固相(?:法|合成|烧结|路线|反应)",
    ),
    "sol_gel": (
        "溶胶-凝胶法",
        r"(?:溶胶[-–—\s]*凝胶|sol[-–—\s]*gel|pechini)(?:法|路线)?",
    ),
    "carbonate_coprecipitation": (
        "碳酸盐共沉淀",
        r"(?:碳酸盐(?:共沉淀|路线|前驱体)?|\([^)]+\)CO3)",
    ),
    "hydroxide_coprecipitation": (
        "氢氧化物共沉淀",
        r"(?:(?:\(OH\)\s*2)|氢氧化物(?:共沉淀|路线|前驱体)?)",
    ),
}


def format_scalar_or_range(value: Any) -> str:
    """将数值或区间字符串统一规范为紧凑可读文本。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):g}"
    return re.sub(r"\s*[~–—-]\s*", "–", str(value).strip())


def parse_numeric_range(value: Any) -> tuple[float, float] | None:
    """从数值或区间字符串解析 (最小值, 最大值)。"""
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return (v, v)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(value))]
    if not nums:
        return None
    return (min(nums), max(nums))


def detect_synthesis_routes(text: str) -> set[str]:
    """识别文本中提及的合成路线 key。"""
    src = str(text or "")
    return {
        route_key
        for route_key, (_, pat) in ROUTE_SPECS.items()
        if re.search(pat, src, re.I)
    }


def compute_cathode_pore_metrics(scheme: dict | None) -> dict[str, float] | None:
    """按正极极片几何、面积与注液量计算理论单极片孔体积与 E/P 液孔比。"""
    if not isinstance(scheme, dict):
        return None
    geometry = scheme.get("geometry") or {}
    cathode_geo = geometry.get("cathode") or {}
    anchors = scheme.get("design_anchors") or {}
    if not isinstance(cathode_geo, dict) or not isinstance(anchors, dict):
        return None
    l_um = cathode_geo.get("L_um")
    porosity = cathode_geo.get("porosity")
    if (
        not isinstance(l_um, (int, float)) or isinstance(l_um, bool) or float(l_um) <= 0
        or not isinstance(porosity, (int, float)) or isinstance(porosity, bool) or float(porosity) <= 0
    ):
        return None
    area_val = anchors.get("cathode_area_cm2")
    area_cm2: float | None = None
    if isinstance(area_val, (int, float)) and not isinstance(area_val, bool) and float(area_val) > 0:
        area_cm2 = float(area_val)
    else:
        diam_val = anchors.get("cathode_diameter_mm")
        if isinstance(diam_val, (int, float)) and not isinstance(diam_val, bool) and float(diam_val) > 0:
            area_cm2 = math.pi * (float(diam_val) / 20.0) ** 2
    vol_range = parse_numeric_range(anchors.get("electrolyte_volume_uL"))
    if area_cm2 is None or vol_range is None:
        return None
    v_pore_uL = area_cm2 * (float(l_um) * 1e-4) * float(porosity) * 1000.0
    if v_pore_uL <= 0:
        return None
    v_e_min, v_e_max = vol_range
    return {
        "area_cm2": area_cm2,
        "L_um": float(l_um),
        "porosity": float(porosity),
        "v_pore_uL": v_pore_uL,
        "v_e_min_uL": v_e_min,
        "v_e_max_uL": v_e_max,
        "ratio_min": v_e_min / v_pore_uL,
        "ratio_max": v_e_max / v_pore_uL,
    }


def _render_design_anchors_lines(anchors: dict, scheme: dict | None = None, has_caveats: bool = False) -> list[str]:
    """将配方与工艺规格锚点渲染为可读条目。"""
    if not isinstance(anchors, dict) or not anchors:
        return []
    items = []
    if anchors.get("primary_formula"):
        formula_label = "主推候选名义化学式（Rank #1，待实验验证）" if has_caveats else "主推荐名义化学式"
        items.append(f"- {formula_label}：{display(_normalize_lidfob_text(anchors['primary_formula']))}")
    if anchors.get("doping_upper_limit_mol_pct") is not None:
        items.append(f"- 总掺杂量上限（总阳离子基准）：≤ {display(anchors['doping_upper_limit_mol_pct'])} mol%")
    if anchors.get("a_site_upper_limit_mol_pct") is not None:
        items.append(f"- A 位掺杂量上限（总阳离子基准）：≤ {display(anchors['a_site_upper_limit_mol_pct'])} mol%")
    if anchors.get("b_site_upper_limit_mol_pct") is not None:
        items.append(f"- B 位掺杂量上限（总阳离子基准）：≤ {display(anchors['b_site_upper_limit_mol_pct'])} mol%")
    if anchors.get("precursor_type"):
        ptype = str(anchors["precursor_type"])
        items.append(f"- 合成路线：{display(PRECURSOR_LABELS.get(ptype, ptype))}")
    temp_val = anchors.get("calcination_temp_range_c") or anchors.get("calcination_temp_c")
    hours_val = anchors.get("calcination_hours_range") or anchors.get("calcination_hours")
    temp_str = format_scalar_or_range(temp_val) if temp_val is not None and temp_val != "" else ""
    hours_str = format_scalar_or_range(hours_val) if hours_val is not None and hours_val != "" else ""
    if temp_str and hours_str:
        items.append(f"- 主烧/烧结条件：{display(temp_str)} °C，保温 {display(hours_str)} h")
    elif temp_str:
        items.append(f"- 主烧/烧结温度：{display(temp_str)} °C")
    elif hours_str:
        items.append(f"- 主烧/烧结保温时间：{display(hours_str)} h")
    al_val = anchors.get("al_foil_um")
    sep_val = anchors.get("separator")
    has_al = al_val is not None and al_val != ""
    has_sep = bool(sep_val)
    if has_al:
        al_str = format_scalar_or_range(al_val)
        items.append(f"- 正极集流体铝箔厚度：{al_str} μm")
    if has_sep:
        items.append(f"- 隔膜型号：{display(sep_val)}")
    if not has_al and not has_sep:
        items.append("- 隔膜与集流体规格：未设设计锚点（由第 5 节实验室入库台账固定同批次规格，非设计锚点）")
    elif not has_sep:
        items.append("- 隔膜规格：未设设计锚点（由第 5 节实验室入库台账固定同批次规格，非设计锚点）")
    elif not has_al:
        items.append("- 正极集流体铝箔规格：未设设计锚点（由第 5 节实验室入库台账固定同批次规格，非设计锚点）")
    diam_mm = anchors.get("cathode_diameter_mm")
    area_cm2 = anchors.get("cathode_area_cm2")
    if diam_mm is not None and area_cm2 is not None:
        items.append(f"- 正极极片尺寸：φ{display(diam_mm)} mm（面积 {display(area_cm2)} cm²）")
    elif diam_mm is not None:
        items.append(f"- 正极极片直径：φ{display(diam_mm)} mm")
    elif area_cm2 is not None:
        items.append(f"- 正极极片面积：{display(area_cm2)} cm²")
    vol_raw = anchors.get("electrolyte_volume_uL")
    if vol_raw is not None and vol_raw != "":
        vol_str = format_scalar_or_range(vol_raw)
        vol_range = parse_numeric_range(vol_raw)
        ep_metrics = compute_cathode_pore_metrics(scheme) if isinstance(scheme, dict) else None
        vol_note = ""
        if vol_range and vol_range[1] <= 80.0:
            if ep_metrics and ep_metrics.get("ratio_min") is not None:
                r_min = float(ep_metrics["ratio_min"])
                r_max = float(ep_metrics.get("ratio_max", r_min))
                ep_str = f"{r_min:.1f}×" if abs(r_max - r_min) < 0.05 else f"{r_min:.1f}–{r_max:.1f}×"
                if r_min >= 3.0:
                    vol_note = f"（扣电过量/泛液测试基准，E/P ≈ {ep_str}；相较 100–120 μL 常规值属减量富液，非贫液/低液量）"
                elif r_max < 3.0:
                    vol_note = f"（低液量测试基准，E/P ≈ {ep_str}；常规参考 100–120 μL）"
                else:
                    vol_note = f"（扣电跨临界注液基准，E/P ≈ {ep_str}；常规参考 100–120 μL）"
            else:
                vol_note = "（扣电测试基准；常规参考 100–120 μL，实际贫液/富液属性须结合极片孔体积核算 E/P）"
        items.append(f"- 扣电设计注液量：{display(vol_str)} μL{vol_note}")
    if anchors.get("slurry_ratio"):
        items.append(f"- 极片浆料配比（活性:导电炭:粘结剂）：{display(anchors['slurry_ratio'])}")
    return items


def render_selected_design(context: dict) -> str:
    """正文只渲染最终版本；初始提案链接留档，不能混入当前配方。"""
    if context.get("ledger_view"):
        from .report_narrative import render_design
        if (context.get("requirements") or {}).get("scheme_required") is False:
            return "本课题以研究分析为主，未要求完整电芯设计。当前已知材料与条件仅用于限定研究对象；研究回答及验证路径见第2、5节，不补造主配方。"
        body = render_design(context["ledger_view"])
        if context.get("requirements"):
            body = body.replace("第 2.6 节", "第6节附录")
        issues = (context.get("reviewer_output") or {}).get("issues") or []
        if issues:
            section = "6节附录" if context.get("requirements") else "2.6 节"
            body += f"\n\n**适用边界。** 本方案仍有未解决的审核意见，完整内容见第 {section}；相关判断不能作为已验证结论。"
        return body
    s, g = context["scheme"], context["geometry"]
    if not s:
        return "*本课题尚未生成独立的 Stage 4 电池体系设计方案 (design_scheme.json)。缺失参数保持空白。*"
    locked_set = {str(x).strip().lower() for x in (s.get("user_locked_components") or []) if str(x).strip()}
    anchors = s.get("design_anchors") if isinstance(s.get("design_anchors"), dict) else {}
    additives = [_canonical_additive_name(a) for a in (s.get("additives") or []) if str(a).strip()]
    additives_locked = _is_additives_locked(s, anchors=anchors, target=context.get("target", ""))
    if additives:
        additives_cell = ", ".join(additives)
        additives_status = (
            "已锁定引入（剂量水平与化成匹配待第 5 节实验筛选）"
            if additives_locked
            else "候选界面添加剂（不预加入 G0/G1 基础电解液 E0；纳入第 5 节 G7 开展 0 vs 1/2 wt% 单因素及组合筛选）"
        )
    else:
        additives_cell = None
        additives_status = "未提供"

    def _comp_status(comp_key: str, val: Any) -> str:
        if not val:
            return "未提供"
        if comp_key in locked_set:
            return "已锁定体系（工艺与规格仍须实验实证）"
        return "Stage 4 选定体系（具体工艺与适配参数待实验验证）"

    lines = [f"**选定设计版本：{display(s.get('design_revision'))}**；几何哈希：`{display(s.get('geometry_hash'))}`。",
             "", "| 组件 | 最终选型 | 锁定与验证状态 |", "|---|---|---|",
             f"| 正极 | {display(s.get('cathode'))} | {_comp_status('cathode', s.get('cathode'))} |",
             f"| 负极 | {display(s.get('anode'))} | {_comp_status('anode', s.get('anode'))} |",
             f"| 电解液 | {display(s.get('electrolyte'))} | {_comp_status('electrolyte', s.get('electrolyte'))} |",
             f"| 添加剂 | {display(additives_cell)} | {additives_status} |", "",
             "| 参数 | 正极声明值 | 负极声明值 |", "|---|---|---|"]
    for key, name in (("L_um", "厚度 μm"), ("porosity", "孔隙率"), ("R_s_um", "颗粒半径 R_s μm"), ("D_s_m2s", "固相扩散系数 m²/s")):
        lines.append(f"| {name} | {display((g.get('cathode') or {}).get(key))} | {display((g.get('anode') or {}).get(key))} |")
    cond = g.get("condition") or {}
    lines += ["", f"设计测试条件：{display(cond.get('c_rate'))} C，{display(cond.get('v_min'))}–{display(cond.get('v_max'))} V。",
              (f"显式 N/P：{display(g['n_p_ratio'])}；实际厚度以仿真有效输入表为准。" if g.get("n_p_ratio") is not None else
               "N/P 未提供，不进行容量比厚度校准，正负极分别使用声明厚度。"),
              "工程设计值不是实测值；模型基准 D_s 不代表文献测量结果。"]
    if s.get("anode") == "li_metal" and (g.get("anode") or {}).get("R_s_um") is None:
        lines.append(
            "金属锂负极物理口径：金属锂箔真密度为 0.534 g/cm³（20 μm 致密锂箔对应 1.068 mg/cm² / 4.12 mAh/cm²）；"
            "正极本征半电池为过量锂（Li-excess）体系，不设定虚构 N/P 比或多孔插层压实密度/孔隙率。"
        )
    lines.append("")
    for path, source in (g.get("parameter_sources") or {}).items():
        lines.append(f"- {display(path)}：{display(source.get('origin'))} / {display(source.get('basis'))}")
    lines += ["", "原始文献分析与初始提案见 [RAG 档案](rag_result.json)，最终结构化方案见 [设计契约](design_scheme.json)。",
              "初始提案的数值和计算未经本轮重新验证，不作为最终设计或实验参数。"]
    review = context.get("reviewer_output") or {}
    review_status = str(context.get("review_status") or "").strip().upper()
    has_caveats = bool(review.get("issues")) or (bool(review_status) and review_status != "APPROVED")
    ranking_table = _extract_candidate_ranking_table(context.get("final_answer") or "")
    if ranking_table:
        lines += [
            "",
            "**候选掺杂组合正式排行与综合对比（源自方案设计，待第 5 节实验闭环验证）**",
            ranking_table,
        ]
        if has_caveats:
            lines.append(
                "注：上述候选排行表中的多圈循环保持率与综合评分预测为考虑双位点放大/工艺波动与严格测试窗口后的保守工程预估，"
                "不能直接与文献中单元素、小倍率、短圈数或不同极片载量/电压窗口下的单点最优值（部分文献报道可达 90%–97%+）简单画等号；"
                "同时，引入电化学惰性掺杂离子虽可稳定晶格与界面，但也会相应稀释部分过渡金属氧化还原（TM redox）比容量，实际容量与循环权衡须以第 5 节同批次对照实验与审稿保留项闭环为准。"
            )
    if review.get("issues"):
        lines += [
            "",
            "**审稿保留意见（非流程阻塞项；需实验室阶段闭环验证）**",
            "流程门禁 `PASSED` 仅表示证据可追溯、结构化设计提取完整、章节契约完备且未触发规则引擎（RelationEngine）硬性驳回，**不代表以下科学或工艺风险已获实验实证或关闭**；下述保留项须在实验室验证阶段逐项闭环：",
        ]
        lines.extend(f"- {display(_normalize_lidfob_text(issue))}" for issue in review["issues"])
    anchor_lines = _render_design_anchors_lines(s.get("design_anchors"), scheme=s, has_caveats=has_caveats)
    if anchor_lines:
        anchor_heading = "**候选设计配方与工艺规格锚点（待实验闭环验证）**" if has_caveats else "**锁定配方与工艺规格锚点**"
        lines += ["", anchor_heading] + anchor_lines
    return "\n".join(lines)


def render_energy(context: dict, target: float | None, tolerance: float) -> str:
    body = _render_energy(context, target, tolerance)
    summary = (context.get("research_doc") or {}).get("pinn_attempt_summary") or {}
    if summary:
        rounds = summary.get("rounds") or []
        repair = sum(r.get("mode") == "input_repair" for r in rounds)
        optimization = sum(r.get("mode") == "energy_optimization" for r in rounds)
        failed = sum(r.get("status") in ("REJECTED", "INTERRUPTED", "SIMULATION_FAILED") for r in rounds)
        body += (f"\n\n**PINN 修订机会。** 已用 {summary['used']}/{summary['max']} 次，剩余 {summary['remaining']} 次；"
                 f"其中输入修复 {repair} 次、能量优化 {optimization} 次、失败或中断 {failed} 次。首次仿真及修订后的验证仿真不另计次。")
        labels = {"INPUT_BLOCKED": "输入仍受契约或权限阻断", "LLM_UNAVAILABLE": "输入修复服务不可用，保留剩余机会",
                  "BUDGET_EXHAUSTED": "修订预算已耗尽", "SIMULATION_FAILED": "验证仿真失败", "NO_VALID_SIMULATION": "没有有效验证仿真"}
        if summary.get("status") in labels:
            body += "\n\n" + labels[summary["status"]] + "。"
            diagnostic = summary.get("diagnostic") or {}
            reason = diagnostic.get("error") or "; ".join(diagnostic.get("errors") or [])
            if reason:
                body += "当前阻断原因：" + display(reason) + "。"
    return body


def _render_energy(context: dict, target: float | None, tolerance: float) -> str:
    r, a = context["result"], context["energy_accounting"]
    energy = r.get("energy_wh_kg")
    if context.get("requirements") and target is None and not isinstance(energy, (int, float)):
        return "本课题没有已声明的电芯比能量目标或有效能量计算结果；课题目标的回应与验证安排见第1、2、5节。"
    if not isinstance(energy, (int, float)):
        return "**目标判定：未完成有效仿真，不能判定达标。** \n\n" + ENERGY_SCOPE
    threshold = target * (1 - tolerance / 100) if target is not None else None
    verdict = "无数值目标" if threshold is None else ("达到容差阈值" if energy >= threshold else "未达标")
    lines = [f"**目标判定：{verdict}。当前选定候选折算比能量 {energy:.1f} Wh/kg。** "]
    if threshold is not None:
        lines.append(f"目标 {target:g} Wh/kg；容差 {tolerance:g}%；下限 {threshold:.1f} Wh/kg；距下限缺口 {max(0, threshold-energy):.1f} Wh/kg。")
    lines += ["", "**能量计算口径**", "",
              f"材料级 {display(a.get('material_energy_wh_kg'))} Wh/kg × 固定折算系数 {display(a.get('cell_energy_ratio'))} = 电芯折算 {energy:.1f} Wh/kg。",
              ENERGY_SCOPE]
    return "\n".join(lines)


def render_recipe(context: dict) -> str:
    """确定性实验参数基线（第五部分回退路径使用）; 未知制备条件留空, 不引入固定数值配方。"""
    if context.get("ledger_view"):
        from .scheme_ledger import render_experiment
        return render_experiment(context["ledger_view"], context["result"])
    g, eff, s = context["geometry"], context["effective"], context["scheme"]
    cond = context["condition"]
    raw_cond = g.get("condition") or {}
    lo, hi = cond.get("voltage_min", raw_cond.get("v_min")), cond.get("voltage_max", raw_cond.get("v_max"))
    rate = context["result"].get("c_rate", raw_cond.get("c_rate"))
    lines = ["#### 设计规格基线", f"- 正极：{display(s.get('cathode'))}；负极：{display(s.get('anode'))}；电解液：{display(s.get('electrolyte'))}。"]
    anchors = s.get("design_anchors") if isinstance(s.get("design_anchors"), dict) else {}
    review = context.get("reviewer_output") or {}
    review_status = str(context.get("review_status") or "").strip().upper()
    has_caveats = bool(review.get("issues")) or (bool(review_status) and review_status != "APPROVED")
    anchor_lines = _render_design_anchors_lines(anchors, scheme=s, has_caveats=has_caveats)
    if anchor_lines:
        lines.extend(anchor_lines)
    lines += ["- 材料纯度、残碱、水分、粒径分布及配方质量比：当前结构化契约未提供，待证据提取或实验确定。",
              "- R_s 为模型颗粒半径，不等同于 D50；禁止把半径直接当作采购直径。", "", "#### 有效几何验收点"]
    for comp, side, title in (("cathode", "pe", "正极"), ("anode", "ne", "负极")):
        actual = eff.get(side) or {}
        declared = g.get(comp) or {}
        thickness = actual["L"] * 1e6 if actual.get("L") is not None else declared.get("L_um")
        radius = actual["R_s"] * 1e6 if actual.get("R_s") is not None else declared.get("R_s_um")
        porosity = actual.get("porosity", declared.get("porosity"))
        lines.append(f"- {title}：厚度 {display(thickness)} μm，孔隙率 {display(porosity)}，模型半径 R_s {display(radius)} μm；以实际生效值作为本轮制备验证目标。")
    if s.get("anode") == "li_metal":
        lines.append(
            "- 金属锂负极物理口径：金属锂真密度 0.534 g/cm³（20 μm 致密锂箔约 1.068 mg/cm² / 4.12 mAh/cm²）；"
            "正极本征半电池为过量锂（Li-excess）体系，不按多孔插层压实/孔隙率核算 N/P；若转入全电池，N/P 须按真实锂箔厚度与 0.534 g/cm³ 真密度计算。"
        )
    lines += ["- 浆料配比、固含量、面载量、干燥温度及辊压制度：待验证，不从通用模板补数值。", "",
              "#### 已声明条件边界", f"- 已声明电压窗口：{display(lo)}–{display(hi)} V。实验不得擅自扩展该上限。",
              ("- 静置时间、化成电流与预锂化剂量：当前契约未提供，保持待验证（扣电设计注液量按上述锚点执行）。"
               if anchors.get("electrolyte_volume_uL") not in (None, "")
               else "- 注液量、静置时间、化成电流与预锂化剂量：当前契约未提供，保持待验证。"),
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
