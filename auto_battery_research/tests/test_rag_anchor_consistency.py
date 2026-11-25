"""Stage 4 设计锚点提取、化学式规范化与一致性提示的通用回归测试."""

import json
from types import SimpleNamespace

import pytest

from lmllm.RAG.agents import ReviewerAgent, WriterAgent
from lmllm.RAG.prompts import REVIEWER_SYSTEM_PROMPT, WRITER_SYSTEM_PROMPT
from lmllm.RAG.rag_pipeline import RAGPipeline


def test_valence_normalization_preserves_sci_notation_and_units():
    raw = (
        "推荐主相为 Li₁.₁₇Na₀.₀₃[Mn₀.₅₈Zn₀.₀₂Ni²⁺₀.₁₆Ni³⁺₀.₀₄]O₂、"
        "对照 LiFe^2+0.5Fe^{3+}0.5PO4 与 O^{2-} / Mn<sup>4+</sup>；"
        "表征扩散系数为 D_Li = 2.5×10⁻¹⁴ m²·s⁻¹ (10^-14 cm^2 s^-1, 10^{-14} m²/s)，"
        "比容量 250 mAh·g⁻¹，电解液 1 mol/L LiPF₆ in EC+DMC。"
    )
    stripped = RAGPipeline._strip_valence_annotations(raw)
    translated = stripped.translate(RAGPipeline._SUB_TRANS)

    assert "Li1.17Na0.03[Mn0.58Zn0.02Ni0.16Ni0.04]O2" in translated
    assert "LiFe0.5Fe0.5PO4" in translated
    assert "O / Mn" in translated
    assert "2.5×10⁻¹⁴" in stripped
    assert "m²·s⁻¹" in stripped
    assert "10^-14" in stripped
    assert "10^{-14}" in stripped
    assert "mAh·g⁻¹" in stripped
    assert "EC+DMC" in stripped
    assert RAGPipeline._parse_sci_number(
        RAGPipeline._strip_valence_annotations("2.5×10⁻¹⁴")
    ) == pytest.approx(2.5e-14)
    assert RAGPipeline._parse_sci_number(
        RAGPipeline._strip_valence_annotations("10^-14")
    ) == pytest.approx(1e-14)


def test_normalize_formula_and_canonical_equivalence():
    f_raw = "Li₁.₁₇Na₀.₀₃[Mn₀.₅₈Zn₀.₀₂Ni²⁺₀.₁₆Ni³⁺₀.₀₄]O₂"
    assert (
        RAGPipeline._normalize_formula(f_raw)
        == "Li1.17Na0.03[Mn0.58Zn0.02Ni0.2]O2"
    )
    assert RAGPipeline._normalize_formula("LiFe²⁺₀.₈₅Fe³⁺₀.₁₅PO₄") == "LiFePO4"
    assert RAGPipeline._formulas_equivalent(
        f_raw, "Li1.17Na0.03(Mn0.58Zn0.02Ni0.20)O2"
    )
    assert RAGPipeline._formulas_equivalent(
        f_raw, "Li1.17Na0.03Mn0.58Zn0.02Ni0.2O2"
    )
    assert not RAGPipeline._formulas_equivalent(
        f_raw, "Li1.15Na0.03Mg0.02[Mn0.59Mg0.01Ni0.20]O2"
    )


