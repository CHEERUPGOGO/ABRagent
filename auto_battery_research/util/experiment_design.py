"""Stage 5B 实验推荐契约；Stage 5 只同步参数，Stage 6 只读取渲染。"""
from __future__ import annotations

import copy
import json
import re
import math
from datetime import datetime
from pathlib import Path

from .scheme_ledger import atomic_write, digest, flatten_scheme, validate_field, calculate
from .report_narrative import amount

FILENAME = "experiment_plan.json"
REPORT_FILENAME = "experiment_report.json"
SECTIONS = {"groups": "5.1 实验目标与分组", "fabrication": "5.2 材料制备与极片加工",
            "assembly": "5.3 电芯组装与化成", "testing": "5.4 性能测试与模型对照",
            "criteria": "5.5 结果判定与下一轮迭代", "gaps": "5.6 关键数据缺口"}
ROLES = {"recipe", "processing", "assembly", "formation", "testing"}
TOKEN = re.compile(r"\{\{([A-Za-z0-9_.-]+)\}\}")
SYSTEM_PROMPT = '''你负责 Stage 5B 的轻量首轮电池实验设计。参考给定文献、最终设计及物理结果，给出可执行推荐。
只输出 JSON：{"parameters":{},"sections":{"groups":[],"fabrication":[],"assembly":[],"testing":[],"criteria":[],"gaps":[]},"citations":[]}。
parameters 的键为字段路径，值有两种：
优先使用 cathode/anode/electrolyte/separator/current_collector/packaging/process/condition 前缀。工艺示例 process.cathode_mixing_temperature_K；实验专用 fabrication、assembly、testing 前缀也允许，只表示实验步骤参数，不写入统一方案表。
已定参数用 {"ref":"cathode.L_um"} 引用当前方案；缺失操作参数用 {"value":数值或字符串,"unit":"规范单位","basis":"engineering_proposal","source":"工程推荐","meaning":"中文含义","role":"recipe|processing|assembly|formation|testing","rationale":"推荐理由"}。
重点保持与方案表已有非空值一致；其他工艺值可合理推荐，不要求每项都有直接文献。配方、加工、装配、化成、测试应尽量具体，不强制每类都有数值。允许 budget_assumption 类型的设计容量等预算假设，注明用途和实测替换方法；不能冒充实测或写入 PINN。本征扩散系数仍需证据。对照变化用 scope=control 单独声明，不能混入主方案。推荐数值用 engineering_proposal。比例说明分母，温度 K。
sections 每项为中文段落数组。所有参数值通过 {{字段路径}} 引入，不在段落中直接复制数值，便于后续几何修订同步。各引用必须在 parameters 声明。材料选择也使用引用，不能擅换当前材料。
给一个主推荐组、少量对照组、平行样数量和固定条件。选择采购或自制路线，不要混写多条路线。说明配浆、涂布、干燥、辊压、装配、注液、化成和测试的具体安排。建议须形成一致方案，不是散乱参数清单。
保持当前材料和电压窗口；对照改变要单独声明。推荐条件不同于未来仿真条件时不能沿用其性能。文献只引用输入中真实 passage_id，用 [passage_id]，并在 citations 列出；没有直接文献支持的操作允许按工程起点推荐，但不能冒充文献实测。gaps 只保留必要物性获取和后续优化，不阻止首轮操作。'''


def effective_data(doc):
    data = copy.deepcopy(doc.get("ledger_view") or {"parameters": {}, "derived": {}, "issues": []})
    for key, value in flatten_scheme(doc.get("scheme") or {}).items():
        data["parameters"].setdefault(key, {})["value"] = value
    return data


def plan_hash(record):
    return digest({k: v for k, v in record.items() if k not in ("plan_hash", "bindings", "sync_history", "text")})


def parameter_validation_key(key):
    """实验步骤命名空间与方案表字段分开；复用相同单位和数值检查。"""
    if isinstance(key, str) and key.partition(".")[0] in ("fabrication", "assembly", "testing"):
        return "process." + key.partition(".")[2]
    return key


