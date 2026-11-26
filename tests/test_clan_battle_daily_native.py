"""Offline page contracts; these do not constitute formal-battle acceptance."""
from copy import deepcopy
import gc
import hashlib
import json
from types import SimpleNamespace as NS
import weakref
import time
from datetime import timedelta

import numpy as np
import pytest

from pcr_maa import clan_battle_daily_native as daily
from test_clan_battle_native import calibration, calibration_v2, context_for, plan, Registry, receipt, result_page_receipt
from test_clan_battle_batch import daily_request, Port
from test_agent_callbacks import registry, FakeContext


@pytest.fixture
def profile(calibration_v2):
    value = deepcopy(calibration_v2)
    label = dict(roi=[10, 10, 100, 30], expected='^离线页面锚点$')
    value['daily'] = dict(version=1, cp_semantics='daily_earned', pages={
        page: dict(labels=[deepcopy(label), deepcopy(label)], rejects=[deepcopy(label)],
            fields={field: [100, 100 + index * 50, 300, 40] for index, field in enumerate(sorted(fields))})
        for page, fields in daily.PAGE_FIELDS.items()},
        samples=[dict(value['samples'][0], page=page) for page in daily.PAGE_FIELDS])
    return value


def reading(profile, plan, page, **changes):
    nodes = daily.daily_pipeline(profile, plan)
    values = dict(remaining_count='0', carry_count='0', cp='0/900', health='0/60000000',
        boss='合成魔物3', phase='第2阶段')
    values.update(changes)
    texts = {f'CBDaily_{page}_{field}': values[field] for field in daily.PAGE_FIELDS[page]}
    hits = {f'CBDaily_{page}_labels_{index}' for index in range(2)}
    context = context_for(nodes, texts, hits)
    return daily.ClanBattleDailyPage(profile, plan), context, np.zeros((720, 1280, 3), np.uint8), hits


def test_daily_profile_has_only_read_operations_and_zero_challenges_is_valid(profile, plan):
    nodes = daily.daily_pipeline(profile, plan)
    assert all(node['action'] == 'DoNothing' for node in nodes.values())
    page, context, image, _ = reading(profile, plan, 'lobby')
    actual = page.read(context, image, 'lobby')
    assert actual['remaining_count'] == actual['carry_count'] == actual['cp'] == 0
    assert actual['cp_semantics'] == 'daily_earned'
    assert actual['evidence']['frame_pixel_sha256'] == hashlib.sha256(image.tobytes()).hexdigest()
    assert not {'used_unit', 'round', 'instance_id', 'reset_verified'} & actual.keys()


@pytest.mark.parametrize('page_name', ['lobby', 'target'])
@pytest.mark.parametrize('text,expected', [('已达今天的上限', 900), ('已达今无的上限', 900), ('0/900', 0), ('900/900', 900), ('345 / 900', 345)])
def test_daily_cp_numeric_and_exact_limit_message_share_the_earned_semantics(profile, plan, page_name, text, expected):
    page, context, image, _ = reading(profile, plan, page_name, cp=text)
    actual = page.read(context, image, page_name)
    assert actual['cp'] == expected and actual['cp_semantics'] == 'daily_earned'
    assert context.get_node_data(f'CBDaily_{page_name}_cp')['threshold'] == .9


@pytest.mark.parametrize('text', ['901/900', '1000/900', '900/', '/900', '已达今天的上', '今天的上限',
    '已达今日的上限', '已达今天的上限。', '其他文案', '已达今天的上限900',
    '没达到今天的上限', '未达今天的上限', '未达今无的上限', '已达今无上限', '已达今无的上限900'])
def test_daily_cp_rejects_partial_different_or_out_of_range_text(profile, plan, text):
    page, context, image, _ = reading(profile, plan, 'lobby', cp=text)
    assert page.analyze(context, NS(image=image, custom_recognition_param='{"page":"lobby"}')) is None


def test_daily_formal_detail_distinguishes_defeated_health_without_fabricated_round(profile, plan):
    page, context, image, _ = reading(profile, plan, 'target')
    actual = page.read(context, image, 'target')
    assert actual['formal'] is True and actual['alive'] is False and actual['health'] == 0
    assert actual['boss'] == 3 and actual['phase'] == 'B'
    assert not {'round', 'instance_id', 'appeared_at', 'simulation'} & actual.keys()


def test_daily_formal_detail_reads_current_health_and_cumulative_cp(profile, plan):
    page, context, image, _ = reading(profile, plan, 'target', health='25,000,000 / 60,000,000',
        remaining_count='1', cp='345 / 900')
    actual = page.read(context, image, 'target')
    assert actual['alive'] is True and actual['health'] == 25000000 and actual['health_max'] == 60000000
    assert actual['cp'] == 345 and actual['remaining_count'] == 1


@pytest.mark.parametrize('field,value', [('cp', '901/900'), ('cp', '0/300'),
    ('remaining_count', '4'), ('health', '70000000/60000000'), ('boss', '合成魔物4'),
    ('phase', '未知阶段')])
def test_daily_unknown_or_invalid_values_do_not_produce_observation(profile, plan, field, value):
    page, context, image, _ = reading(profile, plan, 'target', **{field: value})
    assert page.analyze(context, NS(image=image, custom_recognition_param='{"page":"target"}')) is None


def test_daily_reject_markers_block_background_lobby_and_formal_details(profile, plan):
    for location in ('lobby', 'target'):
        page, context, image, hits = reading(profile, plan, location)
        hits.add(f'CBDaily_{location}_rejects_0')
        with pytest.raises(RuntimeError, match='page_unknown'):
            page.read(context, image, location)


def test_unknown_guide_phase_mapping_keeps_visible_label_without_inventing_phase(profile, plan):
    profile['phase_labels'] = {}
    page, context, image, _ = reading(profile, plan, 'target')
    actual = page.read(context, image, 'target')
    assert actual['phase_label'] == '第2阶段' and actual['phase'] is None


def test_empty_carry_inventory_requires_both_modal_and_empty_status_markers(profile, plan):
    page, context, image, hits = reading(profile, plan, 'carry_empty')
    assert page.read(context, image, 'carry_empty')['carry_stock_indices'] == []
    hits.remove('CBDaily_carry_empty_labels_1')
    with pytest.raises(RuntimeError, match='page_unknown'):
        page.read(context, image, 'carry_empty')


@pytest.mark.parametrize('failure', ['stop', 'shape', 'disconnect', 'unknown', 'params'])
def test_daily_page_callback_stops_without_readback(profile, plan, failure):
    page, context, image, hits = reading(profile, plan, 'lobby')
    params = dict(page='lobby')
    if failure == 'stop': context.tasker.stopping = True
    elif failure == 'shape': image = image[:100]
    elif failure == 'disconnect': hits.add('LabyDisconnect6002Observed')
    elif failure == 'unknown': params['page'] = 'formation'
    else: params['authorize'] = True
    assert page.analyze(context, NS(image=image, custom_recognition_param=json.dumps(params))) is None


@pytest.mark.parametrize('change', ['sample', 'hash', 'semantics', 'version', 'fields', 'labels', 'extra'])
def test_daily_profile_requires_all_bound_pages_and_known_cp_semantics(profile, plan, change):
    spec = profile['daily']
    if change == 'sample': spec['samples'].pop()
    elif change == 'hash': spec['samples'][0]['sha256'] = '0' * 64
    elif change == 'semantics': spec['cp_semantics'] = 'available_balance'
    elif change == 'version': spec['version'] = True
    elif change == 'fields': spec['pages']['lobby']['fields']['reset_verified'] = [1, 1, 20, 20]
    elif change == 'labels': spec['pages']['target']['labels'].pop()
    else: spec['execute'] = True
    with pytest.raises(ValueError):
        daily.daily_pipeline(profile, plan)


