"""Equipment layer guards and complete, bounded team-page successor flow."""
from types import SimpleNamespace as NS
import json

import cv2
import numpy as np
import pytest

from pcr_maa import native
from pcr_maa.labyrinth import special_equipment as equipment
from pcr_maa.labyrinth.batch import BatchStopped


def frame(kind='main', *, enabled=True, title=True, size=(1920, 1080)):
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    names = ['dialog_anchor', 'dialog_title', 'auto_button',
        'equip_confirm_enabled' if enabled else 'equip_confirm_disabled'] if kind in ('main', 'auto') else ['battle_team_button']
    for name in names:
        x, y, w, h = equipment.RECTS[name]
        image[y:y+h, x:x+w] = equipment._templates(1920, 1080)[name]
    if not title:
        x, y, w, h = equipment.RECTS['dialog_title']
        image[y:y+h, x:x+w] = 0
    if kind == 'auto':
        image //= 2
        for name in ('auto_dialog_anchor', 'auto_dialog_confirm_button'):
            x, y, w, h = equipment.RECTS[name]
            image[y:y+h, x:x+w] = equipment._templates(1920, 1080)[name]
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def callback(image, tap=None, *, selected=('1001',), expected=('1001',), boss_tab=None,
        actual_tab=None, trusted=True, cancel=(857, 567, 180, 73)):
    def reco(node, actual_image):
        assert actual_image is image
        if node == equipment.TEAM_GUARD:
            detail = dict(selected=list(selected), members=[dict(trusted=trusted) for _ in selected],
                stable=True, boss_tab=actual_tab)
            return NS(hit=True, raw_detail=dict(best=dict(detail=detail)))
        if node == 'LabySpecialEquipmentTeamCancel':
            return NS(hit=True, box=cancel)
        raise AssertionError(node)
    params = dict(mode='read' if tap is None else 'tap', tap=tap, expected_selected=list(expected),
        page='boss' if boss_tab is not None else 'battle', boss_tab=boss_tab)
    return equipment.EquipmentRecognition().analyze(NS(tasker=NS(stopping=False), run_recognition=reco),
        NS(image=image, custom_recognition_param=json.dumps(params)))


@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.parametrize('size', [(1920, 1080), (1280, 720)])
def test_actual_upstream_button_templates_resolve_by_pixels(enabled, size):
    result = equipment.recognize(frame(enabled=enabled, size=size))
    assert result['stage'] == 'main' and result['enabled'] is enabled
    assert result['confidence'] >= (.97 if size == (1920, 1080) else .90)


def test_shared_series_shell_without_equipment_title_cannot_be_clicked():
    image = frame(title=False, size=(1280, 720))
    assert equipment.recognize(image)['stage'] is None
    for tap in ('auto', 'auto_confirm', 'equip_confirm', 'cancel'):
        assert callback(image, tap) is None


def test_upper_auto_modal_exclusively_owns_frame_and_all_lower_targets_are_rejected():
    image = frame('auto', size=(1280, 720))
    assert equipment.recognize(image)['stage'] == 'auto'
    assert callback(image, 'auto_confirm').box is not None
    for tap in ('auto', 'entry', 'equip_confirm', 'cancel'):
        assert callback(image, tap) is None


def test_fresh_native_guard_rechecks_confirm_enabled_instead_of_trusting_old_observation():
    assert callback(frame(enabled=True, size=(1280, 720)), 'equip_confirm').box is not None
    assert callback(frame(enabled=False, size=(1280, 720)), 'equip_confirm') is None
    assert callback(frame(enabled=False, size=(1280, 720)), 'cancel').box is not None


@pytest.mark.parametrize('change', [dict(selected=('1002',)), dict(trusted=False),
    dict(cancel=(700, 567, 80, 73)), dict(cancel=(857, 650, 180, 60)),
    dict(boss_tab=2, actual_tab=1)])
def test_entry_requires_same_frame_owned_members_tab_and_cancel_geometry(change):
    image = frame('team', size=(1280, 720))
    assert callback(image, 'entry', **change) is None


def test_verified_entry_is_left_of_cancel_and_derived_from_template():
    image = frame('team', size=(1280, 720))
    assert callback(image, 'entry').box == equipment.scaled(equipment.RECTS['battle_team_button'], image)


@pytest.mark.parametrize('image', [np.zeros((1280, 720, 3), np.uint8),
    np.zeros((640, 1138, 3), np.uint8), np.zeros((720, 1280), np.uint8)])
def test_uncalibrated_frame_geometry_cannot_offer_entry(image):
    assert equipment.recognize(image)['entry'] is None


