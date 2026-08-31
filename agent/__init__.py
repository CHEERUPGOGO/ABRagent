# agent - 独立的三阶段提取管道
# Phase 0: 材料发现
# Phase 1: 串行条件收集（构建全局条件列表）
# Phase 2: 并行统一提取（属性+条件）
# Phase 3: 合并匹配 Agent（孤儿归位、跨段落去重）
# flatten: 展开为 ML-ready CSV

from agent.pipeline import run_pipeline
