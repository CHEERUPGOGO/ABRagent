"""Stage 5: PINNPhysicsChecker — PINN 触发判定/参数提取/SPM 仿真门禁检查器.

激活 (非 skip) 时的验收语义按两种合法模式判定:
1. 仿真模式 (triggered=true，体系匹配 registry 且总开关开启): 必须存在
   pinn_simulation_result.json，且 status == CONVERGED、数值在物理区间、
   pinn.spec_hash 与 pinn_input_spec.json 的 cell_spec_hash 一致 (防陈旧结果
   冒充本轮结果)。任何一环缺失/失配均为硬失败。
2. 提取-only 模式 (triggered=false，未匹配体系 / 总开关关闭): pinn_input_spec.json
   结构完整即通过——这是工作流的规范降级路径，不算错误。
"""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base_checker import BaseChecker


class PINNPhysicsChecker(BaseChecker):
    """验证 PINN 物理仿真输出结果与物理边界（可跳过）."""

    # 标量物理合理区间
    CAPACITY_RANGE = (50.0, 4500.0)   # mAh/g
    VOLTAGE_RANGE = (1.0, 5.5)        # V
    ENERGY_RANGE = (50.0, 3000.0)     # Wh/kg
    MAX_PDE_RESIDUAL = 0.05

    def do_check(self, is_complete: bool = False, **kwargs) -> Tuple[bool, Dict[str, Any]]:
        # 1. 核心跳过机制 (动态读取 StageManager 运行状态，杜绝陈旧静态配置)
        is_skipped = False
        skip_reason = ""
        if self.stage_manager:
            stage_obj = self.stage_manager.get_stage_by_id(5)
            if stage_obj:
                is_skipped = stage_obj.skip
                skip_reason = stage_obj.skip_reason
        else:
            is_skipped = self.stage_info.get("skip", False)
            skip_reason = self.stage_info.get("skip_reason", "")

        if is_skipped:
            return True, self.build_diagnostic(
                passed=True,
                observed={"stage_status": "SKIPPED", "reason": skip_reason or "默认快速模式跳过"},
                expected="Stage 5 允许跳过",
                next_action="PINN 物理仿真阶段已跳过，无需仿真输出，直接推进至 Stage 6。",
                details={"skip": True},
            )

        paths = self.config.get("paths", {})
        output_agent_dir = self.resolve_path(paths.get("output_dir", "output/auto_battery_research"))
        task_dir = self.stage_manager.get_task_output_dir() if self.stage_manager else None

        # 2. SPM PINN 放电仿真结果 (体系匹配后的仿真链路，优先验收)
        pinn_candidates = []
        if task_dir is not None:
            pinn_candidates.append(task_dir / "pinn_simulation_result.json")
        # 全局 legacy 目录仅对历史存量课题 / Checker 独立使用保留
        if not self.stage_manager or self.allow_global_legacy_fallback:
            pinn_candidates.append(output_agent_dir / "pinn_simulation_result.json")
        pinn_file = next((p for p in pinn_candidates if p and p.exists() and p.stat().st_size > 10), None)
        if pinn_file is not None:
            return self._validate_pinn_result(pinn_file, self._find_spec_file(output_agent_dir))

        # 3. Stage 5 参数提取清单：PINN 触发判定 + 材料物理参数提取 (未匹配体系/总开关关闭)
        found_spec_file = self._find_spec_file(output_agent_dir)
        if found_spec_file:
            spec_data, spec_err = self.load_json_safe(str(found_spec_file))
            if spec_err or not isinstance(spec_data, dict):
                return False, self.build_diagnostic(
                    passed=False,
                    error_code="PINN_INPUT_SPEC_CORRUPTED",
                    error_msg=f"PINN 参数提取清单 JSON 损坏: {spec_err}",
                    observed={"spec_file": str(found_spec_file)},
                    expected="结构完整的 pinn_input_spec.json (含 trigger / scheme / cell_spec / extraction_summary)",
                    next_action="重新执行参数提取：RunPhysicsSimulation() 或使用 skip 5 跳过本阶段",
                )
            if not isinstance(spec_data.get("cell_spec"), dict):
                return False, self.build_diagnostic(
                    passed=False,
                    error_code="PINN_INPUT_SPEC_INCOMPLETE",
                    error_msg="pinn_input_spec.json 缺少 cell_spec 结构化参数块",
                    observed={"keys": sorted(spec_data.keys())},
                    expected="cell_spec 含 cathode / anode / electrolyte 参数",
                    next_action="检查 Stage 4 方案产物后重新执行参数提取：RunPhysicsSimulation()",
                )
            trigger_info = spec_data.get("trigger") if isinstance(spec_data.get("trigger"), dict) else {}
            extraction = spec_data.get("extraction_summary") if isinstance(spec_data.get("extraction_summary"), dict) else {}
            pinn_triggered = bool(trigger_info.get("triggered", False))
            if pinn_triggered:
                # 触发命中却无结果文件: 仿真被拦截 (越界/异常) 或中途失败 —— 硬失败,
                # 严格区分于"未匹配/开关关闭"的合法提取-only
                return False, self.build_diagnostic(
                    passed=False,
                    error_code="PINN_TRIGGERED_RESULT_MISSING",
                    error_msg="PINN 触发判定命中 (体系匹配注册表且总开关开启)，但不存在仿真结果文件",
                    observed={
                        "spec_file": str(found_spec_file),
                        "trigger_enabled": bool(trigger_info.get("enabled", False)),
                        "match": trigger_info.get("match") or {},
                    },
                    expected="triggered=true 时必须存在 status=CONVERGED 且 spec_hash 一致的 pinn_simulation_result.json",
                    next_action="重跑 RunPhysicsSimulation()；若输入被拦截 (OUT_OF_RANGE/OUT_OF_ENVELOPE) 请先修正越界的倍率/几何参数",
                )
            filled_total = sum(
                len(v.get("fields_filled") or [])
                for v in extraction.values() if isinstance(v, dict)
            )
            missing_total = sum(
                len(v.get("fields_missing") or [])
                for v in extraction.values() if isinstance(v, dict)
            )
            notes = (
                "PINN 参数提取清单结构完整；总开关关闭 (pinn_trigger.enabled=false)，回退提取-only"
                if not trigger_info.get("enabled", False)
                else "PINN 参数提取清单结构完整；体系未匹配注册表 PINN，回退提取-only"
            )
            return True, self.build_diagnostic(
                passed=True,
                observed={
                    "spec_file": str(found_spec_file),
                    "trigger_enabled": bool(trigger_info.get("enabled", False)),
                    "pinn_triggered": pinn_triggered,
                    "match": trigger_info.get("match") or {},
                    "scheme": spec_data.get("scheme") or {},
                    "param_fields_filled": filled_total,
                    "param_fields_missing": missing_total,
                    "notes": notes,
                },
                expected="存在结构完整的 pinn_input_spec.json (触发判定 + 材料物理参数提取)",
                details={"output_path": str(found_spec_file)},
            )

        return False, self.build_diagnostic(
            passed=False,
            error_code="PINN_SIMULATION_RESULT_MISSING",
            error_msg=f"PINN 物理仿真已激活，但未找到仿真结果或参数提取清单 ({task_dir or output_agent_dir})",
            observed={"spec_file_found": False, "pinn_result_found": False},
            expected="pinn_simulation_result.json / pinn_input_spec.json 至少其一",
            next_action="执行参数提取：RunPhysicsSimulation() 或使用 skip 5 跳过本阶段",
        )

    # ────────────────────────── 内部辅助 ──────────────────────────

    def _find_spec_file(self, output_agent_dir: str) -> Optional[Path]:
        """定位 pinn_input_spec.json (课题目录优先，全局 legacy 目录兜底)。"""
        spec_candidates = []
        if self.stage_manager:
            spec_candidates.append(self.stage_manager.get_task_output_dir() / "pinn_input_spec.json")
        if not self.stage_manager or self.allow_global_legacy_fallback:
            spec_candidates.append(Path(output_agent_dir) / "pinn_input_spec.json")
        return next((p for p in spec_candidates if p.exists() and p.stat().st_size > 10), None)

    def _bounds_failure(
        self, q_end: Any, v_mean: Any, energy_density: Any
    ) -> Optional[Dict[str, Any]]:
        """比容量/平均电压/能量密度物理区间校验；通过返回 None，否则返回
        build_diagnostic 关键字参数（比容量 → 电压 → 能量依次检查）。"""
        if not isinstance(q_end, (int, float)) or not (self.CAPACITY_RANGE[0] <= q_end <= self.CAPACITY_RANGE[1]):
            return dict(
                error_code="PINN_CAPACITY_OUT_OF_BOUNDS",
                error_msg=f"放电比容量超出物理合理区间: observed={q_end} mAh/g, expected={list(self.CAPACITY_RANGE)} mAh/g",
                observed={"specific_capacity": q_end},
                expected=f"放电比容量在 {list(self.CAPACITY_RANGE)} mAh/g 之间",
                next_action="检查仿真材料输入配方与倍率设置",
            )
        if not isinstance(v_mean, (int, float)) or not (self.VOLTAGE_RANGE[0] <= v_mean <= self.VOLTAGE_RANGE[1]):
            return dict(
                error_code="PINN_VOLTAGE_OUT_OF_BOUNDS",
                error_msg=f"平均放电平台电压超出物理合理区间: observed={v_mean} V, expected={list(self.VOLTAGE_RANGE)} V",
                observed={"average_voltage": v_mean},
                expected=f"平均电压在 {list(self.VOLTAGE_RANGE)} V 之间",
                next_action="调整电极电位窗口或更正热力学参数",
            )
        if not isinstance(energy_density, (int, float)) or not (self.ENERGY_RANGE[0] <= energy_density <= self.ENERGY_RANGE[1]):
            return dict(
                error_code="PINN_ENERGY_DENSITY_OUT_OF_BOUNDS",
                error_msg=f"有效能量密度超出物理合理区间: observed={energy_density} Wh/kg, expected={list(self.ENERGY_RANGE)} Wh/kg",
                observed={"energy_density": energy_density},
                expected=f"能量密度在 {list(self.ENERGY_RANGE)} Wh/kg 之间",
                next_action="重新评估活性物质面载量与正负极配比",
            )
        return None

    def _validate_pinn_result(self, result_file: Path, spec_file: Optional[Path] = None) -> Tuple[bool, Dict[str, Any]]:
        """校验 SPM PINN 放电仿真结果：收敛状态 + 输入一致性 + 物理区间 + 包络 + 残差。

        spec_file 提供时执行陈旧结果防护: result.pinn.spec_hash 必须与
        pinn_input_spec.json 的 cell_spec_hash 一致 (缺失/失配/spec 不可读均
        fail-closed 判陈旧，强制重跑对齐)。
        """
        sim_data, err = self.load_json_safe(str(result_file))
        if err or not isinstance(sim_data, dict):
            return False, self.build_diagnostic(
                passed=False,
                error_code="PINN_SIM_RESULT_CORRUPTED",
                error_msg=f"PINN 仿真结果 JSON 损坏: {err}",
                observed={"result_file": str(result_file)},
                expected="结构完整的 pinn_simulation_result.json (含 solver / system_id / 标量指标)",
                next_action="重新执行 PINN 仿真：RunPhysicsSimulation()",
            )

        pinn_info = sim_data.get("pinn") if isinstance(sim_data.get("pinn"), dict) else {}

        # 0. 收敛状态: 拦截/异常 payload (OUT_OF_RANGE/OUT_OF_ENVELOPE/ERROR) 无标量契约,
        #    必须显式失败而非落入下方 `or 0` 兜底产生误导性区间错误
        if sim_data.get("status") != "CONVERGED":
            return False, self.build_diagnostic(
                passed=False,
                error_code="PINN_SIMULATION_NOT_CONVERGED",
                error_msg=f"PINN 仿真未收敛 (status={sim_data.get('status')}): {sim_data.get('error') or '未知原因'}",
                observed={
                    "status": sim_data.get("status"),
                    "error": sim_data.get("error"),
                    "violations": pinn_info.get("violations") or [],
                    "result_generated_at": sim_data.get("generated_at"),
                },
                expected="status == CONVERGED (仿真输入在注册表有效范围与训练包络内)",
                next_action="按 error 修正越界输入 (放电倍率/几何参数) 后重跑 RunPhysicsSimulation()",
            )

        # 1. 输入一致性 (陈旧结果防护): spec 存在即强制比对 —— 旧课题无 hash 产物
        #    同样 fail-closed，触发一次重跑后即带上指纹
        if spec_file is not None:
            spec_data, spec_err = self.load_json_safe(str(spec_file))
            spec_hash = spec_data.get("cell_spec_hash") if not spec_err and isinstance(spec_data, dict) else None
            result_hash = pinn_info.get("spec_hash")
            if not spec_hash or not result_hash or result_hash != spec_hash:
                return False, self.build_diagnostic(
                    passed=False,
                    error_code="PINN_RESULT_STALE",
                    error_msg=(
                        f"仿真结果与当前输入不一致 (陈旧结果): result.pinn.spec_hash="
                        f"{result_hash or '缺失'} vs spec.cell_spec_hash={spec_hash or '缺失/不可读'}"
                    ),
                    observed={
                        "result_spec_hash": result_hash,
                        "spec_cell_spec_hash": spec_hash,
                        "result_generated_at": sim_data.get("generated_at"),
                    },
                    expected="result.pinn.spec_hash 与 pinn_input_spec.json 的 cell_spec_hash 一致",
                    next_action="重跑 RunPhysicsSimulation() 使仿真结果与当前输入重新对齐",
                )

        q_end = sim_data.get("specific_capacity_mAh_g") or sim_data.get("q_end_mAh_g") or 0
        v_mean = sim_data.get("average_voltage_V") or sim_data.get("v_mean") or 0
        energy_density = (
            sim_data.get("calculated_cell_energy_wh_kg")
            or sim_data.get("energy_wh_kg")
            or 0
        )
        failure = self._bounds_failure(q_end, v_mean, energy_density)
        if failure:
            return False, self.build_diagnostic(passed=False, **failure)

        if sim_data.get("envelope_ok") is False:
            return False, self.build_diagnostic(
                passed=False,
                error_code="PINN_OUT_OF_ENVELOPE",
                error_msg="PINN 工作点超出训练包络 (|bc|/|bc_ref| 越界)，外推结果不可信",
                observed={"violations": pinn_info.get("violations") or []},
                expected="工作点在训练包络内 (envelope_ok=true)",
                next_action="调整放电倍率或材料参数后重跑 RunPhysicsSimulation()",
            )

        residual_loss = sim_data.get("pde_residual_loss")
        if isinstance(residual_loss, (int, float)) and residual_loss > self.MAX_PDE_RESIDUAL:
            return False, self.build_diagnostic(
                passed=False,
                error_code="PINN_RESIDUAL_LOSS_TOO_HIGH",
                error_msg=f"偏微分方程残差过大: observed={residual_loss} > {self.MAX_PDE_RESIDUAL}",
                observed={"pde_residual_loss": residual_loss},
                expected=f"PDE 残差损失 <= {self.MAX_PDE_RESIDUAL}",
                next_action="检查模型训练质量 (train_report ref_check) 或更换体系模型",
            )

        return True, self.build_diagnostic(
            passed=True,
            observed={
                "result_file": str(result_file),
                "solver": sim_data.get("solver", "pinn_spm"),
                "model": sim_data.get("model"),
                "system_id": sim_data.get("system_id"),
                "direction": sim_data.get("direction"),
                "c_rate": sim_data.get("c_rate"),
                "specific_capacity_mAh_g": q_end,
                "average_voltage_V": v_mean,
                "calculated_energy_wh_kg": energy_density,
                "termination": sim_data.get("termination"),
                "envelope_ok": sim_data.get("envelope_ok", True),
                "pde_residual_loss": residual_loss,
                "notes": "SPM PINN 放电仿真收敛，标量指标在物理合理区间",
            },
            expected="SPM PINN 放电仿真收敛且参数在合理区间",
            details=sim_data,
        )
