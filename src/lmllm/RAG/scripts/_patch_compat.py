import json
from pathlib import Path

base = Path("/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG")

# ── 1. 改种子 relations_seed.json：第 7/9/10 条 ──
sp = base / "data" / "seed" / "relations_seed.json"
d = json.loads(sp.read_text(encoding="utf-8"))
comp = d["compatibility"]

for item in comp:
    t = item["text"]
    if "石墨负极" in t:
        # 第 7 条：明确 EC 基碳酸酯电解液（混合溶剂体系）
        item["text"] = "石墨负极与EC基碳酸酯电解液（EC/DMC/EMC混合溶剂体系）在低电位下形成稳定SEI，二者兼容性良好。"
        item["relations"][0]["source_text"] = "石墨负极与EC基碳酸酯电解液（EC/DMC/EMC混合溶剂体系）在低电位下形成稳定SEI，二者兼容性良好"
    elif "锂金属负极在氟化" in t:
        # 第 9 条：区分 improved_by 与 compatible
        item["text"] = "锂金属负极在氟化溶剂体系（FEC基）中可改善SEI和库仑效率，但液态下充分稳定运行需要高浓或局部高浓电解液。"
        item["relations"] = [
            {"type": "compatibility",
             "subject": {"material": "li_metal", "role": "anode"},
             "object": {"material": "fluorinated", "role": "electrolyte"},
             "relation": "improved_by",
             "condition": {},
             "reason": "FEC 基溶剂改善 SEI 与库仑效率，但不构成充分条件",
             "source_text": "锂金属负极在氟化溶剂体系（FEC基）中可改善SEI和库仑效率"},
            {"type": "compatibility",
             "subject": {"material": "li_metal", "role": "anode"},
             "object": {"material": "high_concentration", "role": "electrolyte"},
             "relation": "compatible",
             "condition": {},
             "reason": "高浓/局部高浓是锂金属液态运行的充分条件",
             "source_text": "液态下充分稳定运行需要高浓或局部高浓电解液"},
        ]
    elif "高温（60°C）" in t:
        # 第 10 条：区分常温 compatible 与高温 incompatible
        item["text"] = "高镍正极NCM811在常温下与常规碳酸酯电解液兼容，但在高温（60°C）下副反应显著加剧，需添加剂或改性电解液。"
        item["relations"] = [
            {"type": "compatibility",
             "subject": {"material": "NCM811", "role": "cathode"},
             "object": {"material": "carbonate_ec", "role": "electrolyte"},
             "relation": "compatible",
             "condition": {"temperature": "ambient"},
             "reason": "常温下在碳酸酯电解液稳定窗口内工作",
             "source_text": "高镍正极NCM811在常温下与常规碳酸酯电解液兼容"},
            {"type": "compatibility",
             "subject": {"material": "NCM811", "role": "cathode"},
             "object": {"material": "carbonate_ec", "role": "electrolyte"},
             "relation": "incompatible",
             "condition": {"temperature": "60°C"},
             "reason": "高温下副反应显著加剧，需添加剂或改性电解液",
             "source_text": "在高温（60°C）下副反应显著加剧，需添加剂或改性电解液"},
        ]

sp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print("relations_seed.json: 第 7/9/10 条已更新")

# ── 2. 改 schema：relation 枚举扩展 ──
bp = base / "schemas" / "battery_relations.py"
s = bp.read_text(encoding="utf-8")
old = '"relation": "str，compatible 或 incompatible",'
new = '"relation": "str，compatible / incompatible / improved_by / conditionally_compatible",'
assert old in s, "schema relation anchor not found"
s = s.replace(old, new)
bp.write_text(s, encoding="utf-8")
print("battery_relations.py: relation 枚举已扩展")

# ── 3. 改 review_seed.py：合法 relation 值集合 ──
rp = base / "scripts" / "review_seed.py"
s = rp.read_text(encoding="utf-8")
old = 'rel.get("relation") not in ("compatible", "incompatible")'
new = ('rel.get("relation") not in '
       '("compatible", "incompatible", "improved_by", "conditionally_compatible")')
assert old in s, "review relation anchor not found"
s = s.replace(old, new)
rp.write_text(s, encoding="utf-8")
print("review_seed.py: 合法 relation 值已扩展")
