"""最终报告统一记录：当前投影、完整来源版本及并发发布保护。"""
import copy
import json

import pytest

from auto_battery_research.tests.test_experiment_design import setup_design
from auto_battery_research.util import report_record as records
from auto_battery_research.util.scheme_ledger import atomic_write


def setup(tmp_path):
    doc, recipe = setup_design(tmp_path)
    (tmp_path/'design_scheme.json').write_text(json.dumps(doc),encoding='utf-8')
    (tmp_path/'design_scheme.md').write_text(doc['final_answer'],encoding='utf-8')
    (tmp_path/'stage5_execution.json').write_text(json.dumps({'physics':{'status':'NOT_APPLICABLE'},'experiment':{'plan_hash':recipe['plan_hash']}}),encoding='utf-8')
    return doc,recipe


def test_capture_uses_one_current_set_and_does_not_mutate_sources(tmp_path):
    doc,recipe=setup(tmp_path)
    before={f.name:f.read_bytes() for f in tmp_path.iterdir() if f.is_file()}
    record=records.capture(tmp_path,doc)
    assert record['design']==doc and record['experiment']==recipe
    assert record['view']['physics_status']=='NOT_APPLICABLE'
    records.validate_saved(tmp_path,record)
    assert before=={f.name:f.read_bytes() for f in tmp_path.iterdir() if f.is_file()}


@pytest.mark.parametrize('name',['design_scheme.json','scheme_ledger.json','experiment_plan.json','stage5_execution.json','design_scheme.md'])
def test_changed_source_invalidates_entire_report_record(tmp_path,name):
    doc,_=setup(tmp_path);record=records.capture(tmp_path,doc)
    with (tmp_path/name).open('a',encoding='utf-8') as f:f.write('\n')
    with pytest.raises(ValueError,match='过期'):
        records.validate_saved(tmp_path,record)


def test_snapshot_rejects_mixed_execution_and_experiment_version(tmp_path):
    doc,_=setup(tmp_path)
    (tmp_path/'stage5_execution.json').write_text(json.dumps({'experiment':{'plan_hash':'old'}}),encoding='utf-8')
    with pytest.raises(ValueError,match='同一版本'):records.capture(tmp_path,doc)


def test_snapshot_rejects_stale_physics_status_even_without_simulation(tmp_path):
    doc,recipe=setup(tmp_path)
    (tmp_path/'stage5_execution.json').write_text(json.dumps({'physics':{'status':'NOT_APPLICABLE','input_hash':'old'},
        'experiment':{'plan_hash':recipe['plan_hash']}}),encoding='utf-8')
    with pytest.raises(ValueError,match='物理执行状态'):records.capture(tmp_path,doc)


def test_completion_journal_does_not_invalidate_its_own_report(tmp_path):
    doc,_=setup(tmp_path)
    journals=[{'stage_id':5,'notes':'当前实验已登记'}]
    path=tmp_path/'stage_journals.json';path.write_text(json.dumps(journals),encoding='utf-8')
    record=records.capture(tmp_path,doc)
    journals.append({'stage_id':6,'notes':'报告发布完成'})
    path.write_text(json.dumps(journals),encoding='utf-8')
    records.validate_saved(tmp_path,record)
    journals[0]['notes']='实验条件变更';path.write_text(json.dumps(journals),encoding='utf-8')
    with pytest.raises(ValueError,match='过期'):records.validate_saved(tmp_path,record)


def test_publish_failure_preserves_old_report_and_snapshot(tmp_path,monkeypatch):
    doc,_=setup(tmp_path);record=records.capture(tmp_path,doc)
    (tmp_path/'final_research_report.md').write_text('旧报告',encoding='utf-8')
    (tmp_path/records.FILENAME).write_text('旧快照',encoding='utf-8')
    from auto_battery_research.util import report_consistency
    monkeypatch.setattr(report_consistency,'check',lambda *a:[])
    def write(path,text):
        if path.name=='final_research_report.md':raise OSError('模拟写入失败')
        atomic_write(path,text)
    monkeypatch.setattr(records,'atomic_write',write)
    with pytest.raises(OSError):records.publish(tmp_path,record,'新报告')
    assert (tmp_path/'final_research_report.md').read_text(encoding='utf-8')=='旧报告'
    assert (tmp_path/records.FILENAME).read_text(encoding='utf-8')=='旧快照'


