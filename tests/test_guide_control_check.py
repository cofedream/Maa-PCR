"""Explicit one-start control check through the normal trial port and Journal."""
from copy import deepcopy
import json
import pytest
from test_guide_opening import candidate, SimulatedMaa
from pcr_maa.guide_trial import execute_trial
from pcr_maa.guide_trial_control_check import authorize_control_check, result_matches, is_control_check_exit
from pcr_maa.preset_execution import Journal


class CheckPort(SimulatedMaa):
    def __init__(self, path, *, exit_unknown=False, drift=False, **kwargs):
        super().__init__(path, auto=True, initial=[True]*5, **kwargs)
        self.exit_unknown=exit_unknown; self.drift=drift
    def run(self, node, *, click=False):
        if self.drift and node=='GTBattleReadSettings':
            self.state['sets'][0]=False
        return super().run(node,click=click)
    def exit_control_check(self, plan):
        self.calls.append(('exit_control_check',True))
        if self.exit_unknown: raise RuntimeError('exit_unknown')
        self.returned=True
        return dict(scene='GTFireMap',verified=True,stage=plan['stage'],account_id=plan['account_id'],
                    frame_sha256='d'*64,receipt_sha256='e'*64)
    def observe_control_check(self, plan):
        return dict(source='synthetic_read_only',scene='unknown',battle_started=False,result_verified=False)
    def recover_control_exit(self, plan, recovery):
        from pcr_maa.guide_trial_control_check import control_exit_result
        self.calls.append(('recover_control_exit',True))
        if self.exit_unknown:raise RuntimeError('exit_unknown')
        self.returned=True
        return control_exit_result(recovery['check'],recovery['readings'],
            dict(scene='GTFireMap',verified=True,stage=plan['stage'],account_id=plan['account_id'],
                 frame_sha256='d'*64,receipt_sha256='e'*64))


def setup(path, **kwargs):
    plan=candidate(31,'红焰深域 8-5'); journal=Journal(path/'ledger')
    authorize_control_check(journal,plan,129701,'保持 AUTO，涅菈 SET 关后开，验证后退出，只开一次。')
    return plan,journal,CheckPort(path,**kwargs)


@pytest.mark.parametrize('order',[(0,1,2,3,4),(4,1,3,0,2)])
def test_control_check_exact_two_toggles_then_exit_no_victory_or_retry(tmp_path,order):
    plan,journal,port=setup(tmp_path,order=order)
    report=execute_trial(port,journal,plan)
    assert report['outcome']=='control_checked_exit' and report['attempts_total']==1
    assert report['exit_verified'] and not report['settlement_verified'] and not report['daily_complete']
    assert [n for n,c in port.calls if c]==['GTBattleStart','GTBattleToggleSet','GTBattleToggleSet','exit_control_check']
    assert port.returned and port.state['auto'] and all(port.state['sets'])
    events=journal.events(); assert len(events)==2 and is_control_check_exit(events[1],events[0])
    assert events[1]['result_verified'] is False
    readings=events[1]['result_evidence']['control_readings']
    assert [r['set_by_unit']['129701'] for r in readings]==[True,False,True]
    assert all(r['auto'] and all(v for uid,v in r['set_by_unit'].items() if uid!='129701') for r in readings)
    second=CheckPort(tmp_path); before=journal.path.read_bytes()
    assert execute_trial(second,journal,plan)['error']=='guide_control_check_already_claimed'
    assert not second.calls and journal.path.read_bytes()==before


@pytest.mark.parametrize('fault',['no_effect','auto_lost','identity_changed'])
def test_failed_readback_stops_after_one_toggle_and_preserves_pending(tmp_path,fault):
    plan,journal,port=setup(tmp_path,fault=fault)
    report=execute_trial(port,journal,plan)
    assert report['error']=='guide_trial_settings_readback_unknown'
    assert [n for n,c in port.calls if c]==['GTBattleStart','GTBattleToggleSet']
    assert len(journal.events())==1
    second=CheckPort(tmp_path);plan['source_hash']='f'*64
    assert execute_trial(second,journal,plan)['error']=='guide_trial_previous_result_unknown'
    assert not second.calls


def test_unexpected_between_step_change_stops_without_correcting_other_roles(tmp_path):
    plan,journal,port=setup(tmp_path,drift=True)
    assert execute_trial(port,journal,plan)['error']=='guide_control_check_state_changed'
    assert [n for n,c in port.calls if c]==['GTBattleStart'] and len(journal.events())==1


