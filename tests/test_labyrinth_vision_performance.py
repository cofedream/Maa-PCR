"""R53 same-frame reuse is numerically equivalent and skips only absent map counters."""
from dataclasses import replace
from types import SimpleNamespace as NS
import json
import cv2
import numpy as np
import pytest
from pcr_maa.labyrinth import character_recognition as c,relics as r,scene
from pcr_maa.labyrinth.batch import BatchStopped


@pytest.fixture(scope='module')
def matcher():return c.IconMatcher()


def original_scores(matcher,frame,box,mask,width,**ignored):
    # Frozen pre-R53 formula: compare every candidate, including the crop-bound gradients.
    x,y,w,h=box;top=y+int(c._round(h*20/128));height=max(16,int(c._round(h*80/128)))
    if not c._inside((x,top,w,height),frame.shape):return np.zeros(len(matcher.entries))
    xr,yr,colors,luma,gradient=matcher.expected[width]
    kept=(yr>=mask.min_y)&(yr<=mask.max_y)
    if mask.right:kept&=xr<=.82
    if mask.top_left:kept&=~((xr<=.46)&(yr<=.38))
    if mask.top_right:kept&=~((xr>=.58)&(yr<=.42))
    if mask.bottom_left:kept&=~((xr<=.32)&(yr>=.72))
    if mask.bottom_band:kept&=yr<.74
    if kept.sum()<(8 if width==12 else 20):return np.zeros(len(matcher.entries))
    fx=x+c._round(xr[kept]*(w-1));fy=top+c._round(yr[kept]*(height-1))
    rgb=frame[:,:,::-1].astype(np.int64)
    observed=rgb[fy,fx,:].astype(float)
    color=np.clip(matcher._correlation(observed,colors[:,kept,:]).mean(axis=1),0,None)
    if width==12:return color
    lum=(rgb[:,:,0]*299+rgb[:,:,1]*587+rgb[:,:,2]*114+500)//1000
    actual_lum=lum[fy,fx].astype(float)
    actual_grad=np.abs(lum[fy,np.minimum(fx+1,x+w-1)]-lum[fy,np.maximum(fx-1,x)])+np.abs(
        lum[np.minimum(fy+1,top+height-1),fx]-lum[np.maximum(fy-1,top),fx])
    ls=np.clip(matcher._correlation(actual_lum[:,None],luma[:,kept,None])[:,0],0,None)
    gs=np.clip(matcher._correlation(actual_grad[:,None].astype(float),gradient[:,kept,None])[:,0],0,None)
    return np.clip(color*.55+ls*.25+gs*.20,0,1)


def art_frame(matcher,index=0,box=(106,206,116,116)):
    frame=np.random.default_rng(53).integers(0,256,(720,1280,3),dtype=np.uint8)
    x,y,w,h=box;frame[y:y+h,x:x+w]=cv2.resize(matcher.images[index][...,::-1],(w,h))
    return frame


@pytest.mark.parametrize('mask,attribute,roster',[
    (c.IconMask(),None,None),
    (c.IconMask(right=True,top_left=True,top_right=True,bottom_left=True,bottom_band=True),None,None),
    (c.IconMask(right=True,min_y=.17,max_y=.84),'WATER',['1001','1059','1075']),
    (c.IconMask(min_y=.95,max_y=1),None,None)])
