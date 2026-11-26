"""Bounded opening-pause retry: isolated saved-image replay only."""
import hashlib
import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from pcr_maa import clan_battle_native as backend, clan_battle_daily_native as daily, native
from pcr_maa.clan_battle_calibration import load_calibration
from pcr_maa.clan_battle_characters import ClanBattleCharacters
from pcr_maa.replay import SavedImages
from test_clan_opening import sampled_plan
from test_clan_formal_opening import formal_reader
from test_clan_battle_daily_native import executable, profile
from test_clan_battle_native import calibration, calibration_v2, plan
from test_context_backend import sdk, invoke


@pytest.mark.parametrize('custom_clock', [None, 'roi', 'expected'])
def test_simulation_reuses_only_identical_general_hud_clock(custom_clock):
    plan = sampled_plan()
    calibration = load_calibration(plan)
    clock = calibration['auto_battle']['opening_guards'][2]
    if custom_clock == 'roi':
        clock['roi'] = [clock['roi'][0] + 1, *clock['roi'][1:]]
    elif custom_clock == 'expected':
        clock['expected'] = '^1:29$'
    nodes = backend.auto_battle_pipeline(calibration, plan)
    for name in ('CBPauseOpening', 'CBPauseRetry'):
        guards = nodes[name]['all_of']
        assert ('CBOpening_opening_guards_2' in guards) == bool(custom_clock)
        assert guards.count('CBOpeningReadyClock') == 1
        assert all(guard in guards for guard in ('CBOpeningInputReady', 'CBOpening_opening_guards_0',
                                                  'CBOpening_opening_guards_1'))


SAMPLES = native.ROOT / 'assets/resource/clan-battle-calibrations/cn-bilibili-202610141448/samples'
LIVE = native.ROOT / '.runtime/clan-open-20261025/live-completion-20261026'
D4_BATTLE = LIVE / 'opening/live-owner/bbcd69aa348f4bfb8ff018292d129485/anchor-battle.png'
D4_RESULT = LIVE / 'd-simulation-20261027-101710/D4_PAUSE_MISS_RESULT.png'


class NoPauseAck(SavedImages):
    def click(self, x, y):
        if len(self.clicks) == 1:
            self.clicks.append([x, y])
            return False
        return super().click(x, y)


class NoRetryAck(SavedImages):
    def click(self, x, y):
        if len(self.clicks) == 2:
            self.clicks.append([x, y])
            return False
        return super().click(x, y)


def opening_replay(monkeypatch, first, following, *, pause_ack=True):
    plan = sampled_plan()
    profile = load_calibration(plan)
    resource = native.load_resource()
    page = backend.ClanBattlePage(plan, profile)
    page.current_page = 'formation'
    characters = ClanBattleCharacters(plan, profile, page)
    monkeypatch.setattr(characters, 'formation', lambda context, image: dict(unit_ids=plan['unit_ids']))
    monkeypatch.setattr(page, 'require_team', lambda detail: None)
    monkeypatch.setattr(page, 'action', lambda context, image, name: [5, 5, 6, 6])
    token = 'saved-image-pause-retry'
    characters.begin_opening(token, owner_source='synthetic-owner')
    nodes = backend.calibration_pipeline(profile, plan)
    nodes['CBStart']['custom_recognition_param'] = dict(mode='start', opening_start_token=token)
    nodes['CBStart']['timeout'] = 2500
    nodes['CBPauseOpening']['timeout'] = 2500
    nodes['CBPauseRetry']['timeout'] = 2500
    assert resource.register_custom_recognition('ClanBattlePage', page)
    assert resource.register_custom_recognition('ClanBattleCharacters', characters)
    assert resource.override_pipeline(nodes)
    stages = [[np.zeros((720, 1280, 3), np.uint8)], [first], *[[frame] for frame in following]]
    controller_type = NoRetryAck if pause_ack == 'retry' else SavedImages if pause_ack else NoPauseAck
    controller = controller_type(stages)
    tasker = native.bind(resource, controller)
    tasker._resource_holder = resource
    try:
        receipt = native.run(tasker, 'CBStart', reset_hits=('CBStart', 'CBPauseOpening', 'CBPauseRetry'))
        return receipt, controller.clicks[:], characters.opening_observer
    finally:
        stopped = native.stop_tasker(tasker)
        assert stopped['idle'] and stopped['stop_completed']


def battle_and_paused():
    battle = native.read_image(SAMPLES / 'battle-two.png')
    paused = native.read_image(SAMPLES / 'paused-all-on.png')
    paused[21:47, 1080:1123] = battle[21:47, 1080:1123]
    return battle, paused


