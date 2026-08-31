# Meta 与三个提取 Agent 结果关联改法

## 目标

让同一篇文献的 meta 信息和三个 agent 的提取结果稳定关联起来：

- meta：标题、作者、DOI、文件路径
- condition agent：电化学测试条件
- material agent：材料属性
- performance agent：电化学测试属性

最小原则：不重构流水线，不新增复杂实体，只在 `miner/extraction_core/extraction_pipeline.py` 里增强 meta 匹配和输出字段。

## 核心思路

使用同一个文献级主键：

```text
paper_id = DOI
```

如果没有 DOI，则退化为文件名 stem：

```text
paper_id = file_stem
```

三个 agent 的结果都挂在同一个 `paper_id` 下。这样每篇文献能对应自己的 meta、材料属性、测试条件和性能数据。

## 为什么不用完整 file_path 关联

你的 `meta_merged.json` 里可能保存的是 Linux 路径，例如：

```text
/home/ls/xiaoyue/LLM2/LMLLM/papers/merged/242/10.1021_acs.jpclett.5c02686.md
```

但现在项目运行在 Windows：

```text
E:\Chem\llm\...
```

所以不要只靠完整路径匹配。更稳的是同时索引：

- DOI：`10.1021/acs.jpclett.5c02686`
- 文件名：`10.1021_acs.jpclett.5c02686.md`
- stem：`10.1021_acs.jpclett.5c02686`
- DOI 转文件名形式：`10.1021_acs.jpclett.5c02686`

## 修改 1：增强 `load_meta_index()`

文件：

```text
miner/extraction_core/extraction_pipeline.py
```

把 `load_meta_index()` 改成：

```python
def load_meta_index() -> Dict[str, dict]:
    """加载 meta_merged.json，建立 doi / 文件名 / stem 的索引。"""
    mp = _PROJECT_ROOT / "miner" / "json" / "meta_merged.json"
    if not mp.exists():
        logger.warning("meta_merged.json 不存在")
        return {}

    with open(mp, encoding="utf-8") as f:
        metas = json.load(f)

    idx = {}
    for m in metas:
        doi = (m.get("doi") or "").strip()
        fp = m.get("file_path") or ""

        if doi:
            doi_as_stem = doi.replace("/", "_")
            idx[doi] = m
            idx[doi_as_stem] = m
            idx[doi_as_stem + ".md"] = m

        if fp:
            fname = os.path.basename(fp)
            stem = os.path.splitext(fname)[0]
            idx[fname] = m
            idx[stem] = m

    logger.info(f"加载 meta 索引: {len(idx)} 条")
    return idx
```

## 修改 2：新增 `find_meta_for_file()`

放在 `load_meta_index()` 后面即可：

```python
def find_meta_for_file(meta_lookup: dict, file_path: str) -> dict:
    """根据当前处理文件匹配 meta，避免受不同操作系统路径影响。"""
    fname = os.path.basename(file_path)
    stem = os.path.splitext(fname)[0]
    doi_from_name = stem.replace("_", "/")

    return (
        meta_lookup.get(fname)
        or meta_lookup.get(stem)
        or meta_lookup.get(doi_from_name)
        or meta_lookup.get(doi_from_name.replace("/", "_"))
        or {}
    )
```

## 修改 3：让 `build_material_context()` 优先使用 meta DOI

把原来的 DOI 生成逻辑：

```python
doi = file_stem.replace("_", "/")
```

改成：

```python
doi = (meta.get("doi") if meta else None) or file_stem.replace("_", "/")
paper_id = doi or file_stem
```

完整函数建议改成：

```python
def build_material_context(meta: dict, component: str, file_stem: str) -> dict:
    """以 meta + 文件名构建 agent 上下文变量。"""
    mid = file_stem
    doi = (meta.get("doi") if meta else None) or file_stem.replace("_", "/")
    paper_id = doi or file_stem
    title = meta.get("title", "")[:80] if meta else ""
    ctx = f"文献: {title}\nDOI: {doi}\n组件类型: {component}"

    return {
        "paper_id": paper_id,
        "material_id": mid,
        "battery_system_context": ctx,
        "doi": doi,
        "meta_title": title,
        "meta_authors": meta.get("authors", "") if meta else "",
        "meta_year": meta.get("publication_date", "") if meta else "",
    }
```

## 修改 4：在 `process_file()` 里使用新匹配函数

找到原来的代码：

```python
meta = meta_lookup.get(fname) or {}
ctx_info = build_material_context(meta, component, file_stem)
```

改成：

```python
meta = find_meta_for_file(meta_lookup, file_path)
ctx_info = build_material_context(meta, component, file_stem)
```

## 修改 5：最终输出加 `paper_id`

找到 `process_file()` 最后的返回值：

```python
return {
    "file": file_path, "component": component, "doi": doi,
    "material_id": mid,
    "meta": {k: ctx_info[k] for k in ["meta_title","meta_authors","meta_year","meta_first_author"] if k in ctx_info},
    "n_items": len(items), "items": items,
}
```

改成：

```python
return {
    "file": file_path,
    "component": component,
    "paper_id": ctx_info["paper_id"],
    "doi": doi,
    "material_id": mid,
    "meta": {k: ctx_info[k] for k in ["meta_title", "meta_authors", "meta_year"] if k in ctx_info},
    "n_items": len(items),
    "items": items,
}
```

## 修改后的输出形态

单篇文献的提取结果会包含：

```json
{
  "file": ".../10.1021_acs.jpclett.5c02686.md",
  "component": "anode",
  "paper_id": "10.1021/acs.jpclett.5c02686",
  "doi": "10.1021/acs.jpclett.5c02686",
  "material_id": "10.1021_acs.jpclett.5c02686",
  "meta": {
    "meta_title": "In Situ NMR Investigations...",
    "meta_authors": "Shiyu Liu, ...",
    "meta_year": "2025"
  },
  "n_items": 3,
  "items": []
}
```

## 后续可选增强

如果后面要把所有 JSON 合并入库，建议把局部 ID 改成全局唯一：

```text
material_id = paper_id + "::" + material_id
condition_id = paper_id + "::" + condition_id
```

但这不是当前最小改法必须做的。当前只加 `paper_id`，已经足够把 meta 和三个 agent 结果按文献关联起来。

## 最小验证

改完后运行一篇文献测试：

```powershell
python -m miner.extraction_core.extraction_pipeline --limit 1 --component anode
```

然后检查输出 JSON 中是否有：

```json
"paper_id": "...",
"meta": {
  "meta_title": "...",
  "meta_authors": "...",
  "meta_year": "..."
}
```

如果 `meta` 为空，优先检查：

- `miner/json/meta_merged.json` 是否存在
- 当前处理文件名是否能由 DOI 转成，例如 `10.1021/xxx` 对应 `10.1021_xxx.md`
- meta 里的 DOI 是否为空
