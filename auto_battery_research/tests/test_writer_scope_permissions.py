"""作用域迁移只改变新字段的存储路径，保留数值身份、依赖、预算及历史调用。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_writer_protocol import example
from auto_battery_research.tests.test_writer_protocol_permissions import EVIDENCE, run, errors
from auto_battery_research.util import writer_protocol as protocol
from auto_battery_research.util.writer_protocol_advice import candidates, apply_corrections
from auto_battery_research.util.numeric_consistency import context, identity


def document():
    data = example()
    data['parameters'] = {
        'cathode.L_um': {'value': 25, 'unit': 'um', 'basis': 'engineering_proposal',
                         'source': '明确工程提案', 'meaning': '正极干涂层厚度', 'conditions': '干涂层'},
        'electrolyte.volume': {'value': 80, 'unit': 'uL', 'basis': 'engineering_proposal',
                               'source': '明确工程提案', 'meaning': '注液量', 'conditions': '扣电泛液基准'},
    }
    data['sections']['goals']['body'] += (
        '干涂层厚度{{param:cathode.L_um}}；扣电泛液基准注液量{{param:electrolyte.volume}}。')
    return data


def choices_for(data, current=None):
    return candidates(data, current or {}, errors(data, current))


def test_scoped_fields_are_relocated_without_changing_their_quantity_identity():
    data = document()
    result, state, calls, _, _ = run(data)
    assert not result['fallback'], result
    assert calls == ['writer_protocol_advice', 'writer_protocol_repair', 'writer_protocol_verify']
    assert state['repair_attempts'] == 1 and state['repair_history'][0]['authority_version'] == 2
    assert json.loads(state['raw_response']) == data
    grants = state['parameter_advice']['grants']
    assert len(grants) == 2
    updated = result['structured_sections']
    expected_sections = copy.deepcopy(data['sections'])
    for grant in grants.values():
        old, new = grant['path'], grant['new_path']
        assert old not in updated['parameters']
        assert updated['parameters'][new] == dict(data['parameters'][old], canonical_path=old)
        assert identity(old, data['parameters'][old]) == identity(new, updated['parameters'][new])
        assert context(updated['parameters'][new]) != ('main', 'main', '[]')
        expected_sections['goals']['body'] = expected_sections['goals']['body'].replace(
            '{{param:' + old + '}}', '{{param:' + new + '}}')
    assert updated['sections'] == expected_sections
    assert 'review_passed' not in result
    offline = protocol.produce(SimpleNamespace(available=False), 's', '完整用户要求与证据', {},
                               json.loads(json.dumps(state)), lambda: None,
                               evidence=EVIDENCE)
    assert offline['draft_answer'] == result['draft_answer']


@pytest.mark.parametrize('metadata', [
    {'group_id': 'control'}, {'scope': 'candidate'}, {'purpose': '高压对照'},
    {'conditions': {'温度': 298, 'purpose': '主方案'}},
    {'conditions': [{'name': '温度', 'value': 298}]},
])
def test_all_scope_metadata_is_preserved_and_does_not_become_main(metadata):
    data = document()
    row = data['parameters']['cathode.L_um']
    row.pop('conditions')
    row.update(metadata)
    grant = next(c for c in choices_for(data).values() if c['path'] == 'cathode.L_um')
    assert grant['after'] == dict(row, canonical_path='cathode.L_um')
    assert identity(grant['path'], row) == identity(grant['new_path'], grant['after'])


def test_new_user_requirement_can_be_relocated_without_becoming_an_engineering_proposal():
    data = document()
    data['parameters']['cathode.L_um']['basis'] = 'user_requirement'
    result, state, *_ = run(data)
    assert not result['fallback']
    grant = next(c for c in state['parameter_advice']['grants'].values() if c['path'] == 'cathode.L_um')
    assert result['numeric_parameters'][grant['new_path']]['basis'] == 'user_requirement'
    assert not result['numeric_parameters'][grant['new_path']]['locked']


@pytest.mark.parametrize('current', [
    {'cathode.L_um': {'value': None, 'locked': True}},
    {'cathode.L_um': {'value': 20, 'locked': False}},
    {'cathode.other_thickness': {'canonical_path': 'cathode.L_um', 'value': None, 'locked': True}},
])
def test_existing_parameters_and_aliases_cannot_be_bypassed_by_scope_relocation(current):
    assert all(c['path'] != 'cathode.L_um' for c in choices_for(document(), current).values())


@pytest.mark.parametrize('change', [
    {'locked': True}, {'value': -1}, {'value': '25'}, {'value': True},
    {'unit': 'mm'}, {'canonical_path': 'anode.L_um'}, {'basis': 'model'},
    {'conditions': {}}, {'conditions': ''},
])
def test_scope_permission_does_not_remove_other_validation_or_override_identity(change):
    data = document()
    data['parameters']['cathode.L_um'].update(change)
    assert all('scope_relocation' not in c['actions'] or c['path'] != 'cathode.L_um'
               for c in choices_for(data).values())


def test_collision_and_unbound_scope_declarations_do_not_receive_relocation_grants():
    data = document()
    grant = next(c for c in choices_for(data).values() if c['path'] == 'cathode.L_um')
    data['parameters'][grant['new_path']] = {'value': 30, 'unit': 'um', 'basis': 'engineering_proposal', 'source': '其他声明'}
    assert all(c['path'] != 'cathode.L_um' for c in choices_for(data).values())
    data['sections']['goals']['body'] = '不引用厚度参数。'
    assert not choices_for(data)


def test_dependency_paths_and_calculated_references_follow_relocation():
    data = document()
    data['parameters']['condition.thickness_ratio'] = {
        'value': '100', 'unit': '%', 'quantity_kind': 'ratio', 'denominator': '正极厚度',
        'basis': 'engineering_proposal', 'source': '明确声明',
        'ratio_inputs': ['cathode.L_um', 'cathode.L_um'],
    }
    data['sections']['goals']['body'] += (
        '厚度比{{param:condition.thickness_ratio}}；按同一厚度相除，厚度比为1，计算口径为正极厚度。')
    result, state, *_ = run(data)
    assert not result['fallback'], result
    grant = next(c for c in state['parameter_advice']['grants'].values() if c['path'] == 'cathode.L_um')
    assert result['structured_sections']['parameters']['condition.thickness_ratio']['ratio_inputs'] == [grant['new_path']] * 2
    record = next(c for c in state['parameter_review']['request']['corrections']
                  if c['path'] == 'condition.thickness_ratio')
    assert record['after'] == result['structured_sections']['parameters']['condition.thickness_ratio']


def granted(data):
    return {i: dict(c, document_hash=protocol.digest(data), refs=[], reason='原声明保持')
            for i, c in choices_for(data).items()}


def test_scoped_calculated_reference_is_synchronized_without_editing_other_body_text():
    data = document()
    data['sections']['goals']['body'] += '{{derived:calculated.cathode.L_um}}'
    grants = granted(data)
    updated, _ = apply_corrections(data, list(grants), {}, grants)
    grant = next(c for c in grants.values() if c['path'] == 'cathode.L_um')
    assert '{{derived:calculated.' + grant['new_path'] + '}}' in updated['sections']['goals']['body']


@pytest.mark.parametrize('change', [
    {'new_path': 'anode.L_um'}, {'after': {'value': 999}}, {'actions': ['scope_relocation', 'numeric_string']},
])
def test_forged_scope_targets_and_changes_are_rejected(change):
    data = document()
    grants = granted(data)
    identifier = next(iter(grants))
    grants[identifier].update(change)
    with pytest.raises(ValueError):
        apply_corrections(data, [identifier], {}, grants)


def test_scope_target_cannot_receive_an_additional_metadata_patch():
    data = document()
    grants = granted(data)
    identifier = next(iter(grants))
    patch = dict(base_hash=protocol.digest(data), corrections=[identifier],
                 parameters=[{'path': grants[identifier]['new_path'], 'metadata': {'unit': 'mm'}}])
    with pytest.raises(ValueError, match='不能同时叠加'):
        protocol.protocol_patch(data, patch, {}, None, {}, lambda: None, grants=grants)


def test_scope_relocation_accepts_existing_equivalent_unit_normalization():
    data = document()
    data['parameters']['cathode.L_um']['unit'] = 'µm'
    data['parameters']['electrolyte.volume']['unit'] = 'μL'
    result, state, *_ = run(data)
    assert not result['fallback'], result
    assert json.loads(state['raw_response']) == data
    for grant in state['parameter_advice']['grants'].values():
        assert result['numeric_parameters'][grant['new_path']]['unit'] in ('um', 'uL')


def test_scope_review_denial_retains_original_and_does_not_retry():
    data = document()
    result, state, calls, client, _ = run(data, review=False)
    assert result['fallback'] and '协议转换语义未通过' in result['error']
    assert json.loads(state['raw_response']) == data and not state.get('accepted_patch')
    again = protocol.produce(client, 's', '完整用户要求与证据', {}, state, lambda: None,
                             evidence=EVIDENCE)
    assert again['fallback'] and len(calls) == 3


@pytest.mark.parametrize('used,limit,success', [(3, 5, True), (1, 5, True), (3, 3, False), (3, 0, False)])
def test_v1_noop_upgrades_only_with_remaining_yaml_budget(used, limit, success):
    data = document()
    raw_patch = json.dumps({'base_hash': protocol.digest(protocol.normalize(data)), 'parameters': [], 'references': []})
    rejected = json.dumps({'base_hash': protocol.digest(protocol.normalize(data)), 'parameters': [
        {'path': 'cathode.L_um', 'new_path': 'condition.cathode.L_um'}]})
    state = {'raw_response': json.dumps(data), 'repair_attempts': used,
             'repair_history': [{'authority_version': 1, 'repair_raw_response': rejected} for _ in range(used - 1)]
                               + [{'authority_version': 1, 'repair_raw_response': raw_patch}]}
    result, state, calls, *_ = run(data, state=state, budget={'protocol_rounds': limit})
    assert result['fallback'] is not success, result
    assert state['repair_attempts'] == used + int(success)
    assert len(calls) == (3 if success else 0)
    assert len(state['repair_history']) == used + int(success)
    if success:
        assert state['repair_history'][-1]['authority_version'] == 2


@pytest.mark.parametrize('tag', ['writer_protocol_advice', 'writer_protocol_verify'])
def test_interrupted_scope_requests_are_not_resent_after_restore(tag):
    result, state, calls, client, _ = run(document(), fail_at=tag)
    assert result['fallback']
    before = list(calls)
    replay = protocol.produce(client, 's', '完整用户要求与证据', {}, json.loads(json.dumps(state)), lambda: None,
                              evidence=EVIDENCE)
    assert replay['fallback'] and calls == before


def test_new_noop_does_not_repeat_scope_requests():
    data = document()
    result, state, calls, *_ = run(data, decision='defer')
    assert result['fallback'] and state['repair_attempts'] == 1
    assert calls == ['writer_protocol_advice', 'writer_protocol_repair']
    assert state['repair_history'][0]['authority_version'] == 2
