"""Public synthetic filter feedback; no device or private game captures."""
from types import SimpleNamespace as NS
import json
import socket
import time

import numpy as np
import pytest

from pcr_maa.labyrinth import character_recognition as m

_NATIVE_OWNERS=[]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):raise AssertionError('network/device prohibited')
    monkeypatch.setattr(socket.socket,'connect',deny)
    monkeypatch.setattr(socket,'create_connection',deny)
    import maa.controller
    from maa.toolkit import Toolkit
    monkeypatch.setattr(maa.controller,'AdbController',deny)
    monkeypatch.setattr(Toolkit,'find_adb_devices',deny)
    yield
    if _NATIVE_OWNERS:
        import threading
        from pcr_maa import native
        assert threading.current_thread() is threading.main_thread()
        from maa.define import MaaInvalidId
        invalid_id=MaaInvalidId.value;assert invalid_id==0
        for tasker,controller,resource,characters,output in _NATIVE_OWNERS:
            assert native.wait_job(tasker.post_stop(),seconds=10).succeeded
            assert not tasker.running and not tasker.stopping
            assert native.RUN_DEADLINE.get()==float('inf')
            finished=getattr(controller,'capture_finished',None)
            if finished is not None:assert finished.wait(5)
            # Stop can reject new posts until the in-flight custom callback leaves its wrapper.
            deadline=time.monotonic()+5;pause=threading.Event();invalid_posts=0
            while True:
                remaining=deadline-time.monotonic();assert remaining>0
                job=controller.post_screencap()
                if job.job_id!=invalid_id:
                    assert native.wait_job(job,seconds=deadline-time.monotonic()).succeeded
                    break
                invalid_posts+=1;pause.wait(min(.01,remaining))
            (output/'native-release-evidence.json').write_text(json.dumps(dict(
                main_thread=True,tasker_idle=True,invalid_posts=invalid_posts,
                accepted_screencap_job_id=job.job_id,accepted_screencap_succeeded=job.succeeded,
                capture_finished=finished.is_set() if finished is not None else None),indent=2),encoding='utf-8')
        _NATIVE_OWNERS.clear()


def frame(page,selected=('EFFECTIVE_EFFECT',)):
    image=np.full((720,1280,3),240,np.uint8)
    for name,(x,y,w,h) in zip(m.FILTERS,m.FILTER_RECTS):
        if name in selected:
            y+=93 if page=='boss' else 0
            image[y:y+h,x:x+w]=(220,170,80)
    if page=='boss':
        for i,x in enumerate((98,334,571)):
            left,top,width,height=(int(m._round(v*2/3)) for v in (x+20,158,155,42))
            image[top:top+height,left:left+width]=(80,180,230) if i==0 else (240,240,240)
    return image


def context(*,recognition=None,freeze=True,stopping=False):
    calls=[];freezes=[]
    def read(name,image):
        calls.append(name)
        if name in ('LabyDateChangedObserved','LabyDisconnect6002Observed'):return None
        if recognition:return recognition(name,image)
        word=dict(text='暗',box=[580,146,15,12],score=.99)
        return NS(hit=True,raw_detail=dict(filtered=[word],best=word))
    def wait(**kwargs):freezes.append(kwargs);return freeze
    return NS(tasker=NS(stopping=stopping),run_recognition=read,wait_freezes=wait,calls=calls,freezes=freezes)


def analyze(image,page,*,mode='read',ctx=None,recognizer=None,**params):
    recognizer=recognizer or NS(stopped=lambda:False,
        recognize=lambda image,page,**kw:dict(page=page,filter=kw.get('current_filter'),
            cards=[dict(trusted=True)],members=[],selected=[],boss_tab=1,stable=True))
    return m.CharacterRecognition('Test',recognizer).analyze(ctx or context(),
        NS(image=image,custom_recognition_param=json.dumps(dict(mode=mode,page=page,**params))))


