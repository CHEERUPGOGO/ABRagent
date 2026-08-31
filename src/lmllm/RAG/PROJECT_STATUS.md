# 高比能液态锂电池设计方案系统 — 实施状态

> 更新: 2026-08-20
> 范围: 锂离子/锂金属**液态**电池（含非水系与水系候选；排除固态、锂硫、锂空、回收、综述）
> 目标: 给定能量密度目标 → 输出**可验证**的材料组合方案（正极+负极+电解液+添加剂）

---

## 1. 系统架构

```
                    ┌─────────────────────────────────────────────┐
                    │  任务生成器 task_generator.py               │
                    │  组合推荐/约束验证/掺杂设计/参数设计        │
                    └──────────────┬──────────────────────────────┘
                                   │ 26 题，全部带 ground truth
                                   ▼
┌──────────────┐   ┌──────────────────────────────────────────────┐
│ 用户问题      │──▶│ RAG 管线（Planner→Retrieval→Writer→Reviewer）│
│ (含能量目标)  │   │  插桩 A: 检索约束过滤（碳酸酯段落降权）     │
└──────────────┘   │  Writer: 方案五段式输出（约束评估注入）       │
                   │  插桩 B: Reviewer 规则校验 + 置信度否决      │
                   └───────┬──────────────────────────┬───────────┘
                           │ 输出设计方案               │ rule_checks
                           ▼                          ▼
                    评测闭环 evaluate_design_tasks.py   错题 → 规则/prompt 迭代

数据资产（src/lmllm/RAG/data/）:
  candidates.json / constraints.json / alias_map.json   领域知识三表
  calibrated/energy_ranges.json + mp_crosscheck.json    公开库校准
  seed/relations_seed.json（36 关系对象）+ candidate_sentences.json
  tasks/design_tasks.json（26 题）+ eval_report.json（评测基线）
```

可靠性三层（方案设计，当前完成第一层）：
1. **规则层** ✅ — 约束表 + 能量估算 + 检索过滤 + 审核否决
2. **记录层** ⏳ — NERRE 关系抽取结果入库反查（抽取器已建，批量抽取待跑）
3. **物理层** ⏳ — physics_agent（方程代理 → PINN，数据源已登记）

---

## 2. 五阶段实施状态

### 阶段 0：领域知识结构化 ✅
| 交付物 | 状态 |
|--------|------|
| `data/candidates.json` | v0.2-mp：3 正极 + 3 负极 + 6 电解液（含 dilute_aqueous/water_in_salt）+ 3 添加剂；6 材料 MP 结构稳定性已验证 |
| `data/constraints.json` | 8 条硬约束（C1-C8），每条带 source；C7 经 BetterBat 实测佐证 |
| `data/alias_map.json` | 15 实体别名归一（含中英文、水系实体） |
| `energy_model.py` | 材料级/电芯级能量估算；BetterBat 校准产出体系能量区间（Nickel rich 50-350 Wh/kg） |
| `data/calibrated/` | energy_ranges.json（141 电芯 4 体系）+ mp_crosscheck.json（6 材料稳定） |
| `data/raw/` | MP 快照 395 条 + BetterBat xlsx + provenance（gitignore） |
| `scripts/fetch_datasets.py` | MP API（chemsys 查询）/ BetterBat / CDX 抓取，统一 provenance |

### 阶段 1：NERRE 关系抽取 ✅
| 交付物 | 状态 |
|--------|------|
| `schemas/battery_relations.py` | 三类关系 schema（doping/compatibility/performance）+ 内嵌 ICL 示例 |
| `data/seed/relations_seed.json` | 36 个关系对象（三类各 10+），自动一致性检查 0 疑点 |
| `extractor.py` | ICL 抽取器（DeepSeek API）+ parsability 解析器，自测 3/3 |
| 冒烟 + 快速批次 | parsability 83%（5/6）；英文/中文均正确抽取 |

### 阶段 2：任务生成器 ✅
| 交付物 | 状态 |
|--------|------|
| `task_generator.py` | 四类任务：组合推荐 3 + 约束验证 5 + 掺杂设计 6 + 参数设计 12 |
| `data/tasks/design_tasks.json` | 26 题，正 21/负 5，ground truth 缺失 0；constraint_check 含"不使用添加剂"前提 |

### 阶段 3：RAG 管线插桩 ✅
| 交付物 | 状态 |
|--------|------|
| 插桩 A（RetrievalAgent） | 检索结果约束过滤：排除段落降权 0.5 / 纳入段落加 0.15，输出 constraint_log |
| 插桩 B（ReviewerAgent） | 规则校验先行（check_scheme）→ 注入审核 prompt → confidence 否决权 → rule_checks |
| Writer 方案五段式 | 设计类问题按 目标/推荐组合/预期指标/可行性依据/风险缺口 输出，段间 --- |
| rag_pipeline.py | relation_engine 注入 + scheme/能量提取 + constraint_log 透传 |
| 验证 | mock 5 项全过；真实检索 constraint_log 生效（碳酸酯降权 8 / LHCE 加分 4）；端到端 rule_checks 出现 |