def overlap_key(key):
    aliases = {"thickness_um": "L_um", "particle_radius_um": "R_s_um", "diffusion_coefficient_m2s": "D_s_m2s"}
    key = parameter_validation_key(key)
    for comp in ("cathode", "anode"):
        for alias, canonical in aliases.items():
            if key in (f"{comp}.{alias}", f"process.{comp}_{alias}"):
                return f"{comp}.{canonical}"
    return key


def validate(record, doc):
    if not isinstance(record, dict) or record.get("schema_version") not in (2, 3) or record.get("generated_stage") not in (4, 5):
        raise ValueError("缺少 Stage 5B 已生成的实验推荐，请基于已有设计及文献重新生成")
    if record.get("plan_hash") != plan_hash(record):
        raise ValueError("实验方案内容指纹不一致")
    data = effective_data(doc)
    current = data["parameters"]
    for key, value in record.get("fixed_fields", {}).items():
        if value is not None and (current.get(key) or {}).get("value") != value:
            raise ValueError(f"实验推荐的材料或固定条件已变更: {key}，需重新生成 Stage 5B 实验方案")
    parameters = record.get("parameters")
    sections = record.get("sections")
    if not isinstance(parameters, dict) or not isinstance(sections, dict):
        raise ValueError("实验方案参数或章节结构不完整")
    roles, bindings = set(), {}
    for key, row in parameters.items():
        if not isinstance(row, dict):
            raise ValueError("实验参数必须为结构化记录")
        if "ref" in row:
            ref = row["ref"]
            value = current.get(ref) or {}
            if value.get("value") is None:
                raise ValueError(f"引用的当前参数缺失: {ref}")
            bindings[key] = copy.deepcopy(value)
        else:
            validation_key = overlap_key(key)
            declared = current.get(validation_key) or {}
            if declared.get("value") is not None and row.get("scope") != "control":
                if row.get("value") != declared["value"] or row.get("unit", "") != declared.get("unit", ""):
                    raise ValueError(f"实验参数与方案表冲突: {key}")
                bindings[key] = copy.deepcopy(declared)
                continue
            if isinstance(row.get("value"), float) and not math.isfinite(row["value"]):
                raise ValueError("推荐数值必须有限")
            if row.get("basis") not in ("engineering_proposal", "budget_assumption") or row.get("value") is None:
                raise ValueError("推荐操作参数须有具体值且标为工程建议")
            bindings[key] = row
            data["parameters"][validation_key] = row
        if isinstance(bindings[key].get("value"), (int, float)) and row.get("role") in ROLES:
            roles.add(row["role"])
    allowed_ids = {str(e.get("passage_id")) for e in (doc.get("evidence") or []) + (doc.get("experiment_evidence") or [])}
    cited = set(record.get("citations") or [])
    if not cited.issubset(allowed_ids):
        raise ValueError("实验推荐引用未归档文献")
    used = set()
    for section in SECTIONS:
        paragraphs = sections.get(section)
        if not isinstance(paragraphs, list) or not paragraphs or not all(isinstance(p, str) and p.strip() for p in paragraphs):
            raise ValueError(f"实验方案缺少可执行章节: {section}")
        for paragraph in paragraphs:
            if any(x in paragraph for x in ("```", "<!--", "已通过实验验证", "已达到目标")) or paragraph.lstrip().startswith("|"):
                raise ValueError("实验段落包含原始表格或未经验证的性能声明")
            refs = set(TOKEN.findall(paragraph))
            if not refs.issubset(bindings):
                raise ValueError("段落引用未声明的实验参数")
            used.update(refs)
            cited.update(re.findall(r"\[([A-Za-z0-9_-]+)\]", paragraph))
            remainder = TOKEN.sub("", paragraph)
            if not any(word in paragraph for word in ("对照", "梯度", "化成", "预算假设")):
                for component, cn in (("cathode", "正极"), ("anode", "负极")):
                    for field, name in (("L_um", "厚度"), ("R_s_um", "颗粒半径")):
                        declared = (current.get(f"{component}.{field}") or {}).get("value")
                        matches = re.findall(cn + r"[^。；\d]{0,12}" + name + r"[^\d]{0,5}(\d+(?:\.\d+)?)\s*(?:μm|um)", remainder)
                        if declared is not None and any(not math.isclose(float(v), float(declared), rel_tol=1e-6) for v in matches):
                            raise ValueError(f"实验正文与方案表重叠参数冲突: {component}.{field}")
            # 非重叠操作值允许直接叙述，不因表达形式反复生成。
    if not cited.issubset(allowed_ids):
        raise ValueError("段落引用未归档文献")
    if not used and len("".join(p for ps in sections.values() for p in ps)) < 100:
        raise ValueError("实验方案缺少可执行内容")
    return bindings


