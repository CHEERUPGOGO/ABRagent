"""既有Stage 5按当前主方案同步，局部修订、独立核对与副本验收后发布。"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

from .scheme_ledger import atomic_write, digest, require_current, task_lock


def write(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2))


def repair_experiment_alignment(mgr, client=None, editorial_changes=None):
    from . import experiment_design as recipes
    from .experiment_alignment import synchronize, issues
    from .experiment_alignment_review import review, current
    from .experiment_consistency import stamp
    from .experiment_delivery import blocking_pending_fields, assessment
    from .experiment_repair import repair_record
    from .issue_resolution import resolve_numeric_issues
    from .research_recovery import _research_client
    from .material_migration import _preview_manager
    from auto_battery_research.tools.workflow_actions import run_synthesis_report
    from auto_battery_research.checkers.rag_design_checker import RAGDesignChecker
    from auto_battery_research.checkers.stage5_checker import Stage5Checker
    from auto_battery_research.checkers.final_report_checker import FinalReportChecker
    task = Path(mgr.get_task_output_dir())
    initial = {p.name: p.read_bytes() for p in task.iterdir() if p.is_file() and not p.name.endswith('.lock')}
    original_doc = json.loads(initial['design_scheme.json'])
    original_record = json.loads(initial['experiment_plan.json'])
    require_current(task, original_doc)
    identity = digest({'doc': original_doc, 'record': original_record})
    root = task / 'experiment_alignment_recovery_history'
    history = next((p.parent for p in sorted(root.glob('*/audit.json'), reverse=True)
                    if json.loads(p.read_text(encoding='utf-8')).get('input_hash') == identity
                    and json.loads(p.read_text(encoding='utf-8')).get('status') != 'PUBLISHED'), None)
    if history is None:
        history = root / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        for folder in ('originals', 'preview', 'calls'):
            (history / folder).mkdir(parents=True)
        for name, content in initial.items():
            (history / 'originals' / name).write_bytes(content)
            (history / 'preview' / name).write_bytes(content)
        write(history/'audit.json', {'input_hash':identity, 'status':'PREPARING'})
    preview = history / 'preview'
    doc, record = copy.deepcopy(original_doc), copy.deepcopy(original_record)
    client = client or _research_client(mgr, 'reviewer')
    if not client or not getattr(client, 'available', False):
        return {'success':False, 'error':'同步需要局部文字核对模型，原文件已保留', 'history':str(history)}
    invoke = client.chat
    def cached(system, user, **kwargs):
        path = history/'calls'/(digest({'system':system,'user':user,'kwargs':kwargs})+'.json')
        if path.exists():
            saved=json.loads(path.read_text(encoding='utf-8'))
            if 'response' not in saved:
                raise ValueError('同步调用已中断，不自动重发')
            return saved['response']
        saved={'system':system,'user':user,'kwargs':kwargs,'attempted':True}
        write(path,saved)
        saved['response']=invoke(system,user,**kwargs)
        write(path,saved)
        return saved['response']
    prior_single = getattr(client, 'single_attempt', None)
    client.chat=cached
    client.single_attempt=True
    try:
        closed = resolve_numeric_issues(doc)
        synchronize(record, doc)
        record['schema_version']=5
        # 主方案明确保留未知的本征物性不能在实验中以占位数值冒充既定值。
        from .experiment_values import separate_pending
        for path in ('cathode.D_s_m2s','anode.D_s_m2s'):
            declaration=doc['ledger_view']['parameters'].get(path)
            row=record.get('parameters',{}).get(path)
            if declaration is not None and declaration.get('value') is None and isinstance(row,dict) and 'ref' not in row:
                record.setdefault('alignment_parameter_history',[]).append({'field':path,'record':copy.deepcopy(row),'reason':'主方案本征物性未确定，移除实验占位值'})
                record['parameters'][path]=dict(row,value=None)
        record, _ = separate_pending(record)
        allowed_pending = set(record.get('pending_sections', {}))
        stamp(record,doc)
        record['plan_hash']=recipes.plan_hash(record)
        if editorial_changes:
            # 显式维护可提交已经逐条检查的文字修订，仍须程序校验及独立审核；主方案、参数与分组不授予修改权。
            from .experiment_alignment import text_entries
            from .experiment_repair import apply_local
            allowed={tuple(p) for p,_ in text_entries(record)}
            if any(tuple(c.get('path',[])) not in allowed or not isinstance(c.get('value'),str) for c in editorial_changes):
                raise ValueError('显式文字修订只能替换已有具体文本字段')
            patch={'base_hash':digest(record),'changes':editorial_changes}
            write(history/'editorial_patch.json',patch)
            record=apply_local(record,patch,[{'path':c['path'],'reason':'显式维护文字修订'} for c in editorial_changes])
            stamp(record,doc)
            record['plan_hash']=recipes.plan_hash(record)
        # 同一输入及调用缓存保证重启不重新生成全文或重发修复。
        if issues(record,doc):
            record=repair_record(record,doc,client,preview,identity)
        if set(record.get('pending_sections',{})) - allowed_pending or len(record.get('pending_tests',[])) > len(original_record.get('pending_tests',[])):
            raise ValueError('本次一致性同步未修复全部受影响正文，保留原报告，不以撤下步骤代替修复')
        if issues(record,doc):
            raise ValueError('局部修订后仍与主方案冲突：'+str(issues(record,doc)))
        record=review(record,doc,client,preview)
        if not current(record,doc):
            return {'success':False,'error':'主方案一致性审核尚未通过，原文件已保留','history':str(history),'review':record.get('alignment_review')}
        # 仍保留真正缺项；本轮已修复的机械局部状态不继续冒充当前错误。
        if not record.get('pending_parameters') and not record.get('pending_sections') and not record.get('pending_groups'):
            record['repair_status']='REPAIRED'
        record['base_ledger_hash']=doc['ledger_hash']
        record['plan_hash']=recipes.plan_hash(record)
        record['bindings']=recipes.validate(record,doc,delivery=True)
        recipe_doc=copy.deepcopy(doc)
        recipe_doc['evidence']=doc.get('experiment_evidence') or doc.get('evidence',[])
        recipe_doc['physics_context']=doc.get('experiment_physics_context') or {}
        record['input_fingerprint']=recipes.input_fingerprint(recipe_doc,doc.get('final_answer','')[:12000])
        record['plan_hash']=recipes.plan_hash(record)
        doc['experiment_plan_hash']=record['plan_hash']
        pending=blocking_pending_fields(record,doc)
        status,reasons=recipes.delivery_status(record,pending,doc=doc)
        readiness=doc.get('report_readiness') or {}
        readiness['issues']=[i for i in readiness.get('issues',[]) if not str(i).startswith('实验交付限制：')]
        if reasons:readiness['issues'].append('实验交付限制：'+'；'.join(reasons))
        readiness['experiment_status']=status
        if status!='PASSED' and readiness.get('category')=='COMPLETE':readiness['category']='CONDITIONAL'
        execution=json.loads(initial['stage5_execution.json'])
        execution['experiment'].update(status=status,reasons=reasons,plan_hash=record['plan_hash'],pending_fields=pending,
                                      validation_notes=assessment(record,doc)['validation_notes'])
        for key in ('scheme','ledger_view','ledger_hash','scheme_parameters','final_answer','evidence','research','requirements','research_narrative','reviewer_output'):
            if original_doc.get(key)!=doc.get(key):raise ValueError('实验同步不得改写主方案或研究论证：'+key)
        write(preview/'experiment_plan.json',record)
        write(preview/'design_scheme.json',doc)
        write(preview/'stage5_execution.json',execution)
        for name in ('rag_result.json','stage4_repair_checkpoint.json'):
            if name in initial:
                value=json.loads(initial[name])
                if isinstance(value.get('research_draft'),dict):value['research_draft']=copy.deepcopy(doc)
                write(preview/name,value)
        pmgr=_preview_manager(mgr,preview)
        generated=run_synthesis_report(doc['target'],stage_manager=pmgr)
        if not generated.get('success'):raise ValueError('报告副本生成失败：'+str(generated))
        for stage_id, cls in ((4,RAGDesignChecker),(5,Stage5Checker),(6,FinalReportChecker)):
            checker=cls();checker.on_init(pmgr,{'id':stage_id},mgr.config)
            passed,diagnostic=checker.do_check()
            if not passed:raise ValueError('副本验收失败：'+str(diagnostic))
        names=('design_scheme.json','experiment_plan.json','experiment_report.json','stage5_execution.json',
               'rag_result.json','stage4_repair_checkpoint.json','report_record.json','final_research_report.md','final_research_report_supplement.md')
        with task_lock(task):
            if any((task/n).read_bytes()!=b for n,b in initial.items()):raise ValueError('同步期间原任务被修改，拒绝覆盖')
            try:
                for name in names:
                    if (preview/name).exists():
                        text=(preview/name).read_text(encoding='utf-8')
                        if name in ("final_research_report.md", "final_research_report_supplement.md"):text=text.replace(str(preview),str(task))
                        atomic_write(task/name,text)
                checker=FinalReportChecker();checker.on_init(mgr,{'id':6},mgr.config)
                if not checker.do_check()[0]:raise ValueError('正式报告验收失败')
            except Exception:
                for name in names:
                    if name in initial:(task/name).write_bytes(initial[name])
                    else:(task/name).unlink(missing_ok=True)
                raise
        summary={'success':True,'status':'PUBLISHED','history':str(history),'input_hash':identity,'closed_issues':closed,
                 'alignment_status':'consistent','experiment_status':status,'stage4_5_6_checks_pass':True,
                 'model_calls':len(list((history/'calls').glob('*.json'))),'report':str(task/'final_research_report.md')}
        write(history/'audit.json',summary)
        return summary
    finally:
        client.chat=invoke
        if prior_single is None:delattr(client,'single_attempt')
        else:client.single_attempt=prior_single
