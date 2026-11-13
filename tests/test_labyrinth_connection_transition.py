from types import SimpleNamespace as NS

import numpy as np
import pytest

from pcr_maa.labyrinth import autoplay, scene
from test_labyrinth_recognition_perf_r50 import SceneContext
from test_labyrinth_upstream_scene_reuse import Batch, Runtime, actions, owner, receipt


@pytest.mark.parametrize('background', ['team', 'map', 'shop', 'item_reward', 'battle_victory'])
def test_connection_covers_background_and_blocks_same_frame_input(background):
    context = SceneContext(scene._RULES[background][0])
    original = context.run_recognition
    def recognize(name, image):
        if name == 'HomeDataConnection':
            return NS(hit=True, reco_id=1, raw_detail={'filtered': [
                dict(text='正在进行数据连接', score=.99, box=[1000, 25, 240, 50])]})
        return original(name, image)
    context.run_recognition = recognize
    def read(**params):
        return context.observer.analyze(context, NS(image=np.zeros((720, 1280, 3), np.uint8),
            custom_recognition_param=params))
    loading = read(probe_kinds=[background]).detail
    assert loading['kind'] == 'connecting' and not loading['payload']['controls']
    assert read(kind=background).box is None
    context.keys.add('DISCONNECT')
    assert read().detail['kind'] == 'disconnected'


@pytest.mark.parametrize('kind', ['normal_challenge', 'ex_challenge', 'boss_challenge'])
def test_owned_start_waits_through_connection_beyond_team_grace(monkeypatch, kind):
    clock = [0]
    pages = [scene.Scene(page, receipt(), {}) for page in
             ('team', 'connecting', 'connecting', 'team', 'battle', 'battle_victory')]
    adapter = actions(Runtime(pages), Batch(), owner())
    adapter.combat_kind = kind
    monkeypatch.setattr(autoplay.native, 'active_now', lambda: clock[0])
    monkeypatch.setattr(autoplay, '_poll_pause', lambda *args: clock.__setitem__(0, clock[0]+5))
    assert adapter.start_battle(NS(enter_id=7), NS(block_id=101, block_type=8 if kind=='boss_challenge' else 2))['outcome'] == 'won'
    assert clock[0] == 25 and adapter.runtime.reads == 6


@pytest.mark.parametrize('fields', [{}, {'acquisition_fields': {'enter_id': 7}},
    {'final_fields': {'enter_id': 7, 'block_type': 8, 'settlement_phase': 'before_score'}}])
def test_wait_polls_loading_without_accepting_or_advancing_it(monkeypatch, fields):
    runtime = Runtime([scene.Scene('connecting', receipt(), {}), scene.Scene('item_reward', receipt(), {})])
    runtime.tasker = owner()
    polls = []
    monkeypatch.setattr(autoplay, '_poll_pause', lambda *args: polls.append(args[2]))
    result = scene.MaaSceneRuntime.wait_for(runtime, Batch(), {'item_reward'}, **fields)
    assert result.kind == 'item_reward' and polls == ['LabySceneTransitionPollSpacing']


@pytest.mark.parametrize('kinds,parent,expected', [
    (['connecting'] * 7 + ['joined'], 100, 'joined'),
    (['connecting'] * 30, 100, 'timeout'),
    (['connecting'] * 7 + ['joined'], 9, 'timeout'),
    (['map'] * 20, 100, 'original_timeout'),
    (['connecting', 'disconnected'], 100, 'disconnected'),
])
def test_acquisition_waits_for_late_joined_without_resending(monkeypatch, kinds, parent, expected):
    clock=[0.]
    runtime=Runtime([scene.Scene(kind,receipt(),{}) for kind in kinds])
    runtime.tasker=owner()
    monkeypatch.setattr(autoplay.native,'active_now',lambda:clock[0])
    def pause(*args):
        clock[0]=min(clock[0]+3,autoplay.native.RUN_DEADLINE.get())
    monkeypatch.setattr(autoplay,'_poll_pause',pause)
    token=autoplay.native.RUN_DEADLINE.set(parent)
    def wait():
        return scene.MaaSceneRuntime.wait_for(runtime,Batch(),{'joined'},source='role_reward',
            timeout=12,reason='role_reward_readback_unknown',acquisition_fields={'enter_id':713})
    try:
        if expected=='joined':
            assert wait().kind=='joined' and clock[0]==21
        else:
            reason='disconnected' if expected=='disconnected' else 'role_reward_readback_unknown'
            with pytest.raises(autoplay.BatchStopped,match='^'+reason+'$'):wait()
            if expected=='original_timeout':assert clock[0]==12
            if expected=='timeout':assert clock[0]==min(parent,60)
        assert autoplay.native.RUN_DEADLINE.get()==parent
    finally:
        autoplay.native.RUN_DEADLINE.reset(token)