class Clock:
    def __init__(self):
        self.now = 100.
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class Runtime(equipment.EquipmentRuntime):
    def __init__(self, clock, *, enabled=True, entry=True, freeze=None, delay=0., evidence=()):
        self.clock, self.enabled, self.entry, self.freeze, self.delay = clock, enabled, entry, freeze, delay
        self.evidence = list(evidence)
        self.stage = 'team'
        self.actions, self.rounds = [], {}
        self.last_normal_team = None
        self.last_action = -float('inf')
        self.resource = NS(override_pipeline=lambda value: True)
        self.confirm_times = []
    def _run(self, params, *, tap=False):
        self.clock.now += .3 + self.delay
        if not tap:
            enabled = self.evidence.pop(0) if self.stage == 'second_main' and self.evidence else self.enabled
            stage = 'main' if self.stage in ('main', 'second_main') else ('auto' if self.stage == 'auto' else None)
            return dict(stage=stage, enabled=enabled, team_verified=stage is None, team_stable=stage is None,
                entry=[790, 572, 65, 65] if self.entry and stage is None else None), {}
        action = params['tap']
        self.actions.append(action)
        if action == 'equip_confirm':
            self.confirm_times.append(self.clock.now)
        if action != self.freeze:
            self.stage = dict(entry='main', auto='auto', auto_confirm='second_main',
                equip_confirm='team', cancel='team')[action]
        return None, dict(action=action)


