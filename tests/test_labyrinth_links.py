"""Link choices: native saved frames, strict quantity and upstream fixed priority."""
from copy import deepcopy
from dataclasses import replace
from itertools import permutations
from pathlib import Path
import json,re
import pytest
from maa.custom_action import CustomAction
from pcr_maa import native
from pcr_maa.replay import SavedImages
from pcr_maa.context_backend import ContextTasker
from pcr_maa.labyrinth import links
from pcr_maa.labyrinth.batch import BatchStopped

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT.parent/'evidence'
FRAME=EVIDENCE/'link-node-live-01/observe-choice-after.png'


def receipt(elements=('火','水','光'),quantity='3',marker='×'):
    rows=[]
    def word(name,text,roi):
        x,y,w,h=roi
        rows.append(dict(name=name,detail=dict(filtered=[dict(text=text,box=[x,y,w,h],score=.95)])))
    word('LabyLinkTitle','获得道具的机会！',links.TITLE_ROI)
    word('LabyLinkSubtitle','请选择要获得的连结印记。',links.SUBTITLE_ROI)
    word('LabyLinkRemaining','还剩1次',links.REMAINING_ROI)
    for i,element in enumerate(elements):
        word(f'LabyLinkName{i}',element+'属性的随机印记',links.NAME_ROIS[i])
        word(f'LabyLinkQuantity{i}',element+'属性的随机印记'+marker+quantity,links.QUANTITY_ROIS[i])
        word(f'LabyLinkButton{i}','选择',links.BUTTON_ROIS[i])
        rows.append(dict(name=f'LabyLinkBright{i}',detail=dict(filtered=[dict(box=list(links.BRIGHT_ROIS[i]),count=1400)])))
    return dict(native_ok=True,nodes=[dict(name='LabyLinkObserved',completed=True,recognition=rows)])


def row(value,label):return next(r['detail'] for r in value['nodes'][0]['recognition'] if r['name']=='LabyLink'+label)


def test_reader_preserves_complete_quantity_and_does_not_claim_inventory():
    offers=links.link_offers(receipt())
    assert [(o.slot,o.element,o.quantity) for o in offers]==[(0,'火',3),(1,'水',3),(2,'光',3)]
    assert all(not hasattr(o,'inventory_added') and not hasattr(o,'character_ids') for o in offers)
    assert all(o.quantity_words[0].text.endswith('×3') for o in offers)


@pytest.mark.parametrize('elements',list(permutations(links.ELEMENT_PRIORITY,3)))
def test_priority_matches_upstream_order_for_every_displayed_permutation(elements):
    offers=links.link_offers(receipt(elements))
    selected=links.preferred_choice(offers)
    assert selected.element==min(elements,key=links.ELEMENT_PRIORITY.index)
    assert selected.slot==elements.index(selected.element)


def test_priority_uses_attribute_instead_of_unconditional_first_slot():
    offers=links.link_offers(receipt(('水','光','暗')))
    assert links.preferred_choice(offers).slot==2
    assert links.preferred_choice(offers,('水','风','光','暗','火')).slot==0


@pytest.mark.parametrize('priority',[None,('火',),('火','火','光','风','水'),('火','暗','光','风','未知'),(True,'暗','光','风','水')])
def test_invalid_priority_and_unknown_choices_have_no_first_slot_fallback(priority):
    with pytest.raises(BatchStopped):links.preferred_choice(links.link_offers(receipt()),priority)
    with pytest.raises(BatchStopped):links.preferred_choice(())


@pytest.mark.parametrize('kind',['native_false','completed_truthy','duplicate_root','missing_leaf','duplicate_leaf',
    'bad_leaf_name','bad_leaf','wrong_title','wrong_subtitle','remaining_zero','wrong_button',
    'duplicate_attribute','unknown_attribute','different_quantity_attribute'])
def test_incomplete_wrong_or_ambiguous_page_stops(kind):
    value=receipt();rows=value['nodes'][0]['recognition']
    if kind=='native_false':value['native_ok']=False
    elif kind=='completed_truthy':value['nodes'][0]['completed']=1
    elif kind=='duplicate_root':value['nodes'].append(deepcopy(value['nodes'][0]))
    elif kind=='missing_leaf':rows.pop()
    elif kind=='duplicate_leaf':rows[-1]=deepcopy(rows[0])
    elif kind=='bad_leaf_name':rows[-1]['name']={}
    elif kind=='bad_leaf':rows[-1]=None
    elif kind=='duplicate_attribute':value=receipt(('火','火','光'))
    else:
        leaf,text={'wrong_title':('Title','获得道具'),'wrong_subtitle':('Subtitle','请选择要获得的迷宫遗物。'),
            'remaining_zero':('Remaining','还剩0次'),'remaining_two':('Remaining','还剩2次'),
            'wrong_button':('Button0','确认'),'unknown_attribute':('Name0','未知属性的随机印记'),
            'different_quantity_attribute':('Quantity0','水属性的随机印记×3')}[kind]
        row(value,leaf)['filtered'][0]['text']=text
    with pytest.raises(BatchStopped):links.link_offers(value)


