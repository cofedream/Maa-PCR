"""Upstream event OCR/pixel port on official saved images, zero live I/O."""
from pathlib import Path
from types import SimpleNamespace
import sys,json,socket,time
import cv2,numpy as np,pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from pcr_maa import native
from pcr_maa.replay import SavedImages
from pcr_maa.labyrinth.scene import MaaSceneRuntime
from pcr_maa.labyrinth.shop_execution import MaaEvent
from pcr_maa.labyrinth.event_observation import recognize_event,button_fraction,catalogue
from pcr_maa.labyrinth.batch import Batch,Binding,BatchStopped
U=ROOT.parent/'upstream/snapshot/android/app/src/test/resources/labyrinth'
FISH=U/'event-single-choice-fishing-20260911.png';RECRUIT=U/'event-guild-tank-healer-20260913.png';EMPTY=U/'event-no-reward-over-map-20260918.jpg';DEFEAT=U/'event-no-reward-confirm-20260917.png'
MAP=ROOT.parent/'evidence/area-transition-live-01/after.png'
pytestmark=pytest.mark.skipif(not all(p.is_file() for p in (FISH,RECRUIT,EMPTY,DEFEAT,MAP)),reason='private pinned event/maps frames are not distributed')
@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a,**k):raise AssertionError('no network/device')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket,'create_connection',deny)
    import maa.controller
    from maa.toolkit import Toolkit
    monkeypatch.setattr(maa.controller,'AdbController',deny);monkeypatch.setattr(Toolkit,'find_adb_devices',deny)
def frame(p):return cv2.resize(cv2.imdecode(np.fromfile(p,np.uint8),cv2.IMREAD_COLOR),(1280,720),interpolation=cv2.INTER_AREA)
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
class Journal:
    def __init__(self):self.values=[]
    def events(self):return self.values[:]
    def append(self,value):self.values.append(value)
def setup(tmp_path,label,images):
    path=tmp_path/label;native.RUNTIME=path/'native';c=SavedImages([[i] for i in images]);r=native.load_resource(ROOT/'assets/resource');t=native.bind(r,c);token=native.RUN_DEADLINE.set(time.monotonic()+70)
    binding=Binding(1,'offline-saved-image-replay');count=[0]
    def stamp(**v):count[0]+=1;return dict(verified=True,binding=binding.payload(),frame=count[0],**v)
    batch=Batch(SimpleNamespace(goals=(),strategy=None),SimpleNamespace(),Journal(),binding)
    return path,c,t,token,stamp,batch
def finish(t,token):
    try:assert native.wait_job(t.post_stop(),seconds=10).succeeded
    finally:native.RUN_DEADLINE.reset(token)
def test_official_recruit_and_fishing_identity_buttons_no_inputs(tmp_path):
    path,c,t,token,stamp,batch=setup(tmp_path,'event-observed',[frame(RECRUIT),frame(FISH)])
    try:
        runtime=MaaSceneRuntime(t)
        for i,expected in enumerate((1101,1314)):
            c.stage=i;c.frame=0;scene=runtime.observe(timeout=20);save(path/f'scene-{i}.json',dict(kind=scene.kind,payload=scene.payload,native=scene.native))
            assert scene.kind=='event' and scene.payload['event']['event_id']==expected
            again=runtime.observe(timeout=20);assert again.payload['event']['trusted'] is True
            assert all(v['enabled_confidence']>=.35 for v in again.payload['event']['choices'])
        assert not c.clicks and not c.swipes
    finally:finish(t,token)
def test_official_single_event_choice_then_independent_none_reward_and_map(tmp_path):
    path,c,t,token,stamp,batch=setup(tmp_path,'event-action',[frame(FISH),frame(EMPTY),frame(MAP)])
    try:
        adapter=MaaEvent(t,stamp);result=adapter.run(batch,1,2,stacks={},owned=())
        save(path/'journal.json',batch.journal.events());save(path/'result.json',result)
        assert result['page']=='item_reward' and result['inventory_added'] is False and result['stacks']=={}
        assert len(c.clicks)==1
        runtime=adapter.scene;scene=runtime.observe(timeout=20);runtime.perform(scene,'close_reward');after=runtime.observe(timeout=20)
        save(path/'after-close.json',dict(kind=after.kind,payload=after.payload,native=after.native))
        assert after.kind=='map' and len(c.clicks)==2
        with pytest.raises(BatchStopped):adapter.run(batch,1,2,stacks={})
        assert len(c.clicks)==2
    finally:finish(t,token)
def test_wrong_page_stop_defeat_overlay_and_disabled_pixel_never_choose(tmp_path):
    path,c,t,token,stamp,batch=setup(tmp_path,'event-stop',[frame(DEFEAT)])
    try:
        runtime=MaaSceneRuntime(t);scene=runtime.observe(timeout=20);save(path/'defeat.json',dict(kind=scene.kind,payload=scene.payload,native=scene.native));assert scene.kind=='battle_defeat'
        adapter=MaaEvent(t,stamp)
        with pytest.raises(BatchStopped):adapter.run(batch,1,2,stacks={})
        batch.stopped=lambda:True
        with pytest.raises(BatchStopped):adapter.run(batch,1,3,stacks={})
        assert not c.clicks and not batch.journal.events()
        gray=np.full((720,1280,3),(173,101,82),np.uint8);assert button_fraction(gray,[10,10,100,40],enabled=True)==0
    finally:finish(t,token)
def test_catalogue_shared_title_and_numeric_variants_are_not_randomly_collapsed(tmp_path):
    assert recognize_event('这里是公会管理协会！请问要找什么样的伙伴？') is None
    event=next(e for e in catalogue()['events'] if e['id']=='1117')
    text=event['name']+''.join(c['name']+c['description'] for c in event['choices'])
    actual=recognize_event(text);assert actual['event_id']==1117 and actual['text_trusted'] is True
    assert recognize_event('与事件无关的角色主菜单资料') is None
