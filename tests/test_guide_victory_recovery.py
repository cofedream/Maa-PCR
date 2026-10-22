import json
import pytest
from test_guide_trial import Port, plan
from test_guide_second_stage import second_plan
from pcr_maa.guide_trial import execute_trial
from pcr_maa.guide_trial_resume import authorize_victory_recovery
from pcr_maa.preset_execution import Journal


class RecoveryPort(Port):
    def __init__(self, result='victory', *, return_failure=False, stopped=False):
        super().__init__()
        self.result, self.return_failure, self.stopped = result, return_failure, stopped
        self.recovered = 0

    def recover_victory(self, p, recovery):
        self.recovered += 1
        if self.stopped:
            raise RuntimeError('stopped')
        return dict(outcome=self.result, result_verified=self.result != 'unknown', settings_verified=False)

    def return_to_stage(self):
        if self.return_failure:
            raise RuntimeError('return_unknown')
        super().return_to_stage()


def pending(tmp_path):
    journal=Journal(tmp_path/'ledger')
    execute_trial(Port(),journal,plan())
    execute_trial(Port(('unknown',)),journal,second_plan())
    frame=tmp_path/'frame.png';frame.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    return journal,frame


@pytest.mark.parametrize('return_failure',[False,True])
def test_recovery_appends_original_intent_and_never_opens_battle(tmp_path,return_failure):
    journal,frame=pending(tmp_path); before=journal.path.read_bytes(); intent=journal.events()[-1]
    authorize_victory_recovery(journal,second_plan(),frame,'Settle this same victory; no new battle.')
    port=RecoveryPort(return_failure=return_failure)
    result=execute_trial(port,journal,second_plan())
    assert result['outcome']=='victory' and result['settlement_verified']
    assert result['attempts_used']==1 and result['consumption_sent'] is False
    assert bool(result.get('stage_return_verified')) is not return_failure
    assert port.recovered==1 and port.prepared==port.starts==0
    assert journal.path.read_bytes().startswith(before) and len(journal.events())==4
    assert journal.events()[-1]['intent_id']==intent['intent_id']
    assert journal.events()[-1]['result_evidence']['settings_verified'] is False
    for p in (second_plan(),dict(second_plan(),source_hash='c'*64),plan()):
        again=RecoveryPort(); assert execute_trial(again,journal,p)['outcome']=='already_cleared'
        assert again.recovered==again.prepared==again.starts==0


@pytest.mark.parametrize('result,stopped',[('unknown',False),('defeat',False),('victory',True)])
def test_recovery_failure_never_retries_or_changes_ledger(tmp_path,result,stopped):
    journal,frame=pending(tmp_path);before=journal.path.read_bytes()
    authorize_victory_recovery(journal,second_plan(),frame,'Read same victory only.')
    port=RecoveryPort(result,stopped=stopped)
    report=execute_trial(port,journal,second_plan())
    assert report['outcome']=='stopped' and not report['settlement_verified']
    assert journal.path.read_bytes()==before and not port.returned
    again=RecoveryPort()
    assert execute_trial(again,journal,second_plan())['error']=='guide_trial_previous_result_unknown'
    assert again.recovered==again.prepared==again.starts==0
    with pytest.raises(FileExistsError):
        authorize_victory_recovery(journal,second_plan(),frame,'Do not recreate this request.')


@pytest.mark.parametrize('change',['plan','stage','ledger','reference','confirmation','intent'])
def test_changed_recovery_binding_rejected_before_native_port(tmp_path,change):
    journal,frame=pending(tmp_path)
    authorize_victory_recovery(journal,second_plan(),frame,'Same victory.')
    prefix='victory-recovery-'+journal.events()[-1]['intent_id']
    p=second_plan();path=journal.directory/(prefix+'-authorization.json')
    if change=='plan':p['source_hash']='c'*64
    elif change=='stage':p=plan()
    elif change=='ledger':journal.path.write_bytes(journal.path.read_bytes()+b' ')
    elif change=='reference':(journal.directory/(prefix+'-reference.png')).write_bytes(b'changed')
    else:
        value=json.loads(path.read_text(encoding='utf-8'))
        if change=='confirmation':value['confirmation']['no_new_battle']=False
        else:value['intent_id']='b'*32
        path.write_text(json.dumps(value),encoding='utf-8')
    port=RecoveryPort()
    with pytest.raises(ValueError):execute_trial(port,journal,p)
    assert port.recovered==port.prepared==port.starts==0