def test_exit_unknown_preserves_pending_and_completed_control_evidence(tmp_path):
    plan,journal,port=setup(tmp_path,exit_unknown=True)
    assert execute_trial(port,journal,plan)['error']=='exit_unknown'
    assert len(journal.events())==1
    assert len(json.loads((tmp_path/'control-check-readings.json').read_text(encoding='utf-8'))['readings'])==3
    second=CheckPort(tmp_path)
    report=execute_trial(second,journal,plan)
    assert report['error']=='guide_trial_previous_result_unknown'
    assert report['control_check_observation']['battle_started'] is False
    assert not second.calls


@pytest.mark.parametrize('failed_read',[1,2])
def test_later_read_failure_keeps_only_already_verified_steps(tmp_path,failed_read):
    plan,journal,port=setup(tmp_path)
    original_run=port.run;reads=0
    def run(node,*,click=False):
        nonlocal reads
        if node=='GTBattleReadSettings':
            reads+=1
            if reads==failed_read:raise RuntimeError('guide_trial_page_unknown:GTBattleReadSettings')
        return original_run(node,click=click)
    port.run=run
    report=execute_trial(port,journal,plan)
    assert report['error']=='guide_trial_page_unknown:GTBattleReadSettings'
    evidence=json.loads((tmp_path/'control-check-readings.json').read_text(encoding='utf-8'))
    assert [r['set_by_unit']['129701'] for r in evidence['readings']]==[True,False][:failed_read]
    assert all(r['auto'] for r in evidence['readings'])
    assert [n for n,c in port.calls if c]==['GTBattleStart']+['GTBattleToggleSet']*(failed_read-1)
    assert len(journal.events())==1 and not port.returned


@pytest.mark.parametrize('mutate',[
    lambda v:v.update(unit_id=122601), lambda v:v.update(steps=[True,False]),
    lambda v:v.update(steps=[0,1]),lambda v:v.update(max_new_starts=2),lambda v:v.update(auto=False),
    lambda v:v.update(created_at=0),lambda v:v.update(ledger_sha256='f'*64),
    lambda v:v['plan'].update(account_id='987654321'),lambda v:v['confirmation'].update(source='draft_import'),
])
def test_request_tampering_never_prepares_or_starts(tmp_path,mutate):
    plan,journal,port=setup(tmp_path)
    request=next(journal.directory.glob('control-check-*.json'))
    value=json.loads(request.read_text(encoding='utf-8'));mutate(value)
    request.write_text(json.dumps(value),encoding='utf-8')
    assert execute_trial(port,journal,plan)['error']=='guide_control_check_request_invalid'
    assert not port.calls and journal.events()==[]


@pytest.mark.parametrize('mutate',[
    lambda r:r.update(result_verified=True),lambda r:r.update(outcome='victory'),
    lambda r:r['control_readings'][1].update(actions=[]),
    lambda r:r['control_readings'][2].update(auto=False),
    lambda r:r['exit_evidence'].update(account_id='987654321'),
    lambda r:r['exit_evidence'].update(stage='红焰深域 8-4'),
])
def test_false_evidence_does_not_unlock_history(tmp_path,mutate):
    plan,journal,port=setup(tmp_path);execute_trial(port,journal,plan)
    intent,event=journal.events();mutate(event['result_evidence'])
    assert not is_control_check_exit(event,intent)
    assert not result_matches(event['result_evidence'],intent['control_check'],None)


def test_new_plan_keeps_total_attempt_budget_after_control_exit(tmp_path):
    plan,journal,port=setup(tmp_path);assert execute_trial(port,journal,plan)['attempts_total']==1
    plan['source_hash']='c'*64
    for attempt in (2,3):
        normal=SimulatedMaa(tmp_path,result='GTDefeat');report=execute_trial(normal,journal,plan)
        assert report['outcome']=='defeat' and report['attempts_total']==attempt
        assert sum(n=='GTBattleStart' for n,_ in normal.calls)==1
    assert len(journal.events())==6
    plan['source_hash']='d'*64; last=CheckPort(tmp_path)
    assert execute_trial(last,journal,plan)['outcome']=='attempt_limit' and not last.calls


def test_no_request_keeps_normal_trial_path(tmp_path):
    plan=candidate(31,'红焰深域 8-5');port=SimulatedMaa(tmp_path,auto=True,initial=[True]*5)
    assert execute_trial(port,Journal(tmp_path/'ledger'),plan)['outcome']=='victory'
    assert [n for n,c in port.calls if c]==['GTBattleStart']