def test_daily_page_callback_belongs_to_native_resource_and_duplicate_registration_stops(profile, plan, tmp_path):
    owner, borrowed = Registry(), Registry()
    tasker = NS(resource=borrowed, _resource_holder=owner)
    port = daily.NativeClanDailyPages(tasker, tmp_path, calibration=profile, plan=plan)
    callback = weakref.ref(port.page)
    del port
    gc.collect()
    assert callback() is owner.callbacks['ClanBattleDailyPage'] and not borrowed.callbacks
    with pytest.raises(RuntimeError, match='callback_registration_failed'):
        daily.NativeClanDailyPages(tasker, tmp_path, calibration=profile, plan=plan)
    assert owner.callbacks['ClanBattleDailyPage'] is callback()


def test_daily_native_probe_dispatches_only_known_read_node(profile, plan, tmp_path, monkeypatch):
    port = daily.NativeClanDailyPages(NS(resource=Registry()), tmp_path, calibration=profile, plan=plan)
    calls = []
    monkeypatch.setattr(port, 'run', lambda node: calls.append(node) or receipt(dict(page='lobby', cp=0)))
    value, evidence = port.read_page('lobby')
    assert value == dict(page='lobby', cp=0) and evidence['native_ok']
    assert calls == ['CBDailyPage_lobby']
    with pytest.raises(ValueError):
        port.read_page('start')
    assert calls == ['CBDailyPage_lobby']


@pytest.fixture
def executable(profile):
    value = deepcopy(profile)
    daily_spec = value['daily']
    daily_spec['version'] = 2
    daily_spec['pages']['lobby']['fields'].update(period=[10, 10, 200, 30], phase=[220, 10, 120, 30])
    label = dict(roi=[10, 10, 100, 30], expected='^离线页面锚点$')
    def layout(count, y):
        return dict(slots=[[10 + i * 70, y, 55, 55] for i in range(count)],
            empty=[deepcopy(label) for _ in range(count)], borrowed=[deepcopy(label) for _ in range(count)])
    pages = {page: dict(labels=[deepcopy(label), deepcopy(label)], rejects=[deepcopy(label)],
        fields={field: [100, 100 + index * 50, 300, 40] for index, field in enumerate(sorted(fields))})
        for page, fields in daily.EXECUTION_FIELDS.items()}
    actions = {name: dict(page=page, label=deepcopy(label), target=[10, 10, 20, 20])
        for name, page in daily.ACTION_PAGES.items()}
    for name in ('characters_top', 'characters_next', 'support_top', 'support_next'):
        actions[name].update(begin=[1000, 350, 1, 1], end=[1000, 180, 1, 1])
    for name in ('characters_top', 'characters_next'):
        actions[name].update({key: deepcopy(value['actions'][name][key]) for key in ('begin', 'end')})
    daily_spec['execution'] = dict(pages=pages, actions=actions,
        used_units=layout(15, 100), boss_slots={str(boss): dict(round=[100 * boss, 200, 90, 30],
            defeated=deepcopy(label)) for boss in range(1, 6)}, formation_owner=[600, 600, 120, 30],
        speed_one=deepcopy(label), carry_slots=[dict(empty=deepcopy(label), occupied=deepcopy(label),
            stock_index=[100, 300, 100, 30], time=[100, 340, 100, 30], members=layout(5, 100 + index * 150),
            support_owner=[100, 380, 100, 30], target=[100, 300, 200, 200]) for index in range(3)],
        samples=[dict(value['samples'][0], page=page) for page in daily.EXECUTION_FIELDS])
    daily_spec['execution']['formation_mode'] = dict(regular=deepcopy(label), carryover=deepcopy(label),
        stock_index=[600, 550, 100, 30], time=[720, 550, 100, 30])
    from pathlib import Path
    from pcr_maa import native
    index = json.loads((native.ROOT / 'assets/resource/clan-battle-calibrations/index.json').read_text(encoding='utf-8'))
    october = next(entry for entry in index['entries']
                   if entry['server'] == 'cn-bilibili' and entry['content_version'] == '202610141448')
    shipped = json.loads((native.ROOT / october['profile_path']).read_text(encoding='utf-8'))
    inheritance = deepcopy(shipped['auto_battle']['inheritance'])
    for sample in inheritance['samples']:
        sample['path'] = str(native.ROOT / sample['path'])
    daily_spec['execution']['opening'] = dict(battle_speed=2,
        opening_guards=[deepcopy(label) for _ in range(3)], paused_guards=[deepcopy(label) for _ in range(3)],
        clock=[500, 200, 100, 40], auto_on=deepcopy(label), auto_off=deepcopy(label),
        speed={str(n):deepcopy(label) for n in (1,2)}, speed_two=deepcopy(label),
        set_badge=deepcopy(label), empty_tp=deepcopy(label),
        targets=dict(pause=[1200,30,6,6], auto=[634,473,6,6], speed=[830,473,6,6],
            resume=[442,574,6,6], sets=[[403+116*i,311,6,6] for i in range(5)]),
        inheritance=inheritance, samples=[dict(value['samples'][0],page=page) for page in ('battle','paused','battle_two')])
    return value


def test_complete_execution_profile_adds_only_guarded_single_inputs(executable, plan):
    nodes = daily.require_daily_execution(executable, plan)
    assert nodes['CBAction_open_formal']['custom_recognition_param'] == dict(page='boss_detail', action='open_formal')
    assert nodes['CBAction_characters_next']['action'] == 'Swipe'
    assert all(node['max_hit'] == 1 for node in nodes.values() if node['action'] in ('Click', 'Swipe'))
    assert nodes['CBDailyPauseOpening']['recognition'] == 'And'
    assert 'CBDailyOpeningBinding' in nodes['CBDailyPauseOpening']['all_of']
    assert 'CBPauseOpening' not in nodes


def test_formal_formation_scroll_reuses_shared_gesture_and_keeps_formal_guard(executable, plan):
    from pcr_maa.clan_battle_native import calibration_pipeline
    executable['actions']['characters_next'].update(begin=[1100, 465, 1, 1], end=[1100, 335, 1, 1],
        duration=700, end_hold=250, post_delay=350)
    for name in ('characters_top', 'characters_next'):
        executable['daily']['execution']['actions'][name].update({
            key: deepcopy(executable['actions'][name][key]) for key in ('begin', 'end')})
    shared = calibration_pipeline(executable, plan)
    nodes = daily.require_daily_execution(executable, plan)
    for name in ('characters_top', 'characters_next'):
        node, gesture = nodes['CBAction_' + name], shared['CBAction_' + name]
        assert {key: node.get(key) for key in ('action', 'begin', 'end', 'duration', 'end_hold', 'post_delay')} == {
            key: gesture.get(key) for key in ('action', 'begin', 'end', 'duration', 'end_hold', 'post_delay')}
        assert node['custom_recognition'] == 'ClanBattleDailyPage'
        assert node['custom_recognition_param'] == dict(page='formation', action=name)
        assert node['max_hit'] == 1
    assert nodes['CBAction_characters_next']['duration'] == 700
    assert nodes['CBAction_characters_next']['end_hold'] == 250
    assert nodes['CBAction_characters_next']['post_delay'] == 350
    assert nodes['CBAction_support_next']['duration'] == 300
    assert nodes['CBAction_support_next']['post_delay'] == 0


@pytest.mark.parametrize('name', ['characters_top', 'characters_next'])
def test_formal_formation_scroll_rejects_divergent_duplicate_geometry(executable, plan, name):
    executable['daily']['execution']['actions'][name]['end'][1] += 1
    with pytest.raises(ValueError, match='formation_scroll_mismatch:' + name):
        daily.require_daily_execution(executable, plan)


@pytest.mark.parametrize('key,value', [('duration', True), ('duration', 0), ('end_hold', -1), ('post_delay', -1)])
def test_formal_formation_scroll_keeps_shared_timing_validation(executable, plan, key, value):
    executable['actions']['characters_next'][key] = value
    with pytest.raises(ValueError, match='scroll_timing_invalid'):
        daily.require_daily_execution(executable, plan)