def test_saved_image_retry_reads_pause_and_keeps_original_battle_anchor(monkeypatch, tmp_path):
    if not D4_BATTLE.is_file():
        pytest.skip('local real D4 source frame not available')
    first = native.read_image(D4_BATTLE)
    battle, paused = battle_and_paused()
    receipt, clicks, observer = opening_replay(monkeypatch, first, [battle, paused])
    anchors = observer.anchor_refs
    native.require_native_input(receipt, ('CBStart', 'CBPauseOpening', 'CBPauseRetry'))
    names = [row['name'] for row in receipt['nodes']]
    assert names.index('CBStart') < names.index('CBPauseOpening') < names.index('CBPauseRetry') < names.index('CBOpeningRead')
    assert len(clicks) == 3 and clicks[1][0] > 1100 and clicks[2][0] > 1100
    assert anchors['battle']['frame_pixel_sha256'] == hashlib.sha256(first.tobytes()).hexdigest()
    assert anchors['battle']['frame_pixel_sha256'] != hashlib.sha256(battle.tobytes()).hexdigest()
    state = backend._detail(receipt)
    assert state['paused'] and not state['first_ub_observation']['not_fired_unit_ids']
    assert 'first_ub_evidence' not in state
    destination = tmp_path / observer.start_token
    destination.mkdir()
    manifest = observer.write_anchors(destination, start_token=observer.start_token,
        owner_source='synthetic-owner', source_valid=True, start_receipt=receipt)
    assert manifest['status'] == 'captured' and all(row['receipt_confirmed'] for row in manifest['anchors'])
    assert manifest['coverage'] == 'unknown' and not manifest['first_ub_verified']


def test_already_paused_needs_no_retry(monkeypatch):
    battle, paused = battle_and_paused()
    receipt, clicks, _ = opening_replay(monkeypatch, battle, [paused])
    native.require_native_input(receipt, ('CBStart', 'CBPauseOpening'))
    assert len(clicks) == 2
    assert 'CBPauseRetry' not in [node['name'] for node in receipt['nodes']]


@pytest.mark.parametrize('case', ['unreadable_pause', 'obscured', 'result', 'unknown'])
def test_retry_never_clicks_after_non_running_page(monkeypatch, case):
    battle, paused = battle_and_paused()
    if case == 'unreadable_pause':
        paused[285:450, 350:1000] = 0  # keep the paused dialog but obscure the team
        after = paused
    elif case == 'obscured':
        after = (battle.astype(np.float32) * 0.45).astype(np.uint8)
    elif case == 'result':
        if not D4_RESULT.is_file():
            pytest.skip('local real D4 result not available')
        after = native.read_image(D4_RESULT)
    else:
        after = np.zeros_like(battle)
    receipt, clicks, _ = opening_replay(monkeypatch, battle, [after])
    assert not receipt['native_ok']
    assert len(clicks) == 2  # start and the original pause only
    assert 'CBPauseRetry' not in [node['name'] for node in receipt['nodes']]


def test_unknown_first_pause_ack_does_not_retry(monkeypatch):
    battle, _ = battle_and_paused()
    receipt, clicks, _ = opening_replay(monkeypatch, battle, [battle], pause_ack=False)
    assert not receipt['native_ok']
    assert len(clicks) == 2
    assert 'CBPauseRetry' not in [node['name'] for node in receipt['nodes']]


def test_unknown_retry_ack_stops_without_third_menu_input(monkeypatch):
    battle, _ = battle_and_paused()
    receipt, clicks, _ = opening_replay(monkeypatch, battle, [battle, battle], pause_ack='retry')
    assert not receipt['native_ok']
    assert len(clicks) == 3
    assert [node['name'] for node in receipt['nodes']].count('CBPauseRetry') <= 1


def test_successful_retry_is_never_sent_a_third_time(monkeypatch):
    battle, _ = battle_and_paused()
    receipt, clicks, _ = opening_replay(monkeypatch, battle, [battle, battle])
    assert not receipt['native_ok']
    assert len(clicks) == 3
    assert [node['name'] for node in receipt['nodes']].count('CBPauseRetry') == 1


def test_simulation_retry_callback_rejects_stop_disconnect_and_paused(monkeypatch):
    reader = object.__new__(ClanBattleCharacters)
    reader.opening_started, reader.opening_token = True, 'start'
    reader.page = NS(current_page='battle')
    hits = set()
    reader.page.hit = lambda context, image, name: name in hits
    frame = np.zeros((720, 1280, 3), np.uint8)
    context = NS(tasker=NS(stopping=False))
    args = NS(image=frame, custom_recognition_param=json.dumps({'mode': 'pause_retry'}))
    assert reader.analyze(context, args) is not None
    for node in ('LabyDisconnect6002Observed', 'CBOpening_paused_guards_0', 'CBOpening_paused_guards_1'):
        hits.add(node)
        assert reader.analyze(context, args) is None
        hits.clear()
    context.tasker.stopping = True
    assert reader.analyze(context, args) is None
    context.tasker.stopping = False
    reader.opening_started = False
    assert reader.analyze(context, args) is None


