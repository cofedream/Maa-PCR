"""Synthetic calibration/replay checks; these are not live clan-page acceptance."""
from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import weakref

import numpy as np
import pytest

from pcr_maa import clan_battle, clan_battle_native as module, native
from pcr_maa.clan_battle_characters import ClanBattleCharacters, box_inside_roi
from pcr_maa.guide_trial_native import NativeGuideTrial


@pytest.fixture
def plan():
    ids = [129701, 116701, 134701, 135501, 104701]
    return dict(account_id='123456789', server='cn-bilibili', period='2026-09', phase='BC',
                round_min=4, round_max=20, boss=3, unit_ids=ids, stars=[5]*5,
                support_mode='none', support_unit_id=None, auto=True,
                set_by_unit={str(uid): True for uid in ids}, switches=[])


def identity(plan, **updates):
    return dict(period=plan['period'], phase='B', round=5, boss=plan['boss'], simulation=True, **updates)


def team(plan, support=None):
    return dict(identity(plan), unit_ids=list(plan['unit_ids']), stars=list(plan['stars']),
                member_count=5, support_unit_id=support, boxes=[[20+150*i, 500, 130, 130] for i in range(5)])


@pytest.fixture
def calibration(tmp_path):
    image = tmp_path/'synthetic.png'
    native.write_png(image, np.full((720, 1280, 3), 255, np.uint8))
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    label = dict(roi=[10, 10, 150, 50], expected='^合成离线标签$')
    pages = {}
    for page in module.PAGES:
        fields = module.IDENTITY_FIELDS if page in ('target', 'formation', 'support', 'result') else (
            ('period', 'phase', 'round') if page == 'lobby' else ('remaining_seconds',) if page == 'battle' else ())
        if page == 'result':
            fields = (*fields, 'damage')
        pages[page] = dict(labels=[deepcopy(label), deepcopy(label)],
                           fields={key: [200, 20+index*50, 300, 45] for index, key in enumerate(fields)})
        if page in ('target', 'formation', 'support'):
            pages[page]['formal'] = deepcopy(label)
    actions = {}
    for name, page in module.ACTION_PAGES.items():
        actions[name] = dict(page=page, label=deepcopy(label), begin=[1000, 350, 1, 1], end=[1000, 180, 1, 1])
    slots = [[20+150*i, 500, 130, 130] for i in range(5)]
    formation = dict(slots=slots, empty=[deepcopy(label) for _ in range(5)],
        stars=[[20+150*i, 630, 30, 30] for i in range(5)], owned=[deepcopy(label) for _ in range(5)],
        borrowed=[deepcopy(label) for _ in range(5)], candidates=[[100, 100, 130, 130]],
        top=deepcopy(label), bottom=deepcopy(label))
    support = dict(slots=[[100, 100, 130, 130]], stars=[[100, 230, 30, 30]], ids=[[100, 270, 200, 40]],
        available=[deepcopy(label)], unavailable=[deepcopy(label)], top=deepcopy(label), bottom=deepcopy(label))
    return dict(version=1, server='cn-bilibili', frame_size=[1280, 720], calibration_id='synthetic-offline',
        evidence_kind='offline_replay', battle_hud='guide-trial-1280x720', pages=pages, actions=actions,
        formation=formation, support=support,
        samples=[dict(page=page, path=str(image), sha256=digest) for page in module.PAGES])


@pytest.fixture
def calibration_v2(calibration):
    value = deepcopy(calibration)
    value.update(version=2, boss_targets={str(index): dict(target=[index*180, 200, 100, 100],
        name=f'合成魔物{index}') for index in range(1, 6)}, phase_labels={'第2阶段': 'B', '第3阶段': 'C'})
    label = deepcopy(value['pages']['lobby']['labels'][0])
    definitions = {
        'adventure': ((), ()), 'lobby': (('period',), ()),
        'boss_detail': (('phase', 'boss'), ('period',)),
        'difficulty': ((), ('period', 'boss')),
        'target': (('phase', 'boss', 'simulation'), ('period',)),
        'formation': (('simulation',), ('period', 'phase', 'boss')),
        'support': (('simulation',), ('period', 'phase', 'boss')),
        'battle': (('remaining_seconds',), ()),
        'result': (('damage', 'simulation'), ('period', 'phase', 'boss')), 'closed': ((), ()),
    }
    for page, (visible, inherited) in definitions.items():
        value['pages'][page] = dict(labels=[deepcopy(label), deepcopy(label)],
            fields={key: [200, 20+index*50, 300, 45] for index, key in enumerate(visible)}, inherit=list(inherited))
        if page in ('formation', 'support'):
            value['pages'][page]['formal'] = deepcopy(label)
    value['pages']['result']['fields']['damage'] = [88, 47, 162, 38]
    value['pages']['lobby']['boss_slots'] = {str(boss): dict(round=[200, 20, 300, 45],
        defeated=deepcopy(label)) for boss in range(1, 6)}
    value['pages']['boss_detail']['navigation'] = {direction: deepcopy(label) for direction in ('previous', 'next')}
    value['pages']['battle']['speed_one'] = deepcopy(label)
    value['pages']['target']['live_state'] = {state: deepcopy(label) for state in ('on', 'off', 'absent')}
    value['actions'] = {name: dict(page=page, label=deepcopy(label), target=[10, 10, 40, 40],
        begin=[1000, 350, 1, 1], end=[1000, 180, 1, 1]) for name, page in module.V2_ACTION_PAGES.items()}
    value['actions']['select_difficulty']['choices'] = {'B': dict(label=deepcopy(label), selected=deepcopy(label))}
    value['samples'].extend(dict(value['samples'][0], page=page) for page in ('boss_detail', 'difficulty'))
    return value


def v2_context(calibration, plan):
    nodes = module.calibration_pipeline(calibration, plan)
    texts = {'CBField_lobby_period': plan['period'], 'CBField_battle_remaining_seconds': '1:30',
             'CBField_result_damage': '12,345×3.1',
             'CBField_result_damage_verify': '12,345×3.1',
             'CBField_result_simulation': '模拟战的战斗结果不会显示在团队战成绩中。'}
    for page in ('boss_detail', 'target'):
        texts.update({f'CBField_{page}_phase': '第2阶段', f'CBField_{page}_boss': f'合成魔物{plan["boss"]}'})
    for page in ('target', 'formation', 'support'):
        texts[f'CBField_{page}_simulation'] = '设定为模拟战。'
    texts.update({'CBNavRound'+str(boss): '第5轮' for boss in range(1, 6)})
    texts.update(CBNavBoss=f'合成魔物{plan["boss"]}', CBNavSimulation='设定为模拟战。')
    guards = {f'CBLabel_{page}_{index}' for page in module.V2_PAGES for index in range(2)} | {'CBOneSpeed', 'CBLiveState_absent'}
    return nodes, texts, guards, context_for(nodes, texts, guards)


def own_only_profile(calibration):
    calibration['support_modes'] = ['none']
    calibration.pop('support')
    for action in ('open_support', 'support_back', 'support_top', 'support_next'):
        calibration['actions'].pop(action)
    return calibration


def test_v2_own_only_scope_omits_unverified_support_input_but_keeps_borrowed_markers(calibration_v2, plan):
    profile = own_only_profile(calibration_v2)
    nodes = module.calibration_pipeline(profile, plan)
    assert all('CBSupport'+str(index) in nodes for index in range(5))
    assert not any(name in nodes for name in ('CBReadSupport', 'CBAddSupport', 'CBAction_open_support',
                                             'CBAction_support_back', 'CBAction_support_next', 'CBSupportId0'))
    profile['formation'].pop('borrowed')
    with pytest.raises(ValueError, match='formation_invalid'):
        module.calibration_pipeline(profile, plan)


@pytest.mark.parametrize('support_mode', ['qsjs', 'specified'])
def test_v2_own_only_scope_rejects_support_plans_before_input(calibration_v2, plan, support_mode, monkeypatch):
    profile = own_only_profile(calibration_v2)
    monkeypatch.setattr(clan_battle, 'load_snapshot', lambda _: dict(ownership_complete=True,
        units=[dict(unit_id=uid) for uid in plan['unit_ids'][:-1]]))
    with pytest.raises(RuntimeError, match='support_mode_uncalibrated'):
        module.calibration_pipeline(profile, dict(plan, support_mode=support_mode))


@pytest.mark.parametrize('scope', [None, [], ['none', 'specified'], 'none', [False]])
def test_v2_support_scope_rejects_unsupported_contract(calibration_v2, plan, scope):
    calibration_v2['support_modes'] = scope
    with pytest.raises(ValueError, match='support_scope_invalid'):
        module.calibration_pipeline(calibration_v2, plan)


def bind_v2_target(page, context, image):
    page.read(context, image, 'lobby')
    page.advance('open_boss')
    page.read(context, image, 'boss_detail')
    return page.read(context, image, 'target')


def test_v2_boss_slots_and_selected_simulation_difficulty_bind_without_invented_round(calibration_v2, plan):
    nodes, texts, guards, context = v2_context(calibration_v2, plan)
    page = module.ClanBattlePage(plan, calibration_v2)
    frame = np.zeros((720, 1280, 3), np.uint8)
    page.read(context, frame, 'lobby')
    assert page.action(context, frame, 'open_boss') == calibration_v2['boss_targets']['3']['target']
    assert 'CBActionLabel_open_boss' not in nodes
    assert nodes['CBField_target_boss']['expected'] == '^合成魔物3$'
    page.advance('open_boss')
    assert 'simulation' not in page.read(context, frame, 'boss_detail')
    target = page.read(context, frame, 'target')
    assert target['round'] is None and target['round_source'] == 'selected_difficulty'
    assert target['selected_difficulty'] == dict(phase='B', boss=3, simulation=True)
    page.advance('open_formation')
    formed = page.read(context, frame, 'formation')
    assert formed['selected_difficulty'] == target['selected_difficulty']
    with pytest.raises(RuntimeError, match='transition_unverified'):
        page.read(context, frame, 'result')
    page.advance('start')
    battle = page.read(context, frame, 'battle')
    assert battle['remaining_seconds'] == 90 and battle['battle_speed'] == 1
    result = page.read(context, frame, 'result')
    assert result['damage'] == 12345 and result['round'] is None
    page.advance('return_to_target')
    with pytest.raises(RuntimeError, match='transition_unverified'):
        page.read(context, frame, 'target')
    page.read(context, frame, 'lobby')
    page.advance('open_boss')
    page.read(context, frame, 'boss_detail')
    assert page.read(context, frame, 'target')['boss'] == 3


@pytest.mark.parametrize('observed', ['boss_detail', 'difficulty', 'target', 'formation', 'support', 'result'])
def test_v2_unbound_pages_cannot_inherit_identity(calibration_v2, plan, observed):
    _, _, _, context = v2_context(calibration_v2, plan)
    page = module.ClanBattlePage(plan, calibration_v2)
    frame = np.zeros((720, 1280, 3), np.uint8)
    with pytest.raises(RuntimeError, match='transition_unverified'):
        page.read(context, frame, observed)
    assert not page.bound


def test_v2_initial_detail_observation_cannot_authorize_battle(calibration_v2, plan):
    _, _, _, context = v2_context(calibration_v2, plan)
    page = module.ClanBattlePage(plan, calibration_v2)
    frame = np.zeros((720, 1280, 3), np.uint8)
    assert page.read(context, frame, 'boss_detail', observe_only=True) == dict(page='boss_detail')
    assert page.action(context, frame, 'back_to_lobby', observe_only=True) == [10, 10, 40, 40]
    assert not page.bound and page.current_page is None
    with pytest.raises(RuntimeError, match='transition_unverified'):
        page.action(context, frame, 'open_formation')
    with pytest.raises(RuntimeError, match='observation_scope_invalid'):
        page.action(context, frame, 'open_boss', observe_only=True)


def test_v2_difficulty_selection_requires_radio_readback_then_actual_detail_phase(calibration_v2, plan):
    _, texts, guards, context = v2_context(calibration_v2, plan)
    texts['CBField_boss_detail_phase'] = '第1阶段'
    page = module.ClanBattlePage(plan, calibration_v2)
    frame = np.zeros((720, 1280, 3), np.uint8)
    page.read(context, frame, 'lobby')
    page.advance('open_boss')
    assert page.read(context, frame, 'boss_detail')['phase'] is None
    page.advance('open_difficulty')
    assert page.read(context, frame, 'difficulty')['boss'] == plan['boss']
    guards.add('CBActionLabel_confirm_difficulty')
    with pytest.raises(RuntimeError, match='selection_unverified'):
        page.action(context, frame, 'confirm_difficulty')
    guards.add('CBDifficultySelected')
    assert page.action(context, frame, 'confirm_difficulty') == [30, 30, 20, 20]
    page.advance('confirm_difficulty')
    texts['CBField_boss_detail_phase'] = '第2阶段'
    assert page.read(context, frame, 'boss_detail')['phase'] == 'B'
    assert page.read(context, frame, 'target')['selected_difficulty']['phase'] == 'B'


