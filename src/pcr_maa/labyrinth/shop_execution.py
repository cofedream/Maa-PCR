"""Run-coin relic shop; adapted upstream shop layout / commit semantics.

CC BY-NC-SA 4.0 for adapted source. Cards, confirmation and completion are read
from fresh Maa frames. Imprints and configured refresh share the same ACK/readback path.
The final spend runs inside Batch.purchase's existing intent, never a nested one.
"""
from dataclasses import dataclass
from hashlib import sha256
import json,math,re
import cv2
import numpy as np
from maa.custom_recognition import CustomRecognition
from .. import native
from .batch import BatchStopped
from .scene import MaaSceneRuntime,SceneRecognition,_primitive,_box,_checkpoint,runtime_resource
from .strategies import Relic,shop_relic,choose_relic
from .relics import Word,_attribute,RelicIconCapture,THRESHOLD
from .timing import Timings

NODE='LabyShopStateObserved';NAME='LabyrinthShop'
X=(56,441,825)
# Fixed upstream shop card/dialog icon geometry, mapped from 1920x1080.
SHOP_ICON_ROIS=((67,210,103,103),(453,210,103,103),(836,210,103,103))
SHOP_DIALOG_ICON_ROI=(356,279,130,130)
_READS={
 'Currency':(r'^持有阿尔法金币$',[570,625,235,48]),
 'Balance':(r'^[0-9][0-9,， ]*$',[830,625,115,48]),
 'DialogTitle':(r'^(购买确认|购买完成|购买完毕|更新确认|刷新确认|确认)$',[420,70,450,190]),
 'Refresh':(r'^(?:用[0-9,， ]+)?(?:更新|刷新)(?:商店)?$',[330,615,250,70]),
 'DialogBody':(r'.+',[310,145,650,340]),
 'Confirm':(r'^确认$',[430,420,450,190]),
}
for i,x in enumerate(X):
    # The right-hand NEW badge is outside the item title.
    _READS[f'Name{i}']=(r'.+',[x+23,153,250,46])
    _READS[f'Attribute{i}']=(r'.+',[x+113,272,202,43])
    _READS[f'Price{i}']=(r'^[0-9][0-9,， ]*$',[x+43,326,126,42])
    _READS[f'Buy{i}']=(r'^购买$',[x+177,320,195,61])

def _normalized(text):
    return re.sub(r'\s+','',text).translate(str.maketrans({'（':'(', '）':')','：':':','＋':'+','，':','}))

def _integer(text):
    if type(text) is not str or not re.fullmatch(r'[0-9][0-9,， ]*',text):raise BatchStopped('shop_amount_unknown')
    value=int(re.sub('[,， ]','',text))
    if not 0<=value<=2**63-1:raise BatchStopped('shop_amount_unknown')
    return value

def shop_pipeline():
    nodes={f'LabyShopRead_{key}':dict(recognition='OCR',expected=pattern,roi=roi,threshold=.8,
        action='DoNothing',next=[],pre_delay=0,post_delay=0) for key,(pattern,roi) in _READS.items()}
    nodes[NODE]=dict(recognition='Custom',custom_recognition=NAME,roi=[0,0,1280,720],action='DoNothing',next=[],timeout=4000,pre_delay=0,post_delay=0)
    nodes['LabyShopSelectedIconCapture']=dict(recognition='Custom',custom_recognition='LabyShopSelectedIconCapture',action='DoNothing',next=[],timeout=1500,pre_delay=0,post_delay=0)
    nodes['LabyShopConfirmedIcon']=dict(recognition='Custom',custom_recognition='LabyShopSelectedIconCapture',custom_recognition_param=dict(mode='obtained'),action='DoNothing',next=[],timeout=1500,pre_delay=0,post_delay=0)
    nodes['LabyShopConfirmedIconNative']=dict(recognition='TemplateMatch',template='LabyShopSelectedIcon.png',roi=[0,0,80,80],method=5,threshold=THRESHOLD,action='DoNothing',next=[],timeout=1500,pre_delay=0,post_delay=0)
    return nodes

def _words(detail,key):
    if detail is None or detail.hit is not True:return []
    raw=detail.raw_detail
    if type(raw) is not dict:return []
    values=raw.get('filtered')
    if type(values) is not list or not 1<=len(values)<=12:return []
    roi=_READS[key][1];out=[]
    for word in values:
        if type(word) is not dict or type(word.get('text')) is not str or not _box(word.get('box')):return []
        score=word.get('score')
        if type(score) not in (int,float):return []
        try:valid=math.isfinite(score) and .8<=score<=1
        except (ValueError,OverflowError):valid=False
        x,y,w,h=word['box']
        if not valid or not(roi[0]<=x and roi[1]<=y and x+w<=roi[0]+roi[2] and y+h<=roi[1]+roi[3]):return []
        out.append(dict(word))
    return sorted(out,key=lambda v:(v['box'][1],v['box'][0]))