def test_0bf069fa_structure_rank1_solid_state_and_range_calcination():
    question = "设计双位点共掺杂改进无钴富锂 Li1.2Mn0.6Ni0.2O2 正极，以抑制氧释放并提升 1 C 保持率"
    draft = """
1. **目标与设计路线**
- 基体：Li₁.₂Mn₀.₆Ni₀.₂O₂；总掺量 Σ = a + b（B 用 c_B 单列），上限 Σ ≤ 0.08（占 A+TM 阳离子总数 2.00 的 4 at%）。

---
2. **推荐组合与关键配方**

| 排名 | 共掺杂对 | 名义化学式 | 目标掺量 | 作用机制 |
| :---: | :---: | :--- | :---: | :--- |
| **1** | Zn–Na | **Li₁.₁₇Na₀.₀₃[Mn₀.₅₈Zn₀.₀₂Ni²⁺₀.₁₆Ni³⁺₀.₀₄]O₂** | 2.5 at% | 抑制层状向尖晶石相变与晶格氧释放 [p12] |
| 2 | Cu–Na | Li₁.₁₇Na₀.₀₃[Mn₀.₅₈Cu₀.₀₂Ni²⁺₀.₁₆Ni³⁺₀.₀₄]O₂ | 2.5 at% | 稳定氧骨架 [p15] |
| 3 | Mg–Na | Li₁.₁₅Na₀.₀₃Mg₀.₀₂[Mn₀.₅₉Mg₀.₀₁Ni₀.₂₀]O₂ | 3.0 at% | 柱撑 Li 层 |

- **唯一最优名义化学式**：Li₁.₁₇Na₀.₀₃[Mn₀.₅₈Zn₀.₀₂Ni²⁺₀.₁₆Ni³⁺₀.₀₄]O₂。
- **合成路线 A：固相法（主推）**
  - 步骤：化学计量混合前驱体 → 450 °C/4 h 预烧（空气去有机）→ 研磨并补加过量 5% Li₂CO₃ → 900–950 °C/10–12 h（空气或 O₂，升温速率 3 °C/min）→ 自然冷却。
  - 关键控制：严格控制烧结气氛与升温速率 [p18]。
- **合成路线 B：溶胶-凝胶法（对照）**
  - 步骤：柠檬酸络合溶胶凝胶 → 480 °C/5 h 预烧 → 750–800 °C/8–10 h 烧结对照。
- 扣电参数：15 μm Al 箔、Celgard 2500 隔膜、正极冲片 φ14 mm，主方案注液量 50 μL，活性物质:Super P:PVDF = 8:1:1。
"""
    anchors = RAGPipeline._extract_design_anchors(question, draft)

    assert (
        anchors["primary_formula"]
        == "Li1.17Na0.03[Mn0.58Zn0.02Ni0.16Ni0.04]O2"
    )
    assert RAGPipeline._formulas_equivalent(
        anchors["primary_formula"], "Li1.17Na0.03[Mn0.58Zn0.02Ni0.20]O2"
    )
    assert anchors["doping_upper_limit_mol_pct"] == 4.0
    assert anchors["precursor_type"] == "solid_state"
    assert anchors["calcination_temp_c"] == 925.0
    assert anchors["calcination_temp_range_c"] == "900-950"
    assert anchors["calcination_hours"] == 11.0
    assert anchors["calcination_hours_range"] == "10-12"
    assert anchors["al_foil_um"] == 15.0
    assert anchors["separator"] == "Celgard 2500"
    assert anchors["cathode_diameter_mm"] == 14.0
    assert anchors["electrolyte_volume_uL"] == "50"
    assert anchors["slurry_ratio"] == "8:1:1"


def test_markdown_rank1_priority_beats_earlier_overview_candidate():
    question = "推荐改进 Li1.2Mn0.6Ni0.2O2 的掺杂配方"
    draft = """
文献综述中常比较 Li₁.₁₅Na₀.₀₅Mn₀.₅₅Ni₀.₂₅O₂ 等对照样本 [p1]。

| 排序 | 方案 | 名义化学式 | 备注 |
| --- | --- | --- | --- |
| 2 | 备选 | Li₁.₁₆Mg₀.₀₄Mn₀.₅₈Ni₀.₁₈O₂ | 次优 |
| **1**（首选） | 主推 | Li₁.₁₇Na₀.₀₃[Mn₀.₅₇Ti₀.₀₃Ni²⁺₀.₁₆Ni³⁺₀.₀₄]O₂ | 最优 |
"""
    anchors = RAGPipeline._extract_design_anchors(question, draft)
    assert (
        anchors["primary_formula"]
        == "Li1.17Na0.03[Mn0.57Ti0.03Ni0.16Ni0.04]O2"
    )

    draft_no_table = """
背景段先对比了 Li₁.₁₅Na₀.₀₅Mn₀.₅₅Ni₀.₂₅O₂ 与基体 [p1]。
- **唯一最优名义化学式**：Li₁.₁₈K₀.₀₂[Mn₀.₅₈Zr₀.₀₂Ni₀.₂₀]O₂
"""
    anchors2 = RAGPipeline._extract_design_anchors(question, draft_no_table)
    assert anchors2["primary_formula"] == "Li1.18K0.02[Mn0.58Zr0.02Ni0.20]O2"


