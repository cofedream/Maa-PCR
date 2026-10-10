"""Official shop saved-frame evidence plus outer purchase intent boundaries."""
from pathlib import Path
from types import SimpleNamespace
import json,socket,sys,time
import cv2,numpy as np,pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from pcr_maa import native
from pcr_maa.replay import SavedImages
from pcr_maa.labyrinth.shop_execution import MaaShop,confirmation_terms,parse_shop_inventory
from pcr_maa.labyrinth.batch import Batch,Binding,BatchStopped
U=ROOT.parent/'upstream/snapshot/android/app/src/test/resources'
SHOP=U/'shop/live_shop_20260909.jpg';CONFIRM=U/'shop/live_purchase_confirmation_20260909.jpg';COMPLETE=U/'shop/purchase_complete.png'
pytestmark=pytest.mark.skipif(not all(p.is_file() for p in (SHOP,CONFIRM,COMPLETE)),reason='pinned upstream private shop frames not distributed')

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a,**k):raise AssertionError('network/device forbidden')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket,'create_connection',deny)
    import maa.controller
    from maa.toolkit import Toolkit
    monkeypatch.setattr(maa.controller,'AdbController',deny);monkeypatch.setattr(Toolkit,'find_adb_devices',deny)

def frame(p):return cv2.resize(cv2.imdecode(np.fromfile(p,dtype=np.uint8),cv2.IMREAD_COLOR),(1280,720),interpolation=cv2.INTER_AREA)
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
class Journal:
    def __init__(self):self.values=[]
    def events(self):return self.values[:]
    def append(self,value):self.values.append(value)
def setup(tmp_path,label,images):
    path=tmp_path/label;native.RUNTIME=path/'native';controller=SavedImages([[image] for image in images]);resource=native.load_resource(ROOT/'assets/resource');tasker=native.bind(resource,controller)
    token=native.RUN_DEADLINE.set(time.monotonic()+60);counter=[0];binding=Binding(1,'offline-saved-image-replay')
    def stamp(**values):counter[0]+=1;return dict(verified=True,binding=binding.payload(),frame=counter[0],**values)
    shop=MaaShop(tasker,stamp);port=SimpleNamespace(buy_relic=lambda quote,oid:shop.purchase_once(quote,oid,batch=batch))
    batch=Batch(SimpleNamespace(goals=(),buy_relics=True),port,Journal(),binding)
    return path,controller,tasker,token,shop,batch,stamp
def finish(tasker,token):
    try:assert native.wait_job(tasker.post_stop(),seconds=10).succeeded
    finally:native.RUN_DEADLINE.reset(token)

def test_official_inventory_confirmation_and_completion_read_from_same_frames(tmp_path):
    path,c,t,token,shop,batch,stamp=setup(tmp_path,'observed',[frame(p) for p in (SHOP,CONFIRM,COMPLETE)])
    try:
        states=[]
        for i in range(3):
            c.stage=i;c.frame=0;value,receipt=shop._capture();states.append(value['state']);save(path/f'frame-{i}.json',dict(value=value,native=receipt))
            if i==0:
                assert value['balance']==5800
                assert [item['price'] for item in value['items']]==[1620,2520,1620]
                assert [item['relic']['mark'] for item in value['items']]==['强化','守备','守备']
            elif i==1:assert confirmation_terms(value)[1]==2520
            else:
                # Original 480x270 completion OCR corrupts the currency word;
                # retaining a known modal never turns it into an added item.
                with pytest.raises(BatchStopped):confirmation_terms(value)
        assert states==['shop','confirmation','complete']
        assert not c.clicks and not c.swipes
    finally:finish(t,token)

