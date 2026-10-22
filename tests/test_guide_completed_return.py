"""Single-use return requests use isolated journals; no game/controller input."""
import hashlib
import json
from pathlib import Path
import pytest
from test_guide_opening import candidate
from test_guide_trial import Port
from pcr_maa.guide_trial import execute_trial
from pcr_maa.guide_trial_resume import claim_completed_return
from pcr_maa.preset_execution import Journal


class CompletedPort(Port):
    def __init__(self, *, stop=False):
        super().__init__(stop=stop)
        self.finished = 0

    def finish_completed_return(self, plan):
        self.checkpoint()
        self.finished += 1


def completed(tmp_path):
    plan = candidate(31, stage='红焰深域 8-6')
    journal = Journal(tmp_path/'ledger')
    journal.directory.mkdir()
    for attempt in range(1, 4):
        intent = dict(state='in_flight', operation='guide_trial', intent_id=f'{attempt:032x}',
            account_id=plan['account_id'], stage=plan['stage'], attempt=attempt,
            source_hash=plan['source_hash'], at=attempt)
        journal.append(intent)
        journal.append(dict(intent, state='settled', outcome='victory' if attempt == 3 else 'defeat',
            result_verified=True))
    return journal, plan


def request_return(journal, plan, *, legacy=False):
    result = journal.events()[-1]
    request = dict(intent_id=result['intent_id'], account_id=plan['account_id'],
        source_hash=result['source_hash'], ledger_sha256=hashlib.sha256(journal.path.read_bytes()).hexdigest(),
        confirmation=dict(source='direct_user_message', text='Return only; preserve victory and 3/3.'))
    prefix = 'completed-return' if legacy else 'completed-return-'+result['intent_id']
    path = journal.directory/(prefix+'-request.json')
    path.write_text(json.dumps(request), encoding='utf-8')
    return path


def old_return_files(journal, intent_id='a'*32):
    files = {'completed-return-request.json': b'preserve historical request',
             'completed-return-claimed.json': json.dumps(dict(intent_id=intent_id)).encode()}
    for name, raw in files.items():
        (journal.directory/name).write_bytes(raw)
    return files


def test_old_claim_for_other_intent_does_not_block_new_victory_return(tmp_path):
    journal, plan = completed(tmp_path)
    historical = old_return_files(journal)
    request = request_return(journal, plan)
    before = journal.path.read_bytes()
    port = CompletedPort()
    result = execute_trial(port, journal, plan)
    assert result['outcome'] == 'already_cleared' and result['settlement_verified']
    assert result['stage_return_verified'] and result['attempts_used'] == result['attempts_total'] == 3
    assert port.finished == 1 and port.starts == port.prepared == 0 and not result['consumption_sent']
    assert journal.path.read_bytes() == before
    assert (journal.directory/('completed-return-'+journal.events()[-1]['intent_id']+'-claimed.json')).exists()
    for name, raw in historical.items():
        assert (journal.directory/name).read_bytes() == raw
    assert execute_trial(port, journal, plan)['outcome'] == 'already_cleared' and port.finished == 1
    request.unlink()  # A missing original request cannot bypass the intent's claim through the legacy path.
    request_return(journal, plan, legacy=True)
    (journal.directory/'completed-return-claimed.json').unlink()
    assert execute_trial(port, journal, plan)['outcome'] == 'already_cleared' and port.finished == 1


@pytest.mark.parametrize('legacy', [False, True])
def test_same_intent_legacy_claim_blocks_request_reissue(tmp_path, legacy):
    journal, plan = completed(tmp_path)
    old_return_files(journal, journal.events()[-1]['intent_id'])
    request_return(journal, plan, legacy=legacy)
    before = journal.path.read_bytes()
    port = CompletedPort()
    assert execute_trial(port, journal, plan)['outcome'] == 'already_cleared'
    assert port.finished == port.starts == port.prepared == 0 and journal.path.read_bytes() == before


