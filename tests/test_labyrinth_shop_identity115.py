"""Current shop identity: native icon association, not OCR correction or live input."""
from pathlib import Path
from types import SimpleNamespace as NS
import json
import cv2,numpy as np,pytest
from pcr_maa import native
from pcr_maa.labyrinth import shop_execution as shops
from pcr_maa.labyrinth.batch import BatchStopped
from test_labyrinth_shop_execution import setup,finish,frame,offline,SHOP

ROOT=Path(__file__).resolve().parents[1]
SCREEN=ROOT/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/62756f1d249d4e28937200cab4f54e84/acceptance-ending/screen.png'
ICONS=ROOT.parent/'upstream/snapshot/android/app/src/main/assets/resource-packs/cn-bilibili/icons/relics'

def word(text):return dict(text=text,box=[1,1,10,10],score=.99)

def test_ocr_label_is_not_a_catalog_identity_and_imprint_meaning_stays_exact():
    value=dict(state='confirmation',words={'DialogBody':[word('深渊蚀杖诅咒')]})
    with pytest.raises(BatchStopped):shops._item_identity(value,'深渊蚀杖退咒','relic')
    value['selected_icon_verified']=True
    assert shops._item_identity(value,'深渊蚀杖退咒','relic')=='native_observed_icon'
    for label in ('攻击型職能的選擇印記','购买了攻击型職能的選擇印記。'):
        value['words']['DialogBody']=[word(label)]
        assert shops._item_identity(value,'攻击型职能的选择印记','role_imprint')=='imprint_semantics'
        for wrong in ('攻击型职能的随机印记','治疗型职能的选择印记','全体选择印记'):
            with pytest.raises(BatchStopped):shops._item_identity(value,wrong,'role_imprint')

@pytest.mark.parametrize('identity,price,text,passes',[
    ('native_observed_icon',2850,'购买了深渊蚀杖诅咒',True),
    ('exact_ocr',2850,'购买了深渊蚀杖诅咒',False),
    ('native_observed_icon',2851,'购买了深渊蚀杖诅咒',False),
    ('native_observed_icon',2850,'是否购买深渊蚀杖诅咒',False),
])
def test_completion_keeps_confirmed_identity_but_requires_actual_completion_amount(identity,price,text,passes):
    shop=shops.MaaShop.__new__(shops.MaaShop);shop.stamp=lambda **v:v
    quote=dict(enter_id=115,item_id='current',kind='relic',name='深渊蚀杖退咒',price=2850,mark='弱体',bonus=3,identity_method=identity)
    value=dict(state='complete',words={'DialogBody':[word('消耗阿尔法金币×'+str(price)),word(text)]})
    shop._wait_state=lambda *a:(value,{'native_ok':True})
    batch=NS(checkpoint=lambda:None);clicked=dict(operation_id='current-ack',input_native={'native_ok':True})
    if passes:assert shop.purchase_readback(batch,quote,clicked)['inventory_added']
    else:
        with pytest.raises(BatchStopped):shop.purchase_readback(batch,quote,clicked)