def test_new_explicit_control_request_preserves_original_claim_and_count(tmp_path):
    plan,journal,port=setup(tmp_path)
    assert execute_trial(port,journal,plan)['attempts_total']==1
    old={p:p.read_bytes() for p in journal.directory.glob('control-check-*.json')}
    authorize_control_check(journal,plan,129701,'User explicitly allows the second control check only.')
    second=CheckPort(tmp_path);report=execute_trial(second,journal,plan)
    assert report['outcome']=='control_checked_exit' and report['attempts_total']==2
    assert sum(n=='GTBattleStart' for n,c in second.calls)==1
    assert all(p.read_bytes()==data for p,data in old.items())
    assert execute_trial(CheckPort(tmp_path),journal,plan)['error']=='guide_control_check_already_claimed'


@pytest.mark.parametrize('mask',range(32))
@pytest.mark.parametrize('order',[(0,1,2,3,4),(4,1,3,0,2)])
def test_opening_only_corrects_mismatches_once_then_exits(tmp_path,mask,order):
    plan=candidate(31,'红焰深域 8-6'); journal=Journal(tmp_path/'ledger')
    authorize_control_check(journal,plan,129701,'Only correct the opening settings and exit once.',opening_only=True)
    port=CheckPort(tmp_path,order=order); port.initial=[bool(mask&(1<<i)) for i in range(5)]
    report=execute_trial(port,journal,plan)
    assert report['outcome']=='control_checked_exit' and report['attempts_total']==1
    intent,event=journal.events(); assert is_control_check_exit(event,intent)
    readings=event['result_evidence']['control_readings']; assert len(readings)==1
    actions=readings[0]['actions']
    if mask==0:
        assert [a['node'] for a in actions]==['GTBattleEnableAllSet']
    else:
        expected=[plan['unit_ids'][order[i]] for i in range(5) if not mask&(1<<i)]
        assert actions==[dict(node='GTBattleToggleSet',unit_id=uid,previous=False,target=True) for uid in expected]
    assert all(port.state['sets']) and port.state['auto'] and port.returned
    assert [n for n,c in port.calls if c]==['GTBattleStart']+[a['node'] for a in actions]+['exit_control_check']
    assert not any(n=='GTBattleReadSettings' for n,_ in port.calls)
    assert execute_trial(CheckPort(tmp_path),journal,plan)['error']=='guide_control_check_already_claimed'


@pytest.mark.parametrize('fault',['no_effect','auto_lost','identity_changed'])
def test_opening_only_unknown_never_pauses_or_exits(tmp_path,fault):
    plan=candidate(31,'红焰深域 8-6'); journal=Journal(tmp_path/'ledger')
    authorize_control_check(journal,plan,129701,'Opening correction and exit.',opening_only=True)
    port=CheckPort(tmp_path,order=(0,1,2,3,4),fault=fault);port.initial=[True,False,True,True,True]
    report=execute_trial(port,journal,plan)
    assert report['error']=='guide_trial_settings_readback_unknown' and len(journal.events())==1
    assert [n for n,c in port.calls if c]==['GTBattleStart','GTBattleToggleSet']
    assert not port.returned


def test_opening_only_request_cannot_add_forced_steps(tmp_path):
    plan=candidate(31,'红焰深域 8-6'); journal=Journal(tmp_path/'ledger')
    authorize_control_check(journal,plan,129701,'Opening correction and exit.',opening_only=True)
    path=next(journal.directory.glob('control-check-*.json'))
    value=json.loads(path.read_text(encoding='utf-8'));value['steps']=[False,True]
    path.write_text(json.dumps(value),encoding='utf-8')
    port=CheckPort(tmp_path)
    assert execute_trial(port,journal,plan)['error']=='guide_control_check_request_invalid'
    assert not port.calls and not journal.events()


@pytest.mark.parametrize('unknown',['paused_team','dialog','map'])
def test_exit_requires_paused_identity_dialog_and_final_map(tmp_path,unknown):
    from types import SimpleNamespace as NS
    from pcr_maa.guide_trial_native import NativeGuideTrial
    port=CheckPort(tmp_path);plan=candidate();port.prepare(plan)
    port.tasker.controller=NS(cached_image=None)
    port.characters.read_paused=lambda _: [dict(unit_id=uid) for uid in (plan['unit_ids'] if unknown!='paused_team' else [1]*5)]
    def wait(names,**_):
        if names==['GTPaused']:return 'GTPaused'
        if unknown=='dialog':raise RuntimeError('dialog_unknown')
        if names[0]=='GTAbandonDialog':return 'GTAbandonDialog'
        raise RuntimeError('map_unknown')
    port.wait=wait
    with pytest.raises(RuntimeError,match={'paused_team':'exit_team_unknown','dialog':'dialog_unknown','map':'map_unknown'}[unknown]):
        NativeGuideTrial.exit_control_check(port,plan)
    expected=['GTPauseBattle']+([] if unknown=='paused_team' else ['GTAbandonBattle'])+(['GTAbandonConfirm'] if unknown=='map' else [])
    assert [n for n,c in port.calls if c]==expected


