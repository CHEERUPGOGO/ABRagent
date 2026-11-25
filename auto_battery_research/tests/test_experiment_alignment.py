"""主方案继承、整套工况、旧诊断结案及局部审核回归。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_experiment_design import setup_design
from auto_battery_research.util import experiment_design as recipes
from auto_battery_research.util.experiment_alignment import attach, issues, equivalent, synchronize, expand_profiles
from auto_battery_research.util.experiment_consistency import stamp
from auto_battery_research.util.experiment_alignment_review import review, current


def profiles(tmp_path):
    doc, record = setup_design(tmp_path)
    params = doc['ledger_view']['parameters']
    for key, value, unit in [('condition.voltage_window','2.0–4.6 V',''),('condition.cycles',200,'cycle'),
                             ('condition.high_voltage_window','2.0–4.8 V',''),('condition.high_voltage_cycles',100,'cycle')]:
        params[key] = {'value':value,'unit':unit,'basis':'engineering_proposal'}
    attach(record,doc)
    stamp(record,doc)
    record['plan_hash']=recipes.plan_hash(record)
    return doc,record


def test_inherited_values_are_rendered_even_if_model_omits_them(tmp_path):
    doc,record=setup_design(tmp_path)
    assert record['parameters']['anode.material']=={'ref':'anode.material'}
    assert 'si_base' in recipes.render(record,doc) or '硅' in recipes.render(record,doc)
    broken=copy.deepcopy(record)
    broken['parameters'].pop('anode.material')
    broken['plan_hash']=recipes.plan_hash(broken)
    with pytest.raises(ValueError,match='已有主方案条件'):
        recipes.validate(broken,doc,delivery=True)


def test_conditions_cannot_mix_high_voltage_with_regular_cycles(tmp_path):
    doc,record=profiles(tmp_path)
    record['requirement_tests']=[{'requirement_id':'x','conditions':'{{condition.high_voltage_window}}；{{condition.cycles}}'}]
    errors=issues(record,doc)
    assert any(e['path']==['requirement_tests',0,'conditions'] and '混用' in e['reason'] for e in errors)
    record['requirement_tests'][0]['conditions']='{{profile:main_cycle}}；{{profile:high_voltage}}'
    assert not issues(record,doc)
    assert 'condition.high_voltage_cycles' in expand_profiles(record['requirement_tests'][0]['conditions'],record)


def test_multiple_profiles_in_separate_method_clauses_are_valid(tmp_path):
    doc,record=profiles(tmp_path)
    record['sections']['testing']=['常规循环{{condition.voltage_window}}、{{condition.cycles}}；高压{{condition.high_voltage_window}}、{{condition.high_voltage_cycles}}。']
    assert not issues(record,doc)
    record['sections']['testing']=['常规窗口{{condition.voltage_window}}与高压窗口{{condition.high_voltage_window}}分开报告，未重复其它已定条件。']
    assert not issues(record,doc)
    record['sections']['testing']=['{{profile:unknown}}']
    assert any('未声明' in e['reason'] for e in issues(record,doc))


def test_declared_design_can_be_unmeasured_but_not_undeclared(tmp_path):
    doc,record=setup_design(tmp_path)
    record['sections']['fabrication']=['正极厚度{{cathode.L_um}}为工程设定，仍需实测核验。']
    assert not issues(record,doc)
    record['sections']['fabrication']=['正极厚度{{cathode.L_um}}尚未确定，先核对后实施。']
    assert any('未定案' in e['reason'] for e in issues(record,doc))


@pytest.mark.parametrize('path,a,b',[
    ('cathode.L_um',{'value':.06,'unit':'mm'},{'value':60,'unit':'um'}),
    ('process.temperature',{'value':25,'unit':'℃'},{'value':298.15,'unit':'K'}),
    ('process.time',{'value':60,'unit':'min'},{'value':1,'unit':'h'}),
])
def test_explicit_equivalent_units_pass(path,a,b):
    assert equivalent(path,a,b)
    a['value']*=2
    assert not equivalent(path,a,b)


def test_equivalent_physical_alias_passes_full_delivery_without_mutating_record(tmp_path):
    doc,record=setup_design(tmp_path)
    record['parameters']['fabrication.cathode_thickness_um']={'value':.06,'unit':'mm','basis':'engineering_proposal','source':'工程建议'}
    record['plan_hash']=recipes.plan_hash(record)
    before=copy.deepcopy(record)
    bindings=recipes.validate(record,doc,delivery=True)
    assert bindings['fabrication.cathode_thickness_um']['value']==60
    assert record==before


def test_alternative_route_not_promoted_or_required_in_main(tmp_path):
    doc,record=setup_design(tmp_path)
    p=doc['ledger_view']['parameters']
    p['process.route_A']={'value':'固相法','meaning':'主推路线','unit':''}
    p['process.route_B']={'value':'溶胶凝胶','meaning':'备选路线，热程另定','unit':''}
    attach(record,doc)
    assert 'process.route_A' in record['execution_contract']['sections']['fabrication']
    assert record['execution_contract']['alternatives']==['process.route_B']
    assert 'process.route_B' not in record['parameters']


def test_loading_issue_is_not_automatically_closed_by_old_calculation_chain():
    from auto_battery_research.util.issue_resolution import resolve_numeric_issues,resolved
    from auto_battery_research.util.report_readiness import unresolved_issues
    p={k:{'value':v,'unit':u} for k,v,u in [('cathode.L_um',25,'um'),('cathode.compaction_g_cm3',2.25,'g/cm3'),
       ('cathode.active_fraction',.8,''),('cathode.active_loading_mg_cm2',4.5,'mg/cm2'),('cathode.loading_mg_cm2',5.625,'mg/cm2')]}
    old='极片载量口径冲突：正文派生值显示“未完成计算”，cathode.compaction_g_cm3须核对。'
    science='cathode.compaction_g_cm3实测适用性存在冲突，需要独立验证。'
    doc={'ledger_view':{'parameters':p},'reviewer_output':{'issues':[old,science]}}
    # 另有高压用途的参数时，不让该用途下不适用的计算覆盖主工况结果。
    p['condition.high_voltage_v_max']={'value':4.8,'unit':'V','canonical_path':'condition.v_max','conditions':{'purpose':'高压应力组'}}
    assert resolve_numeric_issues(doc)==[]
    assert unresolved_issues(doc)==[old,science]
    assert doc['reviewer_output']['issues']==[old,science]
    doc['issue_resolutions']=[{'issue':old,'status':'RESOLVED','kind':'loading_calculation'}]
    assert resolved(doc)==set() and old in unresolved_issues(doc)
    p['cathode.loading_mg_cm2']['value']=6
    assert resolved(doc)==set() and old in unresolved_issues(doc)


def test_same_id_unresolved_control_is_not_removed(tmp_path):
    doc,record=setup_design(tmp_path)
    bad={'record':{'id':'G1','scope':'control','changes':{'cathode.material':{'value':'other'}}},'reason':'待确认'}
    record['pending_groups']=[copy.deepcopy(bad)]
    synchronize(record,doc)
    assert record['pending_groups']==[bad]


def test_review_local_patch_is_rechecked_and_cached(tmp_path):
    doc,record=setup_design(tmp_path)
    record['sections']['groups']=['不同配方共用同一份前驱体。']
    calls=[]
    def chat(system,user,**kw):
        x=json.loads(user);calls.append(kw['trace_tag'])
        if kw['trace_tag'].endswith('patch'):
            return json.dumps({'base_hash':x['base_hash'],'changes':[{'path':['sections','groups',0],'value':'各配方分别制备，采用相同工艺条件进行比较。'}]})
        if '共用' in x['plan']['sections']['groups'][0]:
            return json.dumps({'subject':x['subject'],'status':'conflict','issues':[{'path':['sections','groups',0],'quote':'共用同一份前驱体','reason':'不同组成不能共用','suggestion':'分别制备'}]})
        return json.dumps({'subject':x['subject'],'status':'consistent','issues':[]})
    client=SimpleNamespace(available=True,chat=chat)
    result=review(record,doc,client,tmp_path)
    assert current(result,doc) and len(calls)==3
    assert review(record,doc,client,tmp_path)==result and len(calls)==3
    doc['ledger_view']['parameters']['cathode.L_um']['value']=61
    doc['scheme']['geometry']['cathode']['L_um']=61
    assert not current(result,doc)


def test_protocol_failure_never_forges_consistency_or_repeats_call(tmp_path):
    doc,record=setup_design(tmp_path)
    record['sections']['groups']=['新的主组说明，保持当前材料。']
    calls=[]
    def chat(*a,**kw):calls.append(1);return '{}'
    client=SimpleNamespace(available=True,chat=chat)
    result=review(record,doc,client,tmp_path)
    assert not current(result,doc)
    review(record,doc,client,tmp_path)
    assert len(calls)==1


def test_local_repair_accepts_only_the_test_field_reported_as_conflicting(tmp_path):
    from auto_battery_research.util.experiment_repair import repair_record
    from auto_battery_research.util.scheme_ledger import digest
    doc,record=profiles(tmp_path)
    record['requirement_tests']=[{'requirement_id':'r','conditions':'{{condition.high_voltage_window}}；{{condition.cycles}}'}]
    record['plan_hash']=recipes.plan_hash(record)
    def chat(system,user,**kw):
        x=json.loads(user)
        return json.dumps({'base_hash':x['base_hash'],'changes':[{'path':['requirement_tests',0,'conditions'],'value':'{{profile:main_cycle}}；{{profile:high_voltage}}'}]})
    fixed=repair_record(record,doc,SimpleNamespace(available=True,chat=chat),tmp_path,digest(record))
    assert fixed['requirement_tests'][0]['conditions']=='{{profile:main_cycle}}；{{profile:high_voltage}}'
    assert not fixed.get('pending_tests') and not fixed.get('pending_sections')


def test_schema5_cannot_omit_contract_and_macro_preserves_pending_scope(tmp_path):
    from auto_battery_research.util.experiment_consistency import paragraph_pending,preflight
    doc,record=profiles(tmp_path)
    record['schema_version']=5
    broken=copy.deepcopy(record);broken.pop('execution_contract');broken['plan_hash']=recipes.plan_hash(broken)
    with pytest.raises(ValueError,match='继承契约'):recipes.validate(broken,doc)
    doc['reviewer_output']={'issues':['condition.high_voltage_cycles存在声明冲突。']}
    assert paragraph_pending('{{profile:high_voltage}}','testing',doc,preflight(doc))==['condition.high_voltage_cycles']


def test_review_accepts_unique_requirement_id_as_location_without_new_call(tmp_path):
    from auto_battery_research.util.experiment_alignment_review import _validate
    record={'requirement_tests':[{'requirement_id':'r1','conditions':'旧工况'}]}
    response={'subject':'s','status':'conflict','issues':[{'path':['requirement_tests','r1','conditions'],'quote':'旧工况','reason':'冲突','suggestion':'继承主方案'}]}
    _validate(response,'s',record)
    assert response['issues'][0]['path']==['requirement_tests',0,'conditions']


def test_committed_main_scheme_revision_only_rechecks_existing_experiment(tmp_path):
    from auto_battery_research.util import scheme_ledger
    from auto_battery_research.tests.test_experiment_design import slot_response,recipe_payload
    doc,record=setup_design(tmp_path)
    original=copy.deepcopy(record['sections'])
    doc['scheme']['geometry']['cathode']['L_um']=70
    scheme_ledger.commit_design(tmp_path,doc,reason='已批准几何修订')
    calls=[]
    def chat(system,user,**kw):
        calls.append(kw['trace_tag'])
        assert kw['trace_tag']!='experiment_design'
        return slot_response(recipe_payload(),user,kw['trace_tag'])
    updated=recipes.generate(tmp_path,doc,SimpleNamespace(available=True,chat=chat),doc['final_answer'])
    assert updated['sections']==original
    assert updated['bindings']['cathode.L_um']['value']==70
    assert current(updated,doc) and calls==['experiment_alignment_review']
    assert updated['base_ledger_hash']==doc['ledger_hash']