@pytest.mark.parametrize('correct',[True,False])
def test_actual115_confirmation_uses_native_icon_not_different_ocr_name(tmp_path,correct):
    if not SCREEN.is_file() or not (ICONS/'32002.png').is_file():pytest.skip('private actual115 and pinned source assets not distributed')
    # Synthetic shelf layout from the pinned catalog; actual115 confirmation is unmodified.
    image=np.zeros((720,1280,3),np.uint8)
    icon=cv2.imdecode(np.fromfile(ICONS/('32002.png' if correct else '50014.png'),dtype=np.uint8),cv2.IMREAD_COLOR)
    x,y,w,h=shops.SHOP_ICON_ROIS[1];image[y:y+h,x:x+w]=cv2.resize(icon,(w,h),interpolation=cv2.INTER_AREA)
    path,controller,tasker,token,shop,batch,stamp=setup(tmp_path,'icon115',[image,frame(SCREEN)])
    try:
        assert tasker.resource.override_pipeline({'LabyShopSelectedIconCapture':dict(custom_recognition_param=dict(slot=1))})
        capture=native.run(tasker,'LabyShopSelectedIconCapture')
        assert capture['native_ok'] and shop.observer.icon_capture.icon is not None
        controller.stage=1;controller.frame=0
        value,receipt=shop._capture()
        path.mkdir(parents=True,exist_ok=True)
        (path/'actual-confirmation.json').write_text(json.dumps(dict(value=value,native=receipt),ensure_ascii=False,indent=2),encoding='utf-8')
        assert value['state']=='confirmation' and value['selected_icon_verified'] is correct
        assert shops.confirmation_terms(value,mark='弱体',bonus=3)[1]==2850
        if correct:
            assert shops._item_identity(value,'深渊蚀杖退咒','relic')=='native_observed_icon'
            quote=stamp(enter_id=115,item_id='saved115',kind='relic',currency='run_coins',price=2850,balance=4790,purchasable=True,name='深渊蚀杖退咒',mark='弱体',bonus=3,identity_method='native_observed_icon')
            shop.pending=(quote,batch)
            with pytest.raises(BatchStopped,match='purchase_result_unknown'):batch.purchase(115,'saved115',quote)
            clicked=batch.journal.events()[-1]['result']
            assert clicked['input_action_verified'] and len(controller.clicks)==1 and 'inventory_added' not in clicked
        else:
            with pytest.raises(BatchStopped):shops._item_identity(value,'深渊蚀杖退咒','relic')
            assert not controller.clicks
        assert not controller.swipes
    finally:finish(tasker,token)

def test_new_refresh_clears_old_relic_template_in_existing_guard(tmp_path):
    if not SHOP.is_file():pytest.skip('pinned private shop frame not distributed')
    path,controller,tasker,token,shop,batch,stamp=setup(tmp_path,'clear115',[frame(SHOP)])
    try:
        shop.observer.icon_capture.icon=np.zeros((48,48,3),np.uint8)
        value,_=shop._capture()
        ack=shop._input('refresh',value['words']['Refresh'][0]['box'])
        assert ack['native_ok'] and shop.observer.icon_capture.icon is None and len(controller.clicks)==1
    finally:finish(tasker,token)

def test_official_shelf_proposal_captures_same_frame_icon_for_actual_dialog(tmp_path):
    from test_labyrinth_shop_execution import CONFIRM
    if not SHOP.is_file() or not CONFIRM.is_file():pytest.skip('pinned private shop frames not distributed')
    path,c,t,token,shop,batch,stamp=setup(tmp_path,'official-icon115',[frame(SHOP),frame(CONFIRM)])
    try:
        current,_=shop._capture();selected=next(i for i in current['items'] if i['price']==2520)
        ack=shop._input('shop',selected['box'],name=selected['name'],price=selected['price'])
        assert ack['native_ok'] and shop.observer.icon_capture.icon is not None
        c.stage=1;c.frame=0;dialog,_=shop._capture()
        assert dialog['selected_icon_verified'] and shops._item_identity(dialog,selected['name'],'relic')=='native_observed_icon'
        assert len(c.clicks)==1 and not c.swipes
    finally:finish(t,token)

def test_imprint_proposal_clears_old_relic_icon_and_retains_role_meaning():
    words={'Currency':[dict(text='持有阿尔法金币',box=[580,635,160,25],score=.99)],
        'Balance':[dict(text='2000',box=[850,635,70,25],score=.99)],
        'Name2':[dict(text='破防型职能的随机印记',box=[850,159,220,25],score=.99)],
        'Price2':[dict(text='900',box=[880,337,60,22],score=.99)],
        'Buy2':[dict(text='购买',box=[1010,337,80,26],score=.99)]}
    calls=[]
    def recognize(name,image):
        calls.append(name);value=words.get(name.removeprefix('LabyShopRead_'),[])
        return NS(hit=bool(value),raw_detail=dict(filtered=value))
    capture=NS(icon=np.ones((48,48,3),np.uint8))
    observer=shops.ShopRecognition(icon_capture=capture)
    result=observer.analyze(NS(run_recognition=recognize),NS(image=np.full((720,1280,3),255,np.uint8),custom_recognition_param=json.dumps(dict(state='shop',slot=2,name='破防型职能的随机印记',price=900))))
    assert result is not None and result.box is not None and capture.icon is None
    assert 'LabyShopSelectedIconCapture' not in calls and 'LabyShopConfirmedIcon' not in calls

