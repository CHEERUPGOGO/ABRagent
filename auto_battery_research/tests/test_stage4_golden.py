"""Stage 4 端到端 Golden Test — 产品核心链路的确定性回放与真实链路对照.

双层策略:
1. 离线层 (默认运行): 脚本化 LLM (类级 monkeypatch LLMClient.chat/available) 驱动
   真实 Planner/Writer/Reviewer 代码路径 + 真实 BM25 检索 (tfidf 模式, 种子语料)
   + 真实 RelationEngine C1-C8 校验 + 真实 AbrRagAdapter 契约落盘 + 真实
   RAGDesignChecker 门禁 —— 输入、检索证据、规则审计、方案 JSON、报告全链路可追溯。
2. external 层 (@pytest.mark.external, 默认 deselect): 真实 Chroma 检索 + 真实
   LLM 的完整链路 golden, 产物 trace 落盘 output/tasks/ 供回放比对。

校准说明 (fixture 数值依据, 勿随意改动):
- RelationEngine 候选表 NCM811 (3.8 V x 200 mAh/g) + li_metal 折算 0.50
  → estimate_scheme_energy = 380 Wh/kg; ENERGY_TOLERANCE=0.35 → 声称 400/410 均通过。
- 已验证的硬违规组合是 li_metal + carbonate_ec (常规碳酸酯) —— 原计划中的
  "石墨 + 420 Wh/kg" 在 lhce 体系下并非硬违规, 故负面对照采用 li_metal+碳酸酯。
"""

import json
import os
import pytest

pytest.importorskip("langchain_chroma", reason="RAGPipeline 构造 MultiRetrieval 需要 [rag] extra")
pytest.importorskip("langchain_ollama", reason="RAGPipeline 构造 MultiRetrieval 需要 [rag] extra")

from src.lmllm.RAG.rag_pipeline import RAGPipeline
from src.lmllm.RAG.llm_client import LLMClient
from src.lmllm.RAG.prompts import (
    PLANNER_SYSTEM_PROMPT,
    WRITER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
)
from src.lmllm.RAG.relation_engine import RULES_VERSION
from auto_battery_research.tools.rag_adapter import AbrRagAdapter
from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker


GOLDEN_GOAL = "设计400Wh/kg高比能液态锂金属电池方案"

# ────────────────────────── 固定种子语料 (全锂电体系) ──────────────────────────

GOLDEN_PASSAGES = [
    {
        "passage_id": "golden-P1",
        "source": "golden_corpus/cathode_electrochemical.md",
        "text": "NCM811 高镍三元正极在 2.8-4.3 V 电压窗口内 0.1C 放电比容量约 200 mAh/g，4.3 V 截止电压下层状结构保持稳定。",
        "metadata": {"label": "电化学性能", "component": "cathode", "doi": "10.1000/golden-p1"},
    },
    {
        "passage_id": "golden-P2",
        "source": "golden_corpus/cathode_single_crystal.md",
        "text": "单晶 NCM811 正极颗粒抑制微裂纹，300 周循环容量保持率超过 86%，高镍体系需要匹配稳定界面。",
        "metadata": {"label": "电化学性能", "component": "cathode", "doi": "10.1000/golden-p2"},
    },
    {
        "passage_id": "golden-P3",
        "source": "golden_corpus/electrolyte_formulation.md",
        "text": "局部高浓度电解液 LHCE 采用 1.5 M LiFSI 溶于 DME/TTE (体积比 1:2)，兼顾阻燃性与浸润性，是锂金属电池的候选电解液。",
        "metadata": {"label": "工作方案与策略", "component": "electrolyte", "doi": "10.1000/golden-p3"},
    },
    {
        "passage_id": "golden-P4",
        "source": "golden_corpus/anode_interface_passivation.md",
        "text": "锂金属负极在 LHCE 中形成富 LiF 钝化层，抑制枝晶生长，锂金属负极界面稳定性显著提升。",
        "metadata": {"label": "材料属性与表征", "component": "anode", "doi": "10.1000/golden-p4"},
    },
    {
        "passage_id": "golden-P5",
        "source": "golden_corpus/anode_cycling.md",
        "text": "超薄锂金属负极软包电芯 0.5C 循环 300 周容量保持率 86%，锂金属利用率与沉积均匀性是关键。",
        "metadata": {"label": "电化学性能", "component": "anode", "doi": "10.1000/golden-p5"},
    },
    {
        "passage_id": "golden-P6",
        "source": "golden_corpus/electrolyte_interface_cei.md",
        "text": "LHCE 在高镍正极表面构筑低阻抗 CEI 膜，降低 NCM811 界面副反应，正极-电解液相容性改善。",
        "metadata": {"label": "材料属性与表征", "component": "electrolyte", "doi": "10.1000/golden-p6"},
    },
    {
        "passage_id": "golden-P7",
        "source": "golden_corpus/anode_thin_foil.md",
        "text": "50 μm 超薄锂金属箔负极配合高面载量正极，电芯级质量能量密度可逼近 400 Wh/kg 量级。",
        "metadata": {"label": "工作方案与策略", "component": "anode", "doi": "10.1000/golden-p7"},
    },
    {
        "passage_id": "golden-P8",
        "source": "golden_corpus/cell_level_energy.md",
        "text": "NCM811 与锂金属负极匹配局部高浓度电解液的电芯级能量密度报道集中在 350-420 Wh/kg 区间。",
        "metadata": {"label": "电化学性能", "component": "cell", "doi": "10.1000/golden-p8"},
    },
]
SEEDED_IDS = {p["passage_id"] for p in GOLDEN_PASSAGES}