def test_mixed_team_second_attempt_preserves_prior_recovery_files(tmp_path):
    from test_guide_opening import candidate,SimulatedMaa
    p=candidate();journal=Journal(tmp_path/'ledger')
    class OneDefeat(SimulatedMaa):
        def prepare_retry(self,plan):raise RuntimeError('fixture_stops_after_first_defeat')
    first=execute_trial(OneDefeat(tmp_path,result='GTDefeat'),journal,p)
    assert first['attempts_used']==1 and journal.events()[-1]['outcome']=='defeat'
    stopped=execute_trial(SimulatedMaa(tmp_path,auto=True,initial=[True]*5,fault='no_effect'),journal,p)
    assert stopped['attempts_used']==2 and len(journal.events())==3
    legacy={name:b'preserved historical proof' for name in ('victory-recovery-authorization.json','victory-recovery-reference.png','victory-recovery-claimed.json')}
    legacy['victory-recovery-claimed.json']=json.dumps(dict(intent_id='a'*32)).encode()
    for name,data in legacy.items():(journal.directory/name).write_bytes(data)
    frame=tmp_path/'frame.png';frame.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    before=journal.path.read_bytes();intent=journal.events()[-1]
    authorize_victory_recovery(journal,p,frame,'User confirms no operation; settle same observed WIN only.')
    port=RecoveryPort();result=execute_trial(port,journal,p)
    assert result['outcome']=='victory' and result['attempts_used']==2 and result['stage_return_verified']
    assert port.recovered==1 and port.starts==port.prepared==0
    assert journal.path.read_bytes().startswith(before) and len(journal.events())==4
    assert journal.events()[-1]['intent_id']==intent['intent_id']
    assert journal.events()[-1]['result_evidence']['settings_verified'] is False
    for name,data in legacy.items():assert (journal.directory/name).read_bytes()==data
    assert execute_trial(RecoveryPort(),journal,p)['outcome']=='already_cleared'


def test_legacy_unclaimed_recovery_still_works(tmp_path):
    journal,frame=pending(tmp_path)
    authorize_victory_recovery(journal,second_plan(),frame,'Same historical victory.')
    prefix='victory-recovery-'+journal.events()[-1]['intent_id']
    for suffix in ('-authorization.json','-reference.png'):
        (journal.directory/(prefix+suffix)).rename(journal.directory/('victory-recovery'+suffix))
    port=RecoveryPort();result=execute_trial(port,journal,second_plan())
    assert result['outcome']=='victory' and port.starts==port.prepared==0
    assert (journal.directory/'victory-recovery-claimed.json').exists()


def test_claimed_legacy_intent_cannot_get_a_new_request(tmp_path):
    journal,frame=pending(tmp_path)
    claimed=journal.directory/'victory-recovery-claimed.json'
    claimed.write_text(json.dumps(dict(intent_id=journal.events()[-1]['intent_id'])),encoding='utf-8')
    with pytest.raises(FileExistsError):
        authorize_victory_recovery(journal,second_plan(),frame,'Same old attempt cannot be reissued.')
    assert not list(journal.directory.glob('victory-recovery-*-authorization.json'))
    port=RecoveryPort()
    assert execute_trial(port,journal,second_plan())['error']=='guide_trial_previous_result_unknown'
    assert port.recovered==port.prepared==port.starts==0


@pytest.mark.parametrize('observed',['defeat','victory','unknown'])
def test_same_defeat_recovery_never_retries_or_invents_settings(tmp_path,observed):
    from pcr_maa.guide_trial_resume import authorize_result_recovery
    journal,frame=pending(tmp_path);before=journal.path.read_bytes();intent=journal.events()[-1]
    authorize_result_recovery(journal,second_plan(),frame,'No manual operation; settle this same defeat only.',outcome='defeat')
    port=RecoveryPort(observed);port.recover_result=port.recover_victory
    report=execute_trial(port,journal,second_plan())
    assert port.starts==port.prepared==0 and port.recovered==1 and report['attempts_total']==1
    if observed=='defeat':
        assert report['outcome']=='defeat' and report['settlement_verified'] and report['stage_return_verified']
        assert journal.path.read_bytes().startswith(before) and journal.events()[-1]['intent_id']==intent['intent_id']
        assert journal.events()[-1]['result_evidence']['settings_verified'] is False
    else:
        assert report['error']=='guide_trial_result_unknown' and journal.path.read_bytes()==before and not port.returned
