from pathlib import Path

p = Path("/home/ls/xiaoyue/LLM2/LMLLM/src/lmllm/RAG/rag_pipeline.py")
s = p.read_text(encoding="utf-8")

old = '''        for r in retrieval.get("results", []):
            r["_source_type"] = "literature"
        return {
            "db_type": "literature",
            "results": retrieval.get("results", []),
            "search_logs": retrieval.get("search_logs", []),
        }'''
new = '''        for r in retrieval.get("results", []):
            r["_source_type"] = "literature"
        result = {
            "db_type": "literature",
            "results": retrieval.get("results", []),
            "search_logs": retrieval.get("search_logs", []),
        }
        if retrieval.get("constraint_log"):
            result["constraint_log"] = retrieval["constraint_log"]
        return result'''
assert old in s, "anchor not found"
s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("constraint_log 透传已修复")
