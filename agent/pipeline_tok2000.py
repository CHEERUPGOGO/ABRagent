"""Pipeline RAG token 版 CMAX=2000 tokens — 使用 agent.clean_rag_tok2000
对比用: token 切分 vs 字符切分（rag_clean CMAX=2000）"""
import sys, os, json, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.config import *
from agent.phase0_discovery import discover as phase0_discover
from agent.phase12_extract import run_sequential
from agent.phase3_merge_agent import run_phase3
from agent.flatten_ml import flatten_to_rows, write_csv, write_json
from agent.clean_rag_tok2000 import clean as _rag_clean
from agent.material_norm import MaterialNormalizer
from agent.label_norm import LabelNormalizer
from agent.cell_assembler import assemble_cells
L = logging.getLogger("PipelineTok2000")
def _llm(m, t=0.1):
    from langchain_openai import ChatOpenAI
    import os
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
    if not api_key:
        try:  # 回退到 miner/config.yaml（与 create_llm 一致）
            from miner.config import load_config
            _cfg_llm = load_config().get("llm", {})
            api_key = _cfg_llm.get("api_key", "")
            base_url = os.getenv("DEEPSEEK_API_BASE", _cfg_llm.get("base_url", base_url))
        except Exception:
            pass
    return ChatOpenAI(api_key=api_key, base_url=base_url, model=m,
                      temperature=t, max_tokens=1024, request_timeout=120)
def _meta():
    if not META_JSON_PATH.exists():
        return {}
    with open(META_JSON_PATH) as f:
        i = {}
        for m in json.load(f):
            d = (m.get("doi") or "").strip()
            if d:
                i[d] = i[d.replace("/","_")] = m
        return i
def _doi(fp):
    import re
    s = Path(fp).stem.replace("_","/")
    return s if re.match(r"^10\.\d{4,}/", s) else ""
def _comp(fp):
    for p in reversed(Path(fp).parts):
        if p.lower() in ("cathode","anode","electrolyte"):
            return p.lower()
    return "unknown"
def _fm(comp):
    try:
        if comp=="cathode":
            from miner.cathode_database.cathode_formatter import CathodeFormatter; return CathodeFormatter
        if comp=="anode":
            from miner.anode_database.anode_formatter import AnodeFormatter; return AnodeFormatter
        if comp=="electrolyte":
            from miner.electrolyte_database.electrolyte_formatter import ElectrolyteFormatter; return ElectrolyteFormatter
    except: pass
    return None
