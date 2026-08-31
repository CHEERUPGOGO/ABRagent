"""NERRE 式 ICL 关系抽取器 — 阶段 1（对齐 Dagdelen et al. Nat. Commun. 2024）

从句子中按三类 schema（doping/compatibility/performance）抽取关系对象：
  - few-shot prompt 由 schemas/battery_relations.py 构建（示例种子内嵌）
  - 输出必须可解析（parsability 优先，对齐 NERRE 评估思想）
  - 解析器兼容 markdown 围栏、前后噪声；字段缺失可定位

用法（独立）：
  export DEEPSEEK_API_KEY=...
  python -m src.lmllm.RAG.extractor "Mg/Al-LRMO achieves 160.7 mAh/g at 5.0C" --type performance

或作为模块：
  from src.lmllm.RAG.extractor import RelationExtractor
  ex = RelationExtractor()
  result = ex.extract("...", "performance")
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

from .schemas.battery_relations import build_fewshot_prompt

# 各 schema 的必填字段（parsability 检查用）
REQUIRED_FIELDS = {
    "doping": ["host", "dopants", "source_text"],
    "compatibility": ["subject", "object", "relation", "source_text"],
    "performance": ["material", "property", "value", "unit", "source_text"],
}


def safe_json_extract(raw: str) -> Optional[str]:
    """从模型输出中提取 JSON 数组文本：剥 markdown 围栏 + 定位 [ ... ]。"""
    if not raw:
        return None
    text = raw.strip()
    # 剥 ```json ... ``` 围栏
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if m:
        text = m.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        # 容忍单个对象 {}
        s2, e2 = text.find("{"), text.rfind("}")
        if s2 == -1 or e2 <= s2:
            return None
        return text[s2:e2 + 1]
    return text[start:end + 1]


def parse_relations(raw: str, relation_type: str) -> Tuple[List[Dict], bool, List[str]]:
    """解析并校验模型输出。返回 (relations, parsable, errors)。"""
    errors: List[str] = []
    body = safe_json_extract(raw)
    if body is None:
        return [], False, ["无法定位 JSON 数组"]
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        return [], False, [f"JSON 解析失败: {e}"]
    if not isinstance(data, list):
        data = [data]
    required = REQUIRED_FIELDS.get(relation_type, [])
    relations: List[Dict] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append(f"第 {i} 项不是对象")
            continue
        if item.get("type") != relation_type:
            errors.append(f"第 {i} 项 type={item.get('type')} != {relation_type}")
            continue
        missing = [f for f in required if f not in item]
        if missing:
            errors.append(f"第 {i} 项缺字段: {missing}")
            continue
        relations.append(item)
    parsable = not errors and bool(relations)
    return relations, parsable, errors


class RelationExtractor:
    """ICL 关系抽取器（DeepSeek API 直连，独立于 RAG 包其余部分）。"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 base_url: str = "https://api.deepseek.com"):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        self.base_url = base_url.rstrip("/")

    def extract(self, text: str, relation_type: str) -> Dict:
        """抽取单句子的关系对象。返回 {"relations", "parsable", "raw", "errors"}。"""
        prompt = build_fewshot_prompt(relation_type).replace("{text}", text)
        raw = self._call(prompt)
        relations, parsable, errors = parse_relations(raw, relation_type)
        return {"relations": relations, "parsable": parsable,
                "raw": raw, "errors": errors}

    def _call(self, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY（环境变量或构造参数）")
        import urllib.request
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是电池材料领域信息抽取器，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 1500,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return resp["choices"][0]["message"]["content"]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ICL 关系抽取")
    ap.add_argument("text")
    ap.add_argument("--type", choices=["doping", "compatibility", "performance"],
                    default="performance")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    # ── 解析器自测（不调用 API）──
    print("=== parse_relations 自测 ===")
    ok_json = '[{"type": "performance", "material": "A", "property": "discharge_capacity", "value": 1.0, "unit": "mAh/g", "source_text": "s"}]'
    fenced = '```json\n' + ok_json + '\n```'
    bad = "很抱歉，我没有找到相关信息。"
    for label, raw, expect in [
        ("纯 JSON", ok_json, True),
        ("围栏包裹", fenced, True),
        ("非 JSON 输出", bad, False),
    ]:
        rels, parsable, errs = parse_relations(raw, "performance")
        status = "OK" if parsable == expect else "FAIL"
        print(f"  [{status}] {label}: parsable={parsable} errs={errs[:2]}")

    # ── 真实抽取（需 API key）──
    if os.environ.get("DEEPSEEK_API_KEY"):
        ex = RelationExtractor(model=args.model)
        res = ex.extract(args.text, args.type)
        print(f"\n=== 抽取结果 ({args.type}) ===")
        print(f"parsable={res['parsable']} errors={res['errors']}")
        print(json.dumps(res["relations"], ensure_ascii=False, indent=2))
    else:
        print("\n[DEEPSEEK_API_KEY 未设置，跳过真实 API 抽取]")
        print("设置后运行: export DEEPSEEK_API_KEY=... && "
              "python -m src.lmllm.RAG.extractor '句子' --type performance")
