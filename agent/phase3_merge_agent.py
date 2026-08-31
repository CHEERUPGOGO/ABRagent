"""Phase 3: 确定性去重 + LLM 孤儿匹配 — 按材料归组"""

import json
import hashlib
import logging
import re
from typing import List, Dict, Any

from agent.config import CONDITION_HASH_FIELDS
from agent.prompts import PHASE3_ORPHAN_MATCH

logger = logging.getLogger("Phase3")


# ══════════════════════════════════════════════════════
# 3a: 确定性 canonical hash + 条件合并
# ══════════════════════════════════════════════════════


def _extract_condition_value(cond: Any, key: str) -> Any:
    """从 condition 字典中提取指定 key 的数值（支持嵌套 {value, unit}）"""
    if not isinstance(cond, dict):
        return None
    val = cond.get(key)
    if isinstance(val, dict):
        return val.get("value", val)
    return val


# 电解液配方串：浓度(可选) + 盐 + "in" + 溶剂部分 + 可选括号比例
#   例: "1M LiPF6 in EC/DEC" | "1.15 M LiTFSI in DME:DOL (1:1 v/v)"
_ELEC_FULL_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*[Mm]?\s*([A-Za-z][A-Za-z0-9]*)"
    r"\s+in\s+([^()]+)(?:\s*\(([^)]*)\))?\s*$",
    re.I)


def _split_solvents(s: str) -> list:
    """溶剂列表拆分：/ : , - 、 等分隔符，去空白去重后排序（顺序无关，利于合并）。"""
    parts = re.split(r"[/:,，、\-]", s)
    toks = [p.strip() for p in parts if p.strip()]
    return sorted(set(toks))


def _norm_paren(p: str) -> str:
    """括号比例规范化："1:1 vol%" -> "1:1"；"7:7:46:40" 保持。"""
    p = re.sub(r"\s+", "", p.strip().lower())
    return re.sub(r"(vol%|v/v|wt%|wt|体积比|质量比)", "", p)


def _normalize_electrolyte(e: str) -> str:
    """电解液配方归一化：结构拆解 + 溶剂排序，供条件 hash 去重。

    覆盖（同一体系合并为同一 canonical）：
      "1M LiPF6 in EC/DEC" | "1 M LiPF6 in EC:DEC" | "1M LiPF6 in EC-DMC (1:1 vol%)"
      -> "1m lipf6 in dmc/ec(1:1)"
    非配方串（描述性/中文/仅盐名）走保守归一化：小写 + 压缩空白 + 浓度去空格。
    """
    if not e or not isinstance(e, str):
        return ""
    s = e.strip()
    m = _ELEC_FULL_RE.match(s)
    if m:
        conc, salt, solv, paren = m.groups()
        conc_n = re.sub(r"\s+", "", conc) + "M"   # "1" / "1.15" -> "1M" / "1.15M"
        solv_n = "/".join(_split_solvents(solv))
        canon = f"{conc_n} {salt.lower()} in {solv_n}"
        if paren:
            p = _norm_paren(paren)
            if p:
                canon += f"({p})"
        return canon
    # 保守 fallback：小写 + 压缩空白 + 浓度去空格（"1 M" -> "1m"）
    s = re.sub(r"\s+", " ", s.lower()).strip()
    return re.sub(r"(\d)\s+([m])\b", r"\1\2", s)


def _compute_hash(condition: dict) -> str:
    """根据关键字段生成 canonical hash"""
    canonical = {}
    for field in CONDITION_HASH_FIELDS:
        val = _extract_condition_value(condition, field)
        if val is not None and val != "" and val != 0:
            if field == "electrolyte" and isinstance(val, str):
                val = _normalize_electrolyte(val)
            canonical[field] = val
    if not canonical:
        return "NO_CONDITION"
    raw = json.dumps(canonical, sort_keys=True)
    return "HASH_" + hashlib.sha256(raw.encode()).hexdigest()[:8]


def _merge_field(a_val: Any, b_val: Any) -> Any:
    """合并两个字段值：非空覆盖空"""
    if a_val is None or a_val == "" or a_val == 0:
        return b_val
    if isinstance(a_val, dict) and isinstance(b_val, dict):
        merged = dict(a_val)
        for k, v in b_val.items():
            if k not in merged or merged[k] is None or merged[k] == "":
                merged[k] = v
        return merged
    return a_val


