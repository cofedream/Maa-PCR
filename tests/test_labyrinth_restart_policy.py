"""Server-state restart recovery, no repeated unknown inputs, and keep-run policy."""
import io
from dataclasses import replace

import pytest

from pcr_maa.labyrinth.batch import Batch, BatchStopped, Binding
from pcr_maa.labyrinth.config import Goal, Settings
from pcr_maa.labyrinth.owned_runtime import OwnedRequest, StopSignal, parse_request, ConfigurationError
from pcr_maa.labyrinth.restart import prepare_restart
from pcr_maa.preset_execution import Journal


class Port:
    binding = Binding(123, 'synthetic-device')

    def __init__(self, enter=169, failure=None, stop=None):
        self.enter, self.failure, self.stop = enter, failure, stop
        self.calls, self.frame = [], 0

    def stamp(self, **fields):
        self.frame += 1
        return dict(verified=True, binding=self.binding.payload(), frame=self.frame, **fields)

    def top(self):
        self.calls.append(('top', self.enter))
        return self.stamp(enter_id=self.enter, fresh_start=self.enter == 0)

    def retire(self, enter, oid):
        self.calls.append(('retire', enter, oid))
        assert enter == self.enter
        if self.failure != 'still_active':
            self.enter = 0
        if self.stop:
            self.stop.request()
        if self.failure:
            raise TimeoutError('synthetic lost response')
        return self.stamp(enter_id=enter, operation_id=oid, retired=True)


def request(keep=False):
    return OwnedRequest(Settings((Goal(1, 1),)), 123,
        dict(adb_path='synthetic-adb.exe', serial='synthetic-device'),
        'synthetic-user', 'synthetic-password', keep)


def pending(journal, operation='opening_invite', **changes):
    value = dict(state='in_flight', operation=operation, operation_id='a' * 32,
                 binding=Port.binding.payload(), enter_id=169, expected_roles=['1003'])
    if operation == 'enter':
        value.pop('enter_id')
        value.pop('expected_roles')
        value.update(guild_id=1, difficulty=1)
    value.update(changes)
    with journal.locked():
        journal.append(value)
    return value


@pytest.mark.parametrize('operation', ['enter', 'opening_invite', 'opening_select', 'battle_start',
                                      'purchase', 'node_move_confirm', 'client_open_labyrinth'])
def test_new_start_retires_old_run_and_archives_only_after_fresh_idle_top(tmp_path, operation):
    journal = Journal(tmp_path)
    original = pending(journal, operation)
    before = journal.path.read_bytes()
    port = Port()
    progress = []
    prepare_restart(request(), port, journal, StopSignal(io.StringIO()), progress.append)
    assert [call[0] for call in port.calls] == ['top', 'retire', 'top']
    assert progress == [dict(stage='retiring')]
    rows = journal.events()
    assert rows[0] == original and rows[-1]['state'] == 'historical_unknown'
    assert rows[-1]['original_result_unknown'] and rows[-1]['replay_forbidden']
    assert rows[-1]['evidence']['enter_id'] == 0 and rows[-1]['evidence']['verified']
    assert journal.path.read_bytes().startswith(before)
    recovery = Journal(tmp_path / 'restart').events()
    assert [r['state'] for r in recovery] == ['in_flight', 'settled']
    assert recovery[0]['operation'] == 'restart_retire'
    assert not any(call[0] in ('enter', 'purchase', 'opening_invite') for call in port.calls)


