import sys
sys.path.insert(0, "/home/ls/xiaoyue/LLM2/LMLLM")
from src.lmllm.RAG import RAGPipeline

# 禁用 reranker（4B 模型加载太慢），只验证检索 + 插桩 A
print("初始化 RAGPipeline（reranker 禁用）...")
pipeline = RAGPipeline(reranker_enabled=False)
print("  关系引擎:", "可用" if pipeline.relation_engine is not None else "不可用")

q = "锂金属负极配什么电解液？对比几种方案。"
r = pipeline.retrieve_context(
    db_type="literature",
    question=q,
    queries=["锂金属负极电解液选择", "锂金属与电解液兼容性"],
    top_k=5,
)
clog = r.get("constraint_log")
print(f"\n问题: {q}")
print(f"召回 {len(r.get('results', []))} 条")
if clog:
    print(f"[插桩A] constraint_log 触发:")
    print(f"  exclude_terms: {clog['exclude_terms']}")
    print(f"  boost_terms:   {clog['boost_terms']}")
    print(f"  降权段落数:    {len(clog['downgraded'])}")
    print(f"  boost段落数:   {len(clog['boosted'])}")
    assert clog["exclude_terms"], "exclude_terms 不应为空"
    print("\n[插桩A] 验证通过")
else:
    print("[插桩A] 未触发 constraint_log")
    # 诊断
    entities = pipeline.relation_engine.extract_entities(q) if pipeline.relation_engine else {}
    print("  实体提取结果:", entities)