@pytest.mark.parametrize('field', ['pages', 'actions', 'samples', 'boss_slots', 'used_units', 'carry_slots'])
def test_execution_profile_missing_consumption_evidence_is_rejected(executable, plan, field):
    executable['daily']['execution'][field] = []
    with pytest.raises(ValueError):
        daily.require_daily_execution(executable, plan)


def execution_context(executable, plan, page, **texts):
    nodes = daily.require_daily_execution(executable, plan)
    hits = {f'CBDaily_{page}_labels_{index}' for index in range(2)}
    actual_texts = {f'CBDaily_{page}_{field}': value for field, value in texts.items()}
    return nodes, actual_texts, hits, context_for(nodes, actual_texts, hits)


def test_formal_battle_and_result_require_continuous_reserved_target(executable, plan):
    _, texts, hits, context = execution_context(executable, plan, 'battle', remaining_seconds='1:30')
    port = NS(support_unit_id=None, pending={'intent_id': 'reserved'}, started=True, battle_start='sent')
    page = daily.ClanBattleDailyExecutionPage(executable, plan, port)
    image = np.zeros((720, 1280, 3), np.uint8)
    with pytest.raises(RuntimeError, match='transition_unverified'):
        page.read(context, image, 'battle')
    page.bound = dict(period=plan['period'], boss=plan['boss'], phase='B', round=5, instance_id='actual-round-five', formal=True)
    page.current, page.expected = 'formation', 'battle'
    hits.add('CBDailyOneSpeed')
    state = page.read(context, image, 'battle')
    assert state['formal'] is True and state['round'] == 5 and state['battle_speed'] == 1
    hits.clear()
    hits.update({'CBDaily_result_labels_0', 'CBDaily_result_labels_1'})
    texts['CBDaily_result_damage'] = '12,345'
    # A result may follow the running battle without another input operation.
    assert page.read(context, image, 'result')['damage'] == 12345


def test_initial_formation_only_allows_cancel_before_rebinding_target(executable, plan):
    _, _, hits, context = execution_context(executable, plan, 'formation')
    page = daily.ClanBattleDailyExecutionPage(executable, plan, NS())
    image = np.zeros((720, 1280, 3), np.uint8)
    assert page.read(context, image, 'formation')['navigation_only'] is True
    assert page.bound is None
    hits.add('CBDailyActionLabel_cancel_formation')
    target = executable['daily']['execution']['actions']['cancel_formation']['target']
    assert page.action(context, image, 'cancel_formation') == target
    for action in ('start', 'characters_top', 'open_support'):
        with pytest.raises(RuntimeError, match='transition_unverified'):
            page.action(context, image, action)
    hits.remove('CBDailyActionLabel_cancel_formation')
    with pytest.raises(RuntimeError, match='action_unknown'):
        page.action(context, image, 'cancel_formation')


def test_initial_boss_detail_only_allows_return_without_inventing_identity(executable, plan):
    _, _, hits, context = execution_context(executable, plan, 'boss_detail')
    page = daily.ClanBattleDailyExecutionPage(executable, plan, NS())
    image = np.zeros((720, 1280, 3), np.uint8)
    reading = page.read(context, image, 'boss_detail')
    assert reading['navigation_only'] and not {'boss', 'phase', 'formal', 'instance_id'} & reading.keys()
    hits.add('CBDailyActionLabel_back_to_lobby')
    assert page.action(context, image, 'back_to_lobby') == executable['daily']['execution']['actions']['back_to_lobby']['target']
    with pytest.raises(RuntimeError, match='transition_unverified'):
        page.action(context, image, 'open_formal')


def test_initial_formation_returns_to_lobby_without_a_reserved_action(executable, plan):
    port = object.__new__(daily.NativeClanDailyAdapter)
    port.calibration, port.plan, port.action = deepcopy(executable), plan, None
    port.calibration['daily']['execution']['carry_selection'] = {}
    pages = iter(('CBPage_formation', 'CBPage_boss_detail', 'CBPage_lobby'))
    calls = []
    port.wait = lambda *args, **kwargs: next(pages)
    port.run = lambda node, **kwargs: calls.append(node)
    port.account_binding_valid = lambda plan: True
    port.enter_lobby()
    assert calls == ['CBAction_cancel_formation', 'CBAction_back_to_lobby']


def test_used_units_reader_excludes_support_in_multiple_completed_teams(executable, plan):
    _, _, hits, context = execution_context(executable, plan, 'used_units', game_day='2026-10-25')
    slots = executable['daily']['execution']['used_units']['slots']
    units = list(range(100001, 100016))
    port = NS(all_units=units, catalogue=NS(match=lambda image, uid, roi: {'box': roi}
        if uid == units[slots.index(roi)] else None))
    page = daily.ClanBattleDailyExecutionPage(executable, plan, port)
    hits.update({'CBDailyUsedBorrowed4', 'CBDailyUsedBorrowed9', 'CBDailyUsedBorrowed14'})
    reading = page.read(context, np.zeros((720, 1280, 3), np.uint8), 'used_units')
    assert reading['used_unit'] == [uid for index, uid in enumerate(units) if index not in (4, 9, 14)]


def test_nonempty_carry_stock_requires_actual_index_time_and_bound_team(executable, plan):
    _, texts, hits, context = execution_context(executable, plan, 'carry')
    layout = executable['daily']['execution']['carry_slots'][0]['members']['slots']
    units = plan['unit_ids']
    port = NS(all_units=units, catalogue=NS(match=lambda image, uid, roi: {'box': roi}
        if uid == units[layout.index(roi)] else None))
    page = daily.ClanBattleDailyExecutionPage(executable, plan, port)
    hits.update({'CBDailyCarry0Occupied', 'CBDailyCarry1Empty', 'CBDailyCarry2Empty', 'CBDailyCarry0Borrowed4'})
    texts.update(CBDailyCarry0Index='2', CBDailyCarry0Time='34', CBDailyCarry0Owner='987654321')
    result = page.read(context, np.zeros((720, 1280, 3), np.uint8), 'carry')['stocks']
    assert result[0]['stock_index'] == 2 and result[0]['time'] == 34
    assert result[0]['using_unit'] == units and result[0]['support_owner_viewer_id'] == '987654321'
    texts['CBDailyCarry0Owner'] = '昵称不是玩家ID'
    with pytest.raises(RuntimeError, match='field_unknown'):
        page.read(context, np.zeros((720, 1280, 3), np.uint8), 'carry')


def test_daily_native_start_receipt_is_bound_to_exact_formal_intent(tmp_path, monkeypatch):
    from pcr_maa import native
    port = object.__new__(daily.NativeClanDailyAdapter)
    daily.NativeClanBattle.__init__(port, NS(), tmp_path)
    port.calibration = {'daily': {'execution': {}}}
    port.pending, port.action = dict(intent_id='reserved'), dict(kind='regular')
    port.page = NS(expected=None)
    evidence = dict(input_execution='not_sent')
    def reject(*args, **kwargs):
        raise native.NativeInputNotSent(evidence, 'no start dispatched')
    monkeypatch.setattr(daily.NativeGuideTrial, 'run', reject)
    with pytest.raises(native.NativeInputNotSent) as exc:
        port.run('CBStart', click=True)
    assert port.battle_start == 'not_sent'
    assert exc.value.receipt['daily_intent_id'] == 'reserved'
    assert exc.value.receipt['clan_battle_phase'] == 'regular_start'
    with pytest.raises(RuntimeError, match='start_not_reserved'):
        port.run('CBStart', click=True)


def test_daily_native_unknown_start_cannot_be_resent(tmp_path, monkeypatch):
    port = object.__new__(daily.NativeClanDailyAdapter)
    daily.NativeClanBattle.__init__(port, NS(), tmp_path)
    port.calibration = {'daily': {'execution': {}}}
    port.pending, port.action = dict(intent_id='reserved'), dict(kind='carryover')
    monkeypatch.setattr(daily.NativeGuideTrial, 'run', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('unknown SDK outcome')))
    with pytest.raises(RuntimeError, match='unknown SDK'):
        port.run('CBStart', click=True)
    assert port.battle_start == 'unknown'
    with pytest.raises(RuntimeError, match='start_not_reserved'):
        port.run('CBStart', click=True)


