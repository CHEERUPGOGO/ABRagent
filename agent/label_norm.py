"""agent/label_norm.py — 属性名归一化层（校验 + 未命中收集，不自动改写）

从 format1 的 formatter（miner/*_database/*_formatter.py）提取标准标签集
（材料属性 material + 电化学性能 performance + 合成条件 condition），
对 LLM 输出的 property_name 做确定性校验：

1. 精确命中标准标签              -> OK（method="exact"）
2. 规范化变体命中（小写 + 去下划线/空格/连字符）-> OK（method="normalized"）
3. 未命中                        -> 收集进 unmatched（不猜测对应关系，走注册流程判定）

与 material_norm 对称：只做"校验 + 收集"，不做模糊改写。
"discharge capacity" 到底对应哪个标准标签（Initial? Reversible?）不能靠规则猜，
必须由人/LLM 判定，否则会把两个不同属性焊成一个。

用法：
    from agent.label_norm import LabelNormalizer
    ln = LabelNormalizer()
    r = ln.check("cathode", "Discharge_Specific_Capacity_Initial")  # -> exact
    r = ln.check("cathode", "discharge specific capacity initial")  # -> normalized
    unmatched = ln.check_materials("cathode", merged["materials"])  # 遍历校验 + 收集
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

L = logging.getLogger("LabelNorm")


def _norm_key(s: str) -> str:
    """标签规范化键：小写 + 去下划线/空格/连字符/点。"""
    return re.sub(r"[\s\-_/.]+", "", s).lower()


@dataclass
class LabelCheckResult:
    standard_label: Optional[str]
    method: str          # "exact" | "normalized" | "none"
    input_label: str = ""


class LabelNormalizer:
    """属性名校验器：LLM 输出 property_name -> 标准标签（或未命中收集）。"""

    def __init__(self):
        self._index: Dict[str, Dict[str, str]] = {}  # component -> norm_key -> standard_label
        self._loaded: Set[str] = set()

    # ────────────────────────── 加载（与 phase0._get_labels 同源） ──────────────────────────

    def _load_component(self, component: str) -> None:
        if component in self._loaded:
            return
        try:
            if component == "cathode":
                from miner.cathode_database.cathode_formatter import CathodeFormatter as F
            elif component == "anode":
                from miner.anode_database.anode_formatter import AnodeFormatter as F
            elif component == "electrolyte":
                from miner.electrolyte_database.electrolyte_formatter import ElectrolyteFormatter as F
            else:
                return
            inst = F()
            labels: Set[str] = set()
            for keys_fn in ("material_keys", "performance_keys", "condition_keys"):
                try:
                    labels |= set(getattr(inst, keys_fn)())
                except (AttributeError, StopIteration):
                    continue
            idx = {_norm_key(k): k for k in labels if k}
            self._index[component] = idx
            self._loaded.add(component)
            L.info(f"[label_norm] {component}: {len(labels)} 个标准标签")
        except Exception as e:
            L.warning(f"[label_norm] {component} 标签加载失败: {e}")

    # ────────────────────────── 校验 ──────────────────────────

    def check(self, component: str, property_name: str) -> LabelCheckResult:
        """单个属性名 -> 标准标签（exact/normalized）或 None。"""
        self._load_component(component)
        idx = self._index.get(component, {})
        if not isinstance(property_name, str) or not property_name.strip():
            return LabelCheckResult(None, "none", str(property_name))
        pn = property_name.strip()
        if pn in idx.values():  # 精确命中（含大小写变体，如 devtE/VEd）
            return LabelCheckResult(pn, "exact", pn)
        key = _norm_key(pn)
        if key in idx:
            return LabelCheckResult(idx[key], "normalized", pn)
        return LabelCheckResult(None, "none", pn)

    def check_materials(self, component: str, materials: List[Dict]) -> List[Dict]:
        """遍历 phase3 归组后的 materials，校验所有 property_name，收集未命中。

        未命中项（注册队列种子）：{material_id, property_name, source_type, source_text}
        """
        unmatched: List[Dict] = []
        for m in materials or []:
            if not isinstance(m, dict):
                continue
            mid = m.get("material_id", "")
            for c in m.get("conditions", []):
                for p in (c.get("properties") or []):
                    if not isinstance(p, dict):
                        continue
                    r = self.check(component, p.get("property_name", ""))
                    if r.standard_label:
                        p["property_name"] = r.standard_label  # 标准化（exact 无变化）
                        p["label_norm_method"] = r.method
                    else:
                        unmatched.append({
                            "material_id": mid,
                            "property_name": r.input_label,
                            "source_type": p.get("source_type", ""),
                            "source_text": str(p.get("source_text", ""))[:120],
                        })
            for p in m.get("intrinsic_properties") or []:
                if not isinstance(p, dict):
                    continue
                r = self.check(component, p.get("property_name", ""))
                if r.standard_label:
                    p["property_name"] = r.standard_label
                    p["label_norm_method"] = r.method
                else:
                    unmatched.append({
                        "material_id": mid,
                        "property_name": r.input_label,
                        "source_type": p.get("source_type", ""),
                        "source_text": str(p.get("source_text", ""))[:120],
                    })
        return unmatched


# ══════════════════════════════════════════════════════════════
# 自测（不依赖 API）：python agent/label_norm.py
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ln = LabelNormalizer()

    cases = [
        ("cathode", "Discharge_Specific_Capacity_Initial", "exact"),
        ("cathode", "Initial_Coulombic_Efficiency", "exact"),
        ("cathode", "discharge specific capacity initial", "normalized"),
        ("cathode", "discharge_specific_capacity_initial", "normalized"),
        ("cathode", "Capacity_Retention_Ratio", "exact"),
        ("cathode", "capacity retention", "none"),      # 不猜测
        ("cathode", "First cycle discharge capacity", "none"),
        ("anode", "Reversible_Capacity_First_Cycle", "exact"),
        ("anode", "reversible capacity first cycle", "normalized"),
        ("electrolyte", "Ionic_Conductivity", "exact"),
        ("electrolyte", "ionic conductivity", "normalized"),
        ("cathode", "", "none"),
        ("cathode", 123, "none"),                       # 类型防护
    ]
    fails = 0
    for comp, name, want in cases:
        r = ln.check(comp, name)
        ok = r.method == want and (r.standard_label is not None) == (want != "none")
        if not ok:
            fails += 1
        print(f"  [{'OK' if ok else 'FAIL'}] {comp}/{name!r} -> {r.standard_label or '(none)'} ({r.method}, want {want})")

    # 批量校验自测：一个合法 + 一个变体 + 一个未命中
    mats = [
        {"material_id": "NCM811", "conditions": [
            {"properties": [
                {"property_name": "Discharge_Specific_Capacity_Initial", "value": 200, "source_type": "text"},
                {"property_name": "discharge specific capacity initial", "value": 199, "source_type": "text"},
                {"property_name": "First cycle capacity", "value": 198, "source_type": "text"},
            ]}]},
    ]
    unmatched = ln.check_materials("cathode", mats)
    print(f"\n批量: 3 属性 -> 未命中 {len(unmatched)} 个: {unmatched}")
    print(f"  标准化后: {[p['property_name'] for p in mats[0]['conditions'][0]['properties']]}")
    ok_batch = len(unmatched) == 1 and unmatched[0]["property_name"] == "First cycle capacity"
    if not ok_batch:
        fails += 1

    print(f"\n结果: {'全部通过' if fails == 0 else f'{fails} 个用例失败'}")
    raise SystemExit(1 if fails else 0)