@pytest.mark.parametrize('quantity',['03','0','3.0','?','','三','3×3',str(2**63)])
def test_only_complete_canonical_positive_int63_quantity_is_accepted(quantity):
    with pytest.raises(BatchStopped):links.link_offers(receipt(quantity=quantity))


@pytest.mark.parametrize('marker',['×','?','？','x'])
def test_non_numeric_separator_is_preserved_without_fabricating_multiplication(marker):
    offers=links.link_offers(receipt(marker=marker))
    assert all(o.quantity_marker==marker and marker in o.quantity_words[0].text for o in offers)
    assert all(o.quantity==3 for o in offers)


@pytest.mark.parametrize('score',[True,float('nan'),float('inf'),.8499,1.01])
@pytest.mark.parametrize('leaf',['Name0','Quantity1','Button2'])
def test_leaf_confidence_must_be_finite_and_non_boolean(score,leaf):
    value=receipt();row(value,leaf)['filtered'][0]['score']=score
    with pytest.raises(BatchStopped):links.link_offers(value)


@pytest.mark.parametrize('box',[None,[134,450,275,47],[135,450,0,47],[135,450,1000,47],[True,450,275,47]])
def test_quantity_is_bound_to_entire_card_line_roi(box):
    value=receipt();row(value,'Quantity0')['filtered'][0]['box']=box
    with pytest.raises(BatchStopped):links.link_offers(value)


@pytest.mark.parametrize('count',[True,799,1401,None])
def test_all_three_buttons_require_observed_bright_state(count):
    value=receipt();row(value,'Bright1')['filtered'][0]['count']=count
    with pytest.raises(BatchStopped):links.link_offers(value)


@pytest.mark.parametrize('kind',['bool_slot','bool_quantity','wrong_property','wrong_raw_quantity','duplicate','partial'])
def test_guard_revalidates_every_offer_before_input(kind):
    offers=list(links.link_offers(receipt()))
    if kind=='bool_slot':offers[0]=replace(offers[0],slot=False)
    elif kind=='bool_quantity':offers[0]=replace(offers[0],quantity=True)
    elif kind=='wrong_property':offers[0]=replace(offers[0],element='暗')
    elif kind=='wrong_raw_quantity':offers[0]=replace(offers[0],quantity_words=(replace(offers[0].quantity_words[0],text='火属性的随机印记×13'),))
    elif kind=='duplicate':offers=links.link_offers(receipt());offers=list(offers);offers[1]=replace(offers[1],element='火')
    else:offers.pop()
    with pytest.raises(BatchStopped):links.selection_override(offers,0)


def test_default_input_is_disabled_and_full_quantity_guard_never_crops_last_digit():
    pipe=links.link_pipeline();assert pipe['LabyLinkSelectOnce']['all_of']==['LabyLinkGuardRequired']
    override=links.selection_override(links.link_offers(receipt()),0)
    assert override['LabyLinkSelectOnce']['max_hit']==1 and override['LabyLinkSelectOnce']['next']==[]
    for i in (0,):
        assert override[f'LabyLinkGuardQuantity{i}']['roi']==list(links.QUANTITY_ROIS[i])
        assert links.QUANTITY_ROIS[i][2]==275
    assert 'LabyLinkGuardQuantity1' not in override and 'LabyLinkGuardQuantity2' not in override


@pytest.fixture
def offline(request,monkeypatch,tmp_path):
    if not FRAME.is_file():pytest.skip('private link choice frame is not distributed')
    from maa import controller as controller_module
    def deny(*args,**kwargs):raise AssertionError('ADB forbidden in saved-frame tests')
    monkeypatch.setattr(controller_module,'AdbController',deny)
    label=re.sub(r'[^A-Za-z0-9_.-]','_',request.node.name)
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native'/label)
    resource=native.load_resource(ROOT/'assets/resource');assert resource.override_pipeline(links.link_pipeline())
    controller=SavedImages([[native.read_image(FRAME)]]);tasker=native.bind(resource,controller);receipts=[]
    try:yield resource,controller,tasker,receipts
    finally:
        assert tasker.post_stop().wait().succeeded
        path=tmp_path/'native';path.mkdir(parents=True,exist_ok=True)
        (path/(label+'.json')).write_text(json.dumps(dict(scope='Official CustomController saved frames, no ADB/network/real input; no acquisition result',
            receipts=receipts,clicks=controller.clicks,swipes=controller.swipes),ensure_ascii=False,indent=2),encoding='utf-8')



def observe(tasker,receipts):
    value=native.run(tasker,'LabyLinkObserved');receipts.append(value)
    return links.link_offers(value)


