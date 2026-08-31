# 修改说明

本次修改涉及九个源代码文件（新增）及数项清理工作，均为 v5 提取管线的前后处理优化与配套模块。

---

## 1. `miner/cleaning/clean_text1.py` — 数据清洗模块

### 改动清单

| # | 改动 | 说明 |
|---|---|---|
| 1 | **标题独立成段** | 标题以 `#` 开头作为独立段落输出，不合并到后续正文中 |
| 2 | **标题下多段落合并** | 同一标题下的多个正文段落合并为一个段落，空行不打断合并 |
| 3 | **移除 `<details>` 图表碎片块** | 新增 `_remove_details_blocks()` 预处理，过滤 MinerU 的全部 `<details>` 块 |
| 4 | **过滤 MinerU 碎片行** | 新增 `_is_mineru_chart_line()`，过滤 `line/bar/scatter/textimage/naturalimage/chemical/mermaid` 等图表转换文本 |
| 5 | **SI fallback 补回正文** | 新增 `_detect_si_first_keep()`：当第一个 keep 标题来自 Supporting Information 时，合并 classify 模式结果补充正文 |
| 6 | **SI fallback 噪音过滤** | classify 模式补回的正文再跑一次 EXTRACT_SKIP_NOISE 过滤，避免 Abstract 等混入 |
| 7 | **修复数字空格拼接** | `_clean_single_paragraph` 末尾加入 `(?<=\d)\s+(?=\d)` 正则，修复 MinerU LaTeX 拆散的数值（`1 0 0 0` → `1000`, `9 6 . 5` → `96.5`） |

### 对外接口不变

```python
from miner.cleaning.clean_text1 import clean_text
text = clean_text(file_path, min_text_len=500, mode="extract")  # 接口不变
```

---

## 2. `miner/cleaning/test_clean.py` — 测试查看工具

### 改动

- 段落显示逻辑：短标题不再被字数过滤掉，标题段落标记 `[H]` 前缀

---

## 3. `miner/extraction_core/rule_screening.py` — 规则筛选

### 改动清单

| # | 改动 | 说明 |
|---|---|---|
| 1 | **扩充 `_MATERIAL_KEYWORDS`** | 新增 `crystallite size`、`grain size`、`precursor`、`synthesis` |
| 2 | **通用 extract 规则** | 三组件的 `_screen_*` 函数各加一条规则：`has_num and mat_score >= 1` → 直接 `extract`，不受组件关键词限制 |
| 3 | **include fallback prompt 放宽** | 加入"制备条件、表征数据"，避免 fast LLM 过滤掉合成/表征段落 |

### 修改位置

```python
# _screen_cathode / _screen_anode / _screen_electrolyte 均新增:
if has_num and mat_score >= 1:
    return ScreeningDecision("extract", min(1.0, total / 6), ["all"])

# llm_include_fallback 的 prompt:
# 改前: "相关的材料信息、测试条件或电化学性能数据"
# 改后: "相关的制备条件、材料性质、表征数据、测试条件或电化学性能"
```

---

## 4. `miner/extraction_core/postprocess.py` — electrode_config 值归一化

### 改动

| # | 改动 | 说明 |
|---|---|---|
| 1 | **新增 `ELECTRODE_CONFIG_NORM` 映射表** | 精确匹配：half-cell → half_cell, coin cell → half_cell, pouch full cell → full_cell 等 |
| 2 | **新增 `_ELECTRODE_SUFFIXES` 后缀匹配** | 模糊匹配：`"NCM811\|\|graphite full cell"` 后缀匹配 → full_cell |
| 3 | **`normalize_condition` 调用值归一化** | 当 key 为 electrode_config 时，值经过 `_normalize_electrode_config` 映射后再存入 |

### 值归一化效果

| LLM 原始输出 | 归一化后 |
|---|---|
| `"half-cell"`, `"half cell"`, `"coin cell"`, `"graphite half-cell"` | `"half_cell"` |
| `"full-cell"`, `"pouch full cell"`, `"NCM811\|\|graphite full cell"` | `"full_cell"` |
| `"symmetric cell"`, `"symmetric"` | `"symmetric_cell"` |
| `"three-electrode"` | `"three_electrode"` |

---

## 5. `miner/extraction_core/extraction_pipeline_v5.py` — 新增材料发现 + 元数据

### 改动清单

| # | 改动 | 说明 |
|---|---|---|
| 1 | **导入 MaterialDiscoveryAgent** | 新增 import，复用 v4 的材料发现 Agent |
| 2 | **Phase 0: 材料发现阶段** | 在清洗后、分段前插入 discovery_agent.discover()，识别文献中的材料 |
| 3 | **材料注入 unified context** | 将识别的材料名/化学式/角色注入 battery_system_context，LLM 提取时知道当前处理的是哪种材料 |
| 4 | **输出 materials 字段** | JSON 输出新增 materials / n_materials，记录发现的材料列表 |
| 5 | **CLI 注册 discovery_agent** | 启动时注册，并传递给 process_file |

### 前置条件

运行 v5 前需要先跑元数据提取：

```bash
python -m miner.meta_extraction.extract_meta
```

输出 `miner/json/meta_merged.json`，v5 自动加载。

---

