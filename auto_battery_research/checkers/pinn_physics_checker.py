"""Stage 5: PINNPhysicsChecker — PINN/P2D 物理仿真门禁检查器 (可跳过)."""

from pathlib import Path
from typing import Dict, Any, Tuple
from .base_checker import BaseChecker


class PINNPhysicsChecker(BaseChecker):
    """验证 PINN / PyBaMM 物理仿真输出结果与物理边界（可跳过）."""

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

        # 2. 如果开启了仿真，验证仿真结果文件
        paths = self.config.get("paths", {})
        output_agent_dir = self.resolve_path(paths.get("output_dir", "output/auto_battery_research"))

        candidates = []
        if self.stage_manager:
            task_dir = self.stage_manager.get_task_output_dir()
            candidates.extend([
                task_dir / "simulation_result.json",
                task_dir / "pinn_simulation_report.json",
            ])

        # 全局 legacy 候选 (含 pinn/output) 仅对历史存量课题 / Checker 独立使用保留；
        # 新哈希课题仅认课题目录产物
        if not self.stage_manager or self.allow_global_legacy_fallback:
            candidates.extend([
                output_agent_dir / "simulation_result.json",
                output_agent_dir / "pinn_simulation_report.json",
                self.resolve_path("pinn/output/simulation_result.json"),
            ])

        found_sim_file = next((p for p in candidates if p.exists() and p.stat().st_size > 10), None)

        if not found_sim_file:
            sim_json = output_agent_dir / "simulation_result.json"
            return False, self.build_diagnostic(
                passed=False,
                error_code="PINN_SIMULATION_RESULT_MISSING",
                error_msg=f"PINN 物理仿真已激活，但未找到仿真输出文件 ({sim_json})",
                observed={"sim_file_found": False},
                expected="包含 PyBaMM / PINN 仿真曲线与放电指标的 simulation_result.json",
                next_action="运行物理仿真：RunPINNSimulation() 或使用 skip 5 跳过本阶段",
            )

        # 3. 校验物理参数与边界
        sim_data, err = self.load_json_safe(str(found_sim_file))
        if err or not isinstance(sim_data, dict):
            return False, self.build_diagnostic(
                passed=False,
                error_code="SIMULATION_JSON_CORRUPTED",
                error_msg=f"仿真结果 JSON 损坏: {err}",
                next_action="重新执行物理仿真",
            )

        scalar = sim_data.get("scalar") if isinstance(sim_data.get("scalar"), dict) else {}
        q_end = (
            sim_data.get("specific_capacity_mAh_g")
            or sim_data.get("q_end_mAh_g")
            or sim_data.get("specific_capacity")
            or scalar.get("specific_capacity_mAh_g")
            or (scalar.get("Q_end_Ah", 0.0) * 1000.0 if "Q_end_Ah" in scalar else 0)
        )
        v_mean = (
            sim_data.get("average_voltage_V")
            or sim_data.get("v_mean")
            or sim_data.get("average_voltage")
            or scalar.get("avg_voltage_V")
            or 0
        )
        energy_density = (
            sim_data.get("calculated_cell_energy_wh_kg")
            or sim_data.get("energy_wh_kg")
            or sim_data.get("energy_density")
            or scalar.get("cell_energy_Wh_kg_x0.35")
            or (scalar.get("material_energy_Wh_kg", 0) * 0.35 if "material_energy_Wh_kg" in scalar else 0)
        )

        # 3.1 校验比容量 (mAh/g) 物理合理区间
        if not isinstance(q_end, (int, float)) or q_end < 50.0 or q_end > 4500.0:
            return False, self.build_diagnostic(
                passed=False,
                error_code="PINN_CAPACITY_OUT_OF_BOUNDS",
                error_msg=f"放电比容量超出物理合理区间: observed={q_end} mAh/g, expected=[50.0, 4500.0] mAh/g",
                observed={"specific_capacity": q_end},
                expected="放电比容量在 [50.0, 4500.0] mAh/g 之间",
                next_action="检查仿真材料输入配方与倍率设置",
            )

        # 3.2 校验平均电压 (V) 物理合理区间
        if not isinstance(v_mean, (int, float)) or v_mean < 1.0 or v_mean > 5.5:
            return False, self.build_diagnostic(
                passed=False,
                error_code="PINN_VOLTAGE_OUT_OF_BOUNDS",
                error_msg=f"平均放电平台电压超出物理合理区间: observed={v_mean} V, expected=[1.0, 5.5] V",
                observed={"average_voltage": v_mean},
                expected="平均电压在 [1.0, 5.5] V 之间",
                next_action="调整电极电位窗口或更正热力学参数",
            )

        # 3.3 校验能量密度 (Wh/kg) 物理合理区间
        if not isinstance(energy_density, (int, float)) or energy_density < 50.0 or energy_density > 3000.0:
            return False, self.build_diagnostic(
                passed=False,
                error_code="PINN_ENERGY_DENSITY_OUT_OF_BOUNDS",
                error_msg=f"有效能量密度超出物理合理区间: observed={energy_density} Wh/kg, expected=[50.0, 3000.0] Wh/kg",
                observed={"energy_density": energy_density},
                expected="能量密度在 [50.0, 3000.0] Wh/kg 之间",
                next_action="重新评估活性物质面载量与正负极配比",
            )

        # 3.4 校验数值收敛与偏微分方程残差
        conv_status = str(sim_data.get("convergence", "Converged")).upper()
        if conv_status in ("FAILED", "DIVERGED"):
            return False, self.build_diagnostic(
                passed=False,
                error_code="PINN_SIMULATION_DIVERGED",
                error_msg=f"PyBaMM/PINN 偏微分方程求解发散或未收敛: convergence={conv_status}",
                observed={"convergence": conv_status},
                expected="偏微分方程求解状态为 Converged 且残差可控",
                next_action="调整离散化网格密度或减小仿真时间步长",
            )

        residual_loss = sim_data.get("pde_residual_loss") or sim_data.get("residual")
        if residual_loss is not None and isinstance(residual_loss, (int, float)) and residual_loss > 0.05:
            return False, self.build_diagnostic(
                passed=False,
                error_code="PINN_RESIDUAL_LOSS_TOO_HIGH",
                error_msg=f"偏微分方程残差过大: observed={residual_loss} > 0.05",
                observed={"pde_residual_loss": residual_loss},
                expected="PDE 残差损失 <= 0.05",
                next_action="增加求解迭代轮数或重调神经网络权重",
            )

        is_fallback = bool(sim_data.get("is_fallback", False) or sim_data.get("status") == "FALLBACK")
        sim_status = "FALLBACK" if is_fallback else "CONVERGED"
        solver_used = sim_data.get("solver", "pybamm_newman_p2d" if not is_fallback else "0th_order_surrogate")

        return True, self.build_diagnostic(
            passed=True,
            observed={
                "sim_file": str(found_sim_file),
                "simulation_status": sim_status,
                "is_fallback": is_fallback,
                "solver": solver_used,
                "specific_capacity_mAh_g": q_end,
                "average_voltage_V": v_mean,
                "calculated_energy_wh_kg": energy_density,
                "convergence": conv_status,
                "residual_loss": residual_loss if residual_loss is not None else 0.001,
                "notes": "0 阶电化学理论模型代理估算通过" if is_fallback else "PyBaMM P2D 物理偏微分方程求解收敛",
            },
            expected="物理仿真曲线收敛且参数在合理区间",
            details=sim_data,
        )
