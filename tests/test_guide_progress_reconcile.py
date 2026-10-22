from copy import deepcopy
import hashlib
import json
import time
import pytest

from pcr_maa.guide_trial import execute_trial
from pcr_maa.guide_trial_progress import record_external_progress
from pcr_maa.preset_execution import Journal
from test_guide_trial import Port
from test_guide_trial_progress import pending, request, ObservePort


def save(path, value):
    raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def receipt(name, fields):
    rows = []
    for label, box, text in fields:
        word = dict(text=text, box=box, score=.99)
        rows.append(dict(name=label, algorithm='OCR', box=box,
                         detail=dict(all=[word], best=word, filtered=[word])))
    return dict(native_ok=True, tasker_inited=True, task_id=1,
        nodes=[dict(name=name, completed=True, action=dict(name=name, success=True, raw={}), recognition=rows)])


def evidence(tmp_path):
    from pcr_maa.labyrinth.profile import _LABELS, PROFILE_NODE
    journal, plan = pending(tmp_path / '.runtime/guide-trial-work/ledger')
    request(journal, plan)
    result = execute_trial(ObservePort(), journal, plan)
    run = tmp_path / '.runtime/supported-daily-work' / ('run-' + '1'*32)
    run.mkdir(parents=True)
    profile = receipt(PROFILE_NODE, [(name, roi, text) for (name, (roi, _)), text in zip(_LABELS.items(),
        ('简介', '生成简介', '变更角色', '玩家ID', plan['account_id'], 'ID复制'))])
    save(run / ('010-' + PROFILE_NODE + '.json'), profile)
    map_read = receipt('GTFireMap', [('GTDeepTitle',[80,8,180,65],'深域关卡'),
        ('GTFireMapLabel',[118,70,145,40],'红焰深域'),('GTMapChallengeCount',[638,567,178,43],'剩余挑战次数')])
    receipt_path = run / '021-GTFireMap.json'
    receipt_hash = save(receipt_path, map_read)
    frame = receipt_path.with_suffix('.png')
    frame.write_bytes(b'synthetic-frame-for-binding-test')
    frame_hash = hashlib.sha256(frame.read_bytes()).hexdigest()
    result['progress_observation'] = dict(source='maa_owner_map_observation', account_id=plan['account_id'],
        account_evidence=profile, scene='GTFireMap', observed_at=time.time(), frame_path=str(frame),
        frame_sha256=frame_hash, receipt_path=str(receipt_path), receipt_sha256=receipt_hash,
        map_evidence=map_read, battle_started=False, result_verified=False, settings_verified=False)
    report_path = run / 'report.json'
    save(report_path, dict(selected_modules=['guide_trial'], steps=[dict(name='guide_trial', result=result)]))
    review = dict(source='assistant_visual_review', completed_stage='红焰深域 8-1', next_stage='红焰深域 8-2',
        completion_marker='已完成', next_marker='NEXT', frame_sha256=frame_hash)
    return journal, plan, dict(plan=plan, report_path=report_path, map_review=review)


def test_append_preserves_intent_spent_attempt_unknown_and_forbids_replay(tmp_path):
    journal, plan, args = evidence(tmp_path)
    before = journal.path.read_bytes()
    event = record_external_progress(journal, **args)
    assert journal.path.read_bytes().startswith(before)
    assert journal.events()[1] == event and len(journal.events()) == 2
    assert event['state'] == 'historical_unknown' and event['outcome'] == 'unknown'
    assert not any(event[key] for key in ('result_verified', 'settings_verified', 'settlement_verified'))
    assert all(event[k] == v for k, v in journal.events()[0].items() if k != 'state')
    changed = dict(plan, source_hash='c'*64)
    port = Port()
    result = execute_trial(port, Journal(journal.directory), changed)
    assert result['outcome'] == 'completed_externally' and result['attempts_used'] == 1
    assert result['original_result_unknown'] and not result['settlement_verified']
    assert port.prepared == port.starts == 0 and not port.returned
    with pytest.raises(ValueError, match='pending_binding_invalid'):
        record_external_progress(journal, **args)


@pytest.mark.parametrize('field,value', [('source','program_victory'),('completed_stage','红焰深域 7-4'),
    ('completed_stage','红焰深域 8-3'),('next_stage','红焰深域 8-4'),('frame_sha256','f'*64),
    ('completion_marker','inferred'),('next_marker','locked')])
def test_review_must_describe_the_bound_completed_map(tmp_path, field, value):
    journal, _, args = evidence(tmp_path)
    before = journal.path.read_bytes()
    args['map_review'][field] = value
    with pytest.raises(ValueError, match='review_invalid'):
        record_external_progress(journal, **args)
    assert journal.path.read_bytes() == before


@pytest.mark.parametrize('section,field,value', [('result','observed_intent_id','9'*32),
    ('result','consumption_sent',True),('result','outcome','victory'),('result','battles',[{}]),
    ('observation','account_id','987654321'),('observation','battle_started',True),
    ('observation','settings_verified',True),('observation','scene','GTWinObserved'),
    ('observation','observed_at',0),('observation','observed_at',float('nan')),
    ('observation','frame_path','wrong.png')])