def test_v2_live_battle_settings_must_be_disabled_before_entering_formation(calibration_v2, plan):
    _, _, guards, context = v2_context(calibration_v2, plan)
    guards.remove('CBLiveState_absent')
    guards.update({'CBLiveState_on', 'CBActionLabel_open_formation'})
    page = module.ClanBattlePage(plan, calibration_v2)
    frame = np.zeros((720, 1280, 3), np.uint8)
    assert bind_v2_target(page, context, frame)['live_state'] == 'on'
    with pytest.raises(RuntimeError, match='settings_not_ready'):
        page.action(context, frame, 'open_formation')
    assert page.action(context, frame, 'disable_live_state') == [10, 10, 40, 40]
    page.advance('disable_live_state')
    guards.remove('CBLiveState_on')
    guards.add('CBLiveState_off')
    assert page.read(context, frame, 'target')['live_state'] == 'off'
    with pytest.raises(RuntimeError, match='settings_changed'):
        page.action(context, frame, 'disable_live_state')
    assert page.action(context, frame, 'open_formation') == [30, 30, 20, 20]


@pytest.mark.parametrize('markers', [set(), {'CBLiveState_on', 'CBLiveState_off'}])
def test_v2_unknown_or_ambiguous_live_settings_cannot_prepare(calibration_v2, plan, markers):
    _, _, guards, context = v2_context(calibration_v2, plan)
    guards.remove('CBLiveState_absent')
    guards.update(markers)
    page = module.ClanBattlePage(plan, calibration_v2)
    with pytest.raises(RuntimeError, match='settings_unknown'):
        bind_v2_target(page, context, np.zeros((720, 1280, 3), np.uint8))
    assert 'selected_difficulty' not in page.bound


@pytest.mark.parametrize('field,text', [('phase', '第3阶段'), ('boss', '合成魔物4'), ('simulation', '设定为实战。')])
def test_v2_detail_rejects_wrong_target_phase_or_selected_mode(calibration_v2, plan, field, text):
    plan['phase'] = 'B'
    _, texts, _, context = v2_context(calibration_v2, plan)
    page = module.ClanBattlePage(plan, calibration_v2)
    frame = np.zeros((720, 1280, 3), np.uint8)
    page.read(context, frame, 'lobby')
    page.advance('open_boss')
    page.read(context, frame, 'boss_detail')
    texts[f'CBField_target_{field}'] = text
    with pytest.raises(RuntimeError, match='target_mismatch|field_unknown'):
        page.read(context, frame, 'target')
    assert 'selected_difficulty' not in page.bound


@pytest.mark.parametrize('change', [
    lambda value: value['boss_targets']['2'].update(target=value['boss_targets']['1']['target']),
    lambda value: value['pages']['target']['fields'].pop('phase'),
    lambda value: value['pages']['formation']['inherit'].append('simulation'),
    lambda value: value['pages']['support']['fields'].pop('simulation'),
    lambda value: value['pages']['result']['fields'].pop('simulation'),
])
def test_v2_calibration_cannot_hide_mode_or_target_guards(calibration_v2, plan, change):
    change(calibration_v2)
    with pytest.raises(ValueError, match='calibration_'):
        module.calibration_pipeline(calibration_v2, plan)


def test_v2_calendar_window_derives_current_year_only_from_current_displayed_event(monkeypatch):
    from datetime import datetime, timezone, timedelta
    class Clock:
        @staticmethod
        def now(zone):
            return datetime(2026, 10, 25, 13, 0, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(module, 'datetime', Clock)
    assert module._current_period('10/25 05:00～10/29 23:59') == '2026-10'
    assert module._current_period('10/2505:00~10/2923:59') == '2026-10'
    with pytest.raises(RuntimeError, match='not_current'):
        module._current_period('09/25 05:00～09/29 23:59')
    with pytest.raises(RuntimeError, match='period_unknown'):
        module._current_period('10/40 05:00～10/41 23:59')


@pytest.mark.parametrize('initial', ['CBObserve_lobby', 'CBObserve_boss_detail'])
def test_v2_prepare_normalizes_current_clan_page_before_account_check(calibration_v2, plan, tmp_path, monkeypatch, initial):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    calls = []
    port.page = NS(reset_context=lambda: calls.append('reset_context'))
    monkeypatch.setattr(port, 'configure', lambda _: None)
    monkeypatch.setattr(module, 'profile_player_id', lambda _: int(plan['account_id']))
    states = [initial]
    if initial == 'CBObserve_boss_detail':
        states.append('CBObserve_lobby')
    states += ['LabyClientAdventureObserved', 'LabyClientMainMenuObserved', module.PROFILE_NODE,
               'LabyClientMainMenuObserved', 'LabyClientAdventureObserved', 'CBPage_lobby',
               'CBPage_boss_detail', 'CBPage_formation']
    readings = iter(states)
    monkeypatch.setattr(port, 'wait', lambda *args, **kwargs: next(readings))
    monkeypatch.setattr(port, 'run', lambda node, **kwargs: calls.append(node) or {})
    monkeypatch.setattr(port, 'choose', lambda names, **kwargs: 'CBPage_target')
    monkeypatch.setattr(port, 'read_page', lambda name: (calls.append('read_'+name) or dict(phase='B', live_state='absent'), {}))
    monkeypatch.setattr(port, 'form_team', lambda: calls.append('form_team'))
    monkeypatch.setattr(port, '_open_simulation_target', lambda _: calls.append('read_target') or ({}, {}))
    port.prepare(plan)
    assert calls.index('CBAction_back_to_adventure') < calls.index(module.PROFILE_NODE) < calls.index('CBAction_open_clan')
    assert calls.index('read_target') < calls.index('CBAction_open_formation') < calls.index('form_team')
    assert 'CBStart' not in calls and 'CBAction_open_simulation' not in calls
    assert ('CBAction_back_to_lobby' in calls) == (initial == 'CBObserve_boss_detail')


def test_v2_return_reselects_simulation_after_next_returns_to_lobby(calibration_v2, plan, tmp_path, monkeypatch):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    port.account = plan['account_id']
    calls = []
    observed = result_page_receipt(dict(identity(plan), damage=123))
    port.last_receipt = port._simulation_result_receipt = observed
    monkeypatch.setattr(port, 'read_page', lambda *_: pytest.fail('must not recapture result'))
    def run(name, *, click=False):
        assert click is True
        calls.append(name)
        return {}
    monkeypatch.setattr(port, 'run', run)
    monkeypatch.setattr(port, 'wait', lambda names, **kwargs: calls.append('wait_'+names[0]) or names[0])
    monkeypatch.setattr(port, '_open_simulation_target', lambda _: (calls.append('select_simulation') or identity(plan), {}))
    returned = port.return_to_target(plan)
    assert returned['return_verified'] and returned['boss'] == plan['boss']
    assert returned['native_evidence']['before'] is observed
    assert calls == ['CBAction_return_to_target', 'select_simulation']


def test_v2_return_wait_failure_never_repeats_next_or_opens_boss(calibration_v2, plan, tmp_path, monkeypatch):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    calls = []
    port.last_receipt = port._simulation_result_receipt = result_page_receipt(dict(identity(plan), damage=123))
    monkeypatch.setattr(port, 'read_page', lambda *_: pytest.fail('must not recapture result'))
    monkeypatch.setattr(port, 'run', lambda node, **kwargs: calls.append(node) or {})
    def fail(*args, **kwargs):
        raise RuntimeError('page_unknown')
    monkeypatch.setattr(port, '_open_simulation_target', fail)
    with pytest.raises(RuntimeError, match='page_unknown'):
        port.return_to_target(plan)
    assert calls == ['CBAction_return_to_target']


@pytest.mark.parametrize('fault', ['missing', 'failed', 'wrong_node', 'unfinished', 'wrong_page',
    'bad_damage', 'wrong_target', 'changed_receipt'])
def test_v2_return_rejects_unverified_or_changed_result_without_input(calibration_v2, plan, tmp_path, monkeypatch, fault):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    observed = result_page_receipt(dict(identity(plan), damage=123))
    port.last_receipt = port._simulation_result_receipt = observed
    detail = observed['nodes'][-1]['recognition']['best']['detail']
    if fault == 'missing': port.last_receipt = None
    elif fault == 'failed': observed['native_ok'] = False
    elif fault == 'wrong_node': observed['nodes'][-1]['name'] = 'CBReadSettings'
    elif fault == 'unfinished': observed['nodes'][-1]['completed'] = False
    elif fault == 'wrong_page': detail['page'] = 'battle'
    elif fault == 'bad_damage': detail['damage'] = True
    elif fault == 'wrong_target': detail['boss'] = plan['boss'] % 5 + 1
    else: port.last_receipt = deepcopy(observed)
    monkeypatch.setattr(port, 'run', lambda *args, **kwargs: pytest.fail('must not send return input'))
    with pytest.raises(RuntimeError, match='clan_battle_(result_unknown|target_mismatch_or_not_simulation)'):
        port.return_to_target(plan)


def test_v2_team_star_animation_survives_slow_first_observation_without_input(calibration_v2, plan, tmp_path, monkeypatch):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    incomplete = dict(team(plan), stars=[None]*5)
    observations, calls = iter([incomplete, team(plan)]), []
    monkeypatch.setattr(port, 'run', lambda node: calls.append(node) or receipt(next(observations)))
    ticks = iter([10, 16])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(port, 'checkpoint', lambda: None)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    assert port.read_team()['stars'] == [5]*5
    assert calls == ['CBReadTeam', 'CBReadTeam']


def test_v2_missing_star_digits_stop_before_clearing_team(calibration_v2, plan, tmp_path, monkeypatch):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    port.plan = plan
    calls = []
    monkeypatch.setattr(port, 'run', lambda node: calls.append(node) or receipt(dict(team(plan), stars=[None]*5)))
    ticks = iter([10, 10, 26])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(RuntimeError, match='stars_unreadable'):
        port.form_team()
    assert calls == ['CBReadTeam', 'CBReadTeam']


@pytest.fixture
def formation_replay(calibration_v2, plan, tmp_path, monkeypatch):
    class Replay(module.NativeClanBattle):
        def __init__(self):
            super().__init__(NS(resource=Registry(), controller=NS(
                cached_image=np.zeros((720, 1280, 3), np.uint8))), tmp_path, calibration=calibration_v2)
            self.plan, self.support_unit_id = plan, None
            self.page = module.ClanBattlePage(plan, calibration_v2)
            self.characters = NS(match=lambda image, uid, roi: dict(unit_id=uid, box=[roi[0],roi[1],126,126]))
            self.selected, self.inputs, self.readings = [None]*5, [], []
            self.clear_delta, self.add_change, self.final_change, self.full_reads = 1, None, None, 0

        def _reset_list(self, section): pass

        def checkpoint(self): pass  # Synthetic replay has no native tasker.

        def run(self, node, *, click=False):
            if node == 'CBReadTeam':
                ids = self.selected+[None]*(5-len(self.selected))
                stars = [None]*5
                if len(self.selected) == 5 and all(uid is not None for uid in ids):
                    self.full_reads += 1
                    stars = list(plan['stars'])
                    if self.full_reads > 1:
                        if self.final_change == 'stars': stars[-1] = None
                        elif self.final_change == 'unit': ids[-1] = 999901
                        elif self.final_change == 'support':
                            return receipt(dict(team(plan), support_unit_id=ids[-1]))
                reading = dict(identity(plan), unit_ids=ids, stars=stars,
                    member_count=len(self.selected), support_unit_id=None)
                self.readings.append(deepcopy(reading))
                return receipt(reading)
            self.inputs.append(node)
            assert node == 'CBEditTeam' and click
            params = self.tasker.resource.nodes[node]['custom_recognition_param']
            if params['mode'] == 'clear':
                assert params['count'] == len(self.selected)
                for _ in range(self.clear_delta): self.selected.pop()
            else:
                assert params['mode'] == 'add' and params['unit_id'] not in self.selected
                if self.add_change != 'missing':
                    self.selected.append(999901 if self.add_change == 'wrong_unit' else params['unit_id'])
            return {}

    # A failed strict observation reaches its deadline without slowing the regression check.
    ticks = iter(range(0, 10000, 20))
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(ticks))
    return Replay()


def test_v2_form_team_clears_old_unknown_stars_then_strictly_verifies_target(formation_replay, plan):
    port = formation_replay
    port.form_team()
    assert port.inputs == ['CBEditTeam']*10
    assert [row['member_count'] for row in port.readings[:6]] == [5, 4, 3, 2, 1, 0]
    assert port.selected == plan['unit_ids']
    assert port.readings[-1]['stars'] == plan['stars']
    assert port.full_reads == 2