def load(task_dir):
    path = Path(task_dir) / FILENAME
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def generate(task_dir, doc, client, scheme_md=""):
    """调用点只在 Stage 5B；失败记录不覆盖已有有效实验方案。"""
    fingerprint = digest({"scheme": doc.get("scheme"), "ledger": doc.get("ledger_hash"),
                          "evidence": doc.get("evidence"), "physics": doc.get("physics_context"), "analysis": scheme_md, "version": 4})
    old = load(task_dir)
    if old and old.get("generated_stage") == 4:
        try:
            bindings = validate(old, doc)
            old.update(origin_stage=4, generated_stage=5, schema_version=3,
                       input_fingerprint=fingerprint, bindings=bindings)
            old["plan_hash"] = plan_hash(old)
            atomic_write(Path(task_dir) / FILENAME, json.dumps(old, ensure_ascii=False, indent=2))
            return old
        except (ValueError, TypeError):
            pass
    if old and old.get("input_fingerprint") == fingerprint:
        validate(old, doc)
        return old
    error = "实验设计模型不可用"
    if client is not None and hasattr(client, "max_tokens"):
        client.max_tokens = min(client.max_tokens, 6000)
        client.output_max_tokens = 6000
        client.single_attempt = True
    for _ in range(1) if client is not None and getattr(client, "available", False) else ():
        try:
            raw = client.chat(SYSTEM_PROMPT, json.dumps({"goal": doc.get("target"), "scheme": doc.get("scheme"),
                "ledger": doc.get("ledger_view"), "analysis": scheme_md, "review": doc.get("reviewer_output"),
                "evidence": doc.get("evidence"), "physics_context": doc.get("physics_context"), "last_error": error}, ensure_ascii=False),
                temperature=0.2, trace_tag="experiment_design")
            record = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            record.update(schema_version=3, generated_stage=5, input_fingerprint=fingerprint,
                          generated_at=datetime.now().isoformat(), base_ledger_hash=doc.get("ledger_hash"),
                          fixed_fields={k: v.get("value") for k, v in effective_data(doc)["parameters"].items()
                            if k not in {f"{c}.{f}" for c in ("cathode", "anode") for f in ("L_um", "porosity", "R_s_um", "D_s_m2s")}})
            record["plan_hash"] = plan_hash(record)
            record["warnings"] = []
            from .experiment_repair import repair_record
            record = repair_record(record, doc, client, Path(task_dir), fingerprint)
            for key, row in record["parameters"].items():
                if "ref" not in row and (effective_data(doc)["parameters"].get(overlap_key(key)) or {}).get("value") is None:
                    try:
                        validate_field(parameter_validation_key(key), row)
                    except ValueError as exc:
                        record["warnings"].append(str(exc))
            record["plan_hash"] = plan_hash(record)
            record["bindings"] = validate(record, doc)
            record["sync_history"] = []
            atomic_write(Path(task_dir) / FILENAME, json.dumps(record, ensure_ascii=False, indent=2))
            atomic_write(Path(task_dir) / "experiment_plan_failure.json", json.dumps({"resolved": record.get("repair_status") != "PARTIAL",
                "repair_status": record.get("repair_status"), "warnings": record.get("warnings", [])}, ensure_ascii=False))
            return record
        except Exception as exc:
            error = str(exc)
    atomic_write(Path(task_dir) / "experiment_plan_failure.json", json.dumps({"resolved": False, "error": error,
        "input_fingerprint": fingerprint}, ensure_ascii=False, indent=2))
    if doc.get("delivery_contract") == 1:
        return engineering_fallback(task_dir, doc, fingerprint, error)
    raise ValueError("Stage 5B 实验推荐生成失败: " + error)


