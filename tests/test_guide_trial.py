from copy import deepcopy
import pytest
from pcr_maa.guide_trial import TEAM, STARS, STAGE, execute_trial, validate_plan, record_user_exit
from pcr_maa.preset_execution import Journal
from pcr_maa.guide_identity import SOURCE_SHA256


def plan():
    return dict(version=2, server='cn-bilibili', stage=STAGE, account_id='123456789',
        unit_ids=list(TEAM), stars=list(STARS), all_set=True, auto=True, max_attempts=3, source_hash='a'*64,
        source_image_sha256=SOURCE_SHA256)


class Port:
    def __init__(self, results=('victory',), *, wrong=None, stop=False):
        self.results=iter(results); self.wrong=wrong; self.stop=stop; self.starts=0; self.prepared=0; self.returned=False
    def prepare(self, p): self.prepared += 1
    def checkpoint(self):
        if self.stop: raise RuntimeError('stopped')
    def verify_team(self, p):
        ready={k:deepcopy(p[k]) for k in ('stage','account_id','unit_ids','stars')}
        ready['settings_plan']={'all_set':True,'auto':True}
        if self.wrong: ready[self.wrong]=False
        return ready
    def start_and_read(self, p):
        self.starts += 1
        value=next(self.results)
        if value == 'exception': raise RuntimeError('connection_lost')
        return dict(outcome=value,result_verified=value in ('victory','defeat'))
    def prepare_retry(self, p): raise AssertionError('automatic_retry_is_not_allowed')
    def return_to_stage(self): self.returned=True


def test_win_stops_without_using_other_attempts(tmp_path):
    port=Port(); journal=Journal(tmp_path)
    r=execute_trial(port,journal,plan())
    assert r['outcome']=='victory' and port.starts==1 and port.returned
    again=Port()
    assert execute_trial(again,journal,plan())['outcome']=='already_cleared' and again.starts==0


def test_known_victory_survives_return_failure(tmp_path):
    port=Port(); journal=Journal(tmp_path)
    def fail(): raise RuntimeError('return_page_unknown')
    port.return_to_stage=fail
    result=execute_trial(port,journal,plan())
    assert result['outcome']=='victory' and result['settlement_verified']
    assert result['error']=='return_page_unknown' and port.starts==1
    assert execute_trial(Port(),journal,plan())['outcome']=='already_cleared'


def test_three_total_including_first_survive_new_plan(tmp_path):
    journal=Journal(tmp_path)
    for attempt in range(1,4):
        port=Port(('defeat','victory'))
        r=execute_trial(port,journal,plan())
        assert r['outcome']=='defeat' and port.starts==1 and r['attempts_used']==attempt and port.returned
        assert len(journal.events())==attempt*2
    changed=plan(); changed['source_hash']='b'*64
    again=Port()
    assert execute_trial(again,Journal(tmp_path),changed)['outcome']=='attempt_limit' and again.starts==0


@pytest.mark.parametrize('outcome',['unknown','exception','victory_but_unrecognized'])
def test_unknown_is_never_replayed(tmp_path,outcome):
    port=Port((outcome,)); journal=Journal(tmp_path)
    r=execute_trial(port,journal,plan())
    assert r['outcome']=='stopped' and port.starts==1 and not port.returned
    assert len(journal.events())==1
    again=Port()
    after=execute_trial(again,Journal(tmp_path),plan())
    assert after['error']=='guide_trial_previous_result_unknown' and after['attempts_used']==1
    assert again.starts==again.prepared==0


