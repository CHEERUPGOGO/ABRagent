"""电池领域三类关系 schema — NERRE 方法论的电池实例化（阶段 1）

对应关系抽取的目标结构（JSON），每个关系对象可直接消费为：
  - 任务生成器的题目原料（doping → 掺杂设计题，compatibility → 约束验证题，
    performance → 参数设计/对比题）
  - constraints.json 的候选约束（compatibility.incompatible）
  - performance_records.json 的数值记录（performance）

设计对齐 NERRE（Dagdelen et al., Nat. Commun. 2024）：
  - schema 先行：LLM 输出必须可解析（parsability 优先于字面精确）
  - 关系对象成组输出：一个句子可含多个关系对象
"""

from __future__ import annotations

from typing import Dict, List

# ═══════════════════════════ schema 定义 ═══════════════════════════

DOPING_SCHEMA = {
    "type": "doping",
    "description": "掺杂改性关系：谁掺杂了谁，产物是什么，效果如何",
    "fields": {
        "host": "str，被掺杂的主材料（规范化名称，如 LRMO、NCM811）",
        "dopants": "list[str]，掺杂元素/物质（如 Mg、Al、Ti）",
        "result": "str，掺杂后的产物名称（如 Mg/Al-LRMO）；无明确产物可省略",
        "modifiers": "list[str]，掺杂方式/量的修饰（如 共掺杂、5 mol%、表面掺杂）",
        "condition": "dict，关联的测试条件（c_rate/cycles/temperature/voltage_window）",
        "value": "dict，关联的性能指标 {property, value, unit}（可选）",
        "source_text": "str，原文句子（必填，溯源用）",
    },
}

COMPATIBILITY_SCHEMA = {
    "type": "compatibility",
    "description": "兼容/排除关系：谁与谁兼容或不兼容，在什么条件下",
    "fields": {
        "subject": "dict {material, role}，主体材料（role: cathode/anode/electrolyte/additive）",
        "object": "dict {material, role}，客体材料",
        "relation": "str，compatible / incompatible / improved_by / conditionally_compatible",
        "condition": "dict，触发条件（如 voltage>4.5V、负极=li_metal）",
        "reason": "str，原因简述（如 氧化分解、枝晶生长）",
        "source_text": "str，原文句子（必填）",
    },
}

PERFORMANCE_SCHEMA = {
    "type": "performance",
    "description": "材料-条件-性能绑定：什么材料在什么条件下达到什么性能",
    "fields": {
        "material": "str，材料（规范化名称）",
        "condition": "dict {c_rate, cycles, temperature, voltage_window}，测试条件",
        "property": "str，性能类型（discharge_capacity/energy_density/capacity_retention/"
                    "ice/coulombic_efficiency/conductivity/voltage）",
        "value": "float，数值",
        "unit": "str，单位（mAh/g、Wh/kg、%、S/cm、V）",
        "source_text": "str，原文句子（必填）",
    },
}

SCHEMAS = {
    "doping": DOPING_SCHEMA,
    "compatibility": COMPATIBILITY_SCHEMA,
    "performance": PERFORMANCE_SCHEMA,
}

# ═══════════════════════════ ICL 示例种子 ═══════════════════════════
# 句子来自真实文献/教科书共识，结构化标注为 ground truth。

DOPING_EXAMPLES: List[Dict] = [
    {
        "text": "The prolonged cycling performance at 0.1C shows that after 200 cycles, "
                "Mg/Al-LRMO retains 93.3% of its capacity, significantly better than "
                "LRMO (68.3%), Mg-LRMO (82.2%), and Al-LRMO (85.0%).",
        "relations": [{
            "type": "doping",
            "host": "LRMO",
            "dopants": ["Mg", "Al"],
            "result": "Mg/Al-LRMO",
            "modifiers": ["共掺杂"],
            "condition": {"c_rate": "0.1C", "cycles": 200},
            "value": {"property": "capacity_retention", "value": 93.3, "unit": "%"},
            "source_text": "The prolonged cycling performance at 0.1C shows that after 200 cycles, Mg/Al-LRMO retains 93.3% of its capacity",
        }],
    },
    {
        "text": "The initial Coulombic efficiency is notably higher for Mg/Al-LRMO (85.7%) "
                "compared to Al-LRMO (82.9%), Mg-LRMO (76.0%) and LRMO (72.2%).",
        "relations": [{
            "type": "doping",
            "host": "LRMO",
            "dopants": ["Mg", "Al"],
            "result": "Mg/Al-LRMO",
            "modifiers": [],
            "condition": {},
            "value": {"property": "ice", "value": 85.7, "unit": "%"},
            "source_text": "The initial Coulombic efficiency is notably higher for Mg/Al-LRMO (85.7%)",
        }],
    },
    {
        "text": "Notably, Mg/Al-LRMO achieves a higher discharge capacity of 160.7 mAh/g "
                "even at a high rate of 5.0C, outperforming LRMO (99.0 mAh/g).",
        "relations": [{
            "type": "doping",
            "host": "LRMO",
            "dopants": ["Mg", "Al"],
            "result": "Mg/Al-LRMO",
            "modifiers": [],
            "condition": {"c_rate": "5.0C"},
            "value": {"property": "discharge_capacity", "value": 160.7, "unit": "mAh/g"},
            "source_text": "Mg/Al-LRMO achieves a higher discharge capacity of 160.7 mAh/g even at a high rate of 5.0C",
        }],
    },
]

