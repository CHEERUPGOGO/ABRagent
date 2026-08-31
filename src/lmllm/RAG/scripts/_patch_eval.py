from pathlib import Path

base = Path("/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG")

# 1. task_generator.py: constraint_check 的 expected 加完整 scheme（含 target_energy）
p = base / "task_generator.py"
s = p.read_text(encoding="utf-8")
old = """                tasks.append({
                    "task_type": "constraint_check",
                    "question": f"方案：正极 {cath} + 负极 {an} + 电解液 {ele}。该组合可行吗？为什么？",
                    "expected": {"feasible": False,
                                 "violated_rules": [rule["id"]],
                                 "reason": rule.get("reason", "")},"""
new = """                tasks.append({
                    "task_type": "constraint_check",
                    "question": f"方案：正极 {cath} + 负极 {an} + 电解液 {ele}。该组合可行吗？为什么？",
                    "expected": {"feasible": False,
                                 "violated_rules": [rule["id"]],
                                 "reason": rule.get("reason", ""),
                                 "scheme": scheme},"""
assert old in s, "task_generator anchor"
s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("task_generator.py: constraint_check 已存完整 scheme")

# 2. evaluate_design_tasks.py: 重算优先用 expected.scheme，fallback 正则
p = base / "evaluate_design_tasks.py"
s = p.read_text(encoding="utf-8")
old = """            # 重算：从问题里提取方案并 evaluate
            m = re.search(r"正极 (\\S+) \\+ 负极 (\\S+) \\+ 电解液 (\\S+)", t["question"])
            if not m:
                stats["inconsistent"].append({"task": t["question"][:40], "reason": "问题格式无法解析"})
                continue
            scheme = {"cathode": m.group(1), "anode": m.group(2), "electrolyte": m.group(3)}"""
new = """            # 重算：优先用任务自带的完整 scheme（含 target_energy），fallback 文本解析
            scheme = expected.get("scheme")
            if not scheme:
                m = re.search(r"正极 (\\S+) \\+ 负极 (\\S+) \\+ 电解液 (\\S+)", t["question"])
                if not m:
                    stats["inconsistent"].append({"task": t["question"][:40], "reason": "问题格式无法解析"})
                    continue
                scheme = {"cathode": m.group(1), "anode": m.group(2), "electrolyte": m.group(3)}"""
assert old in s, "evaluate anchor"
s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("evaluate_design_tasks.py: 重算优先用 expected.scheme")