def stopped_before_start(path):
    plan=candidate(31,'红焰深域 8-6'); journal=Journal(path/'ledger')
    authorize_control_check(journal,plan,129701,'One opening check and exit.',opening_only=True)
    port=CheckPort(path)
    def fail(_):raise RuntimeError('map_not_recognized')
    port.prepare=fail
    report=execute_trial(port,journal,plan)
    assert report['attempts_total']==0 and not report['consumption_sent'] and not journal.events()
    return plan,journal,report


def test_explicit_prestart_continuation_preserves_claim_and_starts_once(tmp_path):
    from pcr_maa.guide_trial_control_check import authorize_control_continuation
    plan,journal,stopped=stopped_before_start(tmp_path)
    old={p:p.read_bytes() for p in journal.directory.glob('control-check-*.json')}
    assert execute_trial(CheckPort(tmp_path),journal,plan)['error']=='guide_control_check_already_claimed'
    authorize_control_continuation(journal,plan,stopped,'Continue the same authorized opening check and exit.')
    port=CheckPort(tmp_path);port.initial=[True,False,True,True,True]
    report=execute_trial(port,journal,plan)
    assert report['outcome']=='control_checked_exit' and report['attempts_total']==1
    assert [n for n,c in port.calls if c]==['GTBattleStart','GTBattleToggleSet','exit_control_check']
    assert report['battles'][0]['control_readings'][0]['actions'][0]['target'] is True
    assert all(p.read_bytes()==raw for p,raw in old.items())
    assert execute_trial(CheckPort(tmp_path),journal,plan)['error']=='guide_control_check_already_claimed'


@pytest.mark.parametrize('field,value',[('consumption_sent',True),('attempts_used',1),
    ('attempts_total',1),('attempts_limit',4),('stage','红焰深域 8-5'),('outcome','victory'),('battles',[{}])])
def test_prestart_continuation_rejects_incompatible_stop(tmp_path,field,value):
    from pcr_maa.guide_trial_control_check import authorize_control_continuation
    plan,journal,stopped=stopped_before_start(tmp_path);stopped[field]=value
    with pytest.raises(ValueError):authorize_control_continuation(journal,plan,stopped,'Continue.')
    assert not list(journal.directory.glob('*-continue.json')) and not journal.events()


def test_prestart_continuation_is_consumed_even_if_preparation_fails_again(tmp_path):
    from pcr_maa.guide_trial_control_check import authorize_control_continuation
    plan,journal,stopped=stopped_before_start(tmp_path)
    authorize_control_continuation(journal,plan,stopped,'Continue once.')
    port=CheckPort(tmp_path)
    def fail(_):raise RuntimeError('still_unknown')
    port.prepare=fail
    assert execute_trial(port,journal,plan)['error']=='still_unknown'
    blocked=CheckPort(tmp_path)
    assert execute_trial(blocked,journal,plan)['error']=='guide_control_check_already_claimed'
    assert not blocked.calls and not journal.events()


@pytest.mark.parametrize('change',['plan','claim','ledger','confirmation','expired'])
def test_prestart_continuation_revalidates_all_bindings_before_start(tmp_path,change):
    from pcr_maa.guide_trial_control_check import authorize_control_continuation,claim_control_check
    plan,journal,stopped=stopped_before_start(tmp_path)
    authorize_control_continuation(journal,plan,stopped,'Continue once.')
    path=next(journal.directory.glob('*-continue.json'));value=json.loads(path.read_text(encoding='utf-8'))
    if change=='plan':value['plan']['unit_ids'][0]=104701
    elif change=='claim':value['claim_sha256']='0'*64
    elif change=='ledger':journal.append(dict(state='in_flight',stage='红焰深域 8-4',account_id=plan['account_id']))
    elif change=='confirmation':value['confirmation']['text']=''
    else:value['created_at']=0
    path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises((ValueError,RuntimeError)):claim_control_check(journal,plan)
    assert not list(journal.directory.glob('*-continue-claimed.json'))


