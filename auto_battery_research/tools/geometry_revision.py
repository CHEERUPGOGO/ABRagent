"""Stage 5 几何修订 — scheme.geometry 的 LLM 反馈修订与择优支持 (ABRAgent).

反馈回路: RunPhysicsSimulation 产出 PINN 仿真结果 → LLM 按带容差的达标判据判断 →
ReviseGeometry 修订 scheme.geometry (provenance=stage5_revision) → 重跑仿真；
循环收尾仍未达标时由 StageManager 择优回滚 (见 workflow_actions._select_best_stage5_result)。

本模块提供:
  - GEOMETRY_REVISION_PROVENANCE / EXPLICIT_PROVENANCE_TAGS: provenance 语义常量
    (RAGDesignChecker 硬字段同时接受 stage4_recipe 与 stage5_revision);
  - resolve_input_ranges(): registry input_ranges 解析 (未匹配体系 → 空 dict, 校验退物理常识);
  - collect_geometry_violations(): v1.1 几何块的越界值收集 —— RAGDesignChecker 与
    ReviseGeometry 共用同一份范围逻辑, 避免双份实现漂移;
  - apply_geometry_revision(): 把 LLM 修订应用到 scheme (complete_geometry_block 幂等
    归一化 + 范围校验 + geometry_revisions 修订留痕), 非法输入一律拒绝不落盘;
  - render_revision_log_markdown(): design_scheme.md 尾部修订记录表渲染。

依赖保持轻量 (标准库 + 惰性 import pinn.*) —— checker 可安全导入, 不引入工具层重依赖。
"""

import copy
import re
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Stage 5 修订字段的 provenance 标签; 与 stage4_recipe 同为"LLM/方案显式声明"语义
GEOMETRY_REVISION_PROVENANCE = "stage5_revision"
EXPLICIT_PROVENANCE_TAGS = ("stage4_recipe", GEOMETRY_REVISION_PROVENANCE)

# 每电极可修订字段 (两电极共 8 项, 与 v1.1 声明块硬字段对齐)
REVISABLE_FIELDS = ("L_um", "porosity", "R_s_um", "D_s_m2s")
REVISABLE_COMPONENTS = ("cathode", "anode")

_SIDE_OF = {"cathode": "pe", "anode": "ne"}


def resolve_input_ranges(cathode_id: Any, anode_id: Any) -> Dict[str, Any]:
    """解析方案 (cathode, anode) 匹配 PINN 体系的 input_ranges；未匹配返回空 dict."""
    try:
        from pinn.spm_runner import match_pinn_system
        system = match_pinn_system(str(cathode_id or ""), str(anode_id or ""))
    except Exception:
        system = None
    return (system or {}).get("input_ranges") or {}


def _value_in_range(path: str, value: float, ranges: Dict[str, Any]) -> bool:
    """单字段范围校验 (registry input_ranges 对齐, 未声明范围退物理常识).

    逻辑与 RAGDesignChecker 原内联 _range_ok 一致 (现统一由本模块承载)。
    """
    if "." in path:
        comp, field = path.split(".", 1)
        side_ranges = ranges.get(_SIDE_OF.get(comp)) or {}
    else:
        field = path
        side_ranges = {}
    if field in ("L_um", "R_s_um"):
        r = side_ranges.get("thickness" if field == "L_um" else "R_s")
        if isinstance(r, dict) and r.get("min") is not None and r.get("max") is not None:
            # registry 以 m 声明, 几何块以 μm 表达
            return float(r["min"]) * 1e6 <= value <= float(r["max"]) * 1e6
        return value > 0
    if field == "porosity":
        r = side_ranges.get("porosity")
        return (float(r["min"]) <= value <= float(r["max"])) if isinstance(r, dict) else 0.0 < value < 1.0
    if field == "D_s_m2s":
        r = side_ranges.get("D_s")
        return (float(r["min"]) <= value <= float(r["max"])) if isinstance(r, dict) else value > 0
    if field in ("loading_mg_cm2", "compaction_g_cm3"):
        return value > 0
    if field == "n_p_ratio":
        return 1.0 <= value <= 2.5
    if field in ("v_min", "v_max"):
        return 1.0 <= value <= 5.5
    if field == "c_rate":
        r = ranges.get("c_rate")
        return (float(r["min"]) <= value <= float(r["max"])) if isinstance(r, dict) else value > 0
    return True


