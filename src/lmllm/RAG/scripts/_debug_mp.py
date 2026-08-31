import json
import urllib.parse
import urllib.request

KEY = "15MmhiFX6wdC9X3dl2awWBh5fTxkvIIG"


def q(params):
    url = "https://api.materialsproject.org/materials/summary/?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-API-KEY": KEY, "User-Agent": "deepseek-tui"})
    resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
    n = len(resp.get("data", []))
    print(f"  {params} -> {n} 条")
    if n:
        rec = resp["data"][0]
        keys = list(rec.keys())[:10]
        print(f"    首条字段: {keys}")
        print(f"    material_id={rec.get('material_id')} formula={rec.get('formula_pretty')}")
    return resp


print("== Li2MnO3 快照内首条字段 ==")
d = json.load(open("src/lmllm/RAG/data/raw/mp_snapshot_2026-08-18.json"))
r1 = d["records"][1]["response"]
if r1.get("data"):
    rec = r1["data"][0]
    print("  keys:", list(rec.keys())[:15])
    print("  material_id:", rec.get("material_id"), "| formula_pretty:", rec.get("formula_pretty"))

print("== NCM/LNMO 查询变体测试 ==")
q({"formula": "LiNi0.8Co0.1Mn0.1O2"})
q({"formula": "Li(Ni0.8Co0.1Mn0.1)O2"})
q({"chemsys": "Li-Ni-Co-Mn-O"})
q({"formula": "LiNi0.5Mn1.5O4"})
q({"chemsys": "Li-Ni-Mn-O"})
