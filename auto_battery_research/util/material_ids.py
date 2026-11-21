"""材料标识公共入口：沿用既有别名规则，只归一结构化字段，不改写科研原文。"""
import copy
import re
import unicodedata
from functools import lru_cache
from .electrolyte_composition import looks_like_formulation

VERSION = 1
COMPONENTS = ("cathode", "anode", "electrolyte", "separator", "current_collector")
FIELDS = {comp + ".material" for comp in COMPONENTS}
LITHIUM_METAL_NAMES = ("Li 金属", "Li金属", "金属锂", "锂金属", "金属锂片", "金属锂箔", "锂金属片", "锂金属箔", "锂片", "锂箔")
# 只接纳完整身份声明；盐、浓度、掺量、母体式等局部字段不能拼成主配方。
IDENTITY_KINDS = {"material": "material", "nominal_formula": "nominal_formula",
                  "formula": "nominal_formula", "formulation": "formulation",
                  "recipe": "formulation", "composition": "formulation"}
PARENT_FIELDS = {"base_formula", "parent_formula", "mother_formula"}


@lru_cache(maxsize=1)
def _engine():
    from auto_battery_research.rag import RelationEngine
    return RelationEngine()


def canonical_id(value):
    """已知别名转为规范ID；未知名称和非字符串保持原值。"""
    if not isinstance(value, str) or not value.strip():
        return value
    if looks_like_formulation(value):
        return value
    if value.strip() in LITHIUM_METAL_NAMES:
        return "li_metal"
    return _engine().normalize(value) or value


def canonical_field(path, row):
    result = copy.deepcopy(row)
    if not isinstance(result, dict) or "value" not in result:
        return result
    info = identity_path(path, result)
    if not info or info[1] != "material":
        return result
    value = result["value"]
    canonical = canonical_id(value)
    if canonical != value:
        original = result.get("original_value", value)
        result["original_value"] = original if canonical_id(original) == canonical else value
        result["value"] = canonical
    elif "original_value" in result and canonical_id(result["original_value"]) != canonical:
        # 真正换材料时不能把上一种材料的名称留作当前材料的来源。
        result.pop("original_value")
    return result


def canonical_parameters(parameters):
    return {path: canonical_field(path, row) for path, row in (parameters or {}).items()}


def exact_material_id(value):
    """完整组成仅做精确别名映射，禁止因包含某个盐/母体名丢掉配方。"""
    if not isinstance(value, str) or not value.strip():
        return value
    if looks_like_formulation(value):
        return value
    token = _normalized(value)
    matches = {canonical for canonical, aliases in _engine().alias_map.items()
               if isinstance(aliases, list) and token in {_normalized(a) for a in [canonical] + aliases}}
    return next(iter(matches)) if len(matches) == 1 else value


def identity_path(path, row=None):
    """开放字段通过 canonical_path 显式关联，不按关键词猜材料。"""
    target = (row or {}).get("canonical_path") or path
    if not isinstance(target, str):
        return None
    comp, _, leaf = target.partition(".")
    return (comp, IDENTITY_KINDS[leaf]) if comp in COMPONENTS and leaf in IDENTITY_KINDS else None


def parent_path(path, row=None):
    """仅明确母体字段用于规则分类，不从掺杂化学式或描述反推母体。"""
    target = (row or {}).get("canonical_path") or path
    if not isinstance(target, str):
        return None
    comp, _, leaf = target.partition(".")
    return comp if comp in ("cathode", "anode") and leaf in PARENT_FIELDS else None


