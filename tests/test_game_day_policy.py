"""Beijing 05:00 dates and synthetic transactions with isolated test journals."""
from datetime import datetime, timedelta, timezone
import pytest


@pytest.mark.parametrize('stamp, expected', [
    ('2026-09-25T23:59:00+08:00', '2026-09-25'),
    ('2026-09-26T00:00:00+08:00', '2026-09-25'),
    ('2026-09-26T00:30:00+08:00', '2026-09-25'),
    ('2026-09-26T04:59:59+08:00', '2026-09-25'),
    ('2026-09-26T05:00:00+08:00', '2026-09-26'),
    ('2026-09-26T05:30:00+08:00', '2026-09-26'),
    ('2026-09-26T06:00:00+08:00', '2026-09-26'),
    ('2026-09-26T13:59:59+08:00', '2026-09-26'),
    ('2026-09-26T14:00:00+08:00', '2026-09-26'),
    ('2026-10-01T04:59:00+08:00', '2026-09-30'),
    ('2027-01-01T04:59:00+08:00', '2026-12-31'),
    ('2028-03-01T04:59:00+08:00', '2028-02-29'),
    ('2026-09-25T21:00:00+00:00', '2026-09-26'),
    ('2026-09-25T14:00:00-07:00', '2026-09-26'),
    ('2026-09-26T04:59:00', '2026-09-25'),
    ('2026-09-26T05:00:00', '2026-09-26'),
])
def test_current_game_day(stamp, expected):
    from pcr_maa.game_day import current_game_day
    assert current_game_day(datetime.fromisoformat(stamp)) == expected


def test_default_clock_uses_explicit_beijing(monkeypatch):
    import pcr_maa.game_day as policy
    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            assert tz == timezone(timedelta(hours=8))
            return cls(2026, 9, 26, 0, 30, tzinfo=tz)
    monkeypatch.setattr(policy, 'datetime', Clock)
    assert policy.current_game_day() == '2026-09-25'


def freeze(monkeypatch, module, stamp):
    clock = [datetime.fromisoformat(stamp)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0]
    monkeypatch.setattr(module, 'datetime', Clock)
    return clock


def transaction(kind, tmp_path):
    from pcr_maa import event, preset_execution
    from test_event import Port as EventPort
    from test_preset_execution import Port as PresetPort, PRESET
    port = EventPort() if kind == 'event' else PresetPort()
    port.journal = preset_execution.Journal(tmp_path)
    module = event if kind == 'event' else preset_execution
    def run(day):
        options = dict(game_day=day, execute=True)
        if kind == 'event':
            return module.execute_event(port, port.journal, context='user-reported-unreset', **options)
        return module.execute_one(port, PRESET, 'user-reported-unreset', port.journal, **options)
    return module, port, run


@pytest.mark.parametrize('kind', ['event', 'preset'])
@pytest.mark.parametrize('start,end,day', [
    ('2026-09-25T23:59:59+08:00', '2026-09-26T00:00:01+08:00', '2026-09-25'),
    ('2026-09-26T05:59:59+08:00', '2026-09-26T06:00:01+08:00', '2026-09-26'),
    ('2026-09-26T13:59:59+08:00', '2026-09-26T14:00:01+08:00', '2026-09-26'),
])
def test_ordinary_boundaries_do_not_stop(kind, start, end, day, tmp_path, monkeypatch):
    module, port, run = transaction(kind, tmp_path)
    clock = freeze(monkeypatch, module, start)
    quote = port.quote
    def crossing(reading):
        clock[0] = datetime.fromisoformat(end)
        return quote(reading)
    port.quote = crossing
    result = run(day)
    assert result['outcome'] == 'executed'
    assert result['settlement_verified'] and not result['daily_complete']
    assert port.calls.count('submit') == 1