@pytest.mark.parametrize('outcome,return_failure', [('victory',False),('defeat',False),('defeat',True),('unknown',False)])
def test_one_battle_owner_report_reaches_player_notice(tmp_path,monkeypatch,outcome,return_failure):
    import json
    from pcr_maa.guide_trial import run_owned
    from pcr_maa.gui_contract import result_text
    port=Port((outcome,'victory'))
    if return_failure:
        def fail():raise RuntimeError('return_page_unknown')
        port.return_to_stage=fail
    monkeypatch.setattr('pcr_maa.native.ROOT',tmp_path)
    monkeypatch.setattr('pcr_maa.guide_trial_native.NativeGuideTrial',lambda *_:port)
    report=run_owned(None,{'guide_trial':plan()},tmp_path/'result')
    assert port.starts==1 and report['supported_chain_complete'] is (outcome!='unknown' and not return_failure)
    assert json.loads((tmp_path/'result/report.json').read_text(encoding='utf-8'))==report
    message=result_text(report)
    assert '累计尝试 1/3' in message
    if outcome=='defeat':
        assert '本次未通关' in message and '不自动重试' in message and '已通关' not in message
        assert ('返回地图尚未确认' if return_failure else '已返回地图') in message
    elif outcome=='unknown':
        assert '已停止' in message and '已通关' not in message and not port.returned
    else:assert '已通关' in message and '已返回地图' in message


def exit_evidence(intent):
    confirmation=dict(source='direct_user_message', exited=True, continue_remaining=True,
                      text='I exited this battle; continue with the remaining attempts.')
    evidence=dict(source='maa_observation', scene='GTFireMap', verified=True,
                  account_id=intent['account_id'], observed_at=intent['at'],
                  frame_sha256='1'*64, receipt_sha256='2'*64)
    return dict(intent_id=intent['intent_id'],confirmation=confirmation,evidence=evidence)


def test_explicit_exit_keeps_unknown_result_and_spent_attempt(tmp_path):
    journal=Journal(tmp_path)
    execute_trial(Port(('unknown',)),journal,plan())
    first=journal.events()[0]
    record_user_exit(journal,**exit_evidence(first))
    assert journal.events()[0] == first
    history=journal.events()[1]
    assert history['state']=='historical_unknown' and history['result_verified'] is False
    p=plan();p['source_hash']='c'*64
    port=Port(('defeat','defeat'))
    result=execute_trial(port,Journal(tmp_path),p)
    assert result['attempts_used']==2 and result['outcome']=='defeat' and port.starts==1
    result=execute_trial(port,Journal(tmp_path),p)
    assert result['attempts_used']==3 and result['outcome']=='defeat' and port.starts==2
    assert execute_trial(Port(),Journal(tmp_path),plan())['outcome']=='attempt_limit'


def test_exit_confirmation_does_not_authorize_future_unknowns(tmp_path):
    journal=Journal(tmp_path)
    execute_trial(Port(('unknown',)),journal,plan())
    args=exit_evidence(journal.events()[0]);record_user_exit(journal,**args)
    assert execute_trial(Port(('unknown',)),journal,plan())['attempts_used']==2
    with pytest.raises(ValueError,match='not_pending'): record_user_exit(journal,**args)
    port=Port()
    result=execute_trial(port,journal,plan())
    assert result['error']=='guide_trial_previous_result_unknown' and port.starts==port.prepared==0


@pytest.mark.parametrize('section,key,value',[
    ('confirmation','source','model_assumption'),('confirmation','exited',False),
    ('confirmation','continue_remaining',False),('confirmation','text',''),
    ('evidence','scene','GTPaused'),('evidence','verified',False),
    ('evidence','account_id','987654321'),('evidence','observed_at',0),
    ('evidence','frame_sha256','missing'),('evidence','receipt_sha256','missing')])
def test_exit_requires_user_confirmation_and_bound_nonbattle_evidence(tmp_path,section,key,value):
    journal=Journal(tmp_path);execute_trial(Port(('unknown',)),journal,plan())
    args=exit_evidence(journal.events()[0]);args[section][key]=value
    with pytest.raises(ValueError,match='evidence_invalid'):record_user_exit(journal,**args)
    assert len(journal.events())==1


def test_tampered_exit_binding_is_rejected_by_journal(tmp_path):
    journal=Journal(tmp_path);execute_trial(Port(('unknown',)),journal,plan())
    record_user_exit(journal,**exit_evidence(journal.events()[0]))
    import json
    values=journal.events();values[1]['attempt']=2
    journal.path.write_text('\n'.join(json.dumps(v) for v in values)+'\n',encoding='utf-8')
    with pytest.raises(ValueError,match='sequence invalid'): journal.events()