## 6. `miner/extraction_core/__init__v2.py` — v2 模块统一导出

### 作用

新增模块导出入口，将 v5 新增组件统一暴露，同时保留原有 v4 导出：

| 导出符号 | 来源 |
|---|---|
| `UnifiedExtractionAgent` | `unified_agent.py` |
| `extract_table_contexts` | `table_context.py` |
| `screen_extraction_unit` / `llm_include_fallback` / `ScreeningDecision` | `rule_screening.py` |
| `normalize_conditions` / `normalize_embedded_conditions` / `remove_nulls` / `normalize_label_buckets` | `postprocess.py` |
| `MaterialDiscoveryAgent` / `discover_materials` | 保留 v4 导出 |
| `TokenChecker` / `TokenStep` | 保留 v4 导出 |

### 接口

```python
from miner.extraction_core import UnifiedExtractionAgent, extract_table_contexts
```

---

## 7. `miner/extraction_core/table_context.py` — 表格上下文提取

### 作用

在清洗前从原始 Markdown 中提取表格，防止清洗阶段删除表格数据（尤其是 HTML `<table>`）。

支持两类表格：
- **标准 Markdown 表格**（`| header | header |` + `|---|---|` + 数据行）
- **HTML `<table>` 标签表格**（通过 BeautifulSoup 解析）

### 输出格式

每个表格转换为 `TABLE DATA BLOCK` 字符串，包含：
- 附近文本（表格前/后文字）
- caption（表格标题/注释）
- 表头（headers）
- 行数据（rows）
- 按列展开的数据（columns）

### 接口

```python
from miner.extraction_core.table_context import extract_table_contexts
contexts = extract_table_contexts(markdown_text)
```

---

## 8. `miner/extraction_core/unified_agent.py` — 统一抽取 Agent

### 作用

将 v4 版本的三次 LLM 调用（condition / material / performance）合并为**一次 LLM 调用**，减少 Token 消耗和延迟。

### 设计方案

- 保留 v4 的组件角色设定（cathode/anode/electrolyte 专家系统）
- 保留少样本示例（one-shot example）
- 保留 structured_data schema 引用
- 保留 source_text verbatim 要求
- 输出经 `postprocess.py` 后处理为规范结构

### Prompt 模板结构

统一抽取 prompt 包含以下上下文：

| 字段 | 说明 |
|---|---|
| `{component}` | 组件类型（cathode/anode/electrolyte） |
| `{battery_system_context}` | 材料发现阶段注入的电池系统上下文 |
| `{material_id}` | 当前处理的材料 ID |
| `{doi}` | 文献 DOI |
| `{known_conditions}` | 已有条件（复用 condition_id 避免重复） |

### 接口

```python
from miner.extraction_core.unified_agent import UnifiedExtractionAgent
agent = UnifiedExtractionAgent(llm, component="cathode")
result = agent.extract(paragraph, battery_system_context=ctx)
```

---

## 清除的文件

### 删除旧 v2 提取 JSON（`miner/json/`）

| 文件 | 原因 |
|---|---|
| `10.1021_acsnano.3c02948_electrolyte_extracted_v2.json` | v2 格式与 v5 输出不兼容，不再引用 |
| `10.1038_s41467-024-54637-9_cathode_all.json` | 同上 |
| `10.1038_s41467-024-54637-9_cathode_all_v2.json` | 同上 |
| `10.1038_s41467-024-54637-9_cathode_condition_v2.json` | 同上 |
| `10.1038_s41467-024-54637-9_cathode_material_v2.json` | 同上 |
| `10.1038_s41467-024-54637-9_cathode_perf_v2.json` | 同上 |
| `10.1039_d1ta07306k_anode_cond.json` | 同上 |
| `10.1039_d1ta07306k_anode_consolidated.json` | 同上 |
| `10.1039_d1ta07306k_anode_extracted_v2.json` | 同上 |

### 删除旧测试文件

| 文件 | 原因 |
|---|---|
| `miner/test_cathode_agent_v2.py` | v2 测试脚本，已被 v5 Unified Agent 替代，不再维护 |

---

## v5 Pipeline 三个文件运行结果

`miner/json/test/` 下生成：

```
electrolyte_electrolyte_extracted.json   - 14 个提取项
anode_anode_extracted.json               - 13 个提取项
cathode_cathode_extracted.json           - 26 个提取项 (含 7 个表格块)
_pipeline_summary.json                   - 总计 31 次 unified 调用，$0.098
```

### 数据清洗效果（`cleaner/cleaned_test/`）

```
anode/anode.md         -> 23,854 字, 28 段
cathode/cathode.md     -> 39,414 字, 44 段
electrolyte/electrolyte.md -> 33,878 字, 27 段
```

---

## 注意事项

1. `miner/cleaning/clean_text1.py` 与原有的 `miner/cleaning/clean_text.py` 独立并存，互不影响
2. 数字拼接修复（`1 0 0 0` -> `1000`）对 22/27 个 MinerU markdown 文件有效
3. 规则筛选放宽后，预期合成段落的 `Crystallite_Size` 等属性不会被漏提
4. 删除的旧 v2 JSON 文件不影响 v5 pipeline 运行，v5 使用独立输出路径 `miner/json/test/`