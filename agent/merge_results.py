"""
合并 rag_clean 下所有成分子目录的单篇 JSON → _all_rag.json + _all_conditioned_data.csv + _all_intrinsic_data.csv
不需要重新跑 LLM。
"""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.config import DEFAULT_OUTPUT_DIR
from agent.flatten_ml import flatten_to_rows, write_csv, write_json

OUTPUT = DEFAULT_OUTPUT_DIR / "rag_clean"


def main():
    results = []
    for comp in ["cathode", "anode", "electrolyte"]:
        d = OUTPUT / comp
        if not d.exists():
            continue
        for fp in sorted(d.glob("*_rag.json")):
            data = json.loads(fp.read_text(encoding="utf-8"))
            results.extend(data)
            print(f"  + {fp.relative_to(OUTPUT)}: {len(data)} 篇")

    if not results:
        print("没有找到任何 JSON 文件")
        return

    print(f"\n总计: {len(results)} 篇")

    # 写入 _all_rag.json
    write_json(results, OUTPUT / "_all_rag.json")
    print(f"  _all_rag.json: {len(results)} 篇")

    # 写入 CSV（先清空已有文件避免追加到旧数据）
    csv_path = OUTPUT / "_all_conditioned_data.csv"
    intr_path = OUTPUT / "_all_intrinsic_data.csv"
    if csv_path.exists():
        csv_path.unlink()
    if intr_path.exists():
        intr_path.unlink()

    write_csv(results, OUTPUT)
    print(f"  _all_conditioned_data.csv: 完成")
    print(f"  _all_intrinsic_data.csv: 完成")


if __name__ == "__main__":
    main()