def declared_anode_parameters(doc, parameters):
    """补提正文明确采用的金属锂对电极；不扫描风险/文献/备选，不推断物性。"""
    if doc.get("research_only"):
        return {}
    if any(identity_path(p, r) and identity_path(p, r)[0] == "anode"
           and (r.get("value") is not None or r.get("locked")) for p, r in (parameters or {}).items() if isinstance(r, dict)):
        return {}
    text = doc.get("final_answer") or ""
    name = r"(?:金属锂(?:片|箔)?|锂金属(?:片|箔)?|Li\s*金属|Li\s+metal|lithium\s+metal|锂片|锂箔)"
    role = r"(?:对电极(?:/参比电极)?|反电极|负极)"
    forward = re.compile(role + r"\s*(?:为|采用|使用|[:：])\s*(?P<name>" + name + r")(?![A-Za-z])", re.I)
    reverse = re.compile(r"(?P<name>" + name + r")\s*" + role, re.I)
    active = False
    found = []
    for line in text.splitlines():
        if re.match(r"^#{1,6}\s", line):
            active = bool(re.search(r"用户锁定|电池|电极|装配|推荐|主方案|材料组成|配方", line))
            if re.search(r"文献|风险|对照|候选|假设|数据缺口", line):
                active = False
            continue
        if not active or re.search(r"文献|报道|风险|若|如果|例如|假设|备选|候选|不(?:采用|使用|是|含|得)|禁止|无锂|可(?:选|用)|或", line):
            continue
        match = forward.search(line) or reverse.search(line)
        if match:
            found.append((line, match["name"]))
    if not found:
        return {}
    quote, value = found[0]
    return {"anode.material": canonical_field("anode.material", {
        "value": value, "unit": "", "basis": "engineering_proposal", "source": "当前正文明确对电极声明",
        "source_quote": quote, "meaning": "当前锂金属半电池采用的对电极材料；不推断几何或实测物性", "conditions": {}})}


def _current_identity(row):
    # 普通温度/倍率条件仍可保留；明确备选、待定或不完整配方不得晋升为身份。
    if row.get("scope") not in (None, "main", "current", "primary"):
        return False
    if row.get("status") not in (None, "clear", "accepted", "approved", "complete"):
        return False
    if any(row.get(k) is True for k in ("conditional", "candidate", "control")) or row.get("complete") is False:
        return False
    conditions = row.get("conditions")
    return not (isinstance(conditions, dict) and any(conditions.get(k) is True for k in ("conditional", "candidate", "control")))


def assemble_components(scheme, parameters, *, strict=True):
    """从已验收主方案参数投影组件，保留来源引用，不生成虚构的 .material 参数。"""
    result = canonical_scheme(scheme)
    identities = copy.deepcopy(result.get("component_identities") or {})
    errors = []
    for comp in COMPONENTS:
        declared = []
        has_source = False
        for path, row in (parameters or {}).items():
            if not isinstance(row, dict):
                continue
            info = identity_path(path, row)
            has_source = has_source or bool(info and info[0] == comp)
            if not _current_identity(row):
                continue
            value = row.get("value")
            if info and info[0] == comp and isinstance(value, str) and value.strip():
                kind = info[1]
                normalized = canonical_id(value) if kind == "material" else exact_material_id(value)
                declared.append((path, kind, value, normalized))
        # 显式材料是类别/名称；名义式可以同时描述其改性组成，不强行视作冲突。
        material = [d for d in declared if d[1] == "material"]
        choices = material or declared
        if choices:
            full = [d for d in declared if d[1] != "material"]
            known = {d[3] for d in declared if d[3] in _engine().alias_map}
            if (len({_normalized(d[3]) for d in choices}) != 1
                    or any(len({_normalized(d[3]) for d in full if d[1] == kind}) > 1
                           for kind in ("nominal_formula", "formulation"))
                    or material and len(known) > 1):
                errors.append(f"组件身份声明冲突：{comp}（{', '.join(d[0] for d in declared)}）")
                result.pop(comp, None)
                identities.pop(comp, None)
                continue
            choices.sort(key=lambda d: (d[1] != "nominal_formula", d[0]))
            _, kind, _, value = choices[0]
            # 同值的名义式/配方允许共存；身份引用仍保持与选定 kind 一致。
            choices = [d for d in choices if d[1] == kind]
            result[comp] = value
            identities[comp] = {"kind": kind, "value": value,
                                "parameter_refs": [d[0] for d in choices],
                                "source_values": {d[0]: d[2] for d in choices}}
        elif comp in identities or has_source:
            # 派生身份的来源被撤回/转为备选时，不能保留上一轮的材料。
            result.pop(comp, None)
            identities.pop(comp, None)
    parents = {}
    for comp in ("cathode", "anode"):
        declarations = {path: row["value"] for path, row in (parameters or {}).items()
                        if isinstance(row, dict) and parent_path(path, row) == comp and _current_identity(row)
                        and isinstance(row.get("value"), str) and row["value"].strip()}
        if not result.get(comp) or not declarations:
            continue
        values = {exact_material_id(v) for v in declarations.values()}
        if len(values) != 1:
            errors.append(f"母体声明冲突：{comp}（{', '.join(declarations)}）")
            continue
        parent_value = next(iter(values))
        known_material = exact_material_id(result[comp])
        if (known_material in _engine().alias_map and parent_value in _engine().alias_map
                and known_material != parent_value):
            errors.append(f"材料与声明母体冲突：{comp}（{known_material} / {parent_value}）")
        parents[comp] = {"value": parent_value, "material_value": result[comp],
                         "parameter_refs": sorted(declarations), "source_values": declarations}
    if parents:
        result["rule_parents"] = parents
    else:
        result.pop("rule_parents", None)
    if errors and strict:
        raise ValueError("; ".join(errors))
    if identities:
        result["component_identities"] = identities
    else:
        result.pop("component_identities", None)
    if errors:
        result["component_identity_errors"] = errors
    else:
        result.pop("component_identity_errors", None)
    return result


