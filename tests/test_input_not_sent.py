"""Synthetic dispatch and journal boundaries; no device or real ledger."""
from types import SimpleNamespace
import pytest
from pcr_maa import native, preset_execution, event, abyss, game_day
from pcr_maa.preset_execution import Journal
from test_preset_execution import Port as PresetPort, PRESET
from test_event import Port as EventPort
from test_abyss import Port as AbyssPort
from test_game_day_policy import freeze

@pytest.fixture(autouse=True)
def clock(monkeypatch):
    for module in (preset_execution, event, abyss, game_day):
        freeze(monkeypatch, module, '2026-10-05T14:00:00+08:00')

def make(kind, tmp_path):
    journal = Journal(tmp_path / kind)
    if kind == 'preset':
        port = PresetPort()
        invoke = lambda: preset_execution.execute_one(port, PRESET, 'user-reported-unreset',
            journal, execute=True, game_day='2026-10-05')
    elif kind == 'event':
        port = EventPort()
        invoke = lambda: event.execute_event(port, journal, execute=True,
            context='user-reported-unreset', game_day='2026-10-05')
    else:
        port = AbyssPort()
        device = dict(serial='127.0.0.1:16384', manager=dict(source='mumu_manager_local_v1',
            installation=str(tmp_path), index=0, created_timestamp=123,
            app_package='com.bilibili.priconne'))
        invoke = lambda: abyss.execute_abyss(port, journal, execute=True,
            context='gui-current-state', device=device, game_day='2026-10-05')
    port.journal = journal
    return port, journal, invoke

@pytest.mark.parametrize('kind', ['preset', 'event', 'abyss'])
@pytest.mark.parametrize('proof', ['not_sent', 'unknown', 'failed_receipt'])
def test_current_intent_only_and_no_automatic_retry(kind, proof, tmp_path):
    port, journal, invoke = make(kind, tmp_path)
    calls = []
    def submit(quote):
        calls.append(quote)
        assert journal.events()[-1]['state'] == 'in_flight'
        if proof == 'not_sent':
            raise native.NativeInputNotSent(dict(input_execution='not_sent',
                reason='synthetic_pre_dispatch_refusal', dispatch_attempted=False))
        if proof == 'unknown':
            raise RuntimeError('synthetic action may have started')
        return dict(consumption_dispatched=False, result_verified=False)
    port.submit = submit
    result = invoke()
    assert len(calls) == 1
    prefix = journal.path.read_bytes()
    assert not result['settlement_verified']
    if proof == 'not_sent':
        assert result['outcome'] == 'native_input_not_sent'
        assert result['consumption_sent'] is False
        terminal = journal.events()[-1]
        assert terminal['state'] == 'settled' and terminal['input_execution'] == 'not_sent'
        assert terminal['business_result_verified'] is False
        # A new explicit start reads fresh state; the terminal is not a consumed sweep.
        assert invoke()['outcome'] == 'native_input_not_sent'
        assert len(calls) == 2 and journal.path.read_bytes().startswith(prefix)
    else:
        assert result['outcome'] == 'unknown_settlement'
        assert journal.events()[-1]['state'] == 'in_flight'
        assert invoke()['outcome'] == 'blocked_in_flight'
        assert len(calls) == 1 and journal.path.read_bytes() == prefix

@pytest.mark.parametrize('kind', ['preset', 'event', 'abyss'])
def test_native_snapshot_refusal_is_positive_not_sent(kind):
    from pcr_maa.preset_execution_native import NativePreset
    from pcr_maa.event_native import NativeEvent
    from pcr_maa.abyss_native import NativeAbyss
    if kind == 'abyss':
        port = NativeAbyss(None)
    else:
        port = (NativePreset if kind == 'preset' else NativeEvent).__new__(
            NativePreset if kind == 'preset' else NativeEvent)
        port._confirmed = None
        port.tasker = SimpleNamespace()  # Any accidental dispatch fails this test.
        if kind == 'event':
            port._event_identity_lost = True
            port.event_identity = None
    with pytest.raises(native.NativeInputNotSent) as exc:
        port.submit({})
    assert exc.value.receipt['input_execution'] == 'not_sent'
    assert exc.value.receipt['dispatch_attempted'] is False

def test_not_sent_cannot_close_a_different_or_old_unknown(tmp_path):
    journal = Journal(tmp_path / 'journal')
    with journal.locked():
        intent = dict(state='in_flight', game_day='2026-10-05', slot=2)
        journal.append(intent)
        original = journal.path.read_bytes()
        with pytest.raises(ValueError):
            journal.append_not_sent(dict(intent, slot=3), dict(input_execution='not_sent'))
        with pytest.raises(ValueError):
            journal.append_not_sent(intent, {})
        assert journal.path.read_bytes() == original

@pytest.mark.parametrize('kind', ['preset', 'event', 'abyss'])
def test_result_task_pre_dispatch_refusal_does_not_prove_consumer_not_sent(kind, tmp_path):
    port, journal, invoke = make(kind, tmp_path)
    consumed = []
    def submit(quote):
        consumed.append(quote)  # synthetic already-confirmed consumer ACK
        token = native.RUN_DEADLINE.set(0)
        try:
            native.run(SimpleNamespace(post_task=lambda entry: pytest.fail('expired result posted')),
                       'AbyssResultClose' if kind == 'abyss' else 'ExecResultStart')
        finally:
            native.RUN_DEADLINE.reset(token)
    port.submit = submit
    result = invoke()
    assert len(consumed) == 1 and result['outcome'] == 'unknown_settlement'
    assert result['consumption_sent'] is True
    assert journal.events()[-1]['state'] == 'in_flight'
    original = journal.path.read_bytes()
    assert invoke()['outcome'] == 'blocked_in_flight'
    assert len(consumed) == 1 and journal.path.read_bytes() == original
