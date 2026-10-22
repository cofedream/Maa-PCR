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


def test_same_frame_page_read_binds_target_and_formal_marker(calibration, plan):
    nodes = module.calibration_pipeline(calibration, plan)
    page = module.ClanBattlePage(plan, calibration)
    guards = {'CBLabel_target_0', 'CBLabel_target_1'}
    context = context_for(nodes, field_texts('target', plan), guards)
    image = np.zeros((720, 1280, 3), np.uint8)
    assert page.read(context, image, 'target') == dict(identity(plan), page='target')
    guards.add('CBFormal_target')
    with pytest.raises(RuntimeError, match='formal_battle_forbidden'):
        page.read(context, image, 'target')


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
    monkeypatch.setattr(port, 'read_team', lambda: dict(identity(plan), unit_ids=[None]*5,
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
        observation_intervals_ms=[200]*20, action_latencies_ms=[400]*20)
    path.write_text(json.dumps(value), encoding='utf-8')
    calibration['evidence_kind'] = 'device_saved_frame'
    calibration['timing_measurements'] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    capability = module.timing_capability(calibration)
    assert capability['validated'] is True and capability['max_observation_interval_ms'] == 200
    path.write_text('{}', encoding='utf-8')
    assert module.timing_capability(calibration)['validated'] is False


def test_timing_hash_and_parse_use_one_read_snapshot(calibration, tmp_path, monkeypatch):
    path = tmp_path/'timing-once.json'
    value = dict(kind='owner_native_clan_timing', calibration_id=calibration['calibration_id'],
        battle_hud=calibration['battle_hud'], observation_scope='full_read_tick',
        action_scope='toggle_dispatch_and_full_team_readback',
        observation_intervals_ms=[20]*20, action_latencies_ms=[40]*20)
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


def test_start_waits_for_first_hud_once_then_passes_receipt(plan, tmp_path, monkeypatch):
    port = module.NativeClanBattle(NS(), tmp_path)
    calls = []
    ready = dict(team(plan), account_id=plan['account_id'], server=plan['server'], native_evidence=dict(ready=True))
    monkeypatch.setattr(port, 'verify_team', lambda _: ready)
    def run(node, **kwargs):
        calls.append(node)
        return dict(start=True)
    monkeypatch.setattr(port, 'run', run)
    hud = receipt(dict(auto=True, sets=[True]*5, unit_ids=plan['unit_ids']))
    def wait(names, *, seconds):
        calls.append((names, seconds))
        port.last_receipt = hud
        return names[0]
    monkeypatch.setattr(port, 'wait', wait)
    monkeypatch.setattr(port, 'read_page', lambda page: (dict(identity(plan), damage=123), dict(result=True)))
    def execute(_, __, initial=None):
        assert initial is hud
        return dict(settings_verified=True)
    monkeypatch.setattr(clan_battle, 'execute_switches', execute)
    result = port.start_and_read(plan)
    assert calls == ['CBStart', (['CBReadSettings', 'CBPage_result'], 30), (['CBPage_result'], 180)]
    assert result['damage'] == 123 and result['result_verified'] is True


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
        def read_team(self):
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