@pytest.mark.parametrize('kind', ['ready', 'connecting', 'item_reward', 'unknown', 'disconnected', 'date_changed'])
def test_relic_selection_only_rechecks_the_obtained_receipt(monkeypatch, kind):
    from pcr_maa.labyrinth import relics
    reward = object.__new__(relics.MaaRelicReward)
    reward.tasker, reward.timings = object(), None
    calls = []
    def read(node, *, input=False):
        assert node == 'LabyRelicObtainedObserved' and not input
        calls.append(node)
        if kind != 'ready' and len(calls) == 1:
            raise autoplay.BatchStopped('relic_result_unknown')
        return 'exact receipt'
    reward._run = read
    waits = []
    def wait(batch, allowed, **kwargs):
        assert allowed == {'item_reward'} and kwargs == dict(
            source='relic_reward', timeout=60, reason='relic_result_unknown')
        waits.append(1)
    runtime = NS(observe=lambda **kwargs: NS(kind=kind), wait_for=wait)
    monkeypatch.setattr(scene, 'MaaSceneRuntime', lambda *a, **kw: runtime)
    if kind in ('ready', 'item_reward', 'connecting'):
        assert reward._obtained_receipt(Batch()) == 'exact receipt'
    else:
        reason = 'relic_result_unknown' if kind == 'unknown' else kind
        with pytest.raises(autoplay.BatchStopped, match='^'+reason+'$'):
            reward._obtained_receipt(Batch())
    assert len(calls) == (2 if kind in ('item_reward', 'connecting') else 1)
    assert len(waits) == int(kind == 'connecting')


def test_persistent_connection_obeys_battle_deadline(monkeypatch):
    clock = [0]
    runtime = Runtime([scene.Scene('connecting', receipt(), {})])
    monkeypatch.setattr(autoplay.native, 'active_now', lambda: clock[0])
    monkeypatch.setattr(autoplay, '_poll_pause', lambda *args: clock.__setitem__(0, 300))
    with pytest.raises(autoplay.BatchStopped, match='^battle_timeout$'):
        actions(runtime, Batch(), owner()).start_battle(NS(enter_id=7), NS(block_id=101, block_type=8))
    assert runtime.reads == 1


@pytest.mark.parametrize('kinds', [('team', 'team'), ('battle', 'connecting', 'team')])
def test_stuck_or_returned_team_still_stops(monkeypatch, kinds):
    clock = [0]
    runtime = Runtime([scene.Scene(kind, receipt(), {}) for kind in kinds])
    monkeypatch.setattr(autoplay.native, 'active_now', lambda: clock[0])
    monkeypatch.setattr(autoplay, '_poll_pause', lambda *args: clock.__setitem__(0, clock[0]+5))
    with pytest.raises(autoplay.BatchStopped, match='^battle_result_unknown$'):
        actions(runtime, Batch(), owner()).start_battle(NS(enter_id=7), NS(block_id=101, block_type=8))


def test_overlay_during_map_location_waits_without_preview(tmp_path, monkeypatch):
    from test_labyrinth_autoplay import make, route_for
    route = route_for([1, 6])
    runner, batch, runtime, navigator, _ = make(tmp_path, route,
        [('connecting', {}), ('map', {'area': 1})])
    runner.cursor = autoplay.RunCursor()
    busy = dict(kind='connecting', payload={})
    navigator.locate = lambda *args: (dict(status='scene_changed', scene=busy), receipt())
    monkeypatch.setattr(autoplay, '_poll_pause', lambda *args: runtime.advance())
    assert runner._move(batch, route, scene.Scene('map', receipt(), {'area': 1})).kind == 'map'
    assert not navigator.previews and not runtime.inputs