# ────────────────────────── 脚本化 LLM 输出 (确定性回放) ──────────────────────────

GOLDEN_PLAN = {
    "task_understanding": "面向400Wh/kg级高比能液态锂金属电池的材料筛选与方案设计",
    "retrieval_queries": [
        "NCM811 高镍正极 放电比容量 电压窗口",
        "锂金属负极 枝晶 抑制 界面稳定性",
        "局部高浓度电解液 LHCE LiF 钝化层 配方",
    ],
    "answer_outline": ["目标与设计路线", "推荐材料组合", "预期关键性能指标", "可行性依据", "风险与数据缺口"],
    "focus_labels": [],
    "focus_component": None,
    "needs_reasoning": False,
    "db_type": "literature",
}

GOLDEN_WRITER_DRAFT = """## 目标与设计路线
面向 400 Wh/kg 级高比能液态锂金属电池，采用高镍三元正极匹配超薄锂金属负极与局部高浓度电解液 (LHCE) 的技术路线。[golden-P1]

---

## 推荐材料组合与关键配方
- 正极: NCM811 单晶高镍三元 (Ni 含量 >= 80%)
- 负极: 锂金属 (li_metal) 超薄锂箔 50 μm
- 电解液: LHCE 局部高浓度电解液 1.5 M LiFSI DME/TTE 稀释比 1:2，添加 FEC 2 wt% [golden-P3]

---

## 预期关键性能指标
- 电芯级质量能量密度: 410 Wh/kg (0.5C 放电, 2.8-4.3 V 电压窗口)
- 正极 0.1C 放电比容量: 200 mAh/g
- 300 周循环容量保持率: > 86% [golden-P5]

---

## 可行性依据与机理
[golden-P1] NCM811 在 4.3 V 截止电压下层状结构保持稳定; [golden-P4] 局部高浓度电解液在锂金属表面诱导形成富 LiF 钝化层，抑制枝晶生长; [golden-P6] LHCE 与高镍正极界面的 CEI 膜降低界面阻抗。

---

## 风险与数据缺口
- 高镍正极的产气与存储稳定性需在高温循环条件下进一步验证
- 超薄锂金属负极的大面容量均匀沉积数据缺口
"""

# 负面对照 1: 锂金属 + 常规碳酸酯电解液 (已验证的 C 规则硬违规组合)
BAD_ELECTROLYTE_DRAFT = """## 目标与设计路线
面向 400 Wh/kg 级高比能液态锂金属电池，采用高镍三元正极匹配锂金属负极与常规碳酸酯电解液。[golden-P1]

---

## 推荐材料组合与关键配方
- 正极: NCM811 单晶高镍三元
- 负极: 锂金属 (li_metal) 超薄锂箔
- 电解液: 常规碳酸酯电解液 1.0 M LiPF6 in EC/DMC

---

## 预期关键性能指标
- 电芯级质量能量密度: 410 Wh/kg (0.5C 放电, 2.8-4.3 V 电压窗口)
- 300 周循环容量保持率: > 80% [golden-P5]

---

## 可行性依据与机理
[golden-P1] NCM811 在 4.3 V 截止电压下层状结构保持稳定。

---

## 风险与数据缺口
- 常规碳酸酯体系与锂金属负极的界面副反应风险
"""