def _text(words):return ''.join(w['text'] for w in words)

def confirmation_terms(observation, *, mark=None, bonus=None):
    if observation.get('state') not in ('confirmation','complete','refresh_confirmation'):raise BatchStopped('shop_quote_unknown')
    words=observation.get('words',{}).get('DialogBody',[])
    body=_normalized(_text(words))
    # Native OCR word boundaries keep unrelated background digits out of prices.
    amounts=re.findall(r'(?:要)?消耗阿尔法金币\s*[×xX*]?\s*([0-9,]+)',
        ' '.join(_normalized(word['text']) for word in words))
    # Refresh uses a required-currency row, separate from the balance below it.
    labels=[word for word in words if _normalized(word['text'])=='所需阿尔法金币']
    if labels:
        if len(labels)!=1 or not _box(labels[0].get('box')):raise BatchStopped('shop_quote_unknown')
        x,y,w,h=labels[0]['box']
        values=[word for word in words if re.fullmatch(r'[0-9][0-9,， ]*',word['text'])
            and _box(word.get('box')) and word['box'][0]>=x+w
            and word['box'][1]<y+h and word['box'][1]+word['box'][3]>y]
        if len(values)!=1:raise BatchStopped('shop_quote_unknown')
        required=_integer(values[0]['text'])
        if amounts and (len(amounts)!=1 or _integer(amounts[0])!=required):raise BatchStopped('shop_quote_unknown')
        amounts=[str(required)]
    if len(amounts)!=1:raise BatchStopped('shop_quote_unknown')
    if mark is not None:
        from .strategies import ALIASES
        # Read the series row on this frame; OCR y order can put its value before its label.
        labels=[w for w in words if _normalized(w['text'])=='系列效果']
        series=body.rsplit('系列效果',1)[-1]
        if len(labels)==1:
            x,y,w,h=labels[0]['box']
            series=_text(sorted((v for v in words if v['box'][0]>=x+w
                and v['box'][1]<y+h and v['box'][1]+v['box'][3]>y),key=lambda v:v['box'][0]))
        actual_mark,actual_bonus,_=_attribute(series,require_current=False)
        if (actual_mark,actual_bonus)!=(ALIASES.get(mark,mark),bonus):raise BatchStopped('shop_quote_changed')
    return body,_integer(amounts[0])

def imprint_identity(text):
    # Fixed upstream exact shop title classification; coarse labels never authorize spending.
    value=_normalized(text).translate(str.maketrans({'職':'职','隨':'随','選':'选','擇':'择','記':'记','紀':'记','纪':'记'}))
    if value=='全体选择印记':return dict(imprint_label='全体选择印记',choice_imprint=True,role_class=None)
    roles={'坦克':'掩护者','治疗':'治疗者','强化':'增幅者','增益':'增益者','减益':'减益者','干扰':'妨碍者','攻击':'攻击者','破防':'破防者'}
    match=re.fullmatch(r'(坦克|治疗|强化|增益|减益|干扰|攻击|破防)型?(?:[职识識職取]能|职.)的?(随机|选择)印记',value)
    if match:return dict(imprint_label=match[1]+'型职能印记',choice_imprint=match[2]=='选择',role_class=roles[match[1]])
    match=re.fullmatch(r'随机职能(?:的)?(随机|选择)印记',value)
    if match:return dict(imprint_label='随机职能印记',choice_imprint=match[1]=='选择',role_class=None)
    return None

def _item_identity(observation,name,kind=None):
    words=observation.get('words',{}).get('DialogBody',[])
    if kind=='relic' and observation.get('selected_icon_verified') is True:return 'native_observed_icon'
    if _normalized(name) in _normalized(_text(words)):return 'exact_ocr'
    if kind=='role_imprint':
        expected=imprint_identity(name)
        if expected is not None and any(imprint_identity(_normalized(w['text']).removeprefix('购买了').rstrip('。.'))==expected for w in words):
            return 'imprint_semantics'
    raise BatchStopped('shop_quote_changed')


