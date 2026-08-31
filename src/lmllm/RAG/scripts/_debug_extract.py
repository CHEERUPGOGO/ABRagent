import sys
sys.path.insert(0, "/home/ls/xiaoyue/LLM2/LMLLM")
from src.lmllm.RAG.extractor import RelationExtractor

ex = RelationExtractor()

tests = [
    ("英文不同句", "The as-prepared (FeCoNiCrMn)3O4 HEO achieved a high reversible capacity of 596.5 mAh/g at 2.0C."),
    ("中文句", "Mg/Al-LRMO在0.1C下循环200圈后，容量保持率高达93.3%，优于原始LRMO的68.3%。"),
    ("无关系句", "The synthesis was carried out in an argon-filled glovebox."),
]
for label, text in tests:
    res = ex.extract(text, "performance")
    print(f"[{label}] parsable={res['parsable']} errors={res['errors']}")
    print(f"  raw={res['raw'][:200]}")
    print()
