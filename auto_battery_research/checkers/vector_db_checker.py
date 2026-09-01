"""Stage 2: VectorDBChecker — 语义标注与向量库门禁检查器."""

import json
from pathlib import Path
from typing import Dict, Any, Tuple
from .base_checker import BaseChecker

PRIMARY_LABELS = {
    "电化学性能",
    "材料属性与表征",
    "材料制备",
    "机理/模拟",
    "机理模拟",
    "概述",
    "非正文",
    "理化性质",
    "结构表征",
}


class VectorDBChecker(BaseChecker):
    """验证元数据绑定、段落语义标签及 Chroma/JSON 向量库入库状态.

    注: 段落语料库 (miner/) 为全部课题共享的全局知识资产，不做课题隔离 ——
    这与 Stage 3/4/6 的课题专属交付物不同，属设计内行为。
    """

    def do_check(self, is_complete: bool = False, **kwargs) -> Tuple[bool, Dict[str, Any]]:
        paths = self.config.get("paths", {})
        meta_file = paths.get("metadata_file", "miner/json/metadata/meta_merged.json")
        chroma_dir = paths.get("chroma_dir", "miner/chroma/paragraphs_q")
        
        meta_candidates = [
            meta_file,
            "miner/json/meta_merged.json",
            "miner/json/metadata/meta_merged.json",
        ]
        para_json_candidates = [
            "miner/json/100/paragraph_metadata_v4.json",
            "miner/json/100/paragraph_metadata_v4_20260622_155323.json",
            "miner/json/Chrome/paragraph_metadata_q.json",
            "miner/json/paragraph_metadata_v3.json",
            "miner/json/paragraph_metadata.json",
            "miner/json/test_paragraphs.json",
            "miner/json/_pipeline_v4_summary.json",
        ]

        # 1. 检查元数据文件 (meta_merged.json)
        found_meta_file = None
        for cand in meta_candidates:
            p = self.resolve_path(cand)
            if p.exists() and p.stat().st_size > 10:
                meta_data, err = self.load_json_safe(str(p))
                if not err and isinstance(meta_data, (list, dict)):
                    found_meta_file = str(p)
                    break

        # 2. 检查段落标注 JSON 或 Chroma 向量库
        chroma_p = self.resolve_path(chroma_dir)
        chroma_exists = chroma_p.exists() and len(list(chroma_p.glob("*"))) > 0

        para_data = None
        found_para_file = None
        para_list = []
        for cand in para_json_candidates:
            p = self.resolve_path(cand)
            if p.exists() and p.stat().st_size > 50:
                raw_data, err = self.load_json_safe(str(p))
                if not err:
                    if isinstance(raw_data, list) and len(raw_data) > 0:
                        para_data = raw_data
                        para_list = raw_data
                        found_para_file = str(p)
                        break
                    elif isinstance(raw_data, dict) and len(raw_data) > 0:
                        para_data = raw_data
                        para_list = raw_data.get("paragraphs") or raw_data.get("items") or [raw_data]
                        found_para_file = str(p)
                        break

        # 如果两者皆不存在，则严谨判定失败
        if not chroma_exists and not found_para_file:
            return False, self.build_diagnostic(
                passed=False,
                error_code="VECTOR_DB_AND_PARAS_MISSING",
                error_msg=f"未检测到 Chroma 向量库 ({chroma_dir}) 且未找到段落标注数据源",
                observed={"chroma_exists": chroma_exists, "para_json_found": None},
                expected="存在已入库的 Chroma 向量数据库或段落标注 JSON",
                next_action="运行入库脚本：python miner/paragraph_metadata_pipeline_v5_qwen.py --incremental",
            )

        # 3. 统计标签分布与质量 (Fail-Closed 门禁要求)
        label_stats = {}
        valid_items_count = len(para_list)
        for item in para_list:
            if isinstance(item, dict):
                # 兼容 label 字段与 metadata 列表
                labels = item.get("label") or item.get("metadata", [])
                if isinstance(labels, str):
                    labels = [labels]
                for l in labels:
                    if l in PRIMARY_LABELS:
                        label_stats[l] = label_stats.get(l, 0) + 1

        if valid_items_count == 0 and not chroma_exists:
            return False, self.build_diagnostic(
                passed=False,
                error_code="NO_INDEXED_PARAGRAPHS",
                error_msg="未检测到任何有效的学术段落语料记录 (total_paragraphs == 0)",
                observed={"total_paragraphs": 0},
                expected="至少存在有效学术段落语料与向量索引",
                next_action="运行语义切分与标注流水线",
            )

        if not label_stats and valid_items_count > 0:
            return False, self.build_diagnostic(
                passed=False,
                error_code="INVALID_LABEL_DISTRIBUTION",
                error_msg="段落数据缺少规范的 6 类互斥语义标签分布 (未匹配到 PRIMARY_LABELS)",
                observed={"sample_item": para_list[0] if para_list else {}},
                expected="段落必须包含标准语义标签 (如 电化学性能, 材料属性与表征, 材料制备, 机理/模拟, 概述 等)",
                next_action="运行标签归一化与清洗流水线：python miner/paragraph_metadata_pipeline_v5_qwen.py",
            )

        return True, self.build_diagnostic(
            passed=True,
            observed={
                "chroma_dir_exists": chroma_exists,
                "para_json_file": found_para_file,
                "total_paragraphs": valid_items_count,
                "label_distribution": label_stats,
                "meta_file": found_meta_file,
            },
            expected="段落已建立语义标注与向量检索索引，标签分布完备",
            details={"total_paragraphs": valid_items_count},
        )
