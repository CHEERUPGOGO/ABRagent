"""主方案的只读重点视图：组成、匹配、工况与预算分层，来源仍保留于附录。"""
from .parameter_presentation import parameter_view, combined_rows, displayed_row


def render_design(data):
    from .report_narrative import GROUPS, amount, component_paragraph, describe, label
    params = data.get("parameters", {})
    view = parameter_view(data)

    def rows(group):
        return [row for row in view if row["canonical_key"].startswith(group + ".")
                and not row["canonical_key"].endswith(".original_request") and row.get("value") is not None]

    def clauses(items, emphasize=False):
        values = []
        for row in combined_rows(items):
            value = displayed_row(row, params)
            if emphasize:
                if "range_values" in row:
                    # 区间口径及条件由原视图负责；不另算百分比。
                    name = value.split("为", 1)[0]
                    value = value.replace(name, f"**{name}**", 1)
                else:
                    rendered = amount(row["canonical_key"], row)
                    # 已有原文的Markdown标记先转为文本，避免双重加粗损坏结构。
                    rendered = rendered.replace("**", "")
                    value = value.replace(rendered, f"**{rendered}**", 1)
            values.append(value)
        return "；".join(dict.fromkeys(values))

    parts = ["### 3.1 总体方案与关键组成",
             "> **当前组合。** " + "".join(component_paragraph(data, g, material_only=True)
                                            for g in ("cathode", "anode", "electrolyte")),
             "本节集中说明当前方案的材料身份、极片配置和匹配关系。选材依据见第 2 节，制备与测试步骤见第 5 节；下列值属于设计声明或预算，仍须实验核验。"]
    if rows("requirements"):
        parts.append("**研发目标。** " + clauses(rows("requirements"), True) + "。目标不代表已经实现。")
    for group in ("cathode", "anode"):
        identity = [r for r in rows(group) if any(t in r["canonical_key"] for t in ("formula", "doping.", "dopant", "coating", "composition"))]
        if not identity:
            continue
        parts.append(f"**{GROUPS[group]}组成与改性规格。**")
        buckets = {}
        for row in identity:
            key = row["canonical_key"]
            if "formula" in key:
                bucket = "名义组成"
            elif ".doping." in key and len(key.split(".")) >= 4:
                site = key.split(".")[2]
                if site in ("a_site", "b_site"):
                    element = params.get(group + ".doping." + site + ".element", {}).get("value")
                    bucket = str(element) + "改性声明" if element else {"a_site": "A位声明", "b_site": "B位声明"}[site]
                else:
                    bucket = site + "改性声明"
            else:
                bucket = "改性策略与边界"
            buckets.setdefault(bucket, []).append(row)
        for name, items in buckets.items():
            parts.append("- **" + name + "：** " + clauses(items, name == "名义组成") + "。")
        parts.append("以上规格的计量基准和适用条件随声明保留；含混口径先确认，不自动换算。完整逐字段来源见参数附录。")

    parts += ["### 3.2 极片配置与容量匹配",
              "正负极的厚度、孔隙率和面载量共同限定实际实验对象。几何声明用于确定制备验收点，容量匹配仍需相同工况下的实测面容量与锂库存支撑。"]
    spec_fields = {"L_um", "porosity", "loading_mg_cm2", "compaction_g_cm3", "active_fraction", "conductive_fraction", "binder_fraction", "capacity_mAh_g",
                   "active_material_mass_fraction", "conductive_carbon_mass_fraction", "binder_PVDF_mass_fraction"}
    for group in ("cathode", "anode"):
        specs = [r for r in rows(group) if r["canonical_key"].split(".")[-1] in spec_fields]
        geometry = [r for r in specs if "fraction" not in r["canonical_key"]]
        fractions = [r for r in specs if "fraction" in r["canonical_key"]]
        parts.append(f"**{GROUPS[group]}配置。** " + (clauses(geometry, True) + "。" if geometry else "具体极片几何和面载量尚未完整声明。"))
        if fractions:
            parts.append(f"**{GROUPS[group]}极片组成。** " + clauses(fractions) + "。各比例的质量分母与文献适用条件按原声明保留。")
    ratios = [r for r in view if r["canonical_key"].split(".")[-1] == "n_p_ratio" and r.get("value") is not None]
    for key, row in data.get("derived", {}).items():
        if key.split(".")[-1] == "n_p_ratio" and row.get("value") is not None:
            ratios.append(dict(row, canonical_key=key, source_fields=[key], sources=[]))
    if ratios:
        parts.append("> **容量匹配。** " + clauses(ratios, True) + "。该值为设计声明或预算，仍需按同一工况的可逆面容量及锂库存实测核验。")
    else:
        parts.append("> **容量匹配尚未闭合。** 当前没有可采用的 N/P 值，不能仅凭正负极厚度判断匹配成功；应按同一工况的可逆面容量及锂库存核验，获取安排见第 5 节。")
    parts.append("颗粒半径、固相扩散系数及模型实际输入统一见第 4 节；模型物性不混入本节的制备规格。")

    parts.append("### 3.3 电解液、电芯配套与运行条件")
    electrolyte = [r for r in rows("electrolyte") if not r["canonical_key"].endswith(".material")]
    from .parameter_presentation import render_conditions
    scoped = {}
    for row in electrolyte:
        condition = render_conditions(row.get("conditions"), parameters=params) if row.get("conditions") else ""
        scoped.setdefault(condition, []).append(dict(row, conditions={}))
    for condition, items in scoped.items():
        parts.append("**电解液配方声明。** " + clauses(items, True) + "。" + ("适用范围：" + condition + "。" if condition else ""))
    if not electrolyte:
        parts.append("**电解液配方声明。** 锂盐、浓度、溶剂和添加剂尚未完整声明，不能据体系名称推定具体配方。")
    if any(any(w in scope for w in ("冲突", "基线", "候选")) for scope in scoped):
        parts.append("> **配方边界。** 上述声明含基线或候选适用条件，不能自动拼接为已确定的统一配方；须先确认其与当前电解液类别的一致性。")
    for group in ("separator", "current_collector", "packaging"):
        if rows(group):
            parts.append("- **" + GROUPS[group] + "：** " + clauses(rows(group), True) + "。")
    missing = [GROUPS[g] for g in ("separator", "current_collector", "packaging") if not rows(g)]
    if missing:
        parts.append("**配套待定。** " + "、".join(missing) + "的具体规格尚未确定；第 5 节如给出推荐，仅作为首轮实验起点。")
    operating = {"v_min", "v_max", "c_rate", "temperature_K", "temperature_C", "cycle_c_rate", "1c_current_density", "report_cycles_100", "report_cycles_200"}
    conditions = [r for r in rows("condition") if r["canonical_key"].split(".")[-1] in operating]
    if conditions:
        parts.append("**已声明工况。** " + clauses(conditions, True) + "。未声明工况保持待确定，不用模型默认值补齐。")

    parts.append("### 3.4 设计预算与关键待决事项")
    computed = [(k, v) for k, v in data.get("derived", {}).items() if v.get("value") is not None and k.split(".")[-1] != "n_p_ratio"]
    if computed:
        for key, row in computed:
            value = amount(key, row)
            content = describe(key, row).replace(value, f"**{value}**", 1)
            parts.append("- **" + GROUPS.get(key.split(".")[0], "电芯") + "预算：** " + content + "。")
    else:
        parts.append("**预算状态。** 当前没有可直接引用的完整设计预算，不据材料组合推定容量匹配或比能量达标。")
    if any("pore_volume" in key for key, _ in computed):
        parts.append("孔隙体积仅为注液与浸润核算线索，不能单独证明容量匹配或高比能目标可达。")
    gaps = {}
    for key, row in params.items():
        group, _, leaf = key.partition(".")
        if row.get("value") is None and group in ("cathode", "anode", "electrolyte", "condition") and leaf not in ("R_s_um", "D_s_m2s", "original_request"):
            gaps.setdefault(GROUPS[group], []).append(label(key, row))
    for group, names in gaps.items():
        parts.append("- **" + group + "待补数据：** " + "、".join(dict.fromkeys(names)) + "；获取与验证安排见第 5 节。")
    parts.append("以上为设计声明或预算，尚需实验核验及完整电芯质量闭合；完整审核意见见第 2.6 节。")
    return "\n\n".join(parts)