@pytest.mark.parametrize('kind', ['event', 'preset'])
@pytest.mark.parametrize('phase', ['read', 'quote', 'confirm', 'submit'])
@pytest.mark.parametrize('end', ['2026-09-26T05:00:01+08:00', '2026-09-26T04:58:59+08:00'])
def test_refresh_or_rollback_invalidates_observation(kind, phase, end, tmp_path, monkeypatch):
    module, port, run = transaction(kind, tmp_path)
    clock = freeze(monkeypatch, module, '2026-09-26T04:59:59+08:00')
    original = getattr(port, phase)
    def crossing(*args):
        result = original(*args)
        clock[0] = datetime.fromisoformat(end)
        return result
    setattr(port, phase, crossing)
    result = run('2026-09-25')
    assert not result['settlement_verified'] and not result['daily_complete']
    assert 'need_new_observation' in result.get('observation_invalid_reason', '')
    assert port.calls.count('submit') == (1 if phase == 'submit' else 0)
    assert 'home' not in port.calls
    if phase == 'submit':
        assert port.journal.events()[-1]['state'] == 'in_flight'
        clock[0] = datetime.fromisoformat('2026-09-26T06:00:00+08:00')
        count = len(port.calls)
        assert run('2026-09-26')['outcome'] == 'blocked_in_flight'
        assert len(port.calls) == count


@pytest.mark.parametrize('kind', ['event', 'preset'])
def test_wrong_game_day_zero_port_calls(kind, tmp_path, monkeypatch):
    module, port, run = transaction(kind, tmp_path)
    freeze(monkeypatch, module, '2026-09-26T00:30:00+08:00')
    result = run('2026-09-26')
    assert result['outcome'] == 'blocked_or_failed'
    assert 'current_game_day_required' in result['error']
    assert port.calls == []


@pytest.mark.parametrize('start,end,day,complete', [
    ('2026-09-25T23:59:59+08:00', '2026-09-26T00:00:01+08:00', '2026-09-25', True),
    ('2026-09-26T05:59:59+08:00', '2026-09-26T06:00:01+08:00', '2026-09-26', True),
    ('2026-09-26T04:59:59+08:00', '2026-09-26T05:00:01+08:00', '2026-09-25', False),
    ('2026-09-26T14:30:00+08:00', '2026-09-26T14:29:59+08:00', '2026-09-26', False),
])
def test_chain_boundaries(start, end, day, complete, tmp_path, monkeypatch):
    from pcr_maa import supported_daily as chain
    from test_supported_daily import config, success
    clock = freeze(monkeypatch, chain, start)
    calls = []
    def step(tasker, name, *args, **kwargs):
        calls.append(name)
        clock[0] = datetime.fromisoformat(end)
        return success(name)
    monkeypatch.setattr(chain, '_run_step', step)
    result = chain.execute_supported(None, config(), game_day=day,
        context='user-reported-unreset', directory=tmp_path, execute=True)
    assert result['supported_chain_complete'] is complete
    assert len(calls) == (len(chain.ORDER) if complete else 1)
    assert not result['daily_complete']


@pytest.mark.parametrize('kind', ['event', 'preset'])
@pytest.mark.parametrize('verified', [True, False])
def test_ack_then_refresh_unknown_never_retries(kind, verified, tmp_path, monkeypatch):
    module, port, run = transaction(kind, tmp_path)
    clock = freeze(monkeypatch, module, '2026-09-26T04:59:59+08:00')
    submit = port.submit
    def ack(quote):
        result = submit(quote)
        result['result_verified'] = verified
        clock[0] = datetime.fromisoformat('2026-09-26T05:00:00+08:00')
        return result
    port.submit = ack
    result = run('2026-09-25')
    assert result['submission']['consumption_dispatched']
    assert not result['settlement_verified'] and not result['home_verified']
    assert port.journal.events()[-1]['state'] == 'in_flight'
    assert run('2026-09-26')['outcome'] == 'blocked_in_flight'
    assert port.calls.count('submit') == 1 and 'home' not in port.calls


@pytest.mark.parametrize('kind', ['event', 'preset'])
def test_refresh_before_consumption_requires_new_observation(kind, tmp_path, monkeypatch):
    module, port, run = transaction(kind, tmp_path)
    clock = freeze(monkeypatch, module, '2026-09-26T04:59:59+08:00')
    quote = port.quote
    def crossing(reading):
        clock[0] = datetime.fromisoformat('2026-09-26T05:00:01+08:00')
        return quote(reading)
    port.quote = crossing
    assert run('2026-09-25')['outcome'] == 'need_new_observation'
    assert not port.journal.events()
    assert 'submit' not in port.calls
    # A separate explicitly authorized call obtains a new read for the new day.
    count = port.calls.count('read')
    port.quote = quote
    assert run('2026-09-26')['outcome'] == 'executed'
    assert port.calls.count('read') > count
    assert port.calls.count('submit') == 1


