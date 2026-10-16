"""几何契约：结构化传递、严格数值、证据验证与输入身份（无网络依赖）。"""

import copy
import hashlib
import json
import math
import re
from typing import Any

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
    if claim["material_id"] != material_id or material_id.casefold() not in quote.casefold():
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
    return {"origin": "literature", "basis": "evidence_verified",
            "evidence": copy.deepcopy(claim), "source_text_hash": hashlib.sha256(
                source["text"].encode("utf-8")).hexdigest()}


def build_geometry_contract(candidate: dict, scheme: dict, evidence: list) -> dict:
    """只从结构化候选构建新契约；归一化基准只能由程序注入。"""
    from pinn.input_spec import complete_geometry_block
    from auto_battery_research.tools.geometry_revision import collect_geometry_violations, resolve_input_ranges

    if not isinstance(candidate, dict) or set(candidate) - {"cathode", "anode", "n_p_ratio", "condition", "diffusion_evidence"}:
        raise ValueError("几何候选不是对象或包含未知字段")
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
            if value is None:
                if field in ("L_um", "porosity"):
                    raise ValueError(f"{path} 必须显式给出")
                continue
            if not strict_number(value):
                raise ValueError(f"{path} 必须是有限 JSON 数值")
            source = {"origin": "llm_design", "basis": "engineering_proposal"}
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
    errors = collect_geometry_violations(geo, resolve_input_ranges(scheme.get("cathode"), scheme.get("anode")))
    if errors:
        raise ValueError("; ".join(errors))
    for comp in COMPONENTS:
        for field in FIELDS:
            path = f"{comp}.{field}"
            if path not in sources:
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
    return geo


def validate_geometry_contract(geo: dict, scheme: dict, evidence: list) -> list:
    """共享落盘验收：重算基准、来源证据及范围，不信任客户端的来源标签。"""
    from pinn.input_spec import complete_geometry_block
    from auto_battery_research.tools.geometry_revision import collect_geometry_violations, resolve_input_ranges
    errors = []
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
                if field in ("L_um", "porosity") or baseline is not None or source.get("origin") != "missing":
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
            elif field == "D_s_m2s":
                try:
                    verified = validate_diffusion_evidence(source.get("evidence"), value, str(scheme.get(comp)), evidence)
                    if verified != source:
                        errors.append(f"{path} 证据来源指纹失配")
                except (ValueError, TypeError) as exc:
                    errors.append(f"{path}: {exc}")
            elif source.get("origin") != "llm_design":
                errors.append(f"{path} 不支持的来源类型")
    errors.extend(collect_geometry_violations(geo, resolve_input_ranges(scheme.get("cathode"), scheme.get("anode"))))
    return errors


GEOMETRY_SYSTEM_PROMPT = """你是电池结构化设计工程师。输出纯 JSON，不输出 Markdown。
根据材料组合、设计目标和证据提出参数候选。叙事文本仅供背景，不是用户实测数据。
顶层仅允许 cathode/anode/n_p_ratio/condition/diffusion_evidence。
N/P 不是必填项，不要自行提出或补全 n_p_ratio；仅在输入明确提供该值时原样携带，否则省略或 null。
缺少 N/P 时两电极厚度独立采用声明值，不执行容量比厚度校准。
两电极字段仅允许 L_um(厚度μm)、porosity(0~1)、R_s_um(颗粒半径μm)、D_s_m2s(m²/s)。
厚度和孔隙率必须给出；R_s 可提出工程设计值，禁止将直径/D50直接当半径。
D_s 没有精确材料、25°C条件下恒定/全SOC固相扩散证据时必须为 null，由程序标注模型基准。
不要为了满足设计目标编造 D_s。所有工程候选都不是实测数据。
condition 仅含 v_min/v_max/c_rate；未指定的条件可为 null。
如给出 D_s，diffusion_evidence 按 cathode.D_s_m2s/anode.D_s_m2s 提供对象：
passage_id, quote(连续原文), material_id(当前材料ID), parameter="solid_diffusion_coefficient",
value(原文数值), unit("m2/s"或"cm2/s"), temperature_K, applicability="constant_full_soc", method(原文方法)。
必须遵守给定参数范围。缺证据时保持 D_s=null，不要虚构引用。"""
