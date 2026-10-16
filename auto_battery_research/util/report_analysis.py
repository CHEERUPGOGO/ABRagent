"""六节研报的证据分析与实验路线，保留引用、不将初始提案当作最终参数。"""

import re
from pathlib import Path

from .report_contract import display


def source_identity(e):
    meta = e.get("metadata") or {}
    source = str(e.get("source") or meta.get("source_file") or "")
    doi = e.get("doi") or meta.get("doi") or meta.get("source_paper") or ""
    if not str(doi).startswith("10."):
        match = re.search(r"(10\.\d{4,9})[_/]([^/\\]+?)(?:\.md)?$", source)
        doi = f"{match[1]}/{match[2]}" if match else ""
    title = e.get("title") or meta.get("title") or "题名未提供"
    key = str(doi).lower() or source.replace("\\", "/").split("/")[-1] or str(e.get("passage_id"))
    return key, str(doi), str(title), source


def render_sources(doc):
    groups = {}
    for e in doc.get("evidence") or []:
        key, doi, title, source = source_identity(e)
        group = groups.setdefault(key, {"doi": doi, "title": title, "paths": [], "ids": []})
        if source not in group["paths"]:
            group["paths"].append(source)
        if e.get("passage_id") not in group["ids"]:
            group["ids"].append(e.get("passage_id"))
    lines = [f"共归档 {len(doc.get('evidence') or [])} 个证据段落，按 DOI 或来源文件归并为 {len(groups)} 个来源。",
             "同一论文的不同段落不视为独立重复验证；没有 DOI 的来源按文件名归并，独立性仍需人工核实。", ""]
    for i, g in enumerate(groups.values(), 1):
        lines += [f"### 文献 {i}：{display(g['title'])}", f"- DOI：{display(g['doi'])}",
                  "- 段落 ID：" + ", ".join(f"[{pid}]" for pid in g["ids"] if pid)]
        lines.extend(f"- 原始来源：`{p}`" for p in g["paths"])
    return "\n".join(lines)


def _leading_label(para):
    """段首加粗小标题 (如 **正极（主选 NCM811）**)；无标题时返回空串。"""
    match = re.match(r"\s*\*\*(.+?)\*\*", para)
    return match.group(1) if match else ""


