"""Scoped action guards retain the existing requested physical control."""
from pathlib import Path
from types import SimpleNamespace as NS
import json
import numpy as np
import pytest
from pcr_maa import native
from pcr_maa.labyrinth import scene as s
from pcr_maa.replay import SavedImages
from test_labyrinth_character_port import offline

ACTIONS=[a for a in s._ACTIONS if a not in
         ('advance_character_animation','advance_final_unknown','advance_final_animation')]

@pytest.mark.parametrize('action',ACTIONS)
def test_every_scoped_action_reads_its_existing_physical_control(action):
    kind=sorted(s._ACTIONS[action][0])[0];logical=s._ACTIONS[action][1]
    keys=set(s._RULES[kind][0])
    if logical=='close':key='CONFIRM' if kind in ('shop_complete','chest') else 'ITEM_CLOSE'
    elif logical=='next':key='RUN_RESULT_CLOSE_BUTTON' if kind=='final_clear' else 'NEXT'
    else:key=logical
    keys.add(key)
    calls=[]
    class Context:
        def run_recognition(self,name,image):
            calls.append(name)
            if name.startswith('LabySceneTemplate_'):anchor=name[len('LabySceneTemplate_'):].rsplit('_',1)[0]
            elif name.startswith('LabySceneWord_'):anchor=name[len('LabySceneWord_'):]
            else:return None
            if anchor not in keys:return None
            return NS(hit=True,reco_id=1,raw_detail={'filtered':[dict(box=[40,40,90,30],score=.99,text='test')]})
    observer=s.SceneRecognition()
    # Model optional legacy anchors without changing production assets.
    observer.specs.update({k:dict(rois=[[0,0,1280,720]]) for k in keys if k not in s._OCR and k not in observer.specs})
    guard=dict(kind=kind,control_logical=logical,control_anchor=key,probe_kinds=sorted(s._RULES))
    result=observer.analyze(Context(),NS(image=np.zeros((720,1280,3),dtype=np.uint8),
                                       custom_recognition_param=json.dumps(guard)))
    assert result.box is not None,(action,result.detail)
    assert key in result.detail['payload']['anchors']
    assert logical in result.detail['payload']['controls']

SCREEN=Path(__file__).resolve().parents[1]/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/4b5f2b33091741ab8e435808026797e6/acceptance-ending/screen.png'

def test_run120_real_exit_confirmation_native_control_is_sent_once(tmp_path,monkeypatch,offline):
    if not SCREEN.is_file():pytest.skip('private run120 frame excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[native.read_image(SCREEN)]])
    resource=native.load_resource();s.install_scene(resource);tasker=native.bind(resource,controller)
    try:
        observed=s.observe_scene(tasker)
        assert observed.kind=='shop_exit_confirmation'
        receipt=s.perform_scene_action(tasker,observed,'confirm_shop_exit')
        assert receipt['native_ok'] and receipt['native_evidence']['action_started']
        assert len(controller.clicks)==1 and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded


@pytest.mark.parametrize('phase',['before_score','chest_sequence'])
def test_owned_final_map_background_waits_for_result_without_input(monkeypatch,phase):
    from test_labyrinth_autoplay import receipt
    from pcr_maa.labyrinth import autoplay
    pages=iter([s.Scene('map',receipt(),dict(area=3)),s.Scene('score_result',receipt(2),{})]);waits=[]
    runtime=NS(tasker=NS(),observe=lambda **kw:next(pages),perform=lambda *a,**kw:pytest.fail('pure observation'))
    monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:waits.append(1))
    fields=dict(source='maa',enter_id=124,block_id=30701,block_type=8,settlement_phase=phase)
    actual=s.MaaSceneRuntime.wait_for(runtime,NS(checkpoint=lambda:None),{'map','score_result'},source='boss_summary',final_fields=fields)
    assert actual.kind=='score_result' and waits==[1]
    assert not hasattr(runtime,'_final_settlement_progress')


def test_ordinary_map_transition_still_returns_without_final_phase(monkeypatch):
    from test_labyrinth_autoplay import receipt
    from pcr_maa.labyrinth import autoplay
    monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:pytest.fail('ordinary map needs no extra wait'))
    runtime=NS(tasker=NS(),observe=lambda **kw:s.Scene('map',receipt(),dict(area=3)))
    assert s.MaaSceneRuntime.wait_for(runtime,NS(checkpoint=lambda:None),{'map'}).kind=='map'


def test_owned_final_map_never_grants_clear_or_extends_original_wait(monkeypatch):
    from test_labyrinth_autoplay import receipt
    from pcr_maa.labyrinth import autoplay
    clock=iter([0,0,0,31]);monkeypatch.setattr(s.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:None)
    runtime=NS(tasker=NS(),observe=lambda **kw:s.Scene('map',receipt(),dict(area=3)),perform=lambda *a,**kw:pytest.fail('no final-map input'))
    fields=dict(source='maa',enter_id=124,block_id=30701,block_type=8,settlement_phase='before_score')
    with pytest.raises(s.BatchStopped,match='^scene_readback_unknown$'):
        s.MaaSceneRuntime.wait_for(runtime,NS(checkpoint=lambda:None),{'map','home'},timeout=30,final_fields=fields)
    assert not hasattr(runtime,'_final_settlement_progress')