def _normalize_conditions(global_conditions: List[Dict]) -> List[Dict]:
    """确定性归一化 + 去重合并"""
    hash_map = {}
    for gc in global_conditions:
        h = _compute_hash(gc.get("condition", {}))
        if h in hash_map:
            # 合并字段
            existing = hash_map[h]
            existing_cond = existing.get("condition", {})
            new_cond = gc.get("condition", {})
            for k in new_cond:
                if k not in existing_cond or existing_cond[k] is None or existing_cond[k] == "":
                    existing_cond[k] = new_cond[k]
            # 合并 material_id
            if gc.get("material_id") and not existing.get("material_id"):
                existing["material_id"] = gc["material_id"]
        else:
            gc_copy = json.loads(json.dumps(gc))
            gc_copy["canonical_id"] = h
            hash_map[h] = gc_copy

    return list(hash_map.values())


# ══════════════════════════════════════════════════════
# 3b: LLM 孤儿属性匹配
# ══════════════════════════════════════════════════════


def _find_orphans(conditioned: List[Dict]) -> (List[Dict], List[Dict]):
    """分离有/无 condition_id 的属性（过滤非 dict 脏数据，LLM 输出可能混入 list）"""
    dicts = [c for c in conditioned if isinstance(c, dict)]
    linked = [c for c in dicts if c.get("condition_id")]
    orphan = [c for c in dicts if not c.get("condition_id")]
    return linked, orphan


def _safe_val_str(o: Dict) -> str:
    """安全格式化属性值（LLM 输出的 value 可能是 dict / list / 标量）"""
    val = o.get("value")
    if isinstance(val, dict):
        return str(val.get("value", ""))
    if isinstance(val, list):
        return str(val)
    return str(val if val is not None else "")


def _format_orphans(orphans: List[Dict]) -> str:
    lines = []
    for i, o in enumerate(orphans):
        lines.append(f"  [{i}] material={o.get('material_id','?')}, "
                     f"prop={o.get('property_name','')}, "
                     f"val={_safe_val_str(o)}, "
                     f"source={o.get('source_text','')[:80]}")
    return "\n".join(lines)


def _format_conditions_short(conds: List[Dict]) -> str:
    lines = []
    for c in conds:
        cid = c.get("condition_id", "")
        h = c.get("canonical_id", "")
        cond = c.get("condition", {})
        lines.append(f"- {cid} (hash={h}): {json.dumps(cond, ensure_ascii=False)[:200]}")
    return "\n".join(lines)


def _run_llm_orphan_match(merge_llm, orphans: List[Dict],
                          normalized_conds: List[Dict]) -> List[Dict]:
    """用 LLM 匹配孤儿属性到条件"""
    if not orphans or not normalized_conds:
        return orphans

    known_text = _format_conditions_short(normalized_conds)
    orphan_text = _format_orphans(orphans)

    prompt = PHASE3_ORPHAN_MATCH.format(
        known_conditions=known_text,
        orphan_properties=orphan_text,
    )

    try:
        resp = merge_llm.invoke(prompt)
        raw = resp.content if hasattr(resp, "content") else str(resp)
        matches = _parse_llm_array(raw)
    except Exception as e:
        logger.warning(f"Orphan match LLM failed: {e}")
        matches = []

    for m in matches:
        if not isinstance(m, dict):
            continue  # 防御：LLM 返回的数组元素可能是非 dict
        idx = m.get("index")
        cid = m.get("matched_condition_id")
        if idx is not None and cid and 0 <= idx < len(orphans):
            orphans[idx]["condition_id"] = cid
            orphans[idx]["match_confidence"] = m.get("confidence", "low")

    matched = sum(1 for o in orphans if o.get("condition_id"))
    logger.info(f"  Orphan matching: {matched}/{len(orphans)} matched")
    return orphans