def test_native_actual_fire_water_light_and_exact_one_shot_guard(offline):
    resource,controller,tasker,receipts=offline;offers=observe(tasker,receipts)
    assert [(o.element,o.quantity) for o in offers]==[('火',3),('水',3),('光',3)]
    assert not controller.clicks and not controller.swipes
    selected=links.preferred_choice(offers);assert selected.slot==0
    before=resource.get_node_data('LabyLinkSelectOnce')
    with links.selection_guard(tasker,offers,selected.slot):
        value=native.run(tasker,'LabyLinkSelectOnce');receipts.append(value)
    assert value['native_ok'] is True and len(controller.clicks)==1 and not controller.swipes
    assert resource.get_node_data('LabyLinkSelectOnce')==before
    x,y=controller.clicks[0];assert 175<=x<=370 and 542<=y<=634


@pytest.mark.parametrize('changed_slot,allowed',[(0,False),(2,True)])
def test_native_selected_attribute_stays_exact_unselected_does_not_block(offline,changed_slot,allowed):
    _,controller,tasker,receipts=offline;offers=list(observe(tasker,receipts));offer=offers[changed_slot]
    offers[changed_slot]=replace(offer,element='暗',name_words=(replace(offer.name_words[0],text='暗属性的随机印记'),),
        quantity_words=(replace(offer.quantity_words[0],text='暗属性的随机印记×3'),))
    with links.selection_guard(tasker,offers,0):
        value=native.run(tasker,'LabyLinkSelectOnce');receipts.append(value)
    assert value['native_ok'] is allowed and len(controller.clicks)==int(allowed) and not controller.swipes


def game_font_thirteen():
    import cv2,numpy as np
    file=EVIDENCE/'relic-offer-live-01/select-after.png'
    if not file.is_file():pytest.skip('private game font ×1 source absent')
    frame=native.read_image(FRAME);result=native.read_image(file)
    glyph=result[185:212,564:580]
    mask=cv2.inRange(glyph,np.array([0,70,170]),np.array([80,200,255]))
    mask=cv2.resize(mask,(13,24),interpolation=cv2.INTER_NEAREST)
    changed=frame.copy();cross=frame[463:490,356:374].copy()
    changed[462:492,351:380]=frame[452,360];changed[463:490,349:367]=cross
    region=changed[465:489,367:380]
    region[cv2.dilate(mask,np.ones((3,3),np.uint8))>0]=[250,250,250]
    region[mask>0]=[65,65,72]
    return changed


def test_native_game_font_complete_thirteen_reads_as_thirteen_and_sends_zero_input(offline):
    _,controller,tasker,receipts=offline;offers=observe(tasker,receipts)
    controller.stages=[[game_font_thirteen()]];controller.frame=0
    assert tasker.resource.override_pipeline({'LabyLinkQuantity0':dict(expected='.+')})
    quantity=native.run(tasker,'LabyLinkQuantity0');receipts.append(quantity)
    assert quantity['native_ok'] is True
    text=''.join(w['text'] for w in quantity['nodes'][0]['recognition']['filtered'])
    assert re.findall(r'[0-9]+',text)==['13']
    assert tasker.resource.override_pipeline({'LabyLinkQuantity0':dict(expected=links.link_pipeline()['LabyLinkQuantity0']['expected'])})
    observed=native.run(tasker,'LabyLinkObserved');receipts.append(observed)
    assert links.link_offers(observed)[0].quantity==13
    with links.selection_guard(tasker,offers,0):
        value=native.run(tasker,'LabyLinkSelectOnce');receipts.append(value)
    assert value['native_ok'] is False and not controller.clicks and not controller.swipes


@pytest.mark.parametrize('file_name',['link-node-live-01/confirm-after.png','link-node-live-01/preview-before.png',
    'relic-node-live/observe-after.png','client-resume-live/resume-active-after.png','opening-sample/first-event-joined-close-after.png'])
def test_native_animation_map_relic_role_and_coin_modal_are_not_links(offline,file_name):
    _,controller,tasker,receipts=offline;offers=observe(tasker,receipts)
    file=EVIDENCE/file_name
    if not file.is_file():pytest.skip('private negative saved frame absent')
    controller.stages=[[native.read_image(file)]];controller.frame=0
    observed=native.run(tasker,'LabyLinkObserved');receipts.append(observed)
    with pytest.raises(BatchStopped):links.link_offers(observed)
    with links.selection_guard(tasker,offers,0):
        value=native.run(tasker,'LabyLinkSelectOnce');receipts.append(value)
    assert value['native_ok'] is False and not controller.clicks and not controller.swipes


@pytest.mark.parametrize('dark_slot,allowed',[(0,False),(1,True)])
def test_native_dark_selected_button_stops_but_unselected_does_not(offline,dark_slot,allowed):
    _,controller,tasker,receipts=offline;offers=observe(tasker,receipts)
    frame=native.read_image(FRAME);x,y,w,h=links.BRIGHT_ROIS[dark_slot];frame[y:y+h,x:x+w]=0
    controller.stages=[[frame]];controller.frame=0
    with links.selection_guard(tasker,offers,0):
        value=native.run(tasker,'LabyLinkSelectOnce');receipts.append(value)
    assert value['native_ok'] is allowed and len(controller.clicks)==int(allowed) and not controller.swipes