def test_formal_retry_rechecks_binding_and_does_not_record_battle_anchor(formal_reader, monkeypatch):
    port, reader, context, image, hits, clock = formal_reader
    reader.opening_started = True
    clock[0] = '0:33'
    hits.difference_update(f'CBFormalOpening_paused_guards_{i}' for i in range(3))
    recorded = []
    monkeypatch.setattr(reader, 'record_opening_frame', lambda *args: recorded.append(args))
    args = NS(image=image, custom_recognition_param=json.dumps({'mode': 'formal_pause_retry'}))
    assert reader.analyze(context, args) is not None and recorded == []
    hits.add('LabyDisconnect6002Observed')
    assert reader.analyze(context, args) is None
    hits.clear()
    hits.update(f'CBFormalOpening_paused_guards_{i}' for i in range(3))
    assert reader.analyze(context, args) is None
    hits.clear()
    context.tasker.stopping = True
    assert reader.analyze(context, args) is None
    context.tasker.stopping = False
    port.pending['action']['instance_id'] = 'changed'
    assert reader.analyze(context, args) is None and recorded == []


def test_formal_retry_requires_bright_menu_and_cannot_loop(executable, plan):
    nodes = daily.formal_opening_pipeline(executable, plan)
    assert nodes['CBDailyPauseOpening']['next'] == ['CBOpeningRead', 'CBDailyPauseRetry']
    retry = nodes['CBDailyPauseRetry']
    assert retry['next'] == ['CBOpeningRead'] and retry['max_hit'] == 1
    assert 'CBDailyOpeningInputReady' in retry['all_of']
    assert 'CBDailyOpeningRetryBinding' in retry['all_of']
    assert 'CBDailyOpeningBinding' not in retry['all_of']


def test_start_renews_only_new_pause_nodes(monkeypatch, tmp_path):
    calls = []
    receipt = dict(native_ok=True, nodes=[])
    monkeypatch.setattr(backend.NativeGuideTrial, 'run', lambda self, node, **kw: calls.append((node, kw)) or receipt)
    sim = backend.NativeClanBattle(NS(), tmp_path, calibration={'auto_battle': {'speeds': [1, 2, 4]}})
    sim.plan = sampled_plan()
    sim.run('CBStart', click=True)
    assert calls == [('CBStart', dict(click=True, reset_hits=('CBPauseOpening', 'CBPauseRetry')))]
    with pytest.raises(RuntimeError, match='already_attempted'):
        sim.run('CBStart', click=True)
    assert len(calls) == 1

    calls.clear()
    formal = daily.NativeClanDailyAdapter.__new__(daily.NativeClanDailyAdapter)
    formal.started, formal.pending, formal.battle_start = False, {'intent_id': 'new'}, 'not_sent'
    formal.calibration = {'daily': {'execution': {}}}
    formal.page = NS(expected=None)
    formal.run('CBStart', click=True)
    assert calls == [('CBStart', dict(click=True, reset_hits=('CBDailyPauseOpening', 'CBDailyPauseRetry')))]
    with pytest.raises(RuntimeError, match='not_reserved'):
        formal.run('CBStart', click=True)
    assert len(calls) == 1


def test_borrowed_owner_rearms_new_opening_nodes_each_time(sdk):
    from pcr_maa.context_backend import ContextTasker
    from pcr_maa.guide_trial_native import NativeGuideTrial
    tasker, _, controller, directory = sdk
    receipts = []
    def callback(context):
        with ContextTasker(context) as borrowed:
            assert borrowed.resource.override_pipeline({
                'CBStart': dict(recognition='DirectHit', action='Click', target=[4, 4, 1, 1],
                    max_hit=1, next=['CBPauseOpening'], pre_delay=0, post_delay=0),
                'CBPauseOpening': dict(recognition='DirectHit', action='Click', target=[5, 5, 1, 1],
                    max_hit=1, next=['CBPauseRetry'], pre_delay=0, post_delay=0),
                'CBPauseRetry': dict(recognition='DirectHit', action='Click', target=[6, 6, 1, 1],
                    max_hit=1, next=[], pre_delay=0, post_delay=0)})
            guide = NativeGuideTrial(borrowed, directory)
            for _ in range(3):
                receipts.append(guide.run('CBStart', click=True, reset_hits=('CBPauseOpening', 'CBPauseRetry')))
    assert invoke(sdk, callback)['native_ok']
    assert all(row['native_ok'] for row in receipts)
    assert controller.clicks == [[4, 4], [5, 5], [6, 6]] * 3