def engineering_fallback(task_dir, doc, fingerprint, error):
    """按当前材料给首轮可操作起点；未知物性仍须补测。"""
    current = effective_data(doc)["parameters"]
    parameters = {k: {"ref": k} for k, row in current.items()
                  if row.get("value") is not None and k in ("cathode.material", "anode.material", "electrolyte.material", "condition.v_min", "condition.v_max", "condition.c_rate")}
    def proposal(key, value, unit, meaning):
        parameters[key] = {"value": value, "unit": unit, "meaning": meaning,
            "basis": "engineering_proposal", "source": "首轮工程推荐，待小批验证", "role": "testing"}
    proposal("testing.replicates", 3, "", "每组平行样数量")
    proposal("fabrication.drying_temperature_K", 353.15, "K", "含溶剂极片的小批干燥起点，适用性须先核对")
    fixed = "；".join("{{" + k + "}}" for k in parameters if k.endswith("material"))
    sections = {
        "groups": ["验证当前候选体系：" + (fixed or "核心材料尚未确定，先完成材料定案再投料") + "。设置原始材料基准组、推荐改性组和去除改性步骤的对照组；每组采用{{testing.replicates}}个平行样。所有性能目标均待验证。"],
        "fabrication": ["优先采购同批次可追溯材料；掺杂或包覆采用小批梯度，先用XRD与成分分析确认组成及杂相。含溶剂涂层可将{{fabrication.drying_temperature_K}}作为干燥筛选起点，先核对粘结剂和溶剂适用性，称重至恒重后测残溶、水分及粘附。致密锂片直接裁切，不套用浆料涂布步骤。已有厚度和配方以方案表为准。"],
        "assembly": ["先做扣式电池验证，固定同批隔膜、电极面积与装配结构，在适合当前材料的干燥惰性环境操作。电解液配方保持当前方案；逐滴浸润并称量记录实际注液量，通过拆解确认充分润湿后再确定注液规格。不将缺少容量依据的注液量写成固定g/Ah。"],
        "formation": ["以低电流进行首轮化成，电压严格使用已确定窗口。窗口未定时先完成半电池CV及电解液稳定窗口核对，再确定截止条件；不能直接照搬其他材料的电压。记录首效、产气和阻抗，异常组停止后分析。"],
        "testing": ["测试温度、倍率和截止电压优先沿用当前已定条件；未定条件先做低倍率探索，确认可逆区间后逐级提高电流。记录完整充放电曲线、首效、阻抗与容量保持；基准和推荐组采用相同循环圈数及归一化口径，禁止直接把跨体系文献值当成当前电池结果。"],
        "criteria": ["先确认组成、相结构及质量收支，再比较改性组与基准组的容量、保持率和气体释放；仅当重复样的改善超过测量不确定性时保留该路线。比能量采用实测放电能量和完整电芯质量，不凭模型或材料级数据宣布达标。"],
        "gaps": ["本轮模型实验推荐未成功：" + str(error) + "。上述步骤为工程起点，需人工核对。缺失物性采用GITT、EIS及结构表征补测，缺失配方、化成电压和装配参数通过小批对照确定后回填方案表。"]}
    sections["assembly"] += sections.pop("formation")
    if doc.get("report_readiness", {}).get("category") == "DIAGNOSTIC":
        for section in ("fabrication", "assembly", "testing", "criteria"):
            sections[section] = ["当前尚无可被接受的一致材料方案，暂停投料、装配和定量性能判断。先核对正负极与电解液的明确声明、组成及工况，解决方案表中的冲突；保留原始文献与失败原因，完成定案后再生成具体实验路线。"]
    record = {"schema_version": 3, "generated_stage": 5, "input_fingerprint": fingerprint,
        "base_ledger_hash": doc.get("ledger_hash"), "parameters": parameters, "sections": sections,
        "citations": [], "fixed_fields": {k: v["value"] for k, v in current.items() if v.get("value") is not None},
        "origin": "engineering_fallback", "warnings": [error], "sync_history": []}
    record["plan_hash"] = plan_hash(record)
    record["bindings"] = validate(record, doc)
    atomic_write(Path(task_dir) / FILENAME, json.dumps(record, ensure_ascii=False, indent=2))
    return record


