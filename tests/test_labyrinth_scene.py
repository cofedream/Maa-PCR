"""Public scene runtime: official saved frames, no device or network."""
from pathlib import Path
import json,socket,sys,time
import cv2,numpy as np,pytest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from pcr_maa import native
from pcr_maa.replay import SavedImages
from pcr_maa.labyrinth.scene import Scene,MaaSceneRuntime,classify_scene,perform_scene_action
from pcr_maa.labyrinth.batch import BatchStopped
U=ROOT.parent/'upstream/snapshot/android/app/src/test/resources'
SHOP=U/'shop/live_shop_20260909.jpg';BOSS=U/'labyrinth/boss-win-summary-20260911.png'
SUMMARY=U/'labyrinth/run-result-next-button-20260920.jpg';CLEAR=U/'labyrinth/run-clear-20260917/t0.5.jpg'
pytestmark=pytest.mark.skipif(not all(p.is_file() for p in (SHOP,BOSS,SUMMARY,CLEAR)),reason='pinned upstream private saved-frame fixtures are not distributed')

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a,**k):raise AssertionError('no network/device')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket,'create_connection',deny)
    import maa.controller
    from maa.toolkit import Toolkit
    monkeypatch.setattr(maa.controller,'AdbController',deny);monkeypatch.setattr(Toolkit,'find_adb_devices',deny)

def frame(path):
    image=native.read_image(path)
    return cv2.resize(image,(1280,720),interpolation=cv2.INTER_AREA)

def persist(path,v):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')

def setup(tmp_path,label,images):
    path=tmp_path/label;native.RUNTIME=path/'native';controller=SavedImages([[im] for im in images]);resource=native.load_resource(ROOT/'assets/resource');tasker=native.bind(resource,controller)
    token=native.RUN_DEADLINE.set(time.monotonic()+60)
    return path,controller,tasker,token

def finish(tasker,token):
    try:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded
    finally:native.RUN_DEADLINE.reset(token)

@pytest.mark.parametrize('shift',[-1,0,1])
def test_actual_win51_next_uses_native_text_inside_button_not_template_chrome(tmp_path,shift):
    captured=ROOT.parent/'candidate-r28-source/.runtime/labyrinth-work/runs/a9a69d65a200428fa46012b68d8c781f/acceptance-ending/screen.png'
    reward=ROOT.parent/'evidence/opening-sample/first-role-reward-select-before.png'
    if not captured.is_file() or not reward.is_file():pytest.skip('private run51 frame excluded from release')
    image=frame(captured)
    if shift:image=cv2.warpAffine(image,np.float32([[1,0,shift],[0,1,shift]]),(1280,720))
    path,c,t,token=setup(tmp_path,'win51-'+str(shift),[image,frame(reward)])
    try:
        runtime=MaaSceneRuntime(t);scene=runtime.observe()
        assert scene.kind=='battle_victory' and scene.payload['controls']['next']['anchor']=='NEXT'
        clicked=runtime.perform(scene,'settlement_next')
        assert clicked['native_ok'] and len(c.clicks)==1 and not c.swipes
        assert 1065+shift<=c.clicks[0][0]<=1155+shift and 633+shift<=c.clicks[0][1]<=676+shift
        assert runtime.observe().kind=='role_reward'
    finally:finish(t,token)

def test_next_control_prefers_existing_native_text_with_template_fallback():
    from pcr_maa.labyrinth.scene import _controls
    anchors={key:dict(anchor=key) for key in ('NEXT','BATTLE_RESULT_NEXT_BUTTON','BATTLE_RESULT_BOSS_SUMMARY_NEXT_BUTTON','RUN_RESULT_CLOSE_BUTTON')}
    for kind in ('battle_victory','boss_summary','score_result','chest'):
        assert _controls(kind,anchors)['next']['anchor']=='NEXT'
    assert _controls('final_clear',anchors)['next']['anchor']=='RUN_RESULT_CLOSE_BUTTON'
    del anchors['NEXT']
    assert _controls('boss_summary',anchors)['next']['anchor']=='BATTLE_RESULT_BOSS_SUMMARY_NEXT_BUTTON'

