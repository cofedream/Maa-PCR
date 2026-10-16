"""Area-end reward handoff, using synthetic pages and the real scoped classifier."""
from types import SimpleNamespace

import numpy as np
import pytest

from pcr_maa.labyrinth import autoplay, scene
from pcr_maa.labyrinth.batch import BatchStopped
from test_labyrinth_autoplay import make, route_for
from test_labyrinth_recognition_perf_r50 import SceneContext


@pytest.mark.parametrize('reward',[
    'role_reward','free_role_reward','relic_reward','link_reward','item_reward',
])
@pytest.mark.parametrize('next_area',[2,3])
def test_area_end_handles_late_reward_before_committing_next_area(tmp_path,monkeypatch,reward,next_area):
    route=route_for([1,6,1,8],[1,1,2,2])
    pages=[('map',dict(area=1)),('relic_reward',{}),('map',dict(area=1)),
        (reward,{}),('map',dict(area=1)),('unknown',{}),
        ('map',dict(area=next_area)),('boss_challenge',{}),('team',{}),
        ('battle_victory',{}),('final_clear',{}),('home',{})]
    runner,batch,runtime,nav,actions=make(tmp_path,route,pages)
    observe=runtime.observe;clock=[0.0];reward_scopes=[]

    def scoped_observe(timeout=10):
        current=observe(timeout)
        if runtime.i!=3:return current
        # The old wait omitted these anchors, although full recognition found them.
        scope=getattr(runtime,'_scene_probe_kinds',None)
        reward_scopes.append(scope)
        context=SceneContext(scene._RULES[reward][0])
        actual=context.observer.analyze(context,SimpleNamespace(
            image=np.zeros((720,1280,3),np.uint8),
            custom_recognition_param=dict(probe_kinds=scope))).detail
        return scene.Scene(actual['kind'],current.native,actual['payload'])

    def pause(*args):
        clock[0]+=.5
        # A reward modal stays until its own handler acts; time never dismisses it.
        if runtime.i in (2,4,5):runtime.advance()

    runtime.observe=scoped_observe
    monkeypatch.setattr(scene,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    monkeypatch.setattr(autoplay,'_poll_pause',pause)
    if next_area==3:
        with pytest.raises(BatchStopped,match='^area_transition_unknown$'):runner.play(route,batch)
        assert runner.cursor.index==1 and len(nav.previews)==1
    else:
        result=runner.play(route,batch)
        assert result['clear_verified'] and result['confirmed_nodes']==[n.block_id for n in route.nodes]
        assert len(nav.previews)==2
    assert reward_scopes and all(reward in scope for scope in reward_scopes)
    if reward=='item_reward':assert runtime.inputs.count('close_reward')==1
    else:assert actions.calls.count((reward,6))==(2 if reward=='relic_reward' else 1)
    assert 'next_area' not in runtime.inputs
    assert batch.completed=={1:0}  # Synthetic runner success alone never increments a batch.


@pytest.mark.parametrize('kind,reason',[
    ('date_changed','date_changed'),('disconnected','disconnected'),
    ('shop','area_transition_unknown'),('final_clear','area_transition_unknown'),
])
def test_area_wait_keeps_interruptions_and_unowned_pages_blocked(tmp_path,monkeypatch,kind,reason):
    route=route_for([1,6,1,8],[1,1,2,2])
    pages=[('map',dict(area=1)),('relic_reward',{}),('map',dict(area=1)),(kind,{})]
    runner,batch,runtime,nav,_=make(tmp_path,route,pages)
    monkeypatch.setattr(autoplay,'_poll_pause',lambda *args:runtime.advance())
    with pytest.raises(BatchStopped,match='^'+reason+'$'):runner.play(route,batch)
    assert len(nav.previews)==1 and not runtime.inputs and runner.cursor.index==1