def test_ncm811_carbonate_not_triggered_by_solid_state_lithiation():
    question = "设计 Zr 掺杂改进高镍 NCM811 (LiNi0.8Co0.1Mn0.1O2) 正极方案"
    draft = """
| 排名 | 掺杂体系 | 化学式 |
| --- | --- | --- |
| 1 | Zr 掺杂 | LiNi²⁺₀.₃₅Ni³⁺₀.₄₃Co₀.₁₀Mn₀.₁₀Zr₀.₀₂O₂ |

- **合成路线 A（主推荐）：碳酸盐共沉淀 + 固相锂化**
  - 步骤：控制 (Ni,Co,Mn)CO3 共沉淀结晶，与 LiOH·H2O 混匀后固相锂化，500 °C 预烧 5 h，随后主煅烧 760 °C 保温 12 h（O2 气氛）。
- **合成路线 B（对照）：溶胶-凝胶法**
  - 步骤：柠檬酸络合后于 820 °C 烧结 10 h。
"""
    anchors = RAGPipeline._extract_design_anchors(question, draft)
    assert (
        anchors["primary_formula"] == "LiNi0.35Ni0.43Co0.10Mn0.10Zr0.02O2"
    )
    assert anchors["precursor_type"] == "carbonate_coprecipitation"
    assert anchors["calcination_temp_c"] == 760.0
    assert anchors["calcination_hours"] == 12.0
    assert "calcination_temp_range_c" not in anchors
    assert "calcination_hours_range" not in anchors


def test_lnmo_sol_gel_route_a_vs_solid_state_route_b():
    question = "优化高压尖晶石 LiNi0.5Mn1.5O4 (LNMO) 正极倍率与循环"
    draft = """
- **主推荐配方**：LiNi₀.₄₈Mg₀.₀₂Mn₁.₄₅Ti₀.₀₅O₄
- **合成路线 B（备选对照）：高温固相法**，于 900 °C 下烧结 15 h。
- **合成路线 A（主路线）：溶胶-凝胶法（首选）**
  - 柠檬酸-乙二醇体系形成均匀前驱体，450 °C 预烧 4 h 后于 750 °C 下煅烧 10 h 并空气退火控制氧缺位。
"""
    anchors = RAGPipeline._extract_design_anchors(question, draft)
    assert anchors["primary_formula"] == "LiNi0.48Mg0.02Mn1.45Ti0.05O4"
    assert anchors["precursor_type"] == "sol_gel"
    assert anchors["calcination_temp_c"] == 750.0
    assert anchors["calcination_hours"] == 10.0


def test_lfp_valence_split_fe_not_false_dopant():
    question = "改进磷酸铁锂 LiFePO₄ 正极低温倍率性能"
    draft = """
混价态中间体常写作 LiFe²⁺₀.₈₅Fe³⁺₀.₁₅PO₄，仅作对照基准说明。
主方案选定 Nb 掺杂体系 LiFe²⁺₀.₉₈Nb⁵⁺₀.₀₂PO₄。
- **路线 A（首选）：球磨固相法**
  - 碳包覆球磨混料后于 700 °C 煅烧 10 h（Ar/H2 保护）。
"""
    anchors = RAGPipeline._extract_design_anchors(question, draft)
    assert anchors["primary_formula"] == "LiFe0.98Nb0.02PO4"
    assert anchors["precursor_type"] == "solid_state"
    assert anchors["calcination_temp_c"] == 700.0
    assert anchors["calcination_hours"] == 10.0


def test_comma_separated_precalcination_does_not_mask_main_calcination():
    question = "改进 NCM811 (LiNi0.8Co0.1Mn0.1O2) 正极"
    draft_cn = (
        "主推 LiNi0.78Co0.10Mn0.10Al0.02O2；"
        "合成路线 A（主推）：碳酸盐共沉淀，450 °C/4 h 预烧，主烧 850 °C/10 h。"
    )
    anchors_cn = RAGPipeline._extract_design_anchors(question, draft_cn)
    assert anchors_cn["calcination_temp_c"] == 850.0
    assert anchors_cn["calcination_hours"] == 10.0

    draft_en = (
        "Primary candidate LiNi0.78Co0.10Mn0.10Al0.02O2. "
        "Route A solid-state synthesis: 450 °C/4 h pre-calcination, 850 °C/10 h calcination."
    )
    anchors_en = RAGPipeline._extract_design_anchors(question, draft_en)
    assert anchors_en["calcination_temp_c"] == 850.0
    assert anchors_en["calcination_hours"] == 10.0


