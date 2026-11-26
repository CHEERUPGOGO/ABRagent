"""参数协议权限的先建议后授权、等值转换、语义否决与断点恢复。"""
import copy
import json
from types import SimpleNamespace

import pytest

from auto_battery_research.tests.test_writer_protocol import example
from auto_battery_research.util import writer_protocol as protocol
from auto_battery_research.util.writer_protocol_advice import candidates, apply_corrections


EVIDENCE = [{'passage_id': 'p1', 'source': '文献原文', 'text': '原工艺凝胶温度80 °C，pH为7.5。'}]


def document():
    data = example()
    data['parameters'] = {
        'process.gel_temp_C': {'value': 80, 'unit': '°C', 'basis': 'literature',
                               'source': '[p1]', 'meaning': '参考文献的工艺取值'},
        'process.pH': {'value': '7.5', 'unit': '', 'basis': 'literature', 'source': '[p1]'},
        'process.li_excess_fraction': {'value': 3, 'unit': 'mol%', 'basis': 'engineering_proposal',
            'source': '待验证工程建议', 'quantity_kind': 'ratio', 'denominator': '总阳离子'},
    }
    data['sections']['goals']['body'] += (
        '本方案参考[p1]暂采用凝胶温度{{param:process.gel_temp_C}}，仍待实验验证。'
        '本方案调节pH至{{param:process.pH}}，待验证。'
        '本方案提出以总阳离子为分母的锂过量分数{{param:process.li_excess_fraction}}，待验证。')
    return data


def errors(data, current=None):
    return protocol.diagnostics(protocol.normalize(data), current or {}, None)


def client_for(state, saved, *, review=True, decision='apply', fail_at=None, change_advice=None):
    calls = []
    def chat(system, user, **kwargs):
        tag = kwargs['trace_tag']
        calls.append(tag)
        request = json.loads(user)
        assert client.single_attempt is True
        assert saved[-1]['repair_attempts'] == state['repair_attempts']
        if tag == 'writer_protocol_advice':
            assert saved[-1]['parameter_advice']['attempted']
            if tag == fail_at:
                raise RuntimeError('建议请求中断')
            choices = request['candidate_corrections']
            rows = []
            for identifier, candidate in choices.items():
                body_path = candidate['body_paths'][0]
                node = request['document']
                for key in body_path:
                    node = node[key]
                rows.append(dict(id=identifier, decision=decision, reason='原文明确采用，转换保持原数量与分母',
                                 refs=[dict(body_path=body_path, quote=node)]))
            response = dict(input_hash=request['input_hash'], recommendations=rows)
            if change_advice:
                change_advice(response)
            return json.dumps(response, ensure_ascii=False)
        if tag == 'writer_protocol_repair':
            return json.dumps(dict(base_hash=request['base_hash'], parameters=[],
                                   corrections=list(request.get('allowed_corrections', {}))))
        assert tag == 'writer_protocol_verify'
        assert saved[-1]['parameter_review']['attempted']
        if tag == fail_at:
            raise RuntimeError('复核请求中断')
        return json.dumps(dict(input_hash=request['input_hash'], verdicts=[
            dict(id=r['id'], supported=review, reason='保持同一样品和工况；是待验证采用' if review else '仅为文献原工况，未被主方案采用')
            for r in request['corrections']]))
    client = SimpleNamespace(available=True, chat=chat)
    return client, calls


def run(data=None, *, state=None, current=None, budget=None, **client_options):
    data = data or document()
    state = state if state is not None else {'raw_response': json.dumps(data, ensure_ascii=False)}
    saved = []
    client, calls = client_for(state, saved, **client_options)
    result = protocol.produce(client, '只生成原始设计', '完整用户要求与证据', current or {}, state,
                              lambda: saved.append(copy.deepcopy(state)), budget=budget, evidence=EVIDENCE)
    return result, state, calls, client, saved


