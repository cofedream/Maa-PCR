"""Known simulation damage and unknown return navigation remain separate."""
import json
from types import SimpleNamespace as NS

import pytest

from test_clan_battle import Port, plan
from test_clan_battle_native import identity, calibration
from pcr_maa import clan_battle, clan_battle_native as native_port
from pcr_maa.preset_execution import Journal


def test_damage_is_settled_before_single_return_and_full_evidence_is_reported(plan, tmp_path):
    journal = Journal(tmp_path / 'ledger')
    port = Port(plan)
    original = port.return_to_target
    def returned(p):
        events = journal.events()
        assert len(events) == 2 and events[-1]['damage_verified'] is True
        return original(p)
    port.return_to_target = returned
    result = clan_battle.execute_simulation(port, journal, plan)
    assert result['outcome'] == 'simulated' and result['return_verified'] is True
    assert result['return_error'] is None and result['return_evidence']['simulation'] is True
    assert result['intent_id'] == journal.events()[0]['intent_id']
    assert port.starts == port.returns == 1 and len(journal.events()) == 2


@pytest.mark.parametrize('failure', ['stopped', 'clan_battle_disconnected_6002:battle_start=sent',
    'clan_battle_page_unknown', 'clan_battle_return_readback_unknown'])
def test_return_failure_preserves_known_damage_and_never_reopens(plan, tmp_path, failure):
    journal = Journal(tmp_path / 'ledger')
    port = Port(plan)
    def failed(p):
        port.returns += 1
        assert len(journal.events()) == 2
        raise RuntimeError(failure)
    port.return_to_target = failed
    result = clan_battle.execute_simulation(port, journal, plan)
    assert result['outcome'] == 'simulated' and result['damage'] == 12345678
    assert result['damage_verified'] is result['settings_verified'] is result['settlement_verified'] is True
    assert result['return_verified'] is False and result['return_evidence'] is None
    assert result['error'] == 'clan_battle_return_unverified' and result['return_error'] == failure
    assert port.prepared == port.starts == port.returns == 1
    assert len(journal.events()) == 2 and journal.events()[-1]['damage_verified'] is True


@pytest.mark.parametrize('patch', [dict(return_verified=False), dict(boss=1), dict(simulation=False),
    dict(account_id='999'), dict(server='jp'), dict(native_evidence={})])
def test_wrong_return_page_never_claims_navigation_complete(plan, tmp_path, patch):
    port = Port(plan)
    result = port.return_to_target(plan)
    result.update(patch)
    port.returns = 0
    port.return_to_target = lambda _: result
    report = clan_battle.execute_simulation(port, Journal(tmp_path / 'ledger'), plan)
    assert report['damage_verified'] is True and report['return_verified'] is False
    assert report['error'] == 'clan_battle_return_unverified'


def test_unknown_result_never_attempts_return(plan, tmp_path):
    port = Port(plan, fail='unknown_damage')
    result = clan_battle.execute_simulation(port, Journal(tmp_path / 'ledger'), plan)
    assert result['outcome'] == 'stopped' and port.returns == 0


def test_return_input_not_sent_never_changes_known_battle_settlement(plan, tmp_path):
    from pcr_maa.native import NativeInputNotSent
    journal = Journal(tmp_path / 'ledger')
    port = Port(plan)
    def failed(_):
        port.returns += 1
        raise NativeInputNotSent(dict(input_execution='not_sent', clan_battle_phase='result_return'))
    port.return_to_target = failed
    result = clan_battle.execute_simulation(port, journal, plan)
    assert result['outcome'] == 'simulated' and result['damage_verified'] is True
    assert result['settlement_verified'] is True and result['return_verified'] is False
    assert result['error'] == 'clan_battle_return_unverified'
    assert port.starts == port.returns == 1
    events = journal.events()
    assert len(events) == 2 and events[-1]['damage_verified'] is True
    assert events[-1].get('input_execution') != 'not_sent'


