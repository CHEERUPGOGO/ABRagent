"""完整电解液声明的确定性解析；不猜溶剂比例、不推断溶剂化结构。"""
import math
import re
import unicodedata

VERSION = 2
SALTS = {s.upper(): s for s in ("LiPF6", "LiFSI", "LiTFSI", "LiBF4", "LiClO4", "LiBOB", "LiDFOB", "LiNO3", "LiSac")}
SOLVENTS = {s.upper(): s for s in ("EC", "DMC", "EMC", "DEC", "PC", "FEC", "FEMC", "DME", "DOL", "TEGDME", "TEP", "DMSO", "ACN")}
ADDITIVES = {s.upper(): s for s in ("FEC", "VC", "LiDFOB", "LiBOB", "LiPO2F2", "LiNO3", "DTD", "DTO", "PS", "MPS", "MPL", "PFPN", "TPP")}
DILUENTS = {s: s for s in ("TTE", "HFE", "BTFE")}
NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?"
MOLAR_UNIT = r"(?:mol\s*/\s*(?:[Ll]|kg)|mol\s*[·⋅*]?\s*(?:[Ll]|kg)\s*(?:\^\s*)?-\s*1|mM|M|m)"
QUANTITY = rf"(?P<number>{NUMBER})\s*(?P<unit>{MOLAR_UNIT})"
SALT_RE = re.compile(rf"\s*(?:{QUANTITY}\s*)?(?P<name>(?i:Li)[A-Za-z0-9]+)(?=$|[\s/+])")
AMOUNT_RE = re.compile(rf"\s*(?P<number>{NUMBER})\s*(?P<unit>wt\.?\s*%|vol\.?\s*%|%|{MOLAR_UNIT})\s*(?P<name>[A-Za-z][A-Za-z0-9-]*)\s*")


def normalized_text(value):
    return unicodedata.normalize("NFKC", value).replace("−", "-").replace("⁄", "/")


def looks_like_formulation(value):
    """仅标记可能的完整配方；即使解析失败也不能回退到盐的子串匹配。"""
    if not isinstance(value, str):
        return False
    text = normalized_text(value).strip()
    return bool(re.search(rf"{NUMBER}\s*{MOLAR_UNIT}(?=\s|Li)", text)
                or re.search(r"(?i:Li[A-Za-z0-9]+)\s*(?:/|\+|\bin\b)", text)
                or re.search(r"\s+in\s+", text, re.I)
                or re.search(r"\d\s*(?:wt\.?\s*%|vol\.?\s*%|%)", text))


def _quantity(number, unit, raw):
    value = float(number)
    unit = re.sub(r"\s+|[·⋅*^]", "", unit)
    if unit in ("M", "mol/L", "mol/l", "molL-1", "moll-1"):
        unit = "mol/L"
    elif unit == "mM":
        value, unit = value / 1000, "mol/L"
    elif unit in ("m", "mol/kg", "molkg-1"):
        unit = "mol/kg"
    else:
        unit = unit.replace(".", "")
    if not math.isfinite(value) or value <= 0 or unit in ("wt%", "vol%", "%") and value > 100:
        raise ValueError("浓度/加量必须是有限正数")
    return {"value": value, "unit": unit, "source_text": raw}


def recipe_literal_key(value):
    """精确登记允许单位等价写法，但M与m必须分别保留体积/质量口径。"""
    if not isinstance(value, str):
        return ""
    text = normalized_text(value)
    def replace(match):
        try:
            quantity = _quantity(match["number"], match["unit"], match[0])
            return f"{quantity['value']:.12g}[{quantity['unit']}]"
        except ValueError:
            return match[0]
    text = re.sub(rf"(?<![A-Za-z\d.]){QUANTITY}(?=\s|(?i:Li))", replace, text)
    return re.sub(r"\s+", "", text).casefold()