def test_run124_actual_summary_native_read_is_score_result_zero_input(tmp_path,monkeypatch,offline):
    screen=Path(__file__).resolve().parents[2]/'candidate-r28-source/.runtime/acceptance-after109/.runtime/labyrinth-work/runs/b2d83350add149bb87b84613abf8cc09/acceptance-ending/screen.png'
    if not screen.is_file():pytest.skip('private run124 frame excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[native.read_image(screen)]]);resource=native.load_resource();tasker=native.bind(resource,controller)
    try:
        actual=s.MaaSceneRuntime(tasker).observe(timeout=10)
        assert actual.kind=='score_result' and actual.payload['anchors']['RUN_RESULT_LOGO']['score']>=.92
        assert actual.payload['controls']['next']['anchor']=='NEXT'
        assert actual.payload['clear_verified'] is False and not controller.clicks and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded


@pytest.mark.parametrize('kind,body,key',[
    ('battle_defeat',{'DEFEAT'},'RETRY'),
    ('normal_challenge',{'BATTLE_CHALLENGE_BUTTON','NORMAL'},'BATTLE_CHALLENGE_VIEW_MAP_BUTTON'),
    ('ex_challenge',{'BATTLE_CHALLENGE_BUTTON','EXTREME'},'BATTLE_CHALLENGE_VIEW_MAP_BUTTON'),
    ('boss_challenge',{'BATTLE_CHALLENGE_BUTTON','BOSS'},'BATTLE_CHALLENGE_VIEW_MAP_BUTTON'),
    ('shop_exit_confirmation',{'SHOP_TITLE','SHOP_EXIT_CONFIRMATION_TITLE'},'SHOP_EXIT_CONFIRM_BUTTON'),
    ('team',{'TEAM_TITLE','BOSS_MEMBER','BOSS_TAB1','BOSS_TAB2','BOSS_TAB3'},'START_WORD'),
])
def test_scoped_page_primary_read_exposes_existing_optional_control(kind,body,key):
    keys=body|{key}
    class Context:
        def run_recognition(self,name,image):
            if name.startswith('LabySceneTemplate_'):anchor=name[len('LabySceneTemplate_'):].rsplit('_',1)[0]
            elif name.startswith('LabySceneWord_'):anchor=name[len('LabySceneWord_'):]
            else:return None
            if anchor not in keys:return None
            return NS(hit=True,reco_id=1,raw_detail={'filtered':[dict(box=[40,40,90,30],score=.99,text='队伍1' if anchor=='BOSS_MEMBER' else 'test')]})
    actual=s.SceneRecognition().analyze(Context(),NS(image=np.zeros((720,1280,3),np.uint8),custom_recognition_param=dict(probe_kinds=[kind]))).detail
    assert actual['kind']==kind
    assert key in actual['payload']['scanned_anchor_keys']
    assert key in actual['payload']['controls']
    assert actual['payload']['controls'][key]['anchor']==key
    assert actual['payload']['recognition_scope']=='owned_transition'


def test_scoped_defeat_missing_retry_never_invents_control():
    class Context:
        def run_recognition(self,name,image):
            if name!='LabySceneWord_DEFEAT':return None
            return NS(hit=True,reco_id=1,raw_detail={'filtered':[dict(box=[40,40,90,30],score=.99,text='战斗失败')]})
    actual=s.SceneRecognition().analyze(Context(),NS(image=np.zeros((720,1280,3),np.uint8),custom_recognition_param=dict(probe_kinds=['battle_defeat']))).detail
    assert actual['kind']=='battle_defeat' and 'RETRY' not in actual['payload']['controls']


def test_run126_actual_item_native_read_zero_input(tmp_path,monkeypatch,offline):
    screen=Path(__file__).resolve().parents[2]/'candidate-r28-source/.runtime/acceptance-after109/.runtime/labyrinth-work/runs/ed55721951944fb1bddbd3dabfd9daba/acceptance-ending/screen.png'
    if not screen.is_file():pytest.skip('private run126 frame excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[native.read_image(screen)]]);resource=native.load_resource();tasker=native.bind(resource,controller)
    try:
        actual=s.MaaSceneRuntime(tasker).observe(timeout=10,probe_kinds=['item_reward','event','joined','map'])
        assert actual.kind=='item_reward' and 'close' in actual.payload['controls']
        assert actual.payload['clear_verified'] is False and not controller.clicks and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded


@pytest.mark.parametrize('kind',['team','normal_challenge','ex_challenge','boss_challenge'])
def test_owned_retry_map_background_waits_for_actual_battle_page(monkeypatch,kind):
    from test_labyrinth_autoplay import receipt
    from pcr_maa.labyrinth import autoplay
    pages=iter([s.Scene('map',receipt(),dict(area=3)),s.Scene(kind,receipt(2),{})]);waits=[]
    runtime=NS(tasker=NS(),observe=lambda **kw:next(pages),perform=lambda *a,**kw:pytest.fail('retry readback is pure observation'))
    runner=autoplay.AutoplayRunner.__new__(autoplay.AutoplayRunner);runner.runtime=runtime
    runner.actions=NS(enter_id=127);runner.cursor=autoplay.RunCursor()
    monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:waits.append(1))
    actual=runner._action_transition_readback(NS(checkpoint=lambda:None),s.Scene('battle_defeat',receipt(),{}),{'team','normal_challenge','ex_challenge','boss_challenge'})
    assert actual.kind==kind and waits==[1]
    assert not hasattr(runner.cursor,'clear_verified')


def test_owned_retry_map_never_becomes_success_or_extends_deadline(monkeypatch):
    from test_labyrinth_autoplay import receipt
    from pcr_maa.labyrinth import autoplay
    clock=iter([0,0,0,31]);monkeypatch.setattr(s.time,'monotonic',lambda:next(clock))
    waits=[];monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:waits.append(1))
    runner=autoplay.AutoplayRunner.__new__(autoplay.AutoplayRunner)
    runner.runtime=NS(tasker=NS(),observe=lambda **kw:s.Scene('map',receipt(),dict(area=3)),perform=lambda *a,**kw:pytest.fail('no input'))
    runner.actions=NS(enter_id=127);runner.cursor=autoplay.RunCursor()
    with pytest.raises(s.BatchStopped,match='^autoplay_readback_unknown$'):
        runner._action_transition_readback(NS(checkpoint=lambda:None),s.Scene('battle_defeat',receipt(),{}),{'team','normal_challenge','ex_challenge','boss_challenge'})
    assert waits==[1]