def test_all_scores_order_rivals_variants_and_refine_equal_original(matcher,monkeypatch,mask,attribute,roster):
    box=(106,206,116,116);frame=art_frame(matcher);prepared=dict(frame=frame)
    for width in (12,20):
        actual=matcher._scores(frame,box,mask,width,_prepared=prepared)
        expected=original_scores(matcher,frame,box,mask,width)
        np.testing.assert_array_equal(actual,expected)
        np.testing.assert_array_equal(np.argsort(-actual,kind='stable'),np.argsort(-expected,kind='stable'))
    kwargs=dict(mask=mask,attribute=attribute,roster=roster)
    actual=matcher.match(frame,box,_prepared=prepared,**kwargs)
    initial=replace(actual,trusted=False)
    refined=matcher.refine(frame,box,initial,fallback=(104,204,120,120),_prepared=prepared,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(matcher,'_scores',lambda *a,**kw:original_scores(matcher,*a,**kw))
        assert matcher.match(frame,box,**kwargs)==actual
        assert matcher.refine(frame,box,initial,fallback=(104,204,120,120),**kwargs)==refined
    assert prepared['rgb'].dtype==prepared['lum'].dtype==np.int64


def test_preprocessing_rejects_other_array_even_identical_pixels_and_never_crosses_calls(matcher):
    frame=art_frame(matcher);other=frame.copy();box=(106,206,116,116)
    prepared=dict(frame=frame);first=matcher.match(frame,box,_prepared=prepared)
    rgb,lum=prepared['rgb'],prepared['lum']
    matcher.refine(frame,box,replace(first,trusted=False),_prepared=prepared)
    assert prepared['rgb'] is rgb and prepared['lum'] is lum
    assert matcher.match(other,box)==first
    for call in (lambda:matcher.match(other,box,_prepared=prepared),
                 lambda:matcher.refine(other,box,first,_prepared=prepared),
                 lambda:matcher._scores(other,box,c.IconMask(),20,_prepared=prepared)):
        with pytest.raises(BatchStopped,match='^character_frame_unknown$'):call()
    assert not any(value is prepared for value in vars(matcher).values())



def test_no_visible_samples_and_trusted_refine_leave_preprocessing_lazy(matcher):
    frame=art_frame(matcher);box=(106,206,116,116);prepared=dict(frame=frame)
    for width in (12,20):
        assert not matcher._scores(frame,box,c.IconMask(min_y=.49,max_y=.49),width,_prepared=prepared).any()
    assert set(prepared)=={'frame'}
    trusted=replace(matcher.match(frame,box),trusted=True)
    assert matcher.refine(frame,box,trusted,_prepared=prepared) is trusted
    assert set(prepared)=={'frame'}


def test_character_list_and_members_share_only_callback_frame(matcher,monkeypatch):
    frame=art_frame(matcher);boxes=((100,200,130,130),(250,200,130,130));members=((70,550,70,70),)
    monkeypatch.setattr(c,'detect_grid',lambda *a,**kw:boxes)
    monkeypatch.setattr(c,'card_presence',lambda *a:1)
    monkeypatch.setattr(c,'member_slots',lambda *a,**kw:members)
    monkeypatch.setattr(c,'card_attribute',lambda *a:None)
    real=matcher.match;calls=[]
    def tracked(image,box,**kwargs):
        prepared=kwargs['_prepared'];result=real(image,box,**kwargs)
        calls.append((prepared,prepared['rgb'],prepared['lum']));return result
    monkeypatch.setattr(matcher,'match',tracked)
    recognizer=c.CharacterRecognizer(matcher=matcher)
    recognizer.recognize(frame,'battle',current_filter='ALL')
    assert len(calls)>=3 and all(p is calls[0][0] and rgb is calls[0][1] and lum is calls[0][2] for p,rgb,lum in calls)
    previous=calls[0][0];calls.clear()
    recognizer.recognize(frame.copy(),'battle',current_filter='ALL')
    assert calls and calls[0][0] is not previous
    assert all(p is calls[0][0] for p,_,_ in calls)
    assert all(value is not previous for value in vars(recognizer).values())


def test_pure_ocr_pending_reward_stop_and_filter_do_not_prepare_arrays(matcher,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('no portrait work was needed')
    monkeypatch.setattr(matcher,'match',forbidden)
    monkeypatch.setattr(matcher,'refine',forbidden)
    from pcr_maa.labyrinth import rewards
    monkeypatch.setattr(rewards,'role_offer_from_words',lambda slot,words,optimizer:NS(character_id='1001',words=words))
    monkeypatch.setattr(rewards,'reward_name_family',lambda name:'family')
    monkeypatch.setattr(rewards,'reward_name_key',lambda name:'other')
    frame=np.zeros((720,1280,3),np.uint8);recognizer=c.CharacterRecognizer(matcher=matcher)
    result=recognizer.reward_offers(frame,name_words={0:[dict(text='日和')],1:None,2:[dict(text='日和')]})
    assert [card['identity_source'] for card in result['cards'] if card.get('trusted')]==['maa_name','maa_name']
    assert result['cards'][1]['ocr_state']=='pending'
    with pytest.raises(BatchStopped,match='^stopped$'):
        c.CharacterRecognizer(matcher=matcher,stopped=lambda:True).recognize(frame,'battle')
    def read(name,image):
        if name in ('LabyDateChangedObserved','LabyDisconnect6002Observed'):return None
        return NS(hit=True,raw_detail=dict(filtered=[dict(text='全部',box=[50,130,60,20])],best=dict(text='全部',box=[50,130,60,20])))
    result=c.CharacterRecognition('Perf',recognizer).analyze(NS(tasker=NS(stopping=False),run_recognition=read),
        NS(image=frame,custom_recognition_param=json.dumps(dict(mode='filter',page='battle',filter='ALL'))))
    assert result is not None


def test_reward_and_joined_portraits_share_current_frame(matcher,monkeypatch):
    frame=art_frame(matcher);calls=[];real=matcher.match
    def tracked(image,box,**kwargs):
        result=real(image,box,**kwargs);calls.append(kwargs['_prepared']);return replace(result,trusted=True)
    monkeypatch.setattr(matcher,'match',tracked)
    recognizer=c.CharacterRecognizer(matcher=matcher)
    recognizer.reward_offers(frame)
    assert len(calls)==3 and all(value is calls[0] for value in calls)
    reward_prepared=calls[0];calls.clear()
    monkeypatch.setattr(c,'card_presence',lambda *a:1)
    for slot,role in enumerate(('1001','1002','1003')):
        index=next(i for i,e in enumerate(matcher.entries) if e['character_id']==role)
        frame[233+94*slot:293+94*slot,353:413]=cv2.resize(matcher.images[index][...,::-1],(60,60))
    def read(name,image):
        return NS(hit=False) if name in ('LabyDateChangedObserved','LabyDisconnect6002Observed') or 'JoinedName' in name else NS(hit=True)
    result=c.CharacterRecognition('Perf',recognizer).analyze(NS(tasker=NS(stopping=False),run_recognition=read),
        NS(image=frame,custom_recognition_param=json.dumps(dict(mode='read',page='joined'))))
    assert result is not None and len(calls)==3 and all(value is calls[0] for value in calls)
    assert calls[0] is not reward_prepared


def map_frame(marks):
    image=np.zeros((720,1280,3),np.uint8)
    for mark,(x,y,w,h) in zip(marks,r.MAP_MARK_ROIS):
        if mark is None:continue
        hues=[r.MAP_MARK_HUES[v] for v in mark] if isinstance(mark,tuple) else [r.MAP_MARK_HUES[mark]]
        for column in range(w):
            hue=hues[(column//3)%len(hues)]
            color=cv2.cvtColor(np.array([[[hue,.9,.9]]],np.float32),cv2.COLOR_HSV2BGR)[0,0]
            image[y:y+round(h*.8),x+column]=(color*255).astype(np.uint8)
        image[y+round(h*.82):y+h,x:x+w]=245
    return image


def digit(value,*,score=.99,box=(0,0,30,30)):
    return dict(filtered=[dict(text=str(value),score=score,box=list(box))])


def map_callback(image,digits,monkeypatch,*,kind='map',template_values=None):
    calls=[];templates=[];present=[i for i,roi in enumerate(r.MAP_MARK_ROIS) if r.map_relic_mark(image,roi) is not None]
    def read(name,frame,*override):
        calls.append((name,frame,override))
        if name==scene.NODE:
            assert frame is image
            assert override==({scene.NODE:dict(custom_recognition_param=dict(probe_kinds=sorted(scene._RULES)))},)
            return NS(hit=True,raw_detail=dict(kind=kind))
        assert name=='LabyRelicMapDigitScaled' and frame.shape==(128,128,3)
        slot=present[len(calls)-2]
        return NS(raw_detail=digits.get(slot))
    def template(frame,roi):
        assert frame is image;slot=r.MAP_TEMPLATE_DIGIT_ROIS.index(roi);templates.append(slot)
        return dict(value=(template_values or {}).get(slot),score=.9,margin=.5)
    monkeypatch.setattr(r,'map_small_counter',template)
    result=r.RelicBusinessRecognition().analyze(NS(run_recognition=read),NS(image=image,custom_recognition_param='{"mode":"map"}'))
    detail=None if result is None else json.loads(result.detail) if isinstance(result.detail,str) else result.detail
    return detail,calls,templates


@pytest.mark.parametrize('count',range(6))
def test_map_zero_to_five_slots_skip_only_missing_ocr_and_preserve_business(monkeypatch,count):
    marks=list(r.MAP_MARK_HUES);image=map_frame(marks[:count]+[None]*(5-count))
    digits={i:digit(i+4) for i in range(5)}
    expected=r.map_relic_values(image,digits,digit_rois=((0,0,128,128),)*5)
    actual,calls,templates=map_callback(image,digits,monkeypatch)
    for field in ('values','observed','slots'):assert actual[field]==expected[field]
    assert tuple(map(tuple,actual['signature']))==expected['signature']
    assert len(calls)==count+1 and templates==list(range(5))
    assert actual['digit_ocr_skipped_slots']==list(range(count,5))
    assert set(map(int,actual['digit_native']))==set(range(5))
    for index in range(count,5):assert actual['digit_native'][index] is None
    assert r.map_relic_consensus(expected,actual)==expected['values']


@pytest.mark.parametrize('kind',['unknown','home','relic_reward','link_reward','date_changed','disconnect','battle_victory'])
def test_map_counter_scan_keeps_full_scene_competition_and_never_runs_under_overlay(monkeypatch,kind):
    actual,calls,templates=map_callback(map_frame(list(r.MAP_MARK_HUES)),{},monkeypatch,kind=kind)
    assert actual is None and len(calls)==1 and not templates


@pytest.mark.parametrize('first,second,template,known',[(2,3,None,None),(2,2,None,2),(2,2,3,None),
    (31,31,None,None),(4,4,3,4)])
def test_map_duplicate_conflicting_ocr_template_and_unknown_stay_equivalent(monkeypatch,first,second,template,known):
    image=map_frame(['强化','强化',('加速','弱体'),None,None])
    assert r.map_relic_mark(image,r.MAP_MARK_ROIS[2]) is None
    digits={0:digit(first),1:digit(second),2:digit(12)}
    template_values={0:template,1:template}
    expected=r.map_relic_values(image,digits,digit_rois=((0,0,128,128),)*5,
        templates={i:dict(value=template_values.get(i)) for i in range(5)})
    actual,calls,templates=map_callback(image,digits,monkeypatch,template_values=template_values)
    for field in ('values','observed','slots'):assert actual[field]==expected[field]
    assert actual['observed']['强化']==known and actual['observed']['加速'] is None and actual['observed']['弱体'] is None
    assert actual['digit_ocr_skipped_slots']==[2,3,4] and len(calls)==3 and len(templates)==5
    changed=dict(actual,values={'强化':7})
    assert r.map_relic_consensus(actual,changed)=={}


@pytest.mark.parametrize('raw',[None,digit(5,score=.4),digit(5,box=(120,0,30,30)),dict(filtered=[dict(text='5',score=.99,box=[0,0,20,20]),dict(text='6',score=.99,box=[25,0,20,20])])])
def test_present_slot_missing_ambiguous_or_invalid_ocr_never_becomes_zero(monkeypatch,raw):
    actual,_,_=map_callback(map_frame(['会心',None,None,None,None]),{0:raw},monkeypatch)
    assert actual['values']=={} and all(value is None for value in actual['observed'].values())
    assert actual['slots']==[dict(slot=0,mark='会心',value=None)]
    assert r.map_relic_consensus(actual,dict(values={'会心':0}))=={}