def collect_geometry_violations(geo: Dict[str, Any], ranges: Dict[str, Any]) -> List[str]:
    """收集 v1.1 几何块的全部越界/非法值 (含 invalid provenance 保留值), 去重保序."""
    illegal: List[str] = []
    prov = geo.get("provenance") if isinstance(geo.get("provenance"), dict) else {}
    for comp in ("cathode", "anode"):
        comp_block = geo.get(comp) if isinstance(geo.get(comp), dict) else {}
        for field in ("L_um", "porosity", "R_s_um", "D_s_m2s", "loading_mg_cm2", "compaction_g_cm3"):
            val = comp_block.get(field)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                path = f"{comp}.{field}"
                if prov.get(path) == "invalid" or not _value_in_range(path, float(val), ranges):
                    illegal.append(f"{path}={val}")
    n_p_val = geo.get("n_p_ratio")
    if isinstance(n_p_val, (int, float)) and not isinstance(n_p_val, bool):
        if prov.get("n_p_ratio") == "invalid" or not _value_in_range("n_p_ratio", float(n_p_val), ranges):
            illegal.append(f"n_p_ratio={n_p_val}")
    cond_block = geo.get("condition") if isinstance(geo.get("condition"), dict) else {}
    for field in ("v_min", "v_max", "c_rate"):
        val = cond_block.get(field)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            if prov.get(f"condition.{field}") == "invalid" or not _value_in_range(field, float(val), ranges):
                illegal.append(f"condition.{field}={val}")
    if isinstance(cond_block.get("v_min"), (int, float)) and isinstance(cond_block.get("v_max"), (int, float)) \
            and float(cond_block["v_max"]) <= float(cond_block["v_min"]):
        illegal.append(f"condition.v_max({cond_block['v_max']}) <= v_min({cond_block['v_min']})")
    return list(dict.fromkeys(illegal))