def setup_flow(monkeypatch, *, kind='NORMAL', selected=('NORMAL', 'EX', 'BOSS'), mode='auto_equip', **changes):
    clock = Clock()
    monkeypatch.setattr(equipment.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(equipment.time, 'sleep', clock.sleep)
    runtime = Runtime(clock, **changes)
    transactions, logs = [], []
    def transaction(operation, fields, send):
        result = send('owned-id')
        assert result['input_action_verified'] is True
        transactions.append((operation, fields))
        return result
    batch = NS(settings=NS(strategy=NS(special_equipment_mode=mode, special_equipment_battles=selected)),
        checkpoint=lambda: None, emit_log=logs.append, transaction=transaction)
    def run(block=10101, tab=None):
        return runtime.equip(batch, lambda **value: value, 7, block, kind, ['1001'],
            page='boss' if kind == 'BOSS' else 'battle', roster=['1001'], boss_tab=tab)
    return runtime, batch, run, clock, logs, transactions


@pytest.mark.parametrize('enabled,last', [(True, 'equip_confirm'), (False, 'cancel'), (None, 'cancel')])
def test_full_two_dialog_flow_resumes_after_enabled_or_unchanged_result(monkeypatch, enabled, last):
    runtime, _, run, _, logs, transactions = setup_flow(monkeypatch, enabled=enabled)
    assert run() is True
    assert runtime.actions == ['entry', 'auto', 'auto_confirm', last]
    assert len(transactions) == 4 and runtime.stage == 'team'
    assert ('已穿戴' if enabled else '本次未变更') in logs[-1]


def test_transient_dim_evidence_is_not_used_to_cancel_before_stability_and_one_second(monkeypatch):
    runtime, _, run, clock, _, _ = setup_flow(monkeypatch, evidence=[False, False, True, True, True])
    assert run() is True
    assert runtime.actions[-1] == 'equip_confirm' and 'cancel' not in runtime.actions
    assert runtime.confirm_times[0] > 103.


@pytest.mark.parametrize('change', [dict(mode='off'), dict(selected=()), dict(selected=('BOSS',)), dict(kind=None)])
def test_default_off_empty_and_unselected_kind_add_zero_native_calls(monkeypatch, change):
    runtime, _, run, clock, _, _ = setup_flow(monkeypatch, **change)
    assert run() is False
    assert clock.now == 100 and not runtime.actions


def test_untrusted_entry_skips_without_click_and_logs_reason(monkeypatch):
    runtime, _, run, _, logs, _ = setup_flow(monkeypatch, entry=False)
    assert run() is False and not runtime.actions
    assert '可信入口' in logs[-1]


def test_normal_unchanged_signature_skips_but_ex_and_each_nonempty_boss_tab_run(monkeypatch):
    runtime, _, run, _, _, _ = setup_flow(monkeypatch)
    runtime.last_normal_team = ('1001',)
    assert run() is False and not runtime.actions
    for kind in ('EX', 'BOSS'):
        runtime, _, run, _, _, _ = setup_flow(monkeypatch, kind=kind)
        runtime.last_normal_team = ('1001',)
        assert run(tab=1 if kind == 'BOSS' else None) is True
        if kind == 'BOSS':
            assert run(tab=2) is True
            assert runtime.actions.count('entry') == 2


def test_successful_round_survives_replanning_without_repeating_equipment(monkeypatch):
    runtime, _, run, _, _, _ = setup_flow(monkeypatch, kind='BOSS')
    assert run(tab=1) is True
    assert run(tab=1) is False
    assert runtime.actions.count('entry') == 1


@pytest.mark.parametrize('frozen', ['entry', 'auto', 'auto_confirm'])
def test_ui_retry_is_bounded_and_does_not_repeat_final_equipment_confirmation(monkeypatch, frozen):
    runtime, _, run, _, _, _ = setup_flow(monkeypatch, freeze=frozen)
    if frozen == 'auto_confirm':
        with pytest.raises(BatchStopped, match='dialog_unresolved'):
            run()
    else:
        assert run() is True
    assert runtime.actions.count(frozen) == 3
    assert 'equip_confirm' not in runtime.actions


def test_failed_or_unknown_round_is_never_rearmed_by_same_team_request(monkeypatch):
    runtime, _, run, _, _, _ = setup_flow(monkeypatch)
    original = runtime._run
    def fail(params, **kwargs):
        if kwargs.get('tap'):
            raise RuntimeError('unknown input')
        return original(params, **kwargs)
    runtime._run = fail
    with pytest.raises(RuntimeError, match='unknown input'):
        run()
    runtime._run = original
    with pytest.raises(BatchStopped, match='already_attempted'):
        run()
    assert not runtime.actions


def test_confirm_is_not_resent_when_acknowledged_click_has_no_visual_result(monkeypatch):
    runtime, _, run, _, _, _ = setup_flow(monkeypatch, freeze='equip_confirm')
    assert run() is True
    assert runtime.actions.count('equip_confirm') == 1
    assert runtime.actions == ['entry', 'auto', 'auto_confirm', 'equip_confirm', 'cancel']


def test_open_dialog_wins_race_with_guarded_entry_retry(monkeypatch):
    runtime, batch, run, _, _, _ = setup_flow(monkeypatch, freeze='entry')
    transaction = batch.transaction
    def raced(operation, fields, send):
        if operation == 'special_equipment_entry' and runtime.actions == ['entry']:
            runtime.stage = 'main'
            try:
                raise equipment.native.NativeInputNotSent(dict(input_execution='not_sent'))
            except equipment.native.NativeInputNotSent as error:
                raise BatchStopped('native_input_not_sent') from error
        return transaction(operation, fields, send)
    batch.transaction = raced
    assert run() and runtime.actions == ['entry', 'auto', 'auto_confirm', 'equip_confirm']


def test_timeout_in_upper_modal_never_clicks_lower_cancel(monkeypatch):
    runtime, _, run, _, _, _ = setup_flow(monkeypatch, freeze='auto_confirm')
    with pytest.raises(BatchStopped, match='dialog_unresolved'):
        run()
    assert runtime.actions == ['entry', 'auto', 'auto_confirm', 'auto_confirm', 'auto_confirm']


def test_shorter_parent_deadline_rejects_late_read_before_any_input(monkeypatch):
    runtime, _, run, _, _, _ = setup_flow(monkeypatch, delay=1)
    token = native.RUN_DEADLINE.set(100.5)
    try:
        with pytest.raises(BatchStopped, match='special_equipment_timeout'):
            run()
    finally:
        native.RUN_DEADLINE.reset(token)
    assert not runtime.actions


def test_stop_during_stability_wait_never_confirms_or_cancels(monkeypatch):
    runtime, batch, run, clock, _, _ = setup_flow(monkeypatch)
    def checkpoint():
        if runtime.stage == 'second_main' and clock.now >= runtime.last_action+.3:
            raise BatchStopped('stopped')
    batch.checkpoint = checkpoint
    with pytest.raises(BatchStopped, match='stopped'):
        run()
    assert runtime.actions == ['entry', 'auto', 'auto_confirm']


def test_owner_registration_retains_callback_and_rejects_failed_registration(monkeypatch):
    resource = NS(override_pipeline=lambda value: True, register_custom_recognition=lambda name, callback: False)
    tasker = NS(_resource_holder=resource)
    with pytest.raises(BatchStopped, match='install_failed'):
        equipment.equipment_runtime(tasker)
    assert not hasattr(resource, '_labyrinth_special_equipment')
    registered = []
    resource.register_custom_recognition = lambda name, callback: registered.append(callback) or True
    owner = equipment.equipment_runtime(tasker)
    assert owner is equipment.equipment_runtime(tasker)
    assert owner.observer is registered[0] and owner.resource is resource


def test_legacy_team_adapter_reuses_character_callback_owner_across_battles(monkeypatch):
    from pcr_maa.labyrinth.team_execution import MaaTeam
    from pcr_maa.labyrinth import character_recognition
    calls = []
    monkeypatch.setattr(character_recognition, 'MaaCharacters', lambda *a, **k: calls.append(1) or NS(callback=object()))
    resource = NS()
    first = MaaTeam(NS(_resource_holder=resource), lambda **value: value)
    second = MaaTeam(NS(_resource_holder=resource), lambda **value: value)
    assert first.characters is second.characters
    assert calls == [1] and first.characters is resource._labyrinth_team_characters


def test_team_preparation_calls_equipment_only_after_verified_team_ready(monkeypatch):
    from pcr_maa.labyrinth.team_execution import MaaTeam
    from pcr_maa.labyrinth.roles import Context
    events = []
    desired = ['1001']
    team = MaaTeam.__new__(MaaTeam)
    team.tasker = NS(resource=NS(get_node_data=lambda node: None))
    team._characters = NS(read=lambda *a, **k: (dict(selected=desired, members=[dict(trusted=True)]), {}))
    team._identity = lambda *args: (7, 10101)
    team.optimizer = NS(best=lambda *a, **k: NS(members=desired), profiles={'1001':NS(position=100)})
    team.retry_number, team.roster = 0, tuple(desired)
    team.timings = NS(measure=lambda category: __import__('contextlib').nullcontext())
    team.prepared, team.pages = {}, {}
    team.stamp = lambda **value: value
    def edit(*args):
        events.append('team_ready')
        return dict(selected=desired, members=[dict(trusted=True)]), {}
    team._edit = edit
    team._equipment = lambda *a, **k: events.append('equipment') or False
    result = team.prepare(NS(), 7, 10101, Context())
    assert result['team'] == desired and events == ['team_ready', 'equipment']


@pytest.mark.parametrize('multi,expected_tabs', [(False, [1]), (True, [1, 2, 3])])
def test_actual_boss_preparation_equips_each_nonempty_ready_tab(monkeypatch, multi, expected_tabs):
    from pcr_maa.labyrinth.team_execution import MaaTeam
    from pcr_maa.labyrinth.roles import Context
    from pcr_maa.labyrinth import character_recognition as cr
    from test_labyrinth_upstream_combat import profile, optimizer, FakeBatch
    profiles = [profile('t'+str(i), 100+i, tank=True) for i in range(3)] + [profile('d'+str(i), 300+i) for i in range(12)]
    opt = optimizer(profiles)
    state = dict(boss_tab=1, selected=[], members=[])
    class Nav:
        def __init__(self, *args, **kwargs):
            pass
        def move(self, *args, tab, **kwargs):
            state['boss_tab'] = tab
            return dict(state)
    monkeypatch.setattr(cr, 'CharacterNavigation', Nav)
    calls = []
    def equip(*args, **kwargs):
        assert list(args[5]) == state['selected'] and args[5]
        assert all(member['trusted'] for member in state['members'])
        calls.append(kwargs['boss_tab'])
        return True
    monkeypatch.setattr(equipment, 'equipment_runtime', lambda tasker: NS(equip=equip))
    chars = NS(read=lambda *a, **k: (dict(state), {}))
    team = MaaTeam(NS(), lambda **value: value, tuple(opt.profiles), optimizer=opt, characters=chars, combat_kind='BOSS')
    def edit(batch, enter, block, page, desired, observation):
        state.update(selected=desired, members=[dict(trusted=True) for _ in desired])
        return dict(state), {}
    team._edit = edit
    batch = FakeBatch()
    batch.settings = NS(strategy=NS(boss_mode='single', special_equipment_mode='auto_equip', special_equipment_battles=('BOSS',)))
    result = team.prepare_boss(batch, 7, 30701, Context(defense=4, boss=True), force_multi=multi)
    assert calls == expected_tabs and result['selected_teams_verified'] is True


def test_normal_signature_is_recorded_at_authenticated_battle_start_not_equipment_finish(monkeypatch):
    from pcr_maa.labyrinth.team_execution import MaaTeam
    from test_labyrinth_autoplay import receipt
    team = MaaTeam.__new__(MaaTeam)
    team.combat_kind = 'NORMAL'
    team.pages, team.prepared, team.started = {(7, 10101): 'battle'}, {(7, 10101): ['1001']}, set()
    team.roster, team.tasker = ('1001',), NS()
    team._characters = NS(read=lambda *a, **k: (dict(selected=['1001']), receipt()),
        run=lambda *a, **k: ({}, receipt(action=True)))
    team.stamp = lambda **value: value
    retained = NS(last_normal_team=None)
    monkeypatch.setattr(equipment, 'equipment_runtime', lambda tasker: retained)
    batch = NS(settings=NS(strategy=NS(special_equipment_mode='auto_equip', special_equipment_battles=('NORMAL',))),
        checkpoint=lambda: None, transaction=lambda op, fields, send: send('owned'), observation=lambda value, **kwargs: value)
    def result(enter, block):
        assert retained.last_normal_team == ('1001',)
        return dict(verified=True, outcome='won', enter_id=enter, block_id=block)
    assert retained.last_normal_team is None
    team.start_once(batch, 7, 10101, result)
