"""Isolated synthetic scope checks; no live capability or account is granted."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from pcr_maa import clan_battle_daily_native as daily
from test_clan_battle_daily_native import executable, profile, execution_context
from test_clan_battle_native import calibration, calibration_v2, own_only_profile, plan
from test_clan_battle_calibration import bundle, v2_bundle, daily_bundle, daily_execution_bundle


@pytest.fixture
def regular_only(executable):
    value = own_only_profile(deepcopy(executable))
    section = value['daily']['execution']
    for page in ('support', 'carry', 'used_units'):
        section['pages'].pop(page)
    for action in ('open_support', 'support_back', 'support_top', 'support_next',
                   'close_carry', 'open_carry_for_target', 'select_carry', 'open_used', 'close_used'):
        section['actions'].pop(action)
    section.pop('used_units')
    section['formation_owner'] = None
    section['carry_slots'] = []
    section['formation_mode']['stock_index'] = section['formation_mode']['time'] = None
    section['samples'] = [row for row in section['samples'] if row['page'] not in ('support', 'carry', 'used_units')]
    return value


def test_isolated_copy_is_the_only_code_under_test():
    root = Path(__file__).resolve().parents[1]
    assert Path(daily.__file__).resolve().is_relative_to(root / 'src')


def test_regular_own_scope_uses_only_calibrated_paths(regular_only, plan):
    nodes = daily.require_daily_execution(regular_only, plan, capability='formal')
    assert {'CBPage_carry_empty', 'CBAction_open_carry', 'CBAction_close_empty_carry',
        'CBPage_battle', 'CBPage_result', 'CBDailyFormationRegular', 'CBDailyFormationCarryover'} <= nodes.keys()
    assert not {'CBPage_support', 'CBPage_carry', 'CBAction_open_support', 'CBAction_support_back',
        'CBAction_select_carry', 'CBDailyFormationOwner', 'CBDailyFormationStock', 'CBDailyFormationTime'} & nodes.keys()


def test_formal_and_carryover_share_nodes_except_pause_clock(executable, plan):
    formal = daily.require_daily_execution(executable, plan, capability='formal')
    carry = daily.require_daily_execution(executable, plan, capability='carryover')
    assert {key for key in formal if formal[key] != carry[key]} == {'CBDailyPauseOpening', 'CBDailyPauseRetry'}


@pytest.mark.parametrize('part', ['support', 'carry'])
def test_unused_scopes_can_be_omitted_independently(executable, regular_only, plan, part):
    value = own_only_profile(deepcopy(executable))
    section = value['daily']['execution']
    section['pages'].pop(part)
    section['samples'] = [row for row in section['samples'] if row['page'] != part]
    names = (('open_support', 'support_back', 'support_top', 'support_next') if part == 'support'
        else ('close_carry', 'open_carry_for_target', 'select_carry'))
    for name in names: section['actions'].pop(name)
    if part == 'support': section['formation_owner'] = None
    else:
        section['carry_slots'] = []
        section['formation_mode']['stock_index'] = section['formation_mode']['time'] = None
    nodes = daily.require_daily_execution(value, plan, capability='formal')
    assert 'CBPage_' + part not in nodes
    assert 'CBPage_' + ('carry' if part == 'support' else 'support') in nodes


@pytest.mark.parametrize('regular,carryover', [(False, False), (False, True), (True, True)])
def test_regular_formation_keeps_both_mode_guards(regular_only, plan, regular, carryover):
    import numpy as np
    _, _, hits, context = execution_context(regular_only, plan, 'formation')
    if regular: hits.add('CBDailyFormationRegular')
    if carryover: hits.add('CBDailyFormationCarryover')
    port = NS(action={'kind': 'regular'}, pending=None, started=False, battle_start='not_sent')
    page = daily.ClanBattleDailyExecutionPage(regular_only, plan, port)
    page.current = 'formation'
    page.bound = dict(period=plan['period'], boss=plan['boss'], phase='B', round=5, instance_id='live', formal=True)
    with pytest.raises(RuntimeError, match='consumption_mode_unknown'):
        page.read(context, np.zeros((720, 1280, 3), np.uint8), 'formation')


def test_regular_formation_accepts_exactly_its_guard(regular_only, plan):
    import numpy as np
    _, _, hits, context = execution_context(regular_only, plan, 'formation')
    hits.add('CBDailyFormationRegular')
    port = NS(action={'kind': 'regular'}, pending=None, started=False, battle_start='not_sent')
    page = daily.ClanBattleDailyExecutionPage(regular_only, plan, port)
    page.current = 'formation'
    page.bound = dict(period=plan['period'], boss=plan['boss'], phase='B', round=5, instance_id='live', formal=True)
    assert page.read(context, np.zeros((720, 1280, 3), np.uint8), 'formation')['battle_kind'] == 'regular'


@pytest.mark.parametrize('owns_all', [True, False])
def test_qsjs_only_uses_own_scope_when_every_character_is_owned(regular_only, plan, monkeypatch, owns_all):
    from pcr_maa import clan_battle
    plan = dict(plan, support_mode='qsjs')
    units = plan['unit_ids'] if owns_all else plan['unit_ids'][:-1]
    monkeypatch.setattr(clan_battle, 'load_snapshot', lambda _: dict(ownership_complete=True,
        units=[dict(unit_id=uid) for uid in units]))
    if owns_all:
        assert 'CBAction_start' not in daily.require_daily_execution(regular_only, plan, capability='formal')
    else:
        with pytest.raises(RuntimeError, match='support_mode_uncalibrated'):
            daily.require_daily_execution(regular_only, plan, capability='formal')


def test_regular_team_rejects_a_borrowed_character(regular_only, plan):
    port = NS(action={'kind': 'regular'}, support_unit_id=None)
    page = daily.ClanBattleDailyExecutionPage(regular_only, plan, port)
    page.bound = dict(period=plan['period'], boss=plan['boss'], phase='B', round=5, instance_id='live', formal=True)
    reading = dict(page.bound, battle_kind='regular', member_count=5, unit_ids=plan['unit_ids'], stars=plan['stars'],
        support_unit_id=plan['unit_ids'][0], support_owner_viewer_id='987654321')
    with pytest.raises(RuntimeError, match='team_or_mode_unknown'):
        page.require_team(reading)


@pytest.mark.parametrize('mutation', ['no_own_scope', 'orphan_support_action', 'orphan_support_owner',
    'orphan_carry_action', 'orphan_carry_slot', 'orphan_carry_roi', 'orphan_carry_sample'])
def test_partial_groups_are_not_silently_accepted(regular_only, executable, plan, mutation):
    section = regular_only['daily']['execution']
    original = executable['daily']['execution']
    if mutation == 'no_own_scope': regular_only.pop('support_modes')
    elif mutation == 'orphan_support_action': section['actions']['open_support'] = original['actions']['open_support']
    elif mutation == 'orphan_support_owner': section['formation_owner'] = original['formation_owner']
    elif mutation == 'orphan_carry_action': section['actions']['select_carry'] = original['actions']['select_carry']
    elif mutation == 'orphan_carry_slot': section['carry_slots'] = original['carry_slots']
    elif mutation == 'orphan_carry_roi': section['formation_mode']['time'] = original['formation_mode']['time']
    else: section['samples'].append(next(row for row in original['samples'] if row['page'] == 'carry'))
    with pytest.raises((RuntimeError, ValueError)):
        daily.require_daily_execution(regular_only, plan, capability='formal')


def test_regular_only_profile_never_qualifies_carryover(regular_only, plan):
    with pytest.raises((RuntimeError, ValueError)):
        daily.require_daily_execution(regular_only, plan, capability='carryover')


@pytest.mark.parametrize('count', [1, 2, 3, None, True])
def test_unread_nonempty_or_unknown_inventory_stops_before_input(regular_only, count):
    port = object.__new__(daily.NativeClanDailyAdapter)
    port.calibration = regular_only
    calls = []
    port.read_page = lambda page: (dict(carry_count=count), {'page': page})
    port.run = lambda *args, **kwargs: calls.append((args, kwargs))
    with pytest.raises(RuntimeError, match='carry_inventory_unknown'):
        port.inventory()
    assert calls == []


def test_proven_empty_inventory_uses_actual_empty_screen(regular_only):
    port = object.__new__(daily.NativeClanDailyAdapter)
    port.calibration = regular_only
    port.now = lambda: datetime(2026, 10, 27, tzinfo=timezone.utc)
    calls = []
    port.read_page = lambda page: (dict(carry_count=0) if page == 'lobby' else
        dict(carry_stock_indices=[]), {'page': page})
    port.run = lambda node, **kwargs: calls.append(('run', node))
    def wait(names, **kwargs):
        calls.append(('wait', tuple(names)))
        port.last_receipt = {'qualified_page': names[0]}
        return names[0]
    port.wait = wait
    stocks, receipt, lobby_receipt = port.inventory()
    assert lobby_receipt == {'qualified_page': 'CBPage_lobby'}
    assert stocks == [] and receipt == {'page': 'carry_empty'}
    assert calls == [('run', 'CBAction_open_carry'), ('wait', ('CBPage_carry_empty',)),
        ('run', 'CBAction_close_empty_carry'), ('wait', ('CBPage_lobby',))]


def test_full_inventory_uses_existing_path_without_extra_lobby_read(executable):
    port = object.__new__(daily.NativeClanDailyAdapter)
    port.calibration = executable
    port.now = lambda: datetime(2026, 10, 27, tzinfo=timezone.utc)
    calls = []
    port.read_page = lambda page: (dict(stocks=[{'stock_index': 5}]), {'page': page})
    port.run = lambda node, **kwargs: calls.append(('run', node))
    def wait(names, **kwargs):
        calls.append(('wait', tuple(names)))
        hit = 'CBPage_carry' if 'CBPage_carry' in names else names[0]
        port.last_receipt = {'qualified_page': hit}
        return hit
    port.wait = wait
    stocks, receipt, lobby_receipt = port.inventory()
    assert lobby_receipt == {'qualified_page': 'CBPage_lobby'}
    assert stocks == [{'stock_index': 5}] and receipt == {'page': 'carry'}
    assert calls == [('run', 'CBAction_open_carry'), ('wait', ('CBPage_carry_empty', 'CBPage_carry')),
        ('run', 'CBAction_close_carry'), ('wait', ('CBPage_lobby',))]


def test_nonempty_screen_after_zero_count_is_not_claimed_empty(regular_only):
    port = object.__new__(daily.NativeClanDailyAdapter)
    port.calibration = regular_only
    calls = []
    port.read_page = lambda page: (dict(carry_count=0), {'page': page})
    port.run = lambda node, **kwargs: calls.append(node)
    port.wait = lambda names, **kwargs: 'CBPage_carry'
    with pytest.raises(RuntimeError, match='carry_inventory_unknown'):
        port.inventory()
    assert calls == ['CBAction_open_carry']


def test_empty_screen_without_empty_inventory_readback_stops(regular_only):
    port = object.__new__(daily.NativeClanDailyAdapter)
    port.calibration = regular_only
    calls = []
    port.read_page = lambda page: (dict(carry_count=0) if page == 'lobby' else {}, {'page': page})
    port.run = lambda node, **kwargs: calls.append(node)
    port.wait = lambda names, **kwargs: names[0]
    with pytest.raises(RuntimeError, match='carry_inventory_unknown'):
        port.inventory()
    assert calls == ['CBAction_open_carry']


@pytest.mark.parametrize(('capability', 'caps'), [
    ('formal', ['formal']), ('formal', ['formal', 'carryover']), ('carryover', ['formal', 'carryover'])])
def test_loader_forwards_capability_to_daily_scope(v2_bundle, plan, monkeypatch, capability, caps):
    from pcr_maa import clan_battle_calibration as loader
    root, _, entry, _, save = v2_bundle
    entry['capabilities'] = caps
    save()
    calls = []
    monkeypatch.setattr(daily, 'require_daily_execution', lambda *args, **kwargs: calls.append(kwargs) or {})
    loader.load_calibration(plan, root=root, capability=capability)
    assert calls == [dict(require_scope=True, capability=capability, require_carry_pages='carryover' in caps)]


def test_loader_never_advertises_carryover_with_a_partial_profile(daily_execution_bundle, plan):
    from pcr_maa import clan_battle_calibration as loader
    root, profile, entry, _, save = daily_execution_bundle
    profile['support_modes'] = ['none']
    execution = profile['daily']['execution']
    for page in ('support', 'carry'):
        execution['pages'].pop(page)
    for name in ('open_support', 'support_back', 'support_top', 'support_next',
                 'close_carry', 'open_carry_for_target', 'select_carry'):
        execution['actions'].pop(name)
    execution['formation_owner'] = None
    execution['carry_slots'] = []
    execution['formation_mode']['stock_index'] = execution['formation_mode']['time'] = None
    execution['samples'] = [row for row in execution['samples'] if row['page'] not in ('support', 'carry')]
    entry['capabilities'] = ['formal']
    save()
    assert loader.load_calibration(plan, root=root, capability='formal')['daily']['execution']['carry_slots'] == []
    entry['capabilities'] = ['formal', 'carryover']
    save()
    for capability in ('formal', 'carryover'):
        with pytest.raises(ValueError, match='execution_scope_missing'):
            loader.load_calibration(plan, root=root, capability=capability)


def test_simulation_does_not_hide_a_claimed_but_missing_carryover_profile(v2_bundle, plan):
    from pcr_maa import clan_battle_calibration as loader
    root, profile, entry, _, save = v2_bundle
    profile.pop('daily', None)
    entry['capabilities'] = ['simulation', 'formal', 'carryover']
    save()
    with pytest.raises(ValueError, match='daily_calibration_invalid'):
        loader.load_calibration(plan, root=root, capability='simulation')


def test_own_only_lobby_navigation_does_not_reference_support_page(regular_only):
    port = object.__new__(daily.NativeClanDailyAdapter)
    port.calibration = regular_only
    seen = []
    port.wait = lambda names, **kwargs: seen.extend(names) or 'CBPage_lobby'
    port.account_binding_valid = lambda _plan: True
    port.plan = {'account_id': '123456789'}
    port.enter_lobby()
    assert 'CBPage_support' not in seen
