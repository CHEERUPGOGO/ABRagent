# 运行预算配置

Stage 4 的修复、审核、补检索预算统一由 `auto_battery_research/setting.yaml` 的 `runtime_options` 提供默认值。代码只校验非负整数，不再用另一套常量截断配置；可以调高，也可以调低。环境变量插值后的整数字符串同样可用。未覆盖字段沿用 YAML 默认值。

| 配置 | 控制范围 |
|---|---|
| `stage4_repair.protocol_rounds` | v2参数协议修复轮次；扩展权限每轮包含模型建议、补丁及独立只读复核 |
| `stage4_repair.format_rounds` | v2 局部格式修复次数 |
| `stage4_repair.content_rounds` | v2 科学正文修复轮数 |
| `stage4_repair.review_rounds` | v2 科学审核总次数，包含收尾复核 |
| `stage4_repair.review_service_failures` | 报告优先模式的审核服务失败额度 |
| `stage4_repair.strict_review_service_failures` | 严格模式的审核服务失败额度 |
| `stage4_repair.patch_attempts` | 每轮局部补丁生成与协议重试总次数 |
| `stage4_repair.v3_regular_rounds` | v3 常规修复或证据改善轮数 |
| `stage4_repair.v3_final_rounds` | v3 终审修复轮数，同时决定常规阶段预留机会 |
| `stage4_repair.v3_requests` | v3 各角色的持久请求配额 |
| `stage4_retrieval.supplement_rounds` | 针对性补检索轮数 |
| `stage4_retrieval.rebuild_rounds` | 首次策略整体失效时的重建额度 |
| `stage4_retrieval.queries_per_round` | 每轮查询数 |
| `stage4_retrieval.results_per_round` | 补检索和重建的检索 `top_k` |
| `stage4_retrieval.initial_retry_results` | 首次检索服务重试的 `top_k` |
| `stage4_retrieval.source_neighbours` | 同源邻近段落数 |
| `stage4_retrieval.gain_previous_passages` | 增益审核中的旧证据对照条数 |
| `stage4_retrieval.context_passages` | 推理 Writer 及统一证据包条数 |
| `stage4_retrieval.context_passages_analysis` | 非推理 Writer 证据条数 |
| `stage4_retrieval.context_chars` | 证据正文总字符数 |
| `stage4_retrieval.service_retries` | 检索服务失败重试总额度 |
| `max_retries_per_stage` | ABRAgent 外层阶段执行次数 |

例如，将补检索提高为四轮，并给 v2 正文修复及科学审核更多机会：

```yaml
runtime_options:
  stage4_repair:
    content_rounds: 5
    review_rounds: 10
  stage4_retrieval:
    supplement_rounds: 4
    queries_per_round: 4
    results_per_round: 40
    context_passages: 40
    context_chars: 100000
```

这些值是示例，仓库默认仍保持原来的修复三轮、审核六轮、补检索两轮等数值。提高 `context_passages` 时还需为 `context_chars` 和模型实际上下文留出空间；Writer 和 Reviewer 不再额外按固定 25 条截断。

v3 还受角色总请求配额约束。例如常规修复四轮、终审预留一轮时，需同时将 `v3_requests.repair`、`repair_verify` 至少设为五次，并给 `scientific_review` 留出初审和每次修复后的复核机会。调高调度轮数不会绕过角色配额，也不会扩大补丁写权限。

修改配置后，重启正在运行的 CLI/Web/API 进程。恢复入口及门禁使用本次运行配置，检查点保存有效配置快照和已用次数；增加额度只增加剩余机会，减少额度不会把已用次数清零。已占用但失败或中断的同一请求仍不自动重发。已经完成的任务不会因改配置自动重跑；需要通过已有显式恢复或重建入口继续。

预算只决定允许尝试的次数和上下文规模。首次重建窗口、重复查询去重、证据增益审核、用户锁定和科学审核仍决定动作是否可执行；有额度不代表一定需要发起调用。`ABRAgent(max_stage_retries=...)` 的显式参数保留优先级，省略时读取 YAML。Stage 5 几何修订继续使用同文件中的 `max_geometry_revisions`。
