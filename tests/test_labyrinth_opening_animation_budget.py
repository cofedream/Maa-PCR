"""Three character animations share a bounded budget, including slow fresh guards."""
from types import SimpleNamespace as NS

import pytest

from pcr_maa import native
from pcr_maa.labyrinth import autoplay, opening, scene
from test_labyrinth_autoplay import receipt


@pytest.mark.parametrize('parent', [float('inf'), 10.])
def test_three_opening_animations_finish_with_slow_guards_and_respect_parent(monkeypatch,parent):
    state=NS(now=0.,clicks=0,confirmed=False)
    monkeypatch.setattr(native,'active_now',lambda:state.now)
    def advance(seconds):
        state.now+=seconds
        if state.now>=native.RUN_DEADLINE.get():
            raise RuntimeError('Native task exceeded 90 seconds or inspection deadline')
    runtime=scene.MaaSceneRuntime.__new__(scene.MaaSceneRuntime)
    runtime.tasker=NS()
    runtime.stamp=lambda **fields:fields
    def observe(**kwargs):
        advance(1.5)
        return scene.Scene('unknown' if state.clicks<3 else 'joined',receipt(),{})
    def perform(current,action):
        assert current.kind=='unknown' and action=='advance_character_animation'
        advance(3.9);state.clicks+=1
        return receipt(action=True)
    runtime.observe=observe;runtime.perform=perform
    monkeypatch.setattr(scene,'MaaSceneRuntime',lambda *a,**kw:runtime)
    monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:advance(.5))
    port=opening.MaaOpening(NS(),lambda **fields:fields)
    def joined(batch,page,*,stable,timeout):
        assert page=='joined' and stable is False and timeout==opening.JOINED_CONFIRMATION_TIMEOUT_SECONDS
        advance(1.);state.confirmed=True
        return receipt(),receipt()
    port._characters=NS(read=joined)
    batch=NS(stopped=lambda:False,checkpoint=lambda:None,
             transaction=lambda operation,fields,send:send('owned-invitation'))
    token=native.RUN_DEADLINE.set(parent)
    try:
        if parent==float('inf'):
            assert port._wait_invitation_joined(batch,1,dict(operation_id='invitation'))['native_ok']
            assert state.clicks==3 and state.confirmed and 12<state.now<30
        else:
            with pytest.raises(scene.BatchStopped,match='opening_readback_unknown'):
                port._wait_invitation_joined(batch,1,dict(operation_id='invitation'))
            assert not state.confirmed and state.clicks<3
        assert native.RUN_DEADLINE.get()==parent
    finally:native.RUN_DEADLINE.reset(token)
