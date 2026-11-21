"""Stage 5B 实验推荐契约；Stage 5 只同步参数，Stage 6 只读取渲染。"""
from __future__ import annotations

import copy
import json
import re
import math
from datetime import datetime
from pathlib import Path

from .scheme_ledger import atomic_write, digest, flatten_scheme
from .report_narrative import amount
from .experiment_titles import INSTRUCTIONS as TITLE_INSTRUCTIONS
from .experiment_protocol import STYLE_INSTRUCTIONS
from .experiment_consistency import INSTRUCTIONS as CONSISTENCY_INSTRUCTIONS

FILENAME = "experiment_plan.json"
REPORT_FILENAME = "experiment_report.json"
SECTIONS = {"groups": "5.1 实验设计与样品分组", "fabrication": "5.2 材料制备与极片加工",
            "assembly": "5.3 电池制作与装配", "testing": "5.4 电化学测试与材料表征",
            "criteria": "5.5 数据处理与结果判定", "gaps": "5.6 待确认条件与获取方法"}
TOKEN = re.compile(r"\{\{([A-Za-z0-9_.-]+)\}\}")
SYSTEM_PROMPT = '''你负责 Stage 5B 的轻量首轮电池实验设计。参考给定文献、最终设计及物理结果，给出可执行推荐。
只输出 JSON：{"parameters":{},"sections":{"groups":[],"fabrication":[],"assembly":[],"testing":[],"criteria":[],"gaps":[]},"citations":[]}。
parameters 的键为字段路径，值有两种：
优先使用 cathode/anode/electrolyte/separator/current_collector/packaging/process/condition 前缀。工艺示例 process.cathode_mixing_temperature_K；实验专用 fabrication、assembly、testing 前缀也允许，只表示实验步骤参数，不写入统一方案表。
已定参数用 {"ref":"cathode.L_um"} 引用当前方案；缺失操作参数用 {"value":数值或字符串,"unit":"规范单位","basis":"engineering_proposal","source":"工程推荐","meaning":"中文含义","role":"recipe|processing|assembly|formation|testing","rationale":"推荐理由"}。
重点保持与方案表已有非空值一致；其他工艺值可合理推荐，不要求每项都有直接文献。配方、加工、装配、化成、测试应尽量具体，不强制每类都有数值。允许 budget_assumption 类型的设计容量等预算假设，注明用途和实测替换方法；不能冒充实测或写入 PINN。本征扩散系数仍需证据。对照变化用 scope=control 单独声明，不能混入主方案。推荐数值用 engineering_proposal。比例说明分母，温度 K。
sections 每项为中文段落数组。所有参数值通过 {{字段路径}} 引入，不在段落中直接复制数值，便于后续几何修订同步。各引用必须在 parameters 声明。材料选择也使用引用，不能擅换当前材料。
给一个主推荐组、少量对照组、平行样数量和固定条件。选择采购或自制路线，不要混写多条路线。说明配浆、涂布、干燥、辊压、装配、注液、化成和测试的具体安排。建议须形成一致方案，不是散乱参数清单。
保持当前材料和电压窗口；对照改变要单独声明。推荐条件不同于未来仿真条件时不能沿用其性能。文献只引用输入中真实 passage_id，用 [passage_id]，并在 citations 列出；没有直接文献支持的操作允许按工程起点推荐，但不能冒充文献实测。gaps 只保留必要物性获取和后续优化，不阻止首轮操作。''' + TITLE_INSTRUCTIONS + STYLE_INSTRUCTIONS + CONSISTENCY_INSTRUCTIONS


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