@pytest.mark.parametrize('support_mode', ['none', 'qsjs'])
def test_partial_team_reuses_recognized_own_members_and_verifies_final_team(formation_replay, plan, support_mode):
    plan['support_mode'] = support_mode
    port = formation_replay
    port.selected = [plan['unit_ids'][3], plan['unit_ids'][0], plan['unit_ids'][2], plan['unit_ids'][1]]
    port.form_team()
    assert port.inputs == ['CBEditTeam']
    assert set(port.selected) == set(plan['unit_ids'])
    assert port.readings[-1]['stars'] == plan['stars']
    assert port.full_reads == 2


@pytest.mark.parametrize('selected', ['unknown', 'non_target', 'duplicate'])
def test_partial_team_does_not_reuse_unknown_non_target_or_duplicate_members(formation_replay, plan, selected):
    port = formation_replay
    port.selected = {'unknown': [plan['unit_ids'][0], None],
        'non_target': [plan['unit_ids'][0], 999901],
        'duplicate': [plan['unit_ids'][0], plan['unit_ids'][0]]}[selected]
    port.form_team()
    assert port.inputs == ['CBEditTeam'] * 7
    assert port.selected == plan['unit_ids']


def test_partial_team_does_not_change_support_path(formation_replay, plan, monkeypatch):
    port = formation_replay
    port.selected = plan['unit_ids'][:2]
    port.plan = dict(plan, support_mode='specified', support_unit_id=plan['unit_ids'][-1])
    port.support_unit_id = plan['unit_ids'][-1]
    monkeypatch.setattr(port, '_reset_list', lambda _: (_ for _ in ()).throw(RuntimeError('after_clear')))
    with pytest.raises(RuntimeError, match='after_clear'):
        port.form_team()
    assert port.inputs == ['CBEditTeam'] * 2
    assert port.selected == []


def test_partial_team_still_rejects_unreadable_final_stars(formation_replay, plan):
    port = formation_replay
    port.selected = plan['unit_ids'][:4]
    port.final_change = 'stars'
    with pytest.raises(RuntimeError, match='stars_unreadable'):
        port.form_team()
    assert port.inputs == ['CBEditTeam']
    assert len(port.selected) == 5


@pytest.mark.parametrize('delta', [0, 2])
def test_v2_form_team_stops_when_clear_count_does_not_drop_by_one(formation_replay, delta):
    port = formation_replay
    port.clear_delta = delta
    with pytest.raises(RuntimeError, match='clear_readback_unknown'):
        port.form_team()
    assert port.inputs == ['CBEditTeam']



@pytest.mark.parametrize('change', ['missing', 'wrong_unit'])
def test_v2_form_team_stops_when_added_member_is_not_confirmed(formation_replay, change):
    port = formation_replay
    port.add_change = change
    with pytest.raises(RuntimeError, match='add_readback_unknown'):
        port.form_team()
    assert port.inputs == ['CBEditTeam']*6


@pytest.mark.parametrize('change,error', [('stars', 'stars_unreadable'),
    ('unit', 'team_or_support_mismatch'), ('support', 'team_or_support_mismatch')])
def test_v2_form_team_final_unknown_or_wrong_party_never_starts(formation_replay, plan, change, error):
    port = formation_replay
    port.final_change = change
    with pytest.raises(RuntimeError, match=error):
        port.form_team()
    assert port.inputs == ['CBEditTeam']*10
    with pytest.raises(RuntimeError, match=error):
        port.start_and_read(plan)
    assert 'CBStart' not in port.inputs



@pytest.mark.parametrize('adding', [False, True])
def test_edit_readback_waits_for_click_effect_without_resending(plan, calibration_v2, tmp_path, monkeypatch, adding):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    uid = plan['unit_ids'][0] if adding else None
    before = dict(member_count=0 if adding else 1, unit_ids=[None]*5, stars=[None]*5)
    expected = dict(member_count=1 if adding else 0,
        unit_ids=[uid]+[None]*4, stars=[None]*5)
    # Add can show the new occupied slot before its portrait becomes readable.
    in_between = dict(expected, unit_ids=[None]*5) if adding else before
    frames = iter([before, in_between, expected])
    calls = []
    monkeypatch.setattr(port, 'run', lambda node: calls.append(node) or receipt(next(frames)))
    monkeypatch.setattr(port, 'checkpoint', lambda: None)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    monkeypatch.setattr(module.time, 'monotonic', lambda: 0)
    actual = port._read_team_after_edit(before['member_count'], added_unit_id=uid)
    assert actual == expected
    assert calls == ['CBReadTeam']*3



@pytest.mark.parametrize('adding', [False, True])
@pytest.mark.parametrize('settles', [False, True])
def test_slow_first_unchanged_edit_readback_is_observed_once_more(plan, calibration_v2, tmp_path, monkeypatch, adding, settles):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    uid = plan['unit_ids'][0] if adding else None
    before = dict(member_count=0 if adding else 1, unit_ids=[None]*5, stars=[None]*5)
    after = dict(member_count=1 if adding else 0, unit_ids=[uid]+[None]*4, stars=[None]*5)
    frames = iter([before, after if settles else before])
    reads = []
    monkeypatch.setattr(port, 'read_team', lambda *, require_stars: reads.append(require_stars) or next(frames))
    monkeypatch.setattr(port, 'run', lambda *args, **kwargs: pytest.fail('No input should be sent'))
    monkeypatch.setattr(port, 'checkpoint', lambda: None)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    ticks = iter([0, 6, 12])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(ticks))
    if settles:
        assert port._read_team_after_edit(before['member_count'], added_unit_id=uid) == after
    else:
        with pytest.raises(RuntimeError, match='add_readback_unknown' if adding else 'clear_readback_unknown'):
            port._read_team_after_edit(before['member_count'], added_unit_id=uid)
    assert reads == [False, False]


@pytest.mark.parametrize('count', [2, 5])
def test_slow_first_clear_readback_rejects_unexpected_count_without_another_read(plan, calibration_v2, tmp_path, monkeypatch, count):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    reads = []
    monkeypatch.setattr(port, 'read_team', lambda *, require_stars: reads.append(require_stars) or dict(
        member_count=count, unit_ids=[None]*5, stars=[None]*5))
    monkeypatch.setattr(module.time, 'monotonic', lambda: 0)
    with pytest.raises(RuntimeError, match='clear_readback_unknown'):
        port._read_team_after_edit(1)
    assert reads == [False]


@pytest.mark.parametrize('settles', [True, False])
def test_slow_first_add_readback_observes_animation_once_more(plan, calibration_v2, tmp_path, monkeypatch, settles):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    uid = plan['unit_ids'][3]
    partial = dict(member_count=4, unit_ids=plan['unit_ids'][:3] + [None, None], stars=[5, 5, 5, None, None])
    resolved = dict(partial, unit_ids=plan['unit_ids'][:4] + [None])
    frames = iter([partial, resolved if settles else partial])
    reads = []
    monkeypatch.setattr(port, 'read_team', lambda *, require_stars: reads.append(require_stars) or next(frames))
    monkeypatch.setattr(port, 'run', lambda *args, **kwargs: pytest.fail('No input should be sent'))
    monkeypatch.setattr(port, 'checkpoint', lambda: None)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    ticks = iter([0, 6, 12])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(ticks))
    if settles:
        assert port._read_team_after_edit(3, added_unit_id=uid) == resolved
    else:
        with pytest.raises(RuntimeError, match='add_readback_unknown'):
            port._read_team_after_edit(3, added_unit_id=uid)
    assert reads == [False, False]


@pytest.mark.parametrize('count', [1, 5])
def test_slow_add_readback_rejects_unexpected_count_without_another_read(plan, calibration_v2, tmp_path, monkeypatch, count):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    reads = []
    monkeypatch.setattr(port, 'read_team', lambda *, require_stars: reads.append(require_stars) or dict(
        member_count=count, unit_ids=[None]*5, stars=[None]*5))
    monkeypatch.setattr(module.time, 'monotonic', lambda: 0)
    with pytest.raises(RuntimeError, match='add_readback_unknown'):
        port._read_team_after_edit(3, added_unit_id=plan['unit_ids'][3])
    assert reads == [False]


def test_edit_readback_rejects_known_wrong_new_member_immediately(plan, calibration_v2, tmp_path, monkeypatch):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    calls = []
    reading = dict(member_count=1, unit_ids=[plan['unit_ids'][1]]+[None]*4, stars=[None]*5)
    monkeypatch.setattr(port, 'run', lambda node: calls.append(node) or receipt(reading))
    monkeypatch.setattr(module.time, 'monotonic', lambda: 0)
    with pytest.raises(RuntimeError, match='add_readback_unknown'):
        port._read_team_after_edit(0, added_unit_id=plan['unit_ids'][0])
    assert calls == ['CBReadTeam']


@pytest.mark.parametrize('adding', [False, True])
def test_edit_readback_does_not_retry_an_edit_when_animation_never_resolves(plan, calibration_v2, tmp_path, monkeypatch, adding):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    before = dict(member_count=0 if adding else 1, unit_ids=[None]*5, stars=[None]*5)
    calls = []
    monkeypatch.setattr(port, 'run', lambda node: calls.append(node) or receipt(before))
    monkeypatch.setattr(port, 'checkpoint', lambda: None)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    ticks = iter([0, 0, 1, 1, 6])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(RuntimeError, match='add_readback_unknown' if adding else 'clear_readback_unknown'):
        port._read_team_after_edit(before['member_count'], added_unit_id=plan['unit_ids'][0] if adding else None)
    assert calls == ['CBReadTeam']*2



@pytest.fixture
def list_top_replay(tmp_path):
    def build(positions, *, verified_top=None, direction='top', thumb_lengths=None):
        frames = []
        for index, position in enumerate(positions):
            frame = np.zeros((720, 1280, 3), np.uint8)
            if position is not None:
                length = 30 if thumb_lengths is None else thumb_lengths[index]
                frame[157+position:157+position+length, 1217:1236] = [220, 160, 70]
            # Equipment/star badge animation must not count as scroll progress.
            frame[250:280, 300:330] = index+1
            frames.append(frame)
        port = module.NativeClanBattle(NS(controller=NS(cached_image=frames[0])), tmp_path,
            calibration={'version': 2})
        state = dict(index=0, inputs=[], pages=[])
        def page(section):
            state['pages'].append(section)
            port.tasker.controller.cached_image = frames[state['index']]
            return {}, {}
        def run(node, *, click=False):
            assert node == 'CBAction_characters_'+direction and click
            state['inputs'].append(node)
            state['index'] = min(state['index']+1, len(frames)-1)
        port.read_page, port.run = page, run
        port._list_hit = lambda section, position: verified_top is not None and state['index'] == verified_top
        return port, state
    return build


def test_formation_reset_list_moves_up_until_original_top_is_verified(list_top_replay):
    port, state = list_top_replay([100, 50, 10], verified_top=2)
    port._reset_list('formation')
    assert state['inputs'] == ['CBAction_characters_top']*2
    assert state['pages'] == ['formation']*4


def test_formation_reset_list_already_top_sends_no_input(list_top_replay):
    port, state = list_top_replay([10], verified_top=0)
    port._reset_list('formation')
    assert state['inputs'] == [] and state['pages'] == []


@pytest.mark.parametrize('positions,error,inputs', [
    ([100, 100], 'list_unchanged_no_retry', 1),
    ([100, 150], 'list_unchanged_no_retry', 1),
    ([None, 10], 'list_position_unknown', 0),
    ([100, None], 'list_position_unknown', 1),
])
def test_formation_reset_list_stops_without_verified_scroll_progress(list_top_replay, positions, error, inputs):
    port, state = list_top_replay(positions)
    with pytest.raises(RuntimeError, match=error):
        port._reset_list('formation')
    assert state['inputs'] == ['CBAction_characters_top']*inputs


def test_formation_reset_list_never_accepts_position_without_top_marker(list_top_replay):
    port, state = list_top_replay(list(range(100, 83, -1)))
    with pytest.raises(RuntimeError, match='list_top_not_verified'):
        port._reset_list('formation')
    assert state['inputs'] == ['CBAction_characters_top']*16



