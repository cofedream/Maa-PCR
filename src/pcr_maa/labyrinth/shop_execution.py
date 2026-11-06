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
from .scene import MaaSceneRuntime,SceneRecognition,OBSERVATION_TIMEOUT_SECONDS,_primitive,_box,_checkpoint,runtime_resource,_RULES
from .strategies import Relic,shop_relic,choose_relic
from .relics import Word,_attribute,RelicIconCapture,THRESHOLD
from .timing import Timings

NODE='LabyShopStateObserved';NAME='LabyrinthShop'
# Keep every conflicting page; scoped recognition short-circuits missing anchors.
_EXIT_PAGES=frozenset(_RULES)
X=(56,441,825)
# Fixed upstream shop card/dialog icon geometry, mapped from 1920x1080.
SHOP_ICON_ROIS=((67,210,103,103),(453,210,103,103),(836,210,103,103))
SHOP_DIALOG_ICON_ROI=(356,279,130,130)
# Upstream 1920x1080 reference (500,930,350,85) mapped for the 1280x720 capture.
# A locked/finished refresh keeps its "用N更新" text and frame but dims: live
# frames measure ~163 inner luma while actionable and ~100 once the per-shop
# refresh cap is reached, so OCR text/price alone cannot prove the control is
# clickable. Only the inner blue body is sampled to avoid the bright border.
REFRESH_BUTTON_RECT=(333,620,233,56)
DISABLED_BUTTON_LUMA=110.0
ENABLED_BUTTON_LUMA=145.0
MIN_ENABLED_BUTTON_EVIDENCE=.50
# The confirmation dialog shows within a couple of reads on live machines; a
# disabled refresh never shows it at all, so a bounded probe must classify the
# click as refused instead of spending the generic 30s readback budget.
REFRESH_CONFIRM_BUDGET=12.
REFRESH_LATE_BUDGET=8.

def refresh_enabled_evidence(image):
    """Upstream-parity inner-luma evidence for the refresh control, or None.

    None means the frame does not cover the fixed 1280x720 geometry; synthetic
    and offscreen images then keep the legacy OCR-only evidence path.
    """
    try:height,width=image.shape[:2]
    except AttributeError:return None
    x,y,w,h=REFRESH_BUTTON_RECT
    if width<x+w or height<y+h:return None
    left=x+round(w*.15);right=x+round(w*.85);top=y+round(h*.20);bottom=y+round(h*.80)
    pixels=image[top:bottom:3,left:right:3,:3]
    if pixels.size==0:return None
    # Maa frames are BGR; Rec.709 luma, matching the upstream Android evidence.
    luma=float(np.mean(pixels[:,:,0]*.0722+pixels[:,:,1]*.7152+pixels[:,:,2]*.2126))
    return max(0.,min(1.,(luma-DISABLED_BUTTON_LUMA)/(ENABLED_BUTTON_LUMA-DISABLED_BUTTON_LUMA)))

def _same_shop_row(left,right):
    def row(value):
        items=value.get('items') or ()
        return tuple(sorted((item.get('slot'),item.get('kind'),item.get('name'),item.get('price'),
            item.get('purchasable'),item.get('verified')) for item in items if isinstance(item,dict)))
    return row(left)==row(right) and left.get('stock_state')==right.get('stock_state')

def _same_shop(left,right,price):
    return (left.get('balance')==right.get('balance') and refresh_quote(right)==price
        and _same_shop_row(left,right))

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

def _refresh_price(words):
    if len(words)!=1:return None
    match=re.fullmatch(r'用([0-9][0-9,]*)(?:更新|刷新)(?:商店)?',_normalized(words[0]['text']))
    if match is None:return None
    try:price=_integer(match[1])
    except BatchStopped:return None
    return price if price>0 else None

def refresh_quote(observation):
    """Visible refresh price for this frame; None when the label carries no number."""
    words=observation.get('words',{}).get('Refresh',())
    if type(words) not in (list,tuple) or len(words)!=1 or type(words[0]) is not dict:return None
    return _refresh_price(words)

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