@pytest.mark.parametrize('page',['battle','boss'])
def test_calibrated_filter_survives_missing_ocr_glyph_and_neighbor_ripple(page):
    image=frame(page);offset=93 if page=='boss' else 0
    image[131+offset:173+offset,550:625]=(220,170,80)
    def recognition(name,image):
        if name.endswith('FilterEFFECTIVE_EFFECT'):return None
        word=dict(text='暗',box=[580,146+offset,15,12],score=.99)
        return NS(hit=True,raw_detail=dict(filtered=[word],best=word))
    ctx=context(recognition=recognition)
    actual=analyze(image,page,ctx=ctx)
    assert actual.detail['filter']=='EFFECTIVE_EFFECT'
    assert not any('Filter' in name for name in ctx.calls)


@pytest.mark.parametrize('page',['battle','boss'])
@pytest.mark.parametrize('selected',[(),('DARK','EFFECTIVE_EFFECT')])
def test_absent_or_ambiguous_blue_never_picks_the_largest(page,selected):
    actual=analyze(frame(page,selected),page)
    assert actual.detail['filter'] is None


@pytest.mark.parametrize('page',['battle','boss'])
def test_disabled_gray_blue_effective_does_not_hide_the_selected_all_filter(page):
    image=frame(page,('ALL',))
    x,y,w,h=m.FILTER_RECTS[-1];y+=93 if page=='boss' else 0
    image[y:y+h,x:x+w]=(173,158,151)
    assert m._blue(image,(x,y,w,h))==1
    assert analyze(image,page).detail['filter']=='ALL'


@pytest.mark.parametrize('page,expected_y,expected_height',[('battle',93,54),('boss',187,44)])
def test_filter_layout_maps_upstream_edges_before_computing_size(page,expected_y,expected_height):
    boxes=m._filter_rects(page)
    assert all(box[1]==expected_y and box[3]==expected_height for box in boxes)
    assert boxes[3]==(341,expected_y,98,expected_height)
    assert boxes[-1]==(633,expected_y,107,expected_height)


@pytest.mark.parametrize('page',['battle','boss','opening'])
def test_filter_click_keeps_page_and_label_guards_without_scanning_portraits(page):
    def forbidden(*args,**kwargs):raise AssertionError('filter click scanned character portraits')
    ctx=context();recognizer=NS(stopped=lambda:False,recognize=forbidden)
    actual=analyze(frame(page),page,mode='filter',filter='DARK',ctx=ctx,recognizer=recognizer)
    assert actual is not None and list(actual.box)==[580,146,15,12]
    assert sum('Filter' in name for name in ctx.calls)==1


@pytest.mark.parametrize('page',['battle','boss','opening'])
@pytest.mark.parametrize('freezes',[True,False])
def test_local_native_freeze_checks_success_and_never_scans_or_clicks(page,freezes):
    def forbidden(*args,**kwargs):raise AssertionError('freeze scanned portraits')
    ctx=context(freeze=freezes);recognizer=NS(stopped=lambda:False,recognize=forbidden)
    actual=analyze(frame(page),page,mode='freeze',ctx=ctx,recognizer=recognizer,freeze_timeout_ms=100)
    assert (actual is not None)==freezes
    assert len(ctx.freezes)==1
    param=ctx.freezes[0]['wait_freezes_param']
    assert param.target==([45,120,850,55] if page=='opening' else [40,180,750,75] if page=='boss' else [40,85,750,75])
    assert param.timeout==100 and param.time==200 and param.rate_limit==100


@pytest.mark.parametrize('mode',['read','filter','freeze'])
def test_stop_prevents_filter_work(mode):
    ctx=context(stopping=True)
    assert analyze(frame('battle'),'battle',mode=mode,filter='DARK',ctx=ctx,freeze_timeout_ms=1) is None
    assert not ctx.calls and not ctx.freezes


def test_page_guard_prevents_filter_click_and_freeze():
    for mode in ('filter','freeze'):
        ctx=context(recognition=lambda *args:None)
        assert analyze(frame('battle'),'battle',mode=mode,filter='DARK',ctx=ctx,freeze_timeout_ms=1) is None
        assert not ctx.freezes and not any('Filter' in name for name in ctx.calls)