class LinkOneShot(CustomAction):
    def __init__(self):super().__init__();self.receipts=[];self.error=None;self.blocked=False
    def run(self,context,argv):
        try:
            with ContextTasker(context) as tasker:
                offers=observe(tasker,self.receipts);selected=links.preferred_choice(offers)
                assert tasker.resource.override_pipeline(links.selection_override(offers,selected.slot))
                self.receipts.append(native.run(tasker,'LabyLinkSelectOnce'))
                self.receipts.append(native.run(tasker,'LabyLinkSelectOnce'));self.blocked=tasker._blocked
            return True
        except Exception as error:self.error=str(error);return False


def test_native_same_context_selection_budget_is_not_renewed(offline):
    resource,controller,tasker,receipts=offline;callback=LinkOneShot()
    assert resource.register_custom_action('LinkOneShot',callback)
    assert resource.override_pipeline({'LinkOneShotRoot':dict(recognition='DirectHit',action='Custom',custom_action='LinkOneShot',next=[],pre_delay=0,post_delay=0)})
    assert tasker.post_task('LinkOneShotRoot').wait().succeeded,callback.error
    receipts.extend(callback.receipts)
    assert [r['native_ok'] for r in callback.receipts]==[True,True,False]
    assert callback.blocked and len(controller.clicks)==1 and not controller.swipes


# Result additions keep link-choice-01 and its 138 passing tests as history.
RESULT_FRAME=EVIDENCE/'link-node-live-01/observe-result-after.png'


def obtained_receipt(element='火',quantity=3):
    rows=[]
    for label,text,roi in (
            ('Title','获得道具',links.RESULT_TITLE_ROI),
            ('Message','获得了以下道具。',links.RESULT_MESSAGE_ROI),
            ('Item',f'{element}属性的随机印记×{quantity}',links.RESULT_ITEM_ROI),
            ('Close','关闭',links.RESULT_CLOSE_ROI)):
        x,y,_,_=roi
        rows.append(dict(name='LabyLinkObtained'+label,detail=dict(filtered=[dict(
            text=text,box=[x+5,y+5,200 if label=='Item' else 100,25],score=.95)])))
    return dict(native_ok=True,nodes=[dict(name='LabyLinkObtainedObserved',completed=True,recognition=rows)])


def obtained_row(value,label):
    return next(r['detail'] for r in value['nodes'][0]['recognition'] if r['name']=='LabyLinkObtained'+label)


def test_link_result_exact_imprints_do_not_claim_three_roles():
    raw=obtained_receipt();value=links.link_obtained(raw,'火',quantity=3)
    assert value['verified'] and value['imprints_obtained'] and value['quantity']==3
    assert value['element']=='火' and value['imprint_name']=='火属性的随机印记' and value['native'] is raw
    assert all(k not in value for k in ('inventory_added','character_ids','roles_added','item_id'))


@pytest.mark.parametrize('element,quantity',[('水',3),('火',13),('火',1),('火',0),('火',True)])
def test_link_result_wrong_attribute_or_complete_quantity_is_rejected(element,quantity):
    with pytest.raises(BatchStopped):links.link_obtained(obtained_receipt(element,quantity),'火',quantity=3)


@pytest.mark.parametrize('selected',[None,True,'火属性的随机印记','未知','火×3'])
def test_link_result_requires_known_selected_attribute(selected):
    with pytest.raises(BatchStopped):links.link_obtained(obtained_receipt(),selected)


@pytest.mark.parametrize('kind',['native_false','completed_truthy','duplicate_root','missing_leaf','duplicate_leaf',
    'bad_leaf','bad_name','wrong_title','wrong_message','wrong_close','partial_item','extra_item','relic','coins'])
def test_link_result_incomplete_generic_or_other_item_is_rejected(kind):
    value=obtained_receipt();rows=value['nodes'][0]['recognition']
    if kind=='native_false':value['native_ok']=False
    elif kind=='completed_truthy':value['nodes'][0]['completed']=1
    elif kind=='duplicate_root':value['nodes'].append(deepcopy(value['nodes'][0]))
    elif kind=='missing_leaf':rows.pop()
    elif kind=='duplicate_leaf':rows[-1]=deepcopy(rows[0])
    elif kind=='bad_leaf':rows[-1]=None
    elif kind=='bad_name':rows[-1]['name']={}
    else:
        label,text={'wrong_title':('Title','道具详情'),'wrong_message':('Message','请选择要获得的连结印记。'),
            'wrong_close':('Close','确认'),'partial_item':('Item','火属性的随机印记'),
            'extra_item':('Item','火属性的随机印记×3迷宫金币×200'),
            'relic':('Item','星明仪杖×1'),'coins':('Item','迷宫金币×200')}[kind]
        obtained_row(value,label)['filtered'][0]['text']=text
    with pytest.raises(BatchStopped):links.link_obtained(value,'火',quantity=3)


@pytest.mark.parametrize('score',[True,float('nan'),float('inf'),.8499,1.01])
def test_link_result_full_item_confidence_is_finite_and_non_boolean(score):
    value=obtained_receipt();obtained_row(value,'Item')['filtered'][0]['score']=score
    with pytest.raises(BatchStopped):links.link_obtained(value,'火',quantity=3)


