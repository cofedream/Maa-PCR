import json
import pytest
from test_guide_trial import Port, plan, wrong_team_history, new_confirmation
from pcr_maa.guide_trial import execute_trial
from pcr_maa.guide_trial_authorization import authorize_corrected_team
from pcr_maa.guide_trial_resume import authorize_resume, FILE_NAME
from pcr_maa.preset_execution import Journal


class ResumePort(Port):
    def __init__(self, results=('victory',), **kwargs):
        super().__init__(results, **kwargs)
        self.resumes = 0
    def prepare_resume(self, plan, resume):
        self.checkpoint()
    def resume_and_read(self, plan):
        self.resumes += 1
        value = next(self.results)
        return dict(outcome=value, result_verified=value in ('victory','defeat'))


def pending(tmp_path):
    journal = Journal(tmp_path/'ledger')
    journal.directory.mkdir()
    wrong_team_history(journal)
    authorize_corrected_team(journal, plan(), new_confirmation())
    execute_trial(Port(('unknown',)), journal, plan())
    frame = tmp_path/'frame.png'
    frame.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    return journal, frame


def test_resume_settles_original_intent_without_new_start(tmp_path):
    journal, frame = pending(tmp_path)
    before = journal.path.read_bytes()
    intent = journal.events()[-1]
    authorize_resume(journal, plan(), frame, 'Resume this same paused battle; keep 1/3.')
    port = ResumePort()
    result = execute_trial(port, journal, plan())
    assert result['outcome'] == 'victory' and result['attempts_used'] == 1
    assert result['attempts_total'] == 4 and port.starts == port.prepared == 0 and port.resumes == 1
    assert journal.path.read_bytes().startswith(before)
    assert len(journal.events()) == 8 and journal.events()[-1]['intent_id'] == intent['intent_id']
    assert execute_trial(Port(), journal, plan())['outcome'] == 'already_cleared'


def test_resumed_defeat_leaves_exactly_two_new_attempts(tmp_path):
    journal, frame = pending(tmp_path)
    authorize_resume(journal, plan(), frame, 'Resume; maximum three including this attempt.')
    port = ResumePort(('defeat',)*3)
    result = execute_trial(port, journal, plan())
    assert result['outcome'] == 'defeat' and result['attempts_used'] == 1 and port.resumes == 1 and port.starts == 0
    for attempt in (2,3):
        port=Port(('defeat',));result=execute_trial(port,journal,plan())
        assert result['outcome']=='defeat' and result['attempts_used']==attempt and port.starts==1
    assert len(journal.events()) == 12
    assert execute_trial(Port(), journal, plan())['outcome'] == 'attempt_limit'


@pytest.mark.parametrize('stop', [False,True])
def test_unknown_or_stop_consumes_resume_grant_not_attempt(tmp_path, stop):
    journal, frame = pending(tmp_path)
    before = journal.path.read_bytes()
    authorize_resume(journal, plan(), frame, 'Resume this paused battle once.')
    result = execute_trial(ResumePort(('unknown',),stop=stop), journal, plan())
    assert result['outcome'] == 'stopped' and result['attempts_used'] == 1
    again = ResumePort()
    assert execute_trial(again, journal, plan())['error'] == 'guide_trial_previous_result_unknown'
    assert again.starts == again.resumes == again.prepared == 0
    assert journal.path.read_bytes() == before


@pytest.mark.parametrize('tamper', ['plan','ledger','reference','confirmation','intent'])
def test_changed_binding_is_rejected_before_port(tmp_path, tamper):
    journal, frame = pending(tmp_path)
    authorize_resume(journal, plan(), frame, 'Resume the same paused battle.')
    p = plan()
    path = journal.directory/FILE_NAME
    if tamper == 'plan': p['source_hash'] = 'c'*64
    elif tamper == 'ledger': journal.path.write_bytes(journal.path.read_bytes()+b' ')
    elif tamper == 'reference': (journal.directory/'paused-resume-reference.png').write_bytes(b'changed')
    else:
        value = json.loads(path.read_text(encoding='utf-8'))
        if tamper == 'confirmation': value['confirmation']['same_battle'] = False
        else: value['intent_id'] = 'b'*32
        path.write_text(json.dumps(value),encoding='utf-8')
    port = ResumePort()
    with pytest.raises(ValueError): execute_trial(port, journal, p)
    assert port.starts == port.resumes == port.prepared == 0


def test_grant_not_recreated_after_claim(tmp_path):
    journal, frame = pending(tmp_path)
    authorize_resume(journal, plan(), frame, 'Resume once.')
    execute_trial(ResumePort(('unknown',)), journal, plan())
    with pytest.raises(FileExistsError): authorize_resume(journal, plan(), frame, 'Same message repeated.')


@pytest.mark.parametrize('fail', [False,True])
def test_completed_return_only_once_and_never_new_battle(tmp_path, fail):
    import hashlib
    journal=Journal(tmp_path);execute_trial(Port(),journal,plan())
    before=journal.path.read_bytes();result=journal.events()[-1]
    request=dict(intent_id=result['intent_id'],account_id=plan()['account_id'],source_hash=plan()['source_hash'],
        ledger_sha256=hashlib.sha256(before).hexdigest(),confirmation=dict(source='direct_user_message',text='Finish reward return, never replay.'))
    (tmp_path/'completed-return-request.json').write_text(json.dumps(request),encoding='utf-8')
    port=Port();calls=[]
    def finish(p):
        calls.append(p)
        if fail:raise RuntimeError('return_unknown')
    port.finish_completed_return=finish
    r=execute_trial(port,journal,plan())
    assert r['outcome']=='already_cleared' and r['settlement_verified'] and len(calls)==1
    assert bool(r.get('stage_return_verified')) is not fail
    assert execute_trial(port,journal,plan())['outcome']=='already_cleared' and len(calls)==1
    assert port.starts==port.prepared==0 and journal.path.read_bytes()==before


def test_completed_return_rejects_stale_ledger_before_port(tmp_path):
    journal=Journal(tmp_path);execute_trial(Port(),journal,plan())
    result=journal.events()[-1]
    request=dict(intent_id=result['intent_id'],account_id=plan()['account_id'],source_hash=plan()['source_hash'],
        ledger_sha256='a'*64,confirmation=dict(source='direct_user_message',text='Finish reward return.'))
    (tmp_path/'completed-return-request.json').write_text(json.dumps(request),encoding='utf-8')
    with pytest.raises(ValueError,match='return_request_invalid'):execute_trial(Port(),journal,plan())