COMPATIBILITY_EXAMPLES: List[Dict] = [
    {
        "text": "常规碳酸酯电解液在正极充电截止电压超过4.3V时氧化分解，不适用于高压正极材料。",
        "relations": [{
            "type": "compatibility",
            "subject": {"material": "carbonate_ec", "role": "electrolyte"},
            "object": {"material": "NCM811", "role": "cathode"},
            "relation": "incompatible",
            "condition": {"voltage": ">4.3V"},
            "reason": "电解液氧化分解",
            "source_text": "常规碳酸酯电解液在正极充电截止电压超过4.3V时氧化分解，不适用于高压正极材料",
        }],
    },
    {
        "text": "Lithium metal anode with conventional low-concentration carbonate electrolyte "
                "suffers from dendrite growth and low coulombic efficiency; "
                "high-concentration or localized high-concentration electrolytes are required.",
        "relations": [
            {
                "type": "compatibility",
                "subject": {"material": "li_metal", "role": "anode"},
                "object": {"material": "carbonate_ec", "role": "electrolyte"},
                "relation": "incompatible",
                "condition": {},
                "reason": "枝晶生长、库仑效率低",
                "source_text": "Lithium metal anode with conventional low-concentration carbonate electrolyte suffers from dendrite growth and low coulombic efficiency",
            },
            {
                "type": "compatibility",
                "subject": {"material": "li_metal", "role": "anode"},
                "object": {"material": "high_concentration", "role": "electrolyte"},
                "relation": "compatible",
                "condition": {},
                "reason": "高浓电解液抑制枝晶",
                "source_text": "high-concentration or localized high-concentration electrolytes are required",
            },
        ],
    },
    {
        "text": "硅基负极体积膨胀约300%，需要FEC类添加剂形成稳定SEI膜。",
        "relations": [{
            "type": "compatibility",
            "subject": {"material": "si_base", "role": "anode"},
            "object": {"material": "FEC", "role": "additive"},
            "relation": "compatible",
            "condition": {},
            "reason": "FEC 形成稳定 SEI，抑制膨胀失效",
            "source_text": "硅基负极体积膨胀约300%，需要FEC类添加剂形成稳定SEI膜",
        }],
    },
]

PERFORMANCE_EXAMPLES: List[Dict] = [
    {
        "text": "Mg/Al-LRMO pouch cell achieves an energy density of 314.2 Wh/kg with "
                "a retention of 96.2% after 100 cycles.",
        "relations": [{
            "type": "performance",
            "material": "Mg/Al-LRMO",
            "condition": {"cycles": 100},
            "property": "energy_density",
            "value": 314.2,
            "unit": "Wh/kg",
            "source_text": "Mg/Al-LRMO pouch cell achieves an energy density of 314.2 Wh/kg with a retention of 96.2% after 100 cycles",
        }],
    },
    {
        "text": "In terms of discharge capacity, Mg/Al-LRMO outperforms the others with "
                "269.9 mAh/g, while Al-LRMO, Mg-LRMO and LRMO deliver 263.1, 245.1, "
                "237.7 mAh/g, respectively.",
        "relations": [
            {
                "type": "performance",
                "material": "Mg/Al-LRMO",
                "condition": {},
                "property": "discharge_capacity",
                "value": 269.9,
                "unit": "mAh/g",
                "source_text": "Mg/Al-LRMO outperforms the others with 269.9 mAh/g",
            },
            {
                "type": "performance",
                "material": "LRMO",
                "condition": {},
                "property": "discharge_capacity",
                "value": 237.7,
                "unit": "mAh/g",
                "source_text": "LRMO deliver 237.7 mAh/g",
            },
        ],
    },
    {
        "text": "After 200 cycles, Mg/Al-LRMO retains 90.0% of its initial capacity with "
                "a discharge capacity of 188.1 mAh/g at 1.0C.",
        "relations": [{
            "type": "performance",
            "material": "Mg/Al-LRMO",
            "condition": {"c_rate": "1.0C", "cycles": 200},
            "property": "capacity_retention",
            "value": 90.0,
            "unit": "%",
            "source_text": "After 200 cycles, Mg/Al-LRMO retains 90.0% of its initial capacity",
        }],
    },
]

# 统一示例集（ICL prompt 用）
FEWSHOT_EXAMPLES: Dict[str, List[Dict]] = {
    "doping": DOPING_EXAMPLES,
    "compatibility": COMPATIBILITY_EXAMPLES,
    "performance": PERFORMANCE_EXAMPLES,
}

# ═══════════════════════════ prompt 模板 ═══════════════════════════

SYSTEM_TEMPLATE = """你是电池材料领域的信息抽取器。从给定句子中抽取{relation_type}关系，严格按以下 JSON schema 输出。

Schema 字段：
{schema_fields}

输出规则：
1. 输出一个 JSON 数组，每个元素是一个关系对象
2. 关系对象必须包含 source_text 字段（原文中对应句子片段）
3. 材料名尽量使用规范化名称；无法确定时保留原文
4. 条件/数值缺失的字段用空 dict/None，不要编造
5. 一个句子可包含多个关系对象；没有匹配关系时输出空数组 []

示例："""


def build_fewshot_prompt(relation_type: str) -> str:
    """构建 ICL 抽取 prompt（system + 示例 + 待抽取文本占位）。"""
    schema = SCHEMAS[relation_type]
    fields_desc = "\n".join(f"  {k}: {v}" for k, v in schema["fields"].items())
    system = SYSTEM_TEMPLATE.format(
        relation_type=relation_type, schema_fields=fields_desc
    )
    parts = [system]
    for ex in FEWSHOT_EXAMPLES[relation_type]:
        parts.append(f"\n句子：{ex['text']}\n输出：{json_dumps(ex['relations'])}")
    parts.append("\n句子：{text}\n输出：")
    return "\n".join(parts)


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


if __name__ == "__main__":
    for t in ("doping", "compatibility", "performance"):
        prompt = build_fewshot_prompt(t)
        print(f"=== {t} prompt 预览（前 600 字符）===")
        print(prompt[:600])
        print()