def pending_control_exit(path):
    plan=candidate(31,'红焰深域 8-6');journal=Journal(path/'ledger')
    authorize_control_check(journal,plan,129701,'Opening and exit once.',opening_only=True)
    port=CheckPort(path,exit_unknown=True)
    assert execute_trial(port,journal,plan)['error']=='exit_unknown'
    frame=path/'dialog.png';frame.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    return plan,journal,frame,path/'control-check-readings.json'


def test_same_control_exit_recovers_original_intent_without_start_or_set(tmp_path):
    from pcr_maa.guide_trial_control_check import authorize_control_exit
    plan,journal,frame,readings=pending_control_exit(tmp_path)
    old={p:p.read_bytes() for p in journal.directory.iterdir() if p.is_file()}
    authorize_control_exit(journal,plan,frame,readings,'No manual intervention; finish the same exit.')
    assert all(p.read_bytes()==data for p,data in old.items())
    port=CheckPort(tmp_path);report=execute_trial(port,journal,plan)
    assert report['outcome']=='control_checked_exit' and report['attempts_total']==1
    assert report['opening_evidence_reused'] and not report['consumption_sent'] and not report['settlement_verified']
    assert port.calls==[('recover_control_exit',True)] and port.returned
    events=journal.events();assert len(events)==2 and is_control_check_exit(events[1],events[0])
    assert journal.path.read_bytes().startswith(old[journal.path])
    assert execute_trial(CheckPort(tmp_path),journal,plan)['error']=='guide_control_check_already_claimed'


@pytest.mark.parametrize('change',['plan','account','frame','readings','ledger','confirmation','expired'])
def test_control_exit_binding_changed_stops_before_any_game_input(tmp_path,change):
    from pcr_maa.guide_trial_control_check import authorize_control_exit
    plan,journal,frame,readings=pending_control_exit(tmp_path)
    authorize_control_exit(journal,plan,frame,readings,'No manual intervention.')
    path=next(journal.directory.glob('control-exit-*-authorization.json'))
    value=json.loads(path.read_text(encoding='utf-8'))
    if change=='plan':plan['source_hash']='f'*64
    elif change=='account':plan['account_id']='987654321'
    elif change=='frame':next(journal.directory.glob('control-exit-*-reference.png')).write_bytes(b'changed')
    elif change=='readings':next(journal.directory.glob('control-exit-*-readings.json')).write_text('{}',encoding='utf-8')
    elif change=='ledger':value['ledger_sha256']='0'*64
    elif change=='confirmation':value['confirmation']['no_manual_intervention']=False
    else:value['created_at']=0
    path.write_text(json.dumps(value),encoding='utf-8');before=journal.path.read_bytes()
    port=CheckPort(tmp_path);report=execute_trial(port,journal,plan)
    assert report['outcome']=='stopped' and 'error' in report and not port.calls
    assert journal.path.read_bytes()==before and not list(journal.directory.glob('control-exit-*-claimed.json'))


@pytest.mark.parametrize('change',['missing_reading','auto','sets','request','plan_sets'])
def test_incomplete_opening_evidence_cannot_authorize_exit(tmp_path,change):
    from pcr_maa.guide_trial_control_check import authorize_control_exit
    plan,journal,frame,readings=pending_control_exit(tmp_path)
    value=json.loads(readings.read_text(encoding='utf-8'))
    if change=='missing_reading':value['readings']=[]
    elif change=='auto':value['readings'][0]['auto']=False
    elif change=='sets':value['readings'][0]['set_by_unit']['129701']=False
    elif change=='request':value['check']['request_sha256']='0'*64
    else:plan['set_by_unit']['129701']=False
    readings.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(ValueError):authorize_control_exit(journal,plan,frame,readings,'No manual intervention.')
    assert not list(journal.directory.glob('control-exit-*')) and len(journal.events())==1


def test_control_exit_failed_readback_remains_pending_without_retry(tmp_path):
    from pcr_maa.guide_trial_control_check import authorize_control_exit
    plan,journal,frame,readings=pending_control_exit(tmp_path)
    authorize_control_exit(journal,plan,frame,readings,'No manual intervention.')
    before=journal.path.read_bytes()
    assert execute_trial(CheckPort(tmp_path,exit_unknown=True),journal,plan)['error']=='exit_unknown'
    second=CheckPort(tmp_path)
    assert execute_trial(second,journal,plan)['error']=='guide_trial_previous_result_unknown'
    assert not second.calls and journal.path.read_bytes()==before