def _stock_state(words,buttons,items):
    known={item['slot']:item for item in items};unknown=False;available=False
    for slot in range(3):
        button=buttons.get(slot)
        if button is None:unknown=True;continue
        if not button['enabled']:continue
        item=known.get(slot)
        if (item is not None and item['kind']=='role_imprint'
                or imprint_identity(_text(words.get(f'Name{slot}',[]))) is not None):continue
        if item is not None:available=True
        else:unknown=True
    return 'unknown' if unknown else 'ready' if available else 'exhausted'


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
        # The price grows per refresh (live frames: 300 then 600), never a fixed coin amount.
        refresh_price=refresh_quote(dict(words=words))
        try:balance=_integer(_text(words['Balance'])) if len(words.get('Balance',[]))==1 else None
        except BatchStopped:balance=None
        # The button keeps its label/price when it is no longer actionable, so the
        # inner-luma evidence is required in addition to OCR and balance. A frame
        # that does not cover the fixed geometry keeps the legacy OCR-only result.
        evidence=refresh_enabled_evidence(argv.image) if state=='shop' else None
        refresh_enabled=None if evidence is None else evidence>=MIN_ENABLED_BUTTON_EVIDENCE
        refresh_allowed=(refresh_price is not None and balance is not None
            and balance>=refresh_price and refresh_enabled is not False)
        refresh_known=(refresh_price is not None and balance is not None or balance==0
            or refresh_enabled is False)
        payload=dict(state=state,words=words,buttons=buttons,same_frame=True,source='maa',
            refresh_allowed=bool(refresh_allowed),refresh_price=refresh_price,refresh_known=bool(refresh_known),
            refresh_enabled=refresh_enabled,
            refresh_enabled_evidence=None if evidence is None else round(evidence,4))
        if state=='shop':
            try:
                balance,items=parse_shop_inventory(words,buttons)
                payload['stock_state']=_stock_state(words,buttons,items)
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

    def _wait_state(self,batch,expected,*,accept=None):
        import time
        from .autoplay import _poll_pause
        deadline=min(native.RUN_DEADLINE.get(),native.active_now()+30)
        token=native.RUN_DEADLINE.set(deadline)
        try:
            while True:
                batch.checkpoint()
                if native.active_now()>=deadline:raise BatchStopped('shop_readback_unknown')
                try:current,receipt=self._capture()
                except BatchStopped as error:
                    if error.args!=('shop_readback_unknown',):raise
                else:
                    batch.checkpoint()
                    if (native.active_now()<deadline and current['state']==expected
                            and (accept is None or accept(current))):return current,receipt
                _poll_pause(self.tasker,batch,'LabyShopPollSpacing','shop_readback_unknown')
        except RuntimeError as error:
            if error.args not in (('Native task exceeded 90 seconds or inspection deadline',),
                    ('Inspection deadline exceeded before task input',),
                    ('Context deadline exceeded after synchronous return; outcome unknown',)):raise
            batch.checkpoint();raise BatchStopped('shop_readback_unknown') from error
        finally:native.RUN_DEADLINE.reset(token)

    def _probe_state(self,batch,expected,*,budget=REFRESH_CONFIRM_BUDGET):
        """Bounded inspection that returns None instead of a fatal readback error.

        Used only for the optional refresh transition: "the expected dialog never
        appeared" is a business result the caller classifies, not an unknown page.
        """
        from .autoplay import _poll_pause
        deadline=min(native.RUN_DEADLINE.get(),native.active_now()+budget)
        token=native.RUN_DEADLINE.set(deadline)
        try:
            while True:
                batch.checkpoint()
                if native.active_now()>=deadline:return None,None
                try:current,receipt=self._capture()
                except BatchStopped as error:
                    if error.args!=('shop_readback_unknown',):raise
                else:
                    batch.checkpoint()
                    if native.active_now()<deadline and current.get('state')==expected:return current,receipt
                _poll_pause(self.tasker,batch,'LabyShopPollSpacing','shop_readback_unknown')
        except RuntimeError as error:
            if error.args not in (('Native task exceeded 90 seconds or inspection deadline',),
                    ('Inspection deadline exceeded before task input',),
                    ('Context deadline exceeded after synchronous return; outcome unknown',)):raise
            batch.checkpoint();return None,None
        finally:native.RUN_DEADLINE.reset(token)

    def _capture_once(self,batch,*,budget=REFRESH_LATE_BUDGET):
        """One bounded read for post-click classification; None when unreadable."""
        token=native.RUN_DEADLINE.set(min(native.RUN_DEADLINE.get(),native.active_now()+budget))
        try:return self._capture()
        except BatchStopped as error:
            if error.args!=('shop_readback_unknown',):raise
            return None,None
        except RuntimeError as error:
            if error.args not in (('Native task exceeded 90 seconds or inspection deadline',),
                    ('Inspection deadline exceeded before task input',),
                    ('Context deadline exceeded after synchronous return; outcome unknown',)):raise
            batch.checkpoint();return None,None
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
        price=refresh_quote(current)
        if (len(words)!=1 or price is None or not current.get('refresh_allowed')
                or current.get('refresh_enabled') is False
                or type(current.get('balance')) is not int or current['balance']<price):
            raise BatchStopped('shop_refresh_quote_unknown')
        fields=dict(enter_id=enter_id,block_id=block_id,cycle=cycle,price=price,currency='run_coins')
        def proposal(oid):
            sent=self._input('refresh',words[0]['box'])
            return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
        clicked=batch.transaction('shop_refresh_proposal',fields,proposal)
        dialog,receipt=self._probe_state(batch,'refresh_confirmation')
        refused=False;late=late_receipt=effective=None
        if dialog is None:
            # A very slow machine can still surface the dialog after the probe
            # budget; read fresh, bounded frames before classifying the click.
            late,late_receipt=self._capture_once(batch)
            if late is not None and late.get('state')=='refresh_confirmation':
                dialog,receipt=late,late_receipt
            elif late is not None and late.get('state')=='shop':
                if _same_shop(current,late,price):
                    # Require a second clean, unchanged read so one OCR flicker
                    # cannot turn an optional refresh into a batch stop.
                    again,again_receipt=self._capture_once(batch)
                    if again is not None and again.get('state')=='refresh_confirmation':
                        dialog,receipt=again,again_receipt
                    elif again is not None and again.get('state')=='shop' and _same_shop(late,again,price):
                        refused=True;late,late_receipt=again,again_receipt
                    else:raise BatchStopped('shop_refresh_result_unknown')
                elif late.get('balance')==current.get('balance')-price:
                    # No dialog, but the coins were deducted exactly once: the
                    # refresh took effect without the dialog. Never re-click.
                    effective=late
                else:raise BatchStopped('shop_refresh_result_unknown')
            else:raise BatchStopped('shop_refresh_result_unknown')
        if dialog is not None:
            body,actual=confirmation_terms(dialog)
            if actual!=price or not ('更新' in body or '刷新' in body):raise BatchStopped('shop_refresh_quote_unknown')
            batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,native=receipt,input_native=clicked['input_native']),**fields)
            def confirm(oid):
                sent=self._input('refresh_confirmation',dialog['words']['Confirm'][0]['box'],price=price)
                return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True)
            clicked=batch.transaction('shop_refresh_purchase',fields,confirm)
            result,receipt=self._wait_state(batch,'shop')
            if result['balance']!=current['balance']-price:raise BatchStopped('shop_refresh_result_unknown')
            return batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,refresh_verified=True,native=receipt,input_native=clicked['input_native']),**fields)
        # The click was sent, but the game never opened the confirmation dialog.
        # This is the per-shop refresh cap / a locked control on live builds: the
        # label keeps its price and the shop page stays unchanged. Treat it as a
        # normal "refresh unavailable" business result so the shop can still buy
        # imprints or leave instead of aborting the whole batch.
        if refused:
            batch.emit_log('商店刷新无响应，按不可刷新继续')
            return batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,
                refresh_refused=True,native=late_receipt,input_native=clicked['input_native']),**fields)
        if effective is not None:
            return batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,
                refresh_verified=True,refresh_confirmation_missing=True,native=late_receipt,
                input_native=clicked['input_native']),**fields)
        raise BatchStopped('shop_refresh_result_unknown')

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
                if not selected['choice_imprint']:raise BatchStopped('shop_imprint_result_unknown')
                reward=MaaRoleReward(self.tasker,self.stamp,timings=self.timings,characters=characters)
                reward_id=identifier if not pending and not joined else f'{identifier}:free:{step}'
                result=reward.choose_free(batch,enter_id,reward_id,tuple(owned)+tuple(joined)+tuple(pending),defer_joined=True)
                pending.extend(result['roles_pending']);scene=result['following_scene'];continue
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
            clicked=None
            try:clicked=batch.transaction('shop_imprint_'+action,fields,advance)
            except BatchStopped as error:
                cause=error.__cause__
                receipt=cause.receipt if isinstance(cause,native.NativeInputNotSent) else {}
                proof=receipt.get('native_evidence',{})
                if (action!='advance_character_animation' or error.args!=('native_input_not_sent',)
                        or receipt.get('input_execution')!='not_sent'
                        or receipt.get('pre_input_validation_failed') is True
                        or proof.get('entry')!='LabySceneActionOnce'
                        or proof.get('recognition_failed') is not True
                        or proof.get('action_started') is not False):raise
            previous_roles=tuple(roles) if scene.kind=='joined' else None
            def changed(value):
                if value.kind=='shop':return bool(joined) and not pending
                if value.kind!='joined' or previous_roles is None:return True
                actual=characters.probe(batch,'joined')
                if actual is None:return False
                return tuple(actual['roles_joined'])!=previous_roles
            scene=self.scene.wait_for(batch,{'shop','joined','free_role_reward','event_animation'},source=scene.kind,accept=changed,timeout=30,reason='shop_imprint_result_unknown',acquisition_fields=dict(source='maa',enter_id=enter_id,item_id=identifier))
            if clicked is not None:
                batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,page=scene.kind,native=scene.native,input_native=clicked['input_native']),**fields)
        raise BatchStopped('shop_imprint_result_unknown')

    def run(self,batch,enter_id,block_id,*,owned=(),stacks=None,context=None,buy_relics=True,refresh_shop=False,area=None,locked_focus=None,priority=(),characters=None,final_shop=False):
        if self.blocked:raise BatchStopped('shop_blocked')
        self.observer.stopped=batch.stopped;self.scene.stopped=batch.stopped;self.scene.observer.stopped=batch.stopped;observed_stacks=None if stacks is None else dict(stacks);purchased=[];calibrated_marks=set();roles=list(owned);refreshed=False;cycle=0;relic_cycle=0;refresh_unavailable=False
        from .roles import strategy_value
        strategy=getattr(batch.settings,'strategy',None)
        policy={key:strategy_value(strategy,key,default) for key,default in (('baseline_last_area',3),('baseline_target',4),('debuff_pivot_minimum',12),('debuff_pivot_tolerance',2))}
        priority=priority or strategy_value(strategy,'relic_mark_priority',())
        refresh_permitted=refresh_shop and area is not None and area>=strategy_value(strategy,'refresh_from_area',5)
        def exhausted(value):
            return (relic_cycle>=3 or value.get('stock_state')=='exhausted'
                or len(value['items'])==3 and all(i['kind']=='role_imprint' for i in value['items']))
        def ready(value):
            if exhausted(value) and refresh_permitted:return value.get('refresh_known',True)
            return (any(i['kind']=='relic' and i['purchasable'] for i in value['items'])
                or value.get('stock_state')!='unknown')
        try:
            for _ in range(100):
                batch.checkpoint();current,receipt=self._capture()
                if current['state']!='shop':raise BatchStopped('shop_initial_page_unknown')
                if not buy_relics or (area==4 and not (final_shop or refresh_permitted)):break
                if not ready(current):current,receipt=self._wait_state(batch,'shop',accept=ready)
                items=[dict(i,relic=Relic(**i['relic'])) if i['kind']=='relic' else dict(i) for i in current['items']]
                observed_stacks=calibrated_stacks(observed_stacks,items)
                calibrated_marks.update(i['relic'].mark for i in items if i['kind']=='relic')
                purchasable=any(i.get('purchasable') is True for i in items)
                refresh_price=refresh_quote(current)
                refresh_ok=bool(not refresh_unavailable and refresh_permitted
                    and current.get('refresh_allowed') and current.get('refresh_enabled') is not False
                    and (refresh_price is None or type(current.get('balance')) is int and current['balance']>=refresh_price))
                # Last shop of the run must not carry run coins out: keep
                # refreshing even when the leftover stock has nothing purchasable.
                if (exhausted(current) or (final_shop and not purchasable)) and refresh_ok:
                    result=self._refresh(batch,enter_id,block_id,current,cycle) or {}
                    if result.get('refresh_refused') is True:
                        # The control is already locked (per-shop refresh cap);
                        # never re-click it in this visit. Continue with the
                        # normal imprint fallback / leave path below.
                        refresh_unavailable=True
                        continue
                    cycle+=1;relic_cycle=0;refreshed=True;continue
                selected=None if observed_stacks is None else shop_relic(observed_stacks,items,balance=current['balance'],buy=buy_relics,area=area,final_shop=final_shop,allow_area4=refresh_permitted,locked_focus=locked_focus,priority=priority,**policy)
                if selected is None and any(i['kind']=='relic' and i['purchasable'] for i in items):
                    raise BatchStopped('shop_relic_state_unknown')
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
            def exit_scene():
                deadline=native.active_now()+OBSERVATION_TIMEOUT_SECONDS
                current=self.scene.observe(probe_kinds=_EXIT_PAGES)
                if current.kind!='connecting':return current
                remaining=deadline-native.active_now()
                if remaining<=0:raise BatchStopped('shop_exit_unknown')
                return self.scene.wait_for(batch,_EXIT_PAGES,timeout=remaining,reason='shop_exit_unknown')
            scene=exit_scene()
            def leave(oid):
                sent=self.scene.perform(scene,'leave_shop')
                return self.stamp(operation_id=oid,**fields,native=sent,input_native=sent,input_action_verified=True,source='maa')
            clicked=batch.transaction('shop_leave',fields,leave)
            after=self.scene.wait_for(batch,{'map','shop_exit_confirmation'},source='shop',timeout=30,reason='shop_exit_unknown')
            result=batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,page=after.kind,native=after.native,input_native=clicked['input_native'],source='maa'),**fields)
            if result['page']=='shop_exit_confirmation':
                fields=dict(enter_id=enter_id,block_id=block_id,navigation_target='map')
                scene=exit_scene()
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