@pytest.mark.parametrize('change', ['round', 'phase', 'dead'])
def test_final_boss_selection_reads_current_round_instead_of_cached_identity(executable, plan, change, monkeypatch):
    from pcr_maa.clan_battle_batch import _hash
    actual = dict(boss=plan['boss'], phase='B', round=5, available=True)
    action = dict(actual, instance_id=_hash(dict(period=plan['period'], boss=plan['boss'], phase='B', round=5)))
    page = daily.ClanBattleDailyExecutionPage(executable, plan, NS(action=action))
    if change == 'round': actual['round'] = 6
    elif change == 'phase': actual['phase'] = 'C'
    else: actual['available'] = False
    monkeypatch.setattr(page, 'read', lambda *args: dict(period=plan['period'], bosses=[actual]))
    monkeypatch.setattr(page, 'hit', lambda *args: True)
    with pytest.raises(RuntimeError, match='target_changed_before_selection'):
        page.action(NS(), np.zeros((720, 1280, 3), np.uint8), 'open_boss')


@pytest.mark.parametrize('changed', ['regular_mode', 'stock', 'time'])
def test_carry_formation_cannot_fall_back_to_regular_or_another_stock(executable, plan, changed):
    _, texts, hits, context = execution_context(executable, plan, 'formation')
    action = dict(kind='carryover', stock_index=2, start_seconds=34)
    page = daily.ClanBattleDailyExecutionPage(executable, plan, NS(action=action, support_unit_id=None))
    page.bound = dict(period=plan['period'], formal=True, boss=plan['boss'], phase='B', round=5, instance_id='real-round')
    page.expected = 'formation'
    hits.add('CBDailyFormationRegular' if changed == 'regular_mode' else 'CBDailyFormationCarryover')
    texts.update(CBDailyFormationStock='3' if changed == 'stock' else '2',
        CBDailyFormationTime='33' if changed == 'time' else '34')
    with pytest.raises(RuntimeError, match='consumption_mode_unknown|selected_carry_changed'):
        page.read(context, np.zeros((720, 1280, 3), np.uint8), 'formation')


@pytest.mark.parametrize('change', ['owner', 'order', 'day', 'stale'])
def test_final_start_callback_rechecks_pending_team_and_game_day(executable, plan, monkeypatch, change):
    from pcr_maa.game_day import current_game_day
    now = daily.datetime.now(daily.timezone.utc)
    actual = dict(unit_ids=plan['unit_ids'][:], stars=plan['stars'][:], support_unit_id=plan['unit_ids'][-1],
        support_owner_viewer_id='987654321')
    ready = dict(unit_ids=plan['unit_ids'][:], stars=plan['stars'][:], support_unit=plan['unit_ids'][-1],
        support_owner_viewer_id='987654321')
    action = dict(kind='regular')
    pending = dict(action=action, game_day=current_game_day(now), observed_at=now.isoformat(), ready=ready)
    if change == 'owner': actual['support_owner_viewer_id'] = '888888888'
    elif change == 'order': actual['unit_ids'].reverse()
    elif change == 'day': now += timedelta(days=1)
    else: now += timedelta(seconds=61)
    port = NS(action=action, pending=pending, now=lambda: now, characters=NS(formation=lambda *args: actual))
    page = daily.ClanBattleDailyExecutionPage(executable, plan, port)
    monkeypatch.setattr(page, 'read', lambda *args: {})
    monkeypatch.setattr(page, 'hit', lambda *args: True)
    monkeypatch.setattr(page, 'require_team', lambda value: None)
    with pytest.raises(RuntimeError, match='final_start_binding_changed|final_team_changed'):
        page.action(NS(), np.zeros((720, 1280, 3), np.uint8), 'start')


def test_empty_team_does_not_require_a_support_owner_before_support_is_selected(monkeypatch):
    reader = object.__new__(daily._DailyTeamReader)
    reader.page = NS(word=lambda *args: pytest.fail('empty team must not read an owner ID'))
    monkeypatch.setattr(daily.ClanBattleCharacters, 'formation', lambda *args: dict(support_unit_id=None))
    assert reader.formation(NS(), np.zeros((720, 1280, 3), np.uint8))['support_owner_viewer_id'] is None


def test_native_observe_uses_actual_round_identity_and_only_verified_refresh_appearance(plan, executable, monkeypatch):
    from pcr_maa.game_day import current_game_day
    port = object.__new__(daily.NativeClanDailyAdapter)
    port.plan, port.last_bosses, port.boss_history = plan, {}, {}
    port.calibration, port.tasker = executable, NS()
    port.account_receipt = {'account': plan['account_id']}
    port.pending, port.started, port.battle_start = None, False, 'not_sent'
    clock = [daily.datetime.now(daily.timezone.utc)]
    bosses = [dict(boss=number, phase='B', round=1 if number == 3 else None, available=number == 3)
        for number in range(1, 6)]
    monkeypatch.setattr(port, 'now', lambda: clock[0])
    monkeypatch.setattr(port, 'enter_lobby', lambda: None)
    monkeypatch.setattr(port, 'recover_inheritance', lambda: None)
    monkeypatch.setattr(port, 'run', lambda *args, **kwargs: {})
    monkeypatch.setattr(port, 'wait', lambda names, **kwargs: names[0])
    def read(page):
        if page == 'used_units':
            return dict(game_day=current_game_day(clock[0]), used_unit=[]), {'record_day': current_game_day(clock[0])}
        return dict(period=plan['period'], remaining_count=3, carry_count=0, cp=900, bosses=deepcopy(bosses)), {'frame': clock[0].isoformat()}
    monkeypatch.setattr(port, 'read_page', read)
    monkeypatch.setattr(port, 'inventory', lambda: ([], {'stock_empty': True}, receipt(read('lobby')[0])))
    binding = dict(game_day=current_game_day(clock[0]))
    port.bind_history(deepcopy(binding), {})
    first = port.observe(binding)['bosses'][2]
    assert first['instance_id'] and first['appeared_at'] is None
    clock[0] += timedelta(seconds=1)
    assert port.observe(binding)['bosses'][2]['instance_id'] == first['instance_id']
    bosses[2].update(available=False, round=None)
    assert port.observe(binding)['bosses'][2]['instance_id'] is None
    bosses[2].update(available=True, round=2)
    clock[0] += timedelta(seconds=1)
    refreshed = port.observe(binding)['bosses'][2]
    assert refreshed['instance_id'] != first['instance_id']
    assert refreshed['appeared_at'] == clock[0].isoformat() and 'previous' in refreshed['evidence']


@pytest.mark.parametrize('kind', ['regular', 'carryover'])
def test_native_prepare_uses_formal_target_and_distinct_regular_or_stock_entry(plan, monkeypatch, kind):
    port = object.__new__(daily.NativeClanDailyAdapter)
    action = dict(kind=kind, slot=1, boss=plan['boss'], round=5, phase='B', instance_id='five',
        stock_index=None if kind == 'regular' else 2, start_seconds=90 if kind == 'regular' else 49)
    port.pending, port.support_unit_id = None, None
    port.tasker = NS(resource=Registry())
    monkeypatch.setattr(port, 'prepare_inheritance', lambda: None)
    port.last_bosses = {plan['boss']: deepcopy(action)}
    port.account_receipt, port.calibration = {'account': plan['account_id']}, {
        'calibration_id': 'offline', 'daily':{'execution':{'opening':{'start_seconds':[90]}}}}
    port.page = NS(bound=None, require_team=lambda reading: None)
    calls = []
    monkeypatch.setattr(port, 'enter_lobby', lambda: calls.append('lobby'))
    monkeypatch.setattr(port, 'configure_slot', lambda job, capability: calls.append(capability))
    monkeypatch.setattr(port, 'run', lambda name, **kwargs: calls.append(name))
    monkeypatch.setattr(port, 'wait', lambda names, **kwargs: names[0])
    monkeypatch.setattr(port, 'form_team', lambda: calls.append('form_team'))
    monkeypatch.setattr(port, 'read_team', lambda: dict(unit_ids=plan['unit_ids'], stars=plan['stars'], support_unit_id=None))
    ready = port.prepare(action, dict(plan, confirmed_for='reviewed'), {'account_id': plan['account_id']})
    assert ready['kind'] == kind and ready['evidence']['mode'] == 'formal'
    assert calls[:4] == ['lobby', 'formal' if kind == 'regular' else 'carryover', 'CBAction_open_boss', 'CBAction_open_formal']
    assert ('form_team' in calls) == (kind == 'regular')
    assert ('CBAction_select_carry' in calls) == (kind == 'carryover')