def test_icon_identity_must_survive_the_final_input_frame_even_if_name_stays_same(tmp_path):
    if not SCREEN.is_file() or not (ICONS/'32002.png').is_file():pytest.skip('private actual115 and pinned source assets not distributed')
    path,c,t,token,shop,batch,stamp=setup(tmp_path,'fresh-identity115',[frame(SCREEN)])
    try:
        icon=cv2.imdecode(np.fromfile(ICONS/'32002.png',dtype=np.uint8),cv2.IMREAD_COLOR)
        shop.observer.icon_capture.icon=cv2.resize(icon,(80,80),interpolation=cv2.INTER_LINEAR)[16:64,16:64].copy()
        capture=shop._capture
        def read_then_change():
            value,receipt=capture()
            assert value['selected_icon_verified']
            wrong=cv2.imdecode(np.fromfile(ICONS/'50014.png',dtype=np.uint8),cv2.IMREAD_COLOR)
            shop.observer.icon_capture.icon=cv2.resize(wrong,(80,80),interpolation=cv2.INTER_LINEAR)[16:64,16:64].copy()
            return value,receipt
        shop._capture=read_then_change
        quote=stamp(enter_id=115,item_id='fresh115',kind='relic',currency='run_coins',price=2850,balance=4790,purchasable=True,name='深渊蚀杖诅咒',mark='弱体',bonus=3,identity_method='native_observed_icon')
        shop.pending=(quote,batch)
        with pytest.raises(BatchStopped,match='shop_input_unknown'):batch.purchase(115,'fresh115',quote)
        assert not c.clicks and not c.swipes
    finally:finish(t,token)

def test_shop_loop_carries_verified_icon_through_quote_spend_completion_and_close(tmp_path,monkeypatch):
    from pcr_maa.labyrinth.scene import Scene
    from pcr_maa.labyrinth.strategies import MARKS
    if not SCREEN.is_file():pytest.skip('private actual115 frame not distributed')
    path,c,t,token,shop,batch,stamp=setup(tmp_path,'loop115',[frame(SCREEN)])
    try:
        actual,_=shop._capture();actual['selected_icon_verified']=True
        # Explicitly simulated proposal/ACK/successor sequence; original115 is never resumed.
        item=dict(kind='relic',slot=1,name='深渊蚀杖退咒',price=2850,purchasable=True,box=[650,337,100,26],current_stacks=0,relic=dict(relic_id='observed115',mark='弱体',bonus=3))
        shelf=dict(state='shop',items=[item],balance=4790,refresh_allowed=False,words={});phase=['shop'];inputs=[]
        complete=dict(state='complete',words={'DialogBody':[word('消耗阿尔法金币×2850'),word('购买了深渊蚀杖诅咒。')],'Confirm':[word('确认')]})
        def capture():
            v=shelf if phase[0]=='shop' else actual if phase[0]=='confirmation' else complete
            return v,v
        def ack(state,*a,**kw):
            inputs.append((state,kw))
            if state=='shop':phase[0]='confirmation'
            elif state=='confirmation':
                assert kw['identity_method']=='native_observed_icon';phase[0]='complete'
            elif state=='complete':
                assert kw['name'] is None and kw['price']==2850
                phase[0]='shop';shelf.update(items=[],balance=1940)
            return dict(native_ok=True)
        shop._capture=capture;shop._wait_state=lambda *a:capture();shop._input=ack
        monkeypatch.setattr(shops,'_readback',lambda value:value)
        monkeypatch.setattr(shops,'shop_relic',lambda stacks,items,**kw:next(iter(items),None))
        shop.scene=NS(observer=NS(),observe=lambda:Scene('shop',{},{}),perform=lambda *a:{},wait_for=lambda *a,**kw:Scene('map',{},{}))
        result=shop.run(batch,115,30601,stacks=dict.fromkeys(MARKS,0),area=3)
        assert result['stacks']['弱体']==3 and len(result['purchases'])==1 and result['purchases'][0]['inventory_added']
        assert [x[0] for x in inputs]==['shop','confirmation','complete'] and not c.clicks
    finally:finish(t,token)