def validate(record, doc, *, delivery=False):
    """生成时检查实验设计完整性，交付时核对归档、方案绑定及参数一致性。"""
    if not isinstance(record, dict) or record.get("schema_version") not in (2, 3, 4) or record.get("generated_stage") not in (4, 5):
        raise ValueError("缺少 Stage 5B 已生成的实验推荐，请基于已有设计及文献重新生成")
    if record.get("plan_hash") != plan_hash(record):
        raise ValueError("实验方案内容指纹不一致")
    from .experiment_consistency import validate_snapshot, parameter_issues, paragraph_issues
    data = effective_data(doc)
    current = data["parameters"]
    for key, value in record.get("fixed_fields", {}).items():
        if value is not None and (current.get(key) or {}).get("value") != value:
            raise ValueError(f"实验推荐的材料或固定条件已变更: {key}，需重新生成 Stage 5B 实验方案")
    validate_snapshot(record, doc)
    conflicts = parameter_issues(record, doc)
    if conflicts:
        raise ValueError("；".join(e["reason"] for e in conflicts))
    parameters = record.get("parameters")
    sections = record.get("sections")
    if not isinstance(parameters, dict) or not isinstance(sections, dict):
        raise ValueError("实验方案参数或章节结构不完整")
    from .experiment_values import validate_value, validate_pending, validate_scalar_fields, control_value_issues
    validate_pending(record)
    control_errors = control_value_issues(record, current)
    if control_errors:
        raise ValueError("实验对照变化未通过校验：" + "；".join(e["reason"] for e in control_errors))
    bindings = {}
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
            validate_value(key, row)
            if row.get("value_type") in ("range", "sequence"):
                validate_scalar_fields(key, row)
            from .research_experiments import validate_parameter
            validate_parameter(key, row, doc)
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

    if delivery:
        from .physical_parameters import physical_declarations, identity
        physical_rows = dict(current)
        for key, row in parameters.items():
            if "ref" in row or row.get("scope") == "control":
                continue
            physical = identity(overlap_key(key), row)
            if physical:
                physical_rows["experiment." + key] = dict(row, canonical_path=physical[0])
        _, issues = physical_declarations(physical_rows)
        if issues:
            raise ValueError("实验参数与方案表冲突：" + "；".join(issues))
    allowed_ids = {str(e.get("passage_id")) for e in (doc.get("evidence") or []) + (doc.get("experiment_evidence") or [])}
    cited = set(record.get("citations") or [])
    if not cited.issubset(allowed_ids):
        raise ValueError("实验推荐引用未归档文献")
    used = set()
    if delivery and not any(sections.get(k) for k in SECTIONS):
        raise ValueError("实验方案缺少正文")
    for section in SECTIONS:
        paragraphs = sections.get(section)
        if delivery and not paragraphs:
            continue
        if not isinstance(paragraphs, list) or not paragraphs or not all(isinstance(p, str) and p.strip() for p in paragraphs):
            raise ValueError(f"实验方案缺少可执行章节: {section}")
        for paragraph in paragraphs:
            if any(x in paragraph for x in ("```", "<!--", "已通过实验验证", "已达到目标")) or paragraph.lstrip().startswith("|"):
                raise ValueError("实验段落包含原始表格或未经验证的性能声明")
            refs = set(TOKEN.findall(paragraph))
            if not refs.issubset(bindings):
                raise ValueError("段落引用未声明的实验参数")
            used.update(refs)
            conflicts = paragraph_issues(record, section, paragraph, bindings)
            if conflicts:
                raise ValueError("；".join(conflicts))
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
    if not delivery and not used and len("".join(p for ps in sections.values() for p in ps)) < 100:
        raise ValueError("实验方案缺少可执行内容")
    if doc.get("requirements"):
        from .research_experiments import diagnose_links
        errors = diagnose_links(record, doc)
        if delivery:
            # 主组不可改写当前参数，对照只允许独立变更。覆盖率、重复数及解释完整度留在设计审核。
            for group in record.get("groups") or []:
                if not isinstance(group, dict) or not group.get("id") or group.get("scope") not in ("main", "control"):
                    raise ValueError("实验参数所属分组不明确")
                changes = group.get("changes") or {}
                if any(key not in current or not isinstance(row, dict) or 'value' not in row
                       or row.get('unit', '') != current[key].get('unit', '')
                       or group['scope'] == 'main' and row['value'] != current[key].get('value')
                       for key, row in changes.items()):
                    raise ValueError("实验分组的参数、值或单位与方案表冲突")
            errors = [item for item in errors if item["reason"] in ("实验分组引用未知主方案字段", "测试矩阵引用未声明参数")]
        if errors:
            raise ValueError("; ".join(e["reason"] for e in errors))
    return bindings