@pytest.mark.parametrize('outcome',['correct','wrong_filter','freeze_timeout','stop'])
def test_filter_navigation_shares_deadline_and_never_resends_click(outcome,monkeypatch):
    from pcr_maa import native
    now=[100.];calls=[];observations=[];stopped=[False]
    monkeypatch.setattr(time,'monotonic',lambda:now[0])
    class Characters:
        def run(self,suffix,params,*,input=False):
            calls.append((suffix,input,native.RUN_DEADLINE.get()))
            assert params['roster']==['1001']
            if input:
                assert suffix=='FilterOnce';now[0]+=1
                return None,dict(native_ok=True)
            assert suffix=='FilterStable' and params['mode']=='freeze';now[0]+=2.5
            if outcome=='freeze_timeout':raise m.BatchStopped('character_readback_unknown')
            if outcome=='stop':stopped[0]=True;raise m.BatchStopped('character_readback_unknown')
            return dict(page='battle',filter_frozen=True),dict(native_ok=True)
        def wait_for(self,batch,page,predicate,**kwargs):
            assert native.RUN_DEADLINE.get()==112 and now[0]==103.5
            assert kwargs['roster']==('1001',)
            for selected in ('DARK',None):assert not predicate(dict(filter=selected))
            if outcome=='wrong_filter':raise m.BatchStopped('character_feedback_unknown')
            value=dict(filter='EFFECTIVE_EFFECT',stable=True)
            assert predicate(value);return value,dict(native_ok=True)
    def checkpoint():
        if stopped[0]:raise m.BatchStopped('stopped')
    batch=NS(checkpoint=checkpoint,transaction=lambda kind,fields,send:send('one-click'),
        observation=lambda value,**kwargs:observations.append(value))
    nav=m.CharacterNavigation(Characters(),lambda **kwargs:kwargs,roster=('1001',))
    previous=native.RUN_DEADLINE.get()
    if outcome=='correct':
        assert nav.move(batch,1,'battle',{},filter='EFFECTIVE_EFFECT')['filter']=='EFFECTIVE_EFFECT'
        assert len(observations)==1
    else:
        with pytest.raises(m.BatchStopped,match='^'+('stopped' if outcome=='stop' else 'character_feedback_unknown')+'$'):
            nav.move(batch,1,'battle',{},filter='EFFECTIVE_EFFECT')
        assert not observations
    assert calls==[('FilterOnce',True,112.),('FilterStable',False,112.)]
    assert native.RUN_DEADLINE.get()==previous


def native_fixture(tmp_path,images,*,slowcap=False):
    from maa.define import LoggingLevelEnum
    from maa.resource import Resource
    from maa.tasker import Tasker
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    Tasker.set_log_dir(tmp_path/'maa');Tasker.set_stdout_level(LoggingLevelEnum.Off)
    bundle=tmp_path/'bundle';(bundle/'pipeline').mkdir(parents=True)
    hit=dict(recognition='DirectHit',action='DoNothing',next=[],pre_delay=0,post_delay=0)
    miss=dict(recognition='ColorMatch',roi=[0,0,1,1],lower=[0,0,0],upper=[0,0,0],count=1,
        action='DoNothing',next=[],pre_delay=0,post_delay=0)
    nodes={name:hit for name in ('LabyTeamTitle','LabyTeamMembersLabel')}
    nodes.update({name:miss for name in ('LabyDateChangedObserved','LabyDisconnect6002Observed')})
    (bundle/'pipeline/test.json').write_text(json.dumps(nodes),encoding='utf-8')
    resource=Resource();assert resource.post_bundle(bundle).wait().succeeded
    class SlowImages(SavedImages):
        def __init__(self,stages):
            import threading
            super().__init__(stages);self.capture_finished=threading.Event();self.capture_finished.set()
        def screencap(self):
            self.capture_finished.clear()
            try:
                if self.frame>=1 and hasattr(self,'freeze_capture_started'):self.freeze_capture_started.set()
                time.sleep(.55)
                return super().screencap()
            finally:self.capture_finished.set()
    controller=(SlowImages if slowcap else SavedImages)([images]);tasker=native.bind(resource,controller)
    recognizer=NS(stopped=lambda:tasker.stopping)
    characters=m.MaaCharacters(tasker,recognizer=recognizer)
    _NATIVE_OWNERS.append((tasker,controller,resource,characters,tmp_path))
    return tasker,controller,characters


