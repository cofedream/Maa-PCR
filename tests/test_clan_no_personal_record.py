"""Live counters and validated Journal history replace personal-record navigation."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from pcr_maa import clan_battle_batch as batch
from pcr_maa import clan_battle_daily_native as daily
from pcr_maa.game_day import BEIJING, current_game_day
from pcr_maa.preset_execution import Journal
from test_clan_battle_batch import daily_request, Port, ledgers, run, NOW
from test_clan_battle_daily_native import executable, profile
from test_clan_battle_native import calibration, calibration_v2, plan, receipt


def omit_personal_record(value):
    execution = value['daily']['execution']
    del execution['used_units']
    del execution['pages']['used_units']
    for name in ('open_used', 'close_used'):
        del execution['actions'][name]
    execution['samples'] = [row for row in execution['samples'] if row['page'] != 'used_units']


def test_personal_calibration_group_is_optional_and_legacy_still_parses(executable, plan):
    assert 'CBAction_open_used' in daily.require_daily_execution(executable, plan)
    omit_personal_record(executable)
    nodes = daily.require_daily_execution(executable, plan)
    assert 'CBAction_open_used' not in nodes
    assert 'CBAction_close_used' not in nodes
    assert not any(name.startswith(('CBDailyUsed', 'CBDaily_used_units')) for name in nodes)


@pytest.mark.parametrize('member', ['used_units', 'page', 'open_used', 'close_used', 'sample'])
def test_partial_personal_group_is_rejected(executable, plan, member):
    execution = executable['daily']['execution']
    if member == 'used_units':
        del execution['used_units']
    elif member == 'page':
        del execution['pages']['used_units']
    elif member == 'sample':
        execution['samples'] = [row for row in execution['samples'] if row['page'] != 'used_units']
    else:
        del execution['actions'][member]
    with pytest.raises(ValueError):
        daily.require_daily_execution(executable, plan)


def observer(monkeypatch, request, *, regular=None, remaining=3, cp=900, elapsed=0, start=NOW):
    port = daily.NativeClanDailyAdapter.__new__(daily.NativeClanDailyAdapter)
    port.plan = request['slot_plans']['1']
    port.calibration = {'daily': {'execution': {'formation_mode': {'carryover': 'synthetic-supported'}}}}
    port.tasker = NS()
    port.last_bosses, port.boss_history = {}, {}
    port.account_receipt = {'synthetic_account': port.plan['account_id']}
    port.pending, port.started, port.battle_start = None, False, 'not_sent'
    binding = batch._binding(request, start)
    port.bind_history(binding, regular or {})
    clock, calls = [start], []
    monkeypatch.setattr(port, 'now', lambda: clock[0])
    monkeypatch.setattr(port, 'enter_lobby', lambda: calls.append('enter_lobby'))
    monkeypatch.setattr(port, 'recover_inheritance', lambda: calls.append('recover'))
    def inventory():
        calls.append('inventory')
        clock[0] += timedelta(seconds=elapsed)
        return [], {'synthetic_empty_stock': True}, read('lobby')[1]
    monkeypatch.setattr(port, 'inventory', inventory)
    def read(page):
        assert page == 'lobby', 'personal_record_must_not_be_read'
        calls.append('fresh_lobby')
        value = dict(page='lobby', period=port.plan['period'], remaining_count=remaining, carry_count=0, cp=cp,
            bosses=[dict(boss=n, phase='B', round=1, available=True) for n in range(1, 6)])
        return value, receipt(value)
    monkeypatch.setattr(port, 'read_page', read)
    return port, binding, calls


def test_three_live_attempts_prove_empty_consumption_history_without_record_page(daily_request, monkeypatch):
    port, binding, calls = observer(monkeypatch, daily_request)
    value = port.observe(binding)
    assert value['remaining_count'] == 3 and value['used_unit'] == []
    assert value['reset_evidence']['consumption_history'] == dict(source='journal_and_live_counter',
        settled_regular_intents=[], external_regular_observed_slots=[],
        external_regular_unconfirmed=False, pending_result_intent=None)
    assert 'personal_record' not in value['reset_evidence']
    assert calls == ['enter_lobby', 'recover', 'inventory', 'fresh_lobby']


@pytest.mark.parametrize('cp,remaining', [(0, 0), (300, 1), (600, 2)])
def test_daily_earned_partial_cp_proves_no_consumption_without_history(daily_request, monkeypatch, cp, remaining):
    port, binding, _ = observer(monkeypatch, daily_request, cp=cp, remaining=remaining)
    value = port.observe(binding)
    assert value['cp'] == cp and value['remaining_count'] == remaining and value['used_unit'] == []
    assert port.regular_history == {}


@pytest.mark.parametrize('cp,remaining', [(300, 0), (600, 1), (900, 2)])
def test_consumed_counter_without_matching_history_stops(daily_request, monkeypatch, cp, remaining):
    port, binding, _ = observer(monkeypatch, daily_request, cp=cp, remaining=remaining)
    with pytest.raises(RuntimeError, match='count_or_inventory_changed'):
        port.observe(binding)
    assert port.regular_history == {}


@pytest.mark.parametrize('elapsed', [61, -1])
def test_observation_window_keeps_sixty_second_and_rollback_guards(daily_request, monkeypatch, elapsed):
    port, binding, _ = observer(monkeypatch, daily_request, elapsed=elapsed)
    with pytest.raises(RuntimeError, match='observation_window_invalid'):
        port.observe(binding)


def test_observation_crossing_five_am_stops(daily_request, monkeypatch):
    port, binding, _ = observer(monkeypatch, daily_request, elapsed=2,
        start=datetime(2026, 10, 25, 4, 59, 59, tzinfo=BEIJING))
    assert binding['game_day'] == '2026-10-24'
    with pytest.raises(RuntimeError, match='observation_window_invalid'):
        port.observe(binding)


def test_different_bound_day_stops_before_navigation(daily_request, monkeypatch):
    port, binding, calls = observer(monkeypatch, daily_request)
    binding['game_day'] = '2026-10-24'
    port.bind_history(binding, {})
    with pytest.raises(RuntimeError, match='game_day_changed'):
        port.observe(binding)
    assert calls == []


def settled_first(tmp_path, request, *, cp=900):
    port = Port(request, now=NOW)
    port.cp, port.remaining = cp, cp // 300
    def stop_after_settled(intent, result):
        raise RuntimeError('synthetic_stop_after_settlement')
    port.after_settlement = stop_after_settled
    result, _ = run(tmp_path, request, port, now=port.now())
    assert result['regular_settled'] == 1 and result['error'] == 'synthetic_stop_after_settlement', result.get('error')
    journal = Journal(ledgers(tmp_path)[0].parent)
    events = journal.events()
    binding = batch._binding(request, port.now())
    regular, _, _, pending, _ = batch._history(events, request, binding)
    assert not pending
    return regular, port.executions[0]


def test_known_consumption_uses_actual_ready_members_and_accepts_later_cp_gain(daily_request, tmp_path, monkeypatch):
    regular, _ = settled_first(tmp_path, daily_request, cp=300)
    port, binding, _ = observer(monkeypatch, daily_request, regular=regular, remaining=1, cp=600)
    value = port.observe(binding)
    assert value['used_unit'] == regular[1]['intent']['ready']['unit_ids']
    assert value['reset_evidence']['consumption_history']['settled_regular_intents'] == [regular[1]['intent']['intent_id']]


def test_native_history_boundary_copies_external_nested_state(daily_request, tmp_path):
    regular, _ = settled_first(tmp_path, daily_request)
    binding = batch._binding(daily_request, NOW)
    expected_binding, expected_regular = deepcopy(binding), deepcopy(regular)
    port = daily.NativeClanDailyAdapter.__new__(daily.NativeClanDailyAdapter)
    port.bind_history(binding, regular)
    binding['slot_confirmations']['1'] = 'synthetic_changed_confirmation'
    regular[1]['intent']['ready']['unit_ids'][0] = -1
    assert port.history_binding == expected_binding and port.regular_history == expected_regular


def test_pending_result_is_only_a_temporary_candidate(daily_request, tmp_path, monkeypatch):
    _, intent = settled_first(tmp_path, daily_request)
    port, binding, _ = observer(monkeypatch, daily_request, remaining=2)
    port.pending, port.started, port.battle_start = deepcopy(intent), True, 'sent'
    value = port.observe(binding, result_intent=intent)
    assert value['used_unit'] == intent['ready']['unit_ids']
    assert value['reset_evidence']['consumption_history']['pending_result_intent'] == intent['intent_id']
    assert value['reset_evidence']['consumption_history']['settled_regular_intents'] == []
    assert port.regular_history == {} and port.pending == intent


def test_unknown_pending_or_inconsistent_candidate_never_promotes_history(daily_request, tmp_path, monkeypatch):
    _, intent = settled_first(tmp_path, daily_request)
    port, binding, calls = observer(monkeypatch, daily_request, remaining=3)
    port.pending, port.started, port.battle_start = deepcopy(intent), True, 'sent'
    with pytest.raises(RuntimeError, match='previous_input_unresolved'):
        port.observe(binding)
    assert calls == []
    with pytest.raises(RuntimeError, match='count_or_inventory_changed'):
        port.observe(binding, result_intent=intent)
    assert port.regular_history == {} and port.pending == intent


def test_batch_binds_copies_and_reloads_settled_history_on_each_explicit_start(daily_request, tmp_path):
    port = Port(daily_request)
    seen = []
    def bind_history(binding, regular):
        daily.NativeClanDailyAdapter.bind_history(port, binding, regular)
        seen.append(len(regular))
        port.history_binding['account_id'] = 'synthetic_mutation'
        for row in port.regular_history.values():
            row['intent']['ready']['unit_ids'][0] = -1
    port.bind_history = bind_history
    result, _ = run(tmp_path, daily_request, port)
    assert result['outcome'] == 'complete' and seen == [0, 1, 2, 3]
    before = ledgers(tmp_path)[0].read_bytes()
    again, _ = run(tmp_path, daily_request, port)
    assert again['outcome'] == 'complete' and seen[-1] == 3
    assert len(port.executions) == 3 and ledgers(tmp_path)[0].read_bytes() == before


def test_pending_journal_blocks_repeated_start_before_binding_or_navigation(daily_request, tmp_path):
    port = Port(daily_request)
    seen = []
    port.bind_history = lambda binding, regular: seen.append(len(regular))
    port.fail = 'synthetic_unknown_after_intent'
    first, _ = run(tmp_path, daily_request, port)
    assert first['error'] == port.fail and seen == [0]
    before, observations = ledgers(tmp_path)[0].read_bytes(), len(port.observations)
    port.fail = None
    again, _ = run(tmp_path, daily_request, port)
    assert again['error'] == 'daily_previous_result_unknown'
    assert seen == [0] and len(port.observations) == observations
    assert ledgers(tmp_path)[0].read_bytes() == before


@pytest.mark.parametrize('navigation_seconds', [61, 120])
def test_observation_window_starts_after_identity_and_settings_navigation(daily_request, monkeypatch, navigation_seconds):
    start = datetime(2026,10,25,12,tzinfo=BEIJING)
    port, binding, calls = observer(monkeypatch,daily_request,start=start,elapsed=2)
    clock = iter([start, start+timedelta(seconds=navigation_seconds),
        start+timedelta(seconds=navigation_seconds+2)])
    monkeypatch.setattr(port,'now',lambda:next(clock))
    value = port.observe(binding)
    assert datetime.fromisoformat(value['reset_evidence']['observation_window']['started_at']) == start+timedelta(seconds=navigation_seconds)
    assert datetime.fromisoformat(value['reset_evidence']['observation_window']['ended_at']) == start+timedelta(seconds=navigation_seconds+2)
    assert calls == ['enter_lobby','recover','inventory','fresh_lobby']


def test_navigation_crossing_game_day_stops_before_inventory(daily_request, monkeypatch):
    start = datetime(2026,10,25,4,59,59,tzinfo=BEIJING)
    port,binding,calls = observer(monkeypatch,daily_request,start=start)
    clock = [start]
    monkeypatch.setattr(port,'now',lambda:clock[0])
    def entered():
        calls.append('enter_lobby')
        clock[0] += timedelta(seconds=2)
    monkeypatch.setattr(port,'enter_lobby',entered)
    with pytest.raises(RuntimeError,match='game_day_changed'):
        port.observe(binding)
    assert calls == ['enter_lobby','recover']
