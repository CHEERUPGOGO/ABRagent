"""常用参数命名契约与只读取值：明确别名共享身份，不猜测新字段的物理含义。"""
from __future__ import annotations

import copy
import math
import re


# 只登记明确同义名称；活性载量与总干涂层载量始终为两个字段。
FOIL_THICKNESS_ALIASES = {"anode.li_foil_thickness_um": "anode.L_um"}
ALIASES = dict(FOIL_THICKNESS_ALIASES)
KNOWN_FIELDS = {"condition.v_min", "condition.v_max", "condition.c_rate", "condition.C_rate_reference_current", "electrolyte.volume"}
for electrode in ("cathode", "anode"):
    KNOWN_FIELDS.update(f"{electrode}.{name}" for name in (
        "loading_mg_cm2", "active_loading_mg_cm2", "L_um", "porosity", "R_s_um", "D_s_m2s",
        "compaction_g_cm3", "active_fraction", "conductive_fraction", "binder_fraction", "electrode_area"))
    for alias, canonical in (
        ("total_loading_mg_cm2", "loading_mg_cm2"),
        ("dry_coating_loading_mg_cm2", "loading_mg_cm2"),
        ("active_mass_fraction", "active_fraction"),
        ("conductive_mass_fraction", "conductive_fraction"),
        ("binder_mass_fraction", "binder_fraction"),
    ):
        ALIASES[f"{electrode}.{alias}"] = f"{electrode}.{canonical}"


INSTRUCTIONS = '''
【常用规范字段表（不是必填清单）】
下表列正极字段；多孔涂布负极将cathode替换为anode。金属锂负极厚度按单独的锂箔行填写，不套用干涂层质量组成。只登记实际采用且有依据的值，缺项不补造。
| 含义 | 规范字段 | 单位 |
| 总干涂层面载量（活性物+导电剂+粘结剂，不含集流体） | cathode.loading_mg_cm2 | mg/cm2 |
| 活性物质面载量 | cathode.active_loading_mg_cm2 | mg/cm2 |
| 单面干涂层厚度 | cathode.L_um | um |
| 致密金属锂负极的锂箔厚度（不含另设集流体/保护层） | anode.L_um | um |
| 孔隙率 | cathode.porosity | 空串，0–1 |
| 颗粒半径／固相扩散系数 | cathode.R_s_um / cathode.D_s_m2s | um / m2/s |
| 干涂层压实密度 | cathode.compaction_g_cm3 | g/cm3 |
| 干涂层中活性物／导电剂／粘结剂质量分数 | cathode.active_fraction / cathode.conductive_fraction / cathode.binder_fraction | 空串，0–1 |
| 单片电极有效面积 | cathode.electrode_area | cm2 |
| 主循环放电／充电截止电压 | condition.v_min / condition.v_max | V |
| 主循环倍率／1 C参考比电流 | condition.c_rate / condition.C_rate_reference_current | C / mA/g |
| 单电芯注液体积 | electrolyte.volume | uL |
同一含义优先使用上述规范字段，不能因为是计算结果而另起名称。已有非空值或用户锁定值只引用；未锁定null可补充有依据的声明。
L_um表示电极有效层厚度：多孔极片为干涂层，li_metal负极为锂箔；锂箔不是涂层，但同样使用anode.L_um。若使用anode.li_foil_thickness_um，必须给出canonical_path="anode.L_um"。集流体、保护层及复合结构总厚度独立记录，不并入该字段。
同一物理量确需自定义名称时必须给出canonical_path；不同样品、单/双面、含箔/不含箔或专属工况必须保留scope/group_id/conditions/purpose，不能混入主方案。
表外的新物理量可自行命名并说明meaning；名称相似或单位相同不构成映射依据。总载量不得映射为活性物载量。
'''


def is_foil_thickness(path, row):
    explicit = row.get("canonical_path")
    return path in FOIL_THICKNESS_ALIASES or isinstance(explicit, str) and explicit in FOIL_THICKNESS_ALIASES