# 负面对照 2: 全文不出现任何电解液实体 → scheme 缺 electrolyte
NO_ELECTROLYTE_DRAFT = """## 目标与设计路线
面向 400 Wh/kg 级高比能液态锂金属电池，先确定正负极体系，组分筛选分阶段推进。[golden-P1]

---

## 推荐材料组合与关键配方
- 正极: NCM811 单晶高镍三元
- 负极: 锂金属 (li_metal) 超薄锂箔

---

## 预期关键性能指标
- 电芯级质量能量密度: 410 Wh/kg (0.5C 放电, 2.8-4.3 V 电压窗口)
- 300 周循环容量保持率: > 86% [golden-P5]

---

## 可行性依据与机理
[golden-P1] NCM811 在 4.3 V 截止电压下层状结构保持稳定; [golden-P4] 负极界面钝化层抑制枝晶生长。

---

## 风险与数据缺口
- 第三组分 (隔离体系) 待下一阶段补充筛选
"""


def _install_scripted_llm(monkeypatch, writer_draft: str):
    """类级 monkeypatch LLMClient: 走真实 Agent 代码路径的确定性脚本回放.

    按 system_prompt 对象身份分发 (agents.py 直接传 prompts 模块常量);
    每次 LLM 调用记录到调用日志，供溯源断言。
    """
    call_log = []

    def fake_chat(self, system_prompt, user_prompt, temperature=None):
        if system_prompt is PLANNER_SYSTEM_PROMPT:
            call_log.append({"agent": "planner", "temperature": temperature, "prompt_head": user_prompt[:120]})
            return json.dumps(GOLDEN_PLAN, ensure_ascii=False)
        if system_prompt is WRITER_SYSTEM_PROMPT:
            call_log.append({"agent": "writer", "temperature": temperature, "prompt_head": user_prompt[:120]})
            return writer_draft
        if system_prompt is REVIEWER_SYSTEM_PROMPT:
            call_log.append({"agent": "reviewer", "temperature": temperature, "prompt_head": user_prompt[:120]})
            return json.dumps(
                {"issues": [], "revised_answer": writer_draft, "confidence": "high"},
                ensure_ascii=False,
            )
        raise AssertionError(f"脚本化 LLM 收到未识别的 system_prompt: {str(system_prompt)[:80]}")

    monkeypatch.setattr(LLMClient, "available", property(lambda self: True))
    monkeypatch.setattr(LLMClient, "chat", fake_chat)
    return call_log


def _build_seeded_pipeline(tmp_path):
    """构造离线确定性 RAGPipeline: tfidf 模式 + 种子语料 + 隔离的空 Chroma 目录."""
    pipeline = RAGPipeline(
        chroma_dir=str(tmp_path / "chroma_glit_empty"),
        chroma_collection="golden_stage4",
        ebook_chroma_dir=str(tmp_path / "ebook_glit_empty"),
        ebook_collection="golden_stage4_ebook",
        retrieval_mode="tfidf",
        api_key="offline-dummy",
    )
    for p in GOLDEN_PASSAGES:
        pipeline.kb.add_passage(
            passage_id=p["passage_id"], text=p["text"], source=p["source"], metadata=dict(p["metadata"])
        )
    pipeline.kb.build_index()
    return pipeline


class _StubManager:
    """RAGDesignChecker 所需的最小 StageManager 桩 (课题目录 + 非历史课题)."""

    def __init__(self, task_dir, goal, root_dir):
        self._task_dir = task_dir
        self.target_goal = goal
        self.root_dir = root_dir

    def get_task_output_dir(self, goal=None):
        return self._task_dir

    @property
    def is_legacy_task(self):
        return False


def _gate_check(task_dir, goal, root_dir):
    """用真实 RAGDesignChecker 对课题目录产物执行 Stage 4 门禁."""
    checker = RAGDesignChecker()
    checker.on_init(
        stage_manager=_StubManager(task_dir, goal, root_dir),
        stage_info={"id": 4, "key": "multi_agent_rag_design", "name": "多智能体 RAG 方案设计"},
        config={"paths": {"output_dir": str(task_dir)}},
    )
    return checker.do_check()


def _run_adapter(pipeline, task_dir, goal=GOLDEN_GOAL):
    adapter = AbrRagAdapter(config={"paths": {}}, pipeline=pipeline)
    return adapter.run_rag_design(target_query=goal, task_dir=task_dir)


# ────────────────────────── 离线 golden 主链路 ──────────────────────────


def test_offline_golden_approved_contract(tmp_path, monkeypatch):
    """正向链路: 脚本化 LLM + 真实检索/规则/适配器/门禁 → APPROVED 契约全量可追溯."""
    call_log = _install_scripted_llm(monkeypatch, GOLDEN_WRITER_DRAFT)
    pipeline = _build_seeded_pipeline(tmp_path)
    task_dir = tmp_path / "task_approved"

    res = _run_adapter(pipeline, task_dir, GOLDEN_GOAL)

    # 1. 适配器结果: APPROVED
    assert res["success"] is True, f"golden 链路应通过: {res.get('error')}"
    assert res["review_status"] == "APPROVED"

    # 2. 真实 Agent 代码路径被实际执行 (脚本化的是 LLM 应答, 不是 Agent 本身)
    agents_called = [c["agent"] for c in call_log]
    assert "planner" in agents_called and "writer" in agents_called and "reviewer" in agents_called

    # 3. design_scheme.json 契约: 结构化配方 + 证据溯源 + 规则审计 + 版本指纹
    contract = json.loads((task_dir / "design_scheme.json").read_text(encoding="utf-8"))
    assert contract["review_status"] == "APPROVED"
    assert contract["scheme"]["cathode"] == "NCM811"
    assert contract["scheme"]["anode"] == "li_metal"
    assert contract["scheme"]["electrolyte"] == "lhce"
    assert len(contract["evidence"]) >= 1
    for ev in contract["evidence"]:
        assert ev["passage_id"] in SEEDED_IDS, f"证据越界 (非种子语料): {ev.get('passage_id')}"
    assert contract["rule_checks"]["rule_checks"]["violations"] == []
    assert contract["rule_checks"]["rule_checks"]["rejects"] == []
    assert contract["provenance"]["rules_version"] == RULES_VERSION
    assert len(contract["provenance"]["corpus"]["manifest_hash"]) == 32

    # 4. rag_result.json: 规划/检索日志/Writer/Reviewer 全链路在案
    rag_raw = json.loads((task_dir / "rag_result.json").read_text(encoding="utf-8"))
    logged_queries = {log["query"] for log in rag_raw["retrieval"].get("search_logs", [])}
    assert set(GOLDEN_PLAN["retrieval_queries"]) <= logged_queries
    assert rag_raw["writer_output"]["draft_answer"] == GOLDEN_WRITER_DRAFT
    assert rag_raw["reviewer_output"]["confidence"] == "high"
    assert rag_raw["confidence"] in ("high", "medium")

    # 5. research_context.json 镜像 (复现性快照)
    ctx = json.loads((task_dir / "research_context.json").read_text(encoding="utf-8"))
    assert ctx["review_status"] == "APPROVED"
    assert ctx["provenance"]["rules_version"] == RULES_VERSION

    # 6. 门禁闭环: 真实 RAGDesignChecker 通过 (课题目录产物 + 非历史课题)
    passed, diag = _gate_check(task_dir, GOLDEN_GOAL, tmp_path)
    assert passed is True, f"Stage 4 门禁应通过: {diag.get('error_code')} {diag.get('error')}"


def test_offline_golden_deterministic_replay(tmp_path, monkeypatch):
    """确定性回放: 同输入两次完整运行 → 方案/证据集合/语料指纹完全一致."""
    _install_scripted_llm(monkeypatch, GOLDEN_WRITER_DRAFT)

    runs = []
    for i in (1, 2):
        pipeline = _build_seeded_pipeline(tmp_path / f"run{i}")
        task_dir = tmp_path / f"run{i}" / "task"
        res = _run_adapter(pipeline, task_dir, GOLDEN_GOAL)
        assert res["success"] is True
        contract = json.loads((task_dir / "design_scheme.json").read_text(encoding="utf-8"))
        runs.append(
            {
                "scheme": contract["scheme"],
                "evidence_ids": sorted(e["passage_id"] for e in contract["evidence"]),
                "manifest_hash": contract["provenance"]["corpus"]["manifest_hash"],
                "rules_version": contract["provenance"]["rules_version"],
                "confidence": contract["confidence"],
                "review_status": contract["review_status"],
            }
        )

    assert runs[0] == runs[1], "golden 输入必须产出逐字节等价的方案与证据集合 (时间戳除外)"


# ────────────────────────── 负向对照 (门禁真拦截, 非橡皮章) ──────────────────────────


