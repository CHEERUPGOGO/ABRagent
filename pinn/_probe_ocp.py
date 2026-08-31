# -*- coding: utf-8 -*-
"""探测：PyBaMM 可用的 OCP 函数 / 参数集化学体系 / 多材料覆盖资源"""
import pybamm

print("=== 参数集化学体系 ===")
for name in pybamm.parameter_sets:
    d = dict(pybamm.parameter_sets[name])
    chem = d.get("chemistry")
    pos = ""
    neg = ""
    for k, v in d.items():
        if "positive electrode material" in k.lower():
            pos = v
        if "negative electrode material" in k.lower():
            neg = v
    print(f"  {name:32s} chem={chem}  pos={pos}  neg={neg}")

print("\n=== OCP 相关函数（pybamm.parameters 及全局） ===")
seen = set()
for mod in [pybamm, getattr(pybamm, "parameters", None)]:
    if mod is None:
        continue
    for n in dir(mod):
        if "ocp" in n.lower() or "open_circuit" in n.lower():
            seen.add(n)
for n in sorted(seen):
    print("  ", n)

print("\n=== 全部参数集中含 OCP 的函数值 ===")
for name in pybamm.parameter_sets:
    d = dict(pybamm.parameter_sets[name])
    for k, v in d.items():
        if "ocp" in k.lower() and callable(v):
            print(f"  {name}: {k} = {v.__name__}")

print("\n=== 半电池默认参数集的正极 OCP ===")
model = pybamm.lithium_ion.DFN(options={"working electrode": "positive"})
pv = pybamm.ParameterValues(values=model.default_parameter_values)
for k, v in pv.items():
    if "ocp" in k.lower():
        print(f"  {k} = {v.__name__ if callable(v) else v}")