@pytest.mark.parametrize('motion',['outside','ripple_then_still','never_still'])
def test_actual_native_freeze_observes_local_animation_and_timeout(tmp_path,monkeypatch,motion):
    from pcr_maa import native
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    rng=np.random.default_rng(2);base=rng.integers(10,245,(720,1280,3),np.uint8);images=[]
    for _ in range(40):
        image=base.copy()
        roi=image[85:160,40:790] if motion!='outside' else image[200:300]
        roi[:]=rng.integers(10,245,roi.shape,np.uint8);images.append(image)
    if motion=='ripple_then_still':images=images[:3]+[base]*10
    tasker,controller,chars=native_fixture(tmp_path,images)
    started=time.monotonic()
    try:
        if motion=='never_still':
            with pytest.raises(m.BatchStopped,match='(character|game_session)_readback_unknown'):
                chars.run('FilterStable',dict(mode='freeze',page='battle'))
        else:
            value,receipt=chars.run('FilterStable',dict(mode='freeze',page='battle'))
            assert receipt['native_ok'] and value['filter_frozen'] is True
            assert controller.frame<10
        assert time.monotonic()-started<4
        assert not controller.clicks and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded


def test_actual_native_freeze_observes_stop_after_native_wait_returns(tmp_path,monkeypatch):
    import threading
    from pcr_maa import native
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    rng=np.random.default_rng(3)
    images=[rng.integers(10,245,(720,1280,3),np.uint8) for _ in range(40)]
    tasker,controller,chars=native_fixture(tmp_path,images)
    timer=threading.Timer(.2,tasker.post_stop);started=time.monotonic();timer.start()
    try:
        with pytest.raises(m.BatchStopped,match='(character|game_session)_readback_unknown'):
            chars.run('FilterStable',dict(mode='freeze',page='battle'))
        assert time.monotonic()-started<2.5+.55+1
        assert not controller.clicks and not controller.swipes
    finally:
        timer.join()
        assert native.wait_job(tasker.post_stop(),seconds=10).succeeded


@pytest.mark.parametrize('motion',['still','ripple_then_still','never_still','stop'])
def test_actual_native_slow_screencap_keeps_one_freeze_history(tmp_path,monkeypatch,motion):
    import threading
    from pcr_maa import native
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    rng=np.random.default_rng(4);base=rng.integers(10,245,(720,1280,3),np.uint8)
    moving=[]
    for _ in range(40):
        image=base.copy();image[85:160,40:790]=rng.integers(10,245,(75,750,3),np.uint8)
        moving.append(image)
    images=[base]*20 if motion=='still' else moving[:2]+[base]*20 if motion=='ripple_then_still' else moving
    tasker,controller,chars=native_fixture(tmp_path,images,slowcap=True)
    stopped_at=[];stopper=None
    if motion=='stop':
        controller.freeze_capture_started=threading.Event()
        def stop_after_wait_capture():
            if controller.freeze_capture_started.wait(5):
                stopped_at.append(time.monotonic());tasker.post_stop()
        stopper=threading.Thread(target=stop_after_wait_capture);stopper.start()
    started=time.monotonic()
    try:
        if motion in ('never_still','stop'):
            with pytest.raises(m.BatchStopped,match='(character|game_session)_readback_unknown'):
                chars.run('FilterStable',dict(mode='freeze',page='battle'))
        else:
            value,receipt=chars.run('FilterStable',dict(mode='freeze',page='battle'))
            assert value['filter_frozen'] and receipt['native_ok']
            assert controller.frame>=3
        # Offline bound includes the initial capture, native timeout, one in-flight capture and scheduling.
        elapsed=time.monotonic()-started
        assert elapsed<.55+2.5+.55+1
        if motion=='stop':
            assert controller.freeze_capture_started.is_set() and stopped_at
            assert time.monotonic()-stopped_at[0]<2.5+.55+1
        assert not controller.clicks and not controller.swipes
        (tmp_path/'slowcap-evidence.json').write_text(json.dumps(dict(motion=motion,
            screencap_delay_seconds=.55,elapsed_seconds=elapsed,capture_count=controller.frame,
            native_freeze_timeout_ms=2500,stop_during_wait_capture=bool(stopped_at),
            stop_to_return_seconds=time.monotonic()-stopped_at[0] if stopped_at else None,
            clicks=controller.clicks,swipes=controller.swipes),indent=2),encoding='utf-8')
    finally:
        if stopper:stopper.join()
        assert native.wait_job(tasker.post_stop(),seconds=10).succeeded
