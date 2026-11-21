"""几何契约：结构化传递、严格数值、证据验证与输入身份（无网络依赖）。"""

import copy
import hashlib
import json
import math
import re
from typing import Any
from auto_battery_research.util.material_ids import canonical_id, canonical_scheme, source_matches

FIELDS = ("L_um", "porosity", "R_s_um", "D_s_m2s")
COMPONENTS = ("cathode", "anode")


def constrain_generated_np(candidate: dict, original_request: str) -> dict:
    """LLM 候选不能新增用户未给出的 N/P；保留显式数值的兼容入口。"""
    if not isinstance(candidate, dict):
        return candidate
    result = copy.deepcopy(candidate)
    match = re.search(r"N\s*[/_-]\s*P\s*(?:比|ratio)?\s*(?:为|[=:：])?\s*(\d+(?:\.\d+)?)", original_request, re.I)
    result["n_p_ratio"] = float(match.group(1)) if match else None
    return result


def geometry_hash(geometry: dict) -> str:
    """身份覆盖参数及来源，排除展示文案与时间戳。"""
    return hashlib.sha256(json.dumps(geometry, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode("utf-8")).hexdigest()


def strict_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_diffusion_evidence(claim: dict, value: float, material_id: str,
                                evidence: list) -> dict:
    """保守核验 D_s：只接受可定位原文中的材料、固相参数、数值及条件。

    当前仅接收明确的恒定 D_s 或覆盖全 SOC 的参数。局部 GITT/表观值不能
    直接替代模型常数；不确定时返回缺口，由调用方保留原参数。
    """
    required = {"passage_id", "quote", "material_id", "parameter", "value", "unit",
                "temperature_K", "applicability", "method"}
    if not isinstance(claim, dict) or not required <= claim.keys():
        raise ValueError("D_s 证据缺少来源、原文、材料、参数、数值、单位、温度、方法或适用性声明")
    source = next((e for e in evidence if isinstance(e, dict)
                   and e.get("passage_id") == claim["passage_id"]), None)
    quote = claim["quote"]
    if not source or not isinstance(quote, str) or not quote.strip() or quote not in source.get("text", ""):
        raise ValueError("D_s 证据引用不存在或摘录不是保存原文的连续片段")
    if canonical_id(claim["material_id"]) != canonical_id(material_id) or not source_matches("cathode.material", material_id, quote):
        raise ValueError("D_s 原文未明确匹配当前材料 ID；需补充精确材料证据")
    if claim["parameter"] != "solid_diffusion_coefficient" or not re.search(
            r"solid[ -]?(?:phase|state).*?diffus|固相.*?扩散", quote, re.I | re.S):
        raise ValueError("证据未明确是固相扩散系数")
    if claim["applicability"] != "constant_full_soc" or not re.search(
            r"constant|full SOC|恒定|全\s*SOC", quote, re.I):
        raise ValueError("D_s 证据未支持恒定/全 SOC 模型参数，不能采用局部表观值")
    if not strict_number(claim["temperature_K"]) or abs(claim["temperature_K"] - 298.15) > 0.1:
        raise ValueError("D_s 证据温度与当前 PINN 298.15 K 不一致")
    if not re.search(r"298\.15\s*K|25\s*(?:°\s*C|℃)", quote, re.I):
        raise ValueError("D_s 证据原文缺少 25°C/298.15K 条件")
    if not isinstance(claim["method"], str) or not claim["method"] or claim["method"].casefold() not in quote.casefold():
        raise ValueError("D_s 测量/辨识方法未在原文定位")
    scale = {"m2/s": 1.0, "cm2/s": 1e-4}.get(claim["unit"])
    if scale is None or not strict_number(claim["value"]) or claim["value"] <= 0:
        raise ValueError("D_s 证据单位仅支持 m2/s、cm2/s，且数值必须为正有限数")
    normalized_quote = quote.replace("²", "2").replace("^2", "2")
    pairs = re.findall(r"([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\s*(cm2/s|m2/s)", normalized_quote)
    if not any(u == claim["unit"] and math.isclose(float(n), claim["value"], rel_tol=1e-9)
               for n, u in pairs):
        raise ValueError("D_s 数值与单位无法在原文联合定位（支持十进制/e 记数法）")
    if not math.isclose(claim["value"] * scale, value, rel_tol=1e-9):
        raise ValueError("D_s 修改值与证据换算后的数值不一致")
    normalized_claim = copy.deepcopy(claim)
    normalized_claim["material_id"] = canonical_id(claim["material_id"])
    return {"origin": "literature", "basis": "evidence_verified",
            "evidence": normalized_claim, "source_text_hash": hashlib.sha256(
                source["text"].encode("utf-8")).hexdigest()}


def build_geometry_contract(candidate: dict, scheme: dict, evidence: list) -> dict:
    """只从结构化候选构建新契约；归一化基准只能由程序注入。"""
    from pinn.input_spec import complete_geometry_block
    from auto_battery_research.tools.geometry_revision import collect_geometry_violations, resolve_input_ranges

    if not isinstance(candidate, dict) or set(candidate) - {"cathode", "anode", "n_p_ratio", "condition", "diffusion_evidence"}:
        raise ValueError("几何候选不是对象或包含未知字段")
    scheme = canonical_scheme(scheme)
    raw = {"schema_version": "1.1", "provenance": {}}
    sources = {}
    for comp in COMPONENTS:
        block = candidate.get(comp)
        if not isinstance(block, dict) or set(block) - set(FIELDS):
            raise ValueError(f"{comp} 缺失或包含未知几何字段")
        raw[comp] = {}
        for field in FIELDS:
            value = block.get(field)
            path = f"{comp}.{field}"
            is_li_anode = (comp == "anode" and str(scheme.get("anode") or "") == "li_metal")
            if value is None:
                if is_li_anode and field == "porosity":
                    raw[comp][field] = 0.0
                    raw["provenance"][path] = "stage4_recipe"
                    sources[path] = {"origin": "llm_design", "basis": "dense_metal_foil", "material_id": "li_metal"}
                    continue
                if field in ("L_um", "porosity"):
                    raise ValueError(f"{path} 必须显式给出")
                continue
            if not strict_number(value):
                raise ValueError(f"{path} 必须是有限 JSON 数值")
            source = {"origin": "llm_design", "basis": "engineering_proposal"}
            if is_li_anode and field == "porosity" and float(value) == 0.0:
                source = {"origin": "llm_design", "basis": "dense_metal_foil", "material_id": "li_metal"}
            if field == "D_s_m2s":
                source = validate_diffusion_evidence(
                    (candidate.get("diffusion_evidence") or {}).get(path), value,
                    str(scheme.get(comp) or ""), evidence)
            raw[comp][field] = value
            raw["provenance"][path] = "stage4_recipe"
            sources[path] = source
    if candidate.get("n_p_ratio") is not None:
        if not strict_number(candidate["n_p_ratio"]):
            raise ValueError("n_p_ratio 必须是有限数值")
        raw["n_p_ratio"] = candidate["n_p_ratio"]
    cond = candidate.get("condition", {})
    if not isinstance(cond, dict) or set(cond) - {"v_min", "v_max", "c_rate"}:
        raise ValueError("测试条件包含未知字段")
    for field, value in cond.items():
        if value is not None and not strict_number(value):
            raise ValueError(f"condition.{field} 必须为有限数值")
    raw["condition"] = cond
    geo = complete_geometry_block(raw, scheme.get("cathode"), scheme.get("anode"))
    errors = collect_geometry_violations(
        geo, resolve_input_ranges(scheme.get("cathode"), scheme.get("anode")),
        anode_id=scheme.get("anode"),
    )
    if errors:
        raise ValueError("; ".join(errors))
    for comp in COMPONENTS:
        for field in FIELDS:
            path = f"{comp}.{field}"
            if path not in sources:
                if comp == "anode" and str(scheme.get("anode") or "") == "li_metal" and field in ("R_s_um", "D_s_m2s") and geo[comp][field] is None:
                    sources[path] = {"origin": "not_applicable", "basis": "dense_metal_foil", "material_id": "li_metal"}
                else:
                    sources[path] = {"origin": "model_baseline" if geo[comp][field] is not None else "missing",
                                     "basis": "model_reference" if geo[comp][field] is not None else "unresolved",
                                     "material_id": scheme.get(comp)}
    from pinn.spm_runner import match_pinn_system, load_system_params
    system = match_pinn_system(str(scheme.get("cathode") or ""), str(scheme.get("anode") or ""))
    if system:
        params_hash = hashlib.sha256(json.dumps(load_system_params(system), sort_keys=True).encode()).hexdigest()
        for source in sources.values():
            if source.get("origin") == "model_baseline":
                source.update({"system_id": system["system_id"], "params_path": system["params"],
                               "params_hash": params_hash})
    geo["contract_version"] = "2.0"
    geo["parameter_sources"] = sources
    geo["material_ids"] = {comp: scheme.get(comp) for comp in COMPONENTS}
    return geo


def geometry_from_declared_parameters(parameters: dict, scheme: dict, evidence: list):
    """恢复只重建已声明主工况，不调用模型、不覆盖非法/缺失的显式输入。"""
    from auto_battery_research.util.physical_parameters import fixed_geometry, record_geometry_sources
    from auto_battery_research.util.numeric_consistency import context
    fixed = fixed_geometry(parameters)
    if not all(scheme.get(c) for c in ("cathode", "anode", "electrolyte")):
        raise ValueError("恢复几何前须有完整材料身份")
    prior = scheme.get("geometry") or {}
    if prior.get("contract_version") == "2.0":
        errors = validate_geometry_contract(prior, scheme, evidence)
        if errors:
            raise ValueError("已有几何契约失效，不能用恢复覆盖：" + "；".join(errors))
    for path, row in fixed.items():
        comp, name = path.split(".")
        old = (prior.get(comp) or {}).get(name)
        if old is not None and old != row["value"]:
            raise ValueError("已有几何与声明冲突：" + path)
    candidate = {c: {} for c in COMPONENTS}
    model_values = {}
    for comp in COMPONENTS:
        for name in ("L_um", "porosity"):
            if f"{comp}.{name}" not in fixed:
                raise ValueError(f"恢复缺少已声明主工况 {comp}.{name}")
        for name in ("L_um", "porosity", "R_s_um"):
            declaration = fixed.get(f"{comp}.{name}")
            if declaration is not None:
                candidate[comp][name] = declaration["value"]
        path = comp + ".D_s_m2s"
        row = parameters.get(path) or {}
        if row.get("value") is not None:
            if context(row) != ("main", "main", "[]") or row.get("unit") != "m2/s":
                raise ValueError(path + " 恢复时须有同工况规范单位声明")
            if row.get("basis") == "model_reference":
                model_values[path] = row["value"]
            else:
                candidate[comp]["D_s_m2s"] = row["value"]
                candidate.setdefault("diffusion_evidence", {})[path] = row.get("evidence")
    candidate["condition"] = {key.split(".")[1]: value["value"] for key, value in fixed.items()
                              if key in ("condition.v_min", "condition.v_max", "condition.c_rate")}
    for path in ("anode.n_p_ratio", "condition.n_p_ratio"):
        row = parameters.get(path) or {}
        if row.get("value") is not None:
            if context(row) != ("main", "main", "[]"):
                raise ValueError("N/P恢复不能使用对照工况")
            if "n_p_ratio" in candidate and candidate["n_p_ratio"] != row["value"]:
                raise ValueError("N/P声明冲突")
            candidate["n_p_ratio"] = row["value"]
    geo = build_geometry_contract(candidate, scheme, evidence)
    for path, row in parameters.items():
        comp, _, name = path.partition(".")
        if comp in COMPONENTS and name in FIELDS:
            if row.get("locked") and row.get("value") is None and geo[comp].get(name) is not None:
                raise ValueError(path + " 用户锁定为空，恢复不得回填模型基准")
    for path, value in model_values.items():
        comp, name = path.split(".")
        if geo[comp].get(name) != value or geo["parameter_sources"][path].get("origin") != "model_baseline":
            raise ValueError(path + " 已有模型基准与当前材料不一致")
    record_geometry_sources(geo, fixed)
    errors = validate_geometry_contract(geo, scheme, evidence)
    if errors:
        raise ValueError("；".join(errors))
    return candidate, geo


def synchronize_material_baselines(geo: dict, scheme: dict) -> dict:
    """显式迁移用：补缺失模型基准并重绑材料来源，保留全部显式值（包括越界值）。"""
    from pinn.input_spec import complete_geometry_block
    from pinn.spm_runner import match_pinn_system, load_system_params
    scheme = canonical_scheme(scheme)
    if not isinstance(geo, dict) or geo.get("contract_version") != "2.0":
        raise ValueError("迁移需要已有结构化几何契约，不从正文重建参数")
    if geo.get("material_ids") and {comp: canonical_id(value) for comp, value in geo["material_ids"].items()} != {comp: scheme.get(comp) for comp in COMPONENTS}:
        raise ValueError("几何对应的是其他材料，不能按名称迁移当前模型参数")
    for comp in COMPONENTS:
        if any(not strict_number((geo.get(comp) or {}).get(field)) for field in ("L_um", "porosity")):
            raise ValueError(f"{comp} 厚度或孔隙率缺少原设计声明，不能在迁移中补造")
    normalized = complete_geometry_block(geo, scheme.get("cathode"), scheme.get("anode"))
    result = copy.deepcopy(geo)
    system = match_pinn_system(str(scheme.get("cathode") or ""), str(scheme.get("anode") or ""))
    params_hash = hashlib.sha256(json.dumps(load_system_params(system), sort_keys=True).encode()).hexdigest() if system else None
    sources = result.setdefault("parameter_sources", {})
    for comp in COMPONENTS:
        for field in FIELDS:
            path = f"{comp}.{field}"
            source = copy.deepcopy(sources.get(path) or {})
            if "material_id" in source:
                source["material_id"] = canonical_id(source["material_id"])
            missing = (geo.get(comp) or {}).get(field) is None
            if missing and field in ("R_s_um", "D_s_m2s") and normalized[comp][field] is not None:
                result[comp][field] = normalized[comp][field]
                result.setdefault("provenance", {})[path] = "params_baseline"
                source = {"origin": "model_baseline", "basis": "model_reference", "material_id": scheme.get(comp),
                          "system_id": system["system_id"], "params_path": system["params"], "params_hash": params_hash}
            elif source.get("origin") == "model_baseline" and system:
                # 只同义归一来源，不将旧版本基准静默替换为新版本。
                if source.get("system_id") != system["system_id"] or source.get("params_hash") != params_hash:
                    raise ValueError(f"{path} 模型基准版本不一致，迁移不能重置已有基准")
            elif source.get("origin") == "literature" and field == "D_s_m2s":
                claim = source.get("evidence") or {}
                if "material_id" in claim:
                    claim["material_id"] = canonical_id(claim["material_id"])
            sources[path] = source
    result["material_ids"] = {comp: scheme.get(comp) for comp in COMPONENTS}
    return result


def input_diagnostic(scheme: dict, evidence: list) -> dict:
    """匹配和输入校验的独立事实记录，便于报告披露；不运行推理或修改设计。"""
    from auto_battery_research.simulation import match_pinn_system
    from auto_battery_research.tools.geometry_revision import resolve_input_ranges
    from auto_battery_research.util.scheme_ledger import digest
    from auto_battery_research.util.hashing import stage5_scheme_projection
    scheme = canonical_scheme(scheme)
    system = match_pinn_system(str(scheme.get("cathode") or ""), str(scheme.get("anode") or ""))
    geometry = scheme.get("geometry") or {}
    errors = validate_geometry_contract(geometry, scheme, evidence) if geometry.get("contract_version") == "2.0" else ["缺少有效结构化几何契约"]
    ranges = resolve_input_ranges(scheme.get("cathode"), scheme.get("anode"))
    violations = []
    for comp, side in (("cathode", "pe"), ("anode", "ne")):
        for field, range_key, factor, unit in (("L_um", "thickness", 1e6, "μm"), ("porosity", "porosity", 1, ""),
            ("R_s_um", "R_s", 1e6, "μm"), ("D_s_m2s", "D_s", 1, "m²/s")):
            value = (geometry.get(comp) or {}).get(field)
            bounds = (ranges.get(side) or {}).get(range_key) or {}
            if strict_number(value) and bounds and (value < bounds["min"] * factor or value > bounds["max"] * factor):
                violations.append({"path": comp + "." + field, "value": value, "min": float(f"{bounds['min'] * factor:.12g}"),
                                   "max": float(f"{bounds['max'] * factor:.12g}"), "unit": unit})
    return {"matched": bool(system), "system_id": (system or {}).get("system_id"),
            "scheme_hash": digest(stage5_scheme_projection(scheme)),
            "error_code": "GEOMETRY_CONTRACT_INVALID" if errors else None, "errors": errors, "violations": violations}


def render_input_diagnostic(doc: dict) -> str:
    """仅显示与当前方案指纹相符的输入阻断原因，不将陈旧诊断写成当前结论。"""
    from auto_battery_research.util.hashing import stage5_scheme_projection
    from auto_battery_research.util.scheme_ledger import digest
    diagnostic = doc.get("physics_input_diagnostic") or {}
    if (diagnostic.get("scheme_hash") != digest(stage5_scheme_projection(doc.get("scheme") or {}))
            or not diagnostic.get("matched") or not diagnostic.get("errors")):
        return ""
    reasons = []
    for violation in diagnostic.get("violations", []):
        names = {"L_um": "厚度", "porosity": "孔隙率", "R_s_um": "颗粒半径", "D_s_m2s": "固相扩散系数"}
        comp, _, field = violation["path"].partition(".")
        label = "正极" if comp == "cathode" else "负极"
        reasons.append(f"{label}{names.get(field, field)}为 {violation['value']:g} {violation['unit']}，超出模型范围 [{violation['min']:g}, {violation['max']:g}] {violation['unit']}")
    if not reasons:
        reasons = diagnostic["errors"]
    return (f"已匹配 **{diagnostic['system_id']}** 模型，但当前输入未通过校验，未执行放电仿真。"
            + "；".join(reasons) + "。当前声明值保持不变，课题性能目标仍待验证。")


def validate_geometry_contract(geo: dict, scheme: dict, evidence: list, *, check_ranges: bool = True) -> list:
    """共享落盘验收：重算基准、来源证据及范围，不信任客户端的来源标签。"""
    from pinn.input_spec import complete_geometry_block
    from auto_battery_research.tools.geometry_revision import collect_geometry_violations, resolve_input_ranges
    errors = []
    canonical = canonical_scheme(scheme)
    for comp in COMPONENTS:
        if canonical.get(comp) != scheme.get(comp):
            errors.append(f"{comp} 材料标识未归一化")
    if "material_ids" in geo and geo["material_ids"] != {comp: canonical.get(comp) for comp in COMPONENTS}:
        errors.append("几何契约与当前材料标识不一致")
    baseline = complete_geometry_block({}, scheme.get("cathode"), scheme.get("anode"))
    from pinn.spm_runner import match_pinn_system, load_system_params
    system = match_pinn_system(str(scheme.get("cathode") or ""), str(scheme.get("anode") or ""))
    params_hash = hashlib.sha256(json.dumps(load_system_params(system), sort_keys=True).encode()).hexdigest() if system else None
    sources = geo.get("parameter_sources")
    if not isinstance(sources, dict):
        return ["parameter_sources 缺失"]
    for comp in COMPONENTS:
        block = geo.get(comp)
        if not isinstance(block, dict):
            errors.append(f"{comp} 缺失")
            continue
        for field in FIELDS:
            path = f"{comp}.{field}"
            value = block.get(field)
            source = sources.get(path)
            if not isinstance(source, dict):
                errors.append(f"{path} 缺少来源")
                continue
            if value is None:
                is_li_anode = (comp == "anode" and str(scheme.get("anode") or "") == "li_metal")
                allowed_null_origins = ("missing", "not_applicable") if is_li_anode else ("missing",)
                if field in ("L_um", "porosity") or baseline is not None or source.get("origin") not in allowed_null_origins:
                    errors.append(f"{path} 不允许缺失")
                continue
            if not strict_number(value):
                errors.append(f"{path} 不是有限数值")
                continue
            if source.get("origin") == "model_baseline":
                expected = (baseline or {}).get(comp, {}).get(field)
                if field not in ("R_s_um", "D_s_m2s") or expected is None or value != expected:
                    errors.append(f"{path} 与模型基准不一致")
                if system and (source.get("system_id") != system["system_id"] or source.get("params_hash") != params_hash):
                    errors.append(f"{path} 模型基准版本指纹失配")
                if system and source.get("material_id") != canonical.get(comp):
                    errors.append(f"{path} 模型基准材料标识失配")
            elif field == "D_s_m2s":
                try:
                    verified = validate_diffusion_evidence(source.get("evidence"), value, str(scheme.get(comp)), evidence)
                    if verified != source:
                        errors.append(f"{path} 证据来源指纹失配")
                except (ValueError, TypeError) as exc:
                    errors.append(f"{path}: {exc}")
            elif source.get("origin") != "llm_design":
                errors.append(f"{path} 不支持的来源类型")
    if check_ranges:
        errors.extend(collect_geometry_violations(
            geo, resolve_input_ranges(scheme.get("cathode"), scheme.get("anode")),
            anode_id=scheme.get("anode"),
        ))
    return errors


GEOMETRY_SYSTEM_PROMPT = """你是电池结构化设计工程师。输出纯 JSON，不输出 Markdown。
根据材料组合、设计目标和证据提出参数候选。叙事文本仅供背景，不是用户实测数据。
顶层仅允许 cathode/anode/n_p_ratio/condition/diffusion_evidence。
N/P 不是必填项，不要自行提出或补全 n_p_ratio；仅在输入明确提供该值时原样携带，否则省略或 null。
缺少 N/P 时两电极厚度独立采用声明值，不执行容量比厚度校准。
两电极字段仅允许 L_um(厚度μm)、porosity(0~1)、R_s_um(颗粒半径μm)、D_s_m2s(m²/s)。
厚度和孔隙率必须给出；R_s 可提出工程设计值，禁止将直径/D50直接当半径。
当 materials.anode == "li_metal"（致密金属锂箔，真密度 0.534 g/cm³）时，负极为致密金属沉积/剥离电极而非多孔嵌脱颗粒涂布电极：除非明确研究多孔 3D 亲锂骨架，否则 anode.porosity 应为 0.0（不得套用常规涂布极片的孔隙率），且 anode.R_s_um 与 anode.D_s_m2s 必须为 null（不得编造等效颗粒半径或固相扩散系数）。
D_s 没有精确材料、25°C条件下恒定/全SOC固相扩散证据时必须为 null，由程序标注模型基准。
不要为了满足设计目标编造 D_s。所有工程候选都不是实测数据。
condition 仅含 v_min/v_max/c_rate；未指定的条件可为 null。
如给出 D_s，diffusion_evidence 按 cathode.D_s_m2s/anode.D_s_m2s 提供对象：
passage_id, quote(连续原文), material_id(当前材料ID), parameter="solid_diffusion_coefficient",
value(原文数值), unit("m2/s"或"cm2/s"), temperature_K, applicability="constant_full_soc", method(原文方法)。
必须遵守给定参数范围。缺证据时保持 D_s=null，不要虚构引用。"""
