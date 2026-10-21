"""Stop at the real Batch transaction boundary; retain true unknown results."""
import io
import json
import threading

import pytest

from pcr_maa.labyrinth.batch import Batch, BatchStopped, Binding
from pcr_maa.labyrinth.config import Goal, Settings
from pcr_maa.labyrinth.owned_runtime import StopSignal
from pcr_maa.preset_execution import Journal


def test_stop_during_input_records_ack_before_cancel_and_blocks_next_input(tmp_path):
    stop = StopSignal(io.StringIO())
    journal = Journal(tmp_path)
    batch = Batch(Settings((Goal(1, 1),)), object(), journal, Binding(123, 'synthetic'), stopped=stop)
    entered, release = threading.Event(), threading.Event()
    calls, errors = [], []
    stop.on_stop(lambda: calls.append(('cancel', journal.events()[-1]['state'])))
    def send(oid):
        calls.append(('send', oid))
        entered.set()
        assert release.wait(5)
        assert not stop()
        return dict(verified=True, frame=1, binding=batch.binding.payload(), operation_id=oid, enter_id=169)
    def exercise():
        try:
            with journal.locked():
                batch.transaction('opening_invite', dict(enter_id=169), send)
                assert stop()
                with pytest.raises(BatchStopped, match='^stopped$'):
                    batch.transaction('opening_invite', dict(enter_id=169), lambda _: pytest.fail('Repeated input'))
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=exercise)
    worker.start()
    try:
        assert entered.wait(5)
        stop.request()
        assert stop() and len(calls) == 1
        release.set()
        worker.join(5)
        assert not worker.is_alive() and not errors
        assert [r['state'] for r in journal.events()] == ['in_flight', 'settled']
        assert calls[-1] == ('cancel', 'settled')
    finally:
        release.set()
        worker.join(5)


def test_stop_does_not_turn_failed_input_into_success(tmp_path):
    stop = StopSignal(io.StringIO())
    journal = Journal(tmp_path)
    batch = Batch(Settings((Goal(1, 1),)), object(), journal, Binding(123, 'synthetic'), stopped=stop)
    calls = []
    stop.on_stop(lambda: calls.append('cancel'))
    def send(_):
        stop.request()
        raise BatchStopped('opening_input_unknown')
    with journal.locked(), pytest.raises(BatchStopped, match='^opening_input_unknown$'):
        batch.transaction('opening_invite', dict(enter_id=169), send)
    assert calls == ['cancel'] and stop()
    assert journal.events()[-1]['state'] == 'in_flight'


def test_stop_before_input_writes_nothing_and_callbacks_are_once(tmp_path):
    stop = StopSignal(io.StringIO())
    calls = []
    stop.on_stop(lambda: calls.append('cancel'))
    stop.request()
    stop.request()
    journal = Journal(tmp_path)
    batch = Batch(Settings((Goal(1, 1),)), object(), journal, Binding(123, 'synthetic'), stopped=stop)
    with journal.locked(), pytest.raises(BatchStopped, match='^stopped$'):
        batch.transaction('opening_invite', dict(enter_id=169), lambda _: pytest.fail('Input after Stop'))
    assert not journal.events() and calls == ['cancel']


@pytest.mark.parametrize('invalid', [None, 'active', 'other_operation', 'binding', 'evidence', 'replay'])
def test_retired_invitation_history_preserves_unknown_and_requires_exact_scope(tmp_path, invalid):
    journal = Journal(tmp_path)
    pending = dict(state='in_flight', operation='opening_invite', operation_id='original',
                   binding=dict(account_id=123, device_id='synthetic'), enter_id=169, expected_roles=['1003'])
    history = dict(pending, state='historical_unknown', resolution='user_confirmed_retired',
                   original_result_unknown=True, replay_forbidden=True,
                   evidence=dict(source='user_confirmation', retired=True))
    if invalid == 'active': history['evidence']['retired'] = False
    if invalid == 'other_operation': history['operation'] = 'purchase'
    if invalid == 'binding': history['binding'] = dict(account_id=124, device_id='synthetic')
    if invalid == 'evidence': history['evidence'] = None
    if invalid == 'replay': history['replay_forbidden'] = False
    with journal.locked():
        journal.append(pending)
        before = journal.path.read_bytes()
        if invalid:
            with pytest.raises(ValueError, match='Journal sequence invalid'):
                journal.append(history)
            assert journal.path.read_bytes() == before
        else:
            journal.append(history)
    assert journal.path.read_bytes().startswith(before)
    if invalid:
        # Existing malformed history must still be refused after a fresh read.
        with journal.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(history) + '\n')
        with pytest.raises(ValueError, match='Journal sequence invalid'):
            journal.events()
    else:
        rows = journal.events()
        assert rows == [pending, history]
        assert rows[-1]['original_result_unknown'] and rows[-1]['replay_forbidden']