def test_advice_precedes_patch_and_only_program_grants_can_change_representation():
    original = document()
    result, state, calls, client, saved = run(original)
    assert not result['fallback'], result
    assert calls == ['writer_protocol_advice', 'writer_protocol_repair', 'writer_protocol_verify']
    assert state['repair_attempts'] == 1 and state['status'] == 'READY'
    assert state['parameter_advice']['status'] == 'READY' and state['parameter_review']['status'] == 'ACCEPTED'
    assert result['structured_sections']['sections'] == original['sections']
    assert json.loads(state['raw_response']) == original
    values = result['numeric_parameters']
    assert values['process.gel_temp_C']['basis'] == 'engineering_proposal'
    assert values['process.gel_temp_C']['source'] == '[p1]' and values['process.gel_temp_C']['value'] == 80
    assert values['process.pH']['value'] == 7.5 and values['process.pH']['quantity_kind'] == 'physical'
    assert values['process.li_excess_fraction']['value'] == .03 and values['process.li_excess_fraction']['unit'] == ''
    assert values['process.li_excess_fraction']['denominator'] == '总阳离子'
    assert not any(row.get('locked') for row in values.values())
    assert not hasattr(client, 'single_attempt')
    assert 'review_passed' not in result
    replay = protocol.produce(SimpleNamespace(available=False), 's', '完整用户要求与证据', {},
                              json.loads(json.dumps(state)), lambda: None, evidence=EVIDENCE)
    assert replay['draft_answer'] == result['draft_answer']


def test_scientific_or_scope_denial_rejects_the_whole_conversion_and_is_not_retried():
    result, state, calls, client, _ = run(review=False)
    assert result['fallback'] and '协议转换语义未通过' in result['error']
    assert state['parameter_review']['status'] == 'REJECTED'
    assert not state.get('accepted_patch') and state['repair_attempts'] == 1
    again = protocol.produce(client, 's', '完整用户要求与证据', {}, state, lambda: None, evidence=EVIDENCE)
    assert again['fallback'] and len(calls) == 3 and state['repair_attempts'] == 1


@pytest.mark.parametrize('tag', ['writer_protocol_advice', 'writer_protocol_verify'])
def test_interrupted_advice_or_verification_never_resends_claimed_requests(tag):
    result, state, calls, client, _ = run(fail_at=tag)
    assert result['fallback']
    old_calls = list(calls)
    restored = json.loads(json.dumps(state))
    replay = protocol.produce(client, 's', '完整用户要求与证据', {}, restored, lambda: None, evidence=EVIDENCE)
    assert replay['fallback'] and calls == old_calls and restored['repair_attempts'] == 1


def test_old_noop_patch_can_use_remaining_budget_once_without_repeating_old_call():
    data = document()
    state = {'raw_response': json.dumps(data), 'repair_attempts': 1,
             'repair_raw_response': json.dumps({'base_hash': protocol.digest(protocol.normalize(data)), 'parameters': []})}
    result, state, calls, _, _ = run(data, state=state)
    assert not result['fallback'], result
    assert len(calls) == 3 and state['repair_attempts'] == 2 and len(state['repair_history']) == 2
    assert state['repair_history'][0]['authority_upgrade_pending'] is True
    replay = protocol.produce(SimpleNamespace(available=False), 's', '完整用户要求与证据', {}, state,
                              lambda: None, evidence=EVIDENCE)
    assert replay['draft_answer'] == result['draft_answer'] and state['repair_attempts'] == 2


def test_budget_zero_does_not_call_advisor_or_patch_model():
    result, state, calls, _, _ = run(budget={'protocol_rounds': 0})
    assert result['fallback'] and not calls and not state.get('repair_attempts')


def test_advisor_can_defer_changes_without_gaining_write_permission():
    result, state, calls, _, _ = run(decision='defer')
    assert result['fallback'] and calls == ['writer_protocol_advice', 'writer_protocol_repair']
    assert state['parameter_advice']['grants'] == {} and state['repair_attempts'] == 1


