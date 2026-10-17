"""Interrupted real navigation producers survive the Owner's restart preflight."""
import io
import json
from types import SimpleNamespace as NS

import pytest

from pcr_maa import native
from pcr_maa.labyrinth.batch import Batch, BatchStopped, Binding
from pcr_maa.labyrinth.game_port import MaaGamePort
from pcr_maa.labyrinth.game_session import MaaGameSession
from pcr_maa.labyrinth.owned_runtime import OwnedRuntime, StopSignal
from pcr_maa.labyrinth.restart import prepare_restart
from pcr_maa.labyrinth.snapshots import OpeningSnapshot
from pcr_maa.preset_execution import Journal
from test_labyrinth_restart_policy import Port, request


def interrupted_navigation(journal, monkeypatch, *, producer='session', enter=169,
                           binding=Port.binding, interruption=TimeoutError):
    port = Port(enter)
    port.binding = binding
    batch = Batch(request().settings, port, journal, binding)
    tasker = NS(stopping=False, resource=NS(override_pipeline=lambda _: True))
    snapshot = OpeningSnapshot(binding.account_id, enter, 1, 1, ())
    calls = []
    failing = {'session': 'LabyClientHomeToAdventure',
               'client_stop': 'LabyGamePortStopClientOnce',
               'client_start': 'LabyGamePortStartClientOnce'}[producer]

    def run(_, node, **kwargs):
        calls.append(node)
        if node == failing:
            # The real producer must fsync its intent before any native input.
            assert journal.events()[-1]['state'] == 'in_flight'
            raise interruption('synthetic input interrupted; result unknown')
        if node == 'LabyGameSessionEntryObserved':
            names = ['LabyDateChangedObserved', 'LabyDisconnect6002Observed',
                     'LabyClientTitleObserved', 'LabyClientGameHomeObserved']
            root = dict(name=node, completed=True, recognition=[dict(name=name,
                algorithm='And', box=[0, 0, 1, 1] if name == names[-1] else None)
                for name in names])
        else:
            assert node == 'LabyGamePortStopClientOnce'
            root = dict(name=node, completed=True, action=dict(name=node,
                success=True, raw=dict(package='com.bilibili.priconne')))
        return dict(tasker_inited=True, task_id=len(calls), native_ok=True, nodes=[root])

    monkeypatch.setattr(native, 'run', run)
    with journal.locked(), pytest.raises(interruption):
        if producer == 'session':
            session = MaaGameSession(tasker, port.stamp)
            if enter:
                session.restore_new_opening(batch, snapshot, current_block_id=0)
            else:
                # Exercise the navigation producer before an opening exists;
                # restore_new_opening itself correctly requires a positive run.
                session._navigate(batch, snapshot, 'client_open_adventure',
                    'LabyClientHomeToAdventure', 'adventure')
        else:
            game = MaaGamePort(tasker, port.stamp)
            game._active_creation_oid = 'c' * 32
            game._restart_client(batch, snapshot)
    original = journal.events()[-1]
    assert original['state'] == 'in_flight'
    assert original['enter_id'] == original['protocol_enter_id'] == enter
    return original, calls


