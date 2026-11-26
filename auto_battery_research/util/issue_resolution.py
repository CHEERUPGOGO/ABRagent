"""兼容旧数值结案接口；停止通过固定计算链自动结案。"""
from __future__ import annotations


def resolve_numeric_issues(doc):
    """保留原意见和历史结案，当前计算问题须由模型审核，不自动写入结案。"""
    return []


def resolved(doc):
    """历史程序重算记录不作为当前独立审核的替代依据。"""
    return set()