def calibrated_stacks(stacks,items):
    actual={}
    for item in items:
        if item.get('kind')!='relic':continue
        relic=item['relic'];mark=relic.mark;value=item['current_stacks']
        if mark in actual and actual[mark]!=value:raise BatchStopped('relic_stacks_conflicting')
        actual[mark]=value
    if stacks is None:return None
    return dict(stacks,**actual)

def parse_shop_inventory(words,buttons):
    if len(words.get('Currency',[]))!=1 or _text(words['Currency'])!='持有阿尔法金币' or len(words.get('Balance',[]))!=1:raise BatchStopped('shop_currency_unknown')
    balance=_integer(_text(words['Balance']));items=[]
    for slot in range(3):
        # NEW is a card badge, absent from confirmation and acquisition names.
        name=_text(words.get(f'Name{slot}',[])).removeprefix('NEW').strip()
        price_words=words.get(f'Price{slot}',[]);button=buttons.get(slot)
        if not name or len(name)>48 or len(price_words)!=1 or button is None:continue
        price=_integer(_text(price_words))
        if price<=0:continue
        identifier='visible-shop-item:'+sha256(name.encode('utf-8')).hexdigest()[:16]
        common=dict(slot=slot,name=name,currency='run_coins',price=price,balance=balance,
            verified=True,purchasable=button['enabled'] and price<=balance,box=button['box'])
        imprint=imprint_identity(name)
        if imprint is not None:
            items.append(dict(common,kind='role_imprint',item_id=identifier,**imprint));continue
        if '印记' in name or '职能' in name:continue
        try:mark,bonus,current=_attribute(tuple(Word(w['text'],tuple(w['box']),w['score'])
            for w in sorted(words.get(f'Attribute{slot}',[]),key=lambda w:w['box'][0])))
        except BatchStopped:continue
        items.append(dict(common,kind='relic',current_stacks=current,relic=Relic(identifier,mark,bonus)))
    return balance,items