@pytest.mark.parametrize('target_page', [16, 48, None])
def test_v2_formation_scans_every_destination_without_exceeding_swipe_budget(formation_replay, plan, target_page):
    port = formation_replay
    port.selected = []
    state = dict(page=0, swipes=0, scans=[])
    target = plan['unit_ids'][0]
    def reset(section):
        assert section == 'formation'
        state['page'] = 0
    def next_page(section):
        assert section == 'formation'
        state['swipes'] += 1
        state['page'] += 1
        return True
    def match(frame, uid, roi):
        if uid == target:
            state['scans'].append(state['page'])
            if state['page'] != target_page:
                return None
        return dict(unit_id=uid, box=[roi[0],roi[1],126,126])
    port._reset_list, port._next_list, port.characters.match = reset, next_page, match
    if target_page is None:
        with pytest.raises(RuntimeError, match='character_not_found:'+str(target)):
            port.form_team()
        assert port.inputs == []
    else:
        port.form_team()
        assert port.selected == plan['unit_ids'] and port.inputs == ['CBEditTeam']*5
    last = 48 if target_page is None else target_page
    assert state['scans'] == list(range(last+1))
    assert state['swipes'] == last


@pytest.mark.parametrize('stale', [False, True])
def test_v2_formation_hint_preserves_click_order_and_full_search_fallback(formation_replay, plan, monkeypatch, stale):
    from pcr_maa.labyrinth import character_recognition
    port = formation_replay
    port.selected = []
    first, second, *rest = plan['unit_ids']
    state = dict(page=0, down=0, clicks=[])
    original_run = port.run

    def run(node, *, click=False):
        if node == 'CBAction_characters_top':
            assert click and state['page'] > 0
            state['page'] -= 1
            return {}
        if node == 'CBEditTeam':
            state['clicks'].append(state['page'])
        return original_run(node, click=click)

    def advance(section):
        state['page'] += 1
        state['down'] += 1
        return state['page'] <= 4

    def match(frame, uid, roi):
        location = 4 if uid == first else 1 if uid == second and (not stale or not port.selected) else 0
        if state['page'] == location:
            return dict(unit_id=uid, box=[roi[0], roi[1], 126, 126])

    port._reset_list = module.NativeClanBattle._reset_list.__get__(port)
    port._next_list, port.characters.match, port.run = advance, match, run
    port.read_page = lambda section: None
    port._list_hit = lambda section, position: position == 'top' and state['page'] == 0
    monkeypatch.setattr(character_recognition, 'scrollbar', lambda *a, **k: {'absolute_top': state['page']})
    port.form_team()
    assert port.selected == [first, second, *rest] and port.inputs == ['CBEditTeam'] * 5
    assert state['clicks'] == [4, 0 if stale else 1, 0, 0, 0]
    assert state['down'] == 4



@pytest.mark.parametrize('version', [1, 2])
def test_formation_next_swipe_timing_is_optional_and_does_not_change_other_scrolls(calibration, calibration_v2, plan, version):
    profile = deepcopy(calibration_v2 if version == 2 else calibration)
    original = module.calibration_pipeline(profile, plan)
    assert original['CBAction_characters_next']['duration'] == 400
    assert original['CBAction_characters_next'].get('end_hold', 0) == 0
    assert original['CBAction_characters_next']['post_delay'] == 200
    profile['actions']['characters_next'].update(duration=700, end_hold=250, post_delay=350)
    changed = module.calibration_pipeline(profile, plan)
    assert {key: changed['CBAction_characters_next'][key] for key in ('duration', 'end_hold', 'post_delay')} == dict(
        duration=700, end_hold=250, post_delay=350)
    for name in ('characters_top', 'support_top', 'support_next'):
        assert changed['CBAction_'+name] == original['CBAction_'+name]


@pytest.mark.parametrize('key,value', [('duration', 0), ('duration', -1), ('duration', 5001),
    ('duration', True), ('end_hold', -1), ('end_hold', 5001), ('end_hold', '250'),
    ('post_delay', -1), ('post_delay', 5001), ('post_delay', True)])
def test_formation_next_swipe_rejects_invalid_timing(calibration_v2, plan, key, value):
    profile = deepcopy(calibration_v2)
    profile['actions']['characters_next'][key] = value
    with pytest.raises(ValueError, match='scroll_timing_invalid'):
        module.calibration_pipeline(profile, plan)



def test_formation_next_list_requires_actual_downward_scrollbar_progress(list_top_replay):
    port, state = list_top_replay([100, 150], direction='next')
    assert port._next_list('formation') is True
    assert state['inputs'] == ['CBAction_characters_next']
    assert state['pages'] == ['formation']*2


def test_formation_next_list_at_original_bottom_sends_no_input(list_top_replay):
    port, state = list_top_replay([100], direction='next')
    port._list_hit = lambda section, position: position == 'bottom'
    assert port._next_list('formation') is False
    assert state['inputs'] == [] and state['pages'] == []


@pytest.mark.parametrize('positions,error,inputs', [
    ([100, 100], 'list_unchanged_no_retry', 1),
    ([100, 50], 'list_unchanged_no_retry', 1),
    ([None, 150], 'list_position_unknown', 0),
    ([100, None], 'list_position_unknown', 1),
])
def test_formation_next_list_rejects_badge_only_or_unknown_scroll_changes(list_top_replay, positions, error, inputs):
    port, state = list_top_replay(positions, direction='next')
    with pytest.raises(RuntimeError, match=error):
        port._next_list('formation')
    assert state['inputs'] == ['CBAction_characters_next']*inputs



@pytest.mark.parametrize('band,expected_top,expected', [
    (None, None, dict(visible=False, position=None, can_scroll=None)),
    ((0, 333), None, dict(visible=True, position=None, can_scroll=False)),
    ((8, 31), 165, dict(visible=True, position=8/302, can_scroll=True)),
])
def test_scrollbar_absolute_geometry_is_optional_and_preserves_default_results(band, expected_top, expected):
    from pcr_maa.labyrinth.character_recognition import scrollbar
    frame = np.zeros((720, 1280, 3), np.uint8)
    if band is not None:
        top, length = band
        frame[157+top:157+top+length, 1217:1236] = [220, 160, 70]
    assert scrollbar(frame, 'battle') == expected
    assert scrollbar(frame, 'battle', include_geometry=True) == dict(expected, absolute_top=expected_top)


@pytest.mark.parametrize('direction,lengths', [('top', [31, 28]), ('next', [28, 31])])
def test_formation_scroll_thumb_length_jitter_is_not_movement(list_top_replay, direction, lengths):
    port, state = list_top_replay([8, 8], direction=direction, thumb_lengths=lengths)
    with pytest.raises(RuntimeError, match='list_unchanged_no_retry'):
        if direction == 'top':
            port._reset_list('formation')
        else:
            port._next_list('formation')
    assert state['inputs'] == ['CBAction_characters_'+direction]


@pytest.mark.parametrize('direction,positions', [('top', [9, 8]), ('next', [8, 9])])
def test_formation_scroll_accepts_one_actual_pixel_of_progress(list_top_replay, direction, positions):
    port, state = list_top_replay(positions, direction=direction, verified_top=1 if direction == 'top' else None)
    if direction == 'top':
        port._reset_list('formation')
    else:
        assert port._next_list('formation') is True
    assert state['inputs'] == ['CBAction_characters_'+direction]



def test_v2_formation_bottom_clipped_target_is_added_only_after_complete_readback(formation_replay, plan):
    port = formation_replay
    port.selected = []
    state = dict(page=0, next_calls=0)
    target = plan['unit_ids'][0]
    def match(frame, uid, roi):
        box = [roi[0], roi[1]+roi[3]-30, 126, 126] if uid == target and state['page'] == 0 else [roi[0], roi[1], 126, 126]
        return dict(unit_id=uid, box=box)
    def next_page(section):
        assert section == 'formation' and port.inputs == []
        state['next_calls'] += 1
        state['page'] += 1
        return True
    port.characters.match, port._next_list = match, next_page
    port.form_team()
    assert state['next_calls'] == 1
    assert port.selected == plan['unit_ids'] and port.inputs == ['CBEditTeam']*5


@pytest.mark.parametrize('next_result,error', [(False, 'character_not_found'),
    ('clan_battle_list_position_unknown', 'list_position_unknown'),
    ('clan_battle_list_unchanged_no_retry', 'list_unchanged_no_retry')])
def test_v2_formation_bottom_clipped_target_never_adds_without_safe_next_step(formation_replay, next_result, error):
    port = formation_replay
    port.selected = []
    port.characters.match = lambda frame, uid, roi: dict(unit_id=uid,
        box=[roi[0], roi[1]+roi[3]-30, 126, 126])
    calls = []
    def next_page(section):
        calls.append(section)
        if isinstance(next_result, str):
            raise RuntimeError(next_result)
        return next_result
    port._next_list = next_page
    with pytest.raises(RuntimeError, match=error):
        port.form_team()
    assert calls == ['formation'] and port.inputs == []


@pytest.mark.parametrize('kind', ['top', 'left', 'right', 'wrong_uid', 'ambiguous'])
def test_v2_formation_other_clipping_or_identity_doubt_never_scrolls_or_adds(formation_replay, kind):
    port = formation_replay
    port.selected = []
    if kind == 'ambiguous':
        port.calibration['formation']['candidates'].append([300, 100, 130, 130])
    def match(frame, uid, roi):
        x, y = roi[0], roi[1]+roi[3]-30
        if kind == 'top': y = roi[1]-1
        elif kind == 'left': x -= 1
        elif kind == 'right': x += 5
        return dict(unit_id=999901 if kind == 'wrong_uid' else uid, box=[x, y, 126, 126])
    port.characters.match = match
    port._next_list = lambda _: pytest.fail('Uncertain candidate must not advance the list')
    with pytest.raises(RuntimeError, match='candidate_ambiguous' if kind == 'ambiguous' else 'candidate_clipped'):
        port.form_team()
    assert port.inputs == []


def test_v2_formation_uses_observed_slots_without_invented_owned_label(calibration_v2, plan):
    reader = object.__new__(ClanBattleCharacters)
    reader.team, reader.calibration = plan['unit_ids'], calibration_v2
    slots = calibration_v2['formation']['slots']
    hits = []
    def hit(context, image, name):
        hits.append(name)
        assert not name.startswith('CBOwned')
        return name.endswith('_5')
    reader.page = NS(read=lambda *args: identity(plan), hit=hit)
    reader.match = lambda image, uid, roi: dict(unit_id=uid, box=roi) if uid == plan['unit_ids'][slots.index(roi)] else None
    actual = reader.formation(NS(), np.zeros((720, 1280, 3), np.uint8))
    assert actual['member_count'] == 5 and actual['unit_ids'] == plan['unit_ids']
    assert actual['support_unit_id'] is None and actual['stars'] == [5]*5


def test_v2_account_stars_still_require_same_frame_owned_portraits(calibration_v2, plan, monkeypatch):
    from pcr_maa import clan_battle
    calibration_v2['formation']['star_source'] = 'account_snapshot'
    reader = object.__new__(ClanBattleCharacters)
    reader.plan, reader.team, reader.calibration = plan, plan['unit_ids'], calibration_v2
    slots = calibration_v2['formation']['slots']
    monkeypatch.setattr(clan_battle, 'account_battle_stars', lambda actual: dict(zip(plan['unit_ids'], [5]*5)))
    def hit(context, image, name):
        assert not name.startswith('CBStar')
        return False
    reader.page = NS(read=lambda *args: identity(plan), hit=hit)
    reader.match = lambda image, uid, roi: dict(unit_id=uid, box=roi) if uid == plan['unit_ids'][slots.index(roi)] else None
    actual = reader.formation(NS(), np.zeros((720, 1280, 3), np.uint8))
    assert actual['unit_ids'] == plan['unit_ids'] and actual['stars'] == [5]*5
    assert actual['stars_source'] == 'account_snapshot' and actual['support_unit_id'] is None


@pytest.mark.parametrize('speed', [None, True, 3, 1])
def test_v2_final_timed_input_guard_requires_observed_one_speed(plan, speed):
    reader = object.__new__(ClanBattleCharacters)
    reader.team, reader.calibration = plan['unit_ids'], {'version': 2}
    reader.page = NS(read=lambda *args: dict(remaining_seconds=85, battle_speed=speed))
    reader.read_controls = lambda image: dict(auto=True, sets=[True]*5, unit_ids=plan['unit_ids'])
    context = NS(tasker=NS(stopping=False))
    argv = NS(image=np.zeros((720, 1280, 3), np.uint8))
    result = reader.battle_control(context, argv, dict(mode='battle_set', slot=0, previous=True,
        desired=False, expected_remaining_seconds=85))
    if type(speed) is int and speed == 1:
        assert result.detail['battle_speed'] == 1 and result.detail['same_frame'] is True
    else:
        assert result is None


@pytest.mark.parametrize('node,source,destination', [('CBAction_open_boss', 'lobby', 'boss_detail'),
                                                   ('CBAddSupport', 'support', 'formation')])