@pytest.mark.parametrize('change', ['request_account', 'result_account', 'intent', 'plan', 'stage',
    'ledger', 'not_latest', 'state', 'outcome', 'unverified', 'confirmation'])
def test_changed_return_binding_rejected_before_port(tmp_path, change):
    journal, plan = completed(tmp_path)
    path = request_return(journal, plan)
    result = journal.events()[-1]
    request = json.loads(path.read_text(encoding='utf-8'))
    if change == 'request_account': request['account_id'] = '987654321'
    elif change == 'result_account': plan['account_id'] = request['account_id'] = '987654321'
    elif change == 'intent': request['intent_id'] = 'b'*32
    elif change == 'plan': plan['source_hash'] = 'c'*64
    elif change == 'stage': plan['stage'] = '红焰深域 8-5'
    elif change == 'ledger': request['ledger_sha256'] = 'd'*64
    elif change == 'not_latest':
        result = journal.events()[1]
        request['intent_id'] = result['intent_id']
        path = path.rename(journal.directory/('completed-return-'+result['intent_id']+'-request.json'))
    elif change == 'state': result['state'] = 'in_flight'
    elif change == 'outcome': result['outcome'] = 'defeat'
    elif change == 'unverified': result['result_verified'] = False
    else: request['confirmation']['source'] = 'model_assumption'
    path.write_text(json.dumps(request), encoding='utf-8')
    before = journal.path.read_bytes()
    with pytest.raises(ValueError, match='guide_trial_return_request_invalid'):
        claim_completed_return(journal, result, plan)
    assert journal.path.read_bytes() == before and not list(journal.directory.glob('*-claimed.json'))


def test_stop_consumes_return_request_without_new_battle_or_ledger_change(tmp_path):
    journal, plan = completed(tmp_path)
    request_return(journal, plan)
    before = journal.path.read_bytes()
    port = CompletedPort(stop=True)
    result = execute_trial(port, journal, plan)
    assert result['outcome'] == 'already_cleared' and result['error'] == 'stopped'
    assert result['settlement_verified'] and result['attempts_used'] == 3
    assert port.finished == port.starts == port.prepared == 0 and journal.path.read_bytes() == before
    again = CompletedPort()
    assert execute_trial(again, journal, plan)['outcome'] == 'already_cleared' and again.finished == 0


def test_saved_32_event_journal_keeps_86_victory_and_three_attempts(tmp_path):
    root = Path(__file__).resolve().parents[1]
    original = root/'.runtime/guide-acquisition-20261018/candidate/player/.runtime/guide-trial-work/ledger'
    if not (original/'transactions.jsonl').exists():
        pytest.skip('Private saved journal is not distributed')
    names = ('transactions.jsonl', 'completed-return-request.json', 'completed-return-claimed.json')
    before = {name: (original/name).read_bytes() for name in names}
    journal = Journal(tmp_path/'ledger')
    journal.directory.mkdir()
    for name, raw in before.items():
        (journal.directory/name).write_bytes(raw)
    events = journal.events()
    result = events[-1]
    assert len(events) == 32 and result['intent_id'] == '533c94d074f849708fc9d652cbe20522'
    assert result['outcome'] == 'victory' and result['result_verified'] and result['attempt'] == 3
    plan = candidate(31, stage='红焰深域 8-6')
    plan.update(account_id=result['account_id'], source_hash=result['source_hash'])
    request_return(journal, plan)
    port = CompletedPort()
    report = execute_trial(port, journal, plan)
    assert report['outcome'] == 'already_cleared' and report['settlement_verified']
    assert report['stage_return_verified'] and report['attempts_used'] == report['attempts_total'] == 3
    assert port.finished == 1 and port.starts == port.prepared == 0 and not report['consumption_sent']
    assert journal.path.read_bytes() == before['transactions.jsonl']
    for name, raw in before.items():
        assert (original/name).read_bytes() == raw and (journal.directory/name).read_bytes() == raw