def _as_number(value: Any) -> Optional[float]:
    """严格有限 JSON 数值，禁止字符串、布尔值和 NaN/Infinity。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def count_geometry_revisions(scheme: Dict[str, Any]) -> int:
    """已执行的修订轮数 (仅 kind=revision 计入预算; 择优回滚条目不占预算)。"""
    entries = scheme.get("geometry_revisions")
    if not isinstance(entries, list):
        return 0
    return sum(1 for e in entries if isinstance(e, dict) and e.get("kind") == "revision")


def apply_geometry_revision(
    scheme: Dict[str, Any],
    revisions: Dict[str, Any],
    reason: str = "",
    max_revisions: int = 3,
    evidence_claims: Optional[Dict[str, Any]] = None,
    evidence: Optional[List[dict]] = None,
    base_revision: Optional[int] = None,
    base_geometry_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """把 LLM 修订应用到 scheme.geometry (幂等归一化 + 校验 + 修订留痕).

    revisions 形如 {"cathode": {"L_um": 85.0, "porosity": 0.28}, "anode": {"R_s_um": 9.0}}
    (字段值单位与 v1.1 声明块一致: L_um/R_s_um μm、porosity 小数、D_s_m2s m²/s)。

    校验失败 (块缺失/字段越界/预算耗尽) 一律返回 ok=False 且不改动 scheme —— 修订
    应用与校验在副本上进行, 通过后才写回, 保证"拒绝即零副作用"。
    """
    geo = scheme.get("geometry") if isinstance(scheme.get("geometry"), dict) else None
    if geo is None or geo.get("schema_version") != "1.1" or not isinstance(geo.get("provenance"), dict):
        return {
            "ok": False,
            "error_code": "GEOMETRY_BLOCK_MISSING",
            "error": "design_scheme.json 缺少 v1.1 scheme.geometry 声明块，无法修订 (请先完成 Stage 4)",
        }

    used = count_geometry_revisions(scheme)
    from auto_battery_research.simulation.geometry_contract import geometry_hash, validate_diffusion_evidence
    current_revision = scheme.get("design_revision", used)
    try:
        current_hash = geometry_hash(geo)
    except (ValueError, TypeError):
        return {"ok": False, "error_code": "GEOMETRY_VALUE_ILLEGAL", "error": "当前几何含非法数值"}
    if geo.get("contract_version") == "2.0" or base_revision is not None or base_geometry_hash is not None:
        if base_revision != current_revision or base_geometry_hash != current_hash:
            return {"ok": False, "error_code": "GEOMETRY_REVISION_STALE",
                    "error": "修改请求缺少或不匹配当前版本/哈希，请读取最新仿真上下文",
                    "base_revision": current_revision, "base_geometry_hash": current_hash}
    if used >= max_revisions:
        return {
            "ok": False,
            "error_code": "REVISION_BUDGET_EXHAUSTED",
            "error": f"几何修订预算已用尽 ({used}/{max_revisions})，请基于当前最优结果走 Check → Complete",
            "revisions_used": used,
            "max_revisions": max_revisions,
        }

    if not isinstance(revisions, dict) or not revisions:
        return {"ok": False, "error_code": "REVISION_EMPTY",
                "error": "revisions 为空: 需给出 {'cathode': {...}, 'anode': {...}} 形式的修订字段"}

    # 1. 解析并预检修订字段 (只允许 8 个声明字段; 数值化失败即刻拒绝)
    parsed: List[Tuple[str, str, float]] = []
    for comp, fields in revisions.items():
        if comp not in REVISABLE_COMPONENTS:
            return {"ok": False, "error_code": "REVISION_FIELD_UNKNOWN",
                    "error": f"未知组件 {comp!r} (仅支持 cathode/anode)"}
        if not isinstance(fields, dict) or not fields:
            return {"ok": False, "error_code": "REVISION_FIELD_UNKNOWN",
                    "error": f"{comp} 修订字段为空"}
        for field, value in fields.items():
            if field not in REVISABLE_FIELDS:
                return {"ok": False, "error_code": "REVISION_FIELD_UNKNOWN",
                        "error": f"不可修订字段 {comp}.{field} (允许: {', '.join(REVISABLE_FIELDS)})"}
            num = _as_number(value)
            if num is None:
                return {"ok": False, "error_code": "REVISION_VALUE_ILLEGAL",
                        "error": f"{comp}.{field} 修订值 {value!r} 不是合法数字"}
            parsed.append((comp, field, num))

    # 2. 副本上应用修订 (新值 + stage5_revision provenance), 再走幂等归一化
    new_geo = copy.deepcopy(geo)
    for comp, field, num in parsed:
        source = {"origin": "llm_design", "basis": "engineering_proposal", "rationale": reason}
        if field == "D_s_m2s":
            try:
                source = validate_diffusion_evidence((evidence_claims or {}).get(f"{comp}.{field}"),
                                                     num, str(scheme.get(comp) or ""), evidence or [])
            except ValueError as exc:
                return {"ok": False, "error_code": "DIFFUSION_EVIDENCE_INVALID", "error": str(exc)}
        new_geo.setdefault("parameter_sources", {})[f"{comp}.{field}"] = source
        new_geo.setdefault(comp, {})[field] = num
        new_geo["provenance"][f"{comp}.{field}"] = GEOMETRY_REVISION_PROVENANCE
    try:
        from pinn.input_spec import complete_geometry_block
        normalized = complete_geometry_block(new_geo, scheme.get("cathode"), scheme.get("anode"))
    except Exception as e:
        return {"ok": False, "error_code": "REVISION_NORMALIZE_FAILED",
                "error": f"修订归一化失败 (fail-closed): {type(e).__name__}: {e}"}
    if normalized is None:
        return {"ok": False, "error_code": "REVISION_NORMALIZE_FAILED",
                "error": "修订归一化返回空块 (fail-closed)"}

    # 3. 范围校验 (registry input_ranges 对齐) —— 越界拒绝并点名字段与合法范围
    ranges = resolve_input_ranges(scheme.get("cathode"), scheme.get("anode"))
    illegal = collect_geometry_violations(normalized, ranges)
    if illegal:
        range_hint = format_range_hint(ranges)
        return {"ok": False, "error_code": "GEOMETRY_VALUE_ILLEGAL",
                "error": "修订值越界: " + "; ".join(illegal) + (f"；{range_hint}" if range_hint else ""),
                "illegal": illegal, "legal_ranges": range_hint or None}

    # 4. 修订留痕并写回 scheme
    changes = []
    for comp, field, num in parsed:
        old = (geo.get(comp) or {}).get(field)
        changes.append({"field": f"{comp}.{field}", "old": old, "new": num})
    entry = {
        "kind": "revision",
        "round": used + 1,
        "timestamp": datetime.now().isoformat(),
        "changes": changes,
        "reason": str(reason or "").strip(),
    }
    entries = scheme.get("geometry_revisions") if isinstance(scheme.get("geometry_revisions"), list) else []
    entries.append(entry)
    scheme["geometry_revisions"] = entries
    scheme["geometry"] = normalized
    # 择优回滚可以指向旧版本，新修订号仍必须单调分配。
    scheme["design_revision"] = max(current_revision, used) + 1
    scheme["geometry_hash"] = geometry_hash(normalized)
    entry["base_geometry_hash"] = current_hash
    entry["geometry_hash"] = scheme["geometry_hash"]
    entry["design_revision"] = scheme["design_revision"]
    entry["evidence_claims"] = copy.deepcopy(evidence_claims or {})
    return {
        "ok": True,
        "changes": changes,
        "revision_entry": entry,
        "revisions_used": used + 1,
        "max_revisions": max_revisions,
        "geometry": normalized,
    }


def format_range_hint(ranges: Dict[str, Any]) -> str:
    """把 registry input_ranges 渲染为人读的合法范围提示 (μm/m²/s 口径与声明块一致)."""
    parts: List[str] = []

    def side_hint(side_cn: str, side: str) -> None:
        sr = ranges.get(side) or {}
        seg = []
        for key, unit_cn, scale in (("thickness", "厚度", 1e6), ("porosity", "孔隙率", 1.0),
                                    ("R_s", "R_s", 1e6), ("D_s", "D_s", 1.0)):
            r = sr.get(key)
            if isinstance(r, dict) and r.get("min") is not None and r.get("max") is not None:
                lo, hi = float(r["min"]) * scale, float(r["max"]) * scale
                seg.append(f"{unit_cn}[{lo:g}, {hi:g}]" + ("μm" if scale == 1e6 else ("m²/s" if key == "D_s" else "")))
        if seg:
            parts.append(f"{side_cn}: " + ", ".join(seg))

    side_hint("正极", "pe")
    side_hint("负极", "ne")
    cr = ranges.get("c_rate")
    if isinstance(cr, dict) and cr.get("min") is not None:
        parts.append(f"倍率[{cr['min']:g}, {cr['max']:g}]C")
    return "合法范围 " + "; ".join(parts) if parts else ""


def render_revision_log_markdown(revisions: List[Dict[str, Any]]) -> str:
    """渲染 design_scheme.md 尾部的几何修订记录表 (空列表 → 空串)."""
    if not revisions:
        return ""
    lines = [
        "**几何修订记录**（Stage 5 PINN 反馈回路）:",
        "",
        "| # | 时间 | 类型 | 修订内容 | 理由 |",
        "|---|---|---|---|---|",
    ]
    for entry in revisions:
        if not isinstance(entry, dict):
            continue
        kind_cn = "修订" if entry.get("kind") == "revision" else "择优回滚"
        changes = entry.get("changes") or []
        ch_txt = "; ".join(
            f"{c.get('field')}: {c.get('old')} → {c.get('new')}" if isinstance(c, dict) else str(c)
            for c in changes
        ) or "—"
        reason = re.sub(r"[\r\n|]+", " ", str(entry.get("reason") or "—")) or "—"
        lines.append(
            f"| {entry.get('round', '—')} | {entry.get('timestamp', '—')} | {kind_cn} | {ch_txt} | {reason} |"
        )
    return "\n".join(lines)