class ShopRecognition(CustomRecognition):
    def __init__(self,stopped=None,*,icon_capture=None):super().__init__();self.stopped=stopped;self.icon_capture=icon_capture
    def analyze(self,context,argv):
        if self.stopped is not None and self.stopped():return self.AnalyzeResult(None,dict(status='unknown',reason='stopped'))
        words={k:_words(context.run_recognition('LabyShopRead_'+k,argv.image),k) for k in _READS}
        if not words['Balance']:
            # Small zero glyphs need the same field enlarged before native detection.
            x,y,w,h=_READS['Balance'][1]
            image=cv2.resize(argv.image[y:y+h,x:x+w,:3],(w*4,h*4),interpolation=cv2.INTER_CUBIC)
            seen=context.run_recognition('LabyShopRead_Balance',image,{'LabyShopRead_Balance':dict(roi=[0,0,w*4,h*4])})
            raw=None if seen is None else seen.raw_detail
            if type(raw) is dict and type(raw.get('filtered')) is list:
                for word in raw['filtered']:
                    if type(word) is dict and _box(word.get('box')):
                        a,b,c,d=word['box'];left,top=a//4,b//4
                        word['box']=[x+left,y+top,(a+c+3)//4-left,(b+d+3)//4-top]
            words['Balance']=_words(seen,'Balance')
        title=_text(words.get('DialogTitle',[]));body=_normalized(_text(words.get('DialogBody',[])))
        refresh_dialog=title in ('更新确认','刷新确认','确认') and ('更新' in body or '刷新' in body)
        state='refresh_confirmation' if refresh_dialog else 'complete' if title in ('购买完成','购买完毕') else 'confirmation' if title=='购买确认' else 'shop'
        buttons={}
        for slot,x in enumerate(X):
            if len(words[f'Buy{slot}'])!=1:continue
            # Ported fixed button luma evidence; never equated with probability.
            roi=[x+177,320,195,61];a,b,w,h=roi;pixels=argv.image[b:b+h,a:a+w,:3]
            luma=float(np.mean(pixels[:,:,0]*.114+pixels[:,:,1]*.587+pixels[:,:,2]*.299))
            buttons[slot]=dict(box=words[f'Buy{slot}'][0]['box'],enabled=luma>=145,luma=luma)
        refresh=words.get('Refresh',[])
        refresh_allowed=len(refresh)==1 and _text(words.get('Balance',[])) and _integer(_text(words['Balance']))>=300
        payload=dict(state=state,words=words,buttons=buttons,same_frame=True,source='maa',refresh_allowed=bool(refresh_allowed))
        if state=='shop':
            try:
                balance,items=parse_shop_inventory(words,buttons)
                payload.update(balance=balance,items=[dict(i,relic=dict(relic_id=i['relic'].relic_id,mark=i['relic'].mark,bonus=i['relic'].bonus,recognized=True)) if i['kind']=='relic' else i for i in items])
            except BatchStopped:return self.AnalyzeResult(None,dict(payload,status='unknown'))
        elif not words.get('Confirm') or not words.get('DialogBody'):return self.AnalyzeResult(None,dict(payload,status='unknown'))
        if state=='confirmation' and self.icon_capture is not None and self.icon_capture.icon is not None:
            match=context.run_recognition('LabyShopConfirmedIcon',argv.image)
            payload['selected_icon_verified']=match is not None and match.hit is True
        param=json.loads(argv.custom_recognition_param or '{}')
        if type(param) is dict:
            if param.get('state') and param['state']!=state:return self.AnalyzeResult(None,payload)
            if state!='shop' and (param.get('price') is not None or param.get('mark') is not None):
                try:_,price=confirmation_terms(payload,mark=param.get('mark'),bonus=param.get('bonus'))
                except BatchStopped:return self.AnalyzeResult(None,payload)
                if param.get('price') is not None and price!=param['price']:return self.AnalyzeResult(None,payload)
            if param.get('name'):
                if state=='shop':
                    item=next((i for i in payload.get('items',()) if i['slot']==param.get('slot')),None)
                    if item is None or item['name']!=param['name'] or param.get('price') is not None and item['price']!=param['price']:return self.AnalyzeResult(None,payload)
                else:
                    kind='relic' if param.get('mark') is not None else 'role_imprint' if imprint_identity(param['name']) else None
                    try:identity=_item_identity(payload,param['name'],kind)
                    except BatchStopped:return self.AnalyzeResult(None,payload)
                    if param.get('identity_method')=='native_observed_icon' and identity!='native_observed_icon':return self.AnalyzeResult(None,payload)
            if state=='shop' and param.get('state')=='shop' and self.icon_capture is not None:
                # A new proposal/refresh cannot inherit a previous item's template.
                self.icon_capture.icon=None
                if param.get('name') and item['kind']=='relic':
                    captured=context.run_recognition('LabyShopSelectedIconCapture',argv.image)
                    if captured is None or not captured.hit:return self.AnalyzeResult(None,payload)
        return self.AnalyzeResult([0,0,1280,720],dict(payload,status='observed'))

def _readback(receipt):
    if receipt.get('native_ok') is not True or receipt.get('tasker_inited') is not True or type(receipt.get('task_id')) is not int:raise BatchStopped('shop_readback_unknown')
    nodes=receipt.get('nodes');node=next((n for n in nodes or [] if n.get('name')==NODE and n.get('completed') is True),None)
    raw=node.get('recognition') if node else None
    if type(raw) is not dict or set(raw)!={'all','filtered','best'} or type(raw['filtered']) is not list or len(raw['filtered'])!=1 or raw['all']!=raw['filtered'] or raw['best']!=raw['filtered'][0]:raise BatchStopped('shop_readback_unknown')
    value=raw['best'].get('detail')
    if type(value) is not dict or value.get('status')!='observed' or value.get('same_frame') is not True:raise BatchStopped('shop_readback_unknown')
    return value

class MaaShop:
    def __init__(self,tasker,stamp,*,timings=None):
        self.tasker=tasker;self.stamp=stamp;self.timings=timings or Timings();self.blocked=False;self.pending=None;self.spent=set()
        resource=runtime_resource(tasker)
        self.observer=getattr(resource,'_labyrinth_shop_observer',None)
        if self.observer is None:
            icon=RelicIconCapture(icon_rois=SHOP_ICON_ROIS,image_name='LabyShopSelectedIcon.png',
                match_node='LabyShopConfirmedIconNative',match_roi=SHOP_DIALOG_ICON_ROI)
            self.observer=ShopRecognition(icon_capture=icon)
            if (not resource.override_pipeline(shop_pipeline()) or not resource.register_custom_recognition(NAME,self.observer)
                or not resource.register_custom_recognition('LabyShopSelectedIconCapture',icon)):raise BatchStopped('shop_install_failed')
            resource._labyrinth_shop_observer=self.observer
        self.scene=MaaSceneRuntime(tasker,stamp,timings=self.timings)

    def _capture(self):
        with self.timings.measure('page_wait'):receipt=native.run(self.tasker,NODE)
        return _readback(receipt),receipt

    def _wait_state(self,batch,expected):
        import time
        from .autoplay import _poll_pause
        deadline=min(native.RUN_DEADLINE.get(),time.monotonic()+30)
        token=native.RUN_DEADLINE.set(deadline)
        try:
            while True:
                batch.checkpoint()
                if time.monotonic()>=deadline:raise BatchStopped('shop_readback_unknown')
                try:current,receipt=self._capture()
                except BatchStopped as error:
                    if error.args!=('shop_readback_unknown',):raise
                else:
                    if time.monotonic()<deadline and current['state']==expected:return current,receipt
                _poll_pause(self.tasker,batch,'LabyShopPollSpacing','shop_readback_unknown')
        except RuntimeError as error:
            if error.args not in (('Native task exceeded 90 seconds or inspection deadline',),
                    ('Inspection deadline exceeded before task input',),
                    ('Context deadline exceeded after synchronous return; outcome unknown',)):raise
            batch.checkpoint();raise BatchStopped('shop_readback_unknown') from error
        finally:native.RUN_DEADLINE.reset(token)

    def _input(self,state,box,*,name=None,price=None,mark=None,bonus=None,identity_method=None):
        if not _box(box):raise native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True,reason='shop_input_unknown'))
        if state=='shop':
            slots=[i for i in range(3) if _box(box) and box[0]>=_READS[f'Buy{i}'][1][0] and box[0]+box[2]<=_READS[f'Buy{i}'][1][0]+_READS[f'Buy{i}'][1][2]]
            if len(slots)!=1:raise native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True,reason='shop_input_unknown'))
            roi=_READS[f'Buy{slots[0]}'][1]
        elif state=='refresh':roi=_READS['Refresh'][1]
        else:roi=_READS['Confirm'][1]
        nodes={'LabyShopActionControl':dict(recognition='OCR',expected=r'^购买$' if state=='shop' else _READS['Refresh'][0] if state=='refresh' else r'^确认$',roi=roi,threshold=.8,action='DoNothing',next=[]),
          'LabyShopActionGuard':dict(recognition='Custom',custom_recognition=NAME,custom_recognition_param=dict(state='shop' if state=='refresh' else state,slot=slots[0] if state=='shop' else None,name=name,price=price,mark=mark,bonus=bonus,identity_method=identity_method),roi=[0,0,1280,720],action='DoNothing',next=[]),
          'LabyShopActionOnce':dict(recognition='And',all_of=['LabyShopActionControl','LabyShopActionGuard'],action='Click',target=True,max_hit=1,next=[],timeout=3000,pre_delay=0,post_delay=0)}
        if state=='shop':nodes['LabyShopSelectedIconCapture']=dict(custom_recognition_param=dict(slot=slots[0]))
        if not self.tasker.resource.override_pipeline(nodes):raise native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True,reason='shop_input_unknown'))
        with self.timings.measure('page_wait'):receipt=native.run(self.tasker,'LabyShopActionOnce',reset_hits=('LabyShopActionOnce',))
        node=next((n for n in receipt.get('nodes',[]) if n.get('name')=='LabyShopActionOnce' and n.get('completed') is True),None)
        if receipt.get('native_ok') is not True or node is None or node.get('action',{}).get('success') is not True:raise BatchStopped('shop_input_unknown')
        return receipt

    def purchase_once(self,quote,operation_id,*,batch=None):
        if self.blocked or self.pending is None:raise BatchStopped('shop_purchase_unbound')
        stored,active=self.pending;batch=batch or active
        events=batch.journal.events();intent=events[-1] if events else {}
        if (batch is not active or quote!=stored or type(operation_id) is not str or operation_id in self.spent
            or intent.get('state')!='in_flight' or intent.get('operation')!='purchase' or intent.get('operation_id')!=operation_id
            or intent.get('item_id')!=quote.get('item_id') or intent.get('price')!=quote.get('price')):raise BatchStopped('shop_purchase_unbound')
        batch.checkpoint();self.spent.add(operation_id)
        try:
            receipt=None
            try:
                current,receipt=self._capture();body,price=confirmation_terms(current,mark=quote.get('mark'),bonus=quote.get('bonus'))
                if current['state']!='confirmation' or price!=quote['price']:raise BatchStopped('shop_quote_changed')
                identity=_item_identity(current,quote['name'],quote['kind'])
                if quote.get('identity_method')=='native_observed_icon' and identity!='native_observed_icon':raise BatchStopped('shop_quote_changed')
                confirms=current['words']['Confirm']
                if len(confirms)!=1:raise BatchStopped('shop_quote_changed')
            except BatchStopped as error:
                if str(error)=='stopped':raise
                # Nothing has been submitted to the input node on this path.
                raise native.NativeInputNotSent(dict(input_execution='not_sent',
                    pre_input_readback_failed=True,reason=str(error),observation=receipt)) from None
            sent=self._input('confirmation',confirms[0]['box'],name=quote['name'],price=quote['price'],mark=quote.get('mark'),bonus=quote.get('bonus'),identity_method=quote.get('identity_method'))
            self.pending=None
            return self.stamp(operation_id=operation_id,enter_id=quote['enter_id'],item_id=quote['item_id'],price=quote['price'],
                name=quote['name'],mark=quote.get('mark'),bonus=quote.get('bonus'),currency='run_coins',native=sent,input_native=sent,input_action_verified=True,source='maa')
        except BaseException:self.blocked=True;raise

    def purchase_readback(self,batch,quote,clicked):
        batch.checkpoint();result,receipt=self._wait_state(batch,'complete');body,price=confirmation_terms(result)
        if '购买了' not in body or price!=quote['price']:raise BatchStopped('purchase_result_unknown')
        if quote.get('identity_method')!='native_observed_icon':
            try:_item_identity(result,quote['name'],quote['kind'])
            except BatchStopped:raise BatchStopped('purchase_result_unknown') from None
        return self.stamp(operation_id=clicked['operation_id'],enter_id=quote['enter_id'],item_id=quote['item_id'],price=quote['price'],
            inventory_added=True,name=quote['name'],mark=quote.get('mark'),bonus=quote.get('bonus'),currency='run_coins',
            native=receipt,input_native=clicked['input_native'],input_action_verified=True,source='maa')

    buy_relic=purchase_once

    def _refresh(self,batch,enter_id,block_id,current,cycle):
        words=current.get('words',{}).get('Refresh',())
        if len(words)!=1 or not current.get('refresh_allowed') or re.sub(r'[^0-9]','',words[0]['text'])!='300':
            raise BatchStopped('shop_refresh_quote_unknown')
        fields=dict(enter_id=enter_id,block_id=block_id,cycle=cycle,price=300,currency='run_coins')
        def proposal(oid):
            sent=self._input('refresh',words[0]['box'])
            return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
        clicked=batch.transaction('shop_refresh_proposal',fields,proposal)
        dialog,receipt=self._wait_state(batch,'refresh_confirmation')
        body,price=confirmation_terms(dialog)
        if price!=300 or not ('更新' in body or '刷新' in body):raise BatchStopped('shop_refresh_quote_unknown')
        batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,native=receipt,input_native=clicked['input_native']),**fields)
        def confirm(oid):
            sent=self._input('refresh_confirmation',dialog['words']['Confirm'][0]['box'],price=300)
            return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
        clicked=batch.transaction('shop_refresh_purchase',fields,confirm)
        result,receipt=self._wait_state(batch,'shop')
        if result['balance']!=current['balance']-300:raise BatchStopped('shop_refresh_result_unknown')
        return batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,refresh_verified=True,native=receipt,input_native=clicked['input_native']),**fields)

    def _imprint_following(self,batch,enter_id,identifier,owned,characters,selected):
        from .rewards import MaaRoleReward
        if characters is None:raise BatchStopped('character_callback_unavailable')
        pending=[];joined=[];scene=self.scene.wait_for(batch,{'shop','joined','free_role_reward','event_animation'},source='shop_complete',accept=lambda value:value.kind!='shop',timeout=30,reason='shop_imprint_result_unknown',acquisition_fields=dict(source='maa',enter_id=enter_id,item_id=identifier))
        for step in range(16):
            batch.checkpoint()
            if scene.kind=='shop':
                if not joined or pending:raise BatchStopped('shop_imprint_result_unknown')
                return joined
            if scene.kind=='free_role_reward':
                if not selected['choice_imprint'] or pending:raise BatchStopped('shop_imprint_result_unknown')
                reward=MaaRoleReward(self.tasker,self.stamp,timings=self.timings,characters=characters)
                result=reward.choose_free(batch,enter_id,identifier,tuple(owned),defer_joined=True)
                pending=list(result['roles_pending']);scene=result['following_scene'];continue
            fields=dict(enter_id=enter_id,item_id=identifier,phase=scene.kind,step=step)
            if scene.kind=='joined':
                actual,receipt=characters.read(batch,'joined',stable=False)
                roles=actual['roles_joined']
                if pending and not set(roles)<=set(pending):raise BatchStopped('shop_imprint_result_unknown')
                if selected.get('role_class'):
                    from .roles import load_optimizer
                    profiles=load_optimizer().profiles
                    if any(role not in profiles or profiles[role].data.get('roleClass')!=selected['role_class'] for role in roles):raise BatchStopped('shop_imprint_result_unknown')
                joined.extend(role for role in roles if role not in joined)
                pending=[role for role in pending if role not in roles]
                batch.observation(self.stamp(**fields,roles_joined=roles,inventory_added=True,native=receipt),**fields)
                action='close_reward'
            else:action='advance_character_animation'
            def advance(oid):
                sent=self.scene.perform(scene,action)
                return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
            clicked=batch.transaction('shop_imprint_'+action,fields,advance)
            previous_roles=tuple(roles) if scene.kind=='joined' else None
            def changed(value):
                if value.kind=='shop':return bool(joined) and not pending
                if value.kind!='joined' or previous_roles is None:return True
                actual=characters.probe(batch,'joined')
                if actual is None:return False
                return tuple(actual['roles_joined'])!=previous_roles
            scene=self.scene.wait_for(batch,{'shop','joined','free_role_reward','event_animation'},source=scene.kind,accept=changed,timeout=30,reason='shop_imprint_result_unknown',acquisition_fields=dict(source='maa',enter_id=enter_id,item_id=identifier))
            batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,page=scene.kind,native=scene.native,input_native=clicked['input_native']),**fields)
        raise BatchStopped('shop_imprint_result_unknown')

    def run(self,batch,enter_id,block_id,*,owned=(),stacks=None,context=None,buy_relics=True,refresh_shop=False,area=None,locked_focus=None,priority=(),characters=None):
        if self.blocked:raise BatchStopped('shop_blocked')
        self.observer.stopped=batch.stopped;self.scene.stopped=batch.stopped;self.scene.observer.stopped=batch.stopped;observed_stacks=None if stacks is None else dict(stacks);purchased=[];calibrated_marks=set();roles=list(owned);refreshed=False;cycle=0;relic_cycle=0
        from .roles import strategy_value
        strategy=getattr(batch.settings,'strategy',None)
        policy={key:strategy_value(strategy,key,default) for key,default in (('baseline_last_area',3),('baseline_target',4),('debuff_pivot_minimum',12),('debuff_pivot_tolerance',2))}
        priority=priority or strategy_value(strategy,'relic_mark_priority',())
        try:
            for _ in range(100):
                batch.checkpoint();current,receipt=self._capture()
                if current['state']!='shop':raise BatchStopped('shop_initial_page_unknown')
                items=[dict(i,relic=Relic(**i['relic'])) if i['kind']=='relic' else dict(i) for i in current['items']]
                observed_stacks=calibrated_stacks(observed_stacks,items)
                calibrated_marks.update(i['relic'].mark for i in items if i['kind']=='relic')
                if not buy_relics or area==4:break
                exhausted=relic_cycle>=3 or len(items)==3 and all(i['kind']=='role_imprint' for i in items)
                if exhausted and refresh_shop and area is not None and area>=strategy_value(strategy,'refresh_from_area',5) and current.get('refresh_allowed'):
                    self._refresh(batch,enter_id,block_id,current,cycle);cycle+=1;relic_cycle=0;refreshed=True;continue
                selected=None if observed_stacks is None else shop_relic(observed_stacks,items,balance=current['balance'],buy=buy_relics,area=area,locked_focus=locked_focus,priority=priority,**policy)
                if selected is None:
                    selected=min((i for i in items if i['kind']=='role_imprint' and i['purchasable']),key=lambda i:(i['choice_imprint'],i['slot']),default=None)
                if selected is None:break
                identity=selected['relic'].relic_id if selected['kind']=='relic' else selected['item_id']
                identifier=f'{enter_id}:{block_id}:{cycle}:{len(purchased)}:{identity}'
                fields=dict(enter_id=enter_id,item_id=identifier,phase='shop_confirmation')
                def proposal(oid):
                    sent=self._input('shop',selected['box'],name=selected['name'],price=selected['price'])
                    return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True,source='maa')
                clicked=batch.transaction('shop_proposal',fields,proposal)
                batch.checkpoint();dialog,dialog_receipt=self._wait_state(batch,'confirmation')
                body,amount=confirmation_terms(dialog)
                try:identity_method=_item_identity(dialog,selected['name'],selected['kind'])
                except BatchStopped:raise BatchStopped('shop_quote_unknown') from None
                if amount!=selected['price']:raise BatchStopped('shop_quote_unknown')
                batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,native=dialog_receipt,input_native=clicked['input_native'],source='maa'),**fields)
                # Stamp actual card/confirmation observations, not configured money.
                quote=self.stamp(enter_id=enter_id,item_id=identifier,kind=selected['kind'],currency='run_coins',price=selected['price'],balance=current['balance'],purchasable=True,
                    name=selected['name'],identity_method=identity_method,mark=selected['relic'].mark if selected['kind']=='relic' else None,bonus=selected['relic'].bonus if selected['kind']=='relic' else None,slot=selected['slot'],native=receipt,source='maa')
                self.pending=(quote,batch)
                if selected['kind']=='relic':
                    result=batch.purchase(enter_id,identifier,quote,readback=lambda clicked:self.purchase_readback(batch,quote,clicked))
                    decision=choose_relic(observed_stacks,[i['relic'] for i in items if i['kind']=='relic' and i['purchasable']],locked_focus=locked_focus,area=area,priority=priority,**policy)
                    if decision.phase!='baseline':locked_focus=decision.focus
                    relic_cycle+=1;observed_stacks[selected['relic'].mark]=min(30,observed_stacks[selected['relic'].mark]+selected['relic'].bonus)
                else:
                    batch.observation(quote,enter_id=enter_id,item_id=identifier,kind='role_imprint',currency='run_coins')
                    fields_purchase=dict(enter_id=enter_id,item_id=identifier,price=quote['price'])
                    clicked_purchase=batch.transaction('purchase',fields_purchase,lambda oid:self.purchase_once(quote,oid,batch=batch))
                    result=batch.observation(self.purchase_readback(batch,quote,clicked_purchase),**fields_purchase)
                purchased.append(result)
                close_fields=dict(enter_id=enter_id,item_id=identifier,navigation_target='shop')
                complete=_readback(result['native'])
                confirm=complete['words']['Confirm'][0]['box']
                def close(oid):
                    sent=self._input('complete',confirm,name=None if quote.get('identity_method')=='native_observed_icon' else quote['name'],price=quote['price'])
                    return self.stamp(operation_id=oid,**close_fields,native=sent,input_native=sent,input_action_verified=True,source='maa')
                clicked=batch.transaction('shop_purchase_close',close_fields,close)
                if selected['kind']=='role_imprint':
                    joined=self._imprint_following(batch,enter_id,identifier,roles,characters,selected)
                    roles.extend(role for role in joined if role not in roles)
                    _,after=self._wait_state(batch,'shop')
                else:_,after=self._wait_state(batch,'shop')
                batch.observation(self.stamp(operation_id=clicked['operation_id'],**close_fields,page='shop',native=after,input_native=clicked['input_native'],source='maa'),**close_fields)
            else:raise BatchStopped('shop_action_limit')
            fields=dict(enter_id=enter_id,block_id=block_id,phase='shop_leave')
            scene=self.scene.observe()
            def leave(oid):
                sent=self.scene.perform(scene,'leave_shop')
                return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True,source='maa')
            clicked=batch.transaction('shop_leave',fields,leave)
            after=self.scene.wait_for(batch,{'map','shop_exit_confirmation'},source='shop',timeout=30,reason='shop_exit_unknown')
            result=batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,page=after.kind,native=after.native,input_native=clicked['input_native'],source='maa'),**fields)
            if result['page']=='shop_exit_confirmation':
                fields=dict(enter_id=enter_id,block_id=block_id,navigation_target='map')
                scene=self.scene.observe()
                if scene.kind!='shop_exit_confirmation':raise BatchStopped('shop_exit_unknown')
                def confirm_leave(oid):
                    sent=self.scene.perform(scene,'confirm_shop_exit')
                    return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True,source='maa')
                clicked=batch.transaction('shop_leave_confirm',fields,confirm_leave)
                after=self.scene.wait_for(batch,{'map'},source={'shop_exit_confirmation','shop'},timeout=30,reason='shop_exit_unknown')
                result=batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,page='map',native=after.native,input_native=clicked['input_native'],source='maa'),**fields)
            return dict(result,stacks=observed_stacks,roles=roles,purchases=purchased,refresh_performed=refreshed,focus=locked_focus,calibrated_marks=sorted(calibrated_marks))
        except BaseException:self.blocked=True;raise

# Public facade kept beside shop for the unified autoplay actions API.
from .event_execution import MaaEvent