@pytest.mark.parametrize('box',[None,[450,174,0,10],[449,180,100,25],[455,180,500,25],[True,180,100,25]])
def test_link_result_complete_item_is_bound_to_full_row(box):
    value=obtained_receipt();obtained_row(value,'Item')['filtered'][0]['box']=box
    with pytest.raises(BatchStopped):links.link_obtained(value,'火',quantity=3)


def test_link_result_two_nonoverlapping_horizontal_words_still_bind_full_quantity():
    value=obtained_receipt();words=obtained_row(value,'Item')['filtered']
    words[0].update(text='火属性的随机印记',box=[455,179,180,25])
    words.append(dict(text='×3',box=[645,179,40,25],score=.95))
    assert links.link_obtained(value,'火',quantity=3)['quantity']==3
    words[1]['text']='×13'
    with pytest.raises(BatchStopped):links.link_obtained(value,'火',quantity=3)


def test_link_result_default_close_is_disabled_and_never_navigates_next():
    pipe=links.result_pipeline()
    assert re.fullmatch(pipe['LabyLinkObtainedItem']['expected'],'__qualified_link_element_required__属性的随机印记×__qualified_link_quantity_required__')
    assert pipe['LabyLinkObtainedCloseOnce']['next']==[] and pipe['LabyLinkObtainedCloseOnce']['max_hit']==1
    assert pipe['LabyLinkFollowingObserved']['action']=='DoNothing'


@pytest.fixture
def result_offline(request,monkeypatch,tmp_path):
    if not RESULT_FRAME.is_file():pytest.skip('private link result frame is not distributed')
    from maa import controller as controller_module
    def deny(*args,**kwargs):raise AssertionError('ADB forbidden in link result saved-frame tests')
    monkeypatch.setattr(controller_module,'AdbController',deny)
    label=re.sub(r'[^A-Za-z0-9_.-]','_',request.node.name)
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native'/label)
    resource=native.load_resource(ROOT/'assets/resource')
    assert resource.override_pipeline({**links.link_pipeline(),**links.result_pipeline()})
    controller=SavedImages([[native.read_image(RESULT_FRAME)]]);tasker=native.bind(resource,controller);receipts=[]
    try:yield resource,controller,tasker,receipts
    finally:
        assert tasker.post_stop().wait().succeeded
        path=tmp_path/'native';path.mkdir(parents=True,exist_ok=True)
        (path/(label+'.json')).write_text(json.dumps(dict(scope='Official CustomController; link imprint item proof only; no ADB/network/real input or recruited-role proof',
            receipts=receipts,clicks=controller.clicks,swipes=controller.swipes),ensure_ascii=False,indent=2),encoding='utf-8')


def test_link_result_native_actual_fire_three_scoped_observe_and_one_close(result_offline):
    resource,controller,tasker,receipts=result_offline
    before=resource.get_node_data('LabyLinkObtainedItem')
    with links.obtained_guard(tasker,'火',quantity=3):
        value=native.run(tasker,'LabyLinkObtainedObserved');receipts.append(value)
        assert links.link_obtained(value,'火',quantity=3)['imprints_obtained'] is True
        assert not controller.clicks and not controller.swipes
        close=native.run(tasker,'LabyLinkObtainedCloseOnce');receipts.append(close)
    assert close['native_ok'] and len(controller.clicks)==1 and not controller.swipes
    assert resource.get_node_data('LabyLinkObtainedItem')==before
    x,y=controller.clicks[0];assert 500<=x<=778 and 608<=y<=673


def test_link_result_native_wrong_attribute_sends_zero_close(result_offline):
    _,controller,tasker,receipts=result_offline
    with links.obtained_guard(tasker,'水',quantity=3):
        value=native.run(tasker,'LabyLinkObtainedObserved');receipts.append(value)
        with pytest.raises(BatchStopped):links.link_obtained(value,'水',quantity=3)
        close=native.run(tasker,'LabyLinkObtainedCloseOnce');receipts.append(close)
    assert not close['native_ok'] and not controller.clicks and not controller.swipes


@pytest.mark.parametrize('quantity',[13,1])
def test_link_result_native_complete_wrong_quantity_is_probed_then_zero_close(result_offline,quantity):
    _,controller,tasker,receipts=result_offline
    import cv2
    frame=native.read_image(RESULT_FRAME);frame[180:218,652:694]=255
    cv2.putText(frame,str(quantity),(652,206),cv2.FONT_HERSHEY_SIMPLEX,.7,(0,140,255),2,cv2.LINE_AA)
    controller.stages=[[frame]];controller.frame=0
    assert tasker.resource.override_pipeline({'LabyLinkObtainedItem':dict(expected='.+')})
    probe=native.run(tasker,'LabyLinkObtainedItem');receipts.append(probe)
    assert probe['native_ok']
    text=''.join(w['text'] for w in probe['nodes'][0]['recognition']['filtered'])
    assert re.sub(r'\s+','',''.join(text))=='火属性的随机印记×'+str(quantity)
    with links.obtained_guard(tasker,'火',quantity=3):
        value=native.run(tasker,'LabyLinkObtainedObserved');receipts.append(value)
        with pytest.raises(BatchStopped):links.link_obtained(value,'火',quantity=3)
        close=native.run(tasker,'LabyLinkObtainedCloseOnce');receipts.append(close)
    assert not close['native_ok'] and not controller.clicks and not controller.swipes


