import sys
sys.path.insert(0, "/home/ls/xiaoyue/LLM2/LMLLM")
from src.lmllm.RAG.extractor import RelationExtractor

# 6 句：performance/doping 各 2 句真实文献，compatibility 2 句教科书
TESTS = [
    ("performance", "The as-prepared (FeCoNiCrMn)3O4 HEO achieved a high reversible capacity of 596.5 mAh/g at 2.0C."),
    ("performance", "In contrast, LRMO only delivers an energy density of 270.0 Wh/kg, with the retention dropping to 89.8% after 100 cycles."),
    ("doping", "This result clearly demonstrates that co-doping with Mg and Al enhances structural stability and facilitates the rapid insertion and extraction of Li+."),
    ("doping", "These results indicate that Mg/Al co-doping can reduce the polarization and accelerate the Li+ diffusion, thereby improving the reaction kinetics."),
    ("compatibility", "常规碳酸酯电解液在正极充电截止电压超过4.3V时氧化分解，不适用于高压正极材料。"),
    ("compatibility", "富锂锰基正极充电至4.6V以上时，常规碳酸酯电解液会氧化分解并在正极表面形成CEI，导致阻抗增长。"),
]

ex = RelationExtractor()
ok = 0
for i, (rtype, text) in enumerate(TESTS):
    try:
        res = ex.extract(text, rtype)
    except Exception as e:
        print(f"[{i}] {rtype}: EXCEPTION {e}")
        continue
    parsable = res["parsable"]
    n = len(res["relations"])
    if parsable:
        ok += 1
    print(f"[{i}] {rtype:13s} parsable={parsable} rels={n}  {text[:50]}")
    for r in res["relations"][:2]:
        if rtype == "performance":
            print(f"     -> {r.get('material')} {r.get('property')}={r.get('value')}{r.get('unit','')} cond={r.get('condition')}")
        elif rtype == "doping":
            print(f"     -> host={r.get('host')} dopants={r.get('dopants')} value={r.get('value')}")
        else:
            print(f"     -> {r.get('subject',{}).get('material')} {r.get('relation')} {r.get('object',{}).get('material')} cond={r.get('condition')}")
    if not res["errors"] and n == 0:
        print("     -> (空数组，可能该句确无此类关系)")

print(f"\n=== 快速批次结论: parsability {ok}/{len(TESTS)} ===")