def test_current_appendix_uses_current_methods_not_old_answer(tmp_path):
    from auto_battery_research.util.report_current_view import appendix
    from auto_battery_research.util.research_experiments import fallback_links
    from auto_battery_research.util import experiment_design as recipes
    doc,recipe=setup(tmp_path)
    doc['requirements']={'items':[{'id':'r1','kind':'objective','quote':'比较循环','description':'比较循环','verification_needed':True}],'status':'parsed','scheme_required':True}
    doc['research']={'answers':[{'requirement_id':'r1','response':'已经安排旧的界面对照组。'}]}
    recipe['parameters']['cathode.active_fraction']['canonical_path']='cathode.active_fraction'
    fallback_links(recipe,doc);recipe['plan_hash']=recipes.plan_hash(recipe);recipe['bindings']=recipes.validate(recipe,doc,delivery=True)
    doc['experiment_plan_hash']=recipe['plan_hash']
    for name,value in (('design_scheme.json',doc),('experiment_plan.json',recipe),('stage5_execution.json',{})):
        (tmp_path/name).write_text(json.dumps(value),encoding='utf-8')
    record=records.capture(tmp_path,doc)
    body=appendix(records.attach(record))
    assert '已经安排旧的界面对照组' not in body
    assert '旧的界面对照组' in json.dumps(record,ensure_ascii=False)
    assert '当前实验分组索引' in body and '历史资料' in body


def test_summary_ratio_reads_declared_field_not_only_formula_parser():
    from auto_battery_research.util.report_readiness import notice
    doc={'report_readiness':{'category':'CONDITIONAL'},'scheme':{'electrolyte':'1 M LiPF6/EC+DMC'},
        'ledger_view':{'parameters':{'electrolyte.ec_dmc_ratio':{'value':'EC:DMC=1:1','unit':'','basis':'engineering_proposal'}}},
        'rule_checks':{'material_matches':{'electrolyte':{'basis':'composition','matched':True,'declared_value':'1 M LiPF6/EC+DMC',
            'composition':{'complete':True,'raw':'1 M LiPF6/EC+DMC','solvent_ratio':None}}}}}
    text=notice(doc)
    assert '1:1' in text and '工程设定' in text and '溶剂比例未声明' not in text


def test_rule_advice_is_visible_without_becoming_a_current_violation(tmp_path):
    from auto_battery_research.util.report_current_view import appendix
    from auto_battery_research.util.report_contract import report_identity
    doc, _ = setup(tmp_path)
    advice = [{'rule_id': 'C1', 'reason': '建议核验高压界面。'},
              {'rule_id': 'C3', 'reason': '建议观察锂金属界面。'}]
    doc['rule_checks'] = {'rule_checks': {'violations': [], 'rejects': []}, 'recommendations': advice}
    before = report_identity(doc, None, None)
    (tmp_path/'design_scheme.json').write_text(json.dumps(doc), encoding='utf-8')
    record = records.capture(tmp_path, doc)
    text = appendix(records.attach(record))
    assert '当前规则建议（非阻断）' in text
    assert all(row['reason'] in text for row in advice)
    assert not any('当前相容性规则C' in issue for issue in record['view']['active_issues'])
    doc['rule_checks']['recommendations'][0]['reason'] = '建议补充界面观察。'
    assert report_identity(doc, None, None) != before


def test_unreported_condition_cannot_be_converted_to_negative_claim():
    from auto_battery_research.util.report_consistency import wording_errors
    assert wording_errors('原文未报告循环倍率，属非1 C数据。')
    assert not wording_errors('原文未报告循环倍率，无法确认是否为1 C，不能直接对标。')


def test_user_comparison_and_significance_survive_experiment_binding(tmp_path):
    from auto_battery_research.util.experiment_alignment import issues
    doc,recipe=setup(tmp_path)
    doc['requirements']={'items':[{'id':'r','kind':'objective','description':'保持率显著高于74%','quantity':{'value':74,'unit':'%','operator':'>'}}]}
    recipe['parameters']['testing.target']={'value':74,'unit':'%','basis':'engineering_proposal','requirement_id':'r'}
    recipe['sections']['criteria']=['用户要求为{{testing.target}}。']
    assert any('限定' in e['reason'] or '高于' in e['reason'] for e in issues(recipe,doc))
    recipe['sections']['criteria']=['用户要求为显著高于{{testing.target}}。']
    assert not issues(recipe,doc)