@pytest.mark.parametrize('file_name',['relic-offer-live-01/select-after.png',
    'opening-sample/first-event-joined-close-after.png','link-node-live-01/observe-choice-after.png'])
def test_link_result_native_relic_coins_and_choice_page_sends_zero_close(result_offline,file_name):
    _,controller,tasker,receipts=result_offline;file=EVIDENCE/file_name
    if not file.is_file():pytest.skip('private negative saved frame absent')
    controller.stages=[[native.read_image(file)]];controller.frame=0
    with links.obtained_guard(tasker,'火',quantity=3):
        value=native.run(tasker,'LabyLinkObtainedObserved');receipts.append(value)
        with pytest.raises(BatchStopped):links.link_obtained(value,'火',quantity=3)
        close=native.run(tasker,'LabyLinkObtainedCloseOnce');receipts.append(close)
    assert not close['native_ok'] and not controller.clicks and not controller.swipes


class LinkCloseOneShot(CustomAction):
    def __init__(self):super().__init__();self.receipts=[];self.error=None;self.blocked=False
    def run(self,context,argv):
        try:
            with ContextTasker(context) as tasker:
                self.receipts.append(native.run(tasker,'LabyLinkObtainedObserved'))
                assert links.link_obtained(self.receipts[-1],'火',quantity=3)['quantity']==3
                self.receipts.append(native.run(tasker,'LabyLinkObtainedCloseOnce'))
                self.receipts.append(native.run(tasker,'LabyLinkObtainedCloseOnce'));self.blocked=tasker._blocked
            return True
        except Exception as error:self.error=str(error);return False


def test_link_result_native_same_context_close_budget_is_not_renewed(result_offline):
    resource,controller,tasker,receipts=result_offline;callback=LinkCloseOneShot()
    assert resource.override_pipeline(links.result_pipeline('火',quantity=3))
    assert resource.register_custom_action('LinkCloseOneShot',callback)
    assert resource.override_pipeline({'LinkCloseOneShotRoot':dict(recognition='DirectHit',action='Custom',custom_action='LinkCloseOneShot',next=[],pre_delay=0,post_delay=0)})
    assert tasker.post_task('LinkCloseOneShotRoot').wait().succeeded,callback.error
    receipts.extend(callback.receipts)
    assert [r['native_ok'] for r in callback.receipts]==[True,True,False]
    assert callback.blocked and len(controller.clicks)==1 and not controller.swipes


class FakeLinkResource:
    def __init__(self):self.nodes=deepcopy({**links.link_pipeline(),**links.result_pipeline()})
    def get_node_data(self,name):return deepcopy(self.nodes.get(name))
    def override_pipeline(self,override):
        for name,fields in override.items():self.nodes[name].update(deepcopy(fields))
        return True


class FakeLinkTasker:
    def __init__(self):self.resource=FakeLinkResource()


def mock_link_runtime(monkeypatch,tmp_path,*,bad_result=False,bad_input=None,cancel_after=None):
    from pcr_maa.labyrinth.batch import Batch,Binding
    from pcr_maa.labyrinth.config import Settings,Goal
    from pcr_maa.preset_execution import Journal
    journal=Journal(tmp_path);binding=Binding(123,'synthetic-no-device');stopped=[False];calls=[];sequence=[0]
    def stamp(**fields):
        sequence[0]+=1
        return dict(verified=True,binding=binding.payload(),frame=sequence[0],**fields)
    batch=Batch(Settings((Goal(1,1),)),None,journal,binding,stopped=lambda:stopped[0])
    reward=links.MaaLinkReward(FakeLinkTasker(),stamp)
    def run(tasker,node,*,reset_hits=()):
        calls.append((node,reset_hits))
        if reset_hits:
            assert reset_hits==(node,) and node in ('LabyLinkSelectOnce','LabyLinkResultCloseOnce')
            assert journal.events()[-1]['state']=='in_flight'
        if node=='LabyLinkObserved':value=receipt()
        elif node=='LabyLinkObtainedObserved':value=obtained_receipt('水' if bad_result else '火')
        else:value=dict(native_ok=node!=bad_input,nodes=[dict(name=node,completed=node!=bad_input,
            action=dict(success=node!=bad_input),recognition={})])
        if node==cancel_after:stopped[0]=True
        return value
    monkeypatch.setattr(native,'run',run)
    return batch,reward,journal,calls,stamp,stopped


