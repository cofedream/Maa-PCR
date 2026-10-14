"""Same-frame recognition savings must retain the existing input guards."""
from types import SimpleNamespace as NS
import json
import numpy as np
import pytest
from pcr_maa.labyrinth import autoplay, character_recognition as chars, scene
from pcr_maa import native
from pcr_maa.replay import SavedImages
from test_labyrinth_autoplay import route_for
from test_labyrinth_character_port import offline


def reward_callback(*, text='还剩2次', missing=None, interrupt=None, stop=False, stopping=False):
    image=np.zeros((720,1280,3),np.uint8);calls=[]
    word=dict(text=text,box=[500,100,120,40],score=.99)
    remaining=NS(hit=True,raw_detail=dict(filtered=[word],best=word))
    card=dict(slot=0,character_id='1001',name='佩可莉姆',trusted=True,identity_source='maa_name')
    recognizer=NS(stopped=lambda:stop,
        reward_offers=lambda *a,**kw:dict(page='reward',cards=[dict(card)]))
    def read(name,frame):
        assert frame is image
        calls.append(name)
        if name==interrupt:return NS(hit=True)
        if name==missing or name in ('LabyDateChangedObserved','LabyDisconnect6002Observed'):
            return NS(hit=False)
        if name=='LabyRoleRewardRemaining':return remaining
        return NS(hit=True,raw_detail=dict(filtered=[word],best=word))
    context=NS(tasker=NS(stopping=stopping),run_recognition=read)
    return chars.CharacterRecognition('test',recognizer),context,image,calls,remaining


def test_reward_remaining_uses_the_guard_result_once(offline):
    callback,context,image,calls,remaining=reward_callback()
    result=callback.analyze(context,NS(image=image,custom_recognition_param=json.dumps(dict(mode='read',page='reward'))))
    assert result.detail==dict(page='reward',cards=[dict(slot=0,character_id='1001',
        name='佩可莉姆',trusted=True,identity_source='maa_name')],remaining=2)
    assert calls.count('LabyRoleRewardRemaining')==1
    assert remaining.raw_detail['best'] is remaining.raw_detail['filtered'][0]


@pytest.mark.parametrize('kwargs',[
    dict(text='还剩0次'),dict(text='还剩2次额外文字'),
    dict(missing='LabyRoleRewardTitle'),dict(missing='LabyRoleRewardRemaining'),
    dict(interrupt='LabyDateChangedObserved'),dict(interrupt='LabyDisconnect6002Observed'),
    dict(stop=True),dict(stopping=True),
])
def test_reward_guards_still_reject_invalid_or_interrupted_page(kwargs,offline):
    callback,context,image,_,_=reward_callback(**kwargs)
    assert callback.analyze(context,NS(image=image,custom_recognition_param=json.dumps(dict(mode='read',page='reward')))) is None


@pytest.mark.parametrize('params',[
    dict(expected_slot=0,expected_role='other'),dict(expected_roles=['other']),dict(mode='toggle'),
])
def test_reward_parameter_drift_is_rejected(params,offline):
    callback,context,image,_,_=reward_callback()
    args=dict(mode='read',page='reward');args.update(params)
    assert callback.analyze(context,NS(image=image,custom_recognition_param=json.dumps(args))) is None


def test_reward_native_detail_validation_and_next_frame_are_retained(offline):
    callback,context,image,calls,remaining=reward_callback()
    args=NS(image=image,custom_recognition_param=json.dumps(dict(mode='read',page='reward')))
    assert callback.analyze(context,args).detail['remaining']==2
    remaining.raw_detail['best']=dict(text='还剩3次',box=[500,100,120,40],score=.99)
    assert callback.analyze(context,args) is None
    assert calls.count('LabyRoleRewardRemaining')==2


class SceneContext:
    def __init__(self,keys):
        self.keys=set(keys);self.calls=[];self.scopes=[];self.tasker=NS(stopping=False)
        self.texts={'AREA':'1','MOVE_DESCRIPTION':'将移动到事件格子。'}
        self.observer=scene.SceneRecognition()
        # Include optional legacy anchors without altering production resources.
        self.observer.specs.update({key:dict(rois=[[0,0,1280,720]])
            for terms in scene._RULES.values() for group in terms for key in group
            if key not in scene._OCR and key not in self.observer.specs})

    def run_recognition(self,name,image,override=None):
        self.calls.append(name)
        if name==scene.NODE:
            params=(override or {}).get(name,{}).get('custom_recognition_param',{})
            self.scopes.append(params)
            result=self.observer.analyze(self,NS(image=image,custom_recognition_param=params))
            return NS(hit=result.box is not None,raw_detail=result.detail)
        if name.startswith('LabySceneTemplate_'):key=name[len('LabySceneTemplate_'):].rsplit('_',1)[0]
        elif name.startswith('LabySceneWord_'):key=name[len('LabySceneWord_'):]
        else:return None
        if key not in self.keys:return None
        text=self.texts.get(key,'test')
        return NS(hit=True,reco_id=1,raw_detail=dict(filtered=[dict(text=text,box=[40,40,90,30],score=.99)]))


def target_callback():
    route=route_for([1,2]);columns=[[dict(block_type=2,active=True,box=[400,400,100,100])]]
    pixels=NS(collect=lambda frame:columns)
    callback=autoplay.RouteTargetRecognition(pixels)
    callback.route=route;callback.current=route.nodes[0]
    return callback


