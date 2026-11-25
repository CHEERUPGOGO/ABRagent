"""候选展示格式兼容，同条来源、身份、角色和排序保护的离线回归。"""
import copy

import pytest

from auto_battery_research.util.research_records import normalize_candidate_label, validate_record, collect_research, validate_selection
from auto_battery_research.tests.test_research_contract import research_doc


def candidate(name="LiMnO2", label="Rank 1 Zn–Na", *, role="主推候选", rank=1, scope="candidate", source=None):
    row = {"id": "main", "name": name, "display_name": label, "rank": rank, "scope": scope,
           "source_quotes": [source or "| Rank 1 | Zn–Na | LiMnO2（主推候选，待验证） |"],
           "field_support": {"summary": [0]}, "summary": "LiMnO2为当前探索候选，效果待验证。",
           "rationale": "", "risks": "", "citations": [], "parameter_refs": []}
    if role is not None:
        row["candidate_role"] = role
    return row


def test_ranked_short_label_preserves_identity_and_scientific_content():
    row = candidate()
    original = copy.deepcopy(row)
    assert normalize_candidate_label(row) == original and row == original
    doc = research_doc()
    doc["final_answer"] = row["source_quotes"][0]
    validate_record(row, "candidates", doc, doc["ledger_view"]["parameters"])


@pytest.mark.parametrize("source,scope,label,role", [
    ("| Rank 1 | Zn–Na | LiMnO2（主推候选） |", "candidate", "Rank 1 Zn–Na", "主推候选"),
    ("| Rank 1 | Zn–Na | LiMnO2（备选候选） |", "candidate", "Rank 1 Zn–Na", "备选候选"),
    ("| Rank 1 | Zn–Na 单掺对照 | LiMnO2 |", "control", "Rank 1 Zn–Na 单掺对照", "对照组"),
    ("| Rank 1 | Zn–Na | LiMnO2（候选） |", "candidate", "Rank 1 Zn–Na", None),
    ("| Rank 1 | 未掺杂母体（基线） | LiMnO2 |", "control", "Rank 1 未掺杂母体（基线）", None),
])
def test_missing_role_is_optional_and_completed_only_from_explicit_local_source(source, scope, label, role):
    row = candidate(label=label, scope=scope, role=None, source=source)
    result = normalize_candidate_label(row)
    assert result.get("candidate_role") == role
    assert {k:v for k,v in result.items() if k != "candidate_role"} == row
    assert normalize_candidate_label(result) == result


@pytest.mark.parametrize("label,source", [
    ("Rank 1 Cu–Na", "| Rank 1 | Zn–Na | LiMnO2（主推候选） |\n| Rank 2 | Cu–Na | LiNiO2 |"),
    ("Rank 1 Zn–Na 已证实最佳", "| Rank 1 | Zn–Na | LiMnO2（主推候选，待验证） |"),
    ("Rank 2 Zn–Na", "| Rank 1 | Zn–Na | LiMnO2（主推候选） |"),
    ("Rank 1 C3", "| Rank 1 | C31 | LiMnO2（主推候选） |"),
])
def test_display_name_cannot_borrow_other_material_rank_or_scientific_claim(label, source):
    with pytest.raises(ValueError):
        normalize_candidate_label(candidate(label=label, source=source))


@pytest.mark.parametrize("source", [
    "| Rank 1 | Zn–Na | LiMnO2（非主推候选） |",
    "| Rank 1 | Zn–Na | LiMnO2（备选候选） |\n| Rank 2 | Cu–Na | LiNiO2（主推候选） |",
    "LiMnO2为备选候选，LiNiO2为主推候选。",
])
def test_explicit_role_needs_positive_evidence_for_same_material(source):
    with pytest.raises(ValueError, match="角色"):
        normalize_candidate_label(candidate(source=source))


def test_missing_role_cannot_hide_conflicting_scope_or_choose_ambiguous_role():
    with pytest.raises(ValueError, match="scope"):
        normalize_candidate_label(candidate(role=None, scope="control"))
    row = candidate(role=None, source="| Rank 1 | Zn–Na | LiMnO2（主推候选或备选候选，尚待确定） |")
    assert normalize_candidate_label(row).get("candidate_role") is None


@pytest.mark.parametrize("rank,source", [
    (1, "| Rank 11 | Zn–Na | LiMnO2 |"),
    (1, "| Zn–Na | LiMnO2 | 1 C |"),
    (1, "| Zn–Na | LiMnO2 | 第1次循环保持率待验证 |"),
    (2, "| Rank 1 | Zn–Na | LiMnO2 |\n| Rank 2 | Cu–Na | LiNiO2 |"),
])
def test_rank_requires_same_candidate_and_explicit_order_not_a_formula_or_test_number(rank, source):
    row = candidate(label="Zn–Na", role=None, rank=rank, source=source)
    doc = research_doc()
    doc["final_answer"] = source
    with pytest.raises(ValueError, match="顺序"):
        validate_record(row, "candidates", doc, doc["ledger_view"]["parameters"])


@pytest.mark.parametrize("label,source", [("第1名 Zn–Na", "第1名 Zn–Na，LiMnO2，主推候选。"),
                                        ("#1 Zn–Na", "| #1 | Zn–Na | LiMnO2（主推候选） |"),
                                        ("Zn–Na", "| 1 | Zn–Na | LiMnO2（主推候选） |")])
def test_explicit_ranking_syntax_in_same_row_is_supported(label, source):
    row = candidate(label=label, source=source)
    if not source.startswith("|"):
        row["source_quotes"] = ["第1名（Zn–Na）LiMnO2主推候选。"]
    doc = research_doc()
    doc["final_answer"] = row["source_quotes"][0]
    validate_record(row, "candidates", doc, doc["ledger_view"]["parameters"])


def test_format_acceptance_does_not_invent_selection_or_scientific_approval():
    doc = research_doc()
    row = candidate()
    doc["final_answer"] = row["source_quotes"][0]
    research = collect_research({"candidates": [row]}, doc, doc["ledger_view"]["parameters"])
    assert len(research["candidates"]) == 1 and research["selected_candidate_id"] is None
    assert not research["candidates"][0].get("support_review")
    with pytest.raises(ValueError, match="引用"):
        validate_selection("main", research["candidates"], doc["ledger_view"]["parameters"])


def test_cached_candidate_format_failure_restores_only_its_explicit_valid_selection():
    doc = research_doc()
    row = candidate(name="配方A", source="| Rank 1 | Zn–Na | 配方A（主推候选） |")
    row.update(summary="配方A为当前探索候选，仍需验证。", parameter_refs=["cathode.material"])
    doc["final_answer"] = row["source_quotes"][0]
    previous = {"candidates": [], "selected_candidate_id": None, "record_errors": [
        {"collection": "candidates", "id": "main", "reason": "候选展示角色缺少同一来源支持", "record": row},
        {"collection": "selected_candidate_id", "id": "$", "reason": "探索性首选必须引用当前主方案参数", "record": "main"}]}
    before = copy.deepcopy(previous)
    result = collect_research({}, doc, doc["ledger_view"]["parameters"], previous=previous)
    assert previous == before and result["selected_candidate_id"] == "main"
    assert not any(e["collection"] in ("candidates", "selected_candidate_id") for e in result["record_errors"])
    assert not result["candidates"][0].get("support_review")
    cleared = collect_research({"selected_candidate_id": None}, doc, doc["ledger_view"]["parameters"], previous=previous)
    assert cleared["selected_candidate_id"] is None
    for old_id in (None, "other"):
        prior = copy.deepcopy(previous)
        prior["record_errors"][1]["record"] = old_id
        assert collect_research({}, doc, doc["ledger_view"]["parameters"], previous=prior)["selected_candidate_id"] is None


def test_recovered_label_does_not_bypass_current_main_parameter_bindings():
    doc = research_doc()
    row = candidate(name="配方A", source="| Rank 1 | Zn–Na | 配方A（主推候选） |")
    row.update(summary="配方A为当前探索候选，仍需验证。", parameter_refs=[])
    doc["final_answer"] = row["source_quotes"][0]
    prior = {"record_errors": [
        {"collection": "candidates", "id": "main", "reason": "候选展示角色缺少同一来源支持", "record": row},
        {"collection": "selected_candidate_id", "id": "$", "reason": "探索性首选必须引用当前主方案参数", "record": "main"}]}
    result = collect_research({}, doc, doc["ledger_view"]["parameters"], previous=prior)
    assert len(result["candidates"]) == 1 and result["selected_candidate_id"] is None
    assert any(e["collection"] == "selected_candidate_id" for e in result["record_errors"])


@pytest.mark.parametrize("name", ["Zn–Na 主推候选", "Zn–Na（主推候选）"])
def test_legacy_combined_name_still_normalizes_idempotently(name):
    row = {"name": name, "scope": "candidate", "source_quotes": ["| Zn–Na | 主推候选（待验证） |"]}
    result = normalize_candidate_label(row)
    assert result["name"] == "Zn–Na" and result["display_name"] == name
    assert normalize_candidate_label(result) == result