def _parse_llm_array(raw: str) -> list:
    cleaned = raw.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    import re
    m = re.search(r"(\[.*\])", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(1)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return []


# ══════════════════════════════════════════════════════
# 按材料归组
# ══════════════════════════════════════════════════════


def _group_by_material(normalized_conds: List[Dict],
                       linked: List[Dict],
                       intrinsic: List[Dict],
                       doi: str) -> Dict:
    """按 material → condition 归组"""
    # 构建 condition 索引（condition_id 与 canonical_id 都索引——属性挂的是 canonical_id）
    cond_map = {}
    for c in normalized_conds:
        cond_map[c["condition_id"]] = c
        cond_map[c.get("canonical_id", c["condition_id"])] = c

    # 按 material 分组
    material_map = {}

    for prop in linked:
        cid = prop.get("condition_id", "")
        mat_id = prop.get("material_id", "") or ""
        cond = cond_map.get(cid, {})
        if mat_id not in material_map:
            material_map[mat_id] = {
                "material_id": mat_id,
                "intrinsic_properties": [],
                "conditions": {},
            }
        if cid not in material_map[mat_id]["conditions"]:
            material_map[mat_id]["conditions"][cid] = {
                "canonical_id": cond.get("canonical_id", ""),
                "scenario": cond.get("scenario", ""),
                "condition": cond.get("condition", {}),
                "properties": [],
            }
        material_map[mat_id]["conditions"][cid]["properties"].append(prop)

    # 本征属性归属
    for prop in intrinsic:
        mat_id = prop.get("material_id", "") or ""
        if mat_id not in material_map:
            material_map[mat_id] = {
                "material_id": mat_id,
                "intrinsic_properties": [],
                "conditions": {},
            }
        material_map[mat_id]["intrinsic_properties"].append(prop)

    # 转列表格式
    result = []
    for mat in material_map.values():
        mat["conditions"] = list(mat["conditions"].values())
        for c in mat["conditions"]:
            c["doi"] = doi
        result.append(mat)

    return {"materials": result}


def _norm_num(v):
    """提取可比较的数值（value 可能是 dict / str / 数字）"""
    if isinstance(v, dict):
        v = v.get("value", v)
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except Exception:
        return None


def _merge_prop_list(props: List[Dict]) -> List[Dict]:
    """合并同材料同属性、数值相近（相对偏差 <2%）的重复属性。
    文本与表格重复提取同一数值时，来源标记合并为 text+table，source_text 保留两处原文。
    """
    merged: List[Dict] = []
    for pr in props:
        if not isinstance(pr, dict):
            continue
        pn = pr.get("property_name", "")
        nv = _norm_num(pr.get("value"))
        hit = None
        for ex in merged:
            if ex.get("property_name") != pn:
                continue
            ev = _norm_num(ex.get("value"))
            if nv is None or ev is None:
                if nv == ev:
                    hit = ex
                    break
                continue
            if ev != 0 and abs(nv - ev) / abs(ev) < 0.02:
                hit = ex
                break
            if nv == ev:
                hit = ex
                break
        if hit is not None:
            # 合并来源标记与原文
            st = {s for s in str(hit.get("source_type", "")).split("+") if s}
            st |= {s for s in str(pr.get("source_type", "")).split("+") if s}
            hit["source_type"] = "+".join(sorted(st))
            srcs = [str(hit.get("source_text", "")), str(pr.get("source_text", ""))]
            hit["source_text"] = " || ".join(s for s in srcs if s)
        else:
            merged.append(json.loads(json.dumps(pr)))
    return merged


def _has_value(v) -> bool:
    """过滤空提取：value 为 None / 空串 / 全空 dict/list 的条目视为无效。
    dict 只检查核心 value 字段（unit/state/source_text 等辅助字段非空不算有效）。"""
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, dict):
        core = v.get("value")
        if core is None or core == "":
            return False
        if isinstance(core, (dict, list)):
            return _has_value(core)
        return True
    if isinstance(v, list):
        if len(v) == 0:
            return False
        return any(not (x is None or x == "") for x in v)
    return True


