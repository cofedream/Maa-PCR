from copy import deepcopy
import hashlib

import pytest

from scripts.ocr_benchmark import gates, validate


def row(sample='good', expected=True, hits=None, **kw):
    return dict(sample=sample, expected=expected, hits=hits if hits is not None else [expected]*3,
                group='session', split='held_out', category='fixed_text', method='method',
                real_failure=False, seconds=[.1]*3, **kw)


def result(model, rows): return dict(model=model, status='compatible', rows=rows)


def test_gates_require_real_improvement_and_no_false_accept():
    before = [dict(row(), method='baseline'), dict(row('bad', False), method='baseline')]
    before += [row(),row('bad',False)]
    baseline = result('r53', before)
    current = result('v5', [row(), row('bad', False)])
    assert gates([baseline,current], 'r53')['fixed_text']['selected'] == 'r53'
    before[2].update(hits=[False]*3)
    current['rows'][0]['real_failure'] = True
    assert gates([baseline,current], 'r53')['fixed_text']['selected'] == 'v5'
    current['rows'][1]['hits'] = [True,False,False]
    value = gates([baseline,current], 'r53')['fixed_text']
    assert value['selected'] == 'r53'
    assert 'dangerous_false_accept' in value['candidates'][1]['reasons']


def test_gates_reject_regression_missing_coverage_and_slow_model():
    before = [dict(row(), method='baseline'),dict(row('bad', False),method='baseline')]
    before += [row(),row('bad',False)]
    current = result('v6', [row(hits=[False]*3),row('bad',False)])
    selection = gates([result('r53',before),current], 'r53')
    assert 'baseline_success_regression' in selection['fixed_text']['candidates'][1]['reasons']
    assert selection['numeric_identity']['retained_baseline']
    current['rows'][0]['seconds'] = [2.0]*3
    assert 'p95_budget_or_coverage' in gates([result('r53',before),current], 'r53')['fixed_text']['candidates'][1]['reasons']


def test_method_improvement_cannot_be_claimed_as_new_model_gain():
    original=[dict(row(hits=[False]*3),method='baseline'),dict(row('bad',False),method='baseline')]
    fixed=[dict(row(),real_failure=True),row('bad',False)]
    new_model=result('v5',[dict(row(),real_failure=True),row('bad',False)])
    selected=gates([result('r53',original+fixed),new_model],'r53')['fixed_text']
    assert selected['selected']=='r53'
    assert 'no_real_failed_image_improvement' in selected['candidates'][1]['reasons']


def test_manifest_refuses_group_leaks_and_tampered_models(tmp_path):
    sample = tmp_path/'sample';sample.write_bytes(b'fixed')
    model = tmp_path/'model';model.mkdir()
    for name in ('det.onnx','rec.onnx','keys.txt'): (model/name).write_bytes(b'weight')
    models = [dict(id='r53',directory=str(model),files={name:dict(sha256=hashlib.sha256(b'weight').hexdigest()) for name in ('det.onnx','rec.onnx','keys.txt')})]
    samples = [dict(id='1',path=str(sample),sha256=hashlib.sha256(b'fixed').hexdigest(),group='g',split='calibration',expected=True,entry='Page',pipelines={'method':{}},category='fixed_text')]
    validate(samples,models)
    with pytest.raises(ValueError,match='group_split_leak'):
        validate(samples+[dict(samples[0],id='2',split='held_out')],models)
    with pytest.raises(ValueError,match='duplicate_image_group_leak'):
        validate(samples+[dict(samples[0],id='2',group='other')],models)
    (model/'keys.txt').write_bytes(b'wrong')
    with pytest.raises(ValueError,match='model_hash_mismatch'):validate(samples,models)
    with pytest.raises(ValueError,match='invalid_or_duplicate_model_id'):
        validate(samples,[dict(models[0],id='../escape')])


def test_daily_and_labyrinth_share_exact_home_words():
    import json
    from pcr_maa import native
    from pcr_maa.labyrinth.game_session import game_session_pipeline
    shared=json.loads((native.RESOURCE/'pipeline/home_text.json').read_text(encoding='utf-8'))
    daily=json.loads((native.RESOURCE/'pipeline/presets.json').read_text(encoding='utf-8'))
    assert daily['EnterAdventure']['all_of'] == ['HomeTextLayout','HomeNoKnownLargePanel']
    lab=game_session_pipeline(123)
    for name, node in shared.items(): assert lab[name] == node
    for name, common in [('Adventure','Adventure'),('Schedule','Schedule'),('Gift','Gift')]:
        assert lab['LabyClient'+name+'Label'] == shared['Home'+common+'Text']


def test_inventory_includes_inline_rules_and_dynamic_factories():
    from scripts.ocr_inventory import inventory
    report=inventory()
    assert all(r['status']=='expanded' for r in report['factories'])
    nodes=report['visual_nodes']
    assert any(r['node']=='/HomeScheduleText' and r['only_rec'] and r['expected']=='^日程表$' for r in nodes)
    assert any('/all_of/0' in r['node'] and r['method']=='OCR' for r in nodes)
    assert any(r['source'].endswith(':character_pipeline') for r in nodes)
    assert any(r['source'].endswith(':team_pipeline') for r in nodes)
    assert all(r['source'] and r['line'] and r['construction'] for r in report['python_construction_sites'])
