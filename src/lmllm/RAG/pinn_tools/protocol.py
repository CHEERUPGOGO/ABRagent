# -*- coding: utf-8 -*-
"""PINN 工具协议 — 注入 Reviewer prompt 的声明文本

"告诉模型什么时候用"：触发条件写在这里，决定权在 RAG 的 LLM。
Reviewer 输出 JSON 中的 needs_pinn 字段由模型自主决策。
"""
PINN_TOOL_PROMPT = """\
# PINN 数值验证工具（可选调用）
你有一个 PINN 物理模型工具 run_pinn_prediction，可计算材料组合方案
（正极/负极/电解液 + 倍率/电压窗口）的放电比容量、平均电压、能量密度。

触发条件（满足其一 → 输出中设置 "needs_pinn": true）：
- 用户问题涉及具体材料组合方案的数值性能（容量/电压/能量密度/倍率）
- 草稿答案声称了具体方案的性能数值，需要物理模型交叉验证
- 用户明确要求"计算/验证/模拟/预测"某方案的性能

不触发（needs_pinn: false）：纯文献综述、定性对比、无具体材料组合方案的问题。

JSON 输出新增字段：
  "needs_pinn": true 或 false,
  "pinn_condition": {"c_rate": 0.1, "voltage_min": 2.8, "voltage_max": 4.3,
                      "temperature_C": 25}
      // 可选工况参数；无法确定时可省略整个对象或省略不确定的键

规则：
- 触发后，管线会用材料方案 + pinn_condition 执行 PINN 计算，并把结果注入下一轮审核；
  你不要在本次输出中编造任何"PINN 计算结果"。
- 管线注入结果后，以该数值为准修正草稿答案（与草稿矛盾时以计算结果为准，
  并保留 [passage_id] 文献引用用于支撑其他定性结论）。
"""