def test_extract_scheme_additives_word_boundary():
    class StubRelationEngine:
        @staticmethod
        def extract_entities(text: str):
            return {"cathode": ["lrmo"], "electrolyte": ["carbonate"]}

    pipe = RAGPipeline.__new__(RAGPipeline)
    pipe.relation_engine = StubRelationEngine()

    draft_no_additive = (
        "The synergistic effect significantly reduces surface defect formation "
        "without introducing extra electrolyte additives."
    )
    scheme_none = pipe._extract_scheme("设计 Li1.2Mn0.6Ni0.2O2 方案", {}, draft_no_additive)
    assert scheme_none is not None
    assert "additives" not in scheme_none

    draft_with_additive = "电解液拟引入 1% FEC 与 VC 作为候选成膜添加剂。"
    scheme_with = pipe._extract_scheme("设计 Li1.2Mn0.6Ni0.2O2 方案", {}, draft_with_additive)
    assert scheme_with is not None
    assert scheme_with["additives"] == ["FEC", "VC"]
    assert scheme_with["design_anchors"]["additive_status"] == "candidate_screening"


def test_extract_scheme_additives_canonical_casing_and_status():
    class StubRelationEngine:
        @staticmethod
        def extract_entities(text: str):
            return {"cathode": ["ncm811"], "electrolyte": ["carbonate"]}

    pipe = RAGPipeline.__new__(RAGPipeline)
    pipe.relation_engine = StubRelationEngine()

    # 1. Candidate additives only in draft (including mixed/uppercase LIDFOB, libob, dtd) + LiPF6/LiFSI salts
    q_unlocked = "设计高镍三元 LiNi0.8Mn0.1Co0.1O2 体系，采用 1 mol/L LiPF6/EC+DMC 电解液"
    d_candidates = "界面对照拟筛选 1% LIDFOB、0.5% libob 与 1% dtd 作为成膜添加剂，对比 1.5 M LiFSI DME/TTE。"
    scheme_cand = pipe._extract_scheme(q_unlocked, {}, d_candidates)
    assert scheme_cand is not None
    assert scheme_cand["additives"] == ["LiDFOB", "LiBOB", "DTD"]
    assert "LIDFOB" not in scheme_cand["additives"]
    assert scheme_cand["design_anchors"]["additive_status"] == "candidate_screening"

    # 2. Additive explicitly requested with locked dose in user_query -> locked
    q_locked = "设计 LiNi0.5Mn1.5O4 高压体系，添加 1 wt% LiDFOB 与 FEC 抑制过渡金属溶出"
    d_locked = "正文推荐使用 1 wt% LiDFOB 与 1% FEC 改善 CEI 稳定性。"
    scheme_locked = pipe._extract_scheme(q_locked, {}, d_locked)
    assert scheme_locked is not None
    assert scheme_locked["additives"] == ["FEC", "LiDFOB"]
    assert scheme_locked["design_anchors"]["additive_status"] == "locked"

    # 2b. user_query mentions candidate additive names as open parameters (plus unrelated %)
    #     while draft_text explicitly marks them as unquantified screening candidates -> candidate_screening
    q_open_param = (
        "设计高电压正极体系（1 C 循环 200 周 ≥ 85% 保持率），"
        "请给出主推改性配方与添加剂 FEC/VC/LiDFOB 用量探索范围。"
    )
    d_open_param = (
        "界面优化方面，已列入方案候选添加剂：FEC、VC、LiDFOB，"
        "具体比例与化成制度待实验定量筛选。"
    )
    scheme_open = pipe._extract_scheme(q_open_param, {}, d_open_param)
    assert scheme_open is not None
    assert scheme_open["additives"] == ["FEC", "VC", "LiDFOB"]
    assert scheme_open["design_anchors"]["additive_status"] == "candidate_screening"

    # 2c. user_query names additive without dose AND draft_text does NOT mark it candidate/unquantified -> locked
    q_named_no_cand = "设计 LiNi0.8Co0.1Mn0.1O2 体系，采用常规碳酸酯并含 FEC 修饰负极界面"
    d_named_no_cand = "方案直接采用含 FEC 的电解液改善负极 SEI 膜均匀性。"
    scheme_named_locked = pipe._extract_scheme(q_named_no_cand, {}, d_named_no_cand)
    assert scheme_named_locked is not None
    assert scheme_named_locked["design_anchors"]["additive_status"] == "locked"

    # 3. Canonicalization helpers normalize legacy LIDFOB/LIBOB/LIPF6/LIFSI strings
    assert RAGPipeline._canonicalize_additive("LIDFOB") == "LiDFOB"
    assert RAGPipeline._canonicalize_additive("libob") == "LiBOB"
    assert RAGPipeline._canonicalize_additive("lipf6") == "LiPF6"
    assert RAGPipeline._canonicalize_additive("lifsi") == "LiFSI"
    assert RAGPipeline._normalize_legacy_additives("含 LIDFOB 与 LIBOB") == "含 LiDFOB 与 LiBOB"
    assert RAGPipeline._normalize_legacy_additives(["FEC", "LIDFOB"]) == ["FEC", "LiDFOB"]


