# Stage 3: 材料挖掘与电芯组装指引

## 任务目标
1. 执行 2000-token 滑动窗口数据挖掘，完成 Phase 0 材料实体识别。
2. 实施三层归一化（化学式公式、材料别名、包覆/掺杂改性层剥离），生成标准 `canonical_id`。
3. 校验 format1 材料属性与性能标签。
4. 关联正极-负极-电解液，组装包含测试工况与电化学性能的 Cell 实体，并导出 ML 训练表格。

## 验收门禁 (CellAssemblyChecker)
- 存在有效的 `*_extracted.json` 挖掘或电芯组装产物。
- 包含 `materials`、`cells` 或 `extracted_info` 结构且具备归一化 ID。