@pytest.mark.parametrize('producer', ['session', 'client_stop', 'client_start'])
@pytest.mark.parametrize('interruption', [TimeoutError, KeyboardInterrupt])
@pytest.mark.parametrize('keep', [False, True])
def test_real_navigation_intent_passes_owned_preflight_and_reaches_authenticated_top(
        tmp_path, monkeypatch, producer, interruption, keep):
    from test_labyrinth_owned_startup import runtime
    from pcr_maa.labyrinth import acquisition
    owned, _, calls = runtime(tmp_path, monkeypatch)
    owned.request.keep_old_run = keep
    journal = Journal(tmp_path / '.runtime/labyrinth-work/ledger')
    binding = Binding(owned.request.account_id, owned.request.connection['serial'])
    original, inputs = interrupted_navigation(journal, monkeypatch, producer=producer,
        binding=binding, interruption=interruption)
    before = journal.path.read_bytes()
    ports = []

    class RuntimePort(Port):
        def __init__(self, client, game, binding, **kwargs):
            super().__init__()
            self.client, self.binding = client, binding
            ports.append(self)
        def top(self):
            assert 'authenticate' in calls
            return super().top()
        def close(self): self.client.close()

    monkeypatch.setattr(acquisition, 'ProtocolAcquisitionPort', RuntimePort)
    owned._prepare_restart = lambda journal: OwnedRuntime._prepare_restart(owned, journal)
    result = owned.run()
    assert result['outcome'] == ('kept_run_unknown' if keep else 'stopped')
    assert len(ports) == 1 and ports[0].calls[0] == ('top', 169)
    assert journal.events()[-2 if not keep else -1] == original
    assert journal.path.read_bytes().startswith(before)
    if keep:
        assert journal.path.read_bytes() == before and 'batch' not in calls
    else:
        history = journal.events()[-1]
        assert history['state'] == 'historical_unknown'
        assert history['original_result_unknown'] is history['replay_forbidden'] is True
        assert 'result' not in history
        assert [call[0] for call in ports[0].calls] == ['top', 'retire', 'top']
        prepare_restart(request(), ports[0], journal, StopSignal(io.StringIO()), lambda _: None)
        assert sum(call[0] == 'retire' for call in ports[0].calls) == 1
    assert inputs.count({'session': 'LabyClientHomeToAdventure',
        'client_stop': 'LabyGamePortStopClientOnce',
        'client_start': 'LabyGamePortStartClientOnce'}[producer]) == 1
    assert owned.request.username == owned.request.password == ''


@pytest.mark.parametrize('wire', ['canonical', 'legacy', 'dual'])
def test_navigation_without_run_reconciles_idle_without_creating_or_retiring_run(tmp_path, monkeypatch, wire):
    journal = Journal(tmp_path)
    original, inputs = interrupted_navigation(journal, monkeypatch, enter=0)
    if wire != 'dual':
        original.pop('enter_id' if wire == 'legacy' else 'protocol_enter_id')
        journal.path.write_text(json.dumps(original) + '\n', encoding='utf-8')
    before = journal.path.read_bytes()
    port = Port(0)
    prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert port.calls == [('top', 0)] and inputs == ['LabyClientHomeToAdventure']
    assert journal.events()[0] == original and journal.path.read_bytes().startswith(before)
    history = journal.events()[-1]
    assert history['state'] == 'historical_unknown'
    assert history['resolution'] == 'protocol_idle_confirmed'
    assert history['original_result_unknown'] is history['replay_forbidden'] is True
    assert 'result' not in history


@pytest.mark.parametrize('producer', ['session', 'client_stop', 'client_start'])
@pytest.mark.parametrize('wire', ['canonical', 'legacy', 'dual'])
def test_real_producer_contract_reads_existing_single_field_or_equal_dual_records(
        tmp_path, monkeypatch, producer, wire):
    journal = Journal(tmp_path)
    interrupted_navigation(journal, monkeypatch, producer=producer)
    rows = journal.events()
    if wire != 'dual':
        # Emulate an older wire schema only after the real producer has run.
        for row in rows:
            row.pop('enter_id' if wire == 'legacy' else 'protocol_enter_id')
        journal.path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    original = journal.events()[-1]
    before = journal.path.read_bytes()
    port = Port(0)
    prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert journal.path.read_bytes().startswith(before) and journal.events()[-2] == original
    assert journal.events()[-1]['state'] == 'historical_unknown'
    assert port.calls == [('top', 0)]
    if producer == 'session':
        # The canonical-only history must retain the same duplicate-input gate
        # as the old protocol_enter_id history.
        batch = Batch(request().settings, port, journal, port.binding)
        session = MaaGameSession(NS(stopping=False), port.stamp)
        with journal.locked(), pytest.raises(BatchStopped, match='^game_session_already_attempted$'):
            session.restore_new_opening(batch, OpeningSnapshot(123, 169, 1, 1, ()), current_block_id=0)