def test_extract_design_anchors_doping_limits_and_sites():
    anchors_cap = RAGPipeline._extract_design_anchors(
        "设计双位点掺杂高镍正极方案，总掺杂上限 ≤ 3.0 mol%（以总阳离子为基准）",
        "位点参数：单点掺杂x_A(Mg) ≤ 1.5 mol%，y_B(Ti) 不超过 2.0 mol%。",
    )
    assert anchors_cap["doping_upper_limit_mol_pct"] == 3.0
    assert anchors_cap["a_site_upper_limit_mol_pct"] == 1.5
    assert anchors_cap["b_site_upper_limit_mol_pct"] == 2.0

    anchors_range = RAGPipeline._extract_design_anchors(
        "设计双位点共掺杂高电压尖晶石正极，总掺杂摩尔分数上限为 4.5 mol%",
        "**x_A(Na)** 搜索区间 0.5–2.5 mol%；**y_B(Zn)** 搜索区间 1.0–3.5 mol%。",
    )
    assert anchors_range["doping_upper_limit_mol_pct"] == 4.5
    assert anchors_range["a_site_upper_limit_mol_pct"] == 2.5
    assert anchors_range["b_site_upper_limit_mol_pct"] == 3.5

    # Cross-sentence isolation: site limit in next sentence must not become doping_upper_limit_mol_pct
    anchors_isolated = RAGPipeline._extract_design_anchors(
        "总掺杂比例设为 1:2。单点上限 x_A ≤ 2.0 mol%。",
        "正极材料采用常规烧结条件 850 ℃ 保温 12 h。",
    )
    assert "doping_upper_limit_mol_pct" not in anchors_isolated
    assert anchors_isolated["a_site_upper_limit_mol_pct"] == 2.0

    # Control-group table row must not shadow primary site search range
    anchors_ctrl_ignored = RAGPipeline._extract_design_anchors(
        "设计双位点共掺杂正极材料方案",
        "| 对照 | Zn-K | — | x_A ≤0.5 mol% / y_B=3.75 mol% | 对比组 |\n"
        "**掺量寻优**：**x_A(Na) 搜索区间 1–5 mol%，最优 1.5 mol%**；**y_B 搜索区间 1–5 mol%**。",
    )
    assert anchors_ctrl_ignored["a_site_upper_limit_mol_pct"] == 5.0
    assert anchors_ctrl_ignored["b_site_upper_limit_mol_pct"] == 5.0


