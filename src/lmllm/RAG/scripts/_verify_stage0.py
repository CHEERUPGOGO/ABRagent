import sys, types, importlib.util, json, pathlib

pkg = types.ModuleType("src.lmllm.RAG")
pkg.__path__ = ["/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG"]
sys.modules["src.lmllm.RAG"] = pkg

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m

em = load("src.lmllm.RAG.energy_model", "/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG/energy_model.py")
re_mod = load("src.lmllm.RAG.relation_engine", "/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG/relation_engine.py")

print("=== energy_model 自测 ===")
cands = em.load_candidates(pathlib.Path("/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG/data"))
for m in cands["cathode"]:
    me = em.estimate_material_energy(m["avg_voltage"], m["capacity"]["value"])
    print(f"  {m['id']:6s} 材料级 {me:7.1f} Wh/kg")
e = em.estimate_scheme_energy({"cathode": "LRMO", "anode": "li_metal"}, cands)
print(f"  LRMO+锂金属 电芯级估算: {e:.1f} Wh/kg")

print("\n=== relation_engine 约束拦截自测 ===")
eng = re_mod.RelationEngine(pathlib.Path("/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG/data"))
cases = [
    ("错误1: LRMO高压+常规碳酸酯", {"cathode": "LRMO", "anode": "graphite", "electrolyte": "carbonate_ec"}, False),
    ("错误2: 锂金属+常规碳酸酯", {"cathode": "NCM811", "anode": "li_metal", "electrolyte": "carbonate_ec"}, False),
    ("错误3: 400Wh/kg用石墨", {"cathode": "NCM811", "anode": "graphite", "electrolyte": "carbonate_ec", "target_energy": 400}, False),
    ("正确: NCM811+石墨+碳酸酯+VC", {"cathode": "NCM811", "anode": "graphite", "electrolyte": "carbonate_ec", "additives": ["VC"]}, True),
    ("正确: LRMO+锂金属+LHCE@400", {"cathode": "LRMO", "anode": "li_metal", "electrolyte": "lhce", "target_energy": 400}, True),
    ("拒绝: 500Wh/kg", {"cathode": "LRMO", "anode": "li_metal", "electrolyte": "lhce", "target_energy": 500}, False),
]
n_pass = 0
for name, scheme, expect in cases:
    ev = eng.evaluate(scheme)
    ok = ev["feasible"] == expect
    n_pass += ok
    print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    for v in ev["violations"]:
        print(f"          violation {v['rule_id']}: {v['reason'][:45]}")
    for r in ev["rejects"]:
        print(f"          reject {r['rule_id']}: {r['reason'][:45]}")
    for i in ev["inclusions"]:
        print(f"          include {i['rule_id']}: 需补充 {i['required']}")
print(f"  结果: {n_pass}/{len(cases)}")

print("\n=== 实体归一化 + 插桩演示 ===")
q = "4.6V富锂锰基配锂金属负极，用什么电解液？"
print("  问题:", q)
print("  实体:", eng.extract_entities(q))
mods = eng.query_modifiers({"cathode": "LRMO", "anode": "li_metal", "target_energy": 420})
print("  exclude:", mods["exclude_terms"][:5])
print("  boost:", mods["boost_terms"][:5])
chk = eng.check_scheme(
    {"cathode": "LRMO", "anode": "li_metal", "electrolyte": "carbonate_ec"},
    claimed_energy=420,
    answer_text="该方案能量密度约420 Wh/kg",
)
print("  check_scheme:", chk["confidence"], "| energy:", chk["energy_check"], "| cond_missing:", chk["condition_missing"])
