"""关系绑定引擎 — 高比能液态锂电池设计方案系统（阶段 0，纯规则，无 LLM 依赖）

承载三类关系数据（JSON 表形式）：
  - alias_map.json      实体归一化（谁对应谁）
  - candidates.json     候选材料属性（谁有哪些参数）
  - constraints.json    硬约束（谁排除谁 / 谁纳入谁）

管线中的两个插桩点（阶段 3 接入 RAG）：
  插桩 A（检索前）: query_modifiers()   → exclude_terms / boost_terms 过滤检索结果
  插桩 B（生成后）: check_scheme()      → Reviewer 硬规则校验（violation / energy_mismatch / condition_missing）

所有方法失败时返回保守结果，不抛异常阻断调用方（降级策略）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .energy_model import estimate_scheme_energy, check_energy_claim

DATA_DIR = Path(__file__).resolve().parent / "data"

# 规则版本标识：修改 alias_map/candidates/constraints 数据或约束求值逻辑时务必同步递增，
# 供 Stage 4 方案产物固化溯源 (design_scheme.json 的 provenance.rules_version)。
RULES_VERSION = "C1-C8/v1"

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

    def resolve_scheme(self, scheme: Dict) -> Dict:
        """归一方案中的材料 id，并注入候选表属性（供 trigger 点路径求值）。"""
        resolved: Dict = dict(scheme)
        for field in ("cathode", "anode", "electrolyte"):
            v = scheme.get(field)
            if v is None:
                continue
            canonical = self.normalize(str(v)) or v
            resolved[field] = canonical
            mat = self._material(canonical)
            if mat:
                resolved[f"{field}_attrs"] = mat
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
            for k in ("target_energy", "target_energy_density", "claimed_energy", "energy_density_wh_kg", "theoretical_energy_density"):
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
        return False

    def evaluate(self, scheme: Dict) -> Dict:
        """对方案求值全部约束 (未知材料实行严密 Fail-Closed)。

        Returns:
          feasible:    bool，无 reject 且无 exclude 命中 → True
          violations:  [{"rule_id", "reason", "excluded"}]
          inclusions:  [{"rule_id", "required"}]（建议项，未满足不致命）
          rejects:     [{"rule_id", "reason"}]
          unverified:  [str]
        """
        resolved = self.resolve_scheme(scheme)
        out: Dict = {"feasible": True, "violations": [], "inclusions": [], "rejects": [], "unverified": []}

        # 严格科研约束：未知材料 Fail-Closed 机制（非受控体系必须标记 UNVERIFIED 并拒绝）
        unverified_mats = []
        for field in ("cathode", "anode", "electrolyte"):
            raw_val = scheme.get(field)
            if raw_val is not None:
                canonical = self.normalize(str(raw_val))
                if not canonical or not self._material(canonical):
                    unverified_mats.append(f"{field}:{raw_val}")
        if unverified_mats:
            out["unverified"] = unverified_mats
            out["rejects"].append({
                "rule_id": "C0_UNVERIFIED_MATERIAL",
                "reason": f"方案包含未经验证的材料体系 [{', '.join(unverified_mats)}]，科研引擎拒绝盲目放行 (Fail-Closed)",
            })
            out["feasible"] = False
        for rule in self.constraints.get("rules", []):
            trig = rule.get("trigger", {})
            actual = self._resolve_field(resolved, trig.get("field", ""))
            if not self._op_matches(trig.get("op", "eq"), actual, trig.get("value")):
                continue
            action = rule.get("action")
            if action == "reject":
                out["rejects"].append({"rule_id": rule["id"], "reason": rule.get("reason", "")})
                out["feasible"] = False
            elif action in ("exclude", "include"):
                targets = self._parse_targets(rule.get("target", ""))
                hit = self._targets_hit_scheme(targets, resolved)
                if action == "exclude" and hit:
                    out["violations"].append({
                        "rule_id": rule["id"],
                        "reason": rule.get("reason", ""),
                        "excluded": [t[1] for t in hit],
                    })
                    out["feasible"] = False
                elif action == "include":
                    missing = self._targets_missing(targets, resolved)
                    if missing:
                        out["inclusions"].append({
                            "rule_id": rule["id"],
                            "reason": rule.get("reason", ""),
                            "required": missing,
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
        """include 目标中方案缺失的项（列表 target 语义：至少命中一个）。"""
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
            if rule.get("action") == "exclude":
                exclude_ids.update(t for _, t in self._parse_targets(rule.get("target", "")))
            elif rule.get("action") == "include":
                boost_ids.update(t for _, t in self._parse_targets(rule.get("target", "")))
        return {
            "exclude_terms": self._ids_to_terms(exclude_ids),
            "boost_terms": self._ids_to_terms(boost_ids),
        }

    def _ids_to_terms(self, ids) -> List[str]:
        terms = []
        for cid in ids:
            terms.append(cid)
            terms.extend(self.alias_map.get(cid, []))
        return terms

    # ────────────────────────── 插桩 B：方案校验 ──────────────────────────

    def check_scheme(
        self,
        scheme: Dict,
        claimed_energy: Optional[float] = None,
        answer_text: Optional[str] = None,
    ) -> Dict:
        """Reviewer 硬规则校验（LLM 审核之前执行）。

        Returns:
          rule_checks: {"violations": [...], "inclusions": [...]}
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
            "rule_checks": {
                "violations": ev["violations"],
                "inclusions": ev["inclusions"],
                "rejects": ev["rejects"],
            },
            "energy_check": energy_check,
            "condition_missing": condition_missing,
            "confidence": confidence,
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
    # ── 自测：3 个已知错误方案必须被拦截，1 个正确方案必须通过 ──
    eng = RelationEngine()

    cases = [
        # (名称, 方案, 期望 feasible)
        ("错误1: LRMO高压 + 常规碳酸酯", {"cathode": "LRMO", "anode": "graphite", "electrolyte": "carbonate_ec"}, False),
        ("错误2: 锂金属 + 常规碳酸酯", {"cathode": "NCM811", "anode": "li_metal", "electrolyte": "carbonate_ec"}, False),
        ("错误3: 400Wh/kg 用石墨负极", {"cathode": "NCM811", "anode": "graphite", "electrolyte": "carbonate_ec", "target_energy": 400}, False),
        ("正确: NCM811 + 石墨 + 碳酸酯", {"cathode": "NCM811", "anode": "graphite", "electrolyte": "carbonate_ec", "additives": ["VC"]}, True),
        ("正确: LRMO + 锂金属 + LHCE", {"cathode": "LRMO", "anode": "li_metal", "electrolyte": "lhce", "target_energy": 400}, True),
        ("500Wh/kg 拒绝", {"cathode": "LRMO", "anode": "li_metal", "electrolyte": "lhce", "target_energy": 500}, False),
    ]
    n_pass = 0
    for name, scheme, expect in cases:
        ev = eng.evaluate(scheme)
        ok = ev["feasible"] == expect
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