@pytest.mark.parametrize('keep', [False, True])
def test_manually_finished_old_run_unblocks_without_retire_or_fake_success(tmp_path, keep):
    journal = Journal(tmp_path)
    original = pending(journal)
    port = Port(enter=0)
    prepare_restart(request(keep), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert port.calls == [('top', 0)]
    assert journal.events()[0] == original
    assert journal.events()[-1]['state'] == 'historical_unknown'
    assert 'result' not in journal.events()[-1]


def test_recovery_does_not_overwrite_original_action_scope_or_timestamp(tmp_path):
    journal = Journal(tmp_path)
    original = pending(journal, scope='original_action_scope', at='original-time')
    prepare_restart(request(), Port(0), journal, StopSignal(io.StringIO()), lambda _: None)
    history = journal.events()[-1]
    assert history['scope'] == original['scope'] and history['at'] == original['at']
    assert history['recovery_scope'] == 'labyrinth_restart'


def test_keep_switch_leaves_active_run_and_unknown_history_intact(tmp_path):
    journal = Journal(tmp_path)
    pending(journal)
    before = journal.path.read_bytes()
    port = Port()
    with pytest.raises(BatchStopped, match='^kept_run_unknown$'):
        prepare_restart(request(True), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert port.calls == [('top', 169)] and journal.path.read_bytes() == before
    assert not Journal(tmp_path / 'restart').events()


def test_keep_switch_preserves_active_run_without_unknown_records(tmp_path):
    journal = Journal(tmp_path)
    port = Port()
    prepare_restart(request(True), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert port.calls == [('top', 169)] and not journal.events()


def test_default_new_start_discards_active_run_even_without_unknown_records(tmp_path):
    journal = Journal(tmp_path)
    port = Port()
    prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert [call[0] for call in port.calls] == ['top', 'retire', 'top']
    assert not journal.events()


@pytest.mark.parametrize('failure', ['lost_after', 'still_active'])
def test_unknown_retire_is_read_back_on_next_start_and_never_resent(tmp_path, failure):
    journal = Journal(tmp_path)
    pending(journal)
    before = journal.path.read_bytes()
    port = Port(failure=failure)
    with pytest.raises(BatchStopped, match='^restart_retire_unknown$'):
        prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert journal.path.read_bytes() == before
    recovery = Journal(tmp_path / 'restart')
    assert recovery.events()[-1]['state'] == 'in_flight'
    port.failure = None
    if failure == 'still_active':
        with pytest.raises(BatchStopped, match='^restart_retire_unknown$'):
            prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
        assert journal.path.read_bytes() == before
        port.enter = 0  # Independent manual retirement; no protocol resend.
    prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert sum(call[0] == 'retire' for call in port.calls) == 1
    assert recovery.events()[-1]['state'] == 'historical_unknown'
    assert journal.events()[-1]['state'] == 'historical_unknown'


def test_previous_normal_retire_unknown_is_never_repeated(tmp_path):
    journal = Journal(tmp_path)
    pending(journal, 'retire')
    port = Port()
    with pytest.raises(BatchStopped, match='^restart_retire_unknown$'):
        prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert port.calls == [('top', 169)]
    port.enter = 0
    prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert journal.events()[-1]['state'] == 'historical_unknown'


def test_stop_during_retire_settles_its_ack_then_next_start_only_queries(tmp_path):
    journal = Journal(tmp_path)
    pending(journal)
    before = journal.path.read_bytes()
    stop = StopSignal(io.StringIO())
    calls = []
    stop.on_stop(lambda: calls.append('cancel'))
    port = Port(stop=stop)
    with pytest.raises(BatchStopped, match='^stopped$'):
        prepare_restart(request(), port, journal, stop, lambda _: None)
    assert calls == ['cancel'] and journal.path.read_bytes() == before
    assert Journal(tmp_path / 'restart').events()[-1]['state'] == 'settled'
    port.stop = None
    prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert sum(call[0] == 'retire' for call in port.calls) == 1
    assert journal.events()[-1]['state'] == 'historical_unknown'


@pytest.mark.parametrize('recovered_clears', [False, True])
def test_keep_old_run_does_not_fall_back_to_retire_when_opening_cannot_be_reused(tmp_path, recovered_clears):
    port = Port()
    port.observe_existing_opening = lambda *args, **kwargs: None
    batch = Batch(request().settings, port, Journal(tmp_path), port.binding, keep_old_run=True)
    batch._first_acquire_done = recovered_clears
    with batch.journal.locked(), pytest.raises(BatchStopped, match='^kept_existing_run$'):
        batch.acquire(1)
    assert port.calls == [('top', 169)] and not batch.journal.events()


@pytest.mark.parametrize('recovered_clears', [False, True])
def test_new_activity_between_restart_check_and_batch_start_is_not_discarded_again(tmp_path, recovered_clears):
    port = Port(enter=170)
    batch = Batch(request().settings, port, Journal(tmp_path), port.binding, require_fresh_start=True)
    batch._first_acquire_done = recovered_clears
    with batch.journal.locked(), pytest.raises(BatchStopped, match='^restart_state_changed$'):
        batch.acquire(1)
    assert port.calls == [('top', 170)] and not batch.journal.events()


def test_keep_old_run_overrides_reroll_when_existing_opening_does_not_match(tmp_path):
    port = Port()
    def observe(guild, difficulty, oid, **kwargs):
        return port.stamp(enter_id=169, guild_id=guild, difficulty=difficulty, operation_id=oid)
    port.observe_existing_opening = observe
    batch = Batch(replace(request().settings, reroll=True), port, Journal(tmp_path),
                  port.binding, keep_old_run=True)
    batch.route_for = lambda *args, **kwargs: None
    with batch.journal.locked(), pytest.raises(BatchStopped, match='^kept_existing_run$'):
        batch.acquire(1)
    assert port.calls == [('top', 169)]
    assert [row['operation'] for row in batch.journal.events()] == ['existing_opening'] * 2


@pytest.mark.parametrize('wrong', ['binding', 'bool_enter', 'negative_enter', 'frame', 'fresh_start', 'verified'])
def test_unqualified_top_never_archives_or_retires(tmp_path, wrong):
    journal = Journal(tmp_path)
    pending(journal)
    before = journal.path.read_bytes()
    port = Port()
    top = port.top()
    changes = dict(binding=dict(account_id=124, device_id='synthetic-device'), bool_enter=True,
                   negative_enter=-1, frame=0, fresh_start=True, verified=False)
    top['enter_id' if wrong in ('bool_enter', 'negative_enter') else wrong] = changes[wrong]
    port.top = lambda: top
    with pytest.raises(BatchStopped, match='^restart_state_unknown$'):
        prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert journal.path.read_bytes() == before and len(port.calls) == 1


@pytest.mark.parametrize('wrong', ['account', 'device'])
def test_other_target_is_blocked_before_any_server_request(tmp_path, wrong):
    journal = Journal(tmp_path)
    binding = dict(account_id=124 if wrong == 'account' else 123,
                   device_id='other-device' if wrong == 'device' else 'synthetic-device')
    pending(journal, binding=binding)
    port = Port()
    with pytest.raises(BatchStopped, match='^restart_target_mismatch$'):
        prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert not port.calls


@pytest.mark.parametrize('mutation', ['active', 'source', 'frame', 'binding', 'intent', 'result_known', 'replay'])
def test_closed_run_history_requires_idle_proof_and_unchanged_original(tmp_path, mutation):
    journal = Journal(tmp_path)
    pending(journal)
    prepare_restart(request(), Port(0), journal, StopSignal(io.StringIO()), lambda _: None)
    rows = journal.events()
    event = rows[-1]
    if mutation == 'active': event['evidence']['enter_id'] = 169
    if mutation == 'source': event['evidence']['source'] = 'settings'
    if mutation == 'frame': event['evidence']['frame'] = 0
    if mutation == 'binding': event['evidence']['binding']['account_id'] = 124
    if mutation == 'intent': event['enter_id'] = 170
    if mutation == 'result_known': event['original_result_unknown'] = False
    if mutation == 'replay': event['replay_forbidden'] = False
    import json
    journal.path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='Journal sequence invalid'):
        journal.events()


@pytest.mark.parametrize('keep', [None, False, True, 0, 1, 'true'])
def test_owned_request_policy_defaults_off_and_accepts_only_boolean(tmp_path, keep):
    adb = tmp_path / 'adb.exe'
    adb.write_bytes(b'synthetic; never execute')
    value = dict(version=1, settings=request().settings.payload(), account_id=123,
        connection=dict(adb_path=str(adb), serial='127.0.0.1:65530'),
        credentials=dict(username='synthetic-user', password='synthetic-password'))
    if keep is not None: value['keep_old_run'] = keep
    if keep is None or type(keep) is bool:
        parsed = parse_request(value)
        assert parsed.keep_old_run is (False if keep is None else keep)
        assert parsed.settings.payload() == request().settings.payload()
    else:
        with pytest.raises(ConfigurationError): parse_request(value)


def test_stop_before_recovery_does_not_query_or_send(tmp_path):
    port = Port()
    stop = StopSignal(io.StringIO())
    stop.request()
    with pytest.raises(BatchStopped, match='^stopped$'):
        prepare_restart(request(), port, Journal(tmp_path), stop, lambda _: None)
    assert not port.calls


def test_malformed_history_is_preserved_with_actionable_error(tmp_path):
    journal = Journal(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    journal.path.write_bytes(b'{partial-original-record')
    port = Port()
    with pytest.raises(BatchStopped, match='^restart_records_invalid$'):
        prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert journal.path.read_bytes() == b'{partial-original-record' and not port.calls


@pytest.mark.parametrize('keep', [False, True])
def test_real_owned_start_reaches_restart_check_instead_of_early_unknown_block(tmp_path, monkeypatch, keep):
    from test_labyrinth_owned_startup import runtime
    from pcr_maa.labyrinth import acquisition
    from pcr_maa.labyrinth.owned_runtime import OwnedRuntime
    owned, events, calls = runtime(tmp_path, monkeypatch)
    owned.request.keep_old_run = keep
    journal = Journal(tmp_path / '.runtime/labyrinth-work/ledger')
    original = pending(journal, binding=Binding(123, '127.0.0.1:65530').payload())
    ports = []
    class RuntimePort(Port):
        def __init__(self, client, game, binding, **kwargs):
            super().__init__()
            self.client, self.binding = client, binding
            ports.append(self)
        def close(self): self.client.close()
    monkeypatch.setattr(acquisition, 'ProtocolAcquisitionPort', RuntimePort)
    owned._prepare_restart = lambda journal: OwnedRuntime._prepare_restart(owned, journal)
    result = owned.run()
    assert result['outcome'] == ('kept_run_unknown' if keep else 'stopped')
    assert calls.index('authenticate') > calls.index('client')
    assert journal.events()[0] == original
    if keep:
        assert journal.events()[-1]['state'] == 'in_flight'
        assert 'batch' not in calls and ports[0].calls == [('top', 169)]
    else:
        assert journal.events()[-1]['state'] == 'historical_unknown'
        assert calls.index('batch') > calls.index('authenticate')
        assert [call[0] for call in ports[0].calls] == ['top', 'retire', 'top']
    assert owned.request.username == owned.request.password == ''
