#!/usr/bin/env python3
"""提取结果可视化前端 — Gradio 版"""
import json, glob, os, sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))
import gradio as gr

EXTRACTED_DIR = str(_PROJECT_ROOT / "miner" / "json")
EXTRACTED_FILES = sorted(glob.glob(os.path.join(EXTRACTED_DIR, "*_extracted.json")))

def _fmt_cond_value(c):
    """从 condition 中提取结构化字段显示，不使用 condition_id"""
    parts = []
    if isinstance(c.get('temperature'), dict): t = c['temperature']; parts.append(f"{t.get('value','')}°{t.get('unit','')}")
    if isinstance(c.get('c_rate_or_current'), dict): r = c['c_rate_or_current']; parts.append(f"{r.get('value','')} {r.get('unit','')}")
    elec = c.get('electrolyte', '')[:40]
    if elec: parts.append(elec)
    meth = c.get('test_method', '')[:30]
    if meth: parts.append(meth)
    conf = c.get('electrode_config', '')[:30]
    if conf: parts.append(conf)
    return " | ".join(parts)

def _match_material(text, materials):
    names = []
    for m in materials:
        sn = m.get("short_name","")
        fn = m.get("name","")
        if sn: names.append((sn, len(sn)))
        if fn: names.append((fn, len(fn)))
    names.sort(key=lambda x: -x[1])
    matched = []
    for name, _ in names:
        if name in text and name not in matched:
            matched.append(name)
    return ", ".join(matched[:4]) if matched else "—"

def load_data(doi_label):
    if not doi_label:
        return [], [], "", "", ""
    fpath = doi_label.split(" | ")[0]
    data = json.load(open(fpath))
    meta = data.get("meta", {})
    materials = data.get("materials", [])
    items = data.get("items", [])
    mat_rows = []
    for m in materials:
        icon = "\U0001f195" if m.get("role") == "novel" else "\U0001f4ce"
        mat_rows.append([icon, m.get("short_name",""), m.get("name",""), m.get("formula",""), m.get("description","")[:60]])
    item_rows = []
    for idx, item in enumerate(items):
        # 合并 material 和 performance 信息
        info = {}
        info.update(item.get("extracted_info", {}))
        info.update(item.get("performance_info", {}))
        conds = item.get("conditions", [])
        cond_map = {}
        for c in conds:
            cid = c.get("condition_id", "")
            cond_map[cid] = _fmt_cond_value(c)

        para = item.get("paragraph", "")
        if not info: continue
        for pn, pv in info.items():
            vals = pv if isinstance(pv, list) else [pv]
            for v in vals:
                if isinstance(v, dict):
                    val = v.get("value",""); unit = v.get("unit",""); src = v.get("source_text","")[:100]
                    cid = v.get("condition_id","")
                    cond_detail = cond_map.get(cid, "")
                    if cid and cid in cond_map:
                        linked = _match_material(cond_detail + " " + src, conds, materials)
                    else:
                        linked = _match_material(para, conds, materials)
                    cond_d = cond_detail if cond_detail else "—"
                else:
                    val = str(v); unit = ""; src = ""
                    cond_d = "—"
                item_rows.append([idx, linked, cond_d, pn, f"{val} {unit}".strip(), para[:200], src])
    title = meta.get("meta_title", data.get("doi",""))
    doi = data.get("doi","")
    comp = data.get("component","")
    return mat_rows, item_rows, title, doi, comp

def list_papers():
    choices = []
    for fp in EXTRACTED_FILES:
        try:
            data = json.load(open(fp))
            meta = data.get("meta", {})
            title = meta.get("meta_title", "")[:60]
            choices.append(f"{fp} | {title}")
        except:
            pass
    return choices

choices = list_papers()
with gr.Blocks(title="数据挖掘结果面板") as demo:
    gr.Markdown("# \U0001f52c 数据挖掘提取结果")
    paper_selector = gr.Dropdown(choices=choices, label="选择文献", value=choices[0] if choices else None)
    with gr.Row():
        title_box = gr.Textbox(label="标题", scale=3)
        doi_box = gr.Textbox(label="DOI", scale=1)
        comp_box = gr.Textbox(label="组件", scale=1)
    gr.Markdown("## \U0001f9ea 识别的材料")
    mat_table = gr.Dataframe(headers=["","简称","全名","化学式","描述"], label="材料列表(🆕新型 📎对照)", interactive=False)
    gr.Markdown("## \U0001f4ca 提取的属性数据")
    item_table = gr.Dataframe(headers=["#","关联材料","条件详情","属性","数值","段落来源","原文片段"], label="材料→条件→属性→数值", interactive=False, column_widths=["40px","120px","150px","100px","100px","250px","150px"])

    def update_view(doi_label):
        return load_data(doi_label)

    paper_selector.change(fn=update_view, inputs=paper_selector, outputs=[mat_table, item_table, title_box, doi_box, comp_box])
    if choices:
        demo.load(fn=update_view, inputs=paper_selector, outputs=[mat_table, item_table, title_box, doi_box, comp_box])

if __name__ == "__main__":
    port = int(os.environ.get("GRADIO_SERVER_PORT", 7870))
    demo.launch(server_name="127.0.0.1", server_port=port)