def load(task_dir):
    path = Path(task_dir) / FILENAME
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def delivery_status(record, pending_fields=(), *, doc=None):
    """模板交付、局部待定与完整实验推荐分别标记，不能由空pending清单冒充成功。"""
    reasons = []
    if record.get("origin") == "engineering_fallback":
        return "FALLBACK", ["实验生成失败，当前为工程回退模板"] + list(record.get("warnings") or [])
    if record.get("repair_status") == "PARTIAL" or record.get("pending_parameters") or record.get("pending_sections"):
        reasons.append("实验局部参数或步骤仍待确认，已保留其余有效内容")
    if record.get("pending_groups"):
        reasons.append("部分实验对照分组尚未完成登记")
    if any(row.get("status") == "gap" for row in record.get("requirement_tests", []) if isinstance(row, dict)):
        reasons.append("部分课题目标保留待补充说明，具体方法与限制见目标记录")
    if pending_fields:
        reasons.append("实验参数与当前方案仍有待核对项")
    if record.get("generation_pending"):
        reasons.append("部分实验内容尚未通过分项验收，原响应及原因已保存")
    from .experiment_values import validate_value, validate_pending
    try:
        validate_pending(record)
        for key, row in record.get("parameters", {}).items():
            validate_value(key, row)
    except (ValueError, TypeError):
        reasons.append("实验参数值、缺项记录或引用未通过校验")
    from .experiment_values import control_value_issues
    if control_value_issues(record, effective_data(doc)["parameters"] if doc is not None else None):
        reasons.append("实验对照变化未通过值校验，相关组别尚未就绪")
    from .experiment_review import status as review_status
    _, review_reasons = review_status(record, doc)
    reasons.extend(review_reasons)
    return ("CONDITIONAL" if reasons else "PASSED"), reasons


def input_fingerprint(doc, scheme_md="", *, version=9):
    """生成与同义迁移共用输入身份，迁移不因此重新调用实验模型。"""
    from .experiment_consistency import preflight
    value = {"scheme": doc.get("scheme"), "ledger": doc.get("ledger_hash"),
                   "evidence": doc.get("evidence"), "physics": doc.get("physics_context"), "analysis": scheme_md,
                   "requirements": doc.get("requirements"), "research": doc.get("research"),
                   "consistency": preflight(doc), "version": version}
    if version >= 8:
        value.update(target=doc.get("target"), parameters=effective_data(doc)["parameters"],
                     stage4_review={k: doc.get(k) for k in ("review_completed", "review_passed", "reviewer_output")})
    return digest(value)