def test_map_target_short_circuits_rules_without_dropping_page_guards(offline):
    image=np.zeros((720,1280,3),np.uint8)
    keys=set(scene._RULES['map'][0])|{'AREA'}
    baseline=SceneContext(keys)
    assert baseline.observer.analyze(baseline,NS(image=image,custom_recognition_param={})).detail['kind']=='map'
    context=SceneContext(keys)
    result=target_callback().analyze(context,NS(image=image,custom_recognition_param=json.dumps(dict(mode='read'))))
    assert result.detail['status']=='target_observed'
    assert context.scopes==[dict(probe_kinds=sorted(scene._RULES))]
    assert len(context.calls)<len(baseline.calls)
    assert 'LabySceneWord_DATE' in context.calls and 'LabySceneWord_DISCONNECT' in context.calls


@pytest.mark.parametrize('kind',sorted(scene._RULES)+['date_changed','disconnected','unknown'])
def test_each_page_over_map_matches_full_classification_and_blocks_preview(kind,offline):
    image=np.zeros((720,1280,3),np.uint8)
    keys=set(scene._RULES['map'][0])|{'AREA'}
    if kind in scene._RULES:keys.update(scene._RULES[kind][0])
    elif kind=='unknown':keys=set()
    else:keys.add({'date_changed':'DATE','disconnected':'DISCONNECT'}[kind])
    baseline=SceneContext(keys)
    before=baseline.observer.analyze(baseline,NS(image=image,custom_recognition_param={})).detail
    context=SceneContext(keys);callback=target_callback()
    if before['kind']!='map':callback.pixels.collect=lambda frame:pytest.fail('non-map must not collect a click target')
    result=callback.analyze(context,NS(image=image,custom_recognition_param=json.dumps(dict(mode='preview'))))
    if before['kind']=='map':assert result.detail['status']=='target_observed' and result.box is not None
    else:
        assert result.box is None and result.detail['status']=='scene_changed'
        assert result.detail['scene']['kind']==before['kind']
        assert result.detail['scene']['payload']['score']==before['payload']['score']
        assert result.detail['frame_pixel_sha256']==before['payload']['frame_pixel_sha256']


def test_preview_rechecks_new_frame_overlay_and_area_before_any_input(offline):
    image=np.zeros((720,1280,3),np.uint8)
    keys=set(scene._RULES['map'][0])|{'AREA'};context=SceneContext(keys);callback=target_callback()
    args=NS(image=image,custom_recognition_param=json.dumps(dict(mode='read')))
    assert callback.analyze(context,args).detail['status']=='target_observed'
    callback.pixels.collect=lambda frame:pytest.fail('stale target must not be reused')
    context.keys.update(scene._RULES['joined'][0]);args.image=np.ones_like(image)
    args.custom_recognition_param=json.dumps(dict(mode='preview'))
    result=callback.analyze(context,args)
    assert result.box is None and result.detail['scene']['kind']=='joined'
    context.keys=keys;context.texts['AREA']='2'
    result=callback.analyze(context,args)
    assert result.box is None and result.detail['scene']['payload']['area']==2
    assert len(context.scopes)==3


def test_changed_page_read_preserves_full_scene_and_optional_controls(offline):
    image=np.zeros((720,1280,3),np.uint8)
    keys=set(scene._RULES['map'][0])|set(scene._RULES['joined'][0])|{'AREA','ITEM_CLOSE'}
    baseline=SceneContext(keys)
    full=baseline.observer.analyze(baseline,NS(image=image,custom_recognition_param={})).detail
    context=SceneContext(keys)
    result=target_callback().analyze(context,NS(image=image,custom_recognition_param=json.dumps(dict(mode='read'))))
    assert result.detail['status']=='scene_changed' and result.detail['scene']==full
    assert result.detail['scene']['payload']['controls']['close']['anchor']=='ITEM_CLOSE'
    assert context.scopes==[dict(probe_kinds=sorted(scene._RULES)),{}]


@pytest.mark.parametrize('phase',['before','tasker','after'])
def test_target_stop_never_returns_click_box(phase,offline):
    image=np.zeros((720,1280,3),np.uint8)
    context=SceneContext(set(scene._RULES['map'][0])|{'AREA'});callback=target_callback()
    if phase=='before':callback.stopped=lambda:True
    elif phase=='tasker':context.tasker.stopping=True
    else:
        collect=callback.pixels.collect
        def collect_then_stop(frame):
            columns=collect(frame);context.tasker.stopping=True;return columns
        callback.pixels.collect=collect_then_stop
    assert callback.analyze(context,NS(image=image,custom_recognition_param=json.dumps(dict(mode='preview')))) is None


def test_native_target_scope_is_local_and_retains_the_observation_receipt(tmp_path,monkeypatch,offline):
    scopes=[]
    class SceneSpy(scene.SceneRecognition):
        def analyze(self,context,argv):
            scopes.append(json.loads(argv.custom_recognition_param or '{}'))
            return self.AnalyzeResult([0,0,1280,720],dict(kind='map',payload=dict(area=1)))
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    resource=native.load_resource();assert resource.override_pipeline(scene.scene_pipeline())
    assert resource.register_custom_recognition(scene.NAME,SceneSpy())
    controller=SavedImages([[np.zeros((720,1280,3),np.uint8)]])
    tasker=native.bind(resource,controller)
    try:
        route=route_for([1,2]);callback=target_callback()
        navigator=autoplay.MaaNodeNavigator(tasker,pixels=callback.pixels)
        original=resource.get_node_data(scene.NODE)
        detail,receipt=navigator.locate(route,route.nodes[0])
        assert detail['status']=='target_observed'
        assert receipt['native_ok'] and receipt['tasker_inited']
        assert receipt['nodes'][0]['name']==autoplay._TARGET
        assert autoplay._detail(receipt['nodes'][0]['recognition'])==detail
        assert scopes==[dict(probe_kinds=sorted(scene._RULES))]
        assert resource.get_node_data(scene.NODE)==original
        assert not controller.clicks and not controller.swipes
    finally:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded
