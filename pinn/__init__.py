# -*- coding: utf-8 -*-
"""pinn — 物理仿真层（SPM PINN + 输入 spec 契约）

本目录承载 Stage 5 的物理仿真方案：
  - spm_runner.py     万能族 SPM PINN 推理运行器（纯 numpy，零 TF 依赖），
                      模型资产位于 pinn/models/（registry.json + systems/）
  - input_spec.py     pinn_input_spec.json 的 cell_spec 构建契约
                      （无缺省表回填，仅真实数据来源）
  - models/           每个化学体系的参数/pOCV/权重/金样本 + 注册表

用法：
    from pinn.spm_runner import run_pinn_discharge, match_pinn_system
    from pinn.input_spec import build_cell_spec
"""

from .input_spec import (  # noqa: F401
    build_cell_spec,
    summarize_param_extraction,
    MATERIAL_PARAM_FIELDS,
    ELECTROLYTE_PARAM_FIELDS,
    ELECTRODE_GEOMETRY_FIELDS,
)

try:
    from .spm_runner import (  # noqa: F401
        run_pinn_discharge,
        run_pinn_charge,
        match_pinn_system,
        load_registry,
        load_system_params,
    )
except Exception:  # pragma: no cover - 模型资产缺失时保持可导入
    pass