@pytest.mark.parametrize('kind', ['event', 'preset'])
def test_monotonic_deadline_still_blocks_without_wall_change(kind, tmp_path, monkeypatch):
    module, port, run = transaction(kind, tmp_path)
    freeze(monkeypatch, module, '2026-09-26T05:30:00+08:00')
    elapsed = [0.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: elapsed[0])
    quote = port.quote
    def slow(reading):
        elapsed[0] = 181.0
        return quote(reading)
    port.quote = slow
    result = run('2026-09-26')
    assert result['observation_invalid_reason'] == 'reading_expired'
    assert not result['consumption_sent'] and 'submit' not in port.calls


@pytest.mark.parametrize('kind', ['event', 'preset'])
def test_completed_goal_not_repeated_after_midnight_or_counter_reset(kind, tmp_path, monkeypatch):
    module, port, run = transaction(kind, tmp_path)
    clock = freeze(monkeypatch, module, '2026-09-25T23:59:59+08:00')
    assert run('2026-09-25')['outcome'] == 'executed'
    clock[0] = datetime.fromisoformat('2026-09-26T00:30:00+08:00')
    assert run('2026-09-25')['outcome'] == 'skipped_goal_met'
    for row in port.after['rows']:
        row['remaining'] = 3
    assert run('2026-09-25')['outcome'] == 'counter_reset_or_identity_change'
    assert port.calls.count('submit') == 1


@pytest.mark.parametrize('kind', ['event', 'preset'])
@pytest.mark.parametrize('phase', ['read', 'confirm'])
def test_ordinary_hour_after_read_or_confirmation(kind, phase, tmp_path, monkeypatch):
    module, port, run = transaction(kind, tmp_path)
    clock = freeze(monkeypatch, module, '2026-09-26T05:59:59+08:00')
    original = getattr(port, phase)
    def crossing(*args):
        result = original(*args)
        clock[0] = datetime.fromisoformat('2026-09-26T06:00:01+08:00')
        return result
    setattr(port, phase, crossing)
    assert run('2026-09-26')['outcome'] == 'executed'


def test_fit_count_read_crosses_refresh_before_adjustment(tmp_path, monkeypatch):
    module, port, _ = transaction('preset', tmp_path)
    from test_preset_execution import PRESET
    clock = freeze(monkeypatch, module, '2026-09-26T04:59:59+08:00')
    def read_count(slot):
        clock[0] = datetime.fromisoformat('2026-09-26T05:00:00+08:00')
        return 3
    port.read_count = read_count
    port.fit_count = lambda *args: pytest.fail('stale count adjustment')
    result = module.execute_one(port, PRESET, 'user-reported-unreset', port.journal,
        execute=True, fit_remaining=True, game_day='2026-09-25')
    assert result['outcome'] == 'need_new_observation'
    assert not result['count_fit']['attempted'] and 'submit' not in port.calls


@pytest.mark.parametrize('command', ['execute-supported', 'execute-preset', 'event-hard'])
@pytest.mark.parametrize('stamp,day,accepted', [
    ('2026-09-26T00:30:00+08:00', '2026-09-25', True),
    ('2026-09-26T04:59:00+08:00', '2026-09-26', False),
    ('2026-09-26T05:30:00+08:00', '2026-09-26', True),
])
def test_cli_game_day_checked_before_setup(command, stamp, day, accepted, tmp_path, monkeypatch):
    from pcr_maa import cli, game_day, daily, device, native
    from test_supported_daily import config
    freeze(monkeypatch, game_day, stamp)
    monkeypatch.setattr(daily, 'load_config', lambda _: config())
    calls = []
    monkeypatch.setattr(native, 'setup', lambda: calls.append('setup'))
    monkeypatch.setattr(native, 'save_receipt', lambda *args: tmp_path / 'unused.json')
    def navigate(*args, **kwargs):
        calls.append('navigate')
        return dict(outcome='executed' if command != 'execute-supported' else 'complete',
                    supported_chain_complete=True, home_verified=True, daily_complete=False, steps=[])
    monkeypatch.setattr(device, 'navigate', navigate)
    argv = [command, '--game-day', day, '--execute', '--count-context', 'user-reported-unreset']
    argv += (['--start', 'home', '--input-display', '2', '--screenshot-display', '123']
             if command == 'event-hard' else ['--config', 'synthetic'])
    if command == 'execute-preset':
        argv += ['--kind', 'very_hard']
    code = cli.main(argv)
    assert (code == 0) is accepted
    assert calls == (['setup', 'navigate'] if accepted else [])
