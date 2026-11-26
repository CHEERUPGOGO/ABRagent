"""运行预算只从YAML取默认值；配置可增减，检查点仅保留已用次数和有效配置快照。"""
from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path

import yaml

from .config import resolve_env_vars


@lru_cache(maxsize=1)
def runtime_defaults():
    """读取随包分发的默认配置，不依赖工作目录，也不写运行时快照。"""
    path = Path(__file__).resolve().parents[1] / "setting.yaml"
    with path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    return resolve_env_vars(config.get("runtime_options") or {})


def count(value, path, *, minimum=0):
    """仅校验非负整数，不设预算上限；环境插值的整数字符串同样可用。"""
    if isinstance(value, str) and value.strip().isdecimal():
        value = int(value.strip())
    if type(value) is not int or value < minimum:
        raise ValueError(f"{path} 必须是大于等于 {minimum} 的整数")
    return value


def budget_options(section, overrides=None):
    """按字段合并当前配置，默认数值的唯一来源为setting.yaml。"""
    defaults = runtime_defaults().get(section)
    if not isinstance(defaults, dict) or not isinstance(overrides if overrides is not None else {}, dict):
        raise ValueError(f"runtime_options.{section} 必须是对象")

    def merge(base, patch, path):
        if not isinstance(patch, dict):
            raise ValueError(f"{path} 必须是对象")
        result = copy.deepcopy(base)
        for key, value in patch.items():
            if key not in base:
                # 旧检索配置可能带非预算开关；沿用原先只读取已知预算字段的兼容行为。
                if section == "stage4_retrieval":
                    continue
                raise ValueError(f"未知预算配置：{path}.{key}")
            result[key] = value
        for key, value in result.items():
            result[key] = (merge(base[key], value, path + "." + key) if isinstance(base[key], dict)
                           else count(value, path + "." + key))
        return result

    return merge(defaults, overrides or {}, "runtime_options." + section)


def repair_options(overrides=None):
    return budget_options("stage4_repair", overrides)


def bind_repair_options(pipeline, seed):
    """新一轮执行使用当前配置，已有调用计数和请求签名原样保留。"""
    options = repair_options(getattr(pipeline, "stage4_repair_options", None))
    seed["repair_budget"] = options
    writer = getattr(pipeline, "writer", None)
    if writer is not None:
        writer.repair_options = options
    return options


def checkpoint_repair_options(seed, overrides=None):
    """门禁可显式采用当前运行配置；无运行上下文时兼容检查点快照。"""
    return repair_options(overrides if overrides is not None else seed.get("repair_budget"))