def run(input_root=None, output_dir=None, component="all", extract_model=None, merge_model=None, max_files=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    ir = input_root or str(DEFAULT_INPUT_ROOT)
    od = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    llm = _llm(extract_model or EXTRACTION_MODEL, EXTRACTION_TEMPERATURE)
    mllm = _llm(merge_model or MERGE_MODEL, MERGE_TEMPERATURE)
    meta = _meta()
    norm = MaterialNormalizer()          # 材料归一化（词表：candidates.json + alias_map.json）
    label_norm = LabelNormalizer()       # 属性名归一化（标准标签：format1 formatter）
    unmatched_agg = {}                   # (name, formula) -> 频次，跨文献聚合未命中候选
    unmatched_label_agg = {}             # property_name -> 频次，跨文献聚合未命中属性名
    tasks = []
    root = Path(ir)
    if root.is_file():
        if SKIP_ZH_DOCS and is_zh_doc(str(root)):
            L.info(f"[跳过中文文献] {root.name}"); return tasks
        tasks.append({"fp": str(root)})
    else:
        seen = set()
        for d,_,fs in os.walk(ir):
            if any(x in d for x in ["Solid_State","test","text_cathode"]): continue
            b = os.path.basename(d)
            if b not in ("cathode","anode","electrolyte"): continue
            if component!="all" and b!=component: continue
            for f in fs:
                if not f.endswith(".md"): continue
                fp = os.path.join(d, f)
                if SKIP_ZH_DOCS and is_zh_doc(fp): continue
                s = f.replace(".md","")
                if (b, s) not in seen:
                    seen.add((b, s)); tasks.append({"fp": fp})
    L.info(f"文件: {len(tasks)}")
    # 增量：先过滤已处理的文献，再按 max_files 截断（保证每次跑 N 篇"未处理"的）
    _pending = []
    for _t in tasks:
        _fp = _t["fp"]; _c = _comp(_fp); _stem = Path(_fp).stem
        if not (od / "tok2000" / _c / f"{_stem}_rag.json").exists():
            _pending.append(_t)
    L.info(f"已处理跳过: {len(tasks) - len(_pending)}, 待处理: {len(_pending)}")
    tasks = _pending
    if max_files: tasks = tasks[:max_files]
    results = []
    for t in tasks:
        try:
            fp = t["fp"]
            comp = _comp(fp); doi = _doi(fp)
            stem = Path(fp).stem
            # 跳过已处理的文献（单篇 JSON 存在则跳过）
            skip_path = (od / "tok2000" / comp / f"{stem}_rag.json")
            if skip_path.exists():
                L.info(f"  跳过 (已处理): {stem}")
                continue
            L.info(f"\n--- tok2000: {stem} ---")
            ps = _rag_clean(fp)
            if not ps: continue
            table_rows = []
            try:
                from bs4 import BeautifulSoup
                with open(fp, encoding="utf-8") as rf:
                    raw_md = rf.read()
                soup = BeautifulSoup(raw_md, "html.parser")
                for tag in soup.find_all("table"):
                    headers = [th.get_text(strip=True) for th in tag.find_all("th")]
                    rows = []
                    for tr in tag.find_all("tr"):
                        cells = [td.get_text(strip=True) for td in tr.find_all(["td","th"])]
                        if cells: rows.append(cells)
                    if not headers and rows: headers = rows.pop(0)
                    if headers:
                        tag_str = str(tag)
                        pos = raw_md.find(tag_str)
                        ctx_before = raw_md[max(0,pos-300):pos].strip() if pos >= 0 else ""
                        # 提取表格标题: ctx_before 末尾包含 "Table" 的行
                        caption_lines = [l for l in ctx_before.split("\n") if l.strip().lower().startswith("table")]
                        caption = caption_lines[-1].strip() if caption_lines else ""
                        # 逐行拆成独立段落，让 include 自己判断材料归属
                        for r in rows:
                            row_name = r[0].strip() if r else "?"
                            # 把列头+值拼成 key-value 对
                            kv_pairs = [f"{h}: {c}" for h, c in zip(headers, r) if c.strip()]
                            row_text = f"[TABLE ROW: {row_name}]"
                            if caption:
                                row_text += f" | Table: {caption}"
                            row_text += "\n" + " | ".join(kv_pairs)
                            ps.append(row_text)
                            table_rows.append(row_text)
                        L.info(f"  +表格: {len(rows)} 行 -> {len(rows)} 段")
            except Exception as e:
                L.warning(f"表格提取失败: {e}")
            L.info(f"段落: {len(ps)}")
            from miner.cleaning.cleaner_v2_standalone import clean_markdown
            raw_text = clean_markdown(fp, min_len=0) or ""
            if table_rows:
                # 表格行文本并入材料发现输入（补齐只在表格里出现的材料）
                raw_text = raw_text + "\n\n" + "\n\n".join(table_rows)
            mats, ic = phase0_discover(llm, raw_text, comp, Path(fp).stem)
            unmatched = norm.normalize_materials(mats)
            if unmatched:
                L.info(f"  [归一化] 未命中 {len(unmatched)} 个材料候选: {[u['name'][:30] for u in unmatched]}")
                for u in unmatched:
                    f = u.get("formula", "")
                    if isinstance(f, dict):  # phase0 电解液配方是结构化 dict，序列化后做 key
                        f = json.dumps(f, sort_keys=True, ensure_ascii=False)
                    k = (u.get("name", ""), f)
                    unmatched_agg[k] = unmatched_agg.get(k, 0) + 1
            gc, ac, ai = run_sequential(llm, ps, component=comp, ic=ic, materials=mats)
            norm.normalize_condition_components(gc)  # 条件组件归一化：electrolyte/counter_electrode -> id
            merged = run_phase3(mllm, gc, ac, ai, doi)
            cells = assemble_cells(merged.get("materials", []), doi=doi)  # cell 组装（电化学属性挂 cell）
            if cells:
                L.info(f"  [cell] 组装 {len(cells)} 个电芯")
            unmatched_labels = label_norm.check_materials(comp, merged.get("materials", []))
            if unmatched_labels:
                L.info(f"  [属性归一化] 未命中 {len(unmatched_labels)} 个属性名: {sorted(set(u['property_name'] for u in unmatched_labels))[:8]}")
                for u in unmatched_labels:
                    k = u.get("property_name", "")
                    unmatched_label_agg[k] = unmatched_label_agg.get(k, 0) + 1
            r = {"file":fp,"component":comp,"doi":doi,"title":"","global_conditions":gc,"materials":merged.get("materials",[]),"discovered_materials":mats,"unmatched_materials":unmatched,"unmatched_labels":unmatched_labels,"cells":cells}
            if r.get("materials"):
                results.append(r)
                sd = od / "tok2000" / comp; sd.mkdir(parents=True,exist_ok=True)
                write_json([r], sd / f"{Path(fp).stem}_rag.json")
                cond,_ = flatten_to_rows(r)
                if cond:
                    from agent.flatten_ml import _write_csv_rows, FLATTEN_CONDITIONED_HEADERS
                    _write_csv_rows(cond, FLATTEN_CONDITIONED_HEADERS, sd / f"{Path(fp).stem}_cond.csv")
        except Exception as e:
            import traceback
            L.error(f"fail {t.get('fp','?')}: {e}\n{traceback.format_exc()}")
    if results:
        write_csv(results, od / "tok2000")
        write_json(results, od / "tok2000" / "_all_rag.json")
        L.info(f"\n完成: {len(results)} 篇")
    if unmatched_agg:
        agg_out = [{"name": n, "formula": f, "count": c}
                   for (n, f), c in sorted(unmatched_agg.items(), key=lambda kv: -kv[1])]
        agg_path = od / "tok2000" / "_unmatched_all.json"
        write_json(agg_out, agg_path)
        L.info(f"未命中聚合: {len(agg_out)} 个候选 -> {agg_path}")
    if unmatched_label_agg:
        agg_out = [{"property_name": n, "count": c}
                   for n, c in sorted(unmatched_label_agg.items(), key=lambda kv: -kv[1])]
        agg_path = od / "tok2000" / "_unmatched_labels_all.json"
        write_json(agg_out, agg_path)
        L.info(f"属性未命中聚合: {len(agg_out)} 个候选 -> {agg_path}")
    return results
if __name__ == "__main__":
    import argparse
    a = argparse.ArgumentParser(); a.add_argument("-i"); a.add_argument("-o"); a.add_argument("-c",default="all"); a.add_argument("--extract-model"); a.add_argument("--merge-model"); a.add_argument("--max-files",type=int)
    k = vars(a.parse_args())
    run(input_root=k.pop("i"), output_dir=k.pop("o"), component=k.pop("c"), **{x:y for x,y in k.items() if y})
