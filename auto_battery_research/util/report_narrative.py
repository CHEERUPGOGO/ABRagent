"""报告专用文字视图：数据仍来自方案表，不把内部表格或字段路径交给读者。"""
from __future__ import annotations

import re

RENDER_VERSION = 7
GROUPS = {"cathode": "正极", "anode": "负极", "electrolyte": "电解液",
          "separator": "隔膜", "current_collector": "集流体", "packaging": "封装",
          "process": "制备与装配", "condition": "测试条件", "requirements": "研发目标"}
NAMES = {
    "material": "材料", "L_um": "涂层厚度", "porosity": "孔隙率", "R_s_um": "颗粒半径",
    "D_s_m2s": "固相扩散系数", "loading_mg_cm2": "涂层面载量", "compaction_g_cm3": "压实密度",
    "capacity_mAh_g": "比容量", "active_fraction": "活性物质量占比", "conductive_fraction": "导电剂质量占比",
    "binder_fraction": "粘结剂质量占比", "n_p_ratio": "负正极容量比（N/P）",
    "additives": "添加剂", "salt": "锂盐", "solvent": "溶剂", "concentration_M": "锂盐浓度",
    "conc_M": "锂盐浓度", "solvent_ratio": "溶剂配比", "composition": "组成",
    "v_min": "放电截止电压", "v_max": "充电截止电压", "c_rate": "测试倍率",
    "temperature_K": "测试温度", "temperature_C": "测试温度", "drying_temperature_K": "干燥温度",
    "drying_time_h": "干燥时间", "formation": "化成制度", "synthesis": "合成路线",
    "coating": "涂层", "target_energy_wh_kg": "电芯比能量目标",
    "li_filling_fraction": "载锂孔隙填充率", "porosity_basis": "孔隙率定义",
    "li_density_g_cm3": "锂密度", "li_capacity_mAh_g": "锂比容量",
    "density_g_cm3": "材料密度", "mass_mg_cm2": "面质量", "composite_mass_mg_cm2": "复合层面质量",
    "cathode_mass_mg_cm2": "正极集流体面质量", "anode_mass_mg_cm2": "负极集流体面质量",
    "volume_uL_cm2": "单位面积注液量", "average_discharge_voltage_V": "平均放电电压",
    "pore_volume_uL_cm2": "孔隙体积", "remaining_pore_volume_uL_cm2": "载锂后剩余孔隙体积",
    "available_pore_volume_uL_cm2": "可供电解液占据的孔隙体积", "areal_capacity_mAh_cm2": "面容量",
    "li_capacity_mAh_cm2": "锂库存容量", "stack_mass_mg_cm2": "叠层面质量",
    "cell_mass_mg_cm2": "完整电芯面质量", "stack_energy_wh_kg": "叠层比能量预算",
    "cell_energy_wh_kg": "完整电芯比能量预算",
}
ALIASES = {"li_metal": "锂金属", "lhce": "局部高浓度电解液（LHCE）", "si_base": "硅基负极",
           "carbonate_ec": "EC 基碳酸酯电解液", "LRMO": "富锂锰基层状氧化物（LRMO）",
           "high_concentration": "高浓度电解液", "before_loading": "载锂前", "after_loading": "载锂后"}
UNITS = {"um": "μm", "m2/s": "m²/s", "mg/cm2": "mg/cm²", "g/cm3": "g/cm³",
         "mAh/g": "mAh/g", "mAh/cm2": "mAh/cm²", "uL/cm2": "μL/cm²", "C": "C", "K": "K", "V": "V"}


def label(key, row=None):
    leaf = key.split(".")[-1]
    if key == "separator.L_um":
        return "隔膜厚度"
    if leaf in NAMES:
        return NAMES[leaf]
    meaning = (row or {}).get("meaning", "")
    if isinstance(meaning, str) and re.search(r"[\u4e00-\u9fff]", meaning) and key not in meaning:
        return meaning.strip().rstrip("。")
    # 未注册的新字段不能在报告中暴露程序路径；保留其值及明确的待完善语义。
    return "补充参数（含义需进一步明确）"