@pytest.mark.parametrize('field',['stage','account_id','unit_ids','stars','settings_plan'])
def test_bad_team_or_settings_cannot_start(tmp_path,field):
    port=Port(wrong=field); journal=Journal(tmp_path)
    assert execute_trial(port,journal,plan())['outcome']=='stopped'
    assert port.starts==0 and journal.events()==[]


def test_stop_before_start_preserves_budget(tmp_path):
    port=Port(stop=True); journal=Journal(tmp_path)
    assert execute_trial(port,journal,plan())['outcome']=='stopped'
    assert port.starts==0 and journal.events()==[]


def test_game_position_order_is_not_guide_listing_order(tmp_path):
    port=Port(); original=port.verify_team
    def reordered(p):
        value=original(p);value['unit_ids']=[134701,129701,116701,135501,104701];return value
    port.verify_team=reordered
    assert execute_trial(port,Journal(tmp_path),plan())['outcome']=='victory'


def test_defeat_returns_and_next_start_keeps_first_attempt(tmp_path):
    journal=Journal(tmp_path); port=Port(('defeat',))
    def stop(p): raise RuntimeError('stopped')
    port.prepare_retry=stop
    assert execute_trial(port,journal,plan())['attempts_used']==1
    again=Port(('defeat','victory'))
    assert execute_trial(again,Journal(tmp_path),plan())['attempts_used']==2 and again.starts==1
    assert execute_trial(again,Journal(tmp_path),plan())['attempts_used']==3 and again.starts==2


@pytest.mark.parametrize('key,value',[('max_attempts',4),('max_attempts',True),('stage','红焰深域 7-4'),
    ('all_set',False),('auto',False),('unit_ids',[104701]*5),('source_hash','broken'),
    ('version',1),('source_image_sha256','b'*64),('unit_ids',[129701,116701,122601,135501,104701])])
def test_invalid_plan_rejected(key,value):
    p=plan();p[key]=value
    with pytest.raises(ValueError):validate_plan(p)


def test_trial_selection_is_removed_from_formal_combinations():
    from pcr_maa.gui_contract import make_combination
    for modules,final in ((['guide_trial'],False),(['guide_trial','rewards'],False),(['guide_trial'],True)):
        with pytest.raises(ValueError,match='guide_trial_removed'):
            make_combination(modules,rewards_final=final)


def test_pi_trial_flag_is_rejected_by_agent_entry():
    import json
    from types import SimpleNamespace
    from pcr_maa.agent_entry import parse_selection
    def node(name):
        return dict(enabled=False,action=dict(type='DoNothing',param={}),next=[],on_error=[],
                    attach=dict(selected=name=='PCRAlphaSelectGuideTrial'))
    with pytest.raises(ValueError,match='guide_trial_removed'):
        parse_selection(json.dumps(dict(selection_mode='pi_nodes_v1',selected_modules=['guide_trial'])),
                        context=SimpleNamespace(get_node_data=node))


def wrong_team_history(journal):
    from uuid import uuid4
    for number in range(1,4):
        intent=dict(state='in_flight', operation='guide_trial', intent_id=uuid4().hex,
            account_id=plan()['account_id'], stage=STAGE, attempt=number, at=1,
            team_evidence=dict(unit_ids=[122601,129701,116701,135501,104701]))
        journal.append(intent)
        if number == 1:
            record_user_exit(journal,**exit_evidence(intent))
        else:
            journal.append(dict(intent,state='settled',outcome='defeat',result_verified=True))


def new_confirmation():
    return dict(source='direct_user_message',text='Correct Phoenix team: three additional attempts; keep history and stop if unknown.',
        preserve_history=True,stop_on_unknown=True)