@pytest.mark.parametrize('outcome', ['sent', 'not_sent', 'failed', '6002'])
def test_v2_transition_advances_only_after_verified_native_input(calibration_v2, plan, tmp_path, monkeypatch,
                                                               node, source, destination, outcome):
    port = module.NativeClanBattle(NS(), tmp_path, calibration=calibration_v2)
    port.page = module.ClanBattlePage(plan, calibration_v2)
    port.page.current_page = source
    def execute(self, requested, *, click=False):
        assert requested == node and click is True
        if outcome == 'not_sent':
            raise native.NativeInputNotSent(dict(input_execution='not_sent'))
        if outcome == 'failed':
            raise RuntimeError('native_failed')
        return dict(nodes=[dict(name='LabyDisconnect6002Observed' if outcome == '6002' else node)])
    monkeypatch.setattr(NativeGuideTrial, 'run', execute)
    if outcome == 'sent':
        port.run(node, click=True)
        assert port.page.expected_page == destination
    else:
        with pytest.raises((RuntimeError, native.NativeInputNotSent)):
            port.run(node, click=True)
        assert port.page.expected_page is None
    assert port.page.current_page == source


def test_word_accepts_sdk_nested_ocr_node_with_one_expected_pattern(calibration, plan):
    nodes = module.calibration_pipeline(calibration, plan)
    context = context_for(nodes, field_texts('target', plan))
    context.get_node_data = lambda name: dict(recognition=dict(type='OCR', param=dict(
        roi=nodes[name]['roi'], expected=[nodes[name]['expected']])))
    page = module.ClanBattlePage(plan, calibration)
    frame = np.zeros((720, 1280, 3), np.uint8)
    assert page.word(context, frame, 'CBField_target_boss') == str(plan['boss'])
    context.get_node_data = lambda name: dict(recognition=dict(type='OCR', param=dict(
        roi=nodes[name]['roi'], expected=[nodes[name]['expected'], '^.*$'])))
    with pytest.raises(RuntimeError, match='field_unknown'):
        page.word(context, frame, 'CBField_target_boss')


def test_v2_closed_sample_is_optional_but_actionable_page_sample_is_required(calibration_v2, plan):
    calibration_v2['samples'] = [row for row in calibration_v2['samples'] if row['page'] != 'closed']
    assert module.calibration_pipeline(calibration_v2, plan)['CBPage_closed']['action'] == 'DoNothing'
    calibration_v2['samples'] = [row for row in calibration_v2['samples'] if row['page'] != 'formation']
    with pytest.raises(ValueError, match='pages_missing'):
        module.calibration_pipeline(calibration_v2, plan)


def test_v2_unsampled_closed_page_can_be_absent_and_never_recognizes(calibration_v2, plan):
    calibration_v2['pages'].pop('closed')
    calibration_v2['samples'] = [row for row in calibration_v2['samples'] if row['page'] != 'closed']
    nodes = module.calibration_pipeline(calibration_v2, plan)
    assert nodes['CBPage_closed']['action'] == 'DoNothing'
    context = context_for(nodes, {}, set())
    page = module.ClanBattlePage(plan, calibration_v2)
    args = NS(image=np.zeros((720, 1280, 3), np.uint8), custom_recognition_param='{"page":"closed"}')
    assert page.analyze(context, args) is None
    with pytest.raises(RuntimeError, match='page_uncalibrated:closed'):
        page.read(context, args.image, 'closed')


def test_template_alternatives_require_each_bound_file(calibration, tmp_path, monkeypatch):
    root = tmp_path/'resource'
    (root/'image').mkdir(parents=True)
    data = Path(calibration['samples'][0]['path']).read_bytes()
    for name in ('first.png', 'second.png'):
        (root/'image'/name).write_bytes(data)
    monkeypatch.setattr(native, 'RESOURCE', root)
    spec = dict(recognition='TemplateMatch', roi=[0, 0, 100, 100],
        template=['first.png', 'second.png'], sha256=[hashlib.sha256(data).hexdigest()]*2)
    assert module._node(spec)['template'] == ['first.png', 'second.png']
    changed = deepcopy(spec)
    changed['sha256'][1] = '0'*64
    with pytest.raises(ValueError, match='template_changed'):
        module._node(changed)
    changed['sha256'].pop()
    with pytest.raises(ValueError, match='template_invalid'):
        module._node(changed)


class Registry:
    def __init__(self):
        self.nodes, self.callbacks = {}, {}

    def override_pipeline(self, nodes):
        self.nodes.update(deepcopy(nodes))
        return True

    def register_custom_recognition(self, name, callback):
        if name in self.callbacks:
            return False
        self.callbacks[name] = callback
        return True


def receipt(detail):
    return dict(native_ok=True, nodes=[dict(name='CBRead', recognition=dict(best=dict(detail=detail)))])


def result_page_receipt(detail):
    value = receipt(dict(detail, page='result'))
    value['nodes'][-1].update(name='CBPage_result', completed=True)
    return value


def context_for(nodes, texts, hits=None):
    hits = hits or set()
    def read(name, image):
        if name in texts:
            word = dict(text=texts[name], score=.99, box=list(nodes[name]['roi']))
            return NS(hit=True, raw_detail=dict(filtered=[word], best=word), box=word['box'])
        return NS(hit=name in hits, box=[30, 30, 20, 20], raw_detail={})
    return NS(tasker=NS(stopping=False), run_recognition=read, get_node_data=nodes.get)


def field_texts(page, plan, **changes):
    values = dict(period=plan['period'], phase='B', round='5', boss=str(plan['boss']), simulation='模拟战')
    values.update(changes)
    return {f'CBField_{page}_{key}': value for key, value in values.items()}


def test_default_resource_has_no_input_nodes():
    path = native.RESOURCE/'pipeline/clan_battle.json'
    nodes = json.loads(path.read_text(encoding='utf-8'))
    assert nodes and all(row['action'] == 'DoNothing' for row in nodes.values())
    assert nodes['CBClosedReadOnly']['recognition'] == 'And'
    assert nodes['CBReadPage']['custom_recognition_param']['page'] == 'uncalibrated'


def test_missing_calibration_fails_closed_before_registration(plan, tmp_path, monkeypatch):
    registry = Registry()
    port = module.NativeClanBattle(NS(resource=registry), tmp_path)
    reads = []
    monkeypatch.setattr(port, 'choose', lambda names: reads.append(names) or 'GTUnknown')
    with pytest.raises(RuntimeError, match='calibration_missing'):
        port.prepare(plan)
    assert reads == [['CBClosedReadOnly']]
    assert not registry.callbacks and not registry.nodes


def test_default_closed_is_read_only_unavailable(plan, tmp_path, monkeypatch):
    port = module.NativeClanBattle(NS(resource=Registry()), tmp_path)
    monkeypatch.setattr(port, 'choose', lambda names: 'CBClosedReadOnly')
    monkeypatch.setattr(port, 'configure', lambda _: pytest.fail('closed must not configure input'))
    with pytest.raises(RuntimeError, match='^clan_battle_closed$'):
        port.prepare(plan)
    assert port.started is False


def test_pipeline_calibration_generates_only_guarded_inputs(calibration, plan):
    nodes = module.calibration_pipeline(calibration, plan)
    inputs = {key: value for key, value in nodes.items() if value['action'] != 'DoNothing'}
    assert inputs and all(value['recognition'] == 'Custom' and value['max_hit'] == 1 for value in inputs.values())
    assert 'CBAction_start' not in nodes
    assert inputs['CBStart']['custom_recognition'] == 'ClanBattleCharacters'
    assert nodes['CBActionLabel_open_simulation']['expected'] == '^模拟战$'
    assert nodes['CBActionLabel_open_boss']['expected'] == r'^BOSS\s*3$'


@pytest.mark.parametrize('change,error', [
    (lambda c: c['samples'].pop(), 'pages_missing'),
    (lambda c: c['samples'][0].update(sha256='0'*64), 'sample_changed'),
    (lambda c: c['formation']['slots'][0].__setitem__(0, -1), 'roi_invalid'),
    (lambda c: c['pages']['formation']['fields'].pop('simulation'), 'fields_missing'),
    (lambda c: c['actions']['start'].update(page='target'), 'action_page_invalid'),
    (lambda c: c['pages']['target']['labels'][0].update(expected='.*'), 'label_invalid'),
])
def test_calibration_requires_bound_frames_and_strict_guards(calibration, plan, change, error):
    change(calibration)
    with pytest.raises(ValueError, match=error):
        module.calibration_pipeline(calibration, plan)


@pytest.mark.parametrize('updates', [dict(boss=4), dict(round=3), dict(round=21), dict(phase='D'),
                                    dict(simulation=False), dict(period='2026-10'), dict(round=True)])
def test_target_identity_rejects_other_target_and_formal(plan, updates):
    value = identity(plan)
    value.update(updates)
    with pytest.raises(RuntimeError, match='target_mismatch'):
        module.require_target_identity(plan, value)


@pytest.mark.parametrize('phase', ['B', 'C', 'BC'])
def test_bc_job_accepts_actual_b_or_c(plan, phase):
    value = identity(plan)
    value['phase'] = phase
    module.require_target_identity(plan, value)


def test_boss_detail_allows_both_battle_entries_but_formation_rejects_formal(calibration, plan):
    nodes = module.calibration_pipeline(calibration, plan)
    page = module.ClanBattlePage(plan, calibration)
    guards = {'CBLabel_target_0', 'CBLabel_target_1'}
    context = context_for(nodes, field_texts('target', plan), guards)
    image = np.zeros((720, 1280, 3), np.uint8)
    assert page.read(context, image, 'target') == dict(identity(plan), page='target')
    guards.add('CBFormal_target')
    assert page.read(context, image, 'target') == dict(identity(plan), page='target')
    guards.update({'CBLabel_formation_0', 'CBLabel_formation_1', 'CBFormal_formation'})
    with pytest.raises(RuntimeError, match='formal_battle_forbidden'):
        page.read(context, image, 'formation')


@pytest.mark.parametrize('change', ['missing_label', 'wrong_damage_mode', 'disconnect', 'stop', 'box_outside'])
def test_page_unknown_never_yields_action(calibration, plan, change):
    nodes = module.calibration_pipeline(calibration, plan)
    guards = {'CBLabel_target_0', 'CBLabel_target_1', 'CBActionLabel_open_simulation'}
    texts = field_texts('target', plan)
    if change == 'missing_label':
        guards.remove('CBLabel_target_0')
    if change == 'wrong_damage_mode':
        texts['CBField_target_simulation'] = '正式战'
    if change == 'disconnect':
        guards.add('LabyDisconnect6002Observed')
    context = context_for(nodes, texts, guards)
    if change == 'stop':
        context.tasker.stopping = True
    if change == 'box_outside':
        old = context.run_recognition
        def outside(name, image):
            result = old(name, image)
            if name == 'CBField_target_boss':
                result.raw_detail['best']['box'] = [1270, 700, 100, 100]
            return result
        context.run_recognition = outside
    page = module.ClanBattlePage(plan, calibration)
    args = NS(image=np.zeros((720, 1280, 3), np.uint8),
              custom_recognition_param=json.dumps(dict(page='target', action='open_simulation')))
    assert page.analyze(context, args) is None


@pytest.mark.parametrize('change', ['star', 'support', 'duplicate', 'missing', 'bool_star'])
def test_team_start_guard_rejects_wrong_five_or_support(calibration, plan, change):
    page = module.ClanBattlePage(plan, calibration)
    reading = team(plan)
    if change == 'star': reading['stars'][0] = 6
    elif change == 'support': reading['support_unit_id'] = plan['unit_ids'][0]
    elif change == 'duplicate': reading['unit_ids'][1] = reading['unit_ids'][0]
    elif change == 'missing': reading['unit_ids'][1] = None
    else: reading['stars'][0] = True
    with pytest.raises(RuntimeError, match='team_or_support_mismatch'):
        page.require_team(reading)


def test_team_start_guard_uses_ids_for_reordered_stars(calibration, plan):
    page = module.ClanBattlePage(plan, calibration)
    reading = team(plan)
    reading['unit_ids'].reverse()
    reading['stars'].reverse()
    page.require_team(reading)


def test_unknown_old_party_can_be_cleared_without_accepting_it_as_start(calibration, plan):
    value = object.__new__(ClanBattleCharacters)
    value.plan, value.team, value.calibration = plan, plan['unit_ids'], calibration
    class Page:
        def read(self, context, image, page): return identity(plan)
        def hit(self, context, image, name): return name.startswith('CBOwned') or name.endswith('_5')
    value.page = Page()
    value.match = lambda image, uid, roi: None
    reading = value.formation(NS(), np.zeros((720, 1280, 3), np.uint8))
    assert reading['member_count'] == 5 and reading['unit_ids'] == [None]*5
    assert reading['boxes'] == calibration['formation']['slots']
    with pytest.raises(RuntimeError, match='team_or_support_mismatch'):
        module.ClanBattlePage(plan, calibration).require_team(reading)