def test_official_continuous_scene_shop_boss_and_clear_not_character_result(tmp_path):
    path,controller,tasker,token=setup(tmp_path,'continuous',[frame(p) for p in (SHOP,BOSS,SUMMARY,CLEAR)])
    try:
        runtime=MaaSceneRuntime(tasker)
        kinds=[]
        for i in range(4):
            controller.stage=i;controller.frame=0;scene=runtime.observe(timeout=20);kinds.append(scene.kind)
            persist(path/f'scene-{i}.json',dict(kind=scene.kind,payload=scene.payload,native=scene.native))
        assert kinds==['shop','boss_summary','score_result','final_clear']
        assert not controller.clicks and not controller.swipes
    finally:finish(tasker,token)

def test_action_fresh_scene_guard_stops_wrong_page_and_stop_with_zero_inputs(tmp_path):
    path,controller,tasker,token=setup(tmp_path,'wrong-page',[frame(SHOP),frame(SUMMARY)])
    try:
        runtime=MaaSceneRuntime(tasker);shop=runtime.observe(timeout=20)
        controller.stage=1;controller.frame=0
        with pytest.raises((BatchStopped,RuntimeError)):runtime.perform(shop,'leave_shop')
        assert not controller.clicks and not controller.swipes
        with pytest.raises(BatchStopped,match='^stopped$'):perform_scene_action(tasker,shop,'leave_shop',stopped=lambda:True)
    finally:finish(tasker,token)

def test_same_frame_scene_action_exact_shop_close_once(tmp_path):
    path,controller,tasker,token=setup(tmp_path,'close-shop',[frame(SHOP),frame(SUMMARY)])
    try:
        runtime=MaaSceneRuntime(tasker);scene=runtime.observe(timeout=20);assert scene.kind=='shop'
        receipt=runtime.perform(scene,'leave_shop');persist(path/'input.json',receipt)
        assert len(controller.clicks)==1 and not controller.swipes
        assert 945<=controller.clicks[0][0]<=1235 and 600<=controller.clicks[0][1]<=680
        assert runtime.observe(timeout=20).kind=='score_result'
    finally:finish(tasker,token)

def test_terminal_and_malformed_score_boundaries_do_not_assert_clear(tmp_path):
    assert classify_scene({'RUN_RESULT_LOGO':{'score':1},'RUN_RESULT_CLOSE_BUTTON':{'score':1}})[0]=='score_result'
    assert classify_scene({'LOSE':{'score':1}})[0]=='battle_defeat'
    assert classify_scene({'DATE':{'score':1}})[0]=='date_changed'
    assert classify_scene({'DISCONNECT':{'score':1}})[0]=='disconnected'
    assert classify_scene({})[0]=='unknown'


@pytest.mark.parametrize('shift',[-1,0,1])
def test_actual_run57_start_overlay_remains_read_only_battle(tmp_path,shift,monkeypatch):
    captured=ROOT.parent/'candidate-r28-source/.runtime/labyrinth-work/runs/fd172143ea604fc694891db0fd5bf610/acceptance-ending/screen.png'
    if not captured.exists():pytest.skip('private run57 frame excluded from release')
    image=frame(captured)
    if shift:image=cv2.warpAffine(image,np.float32([[1,0,shift],[0,1,0]]),(1280,720))
    path,controller,tasker,token=setup(tmp_path,'start57-'+str(shift),[image])
    try:
        observed=MaaSceneRuntime(tasker).observe(timeout=20)
        assert observed.kind in ('battle','unknown')
        from types import SimpleNamespace
        from test_labyrinth_upstream_scene_reuse import actions,Batch,Runtime,receipt
        from pcr_maa.labyrinth import autoplay as autoplay_module
        pending=Runtime([observed,Scene('battle_victory',receipt(),{})])
        monkeypatch.setattr(autoplay_module.native,'run',lambda *a,**k:receipt())
        result=actions(pending,Batch(),tasker).start_battle(SimpleNamespace(enter_id=57),SimpleNamespace(block_id=10201,block_type=2))
        assert result['outcome']=='won' and pending.reads==2
        assert not controller.clicks and not controller.swipes
    finally:finish(tasker,token)
