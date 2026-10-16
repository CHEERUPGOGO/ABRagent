"""规范化 JSON 哈希与 Stage 5 scheme 投影 (workflow_actions 与 checker 共用).

checker 需要重算仿真结果的输入指纹做陈旧防护，但不能 import 工具层重模块
(workflow_actions 牵引整条 RAG/pinn 链路) —— 故把纯函数下沉到 util。
"""

import hashlib
import json
from typing import Any, Dict


def canonical_hash(obj: Any) -> str:
    """任意 JSON 对象的稳定输入指纹 (md5 前 12 位)。

    算法沿用 rag_adapter provenance 的 `json.dumps(sort_keys=True)` 先例，
    用于把 pinn_input_spec.json 与 pinn_simulation_result.json 绑定到同一份输入。
    """
    try:
        return hashlib.md5(
            json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()[:12]
    except Exception:
        return ""


def stage5_scheme_projection(scheme: Dict[str, Any]) -> Dict[str, Any]:
    """Stage 5 spec/result 携带的 scheme 投影 (与仿真输入相关的字段子集).

    必须包含 geometry —— 几何修订后投影随之变化, scheme_hash 才能把
    "仿真结果 ↔ 当前 design_scheme.json 几何" 绑定起来 (PINN_RESULT_STALE 防陈旧)。
    workflow_actions._generate_pinn_input_spec 与 pinn_physics_checker 共用本函数，
    保证两侧对同一 scheme 算出同一投影。
    """
    scheme = scheme if isinstance(scheme, dict) else {}
    return {
        "cathode": scheme.get("cathode"),
        "anode": scheme.get("anode"),
        "electrolyte": scheme.get("electrolyte"),
        "additives": scheme.get("additives") or [],
        "target_energy_wh_kg": scheme.get("target_energy_wh_kg") or scheme.get("target_energy"),
        "loading_mg_cm2": scheme.get("loading_mg_cm2"),
        "geometry": scheme.get("geometry"),
    }