def test_one_second_regular_fails_before_opening_the_boss(plan, monkeypatch):
    port = object.__new__(daily.NativeClanDailyAdapter)
    port.pending = None
    port.calibration = {'daily': {'execution': {'opening': {'start_seconds': [1, 90]}}}}
    calls = []
    monkeypatch.setattr(port, 'enter_lobby', lambda: calls.append('lobby'))
    monkeypatch.setattr(port, 'configure_slot', lambda job, capability: calls.append(capability))
    monkeypatch.setattr(port, 'run', lambda *args, **kwargs: pytest.fail('no battle input expected'))
    with pytest.raises(RuntimeError, match='clan_battle_daily_start_time_uncalibrated'):
        port._prepare_once(dict(kind='regular', start_seconds=1), plan, {})
    assert calls == ['lobby', 'formal']


def test_daily_adapter_callbacks_remain_on_real_owner_when_slot_reader_changes(executable, plan, tmp_path, monkeypatch):
    from pcr_maa import clan_battle
    plan = dict(plan, confirmed_for='synthetic-reviewed-plan')
    monkeypatch.setattr(clan_battle, 'load_snapshot', lambda _: dict(ownership_complete=True,
        units=[dict(unit_id=uid) for uid in plan['unit_ids']]))
    owner, borrowed = Registry(), Registry()
    tasker = NS(resource=borrowed, _resource_holder=owner)
    port = daily.NativeClanDailyAdapter(tasker, tmp_path, request={'slot_plans': {'1': plan}},
        calibrations={plan['confirmed_for']: executable})
    previous = weakref.ref(port.characters)
    router = owner.callbacks['ClanBattleCharacters']
    port.configure_slot(plan)
    gc.collect()
    assert previous() is None and router.port.characters is port.characters
    reference = weakref.ref(port)
    del port
    gc.collect()
    assert reference() is router.port and not borrowed.callbacks


@pytest.mark.parametrize('capability', ['formal', 'carryover'])
def test_formal_slot_excludes_simulation_opening_without_changing_shared_calibration(
        executable, plan, tmp_path, monkeypatch, capability):
    from pcr_maa import clan_battle, clan_battle_calibration
    from pcr_maa.clan_battle_native import calibration_pipeline
    from test_clan_opening import sampled_plan
    shipped = clan_battle_calibration.load_calibration(sampled_plan())
    executable['auto_battle'] = deepcopy(shipped['auto_battle'])
    executable['pages']['battle']['speed_two'] = deepcopy(shipped['pages']['battle']['speed_two'])
    original = deepcopy(executable)
    plan = dict(plan, confirmed_for='synthetic-reviewed-plan')
    monkeypatch.setattr(clan_battle, 'load_snapshot', lambda _: dict(ownership_complete=True,
        units=[dict(unit_id=uid) for uid in plan['unit_ids']]))
    loaded = []
    def carry_calibration(job, *, capability):
        loaded.append(capability)
        return executable
    monkeypatch.setattr(clan_battle_calibration, 'load_calibration', carry_calibration)
    resource = Registry()
    port = daily.NativeClanDailyAdapter(NS(resource=resource), tmp_path,
        request={'slot_plans': {'1': plan}}, calibrations={plan['confirmed_for']: executable})
    port.configure_slot(plan, capability=capability)
    assert resource.nodes['CBStart']['next'] == ['CBDailyPauseOpening']
    assert 'CBPauseOpening' not in resource.nodes and 'auto_battle' not in port.calibration
    shared_scroll = calibration_pipeline(port.calibration, plan)['CBAction_characters_next']
    actual_scroll = resource.nodes['CBAction_characters_next']
    assert {key: actual_scroll.get(key) for key in ('begin', 'end', 'duration', 'end_hold', 'post_delay')} == {
        key: shared_scroll.get(key) for key in ('begin', 'end', 'duration', 'end_hold', 'post_delay')}
    assert actual_scroll['custom_recognition'] == 'ClanBattleDailyPage'
    assert executable == original and port.calibrations[plan['confirmed_for']] == original
    assert loaded == (['carryover'] if capability == 'carryover' else [])
    assert calibration_pipeline(executable, plan)['CBStart']['next'] == ['CBPauseOpening']


@pytest.mark.filterwarnings('error::pytest.PytestUnraisableExceptionWarning')
def test_real_sdk_dispatches_daily_callbacks_after_port_gc_without_input(executable, plan, tmp_path, monkeypatch):
    from pcr_maa import clan_battle, native
    from pcr_maa.replay import SavedImages
    plan = dict(plan, confirmed_for='synthetic-reviewed-plan')
    monkeypatch.setattr(clan_battle, 'load_snapshot', lambda _: dict(ownership_complete=True,
        units=[dict(unit_id=uid) for uid in plan['unit_ids']]))
    resource = native.load_resource()
    controller = SavedImages([[np.zeros((720, 1280, 3), np.uint8)]])
    tasker = native.bind(resource, controller)
    tasker._resource_holder = resource
    port = daily.NativeClanDailyAdapter(tasker, tmp_path, request={'slot_plans': {'1': plan}},
        calibrations={plan['confirmed_for']: executable})
    reads, analyze = [], daily.ClanBattleDailyExecutionPage.analyze
    def watched(self, context, argv):
        reads.append('page')
        return analyze(self, context, argv)
    monkeypatch.setattr(daily.ClanBattleDailyExecutionPage, 'analyze', watched)
    reference = weakref.ref(port)
    del port
    gc.collect()
    try:
        assert reference() is not None
        assert native.run(tasker, 'CBPage_lobby')['native_ok'] is False
        assert native.run(tasker, 'CBReadTeam')['native_ok'] is False
        assert reads and not controller.clicks and not controller.swipes
    finally:
        stopped = native.stop_tasker(tasker)
        assert stopped['idle'] and stopped['stop_completed']


def test_daily_adapter_registers_and_dispatches_through_owner_scope(registry, executable, plan, tmp_path, monkeypatch):
    from pcr_maa import clan_battle
    plan = dict(plan, confirmed_for='synthetic-reviewed-plan')
    monkeypatch.setattr(clan_battle, 'load_snapshot', lambda _: dict(ownership_complete=True,
        units=[dict(unit_id=uid) for uid in plan['unit_ids']]))
    context, calls = FakeContext(), []
    with registry.scope(context, deadline=time.monotonic() + 10) as facade:
        port = daily.NativeClanDailyAdapter(facade, tmp_path, request={'slot_plans': {'1': plan}},
            calibrations={plan['confirmed_for']: executable})
        assert facade._callback_scope.bindings['ClanBattleDailyPage'] is port.page
        assert facade._callback_scope.bindings['ClanBattleCharacters'] is port.callback
        def analyze(ctx, argv):
            calls.append(argv.custom_recognition_name)
            return port.page.AnalyzeResult(box=[0, 0, 1, 1], detail={'scope_checked': True})
        monkeypatch.setattr(port.page, 'analyze', analyze)
        argv = NS(custom_recognition_name='ClanBattleDailyPage', task_detail=NS(task_id=1),
            image=np.zeros((720, 1280, 3), np.uint8), custom_recognition_param='{"page":"lobby"}')
        with facade._callback_scope.dispatch():
            assert registry.recognitions['ClanBattleDailyPage'].analyze(context, argv) is not None
    assert calls == ['ClanBattleDailyPage'] and not facade._callback_scope.bindings
    assert registry.recognitions['ClanBattleDailyPage'].analyze(context, argv) is None


