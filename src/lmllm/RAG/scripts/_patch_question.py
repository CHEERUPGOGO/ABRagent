from pathlib import Path

p = Path("/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG/task_generator.py")
s = p.read_text(encoding="utf-8")

old = '"question": f"方案：正极 {cath} + 负极 {an} + 电解液 {ele}。该组合可行吗？为什么？",'
new = ('"question": f"方案：正极 {cath} + 负极 {an} + 电解液 {ele}，'
       '不使用任何界面添加剂或表面改性。该组合可行吗？为什么？",')
assert old in s, "anchor not found"
s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("task_generator.py: constraint_check 措辞已加'不使用添加剂'前提")