def component_signature(scheme):
    """身份值及匹配口径变更都使旧约束校验失效；仅来源引用变化不等于换材料。"""
    scheme = scheme or {}
    identities = scheme.get("component_identities") or {}
    parents = scheme.get("rule_parents") or {}
    return {comp: (scheme.get(comp), identities.get(comp, {}).get("kind") in ("nominal_formula", "formulation"),
                   parents.get(comp), scheme.get("component_identity_errors"))
            for comp in ("cathode", "anode", "electrolyte")}


def canonical_scheme(scheme):
    result = copy.deepcopy(scheme or {})
    for comp in COMPONENTS:
        if comp in result:
            identity = (result.get("component_identities") or {}).get(comp, {})
            normalizer = exact_material_id if identity.get("kind") in ("nominal_formula", "formulation") else canonical_id
            result[comp] = normalizer(result[comp])
            if identity:
                identity["value"] = normalizer(identity.get("value"))
                if identity.get("kind") == "material":
                    identity["source_values"] = {p: canonical_id(v) for p, v in (identity.get("source_values") or {}).items()}
    return result


def canonical_requirements(requirements):
    result = copy.deepcopy(requirements or {})
    for item in result.get("items", []):
        if isinstance(item, dict) and isinstance(item.get("binding"), dict):
            item["binding"] = canonical_field(item["binding"].get("path"), item["binding"])
    return result


def canonical_document(doc):
    """只更新当前结构化容器；原文、审核历史、失败记录及证据档案不做文本替换。"""
    result = copy.deepcopy(doc)
    if not isinstance(result, dict):
        return result
    if isinstance(result.get("scheme"), dict):
        result["scheme"] = canonical_scheme(result["scheme"])
    for key in ("parameters", "scheme_parameters", "repair_parameters"):
        if isinstance(result.get(key), dict):
            result[key] = canonical_parameters(result[key])
    if isinstance(result.get("ledger_view"), dict):
        view = result["ledger_view"]
        view["parameters"] = canonical_parameters(view.get("parameters"))
        view["material_id_version"] = VERSION
    if isinstance(result.get("requirements"), dict):
        result["requirements"] = canonical_requirements(result["requirements"])
    if isinstance(result.get("plan"), dict) and isinstance(result["plan"].get("requirements"), dict):
        result["plan"]["requirements"] = canonical_requirements(result["plan"]["requirements"])
    if isinstance(result.get("research_draft"), dict):
        result["research_draft"] = canonical_document(result["research_draft"])
    research = result.get("research")
    if isinstance(research, dict) and isinstance(research.get("bound_fields"), dict):
        research["bound_fields"] = canonical_parameters(research["bound_fields"])
    result["material_id_version"] = VERSION
    return result


def canonical_experiment(record):
    result = copy.deepcopy(record)
    if isinstance(result.get("parameters"), dict):
        result["parameters"] = canonical_parameters(result["parameters"])
    if isinstance(result.get("fixed_fields"), dict):
        result["fixed_fields"] = {path: canonical_id(value) if path in FIELDS else value
                                  for path, value in result["fixed_fields"].items()}
    for group in result.get("groups", []):
        if isinstance(group, dict) and isinstance(group.get("changes"), dict):
            group["changes"] = canonical_parameters(group["changes"])
    return result


def _normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold()


