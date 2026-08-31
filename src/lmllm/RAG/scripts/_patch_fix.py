from pathlib import Path

base = Path("/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG")

# 1. energy_model.py: 修复材料级比能量单位（V × mAh/g = mWh/g = Wh/kg，无需再除 1000）
p = base / "energy_model.py"
s = p.read_text(encoding="utf-8")
s = s.replace(
    "    return avg_voltage * capacity_mah_g / 1000.0",
    "    return avg_voltage * capacity_mah_g   # V × mAh/g = mWh/g = Wh/kg",
)
s = s.replace(
    "  1. 材料级比能量 = 平均电压(V) × 比容量(mAh/g) / 1000   → Wh/kg(活性物质)",
    "  1. 材料级比能量 = 平均电压(V) × 比容量(mAh/g)   → Wh/kg(活性物质)",
)
p.write_text(s, encoding="utf-8")
print("energy_model.py patched")

# 2. relation_engine.py: include 列表语义改为"至少命中一个"
p = base / "relation_engine.py"
s = p.read_text(encoding="utf-8")
old = '''        """include 目标中方案缺失的项。"""
        missing = []
        for cat, tid in targets:
            if cat == "electrolyte":
                vals = [resolved.get("electrolyte")]
            elif cat == "additive":
                vals = resolved.get("additives") or []
            else:
                vals = [resolved.get(cat)]
            if not any(str(v) == tid for v in vals if v is not None):
                missing.append(f"{cat}:{tid}")
        return missing'''
new = '''        """include 目标中方案缺失的项（列表 target 语义：至少命中一个）。"""
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
        return missing'''
assert old in s, "anchor not found"
s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("relation_engine.py patched")
