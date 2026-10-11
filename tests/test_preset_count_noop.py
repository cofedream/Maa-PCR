"""No-op count fitting keeps the consuming guards and unknown-result ledger."""
from copy import deepcopy

import pytest

from test_preset_count_fit import FitPort, invoke_fit
from test_preset_execution import frozen_game_clock


def port_at_target():
    port = FitPort()
    port.count = 4
    port._count_receipt = dict(native_ok=True, task_id=123, nodes=[dict(name='FitCount',
        completed=True, recognition=dict(filtered=[dict(text='4', score=.99)]))])
    return port


def test_matching_count_skips_edit_and_restore_but_keeps_consuming_guards(tmp_path):
    port = port_at_target()
    result = invoke_fit(tmp_path, port, execute=True)
    assert result['outcome'] == 'executed' and result['settlement_verified']
    fit = result['count_fit']
    assert fit['original'] == fit['target'] == fit['readback'] == 4
    assert not fit['attempted'] and not fit['restoration_attempted']
    assert fit['adjustment']['initial_evidence'] is port._count_receipt
    assert fit['adjustment']['no_change'] and fit['adjustment']['steps'] == []
    assert port.calls == ['read', 'count', 'quote', 'confirm', 'submit', 'read', 'home']
    assert port.journal.events()[-1]['state'] == 'settled'


def test_changed_quote_count_stops_without_overwriting_or_consuming(tmp_path):
    port = port_at_target()
    original_quote = port.quote
    def changed(reading):
        port.count = 3
        return original_quote(reading)
    port.quote = changed
    result = invoke_fit(tmp_path, port, execute=True)
    assert result['outcome'] == 'count_fit_quote_mismatch'
    assert not result['consumption_sent'] and 'submit' not in port.calls
    assert not any(isinstance(call, tuple) for call in port.calls)
    assert port.count == 3 and not port.journal.events()


def test_failed_confirmation_still_prevents_submit(tmp_path):
    port = port_at_target()
    def changed(reading, quote):
        port.calls.append('confirm')
        return dict(verified=False)
    port.confirm = changed
    result = invoke_fit(tmp_path, port, execute=True)
    assert not result['consumption_sent'] and 'submit' not in port.calls
    assert not any(isinstance(call, tuple) for call in port.calls)
    assert not port.journal.events()


@pytest.mark.parametrize('failure', ['unknown', 'exception'])
def test_unknown_submit_keeps_inflight_and_never_restores_or_retries(tmp_path, failure):
    port = port_at_target()
    if failure == 'unknown':
        port.settled = False
    else:
        def lost(quote):
            port.calls.append('submit')
            raise RuntimeError('submit outcome unknown')
        port.submit = lost
    result = invoke_fit(tmp_path, port, execute=True)
    assert result['outcome'].startswith('unknown_') and result['consumption_sent']
    assert 'home' not in port.calls and port.calls.count('read') == 1
    assert not any(isinstance(call, tuple) for call in port.calls)
    assert port.journal.events()[-1]['state'] == 'in_flight'
    again = port_at_target()
    assert invoke_fit(tmp_path, again, execute=True)['outcome'] == 'blocked_in_flight'
    assert again.calls == []


def test_noop_preserves_original_native_evidence_without_fabricating_it(tmp_path):
    port = port_at_target()
    expected = deepcopy(port._count_receipt)
    result = invoke_fit(tmp_path, port, preview=True)
    assert result['count_fit']['adjustment']['initial_evidence'] == expected
    assert port._count_receipt == expected and not result['consumption_sent']
    assert port.calls == ['read', 'count', 'quote', 'confirm', 'cancel', 'home']


@pytest.mark.parametrize('phase', ['read_count', 'quote'])
def test_refresh_crossing_in_noop_path_stops_before_confirmation(tmp_path, monkeypatch, phase):
    from datetime import datetime
    from pcr_maa import preset_execution
    from test_game_day_policy import freeze
    clock = freeze(monkeypatch, preset_execution, '2026-09-23T14:00:00+08:00')
    port = port_at_target()
    original = getattr(port, phase)
    def crossing(*args):
        result = original(*args)
        clock[0] = datetime.fromisoformat('2026-09-24T05:00:00+08:00')
        return result
    setattr(port, phase, crossing)
    result = invoke_fit(tmp_path, port, execute=True)
    assert result['outcome'] == 'need_new_observation' and not result['consumption_sent']
    assert 'confirm' not in port.calls and 'submit' not in port.calls
    assert not any(isinstance(call, tuple) for call in port.calls)
    assert not port.journal.events()
