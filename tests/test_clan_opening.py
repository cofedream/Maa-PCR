"""Opening-only control changes use a frozen, verified clan simulation menu."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from pcr_maa import clan_battle, clan_battle_native as backend, native
from pcr_maa.clan_battle_calibration import load_calibration
from pcr_maa.clan_battle_characters import ClanBattleCharacters
from test_clan_battle import Controls, plan


def paused_port(plan):
    port = Controls(plan)
    port.calibration = {'version': 2, 'auto_battle': {'speeds': [1, 2, 4]}}
    port.opening_paused = True
    port.state.update(paused=True, remaining_seconds=90, same_frame=True)
    # Synthetic trusted history exercises the consumer; it is not a single-frame UB claim.
    port.state.update(opening_start_token='synthetic-new-start', first_ub_evidence=dict(
        source='controls_disabled_since_start', start_token='synthetic-new-start',
        initial_frame_pixel_sha256='a'*64, not_fired_unit_ids=list(plan['unit_ids']),
        control_policy=dict(auto_release_on_ub=False, source='owner_account_bound_settings_readback',
            same_frame=True, frame_pixel_sha256='b'*64)))
    return port


@pytest.mark.parametrize('auto', [False, True])
def test_paused_opening_reuses_identity_mapping_and_readback(plan, auto):
    plan['auto'] = auto
    plan['set_by_unit'][str(plan['unit_ids'][0])] = False
    port = paused_port(plan)
    port.state['auto'] = not auto
    evidence = clan_battle.execute_switches(port, plan)
    assert evidence['opening_evidence']['paused'] is True
    assert evidence['opening_evidence']['remaining_seconds'] == 90
    assert evidence['set_by_unit'] == plan['set_by_unit']
    assert ('set', plan['unit_ids'][0], True, False) in port.actions
    assert port.actions[-1][0] == ('auto' if auto else 'set')


@pytest.mark.parametrize('patch', [dict(paused=False), dict(remaining_seconds=0), dict(remaining_seconds=True),
    dict(same_frame=False), dict(battle_speed=None), dict(battle_speed=True),
    dict(remaining_seconds=91), dict(remaining_seconds=-1)])
def test_pause_flag_cannot_replace_opening_evidence(plan, patch):
    port = paused_port(plan)
    port.state.update(patch)
    with pytest.raises(RuntimeError, match='opening_not_verified'):
        clan_battle.execute_switches(port, plan)
    assert port.actions == []


def test_paused_opening_after_two_seconds_uses_first_ub_history(plan):
    port = paused_port(plan)
    port.state.update(remaining_seconds=84, zero_damage_verified=False, zero_tp_verified=False)
    plan['set_by_unit'][str(plan['unit_ids'][0])] = False
    evidence = clan_battle.execute_switches(port, plan)
    assert evidence['opening_evidence']['remaining_seconds'] == 84
    assert len(port.actions) == 1


@pytest.mark.parametrize('patch', [dict(paused=False), dict(remaining_seconds=89), dict(battle_speed=2), dict(same_frame=False)])
def test_unknown_after_first_set_stops_without_more_inputs(plan, patch):
    plan['set_by_unit'] = {str(uid): False for uid in plan['unit_ids']}
    port = paused_port(plan)
    toggle = port.toggle_set
    def changed(*args, **kwargs):
        return dict(toggle(*args, **kwargs), **patch)
    port.toggle_set = changed
    with pytest.raises(RuntimeError, match='opening_not_verified'):
        clan_battle.execute_switches(port, plan)
    assert len(port.actions) == 1


@pytest.mark.parametrize('speed', [0, 3, 8, True, 2.0, '4'])
def test_invalid_speed_rejected_before_input(plan, speed):
    with pytest.raises(ValueError, match='speed_invalid'):
        backend.require_battle_speed({}, plan, speed)


def test_acceleration_requires_opening_calibration_and_no_axis(plan):
    profile = {'auto_battle': {'speeds': [1, 2, 4]}}
    backend.require_battle_speed(profile, plan, 4)
    with pytest.raises(RuntimeError, match='auto_speed_uncalibrated'):
        backend.require_battle_speed({}, plan, 4)
    plan['switches'] = [{'remaining_seconds': 60}]
    with pytest.raises(RuntimeError, match='auto_speed_uncalibrated'):
        backend.require_battle_speed(profile, plan, 2)


def sampled_plan():
    return dict(account_id='123456789', server='cn-bilibili', content_version='202610141448',
        period='2026-10', phase='B', round_min=1, round_max=6, boss=5,
        unit_ids=[129701, 134701, 107101, 135501, 134901], stars=[5, 5, 6, 5, 5],
        support_mode='none', support_unit_id=None, auto=True, set_by_unit={}, switches=[])


def test_optional_paused_boss_roi_preserves_legacy_and_exact_boss_match():
    plan = sampled_plan()
    profile = load_calibration(plan)
    assert backend.auto_battle_pipeline(profile, plan)['CBOpeningBoss']['roi'] == [805, 160, 135, 33]
    profile['auto_battle']['paused_boss_roi'] = [760, 157, 178, 40]
    profile['boss_targets'][str(plan['boss'])]['name'] = '厄勒克特拉夫人'
    node = backend.auto_battle_pipeline(profile, plan)['CBOpeningBoss']
    assert node['roi'] == [760, 157, 178, 40]
    assert node['expected'] == '^厄勒克特拉夫人$'
    assert node['threshold'] == .9
    profile['auto_battle']['paused_boss_roi'] = [1200, 157, 178, 40]
    with pytest.raises(ValueError, match='calibration_roi_invalid'):
        backend.auto_battle_pipeline(profile, plan)


def test_optional_paused_clock_roi_compiles_an_exact_time_on_the_original_frame():
    plan = sampled_plan()
    profile = load_calibration(plan)
    assert 'CBOpeningClockAlternate' not in backend.auto_battle_pipeline(profile, plan)
    profile['auto_battle']['paused_clock_alternate_roi'] = [1077, 18, 44, 32]
    node = backend.auto_battle_pipeline(profile, plan)['CBOpeningClockAlternate']
    assert node['roi'] == [1077, 18, 44, 32] and node['threshold'] == .9
    assert node['expected'] == backend.OPENING_CLOCK_PATTERN
    profile['auto_battle']['paused_clock_alternate_roi'] = [1279, 18, 44, 32]
    with pytest.raises(ValueError, match='calibration_roi_invalid'):
        backend.auto_battle_pipeline(profile, plan)


@pytest.mark.parametrize('sample,auto,sets,speed,second', [
    ('paused-all-on.png', False, [True]*5, 4, 90), ('paused-all-off.png', False, [False]*5, 4, 90),
    ('paused-mixed-1.png', True, [False, True, True, True, True], 1, 90),
    ('paused-mixed-2.png', True, [False, True, True, True, True], 2, 90),
    ('paused-mixed-4.png', True, [False, True, True, True, True], 4, 90),
    ('paused89-all-off.png', False, [False]*5, 1, 89),
    ('paused89-first-on-two.png', True, [True, False, False, False, False], 2, 89)])
def test_real_saved_menu_has_same_frame_team_controls_and_speed(sample, auto, sets, speed, second, monkeypatch):
    from pcr_maa.replay import SavedImages
    plan = sampled_plan()
    profile = load_calibration(plan)
    path = native.ROOT / 'assets/resource/clan-battle-calibrations/cn-bilibili-202610141448/samples' / sample
    frame = native.read_image(path)
    resource = native.load_resource()
    page = backend.ClanBattlePage(plan, profile)
    page.current_page = 'battle'
    characters = ClanBattleCharacters(plan, profile, page)
    assert resource.register_custom_recognition('ClanBattlePage', page)
    assert resource.register_custom_recognition('ClanBattleCharacters', characters)
    assert resource.override_pipeline(backend.calibration_pipeline(profile, plan))
    controller = SavedImages([[frame]])
    tasker = native.bind(resource, controller)
    tasker._resource_holder = resource
    try:
        receipt = native.run(tasker, 'CBOpeningRead')
        state = backend._detail(receipt)
        assert state['unit_ids'] == plan['unit_ids']
        assert state['sets'] == sets and state['auto'] is auto
        assert state['paused'] is True and state['remaining_seconds'] == second and state['battle_speed'] == speed
        assert 'zero_damage_verified' not in state and 'zero_tp_verified' not in state
        assert state['same_frame'] and state['frame_pixel_sha256'] == hashlib.sha256(frame.tobytes()).hexdigest()
        assert controller.clicks == [] and controller.swipes == []
    finally:
        stopped = native.stop_tasker(tasker)
        assert stopped['idle'] and stopped['stop_completed']


def test_speed_result_requires_matching_opening_and_resumed_readback(plan):
    port = paused_port(plan)
    evidence = clan_battle.execute_switches(port, plan)
    evidence['battle_speed'] = 4
    evidence['opening_evidence']['battle_speed'] = 4
    evidence['observation']['battle_speed'] = 4
    result = {'settings_evidence': evidence}
    assert clan_battle._settings(result, plan)
    evidence['observation']['battle_speed'] = 1
    assert not clan_battle._settings(result, plan)


@pytest.mark.parametrize('where', ['opening_evidence', 'observation'])
def test_boolean_speed_cannot_pass_integer_one_evidence(plan, where):
    evidence = clan_battle.execute_switches(paused_port(plan), plan)
    evidence['battle_speed'] = 1
    evidence[where]['battle_speed'] = True
    assert not clan_battle._settings({'settings_evidence': evidence}, plan)


def test_actual_two_speed_hud_cannot_match_one_or_four():
    import cv2
    import numpy as np
    profile = load_calibration(sampled_plan())
    frame = native.read_image(native.ROOT/'assets/resource/clan-battle-calibrations/cn-bilibili-202610141448/samples/battle-two.png')
    scores = {}
    for speed, key in ((1, 'speed_one'), (2, 'speed_two'), (4, 'speed_four')):
        marker = profile['pages']['battle'][key]
        x,y,w,h = marker['roi']
        template = cv2.imdecode(np.frombuffer((native.RESOURCE/'image'/marker['template']).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        scores[speed] = float(cv2.minMaxLoc(cv2.matchTemplate(frame[y:y+h,x:x+w], template, cv2.TM_CCOEFF_NORMED))[1])
    assert scores[2] > .99 and scores[1] < .9 and scores[4] < .9


def test_filled_tp_does_not_reject_a_paused_read_or_invent_ub_history():
    from pcr_maa.replay import SavedImages
    plan = sampled_plan()
    profile = load_calibration(plan)
    frame = native.read_image(native.ROOT/'assets/resource/clan-battle-calibrations/cn-bilibili-202610141448/samples/paused89-first-on-two.png')
    frame[388,365] = [220,180,50]
    resource = native.load_resource()
    page = backend.ClanBattlePage(plan, profile)
    page.current_page = 'battle'
    characters = ClanBattleCharacters(plan, profile, page)
    assert resource.register_custom_recognition('ClanBattlePage', page)
    assert resource.register_custom_recognition('ClanBattleCharacters', characters)
    assert resource.override_pipeline(backend.calibration_pipeline(profile, plan))
    controller = SavedImages([[frame]])
    tasker = native.bind(resource, controller)
    tasker._resource_holder = resource
    try:
        receipt = native.run(tasker, 'CBOpeningRead')
        assert receipt['native_ok']
        assert 'first_ub_evidence' not in backend._detail(receipt)
        assert not controller.clicks and not controller.swipes
    finally:
        stopped = native.stop_tasker(tasker)
        assert stopped['idle'] and stopped['stop_completed']


@pytest.mark.parametrize('previous', [True, False])
def test_native_role_toggle_without_first_ub_history_sends_no_input(previous):
    from pcr_maa.replay import SavedImages
    plan = sampled_plan()
    profile = load_calibration(plan)
    directory = native.ROOT/'assets/resource/clan-battle-calibrations/cn-bilibili-202610141448/samples'
    before = [previous, True, True, True, True]
    after = [not previous, True, True, True, True]
    initial = 'paused-all-on-auto-one.png' if previous else 'paused-mixed-1.png'
    final = 'paused-mixed-1.png' if previous else 'paused-all-on-auto-one.png'
    nodes = backend.calibration_pipeline(profile, plan)
    nodes['CBOpeningSet']['custom_recognition_param'] = dict(mode='opening_set', slot=0,
        previous=previous, desired=not previous, unit_ids=plan['unit_ids'], sets=before, auto=True, battle_speed=1)
    nodes['CBOpeningConfirmed']['custom_recognition_param'] = dict(mode='opening_read',
        unit_ids=plan['unit_ids'], sets=after, auto=True, battle_speed=1)
    resource = native.load_resource()
    page = backend.ClanBattlePage(plan, profile)
    page.current_page = 'battle'
    characters = ClanBattleCharacters(plan, profile, page)
    assert resource.register_custom_recognition('ClanBattlePage', page)
    assert resource.register_custom_recognition('ClanBattleCharacters', characters)
    assert resource.override_pipeline(nodes)
    controller = SavedImages([[native.read_image(directory/initial)], [native.read_image(directory/final)]])
    tasker = native.bind(resource, controller)
    tasker._resource_holder = resource
    try:
        receipt = native.run(tasker, 'CBOpeningSet', reset_hits=('CBOpeningSet',))
        assert not receipt['native_ok']
        assert controller.clicks == [] and controller.swipes == []
    finally:
        stopped = native.stop_tasker(tasker)
        assert stopped['idle'] and stopped['stop_completed']


def test_pause_guard_requires_a_running_positive_clock():
    import re
    nodes = backend.calibration_pipeline(load_calibration(sampled_plan()), sampled_plan())
    ready = nodes['CBOpeningReadyClock']
    assert 'CBOpeningReadyClock' in nodes['CBPauseOpening']['all_of']
    assert ready['roi'] == [1080, 21, 43, 26] and ready['threshold'] == .9
    for text in ('1:29', '1：00', '0:59', '0:01'):
        assert re.fullmatch(ready['expected'], text)
    for text in ('1:30', '0:00', '1:31', '0:60', ''):
        assert re.fullmatch(ready['expected'], text) is None


def test_native_opening_pause_chain_reads_guards_once_per_frame_then_frozen_team():
    from pcr_maa.replay import SavedImages
    plan = sampled_plan()
    profile = load_calibration(plan)
    directory = native.ROOT / 'assets/resource/clan-battle-calibrations/cn-bilibili-202610141448/samples'
    nodes = backend.calibration_pipeline(profile, plan)
    assert nodes['CBStart']['next'] == ['CBPauseOpening'] and nodes['CBStart']['rate_limit'] == 1
    assert nodes['CBPauseOpening']['recognition'] == 'And' and nodes['CBPauseOpening']['max_hit'] == 1
    resource = native.load_resource()
    page = backend.ClanBattlePage(plan, profile)
    page.current_page = 'battle'
    characters = ClanBattleCharacters(plan, profile, page)
    assert resource.register_custom_recognition('ClanBattlePage', page)
    assert resource.register_custom_recognition('ClanBattleCharacters', characters)
    assert resource.override_pipeline(nodes)
    battle = native.read_image(directory/'battle-two.png')
    paused = native.read_image(directory/'paused-all-on.png')
    # This composite clock keeps the synthetic replay timestamps consistent.
    paused[21:47, 1080:1123] = battle[21:47, 1080:1123]
    controller = SavedImages([[battle], [paused]])
    tasker = native.bind(resource, controller)
    tasker._resource_holder = resource
    try:
        receipt = native.run(tasker, 'CBPauseOpening', reset_hits=('CBPauseOpening',))
        native.require_native_input(receipt, ('CBPauseOpening',))
        state = backend._detail(receipt)
        assert state['paused'] and state['remaining_seconds'] == 88 and state['sets'] == [True]*5
        assert len(controller.clicks) == 1 and not controller.swipes
    finally:
        stopped = native.stop_tasker(tasker)
        assert stopped['idle'] and stopped['stop_completed']


@pytest.mark.parametrize('fading', [False, True])
def test_native_readiness_excludes_intro_even_when_clock_and_menu_templates_match(fading):
    import numpy as np
    from pcr_maa.replay import SavedImages
    plan = sampled_plan()
    nodes = backend.calibration_pipeline(load_calibration(plan), plan)
    path = native.ROOT / 'assets/resource/clan-battle-calibrations/cn-bilibili-202610141448/samples/battle-opening.png'
    frame = native.read_image(path)
    if fading:
        frame = (frame.astype(float)*.6).astype(np.uint8)
    resource = native.load_resource()
    assert resource.override_pipeline({
        'OpeningReadiness': dict(recognition='DirectHit', action='DoNothing', next=['CBOpeningInputReady', 'OpeningNotReady'],
            timeout=1000, pre_delay=0, post_delay=0),
        'OpeningNotReady': dict(recognition='DirectHit', action='DoNothing', next=[], pre_delay=0, post_delay=0),
        'CBOpeningInputReady': nodes['CBOpeningInputReady']})
    controller = SavedImages([[frame]])
    tasker = native.bind(resource, controller)
    tasker._resource_holder = resource
    try:
        receipt = native.run(tasker, 'OpeningReadiness')
        assert receipt['native_ok'] and receipt['nodes'][-1]['name'] == ('OpeningNotReady' if fading else 'CBOpeningInputReady')
        assert not controller.clicks and not controller.swipes
    finally:
        stopped = native.stop_tasker(tasker)
        assert stopped['idle'] and stopped['stop_completed']


def _saved_opening_read(frame, selected):
    """Use the real callback and independent native owner; never send controller inputs."""
    from pcr_maa.replay import SavedImages
    profile = load_calibration(selected)
    resource = native.load_resource()
    page = backend.ClanBattlePage(selected, profile)
    page.current_page = 'battle'
    characters = ClanBattleCharacters(selected, profile, page)
    assert resource.register_custom_recognition('ClanBattlePage', page)
    assert resource.register_custom_recognition('ClanBattleCharacters', characters)
    nodes = backend.calibration_pipeline(profile, selected)
    nodes['TestOpeningChoice'] = dict(recognition='DirectHit', action='DoNothing',
        next=['CBOpeningRead', 'TestOpeningRejected'], pre_delay=0, post_delay=0)
    nodes['TestOpeningRejected'] = dict(recognition='DirectHit', action='DoNothing',
        next=[], pre_delay=0, post_delay=0)
    assert resource.override_pipeline(nodes)
    controller = SavedImages([[frame]])
    tasker = native.bind(resource, controller)
    tasker._resource_holder = resource
    try:
        receipt = native.run(tasker, 'TestOpeningChoice')
        assert receipt['native_ok']
        assert not controller.clicks and not controller.swipes
        return receipt
    finally:
        stopped = native.stop_tasker(tasker)
        assert stopped['idle'] and stopped['stop_completed']


def test_real_c_zero_damage_is_independent_of_animated_background():
    selected = dict(sampled_plan(), phase='C', round_min=7, round_max=22, boss=3)
    path = native.ROOT/'assets/resource/clan-battle-calibrations/cn-bilibili-202610141448/samples/paused-c3-four-on.png'
    frame = native.read_image(path)
    receipt = _saved_opening_read(frame, selected)
    state = backend._detail(receipt)
    assert receipt['nodes'][-1]['name'] == 'CBOpeningRead'
    assert state['sets'] == [True, True, True, True, False]
    assert state['auto'] is False and state['battle_speed'] == 1 and state['remaining_seconds'] == 89
    assert 'first_ub_evidence' not in state
    assert len(load_calibration(selected)['auto_battle']['paused_guards']) == 3


@pytest.mark.parametrize('brightness', [1.0, .5], ids=['real-field', 'derived-half-brightness'])
@pytest.mark.parametrize('index,total', tuple(enumerate(['1510456', '1678288', '10982867', '21380644', '28070534', '60294202'])))
def test_real_multidigit_damage_does_not_block_paused_controls(index, total, brightness):
    import cv2
    import numpy as np
    path = Path(__file__).parent/'data/clan-nonzero-damage-rows.png'
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == '415de013eecb0157f0e74f98737d364d16382bf64f30d0e3234335ce432c19a9'
    rows = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert rows.shape == (192, 223, 3)
    # These are complete real numeric rows, composited into the paused page. The .5 cases
    # are derived fixtures, not claims of additional device captures.
    strip = (rows[index*32:(index+1)*32].astype(float)*brightness).astype(np.uint8)
    selected = sampled_plan()
    frame = native.read_image(native.ROOT/'assets/resource/clan-battle-calibrations/cn-bilibili-202610141448/samples/paused-all-on-auto-one.png')
    frame[48:80, :223] = strip
    receipt = _saved_opening_read(frame, selected)
    assert receipt['nodes'][-1]['name'] == 'CBOpeningRead'


@pytest.mark.parametrize('points,accepted', [(0, True), (1, True), (2, True)])
def test_damage_pixels_are_not_a_first_ub_guard(points, accepted):
    selected = sampled_plan()
    frame = native.read_image(native.ROOT/'assets/resource/clan-battle-calibrations/cn-bilibili-202610141448/samples/paused-all-on-auto-one.png')
    for x in (20, 200)[:points]:
        frame[60, x] = [0, 100, 100]
    receipt = _saved_opening_read(frame, selected)
    assert (receipt['nodes'][-1]['name'] == 'CBOpeningRead') is accepted


@pytest.mark.parametrize('invalid', ['wrong_boss', 'not_paused', 'unverified_clock'])
def test_zero_damage_does_not_replace_boss_pause_or_initial_clock_guards(invalid):
    selected = dict(sampled_plan(), phase='C', round_min=7, round_max=22, boss=3)
    directory = native.ROOT/'assets/resource/clan-battle-calibrations/cn-bilibili-202610141448/samples'
    frame = native.read_image(directory/'paused-c3-four-on.png')
    if invalid == 'wrong_boss':
        selected['boss'] = 5
    elif invalid == 'not_paused':
        frame = native.read_image(directory/'battle-opening.png')
    else:
        frame[21:47,1080:1123] = 0
    receipt = _saved_opening_read(frame, selected)
    assert receipt['nodes'][-1]['name'] == 'TestOpeningRejected'


def test_matched_inherited_settings_need_no_ub_history_and_send_no_toggle(plan):
    port = paused_port(plan)
    port.state.pop('first_ub_evidence')
    port.state.pop('opening_start_token')
    port.state.update(remaining_seconds=70, zero_damage_verified=False, zero_tp_verified=False)
    result = clan_battle.execute_switches(port, plan)
    assert result['settings_verified'] and result['actions'] == [] and port.actions == []


@pytest.mark.parametrize('missing', ['unknown', 'fired', 'wrong_start', 'release_on', 'release_unknown'])
def test_adjustment_requires_related_first_ub_and_configuration_history(plan, missing):
    port = paused_port(plan)
    uid = plan['unit_ids'][0]
    plan['set_by_unit'][str(uid)] = False
    evidence = port.state['first_ub_evidence']
    if missing in ('unknown', 'fired'):
        evidence['not_fired_unit_ids'].remove(uid)
    elif missing == 'wrong_start':
        evidence['start_token'] = 'another-battle'
    elif missing == 'release_on':
        evidence['control_policy']['auto_release_on_ub'] = True
    else:
        evidence.pop('control_policy')
    with pytest.raises(RuntimeError, match='first_ub_unverified'):
        clan_battle.execute_switches(port, plan)
    assert port.actions == []


def test_speed_difference_also_requires_all_affected_first_ubs(plan):
    port = paused_port(plan)
    port.battle_speed = 2
    port.state.pop('first_ub_evidence')
    with pytest.raises(RuntimeError, match='first_ub_unverified'):
        clan_battle.execute_switches(port, plan)
    assert port.actions == []


@pytest.mark.parametrize('policy', [None, {'auto_release_on_ub': True}])
def test_false_pause_controls_without_verified_release_policy_do_not_claim_history(plan, policy):
    reader = object.__new__(ClanBattleCharacters)
    reader.team = plan['unit_ids']
    reader.begin_opening('synthetic-start', control_policy=policy)
    reader.opening_started = True
    state = dict(unit_ids=plan['unit_ids'], auto=False, sets=[False]*5,
        remaining_seconds=80, frame_pixel_sha256='a'*64)
    result = reader.bind_paused_opening(state, 'opening_read')
    assert 'first_ub_evidence' not in result


def test_first_pause_causal_proof_is_bound_to_start_policy_and_frozen_clock(plan):
    reader = object.__new__(ClanBattleCharacters)
    reader.team = plan['unit_ids']
    policy = dict(auto_release_on_ub=False, source='owner_account_bound_settings_readback',
        same_frame=True, frame_pixel_sha256='b'*64)
    reader.begin_opening('synthetic-start', control_policy=policy)
    reader.opening_started = True
    state = dict(unit_ids=plan['unit_ids'], auto=False, sets=[False,True,False,False,False],
        remaining_seconds=80, frame_pixel_sha256='a'*64)
    result = reader.bind_paused_opening(deepcopy(state), 'opening_read')
    assert result['first_ub_evidence']['not_fired_unit_ids'] == [uid for i,uid in enumerate(plan['unit_ids']) if i != 1]
    clan_battle.require_first_ub(result, [plan['unit_ids'][0]])
    with pytest.raises(RuntimeError, match='first_ub_unverified'):
        clan_battle.require_first_ub(result, [plan['unit_ids'][1]])
    assert reader.bind_paused_opening(dict(state, remaining_seconds=79), 'opening_read') is None
    assert reader.bind_paused_opening(dict(state, sets=[True]*5), 'opening_read')['first_ub_evidence'] == result['first_ub_evidence']


@pytest.mark.parametrize('original,scaled,expected', [
    ('1:28',None,88),('128',None,88),(None,'128',88),('1:28','128',88),
    ('0:34','034',34),('059',None,59),('29','129',89)])
def test_complete_original_or_scaled_opening_clock_format_is_dynamic(original,scaled,expected):
    import numpy as np
    reader=object.__new__(ClanBattleCharacters)
    def word(context,image,name,**options):
        text=scaled if name.endswith('Scaled') else original
        if text is None: raise RuntimeError('synthetic_low_confidence')
        return text
    reader.page=NS(word=word)
    frame=np.zeros((720,1280,3),np.uint8)
    value,evidence=reader.read_opening_clock(None,frame,'Clock',[1080,21,43,26])
    assert value==expected and len(evidence['readings'])==3
    assert evidence['frame_pixel_sha256']==hashlib.sha256(frame.tobytes()).hexdigest()


@pytest.mark.parametrize('original,scaled', [('1:28','127'),('29',None),('L29',None),
    ('1:31',None),('159',None),('0029',None),(None,None)])
def test_incomplete_conflicting_or_illegal_clock_does_not_pick_a_favourable_value(original,scaled):
    import numpy as np
    reader=object.__new__(ClanBattleCharacters)
    def word(context,image,name,**options):
        text=scaled if name.endswith('Scaled') else original
        if text is None: raise RuntimeError('synthetic_low_confidence')
        return text
    reader.page=NS(word=word)
    with pytest.raises(RuntimeError,match='opening_clock_unknown'):
        reader.read_opening_clock(None,np.zeros((720,1280,3),np.uint8),'Clock',[1080,21,43,26])


@pytest.mark.parametrize('values,expected', [
    ((None,None,'128'),88), ((None,None,'034'),34),
    (('128','128','128'),88), (('128',None,'127'),None),
    ((None,'129','128'),None), ((None,None,'28'),None),
    ((None,None,'L28'),None), ((None,None,None),None)])
def test_opening_clock_gray_linear_requires_complete_unambiguous_same_frame_readings(values,expected):
    import numpy as np
    reader=object.__new__(ClanBattleCharacters)
    transforms=('original','crop_2x_cubic','crop_gray_2x_linear')
    def word(context,image,name,**options):
        text=values[transforms.index(options['evidence']['transform'])]
        if text is None: raise RuntimeError('synthetic_low_confidence')
        return text
    reader.page=NS(word=word)
    frame=np.zeros((720,1280,3),np.uint8)
    if expected is None:
        with pytest.raises(RuntimeError,match='opening_clock_unknown'):
            reader.read_opening_clock(None,frame,'Clock',[1080,21,43,26])
    else:
        seconds,evidence=reader.read_opening_clock(None,frame,'Clock',[1080,21,43,26])
        assert seconds==expected
        assert [row['transform'] for row in evidence['readings']]==list(transforms)
        assert evidence['frame_pixel_sha256']==hashlib.sha256(frame.tobytes()).hexdigest()


@pytest.mark.parametrize('primary,alternate,expected', [
    ('17','1:17',77), ('1:17','117',77), ('1:17','1:27',None), ('17','1:37',None)])
def test_calibrated_alternate_paused_clock_rejects_conflicting_or_incomplete_times(primary,alternate,expected):
    import numpy as np
    frame = np.zeros((720,1280,3),np.uint8)
    reader = object.__new__(ClanBattleCharacters)
    def word(context, image, name, **options):
        if name == 'ClockAlternate':
            assert image is frame
            return alternate
        return primary
    reader.page = NS(word=word)
    args = (None, frame, 'Clock', [1081,18,45,28])
    roi = [1077,18,44,32]
    if expected is None:
        with pytest.raises(RuntimeError, match='opening_clock_unknown'):
            reader.read_opening_clock(*args, alternate_roi=roi)
    else:
        actual, evidence = reader.read_opening_clock(*args, alternate_roi=roi)
        assert actual == expected
        assert evidence['readings'][-1]['roi'] == roi
        assert evidence['readings'][-1]['image_pixel_sha256'] == evidence['frame_pixel_sha256']


@pytest.mark.parametrize('wrong_token',[False,True])
def test_native_cbstart_token_to_first_pause_has_only_start_and_pause_inputs(monkeypatch,wrong_token):
    import numpy as np
    from pcr_maa.replay import SavedImages
    selected=sampled_plan()
    profile=load_calibration(selected)
    directory=native.ROOT/'assets/resource/clan-battle-calibrations/cn-bilibili-202610141448/samples'
    resource=native.load_resource()
    page=backend.ClanBattlePage(selected,profile)
    page.current_page='formation'
    characters=ClanBattleCharacters(selected,profile,page)
    # Synthetic formation isolates the real shared start-token callback and native input chain.
    monkeypatch.setattr(characters,'formation',lambda context,image:dict(unit_ids=selected['unit_ids']))
    monkeypatch.setattr(page,'require_team',lambda detail:None)
    monkeypatch.setattr(page,'action',lambda context,image,name:[5,5,6,6])
    token='synthetic-owned-new-battle'
    characters.begin_opening(token,control_policy=dict(auto_release_on_ub=False,
        source='owner_account_bound_settings_readback',same_frame=True,frame_pixel_sha256='b'*64))
    assert not characters.opening_started
    nodes=backend.calibration_pipeline(profile,selected)
    nodes['CBStart']['custom_recognition_param']=dict(mode='start',opening_start_token='other' if wrong_token else token)
    assert resource.register_custom_recognition('ClanBattlePage',page)
    assert resource.register_custom_recognition('ClanBattleCharacters',characters)
    assert resource.override_pipeline(nodes)
    battle=native.read_image(directory/'battle-two.png')
    paused=native.read_image(directory/'paused89-all-off.png')
    # This composite clock keeps the synthetic replay timestamps consistent.
    paused[21:47,1080:1123]=battle[21:47,1080:1123]
    controller=SavedImages([[np.zeros((720,1280,3),np.uint8)], [battle], [paused]])
    tasker=native.bind(resource,controller);tasker._resource_holder=resource
    try:
        receipt=native.run(tasker,'CBStart',reset_hits=('CBStart','CBPauseOpening'))
        if wrong_token:
            assert not receipt['native_ok'] and not characters.opening_started
            assert not controller.clicks and not controller.swipes
        else:
            native.require_native_input(receipt,('CBStart','CBPauseOpening'))
            state=backend._detail(receipt)
            assert state['opening_start_token']==token and state['remaining_seconds']==88
            assert set(state['first_ub_evidence']['not_fired_unit_ids'])==set(selected['unit_ids'])
            assert len(controller.clicks)==2 and not controller.swipes
            names=[row['name'] for row in receipt['nodes']]
            assert names.index('CBStart')<names.index('CBPauseOpening')<names.index('CBOpeningRead')
            assert not {'CBOpeningSet','CBOpeningAuto','CBOpeningSpeed','CBToggleSet','CBToggleAuto'}&set(names)
    finally:
        stopped=native.stop_tasker(tasker);assert stopped['idle'] and stopped['stop_completed']


def test_native_6002_preserves_the_exact_receipt_and_invalidates_opening(monkeypatch,tmp_path):
    from pcr_maa.clan_battle_recovery import ClanDisconnected
    port=backend.NativeClanBattle(NS(),tmp_path)
    invalid=[];port.characters=NS(invalidate_opening=lambda reason:invalid.append(reason))
    receipt=dict(native_ok=True,nodes=[dict(name='LabyDisconnect6002Observed',completed=True)])
    monkeypatch.setattr(backend.NativeGuideTrial,'run',lambda *args,**options:receipt)
    with pytest.raises(ClanDisconnected) as caught:
        port.run('GTChoose')
    assert caught.value.receipt is receipt and port.last_receipt is receipt
    assert invalid==['owner_binding_invalidated']


@pytest.mark.parametrize('resume',[False,True])
def test_prestart_6002_configures_callbacks_once_and_only_reobserves_after_verified_recovery(monkeypatch,tmp_path,resume):
    from pcr_maa.clan_battle_recovery import ClanDisconnected
    port=backend.NativeClanBattle(NS(),tmp_path,calibration={'version':2})
    calls=[]
    monkeypatch.setattr(port,'configure',lambda plan:calls.append('configured_once'))
    def prepare(plan):
        calls.append('observed')
        if calls.count('observed')==1:raise ClanDisconnected({},'not_sent')
    monkeypatch.setattr(port,'_prepare_from_current_page',prepare)
    monkeypatch.setattr(backend,'recover_disconnect',lambda *args,**options:dict(can_continue=resume))
    if resume:port.prepare({})
    else:
        with pytest.raises(ClanDisconnected):port.prepare({})
    assert calls.count('configured_once')==1 and calls.count('observed')==1+int(resume)


def test_sent_6002_recovers_connection_without_reentering_start(monkeypatch,tmp_path):
    from pcr_maa.clan_battle_recovery import ClanDisconnected
    port=backend.NativeClanBattle(NS(),tmp_path);port.battle_start='sent';calls=[]
    def started(plan):calls.append('one_start');raise ClanDisconnected({},'sent')
    monkeypatch.setattr(port,'_start_and_read_bound',started)
    monkeypatch.setattr(port,'finish_opening_capture',lambda:calls.append('capture_finished'))
    monkeypatch.setattr(backend,'recover_disconnect',lambda *args,**options:calls.append('connection_recovered') or dict(can_continue=False))
    with pytest.raises(ClanDisconnected):port.start_and_read({})
    assert calls.count('one_start')==1 and calls.count('connection_recovered')==1