def test_native_confirmation_one_spend_unknown_different_result_never_resends(tmp_path):
    path,c,t,token,shop,batch,stamp=setup(tmp_path,'unknown-spend',[frame(CONFIRM),frame(COMPLETE)])
    try:
        current,receipt=shop._capture();body,price=confirmation_terms(current)
        assert '魔护灵装' in body and price==2520
        quote=stamp(enter_id=1,item_id='visible-item',kind='relic',currency='run_coins',price=price,balance=5800,purchasable=True,name='魔护灵装',mark='守备',bonus=2,native=receipt,source='maa')
        shop.pending=(quote,batch)
        with pytest.raises(BatchStopped):batch.purchase(1,'visible-item',quote,readback=lambda clicked:shop.purchase_readback(batch,quote,clicked))
        save(path/'journal.json',batch.journal.events());save(path/'inputs.json',c.clicks)
        assert len(c.clicks)==1 and batch.journal.events()[-1]['state']=='settled'
        assert batch.journal.events()[-1]['result']['input_action_verified'] is True
        assert 'inventory_added' not in batch.journal.events()[-1]['result']
        with pytest.raises(BatchStopped):shop.purchase_once(quote,batch.journal.events()[-1]['operation_id'],batch=batch)
        assert len(c.clicks)==1
    finally:finish(t,token)

def test_quote_changed_or_stop_and_refresh_never_spend(tmp_path):
    path,c,t,token,shop,batch,stamp=setup(tmp_path,'guards',[frame(CONFIRM)])
    try:
        value,receipt=shop._capture();quote=stamp(enter_id=1,item_id='changed',kind='relic',currency='run_coins',price=2521,balance=5800,purchasable=True,name='魔护灵装',mark='守备',bonus=2,native=receipt)
        shop.pending=(quote,batch)
        with pytest.raises(BatchStopped,match='native_input_not_sent'):batch.purchase(1,'changed',quote)
        assert batch.journal.events()[-1]['state']=='settled'
        assert batch.journal.events()[-1]['result']['input_execution']=='not_sent'
        assert not c.clicks
        with pytest.raises(BatchStopped):shop.run(batch,1,2,refresh_shop=True)
        batch.stopped=lambda:True
        with pytest.raises(BatchStopped):shop.purchase_once(quote,'b'*32,batch=batch)
        assert not c.clicks
    finally:finish(t,token)

def test_purchase_success_is_inside_existing_intent_not_nested_and_currency_is_strict(tmp_path):
    path,c,t,token,shop,batch,stamp=setup(tmp_path,'synthetic-commit',[frame(CONFIRM)])
    try:
        def word(text):return {'text':text,'box':[1,1,10,10],'score':1}
        states=iter([dict(state='confirmation',words={'DialogBody':[word('要消耗阿尔法金币×2520购买魔护灵装【守备+2】')],'Confirm':[word('确认')]}),dict(state='complete',words={'DialogBody':[word('消耗阿尔法金币×2520购买了魔护灵装')],'Confirm':[word('确认')]})])
        seen=[]
        shop._capture=lambda:(next(states),{'native_ok':True})
        def input_once(*a,**k):
            tail=batch.journal.events()[-1];assert tail['operation']=='purchase' and tail['state']=='in_flight';seen.append(tail['operation_id']);return {'native_ok':True}
        shop._input=input_once
        quote=stamp(enter_id=1,item_id='visible',kind='relic',currency='run_coins',price=2520,balance=5800,purchasable=True,name='魔护灵装',mark='守备',bonus=2)
        shop.pending=(quote,batch);result=batch.purchase(1,'visible',quote,readback=lambda clicked:shop.purchase_readback(batch,quote,clicked))
        assert result['inventory_added'] is True and len(seen)==1
        assert [e['operation'] for e in batch.journal.events()]==['purchase','purchase']
        with pytest.raises(BatchStopped):confirmation_terms(dict(state='complete',words={'DialogBody':[word('消耗宝石×2520购买了魔护灵装')]}))
        assert not c.clicks
    finally:finish(t,token)