def sync(task_dir, doc, dry_run=False):
    if not doc.get("experiment_contract"):
        return
    record = load(task_dir)
    if not record or record.get("plan_hash") != doc.get("experiment_plan_hash"):
        raise ValueError("当前设计与实验推荐不匹配")
    bindings = validate(record, doc)
    if not dry_run and bindings != record.get("bindings"):
        record["bindings"] = bindings
        record.setdefault("sync_history", []).append({"ledger_hash": doc.get("ledger_hash"), "time": datetime.now().isoformat()})
        atomic_write(Path(task_dir) / FILENAME, json.dumps(record, ensure_ascii=False, indent=2))


def render(record, doc, result=None):
    from .report_narrative import readable_units
    bindings = validate(record, doc)
    def substitute(match):
        key = match[1]
        return amount(key, bindings[key])
    sections = ["以下为首轮实验推荐，补充的操作参数属于工程建议，尚不代表实测或性能已验证。"]
    assumptions = [(k, row) for k, row in record.get("parameters", {}).items() if row.get("basis") == "budget_assumption"]
    if assumptions:
        sections.append("预算假设：" + "；".join(f"{row.get('meaning', '设计估算')}取{amount(k, row)}" for k, row in assumptions)
            + "。这些假设仅用于实验预算与容量匹配，不作为材料实测值或 PINN 物性输入，须按首轮实测修正。")
    for key, heading in SECTIONS.items():
        sections.append("### " + heading)
        for index, paragraph in enumerate(record["sections"][key], 1):
            body = readable_units(TOKEN.sub(substitute, paragraph))
            if key in ("fabrication", "assembly", "testing"):
                # 只添加步骤标记，不改变已通过校验的操作内容及先后顺序。
                titles = (("配浆|浆料|混料", "配浆与分散"), ("涂布|干燥|烘烤", "涂布与干燥"),
                          ("辊压", "辊压与检查"), ("注液|浸润", "注液与浸润"),
                          ("化成|老化", "化成与老化"), ("容量标定|标定", "容量标定"),
                          ("循环", "循环测试"), ("阻抗|内阻", "阻抗表征"), ("拆解|称量", "拆解与质量核算"))
                # 用开头动作识别段落主题，不借格式化推断新的操作步骤。
                hits = [(m.start(), title) for pattern, title in titles
                        if (m := re.search(pattern, body[:45]))]
                title = min(hits)[1] if hits else {"fabrication": "极片制备", "assembly": "电芯装配", "testing": "性能验证"}[key]
                body = body.replace("\n", "\n   ")
                sections.append(f"{index}. **{title}：** " + body)
            elif key == "criteria":
                sections.append(f"- {body}")
            elif key == "groups":
                chunks = re.split(r"(?<=[。；])(?=(?:对照组\s*[A-Z]\d|固定条件|两组对照))", body)
                for chunk in chunks:
                    control = re.match(r"对照组\s*[A-Z]\d+", chunk)
                    title = (control[0] if control else "固定条件" if chunk.startswith("固定条件") else
                             "容量匹配" if chunk.startswith("主组") else "分组安排")
                    sections.append(f"**{title}。** " + chunk)
            else:
                sections.append(body)
        if key == "groups":
            data = effective_data(doc)
            sections.append("**方案绑定。** 本轮材料沿用第 3 节的当前方案。第 3 节尚未声明的操作条件如在本节给出，均为首轮实验建议；对照组的变更仅适用于对应分组，不改变主方案或仿真输入。")
        if key == "testing":
            if (result or {}).get("status") == "CONVERGED":
                sections.append("现有模型仅验证对应输入下的单次放电。推荐实验新增的配方、工艺和测试条件未因此得到验证，不能沿用模型结果作为实验性能。")
            else:
                sections.append("本次未完成有效放电仿真，不存在可用于性能判断的仿真值。")
    if record.get("citations"):
        sections.append("本节参考的归档文献证据为" + "、".join(f"[{pid}]" for pid in record["citations"]) + "。具体工程推荐仍需通过首轮实验验证。")
    return "\n\n".join(sections)


def require_current(task_dir, doc):
    record = load(task_dir)
    bindings = validate(record, doc)
    if record["plan_hash"] != doc.get("experiment_plan_hash") or bindings != record.get("bindings"):
        raise ValueError("实验推荐与当前设计或参数绑定不一致，请先同步 Stage 5B 实验方案")