@pytest.mark.parametrize('mode,previous,desired,state_auto,allowed', [
    ('battle_auto', True, False, True, True), ('battle_auto', False, True, False, True),
    ('battle_auto', True, False, False, False), ('battle_auto', True, True, True, False),
    ('battle_set', False, True, False, True), ('battle_set', True, False, False, False),
])
def test_hud_callback_supports_safe_auto_off_and_rechecks_previous(plan, mode, previous, desired, state_auto, allowed):
    value = object.__new__(ClanBattleCharacters)
    value.team = plan['unit_ids']
    value.page = NS(read=lambda *args: dict(remaining_seconds=85))
    value.read_controls = lambda _: dict(auto=state_auto, sets=[False]*5, unit_ids=plan['unit_ids'])
    context = NS(tasker=NS(stopping=False))
    result = value.battle_control(context, NS(image=np.zeros((720, 1280, 3), np.uint8)),
        dict(mode=mode, previous=previous, desired=desired, slot=2))
    assert bool(result) is allowed


def test_battle_read_has_clock_and_five_controls_from_one_image(plan):
    value = object.__new__(ClanBattleCharacters)
    value.team = plan['unit_ids']
    image = np.full((720, 1280, 3), 31, np.uint8)
    images = []
    def page_read(context, frame, page):
        images.append(frame)
        assert page == 'battle'
        return dict(remaining_seconds=85)
    def controls(frame):
        images.append(frame)
        return dict(auto=False, sets=[True, False, True, False, True], unit_ids=plan['unit_ids'])
    value.page, value.read_controls = NS(read=page_read), controls
    result = value.battle_control(NS(tasker=NS(stopping=False)), NS(image=image), dict(mode='battle_read'))
    assert len(images) == 2 and all(frame is image for frame in images)
    assert result.detail['remaining_seconds'] == 85 and result.detail['same_frame'] is True
    assert result.detail['frame_pixel_sha256'] == hashlib.sha256(image.tobytes()).hexdigest()
    assert result.detail['unit_ids'] == plan['unit_ids'] and result.detail['auto'] is False


def test_read_tick_uses_single_settings_receipt_without_recapture(plan, tmp_path, monkeypatch):
    port = module.NativeClanBattle(NS(), tmp_path)
    state = dict(auto=True, sets=[False]*5, unit_ids=plan['unit_ids'], remaining_seconds=85,
        same_frame=True, frame_pixel_sha256='a'*64)
    calls = []
    monkeypatch.setattr(port, 'checkpoint', lambda: None)
    def choose(names):
        calls.append(names)
        port.last_receipt = receipt(state)
        return 'CBReadSettings'
    monkeypatch.setattr(port, 'choose', choose)
    monkeypatch.setattr(port, 'read_page', lambda *_: pytest.fail('must not recapture page'))
    monkeypatch.setattr(port, 'run', lambda *_a, **_k: pytest.fail('must not recapture controls'))
    tick = port.read_tick()
    assert calls == [['CBPage_result', 'CBReadSettings']]
    assert tick['remaining_seconds'] == tick['state']['remaining_seconds'] == 85
    assert tick['same_frame'] is True and tick['frame_pixel_sha256'] == 'a'*64


@pytest.mark.parametrize('patch', [dict(same_frame=False), dict(frame_pixel_sha256='unknown'),
    dict(remaining_seconds=None), dict(remaining_seconds=True)])
def test_read_tick_rejects_missing_same_frame_proof(plan, tmp_path, monkeypatch, patch):
    port = module.NativeClanBattle(NS(), tmp_path)
    state = dict(auto=True, sets=[False]*5, unit_ids=plan['unit_ids'], remaining_seconds=85,
        same_frame=True, frame_pixel_sha256='a'*64)
    state.update(patch)
    port.last_receipt = receipt(state)
    monkeypatch.setattr(port, 'checkpoint', lambda: None)
    monkeypatch.setattr(port, 'choose', lambda _: 'CBReadSettings')
    with pytest.raises(RuntimeError, match='tick_same_frame_unknown'):
        port.read_tick()


def test_read_tick_result_reuses_observed_receipt(tmp_path, monkeypatch):
    port = module.NativeClanBattle(NS(), tmp_path)
    observed = receipt(dict(damage=123456, simulation=True))
    port.last_receipt = observed
    monkeypatch.setattr(port, 'checkpoint', lambda: None)
    monkeypatch.setattr(port, 'choose', lambda _: 'CBPage_result')
    monkeypatch.setattr(port, 'read_page', lambda *_: pytest.fail('must not recapture result'))
    assert port.read_tick()['result']['result_evidence'] is observed


@pytest.mark.parametrize('box,inside', [([100,100,130,130],True), ([101,101,128,128],True),
    ([99,100,130,130],False), ([100,99,130,130],False), ([101,100,130,130],False),
    ([100,101,130,130],False), ([100,100,0,130],False), ([True,100,130,130],False)])
def test_candidate_full_box_must_fit_roi_including_boundary(box, inside):
    assert box_inside_roi(box, [100,100,130,130]) is inside


@pytest.mark.parametrize('kind', ['full', 'half', 'two'])
def test_add_callback_rejects_partial_or_two_candidates(plan, calibration, kind):
    value = object.__new__(ClanBattleCharacters)
    value.plan, value.team, value.calibration = plan, plan['unit_ids'], deepcopy(calibration)
    if kind == 'two':
        value.calibration['formation']['candidates'].append([300,100,130,130])
    value.formation = lambda *_: dict(member_count=0, unit_ids=[None]*5)
    value.match = lambda image, uid, roi: dict(unit_id=uid,
        box=[roi[0] - (1 if kind == 'half' else 0), roi[1], 130, 130])
    result = value.analyze(NS(tasker=NS(stopping=False)), NS(image=np.zeros((720,1280,3),np.uint8),
        custom_recognition_param=json.dumps(dict(mode='add', unit_id=plan['unit_ids'][0]))))
    assert bool(result) is (kind == 'full')


@pytest.mark.parametrize('kind', ['full', 'half', 'two'])
def test_support_add_callback_requires_one_fully_visible_card(plan, calibration, kind):
    value = object.__new__(ClanBattleCharacters)
    value.calibration = calibration
    candidate = dict(unit_id=plan['unit_ids'][0], stars=5, support_id='123', available=True,
        box=[99 if kind == 'half' else 100,100,130,130])
    value.support_candidates = lambda *_: dict(candidates=[candidate]*(2 if kind == 'two' else 1))
    result = value.analyze(NS(tasker=NS(stopping=False)), NS(image=np.zeros((720,1280,3),np.uint8),
        custom_recognition_param=json.dumps(dict(mode='support_add',unit_id=plan['unit_ids'][0],stars=5,support_id='123'))))
    assert bool(result) is (kind == 'full')


def test_support_read_does_not_offer_half_card(plan, calibration):
    value = object.__new__(ClanBattleCharacters)
    value.plan, value.team, value.calibration = plan, plan['unit_ids'], calibration
    value.page = NS(read=lambda *_: identity(plan))
    value.match = lambda image, uid, roi: (dict(unit_id=uid, box=[99,100,130,130])
        if uid == plan['unit_ids'][0] else None)
    reading = value.support_candidates(NS(), np.zeros((720,1280,3),np.uint8))
    assert reading['candidates'] == []


@pytest.mark.parametrize('kind', ['half', 'two'])
def test_form_team_does_not_dispatch_add_for_clipped_or_ambiguous_card(plan, calibration, tmp_path, monkeypatch, kind):
    calibration = deepcopy(calibration)
    if kind == 'two':
        calibration['formation']['candidates'].append([300,100,130,130])
    port = module.NativeClanBattle(NS(resource=Registry(), controller=NS(
        cached_image=np.zeros((720,1280,3),np.uint8))), tmp_path, calibration=calibration)
    port.plan, port.support_unit_id = plan, None
    port.page = module.ClanBattlePage(plan, calibration)
    port.characters = NS(match=lambda image, uid, roi: dict(unit_id=uid,
        box=[roi[0] - (1 if kind == 'half' else 0), roi[1], 130, 130]))
    monkeypatch.setattr(port, 'read_team', lambda **kwargs: dict(identity(plan), unit_ids=[None]*5,
        stars=[None]*5, member_count=0, support_unit_id=None))
    monkeypatch.setattr(port, '_reset_list', lambda *_: None)
    clicks = []
    monkeypatch.setattr(port, 'run', lambda name, **kwargs: clicks.append(name))
    with pytest.raises(RuntimeError, match='candidate_clipped' if kind == 'half' else 'candidate_ambiguous'):
        port.form_team()
    assert clicks == []


def test_registered_callbacks_owned_by_resource_survive_port_gc(calibration, plan, tmp_path, monkeypatch):
    from pcr_maa import clan_battle_characters
    class Callback:
        def __init__(self, *args): pass
    monkeypatch.setattr(clan_battle_characters, 'ClanBattleCharacters', Callback)
    monkeypatch.setattr(clan_battle, 'load_snapshot', lambda _: dict(ownership_complete=True, units=[dict(unit_id=uid) for uid in plan['unit_ids']]))
    owner, view = Registry(), Registry()
    tasker = NS(_resource_holder=owner, resource=view)
    port = module.NativeClanBattle(tasker, tmp_path, calibration=calibration)
    port.configure(plan)
    references = [weakref.ref(port.characters), weakref.ref(port.page)]
    del port
    gc.collect()
    assert all(reference() is not None for reference in references)
    assert not view.callbacks
    with pytest.raises(RuntimeError, match='callback_registration_failed'):
        module.NativeClanBattle(tasker, tmp_path, calibration=calibration).configure(plan)
    assert owner.callbacks['ClanBattleCharacters'] is references[0]()


def test_start_not_sent_marker_applies_only_to_reserved_start(tmp_path, monkeypatch):
    def reject(self, node, click=False):
        raise native.NativeInputNotSent(dict(node=node))
    monkeypatch.setattr(NativeGuideTrial, 'run', reject)
    port = module.NativeClanBattle(NS(), tmp_path)
    with pytest.raises(native.NativeInputNotSent) as start:
        port.run('CBStart', click=True)
    assert start.value.receipt['clan_battle_phase'] == 'simulation_start' and port.battle_start == 'not_sent'
    with pytest.raises(RuntimeError, match='already_attempted'):
        port.run('CBStart', click=True)
    with pytest.raises(native.NativeInputNotSent) as toggle:
        port.run('CBToggleSet', click=True)
    assert 'clan_battle_phase' not in toggle.value.receipt


def test_unknown_start_stays_unknown_and_is_never_repeated(tmp_path, monkeypatch):
    monkeypatch.setattr(NativeGuideTrial, 'run', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('timeout')))
    port = module.NativeClanBattle(NS(), tmp_path)
    with pytest.raises(RuntimeError, match='timeout'):
        port.run('CBStart', click=True)
    assert port.battle_start == 'unknown'
    with pytest.raises(RuntimeError, match='already_attempted'):
        port.run('CBStart', click=True)


@pytest.mark.parametrize('state', ['not_sent', 'sent', 'unknown'])
def test_6002_is_prioritized_and_preserves_start_state(tmp_path, monkeypatch, state):
    port = module.NativeClanBattle(NS(resource=Registry()), tmp_path)
    port.battle_start = state
    def run(node, **kwargs):
        assert port.tasker.resource.nodes['GTChoose']['next'][0] == 'LabyDisconnect6002Observed'
        return dict(nodes=[dict(name='LabyDisconnect6002Observed')])
    monkeypatch.setattr(port, 'run', run)
    with pytest.raises(RuntimeError, match='clan_battle_disconnected_6002:battle_start='+state):
        port.choose(['CBPage_result'])


def test_toggle_auto_off_one_click_and_full_readback(plan, tmp_path, monkeypatch):
    registry = Registry()
    port = module.NativeClanBattle(NS(resource=registry), tmp_path)
    initial = dict(auto=True, sets=[True]*5, unit_ids=plan['unit_ids'])
    monkeypatch.setattr(port, 'read_battle_settings', lambda: deepcopy(initial))
    clicks = []
    def run(node, *, click=False):
        clicks.append((node, click))
        expected = registry.nodes['CBSettingsConfirmed']['custom_recognition_param']
        return receipt({key: expected[key] for key in ('auto', 'sets', 'unit_ids')})
    monkeypatch.setattr(port, 'run', run)
    assert port.toggle_auto(True, False)['auto'] is False
    assert clicks == [('CBToggleAuto', True)]
    assert registry.nodes['CBToggleAuto']['custom_recognition_param']['auto'] is True


def test_toggle_unchanged_readback_does_not_retry(plan, tmp_path, monkeypatch):
    port = module.NativeClanBattle(NS(resource=Registry()), tmp_path)
    state = dict(auto=True, sets=[False]*5, unit_ids=plan['unit_ids'])
    monkeypatch.setattr(port, 'read_battle_settings', lambda: deepcopy(state))
    clicks = []
    monkeypatch.setattr(port, 'run', lambda node, **kwargs: clicks.append(node) or receipt(state))
    with pytest.raises(RuntimeError, match='readback_unknown'):
        port.toggle_set(2, False, True)
    assert clicks == ['CBToggleSet']


