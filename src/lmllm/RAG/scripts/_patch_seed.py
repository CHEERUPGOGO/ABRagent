from pathlib import Path

p = Path("/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG/data/seed/relations_seed.json")
s = p.read_text(encoding="utf-8")

repl = [
    # doping#4：source_text 跳过中间内容 → 用真子串
    ('"source_text": "The capacities in the plateau region decrease with the Mg2+ and Al3+ doping: 194.9 mAh/g for Mg/Al-LRMO"',
     '"source_text": "194.9 mAh/g for Mg/Al-LRMO"'),
    # performance#2 第二个：非连续子串 → 用真子串
    ('"source_text": "LRMO deliver 237.7 mAh/g"',
     '"source_text": "237.7 mAh/g"'),
    # performance#6 第二个：source_text 抄了完整原文，text 是精简版 → 对齐 text
    ('"source_text": "the pristine LRMO pouch cell only displays a specific capacity retention of 54.1%"',
     '"source_text": "the pristine LRMO pouch cell only displays 54.1%"'),
]
for old, new in repl:
    assert old in s, f"anchor not found: {old[:70]}"
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("patched 3 source_text → 全部为原文连续子串")
