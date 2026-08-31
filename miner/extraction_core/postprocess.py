# -*- coding: utf-8 -*-
"""确定性后处理 — 在 LLM 输出后做字段规范、标签归桶、嵌套拍平、空值清理

保留 {value, unit} 结构不展平，供 ML 直接取数值特征。
参考: TECHNICAL_GUIDE.md 改动 4
"""

from typing import Any, Dict, Iterable, List, Mapping, Tuple


CONDITION_KEY_ALIASES = {
    "current_density": "c_rate_or_current",
    "c_rate": "c_rate_or_current",
    "rate": "c_rate_or_current",
    "current": "c_rate_or_current",
    "voltage": "voltage_range",
    "voltage_window": "voltage_range",
    "cell_voltage": "voltage_range",
    "soc": "soc_state",
    "state_of_charge": "soc_state",
    "loading": "mass_loading",
    "areal_loading": "mass_loading",
    "configuration": "electrode_config",
    "cell_config": "electrode_config",
    "method": "test_method",
}

CONDITION_KEYS = {
    "condition_id", "material_id", "battery_system_context",
    "temperature", "c_rate_or_current", "voltage_range", "electrolyte",
    "cycle_number", "mass_loading", "electrode_config",
    "reference_electrode", "test_method", "soc_state",
    "aging_condition", "source_text",
}


def remove_nulls(value: Any) -> Any:
    """递归去除 None、空字符串"""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if item is None or (isinstance(item, str) and not item.strip()):
                continue
            cleaned_item = remove_nulls(item)
            if cleaned_item is None:
                continue
            cleaned[key] = cleaned_item
        return cleaned
    if isinstance(value, list):
        return [
            remove_nulls(item)
            for item in value
            if item is not None and not (isinstance(item, str) and not item.strip())
        ]
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _canonical_condition_key(key: str) -> str:
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
    return CONDITION_KEY_ALIASES.get(normalized, normalized)


ELECTRODE_CONFIG_NORM = {
    "half-cell": "half_cell",
    "half cell": "half_cell",
    "halfcell": "half_cell",
    "coin cell": "half_cell",
    "cr2032": "half_cell",
    "cr2025": "half_cell",
    "full-cell": "full_cell",
    "full cell": "full_cell",
    "fullcell": "full_cell",
    "pouch cell": "full_cell",
    "pouch full cell": "full_cell",
    "symmetric cell": "symmetric_cell",
    "symmetric": "symmetric_cell",
    "symmetrical cell": "symmetric_cell",
    "three-electrode": "three_electrode",
    "three electrode": "three_electrode",
}
_ELECTRODE_SUFFIXES = {
    "half-cell": "half_cell",
    "half cell": "half_cell",
    "full-cell": "full_cell",
    "full cell": "full_cell",
}


def _normalize_electrode_config(value: str) -> str:
    val = value.strip().lower()
    if val in ELECTRODE_CONFIG_NORM:
        return ELECTRODE_CONFIG_NORM[val]
    for suffix, norm in _ELECTRODE_SUFFIXES.items():
        if val.endswith(suffix):
            return norm
    return value


def normalize_condition(condition: Dict[str, Any]) -> Dict[str, Any]:
    """拍平嵌套 structure 并标准化 key. 保留 {value, unit} 供 ML 取数值."""
    condition = dict(condition)

    nested = condition.pop("condition", None)
    if isinstance(nested, dict):
        for k, v in nested.items():
            condition.setdefault(k, v)

    parameters = condition.pop("parameters", None)
    if isinstance(parameters, dict):
        for k, v in parameters.items():
            condition.setdefault(k, v)

    label = condition.pop("label", None)
    value = condition.pop("value", None)
    if label and value is not None:
        key = _canonical_condition_key(str(label))
        if key in CONDITION_KEYS:
            condition.setdefault(key, value)
        else:
            condition.setdefault(str(label), value)

    normalized = {}
    for k, v in condition.items():
        key = _canonical_condition_key(k)
        # 对 electrode_config 做值归一化
        if key in CONDITION_KEYS and key == "electrode_config" and isinstance(v, str):
            normalized[key] = _normalize_electrode_config(v)
        else:
            normalized[key] = v
    normalized.pop("source_text", None)
    return remove_nulls(normalized)


def normalize_conditions(conditions: Iterable[Any]) -> List[Dict[str, Any]]:
    normalized = []
    for condition in conditions:
        if isinstance(condition, dict):
            cleaned = normalize_condition(condition)
            if cleaned:
                normalized.append(cleaned)
    return normalized


def normalize_embedded_conditions(value: Any) -> Any:
    """递归处理 performance_info 中的内嵌 condition"""
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if key == "condition" and isinstance(item, dict):
                normalized[key] = normalize_condition(item)
            else:
                normalized[key] = normalize_embedded_conditions(item)
        return remove_nulls(normalized)
    if isinstance(value, list):
        return [normalize_embedded_conditions(item) for item in value]
    return value


def _formatter_keys(formatter: Any, attr: str) -> set:
    view = getattr(formatter, attr, None)
    data = getattr(view, "data", view)
    if isinstance(data, Mapping):
        return set(data.keys())
    return set()


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> List[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def normalize_label_buckets(
    formatter: Any,
    property_types: Any,
    extracted_info: Any,
    performance_types: Any,
    performance_info: Any,
) -> Tuple[List[str], Dict[str, Any], List[str], Dict[str, Any]]:
    material_labels = _formatter_keys(formatter, "material_explanation") or _formatter_keys(formatter, "explanation")
    performance_labels = _formatter_keys(formatter, "perf_explanation")

    material_info = dict(_as_dict(extracted_info))
    perf_info = dict(_as_dict(performance_info))
    material_types = []
    perf_types = []

    for label in _string_list(property_types):
        if label in material_labels:
            material_types.append(label)
        elif label in performance_labels:
            perf_types.append(label)
            if label in material_info and label not in perf_info:
                perf_info[label] = material_info.pop(label)

    for label in _string_list(performance_types):
        if label in performance_labels:
            perf_types.append(label)
        elif label in material_labels:
            material_types.append(label)
            if label in perf_info and label not in material_info:
                material_info[label] = perf_info.pop(label)

    for label in list(material_info):
        if label in performance_labels and label not in material_labels:
            perf_info.setdefault(label, material_info.pop(label))
            perf_types.append(label)
        elif material_labels and label not in material_labels:
            material_info.pop(label)

    for label in list(perf_info):
        if label in material_labels and label not in performance_labels:
            material_info.setdefault(label, perf_info.pop(label))
            material_types.append(label)
        elif performance_labels and label not in performance_labels:
            perf_info.pop(label)

    material_types = list(dict.fromkeys(material_types))
    perf_types = list(dict.fromkeys(perf_types))

    return material_types, remove_nulls(material_info), perf_types, normalize_embedded_conditions(perf_info)