@pytest.fixture
def native_protocol(tmp_path, daily_request, executable, monkeypatch):
    from pcr_maa import clan_battle, clan_battle_calibration
    executable['daily']['execution']['opening']['start_seconds'] = [90,34]
    class Protocol(daily.NativeClanDailyAdapter):
        def __init__(self):
            self.state = Port(daily_request)
            self.directory = tmp_path / 'native-protocol'
            self.directory.mkdir()
            self.pending, self.action, self.started = None, None, False
            self.battle_start, self.stocks, self.return_failure = 'not_sent', [], False
            self.page = NS(require_team=lambda team: None)
            self.calibration = {'version': 2}
            self.timing_capability = self.state.timing_capability
            self.calls, self.result_fault = [], None
        def checkpoint(self): pass
        def consumption_binding(self):
            return dict(source_sha256='1'*64, calibration_sha256='2'*64,
                owner_core_sha256='3'*64, authorization_generation='4'*32)
        def request_consumption_consent(self, plan_hash, preview):
            return dict(ok=True, request_id=('a' if preview['kind'] == 'regular' else 'b')*32,
                daily_plan_hash=plan_hash, preview=deepcopy(preview))
        def arm_opening_start(self, token, *, start_seconds, action_kind):
            assert token == self.pending['intent_id']
            assert type(start_seconds) is int and start_seconds == self.action['start_seconds'] == self.start_seconds
            assert action_kind == self.action['kind']
        def after_settlement(self, intent, result): pass
        def configure_opening(self, job, initial): return self.finished['settings_evidence']
        def now(self): return self.state.now()
        def prepare(self, action, job, binding):
            self.action, self.plan, self.start_seconds = action, job, action['start_seconds']
            self.current_ready = self.state.prepare(action, job, binding)
            return deepcopy(self.current_ready)
        def read_team(self):
            row = self.current_ready
            return dict(unit_ids=row['unit_ids'], stars=row['stars'], support_unit_id=row['support_unit'],
                support_owner_viewer_id=row['support_owner_viewer_id'])
        def run(self, node, *, click=False):
            self.calls.append(node)
            if node == 'CBStart':
                self.started, self.battle_start = True, 'sent'
                self.finished = self.state.execute(self.pending, self.plan)
                if self.finished['grant']:
                    self.stocks.append(deepcopy(self.finished['grant']))
                self.stocks = [row for row in self.stocks if row['stock_index'] in self.state.inventory]
            if node == 'CBAction_return_to_lobby' and self.return_failure:
                raise RuntimeError('synthetic_return_failed')
            self.last_receipt = receipt({'node': node, **({'paused': True} if node == 'CBStart' else {})})
            return self.last_receipt
        def wait(self, names, **kwargs):
            if names == ['CBPage_result']:
                reading = dict(period=self.plan['period'], formal=True, damage=self.finished['damage'],
                    **{key: self.action[key] for key in ('boss', 'phase', 'round', 'instance_id')})
                fault = self.result_fault
                if fault == 'boss': reading['boss'] = self.action['boss'] % 5 + 1
                elif fault == 'phase': reading['phase'] = 'E'
                elif fault == 'round': reading['round'] += 1
                elif fault == 'instance_id': reading['instance_id'] = 'different-instance'
                elif fault == 'period': reading['period'] = '2000-01'
                elif fault == 'formal': reading['formal'] = False
                elif fault == 'damage': reading['damage'] = True
                self.last_receipt = result_page_receipt(reading)
                if fault == 'wrong_node': self.last_receipt['nodes'][-1]['name'] = 'CBReadSettings'
                elif fault == 'failed': self.last_receipt['native_ok'] = False
            return names[0]
        def read_page(self, name):
            pytest.fail('must not recapture result')
        def observe_external_baseline(self, binding): return self.observe(binding)
        def observe(self, binding, *, result_intent=None):
            if result_intent is not None:
                assert result_intent == self.pending
            row = self.state.observe(binding)
            row['reset_evidence']['inventory'] = {'source': 'synthetic_bound_stock_page'}
            if hasattr(self, 'finished') and self.finished['grant']:
                self.stock_observed_at = self.finished['grant']['issued_at']
            else:
                self.stock_observed_at = row['observed_at']
            return row
    port = Protocol()
    monkeypatch.setattr(clan_battle, 'execute_switches', lambda owner, job, initial: port.finished['settings_evidence'])
    monkeypatch.setattr(clan_battle_calibration, 'load_calibration', lambda job, capability: executable)
    return port


def test_native_execute_protocol_settles_three_regular_and_bound_carryover(native_protocol, daily_request, tmp_path):
    from pcr_maa.clan_battle_batch import execute_batch
    from pcr_maa.preset_execution import Journal
    native_protocol.state.issue = {1}
    result = execute_batch(native_protocol, Journal(tmp_path / 'ledger'), daily_request,
        now=native_protocol.now(), explicit_start=True)
    assert result.get('error') is None and result['outcome'] == 'complete'
    assert result['regular_settled'] == 3 and result['carryover_settled'] == 1
    assert native_protocol.calls.count('CBStart') == 4
    assert len(list(native_protocol.directory.glob('result-*.json'))) == 4


@pytest.mark.parametrize('fault', ['wrong_node', 'failed', 'boss', 'phase', 'round',
    'instance_id', 'period', 'formal', 'damage'])
def test_native_execute_rejects_unverified_or_unbound_result_without_return(native_protocol, daily_request, tmp_path, fault):
    from pcr_maa.clan_battle_batch import execute_batch
    from pcr_maa.preset_execution import Journal
    native_protocol.result_fault = fault
    journal = Journal(tmp_path / 'ledger')
    result = execute_batch(native_protocol, journal, daily_request, now=native_protocol.now(), explicit_start=True)
    assert result['outcome'] == 'stopped'
    assert result['error'] in ('clan_battle_result_unknown', 'clan_battle_daily_result_binding_mismatch')
    assert native_protocol.calls.count('CBStart') == 1
    assert 'CBAction_return_to_lobby' not in native_protocol.calls
    assert not list(native_protocol.directory.glob('result-*.json'))
    again = execute_batch(native_protocol, journal, daily_request, now=native_protocol.now(), explicit_start=True)
    assert again['error'] == 'daily_previous_result_unknown'
    assert native_protocol.calls.count('CBStart') == 1


def test_native_execute_persists_damage_before_return_failure_and_keeps_intent_pending(native_protocol, daily_request, tmp_path):
    from pcr_maa.clan_battle_batch import execute_batch
    from pcr_maa.preset_execution import Journal
    native_protocol.return_failure = True
    journal = Journal(tmp_path / 'ledger')
    result = execute_batch(native_protocol, journal, daily_request, now=native_protocol.now(), explicit_start=True)
    assert result['error'] == 'synthetic_return_failed' and result['consumption_sent'] is True
    saved = json.loads(next(native_protocol.directory.glob('result-*.json')).read_text(encoding='utf-8'))
    assert saved['damage'] == 12345678
    again = execute_batch(native_protocol, journal, daily_request, now=native_protocol.now(), explicit_start=True)
    assert again['error'] == 'daily_previous_result_unknown'
    assert native_protocol.calls.count('CBStart') == 1