def test_reconciliation_rejects_wrong_or_unknown_owner_evidence(tmp_path, section, field, value):
    journal, _, args = evidence(tmp_path)
    before = journal.path.read_bytes()
    report = json.loads(args['report_path'].read_text(encoding='utf-8'))
    result = report['steps'][0]['result']
    target = result if section == 'result' else result['progress_observation']
    target[field] = value
    save(args['report_path'], report)
    with pytest.raises(ValueError):
        record_external_progress(journal, **args)
    assert journal.path.read_bytes() == before


@pytest.mark.parametrize('file', ['frame', 'map', 'account', 'request', 'claim', 'ledger'])
def test_changed_evidence_or_ledger_never_appends(tmp_path, file):
    journal, _, args = evidence(tmp_path)
    intent = journal.events()[-1]
    request_path = journal.directory / f"progress-observation-{intent['intent_id']}.json"
    paths = dict(frame=args['report_path'].with_name('021-GTFireMap.png'),
        map=args['report_path'].with_name('021-GTFireMap.json'),
        account=args['report_path'].with_name('010-LabyClientProfileObserved.json'),
        request=request_path, claim=request_path.with_name(request_path.stem + '-claimed.json'), ledger=journal.path)
    if file in ('account', 'claim'):
        value = json.loads(paths[file].read_text(encoding='utf-8'))
        value['tasker_inited' if file == 'account' else 'intent_id'] = False
        save(paths[file], value)
    else:
        with paths[file].open('ab') as stream:
            stream.write(b' ' if file != 'frame' else b'changed')
    before = journal.path.read_bytes()
    with pytest.raises(ValueError):
        record_external_progress(journal, **args)
    assert journal.path.read_bytes() == before


@pytest.mark.parametrize('field,value', [('state','settled'),('result_verified',True),
    ('settings_verified',True),('attempt',2),('at',0),('outcome','victory'),('original_result_unknown',False)])
def test_history_validation_never_upgrades_unknown_to_victory(tmp_path, field, value):
    from pcr_maa.guide_trial_progress import is_progress_history
    journal, _, args = evidence(tmp_path)
    event = record_external_progress(journal, **args)
    event[field] = value
    assert not is_progress_history(event, journal.events()[0])
    original = journal.events()[0]
    journal.path.write_text('\n'.join(json.dumps(e) for e in (original, event))+'\n', encoding='utf-8')
    with pytest.raises(ValueError, match='sequence invalid'):
        journal.events()


def test_new_stage_uses_same_ledger_and_three_attempt_limit(tmp_path, monkeypatch):
    from pcr_maa.guide_identity import SOURCE_HASHES
    journal, old_plan, args = evidence(tmp_path)
    record_external_progress(journal, **args)
    prefix = journal.path.read_bytes()
    monkeypatch.setitem(SOURCE_HASHES, '红焰深域 8-2', 'd'*64)
    monkeypatch.setattr('pcr_maa.guide_identity.require_source_team', lambda _: None)
    plan = dict(old_plan, stage='红焰深域 8-2', source_image_sha256='d'*64)
    first = Port(('defeat',), stop=False)
    first.prepare_retry = lambda _: (_ for _ in ()).throw(RuntimeError('stopped'))
    assert execute_trial(first, journal, plan)['attempts_used'] == 1
    plan['source_hash'] = 'e'*64
    second = Port(('defeat', 'defeat'))
    result = execute_trial(second, Journal(journal.directory), plan)
    assert result['outcome'] == 'defeat' and result['attempts_used'] == 2 and second.starts == 1
    result = execute_trial(second, Journal(journal.directory), plan)
    assert result['outcome'] == 'defeat' and result['attempts_used'] == 3 and second.starts == 2
    third = Port()
    assert execute_trial(third, journal, plan)['outcome'] == 'attempt_limit'
    assert third.starts == third.prepared == 0 and journal.path.read_bytes().startswith(prefix)


def test_new_stage_unknown_blocks_even_changed_plan(tmp_path, monkeypatch):
    from pcr_maa.guide_identity import SOURCE_HASHES
    journal, old_plan, args = evidence(tmp_path)
    record_external_progress(journal, **args)
    monkeypatch.setitem(SOURCE_HASHES, '红焰深域 8-2', 'd'*64)
    monkeypatch.setattr('pcr_maa.guide_identity.require_source_team', lambda _: None)
    plan = dict(old_plan, stage='红焰深域 8-2', source_image_sha256='d'*64)
    assert execute_trial(Port(('unknown',)), journal, plan)['attempts_used'] == 1
    plan['source_hash'] = 'e'*64
    port = Port()
    assert execute_trial(port, journal, plan)['error'] == 'guide_trial_previous_result_unknown'
    assert port.prepared == port.starts == 0
