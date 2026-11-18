"""第五部分渲染已验收的 Stage 5B 实验推荐，兼容无方案表的旧任务。

输入优先级: 最终结构化设计 + 实际仿真参数 > 完整 design_scheme.md 分析背景 >
检索证据原文 > 模型提出的实验建议。模型正文属于待验证建议, 不能改写最终参数,
不能把跨体系文献数值写成本体系已验证结果, 也不能宣称性能目标已达成。
"""

import json
import re
from datetime import datetime
from pathlib import Path

from .hashing import canonical_hash
from .report_narrative import RENDER_VERSION
from .report_contract import (
    PRECURSOR_LABELS,
    ROUTE_SPECS,
    compute_cathode_pore_metrics,
    format_scalar_or_range,
    parse_numeric_range,
)

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
1. 最终设计数值 (材料体系、电极厚度、孔隙率、颗粒半径、电压窗口、倍率) 以"最终结构化设计"与"锁定配方与工艺锚点"为准;
   完整方案 Markdown 中的数值若与最终设计或实际生效参数不一致, 必须说明差异并以最终值为验证对象。
2. 引用文献时保留原文段落 ID 形式 [passage_id]; 只能引用"可用证据"中真实存在的 ID, 不得虚构。
3. 文献数值必须注明材料体系与测试条件, 并标明是原文研究对象, 不得表述为本设计已实现。
4. 仿真仅覆盖单次放电, 不得推断循环寿命、首效、安全等已通过; 电芯比能量为材料级值乘固定系数折算。
5. 未确定的配比、温度、化成制度等给出"如何确定"的实验途径, 不填无依据的固定值。
6. 建议分组与验证路线 (基准组/推荐组/对照), 说明哪些变量保持一致、哪些做梯度实验。
7. 若涉及元素掺杂, 必须给出唯一的"掺量口径与推荐组合表" (A/B 位全文统一采用同一分母口径，推荐统一以总阳离子为唯一分母，严禁混用"总金属"与"总阳离子"或 A/B 各自采用不同分母),
   锁定主推候选名义化学式 (Rank #1, 待实验验证; 存在审稿保留项或未获实验批准时全文严禁使用"唯一最优"字样), 保留候选排行对比并解释多圈预测保守性与电化学惰性掺杂离子对 TM 氧化还原容量的稀释权衡;
   同时明确 A/B 位与总掺杂量上限, 严禁出现"3.0 mol% 上限内（x_A ≤ 2.0 mol%）"等内外矛盾上限, 且第 5.1 节最高梯度水平 (如 G6) 必须严格不超过所声明的 A/B 位与总掺杂量上限;
   第 5.1 节与第 5.5 节的验收判据条数、名称、指标及阈值必须 100% 一一对应 (推荐 5 项联立判据: ①主目标倍率循环保持率严格区分目标线如 ≥80.0% 与最低放行线如 ≥78.0% 并规定 78.0%–79.9% 的有条件处置路径; ②首周比容量 ≥ 95% G0 且 ICE ≥ G0; ③首周不可逆氧释放/析氧目标如 <5.0 mAh·g⁻¹; ④平均工作电压衰减上限如 ≤2.0 mV/cycle; ⑤XRD Rietveld 晶格畸变与杂相上限判据)。
8. 主推荐电解液与对照组严禁与选定设计/用户硬锁定组件主次反转: 锁定 carbonate_ec 时必须以 1 mol/L LiPF6/EC+DMC 为主配方,
   LHCE 仅可列为机理对照组; 锁定 lhce 时不得将 1 mol/L LiPF6 写为主配方。
   若第 3 节添加剂为已锁定引入 (additive_status=locked), 须明确表述为"种类已选定引入, 剂量水平 (0 vs x wt%) 与化成匹配待实验优化", 严禁写成"<添加剂>未锁定";
   若添加剂为候选筛选 (candidate_screening), 则 G0–G6 必须统一使用无添加剂基础电解液 E0, 严禁将添加剂预混入 G0/G1 基础电解液, 而全部纳入第 5.1 节 G7 与第 5.3 节筛选 DOE (覆盖全部候选添加剂各自的 0 vs 1/2 wt% 单因素梯度及优胜二元组合 × 化成协议 A/B)。
   统一使用规范拼写 `LiDFOB` (严禁写为 `LIDFOB`)。
9. 若负极为金属锂 (li_metal), 必须遵循金属锂真密度 0.534 g/cm³ (20 μm 致密锂箔约 1.068 mg/cm² / 4.12 mAh/cm²),
   严禁赋予金属锂多孔插层压实密度 (如 2.8 g/cm³) 或 30% 孔隙率; 正极本征半电池中的锂过量倍数必须标明为"半电池事后容量比估算 (非全电池 N/P 设计输入)",
   不得虚构全电池 N/P=1.2; 正极极片面载量与压实密度须严格按 `总面载量 = 活性面载量 / f_act`、`压实密度 = 总面载量 / (L_μm × 0.1)` 与 `骨架密度 = 压实密度 / (1 - porosity)` 核算, 严禁错用 `× 9/8`。
10. 材料合成须先给出路线对比表 (适用实验室环境/设备条件及优缺点) 并对比 A 位掺杂"共沉淀 vs 湿法浸渍"方式,
    严格锁定第 3 节指定的合成路线 (`precursor_type`: solid_state 固相法 / sol_gel 溶胶-凝胶法 / carbonate_coprecipitation 碳酸盐共沉淀 / hydroxide_coprecipitation 氢氧化物共沉淀) 与主烧温度/保温时间,
    严禁将对照/备选路线 (如路线 B) 反转为推荐主路线; 若采用固相法 (solid_state) 须写明球磨/混料介质、转速与时间、预烧/主烧升温制度、气氛与研磨过筛粒径;
    若主路线或对照涉及湿化学共沉淀, 须写明可执行细节: 金属盐与沉淀剂溶液配制及注意事项、采用双通道蠕动泵或恒压滴液漏斗并流滴加 (严禁用胶头滴管)、
    滴加速率、搅拌转速 (rpm)、反应温度、pH 控制及 N2 保护气氛; 若包含水洗除残碱步骤, 须安排水洗前后 ICP-OES/MS 检验掺杂元素流失。
    第 5.4 节 ICP-OES/MS 与 XPS 元素溶解/沉积表征必须全覆盖第 5.1 节候选及对照配方中的全部过渡金属与掺杂离子 (含痕量对照元素如 Cu 及其在负极表面的迁移沉积)。
11. 扣式电池硬件 (CR2032、集流体箔材、隔膜型号、正极冲片直径/面积) 须严格区分"设计已锁定锚点"与"未设设计锚点":
    对未设设计锚点的隔膜或铝箔等, 必须标注"未设设计锚点 (由实验室入库台账固定同批次规格)", 严禁写为"与锚点一致";
    注液量须对齐锁定锚点 `electrolyte_volume_uL`, 并基于理论单极片孔体积 V_pore = A_cathode × L_cathode × porosity 核对注液比 E/P = V_E / V_pore
    (常规扣电参考 100–120 μL): 当 V_E / V_pore ≥ 3.0 时必须严密表述为"扣电过量/泛液测试基准 (E/P ≈ XX×；减量富液，非贫液/低液量)", 全文严禁出现"低液量测试基准"、"低液量/泛液"或"贴近/接近/匹配孔体积"字样;
    仅当 V_E / V_pore < 3.0 时方可称为低液量/贫液测试基准。
12. 倍率与电流口径须全节统一: 5.1–5.6 全文 (含母体基准组与改性目标组) 必须锁定同一名义比电流基准 `1 C = X mA·g⁻¹` (例如 LRMO 250、NCM811 200、LFP 170、LNMO 147 mA·g⁻¹),
    并基于正极面积与活性面载量核算绝对电流 `I_abs = m_active × C_rate × X`; 严禁前节写 `1 C = X mA/g` 后节又改用另一数值或改为"以本批母体实测容量定义 1 C"。
13. 严禁引用报告中不存在的内部章节表名 (如"完整方案第三节排行表"、"原方案第 X 节表"); 如需溯源原方案分析请指向 `[设计方案原文](design_scheme.md)` 或在正文内嵌完整摘要表。
14. 表格与正文证据等级必须严格同步: 凡表格标记为"降级/外推/单元素迁移/DFT 近似 (如 α=0.5 插值)"的数值, 正文严禁将其升格为本体系可实现的定量性能预期或循环保持率承诺, 须保持"趋势参考/待实验标定"口径。
15. 若存在审稿保留意见 (issues), 第 5.5 节必须同时包含两张核对表: ①《审稿保留意见逐条闭环状态表》(1..N 全覆盖审稿意见, 列为: 序号 | 审稿意见要点 | 闭环状态(文档已修正 / 待实验闭环) | 对应节次/实验组别及量化判据); ②《关键工程口径与状态闭环核对表》。
16. 输出正文控制在 6500 中文字以内, 必须完整, 不得截断。

章节: 5.1 实验目标与分组 / 5.2 材料制备与极片加工 / 5.3 电芯组装与化成 /
5.4 性能测试与模型对照 / 5.5 结果判定与下一轮迭代 / 5.6 数据缺口与确定方法。"""


def input_fingerprint(doc, context, result, spec, scheme_md):
    """第五部分输入指纹：方案文本、证据、最终设计、仿真与测试条件。"""
    return canonical_hash({
        "render_version": RENDER_VERSION,
        "scheme_md": scheme_md,
        "scheme": doc.get("scheme") or {},
        "ledger_hash": doc.get("ledger_hash"),
        "experiment_plan_hash": doc.get("experiment_plan_hash"),
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
    "li_metal": ("锂金属", "li metal", "lithium metal", "金属锂", "锂箔", "li 箔", "锂负极", "li 负极", "锂片", "锂带"),
    "si_base": ("硅碳", "硅基", "si-c", "si/c", "si@", "硅", "silicon"),
    "graphite": ("石墨", "graphite"),
    "ncm811": ("ncm811", "ncm 811", "高镍ncm811"),
    "lrmo": ("lrmo", "富锂", "li1.2mn0.6ni0.2o2", "li1.2ni0.2mn0.6o2"),
    "lnmo": ("lnmo", "尖晶石", "lini0.5mn1.5o4"),
    "high_concentration": ("高浓度", "高浓", "high concentration", "high_concentration"),
    "lhce": ("lhce", "局部高浓", "局部高浓度"),
    "carbonate_ec": ("carbonate_ec", "碳酸酯", "lipf6", "lipf₆", "ec/dmc", "ec+dmc", "ec/emc", "ec+emc"),
}

_SUBSCRIPT_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉․", "0123456789.")
_VALENCE_UNICODE_RE = re.compile(
    r"(?<=[A-Za-z\]\)])(?:[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,2}[⁺⁻]|[⁺⁻](?![⁰¹²³⁴⁵⁶⁷⁸⁹]))"
)
_VALENCE_CARET_RE = re.compile(
    r"(?<=[A-Za-z\]\)])\^(?:\{\s*\d{0,2}[+-]\s*\}|\(\s*\d{0,2}[+-]\s*\)|\d{0,2}[+-])"
)
_VALENCE_WRAP_RE = re.compile(r"(?<=[A-Za-z])[\(\{]\s*\d{1,2}[+-]\s*[\)\}]")
_VALENCE_PLAIN_RE = re.compile(r"([A-Z][a-z]?)[1-7][+-](?=\d)")
_FORMULA_RE = re.compile(
    r"(?<![A-Za-z])Li(?:\d+(?:\.\d+)?)?(?:[A-Z][a-z]?(?:\d+(?:\.\d+)?)?|\((?:[A-Z][a-z]?(?:\d+(?:\.\d+)?)?){2,}\)(?:\d+(?:\.\d+)?)?|\[(?:[A-Z][a-z]?(?:\d+(?:\.\d+)?)?){2,}\](?:\d+(?:\.\d+)?)?)+O(?:\d+(?:\.\d+)?)?(?![A-Za-z0-9])"
)
_INORGANIC_LI_SALTS = {
    "Li2CO3", "LiNO3", "LiClO4", "Li2SO4", "Li3PO4",
    "Li2SiO3", "Li2C2O4", "LiBO2", "Li2B4O7",
}
_DOPANT_ELEMS = {"Na", "Zn", "Cu", "Ti", "Al", "Mg", "Zr", "Nb", "W", "Mo", "B", "S", "F", "K", "Ca", "Cr", "Ga", "Ge", "La", "Y"}


def _normalize_subscripts(text):
    return str(text or "").translate(_SUBSCRIPT_MAP)


def _strip_valence_superscripts(text):
    """剥离元素后的化学价态/电荷上标，保留单位指数 (m²/s, g⁻¹, 10⁻¹⁴) 与 +FEC 添加剂拼接。"""
    cleaned = str(text or "")
    for pat in (_VALENCE_UNICODE_RE, _VALENCE_CARET_RE, _VALENCE_WRAP_RE):
        cleaned = pat.sub("", cleaned)
    return _VALENCE_PLAIN_RE.sub(r"\1", cleaned)


def _normalize_formula(formula):
    cleaned = _strip_valence_superscripts(_normalize_subscripts(formula).strip())
    cleaned = re.sub(r"(\.\d*?[1-9])0+(?=[A-Za-z\]\)]|$)", r"\1", cleaned)
    cleaned = re.sub(r"(?<=\d)\.0+(?=[A-Za-z\]\)]|$)", "", cleaned)
    return cleaned


def _extract_formulas(text):
    cleaned = _strip_valence_superscripts(_normalize_subscripts(text))
    formulas = []
    for raw in _FORMULA_RE.findall(cleaned):
        norm = _normalize_formula(raw)
        if norm not in _INORGANIC_LI_SALTS:
            formulas.append(norm)
    return formulas


def _formula_stoichiometry(formula: str) -> dict[str, float]:
    """解析规范化化学式的元素计量比（支持圆/方括号团簇与同元素多价态拆分求和）。"""
    norm = _normalize_formula(formula)
    tokens = re.findall(r"[A-Z][a-z]?|\d+(?:\.\d+)?|[\(\)\[\]]", norm)
    stack: list[dict[str, float]] = [{}]
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok in ("(", "["):
            stack.append({})
            idx += 1
        elif tok in (")", "]"):
            if len(stack) == 1:
                idx += 1
                continue
            group = stack.pop()
            mult = 1.0
            if idx + 1 < len(tokens) and re.match(r"^\d+(?:\.\d+)?$", tokens[idx + 1]):
                mult = float(tokens[idx + 1])
                idx += 1
            for elem, cnt in group.items():
                stack[-1][elem] = stack[-1].get(elem, 0.0) + cnt * mult
            idx += 1
        elif re.match(r"^[A-Z][a-z]?$", tok):
            cnt = 1.0
            if idx + 1 < len(tokens) and re.match(r"^\d+(?:\.\d+)?$", tokens[idx + 1]):
                cnt = float(tokens[idx + 1])
                idx += 1
            stack[-1][tok] = stack[-1].get(tok, 0.0) + cnt
            idx += 1
        else:
            idx += 1
    while len(stack) > 1:
        group = stack.pop()
        for elem, cnt in group.items():
            stack[-1][elem] = stack[-1].get(elem, 0.0) + cnt
    return stack[0]


def _same_formula(f1: str, f2: str) -> bool:
    """判断两化学式在去除价态上标、括号排版及同元素拆分后是否化学计量等价。"""
    n1, n2 = _normalize_formula(f1), _normalize_formula(f2)
    if n1 == n2:
        return True
    s1, s2 = _formula_stoichiometry(n1), _formula_stoichiometry(n2)
    if not s1 or set(s1) != set(s2):
        return False
    return all(abs(s1[k] - s2[k]) <= 1e-4 for k in s1)


def _material_named(value, text, design_anchors=None):
    token = str(value or "").strip()
    if not token:
        return True
    lowered = _normalize_subscripts(text).lower()
    if token.lower() in lowered:
        return True
    norm_lowered = _normalize_formula(text).lower()
    aliases = _MATERIAL_ALIASES.get(token.lower(), ())
    if any(alias.lower() in lowered or alias.lower() in norm_lowered for alias in aliases):
        return True
    extracted = _extract_formulas(text)
    if extracted:
        for cand in (token, *aliases):
            if cand.lower().startswith("li") and any(_same_formula(f, cand) for f in extracted):
                return True
        primary = str((design_anchors or {}).get("primary_formula") or "").strip()
        if primary and any(_same_formula(f, primary) for f in extracted):
            return True
    return False


def _detect_primary_route_conflict(norm_text: str, expected_ptype: str) -> bool:
    """检查正文是否将非选定合成路线声明为主推荐/首选/路线 A。"""
    if expected_ptype not in ROUTE_SPECS:
        return False
    other_routes = {k: pat for k, (_, pat) in ROUTE_SPECS.items() if k != expected_ptype}
    _, target_pat = ROUTE_SPECS[expected_ptype]

    fwd_pat = (
        r"(?<!不)(?<!非)(?:优先(?:采用|选用|推荐)?|主(?:推|要|选|路线|方案)|首选|"
        r"(?:推荐|最终|确定|本方案|主路线)采用|(?:^|[|\n])\s*\*{0,2}(?:合成)?路线\s*[A甲1](?!\w))"
        r"([^\n。；;|]{0,65})"
    )
    for m in re.finditer(fwd_pat, norm_text, re.M):
        pre_line = _pre_clause(norm_text, m.start(), 48, split_commas=False)
        if re.search(r"(?:对照路线|备选路线|不改变|并非|不是|严禁|不得|禁止|仅用于)", pre_line):
            continue
        if re.match(r"^\s*(?:地位|位置|资格)", m.group(1)):
            continue
        clause = re.split(
            r"相比|仅作|作为对照|对照|备选|而非|不(?:采用|作|选)|(?:合成)?路线\s*[BC乙丙]",
            m.group(1),
        )[0]
        hits: list[tuple[int, str]] = []
        tm = re.search(target_pat, clause, re.I)
        if tm:
            hits.append((tm.start(), expected_ptype))
        for rkey, rpat in other_routes.items():
            om = re.search(rpat, clause, re.I)
            if om:
                hits.append((om.start(), rkey))
        if hits and min(hits, key=lambda x: x[0])[1] != expected_ptype:
            return True

    for _, rpat in other_routes.items():
        rev_pat = (
            rf"(?<!不)(?<!非)(?:{rpat})[^\n。；;|]{{0,24}}?"
            rf"(?:为主(?:推|要|选|路线|方案)(?![^\n。；;]{{0,4}}(?:对照|备选))|"
            rf"作为主(?:路线|方案|推荐)|替代(?:合成)?路线\s*[A甲1])"
        )
        for rm in re.finditer(rev_pat, norm_text, re.I):
            if not re.search(r"(?:不|非|严禁|禁止|不得|切勿|避免|对照|备选)", _pre_clause(norm_text, rm.start(), 32)):
                return True
    return False


def _detect_primary_formula_inversion(norm_text: str, primary_norm: str, dopant_elems: set[str]) -> bool:
    """检查表格排行行 (1/G1/主推荐) 或正文主推荐声明是否将锚点化学式替换为备选体系。"""
    md_tables = [
        tm.group(1).strip().splitlines()
        for tm in re.finditer(r"(^\|\s*[^\n]+\|\s*\n\|\s*[-:| ]+\|\s*\n(?:\|[^\n]+\n?)+)", norm_text, re.M)
    ]

    # 1a. 显式排行 1 / G1 / R1 / 第1组 表格行
    for m in re.finditer(r"^\|\s*\*{0,2}(?:1|G1|R1|第\s*1\s*(?:位|名|组))\*{0,2}\s*\|([^\n]+)", norm_text, re.M):
        row_line = m.group(0).strip()
        row_body = m.group(1)
        is_g1 = bool(re.match(r"^\|\s*\*{0,2}G1\b", row_line))
        has_primary_label = bool(re.search(r"主推|主推荐|Rank\s*#?1|首选|推荐组", row_body))
        if is_g1 and not has_primary_label:
            if re.search(r"低掺|中掺|高掺|梯度|端|对照|单掺|探索|备选|基准|母体", row_body):
                continue
            containing = next((t for t in md_tables if any(r.strip() == row_line for r in t)), None)
            if containing and any(
                r.strip() != row_line
                and re.search(r"主推|主推荐|Rank\s*#?1|首选", r)
                and any(_same_formula(f, primary_norm) for f in _extract_formulas(r))
                for r in containing[2:]
            ):
                continue
        row_formulas = _extract_formulas(row_body)
        if row_formulas and not any(_same_formula(f, primary_norm) for f in row_formulas):
            return True

    # 1b. 显式标注“主推荐/唯一主推荐/首选”的表格行
    for m in re.finditer(r"^\|([^\n]*(?:唯一主推荐|(?<![非原])主推荐|(?<![非原])首选)[^\n]*)$", norm_text, re.M):
        row_text = m.group(1)
        if re.search(r"非主推荐|非首选|对照|备选|探索组?", row_text):
            continue
        row_formulas = _extract_formulas(row_text)
        if row_formulas and not any(_same_formula(f, primary_norm) for f in row_formulas):
            return True

    # 1c. 组别/排行/推荐表格：跳过基准/G0 对照与梯度/单掺前置行后的首个主推数据行即与锚点不符，且同表后行降格出现锚点
    for rows in md_tables:
        if len(rows) < 3 or not re.search(r"组|排行|排序|推荐|化学式", rows[0]):
            continue
        data_rows = rows[2:]
        table_marks_primary = any(
            re.search(r"主推|主推荐|Rank\s*#?1|首选", r)
            and any(_same_formula(f, primary_norm) for f in _extract_formulas(r))
            for r in data_rows
        )
        first_cand_formulas: list[str] | None = None
        first_is_primary = False
        anchor_demoted_later = False
        for row in data_rows:
            r_formulas = _extract_formulas(row)
            if not r_formulas:
                continue
            if first_cand_formulas is None:
                is_primary_row = bool(re.search(r"主推|主推荐|Rank\s*#?1|首选|推荐组", row))
                if (
                    (re.search(r"G0\b|基准|母体|未掺杂", row) and not re.search(r"G1\b|主推荐|主推候选|主推方案|Rank\s*#?1|首选", row))
                    or (re.search(r"低掺|中掺|高掺|梯度|端|对照|单掺", row) and not is_primary_row)
                    or (table_marks_primary and not is_primary_row)
                ):
                    continue
                first_cand_formulas = r_formulas
                first_is_primary = any(_same_formula(f, primary_norm) for f in r_formulas)
            elif (
                any(_same_formula(f, primary_norm) for f in r_formulas)
                and re.search(r"探索|对照|备选|G[2-9]\b", row)
                and not re.search(r"主推|主推荐|Rank\s*#?1|首选|推荐组", row)
            ):
                anchor_demoted_later = True
        if first_cand_formulas and not first_is_primary and anchor_demoted_later:
            return True

    # 2a. 正文主推荐/锁定锚点声明 (`primary_formula = ...` / `主推荐为 ...`)
    for pat in (
        r"primary_formula\s*[=＝:：]\s*([^\n。；;]+)",
        r"(?<!不)(?<!非)(?:唯一)?(?:主推荐(?:组|方案|配方|材料|化学式)?|主推候选(?:名义)?(?:化学式|组|方案|配方)?|"
        r"主推方案(?:化学式)?|首选(?:组|方案|配方)?|"
        r"最终锁定(?:锚点)?|最优名义?化学式|主配方(?:化学式)?)\s*(?:为|是|采用|锁定为|[=＝:：])\s*([^\n。；;]+)",
    ):
        for m in re.finditer(pat, norm_text, re.I):
            clause = re.split(r"仅作|作为对照|对照|备选|探索|而非|不作为主", m.group(1))[0]
            clause_formulas = _extract_formulas(clause)
            if clause_formulas and not any(_same_formula(f, primary_norm) for f in clause_formulas):
                return True

    # 2b. `以 <化学式> 作为/为唯一主推荐组`
    for m in re.finditer(
        r"(?:以|将|把)\s*([^\n。；;]{1,80}?)\s*(?:为|作为|定为|列为|锁定为)\s*"
        r"(?:唯一)?(?:主推荐(?:组|方案|配方)?|主推候选(?:组|方案|配方)?|主推方案|首选(?:组|方案)?|主配方)",
        norm_text,
        re.I,
    ):
        clause_formulas = _extract_formulas(m.group(1))
        if clause_formulas and not any(_same_formula(f, primary_norm) for f in clause_formulas):
            return True

    # 2c. 锚点化学式全文仅出现于 探索/对照/备选/G2+ 语境，而另有其他掺杂体系
    anchor_has_non_demoted = False
    anchor_seen_in_demoted = False
    for sent in re.split(r"[\n。；;]+", norm_text):
        s_formulas = _extract_formulas(sent)
        if any(_same_formula(f, primary_norm) for f in s_formulas):
            if re.search(r"探索组?|对照|备选|G[2-9]\b", sent):
                anchor_seen_in_demoted = True
            else:
                anchor_has_non_demoted = True
    if anchor_seen_in_demoted and not anchor_has_non_demoted:
        if any(
            not _same_formula(f, primary_norm) and (set(re.findall(r"[A-Z][a-z]?", f)) & dopant_elems)
            for f in _extract_formulas(norm_text)
        ):
            return True
    return False


def _pre_clause(text: str, pos: int, max_chars: int = 40, *, split_commas: bool = True) -> str:
    """提取紧邻匹配位置之前、同一分句边界内的上文。"""
    window = text[max(0, pos - max_chars):pos]
    sep = r"[。\n；;，,]" if split_commas else r"[。\n；;]"
    return re.split(sep, window)[-1]


def _post_clause(text: str, pos: int, max_chars: int = 60, *, split_commas: bool = False) -> str:
    """提取紧邻匹配位置之后、同一分句边界内的下文。"""
    window = text[pos:pos + max_chars]
    sep = r"[。\n；;，,]" if split_commas else r"[。\n；;]"
    return re.split(sep, window)[0]


def _is_disclaimed_metric_mention(
    text: str,
    start: int,
    end: int,
    extra_pre_pat: str = "",
) -> bool:
    """判断载量/压实数值是否为反例警示、非正极参数或对原文偏差的纠正说明。"""
    pre_short = _pre_clause(text, start, 36, split_commas=True)
    if re.search(r"严禁|禁止|不得|不应|切勿|错用|误用|错算|错写|而非|避免", pre_short):
        return True
    if extra_pre_pat:
        pre_extra = _pre_clause(text, start, 28, split_commas=False)
        em = re.search(extra_pre_pat, pre_extra)
        if em and not re.search(r"正极", pre_extra[em.end():]):
            return True
    repudiation_pat = (
        r"不一致|不符|已?作废|已?更正|已?修正|已?更新|非验证|不采用|不再沿用"
        r"|偏差|偏低|偏高|错算|误算|错写|误写|误按|非实际|应为|实为|改按|校准为"
    )
    post_immediate = _post_clause(text, end, 48, split_commas=True)
    if re.search(repudiation_pat, post_immediate):
        return True
    pre_sent = _pre_clause(text, start, 120, split_commas=False)
    post_sent = _post_clause(text, end, 72, split_commas=False)
    source_pre_pat = (
        r"原文|原方案|原提案|旧方案|旧版|此前|原稿|来源方案"
        r"|design_scheme\.md(?:\s*[\)\]]\s*(?:\([^\)]*\))?)?\s*(?:中|所列|所述|标注|写为|记为|写的|记载|载明)"
    )
    return bool(re.search(source_pre_pat, pre_sent, re.I) and re.search(repudiation_pat, post_sent))


def _validate_design_anchors(norm_text, anchors, scheme=None, context=None, doc=None):
    """校验第 5 节正文与第 3 节锁定锚点及选定添加剂的一致性，仅对真实冲突报错。"""
    from auto_battery_research.util.report_contract import _canonical_additive_name, _is_additives_locked
    norm_text = _normalize_subscripts(norm_text)
    errors = []
    scheme = scheme if isinstance(scheme, dict) else {}
    target_goal = str((context or {}).get("target") or (doc or {}).get("target") or "")
    raw_additives = [str(a).strip() for a in (scheme.get("additives") or []) if str(a).strip()]
    additives = [_canonical_additive_name(a) for a in raw_additives]
    add_locked = _is_additives_locked(scheme, anchors=anchors if isinstance(anchors, dict) else {}, target=target_goal)
    for add in dict.fromkeys(a for a in additives if a):
        add_esc = re.escape(add)
        for am in re.finditer(
            rf"(?:{add_esc}[^\n。；;,]{{0,14}}(?:未锁定|未选定|不引入|暂不纳入)|未锁定[^\n。；;,]{{0,14}}{add_esc})",
            norm_text,
            re.I,
        ):
            pre_ctx = _pre_clause(norm_text, am.start(), 32)
            post_ctx = _post_clause(norm_text, am.start(), 56, split_commas=False)
            if re.search(r"(?:不得|严禁|禁止|切勿|不可|不要|并非|不能|避免|不将|不把|勿将)", pre_ctx):
                continue
            # 候选筛选模式下允许明确声明“不预混入/不引入 G0/G1 基础电解液 E0，而纳入 G7/5.3 筛选”
            if not add_locked and re.search(r"G0|G1|基础电解液|E0|G7|5\.3|筛选", post_ctx + pre_ctx, re.I):
                continue
            errors.append(f"实验方案将第 3 节已选定添加剂 {add} 错误表述为未锁定/未选定")
            break
    if additives and not add_locked and re.search(r"(?:^|\n)###\s*5\.[13]", norm_text):
        sec51_m = re.search(r"###\s*5\.1[\s\S]*?(?=###\s*5\.[2-6]|$)", norm_text)
        sec53_m = re.search(r"###\s*5\.3[\s\S]*?(?=###\s*5\.[4-6]|$)", norm_text)
        if sec51_m or sec53_m:
            scoped = (sec51_m.group(0) if sec51_m else "") + "\n" + (sec53_m.group(0) if sec53_m else "")
            if re.search(r"G7|5\.3[^\n]{0,20}筛选|正交|添加剂", scoped):
                missing_adds = [
                    a for a in dict.fromkeys(additives)
                    if not re.search(rf"(?<![A-Za-z0-9]){re.escape(a)}(?![A-Za-z0-9])", scoped, re.I)
                ]
                if missing_adds:
                    errors.append(f"实验方案第 5 节筛选矩阵遗漏候选添加剂: {', '.join(missing_adds)}")
        if sec51_m:
            for gline in sec51_m.group(0).splitlines():
                if re.match(r"^\s*(?:\|\s*|[-*•]\s*|\*\*\s*)?G[01]\b", gline):
                    if any(
                        re.search(rf"(?<![A-Za-z0-9]){re.escape(a)}(?![A-Za-z0-9])", gline, re.I)
                        for a in additives
                    ) and not re.search(r"不(?:含|预加入|添加|引入)|无添加剂|纯\s*E0|不含", gline):
                        errors.append("实验方案违背候选添加剂筛选隔离口径：G0/G1 基础电解液不得预混入未锁定添加剂")
                        break

    if not isinstance(anchors, dict) or not anchors:
        return errors

    primary_raw = str(anchors.get("primary_formula") or "").strip()
    if primary_raw:
        primary_norm = _normalize_formula(primary_raw)
        primary_elems = set(re.findall(r"[A-Z][a-z]?", primary_norm))
        host_elems = {"Li", "Mn", "Ni", "Co", "O"}
        if "P" in primary_elems:
            host_elems |= {"Fe", "P"}
        dopant_elems = (primary_elems - host_elems) | _DOPANT_ELEMS
        all_formulas = _extract_formulas(norm_text)
        has_primary = any(_same_formula(f, primary_norm) for f in all_formulas)
        doped_others = [
            f for f in all_formulas
            if not _same_formula(f, primary_norm) and (set(re.findall(r"[A-Z][a-z]?", f)) & dopant_elems)
        ]
        rank1_mismatch = _detect_primary_formula_inversion(norm_text, primary_norm, dopant_elems)
        if rank1_mismatch or (doped_others and not has_primary):
            errors.append("实验方案最优名义化学式与第 3 节选定设计不一致")

    cap = anchors.get("doping_upper_limit_mol_pct")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool):
        cap_pat = (
            r"(?:总掺(?:杂)?(?:量)?|掺杂总量|总?掺量上限)[^\n，,；;。]{0,20}?"
            r"(?:<=|≤|<|不超过|上限[^\n\d，,；;。]{0,6})\s*(?:\d+(?:\.\d+)?\s*[~–—-]\s*)?"
            r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:mol%|at%)"
            r"|(?<![A-Za-z])(?:总?掺(?:杂)?(?:量)?上限)[^\n\d，,；;。]{0,6}"
            r"(?:\d+(?:\.\d+)?\s*[~–—-]\s*)?(\d+(?:\.\d+)?)\s*(?:mol%|at%)"
            r"|(?:低于|<=|≤|<)\s*(?<![\d.])(\d+(?:\.\d+)?)\s*(?:mol%|at%)[^\n，,；;。]{0,8}上限"
        )
        for m in re.finditer(cap_pat, norm_text, re.I):
            val_str = m.group(1) or m.group(2) or m.group(3)
            if abs(float(val_str) - float(cap)) > 0.1:
                errors.append("实验方案总掺杂量上限与选定设计不一致")
                break

    ptype = str(anchors.get("precursor_type") or "").strip()
    if ptype in ROUTE_SPECS and _detect_primary_route_conflict(norm_text, ptype):
        errors.append(f"实验方案合成路线与选定设计({PRECURSOR_LABELS[ptype]})矛盾")

    temp_target = parse_numeric_range(anchors.get("calcination_temp_range_c") or anchors.get("calcination_temp_c"))
    if temp_target is not None:
        t_lo, t_hi = temp_target
        calc_ranges = []
        for clause_m in re.finditer(r"(?:主烧|烧结|煅烧)[^\n。；;]*", norm_text):
            for tm in re.finditer(r"(?<![\d.])(\d{3,4})(?:\s*[~–—-]\s*(\d{3,4}))?\s*(?:°C|℃)", clause_m.group(0)):
                lo = float(tm.group(1))
                hi = float(tm.group(2)) if tm.group(2) else lo
                if lo > hi:
                    lo, hi = hi, lo
                calc_ranges.append((lo, hi))
        if calc_ranges and not any(lo <= (t_hi + 5.0) and hi >= (t_lo - 5.0) for lo, hi in calc_ranges):
            errors.append("实验方案主烧/烧结温度与选定设计不一致")

    hours_target = parse_numeric_range(anchors.get("calcination_hours_range") or anchors.get("calcination_hours"))
    if hours_target is not None:
        h_lo, h_hi = hours_target
        hour_ranges = []
        for clause_m in re.finditer(r"(?:主烧|烧结|煅烧)[^\n。；;]*", norm_text):
            for hm in re.finditer(r"(?<![\d.])(\d+(?:\.\d+)?)(?:\s*[~–—-]\s*(\d+(?:\.\d+)?))?\s*(?:h|小时)", clause_m.group(0), re.I):
                lo = float(hm.group(1))
                hi = float(hm.group(2)) if hm.group(2) else lo
                if lo > hi:
                    lo, hi = hi, lo
                hour_ranges.append((lo, hi))
        if hour_ranges and not any(lo <= (h_hi + 0.5) and hi >= (h_lo - 0.5) for lo, hi in hour_ranges):
            errors.append("实验方案主烧/烧结保温时间与选定设计不一致")

    al_um = anchors.get("al_foil_um")
    al_min = al_max = None
    if isinstance(al_um, (int, float)) and not isinstance(al_um, bool):
        al_min = al_max = float(al_um)
    elif al_um is not None:
        al_nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(al_um))]
        if al_nums:
            al_min, al_max = min(al_nums), max(al_nums)
    if al_min is not None and al_max is not None:
        al_ranges = []
        al_pat = (
            r"(?<![\d.])(\d+(?:\.\d+)?)(?:\s*[~–—-]\s*(\d+(?:\.\d+)?))?\s*(?:μm|um)[^\n,，；;。]{0,14}(?:Al|铝)\s*(?:箔|foil)"
            r"|(?:Al|铝)\s*(?:箔|foil)[^\n,，；;。]{0,14}?(?<![\d.])(\d+(?:\.\d+)?)(?:\s*[~–—-]\s*(\d+(?:\.\d+)?))?\s*(?:μm|um)"
        )
        for m in re.finditer(al_pat, norm_text, re.I):
            if m.group(1):
                lo, hi = float(m.group(1)), float(m.group(2) or m.group(1))
            else:
                lo, hi = float(m.group(3)), float(m.group(4) or m.group(3))
            if lo > hi:
                lo, hi = hi, lo
            al_ranges.append((lo, hi))
        if al_ranges and not all(lo <= (al_max + 0.5) and hi >= (al_min - 0.5) for lo, hi in al_ranges):
            errors.append("实验方案 Al 箔厚度与选定设计不一致")

    sep = str(anchors.get("separator") or "").strip()
    if sep and re.search(r"Celgard\s*\d{4}", norm_text, re.I):
        if re.sub(r"\s+", "", sep.lower()) not in re.sub(r"\s+", "", norm_text.lower()):
            errors.append("实验方案隔膜型号与选定设计不一致")

    diam = anchors.get("cathode_diameter_mm")
    if isinstance(diam, (int, float)):
        diam_matches = re.findall(r"正极[^\n,，；;。]{0,10}?[φΦ]?\s*(?<![\d.])(\d+(?:\.\d+)?)\s*mm", norm_text)
        if diam_matches and not all(abs(float(m) - float(diam)) <= 0.5 for m in diam_matches):
            errors.append("实验方案正极尺寸与选定设计不一致")

    _anchor_neg_re = re.compile(
        r"不得|严禁|禁止|并非|未设|无|不写|不用|不作|不要|不表述|不可|不能|切勿|避免|不使用|不以|未锚定"
    )
    if not str(anchors.get("separator") or "").strip():
        for m in re.finditer(r"隔膜[^\n。；;]{0,30}与(?:锁定)?(?:设计)?锚点(?:完全)?一致", norm_text):
            ctx_span = _pre_clause(norm_text, m.start(), 28, split_commas=False) + m.group(0) + _post_clause(norm_text, m.end(), 20, split_commas=False)
            if not _anchor_neg_re.search(ctx_span):
                errors.append("实验方案将未设设计锚点的隔膜规格误称“与锚点一致”")
                break
    if anchors.get("al_foil_um") in (None, ""):
        for m in re.finditer(r"(?:Al\s*箔|铝箔|集流体)[^\n。；;]{0,30}与(?:锁定)?(?:设计)?锚点(?:完全)?一致", norm_text, re.I):
            ctx_span = _pre_clause(norm_text, m.start(), 28, split_commas=False) + m.group(0) + _post_clause(norm_text, m.end(), 20, split_commas=False)
            if not _anchor_neg_re.search(ctx_span):
                errors.append("实验方案将未设设计锚点的集流体铝箔规格误称“与锚点一致”")
                break

    return errors


def validate_plan_text(text, doc, context, target, tolerance):
    """验收历史实验章节；结构化 Stage 5B 推荐走独立的绑定验收。"""
    if doc.get("ledger_view"):
        from .scheme_ledger import render_experiment
        expected = render_experiment(doc["ledger_view"], context.get("result") or {})
        return [] if isinstance(text, str) and text.startswith(expected) else ["实验章节与方案表不一致"]
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
        anchors = scheme.get("design_anchors") if material_key == "cathode" else None
        if not _material_named(scheme.get(material_key), text, design_anchors=anchors):
            errors.append(f"实验方案未体现最终{('正极' if material_key == 'cathode' else '负极')}材料 {scheme.get(material_key)}")
    geometry = (scheme.get("geometry") or {})
    effective = context.get("effective") or {}
    allowed = []
    for comp, side in (("cathode", "pe"), ("anode", "ne")):
        declared = (geometry.get(comp) or {}).get("L_um")
        actual = (effective.get(side) or {}).get("L")
        allowed += [v for v in (declared, actual * 1e6 if actual else None) if v]
    stated = []
    for m in re.finditer(r"(?:厚度|L)[^\d]{0,6}(\d+(?:\.\d+)?)\s*(?:μm|um)", text):
        pre_ctx = _pre_clause(text, m.start(), 48, split_commas=False)
        comp_hits = list(
            re.finditer(
                r"(Al\s*箔|铝箔|铜箔|集流体|隔膜|separator|Celgard|正极|负极|活性(?:物|层|极片)|极片)",
                pre_ctx,
                re.I,
            )
        )
        if comp_hits and re.match(
            r"^(?:Al\s*箔|铝箔|铜箔|集流体|隔膜|separator|Celgard)$",
            comp_hits[-1].group(1),
            re.I,
        ):
            continue
        stated.append(m.group(1))
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

    norm_text = _normalize_subscripts(text)
    electrolyte = str(scheme.get("electrolyte") or "").strip().lower()
    if electrolyte == "carbonate_ec":
        for m in re.finditer(r"主(?:推荐|配方|方案)[^\n。；;，,]{0,60}(?:LHCE|局部高浓)", norm_text, re.I):
            clause = re.split(r"[。；;，,\n]", norm_text[m.start():])[0]
            if not re.search(r"不(?:采用|使用|作|选)|非|仅作|对照|对比|备选", clause):
                errors.append("实验方案主电解液与选定设计(carbonate_ec)矛盾")
                break
    elif electrolyte == "lhce":
        for m in re.finditer(r"主(?:推荐|配方|方案)[^\n。；;，,]{0,60}LiPF6", norm_text, re.I):
            clause = re.split(r"[。；;，,\n]", norm_text[m.start():])[0]
            if not re.search(r"LHCE|局部高浓|LiFSI|不(?:采用|使用|作|选)|仅作|对照|对比|备选", clause, re.I):
                errors.append("实验方案主电解液与选定设计(lhce)矛盾")
                break

    if str(scheme.get("anode") or "").strip().lower() == "li_metal":
        for m in re.finditer(
            r"(?:负极|金属锂[箔片]?|锂[箔片])[^\n。；;]{0,30}压实(?:密度)?[^\d]{0,8}(\d+(?:\.\d+)?)\s*g\s*[·/]?\s*cm",
            norm_text,
        ):
            if re.search(r"(?:严禁|禁止|不得|切勿|避免|不赋予|不可)", _pre_clause(norm_text, m.start(), 32)):
                continue
            if float(m.group(1)) > 0.6:
                errors.append("锂金属负极参数非物理：压实密度超出金属锂真密度 0.534 g/cm³")
                break

    errors.extend(_validate_design_anchors(norm_text, scheme.get("design_anchors"), scheme=scheme, context=context, doc=doc))
    errors.extend(_validate_ep_and_plan_consistency(norm_text, scheme))
    errors.extend(_validate_plan_integrity(text, norm_text, scheme, context, doc))
    return errors


def _parse_slurry_active_fraction(slurry_ratio) -> float | None:
    parts = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(slurry_ratio or ""))]
    if len(parts) >= 2 and sum(parts) > 0 and parts[0] > 0:
        return parts[0] / sum(parts)
    return None


def _extract_cathode_loading_mg_cm2(scheme: dict, text: str = "", *, require_active_word: bool = True) -> float | None:
    anchors = scheme.get("design_anchors") if isinstance(scheme.get("design_anchors"), dict) else {}
    cath_geo = ((scheme.get("geometry") or {}).get("cathode")) or {}
    for cand in (anchors.get("loading_mg_cm2"), cath_geo.get("loading_mg_cm2")):
        if isinstance(cand, (int, float)) and not isinstance(cand, bool) and 0.5 <= float(cand) <= 60.0:
            return float(cand)
    if text:
        pat = (
            r"(?:正极)?活性(?:物质|材料|物)?(?:面)?载量[^\n\d。；;]{0,16}?(\d+(?:\.\d+)?)\s*mg\s*[·/]?\s*cm"
            if require_active_word
            else r"(?<!总)(?:正极)?(?:活性(?:物质|材料|物)?)?(?:面)?(?:负载|载)量[^\n\d。；;]{0,16}?(\d+(?:\.\d+)?)\s*mg\s*[·/]?\s*cm"
        )
        for m in re.finditer(pat, text, re.I):
            if re.search(r"严禁|不得|错用|误用|示例|全电池|商业", _pre_clause(text, m.start(), 24)):
                continue
            val = float(m.group(1))
            if 0.5 <= val <= 60.0:
                return val
    return None


def _extract_scheme_tm_dopants(text_sources: list[str], extra_text: str = "") -> list[str]:
    """动态提取候选及对照组化学式与组分中的非 Li/O 金属与掺杂元素符号。"""
    excluded = {"Li", "O", "H", "C", "N", "F", "S", "Cl", "Br", "I"}
    allowed_metals_dopants = {
        "Mn", "Ni", "Co", "Fe", "Zn", "Mg", "Cu", "Na", "K", "Ca", "Sr", "Ba",
        "Al", "Ti", "Zr", "Nb", "W", "Mo", "Cr", "V", "Ga", "Ge", "Sn", "Sb",
        "Bi", "La", "Ce", "Y", "Yb", "Sc", "Ru", "Ta", "Hf", "B", "P", "Si",
    }
    found: list[str] = []
    seen: set[str] = set()

    def _add_el(sym: str) -> None:
        if sym in allowed_metals_dopants and sym not in excluded and sym not in seen:
            seen.add(sym)
            found.append(sym)

    for src in list(text_sources) + ([extra_text] if extra_text else []):
        norm = _normalize_subscripts(str(src or ""))
        for f in _extract_formulas(norm):
            for sym in re.findall(r"[A-Z][a-z]?", f):
                _add_el(sym)
        for dm in re.finditer(
            r"(?<![A-Za-z])(Mn|Ni|Co|Fe|Zn|Mg|Cu|Na|K|Al|Ti|Zr|Nb|W|Mo|Cr|Ga|La|Ce|Y|B|Si)"
            r"(?=\s*(?:[²³⁴⁺\+]|\d\+|掺杂|共掺|单掺|取代|置换|离子|溶出|流失|沉积|/|、|与|\+))",
            norm,
        ):
            _add_el(dm.group(1))
        for gm in re.finditer(r"(?:^|\n)[^\n]*\bG[0-9]\b[^\n]*", norm):
            gline = gm.group(0)
            for sym in re.findall(
                r"(?<![A-Za-z])(Mn|Ni|Co|Fe|Zn|Mg|Cu|Na|K|Al|Ti|Zr|Nb|Mo|Cr|Ga|La|Ce|Y|Si)(?![a-z])",
                gline,
            ):
                _add_el(sym)
    return found


def _validate_plan_integrity(raw_text: str, norm_text: str, scheme: dict, context: dict, doc: dict) -> list[str]:
    """校验 P0-1..P0-8、P1-10、P2 报告与工程口径闭环一致性。"""
    errors: list[str] = []
    anchors = scheme.get("design_anchors") if isinstance(scheme.get("design_anchors"), dict) else {}
    review_issues = (
        ((doc or {}).get("reviewer_output") or {}).get("issues")
        or ((context or {}).get("reviewer_output") or {}).get("issues")
        or []
    )

    # P2: 禁止添加剂 LiDFOB 全大写误拼 LIDFOB
    for m in re.finditer(r"\bLIDFOB\b", raw_text):
        pre = _pre_clause(raw_text, m.start(), 24)
        post = _post_clause(raw_text, m.end(), 24)
        if re.search(r"严禁|禁止|不得|切勿|避免|误拼|错写|改为|规范为", pre + post):
            continue
        errors.append("实验方案存在添加剂名称大小写误拼（LIDFOB 应规范写为 LiDFOB）")
        break

    # P0-2: 存在审稿保留意见或未获实验批准时，严禁宣称“唯一最优”
    if review_issues:
        for m in re.finditer(r"唯一最优", norm_text):
            pre = _pre_clause(norm_text, m.start(), 32)
            post = _post_clause(norm_text, m.end(), 28)
            if re.search(
                r"严禁|禁止|不得|切勿|避免|不使用|不宣称|不标|不写|不作|不用|不表述|不称|非|不是|不能|并非|勿|去掉|删除|废止",
                pre + post,
            ):
                continue
            errors.append("实验方案存在审稿保留意见，严禁宣称“唯一最优”（应表述为主推候选 Rank #1，待实验验证）")
            break

    sec51_m = re.search(r"###\s*5\.1[\s\S]*?(?=###\s*5\.[2-6]|$)", norm_text)
    sec54_m = re.search(r"###\s*5\.4[\s\S]*?(?=###\s*5\.[56]|$)", norm_text)
    sec55_m = re.search(r"###\s*5\.5[\s\S]*?(?=###\s*5\.6|$)", norm_text)
    sec51 = sec51_m.group(0) if sec51_m else ""
    sec54 = sec54_m.group(0) if sec54_m else ""
    sec55 = sec55_m.group(0) if sec55_m else ""

    # P0-1: 掺杂上限自相矛盾（如 3.0 mol% 上限内（x_A ≤ 2.0 mol%））及 G6 梯度超限、双分母混用
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:mol%|at%)[^\n。；;]{0,12}上限内[（(]([^）)\n]{1,60})[）)]", norm_text, re.I):
        outer_cap = float(m.group(1))
        inner = m.group(2)
        if re.search(r"严禁|禁止|不得|切勿|示例|避免", _pre_clause(norm_text, m.start(), 28)):
            continue
        inner_bounds = [float(x) for x in re.findall(r"(?:<=|≤|<)\s*(\d+(?:\.\d+)?)\s*(?:mol%|at%)", inner, re.I)]
        has_split_sites = ("+" in inner or "＋" in inner or "、" in inner or "," in inner) and len(inner_bounds) >= 2
        if len(inner_bounds) == 1 and not has_split_sites and inner_bounds[0] < outer_cap - 0.1:
            errors.append(
                f"实验方案掺杂上限表述自相矛盾：{outer_cap:g} mol% 上限内嵌套了更紧单上限 {inner_bounds[0]:g} mol%"
            )
            break

    total_cap = anchors.get("doping_upper_limit_mol_pct")
    if total_cap is None and sec51:
        cap_m = re.search(
            r"(?:总掺(?:杂)?(?:量)?|掺杂总量|总?掺量上限)[^\n，,；;。]{0,16}?(?:<=|≤|<)\s*(\d+(?:\.\d+)?)\s*(?:mol%|at%)",
            sec51,
            re.I,
        )
        if cap_m:
            total_cap = float(cap_m.group(1))
    a_cap = anchors.get("a_site_upper_limit_mol_pct")
    b_cap = anchors.get("b_site_upper_limit_mol_pct")

    if isinstance(total_cap, (int, float)) and not isinstance(total_cap, bool) and sec51:
        cap_val = float(total_cap)
        for line in sec51.splitlines():
            if not re.match(r"^\s*(?:\|\s*|[-*•]\s*|\*\*\s*)?G[1-9]\d?\b", line):
                continue
            if re.search(r"上限|严禁|不得", line) and not line.lstrip().startswith("|"):
                continue
            # 检查单行中显式声明的“总掺杂/合计 X mol%”或“a mol% + b mol%”之和是否超越总上限
            tot_m = re.search(r"(?:总掺(?:杂)?(?:量)?|合计|total)[^\d\n]{0,8}(\d+(?:\.\d+)?)\s*(?:mol%|at%)", line, re.I)
            if tot_m and float(tot_m.group(1)) > cap_val + 0.05:
                errors.append(
                    f"实验方案第 5.1 节实验组总掺量（{float(tot_m.group(1)):g} mol%）超出声明上限（≤ {cap_val:g} mol%）"
                )
                break
            plus_m = re.search(
                r"(\d+(?:\.\d+)?)\s*(?:mol%|at%)\s*(?:\+|＋)\s*(\d+(?:\.\d+)?)\s*(?:mol%|at%)"
                r"(?:\s*(?:\+|＋)\s*(\d+(?:\.\d+)?)\s*(?:mol%|at%))?",
                line,
                re.I,
            )
            if plus_m:
                plus_sum = float(plus_m.group(1)) + float(plus_m.group(2)) + float(plus_m.group(3) or 0.0)
                if plus_sum > cap_val + 0.05:
                    errors.append(
                        f"实验方案第 5.1 节实验组共掺加和（{plus_sum:g} mol%）超出总掺杂上限（≤ {cap_val:g} mol%）"
                    )
                    break
            # 忽略以 TM 位明确折算后标注的括号说明，只校验未限定为 TM 分母的 mol% 数值
            cleaned_line = re.sub(r"[（(][^）)\n]*(?:TM|过渡金属)[^）)\n]*[）)]", "", line, flags=re.I)
            for sm in re.finditer(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:mol%|at%)", cleaned_line, re.I):
                if float(sm.group(1)) > cap_val + 0.05:
                    errors.append(
                        f"实验方案第 5.1 节梯度组掺杂水平（{float(sm.group(1)):g} mol%）超出声明总上限（≤ {cap_val:g} mol%）"
                    )
                    break
            else:
                continue
            break

    if isinstance(a_cap, (int, float)) and not isinstance(a_cap, bool) and sec51:
        for am in re.finditer(r"A\s*位[^\n。；;]{0,16}?(\d+(?:\.\d+)?)\s*(?:mol%|at%)", sec51, re.I):
            if re.search(r"严禁|不得|上限|不超过|≤|<=", sec51[max(0, am.start() - 6):am.end()]):
                continue
            if float(am.group(1)) > float(a_cap) + 0.05:
                errors.append(f"实验方案 A 位梯度掺量（{float(am.group(1)):g} mol%）超出 A 位上限（≤ {float(a_cap):g} mol%）")
                break
    if isinstance(b_cap, (int, float)) and not isinstance(b_cap, bool) and sec51:
        for bm in re.finditer(r"B\s*位[^\n。；;]{0,16}?(\d+(?:\.\d+)?)\s*(?:mol%|at%)", sec51, re.I):
            if re.search(r"严禁|不得|上限|不超过|≤|<=", sec51[max(0, bm.start() - 6):bm.end()]):
                continue
            if float(bm.group(1)) > float(b_cap) + 0.05:
                errors.append(f"实验方案 B 位梯度掺量（{float(bm.group(1)):g} mol%）超出 B 位上限（≤ {float(b_cap):g} mol%）")
                break

    # P0-4: 极片总面载量与压实密度公式核对
    f_act = _parse_slurry_active_fraction(anchors.get("slurry_ratio"))
    active_loading = _extract_cathode_loading_mg_cm2(scheme, norm_text)
    cath_l_um = ((scheme.get("geometry") or {}).get("cathode") or {}).get("L_um")
    ref_total_loading: float | None = None
    if f_act and active_loading:
        ref_total_loading = active_loading / f_act
        for tm in re.finditer(
            r"(?:极片|涂覆)?总(?:面)?载量[^\d\n。；;]{0,12}(?<![\d.])(\d+(?:\.\d+)?)(?:\s*[~–—-]\s*(\d+(?:\.\d+)?))?\s*mg\s*[·/]?\s*cm",
            norm_text,
            re.I,
        ):
            if _is_disclaimed_metric_mention(norm_text, tm.start(), tm.end()):
                continue
            lo = float(tm.group(1))
            hi = float(tm.group(2) or tm.group(1))
            if lo > hi:
                lo, hi = hi, lo
            if ref_total_loading < lo * 0.95 or ref_total_loading > hi * 1.05:
                errors.append(
                    f"实验方案正极总面载量算术有误：活性载量 {active_loading:g} mg/cm² 除以活性占比 {f_act:.3g} "
                    f"应为 {ref_total_loading:.3f} mg/cm²，实写 {tm.group(0)}"
                )
                break
    if isinstance(cath_l_um, (int, float)) and float(cath_l_um) > 0 and ref_total_loading:
        expected_compaction = ref_total_loading / (float(cath_l_um) * 0.1)
        for cm in re.finditer(
            r"压实(?:密度)?[^\d\n。；;]{0,12}(?<![\d.])(\d+(?:\.\d+)?)(?:\s*[~–—-]\s*(\d+(?:\.\d+)?))?\s*g\s*[·/]?\s*cm",
            norm_text,
            re.I,
        ):
            if _is_disclaimed_metric_mention(
                norm_text,
                cm.start(),
                cm.end(),
                extra_pre_pat=r"负极|金属锂|锂[箔片]|骨架|真密度",
            ):
                continue
            lo = float(cm.group(1))
            hi = float(cm.group(2) or cm.group(1))
            if lo > hi:
                lo, hi = hi, lo
            if expected_compaction < lo * 0.92 or expected_compaction > hi * 1.08:
                errors.append(
                    f"实验方案正极压实密度与总面载量/厚度不一致：按 {ref_total_loading:.3f} mg/cm² 与 {cath_l_um:g} μm "
                    f"应约为 {expected_compaction:.3f} g/cm³，实写 {cm.group(0)}"
                )
                break

    # P0-8: 5.4 ICP/溶解测试必须覆盖 5.1 候选及对照分组中的全部过渡金属与掺杂元素
    if sec51 and sec54 and re.search(r"ICP|溶解|溶出|流失|迁移", sec54, re.I):
        sec51_elems = _extract_scheme_tm_dopants([sec51])
        missing_elems = [
            el for el in sec51_elems
            if not re.search(rf"(?<![A-Za-z]){re.escape(el)}(?![a-z])", sec54)
        ]
        if missing_elems:
            errors.append(
                f"实验方案第 5.4 节元素溶解/ICP-OES 表征遗漏了第 5.1 节候选/对照涉及的元素: {', '.join(missing_elems)}"
            )

    # P0-3: 5.1 与 5.5 验收判据一致性与层级界定
    if sec51 and sec55:
        r51 = {float(m.group(1)) for m in re.finditer(r"保持率[^\d\n。；;]{0,10}(?:>=|≥|>)\s*(\d+(?:\.\d+)?)\s*%", sec51)}
        r55 = {float(m.group(1)) for m in re.finditer(r"保持率[^\d\n。；;]{0,10}(?:>=|≥|>)\s*(\d+(?:\.\d+)?)\s*%", sec55)}
        if r51 and r55 and r51 != r55:
            both_tiered = (
                bool(re.search(r"目标(?:值|线)?", sec51) and re.search(r"最低(?:放行)?|放行(?:线|门槛)|有条件", sec51))
                and bool(re.search(r"目标(?:值|线)?", sec55) and re.search(r"最低(?:放行)?|放行(?:线|门槛)|有条件", sec55))
            )
            if not both_tiered:
                errors.append("实验方案第 5.1 节与第 5.5 节循环保持率验收阈值前后不一致且未明确双层目标/放行分级")
        sec55_gates_scope = re.split(
            r"(?:^|\n)(?:#{2,5}\s*[^\n]*(?:审稿保留|逐条闭环)|\*\*[^\n]*(?:审稿保留|逐条闭环))",
            sec55,
            maxsplit=1,
        )[0]
        _circled_map = {"①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5", "⑥": "6", "⑦": "7", "⑧": "8"}
        _gate_pat = re.compile(
            r"([①②③④⑤⑥⑦⑧]|(?<![\d.])\(([1-9])\)|(?:^|\|\s*|(?<=[\s;；(（]))\*{0,2}([1-9])\.(?!\d)|(?<![A-Za-z0-9.])C([1-9])(?![\d.A-Za-z]))"
            r"[^\n]{0,40}(?:保持率|比容量|首效|ICE|氧释放|析氧|电压衰减|XRD|杂相|晶格)"
        )
        gate_55_keys = {
            _circled_map.get(m.group(1), m.group(2) or m.group(3) or m.group(4) or m.group(1))
            for m in _gate_pat.finditer(sec55_gates_scope)
        }
        gate_51_keys = {
            _circled_map.get(m.group(1), m.group(2) or m.group(3) or m.group(4) or m.group(1))
            for m in _gate_pat.finditer(sec51)
        }
        if len(gate_55_keys) >= 4 and 1 <= len(gate_51_keys) < len(gate_55_keys):
            errors.append(
                f"实验方案第 5.1 节与第 5.5 节验收判据条数不一致（5.1 列出 {len(gate_51_keys)} 项，5.5 列出 {len(gate_55_keys)} 项）"
            )

    # P1-10: 有审稿保留意见时，5.5 节必须包含逐条闭环状态表/清单覆盖全部 N 条
    if review_issues and sec55:
        has_closure_heading = bool(re.search(r"审稿保留|保留意见|逐条闭环|闭环状态", sec55))
        item_nums = set()
        for nm in re.finditer(r"(?:^\s*\|\s*(\d+)\s*\||^\s*(?:[-*]\s*)?(\d+)[\.\)、]\s+)", sec55, re.M):
            idx = int(nm.group(1) or nm.group(2))
            if 1 <= idx <= len(review_issues):
                item_nums.add(idx)
        if not has_closure_heading or len(item_nums) < len(review_issues):
            errors.append(
                f"实验方案第 5.5 节缺少覆盖全部 {len(review_issues)} 条审稿保留意见的逐条闭环状态表"
            )

    return errors


def _validate_ep_and_plan_consistency(norm_text: str, scheme: dict) -> list[str]:
    """校验注液孔体积物理宣称、单一 1 C 基准口径、绝对电流核算与禁止幻象交叉引用/降级数据升格。"""
    errors: list[str] = []
    ep_metrics = compute_cathode_pore_metrics(scheme)
    if ep_metrics and ep_metrics["ratio_min"] >= 3.0:
        for m in re.finditer(r"(?:贴近|接近|匹配)[^\n。；;]{0,12}孔体积", norm_text):
            sent_pre = _pre_clause(norm_text, m.start(), 48, split_commas=False)
            sent_post = _post_clause(norm_text, m.end(), 64, split_commas=False)
            if re.search(
                r"(?:不|非|严禁|禁止|不得|切勿|避免|并非|不是|未|无|勿)",
                _pre_clause(norm_text, m.start(), 24),
            ) or re.search(
                r"(?:原文|原方案|原提案|旧方案|此前)", sent_pre
            ) or re.search(
                r"(?:作废|不成立|自相矛盾|矛盾|纠正|并非成立|已废止|误称|错误表述)", sent_post
            ):
                continue
            errors.append(
                f"实验方案注液量表述与物理孔体积矛盾：单片正极理论孔体积约 {ep_metrics['v_pore_uL']:.2f} μL，"
                f"当前注液比 E/P ≥ {ep_metrics['ratio_min']:.1f}×（≥ 3.0×），严禁宣称贴近/接近/匹配孔体积"
            )
            break
        for m in re.finditer(r"(?:低液量测试基准|低液量\s*/\s*泛液(?:基准)?|低液量泛液|贫液测试基准)", norm_text):
            sent_pre = _pre_clause(norm_text, m.start(), 36, split_commas=False)
            sent_post = _post_clause(norm_text, m.end(), 48, split_commas=False)
            if re.search(
                r"(?:不|非|严禁|禁止|不得|切勿|避免|并非|不是|未|无|勿|不属|废止)",
                _pre_clause(norm_text, m.start(), 28),
            ) or re.search(r"(?:原文|原方案|旧方案)", sent_pre) or re.search(
                r"(?:作废|误称|错误表述|纠正|不成立)", sent_post
            ):
                continue
            errors.append(
                f"实验方案注液量定性自相矛盾：当前注液比 E/P ≥ {ep_metrics['ratio_min']:.1f}×（≥ 3.0×，属减量富液/过量泛液），"
                "严禁使用“低液量测试基准”或“低液量/泛液”字样"
            )
            break

    fixed_1c_vals: list[float] = []
    for m in re.finditer(
        r"(?<![\d.])1(?:\.0+)?\s*C\s*(?:=|≡|对应|定义为|按|设为|取|[:：])\s*(?:约\s*)?"
        r"(\d+(?:\.\d+)?)\s*mA(?!h)\s*[·/]?\s*g(?:⁻¹|\^-?1)?",
        norm_text,
        re.I,
    ):
        pre = _pre_clause(norm_text, m.start(), 32, split_commas=False)
        post = _post_clause(norm_text, m.end(), 64, split_commas=False)
        if re.search(
            r"(?:不得|禁止|严禁|切勿|避免|而非|不按|不要|另有|对比|文献[^\n。；;]{0,12}口径|不采用|不用于本)",
            pre,
            re.I,
        ) or re.search(
            r"(?:仅作[^\n。；;]{0,12}(?:换算|备注|参考|对照)|不用于本|不采用|仅供参考|仅供换算)",
            post,
            re.I,
        ):
            continue
        fixed_1c_vals.append(float(m.group(1)))
    if len(fixed_1c_vals) >= 2 and (max(fixed_1c_vals) - min(fixed_1c_vals)) > 1.0:
        errors.append("实验方案 1 C 基准比电流口径前后不一致（混用多个名义比电流值）")

    has_dynamic_1c = False
    for pat in (
        r"(?:以|按|据|依)[^\n。；;]{0,24}(?:实测|首圈|本批|母体)[^\n。；;]{0,16}容量[^\n。；;]{0,12}(?:定义|标定|换算|确定|作为)[^\n。；;]{0,10}1(?:\.0+)?\s*C",
        r"(?<![\d.])1(?:\.0+)?\s*C[^\n。；;]{0,18}(?:随|按|依|由)[^\n。；;]{0,18}(?:实测|首圈|本批)[^\n。；;]{0,12}容量[^\n。；;]{0,10}(?:动态|浮动|重新|定义|标定)",
    ):
        for m in re.finditer(pat, norm_text, re.I):
            if re.search(
                r"(?:严禁|禁止|不得|切勿|避免|不以|非按|不改为|不采用|不将)",
                _pre_clause(norm_text, m.start(), 32, split_commas=False),
            ):
                continue
            has_dynamic_1c = True
            break
    if has_dynamic_1c and fixed_1c_vals:
        errors.append("实验方案混用固定名义 1 C 与按实测容量动态定义 1 C，倍率口径前后矛盾")
    elif has_dynamic_1c:
        errors.append("实验方案采用动态实测容量定义 1 C，缺少统一的名义 1 C = X mA·g⁻¹ 基准")

    if len(fixed_1c_vals) >= 1 and (max(fixed_1c_vals) - min(fixed_1c_vals)) <= 1.0:
        base_1c = fixed_1c_vals[0]
        mass_vals = [
            float(mm.group(1))
            for mm in re.finditer(
                r"(?:活性(?:物|物质)?质量|m_active|单片活性物质)[^\n。；;]{0,10}?(?:=|为|约)?\s*(\d+(?:\.\d+)?)\s*mg(?!/\s*cm)",
                norm_text,
                re.I,
            )
        ]
        if mass_vals and (max(mass_vals) - min(mass_vals)) / max(max(mass_vals), 1e-9) <= 0.02:
            m_mg = sum(mass_vals) / len(mass_vals)
            for im in re.finditer(
                r"(?<![\d.])(\d+(?:\.\d+)?)\s*C[^\n。；;]{0,18}?(?:绝对电流|对应电流|电流(?:=|为|约)?|即|（|\()\s*(?:I\s*=\s*)?(\d+(?:\.\d+)?)\s*mA(?!\s*[·/]?\s*(?:g|cm|h))",
                norm_text,
                re.I,
            ):
                rate_val = float(im.group(1))
                stated_i = float(im.group(2))
                expected_i = (m_mg / 1000.0) * base_1c * rate_val
                if expected_i > 0 and abs(stated_i - expected_i) / expected_i > 0.15:
                    errors.append(
                        f"实验方案绝对电流与活性质量×1C口径不一致（{rate_val:g} C 实写 {stated_i:g} mA，"
                        f"按 {m_mg:g} mg 与 1 C={base_1c:g} mA/g 应约为 {expected_i:.3g} mA）"
                    )
                    break

    if re.search(r"(?:完整|原|上述)方案第[一二三四五六七八九十\d]+节(?:排行)?表", norm_text):
        errors.append("实验方案包含不存在的内部章节引用（请改用 [设计方案原文](design_scheme.md) 或文内摘要表）")

    for m in re.finditer(
        r"(?:α|alpha)\s*[=＝≈]\s*0\.5[^\n。；;]{0,36}(?:实测|实现|达到|确保|保证)[^\n。；;]{0,18}\d+(?:\.\d+)?\s*(?:%|mAh)",
        norm_text,
        re.I,
    ):
        clause = norm_text[max(0, m.start() - 12):m.end() + 18]
        if not re.search(r"近似|外推|降级|参考|趋势|待验证|不作为|并非|不能", clause):
            errors.append("实验方案正文将 α=0.5 外推/降级估算升格为无保留定量指标，与证据等级不符")
            break
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
    from auto_battery_research.util.report_contract import _canonical_additive_name, _is_additives_locked
    locked = scheme.get("user_locked_components") or []
    anchors = scheme.get("design_anchors") or {}
    raw_additives = [str(a).strip() for a in (scheme.get("additives") or []) if str(a).strip()]
    additives = [_canonical_additive_name(a) for a in raw_additives]
    ep_metrics = compute_cathode_pore_metrics(scheme)
    add_locked = _is_additives_locked(scheme, anchors=anchors if isinstance(anchors, dict) else {}, target=task_goal)
    if locked or anchors or additives:
        lines += ["", "## 锁定配方与工艺锚点 (第 5 节必须 100% 一致，不得改写)", ""]
        if locked:
            lines.append(f"- 用户硬锁定组件（不得被备选方案反转主次）: {', '.join(str(x) for x in locked)}")
        if additives:
            add_list = ", ".join(dict.fromkeys(a for a in additives if a))
            if add_locked:
                lines.append(f"- 添加剂锁定与优化边界: 已锁定引入 {add_list}；剂量水平（0 vs x wt%）与化成匹配待第 5 节梯度实验优化，严禁写成“{add_list} 未锁定”")
            else:
                lines.append(
                    f"- 添加剂选定与筛选隔离边界（candidate_screening）: 候选界面添加剂为 {add_list}；"
                    "G0–G6 必须统一使用无添加剂基础电解液 E0，严禁将候选添加剂预混入 G0/G1 基础电解液；"
                    f"全部候选添加剂（{add_list}）纳入第 5.1 节 G7 与第 5.3 节筛选 DOE（覆盖各自 0 vs 1/2 wt% 单因素梯度及优胜二元组合 × 化成协议 A/B）。统一规范拼写为 LiDFOB（严禁 LIDFOB）。"
                )
        if isinstance(anchors, dict):
            for k, v in anchors.items():
                if k == "al_foil_um" and v is not None and v != "":
                    lines.append(f"- {k}: {format_scalar_or_range(v)} μm")
                elif k == "precursor_type" and v is not None and v != "":
                    lines.append(f"- {k}: {v}（锁定合成路线：{PRECURSOR_LABELS.get(str(v), str(v))}；严禁由对照/备选路线反转主次）")
                elif k == "electrolyte_volume_uL" and v is not None and v != "":
                    vol_str = format_scalar_or_range(v)
                    vol_range = parse_numeric_range(v)
                    vol_note = ""
                    if vol_range and vol_range[1] <= 80.0:
                        if ep_metrics and ep_metrics["ratio_min"] >= 3.0:
                            r_desc = (
                                f"{ep_metrics['ratio_min']:.1f}×"
                                if abs(ep_metrics["ratio_max"] - ep_metrics["ratio_min"]) < 0.05
                                else f"{ep_metrics['ratio_min']:.1f}–{ep_metrics['ratio_max']:.1f}×"
                            )
                            vol_note = f"（扣电过量/泛液测试基准，E/P ≈ {r_desc}；相较 100–120 μL 常规值属减量富液，严禁称为“低液量测试基准”）"
                        elif ep_metrics and ep_metrics["ratio_max"] < 3.0:
                            vol_note = "（低液量测试基准；常规参考 100–120 μL）"
                        else:
                            vol_note = "（扣电测试基准；常规参考 100–120 μL，须核算 E/P 后定性）"
                    lines.append(f"- {k}: {vol_str} μL{vol_note}")
                else:
                    lines.append(f"- {k}: {v}")

    area_cm2 = (anchors.get("cathode_area_cm2") if isinstance(anchors, dict) else None) or (ep_metrics["area_cm2"] if ep_metrics else None)
    lines += ["", "## 物理口径与一致性硬约束", ""]
    if ep_metrics:
        v_e_desc = (
            f"{ep_metrics['v_e_min_uL']:g} μL"
            if abs(ep_metrics["v_e_max_uL"] - ep_metrics["v_e_min_uL"]) < 1e-6
            else f"{ep_metrics['v_e_min_uL']:g}–{ep_metrics['v_e_max_uL']:g} μL"
        )
        ratio_desc = (
            f"{ep_metrics['ratio_min']:.1f}×"
            if abs(ep_metrics["ratio_max"] - ep_metrics["ratio_min"]) < 0.05
            else f"{ep_metrics['ratio_min']:.1f}–{ep_metrics['ratio_max']:.1f}×"
        )
        ep_line = (
            f"- 孔体积与注液比核对: 按 A={ep_metrics['area_cm2']:.3g} cm²、L={ep_metrics['L_um']:g} μm、porosity={ep_metrics['porosity']:g}，"
            f"单极片理论孔体积 V_pore ≈ {ep_metrics['v_pore_uL']:.2f} μL；设计注液量 {v_e_desc} 对应 E/P ≈ {ratio_desc}。"
        )
        if ep_metrics["ratio_min"] >= 3.0:
            ep_line += (
                f" 由于 E/P ≥ 3.0（当前约 {ratio_desc}），属于扣电过量/泛液测试条件（减量富液，非贫液/低液量），"
                "请直接表述为“扣电过量/泛液测试基准（减量富液，非贫液/低液量）”，严禁使用“低液量测试基准”、“低液量/泛液”或“贴近/接近/匹配孔体积”字样。"
            )
        lines.append(ep_line)

    # P0-4 & P2: 精确预计算面载量、压实密度、骨架密度与半电池锂过量倍数
    slurry_raw = anchors.get("slurry_ratio") if isinstance(anchors, dict) else None
    f_act = _parse_slurry_active_fraction(slurry_raw)
    active_loading = _extract_cathode_loading_mg_cm2(scheme, scheme_md or "", require_active_word=False)
    cath_geo = geometry.get("cathode") or {}
    cath_l_um = cath_geo.get("L_um") or ((effective.get("pe") or {}).get("L") * 1e6 if (effective.get("pe") or {}).get("L") else None)
    cath_eps = cath_geo.get("porosity") or (effective.get("pe") or {}).get("eps")
    if active_loading and f_act:
        total_loading = active_loading / f_act
        wrong_9_8 = active_loading * 9.0 / 8.0
        math_line = (
            f"- 面载量与压实密度精准核算: 活性面载量 = {active_loading:g} mg/cm²，浆料配比 {slurry_raw}（活性占比 f_act = {f_act:.4g}），"
            f"故极片总面载量 = {active_loading:g} / {f_act:.4g} = {total_loading:.3f} mg/cm²（严禁错算为 ×9/8 = {wrong_9_8:.2f} mg/cm²）"
        )
        if isinstance(cath_l_um, (int, float)) and float(cath_l_um) > 0:
            compaction = total_loading / (float(cath_l_um) * 0.1)
            wrong_comp = wrong_9_8 / (float(cath_l_um) * 0.1)
            math_line += (
                f"；按涂覆厚度 L = {float(cath_l_um):g} μm，极片整体压实密度 = {total_loading:.3f} / ({float(cath_l_um):g} × 0.1) "
                f"= {compaction:.3f} g/cm³（严禁错写 {wrong_comp:.2f} g/cm³）"
            )
            if isinstance(cath_eps, (int, float)) and 0 < float(cath_eps) < 1:
                skeleton = compaction / (1.0 - float(cath_eps))
                math_line += f"；按孔隙率 {float(cath_eps):g} 对应平均骨架密度 ≈ {skeleton:.2f} g/cm³"
        lines.append(math_line + "。")

    if str(scheme.get("anode") or "").strip().lower() == "li_metal":
        anode_geo = geometry.get("anode") or {}
        anode_l = anode_geo.get("L_um") or ((effective.get("ne") or {}).get("L") * 1e6 if (effective.get("ne") or {}).get("L") else None)
        if isinstance(anode_l, (int, float)) and float(anode_l) > 0 and active_loading:
            li_cap_cm2 = float(anode_l) * 0.0001 * 0.534 * 3861.0
            cath_key = str(scheme.get("cathode") or "").strip().lower()
            cap_map = {"lrmo": 250.0, "ncm811": 200.0, "ncm": 180.0, "lfp": 170.0, "lnmo": 147.0, "lco": 160.0, "nca": 200.0}
            nom_cap = None
            if isinstance(result.get("specific_capacity_mAh_g"), (int, float)) and float(result["specific_capacity_mAh_g"]) > 80:
                nom_cap = float(result["specific_capacity_mAh_g"])
            else:
                for ck, cv in cap_map.items():
                    if ck in cath_key:
                        nom_cap = cv
                        break
            if nom_cap:
                cath_areal = active_loading * nom_cap / 1000.0
                li_excess = li_cap_cm2 / cath_areal
                lines.append(
                    f"- 半电池锂过量倍数精准口径: {float(anode_l):g} μm 致密锂箔理论面容量约 {li_cap_cm2:.2f} mAh/cm²，"
                    f"相对正极 {active_loading:g} mg/cm²（按 {nom_cap:g} mAh/g 名义比容量计约 {cath_areal:.3f} mAh/cm²）"
                    f"对应约 {li_excess:.1f} 倍；正文必须明确标注为“半电池事后容量比估算（非全电池 N/P 设计输入）”。"
                )

    # P0-8: 动态收集所有涉及过渡金属及掺杂元素，强制 5.4 ICP 全覆盖
    all_elems = _extract_scheme_tm_dopants(
        [
            str((anchors.get("primary_formula") if isinstance(anchors, dict) else "") or ""),
            str(doc.get("final_answer") or ""),
            str(scheme_md or ""),
        ]
    )
    if all_elems:
        lines.append(
            f"- 全元素溶解与负极沉积覆盖硬约束: 本方案及对照组共涉及晶体/掺杂元素 {', '.join(all_elems)}，"
            f"第 5.4 节 ICP-OES/MS 与负极界面 XPS/沉积检测必须全覆盖 {', '.join(all_elems)}（严禁遗漏对照元素如 Cu 及其在负极表面沉积）。"
        )

    # P0-7: 明确列出未设设计锚点的硬件项
    unset_hw = []
    if not (isinstance(anchors, dict) and str(anchors.get("separator") or "").strip()):
        unset_hw.append("separator（隔膜型号）")
    if not (isinstance(anchors, dict) and anchors.get("al_foil_um") not in (None, "")):
        unset_hw.append("al_foil_um（正极铝箔厚度）")
    if unset_hw:
        lines.append(
            f"- 未锚定硬件真实声明: 当前设计未设立 {'、'.join(unset_hw)} 锚点，第 5 节必须标注为“未设设计锚点（由实验室入库台账固定同批次规格）”，"
            "严禁写为“与锚点一致”。"
        )

    lines.append(
        f"- 材料体系命名规范: 5.1–5.2 须同时保留选定体系代号（如正极 {scheme.get('cathode')} / 负极 {scheme.get('anode')}）与主推候选名义化学式。"
    )
    area_note = f"正极极片面积 {float(area_cm2):.3g} cm²、" if isinstance(area_cm2, (int, float)) else ""
    lines.append(
        f"- 1 C 唯一基准与电流核算: 5.1–5.6 必须锁定同一名义 `1 C = X mA·g⁻¹`（母体与修饰组共享同一 X），"
        f"并依据 {area_note}目标活性面载量换算单片活性物质质量 `m_active` 及绝对电流 `I_abs = m_active(g) × C_rate × X`；"
        "严禁混用两套 `1 C` 定义或写入“以本批母体实测容量定义 1 C”。"
    )
    lines.append(
        "- 交叉引用与表格-正文证据等级同步: 不得引用不存在的“完整方案第X节排行表”（请用 `[设计方案原文](design_scheme.md)` 或文内摘要表）；"
        "凡表格标注为降级/外推/单元素 DFT 近似（如 α=0.5 插值）的数据，正文不得升格为定量预期。"
    )
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
        lines += [
            "",
            f"## 审稿保留意见（共 {len(issues)} 条，非流程阻塞项；严禁写成“唯一最优”或已实证结论，第 5.5 节须建立逐条闭环状态表全覆盖 1..{len(issues)}）",
            "",
        ] + [f"- ({idx}) {i}" for idx, i in enumerate(issues, 1)]
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
    if doc.get("experiment_contract"):
        from .experiment_design import load, render, REPORT_FILENAME, require_current
        from .scheme_ledger import atomic_write
        recipe = load(task_dir)
        require_current(task_dir, doc)
        if not recipe or recipe.get("plan_hash") != doc.get("experiment_plan_hash"):
            raise ValueError("设计与 Stage 5B 实验方案不匹配")
        body = render(recipe, doc, result)
        record = {"fingerprint": input_fingerprint(doc, context, result, spec, scheme_md),
                  "plan_hash": recipe["plan_hash"], "text": body, "source": "stage4_projection"}
        atomic_write(Path(task_dir) / REPORT_FILENAME, json.dumps(record, ensure_ascii=False, indent=2))
        return {"text": body, "marker": (PLAN_START, PLAN_END), "source": "stage4_projection"}
    if doc.get("ledger_view"):
        return _generate_ledger_plan(mgr, task_dir, doc, context, result, spec, scheme_md)
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
    if doc.get("experiment_contract"):
        from .experiment_design import load, render, REPORT_FILENAME, require_current
        try:
            require_current(task_dir, doc)
            recipe = load(task_dir)
            if not recipe or recipe.get("plan_hash") != doc.get("experiment_plan_hash"):
                return ["实验推荐与设计不一致"]
            body = render(recipe, doc, result)
            record = json.loads(Path(task_dir, REPORT_FILENAME).read_text(encoding="utf-8"))
            fp = input_fingerprint(doc, context, result, spec, scheme_md)
            expected = f"{PLAN_START}\n{plan_fingerprint_marker(fp)}\n{body}\n{PLAN_END}"
            return [] if record.get("fingerprint") == fp and record.get("text") == body and expected in content else ["报告实验章节与 Stage 5B 方案不一致"]
        except (ValueError, OSError, TypeError) as exc:
            return [str(exc)]
    record = load_plan_record(task_dir)
    if not record:
        return ["未找到实验方案生成记录 experiment_plan.json"]
    current = input_fingerprint(doc, context, result, spec, scheme_md)
    if record.get("fingerprint") != current or plan_fingerprint_marker(current) not in content:
        return ["实验方案输入指纹与当前设计/证据/仿真不一致，需重新生成"]
    errors = validate_plan_text(record.get("text", ""), doc, context, target, tolerance)
    if doc.get("ledger_view"):
        from .scheme_ledger import render_experiment
        suggestion_errors = _validate_suggestions(record.get("suggestions", {}), doc["ledger_view"])
        if suggestion_errors:
            return suggestion_errors
        if record.get("text") != render_experiment(doc["ledger_view"], result or {}) + _render_suggestions(record.get("suggestions", {})):
            errors.append("实验章节包含不属于当前方案或归档候选的内容")
    expected_block = f"{PLAN_START}\n{plan_fingerprint_marker(current)}\n{record.get('text','')}\n{PLAN_END}"
    if expected_block not in content:
        errors.append("报告中的实验方案与保存记录不一致")
    return errors


def _render_suggestions(suggestions):
    from .report_narrative import render_suggestions
    return "\n\n" + render_suggestions(suggestions)


def _validate_suggestions(suggestions, data):
    from .scheme_ledger import validate_field
    if not isinstance(suggestions, dict):
        return ["候选建议必须是字段映射"]
    try:
        for key, row in suggestions.items():
            validate_field(key, row)
            if (data["parameters"].get(key) or {}).get("value") is not None:
                raise ValueError("候选建议不能替换当前参数")
            if row.get("basis") != "engineering_proposal":
                raise ValueError("候选不能冒充文献或实测")
            for name in ("method", "rationale", "acceptance"):
                if row.get(name) is not None and not isinstance(row[name], str):
                    raise ValueError("候选获取方式及理由必须是文字")
    except (ValueError, TypeError, AttributeError) as exc:
        return [str(exc)]
    return []


def _generate_ledger_plan(mgr, task_dir, doc, context, result, spec, scheme_md):
    """模型只提出缺失字段的结构化候选；当前参数与计算由程序渲染。"""
    from .scheme_ledger import render_experiment, validate_field, atomic_write
    fingerprint = input_fingerprint(doc, context, result, spec, scheme_md)
    record = load_plan_record(task_dir)
    if record and record.get("fingerprint") == fingerprint and record.get("source") == "scheme_ledger" and not _validate_suggestions(record.get("suggestions", {}), doc["ledger_view"]):
        expected = render_experiment(doc["ledger_view"], result or {}) + _render_suggestions(record.get("suggestions", {}))
        if record.get("text") == expected:
            return {"text": expected, "marker": (PLAN_START, PLAN_END), "source": "scheme_ledger"}
    from .report_narrative import missing_suggestions
    suggestions = missing_suggestions(doc["ledger_view"])
    client = _llm_client(mgr)
    if client is not None:
        for _ in range(2):
            try:
                raw = client.chat("你提出实验候选，仅输出 JSON 对象，键为方案字段路径，值包含 value/unit/basis/source/meaning/conditions。"
                    "还须包含 kind（value 或 acquisition）、rationale（影响与建议理由）、method（获取或筛选步骤）、acceptance（采用判据）。"
                    "没有可靠数值时 value 为 null，给出具体获取方法；不要为了完整性编造默认值。meaning 用清楚的中文参数名称。"
                    "只补 value 为 null 或不存在的字段，不改变已有值。basis 必须 engineering_proposal，source 为 LLM候选。"
                    "不得声称实测、仿真或目标已达成；没有依据可以返回空对象。最多十二项。",
                    json.dumps(doc["ledger_view"], ensure_ascii=False), temperature=0.1, trace_tag="report_recipe")
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
                candidate = json.loads(raw)
                if not isinstance(candidate, dict) or len(candidate) > 12:
                    raise ValueError("候选结构错误")
                for key, row in candidate.items():
                    validate_field(key, row)
                    if (doc["ledger_view"]["parameters"].get(key) or {}).get("value") is not None:
                        raise ValueError("候选扩展不能替换已有值")
                    if row.get("basis") != "engineering_proposal" or row.get("source") != "LLM候选":
                        raise ValueError("候选不能冒充文献或实测")
                    for name in ("rationale", "method", "acceptance"):
                        if row.get(name) is not None and not isinstance(row[name], str):
                            raise ValueError("候选说明必须为文字")
                suggestions.update(candidate)
                break
            except Exception:
                continue
    text = render_experiment(doc["ledger_view"], result or {}) + _render_suggestions(suggestions)
    record = {"fingerprint": fingerprint, "text": text, "suggestions": suggestions,
              "source": "scheme_ledger", "render_version": RENDER_VERSION, "generated_at": datetime.now().isoformat()}
    atomic_write(Path(task_dir) / PLAN_FILENAME, json.dumps(record, ensure_ascii=False, indent=2))
    return {"text": text, "marker": (PLAN_START, PLAN_END), "source": "scheme_ledger"}