def test_new_explicit_start_after_return_failure_still_requires_fresh_target(plan, tmp_path):
    journal = Journal(tmp_path / 'ledger')
    first = Port(plan)
    def failed_return(_):
        first.returns += 1
        raise RuntimeError('clan_battle_page_unknown:target')
    first.return_to_target = failed_return
    result = clan_battle.execute_simulation(first, journal, plan)
    assert result['damage_verified'] is True and result['return_verified'] is False
    assert first.starts == first.returns == 1 and len(journal.events()) == 2
    before = journal.path.read_bytes()
    second = Port(plan)
    def unknown_target(_):
        second.prepared += 1
        raise RuntimeError('clan_battle_page_unknown:target')
    second.prepare = unknown_target
    stopped = clan_battle.execute_simulation(second, journal, plan)
    assert stopped['outcome'] == 'stopped' and stopped['simulation_started'] is False
    assert second.prepared == 1 and second.starts == second.returns == 0
    assert journal.path.read_bytes() == before
    # A later explicit Start with a freshly verified target is a new invocation.
    third = Port(plan)
    complete = clan_battle.execute_simulation(third, journal, plan)
    assert complete['return_verified'] is True and third.starts == third.returns == 1
    events = journal.events()
    assert len(events) == 4 and events[0]['intent_id'] != events[2]['intent_id']


def test_owner_return_failure_writes_stopped_report_with_known_result(plan, tmp_path, monkeypatch):
    port = Port(plan)
    def failed(_):
        port.returns += 1
        raise RuntimeError('clan_battle_page_timeout')
    port.return_to_target = failed
    monkeypatch.setattr('pcr_maa.native.ROOT', tmp_path)
    monkeypatch.setattr('pcr_maa.clan_battle_calibration.load_calibration', lambda _: {'synthetic': True})
    monkeypatch.setattr(native_port, 'NativeClanBattle', lambda *args, **kwargs: port)
    report = clan_battle.run_owned(NS(), {'clan_battle_simulation': plan}, tmp_path / 'report')
    result = report['steps'][0]['result']
    assert report['outcome'] == 'stopped' and report['supported_chain_complete'] is False
    assert result['outcome'] == 'simulated' and result['damage_verified'] is True
    assert result['return_verified'] is False and port.starts == port.returns == 1
    assert json.loads((tmp_path / 'report/report.json').read_text(encoding='utf-8')) == report


def test_native_return_has_one_guarded_action_then_actual_target_readback(plan, calibration, tmp_path, monkeypatch):
    port = native_port.NativeClanBattle(NS(), tmp_path, calibration=calibration)
    port.account = plan['account_id']
    calls = []
    def read(page):
        calls.append(('read', page))
        return identity(plan), {'observed_page': page}
    monkeypatch.setattr(port, 'read_page', read)
    monkeypatch.setattr(port, 'run', lambda node, **kwargs: calls.append((node, kwargs)) or {'action': node})
    monkeypatch.setattr(port, 'wait', lambda names, **kwargs: calls.append(('wait', names, kwargs)) or names[0])
    result = port.return_to_target(plan)
    assert result['return_verified'] is True and result['boss'] == plan['boss']
    assert calls == [('read', 'result'), ('CBAction_return_to_target', {'click': True}),
        ('wait', ['CBPage_target'], {'seconds': 15}), ('read', 'target')]
    assert set(result['native_evidence']) == {'before', 'action', 'target'}


def test_native_return_mismatched_result_page_never_clicks(plan, calibration, tmp_path, monkeypatch):
    port = native_port.NativeClanBattle(NS(), tmp_path, calibration=calibration)
    monkeypatch.setattr(port, 'read_page', lambda _: (dict(identity(plan), simulation=False), {}))
    monkeypatch.setattr(port, 'run', lambda *a, **k: pytest.fail('must not click'))
    with pytest.raises(RuntimeError, match='target_mismatch'):
        port.return_to_target(plan)


@pytest.mark.parametrize('failure', ['stopped', 'clan_battle_disconnected_6002:battle_start=sent', 'unknown_after_return'])
def test_native_return_never_repeats_action_after_wait_failure(plan, calibration, tmp_path, monkeypatch, failure):
    port = native_port.NativeClanBattle(NS(), tmp_path, calibration=calibration)
    monkeypatch.setattr(port, 'read_page', lambda _: (identity(plan), {'result': True}))
    actions = []
    monkeypatch.setattr(port, 'run', lambda node, **kwargs: actions.append(node) or {'action': node})
    def failed(*args, **kwargs):
        raise RuntimeError(failure)
    monkeypatch.setattr(port, 'wait', failed)
    with pytest.raises(RuntimeError, match=failure):
        port.return_to_target(plan)
    assert actions == ['CBAction_return_to_target']