def test_explicit_new_budget_keeps_history_and_cannot_reset(tmp_path):
    from pcr_maa.guide_trial_authorization import authorize_corrected_team
    journal=Journal(tmp_path);wrong_team_history(journal)
    before=journal.path.read_bytes()
    assert execute_trial(Port(),journal,plan())['outcome']=='attempt_limit'
    authorize_corrected_team(journal,plan(),new_confirmation())
    with pytest.raises(FileExistsError): authorize_corrected_team(journal,plan(),new_confirmation())
    for attempt in range(1,4):
        port=Port(('defeat',));result=execute_trial(port,journal,plan())
        assert port.starts==1 and result['attempts_used']==attempt and result['outcome']=='defeat'
    assert result['attempts_total']==6 and result['historical_attempts']==3
    assert journal.path.read_bytes().startswith(before)
    assert [e['attempt'] for e in journal.events()[::2]]==list(range(1,7))
    changed=plan();changed['source_hash']='c'*64
    again=Port();assert execute_trial(again,Journal(tmp_path),changed)['outcome']=='attempt_limit'
    assert again.prepared==again.starts==0
    with pytest.raises(ValueError):authorize_corrected_team(journal,plan(),new_confirmation())


def test_new_budget_unknown_stops_and_survives_restart(tmp_path):
    from pcr_maa.guide_trial_authorization import authorize_corrected_team
    journal=Journal(tmp_path);wrong_team_history(journal)
    authorize_corrected_team(journal,plan(),new_confirmation())
    first=Port(('unknown',));result=execute_trial(first,journal,plan())
    assert result['attempts_used']==first.starts==1 and result['attempts_total']==4
    again=Port();result=execute_trial(again,Journal(tmp_path),plan())
    assert result['error']=='guide_trial_previous_result_unknown' and again.prepared==again.starts==0


@pytest.mark.parametrize('field,value',[('source','inferred'),('text',''),('preserve_history',False),('stop_on_unknown',False)])
def test_new_budget_requires_explicit_complete_confirmation(tmp_path,field,value):
    from pcr_maa.guide_trial_authorization import authorize_corrected_team,FILE_NAME
    journal=Journal(tmp_path);wrong_team_history(journal)
    confirmation=new_confirmation();confirmation[field]=value
    with pytest.raises(ValueError):authorize_corrected_team(journal,plan(),confirmation)
    assert not (tmp_path/FILE_NAME).exists() and len(journal.events())==6


@pytest.mark.parametrize('field,value',[('additional_attempts',4),('account_id','987654321'),('source_image_sha256','0'*64),('history_sha256','0'*64)])
def test_tampered_budget_stops_before_prepare(tmp_path,field,value):
    import json
    from pcr_maa.guide_trial_authorization import authorize_corrected_team,FILE_NAME
    journal=Journal(tmp_path);wrong_team_history(journal)
    grant=authorize_corrected_team(journal,plan(),new_confirmation());grant[field]=value
    (tmp_path/FILE_NAME).write_text(json.dumps(grant),encoding='utf-8')
    port=Port()
    with pytest.raises(ValueError):execute_trial(port,journal,plan())
    assert port.prepared==port.starts==0


def test_changed_original_history_invalidates_new_budget(tmp_path):
    from pcr_maa.guide_trial_authorization import authorize_corrected_team
    journal=Journal(tmp_path);wrong_team_history(journal)
    authorize_corrected_team(journal,plan(),new_confirmation())
    journal.path.write_bytes(b' '+journal.path.read_bytes())
    port=Port()
    with pytest.raises(ValueError):execute_trial(port,journal,plan())
    assert port.prepared==port.starts==0


def test_incomplete_original_history_cannot_get_new_budget(tmp_path):
    from pcr_maa.guide_trial_authorization import authorize_corrected_team
    journal=Journal(tmp_path)
    with pytest.raises(ValueError):authorize_corrected_team(journal,plan(),new_confirmation())


def test_summary_distinguishes_additional_budget_from_preserved_history():
    from pcr_maa.supported_daily import summary
    result=dict(outcome='stopped',attempts_used=1,attempts_limit=3,authorization_id='a'*32,historical_attempts=3)
    report=dict(selected_modules=['guide_trial'],steps=[dict(name='guide_trial',status='stopped',result=result)])
    assert '本次授权已尝试 1/3' in summary(report) and '旧队伍 3 次记录保留' in summary(report)
    del result['authorization_id']
    result['historical_attempts']=0
    assert '累计尝试 1/3' in summary(report) and '旧队伍' not in summary(report)