def _merge_props_global(props: List[Dict], seen: List[Dict]) -> List[Dict]:
    """跨条件组全局去重：按 材料+属性名+数值相近 合并，重复项并入首次出现的条目。
    seen 由调用方在材料级别共享，解决跨条件组重复（原 _merge_prop_list 只做条件组内）。
    合并时 source_type 标记叠加（text+table），source_text 用 || 拼接保留两处原文。"""
    out: List[Dict] = []
    for pr in props:
        if not isinstance(pr, dict):
            continue
        pn = pr.get("property_name", "")
        nv = _norm_num(pr.get("value"))
        mat = pr.get("material_id", "")
        hit = None
        for ex in seen:
            if ex.get("property_name") != pn or ex.get("material_id") != mat:
                continue
            ev = _norm_num(ex.get("value"))
            if nv is None or ev is None:
                if nv == ev:
                    hit = ex
                    break
                continue
            if ev != 0 and abs(nv - ev) / abs(ev) < 0.02:
                hit = ex
                break
            if nv == ev:
                hit = ex
                break
        if hit is not None:
            st = {s for s in str(hit.get("source_type", "")).split("+") if s}
            st |= {s for s in str(pr.get("source_type", "")).split("+") if s}
            hit["source_type"] = "+".join(sorted(st))
            srcs = [str(hit.get("source_text", "")), str(pr.get("source_text", ""))]
            hit["source_text"] = " || ".join(s for s in srcs if s)
        else:
            seen.append(pr)
            out.append(pr)
    return out


def _merge_duplicate_props(result: Dict) -> Dict:
    """对归组后的每个材料：条件属性与本征属性共享同一去重通道（跨条件组 + 跨 conditions/intrinsic），过滤空提取。"""
    for m in result.get("materials", []):
        seen: List[Dict] = []
        for c in m.get("conditions", []):
            props = [p for p in c.get("properties", []) if _has_value(p.get("value"))]
            c["properties"] = _merge_props_global(props, seen)
        intr = [p for p in m.get("intrinsic_properties", []) if _has_value(p.get("value"))]
        m["intrinsic_properties"] = _merge_props_global(intr, seen)
    return result


# ══════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════


def run_phase3(merge_llm, global_conditions: List[Dict],
               conditioned_properties: List[Dict],
               intrinsic_properties: List[Dict],
               doi: str = "") -> Dict:
    """Phase 3: 确定性去重 + LLM孤儿匹配 + 按材料归组"""

    logger.info(f"Phase3 开始: {len(conditioned_properties)} 条件属性, "
                f"{len(intrinsic_properties)} 本征属性, "
                f"{len(global_conditions)} 原始条件")

    # 3a: 确定性去重合并
    normalized_conds = _normalize_conditions(global_conditions)
    logger.info(f"  3a: 条件去重: {len(global_conditions)} -> {len(normalized_conds)}")

    # 分离孤儿属性
    linked, orphans = _find_orphans(conditioned_properties)
    logger.info(f"  孤儿属性: {len(orphans)}/{len(conditioned_properties)}")

    # 3b: LLM 孤儿匹配
    if orphans and merge_llm:
        orphans = _run_llm_orphan_match(merge_llm, orphans, normalized_conds)

    # 合并 linked + 匹配后的 orphans
    all_linked = linked + orphans

    # 属性 condition_id → canonical_id：LLM 可能给同一条件挂不同 id（如 C010/C011 变体），
    # 统一映射到 canonical_id 后，同一 canonical 条件的属性归入同一块，再由 _merge_prop_list 去重。
    canon_map = {}
    for c in normalized_conds:
        cid = c.get("condition_id")
        if cid:
            canon_map[cid] = c.get("canonical_id", cid)
    for prop in all_linked:
        cid = prop.get("condition_id", "")
        if cid and cid in canon_map:
            prop["condition_id"] = canon_map[cid]

    # 按材料归组
    result = _group_by_material(normalized_conds, all_linked, intrinsic_properties, doi)

    # 3c: 合并同材料同属性数值相近的重复（文本+表格来源）
    result = _merge_duplicate_props(result)

    total_cond = sum(len(m.get("conditions", [])) for m in result.get("materials", []))
    total_props = sum(
        len(c.get("properties", []))
        for m in result.get("materials", [])
        for c in m.get("conditions", [])
    )
    total_intr = sum(
        len(m.get("intrinsic_properties", []))
        for m in result.get("materials", [])
    )

    logger.info(f"Phase3 完成: {len(result['materials'])} 种材料, "
                f"{total_cond} 个条件组, {total_props} 个条件属性, "
                f"{total_intr} 个本征属性")

    return result