def render_materials(doc):
    evidence = {str(e.get("passage_id")): e for e in doc.get("evidence") or []}
    text = doc.get("final_answer") or ""
    # (显示名, 段首标题关键词, 正文关键词, 通用选择准则)
    categories = [
        ("正极", ("正极",), ("正极", "NCM", "单晶"), "正极选择需要同时核对容量、电压窗口和结构稳定性。单晶化、掺杂或包覆应分别列为候选路线，不能把不同材料的优势叠加为本方案已验证性能。"),
        ("负极", ("负极",), ("负极", "硅", "碳宿主", "宿主", "锂金属"), "负极选择应区分材料半电池表现与全电池表现。首效、膨胀和循环保持率必须连同对电极、倍率和质量归一口径解读；良好的半电池结果不直接证明全电池目标达成。"),
        ("电解液与添加剂", ("电解液",), ("电解液", "LiFSI", "FEC", "添加剂"), "电解液候选需要同时匹配正负极界面与工作电压。其他负极体系中的高压循环结果只能作为筛选线索，不能证明当前负极的成膜、产气和寿命表现。PINN 使用的训练电解液也不等于候选配方已被模拟。"),
        ("隔膜", ("隔膜",), ("隔膜", "separator"), "隔膜的选择依据应覆盖厚度、孔结构、润湿与机械完整性。来自其他体系的薄膜规格仅作对照，不能直接写入最终电芯；结构化契约没有声明的规格保持待确定。"),
        ("集流体", ("集流体",), ("集流体", "PET-Cu", "铜箔", "铝箔"), "集流体需同时评估导电、附着、机械缓冲及质量贡献。轻质复合集流体在邻近体系的循环收益属于迁移假设，须用当前活性材料和最终极片几何开展对照验证。"),
    ]
    scheme = doc.get("scheme") or {}
    labels = {"si_base": "硅基/硅碳负极（si_base）", "high_concentration": "高浓度电解液（high_concentration）",
              "lhce": "局部高浓度电解液（lhce）", "li_metal": "锂金属负极（li_metal）"}
    keys = ("cathode", "anode", "electrolyte", "separator", "current_collector")
    # 仅转录有引用的分析段落；排除几何声明、性能表和主观评分。
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    lines = ["**选型结论先行**：以下区分已选材料体系和待验证的具体路线。材料体系取自最终 JSON；形貌、制备和具体配方只有在契约明确声明时才算定案。",
             "", "| 组件 | 当前采用的材料体系 | 决策状态 |", "|---|---|---|"]
    for (name, _, _, _), key in zip(categories, keys):
        value = scheme.get(key)
        lines.append(f"| {name} | {display(labels.get(str(value), value))} | {'体系已选定，具体规格见下文' if value else '尚未定案，下面给出候选与选择条件'} |")
    lines += ["", "文献数值仅对应原文研究对象，不能替代第 3 节的最终设计参数；邻近体系证据仅用于确定验证优先级。"]
    for index, (name, label_keys, terms, rationale) in enumerate(categories, 1):
        value = scheme.get(keys[index - 1])
        lines += ["", f"### 2.{index} {name}：选型结论与取舍",
                  f"**当前决定**：{'采用 ' + display(labels.get(str(value), value)) + ' 材料体系。' if value else '最终契约尚未确定该组件，不将文献候选冒充最终选型。'}", rationale, ""]
        label_hits, term_hits = [], []
        for para in paragraphs:
            if para.lstrip().startswith(("|", "#")) or any(w in para for w in ("冻结几何", "推荐指数", "硬性几何")):
                continue
            ids = [pid for pid in re.findall(r"\[([^\]]+)\]", para)]
            resolved = [pid for pid in ids if pid in evidence]
            if not resolved:
                continue  # 无可定位引用的段落不作为选型依据
            unresolved = [pid for pid in ids if pid not in evidence]
            entry = (para, resolved, unresolved)
            if any(key in _leading_label(para) for key in label_keys):
                label_hits.append(entry)
            elif any(term.lower() in para.lower() for term in terms):
                term_hits.append(entry)
        # 段首标题命中优先；没有标题命中时才回退到正文关键词命中。
        selected = label_hits or term_hits
        if not selected:
            lines.append("当前归档提案中没有可直接引用的该组件分析段落；不补造选型依据。")
        if selected:
            decision, resolved, unresolved = selected[0]
            primary, sep, alternative = decision.partition('\n- 或 ')
            lines += ["**具体路线建议（来自已有提案，尚待验证）**：", primary,
                      "**采用条件**：先核对该路线与最终材料体系及实际电极几何一致，再以相同工况验证；文献中的工艺数值不自动成为当前规格。"]
            if sep:
                lines += ["**备选路线**：" + alternative,
                          "**取舍原则**：优先比较同体系全电池证据；若备选仅有半电池或邻近体系证据，则先作为对照，不直接替换主路线。"]
            if unresolved:
                lines.append("**引用核对提示**：" + "、".join(f"[{pid}]" for pid in unresolved) +
                             " 未在本次归档证据中定位，涉及这些引用的表述不作为选型依据，需回到原文核实。")
        for para, resolved, unresolved in selected[1:3]:
            lines.append("**归档论证摘录（候选/待验证）**：\n" + "\n".join(
                "> " + line for line in para.splitlines()) + "\n")
        ids = {pid for _, resolved, _ in selected[:3] for pid in resolved}
        sources = {source_identity(evidence[pid])[0] for pid in ids}
        lines.append(f"本小节引用 {len(ids)} 个段落、{len(sources)} 个归并来源。该数量只表示来源覆盖，不证明对当前体系的独立交叉验证。")
        lines.append("**取舍与验证边界**：采用与当前材料和测试条件一致的证据作为优先依据；跨体系证据保留为对照假设。未声明的配比、工况和尺寸不从上述摘录自动补入最终设计。")
    return "\n".join(lines)


