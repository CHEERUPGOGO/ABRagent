"""Stage 5 组合门禁，Check 只读诊断，不调用模型。"""
import json
from .pinn_physics_checker import PINNPhysicsChecker


class Stage5Checker(PINNPhysicsChecker):
    def do_check(self, is_complete=False, **kwargs):
        if not self.stage_manager:
            return super().do_check(is_complete=is_complete, **kwargs)
        from auto_battery_research.util import experiment_design, scheme_ledger
        from auto_battery_research.util.experiment_delivery import blocking_pending_fields, assessment
        from auto_battery_research.util.stage5_execution import physics_exemption
        path = self.stage_manager.get_task_output_dir()
        try:
            doc = json.loads((path / "design_scheme.json").read_text(encoding="utf-8"))
            scheme_ledger.require_current(path, doc)
            experiment_design.require_current(path, doc)
            recipe = experiment_design.load(path)
            if recipe.get("generated_stage") != 5:
                raise ValueError("已有推荐尚未完成 Stage 5B 参数核对，请调用 FinalizeStage5")
        except (OSError, ValueError, TypeError) as exc:
            return False, self.build_diagnostic(passed=False, error_code="STAGE5_EXPERIMENT_MISSING",
                error_msg=str(exc), next_action="基于当前方案表重新核对 Stage 5B 实验方案")
        pending = blocking_pending_fields(recipe, doc)
        experiment_status, experiment_reasons = experiment_design.delivery_status(recipe, pending, doc=doc)
        try:
            exemption = physics_exemption(doc)
            if exemption:
                physical, diagnostic = True, {"status": "NOT_APPLICABLE", "reason": exemption}
            else:
                physical, diagnostic = super().do_check(is_complete=is_complete, **kwargs)
        except (OSError, ValueError) as exc:
            physical, diagnostic = False, {"error": str(exc)}
            exemption = None
        if experiment_status == "PASSED" and not exemption and physical:
            return physical, diagnostic
        fallback = bool(experiment_status != "PASSED" or not physical or (diagnostic.get("observed") or {}).get("is_fallback"))
        passed = not fallback or doc.get("delivery_contract") == 1
        return passed, self.build_diagnostic(passed=passed,
            error_code=None if passed else "STAGE5_CHECK_INCOMPLETE",
            error_msg=None if passed else "物理检查或实验参数一致性检查未通过",
            observed={"is_fallback": fallback, "physics_passed": physical, "physical_diagnostic": diagnostic,
                      "experiment_ready": experiment_status == "PASSED", "experiment_status": experiment_status,
                      "experiment_reasons": experiment_reasons, "pending_fields": pending,
                      "validation_notes": assessment(recipe, doc)["validation_notes"]})