@pytest.mark.parametrize('keep', [False, True])
def test_navigation_without_run_still_obeys_active_run_policy(tmp_path, monkeypatch, keep):
    journal = Journal(tmp_path)
    original, inputs = interrupted_navigation(journal, monkeypatch, enter=0)
    before = journal.path.read_bytes()
    port = Port(169)
    if keep:
        with pytest.raises(BatchStopped, match='^kept_run_unknown$'):
            prepare_restart(request(True), port, journal, StopSignal(io.StringIO()), lambda _: None)
        assert port.calls == [('top', 169)] and journal.path.read_bytes() == before
    else:
        prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
        assert [call[0] for call in port.calls] == ['top', 'retire', 'top']
        assert journal.events()[0] == original and journal.path.read_bytes().startswith(before)
        assert journal.events()[-1]['resolution'] == 'protocol_idle_confirmed'
    assert inputs == ['LabyClientHomeToAdventure']


@pytest.mark.parametrize('operation', ['client_open_adventure', 'client_stop_after_protocol',
    'client_start_after_protocol', 'purchase', 'retire', 'unknown_operation', 'enter'])
@pytest.mark.parametrize('fields', [dict(enter_id=169, protocol_enter_id=170),
    dict(enter_id=True, protocol_enter_id=1), dict(enter_id=1, protocol_enter_id=True),
    dict(enter_id=169, protocol_enter_id=None), dict(enter_id=-1),
    dict(protocol_enter_id=False), dict(enter_id='169'), {}])
def test_conflicting_invalid_or_absent_run_fields_preserve_record_before_server_access(
        tmp_path, operation, fields):
    journal = Journal(tmp_path)
    value = dict(state='in_flight', operation=operation, operation_id='a' * 32,
                 binding=Port.binding.payload(), **fields)
    # ENTER has a different contract, so missing guild/difficulty is invalid too.
    with journal.locked(): journal.append(value)
    before = journal.path.read_bytes()
    port = Port(0)
    with pytest.raises(BatchStopped, match='^blocked_in_flight$'):
        prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert port.calls == [] and journal.path.read_bytes() == before


@pytest.mark.parametrize('operation', ['client_stop_after_protocol', 'client_start_after_protocol',
    'opening_invite', 'opening_depart', 'existing_opening', 'map_scroll', 'node_preview',
    'battle_start', 'purchase', 'retire', 'restart_retire', 'unknown_operation'])
@pytest.mark.parametrize('key', ['enter_id', 'protocol_enter_id'])
def test_zero_is_not_a_run_identity_for_consumption_or_post_creation_actions(tmp_path, operation, key):
    journal = Journal(tmp_path)
    with journal.locked():
        journal.append(dict(state='in_flight', operation=operation, operation_id='a' * 32,
            binding=Port.binding.payload(), **{key: 0}))
    before = journal.path.read_bytes()
    port = Port(0)
    with pytest.raises(BatchStopped, match='^blocked_in_flight$'):
        prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert port.calls == [] and journal.path.read_bytes() == before


@pytest.mark.parametrize('scope', ['account', 'device'])
@pytest.mark.parametrize('enter', [0, 169])
def test_real_navigation_legacy_intent_cannot_cross_owner_binding(tmp_path, monkeypatch, scope, enter):
    journal = Journal(tmp_path)
    original, _ = interrupted_navigation(journal, monkeypatch, enter=enter)
    original.pop('enter_id')
    journal.path.write_text(json.dumps(original) + '\n', encoding='utf-8')
    before = journal.path.read_bytes()
    port = Port(0)
    port.binding = Binding(124 if scope == 'account' else 123,
        'other-device' if scope == 'device' else 'synthetic-device')
    with pytest.raises(BatchStopped, match='^restart_target_mismatch$'):
        prepare_restart(request(), port, journal, StopSignal(io.StringIO()), lambda _: None)
    assert port.calls == [] and journal.path.read_bytes() == before