### 阶段 4：评测闭环 ✅
| 指标 | 基线 |
|------|------|
| rule 自洽性 | 26/26（100%）——任务 ground truth 与规则引擎/能量模型一致 |
| pipeline 约束验证 | 3/3（100%）——措辞修复后从 2/3 提升（LRMO 语义分歧 → 加"不使用添加剂"前提） |
| 抽取 parsability | 83%（快速批次 5/6） |

---

## 3. 关键决策记录

1. **不做知识图谱**：关系用 JSON 表承载（alias/candidates/constraints），规模小、精度优先；图谱是数据量变大后的演进选项
2. **关系抽取 = NERRE schema 方法**（Dagdelen et al. Nat. Commun. 2024）：schema 先行、少样本微调、parsability 优先
3. **公开库分角色**：MP（结构稳定性锚点，API+快照）、BetterBat（能量区间校准）、NASA/CALCE（物理层数据，阶段 4 用）；材料级关系数据靠自建
4. **任务生成器先于挖掘**：任务定义 schema，schema 驱动抽取，抽取反哺任务
5. **可验证性是硬要求**：所有任务带 ground truth 或 provenance，评测可复现计算
6. **约束条件化**：区分"裸体系排除"与"有条件兼容"——relation 枚举含 improved_by/conditionally_compatible；评测暴露的 LRMO+碳酸酯分歧 → 任务措辞加"不使用添加剂"前提对齐语义
7. **PINN 最后做**：可靠性三层逐层解锁，物理层条件成熟才启动

---

## 4. 遗留项与后续路线

| 项 | 说明 | 触发时机 |
|----|------|---------|
| 批量抽取 | batch_extract.py 已写；16 句全量跑（TUI 长任务易回收，建议终端手动） | 需要大规模关系数据时 |
| 实体归一升级 | 化学式顺序变体解析 + unknown 泛化描述收集（抽取结果入库前必需） | 批量抽取后 |
| 种子扩展 | 机理句示例、添加剂缓解类 conditionally_compatible 句子、doping 来源分散 | 抽取质量不足时 |
| Writer 端到端确认 | 设计类问题完整跑一遍看五段式效果（约 20 分钟/题） | 汇报前 |
| 评测集扩展 | 26 题全跑 + doping/parameter 类评测 | 需要完整基线时 |
| 物理层 | physics_agent：简单代理模型 → P2D 求解 → PINN（NASA/CALCE 数据） | 规则层+评测跑通、定量错误为主时 |

---

## 5. 环境与运行

- **Python 环境**: `/home/ls/anaconda3/envs/py3134_conda/bin/python`（torch 2.6.0+cu124 GPU、transformers 5.13.0）
- **API Key**: `DEEPSEEK_API_KEY`（环境变量或 `miner/config.yaml` 的 llm.api_key）
- **关键命令**:
```bash
PY=/home/ls/anaconda3/envs/py3134_conda/bin/python
$PY -m src.lmllm.RAG.task_generator                    # 重新生成任务
$PY -m src.lmllm.RAG.evaluate_design_tasks --mode rule  # 自洽性检查（秒级）
$PY -m src.lmllm.RAG.evaluate_design_tasks --mode pipeline --limit 3  # 管线评测（慢）
$PY src/lmllm/RAG/scripts/_verify_stage3.py            # 插桩 mock 验证
$PY src/lmllm/RAG/scripts/_verify_stage3a.py           # 插桩 A 真实检索验证
$PY src/lmllm/RAG/scripts/review_seed.py               # 种子审核
$PY -m src.lmllm.RAG.extractor "句子" --type performance  # 单句关系抽取
```

---

## 6. 领域知识备忘（约束表核心依据）

- 常规碳酸酯氧化窗口 ~4.3V；含氟 ~4.8V；高浓/LHCE ~5.0V
- 水系窗口 ~1.23V（热力学）；water-in-salt 扩窗至 ~3V 但高压正极仍处极限
- 锂金属负极：碳酸酯（低浓）枝晶问题；高浓/LHCE 为液态充分条件；含氟为"改善"（improved_by）
- 石墨体系电芯级能量上限 ~350 Wh/kg（BetterBat 实测 max=350 佐证 C7）
- 富锂锰基/高压尖晶石：碳酸酯裸体系 Mn 溶解 + 氧化分解；添加剂可缓解（条件化语义）