def test_prompts_and_agents_stage4_consistency_guidance():
    for keyword in (
        "固相法",
        "已列入方案候选添加剂，具体比例与化成制度待实验定量筛选",
        "跨体系推断/无直接二元实验",
        "贴近孔体积",
        "1 C",
        "主推候选名义化学式（待实验验证）",
        "总阳离子",
        "总面载量 = 活性面载量 / 活性物质占比",
        "压实密度 = 总面载量 / 极片厚度",
        "扣电过量/泛液测试基准（相较常规 100–120 μL 属减量富液，绝非贫液/低液量）",
        "低液量测试基准",
        "E/P = V_E / V_pore >= 3×",
        "稀释风险",
        "ICP-OES/XPS",
    ):
        assert keyword in WRITER_SYSTEM_PROMPT
        assert keyword in REVIEWER_SYSTEM_PROMPT

    captured = []

    class CaptureLLM:
        available = True

        def chat(self, system_prompt, user_prompt, temperature=0.2, trace_tag="", *, json_mode=False):
            captured.append((trace_tag, system_prompt, user_prompt))
            if trace_tag == "reviewer":
                return json.dumps(
                    {
                        "issues": [],
                        "confidence": "high",
                        "error_type": "none",
                    }
                )
            from auto_battery_research.util.design_structure import SECTION_IDS
            return json.dumps({"version": 2, "parameters": {}, "sections": {key: {"body": "对应证据仍需核对适用体系与条件。[p1]"} for key in SECTION_IDS}})

    scheme = {
        "cathode": "lrmo",
        "electrolyte": "carbonate",
        "user_locked_components": ["electrolyte"],
        "additives": ["FEC"],
        "design_anchors": {
            "primary_formula": "Li1.17Na0.03[Mn0.58Zn0.02Ni0.16Ni0.04]O2",
            "precursor_type": "solid_state",
            "calcination_temp_c": 925.0,
            "calcination_temp_range_c": "900-950",
        },
    }
    evidence = [{"passage_id": "p1", "source": "doc", "text": "Zn-Na co-doped LRMO"}]
    plan = {"answer_outline": ["1", "2", "3", "4", "5"], "needs_reasoning": False}

    writer = WriterAgent(CaptureLLM())
    res_w = writer.run("设计 Li1.2Mn0.6Ni0.2O2 方案", plan, evidence, scheme=scheme)
    res_r = writer.revise(
        "设计 Li1.2Mn0.6Ni0.2O2 方案",
        plan,
        evidence,
        res_w["draft_answer"],
        feedback=["请统一 Rank #1 化学式"],
        scheme=scheme,
    )
    assert res_r == res_w["draft_answer"]  # 纯字符串审核摘要不能获得正文写权限。
    reviewer = ReviewerAgent(CaptureLLM())
    rev = reviewer.run("设计 Li1.2Mn0.6Ni0.2O2 方案", evidence, res_w["draft_answer"], scheme=scheme)
    assert rev["confidence"] == "high"

    assert len(captured) == 2
    tag0, sys0, user0 = captured[0]
    tag1, sys1, user1 = captured[1]
    assert (tag0, tag1) == ("writer", "reviewer")
    assert sys0 is WRITER_SYSTEM_PROMPT
    assert sys1 is REVIEWER_SYSTEM_PROMPT
    for prompt_body in (user0, user1):
        assert "已列入方案候选添加剂，具体比例与化成制度待实验定量筛选" in prompt_body
        assert "严禁误标为「未锁定」或「未选用」" in prompt_body
        assert "跨体系推断/无直接二元实验" in prompt_body
        assert "贴近孔体积" in prompt_body
        assert "solid_state" in prompt_body
        assert "主推候选名义化学式（待实验验证）" in prompt_body
        assert "总面载量 = 活性面载量 / 活性物质占比" in prompt_body
        assert "扣电过量/泛液测试基准（相较常规 100–120 μL 属减量富液，绝非贫液/低液量）" in prompt_body

    # Legacy LIDFOB canonicalization and locked vs candidate_screening wording in note builder
    from src.lmllm.RAG.agents import _build_scheme_consistency_note

    note_cand = _build_scheme_consistency_note(
        {
            "additives": ["FEC", "LIDFOB"],
            "design_anchors": {"additive_status": "candidate_screening"},
        },
        design_mode=True,
    )
    assert "LiDFOB" in note_cand
    assert "LIDFOB" not in note_cand
    assert "已列入方案候选添加剂，具体比例与化成制度待实验定量筛选" in note_cand

    note_locked = _build_scheme_consistency_note(
        {
            "additives": ["FEC", "LIDFOB"],
            "design_anchors": {"additive_status": "locked"},
        },
        design_mode=True,
    )
    assert "LiDFOB" in note_locked
    assert "LIDFOB" not in note_locked
    assert "用户课题已显式指定添加剂" in note_locked