def checked_name(path, row):
    """保留原路径，明确别名补关联；规范字段不得被重新解释成另一物理量。"""
    from .numeric_consistency import canonical, UNITS

    result = copy.deepcopy(row)
    explicit = result.get("canonical_path")
    if explicit is not None and (not isinstance(explicit, str) or "." not in explicit):
        raise ValueError("canonical_path须为规范字段路径字符串：" + path)
    natural = canonical(path, {key: value for key, value in result.items() if key != "canonical_path"})
    target = canonical(path, result)
    known = natural in UNITS or natural in KNOWN_FIELDS
    if explicit and natural != canonical(path) and target == canonical(path):
        raise ValueError("字段映射与已声明的物理口径冲突：" + path)
    if known and target != natural:
        raise ValueError(f"规范字段身份冲突：{path} 对应 {natural}，不能映射为 {target}")
    if is_foil_thickness(path, result):
        value = result.get("value")
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
            raise ValueError("锂箔厚度必须为正有限数值：" + path)
        description = str(result.get("meaning") or "") + " " + str(result.get("display_label") or "")
        description = re.sub(r"(?:不包含|不包括|未包含|不含|未含|排除)(?:另设)?(?:集流体|保护层|涂层|铜箔)", "", description)
        if re.search(r"(?:含|包括|加上)(?:另设)?(?:集流体|保护层|涂层|铜箔)|复合(?:结构|电极)?总厚|双面总厚", description):
            raise ValueError("锂箔本体厚度不能包含集流体、保护层或复合结构总厚度：" + path)
    if target.endswith(".loading_mg_cm2"):
        meaning = str(result.get("meaning") or "") + " " + str(result.get("display_label") or "")
        # 只拦截明确矛盾，不从文字推断未知对象或涂布面数。
        if re.search(r"(?:活性物(?:质)?|活性材料)(?:面载量|质量面载量)", meaning) and not re.search(r"总|干涂层|\+|与|和", meaning):
            raise ValueError("总干涂层面载量字段与活性物载量含义冲突：" + path)
        positive = re.sub(r"(?:不包含|不包括|未包含|不含|未含|排除)(?:有)?(?:集流体|铝箔|铜箔)", "", meaning)
        positive = re.sub(r"(?:不是|并非|非|不按)湿(?:膜|涂层|浆料)(?:质量)?", "", positive)
        if re.search(r"湿(?:膜|涂层|浆料)|含(?:有)?(?:集流体|铝箔|铜箔)", positive):
            raise ValueError("总干涂层面载量不能混用湿膜或含集流体质量：" + path)
    if known and natural != path and explicit is None:
        result["canonical_path"] = natural
    return result


def foil_material_errors(parameters, scheme=None):
    """明确锂箔字段不能作为另一种负极的主厚度；未知材料留待材料契约验收。"""
    from .numeric_consistency import context
    from .material_ids import identity_path, canonical_id
    foil = [(path, row) for path, row in parameters.items() if isinstance(row, dict)
            and is_foil_thickness(path, row)
            and row.get("value") is not None and row.get("quantity_role", "value") == "value"]
    errors = []
    for path, row in foil:
        scope = context(row)
        materials = [other.get("value") for key, other in parameters.items() if isinstance(other, dict)
                     and identity_path(key, other) == ("anode", "material") and context(other) == scope
                     and other.get("value") is not None]
        if scope == ("main", "main", "[]") and (scheme or {}).get("anode"):
            materials.append(scheme["anode"])
        if any(canonical_id(material) != "li_metal" for material in materials):
            errors.append("锂箔厚度字段与同组负极材料不一致：" + path)
    return errors


def effective_parameters(parameters):
    """同身份的空占位只读采用唯一有效声明；原参数、来源和锁定不变。"""
    from .numeric_consistency import identity, normalized_number, canonical

    result = copy.deepcopy(parameters)
    incompatible_foil = bool(foil_material_errors(parameters))
    groups = {}
    for path, row in parameters.items():
        if not isinstance(row, dict):
            continue
        if incompatible_foil and is_foil_thickness(path, row):
            continue
        try:
            checked_name(path, row)
            groups.setdefault(identity(path, row), []).append((path, row))
        except (ValueError, TypeError):
            continue  # 非法映射留给门禁，不能成为其他字段的有效来源。
    for rows in groups.values():
        if any(row.get("locked") and row.get("value") is None for _, row in rows):
            continue
        declared = [(path, row) for path, row in rows if row.get("value") is not None]
        if not declared:
            continue
        quantities = []
        for path, row in declared:
            try:
                if type(row["value"]) not in (int, float):
                    raise ValueError("仅以已校验数值消解空占位")
                value, unit = normalized_number(path, row)
                key = canonical(path, row)
                if value < 0 or key.endswith((".porosity", "_fraction")) and value > 1:
                    raise ValueError("越界值不能消解空占位")
                quantities.append((value, unit))
            except (ValueError, TypeError):
                break
        if len(quantities) != len(declared) or any(
                unit != quantities[0][1] or not math.isclose(value, quantities[0][0], rel_tol=1e-9, abs_tol=1e-12)
                for value, unit in quantities):
            continue  # 多个不同值或非法单位仍保持冲突，不能任选一个。
        source_path, source = next(((p, r) for p, r in declared if r.get("locked")), declared[0])
        for path, row in rows:
            if row.get("value") is not None:
                continue
            # 空占位的单位也须与来源兼容，不能用mg/cm2填充kg等错误声明。
            try:
                _, expected = normalized_number(path, dict(row, value=0))
            except (ValueError, TypeError):
                continue
            if expected != quantities[0][1]:
                continue
            resolved = copy.deepcopy(source)
            resolved["locked"] = False
            resolved["resolved_from"] = source_path
            result[path] = resolved
    return result