@pytest.mark.parametrize('source,after',[('battle_defeat','item_reward'),('normal_challenge','map')])
def test_retry_background_exception_keeps_unexpected_pages_fail_closed(monkeypatch,source,after):
    from test_labyrinth_autoplay import receipt
    from pcr_maa.labyrinth import autoplay
    monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:pytest.fail('unexpected page must stop'))
    runner=autoplay.AutoplayRunner.__new__(autoplay.AutoplayRunner)
    runner.runtime=NS(tasker=NS(),observe=lambda **kw:s.Scene(after,receipt(),dict(area=3)))
    runner.actions=NS(enter_id=127);runner.cursor=autoplay.RunCursor()
    with pytest.raises(s.BatchStopped,match='^autoplay_readback_unknown$'):
        runner._action_transition_readback(NS(checkpoint=lambda:None),s.Scene(source,receipt(),{}),{'team','normal_challenge','ex_challenge','boss_challenge'}- {source})


@pytest.mark.parametrize('page',['defeat-read/200020819-50312b2a8e96.png','acceptance-ending/screen.png'])
def test_run127_actual_retry_pages_native_zero_input(tmp_path,monkeypatch,offline,page):
    screen=Path(__file__).resolve().parents[1]/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/c53f27e9122649578911da3f1f5f607a'/page
    if not screen.is_file():pytest.skip('private run127 frame excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[native.read_image(screen)]]);resource=native.load_resource();tasker=native.bind(resource,controller)
    try:
        actual=s.MaaSceneRuntime(tasker).observe(timeout=10,probe_kinds=['battle_defeat','team','normal_challenge','ex_challenge','boss_challenge'])
        if page.startswith('defeat'):
            assert actual.kind=='battle_defeat' and actual.payload['controls']['RETRY']['text']=='重新挑战'
        else:assert actual.kind=='ex_challenge' and 'BATTLE_CHALLENGE_BUTTON' in actual.payload['controls']
        assert actual.payload['clear_verified'] is False and not controller.clicks and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded


@pytest.mark.parametrize('node',['LabyOpeningAnyJoinedObserved','LabyOpeningJoinedObserved'])
def test_owned_opening_map_hands_off_immediately_to_original_name_read(monkeypatch,node):
    from test_labyrinth_autoplay import receipt
    from pcr_maa.labyrinth import opening,autoplay
    pages=iter([s.Scene('map',receipt(),dict(area=1)),s.Scene('joined',receipt(2),{})]);waits=[];names=[]
    runtime=NS(tasker=NS(),observe=lambda **kw:next(pages),perform=lambda *a,**kw:pytest.fail('MAP background only observes'))
    wait=s.MaaSceneRuntime.wait_for;runtime.wait_for=lambda *a,**kw:wait(runtime,*a,**kw)
    monkeypatch.setattr(s,'MaaSceneRuntime',lambda *a,**kw:runtime)
    monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:waits.append(1))
    port=opening.MaaOpening(NS(),lambda **kw:kw);port._run=lambda name:names.append(name) or receipt(3)
    actual=port._wait_invitation_joined(NS(checkpoint=lambda:None,stopped=lambda:False),129,dict(operation_id='already-confirmed-invitation'),node)
    assert actual['native_ok'] and waits==[] and names==[node]


