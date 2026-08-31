import email.parser
import glob
import os

sp = "/home/ls/anaconda3/lib/python3.12/site-packages"
TODAY = {"accelerate", "click", "hf_xet", "huggingface_hub", "rank_bm25",
         "safetensors", "sympy", "tokenizers", "torch", "transformers"}

# 今天装的包 → 被哪些"原本就有的包"依赖
others_require = {}

def norm(dep: str) -> str:
    d = dep.split()[0]
    for sep in (">=", "<=", "==", ">", "<", ";"):
        d = d.split(sep)[0]
    return d.strip().lower().replace("_", "-")

for meta in glob.glob(os.path.join(sp, "*.dist-info", "METADATA")):
    name = os.path.basename(os.path.dirname(meta))
    pkg = name.split("-")[0].lower()
    if pkg in TODAY:
        continue
    try:
        with open(meta, encoding="utf-8", errors="replace") as f:
            msg = email.parser.Parser().parsestr(f.read())
    except Exception:
        continue
    for dep in msg.get_all("Requires-Dist") or []:
        base = norm(dep)
        for t in TODAY:
            if base == t or base.startswith(t + "-") or t.startswith(base + "-") or base.startswith(t.split("-")[0]):
                others_require.setdefault(t, set()).add(pkg)

print("今天装的包 → 被原本存在的包依赖的情况：")
if not others_require:
    print("  (无，全部可安全删除)")
for t, users in sorted(others_require.items()):
    print(f"  {t:18s} ← {sorted(users)}")
