"""Independent regressions: a proven unsent close preserves final progress."""
from types import SimpleNamespace as NS
import pytest
from pcr_maa import native
from pcr_maa.labyrinth import autoplay as a, scene as s
from test_labyrinth_autoplay import make, route_for, receipt
from test_labyrinth_character_port import offline


@pytest.mark.parametrize('source,action,phase,page,expected', [
    ('score_result', 'settlement_next', 'before_score', 'chest', 'chest_sequence'),
    ('final_clear', 'settlement_next', 'before_score', 'chest', 'chest_sequence'),
    ('item_reward', 'close_reward', 'chest_sequence', 'home', 'waiting_home'),
])
def test_unsent_final_close_keeps_verified_successor_phase(tmp_path, source, action, phase, page, expected):
    route = route_for([1, 8])
    runner, batch, runtime, _, _ = make(tmp_path, route, [(page, {})])
    runner.cursor = a.RunCursor(index=1, final_boss_won=True,
        clear_observed=True, final_settlement_phase=phase)
    runner.route = route
    def unsent(*args, **kwargs):
        raise native.NativeInputNotSent(dict(input_execution='not_sent',
            native_evidence=dict(entry='LabySceneActionOnce', finished=True, action_started=False)))
    runtime.perform = unsent
    following = runner._do(batch, route, s.Scene(source, receipt(), {}), action, {page})
    assert following.kind == page
    assert runner.cursor.final_settlement_phase == expected
    assert runner.last_scene is following
    rows = batch.journal.events()
    assert len(rows) == 2 and rows[-1]['result']['input_execution'] == 'not_sent'
    assert rows[-1]['result']['input_action_verified'] is False


def test_unsent_final_item_close_waits_for_home_without_animation_input(tmp_path, monkeypatch):
    route = route_for([1, 8])
    runner, batch, runtime, _, _ = make(tmp_path, route, [('unknown', {}), ('home', {})])
    runner.cursor = a.RunCursor(index=1, final_boss_won=True,
        clear_observed=True, final_settlement_phase='chest_sequence')
    runner.route = route
    clock = [0.0]
    monkeypatch.setattr(s, 'time', NS(monotonic=lambda: clock[0]))
    def pause(*args):
        clock[0] += .5
        runtime.advance()
    monkeypatch.setattr(a, '_poll_pause', pause)
    attempts = []
    def unsent(scene, action, **kwargs):
        attempts.append(action)
        if action != 'close_reward':
            pytest.fail('a zero-input close recovery dispatched a new animation input')
        raise native.NativeInputNotSent(dict(input_execution='not_sent',
            native_evidence=dict(entry='LabySceneActionOnce', finished=True, action_started=False)))
    runtime.perform = unsent
    following = runner._do(batch, route, s.Scene('item_reward', receipt(), {}), 'close_reward', {'home'})
    assert following.kind == 'home' and attempts == ['close_reward']
    assert runner.cursor.final_settlement_phase == 'waiting_home'
    assert len(batch.journal.events()) == 2


@pytest.mark.parametrize('following', ['map', 'item_reward'])
def test_link_close_waits_for_a_different_business_successor(tmp_path, monkeypatch, following):
    from test_labyrinth_links import mock_link_runtime
    batch, reward, journal, calls, stamp, _ = mock_link_runtime(monkeypatch, tmp_path)
    old = ('获得道具', '火属性的随机印记×3')
    new = ('获得道具', '守备+1×1')
    from test_labyrinth_autoplay import Runtime
    runtime = Runtime([('item_reward', {'item_result_signature': old}),
        (following, {'area': 1, 'item_result_signature': new})])
    runtime.tasker = reward.tasker
    actions = a.MaaRunActions.__new__(a.MaaRunActions)
    actions.batch, actions.tasker, actions.stamp = batch, reward.tasker, stamp
    actions.link, actions.runtime = reward, runtime
    actions.item_scene = lambda scene, *args, **kwargs: scene
    clock = [0.0]
    monkeypatch.setattr(s, 'time', NS(monotonic=lambda: clock[0]))
    def pause(*args):
        clock[0] += .5
        runtime.advance()
    monkeypatch.setattr(a, '_poll_pause', pause)
    route = route_for([1, 4])
    with journal.locked():
        result = actions.choose_reward('link_reward', route, route.nodes[1], 'owned-link')
    assert result['following_scene'].kind == following
    if following == 'item_reward':
        assert tuple(result['following_scene'].payload['item_result_signature']) == new
    assert runtime.reads == 2 and not runtime.inputs
    assert sum(name == 'LabyLinkResultCloseOnce' for name, _ in calls) == 1
    assert tuple(result['closed_reward_signature']) == old