def text(value):
    if isinstance(value, dict):
        return "、".join(f"{label(str(k))}为{text(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return "、".join(text(v) for v in value)
    if value is None:
        return "未确定"
    if isinstance(value, float):
        return f"{value:.6g}"
    return ALIASES.get(str(value), str(value)).replace("\n", " ")


def amount(key, row):
    value, unit = row.get("value"), row.get("unit", "")
    if isinstance(value, (float, int)) and unit == "K":
        return f"{value - 273.15:.6g} ℃"
    if isinstance(value, (float, int)) and ("fraction" in key or key.split(".")[-1] == "porosity"):
        return f"{value * 100:.6g}%"
    if unit in ("1", "N/A", "formula", "cells"):
        unit = "只" if unit == "cells" else ""
    return text(value) + (" " + UNITS.get(unit, unit) if unit else "")


def readable_units(body):
    """仅改变明确标注的温标，不猜测无单位数字的物理含义。"""
    return re.sub(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*K\b",
                  lambda m: f"{float(m[1]) - 273.15:.6g} ℃", body)


def describe(key, row):
    name = label(key, row)
    if row.get("value") is None:
        return f"{name}尚未确定"
    status = "（文献参考值，须核对材料及工况）" if row.get("basis") in ("literature", "literature_measurement") else ""
    conditions = row.get("conditions") or {}
    condition_text = "，适用条件为" + text(conditions) if conditions else ""
    return f"{name}为{amount(key, row)}{status}{condition_text}"


def component_paragraph(data, group, material_only=False):
    params = data.get("parameters", {})
    selected = [(k, v) for k, v in params.items() if k.startswith(group + ".") and not k.endswith(".original_request")]
    if material_only:
        selected = [(k, v) for k, v in selected if k.endswith(".material")]
    if not selected:
        if group == "requirements":
            return ""
        return f"{GROUPS[group]}的具体规格尚未确定。"
    material = next((v for k, v in selected if k.endswith(".material")), None)
    if material and material.get("value") is not None:
        introduction = f"{GROUPS[group]}采用{text(material['value'])}。"
        remaining = [(k, v) for k, v in selected if not k.endswith(".material")]
        return introduction + ("具体设计中，" + "；".join(describe(k, row) for k, row in remaining) + "。" if remaining else "")
    return GROUPS[group] + "方面，" + "；".join(describe(k, row) for k, row in selected) + "。"


def render_design(data):
    """以匹配关系组织方案表，模型参数由第四部分统一回显。"""
    params = data.get("parameters", {})
    def rows(group):
        return [(k, v) for k, v in params.items() if k.startswith(group + ".")
                and not k.endswith(".original_request") and v.get("value") is not None]
    def clauses(items, emphasize=False):
        return "；".join(describe(k, v).replace(amount(k, v), f"**{amount(k, v)}**", 1)
                        if emphasize else describe(k, v) for k, v in items)
    parts = ["### 3.1 总体方案", "> **当前组合。** " + "".join(
        component_paragraph(data, g, material_only=True) for g in ("cathode", "anode", "electrolyte"))]
    if rows("requirements"):
        parts.append("**研发目标。** " + clauses(rows("requirements")) + "。目标属于设计要求，不代表已实现。")
    parts += ["选材依据见第 2 节；本节仅说明当前已声明的设计与匹配边界，首轮实验建议见第 5 节。",
              "### 3.2 正负极配置与容量匹配"]
    for group in ("cathode", "anode"):
        specs = [(k, v) for k, v in rows(group) if k.split(".")[-1] not in ("material", "R_s_um", "D_s_m2s", "n_p_ratio")]
        parts.append(f"**{GROUPS[group]}配置。** " + (clauses(specs, True) + "。" if specs else "具体极片规格尚未确定。"))
    ratios = [(k, v) for k, v in list(params.items()) + list(data.get("derived", {}).items())
              if k.split(".")[-1] == "n_p_ratio" and v.get("value") is not None]
    if ratios:
        parts.append("**容量匹配。** " + clauses(ratios) + "。该值为设计声明或预算，仍需按同一工况的可逆面容量及锂库存实测核验。")
    else:
        parts.append("**容量匹配尚未闭合。** 当前没有可采用的 N/P 值，不能仅凭正负极厚度判断匹配成功；应按同一工况的可逆面容量及锂库存核验，获取安排见第 5 节。")
    conditions = rows("condition")
    if conditions:
        parts.append("**已声明工况。** " + clauses(conditions) + "。未声明的工况不能由实验建议或模型默认值自动补为定案。")
    parts += ["颗粒半径、固相扩散系数及模型实际输入统一见第 4 节。",
              "### 3.3 电解液与电芯配套"]
    electrolyte = [(k, v) for k, v in rows("electrolyte") if not k.endswith(".material")]
    # 相同适用条件合并展示；不把条件性基线配方拼成已验证的主方案。
    scoped = {}
    for k, v in electrolyte:
        condition = text(v.get("conditions")) if v.get("conditions") else ""
        scoped.setdefault(condition, []).append((k, dict(v, conditions={})))
    for condition, items in scoped.items():
        parts.append("**配方声明。** " + clauses(items) + "。" + ("适用范围：" + condition + "。" if condition else ""))
    if not electrolyte:
        parts.append("电解液的锂盐、溶剂及添加剂配方尚未完整声明，不能据体系名称推定具体组成。")
    if any(any(w in scope for w in ("冲突", "基线", "候选")) for scope in scoped):
        parts.append("**配方边界。** 上述声明含基线或候选适用条件，不能自动拼接为已确定的统一配方；须先确认其与当前电解液类别的一致性。")
    missing_components = []
    for group in ("separator", "current_collector", "packaging"):
        if rows(group):
            parts.append(f"**{GROUPS[group]}。** " + clauses(rows(group)) + "。")
        else:
            missing_components.append(GROUPS[group])
    if missing_components:
        parts.append("**配套待定。** " + "、".join(missing_components) + "的具体规格尚未确定；第 5 节如给出推荐，仅作为首轮实验起点。")
    parts.append("### 3.4 设计预算与待决事项")
    computed = [(k, v) for k, v in data.get("derived", {}).items() if v.get("value") is not None and k.split(".")[-1] != "n_p_ratio"]
    for key, row in computed:
        parts.append("- **设计预算。** " + GROUPS.get(key.split(".")[0], "") + describe(key, row) + "。")
    if not computed:
        parts.append("当前没有可直接引用的完整设计预算，不据材料组合推定容量匹配或比能量达标。")
    if any("pore_volume" in k for k, _ in computed):
        parts.append("孔隙体积仅为注液与浸润核算线索，不能单独证明容量匹配或高比能目标可达。")
    gaps = {}
    for k, v in params.items():
        group, _, leaf = k.partition(".")
        if v.get("value") is None and group in ("cathode", "anode", "electrolyte", "condition") and leaf not in ("R_s_um", "D_s_m2s", "original_request"):
            gaps.setdefault(GROUPS[group], []).append(label(k, v))
    for group, names in gaps.items():
        parts.append(f"- **{group}待补数据。** " + "、".join(names) + "；获取与验证安排见第 5 节。")
    parts.append("以上为设计声明或预算，尚需实验核验及完整电芯质量闭合；完整审核意见见第 2.6 节。")
    return "\n\n".join(p for p in parts if p)


def render_model_parameters(data):
    parts = ["### 模型参数声明", "以下为方案表中的模型参数，声明值不等于本批材料的实测物性，也不表示本次已完成仿真。"]
    for group in ("cathode", "anode"):
        rows = [(k, v) for k, v in data.get("parameters", {}).items()
                if k.startswith(group + ".") and k.split(".")[-1] in ("R_s_um", "D_s_m2s")]
        if rows:
            parts.append(f"**{GROUPS[group]}模型参数。** " + "；".join(describe(k, v) for k, v in rows) + "。")
    return "\n\n".join(parts)


def render_materials(data):
    reasons = {"cathode": "正极选择需结合容量、电压窗口与结构稳定性核验。",
               "anode": "负极应结合可逆容量、锂库存及界面稳定性开展全电池验证。",
               "electrolyte": "电解液需要同时匹配两侧电极及工作电压，添加剂收益仍需对照实验确认。",
               "separator": "隔膜应核对润湿、机械完整性及质量贡献。",
               "current_collector": "集流体应核对导电、附着、厚度与质量贡献。"}
    return "\n\n".join(component_paragraph(data, g, material_only=True) + reason for g, reason in reasons.items()) + \
        "\n\n这些选型是当前研究对象；归档文献用于提供验证线索，不能直接证明本方案性能。"


def acquisition(key):
    """缺省参数的确定路径，不输出未经证据支持的数值。"""
    leaf = key.split(".")[-1]
    if leaf == "D_s_m2s":
        return ("动力学参数决定模型适用性。先核对材料体系及传输模型；对适用的插层电极，可在目标温度和荷电状态下"
                "开展间歇滴定并结合阻抗分析。" +
                ("若负极为锂金属，其沉积与溶解不能直接沿用插层材料的扩散系数，须先确认模型是否适用。" if key.startswith("anode.") else ""))
    if "capacity" in leaf:
        return "该量影响容量匹配与比能量预算。应在方案规定的倍率、温度及电压窗口下测试可逆容量，并注明质量归一口径。"
    if leaf == "n_p_ratio" or "filling" in leaf:
        return "该量影响锂库存及剩余孔隙。应结合实际载锂质量、载锂前后孔隙体积与正极实测面容量计算，明确初始荷电状态。"
    if "mass" in leaf or "loading" in leaf or "density" in leaf or "compaction" in leaf:
        return "该量影响质量预算。应对相同面积的干燥组件逐件称量，结合厚度测量核算，并明确是否包含集流体、涂层或封装。"
    if "fraction" in leaf or "ratio" in leaf or "concentration" in leaf or leaf == "conc_M":
        return "该量决定配方口径。先明确比例分母及材料批次，再进行单变量梯度筛选，以可加工性和相同工况下的电化学表现确定采用值。"
    if "temperature" in leaf or "drying" in leaf:
        return "该条件影响加工与界面状态。应结合材料和溶剂的适用条件进行小批试验，以残留溶剂、水分和电极完整性确定工艺窗口。"
    if "volume" in leaf or "porosity" in leaf:
        return "该量影响润湿与传输。应区分载锂前后孔隙，结合厚度、组分密度及实际吸液量核算，再以浸润和电化学对照确认。"
    if "voltage" in leaf:
        return "该量影响放电能量预算。应在规定测试条件下采集完整放电曲线，按实际容量积分计算平均电压，不用截止电压直接代替。"
    return "应先查证相同材料体系及工况下的原始资料；仍有缺口时，以小批对照实验或适用的材料表征确定，记录方法与采用依据。"


def missing_suggestions(data):
    from .scheme_ledger import canonical_unit
    params = data.get("parameters", {})
    missing = {k for k, v in params.items() if v.get("value") is None}
    for row in data.get("derived", {}).values():
        missing.update(row.get("missing", []))
    return {k: {"value": None, "unit": canonical_unit(k) or "", "basis": "engineering_proposal", "source": "确定性获取建议",
                "kind": "acquisition", "method": acquisition(k), "meaning": label(k, params.get(k))}
            for k in sorted(missing) if (params.get(k) or {}).get("value") is None and k not in data.get("derived", {})}


def render_suggestions(suggestions):
    if not suggestions:
        return "\n\n### 待确定参数及建议获取方式\n当前未归档额外的参数建议；不据此认定实验验证已完成。"
    paragraphs = ["### 待确定参数及建议获取方式", "以下为待验证建议，尚未采用；确定采用值后需更新方案并重新评估相关计算。"]
    methods = {}
    for key, row in suggestions.items():
        group = GROUPS.get(key.split(".")[0], "方案")
        if row.get("value") is None and not any(row.get(k) for k in ("recommended_range", "rationale", "conditions", "acceptance")):
            method = row.get("method") or acquisition(key)
            methods.setdefault((group, method), []).append(label(key, row))
            continue
        intro = f"{group}的{label(key, row)}"
        if row.get("value") is not None:
            intro += f"可将{amount(key, row)}作为候选起点，不能作为已确定规格或实测结果。"
        elif row.get("recommended_range"):
            intro += f"可在{text(row['recommended_range'])}的候选范围内筛选，范围仍需验证。"
        else:
            intro += "目前没有可直接采用的可靠数值。"
        rationale = row.get("rationale") or ""
        method = row.get("method") or acquisition(key)
        conditions = "适用条件为" + text(row["conditions"]) + "。" if row.get("conditions") else ""
        criterion = "采用判据为" + text(row["acceptance"]) + "。" if row.get("acceptance") else ""
        paragraphs.append(intro + text(rationale) + method + conditions + criterion)
    for (group, method), names in methods.items():
        paragraphs.append(f"{group}的{'、'.join(names)}目前没有可直接采用的可靠数值。{method}")
    return "\n\n".join(paragraphs)


def render_experiment(data, result):
    params = data.get("parameters", {})
    subjects = "、".join(text((params.get(g + ".material") or {}).get("value")) for g in ("cathode", "anode", "electrolyte"))
    return "\n\n".join([
        "### 5.1 验证对象", f"实验围绕{subjects}组成的候选电池体系开展，采用前述最终方案中的规格。" + component_paragraph(data, "condition"),
        "### 5.2 制备与加工", component_paragraph(data, "process") + "未声明的合成、干燥及辊压条件需先以小批试验确定，不沿用初始提案的旧值。",
        "### 5.3 装配与化成", component_paragraph(data, "separator") + component_paragraph(data, "current_collector") +
        component_paragraph(data, "electrolyte") + "注液与化成条件未确定时，应先核算剩余孔隙并做浸润、首效及界面阻抗对照。",
        "### 5.4 性能与模型对照", "本次存在匹配的放电仿真，仅可用于相同条件下的放电对照。" if result.get("status") == "CONVERGED" else
        "本次未完成有效放电仿真，不存在可用于性能判断的仿真值。", "循环寿命、首效及安全性仍需独立实测。",
        "### 5.5 判定与迭代", "电芯比能量以指定工况的实测放电能量除以完整电芯质量判定。修改材料、配方或几何后，应重新计算容量匹配及质量预算。",
        "### 5.6 数据缺口", "尚未确定的参数及其获取路径见后文。测量时须保持材料、温度、倍率和归一口径一致，不以模型默认值代替实测。"])


def render_geometry_comparison(summary, selected, initial):
    paragraphs = []
    for comp, side in (("cathode", "pe"), ("anode", "ne")):
        actual = ((summary or {}).get("design") or {}).get(side) or {}
        clauses = []
        for field, effective_key, scale, unit in (("L_um", "L", 1e6, "um"), ("porosity", "porosity", 1, ""),
                ("R_s_um", "R_s", 1e6, "um"), ("D_s_m2s", "D_s", 1, "m2/s")):
            before, current = (initial.get(comp) or {}).get(field), (selected.get(comp) or {}).get(field)
            if before is not None and before != current:
                clauses.append(f"{label(field)}由初始的{amount(field, {'value': before, 'unit': unit})}调整为"
                               f"{amount(field, {'value': current, 'unit': unit})}")
            value = actual.get(effective_key)
            if isinstance(value, (int, float)):
                clauses.append(f"模型实际采用的{label(field)}为{amount(field, {'value': value*scale, 'unit': unit})}")
        if clauses:
            paragraphs.append(f"**{GROUPS[comp]}参数对照。** " + "；".join(clauses) + "。")
    return "\n\n" + "\n\n".join(paragraphs) if paragraphs else ""
