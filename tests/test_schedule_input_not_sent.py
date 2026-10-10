"""Synthetic schedule dispatch proofs; no controller, device, or real ledger."""
from types import SimpleNamespace

import pytest

from pcr_maa import native
from pcr_maa import schedule_house as house
import pcr_maa.schedule_onekey as onekey
from pcr_maa.preset_execution import Journal
from test_recognition_error_evidence import Resource

DAY = '2026-10-05'
DEVICE = dict(serial='127.0.0.1:16384', input_display=2, screenshot_display='123')


def success(name, task_id=1):
    return dict(native_ok=True, tasker_inited=True, task_id=task_id,
                nodes=[dict(name=name, completed=True, action=dict(name=name, success=True))])


def refusal(entry, task_id=99, **changes):
    proof = dict(source='native_task_events', complete=True, entry=entry, task_id=task_id,
                 started=True, finished=True, recognition_failed=True, action_started=False,
                 non_input_actions=[])
    proof.update(changes)
    return dict(native_ok=False, tasker_inited=True, task_id=task_id, nodes=[], native_evidence=proof)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(house, 'current_game_day', lambda: DAY)
    monkeypatch.setattr(onekey, 'now_cn', lambda: datetime.fromisoformat(DAY + 'T14:00:00+08:00'))


def make_house(monkeypatch, tmp_path, claim='not_sent', result_close=False):
    resource = Resource()
    tasker = SimpleNamespace(resource=resource)
    journal = Journal(tmp_path / 'house')
    calls = []
    state = 'ready'

    def run(tasker, entry):
        nonlocal state
        calls.append(entry)
        if entry == 'HouseRead':
            resource.callbacks['HousePage'].last = dict(status=state, reason=None, image_sha256='a' * 64)
            return success('HouseSnapshot')
        if entry == 'HouseClaim':
            if claim == 'not_sent':
                return refusal(entry)
            if claim == 'unknown':
                return refusal(entry, action_started=True)
            state = 'result'
            return success('HouseClaimClick')
        if entry == 'HouseResultClose':
            assert result_close
            return refusal(entry)
        raise AssertionError('Unexpected input or cleanup: ' + entry)

    monkeypatch.setattr(native, 'run', run)
    invoke = lambda: house.schedule_house(tasker, execute=True, game_day=DAY, device=DEVICE, journal=journal)
    return invoke, journal, calls


def test_house_not_sent_closes_only_current_intent_and_fresh_start_can_read(monkeypatch, tmp_path):
    invoke, journal, calls = make_house(monkeypatch, tmp_path)
    result = invoke()
    assert result['outcome'] == 'native_input_not_sent' and result['input_execution'] == 'not_sent'
    assert not result['schedule_executed'] and not result['result_verified']
    assert calls == ['HouseRead', 'HouseClaim']
    start, end = journal.events()
    assert end['state'] == 'settled' and end['input_execution'] == 'not_sent'
    assert end['intent_id'] == start['intent_id'] and end['native_evidence']['input_execution'] == 'not_sent'
    prefix = journal.path.read_bytes()
    assert invoke()['outcome'] == 'native_input_not_sent'
    assert calls == ['HouseRead', 'HouseClaim'] * 2
    assert len(journal.events()) == 4 and journal.path.read_bytes().startswith(prefix)


def test_house_unknown_prefix_stays_unchanged_and_cannot_resend(monkeypatch, tmp_path):
    invoke, journal, calls = make_house(monkeypatch, tmp_path, claim='unknown')
    assert invoke()['outcome'] == 'unknown'
    assert journal.events()[-1]['state'] == 'in_flight'
    prefix = journal.path.read_bytes()
    assert invoke()['reason'] == 'in_flight_parent_reconciliation_required'
    assert journal.path.read_bytes() == prefix and calls == ['HouseRead', 'HouseClaim']


def test_house_successful_same_day_history_still_blocks(monkeypatch, tmp_path):
    invoke, journal, calls = make_house(monkeypatch, tmp_path)
    with journal.locked():
        start = dict(state='in_flight', operation='schedule_house', game_day=DAY, device=DEVICE)
        journal.append(start)
        journal.append(dict(start, state='settled'))
    prefix = journal.path.read_bytes()
    assert invoke()['outcome'] == 'already_recorded_not_executed'
    assert not calls and journal.path.read_bytes() == prefix


@pytest.mark.parametrize('fault', ['proof', 'binding', 'missing_intent', 'wrong_entry'])
def test_house_unverified_not_sent_label_cannot_bypass_history(monkeypatch, tmp_path, fault):
    invoke, journal, calls = make_house(monkeypatch, tmp_path)
    with journal.locked():
        start = dict(state='in_flight', operation='schedule_house', game_day=DAY, device=DEVICE,
                     intent_id='a' * 32, image_sha256='a' * 64)
        receipt = refusal('OtherEntry' if fault == 'wrong_entry' else 'HouseClaim')
        receipt['input_execution'] = 'not_sent'
        if fault == 'proof':
            receipt = dict(input_execution='not_sent')
        if fault == 'missing_intent':
            start.pop('intent_id')
        terminal = dict(start, state='settled', input_execution='not_sent', consumption_sent=False,
                        settlement_verified=False, business_result_verified=False, native_evidence=receipt)
        if fault == 'binding':
            terminal['intent_id'] = 'b' * 32
        journal.append(start)
        journal.append(terminal)
    prefix = journal.path.read_bytes()
    result = invoke()
    assert result['outcome'] in ('unknown', 'already_recorded_not_executed')
    assert not calls and journal.path.read_bytes() == prefix