def test_attempt_completion_is_not_displayed_as_confirmed_clears(tmp_path):
    from pathlib import Path
    from pcr_maa.labyrinth.batch import Batch
    from pcr_maa.labyrinth.config import Settings, Goal
    from pcr_maa.preset_execution import Journal
    from test_labyrinth_outer_coverage import TerminalPort
    port = TerminalPort(10)
    result = Batch(Settings((Goal(1, 2, 'attempts'),)), port, Journal(tmp_path), port.binding).run()
    assert result['batch_complete'] and result['completed'] == {1: 2} and port.retires == 2
    text = (Path(__file__).resolve().parents[1] / 'gui/MFAAvalonia/MFAAvalonia/Pcr/PcrLabyrinthOwner.cs').read_text(encoding='utf-8')
    assert '（已确认通关 {done}/{total} 次）' not in text
    assert '目标通关次数已全部完成。' not in text
    assert '（目标已完成 {done}/{total} 次）' in text
    assert '目标次数已全部完成。' in text


@pytest.mark.parametrize('source,page', [
    ('clear_congratulations','clear_character_summary'),
    ('clear_reward_animation','score_result'),
    ('clear_chest_animation','chest'),
])
def test_unsent_recognized_final_animation_uses_pure_existing_successor(tmp_path,monkeypatch,source,page):
    route=route_for([1,8]);runner,batch,runtime,_,_=make(tmp_path,route,[('unknown',{}),(page,{})])
    runner.cursor=a.RunCursor(index=1,final_boss_won=True,clear_observed=True,final_settlement_phase='chest_sequence')
    runner.route=route;clock=[0.];attempts=[]
    monkeypatch.setattr(s,'time',NS(monotonic=lambda:clock[0]))
    def pause(*args):clock[0]+=.5;runtime.advance()
    monkeypatch.setattr(a,'_poll_pause',pause)
    def unsent(scene,action,**kwargs):
        attempts.append(action)
        if len(attempts)>1:pytest.fail('pure recovery dispatched another animation input')
        raise native.NativeInputNotSent(dict(input_execution='not_sent',native_evidence=dict(entry='LabySceneActionOnce',finished=True,recognition_failed=True,action_started=False)))
    runtime.perform=unsent
    following=runner._do(batch,route,s.Scene(source,receipt(),{}),'advance_final_animation',{page})
    assert following.kind==page and runner.last_scene is following
    assert runner.cursor.final_settlement_phase=='chest_sequence' and attempts==['advance_final_animation']
    assert batch.journal.events()[-1]['result']['input_execution']=='not_sent'
    assert batch.journal.events()[-1]['result']['input_action_verified'] is False


def test_actual121_chest_recovers_unsent_animation_without_native_input(tmp_path,monkeypatch,offline):
    from pathlib import Path
    from pcr_maa.replay import SavedImages
    screen=Path(__file__).resolve().parents[1]/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/1737c7893b05448e9a57bc8baa9788c6/acceptance-ending/screen.png'
    if not screen.is_file():pytest.skip('private121 frame excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[native.read_image(screen)]]);resource=native.load_resource();owner=native.bind(resource,controller)
    try:
        runtime=s.MaaSceneRuntime(owner);route=route_for([1,8]);(tmp_path/'batch').mkdir();runner,batch,_,_,_=make(tmp_path/'batch',route,[])
        runner.runtime=runtime;runner.route=route;runner.cursor=a.RunCursor(index=1,final_boss_won=True,clear_observed=True,final_settlement_phase='chest_sequence')
        attempts=[]
        def unsent(scene,action,**kwargs):
            attempts.append(action)
            raise native.NativeInputNotSent(dict(input_execution='not_sent',native_evidence=dict(entry='LabySceneActionOnce',finished=True,recognition_failed=True,action_started=False)))
        runtime.perform=unsent
        following=runner._do(batch,route,s.Scene('clear_chest_animation',receipt(),{}),'advance_final_animation',{'chest'})
        assert following.kind=='chest' and runner.last_scene is following
        assert attempts==['advance_final_animation'] and controller.clicks==[] and controller.swipes==[]
        assert runner.cursor.final_settlement_phase=='chest_sequence'
    finally:assert native.wait_job(owner.post_stop(),seconds=10).succeeded


@pytest.mark.parametrize('uncertain',[False,True])
def test_final_animation_unchanged_or_unknown_input_never_replays(tmp_path,uncertain):
    route=route_for([1,8]);runner,batch,runtime,_,_=make(tmp_path,route,[('clear_chest_animation',{})])
    runner.cursor=a.RunCursor(index=1,final_boss_won=True,clear_observed=True,final_settlement_phase='chest_sequence');runner.route=route;attempts=[]
    def failed(scene,action,**kwargs):
        attempts.append(action)
        if uncertain:raise RuntimeError('Native input outcome unknown; no retry')
        raise native.NativeInputNotSent(dict(input_execution='not_sent',native_evidence=dict(entry='LabySceneActionOnce',finished=True,recognition_failed=True,action_started=False)))
    runtime.perform=failed
    from pcr_maa.labyrinth.batch import BatchStopped
    expected=RuntimeError if uncertain else BatchStopped
    with pytest.raises(expected) as raised:runner._do(batch,route,s.Scene('clear_chest_animation',receipt(),{}),'advance_final_animation',{'chest','clear_chest_animation'})
    assert type(raised.value) is expected
    assert attempts==['advance_final_animation']
    assert runtime.reads==(0 if uncertain else 1)
    assert batch.journal.events()[-1]['state']==('in_flight' if uncertain else 'settled')
