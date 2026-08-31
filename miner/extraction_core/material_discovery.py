# -*- coding: utf-8 -*-
"""材料识别 Agent — Phase 0

扫描整篇文献，识别所有被研究或作为对比的电极材料。
为后续 Condition/Material/Performance 按材料分路处理提供基础。

用法:
    agent = MaterialDiscoveryAgent.from_llm(llm)
    materials = agent.discover(full_text, component="cathode")
    # [{"name":"NCM811","short_name":"NCM811","formula":"LiNi0.8Co0.1Mn0.1O2",
    #   "role":"novel","description":"primary material"}, ...]
"""

import json, re, logging
from typing import Any, Dict, List, Optional

from miner.extraction_core.discovery_prompts import (
    PROMPT_MATERIAL_DISCOVERY, PROMPT_ELECTROLYTE_DISCOVERY,
)

log = logging.getLogger("MaterialDiscovery")


class MaterialDiscoveryAgent:
    """材料识别 Agent — 不继承 Chain/BaseAgent，只有一次 LLM 调用。"""

    llm: Any

    def __init__(self, llm: Any):
        self.llm = llm

    def discover(self, text: str, component: str = "cathode",
                 file_stem: str = "", max_chars: int = 50000) -> List[Dict]:
        """
        扫描全文，识别电极材料。

        Args:
            text: 论文全文（clean_text 或合并后的文本）
            component: "cathode" | "anode" | "electrolyte"
            file_stem: 文件名（用于生成 material_id 前缀）
            max_chars: 截断长度（默认 50k chars，超出则截断）

        Returns:
            [{"name","short_name","formula","role","description","material_id"}, ...]
        """
        truncated = text[:max_chars] if len(text) > max_chars else text
        # 根据组件类型选择发现 prompt
        if component == "electrolyte":
            prompt = PROMPT_ELECTROLYTE_DISCOVERY.format(
                component_type=component,
                text=truncated,
            )
        else:
            prompt = PROMPT_MATERIAL_DISCOVERY.format(
                component_type=component,
                text=truncated,
            )
        try:
            response = self.llm.invoke(prompt)
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            log.warning(f"LLM invoke failed: {e}")
            return []

        materials = self._parse(raw)
        return self._assign_ids(materials, file_stem)

    def _parse(self, raw: str) -> List[Dict]:
        """解析 LLM 输出为材料列表"""
        raw = raw.strip()
        # 去掉 markdown 代码块标记
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        # 处理 "I do not know" / 空响应
        if re.search(r"[Ii] do not know|no\s+materials? found|empty|\[\]", raw):
            return []

        # 尝试提取 JSON 数组（手动匹配 [] 深度，避免 Python re 不支持递归）
        brace_depth = 0
        start_idx = -1
        for i, ch in enumerate(raw):
            if ch == '[' and brace_depth == 0:
                start_idx = i
                brace_depth = 1
            elif ch == '[':
                brace_depth += 1
            elif ch == ']':
                brace_depth -= 1
                if brace_depth == 0 and start_idx >= 0:
                    raw = raw[start_idx:i+1]
                    break
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [p for p in parsed if isinstance(p, dict) and p.get("name")]
            return []
        except json.JSONDecodeError:
            # fallback: 逐个对象解析
            objs = re.findall(r"\{[^{}]*\}", raw)
            result = []
            for o in objs:
                try:
                    d = json.loads(o)
                    if d.get("name"):
                        result.append(d)
                except json.JSONDecodeError:
                    continue
            return result

    def _assign_ids(self, materials: List[Dict], file_stem: str) -> List[Dict]:
        """为每个材料分配 material_id"""
        for i, mat in enumerate(materials):
            mat["material_id"] = f"{file_stem}_M{i+1:03d}" if file_stem else f"M{i+1:03d}"
        return materials

    @classmethod
    def from_llm(cls, llm: Any) -> "MaterialDiscoveryAgent":
        return cls(llm)


# 便捷函数：一次调用完成材料识别
def discover_materials(llm: Any, text: str, component: str = "cathode",
                       file_stem: str = "", max_chars: int = 50000) -> List[Dict]:
    """便捷函数，一次调用完成识别"""
    agent = MaterialDiscoveryAgent(llm)
    return agent.discover(text, component, file_stem, max_chars)