@pytest.mark.parametrize('start,paused', [(90, 84), (34, 29)])
def test_regular_first_pause_freezes_actual_progress(plan, start, paused):
    reader = object.__new__(daily._DailyTeamReader)
    token = 'synthetic-regular' if start == 90 else 'synthetic-carry'
    pending = dict(intent_id=token, action=dict(start_seconds=start))
    port = NS(opening_binding=lambda: pending, opening_clock=None)
    page = NS(port=port, current='battle', expected=None)
    page.hit = lambda context, image, name: name != 'LabyDisconnect6002Observed'
    clock = [paused]
    page.word = lambda context, image, name, **options: f'{clock[0]//60}:{clock[0]%60:02d}'
    reader.page, reader.plan, reader.team = page, plan, plan['unit_ids']
    reader.calibration = {'daily':{'execution':{'opening':{'clock':[1080,21,43,26],'targets':{'resume':[1,2,3,4]}}}}}
    reader.begin_opening(token)
    reader.opening_started = True
    def state(context, image, *, remaining_seconds, **kwargs):
        return dict(unit_ids=plan['unit_ids'], sets=[True]*5, auto=True, battle_speed=2,
            remaining_seconds=remaining_seconds, frame_pixel_sha256='a'*64, paused=True, same_frame=True)
    reader.read_paused_controls = state
    args = NS(image=np.zeros((720,1280,3),np.uint8))
    context = NS(tasker=NS(stopping=False))
    read = reader.opening_control(context,args,{'mode':'opening_read'})
    assert read is not None and port.opening_clock == (token,paused)
    assert reader.opening_control(context,args,dict(mode='opening_resume', unit_ids=plan['unit_ids'],
        sets=[True]*5, auto=True, battle_speed=2, remaining_seconds=paused)) is not None
    clock[0] -= 1
    assert reader.opening_control(context,args,{'mode':'opening_read'}) is None
    clock[0] = paused
    pending['intent_id'] = 'another-intent'
    assert reader.opening_control(context,args,{'mode':'opening_read'}) is None


@pytest.mark.parametrize('inherited', [True, False])
def test_formal_recovery_checks_simulation_and_daily_legacy_journals(tmp_path, monkeypatch, inherited):
    from pcr_maa import clan_battle_inheritance as inheritance
    from pcr_maa.preset_execution import Journal
    port = object.__new__(daily.NativeClanDailyAdapter)
    port.account = '123456789'
    port.inheritance_recovered = False
    own = inheritance.BattleInheritance(port,Journal(tmp_path/'daily-inheritance'))
    legacy = inheritance.BattleInheritance(port,Journal(tmp_path/'simulation-inheritance'))
    pending = dict(operation=inheritance.OPERATION,intent_id='legacy-simulation-pending',
        account_id=port.account,original=True,state='in_flight')
    with legacy.journal.locked():
        legacy.journal.append(pending)
    before = legacy.journal.events()
    monkeypatch.setattr(port,'inheritance_manager',lambda:own)
    monkeypatch.setattr(inheritance.BattleInheritance,'for_port',classmethod(lambda cls,p:legacy))
    actions = []
    monkeypatch.setattr(port,'inheritance_menu',lambda:actions.append('menu'))
    monkeypatch.setattr(port,'inheritance_lobby',lambda:actions.append('lobby'))
    monkeypatch.setattr(port,'read_inheritance',lambda:dict(inherit=inherited,same_frame=True,source='synthetic'))
    if inherited:
        port.recover_inheritance()
        assert port.inheritance_recovered and len(legacy.journal.events()) == 2
        assert legacy.journal.events()[0] == before[0]
        assert legacy.journal.events()[1]['restore_evidence']['inherit'] is True
        assert actions == ['menu','lobby']
    else:
        with pytest.raises(RuntimeError,match='user_state_confirmation_required'):
            port.recover_inheritance()
        assert not port.inheritance_recovered and legacy.journal.events() == before
        assert actions == ['menu']
    assert own.journal.events() == []


def dialog_profile(executable, name):
    value = deepcopy(executable)
    count = 3 if name in ('start_confirmation','carry_start_confirmation') else 2
    value['daily']['execution'][name] = dict(
        labels=[dict(roi=[10,10,100,30], expected='^SyntheticDialog$') for _ in range(count)],
        target=[639,226,2,2], samples=[dict(value['samples'][0], page=name)])
    return value


@pytest.mark.parametrize('name,count', [('start_confirmation',3),('carry_start_confirmation',3),('result_reward',2)])
def test_formal_dialog_calibration_is_optional_exact_and_single_attempt(executable, plan, name, count):
    value = dialog_profile(executable, name)
    nodes = daily.require_daily_execution(value, plan)
    assert all(f'CBDailyDialog_{name}_{i}' in nodes for i in range(count))
    node = 'CBDailyStartConfirmation' if name in ('start_confirmation','carry_start_confirmation') else 'CBDailyResultRewardConfirm'
    assert nodes[node]['max_hit'] == 1 and nodes[node]['action'] == 'Click'
    for fault in ('extra', 'labels', 'target', 'sample_page', 'sample_hash'):
        bad = deepcopy(value)
        dialog = bad['daily']['execution'][name]
        if fault == 'extra': dialog['enabled'] = True
        elif fault == 'labels': dialog['labels'].pop()
        elif fault == 'target': dialog['target'] = [0,0,0,0]
        elif fault == 'sample_page': dialog['samples'][0]['page'] = 'simulation'
        else: dialog['samples'][0]['sha256'] = '0'*64
        with pytest.raises(ValueError): daily.require_daily_execution(bad, plan)


@pytest.mark.parametrize('mode', ['formal_start_confirmation','result_reward_confirm'])
@pytest.mark.parametrize('fault', [None, 'missing_guard', 'no_authority', 'wrong_intent', 'changed_source', 'changed_owner', 'carryover'])
def test_formal_dialog_click_is_bound_to_original_authorized_intent_only_once(executable, plan, mode, fault):
    name = 'start_confirmation' if mode == 'formal_start_confirmation' else 'result_reward'
    value = dialog_profile(executable, name)
    nodes = daily.require_daily_execution(value, plan)
    count = 3 if name in ('start_confirmation','carry_start_confirmation') else 2
    hits = {f'CBDailyDialog_{name}_{i}' for i in range(count)}
    context = context_for(nodes, {}, hits)
    fingerprint = dict(source_sha256='1'*64, calibration_sha256='2'*64, owner_core_sha256='3'*64,
        authorization_generation='4'*32)
    permission = dict(kind='clan_consumption', reserved_intent_id='a'*64,
        preview=dict(kind='regular', **{key:fingerprint[key] for key in ('source_sha256','calibration_sha256','owner_core_sha256')}),
        authorization_generation=fingerprint['authorization_generation'])
    pending = dict(intent_id='a'*64, action={'kind':'regular'}, ready={'evidence':{'consumption_authorization':permission}})
    port = NS(opening_binding=lambda **_:pending, battle_start='sent', consumption_binding=lambda:deepcopy(fingerprint))
    page = daily.ClanBattleDailyExecutionPage(value, plan, port)
    page.current = 'formation' if name == 'start_confirmation' else 'battle'
    reader = daily._DailyTeamReader(plan,value,page)
    if fault == 'missing_guard': hits.pop()
    elif fault == 'no_authority': pending['ready']['evidence'].clear()
    elif fault == 'wrong_intent': permission['reserved_intent_id'] = 'f'*64
    elif fault == 'changed_source': fingerprint['source_sha256'] = 'e'*64
    elif fault == 'changed_owner': fingerprint['authorization_generation'] = 'f'*32
    elif fault == 'carryover': pending['action']['kind'] = 'carryover'
    argv = NS(image=np.zeros((720,1280,3),np.uint8),custom_recognition_param=json.dumps({'mode':mode}))
    first = reader.analyze(context,argv)
    if fault:
        assert first is None
    else:
        assert first is not None and list(first.box) == value['daily']['execution'][name]['target']
        assert reader.analyze(context,argv) is None


