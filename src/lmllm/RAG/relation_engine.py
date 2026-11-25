"""关系绑定引擎 — 高比能液态锂电池设计方案系统（纯规则，无 LLM 依赖）

承载三类关系数据（JSON 表形式）：
  - alias_map.json      实体归一化（谁对应谁）
  - candidates.json     候选材料属性（谁有哪些参数）
  - constraints.json    层级约束规则（v2.4: C1/C3 为建议，C2 保留质询，C4 已移除；
                        支持 challenge / recommend / audit_note / coin_cell_eval /
                        flag_frontier 与兼容动作，及 C0_UNVERIFIED_MATERIAL 校验）

管线中的两个插桩点（接入 RAG 管线）：
  插桩 A（检索前）: query_modifiers()   → 生成 exclude_terms / boost_terms 检索修饰词
  插桩 B（生成后）: check_scheme()      → Reviewer 规则校验（rule_checks / energy_check / condition_missing / confidence）

所有方法失败时返回保守结果，不抛异常阻断调用方（fail-closed 降级策略）。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .energy_model import estimate_scheme_energy, check_energy_claim
from auto_battery_research.util.electrolyte_composition import classify_electrolyte, looks_like_formulation, recipe_literal_key

DATA_DIR = Path(__file__).resolve().parent / "data"

# 规则版本标识：修改 alias_map/candidates/constraints 数据或约束求值逻辑时务必同步递增，
# 供 Stage 4 方案产物固化溯源 (design_scheme.json 的 provenance.rules_version)。
RULES_VERSION = "C1-C8/v2.4-c1-c3-advisory"

# 数值-条件绑定检测：出现数值单位但全文无任何条件词 → condition_missing
_NUM_UNIT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:mAh/g|mAh g|Wh/kg|Wh kg|%)", re.I)
_CONDITION_RE = re.compile(r"\d+(?:\.\d+)?\s*C\b|°C|温度|\d+(?:\.\d+)?\s*V\b|cycle|循环|倍率|rate|窗口", re.I)


class RelationEngine:
    """别名归一 + 约束求值 + 组合校验。"""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.alias_map: Dict[str, List[str]] = {}
        self.candidates: Dict = {}
        self.constraints: Dict = {}
        self.electrolyte_families: Dict = {}
        self._alias_index: Dict[str, str] = {}  # 小写别名 → canonical id
        self._load()

    # ────────────────────────── 加载 ──────────────────────────

    def _load(self) -> None:
        try:
            self.alias_map = json.loads(
                (self.data_dir / "alias_map.json").read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[relation_engine] alias_map 加载失败: {e}")
        try:
            self.candidates = json.loads(
                (self.data_dir / "candidates.json").read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[relation_engine] candidates 加载失败: {e}")
        try:
            self.constraints = json.loads(
                (self.data_dir / "constraints.json").read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[relation_engine] constraints 加载失败: {e}")
        try:
            self.electrolyte_families = json.loads((self.data_dir / "electrolyte_families.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"[relation_engine] 电解液组成分类表不可用，保持精确登记检查: {e}")
        self._build_alias_index()

    def _build_alias_index(self) -> None:
        self._alias_index = {}
        for canonical, aliases in self.alias_map.items():
            self._alias_index[canonical.lower()] = canonical
            for a in aliases:
                key = a.strip().lower()
                if key and key not in self._alias_index:
                    self._alias_index[key] = canonical

    # ────────────────────────── 实体归一化 ──────────────────────────

    def normalize(self, text: str) -> Optional[str]:
        """字符串 → canonical id（精确/包含匹配；未命中返回 None）。"""
        t = text.strip().lower()
        if not t:
            return None
        if t in self._alias_index:
            return self._alias_index[t]
        # 包含匹配（长别名优先，避免 "C" 误命中）
        for alias, canonical in sorted(
            self._alias_index.items(), key=lambda kv: -len(kv[0])
        ):
            if len(alias) >= 2 and alias in t:
                return canonical
        return None

    def extract_entities(self, question: str) -> Dict[str, List[str]]:
        """从问题文本提取材料实体，按组件分类返回。

        Returns: {"cathode": ["NCM811"], "anode": [...], "electrolyte": [...]}
        """
        found: Dict[str, List[str]] = {}
        q = question.lower()
        for alias, canonical in sorted(
            self._alias_index.items(), key=lambda kv: -len(kv[0])
        ):
            if len(alias) < 2 or alias not in q:
                continue
            cat = self._category_of(canonical)
            if cat not in found:
                found[cat] = []
            if canonical not in found[cat]:
                found[cat].append(canonical)
        return found

    def _category_of(self, canonical: str) -> str:
        for cat in ("cathode", "anode", "electrolyte", "additive"):
            if any(m.get("id") == canonical for m in self.candidates.get(cat, [])):
                return cat
        return "unknown"

    # ────────────────────────── 约束求值 ──────────────────────────

    def _exact_id(self, value):
        """母体及完整组成按精确别名识别，允许空白/全半角差异。"""
        def token(v):
            return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(v))).casefold()
        matches = {canonical for alias, canonical in self._alias_index.items() if token(alias) == token(value)}
        return next(iter(matches)) if len(matches) == 1 else None

    def _parent_id(self, scheme: Dict, field: str):
        parent = (scheme.get("rule_parents") or {}).get(field)
        if field not in ("cathode", "anode") or not isinstance(parent, dict):
            return None
        refs, sources = parent.get("parameter_refs") or [], parent.get("source_values") or {}
        if (not isinstance(refs, list) or not isinstance(sources, dict)
                or any(not isinstance(p, str) for p in refs)):
            return None
        canonical = self._exact_id(parent.get("value"))
        if (parent.get("material_value") != scheme.get(field) or not refs or set(refs) != set(sources)
                or not canonical or self._category_of(canonical) != field
                or any(self._exact_id(sources[p]) != canonical for p in refs)):
            return None
        return canonical

    def _scheme_material_id(self, scheme: Dict, field: str):
        """电极按母体、完整电解液按已覆盖组成分类；未命中禁止盐子串回退。"""
        if field in ("cathode", "anode") and field in (scheme.get("rule_parents") or {}):
            return self._parent_id(scheme, field)
        value = scheme.get(field)
        identity = (scheme.get("component_identities") or {}).get(field, {})
        if field == "electrolyte" and looks_like_formulation(value):
            return self._electrolyte_match(value)[0]
        if identity.get("kind") in ("nominal_formula", "formulation"):
            return self._exact_id(value)
        return self.normalize(str(value)) if value is not None else None

    def _electrolyte_match(self, value):
        composition = classify_electrolyte(value, self.electrolyte_families)
        family = composition.get("rule_material_id")
        if family and self._category_of(family) == "electrolyte":
            return family, "composition", composition
        token = recipe_literal_key(value)
        exact_matches = {canonical for canonical, aliases in self.alias_map.items() if isinstance(aliases, list)
                         and any(recipe_literal_key(alias) == token for alias in [canonical] + aliases)}
        exact = next(iter(exact_matches)) if len(exact_matches) == 1 else None
        # 精确登记的具体配方继续可用；盐/浓度的宽类别别名不能绕过组成解析。
        generic = {"carbonate_ec", "high_concentration", "lhce", "fluorinated", "ether_ec", "dilute_aqueous", "water_in_salt"}
        if exact and exact not in generic and self._category_of(exact) == "electrolyte":
            return exact, "direct", composition
        return None, "composition", composition

    def resolve_scheme(self, scheme: Dict) -> Dict:
        """归一方案中的材料 id，并注入候选表属性（供 trigger 点路径求值）。"""
        resolved: Dict = dict(scheme)
        for field in ("cathode", "anode", "electrolyte"):
            v = scheme.get(field)
            if v is None:
                continue
            canonical = self._scheme_material_id(scheme, field) or v
            resolved[field] = canonical
            mat = self._material(canonical)
            if mat:
                resolved[f"{field}_attrs"] = mat
            if field == "electrolyte" and looks_like_formulation(v):
                _, _, composition = self._electrolyte_match(v)
                resolved["electrolyte_composition"] = composition
        return resolved

    def _material(self, canonical: str) -> Optional[Dict]:
        for cat in ("cathode", "anode", "electrolyte", "additive"):
            for m in self.candidates.get(cat, []):
                if m.get("id") == canonical:
                    return m
        return None

    def _resolve_field(self, resolved: Dict, field: str) -> Optional[object]:
        """点路径求值: cathode.voltage_limit → 查候选表属性。"""
        if "." in field:
            head, _, attr = field.partition(".")
            attrs = resolved.get(f"{head}_attrs")
            if isinstance(attrs, dict):
                return attrs.get(attr)
            return None
        if field in ("target_energy", "target_energy_density"):
            for k in (
                "target_energy",
                "target_energy_wh_kg",
                "target_energy_density",
                "claimed_energy",
                "energy_density_wh_kg",
                "theoretical_energy_density",
            ):
                if resolved.get(k) is not None:
                    return resolved.get(k)
        return resolved.get(field)

    @staticmethod
    def _op_matches(op: str, actual: Optional[object], expected: object) -> bool:
        if actual is None:
            return False
        if op == "gt":
            return float(actual) > float(expected)
        if op == "gte":
            return float(actual) >= float(expected)
        if op == "lt":
            return float(actual) < float(expected)
        if op == "lte":
            return float(actual) <= float(expected)
        if op == "eq":
            return str(actual) == str(expected)
        if op == "in":
            return actual in (expected or [])
        if op == "not_in":
            return actual not in (expected or [])
        if op == "contains":
            if isinstance(actual, (list, tuple, set)):
                return expected in actual or any(str(expected).lower() in str(x).lower() for x in actual)
            return str(expected).lower() in str(actual).lower()
        return False

    @staticmethod
    def _has_mitigation(scheme: Dict, resolved: Dict, whitelist: List[str]) -> Tuple[bool, List[str]]:
        """检查方案中是否包含白名单中声明的改性策略/添加剂/界面工程."""
        matched = []
        texts_to_check = []

        def _append_val(val: object) -> None:
            if isinstance(val, (list, tuple, set)):
                for item in val:
                    _append_val(item)
            elif isinstance(val, dict):
                for dk, dv in val.items():
                    if dv is not None:
                        texts_to_check.append(f"{dk}:{dv}".lower())
            elif val:
                texts_to_check.append(str(val).lower())

        for k in (
            "cathode", "anode", "electrolyte", "additives", "additive",
            "notes", "strategy", "modifications", "user_locked_components", "design_anchors",
        ):
            if k == "electrolyte" and resolved.get("electrolyte_composition") is not None:
                composition = resolved["electrolyte_composition"]
                if composition.get("complete"):
                    # 仅明确的正加量成分可作为质询对策；未知尾部不参与子串匹配。
                    _append_val([r["name"] for r in composition["additives"] if r.get("amount", {}).get("value", 0) > 0])
                    _append_val([r["name"] for r in composition["solvents"]])
                if resolved.get("electrolyte") != scheme.get("electrolyte"):
                    _append_val(resolved.get("electrolyte"))
                continue
            v = scheme.get(k)
            if v:
                _append_val(v)
            v_res = resolved.get(k)
            if v_res and v_res != v:
                _append_val(v_res)

        full_text = " ".join(texts_to_check)

        for item in whitelist:
            prefix, _, token = item.partition(":")
            token = token.lower() if token else item.lower()
            aliases = [token]
            if token == "fec":
                aliases.extend(["氟代碳酸乙烯酯", "fluoroethylene carbonate"])
            elif token == "vc":
                aliases.extend(["碳酸亚乙烯酯", "vinylene carbonate"])
            elif token == "lipo2f2":
                aliases.extend(["二氟磷酸锂", "difluorophosphate"])
            elif token == "single_crystal":
                aliases.extend(["单晶", "sc-", "single crystal"])
            elif token == "artificial_sei":
                aliases.extend(["人工sei", "人工界面", "保护层", "artificial sei"])
            elif token in ("alloy_li_ag", "li-ag", "las"):
                aliases.extend(["li-ag", "las", "合金", "亲锂"])
            elif token == "lithiophilic_framework":
                aliases.extend(["亲锂", "三维", "3d", "骨架"])
            elif token in ("lpo", "al2o3"):
                aliases.extend(["包覆", "coating", "快离子"])

            for a in aliases:
                if a in full_text:
                    matched.append(item)
                    break

        if not matched:
            has_interface_whitelist = any(
                str(w).startswith(("additive:", "coating:", "morphology:", "anode_mod:", "anode:", "electrolyte:"))
                for w in (whitelist or [])
            )
            user_locked = scheme.get("user_locked_components") or []
            if has_interface_whitelist and "electrolyte" in user_locked and bool(scheme.get("modifications")):
                return True, ["user_locked_baseline_with_modification"]

        return len(matched) > 0, matched

    def evaluate(self, scheme: Dict) -> Dict:
        """对方案求值全部约束 (软质询矩阵 · 实验室扣电科研版).

        Returns:
          feasible:        bool，无 reject（含未登记材料）且无违规（exclude / 未缓解 challenge / coin_cell_eval 命中）→ True
          violations:      [{"rule_id", "reason", "excluded"}]
          inclusions:      [{"rule_id", "required"}]
          rejects:         [{"rule_id", "reason"}]
          warnings:        [{"rule_id", "reason"}]
          challenges:      [{"rule_id", "status", "mitigated_by", "note"}]
          recommendations: [{"rule_id", "reason"}]
          audit_notes:     [{"rule_id", "reason"}]
          coin_cell_evals: [{"rule_id", "material_energy_wh_kg", "assessment", "note"}]
          frontier_flags:  [{"rule_id", "target_energy", "note"}]
          unverified:      [str]
        """
        resolved = self.resolve_scheme(scheme)
        out: Dict = {
            "feasible": True,
            "violations": [],
            "inclusions": [],
            "rejects": [],
            "warnings": [],
            "challenges": [],
            "recommendations": [],
            "audit_notes": [],
            "coin_cell_evals": [],
            "frontier_flags": [],
            "unverified": [],
            "material_matches": {},
        }

        # 登记范围检查：电极可按声明母体匹配，不能把未登记解释为实验不可行。
        unverified_mats = []
        for field in ("cathode", "anode", "electrolyte"):
            raw_val = scheme.get(field)
            if raw_val is not None:
                canonical = self._scheme_material_id(scheme, field)
                parent = (scheme.get("rule_parents") or {}).get(field)
                recognized = bool(canonical and self._material(canonical))
                composition, basis = None, "parent" if parent else "direct"
                if field == "electrolyte" and looks_like_formulation(raw_val):
                    _, basis, composition = self._electrolyte_match(raw_val)
                out["material_matches"][field] = {"declared_value": raw_val, "rule_material_id": canonical,
                    "basis": basis, "matched": recognized,
                    "parent_declaration": parent}
                if composition is not None:
                    out["material_matches"][field]["composition"] = composition
                if basis == "composition" and recognized:
                    out["audit_notes"].append({"rule_id": "C0_COMPOSITION_MATCH", "component": field,
                        "reason": f"电解液按已解析组成归类为 {canonical}，原配方、浓度、比例和添加成分保留；"
                                  "只用于相容性规则，不代表完整配方已验证或获得PINN适用性。"})
                if parent and recognized:
                    out["audit_notes"].append({"rule_id": "C0_PARENT_MATCH", "component": field,
                        "reason": f"{field} 按声明母体 {canonical} 匹配材料类别与相容性规则，掺杂配方无需单独登记；"
                                  "母体匹配不验证掺杂位点、性能或协同效应，不将母体物性或PINN模型用于该掺杂配方。"})
                if not canonical or not self._material(canonical):
                    label = f"{raw_val}（声明母体：{parent.get('value')}）" if isinstance(parent, dict) else raw_val
                    unverified_mats.append(f"{field}:{label}")
        if unverified_mats:
            out["unverified"] = unverified_mats
            out["rejects"].append({
                "rule_id": "C0_UNVERIFIED_MATERIAL",
                "reason": f"材料或声明母体未匹配规则库 [{', '.join(unverified_mats)}]，无法完成规则判定；未登记不等于实验不可行。",
            })
            out["feasible"] = False
        if scheme.get("component_identity_errors"):
            out["rejects"].append({"rule_id": "C0_DECLARATION_CONFLICT",
                                   "reason": "; ".join(scheme["component_identity_errors"])})
            out["feasible"] = False

        for rule in self.constraints.get("rules", []):
            rule_id = rule.get("id")
            trig = rule.get("trigger", {})
            actual = self._resolve_field(resolved, trig.get("field", ""))
            if not self._op_matches(trig.get("op", "eq"), actual, trig.get("value")):
                continue

            cond = rule.get("condition")
            if cond:
                cond_actual = self._resolve_field(resolved, cond.get("field", ""))
                if not self._op_matches(cond.get("op", "eq"), cond_actual, cond.get("value")):
                    continue

            action = rule.get("action")
            reason = rule.get("reason", "")

            if action == "reject":
                out["rejects"].append({"rule_id": rule_id, "reason": reason})
                out["feasible"] = False

            elif action in ("exclude", "include"):
                targets = self._parse_targets(rule.get("target", ""))
                hit = self._targets_hit_scheme(targets, resolved)
                if action == "exclude" and hit:
                    out["violations"].append({
                        "rule_id": rule_id,
                        "reason": reason,
                        "excluded": [t[1] for t in hit],
                    })
                    out["feasible"] = False
                elif action == "include":
                    missing = self._targets_missing(targets, resolved)
                    if missing:
                        out["inclusions"].append({
                            "rule_id": rule_id,
                            "reason": reason,
                            "required": missing,
                        })

            elif action == "challenge":
                # 自洽性质询（C2）：声明白名单对策即放行，纯裸体系且无保护措施则硬拦截。
                # C1/C3 使用 recommend 分支，建议不进入违规或强制降级。
                whitelist = rule.get("mitigation_whitelist", [])
                has_mit, matched = self._has_mitigation(scheme, resolved, whitelist)
                if has_mit:
                    out["challenges"].append({
                        "rule_id": rule_id,
                        "status": "passed",
                        "mitigated_by": matched,
                        "note": f"检测到相应界面/改性对策: {matched}",
                    })
                    if matched == ["user_locked_baseline_with_modification"]:
                        audit_reason = (
                            "用户课题显式锁定常规碳酸酯作为改性正极扣电测试基线；"
                            "鉴于 >4.3V 及锂金属界面副反应，建议以同批未掺杂样品为相对基准评估保持率，"
                            "并将 LHCE/含氟添加剂列为界面对照体系"
                        )
                        if not any(n.get("reason") == audit_reason for n in out["audit_notes"]):
                            out["audit_notes"].append({
                                "rule_id": rule_id,
                                "reason": audit_reason,
                            })
                else:
                    cond_val = str((cond or {}).get("value", ""))
                    excluded = ["carbonate_ec"] if "carbonate" in cond_val else ([cond_val] if cond_val else [])
                    out["violations"].append({
                        "rule_id": rule_id,
                        "reason": reason,
                        "excluded": excluded,
                    })
                    out["feasible"] = False

            elif action == "warn":
                out["warnings"].append({
                    "rule_id": rule_id,
                    "reason": reason,
                })

            elif action == "recommend":
                out["recommendations"].append({
                    "rule_id": rule_id,
                    "reason": reason,
                })
                target = rule.get("target")
                if target:
                    targets = self._parse_targets(target)
                    missing = self._targets_missing(targets, resolved)
                    if missing:
                        out["inclusions"].append({
                            "rule_id": rule_id,
                            "reason": reason,
                            "required": missing,
                        })

            elif action == "audit_note":
                out["audit_notes"].append({
                    "rule_id": rule_id,
                    "reason": reason,
                })

            elif action == "coin_cell_eval":
                # 纽扣电池材料级核算（C7）
                anode = resolved.get("anode")
                target_e = float(actual) if actual is not None else 0.0
                if anode == "graphite" and target_e >= 400:
                    # 石墨负极理论比容量仅 372 mAh/g，纽扣电池材料级与全电池均无法达到 >=400 Wh/kg
                    out["violations"].append({
                        "rule_id": rule_id,
                        "reason": "石墨负极理论比容量仅 372 mAh/g，纽扣电池材料级与电芯级能量密度均无法达到 ≥400 Wh/kg（该指标必须采用锂金属或高硅基负极）",
                        "excluded": ["graphite"],
                    })
                    out["feasible"] = False
                elif self._parent_id(scheme, "cathode") and resolved.get("cathode") != scheme.get("cathode"):
                    out["coin_cell_evals"].append({"rule_id": rule_id, "material_energy_wh_kg": None,
                        "assessment": "not_evaluated", "basis": "parent_classification_only",
                        "note": "母体仅用于材料类别规则；掺杂后的容量与平均电压未由母体继承，不能据母体数值认定能量目标可行。"})
                else:
                    cat_mat = resolved.get("cathode_attrs") or {}
                    cap = (cat_mat.get("capacity") or {}).get("value") or 200
                    volt = cat_mat.get("avg_voltage") or 3.8
                    mat_e = volt * cap
                    out["coin_cell_evals"].append({
                        "rule_id": rule_id,
                        "material_energy_wh_kg": mat_e,
                        "assessment": "passed",
                        "note": reason,
                    })

            elif action == "flag_frontier":
                out["frontier_flags"].append({
                    "rule_id": rule_id,
                    "target_energy": actual,
                    "note": reason,
                })

        return out

    @staticmethod
    def _parse_targets(target: str) -> List[Tuple[str, str]]:
        """'electrolyte:carbonate_ec' / 'electrolyte:[a, b]' → [(cat, id), ...]"""
        if not target:
            return []
        cat, _, rest = target.partition(":")
        if rest.startswith("[") and rest.endswith("]"):
            ids = [x.strip() for x in rest[1:-1].split(",") if x.strip()]
        else:
            ids = [rest.strip()]
        return [(cat, i) for i in ids if i]

    @staticmethod
    def _targets_hit_scheme(
        targets: List[Tuple[str, str]], resolved: Dict
    ) -> List[Tuple[str, str]]:
        """方案当前是否包含被排除目标（exclude 命中 → 违规）。"""
        hit = []
        for cat, tid in targets:
            if cat == "electrolyte":
                vals = [resolved.get("electrolyte")]
            elif cat == "anode":
                vals = [resolved.get("anode")]
            elif cat == "cathode":
                vals = [resolved.get("cathode")]
            elif cat == "additive":
                vals = resolved.get("additives") or []
            else:
                vals = [resolved.get(cat)]
            if any(str(v) == tid for v in vals if v is not None):
                hit.append((cat, tid))
        return hit

    @staticmethod
    def _targets_missing(
        targets: List[Tuple[str, str]], resolved: Dict
    ) -> List[str]:
        """include / recommend 目标中方案缺失的项（列表 target 语义：至少命中一个）。"""
        groups: Dict[str, List[str]] = {}
        for cat, tid in targets:
            groups.setdefault(cat, []).append(tid)
        missing = []
        for cat, tids in groups.items():
            if cat == "electrolyte":
                vals = [resolved.get("electrolyte")]
            elif cat == "additive":
                vals = resolved.get("additives") or []
            else:
                vals = [resolved.get(cat)]
            vals = [str(v) for v in vals if v is not None]
            if not any(tid in vals for tid in tids):
                missing.append(f"{cat}:{'/'.join(tids)}")
        return missing

    # ────────────────────────── 插桩 A：检索过滤 ──────────────────────────

    def query_modifiers(self, scheme: Dict) -> Dict[str, List[str]]:
        """从方案/问题实体生成检索修饰词。

        Returns: {"exclude_terms": [...], "boost_terms": [...]}
        exclude/boost 词为候选材料的中英文别名（用于段落文本匹配）。
        """
        resolved = self.resolve_scheme(scheme)
        exclude_ids, boost_ids = set(), set()
        for rule in self.constraints.get("rules", []):
            trig = rule.get("trigger", {})
            actual = self._resolve_field(resolved, trig.get("field", ""))
            if not self._op_matches(trig.get("op", "eq"), actual, trig.get("value")):
                continue
            cond = rule.get("condition")
            if cond:
                cond_actual = self._resolve_field(resolved, cond.get("field", ""))
                # 检索前置阶段（如问题只给正负极、未选定电解液）允许条件字段缺省时仍生成引导修饰词
                if cond_actual is not None and not self._op_matches(
                    cond.get("op", "eq"), cond_actual, cond.get("value")
                ):
                    continue

            action = rule.get("action")
            if action == "exclude":
                exclude_ids.update(t for _, t in self._parse_targets(rule.get("target", "")))
            elif action in ("include", "recommend"):
                boost_ids.update(t for _, t in self._parse_targets(rule.get("target", "")))
                # 建议仅提升相关措施的证据排序，不给原配方相关文献降权。
                for item in rule.get("retrieval_boost", []):
                    for cat, tid in self._parse_targets(item):
                        if cat in ("electrolyte", "additive") and tid in self.alias_map:
                            boost_ids.add(tid)
            elif action in ("challenge", "warn"):
                whitelist = rule.get("mitigation_whitelist", [])
                has_mit, _ = self._has_mitigation(scheme, resolved, whitelist)
                if not has_mit and cond and cond.get("field") == "electrolyte" and "carbonate" in str(cond.get("value", "")):
                    exclude_ids.add("carbonate_ec")
                for item in list(whitelist) + list(rule.get("retrieval_boost", [])):
                    for cat, tid in self._parse_targets(item):
                        if cat in ("electrolyte", "additive") and tid in self.alias_map:
                            boost_ids.add(tid)
            elif action == "coin_cell_eval":
                if resolved.get("anode") in (None, "graphite"):
                    exclude_ids.add("graphite")
                if resolved.get("anode") is None:
                    boost_ids.update(["li_metal", "si_base"])

        return {
            "exclude_terms": self._ids_to_terms(exclude_ids),
            "boost_terms": self._ids_to_terms(boost_ids),
        }

    def _ids_to_terms(self, ids) -> List[str]:
        terms: List[str] = []
        seen = set()
        for cid in sorted(ids):
            for candidate in [cid, *self.alias_map.get(cid, [])]:
                clean = str(candidate).strip()
                # 过滤过短别名（如 graphite 的 "C"），防止在检索文本匹配中全域误杀
                if len(clean) >= 2 and clean not in seen:
                    seen.add(clean)
                    terms.append(clean)
        return terms

    # ────────────────────────── 插桩 B：方案校验 ──────────────────────────

    def check_scheme(
        self,
        scheme: Dict,
        claimed_energy: Optional[float] = None,
        answer_text: Optional[str] = None,
    ) -> Dict:
        """Reviewer 规则校验（LLM 审核之前执行）。

        Returns:
          rule_checks: 规则评估结果字典（含 violations / inclusions 等字段）
          energy_check: ok / energy_mismatch / na
          condition_missing: bool
          confidence: high / medium / low（强制降级依据）
        """
        ev = self.evaluate(scheme)
        energy_check = "na"
        if claimed_energy is not None:
            est = estimate_scheme_energy(scheme, self.candidates)
            if est is None:
                energy_check = "na"
            else:
                energy_check = check_energy_claim(claimed_energy, est)
        condition_missing = (
            self.check_numeric_claims(answer_text) if answer_text else False
        )

        # 置信度：硬违规/能量不符 → low；条件缺失 → medium；否则 high
        if not ev["feasible"] or energy_check == "energy_mismatch":
            confidence = "low"
        elif condition_missing:
            confidence = "medium"
        else:
            confidence = "high"

        return {
            "rules_version": RULES_VERSION,
            "rule_checks": {
                "violations": ev["violations"],
                "inclusions": ev["inclusions"],
                "rejects": ev["rejects"],
                "warnings": ev.get("warnings", []),
                "challenges": ev.get("challenges", []),
                "recommendations": ev.get("recommendations", []),
                "audit_notes": ev.get("audit_notes", []),
                "coin_cell_evals": ev.get("coin_cell_evals", []),
                "frontier_flags": ev.get("frontier_flags", []),
                "material_matches": ev["material_matches"],
            },
            "energy_check": energy_check,
            "condition_missing": condition_missing,
            "confidence": confidence,
            "warnings": ev.get("warnings", []),
            "challenges": ev.get("challenges", []),
            "recommendations": ev.get("recommendations", []),
            "audit_notes": ev.get("audit_notes", []),
            "coin_cell_evals": ev.get("coin_cell_evals", []),
            "frontier_flags": ev.get("frontier_flags", []),
            "material_matches": ev["material_matches"],
        }

    @staticmethod
    def check_numeric_claims(text: str) -> bool:
        """数值-条件绑定检测：文本含性能数值单位但无任何条件词 → True。

        保守策略：规则层只做兜底拦截，误报由 LLM Reviewer 兜底复核。
        """
        if not text:
            return False
        has_num = bool(_NUM_UNIT_RE.search(text))
        has_cond = bool(_CONDITION_RE.search(text))
        return has_num and not has_cond


if __name__ == "__main__":
    # ── 自测：校验违规拦截、缓解/frontier 放行与 check_scheme 行为 ──
    eng = RelationEngine()

    cases = [
        # (名称, 方案, 期望 feasible)
        ("错误1: LRMO高压 + 常规碳酸酯", {"cathode": "LRMO", "anode": "graphite", "electrolyte": "carbonate_ec"}, False),
        ("错误2: 锂金属 + 常规碳酸酯", {"cathode": "NCM811", "anode": "li_metal", "electrolyte": "carbonate_ec"}, False),
        ("错误3: 400Wh/kg 用石墨负极", {"cathode": "NCM811", "anode": "graphite", "electrolyte": "carbonate_ec", "target_energy": 400}, False),
        ("正确: NCM811 + 石墨 + 碳酸酯", {"cathode": "NCM811", "anode": "graphite", "electrolyte": "carbonate_ec", "additives": ["VC"]}, True),
        ("正确: LRMO + 锂金属 + LHCE", {"cathode": "LRMO", "anode": "li_metal", "electrolyte": "lhce", "target_energy": 400}, True),
        ("前沿标记: 500Wh/kg 放行并标记 C8", {"cathode": "LRMO", "anode": "li_metal", "electrolyte": "lhce", "target_energy": 500}, True),
    ]
    n_pass = 0
    for name, scheme, expect in cases:
        ev = eng.evaluate(scheme)
        ok = ev["feasible"] == expect
        if "C8" in name:
            ok = ok and any(f.get("rule_id") == "C8" for f in ev.get("frontier_flags", []))
        n_pass += ok
        print(f"[{'OK' if ok else 'FAIL'}] {name}")
        if ev["violations"]:
            for v in ev["violations"]:
                print(f"        violation {v['rule_id']}: {v['reason']}")
        if ev["rejects"]:
            for r in ev["rejects"]:
                print(f"        reject {r['rule_id']}: {r['reason']}")
        if ev["inclusions"]:
            for i in ev["inclusions"]:
                print(f"        include {i['rule_id']}: 需补充 {i['required']}")

    print(f"\n自测通过 {n_pass}/{len(cases)}")

    # ── 实体归一化与插桩演示 ──
    q = "4.6V富锂锰基配锂金属负极，用什么电解液？"
    print("\n问题:", q)
    print("实体:", eng.extract_entities(q))
    mods = eng.query_modifiers({"cathode": "LRMO", "anode": "li_metal", "target_energy": 420})
    print("检索修饰(exclude):", mods["exclude_terms"][:6], "...")
    print("检索修饰(boost):", mods["boost_terms"][:6], "...")
    chk = eng.check_scheme(
        {"cathode": "LRMO", "anode": "li_metal", "electrolyte": "carbonate_ec"},
        claimed_energy=420,
        answer_text="该方案能量密度约420 Wh/kg",
    )
    print("方案校验:", chk["confidence"], "| energy:", chk["energy_check"], "| cond_missing:", chk["condition_missing"])