def test_actual_run47_purchase_complete_uses_existing_shop_native_close(tmp_path):
    captured=ROOT.parent/'candidate-r28-source/.runtime/labyrinth-work/runs/55266181d7a9446bb0b0dd335a990294/acceptance-ending/screen.png'
    if not captured.is_file():pytest.skip('private run47 evidence excluded from release')
    path,c,t,token,shop,batch,stamp=setup(tmp_path,'run47-complete',[frame(captured),frame(SHOP)])
    try:
        value,receipt=shop._capture()
        assert value['state']=='complete'
        body,price=confirmation_terms(value)
        assert price==1350 and '购买了加速玉饰' in body and '4,410' in body and '3,060' in body
        fields=dict(enter_id=47,item_id='private-observed-relic',navigation_target='shop')
        def close(oid):
            sent=shop._input('complete',value['words']['Confirm'][0]['box'],name='加速玉饰',price=price)
            return stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
        clicked=batch.transaction('shop_purchase_close',fields,close)
        assert len(c.clicks)==1 and not c.swipes and batch.journal.events()[-1]['state']=='settled'
        after,observed=shop._wait_state(batch,'shop')
        assert after['state']=='shop' and clicked['input_action_verified']
        save(path/'receipt.json',dict(before=value,native=observed,clicks=c.clicks,scope='saved frames only; no game/device input'))
    finally:finish(t,token)

def test_actual_run50_new_badge_does_not_change_quote_identity(tmp_path):
    saved=ROOT.parent/'evidence/role-chain-audit-20261005/run50-inventory-words.json'
    captured=ROOT.parent/'candidate-r28-source/.runtime/labyrinth-work/runs/0a1668e7ca704114a903e0f4f12f7a5e/acceptance-ending/screen.png'
    if not saved.is_file() or not captured.is_file():pytest.skip('private run50 evidence excluded from release')
    observed=json.loads(saved.read_text(encoding='utf-8'))
    balance,items=parse_shop_inventory(observed['words'],{int(k):v for k,v in observed['buttons'].items()})
    assert balance==4440 and [(i['name'],i['price']) for i in items]==[('炼狱战斧',2100),('杀戮之矛',1350),('冷玉之杖',1350)]
    selected=items[0]
    path,c,t,token,shop,batch,stamp=setup(tmp_path,'run50-confirm',[frame(captured)])
    try:
        current,receipt=shop._capture();body,price=confirmation_terms(current)
        assert selected['name'] in body and selected['price']==price==2100
        # A changed name or amount remains zero input; the corrected identity clicks once.
        for name,amount in (('错误道具',2100),('炼狱战斧',2101)):
            with pytest.raises(BatchStopped):shop._input('confirmation',current['words']['Confirm'][0]['box'],name=name,price=amount,mark='暴击',bonus=2)
            assert not c.clicks
        clicked=shop._input('confirmation',current['words']['Confirm'][0]['box'],name=selected['name'],price=price,mark='暴击',bonus=2)
        assert clicked['native_ok'] and len(c.clicks)==1 and not c.swipes
    finally:finish(t,token)

@pytest.mark.parametrize('attribute,accepted',[
    ('【暴击+2】（(当前：0)',True),
    ('【暴击+2】（当前：-1）',False),
    ('【暴击+2.5】（当前：0）',False),
    ('【暴击+2】（当前：1.5）',False),
])
def test_shop_reuses_existing_relic_business_attribute_parser(attribute,accepted):
    def word(text):return dict(text=text,box=[1,1,10,10],score=1)
    words=dict(Currency=[word('持有阿尔法金币')],Balance=[word('4440')],Name0=[word('NEW炼狱战斧')],Attribute0=[word(attribute)],Price0=[word('2100')])
    balance,items=parse_shop_inventory(words,{0:dict(enabled=True,box=[233,320,100,40])})
    assert balance==4440 and bool(items)==accepted
    if accepted:assert items[0]['name']=='炼狱战斧' and items[0]['relic'].bonus==2 and items[0]['current_stacks']==0