def source_matches(path, value, source):
    """主方案参数可对应原句中的已知同义材料名，其他参数仍按原值定位。"""
    if value is None or not isinstance(source, str):
        return False
    needle, haystack = _normalized(value), _normalized(source)
    if needle and needle in haystack:
        return True
    if path not in FIELDS:
        return False
    canonical = canonical_id(value)
    aliases = _engine().alias_map.get(canonical, [])
    if not isinstance(aliases, list):
        return False
    extra = list(LITHIUM_METAL_NAMES) if canonical == "li_metal" else []
    return any((len(token) >= 2 and token in haystack) or token == haystack
               for token in (_normalized(alias) for alias in [canonical] + aliases + extra) if token)


def document_id_errors(doc):
    """新写入契约须使用规范ID；旧产物通过显式迁移，不在读取时偷偷改写。"""
    errors = []
    if doc.get("material_id_version") != VERSION:
        return errors
    scheme = doc.get("scheme") or {}
    errors.extend(scheme.get("component_identity_errors") or [])
    parameters = {**((doc.get("ledger_view") or {}).get("parameters") or {}),
                  **(doc.get("parameters") or {}), **(doc.get("scheme_parameters") or {})}
    for comp, value in scheme.items():
        identity = (scheme.get("component_identities") or {}).get(comp, {})
        normalizer = exact_material_id if identity.get("kind") in ("nominal_formula", "formulation") else canonical_id
        if comp in COMPONENTS and normalizer(value) != value:
            errors.append(f"材料标识未归一化：scheme.{comp}")
        if identity:
            refs = identity.get("parameter_refs") or []
            sources = identity.get("source_values") or {}
            if (identity.get("value") != value or not refs or set(refs) != set(sources)
                    or any(p not in parameters or parameters[p].get("value") != sources[p]
                           or not _current_identity(parameters[p])
                           or identity_path(p, parameters[p]) != (comp, identity.get("kind"))
                           or normalizer(sources[p]) != value for p in refs)):
                errors.append(f"组件身份来源失效：scheme.{comp}")
    for comp, parent in (scheme.get("rule_parents") or {}).items():
        if not isinstance(parent, dict):
            errors.append(f"母体规则来源失效：scheme.{comp}")
            continue
        refs = parent.get("parameter_refs") or []
        sources = parent.get("source_values") or {}
        if (comp not in ("cathode", "anode") or parent.get("material_value") != scheme.get(comp)
                or not refs or set(refs) != set(sources)
                or any(p not in parameters or parent_path(p, parameters[p]) != comp
                       or not _current_identity(parameters[p]) or parameters[p].get("value") != sources[p]
                       or exact_material_id(sources[p]) != parent.get("value") for p in refs)):
            errors.append(f"母体规则来源失效：scheme.{comp}")
    for collection in ("parameters", "scheme_parameters"):
        for path, row in (doc.get(collection) or {}).items():
            if isinstance(row, dict) and identity_path(path, row) and identity_path(path, row)[1] == "material" and canonical_id(row.get("value")) != row.get("value"):
                errors.append(f"材料标识未归一化：{collection}.{path}")
    for item in (doc.get("requirements") or {}).get("items", []):
        binding = item.get("binding") if isinstance(item, dict) else None
        if isinstance(binding, dict) and binding.get("path") in FIELDS and canonical_id(binding.get("value")) != binding.get("value"):
            errors.append("材料标识未归一化：requirements." + binding["path"])
    geometry = (doc.get("scheme") or {}).get("geometry") or {}
    if geometry.get("material_ids") and geometry["material_ids"] != {comp: (doc.get("scheme") or {}).get(comp) for comp in ("cathode", "anode")}:
        errors.append("当前材料与几何契约材料身份不一致，须重新验证几何来源")
    return errors


def checkpoint_identity(goal, ledger, provenance):
    """生成和迁移使用同一检查点身份，包含现有科研契约版本，不重置任何预算。"""
    from .scheme_ledger import digest
    from .research_contract import VERSION as RESEARCH_VERSION
    from .adaptive_retrieval import VERSION as RETRIEVAL_VERSION
    provenance = copy.deepcopy(provenance)
    provenance.pop("recorded_at", None)
    provenance["research_contract_version"] = RESEARCH_VERSION
    provenance["adaptive_retrieval_version"] = RETRIEVAL_VERSION
    return digest({"goal": goal, "ledger": ledger, "provenance": provenance})