def test_link_transaction_result_proof_precedes_close_and_settlement(monkeypatch,tmp_path):
    batch,reward,journal,calls,_,_=mock_link_runtime(monkeypatch,tmp_path)
    with journal.locked():value=reward.choose(batch,1,'reward-a')
    assert value['imprints_obtained'] is True and value['quantity']==3
    assert all(k not in value for k in ('inventory_added','roles_added','character_ids'))
    assert value['native']['following_page']=='unclassified'
    assert [n for n,_ in calls]==['LabyLinkObserved','LabyLinkSelectOnce','LabyLinkObtainedObserved',
        'LabyLinkResultCloseOnce','LabyLinkFollowingObserved']
    assert [r for _,r in calls]==[(),('LabyLinkSelectOnce',),(),('LabyLinkResultCloseOnce',),()]
    assert [e['state'] for e in journal.events()]==['in_flight','settled']*2
    assert journal.events()[0]['operation']=='link_reward_select'


def test_link_transaction_same_reward_cannot_resend_even_with_fresh_adapter(monkeypatch,tmp_path):
    batch,reward,journal,calls,stamp,_=mock_link_runtime(monkeypatch,tmp_path)
    with journal.locked():
        reward.choose(batch,1,'reward-a')
        with pytest.raises(BatchStopped,match='already_attempted'):reward.choose(batch,1,'reward-a')
        fresh=links.MaaLinkReward(FakeLinkTasker(),stamp)
        with pytest.raises(BatchStopped,match='already_attempted'):fresh.choose(batch,1,'reward-a')
    assert sum(n=='LabyLinkSelectOnce' for n,_ in calls)==1 and len(journal.events())==4


@pytest.mark.parametrize('bad_input',['LabyLinkSelectOnce','LabyLinkResultCloseOnce','LabyLinkFollowingObserved'])
def test_link_transaction_unknown_native_input_or_observation_leaves_intent(monkeypatch,tmp_path,bad_input):
    batch,reward,journal,calls,_,_=mock_link_runtime(monkeypatch,tmp_path,bad_input=bad_input)
    with journal.locked():
        with pytest.raises(BatchStopped):reward.choose(batch,1,'reward-a')
        with pytest.raises(BatchStopped,match='already_attempted'):reward.choose(batch,1,'reward-a')
    expected_count=1 if bad_input=='LabyLinkSelectOnce' else 3 if bad_input=='LabyLinkResultCloseOnce' else 4
    assert len(journal.events())==expected_count
    assert journal.events()[-1]['state']==('settled' if bad_input=='LabyLinkFollowingObserved' else 'in_flight')
    if expected_count>1:assert journal.events()[1]['state']=='settled'
    assert sum(n=='LabyLinkSelectOnce' for n,_ in calls)==1
    if bad_input=='LabyLinkSelectOnce':assert len(calls)==2
    if bad_input=='LabyLinkResultCloseOnce':assert len(calls)==4


def test_link_transaction_wrong_result_never_closes_or_resends(monkeypatch,tmp_path):
    batch,reward,journal,calls,stamp,_=mock_link_runtime(monkeypatch,tmp_path,bad_result=True)
    with journal.locked():
        with pytest.raises(BatchStopped,match='mismatch'):reward.choose(batch,1,'reward-a')
        fresh=links.MaaLinkReward(FakeLinkTasker(),stamp)
        with pytest.raises(BatchStopped,match='already_attempted'):fresh.choose(batch,1,'reward-a')
    assert sum(n=='LabyLinkSelectOnce' for n,_ in calls)==1 and not any(n=='LabyLinkResultCloseOnce' for n,_ in calls)
    assert len(journal.events())==2 and journal.events()[-1]['state']=='settled'
    assert journal.events()[-1]['result']['input_action_verified'] is True
    assert not journal.events()[-1]['result'].get('inventory_added',False)
    assert not journal.events()[-1]['result'].get('imprints_obtained',False)


@pytest.mark.parametrize('cancel_after',['LabyLinkObserved','LabyLinkSelectOnce','LabyLinkObtainedObserved','LabyLinkResultCloseOnce'])
def test_link_transaction_stop_truncates_all_subsequent_inputs(monkeypatch,tmp_path,cancel_after):
    batch,reward,journal,calls,_,_=mock_link_runtime(monkeypatch,tmp_path,cancel_after=cancel_after)
    with journal.locked():
        with pytest.raises(BatchStopped,match='stopped'):reward.choose(batch,1,'reward-a')
    assert calls[-1][0]==cancel_after
    if cancel_after=='LabyLinkObserved':assert not journal.events()
    else:
        assert len(journal.events())==(3 if cancel_after=='LabyLinkResultCloseOnce' else 2 if cancel_after=='LabyLinkObtainedObserved' else 1)
        assert journal.events()[-1]['state']==('settled' if cancel_after=='LabyLinkObtainedObserved' else 'in_flight')


def test_link_transaction_already_stopped_does_not_observe(monkeypatch,tmp_path):
    batch,reward,journal,calls,_,stopped=mock_link_runtime(monkeypatch,tmp_path);stopped[0]=True
    with pytest.raises(BatchStopped,match='stopped'):reward.choose(batch,1,'reward-a')
    assert not calls and not journal.events()


@pytest.mark.parametrize('enter,reward_id',[(True,'a'),(0,'a'),(1,''),(1,None),(1,'a\nb')])
def test_link_transaction_invalid_identity_never_observes(monkeypatch,tmp_path,enter,reward_id):
    batch,reward,journal,calls,_,_=mock_link_runtime(monkeypatch,tmp_path)
    with pytest.raises(BatchStopped):reward.choose(batch,enter,reward_id)
    assert not calls and not journal.events()


class LinkTransactionsContinuous(CustomAction):
    def __init__(self,controller,journal):
        super().__init__();self.controller,self.journal=controller,journal;self.results=[];self.error=None;self.duplicate_blocked=False
    def run(self,context,argv):
        from pcr_maa.labyrinth.batch import Batch,Binding
        from pcr_maa.labyrinth.config import Settings,Goal
        try:
            with ContextTasker(context) as tasker,self.journal.locked():
                binding=Binding(123,'official-saved-controller');sequence=0
                def stamp(**fields):
                    nonlocal sequence
                    sequence+=1;return dict(verified=True,binding=binding.payload(),frame=sequence,**fields)
                batch=Batch(Settings((Goal(1,1),)),None,self.journal,binding)
                reward=links.MaaLinkReward(tasker,stamp)
                for reward_id in ('first-reward','second-reward'):
                    self.controller.stage=0;self.controller.frame=0
                    self.results.append(reward.choose(batch,1,reward_id))
                try:reward.choose(batch,1,'first-reward')
                except BatchStopped as error:self.duplicate_blocked=str(error)=='link_reward_already_attempted'
            return True
        except Exception as error:self.error=str(error);return False


def test_link_transaction_native_two_new_intents_share_context_and_only_scoped_rearm(result_offline,tmp_path,monkeypatch):
    from pcr_maa.preset_execution import Journal
    if not all(path.is_file() for path in (FRAME, EVIDENCE/'link-node-live-01/preview-before.png')):
        pytest.skip('private link transaction saved frames are not distributed')
    resource,controller,tasker,receipts=result_offline
    controller.stages=[[native.read_image(FRAME)],[native.read_image(RESULT_FRAME)],
        [native.read_image(EVIDENCE/'link-node-live-01/preview-before.png')]]
    original_run=native.run
    def capture(*args,**kwargs):
        value=original_run(*args,**kwargs);receipts.append(value);return value
    monkeypatch.setattr(native,'run',capture)
    callback=LinkTransactionsContinuous(controller,Journal(tmp_path))
    assert resource.register_custom_action('LinkTransactionsContinuous',callback)
    assert resource.override_pipeline({'LinkTransactionsContinuousRoot':dict(recognition='DirectHit',action='Custom',
        custom_action='LinkTransactionsContinuous',next=[],pre_delay=0,post_delay=0)})
    assert tasker.post_task('LinkTransactionsContinuousRoot').wait().succeeded,callback.error
    assert len(callback.results)==2 and callback.duplicate_blocked
    assert len(controller.clicks)==4 and not controller.swipes
    assert [e['state'] for e in callback.journal.events()]==['in_flight','settled']*4
    assert all(r['imprints_obtained'] is True and r['quantity']==3 and r['element']=='火'
        and 'inventory_added' not in r and r['native']['following_page']=='unclassified' for r in callback.results)


def test_link_transaction_native_unknown_result_has_one_select_and_no_close(result_offline,tmp_path,monkeypatch):
    from pcr_maa.labyrinth.batch import Batch,Binding
    from pcr_maa.labyrinth.config import Settings,Goal
    from pcr_maa.preset_execution import Journal
    if not all(path.is_file() for path in (FRAME, EVIDENCE/'relic-offer-live-01/select-after.png')):
        pytest.skip('private link transaction saved frames are not distributed')
    _,controller,tasker,receipts=result_offline
    controller.stages=[[native.read_image(FRAME)],[native.read_image(EVIDENCE/'relic-offer-live-01/select-after.png')]]
    journal=Journal(tmp_path);binding=Binding(123,'official-saved-controller')
    batch=Batch(Settings((Goal(1,1),)),None,journal,binding)
    original_run=native.run
    def capture(*args,**kwargs):
        value=original_run(*args,**kwargs);receipts.append(value);return value
    monkeypatch.setattr(native,'run',capture)
    reward=links.MaaLinkReward(tasker,lambda **fields:dict(verified=True,binding=binding.payload(),frame=1,**fields))
    with journal.locked():
        with pytest.raises(BatchStopped,match='link_result_unknown'):reward.choose(batch,1,'first-reward')
        with pytest.raises(BatchStopped,match='already_attempted'):reward.choose(batch,1,'first-reward')
    assert len(controller.clicks)==1 and not controller.swipes
    assert len(journal.events())==2 and journal.events()[-1]['state']=='settled'
    assert journal.events()[-1]['result']['input_action_verified'] is True