def test_owned_opening_map_cannot_supply_names_and_keeps_original3s(monkeypatch):
    from test_labyrinth_autoplay import receipt
    from pcr_maa.labyrinth import opening,autoplay
    calls=[];clock=iter([0,0,0,0]);monkeypatch.setattr(s.time,'monotonic',lambda:next(clock,0))
    runtime=NS(tasker=NS(),observe=lambda **kw:s.Scene('map',receipt(),dict(area=1)),perform=lambda *a,**kw:pytest.fail('no map input'))
    wait=s.MaaSceneRuntime.wait_for;runtime.wait_for=lambda *a,**kw:wait(runtime,*a,**kw)
    monkeypatch.setattr(s,'MaaSceneRuntime',lambda *a,**kw:runtime);monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:pytest.fail('must hand off to original name reader'))
    port=opening.MaaOpening(NS(),lambda **kw:kw)
    def names(node):
        calls.append(node);assert native.RUN_DEADLINE.get()==3
        raise s.BatchStopped('opening_joined_unknown')
    port._run=names
    with pytest.raises(s.BatchStopped,match='^opening_joined_unknown$'):
        port._wait_invitation_joined(NS(checkpoint=lambda:None,stopped=lambda:False),130,dict(operation_id='already-confirmed-invitation'),'LabyOpeningAnyJoinedObserved')
    assert calls==['LabyOpeningAnyJoinedObserved']


@pytest.mark.parametrize('kind',['date_changed','disconnected'])
def test_owned_opening_background_wait_keeps_terminal_pages_fail_closed(monkeypatch,kind):
    from test_labyrinth_autoplay import receipt
    from pcr_maa.labyrinth import opening,autoplay
    runtime=NS(tasker=NS(),observe=lambda **kw:s.Scene(kind,receipt(),{}))
    wait=s.MaaSceneRuntime.wait_for;runtime.wait_for=lambda *a,**kw:wait(runtime,*a,**kw)
    monkeypatch.setattr(s,'MaaSceneRuntime',lambda *a,**kw:runtime);monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:pytest.fail('terminal page stops immediately'))
    port=opening.MaaOpening(NS(),lambda **kw:kw);port._run=lambda name:pytest.fail('terminal cannot supply joined names')
    with pytest.raises(s.BatchStopped,match='^'+kind+'$'):
        port._wait_invitation_joined(NS(checkpoint=lambda:None,stopped=lambda:False),129,dict(operation_id='already-confirmed-invitation'),'LabyOpeningAnyJoinedObserved')


@pytest.mark.parametrize('node',['LabyOpeningAnyJoinedObserved','LabyOpeningJoinedObserved'])
def test_run129_actual_joined_names_in_original3s_zero_input(tmp_path,monkeypatch,offline,node):
    from pcr_maa.labyrinth.opening import MaaOpening,opening_catalog_pipeline
    from pcr_maa.labyrinth.portrait_cache import joined_portrait_readback
    screen=Path(__file__).resolve().parents[1]/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/aa73ebfd5d5c4470a79c7ac7447a53e7/acceptance-ending/screen.png'
    if not screen.is_file():pytest.skip('private129 frame excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native');controller=SavedImages([[native.read_image(screen)]])
    resource=native.load_resource();assert resource.override_pipeline(opening_catalog_pipeline());tasker=native.bind(resource,controller)
    try:
        actual=MaaOpening(tasker,lambda **kw:kw)._wait_invitation_joined(NS(checkpoint=lambda:None,stopped=lambda:False),129,dict(operation_id='offline already-confirmed-invitation'),node)
        _,people=joined_portrait_readback(actual);assert sorted(p.character_id for p in people)==['1059','1075','1351']
        assert not controller.clicks and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded


def test_run130_prior_map_then_actual_joined_original3s_zero_input(tmp_path,monkeypatch,offline):
    from pcr_maa.labyrinth.opening import MaaOpening,opening_catalog_pipeline
    from pcr_maa.labyrinth.portrait_cache import joined_portrait_readback
    root=Path(__file__).resolve().parents[1];source=root.parent/'evidence/opening-sample/close-joined-after.png'
    joined=root/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/d828ee9ba945410f9af4ffcf10db8a22/acceptance-ending/screen.png'
    if not source.is_file() or not joined.is_file():pytest.skip('private prior MAP and actual130 joined frames excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native');controller=SavedImages([[native.read_image(source),native.read_image(joined)]])
    resource=native.load_resource();assert resource.override_pipeline(opening_catalog_pipeline());tasker=native.bind(resource,controller);reads=[];original=s.MaaSceneRuntime.observe
    def record(self,**kw):
        value=original(self,**kw);reads.append(value.kind);return value
    monkeypatch.setattr(s.MaaSceneRuntime,'observe',record)
    from pcr_maa.labyrinth import autoplay
    monkeypatch.setattr(autoplay,'_poll_pause',lambda *a:pytest.fail('MAP source hands off to names'))
    try:
        actual=MaaOpening(tasker,lambda **kw:kw)._wait_invitation_joined(NS(checkpoint=lambda:None,stopped=lambda:False),130,dict(operation_id='offline already-confirmed-invitation'),'LabyOpeningAnyJoinedObserved')
        _,people=joined_portrait_readback(actual);assert sorted(p.character_id for p in people)==['1059','1075','1351']
        assert reads==['map'] and not controller.clicks and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded



def test_opening_map_alone_cannot_supply_native_joined_identities(tmp_path,monkeypatch,offline):
    from pcr_maa.labyrinth.opening import MaaOpening,opening_catalog_pipeline
    root=Path(__file__).resolve().parents[1];screen=root.parent/'evidence/opening-sample/close-joined-after.png'
    if not screen.is_file():pytest.skip('private MAP frame excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native');controller=SavedImages([[native.read_image(screen)]])
    resource=native.load_resource();assert resource.override_pipeline(opening_catalog_pipeline());tasker=native.bind(resource,controller);calls=[];run=native.run
    def record(owner,node,**kw):calls.append(node);return run(owner,node,**kw)
    monkeypatch.setattr(native,'run',record)
    try:
        with pytest.raises((s.BatchStopped,RuntimeError)):
            MaaOpening(tasker,lambda **kw:kw)._wait_invitation_joined(NS(checkpoint=lambda:None,stopped=lambda:False),130,dict(operation_id='offline already-confirmed-invitation'),'LabyOpeningAnyJoinedObserved')
        assert calls==['LabySceneObserved','LabyOpeningAnyJoinedObserved']
        assert not controller.clicks and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded



def _edge131_view(*,clipped=False,position=.9857):
    cards=[dict(slot_id='available_row_'+('2' if clipped else '1')+'_1',box=[82,356 if clipped else 325,130,130],character_id='1068',trusted=True,selected=False)]
    if clipped:cards.insert(0,dict(slot_id='available_row_1_1',box=[82,210,130,130],character_id=None,trusted=False,selected=False))
    return dict(page='boss',filter='EFFECTIVE_EFFECT',boss_tab=1,count=0,members=[],selected=[],cards=cards,signature='clipped' if clipped else 'whole-row',scrollbar=dict(visible=True,can_scroll=True,position=position))


def test_edge131_clipped_row_does_not_restart_stationary_list_observation():
    from pcr_maa.labyrinth.character_recognition import CharacterNavigation
    assert CharacterNavigation.unchanged(_edge131_view(),_edge131_view(clipped=True,position=.989))


@pytest.mark.parametrize('change',['identity','trust','filter','tab','movement'])
def test_edge131_keeps_semantic_changes_and_slow_motion_visible(change):
    import copy
    from pcr_maa.labyrinth.character_recognition import CharacterNavigation
    before=_edge131_view();after=copy.deepcopy(before);after['signature']='changed'
    if change=='identity':after['cards'][0]['character_id']='1067'
    elif change=='trust':after['cards'][0]['trusted']=False
    elif change=='filter':after['filter']='ALL'
    elif change=='tab':after['boss_tab']=2
    else:after['scrollbar']['position']=.950
    assert not CharacterNavigation.unchanged(before,after)


def test_edge131_actual_native_bottom_readbacks_restore_all_after_two_observations(monkeypatch):
    import itertools,copy
    from pcr_maa.labyrinth import character_recognition as cr
    from pcr_maa.labyrinth.encounters import scan_effective
    path=Path(__file__).resolve().parents[1].parent/'evidence/resume-20261008/LIVE131_BOSS_READ_PROGRESS.json'
    if not path.is_file():pytest.skip('private actual131 native observations excluded from public source')
    saved=json.loads(path.read_text(encoding='utf-8'))
    # Actual 7/15 native reads; repeat their already-observed rebound variation.
    first=saved[0]['detail'];second=saved[1]['detail'];third=saved[2]['detail'];short=saved[3]['detail']
    assert len(first['cards'])==7 and len(second['cards'])==len(third['cards'])==15 and len(short['cards'])==7
    assert any(c['character_id']=='1001' and c['trusted'] for c in third['cards'])
    assert not any(c['character_id']=='1001' and c['trusted'] for c in second['cards'])
    reads=itertools.chain((second,third),itertools.cycle((short,third)));calls=[];events=[]
    class Navigation(cr.CharacterNavigation):
        def edge(self,*a,**kw):return copy.deepcopy(first)
        def move(self,*a,filter=None,**kw):
            calls.append(filter or kw['direction'])
            if filter:return dict(first,filter=filter)
            return copy.deepcopy(next(reads))
    monkeypatch.setattr(cr,'CharacterNavigation',Navigation)
    roster=tuple(sorted({c['character_id'] for v in (first,second,third,short) for c in v['cards'] if c['trusted']}))
    chars=NS(read=lambda *a,**kw:(first,{}));batch=NS(observation=lambda v,**kw:events.append(v) or v)
    actual=scan_effective(chars,batch,lambda **kw:kw,131,30701,page='boss',roster=roster)
    assert actual==roster and calls==['down']*4+['ALL']
    assert events[-1]['effective_scan_verified'] and events[-1]['filter_restored']
    assert events[-1]['effective_scan_skipped_unsafe'] is True