def _ratio(text):
    """读取明确比例及口径；无体积/质量说明时口径保持空白。"""
    source_text = text
    named = re.match(r"\s*([A-Za-z][A-Za-z0-9]*(?:\s*[:/+,、]\s*[A-Za-z][A-Za-z0-9]*)+)\s*=\s*", text)
    components = None
    # v/v、w/w 是既有计量口径，不能当成溶剂名称列表。
    if named and re.sub(r"\s+", "", named[1]).lower() not in ("v/v", "w/w"):
        names = [name.strip().upper() for name in re.split(r"[:/+,、]", named[1])]
        if any(name not in SOLVENTS for name in names):
            raise ValueError("比例中的溶剂名称未识别")
        if len(set(names)) != len(names):
            raise ValueError("比例中的溶剂名称重复")
        components = [SOLVENTS[name] for name in names]
        text = text[named.end():]
    match = re.fullmatch(rf"\s*(?:(v/v|w/w|vol|wt|体积比|质量比)\s*=?\s*)?"
                         rf"({NUMBER}(?:\s*[:/]\s*{NUMBER})+)\s*(v/v|w/w|vol|wt|体积比|质量比)?\s*", text)
    if not match:
        raise ValueError("比例或括号内容无法完整解析")
    kinds = {"v/v": "volume", "vol": "volume", "体积比": "volume", "w/w": "mass", "wt": "mass", "质量比": "mass"}
    bases = {kinds[k] for k in (match[1], match[3]) if k}
    values = [float(v.strip()) for v in re.split(r"[:/]", match[2])]
    if len(bases) > 1 or any(not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError("溶剂比例或计量口径冲突")
    result = {"values": values, "basis": next(iter(bases), None), "source_text": source_text}
    if components is not None:
        result["components"] = components
    return result


def parse_electrolyte(value):
    """只接受全部可消费的配方语法，未知成分/尾部原文保留为诊断。"""
    result = {"parser_version": VERSION, "raw": value, "complete": False, "salts": [], "solvents": [],
              "additives": [], "diluents": [], "solvent_ratio": None, "diagnostics": []}
    if not isinstance(value, str) or not value.strip():
        result["diagnostics"].append("缺少完整电解液字符串")
        return result
    text = normalized_text(value).strip()
    try:
        rest = text
        while True:
            match = SALT_RE.match(rest)
            if not match or match["name"].upper() not in SALTS:
                raise ValueError("盐或浓度语法未完整识别：" + rest)
            concentration = _quantity(match["number"], match["unit"], match[0].strip()) if match["number"] else None
            result["salts"].append({"name": SALTS[match["name"].upper()], "concentration": concentration,
                                    "source_text": match[0].strip()})
            rest = rest[match.end():].strip()
            if rest.startswith("+") and SALT_RE.match(rest[1:].lstrip()):
                rest = rest[1:].lstrip()
                continue
            break
        if len({s["name"] for s in result["salts"]}) != len(result["salts"]):
            raise ValueError("同一盐有重复声明，需确认浓度归属")
        rest = re.sub(r"^in\b\s*", "", rest, flags=re.I)
        if rest.startswith(("/", "+")):
            rest = rest[1:].lstrip()
        if not rest:
            raise ValueError("缺少溶剂声明")
        # 只有比例括号可单独提取；未识别括号禁止静默删除。
        brackets = list(re.finditer(r"\(([^()]*)\)", rest))
        if len(brackets) > 1:
            raise ValueError("存在多个比例/括号声明，需确认各自范围")
        ratio_solvents = None
        if brackets:
            bracket = brackets[0]
            result["solvent_ratio"] = _ratio(bracket[1])
            ratio_solvents = rest[:bracket.start()]
            rest = rest[:bracket.start()] + rest[bracket.end():]
        parts = re.split(r"\s*\+\s*", rest)
        extra_seen = False
        for n, part in enumerate(parts):
            amount_match = AMOUNT_RE.fullmatch(part)
            if n > 0 and amount_match:
                name = amount_match["name"].upper()
                group = "diluents" if name in DILUENTS else "additives" if name in ADDITIVES else None
                if group is None:
                    raise ValueError("添加成分或用途未识别：" + part)
                names = DILUENTS if group == "diluents" else ADDITIVES
                result[group].append({"name": names[name], "amount": _quantity(amount_match["number"], amount_match["unit"], part), "source_text": part})
                extra_seen = True
                continue
            names = [x.strip().upper() for x in re.split(r"[/,、]", part)]
            if (not names or any(name not in SOLVENTS for name in names) or extra_seen
                    or n > 0 and any(name in ADDITIVES for name in names)):
                raise ValueError("溶剂/添加剂未完整解析或用途不明确：" + part)
            result["solvents"].extend({"name": SOLVENTS[name]} for name in names)
        solvent_names = [s["name"] for s in result["solvents"]]
        if len(set(solvent_names)) != len(solvent_names):
            raise ValueError("重复溶剂声明，需确认配比")
        ratio = result["solvent_ratio"]
        if ratio:
            before = [x.strip().upper() for x in re.split(r"[/,+、]", ratio_solvents)]
            if before != [n.upper() for n in solvent_names] or len(ratio["values"]) != len(solvent_names):
                raise ValueError("比例未唯一对应全部主体溶剂")
            if "components" in ratio and ratio["components"] != solvent_names:
                raise ValueError("比例声明的溶剂名称或顺序与主体溶剂不一致")
            ratio["components"] = solvent_names
        for group in ("additives", "diluents"):
            if len({r["name"] for r in result[group]}) != len(result[group]):
                raise ValueError("重复添加成分，需确认加量")
        result["complete"] = True
    except ValueError as exc:
        result["diagnostics"].append(str(exc))
    return result


def classify_electrolyte(value, policies):
    """分类范围由本地表显式声明；解析成功不等于规则覆盖或性能已验证。"""
    result = parse_electrolyte(value)
    result.update(rule_material_id=None, policy_id=None)
    if not result["complete"]:
        return result
    families = policies.get("families", []) if isinstance(policies, dict) else []
    for policy in families if isinstance(families, list) else []:
        # 分类表损坏时不借宽松匹配放行，其他有效分类仍可独立检查。
        if (not isinstance(policy, dict) or not isinstance(policy.get("concentration"), dict)
                or any(not isinstance(policy.get(k), list) for k in ("salts", "solvents", "additives"))
                or not policy.get("rule_material_id") or not policy.get("id")):
            continue
        salts = result["salts"]
        if len(salts) != 1 or salts[0]["name"] not in policy["salts"]:
            continue
        concentration = salts[0]["concentration"]
        allowed = policy["concentration"]
        if (not isinstance(allowed.get("values"), list) or not concentration or concentration["unit"] != allowed.get("unit")
                or concentration["value"] not in (allowed.get("values") or [])):
            continue
        if (not all(s["name"] in policy["solvents"] for s in result["solvents"])
                or result["diluents"] or not all(a["name"] in policy["additives"] for a in result["additives"])):
            continue
        result.update(rule_material_id=policy["rule_material_id"], policy_id=policy["id"])
        return result
    result["diagnostics"].append("组成已解析，但盐/浓度/溶剂/添加成分超出已声明分类范围；不推断高浓或LHCE")
    return result
