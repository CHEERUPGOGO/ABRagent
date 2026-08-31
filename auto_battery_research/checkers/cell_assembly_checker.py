"""Stage 3: CellAssemblyChecker — 材料挖掘、归一化与电芯组装门禁检查器."""

from pathlib import Path
from typing import Dict, Any, Tuple, List
from .base_checker import BaseChecker


class CellAssemblyChecker(BaseChecker):
    """验证数据挖掘、材料/属性归一化、以及 Cell 实体组装的完备性与契约."""

    def do_check(self, is_complete: bool = False, **kwargs) -> Tuple[bool, Dict[str, Any]]:
        paths = self.config.get("paths", {})
        miner_json_dir = self.resolve_path(paths.get("miner_json_dir", "miner/json"))
        output_agent_dir = self.resolve_path(paths.get("output_dir", "output/auto_battery_research"))
        cell_output_dir = output_agent_dir / "cell_assembly"

        candidate_dirs = []
        if self.stage_manager:
            task_dir = self.stage_manager.get_task_output_dir()
            task_cell_dir = task_dir / "cell_assembly"
            if task_cell_dir.exists():
                # 课题目录存在即权威：不再聚合全局 legacy 目录，
                # 防止其他课题遗留的组装产物通过本课题的门禁
                candidate_dirs = [task_cell_dir]

        if not candidate_dirs and cell_output_dir.exists():
            candidate_dirs.append(cell_output_dir)

        # 仅当特定输出目录均不存在时，作为 fallback 探测默认全局候选目录
        if not candidate_dirs:
            candidate_dirs = [
                cell_output_dir,
                miner_json_dir,
                self.resolve_path("results"),
                self.resolve_path("agent/output"),
                self.resolve_path("output/battery_agent/cell_assembly"),
            ]

        extracted_files = []
        for cdir in candidate_dirs:
            if cdir.exists():
                extracted_files.extend(list(cdir.rglob("*_extracted*.json")))
                extracted_files.extend(list(cdir.rglob("*_rag.json")))
                extracted_files.extend(list(cdir.rglob("*_pipeline.json")))

        if not extracted_files:
            return False, self.build_diagnostic(
                passed=False,
                error_code="NO_EXTRACTED_DATA_FOUND",
                error_msg="未检测到任何结构化挖掘或电芯组装 JSON 文件 (*_extracted.json / *_rag.json)",
                observed={"found_extracted_files": 0},
                expected="存在至少 1 个经过挖掘归一化与组装的结构化 JSON 产物",
                next_action="运行数据挖掘与组装脚本：python agent/pipeline_tok2000.py -i database/type -o output/auto_battery_research/cell_assembly --max-files 5",
            )

        valid_files = 0
        total_materials = 0
        total_cells = 0
        has_normalized_id = False
        sample_checked = []

        for f in extracted_files[:10]:
            data, err = self.load_json_safe(str(f))
            if err or not isinstance(data, (dict, list)):
                continue

            valid_files += 1
            sample_checked.append(f.name)

            if isinstance(data, dict):
                materials = data.get("materials", [])
                cells = data.get("cells", [])
                total_materials += len(materials)
                total_cells += len(cells)

                for m in materials:
                    if isinstance(m, dict) and ("canonical_id" in m or "material_id" in m or "name" in m):
                        has_normalized_id = True
                        break

            elif isinstance(data, list):
                total_materials += len(data)
                for item in data:
                    if isinstance(item, dict) and ("extracted_info" in item or "performance_info" in item):
                        has_normalized_id = True

        if valid_files == 0:
            return False, self.build_diagnostic(
                passed=False,
                error_code="EXTRACTED_JSON_CONTENT_CORRUPTED",
                error_msg="所有检测到的挖掘 JSON 文件均解析失败或为空",
                observed={"checked_files": [f.name for f in extracted_files[:5]]},
                expected="包含有效的 materials, cells 或 extracted_info 结构",
                next_action="检查抽取管线日志并重新执行挖掘",
            )

        # 严格门禁：必须确保至少抽取并归一化了有效材料实体
        if total_materials == 0:
            return False, self.build_diagnostic(
                passed=False,
                error_code="NO_MATERIALS_EXTRACTED",
                error_msg="检测到的数据挖掘产物中材料实体数量为 0 (total_materials == 0)，未完成有效材料挖掘",
                observed={"total_materials": 0, "total_cells": total_cells},
                expected="抽取并归一化至少 1 种以上材料实体 (包含 canonical_id 与微观表征)",
                next_action="重新运行材料抽取与归一化流水线：python agent/pipeline_tok2000.py",
            )

        # 严格门禁：必须确保组装了真实非空电芯 (Cell) 实体
        if total_cells == 0:
            return False, self.build_diagnostic(
                passed=False,
                error_code="NO_CELLS_ASSEMBLED",
                error_msg="检测到的产物中电芯实体数量为 0 (total_cells == 0)，不允许仅有材料列表而缺少完整组装电芯",
                observed={"total_materials": total_materials, "total_cells": 0},
                expected="组装至少 1 个包含正极、负极、电解液及文献溯源信息的完整电芯 (Cell) 实体",
                next_action="执行电芯组装算法将材料实体组合为完整电芯",
            )

        # 严格门禁：校验 Cell 实体的组件引用与文献溯源 Provenance 完备性
        invalid_cells = []
        for f in extracted_files[:10]:
            data, err = self.load_json_safe(str(f))
            if err or not isinstance(data, dict):
                continue
            cells = data.get("cells", [])
            for c in cells:
                if not isinstance(c, dict):
                    continue
                c_id = c.get("cell_id") or c.get("id")
                has_cathode = bool(c.get("cathode") or c.get("positive") or c.get("cathode_id"))
                has_anode = bool(c.get("anode") or c.get("negative") or c.get("anode_id"))
                has_electrolyte = bool(c.get("electrolyte") or c.get("electrolyte_id"))
                has_provenance = bool(c.get("provenance") or c.get("source_paper") or c.get("doi") or c.get("source_file"))

                if not c_id or not (has_cathode and has_anode and has_electrolyte) or not has_provenance:
                    invalid_cells.append({
                        "cell_id": c_id or "UNKNOWN",
                        "missing_fields": [
                            k for k, v in [
                                ("cell_id", bool(c_id)),
                                ("cathode", has_cathode),
                                ("anode", has_anode),
                                ("electrolyte", has_electrolyte),
                                ("provenance", has_provenance),
                            ] if not v
                        ]
                    })

        if invalid_cells:
            return False, self.build_diagnostic(
                passed=False,
                error_code="CELL_SPEC_INCOMPLETE",
                error_msg=f"发现不完整的电芯实体定义 (缺少必要组件引用或文献溯源): {invalid_cells[:3]}",
                observed={"invalid_cells_count": len(invalid_cells), "sample_invalid": invalid_cells[:3]},
                expected="每个组装电芯必须具备 cell_id、正极、负极、电解液及文献 provenance 溯源信息",
                next_action="补齐电芯装配信息并确保正负极与电解液引用完整",
            )

        if not has_normalized_id:
            return False, self.build_diagnostic(
                passed=False,
                error_code="MISSING_NORMALIZED_IDS",
                error_msg="抽取产物缺少符合规范的三层归一化 ID (canonical_id / material_id)",
                observed={"has_normalized_id": False},
                expected="所有材料实体具备 canonical_id / base_id 归一化标识",
                next_action="运行实体注册与材料归一化对齐模块",
            )

        return True, self.build_diagnostic(
            passed=True,
            observed={
                "valid_extracted_files_count": len(extracted_files),
                "sample_analyzed_files": sample_checked[:5],
                "total_materials_indexed": total_materials,
                "total_cells_assembled": total_cells,
                "normalization_contract_verified": has_normalized_id,
            },
            expected="材料归一化与电芯组装产物符合规范且数据非空",
            details={"sample_file": str(extracted_files[0])},
        )