def render_experiments(doc):
    lines = ["### 5.5 有来源的制备路线与迁移边界",
             "以下摘录来自归档证据原文，只是原论文的制备参考，不能直接作为本任务工艺指令。原文温度、配比等参数必须连同材料体系理解；引用中未给出的条件保持待确定。"]
    seen = set()
    for e in doc.get("evidence") or []:
        meta = e.get("metadata") or {}
        text = str(e.get("text") or "")
        if not (meta.get("label") == "材料制备" or re.search(r"preparation|fabrication|synthesis|制备|合成", text, re.I)):
            continue
        key = source_identity(e)[0]
        if key in seen:
            continue
        seen.add(key)
        lines += ["", f"**文献制备参考 [{e.get('passage_id')}]**（{display(source_identity(e)[2])}）",
                  "> " + text[:1200].replace("\n", "\n> ") + (" …（节选，完整段落见归档证据）" if len(text) > 1200 else ""),
                  "迁移验证：核对原文材料组成、设备与气氛，并先验证产物结构和批间一致性，再评估是否适用于最终选定电极。"]
        if len(seen) == 4:
            break
    if not seen:
        lines.append("当前证据未提供明确的制备段落，制备温度、时间与气氛均待确定。")
    lines += ["", "### 5.6 分阶段实验与判定路线",
              "**材料与界面筛选**：对最终选定材料开展组成、形貌和粒径表征，区分一次颗粒与团聚体；以未改性材料为对照逐项验证包覆、掺杂或碳宿主路线，避免同时改变多个因素而无法归因。采用同一测试窗口比较容量、首效与阻抗，缺失窗口先通过小样稳定性测试确定。",
              "**极片制备窗口**：以第 3、4 节的最终声明和实际生效厚度、孔隙率为验证点。通过涂层称重、截面测量和密度测定建立面载量—厚度—孔隙率关系；浆料固含量、粘结剂比例、干燥和辊压条件分别做梯度试验，以附着、开裂、电子导通及浸润共同筛选。未知配方不填默认数值。",
              "**电解液、隔膜与集流体对照**：保持活性材料和几何一致，分别比较候选与基准电解液、隔膜和集流体。记录润湿、界面阻抗、循环后形貌和产气，检验跨体系证据的可迁移性；不能把模型固定电解液的仿真结果当成新配方已通过。",
              "**装配与化成**：注液量通过孔隙体积估算、吸液称重和浸润实验确定；静置时间以浸润与阻抗趋稳为依据。化成倍率、温度、阶梯和预锂化剂量均待实验确定，试验设计不得擅自扩展已声明电压窗口。N/P 未提供时不把补齐该值列为仿真前置条件。",
              "**电化学与模型比对**：先在仿真实际倍率和温度下获取放电曲线，统一容量归一口径，比较平台、电压终点与可逆容量。再独立测试首效、循环、膨胀和温度依赖；模型仅预测单次放电，不预先给出这些实验的合格结果。",
              "**电芯能量确认**：分别称量正负极涂层、集流体、隔膜、电解液及包装，积分实际放电能量并除以同一口径的质量。将逐项质量结果与固定系数折算并列，解释差异后再判断性能目标，而不是把材料级比能量直接当电芯级指标。",
              "**反馈与停止条件**：若实测偏差来自材料参数，补充对应材料与条件下的证据后修订；D_s 必须有明确证据，不能为了提高输出而调整。若偏差主要来自非活性质量或制造因素，应补充质量与工艺模型。最终结论区分实验支持、模型支持和仍未验证的部分。"]
    return "\n\n".join(lines)


def render_history(task_dir):
    import json
    path = Path(task_dir) / "pinn_simulation_history.json"
    if not path.exists():
        return "### 仿真与优化历史\n未保存逐轮仿真历史，不能重建优化趋势。"
    history = json.loads(path.read_text(encoding="utf-8"))
    lines = ["### 逐轮候选比较", "| 仿真轮 | 状态 | 倍率 C | 比能量 Wh/kg | 较上一有效轮变化 |", "|---|---|---|---|---|"]
    previous = None
    for r in history.get("rounds", []):
        energy = r.get("energy_wh_kg")
        delta = energy - previous if isinstance(energy, (int, float)) and previous is not None else None
        lines.append(f"| {r.get('round')} | {display(r.get('status'))} | {display(r.get('c_rate'))} | {display(energy)} | {display(delta)} |")
        if isinstance(energy, (int, float)):
            previous = energy
    lines += ["", "### 各轮有效参数与结果解释"]
    for r in history.get("rounds", []):
        lines.append(f"\n**第 {r.get('round')} 轮**：{display(r.get('status'))}。")
        name = r.get("result_file")
        sidecar = Path(task_dir) / str(name or "")
        if not name or Path(name).name != name or not sidecar.is_file():
            lines.append("未保存本轮完整结果恢复件；不能推断有效几何或具体失败原因，状态以历史索引为准。")
            continue
        result = json.loads(sidecar.read_text(encoding="utf-8"))
        effective = (result.get("pinn") or {}).get("design") or {}
        for side, title in (("pe", "正极"), ("ne", "负极")):
            g = effective.get(side) or {}
            lines.append(f"- {title}实际厚度 {display(g['L']*1e6 if g.get('L') is not None else None)} μm；孔隙率 {display(g.get('porosity'))}；R_s {display(g['R_s']*1e6 if g.get('R_s') is not None else None)} μm。")
        lines.append(f"- 终止模式：{display(result.get('termination'))}；限制电极：{display(result.get('limiting_electrode'))}；平均电压：{display(result.get('average_voltage_V'))} V。")
        for warning in result.get("warnings") or []:
            lines.append(f"- 运行诊断：{display(warning)}")
    lines.append("\n修订记录中的理由是当时的优化假设；能量升降是本轮观察，二者不能直接等同为因果证明。多参数同时修改时，需单变量对照进一步辨别敏感性。有限候选最优不代表物理上限。")
    return "\n".join(lines)
