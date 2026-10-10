"""CC BY-NC-SA 4.0 port of upstream event text/layout/pixel recognizer.

All OCR and button samples use the same Maa image. Scores are similarity and
pixel fractions, never probabilities. The event catalogue cannot prove a
reward was added; the subsequent page must be processed independently.
"""
from collections import Counter
from functools import lru_cache
from pathlib import Path
import hashlib,json,re
from maa.custom_recognition import CustomRecognition
from .batch import BatchStopped

NODE='LabyEventObserved';NAME='LabyrinthEvent'
DATA=Path(__file__).resolve().parents[3]/'assets/labyrinth'
ROI=[75,65,1130,500]

@lru_cache(maxsize=1)
def catalogue():
    raw=json.loads((DATA/'event-recognition.json').read_text(encoding='utf-8'))
    if raw.get('schemaVersion')!=1 or len(raw.get('events',()))!=22:raise BatchStopped('event_resources_unknown')
    return raw

def event_pipeline():
    return {'LabyEventWords':dict(recognition='OCR',expected=r'.+',roi=ROI,threshold=.8,action='DoNothing',next=[],pre_delay=0,post_delay=0),
       NODE:dict(recognition='Custom',custom_recognition=NAME,roi=[0,0,1280,720],action='DoNothing',next=[],timeout=5000,pre_delay=0,post_delay=0)}

def normalized(value):return ''.join(c for c in value.lower() if c.isalnum())
def contained(target,observed):
    if not target:return 0
    if target in observed:return 1
    previous=[0]*(len(observed)+1)
    for left in target:
        current=[0]*(len(observed)+1)
        for i,right in enumerate(observed):current[i+1]=previous[i]+1 if left==right else max(current[i],previous[i+1])
        previous=current
    lcs=previous[-1]/len(target);dice=0
    if len(target)>=2 and len(observed)>=2:
        pairs=Counter(target[i:i+2] for i in range(len(target)-1));width=min(len(target)+4,len(observed));step=max(1,len(target)//8)
        for start in range(0,len(observed)-width+1,step):
            window=observed[start:start+width];other=Counter(window[i:i+2] for i in range(len(window)-1));den=sum(pairs.values())+sum(other.values())
            if den:dice=max(dice,2*sum(min(n,other[k]) for k,n in pairs.items())/den)
    return lcs*.55+dice*.45

def recognize_event(text):
    observed=normalized(text)
    if len(observed)<12:return None
    ranked=[]
    for event in catalogue()['events']:
        title=contained(normalized(event['name']),observed);choices=[]
        for c in event['choices']:
            name,desc=normalized(c['name']),normalized(c['description']);nums=set(re.findall('[0-9]+',name+desc));actual=set(re.findall('[0-9]+',observed))
            digits=len(nums&actual)/len(nums) if nums else 1
            choices.append(contained(name,observed)*.48+contained(desc,observed)*.37+digits*.15)
        choice=sum(choices)/len(choices);ranked.append((title*.35+choice*.65,title,choice,event))
    ranked.sort(key=lambda v:(-v[0],int(v[3]['id'])))
    score,title,choice,event=ranked[0];margin=score-ranked[1][0]
    if score<.62 or title<.62 or choice<.48:return None
    return dict(event_id=int(event['id']),catalogue_event=event,score=score,title_score=title,choice_score=choice,margin=margin,
        text_trusted=score>=.72 and margin>=.045)

def pixel_rect(v):
    left=int(v['left']*1280);top=int(v['top']*720);right=int(v['right']*1280);bottom=int(v['bottom']*720)
    return [left,top,right-left,bottom-top]

def button_fraction(image,box,*,enabled=False):
    x,y,w,h=box;hits=0
    for row in range(1,9):
        py=y+int(row*(h-1)/9+.5)
        for col in range(1,17):
            px=x+int(col*(w-1)/17+.5);b,g,r=(int(a) for a in image[py,px,:3])
            hits+=(b>=200 and b-r>=60) if enabled else (b>=105 and b>=r+18 and b>=g-25)
    return hits/128

class EventRecognition(CustomRecognition):
    def __init__(self,stopped=None):super().__init__();self.stopped=stopped;self.last=None;self.stable=0
    def analyze(self,context,argv):
        if (self.stopped is not None and self.stopped()) or tuple(argv.image.shape[:2])!=(720,1280):return self.AnalyzeResult(None,{'status':'unknown'})
        reco=context.run_recognition('LabyEventWords',argv.image);raw=getattr(reco,'raw_detail',None)
        if type(raw) is not dict or getattr(reco,'hit',False) is not True:return self.AnalyzeResult(None,{'status':'unknown'})
        words=raw.get('filtered',[])
        if type(words) is not list or not words:return self.AnalyzeResult(None,{'status':'unknown'})
        # Model threshold already filters words; preserve exact raw words as evidence.
        text=''.join(w.get('text','') for w in sorted(words,key=lambda w:(w.get('box',[0,0])[1],w.get('box',[0])[0])))
        match=recognize_event(text)
        if match is None:self.last=None;self.stable=0;return self.AnalyzeResult(None,dict(status='unknown',words=words))
        event=match['catalogue_event'];layout=next((l for l in catalogue()['layouts'] if l['optionCount']==len(event['choices'])),None)
        if layout is None or layout.get('calibrationRequired') is not False:return self.AnalyzeResult(None,{'status':'unknown'})
        choices=[]
        for choice,roi in zip(sorted(event['choices'],key=lambda c:c['slot']),layout['selectButtonRois']):
            box=pixel_rect(roi);present=button_fraction(argv.image,box);enabled=button_fraction(argv.image,box,enabled=True)
            if present<.35:return self.AnalyzeResult(None,dict(status='unknown',reason='event_button_absent',words=words,match=match))
            choices.append(dict(choice_id=int(choice['id']),slot=choice['slot']-1,box=box,label=choice['name'],button_confidence=present,enabled_confidence=enabled))
        self.stable=self.stable+1 if self.last==match['event_id'] else 1;self.last=match['event_id']
        payload=dict(match,choices=choices,words=words,text=text,trusted=match['text_trusted'] and self.stable>=2,stable_frames=self.stable,same_frame=True,
            frame_pixel_sha256=hashlib.sha256(argv.image.tobytes()).hexdigest(),source='maa',status='observed')
        params=argv.custom_recognition_param
        if type(params) is str:params=json.loads(params or '{}')
        if type(params) is not dict:params={}
        if params.get('event_id') and (params['event_id']!=match['event_id'] or payload['trusted'] is not True):return self.AnalyzeResult(None,payload)
        slot=params.get('slot')
        if slot is not None and (type(slot) is not int or slot<0 or slot>=len(choices) or choices[slot]['enabled_confidence']<.35):return self.AnalyzeResult(None,payload)
        return self.AnalyzeResult([0,0,1280,720],payload)

def event_readback(receipt):
    if receipt.get('native_ok') is not True:raise BatchStopped('event_unknown')
    node=next((n for n in receipt.get('nodes',[]) if n.get('name')==NODE and n.get('completed') is True),None);raw=node.get('recognition') if node else None
    if type(raw) is not dict or type(raw.get('filtered')) is not list or len(raw['filtered'])!=1 or raw.get('all')!=raw['filtered'] or raw.get('best')!=raw['filtered'][0]:raise BatchStopped('event_unknown')
    value=raw['best'].get('detail')
    if type(value) is not dict or value.get('status')!='observed':raise BatchStopped('event_unknown')
    return value