def generate(task_dir, doc, client, scheme_md="", *, restart=False):
    """恢复完整JSON初稿加一次局部补丁；已有产物兼容读取，显式重建才重新生成。"""
    from .experiment_consistency import preflight, stamp
    from .experiment_titles import normalize_step_titles
    from .experiment_repair import repair_record
    from .research_experiments import INSTRUCTIONS, required_tests
    from .scheme_ledger import task_lock
    task_dir = Path(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = input_fingerprint(doc, scheme_md)
    old = load(task_dir)
    if old and old.get("generated_stage") == 4 and not restart:
        try:
            bindings = validate(old, doc)
            old.update(origin_stage=4, generated_stage=5, schema_version=3,
                       input_fingerprint=fingerprint, bindings=bindings)
            old["plan_hash"] = plan_hash(old)
            atomic_write(Path(task_dir) / FILENAME, json.dumps(old, ensure_ascii=False, indent=2))
            return old
        except (ValueError, TypeError):
            pass
    compatible = {fingerprint}
    if old:
        compatible.add(input_fingerprint(doc, scheme_md, version=8))
        if not old.get("generation_contract"):
            compatible.add(input_fingerprint(doc, scheme_md, version=7))
    if old and old.get("input_fingerprint") in compatible and not restart:
        validate(old, doc, delivery=True)
        return old
    def source_identity():
        return {name: digest((task_dir / name).read_bytes().hex()) if (task_dir / name).exists() else None
                for name in ("scheme_ledger.json", "design_scheme.json")}
    sources = source_identity()
    if restart:
        for name in (FILENAME, "experiment_generation_checkpoint.json"):
            path = task_dir / name
            if path.exists():
                archive = task_dir / "experiment_generation_history" / ("before_whole_json_" + digest(path.read_text(encoding="utf-8"))[:20] + "_" + name)
                if not archive.exists():
                    atomic_write(archive, path.read_text(encoding="utf-8"))
    error = "实验设计模型不可用"
    attrs = {}
    try:
        if client is None or not getattr(client, "available", False):
            raise ValueError(error)
        attrs = {key: (hasattr(client, key), getattr(client, key, None))
                 for key in ("max_tokens", "output_max_tokens", "single_attempt")}
        client.max_tokens = min(getattr(client, "max_tokens", 6000), 6000)
        client.output_max_tokens = 6000
        client.single_attempt = True
        raw = client.chat(SYSTEM_PROMPT + (INSTRUCTIONS if doc.get("requirements") else ""),
            json.dumps({"goal": doc.get("target"), "scheme": doc.get("scheme"), "requirements": doc.get("requirements"),
                        "required_tests": required_tests(doc), "research": doc.get("research"), "consistency": preflight(doc),
                        "ledger": doc.get("ledger_view"), "analysis": scheme_md, "review": doc.get("reviewer_output"),
                        "evidence": doc.get("evidence"), "physics_context": doc.get("physics_context"), "last_error": error}, ensure_ascii=False),
            temperature=0.2, trace_tag="experiment_design")
        atomic_write(task_dir / "experiment_draft.json", json.dumps({"audit": {"input_fingerprint": fingerprint,
            "raw_response": raw, "generation_method": "whole_json"}}, ensure_ascii=False, indent=2))
        record = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
        allowed = {"parameters", "sections", "citations", "groups", "requirement_tests", "extensions", "step_titles"}
        if not isinstance(record, dict) or set(record) - allowed:
            raise ValueError("完整实验方案只能声明参数、正文、分组、目标安排、引用及可选步骤标题")
        record.update(schema_version=4 if doc.get("requirements") else 3, generated_stage=5,
                      input_fingerprint=fingerprint, generated_at=datetime.now().isoformat(),
                      base_ledger_hash=doc.get("ledger_hash"), sync_history=[],
                      fixed_fields={k: v.get("value") for k, v in effective_data(doc)["parameters"].items()
                                    if k not in {f"{c}.{f}" for c in ("cathode", "anode") for f in ("L_um", "porosity", "R_s_um", "D_s_m2s")}})
        record["warnings"] = normalize_step_titles(record, bind_unbound=True)
        stamp(record, doc)
        record["plan_hash"] = plan_hash(record)
        record = repair_record(record, doc, client, task_dir, fingerprint)
        draft_path = task_dir / "experiment_draft.json"
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["audit"].update(raw_response=raw, generation_method="whole_json")
        atomic_write(draft_path, json.dumps(draft, ensure_ascii=False, indent=2))
        record["plan_hash"] = plan_hash(record)
        record["bindings"] = validate(record, doc, delivery=True)
        with task_lock(task_dir):
            if source_identity() != sources:
                raise RuntimeError("发布前方案表或设计已更新，拒绝写入过期实验方案")
            atomic_write(task_dir / FILENAME, json.dumps(record, ensure_ascii=False, indent=2))
            # 原程序化检查点只作历史档案，不能再进入默认实验生成。
            if restart:
                (task_dir / "experiment_generation_checkpoint.json").unlink(missing_ok=True)
        outcome, reasons = delivery_status(record, doc=doc)
        atomic_write(Path(task_dir) / "experiment_plan_failure.json", json.dumps({"resolved": outcome == "PASSED",
            "repair_status": record.get("repair_status"), "warnings": reasons}, ensure_ascii=False, indent=2))
        return record
    except Exception as exc:
        error = str(exc)
        if source_identity() != sources:
            raise ValueError("生成期间方案表或设计输入已变化，拒绝发布旧稿或回退模板") from exc
    finally:
        for key, (had, value) in attrs.items():
            if had:
                setattr(client, key, value)
            else:
                delattr(client, key)
    atomic_write(Path(task_dir) / "experiment_plan_failure.json", json.dumps({"resolved": False, "error": error,
        "input_fingerprint": fingerprint}, ensure_ascii=False, indent=2))
    if old and old.get("origin") != "engineering_fallback":
        raise ValueError("Stage 5B 新方案生成失败，原实验产物已保留：" + error)
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
    if doc.get("requirements", {}).get("scheme_required") is False:
        parameters = {}
        sections = {
            "groups": ["本课题以研究分析为主，不新增电芯配方；比较对象及条件沿用用户原始要求。"],
            "fabrication": ["不安排新的材料制备。先收集对应研究对象的文献、已有样品记录与实验条件，核对证据适用范围。"],
            "assembly": ["本次不要求组装新电芯。若后续需判别实验，应先依据研究假设明确样品、仪器与对照条件。"],
            "testing": ["围绕课题中的每个待验证判断，比较已有证据与替代解释；实验方法和设备条件未明确的项按目标关联表补充。"],
            "criteria": ["仅在证据对应相同对象和可比条件时形成结论；没有直接支持的解释保留为假设，不用通用性能指标替代课题目标。"],
            "gaps": ["分析或实验设计服务未成功，需补充逐项研究方法与证据；不因此编造配方或未经执行的结果。"]}
    record = {"schema_version": 3, "generated_stage": 5, "input_fingerprint": fingerprint,
        "base_ledger_hash": doc.get("ledger_hash"), "parameters": parameters, "sections": sections,
        "citations": [], "fixed_fields": {k: v["value"] for k, v in current.items() if v.get("value") is not None},
        "origin": "engineering_fallback", "warnings": [error], "sync_history": []}
    if doc.get("requirements"):
        from .research_experiments import fallback_links
        fallback_links(record, doc, error)
        record["schema_version"] = 4
    from .experiment_consistency import stamp
    stamp(record, doc)
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
    from .experiment_protocol import render_methods, render_group_notes, consistency_notice
    bindings = validate(record, doc, delivery=True)
    sections = ["以下为首轮实验推荐，补充的操作参数属于工程建议，尚不代表实测或性能已验证。"]
    status, reasons = delivery_status(record, doc=doc)
    if status != "PASSED":
        sections.append("**实验交付状态：** " + ("工程回退模板" if status == "FALLBACK" else "附条件实验方案") + "。" + "；".join(reasons) + "。")
    from .experiment_review import render_notice
    review_notice = render_notice(record, doc)
    if review_notice:
        sections.append(review_notice)
    notice = consistency_notice(doc, record.get("consistency"))
    if notice:
        sections.append(notice)
    assumptions = [(k, row) for k, row in record.get("parameters", {}).items() if row.get("basis") == "budget_assumption"]
    if assumptions:
        sections.append("预算假设：" + "；".join(f"{row.get('meaning', '设计估算')}取{amount(k, row)}" for k, row in assumptions)
            + "。这些假设仅用于实验预算与容量匹配，不作为材料实测值或 PINN 物性输入，须按首轮实测修正。")
    for key, heading in SECTIONS.items():
        sections.append("### " + heading)
        if doc.get("requirements") and key == "groups":
            from .research_experiments import render_groups
            sections.append("**表5-1 实验样品与分组设计**")
            sections.append(render_groups(record, doc))
            notes = render_group_notes(record, bindings)
            if notes:
                sections.append(notes)
        elif record.get("generation_contract") == 1 and key == "groups":
            from .report_narrative import describe
            for group in record.get("groups", []):
                detail = "；".join(describe(path, row) + "；改变目的：" + row["reason"]
                                   for path, row in group.get("changes", {}).items())
                sections.append(group["id"] + "（" + group["name"] + "）："
                                + (detail or "完整采用第3节当前主方案，材料与固定条件不另行变更") + "。")
            notes = render_group_notes(record, bindings)
            if notes:
                sections.append(notes)
        else:
            sections.append(render_methods(record, key, heading.split()[0], bindings, doc))
        if doc.get("requirements") and key == "testing" and record.get("requirement_tests"):
            from .research_experiments import render_tests
            sections.append("**各目标的方法与待解决事项**")
            sections.append(render_tests(record, doc, bindings))
        if key == "groups":
            sections.append("> **方案绑定。** 本轮材料沿用第 3 节的当前方案。第 3 节尚未声明的操作条件如在本节给出，均为首轮实验建议；对照组的变更仅适用于对应分组，不改变主方案或仿真输入。")
        if key == "testing":
            if (result or {}).get("status") == "CONVERGED":
                sections.append("现有模型仅验证对应输入下的单次放电。推荐实验新增的配方、工艺和测试条件未因此得到验证，不能沿用模型结果作为实验性能。")
            else:
                sections.append("本次未完成有效放电仿真，不存在可用于性能判断的仿真值。")
    if record.get("citations"):
        sections.append("本节参考的归档文献证据为" + "、".join(f"[{pid}]" for pid in record["citations"]) + "。具体工程推荐仍需通过首轮实验验证。")
    from .experiment_values import render_pending
    pending_notice = render_pending(record)
    if pending_notice:
        sections.append(pending_notice)
    return "\n\n".join(sections)


def require_current(task_dir, doc):
    record = load(task_dir)
    bindings = validate(record, doc, delivery=True)
    if record["plan_hash"] != doc.get("experiment_plan_hash") or bindings != record.get("bindings"):
        raise ValueError("实验推荐与当前设计或参数绑定不一致，请先同步 Stage 5B 实验方案")