def test_toggle_reordered_hud_stops_before_click(plan, tmp_path, monkeypatch):
    port = module.NativeClanBattle(NS(resource=Registry()), tmp_path)
    port.last_controls = dict(auto=True, sets=[False]*5, unit_ids=plan['unit_ids'])
    changed = dict(auto=True, sets=[False]*5, unit_ids=list(reversed(plan['unit_ids'])))
    monkeypatch.setattr(port, 'read_battle_settings', lambda: changed)
    monkeypatch.setattr(port, 'run', lambda *a, **k: pytest.fail('changed order must not click'))
    with pytest.raises(RuntimeError, match='state_changed'):
        port.toggle_set(2, False, True)


@pytest.mark.parametrize('control', ['auto', 'set'])
@pytest.mark.parametrize('actual,same_frame', [(84,True), (85,False), (None,True)])
def test_timed_toggle_rechecks_target_second_before_dispatch(plan, tmp_path, monkeypatch, control, actual, same_frame):
    port = module.NativeClanBattle(NS(resource=Registry()), tmp_path)
    port.last_controls = dict(auto=True, sets=[False]*5, unit_ids=plan['unit_ids'],
        remaining_seconds=85, same_frame=True)
    fresh = dict(auto=True, sets=[False]*5, unit_ids=plan['unit_ids'],
        remaining_seconds=actual, same_frame=same_frame)
    monkeypatch.setattr(port, 'read_battle_settings', lambda: fresh)
    monkeypatch.setattr(port, 'run', lambda *_a, **_k: pytest.fail('late or unknown frame must not dispatch input'))
    with pytest.raises(RuntimeError, match='switch_window_missed'):
        if control == 'auto':
            port.toggle_auto(True, False, remaining_seconds=85)
        else:
            port.toggle_set(2, False, True, remaining_seconds=85)


@pytest.mark.parametrize('control', ['auto', 'set'])
def test_timed_toggle_passes_target_second_to_final_callback(plan, tmp_path, monkeypatch, control):
    registry = Registry()
    port = module.NativeClanBattle(NS(resource=registry), tmp_path)
    state = dict(auto=True, sets=[False]*5, unit_ids=plan['unit_ids'], remaining_seconds=85, same_frame=True)
    monkeypatch.setattr(port, 'read_battle_settings', lambda: state)
    calls = []
    def run(node, **kwargs):
        calls.append(node)
        assert registry.nodes[node]['custom_recognition_param']['expected_remaining_seconds'] == 85
        expected = registry.nodes['CBSettingsConfirmed']['custom_recognition_param']
        return receipt({key: expected[key] for key in ('auto','sets','unit_ids')})
    monkeypatch.setattr(port, 'run', run)
    if control == 'auto':
        port.toggle_auto(True, False, remaining_seconds=85)
    else:
        port.toggle_set(2, False, True, remaining_seconds=85)
    assert calls == ['CBToggleAuto' if control == 'auto' else 'CBToggleSet']


@pytest.mark.parametrize('speed', [None, True, 3])
def test_v2_timed_toggle_rejects_changed_speed_after_input_without_retry(calibration_v2, plan, tmp_path, monkeypatch, speed):
    port = module.NativeClanBattle(NS(resource=Registry()), tmp_path, calibration=calibration_v2)
    state = dict(auto=True, sets=[False]*5, unit_ids=plan['unit_ids'], remaining_seconds=85,
                 same_frame=True, battle_speed=1)
    monkeypatch.setattr(port, 'read_battle_settings', lambda: state)
    calls = []
    monkeypatch.setattr(port, 'run', lambda node, **kwargs: calls.append(node) or receipt(
        dict(state, auto=False, battle_speed=speed)))
    with pytest.raises(RuntimeError, match='speed_unknown'):
        port.toggle_auto(True, False, remaining_seconds=85)
    assert calls == ['CBToggleAuto']


@pytest.mark.parametrize('mode', ['battle_auto', 'battle_set'])
@pytest.mark.parametrize('actual,allowed', [(85,True),(84,False)])
def test_final_callback_requires_target_second_in_its_own_image(plan, mode, actual, allowed):
    value = object.__new__(ClanBattleCharacters)
    value.team = plan['unit_ids']
    value.page = NS(read=lambda *_: dict(remaining_seconds=actual))
    value.read_controls = lambda _: dict(auto=True, sets=[False]*5, unit_ids=plan['unit_ids'])
    params = dict(mode=mode, previous=mode=='battle_auto', desired=mode=='battle_set',
        slot=2, expected_remaining_seconds=85)
    result = value.analyze(NS(tasker=NS(stopping=False)), NS(image=np.zeros((720,1280,3),np.uint8),
        custom_recognition_param=json.dumps(params)))
    assert bool(result) is allowed


@pytest.mark.parametrize('failure', ['stop', '6002'])
def test_target_second_guard_preserves_stop_and_disconnect_safety(plan, failure):
    value = object.__new__(ClanBattleCharacters)
    value.team = plan['unit_ids']
    def read(*_):
        raise RuntimeError('clan_battle_disconnected_6002')
    value.page = NS(read=read)
    value.read_controls = lambda _: pytest.fail('stop or disconnect must not reach controls')
    result = value.analyze(NS(tasker=NS(stopping=failure=='stop')), NS(image=np.zeros((720,1280,3),np.uint8),
        custom_recognition_param=json.dumps(dict(mode='battle_auto', previous=True, desired=False,
            expected_remaining_seconds=85))))
    assert result is None


def test_unverified_timing_flag_cannot_enable_axis(calibration):
    calibration['timing_capability'] = dict(validated=True, max_observation_interval_ms=1, max_action_latency_ms=1)
    assert module.timing_capability(calibration) == dict(validated=False)


def test_timing_uses_hash_bound_measurement_maxima(calibration, tmp_path):
    path = tmp_path/'timing.json'
    value = dict(kind='owner_native_clan_timing', calibration_id=calibration['calibration_id'],
        battle_hud=calibration['battle_hud'], observation_scope='full_read_tick',
        action_scope='toggle_dispatch_and_full_team_readback',
        observation_intervals_ms=[200]*20, action_latencies_ms=[400]*20,
        battle_speed=1, game_second_ms=[990]*20)
    path.write_text(json.dumps(value), encoding='utf-8')
    calibration['evidence_kind'] = 'device_saved_frame'
    calibration['timing_measurements'] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    capability = module.timing_capability(calibration)
    assert capability['validated'] is True and capability['max_observation_interval_ms'] == 200
    assert capability['battle_speed'] == 1 and capability['min_game_second_ms'] == 990
    path.write_text('{}', encoding='utf-8')
    assert module.timing_capability(calibration)['validated'] is False


def test_timing_hash_and_parse_use_one_read_snapshot(calibration, tmp_path, monkeypatch):
    path = tmp_path/'timing-once.json'
    value = dict(kind='owner_native_clan_timing', calibration_id=calibration['calibration_id'],
        battle_hud=calibration['battle_hud'], observation_scope='full_read_tick',
        action_scope='toggle_dispatch_and_full_team_readback',
        observation_intervals_ms=[20]*20, action_latencies_ms=[40]*20,
        battle_speed=1, game_second_ms=[990]*20)
    data = json.dumps(value).encode('utf-8')
    path.write_bytes(data)
    calibration['evidence_kind'] = 'device_saved_frame'
    calibration['timing_measurements'] = dict(path=str(path), sha256=hashlib.sha256(data).hexdigest())
    reads = []
    original = Path.read_bytes
    def read_once(target):
        if target == path:
            reads.append(target)
            captured = original(target)
            target.write_bytes(b'{"replacement_after_hash_read":true}')
            return captured
        return original(target)
    monkeypatch.setattr(Path, 'read_bytes', read_once)
    monkeypatch.setattr(Path, 'read_text', lambda *_a, **_k: pytest.fail('parse must use bound bytes'))
    capability = module.timing_capability(calibration)
    assert len(reads) == 1 and capability['validated'] is True
    assert capability['max_observation_interval_ms'] == 20 and capability['max_action_latency_ms'] == 40


@pytest.mark.parametrize('change', [lambda row: row.pop('battle_speed'), lambda row: row.update(battle_speed=True),
    lambda row: row.update(battle_speed=3), lambda row: row.update(game_second_ms=[990]*19),
    lambda row: row.update(game_second_ms=[0]*20)])
def test_timing_requires_measured_one_speed_game_seconds(calibration, tmp_path, change):
    value = dict(kind='owner_native_clan_timing', calibration_id=calibration['calibration_id'],
        battle_hud=calibration['battle_hud'], observation_scope='full_read_tick',
        action_scope='toggle_dispatch_and_full_team_readback', battle_speed=1,
        observation_intervals_ms=[20]*20, action_latencies_ms=[40]*20, game_second_ms=[990]*20)
    change(value)
    path = tmp_path/'timing-invalid.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    calibration.update(evidence_kind='device_saved_frame', timing_measurements=dict(path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    assert module.timing_capability(calibration) == dict(validated=False)


@pytest.mark.parametrize('target_matches', [True, False])
def test_start_waits_for_first_hud_once_then_passes_receipt(plan, tmp_path, monkeypatch, target_matches):
    port = module.NativeClanBattle(NS(), tmp_path)
    calls = []
    ready = dict(team(plan), account_id=plan['account_id'], server=plan['server'], native_evidence=dict(ready=True))
    monkeypatch.setattr(port, 'verify_team', lambda _: ready)
    def run(node, **kwargs):
        calls.append(node)
        return dict(start=True)
    monkeypatch.setattr(port, 'run', run)
    hud = receipt(dict(auto=True, sets=[True]*5, unit_ids=plan['unit_ids']))
    reading = dict(identity(plan), damage=123)
    if not target_matches:
        reading['boss'] = plan['boss'] % 5 + 1
    result_receipt = result_page_receipt(reading)
    def wait(names, *, seconds):
        calls.append((names, seconds))
        port.last_receipt = hud if names[0] == 'CBReadSettings' else result_receipt
        return names[0]
    monkeypatch.setattr(port, 'wait', wait)
    monkeypatch.setattr(port, 'read_page', lambda *_: pytest.fail('must not recapture result'))
    def execute(_, __, initial=None):
        assert initial is hud
        return dict(settings_verified=True)
    monkeypatch.setattr(clan_battle, 'execute_switches', execute)
    if target_matches:
        result = port.start_and_read(plan)
        assert result['damage'] == 123 and result['result_verified'] is True
        assert result['result_evidence'] is result_receipt
        assert result['native_evidence']['result'] is result_receipt
    else:
        with pytest.raises(RuntimeError, match='clan_battle_target_mismatch_or_not_simulation'):
            port.start_and_read(plan)
    assert calls == ['CBStart', (['CBReadSettings', 'CBPage_result'], 30), (['CBPage_result'], 360)]


@pytest.mark.parametrize('failure', ['stopped', '6002', 'early_result'])
def test_loading_stop_disconnect_or_early_result_never_restarts(plan, tmp_path, monkeypatch, failure):
    port = module.NativeClanBattle(NS(), tmp_path)
    monkeypatch.setattr(port, 'verify_team', lambda _: dict(native_evidence={}))
    calls = []
    monkeypatch.setattr(port, 'run', lambda node, **kwargs: calls.append(node) or {})
    def wait(names, **kwargs):
        if failure == 'early_result': return 'CBPage_result'
        raise RuntimeError(failure)
    monkeypatch.setattr(port, 'wait', wait)
    monkeypatch.setattr(clan_battle, 'execute_switches', lambda *a, **k: pytest.fail('must not toggle'))
    with pytest.raises(RuntimeError, match='settings_not_observed' if failure == 'early_result' else failure):
        port.start_and_read(plan)
    assert calls == ['CBStart']


