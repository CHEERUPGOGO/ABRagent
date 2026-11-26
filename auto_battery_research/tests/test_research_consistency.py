"""研究论证的实质一致性：轻微书写兼容、局部失效和独立审核保护。"""
import copy

import pytest

from auto_battery_research.tests.test_research_support import sample, verdict
from auto_battery_research.util.research_support import normalize, apply_review, supported, locate_sources
from auto_battery_research.util.research_sources import compatible_quote


def test_explanatory_insertion_relocates_quote_and_records_actual_source():
    doc, row = sample()
    old = "主循环的换算基准为250 mA/g，即按声明倍率进行测试，结果须独立验证。"
    new = old.replace("250 mA/g", "250 mA/g（比电流）")
    doc['final_answer'] = '<a id="design-rate"></a>\n\n### 1.1 测试\n\n' + new
    row.update(source_quotes=[old], field_support={'response':[0]}, response='按声明倍率测试，结果仍待验证。', reason='', next_step='', citations=[], parameter_refs=[])
    result = normalize(row,'answers',doc,doc['ledger_view']['parameters'])
    assert result['source_quotes'] == [new]
    assert result['source_locations'][0]['section_id'] == 'rate'
    assert result['source_locations'][0]['paragraph_id'] == 'rate:p1'
    assert not supported(result,'response',doc,doc['ledger_view']['parameters'])


@pytest.mark.parametrize('after',[
    '主组采用250 mA/g测试，观察循环结果，尚未验证。',
    '对照采用200 mA/g测试，观察循环结果，尚未验证。',
    '主组采用200 mA/g测试，观察循环结果，已经验证。',
    '主组采用200 mA/g测试，观察氧化结果，尚未验证。',
])
def test_substantive_changes_are_not_silently_reanchored(after):
    assert not compatible_quote('主组采用200 mA/g测试，观察循环结果，尚未验证。',after)


def test_unrelated_paragraph_and_other_field_changes_keep_review():
    doc,row=sample();params=doc['ledger_view']['parameters']
    row=normalize(row,'answers',doc,params)
    apply_review({'answers':[row]},[verdict('answers',row,doc)],doc,params,doc['evidence'])
    doc['final_answer']+='\n\n另一个不相关的设备说明。'
    assert supported(row,'response',doc,params)
    row['next_step']='改变了后续验证安排。'
    assert supported(row,'response',doc,params)
    assert not supported(row,'next_step',doc,params)
    doc['final_answer']=doc['final_answer'].replace('方案采用配方A。','方案采用配方B。')
    assert not supported(row,'response',doc,params)


def test_parameter_reference_can_attach_its_signed_declaration():
    from auto_battery_research.util.numeric_bindings import render
    doc,row=sample();p=doc['ledger_view']['parameters']
    p['cathode.doping.x_A']={'value':.01,'unit':'','basis':'engineering_proposal','source':'原方案','quantity_kind':'site_fraction','denominator':'总阳离子'}
    source=render('主组Na占总阳离子{{param:cathode.doping.x_A}}，只是工程起点。',p)
    doc['final_answer']+='\n\n'+source
    row.update(response='主组采用{{cathode.doping.x_A}}，仍须验证。',parameter_refs=['cathode.doping.x_A'])
    revised=normalize(row,'answers',doc,p)
    assert source in revised['source_quotes']
    assert len(revised['field_support']['response'])==4
    assert not supported(revised,'response',doc,p)


def test_partial_arguments_do_not_suppress_other_archived_rationale():
    from auto_battery_research.util.research_reporting import render_research
    doc,row=sample()
    doc['research']={'answers':[], 'candidates':[], 'arguments':[{'id':'a','basis':'inference','text':'已有机理摘录。','source_quote':'已有机理摘录。'}]}
    doc['final_answer']='已有机理摘录。\n\n<a id="design-why-cu-second"></a>\n\n### 4.3 备选取舍\n\n'+('另一备选有明确适用边界，仍须同批对照验证。'*4)
    body=render_research(doc)
    assert '已有机理摘录' in body and '另一备选有明确适用边界' in body


def test_cited_evidence_can_supply_original_condition_requested_by_review():
    from auto_battery_research.tests.test_research_narrative import fixture
    from auto_battery_research.util.research_narrative import normalize as normalize_narrative
    doc,raw=fixture()
    doc['evidence'][0]['text']+='；该文献使用30%高掺杂条件。'
    paragraph=raw['sections'][0]['paragraphs'][0]
    paragraph['text']+='文献采用30%高掺杂条件，不能直接外推本方案。'
    assert normalize_narrative(raw,doc)['sections']
    paragraph['text']=paragraph['text'].replace('30%','35%')
    with pytest.raises(ValueError,match='数值'):
        normalize_narrative(raw,doc)