def test_offline_golden_rejects_rule_violation(tmp_path, monkeypatch):
    """负向 1: Writer 力推 锂金属+常规碳酸酯 → RelationEngine 硬违规,
    即使脚本化 Reviewer 自信满满 (high) 也被规则引擎强制否决 → REJECTED + 门禁拦截."""
    _install_scripted_llm(monkeypatch, BAD_ELECTROLYTE_DRAFT)
    pipeline = _build_seeded_pipeline(tmp_path)
    task_dir = tmp_path / "task_rejected_rule"

    res = _run_adapter(pipeline, task_dir, GOLDEN_GOAL)

    assert res["success"] is False
    assert res["review_status"] == "REJECTED"

    # REJECTED 产物仍落盘 (保留供诊断回溯)
    contract = json.loads((task_dir / "design_scheme.json").read_text(encoding="utf-8"))
    assert contract["scheme"]["electrolyte"] == "carbonate_ec"
    violations = contract["rule_checks"]["rule_checks"]["violations"]
    rejects = contract["rule_checks"]["rule_checks"]["rejects"]
    assert (violations or rejects), "锂金属+常规碳酸酯必须触发 C 规则硬违规"
    # 规则引擎对过度自信 LLM 的否决权: 最终置信度被强制压到 low
    assert contract["confidence"] == "low"

    # 门禁: 真实 RAGDesignChecker 拦截 (置信度检查 3.2 先于规则审计 3.4 短路,
    # 两者都是合法拦截: 规则强制压低的 low 置信度 / C3 硬违规本身)
    passed, diag = _gate_check(task_dir, GOLDEN_GOAL, tmp_path)
    assert passed is False
    assert diag["error_code"] in ("RELATION_ENGINE_VIOLATION", "REVIEWER_CONFIDENCE_TOO_LOW")


def test_offline_golden_rejects_incomplete_scheme(tmp_path, monkeypatch):
    """负向 2: 全文无任何电解液实体 → scheme 缺 electrolyte → 适配器 REJECTED,
    门禁报 STRUCTURED_SCHEME_INCOMPLETE."""
    _install_scripted_llm(monkeypatch, NO_ELECTROLYTE_DRAFT)
    pipeline = _build_seeded_pipeline(tmp_path)
    task_dir = tmp_path / "task_rejected_scheme"

    res = _run_adapter(pipeline, task_dir, GOLDEN_GOAL)

    assert res["success"] is False
    assert res["review_status"] == "REJECTED"

    contract = json.loads((task_dir / "design_scheme.json").read_text(encoding="utf-8"))
    assert contract["scheme"].get("cathode") == "NCM811"
    assert "electrolyte" not in contract["scheme"] or not contract["scheme"].get("electrolyte")

    passed, diag = _gate_check(task_dir, GOLDEN_GOAL, tmp_path)
    assert passed is False
    assert diag["error_code"] == "STRUCTURED_SCHEME_INCOMPLETE"


# ────────────────────────── external 真实链路 golden ──────────────────────────


@pytest.mark.external
@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="需要 OPENAI_API_KEY (真实 LLM 链路), 且需 Ollama 嵌入/Chroma 服务可用")
def test_external_real_pipeline_golden(tmp_path):
    """真实链路 golden: 真实 Chroma 检索 + 真实 LLM Planner/Writer/Reviewer 全流程.

    默认 `pytest -m "unit or not external"` 不运行; trace 落盘供人工回放比对。
    """
    from auto_battery_research.util.env_loader import load_env
    load_env()
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY 未配置")

    adapter = AbrRagAdapter()  # 真实初始化: hybrid 检索 + repo Chroma + 真实 LLM
    if adapter.pipeline is None:
        pytest.skip(f"RAGPipeline 初始化不可用 (Chroma/Ollama/依赖缺失): {adapter._init_error}")

    task_dir = tmp_path / "golden_live"
    res = adapter.run_rag_design(target_query=GOLDEN_GOAL, task_dir=task_dir)

    trace = {
        "goal": GOLDEN_GOAL,
        "success": res.get("success"),
        "review_status": res.get("review_status"),
        "error": res.get("error"),
        "key_findings": res.get("key_findings"),
    }
    trace_file = task_dir / "golden_live_trace.json"
    task_dir.mkdir(parents=True, exist_ok=True)
    trace_file.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")

    assert res["success"] is True, f"真实链路 golden 未通过: {res.get('error')}"
    assert res["review_status"] == "APPROVED"
    contract = json.loads((task_dir / "design_scheme.json").read_text(encoding="utf-8"))
    assert contract["scheme"].get("cathode") and contract["scheme"].get("anode") and contract["scheme"].get("electrolyte")
    assert len(contract["evidence"]) >= 1
    assert contract["provenance"]["rules_version"] == RULES_VERSION
