"""Stage 5 组合门禁，Check 只读诊断，不调用模型。"""
import json
from .pinn_physics_checker import PINNPhysicsChecker


class Stage5Checker(PINNPhysicsChecker):
    def do_check(self, is_complete=False, **kwargs):
        physical, diagnostic = super().do_check(is_complete=is_complete, **kwargs)
        if self.stage_manager:
            from auto_battery_research.util.experiment_design import require_current
            path = self.stage_manager.get_task_output_dir()
            try:
                doc = json.loads((path / "design_scheme.json").read_text(encoding="utf-8"))
                require_current(path, doc)
                recipe = json.loads((path / "experiment_plan.json").read_text(encoding="utf-8"))
                if recipe.get("generated_stage") != 5:
                    raise ValueError("已有推荐尚未完成 Stage 5B 参数核对，请调用 FinalizeStage5")
            except (OSError, ValueError, TypeError) as exc:
                return False, self.build_diagnostic(passed=False, error_code="STAGE5_EXPERIMENT_MISSING",
                    error_msg=str(exc), next_action="调用 FinalizeStage5 完成工艺检索和实验设计，不重跑材料 RAG")
        conditional = self.stage_manager and (recipe.get("consistency", {}).get("status") == "PENDING"
            or recipe.get("repair_status") == "PARTIAL" or bool(recipe.get("pending_groups"))
            or any(t.get("status") == "gap" for t in recipe.get("requirement_tests", [])))
        if conditional:
            deliverable = doc.get("delivery_contract") == 1
            return deliverable, self.build_diagnostic(passed=deliverable,
                error_code=None if deliverable else "STAGE5_EXPERIMENT_CONDITIONAL",
                error_msg=None if deliverable else "实验仍有待定字段、分组或目标测试缺口，严格模式不能视为已就绪",
                observed={"is_fallback": True, "physics_passed": physical, "experiment_ready": False,
                          "pending_fields": recipe.get("consistency", {}).get("pending_fields", [])},
                next_action="先确认待定字段、分组和测试目标，再重新生成或核对实验方案")
        if not physical and self.stage_manager and doc.get("delivery_contract") == 1:
            return True, self.build_diagnostic(passed=True, observed={"is_fallback": True,
                "physics_passed": False, "physical_diagnostic": diagnostic}, next_action="输出附条件报告，不使用失败仿真结果")
        return physical, diagnostic