@pytest.mark.parametrize('matching_page', [1, None])
def test_form_team_borrows_exact_star_on_second_page_or_stops_at_bottom(plan, calibration, tmp_path, matching_page):
    plan['support_mode'] = 'qsjs'
    support = plan['unit_ids'][-1]
    class Replay(module.NativeClanBattle):
        def __init__(self):
            super().__init__(NS(resource=Registry()), tmp_path, calibration=calibration)
            self.plan, self.support_unit_id = plan, support
            self.snapshot = dict(ownership_complete=True, units=[dict(unit_id=uid) for uid in plan['unit_ids'][:-1]])
            self.page = module.ClanBattlePage(plan, calibration, support)
            self.chosen, self.page_index, self.inputs = [], 0, []
            self.characters = NS(match=lambda image, uid, roi: dict(unit_id=uid, box=[100, 100, 126, 126]))
            self.tasker.controller = NS(cached_image=np.zeros((720, 1280, 3), np.uint8))
        def _reset_list(self, section): pass
        def _next_list(self, section):
            assert section == 'support'
            self.page_index += 1
            return self.page_index < 2
        def read_team(self, *, require_stars=True):
            ids = self.chosen+[None]*(5-len(self.chosen))
            return dict(identity(plan), unit_ids=ids, stars=[5 if uid else None for uid in ids],
                member_count=len(self.chosen), support_unit_id=support if support in self.chosen else None)
        def run(self, node, *, click=False):
            self.inputs.append(node)
            if node == 'CBEditTeam':
                self.chosen.append(self.tasker.resource.nodes[node]['custom_recognition_param']['unit_id'])
            elif node == 'CBReadSupport':
                return receipt(dict(candidates=[dict(unit_id=support, stars=5 if self.page_index == matching_page else 4,
                    available=True, support_id='111' if self.page_index == 0 else '222')]))
            elif node == 'CBAddSupport':
                selected = self.tasker.resource.nodes[node]['custom_recognition_param']
                assert selected['support_id'] == '222'
                self.chosen.append(support)
            return {}
        def wait(self, names, **kwargs): return names[0]
    replay = Replay()
    if matching_page is None:
        with pytest.raises(RuntimeError, match='matching_support_unavailable'):
            replay.form_team()
        assert replay.page_index == 2 and 'CBAddSupport' not in replay.inputs
        return
    replay.form_team()
    assert replay.page_index == 1 and replay.chosen == plan['unit_ids']
    assert replay.inputs.count('CBAddSupport') == 1


def test_prepare_closed_after_account_check_never_opens_boss(plan, tmp_path, monkeypatch):
    port = module.NativeClanBattle(NS(), tmp_path, calibration={})
    monkeypatch.setattr(port, 'configure', lambda _: None)
    monkeypatch.setattr(module, 'profile_player_id', lambda _: int(plan['account_id']))
    states = iter(['LabyClientAdventureObserved', 'LabyClientMainMenuObserved', module.PROFILE_NODE,
                   'LabyClientMainMenuObserved', 'LabyClientAdventureObserved', 'CBPage_closed'])
    monkeypatch.setattr(port, 'wait', lambda *args, **kwargs: next(states))
    actions = []
    monkeypatch.setattr(port, 'run', lambda node, **kwargs: actions.append(node) or {})
    with pytest.raises(RuntimeError, match='^clan_battle_closed$'):
        port.prepare(plan)
    assert 'CBAction_open_clan' in actions and not any(node in actions for node in ('CBStart', 'CBAction_open_boss'))


@pytest.mark.filterwarnings('error::pytest.PytestUnraisableExceptionWarning')
def test_real_sdk_callback_dispatch_remains_alive_and_zero_input(calibration, plan, monkeypatch):
    from pcr_maa.replay import SavedImages
    monkeypatch.setattr(clan_battle, 'load_snapshot', lambda _: dict(ownership_complete=True,
        units=[dict(unit_id=uid) for uid in plan['unit_ids']]))
    resource = native.load_resource()
    controller = SavedImages([[native.read_image(calibration['samples'][0]['path'])]])
    tasker = native.bind(resource, controller)
    tasker._resource_holder = resource
    directory = native.ROOT/'.runtime/clan-agent-20261020/execution/sdk-dispatch'
    port = module.NativeClanBattle(tasker, directory, calibration=calibration)
    port.configure(plan)
    callbacks = []
    original_page, original_characters = module.ClanBattlePage.analyze, ClanBattleCharacters.analyze
    monkeypatch.setattr(module.ClanBattlePage, 'analyze', lambda self, *args: callbacks.append('page') or original_page(self, *args))
    monkeypatch.setattr(ClanBattleCharacters, 'analyze', lambda self, *args: callbacks.append('characters') or original_characters(self, *args))
    references = [weakref.ref(port.characters), weakref.ref(port.page)]
    del port
    gc.collect()
    assert all(reference() is not None for reference in references)
    observed = native.run(tasker, 'CBReadPage')
    assert observed['native_ok'] is False and controller.clicks == []
    members = native.run(tasker, 'CBReadTeam')
    assert members['native_ok'] is False and controller.clicks == []
    assert 'page' in callbacks and 'characters' in callbacks


@pytest.mark.filterwarnings('error::pytest.PytestUnraisableExceptionWarning')
def test_real_sdk_saved_image_returns_same_frame_tick_without_input(calibration, plan, monkeypatch):
    from pcr_maa.replay import SavedImages
    monkeypatch.setattr(clan_battle, 'load_snapshot', lambda _: dict(ownership_complete=True,
        units=[dict(unit_id=uid) for uid in plan['unit_ids']]))
    image = np.full((720,1280,3), 247, np.uint8)
    frames = []
    def page_read(self, context, frame, page):
        if page != 'battle':
            raise RuntimeError('synthetic replay only has battle observation')
        frames.append(frame)
        return dict(remaining_seconds=85)
    def controls(self, frame):
        frames.append(frame)
        return dict(auto=True, sets=[False]*5, unit_ids=plan['unit_ids'])
    monkeypatch.setattr(module.ClanBattlePage, 'read', page_read)
    monkeypatch.setattr(ClanBattleCharacters, 'read_controls', controls)
    resource = native.load_resource()
    controller = SavedImages([[image]])
    tasker = native.bind(resource, controller)
    tasker._resource_holder = resource
    directory = native.ROOT/'.runtime/clan-agent-20261020/execution/same-frame-sdk-dispatch'
    port = module.NativeClanBattle(tasker, directory, calibration=calibration)
    port.configure(plan)
    tick = port.read_tick()
    assert tick['remaining_seconds'] == 85 and tick['state']['sets'] == [False]*5
    assert tick['same_frame'] is True and tick['frame_pixel_sha256'] == hashlib.sha256(image.tobytes()).hexdigest()
    assert len(frames) == 2 and frames[0] is frames[1]
    assert controller.clicks == []


@pytest.mark.filterwarnings('error::pytest.PytestUnraisableExceptionWarning')
def test_real_sdk_final_callback_84_after_preread_85_has_zero_clicks(calibration, plan, monkeypatch):
    from pcr_maa.replay import SavedImages
    monkeypatch.setattr(clan_battle, 'load_snapshot', lambda _: dict(ownership_complete=True,
        units=[dict(unit_id=uid) for uid in plan['unit_ids']]))
    reads = []
    def page_read(self, context, frame, page):
        if page != 'battle':
            raise RuntimeError('synthetic saved-image battle only')
        reads.append(page)
        return dict(remaining_seconds=85 if len(reads) == 1 else 84)
    monkeypatch.setattr(module.ClanBattlePage, 'read', page_read)
    monkeypatch.setattr(ClanBattleCharacters, 'read_controls', lambda self, frame:
        dict(auto=True, sets=[False]*5, unit_ids=plan['unit_ids']))
    resource = native.load_resource()
    controller = SavedImages([[np.full((720,1280,3),247,np.uint8)]])
    tasker = native.bind(resource, controller)
    tasker._resource_holder = resource
    directory = native.ROOT/'.runtime/clan-agent-20261020/reuse-followup/native/sdk-target-second'
    port = module.NativeClanBattle(tasker, directory, calibration=calibration)
    port.configure(plan)
    with pytest.raises(native.NativeInputNotSent):
        port.toggle_auto(True, False, remaining_seconds=85)
    assert len(reads) >= 2 and controller.clicks == []


@pytest.mark.parametrize('box,score,needs_scroll', [
    ([224,369,126,126], .9514822363853455, True),  # GUI25 frame 152: exactly at bottom 495.
    ([225,370,126,126], .9315864443778992, True),  # GUI25 frame 153: one pixel beyond.
    ([224,368,126,126], .9514822363853455, False),
])
def test_v2_gui25_bottom_boundary_scrolls_before_the_first_add(formation_replay,plan,box,score,needs_scroll):
    port = formation_replay
    plan['unit_ids'][:2] = [118101,132001]
    port.selected = [118101]
    port.calibration['formation']['candidates'] = [[80,153,1120,342]]
    state = {'scrolled':False,'next_calls':0}
    run = port.run
    def match(frame,uid,roi):
        return dict(unit_id=uid,score=score,box=box if uid==132001 and not state['scrolled'] else [224,220,126,126])
    def next_page(section):
        assert section=='formation' and port.inputs==[]
        state.update(scrolled=True,next_calls=state['next_calls']+1)
        return True
    def guarded_run(node,**kwargs):
        if node=='CBEditTeam' and needs_scroll:
            assert state['scrolled'], 'edge candidate must not dispatch an add before scrolling'
        return run(node,**kwargs)
    port.characters.match,port._next_list,port.run = match,next_page,guarded_run
    port.form_team()
    assert state['next_calls']==int(needs_scroll)
    assert port.selected==plan['unit_ids'] and port.inputs==['CBEditTeam']*4


@pytest.mark.parametrize('next_result,error',[(False,'character_not_found'),
    ('clan_battle_list_unchanged_no_retry','list_unchanged_no_retry')])
def test_v2_gui25_touching_bottom_never_adds_after_failed_scroll(formation_replay,plan,next_result,error):
    port = formation_replay
    plan['unit_ids'][:2] = [118101,132001]
    port.selected = [118101]
    port.calibration['formation']['candidates'] = [[80,153,1120,342]]
    port.characters.match = lambda frame,uid,roi: dict(unit_id=uid,score=.9514822363853455,box=[224,369,126,126])
    def next_page(section):
        assert section=='formation' and port.inputs==[]
        if isinstance(next_result,str): raise RuntimeError(next_result)
        return next_result
    port._next_list = next_page
    with pytest.raises(RuntimeError,match=error): port.form_team()
    assert port.selected==[118101] and port.inputs==[]


@pytest.fixture
def paused_control_reader(tmp_path,monkeypatch):
    port = module.NativeClanBattle(NS(resource=Registry()),tmp_path)
    port.opening_paused = True
    clock,calls = [0.0],[]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(module.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    port.run = lambda *args,**kwargs: pytest.fail('read-only wait must not dispatch input')
    state = dict(auto=True,sets=[False]*5,unit_ids=[118101,132001,180601,106701,133101],
        paused=True,same_frame=True,battle_speed=2,remaining_seconds=88,opening_start_token='synthetic-intent',
        first_ub_evidence={'source':'synthetic-unchanged-proof'},first_ub_observation=None)
    def choose(names,**kwargs):
        calls.append(list(names))
        port.last_receipt = receipt(deepcopy(state))
        return names[0] if len(calls)>1 else 'GTUnknown'
    port.choose = choose
    return port,state,clock,calls


def test_paused_controls_wait_for_a_qualified_read_without_input(paused_control_reader):
    port,state,clock,calls = paused_control_reader
    assert port.read_battle_settings()==state
    assert calls==[['CBOpeningRead'],['CBOpeningRead']] and clock[0]==.3
    assert port.last_controls==state


def test_paused_controls_unknown_remains_bounded_and_read_only(paused_control_reader):
    port,_,clock,calls = paused_control_reader
    port.choose = lambda names,**kwargs: calls.append(list(names)) or 'GTUnknown'
    with pytest.raises(RuntimeError,match='guide_trial_page_timeout'):
        port.read_battle_settings()
    assert 5<=clock[0]<5.5 and calls and all(names==['CBOpeningRead'] for names in calls)
    assert not hasattr(port,'last_controls')


def test_paused_controls_stop_does_not_retry(paused_control_reader):
    port,_,_,calls = paused_control_reader
    def stopped(names,**kwargs):
        calls.append(list(names))
        raise RuntimeError('synthetic_owner_stopped')
    port.choose = stopped
    with pytest.raises(RuntimeError,match='synthetic_owner_stopped'):port.read_battle_settings()
    assert calls==[['CBOpeningRead']]


def test_running_controls_keep_single_read_for_timed_switches(paused_control_reader):
    port,_,clock,calls = paused_control_reader
    port.opening_paused = False
    with pytest.raises(RuntimeError,match='clan_battle_controls_unknown'):port.read_battle_settings()
    assert calls==[['CBReadSettings']] and clock[0]==0


def test_paused_control_wait_still_rejects_changed_frozen_clock_before_toggle(paused_control_reader):
    port,state,_,calls = paused_control_reader
    port.last_controls = dict(deepcopy(state),remaining_seconds=89)
    with pytest.raises(RuntimeError,match='clan_battle_opening_not_verified'):
        port.toggle_set(0,False,True)
    assert calls==[['CBOpeningRead'],['CBOpeningRead']]


def test_supplied_control_receipt_does_not_poll_again(paused_control_reader):
    port,state,_,calls = paused_control_reader
    assert port.read_battle_settings(receipt(state))==state and calls==[]