def test_registered_formal_damage_uses_same_frame_full_line_pair(executable,plan):
    value = deepcopy(executable)
    value['calibration_id'] = 'cn-bilibili-202610141448-clan-simulation'
    value['frame_size'] = [1280,720]
    value['daily']['execution']['pages']['result']['fields']['damage'] = [88,47,162,38]
    nodes,texts,hits,context = execution_context(value,plan,'result',damage='265113492×4.5')
    assert nodes['CBDaily_result_damage']['roi'] == [80,45,332,42]
    assert nodes['CBDaily_result_damage_verify']['only_rec'] is True
    texts['CBDaily_result_damage_verify'] = '265113492×4.5'
    port = NS(pending={'intent_id':'test'},started=True,battle_start='sent')
    page = daily.ClanBattleDailyExecutionPage(value,plan,port)
    page.current = 'battle'
    page.bound = dict(period=plan['period'],formal=True,boss=plan['boss'],phase='B',round=5,instance_id='actual')
    image = np.zeros((720,1280,3),np.uint8)
    assert page.read(context,image,'result')['damage'] == 265113492
    texts['CBDaily_result_damage_verify'] = '26511349×4.5'
    with pytest.raises(RuntimeError,match='damage_mismatch'): page.read(context,image,'result')


def test_native_result_reward_is_dismissed_once_before_clear_result_and_return(native_protocol, daily_request, tmp_path):
    from pcr_maa.clan_battle_batch import execute_batch
    from pcr_maa.preset_execution import Journal
    native_protocol.calibration = {'daily': {'execution': {'result_reward': {}}}}
    original = native_protocol.wait
    def wait(names, **kwargs):
        if names == ['CBPage_result','CBDailyResultRewardObserved']:
            return 'CBDailyResultRewardObserved'
        return original(names,**kwargs)
    native_protocol.wait = wait
    result = execute_batch(native_protocol,Journal(tmp_path/'ledger'),daily_request,now=native_protocol.now(),explicit_start=True)
    assert result.get('error') is None and result['regular_settled'] == 3, result
    assert native_protocol.calls.count('CBStart') == 3
    assert native_protocol.calls.count('CBDailyResultRewardConfirm') == 3
    for index,name in enumerate(native_protocol.calls):
        if name == 'CBDailyResultRewardConfirm':
            assert native_protocol.calls[index+1] == 'CBAction_return_to_lobby'


def test_unknown_result_reward_keeps_original_intent_without_new_start(native_protocol,daily_request,tmp_path):
    from pcr_maa.clan_battle_batch import execute_batch
    from pcr_maa.preset_execution import Journal
    native_protocol.calibration = {'daily': {'execution': {'result_reward': {}}}}
    wait,run = native_protocol.wait,native_protocol.run
    native_protocol.wait = lambda names,**kwargs: 'CBDailyResultRewardObserved' if len(names)==2 else wait(names,**kwargs)
    def unknown(node,**kwargs):
        if node == 'CBDailyResultRewardConfirm': raise RuntimeError('unknown_reward_input')
        return run(node,**kwargs)
    native_protocol.run = unknown
    journal = Journal(tmp_path/'ledger')
    first = execute_batch(native_protocol,journal,daily_request,now=native_protocol.now(),explicit_start=True)
    assert first['error'] == 'unknown_reward_input' and first['regular_settled'] == 0
    second = execute_batch(native_protocol,journal,daily_request,now=native_protocol.now(),explicit_start=True)
    assert second['error'] == 'daily_previous_result_unknown' and native_protocol.calls.count('CBStart') == 1


@pytest.mark.parametrize('boss', [1, 2, 3, 4, 5])
def test_final_boss_selection_clicks_the_center_of_the_bound_target(executable, plan, boss, monkeypatch):
    from pcr_maa.clan_battle_batch import _hash
    plan = dict(plan, boss=boss)
    actual = dict(boss=boss, phase='B', round=5, available=True)
    action = dict(actual, instance_id=_hash(dict(period=plan['period'], boss=boss, phase='B', round=5)))
    page = daily.ClanBattleDailyExecutionPage(executable, plan, NS(action=action))
    monkeypatch.setattr(page, 'read', lambda *args: dict(period=plan['period'], bosses=[actual]))
    monkeypatch.setattr(page, 'hit', lambda *args: True)
    x, y, w, h = executable['boss_targets'][str(boss)]['target']
    assert page.action(NS(), np.zeros((720, 1280, 3), np.uint8), 'open_boss') == [x + w // 2, y + h // 2, 1, 1]


@pytest.mark.parametrize('kind', ['regular','carryover'])
@pytest.mark.parametrize('screen', ['regular','carryover'])
def test_start_confirmation_selects_exact_intent_kind_and_never_falls_back(executable,plan,kind,screen):
    value=dialog_profile(dialog_profile(executable,'start_confirmation'),'carry_start_confirmation')
    value['daily']['execution']['start_confirmation']['target']=[10,20,2,2]
    value['daily']['execution']['carry_start_confirmation']['target']=[30,40,2,2]
    nodes=daily.require_daily_execution(value,plan)
    name='carry_start_confirmation' if screen=='carryover' else 'start_confirmation'
    hits={f'CBDailyDialog_{name}_{i}' for i in range(3)}
    context=context_for(nodes,{},hits)
    fingerprint=dict(source_sha256='1'*64,calibration_sha256='2'*64,owner_core_sha256='3'*64,authorization_generation='4'*32)
    authorization=dict(reserved_intent_id='a'*64,preview=dict(kind=kind,**{k:fingerprint[k] for k in ('source_sha256','calibration_sha256','owner_core_sha256')}),authorization_generation=fingerprint['authorization_generation'])
    pending=dict(intent_id='a'*64,action={'kind':kind},ready={'evidence':{'consumption_authorization':authorization}})
    port=NS(opening_binding=lambda **_:pending,battle_start='sent',consumption_binding=lambda:deepcopy(fingerprint))
    page=daily.ClanBattleDailyExecutionPage(value,plan,port)
    page.current='formation'
    reader=daily._DailyTeamReader(plan,value,page)
    argv=NS(image=np.zeros((720,1280,3),np.uint8),custom_recognition_param=json.dumps({'mode':'formal_start_confirmation'}))
    hit=reader.analyze(context,argv)
    if kind!=screen:
        assert hit is None and not hasattr(port,'_formal_start_confirmation_token')
    else:
        assert hit is not None and list(hit.box)==value['daily']['execution'][name]['target']
        assert hit.detail==dict(page=name,intent_id='a'*64)
        assert reader.analyze(context,argv) is None


@pytest.mark.parametrize('fault', ['missing_guard','wrong_authority_kind','wrong_intent','changed_source','changed_owner'])
def test_carry_confirmation_keeps_existing_single_intent_authority_guards(executable,plan,fault):
    value=dialog_profile(executable,'carry_start_confirmation')
    nodes=daily.require_daily_execution(value,plan)
    hits={f'CBDailyDialog_carry_start_confirmation_{i}' for i in range(3)}
    context=context_for(nodes,{},hits)
    fingerprint=dict(source_sha256='1'*64,calibration_sha256='2'*64,owner_core_sha256='3'*64,authorization_generation='4'*32)
    permission=dict(reserved_intent_id='a'*64,preview=dict(kind='carryover',**{k:fingerprint[k] for k in ('source_sha256','calibration_sha256','owner_core_sha256')}),authorization_generation=fingerprint['authorization_generation'])
    pending=dict(intent_id='a'*64,action={'kind':'carryover'},ready={'evidence':{'consumption_authorization':permission}})
    port=NS(opening_binding=lambda **_:pending,battle_start='sent',consumption_binding=lambda:deepcopy(fingerprint))
    page=daily.ClanBattleDailyExecutionPage(value,plan,port);page.current='formation'
    reader=daily._DailyTeamReader(plan,value,page)
    if fault=='missing_guard':hits.pop()
    elif fault=='wrong_authority_kind':permission['preview']['kind']='regular'
    elif fault=='wrong_intent':permission['reserved_intent_id']='b'*64
    elif fault=='changed_source':fingerprint['source_sha256']='b'*64
    else:fingerprint['authorization_generation']='b'*32
    argv=NS(image=np.zeros((720,1280,3),np.uint8),custom_recognition_param=json.dumps({'mode':'formal_start_confirmation'}))
    assert reader.analyze(context,argv) is None
    assert not hasattr(port,'_formal_start_confirmation_token')