def test_house_result_close_refusal_cannot_close_consumer_as_not_sent(monkeypatch, tmp_path):
    invoke, journal, calls = make_house(monkeypatch, tmp_path, claim='success', result_close=True)
    result = invoke()
    assert result['outcome'] == 'unknown' and result['claim_click_completed'] and result['result_verified']
    assert result.get('input_execution') != 'not_sent'
    assert journal.events()[-1]['state'] == 'in_flight'
    assert calls == ['HouseRead', 'HouseClaim', 'HouseRead', 'HouseResultClose']


def make_onekey(monkeypatch, tmp_path, rejected, *, unknown=False):
    resource = Resource()
    tasker = SimpleNamespace(resource=resource)
    journal = Journal(tmp_path / 'onekey')
    calls = []
    state = 'home'
    sequence = 0

    def reading(reader):
        nonlocal sequence
        sequence += 1
        reader.capture_index = sequence
        detail = dict(state=state, reason=None, session_id=reader.session_id,
                      capture_index=sequence, captured_at=DAY + 'T14:00:00+08:00',
                      acquisition='current_tasker_capture', image_sha256=f'{sequence:064x}')
        if state == 'home':
            detail['resources'] = dict(ap=100, jewels=500)
        elif state == 'schedule':
            detail.update(rows={key: dict(state='pending' if key == 'mana_skip' else 'complete', y=index * 10)
                                for index, key in enumerate(onekey.ITEMS)},
                          sections={'delegated': dict(completed=8, total=9)}, bottom=True)
        elif state == 'mana_confirm':
            detail['quote'] = dict(times=2, remaining=2, tickets=2)
        return detail

    def run(tasker, entry):
        nonlocal state
        calls.append(entry)
        reader = resource.callbacks['OneKeyPage']
        reader.last = reading(reader)
        if entry == 'OneKeyExecRead':
            return success('OneKeyExecSnapshot', len(calls))
        base = entry.removeprefix('OneKeyDo_')
        if base == rejected:
            return refusal(entry, len(calls), action_started=unknown)
        if base == 'enter':
            state = 'schedule'
        elif base == 'onekey':
            state = 'mana_confirm'
        elif base == 'mana_confirm':
            state = 'sweep_result'
        else:
            raise AssertionError('Unexpected later input: ' + base)
        return success('OneKeyInput_' + base, len(calls))

    monkeypatch.setattr(native, 'run', run)
    invoke = lambda: onekey.schedule_onekey(tasker, execute=True, game_day=DAY,
        device=DEVICE, authorizations=onekey.ITEMS, journal=journal, attest_current_plan=True)
    return invoke, journal, calls


@pytest.mark.parametrize('rejected', ['onekey', 'mana_confirm', 'mana_result'])
def test_onekey_current_refusal_never_settles_composite_or_allows_retry(monkeypatch, tmp_path, rejected):
    invoke, journal, calls = make_onekey(monkeypatch, tmp_path, rejected)
    result = invoke()
    assert result['outcome'] == 'unknown' and result['consumer_state'] == 'in_flight'
    assert result['native_input_state'] == 'not_sent'
    assert result['failed_input_operation'] == rejected
    assert result['failed_input_receipt']['input_execution'] == 'not_sent'
    assert not result.get('settlement_verified') and not result['schedule_executed']
    assert journal.events()[-1]['state'] == 'in_flight'
    actions = Journal(journal.directory / 'actions' / journal.events()[-1]['intent_id']).events()
    assert actions[-1]['state'] == 'in_flight' and actions[-1]['operation'] == rejected
    if rejected != 'onekey':
        assert any(event['state'] == 'settled' and event['operation'] == 'onekey' for event in actions)
    if rejected == 'mana_result':
        assert any(event['state'] == 'settled' and event['operation'] == 'mana_confirm' for event in actions)
    root_prefix = journal.path.read_bytes()
    action_path = journal.directory / 'actions' / journal.events()[-1]['intent_id'] / 'transactions.jsonl'
    action_prefix = action_path.read_bytes()
    attempted = list(calls)
    assert invoke()['reason'] == 'unsettled_intent_any_day_no_resend'
    assert journal.path.read_bytes() == root_prefix and action_path.read_bytes() == action_prefix
    assert calls == attempted


def test_onekey_uncertain_receipt_keeps_legacy_reason_and_original_evidence(monkeypatch, tmp_path):
    invoke, journal, calls = make_onekey(monkeypatch, tmp_path, 'mana_confirm', unknown=True)
    result = invoke()
    assert result['outcome'] == 'unknown' and result['reason'] == 'input_unverified_no_retry:mana_confirm'
    assert result['consumer_state'] == 'in_flight' and result['native_input_state'] == 'unknown'
    assert result['input_evidence'] == 'unknown' and 'failed_input_operation' not in result
    assert result['receipts'][-1]['native']['native_evidence']['action_started'] is True
    assert result['recovery']['original_reason'] == result['reason']
    prefix = journal.path.read_bytes()
    attempted = list(calls)
    assert invoke()['reason'] == 'unsettled_intent_any_day_no_resend'
    assert journal.path.read_bytes() == prefix and calls == attempted