def test_edge131_actual_saved_boss_frame_native_read_zero_input(tmp_path,monkeypatch,offline):
    from pcr_maa.labyrinth.character_recognition import MaaCharacters,CharacterNavigation,VIEWPORTS
    root=Path(__file__).resolve().parents[1];screen=root/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/b713373cc5764740826fa5f366c408a8/acceptance-ending/screen.png'
    if not screen.is_file():pytest.skip('private actual131 Boss frame excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native');controller=SavedImages([[native.read_image(screen)]])
    owner=native.bind(native.load_resource(),controller);characters=MaaCharacters(owner)
    batch=NS(checkpoint=lambda:None,stopped=lambda:False)
    try:
        actual,receipt=characters.read(batch,'boss');assert actual['filter']=='EFFECTIVE_EFFECT' and actual['boss_tab']==1 and actual['selected']==[]
        vy,vh=VIEWPORTS['boss'][1],VIEWPORTS['boss'][3]
        complete=[dict(c,slot_id='projected_'+str(i)) for i,c in enumerate(actual['cards']) if vy<=c['box'][1] and c['box'][1]+c['box'][3]<=vy+vh]
        assert complete and len(complete)<len(actual['cards'])
        projected=dict(actual,cards=complete,signature='synthetic clipped-row omission from actual131 saved frame')
        assert CharacterNavigation.unchanged(actual,projected)
        assert not controller.clicks and not controller.swipes
        (tmp_path/'boss131-native-zero-input.json').write_text(json.dumps(dict(scope='actual saved-frame native recognition plus explicitly simulated cropped-row omission; not repaired live successor',observation=actual,projection=projected,native=receipt,actual_inputs=0),ensure_ascii=False,indent=2),encoding='utf-8')
    finally:assert native.wait_job(owner.post_stop(),seconds=10).succeeded



def test_edge131_new_trusted_clipped_identity_is_consumed_before_list_end():
    import copy
    from pcr_maa.labyrinth.character_recognition import CharacterNavigation
    before=_edge131_view(clipped=True);after=copy.deepcopy(before)
    after['signature']='new actual partial identity';after['cards'][0].update(character_id='1067',trusted=True)
    assert not CharacterNavigation.unchanged(before,after)


@pytest.mark.parametrize('pending,expected_active',[
    (['1265','1331'],True), (['1265'],False), ([],True)])
def test_finite_joined_batch_ends_driver_only_after_actual_last_selected_role(monkeypatch,pending,expected_active):
    from pcr_maa.labyrinth import autoplay
    from test_labyrinth_autoplay import receipt
    joined=dict(roles_joined=['1265'],native=receipt())
    actions=autoplay.MaaRunActions.__new__(autoplay.MaaRunActions)
    actions.batch=NS();actions.opening=NS(observe_joined=lambda *a:joined)
    actions.owned=[];actions.role_pending=list(pending);actions.acquisition_active=True;actions.enter_id=133
    route=NS(enter_id=133,nodes=[NS(block_type=8)])
    actions.accept_joined(route,route.nodes[0])
    assert actions.owned==['1265']
    runner=autoplay.AutoplayRunner.__new__(autoplay.AutoplayRunner)
    runner.actions=actions;runner.route=route;runner.cursor=autoplay.RunCursor();runner.runtime=NS()
    def wait(runtime,batch,allowed,**kw):
        assert kw['timeout']==30
        assert (kw['acquisition_fields'] is not None) is expected_active
        return s.Scene('map',receipt(),dict(area=4))
    monkeypatch.setattr(s.MaaSceneRuntime,'wait_for',wait)
    assert runner._action_transition_readback(NS(),s.Scene('joined',receipt(),{}),{'joined','map'}).kind=='map'
    assert actions.acquisition_active is expected_active


@pytest.mark.parametrize('text,disconnected',[
    ('5-1600290',False), ('6002',False), ('1600290',False),
    ('错误代码: 6002',True), ('错误代码：6002',True),
    ('错误代码：6000',False), ('战斗伤害6002',False)])
def test_scene_disconnect_reuses_explicit_error_code_text_not_battle_numbers(text,disconnected):
    import re
    from pcr_maa.labyrinth.session import reconnect_pipeline
    rule=s.scene_pipeline()['LabySceneWord_DISCONNECT']
    assert bool(re.search(rule['expected'],text)) is disconnected
    assert rule['expected']==reconnect_pipeline()['LabyDisconnectCode6002']['expected']
    assert rule['threshold']==.8 and rule['roi']==[320,150,650,240]


@pytest.mark.parametrize('label,screen,disconnected',[
    ('actual134-battle','.runtime/acceptance-after109/.runtime/labyrinth-work/runs/4961d960c2f2481f84e3a585139a45cc/acceptance-ending/screen.png',False),
    ('actual6002','../evidence/current-client-profile-live-01/steps/d709552112934633a0eeee29985f4ffa/screen-frame.png',True)])
def test_actual_battle_and_6002_saved_native_scene_zero_input(label,screen,disconnected,tmp_path,monkeypatch,offline):
    screen=Path(__file__).resolve().parents[1]/screen
    if not screen.is_file():pytest.skip('private game frame excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[native.read_image(screen)]]);resource=native.load_resource();tasker=native.bind(resource,controller)
    try:
        actual=s.MaaSceneRuntime(tasker).observe(timeout=10)
        assert (actual.kind=='disconnected') is disconnected
        assert not controller.clicks and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded



def test_scene_area_reuses_existing_numeric_recognition_mode():
    from pcr_maa.labyrinth.area import area_pipeline
    rule=s.scene_pipeline()['LabySceneWord_AREA']
    assert rule.get('only_rec',False)==area_pipeline()['LabyAreaCurrent']['only_rec']
    assert rule['threshold']==.8 and rule['roi']==[430,42,30,32]


@pytest.mark.parametrize('mode',['scene','existing_area_reader'])
def test_actual_area4_saved_native_scene_zero_input(mode,tmp_path,monkeypatch,offline):
    screen=Path(__file__).resolve().parents[1]/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/ea3a0ba9e5214a3ba364e2a731aca89b/acceptance-ending/screen.png'
    if not screen.is_file():pytest.skip('private game frame excluded from public source')
    if mode=='existing_area_reader':
        from pcr_maa.labyrinth.area import area_pipeline
        original=s.scene_pipeline
        def reused():
            nodes=original()
            nodes['LabySceneWord_AREA']['only_rec']=area_pipeline()['LabyAreaCurrent']['only_rec']
            return nodes
        monkeypatch.setattr(s,'scene_pipeline',reused)
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[native.read_image(screen)]]);resource=native.load_resource();tasker=native.bind(resource,controller)
    try:
        actual=s.MaaSceneRuntime(tasker).observe(timeout=10)
        assert actual.kind=='map' and actual.payload.get('area')==4
        assert not controller.clicks and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded


@pytest.mark.parametrize('area,node',[(1,10101),(2,20101),(3,30101)])
def test_actual_other_area_saved_native_scene_zero_input(area,node,tmp_path,monkeypatch,offline):
    screen=Path(__file__).resolve().parents[1]/('../evidence/full-batch-acceptance-01/map-readbacks/ea3a0ba9e5214a3ba364e2a731aca89b/'+str(node)+'/first-read.png')
    if not screen.is_file():pytest.skip('private game frame excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[native.read_image(screen)]]);resource=native.load_resource();tasker=native.bind(resource,controller)
    try:
        actual=s.MaaSceneRuntime(tasker).observe(timeout=10)
        assert actual.kind=='map' and actual.payload.get('area')==area
        assert not controller.clicks and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded


@pytest.mark.parametrize('portrait_id,confidence,margin,safe',[
    ('1270',.8,.3,True),('1045',.8,.3,True),('1270',.49,.3,False),('1270',.8,.09,False)])
def test_reward_base_name_keeps_actual_portrait_variant_and_existing_gate(portrait_id,confidence,margin,safe):
    from pcr_maa.labyrinth.character_recognition import CharacterRecognizer,IconMatch,load_character_pack
    from pcr_maa.labyrinth.rewards import reward_identity_safe
    names={e['character_id']:e['name'] for e in load_character_pack()[0]}
    matched=IconMatch(portrait_id,names[portrait_id],None,confidence,0,margin,True,())
    matcher=NS(match=lambda *a,**kw:matched)
    # Actual136 native filtered name; the .785751 summer line was excluded at .85.
    words=[dict(box=[241,447,58,36],score=.967236,text='空花')]
    card=CharacterRecognizer(matcher=matcher).reward_offers(np.zeros((720,1280,3),np.uint8),slot=0,name_words={0:words})['cards'][0]
    assert card['slot']==0 and card['character_id']==portrait_id
    assert card['identity_source']==('maa_name' if portrait_id=='1045' else 'portrait')
    assert reward_identity_safe(card) is safe


def test_actual136_joined_native_portrait_zero_input(tmp_path,monkeypatch,offline):
    from pcr_maa.labyrinth.character_recognition import MaaCharacters
    screen=Path(__file__).resolve().parents[1]/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/a3e0cb8f20c74486ad66d8e4c95c5353/acceptance-ending/screen.png'
    if not screen.is_file():pytest.skip('private game frame excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[native.read_image(screen)]]);tasker=native.bind(native.load_resource(),controller)
    try:
        actual,ack=MaaCharacters(tasker).run('Read',dict(mode='read',page='joined',expected_role='1270'))
        assert ack['native_ok'] and actual['roles_joined']==['1270']
        assert not controller.clicks and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded


@pytest.mark.parametrize('case,valid,balance',[
    ('primary',True,4750),('upscaled',True,0),('missing',False,None),
    ('low_score',False,None),('non_number',False,None),('bad_box',False,None)])
def test_balance_native_crop_fallback_keeps_existing_validation(case,valid,balance):
    from pcr_maa.labyrinth import shop_execution as shops
    def read(name,image,override=None):
        if name=='LabyShopRead_Currency':return NS(hit=True,raw_detail={'filtered':[dict(text='持有阿尔法金币',score=.99,box=[607,642,132,18])]})
        if name!='LabyShopRead_Balance':return None
        if override is None:
            if case=='primary':return NS(hit=True,raw_detail={'filtered':[dict(text='4750',score=.99,box=[850,635,70,24])]})
            return NS(hit=False,raw_detail={'filtered':[]})
        assert case!='primary'
        assert image.shape[:2]==(192,460)
        assert override[name]['roi']==[0,0,460,192] and 'threshold' not in override[name]
        if case=='missing':return None
        return NS(hit=True,raw_detail={'filtered':[dict(text='O' if case=='non_number' else '0',score=.79 if case=='low_score' else .96756,box=None if case=='bad_box' else [347,62,53,75])]})
    actual=shops.ShopRecognition().analyze(NS(run_recognition=read),NS(image=np.zeros((720,1280,3),np.uint8),custom_recognition_param='{}'))
    assert (actual.box is not None) is valid
    if valid:assert actual.detail['state']=='shop' and actual.detail['balance']==balance


@pytest.mark.parametrize('blank',[False,True])
def test_actual137_zero_balance_native_shop_zero_input(blank,tmp_path,monkeypatch,offline):
    from pcr_maa.labyrinth import shop_execution as shops
    screen=Path(__file__).resolve().parents[1]/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/05ff7bda3ae9473fb09074f295ae494b/acceptance-ending/screen.png'
    if not screen.is_file():pytest.skip('private game frame excluded from public source')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native');image=native.read_image(screen)
    if blank:image[625:673,830:945]=255 # Negative fixture, never reported as a real game frame.
    controller=SavedImages([[image]]);tasker=native.bind(native.load_resource(),controller)
    try:
        shop=shops.MaaShop(tasker,lambda **fields:fields)
        if blank:
            with pytest.raises(shops.BatchStopped,match='^shop_readback_unknown$'):shop._capture()
        else:
            actual,ack=shop._capture()
            assert ack['native_ok'] and actual['state']=='shop' and actual['balance']==0
            assert not actual['refresh_allowed'] and actual['items'] and all(not i['purchasable'] for i in actual['items'])
        assert not controller.clicks and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded


@pytest.mark.parametrize('kind',['filter','scroll','tab'])
def test_navigation_relays_existing_owned_roster_for_guard_and_feedback(kind):
    from pcr_maa.labyrinth.character_recognition import CharacterNavigation
    roster=('1004','1091','1256');calls=[]
    view=dict(page='boss',filter='ALL',boss_tab=1,signature='same',scrollbar=dict(visible=False,can_scroll=False),selected=list(roster),cards=[],members=[])
    class Characters:
        def run(self,suffix,params,*,input):
            assert params['roster']==list(roster)
            if suffix=='FilterStable':
                assert not input and params['mode']=='freeze'
                return dict(page='boss',filter_frozen=True),dict(native_ok=True)
            assert input
            calls.append(suffix)
            if 'filter' in params:view['filter']=params['filter']
            if 'tab' in params:view['boss_tab']=params['tab']
            return None,dict(native_ok=True)
        def wait_for(self,batch,page,predicate,**kwargs):
            assert kwargs['roster']==roster
            if not predicate(view):assert predicate(view)
            return view,dict(native_ok=True)
    batch=NS(checkpoint=lambda:None,transaction=lambda kind,fields,send:send('offline'),observation=lambda value,**fields:value)
    move={'filter':'FIRE'} if kind=='filter' else {'direction':'down'} if kind=='scroll' else {'tab':3}
    after=CharacterNavigation(Characters(),lambda **fields:fields,roster=roster).move(batch,138,'boss',view,**move)
    assert after['selected']==list(roster) and len(calls)==1


def test_navigation_default_keeps_full_pack_for_opening_free_and_roster():
    from pcr_maa.labyrinth.character_recognition import CharacterNavigation
    view=dict(page='opening',filter='ALL',signature='same',selected=[],cards=[],members=[],scrollbar=dict(visible=False,can_scroll=False))
    class Characters:
        def run(self,suffix,params,*,input):
            assert 'roster' not in params
            return None,dict(native_ok=True)
        def wait_for(self,batch,page,predicate,**kwargs):
            assert 'roster' not in kwargs
            if not predicate(view):assert predicate(view)
            return view,dict(native_ok=True)
    batch=NS(checkpoint=lambda:None,transaction=lambda kind,fields,send:send('offline'),observation=lambda value,**fields:value)
    assert CharacterNavigation(Characters(),lambda **fields:fields).move(batch,138,'opening',view,direction='down')==view


def test_team_edit_keeps_owned_member_scope_across_filter_seek_and_toggle():
    from pcr_maa.labyrinth.team_execution import MaaTeam
    from pcr_maa.labyrinth.roles import load_optimizer
    roster=('1004','1091','1256','1105');selected=['1004','1091','1256'];current_filter='ALL';filtered=False
    def view():
        return dict(page='boss',filter=current_filter,boss_tab=3,signature='same',stable=True,selected=sorted(selected),
            cards=[dict(character_id='1105',trusted=True)] if filtered else [],members=[dict(character_id=r,trusted=True) for r in selected],scrollbar=dict(visible=False,can_scroll=False))
    class Characters:
        def run(self,suffix,params,*,input):
            nonlocal current_filter,filtered
            assert params['roster']==list(roster)
            if suffix=='FilterStable':
                assert not input and params['mode']=='freeze'
                return dict(page='boss',filter_frozen=True),dict(native_ok=True)
            assert input
            if suffix=='FilterOnce':current_filter=params['filter'];filtered=True
            elif suffix=='ToggleOnce':selected.append(params['role_id'])
            else:raise AssertionError(suffix)
            return None,dict(native_ok=True)
        def wait_for(self,batch,page,predicate,**kwargs):
            assert kwargs['roster']==roster and predicate(view())
            return view(),dict(native_ok=True)
        def read(self,batch,page,**kwargs):
            assert kwargs['roster']==roster
            return view(),dict(native_ok=True)
    batch=NS(checkpoint=lambda:None,transaction=lambda kind,fields,send:send('offline'),observation=lambda value,**fields:value)
    team=MaaTeam(None,lambda **fields:fields,roster,optimizer=load_optimizer(),characters=Characters())
    actual,_=team._edit(batch,138,30701,'boss',sorted(roster),view())
    assert actual['selected']==sorted(roster)
