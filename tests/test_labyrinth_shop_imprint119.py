"""Run119: short shop background is not a completed imprint join."""
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from pcr_maa import native
from pcr_maa.labyrinth import shop_execution as shops,rewards,scene as scenes,autoplay
from pcr_maa.labyrinth.character_recognition import MaaCharacters
from pcr_maa.replay import SavedImages
from test_labyrinth_reward_shop_upstream import harness
from test_labyrinth_autoplay import Runtime,receipt
from test_labyrinth_character_port import offline

SCREEN=Path(__file__).resolve().parents[1]/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/7552bebd3f994c30838a689a55e80ab1/acceptance-ending/screen.png'

@pytest.mark.parametrize('choice',[False,True])
def test_imprint_initial_and_between_join_background_waits_without_old_purchase_replay(monkeypatch,choice):
    batch,stamp=harness();clock=[0.]
    monkeypatch.setattr(scenes,'time',NS(monotonic=lambda:clock[0]))
    pages=[('shop',{}),('free_role_reward',{}) if choice else ('joined',{'role':'1'}),
           ('joined',{'role':'1'}),('shop',{}),('joined',{'role':'2'}),('shop',{})] if choice else [
           ('shop',{}),('joined',{'role':'1'}),('shop',{})]
    runtime=Runtime(pages);runtime.tasker=NS()
    def pause(*args):clock[0]+=.5;runtime.advance()
    monkeypatch.setattr(autoplay,'_poll_pause',pause)
    chars=NS(read=lambda *a,**kw:(dict(roles_joined=[runtime.pages[runtime.i][1]['role']]),receipt()),
             probe=lambda *a:dict(roles_joined=[runtime.pages[runtime.i][1].get('role')]))
    if choice:
        class Reward:
            def __init__(self,*a,**kw):pass
            def choose_free(self,*a,**kw):
                runtime.advance()
                return dict(roles_pending=['1','2'],following_scene=runtime.observe())
        monkeypatch.setattr(rewards,'MaaRoleReward',Reward)
    shop=shops.MaaShop.__new__(shops.MaaShop)
    shop.scene,shop.tasker,shop.stamp,shop.timings=runtime,runtime.tasker,stamp,NS()
    assert shop._imprint_following(batch,119,'new-imprint',[],chars,dict(choice_imprint=choice,role_class=None))==(['1','2'] if choice else ['1'])
    assert runtime.inputs==['close_reward']*(2 if choice else 1)
    assert all(x['operation']=='shop_imprint_close_reward' for x in batch.journal.events())
    assert clock[0]>.0

def test_shop_without_any_join_expires_original_budget_and_has_no_input(monkeypatch):
    batch,stamp=harness();clock=[0.]
    monkeypatch.setattr(scenes,'time',NS(monotonic=lambda:clock[0]))
    monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:clock.__setitem__(0,clock[0]+.5))
    runtime=Runtime([('shop',{})]);runtime.tasker=NS()
    shop=shops.MaaShop.__new__(shops.MaaShop)
    shop.scene,shop.tasker,shop.stamp,shop.timings=runtime,runtime.tasker,stamp,NS()
    with pytest.raises(shops.BatchStopped,match='^shop_imprint_result_unknown$'):
        shop._imprint_following(batch,119,'new-imprint',[],NS(),dict(choice_imprint=False))
    assert clock[0]==30 and runtime.inputs==[] and batch.journal.events()==[]

def test_actual119_joined_over_shop_native_scene_and_identity(tmp_path,monkeypatch,offline):
    if not SCREEN.is_file():pytest.skip('private119 evidence excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[native.read_image(SCREEN)]])
    resource=native.load_resource();owner=native.bind(resource,controller)
    try:
        runtime=scenes.MaaSceneRuntime(owner)
        observed=runtime.observe(timeout=3,probe_kinds={'shop','joined','free_role_reward'})
        assert observed.kind=='joined'
        characters=MaaCharacters(owner)
        value,read=characters.read(NS(checkpoint=lambda:None),'joined',stable=False)
        assert len(value['roles_joined'])==1 and read['native_ok']
        from pcr_maa.labyrinth.roles import load_optimizer
        profile=load_optimizer().profiles[value['roles_joined'][0]]
        assert profile.data['roleClass']=='增幅者'
        assert controller.clicks==[] and controller.swipes==[]
    finally:assert native.wait_job(owner.post_stop(),seconds=10).succeeded