@pytest.mark.parametrize('mutation', [
    lambda r: r['recommendations'].pop(),
    lambda r: r['recommendations'].append(copy.deepcopy(r['recommendations'][0])),
    lambda r: r.update(input_hash='stale'),
    lambda r: r['recommendations'][0].update(after={'value': 1000}),
    lambda r: r['recommendations'][0].update(refs=[]),
    lambda r: r['recommendations'][0]['refs'][0].update(body_path=['parameters', 'process.pH']),
    lambda r: r['recommendations'][0]['refs'][0].update(quote='不存在的原句'),
])
def test_invalid_advice_does_not_reach_patch_call(mutation):
    result, state, calls, _, _ = run(change_advice=mutation)
    assert result['fallback'] and calls == ['writer_protocol_advice']
    assert not state.get('repair_raw_response') and state['repair_attempts'] == 1


@pytest.mark.parametrize('current', [
    {'process.pH': {'value': None, 'locked': True}},
    {'process.pH': {'value': 7, 'locked': False}},
])
def test_existing_parameters_and_locked_null_never_receive_extended_authority(current):
    data = document()
    choices = candidates(data, current, errors(data, current), EVIDENCE)
    assert all(row['path'] != 'process.pH' for row in choices.values())


def test_alias_cannot_bypass_current_parameter_protection():
    data = document()
    data['parameters']['process.pH']['canonical_path'] = 'cathode.porosity'
    current = {'cathode.porosity': {'value': .3, 'locked': True}}
    assert not any(r['path'] == 'process.pH' for r in candidates(data, current, errors(data, current), EVIDENCE).values())


@pytest.mark.parametrize('value', ['007', '7.5-8', '>7.5', '7.5 pH', 'NaN', 'Infinity', '7,5', True, None])
def test_ambiguous_strings_and_invalid_scalars_are_not_numeric_conversion_candidates(value):
    data = document()
    data['parameters'] = {'process.pH': dict(data['parameters']['process.pH'], value=value, basis='engineering_proposal')}
    choices = candidates(data, {}, [{'code': 'PARAMETER', 'path': ['parameters', 'process.pH']}], EVIDENCE)
    assert not choices


@pytest.mark.parametrize('patch', [
    {'denominator': ''}, {'unit': 'mAh/g'}, {'quantity_kind': 'physical'}, {'value': 101}, {'value': -1},
])
def test_percent_conversion_requires_explicit_fraction_and_denominator(patch):
    data = document()
    path = 'process.li_excess_fraction'
    data['parameters'] = {path: dict(data['parameters'][path], **patch)}
    assert not candidates(data, {}, [{'code': 'PARAMETER', 'path': ['parameters', path]}], EVIDENCE)


def test_literature_without_archived_source_cannot_be_adopted():
    data = document()
    data['parameters'].pop('process.li_excess_fraction')
    assert not candidates(data, {}, errors(data), [])


def test_forged_correction_ids_and_arbitrary_values_are_rejected():
    data = protocol.normalize(document())
    for ids in (['fake-id'], [{'path': 'process.pH', 'value': 8}], ['fake-id', 'fake-id']):
        with pytest.raises(ValueError):
            apply_corrections(data, ids, {}, {})


def test_advice_and_verification_cannot_be_reused_with_different_context_or_evidence():
    result, state, _, _, _ = run()
    assert not result['fallback']
    replay = protocol.produce(SimpleNamespace(available=False), 's', '已变化的要求', {}, state,
                              lambda: None, evidence=EVIDENCE)
    assert replay['fallback'] and '输入已变化' in replay['error']


def test_explicit_numeric_text_identifier_remains_a_text_declaration():
    data = document()
    data['parameters']['process.pH']['quantity_kind'] = 'text'
    choices = candidates(data, {}, errors(data), EVIDENCE)
    assert all(row['path'] != 'process.pH' for row in choices.values())
