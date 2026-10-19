"""Upstream full icon-pack matching and measured card-grid geometry.



Adapted from wbero LabyrinthCharacterIconMatcher/CharacterGridDetector/

BattleTeamRecognition in the pinned upstream snapshot, CC BY-NC-SA 4.0.

Resources and separate catalog/game-image terms: character-pack/PROVENANCE.json.

No private captures or per-card learned templates are needed.

"""

from collections import Counter

from dataclasses import dataclass,asdict

from functools import lru_cache

from pathlib import Path

import hashlib,json,math,re

import cv2

import numpy as np

from maa.custom_recognition import CustomRecognition

from .batch import BatchStopped



PACK=Path(__file__).resolve().parents[3]/'assets/labyrinth/character-pack'

CALLBACK='LabyrinthCharacters'

CONFIDENCE=.35

RIVAL_MARGIN=.045

ATTRIBUTE_HUES={'FIRE':.021,'DARK':.729,'LIGHT':.104,'WIND':.271,'WATER':.604}

VIEWPORTS={'opening':(40,179,1200,404),'battle':(40,153,1200,344),

    'boss':(40,240,1200,257),'roster':(40,200,1200,385),'members':(46,530,740,147)}

FILTERS=('ALL','FIRE','WATER','WIND','LIGHT','DARK','EFFECTIVE_EFFECT')

_FILTER_X_BOUNDS=((70,150),(220,146),(366,146),(512,146),(658,146),(804,146),(950,160))

FILTER_RECTS=tuple((round(x*2/3),round(140*2/3),round(w*2/3),round(80*2/3))

    for x,w in _FILTER_X_BOUNDS)

FILTER_ROIS={'opening':[45,120,850,55],'battle':[40,85,750,75],'boss':[40,180,750,75]}

BOSS_TABS=tuple(tuple(round(v*2/3) for v in box) for box in ((98,150,195,65),(334,150,195,65),(571,150,195,65)))





def canonical_id(value):

    if type(value) is not str or re.fullmatch(r'[0-9]{4,6}',value) is None:

        raise BatchStopped('character_identity_unknown')

    # Upstream canonicalLabyrinthRoleId combines paired/triple unit artwork.

    # Seasonal IDs remain distinct; a six-digit role ID is never truncated.

    return {'1183':'1807','1184':'1807','1204':'1808','1205':'1808','1206':'1808',

        '1217':'1809','1218':'1809','1243':'1810','1244':'1810','1281':'1811','1282':'1811'}.get(value,value)







def resolve_character_name(value,*,optimizer=None):

    """Exact full catalog name/alias only; variants never fall back to base IDs."""

    from .rewards import normalized_name

    if type(value) is not str or not value.strip():raise BatchStopped('character_identity_unknown')

    name=normalized_name(value);catalog=json.loads((PACK/'characters.landosolroster.json').read_text(encoding='utf-8'))

    keys=set()

    for person in catalog['characters']:

        words=[person.get('name'),person.get('officialName'),*person.get('aliases',[])]

        if any(type(word) is str and normalized_name(word)==name for word in words):keys.add(canonical_id(person['id']))

    from .roles import load_optimizer

    for key,person in (optimizer or load_optimizer()).profiles.items():

        if normalized_name(person.data.get('displayName',''))==name:keys.add(canonical_id(key))

    if len(keys)!=1 or '1000' in keys:raise BatchStopped('character_identity_unknown')

    return next(iter(keys))



def _round(value):return np.floor(np.asarray(value)+.5).astype(np.int64)

def _normal(value,low,high):return np.clip((value-low)/(high-low),0,1)





def _inside(box,shape):

    x,y,w,h=box

    return all(type(v) is int for v in box) and w>0 and h>0 and x>=0 and y>=0 and x+w<=shape[1] and y+h<=shape[0]





@dataclass(frozen=True)

class IconCandidate:

    character_id:str

    name:str

    variant:str

    confidence:float

    icon_owner_id:str

    unit_icon_id:str





@dataclass(frozen=True)

class IconMatch:

    character_id:str|None

    name:str|None

    variant:str|None

    confidence:float

    rival_confidence:float

    rival_margin:float

    trusted:bool

    candidates:tuple[IconCandidate,...]

    unit_icon_id:str|None=None





@dataclass(frozen=True)

class IconMask:

    right:bool=False

    top_left:bool=False

    top_right:bool=False

    bottom_left:bool=False

    bottom_band:bool=False

    min_y:float=0

    max_y:float=1





@lru_cache(maxsize=1)

def load_character_pack():

    catalog=json.loads((PACK/'characters.landosolroster.json').read_text(encoding='utf-8'))

    icons=json.loads((PACK/'icons.json').read_text(encoding='utf-8'))

    names={c['id']:c for c in catalog['characters']}

    from .roles import load_optimizer

    profiles=load_optimizer().profiles

    entries=[];images=[]

    for icon in icons['characterIcons']:

        key=canonical_id(icon['ownerId']);p=PACK/icon['file']

        if icon['ownerId'] not in names:continue

        raw=p.read_bytes()

        if hashlib.sha256(raw).hexdigest()!=icon['sha256']:raise BatchStopped('character_resource_changed')

        image=cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)

        if image is None or image.shape[:2]!=(icon['height'],icon['width']) or image.shape[:2]!=(128,128):

            raise BatchStopped('character_resource_unknown')

        person=names[icon['ownerId']]

        entries.append(dict(character_id=key,name=person.get('officialName') or person['name'],

            variant=icon['variant'],icon_owner_id=icon['ownerId'],unit_icon_id=icon['ownerId']+icon['variant'],attribute=profiles[key].data.get('attribute') if key in profiles else None,

            aliases=tuple(dict.fromkeys([person['name'],person.get('officialName',''),*person.get('aliases',[])]))))

        images.append(image[:,:,::-1].copy())

    if not entries:raise BatchStopped('character_resource_unknown')

    return tuple(entries),np.stack(images),names





class IconMatcher:

    """Exact upstream coarse/fine correlation, per-character rivals and roster override."""

    def __init__(self,*,confidence=CONFIDENCE,margin=RIVAL_MARGIN):

        self.entries,self.images,self.names=load_character_pack()

        if not 0<=confidence<=1 or not 0<=margin<=1:raise ValueError('invalid_icon_gate')

        self.confidence,self.margin=confidence,margin

        rgb=self.images.astype(np.int64)

        self.luminance=(rgb[:,:,:,0]*299+rgb[:,:,:,1]*587+rgb[:,:,:,2]*114+500)//1000

        self.expected={}

        for width,height in ((12,9),(20,12)):

            xr,yr=np.meshgrid(np.linspace(0,1,width),np.linspace(0,1,height))

            ix=_round(xr.ravel()*127);iy=20+_round(yr.ravel()*79)

            colors=rgb[:,iy,ix,:].astype(float)

            luma=self.luminance[:,iy,ix].astype(float)

            gradients=np.abs(self.luminance[:,iy,np.minimum(ix+1,127)]-self.luminance[:,iy,np.maximum(ix-1,0)])+np.abs(

                self.luminance[:,np.minimum(iy+1,99),ix]-self.luminance[:,np.maximum(iy-1,20),ix])

            self.expected[width]=(xr.ravel(),yr.ravel(),colors,luma,gradients.astype(float))



    @staticmethod

    def _correlation(observed,expected):

        observed=observed-observed.mean(axis=0,keepdims=True)

        expected=expected-expected.mean(axis=1,keepdims=True)

        numerator=np.sum(expected*observed[None,...],axis=1)

        denominator=np.sqrt(np.sum(expected**2,axis=1)*np.sum(observed**2,axis=0))

        return np.divide(numerator,denominator,out=np.zeros_like(numerator),where=denominator>1e-9)



    def _scores(self,frame,box,mask,width,*,_prepared=None,_indices=None):

        if _prepared is None:_prepared=dict(frame=frame)
        if type(_prepared) is not dict or _prepared.get('frame') is not frame:
            raise BatchStopped('character_frame_unknown')
        x,y,w,h=box;top=y+int(_round(h*20/128));height=max(16,int(_round(h*80/128)))

        if not _inside((x,top,w,height),frame.shape):return np.zeros(len(self.entries))

        xr,yr,colors,luma,gradient=self.expected[width]
        if _indices is not None:
            colors,luma,gradient=colors[_indices],luma[_indices],gradient[_indices]

        kept=(yr>=mask.min_y)&(yr<=mask.max_y)

        if mask.right:kept&=xr<=.82

        if mask.top_left:kept&=~((xr<=.46)&(yr<=.38))

        if mask.top_right:kept&=~((xr>=.58)&(yr<=.42))

        if mask.bottom_left:kept&=~((xr<=.32)&(yr>=.72))

        if mask.bottom_band:kept&=yr<.74

        if kept.sum()<(8 if width==12 else 20):return np.zeros(len(self.entries))

        fx=x+_round(xr[kept]*(w-1));fy=top+_round(yr[kept]*(height-1))

        if 'rgb' not in _prepared:_prepared['rgb']=frame[:,:,::-1].astype(np.int64)
        rgb=_prepared['rgb']

        observed=rgb[fy,fx,:].astype(float)

        color=np.clip(self._correlation(observed,colors[:,kept,:]).mean(axis=1),0,None)

        if width==12:return color

        if 'lum' not in _prepared:_prepared['lum']=(rgb[:,:,0]*299+rgb[:,:,1]*587+rgb[:,:,2]*114+500)//1000
        lum=_prepared['lum']

        actual_lum=lum[fy,fx].astype(float)

        actual_grad=np.abs(lum[fy,np.minimum(fx+1,x+w-1)]-lum[fy,np.maximum(fx-1,x)])+np.abs(

            lum[np.minimum(fy+1,top+height-1),fx]-lum[np.maximum(fy-1,top),fx])

        ls=np.clip(self._correlation(actual_lum[:,None],luma[:,kept,None])[:,0],0,None)

        gs=np.clip(self._correlation(actual_grad[:,None].astype(float),gradient[:,kept,None])[:,0],0,None)

        scores=np.clip(color*.55+ls*.25+gs*.20,0,1)
        if _indices is None:return scores
        # Keep original row indices; match consumes only its already selected rows.
        complete=np.zeros(len(self.entries));complete[_indices]=scores
        return complete



    def match(self,frame,box,*,mask=IconMask(),attribute=None,roster=None,_prepared=None):

        if type(frame) is not np.ndarray or frame.dtype!=np.uint8 or not _inside(box,frame.shape):

            raise BatchStopped('character_frame_unknown')

        if _prepared is None:_prepared=dict(frame=frame)
        if type(_prepared) is not dict or _prepared.get('frame') is not frame:
            raise BatchStopped('character_frame_unknown')
        coarse=self._scores(frame,box,mask,12,_prepared=_prepared)

        allowed=np.array([attribute is None or e['attribute'] is None or e['attribute']==attribute for e in self.entries])

        order=np.argsort(-coarse,kind='stable');order=order[allowed[order]]

        roster_set={canonical_id(x) for x in roster} if roster else set()

        if roster_set:

            inside=np.array([i for i in order if self.entries[i]['character_id'] in roster_set],dtype=int)

            if len(inside) and (coarse[order[0]]-coarse[inside[0]])<.08:order=inside

        if len(order):

            cutoff=coarse[order[0]]*.5

            order=order[np.array([rank<64 or coarse[i]>=cutoff for rank,i in enumerate(order)])][:512]

        fine=self._scores(frame,box,mask,20,_prepared=_prepared,_indices=order);by_id={}

        for index in order:

            entry=self.entries[index];candidate=IconCandidate(entry['character_id'],entry['name'],entry['variant'],float(fine[index]),entry['icon_owner_id'],entry['unit_icon_id'])

            if candidate.character_id not in by_id or by_id[candidate.character_id].confidence<candidate.confidence:

                by_id[candidate.character_id]=candidate

        candidates=sorted(by_id.values(),key=lambda c:c.confidence,reverse=True)

        best=candidates[0] if candidates else None;rival=candidates[1].confidence if len(candidates)>1 else 0

        score=best.confidence if best else 0;margin=score-rival;trusted=best is not None and score>=self.confidence and margin>=self.margin

        return IconMatch(best.character_id if trusted else None,best.name if trusted else None,best.variant if trusted else None,

            score,rival,margin,trusted,tuple(candidates[:5]),best.unit_icon_id if trusted else None)



    def refine(self,frame,box,initial,*,fallback=None,_prepared=None,**kwargs):
        if _prepared is None:_prepared=dict(frame=frame)
        if type(_prepared) is not dict or _prepared.get('frame') is not frame:
            raise BatchStopped('character_frame_unknown')
        if initial.trusted:return initial
        # A failed border crop retains the existing fixed-strip hint. Every
        # crop's rivals still compete under the original confidence floors.
        rects=[fallback] if fallback is not None else []
        for x,y,w,h in (box,) + ((fallback,) if fallback is not None else ()):
            radius=max(1,min(3,int(_round(w/90))))
            deltas=tuple(dict.fromkeys((-radius,-1,1,radius)))
            for d in deltas:rects.extend(((x+d,y+d,w-2*d,h-2*d),(x+d,y,w,h),(x,y+d,w,h)))
        by_id={}

        for result in (initial,*(self.match(frame,r,_prepared=_prepared,**kwargs) for r in dict.fromkeys(rects) if _inside(r,frame.shape))):

            for candidate in result.candidates:

                if candidate.character_id not in by_id or candidate.confidence>by_id[candidate.character_id].confidence:

                    by_id[candidate.character_id]=candidate

        candidates=sorted(by_id.values(),key=lambda c:c.confidence,reverse=True)

        if not candidates:return initial

        best=candidates[0];rival=candidates[1].confidence if len(candidates)>1 else 0

        if best.confidence<self.confidence or best.confidence-rival<self.margin:return initial

        return IconMatch(best.character_id,best.name,best.variant,best.confidence,rival,best.confidence-rival,True,tuple(candidates[:5]),best.unit_icon_id)





def card_attribute(frame,box):

    x,y,w,h=box;rx=x+int(w*.78);ry=y+int(h*.76);rw=max(8,int(w*.19));rh=max(8,int(h*.21))

    if not _inside((rx,ry,rw,rh),frame.shape):return None

    step=max(1,min(rw,rh)//16);rgb=frame[ry:ry+rh:step,rx:rx+rw:step,::-1].astype(np.float32)/255

    hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV);hue=hsv[:,:,0]/360;saturation=hsv[:,:,1];value=hsv[:,:,2]

    accepted=(saturation>=.30)&(value>=.20)

    if accepted.mean()<.10:return None

    vx=np.sum(np.cos(hue[accepted]*2*np.pi)*saturation[accepted]);vy=np.sum(np.sin(hue[accepted]*2*np.pi)*saturation[accepted])

    observed=(math.atan2(vy,vx)/(2*np.pi))%1

    ranked=sorted(((key,1-min(abs(observed-h),1-abs(observed-h))/.5) for key,h in ATTRIBUTE_HUES.items()),key=lambda v:v[1],reverse=True)

    return ranked[0][0] if ranked[0][1]>=.72 and ranked[0][1]-ranked[1][1]>=.10 else None





def opening_marker(frame,box):

    x,y,w,h=box

    if w<16 or h<w*.42:return 0.

    cx=x+w*.83;cy=y+w*.20;radius=w*.26

    left=max(x,int(cx-radius),0);right=min(x+w,int(cx+radius)+1,frame.shape[1])

    top=max(y,int(cy-radius),0);bottom=min(y+h,int(cy+radius)+1,frame.shape[0])

    ys,xs=np.mgrid[top:bottom:2,left:right:2];distance=np.hypot(xs-cx,ys-cy)

    rgb=frame[top:bottom:2,left:right:2,::-1].astype(float);r,g,b=rgb[:,:,0],rgb[:,:,1],rgb[:,:,2]

    inner=distance<=w*.16;outer=(distance>=w*.205)&(distance<=radius)

    if inner.sum()<24 or outer.sum()<24:return 0.

    luminance=(r*299+g*587+b*114)/1000

    white=(rgb.min(axis=2)>=185)&((rgb.max(axis=2)-rgb.min(axis=2))<=55)

    blue=(b>=105)&(b-r>=18)&(b-g>=6)

    return float(_normal(luminance[inner].mean()-luminance[outer].mean(),45,100)*.45+

        _normal(white[inner].mean()-white[outer].mean(),.30,.70)*.45+_normal(blue[inner].mean(),.005,.04)*.10)





def battle_marker(frame,box):

    x,y,w,h=box;rx=x+int(_round(w*.64));ry=y+int(_round(h*.02));rw=max(1,int(_round(w*.40)));rh=max(1,int(_round(h*.46)))

    rgb=frame[ry:min(ry+rh,frame.shape[0]):3,rx:min(rx+rw,frame.shape[1]):3,::-1].astype(int)

    if not rgb.size:return 0.

    r,g,b=rgb[:,:,0],rgb[:,:,1],rgb[:,:,2]

    yellow=(r>175)&(g>125)&(b<165)&(r-b>55)

    white=(rgb.min(axis=2)>215)&((rgb.max(axis=2)-rgb.min(axis=2))<28)

    return float(min(_normal(yellow.mean(),.018,.070),_normal(white.mean(),.025,.120)))





def _content(frame):

    rgb=frame.astype(np.int16)

    return ((rgb.max(axis=2)-rgb.min(axis=2))>=18)|(rgb.mean(axis=2)<=224)





def _bands(values,origin,size):

    smooth=np.convolve(values,np.ones(3)/3,mode='same')

    baseline=np.quantile(smooth,.1,method='nearest');peak=np.quantile(smooth,.9,method='nearest')

    if peak-baseline<.06:return []

    active=smooth>=baseline+(peak-baseline)*.45;gap=max(1,int(_round(size*.035)))

    start=None

    for i in range(len(active)):

        if not active[i] and start is None:start=i

        if active[i] and start is not None:

            if start>0 and i-start<=gap:active[start:i]=True

            start=None

    change=np.diff(np.r_[False,active,False].astype(int))

    return [(origin+int(a),origin+int(z)) for a,z in zip(np.flatnonzero(change==1),np.flatnonzero(change==-1)) if z-a>=int(_round(size*.48))]





def _presence(content,box,viewport):

    x,y,w,h=box;vx,vy,vw,vh=viewport

    left=max(x+8,vx);right=min(x+w-8,vx+vw);top=max(y+8,vy);bottom=min(y+h-8,vy+vh)

    values=content[top:bottom:6,left:right:6]

    return float(values.mean()) if values.size else 0.





def detect_grid(frame,viewport,*,card_size=130,pitch=141,row_pitch=141,max_columns=8):

    """Projection bands, pitch recovery, clipped rows and edge snapping from upstream."""

    vx,vy,vw,vh=viewport;content=_content(frame);window=min(vh,card_size)

    cumulative=np.pad(np.cumsum(content[vy:vy+vh,vx:vx+vw],axis=0),((1,0),(0,0)))

    projection=(cumulative[window:]-cumulative[:-window]).max(axis=0)/window

    measured=_bands(projection,vx,card_size)

    usable=[band for band in measured if card_size*.48<=band[1]-band[0]<=card_size*1.25]

    if not usable:return ()

    centers=[(a+z)//2 for a,z in usable];samples=[b-a for a,b in zip(centers,centers[1:]) if pitch*.82<=b-a<=pitch*1.18]

    actual_pitch=int(np.median(samples)) if samples else pitch

    columns=[];center=centers[0]

    while len(columns)<max_columns and center-card_size//2<vx+vw:

        nearest=min(centers,key=lambda c:abs(c-center));chosen=nearest if abs(nearest-center)<=actual_pitch*.08 else center

        if chosen-card_size//2>=vx and chosen+card_size//2<=vx+vw:columns.append((chosen-card_size//2,chosen-card_size//2+card_size))

        center+=actual_pitch

    if len(columns)<2:return ()

    support=usable or columns

    scores=np.stack([content[vy:vy+vh,a+2:z-2:2].mean(axis=1) for a,z in support])

    rows=_bands(np.sort(scores,axis=0)[-2:].mean(axis=0),vy,card_size)

    normalized=[]

    for a,z in rows:

        if z-a>card_size*1.25:continue

        if a<=vy+card_size*.08 and z-a<card_size*.88:start=z-card_size

        elif z>=vy+vh-card_size*.08 and z-a<card_size*.88:start=a

        else:start=(a+z)//2-card_size//2

        if normalized and normalized[-1]+card_size>start+2:start=normalized[-1]+row_pitch

        if any(_presence(content,(x,start,card_size,card_size),viewport)>=.055 for x,_ in columns):normalized.append(start)

    if not normalized:return ()

    for start in (normalized[0]-row_pitch,normalized[-1]+row_pitch):

        visible=min(start+card_size,vy+vh)-max(start,vy)

        if visible>=card_size*.68 and all(abs(start-old)>=card_size-2 for old in normalized):

            if any(_presence(content,(x,start,card_size,card_size),viewport)>=.055 for x,_ in columns):normalized.append(start)

    rgb=frame[:,:,::-1].astype(float)

    # Leading boundaries reward a change to colourful card art; horizontal

    # borders additionally reward the warm pink/gold outer card frame.

    def edge(axis,pos,bands,*,trailing=False):

        if pos<1 or pos+2>=(frame.shape[1] if axis==0 else frame.shape[0]):return 0.

        values=[]

        for a,z in bands:

            points=np.arange(max(a+10,0),min(z-10,frame.shape[0 if axis==0 else 1]),3)

            if not len(points):continue

            outside=rgb[points,pos-1] if axis==0 else rgb[pos+1 if trailing else pos-1,points]

            inside=rgb[points,pos+2] if axis==0 else rgb[pos-2 if trailing else pos+2,points]

            spread=inside.max(axis=1)-inside.min(axis=1)

            outside_content=((outside.max(axis=1)-outside.min(axis=1))>=18)|(outside.mean(axis=1)<=224)

            structure=(~outside_content&((spread>=18)|(inside.mean(axis=1)<=224))).astype(float)

            transition=np.abs(inside-outside).sum(axis=1)/765

            warm=(inside[:,0]>=90)&(inside[:,0]-inside[:,1]>=10)&(inside[:,0]-inside[:,2]>=8)

            values.append(float((structure*.75+transition*.25+((warm&~outside_content)*1.25 if axis else 0)).mean()))

        return float(np.mean(sorted(values,reverse=True)[:2])) if values else 0.

    snapped_columns=[]

    row_bands=[(max(y,vy),min(y+card_size,vy+vh)) for y in sorted(normalized)]

    for left,_ in columns:

        candidates=range(max(vx,left-13),min(vx+vw-card_size,left+13)+1)

        best=max(candidates,key=lambda x:edge(0,x,row_bands)) if candidates else left

        if edge(0,best,row_bands)<.05:best=left

        snapped_columns.append((best+2,best+2+card_size))

    def alignment(top):

        visible_top=max(vy,top);visible_bottom=min(vy+vh,top+card_size)

        scores=[]

        for left,right in snapped_columns:

            rows=content[visible_top+8:visible_bottom-8:3,left+8:right-8:6]

            if not rows.size:continue

            active=rows.mean(axis=1)>=.35;longest=run=0

            for hit in active:

                run=run+1 if hit else 0;longest=max(longest,run)

            scores.append(longest/len(active))

        return max(scores,default=0.)

    boxes=[]

    for start in sorted(normalized):

        if start<vy:

            expected=start+card_size;candidates=range(max(2,expected-39),min(frame.shape[0]-2,expected+39)+1)

            best=max(candidates,key=lambda y:edge(1,y,snapped_columns,trailing=True)+alignment(y-card_size)*2)

            if edge(1,best,snapped_columns,trailing=True)+alignment(best-card_size)*2<.05:best=expected

            top=best+1-card_size

        else:

            candidates=range(max(1,start-39),min(frame.shape[0]-3,start+39)+1)

            best=max(candidates,key=lambda y:edge(1,y,snapped_columns)+alignment(y)*2)

            if edge(1,best,snapped_columns)+alignment(best)*2<.05:best=start

            top=best+2

        for left,_ in snapped_columns:

            rect=(int(left),int(top),card_size,card_size)

            visible=min(top+card_size,vy+vh)-max(top,vy)

            if visible<card_size*.68 or not _inside(rect,frame.shape):continue

            if _presence(content,rect,viewport)>=.055:boxes.append(rect)

    # Never identify the same geometry twice, nor overlapping full rows.

    return tuple(dict.fromkeys(boxes))





def scrollbar(frame,page):

    reference=(1825,290,28,562) if page=='opening' else (1825,377,28,349) if page=='boss' else (1825,235,28,500)

    x,y,w,h=(round(v*2/3) for v in reference)

    rgb=frame[y:y+h,x:x+w,::-1].astype(int);r,g,b=rgb[:,:,0],rgb[:,:,1],rgb[:,:,2]

    rows=((b>=140)&(b-r>=45)&(g-r>=15)).mean(axis=1)>=.25

    change=np.diff(np.r_[False,rows,False].astype(int));bands=list(zip(np.flatnonzero(change==1),np.flatnonzero(change==-1)))

    if not bands:return dict(visible=False,position=None,can_scroll=None)

    first,last=max(bands,key=lambda v:v[1]-v[0]);length=int(last-first)

    if length>=h*.92:return dict(visible=True,position=None,can_scroll=False)

    return dict(visible=True,position=float(first/max(1,h-length)),can_scroll=True)





def _blue(frame,box,*,gold=False):

    x,y,w,h=box;rgb=frame[y:y+h:3,x:x+w:3,::-1].astype(int)

    if not rgb.size:return 0.

    r,g,b=rgb[:,:,0],rgb[:,:,1],rgb[:,:,2]

    return float(((r>170)&(g>110)&(b<150)&(r-b>50)).mean()) if gold else float(((b>=95)&(b-r>=10)&(g-r>=4)).mean())


def _filter_rects(page):
    top,height=(280,66) if page=='boss' else (140,80)
    edge=lambda value:math.floor(value*2/3+.5)
    return tuple((edge(x),edge(top),edge(x+w)-edge(x),edge(top+height)-edge(top)) for x,w in _FILTER_X_BOUNDS)


def _filter_blue(frame,box):
    # Pinned upstream filter colors exclude disabled gray-blue controls.
    x,y,w,h=box;bgr=frame[y:y+h:4,x:x+w:4].astype(int)
    if not bgr.size:return 0.
    b,g,r=bgr[:,:,0],bgr[:,:,1],bgr[:,:,2]
    return float(((b>=120)&(b-r>=24)&(g-r>=8)).mean())


def _selected_filter(frame,page):
    boxes=_filter_rects(page) if page in ('battle','boss') else FILTER_RECTS
    if page=='roster' or not all(_inside(box,frame.shape) for box in boxes):return None
    coverage=_filter_blue if page in ('battle','boss') else _blue
    selected=[name for name,box in zip(FILTERS,boxes) if coverage(frame,box)>=.35]
    return selected[0] if len(selected)==1 else None




def card_presence(frame,box):

    x,y,w,h=box;pixels=frame[max(0,y+8):min(frame.shape[0],y+h-8):6,max(0,x+8):min(frame.shape[1],x+w-8):6].astype(float)

    return float((((pixels.max(axis=2)-pixels.min(axis=2))>=28)&(pixels.mean(axis=2)<248)).mean()) if pixels.size else 0.





def member_slots(frame,*,refine=True):

    """Upstream fixed current-member strip refinement, including a lone member."""

    expected=[(int(_round(x*2/3)),int(_round(811*2/3)),130,130) for x in (95,314,532,750,969)]

    if not refine:return tuple(box for box in expected if card_presence(frame,box)>=.055)

    distance=23;edge_offset=3

    def evidence(ox,oy,ix,iy):

        if not (0<=ox<1280 and 0<=ix<1280 and 0<=oy<720 and 0<=iy<720):return 0.

        outside=int(frame[oy,ox].min());inside=int(frame[iy,ix].min())

        return max(0,outside-inside) if outside>=215 else 0.

    def bottom_score(offset):

        values=[]

        for left,top,w,h in expected:

            bottom=top+h+offset

            values.append(sum(evidence(left+int(_round(w*(.06+sample*.12))),bottom+edge_offset,

                left+int(_round(w*(.06+sample*.12))),bottom-edge_offset) for sample in range(1,7))/6)

        return sum(sorted(values,reverse=True)[:2])/2-abs(offset)*.10

    bottom_offset=max(range(-distance,distance+1),key=bottom_score);refined=[]

    for left,top,w,h in expected:

        best=(left,top,w,h);best_score=25.;bottom=top+h+bottom_offset

        for width in range(w-7,w+8):

            for candidate_left in range(left-distance,left+distance+1):

                candidate_top=bottom-width

                if candidate_left<0 or candidate_top<0 or candidate_left+width>1280 or bottom>720:continue

                ls=rs=0.

                for sample in range(7):

                    y=candidate_top+int(_round(width*(.25+sample/12)))

                    ls+=evidence(candidate_left-edge_offset,y,candidate_left+edge_offset,y)

                    rs+=evidence(candidate_left+width+edge_offset,y,candidate_left+width-edge_offset,y)

                score=min(ls,rs)/7-abs(width-w)*.15-abs(candidate_left-left)*.04

                if score>best_score:best_score=score;best=(candidate_left,candidate_top,width,width)

        refined.append(best)

    if any(a[0]+a[2]>z[0] for a,z in zip(refined,refined[1:])):refined=expected

    return tuple(box for box in refined if card_presence(frame,box)>=.055)





def boss_team_index(frame):

    scores=[]

    for x in (98,334,571):

        left,top,width,height=(int(_round(v*2/3)) for v in (x+20,158,155,42))

        rgb=frame[top:top+height:3,left:left+width:3,::-1].astype(int);r,g,b=rgb[:,:,0],rgb[:,:,1],rgb[:,:,2]

        gold=(r>=190)&(g>=140)&(b<150)&(r-b>=60)

        white=(rgb.min(axis=2)>=190)&((rgb.max(axis=2)-rgb.min(axis=2))<45)

        scores.append((float(gold.mean()),float(white.mean())))

    selected=[i for i,scores in enumerate(scores) if scores[0]>=.30]

    if len(selected)!=1 or any(scores[i][1]<.40 for i in range(3) if i!=selected[0]):return None

    return selected[0]+1





def _unknown_visual_fingerprint(frame,boxes):

    """Pinned LabyrinthBattleTeamRecognition.kt:93-120 portrait-interior FNV key.



    BGR input becomes quantized RGB; only unknown visual identity uses this key.

    Full crop SHA remains diagnostic evidence, never a stability requirement.

    """

    value=14695981039346656037

    def mix(number):

        nonlocal value

        value=((value^number)*1099511628211)&((1<<64)-1)

    mix(len(boxes))

    for index,(x,y,w,h) in enumerate(boxes):

        mix(index)

        for yr in (35,50,65):

            for xr in (25,40,52):

                px=min(frame.shape[1]-1,max(0,x+w*xr//100))

                py=min(frame.shape[0]-1,max(0,y+h*yr//100))

                blue,green,red=(int(v) for v in frame[py,px])

                mix(((red>>3)<<10)|((green>>3)<<5)|(blue>>3))

    return value if value<(1<<63) else value-(1<<64)





def _viewport_signature(frame,*,page,cards,members,boxes,current_filter,count,scroll):

    def facts(card):

        candidates=sorted({(c['character_id'],c['variant']) for c in card.get('candidates',())}) if not card['trusted'] else []

        return (card.get('slot_id'),card['box'],card.get('character_id'),card.get('name'),card.get('variant'),

            card.get('attribute'),card['selected'],card['trusted'],candidates)

    value=dict(page=page,boxes=[list(b) for b in boxes],filter=current_filter,count=count,

        scrollbar=scroll,cards=[facts(c) for c in cards],members=[facts(c) for c in members],

        unknown_visual=_unknown_visual_fingerprint(frame,[c['box'] for c in cards if not c['trusted']]))

    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()





class CharacterRecognizer:

    """Per-owner state; no identity cache crosses page, filter or scroll changes."""

    def __init__(self,*,matcher=None,stopped=lambda:False):

        self.matcher=matcher or IconMatcher();self.stopped=stopped;self.previous={};self.pending={};self.revision=0



    def reward_offers(self,frame,slot=None,*,name_words=None):
        from .rewards import role_offer_from_words,reward_name_key,reward_name_family
        from .roles import load_optimizer
        optimizer=load_optimizer()
        names={entry['character_id']:entry['name'] for entry in load_character_pack()[0]}
        # Read all offers for strategy; guard only the selected current portrait.
        if slot is not None and (type(slot) is not int or slot not in (0,1,2)):
            raise BatchStopped('role_reward_profile_unknown')
        cards=[];prepared=dict(frame=frame)
        for index,x in enumerate((195,566,935)):
            if slot is not None and index!=slot:continue
            if self.stopped():raise BatchStopped('stopped')
            offer=None
            # None means OCR has no completed result; an empty row is a completed miss.
            if name_words is not None:
                words=name_words.get(index)
                if words is None:
                    cards.append(dict(slot=index,character_id=None,name=None,trusted=False,ocr_state='pending'))
                    continue
                try:offer=role_offer_from_words(index,words,optimizer)
                except BatchStopped:pass
            def named_card():
                return dict(slot=index,character_id=offer.character_id,name=''.join(w['text'] for w in offer.words),
                    trusted=True,name_native=list(offer.words),identity_source='maa_name',ocr_state='completed')
            display=names.get(offer.character_id,'') if offer else ''
            has_outfits=offer and reward_name_family(display)==reward_name_key(display) and any(
                key!=offer.character_id and reward_name_family(name)==reward_name_family(display)
                for key,name in names.items())
            if offer and not has_outfits:
                cards.append(named_card());continue
            box=(x,245,148,148);mask=IconMask(right=True,bottom_left=True)
            attribute=card_attribute(frame,box)
            match=self.matcher.match(frame,box,mask=mask,attribute=attribute,_prepared=prepared)
            if not match.trusted:match=self.matcher.refine(frame,box,match,mask=mask,attribute=attribute,_prepared=prepared)
            if offer and match.character_id==offer.character_id:
                cards.append(named_card())
            else:cards.append(dict(slot=index,**asdict(match),identity_source='portrait',ocr_state='completed' if name_words is not None else 'not_requested'))
        return dict(page='reward',cards=cards)

    def recognize(self,frame,page,*,roster=None,current_filter=None,observed_count=None,members_only=False):

        if self.stopped():raise BatchStopped('stopped')

        if members_only and page not in ('battle','boss'):raise ValueError('member_read_page_required')

        viewport=VIEWPORTS[page];boxes=() if members_only else detect_grid(frame,viewport,card_size=120 if page=='roster' else 130,

            pitch=131 if page=='roster' else 141,row_pitch=131 if page=='roster' else 141 if page=='opening' else 145)

        boxes=tuple(box for box in boxes if card_presence(frame,box)>=.055)

        row_keys=sorted(set(box[1] for box in boxes));column_keys=sorted(set(box[0] for box in boxes))

        slots={box:f'available_row_{row_keys.index(box[1])+1}_{column_keys.index(box[0])+1}' for box in boxes}

        current_filter=current_filter or _selected_filter(frame,page)

        scroll=scrollbar(frame,page);cards=[];prepared=dict(frame=frame)

        previous=self.previous.get(page)

        same_view=bool(previous and previous['filter']==current_filter and {card['slot_id'] for card in previous['cards']}==set(slots.values())

            and (previous['scrollbar']['position'] is None and scroll['position'] is None or

                previous['scrollbar']['position'] is not None and scroll['position'] is not None

                and abs(previous['scrollbar']['position']-scroll['position'])<=.02))

        old={card['slot_id']:card for card in previous['cards']} if same_view else {}

        for box in boxes:

            if self.stopped():raise BatchStopped('stopped')

            x,y,w,h=box;vx,vy,vw,vh=viewport

            clipped_top=max(0,vy-y);visible=min(y+h,vy+vh)-max(y,vy)

            selected=opening_marker(frame,box)>=.72 if page=='opening' else battle_marker(frame,box)>=.45

            recognition_box=box

            if page=='opening' and visible<h and scroll['can_scroll'] and scroll['position'] is not None:

                fallback_rows=(162,303,443) if scroll['position']>=.95 else (188,329,471) if scroll['position']>=.65 else ()

                fallback_x=(81,223,363,505,646,787,929,1070)

                if fallback_rows:

                    fy=min(fallback_rows,key=lambda value:abs(value-y));fx=min(fallback_x,key=lambda value:abs(value-x))

                    if abs(fy-y)<=w/4 and abs(fx-x)<=w/4:recognition_box=(fx,fy,w,h)

            rx,ry,rw,rh=recognition_box

            icon=(rx+6,ry+6,rw-14,rw-14)

            clipped_top=max(0,vy-ry);visible=min(ry+rh,vy+vh)-max(ry,vy)

            attribute=card_attribute(frame,recognition_box) if (page in ('battle','boss') or not selected) and (visible==h or page in ('battle','boss') and ry+rh<=vy+vh) else None

            mask=IconMask(right=True,top_left=True,top_right=page=='opening' and selected,

                bottom_left=True,bottom_band=page=='opening' and selected,

                min_y=min(1,clipped_top/h) if page=='opening' else 0,max_y=min(1,visible/h) if page=='opening' and visible<h else 1)

            match=self.matcher.match(frame,icon,mask=mask,attribute=attribute,roster=roster,_prepared=prepared)

            if page!='opening' and not match.trusted:

                match=self.matcher.refine(frame,icon,match,mask=mask,attribute=attribute,roster=roster,_prepared=prepared)

            item=dict(slot_id=slots[box],box=list(box),icon_box=list(icon),attribute=attribute,selected=selected,**asdict(match))

            item['candidates']=[asdict(candidate) for candidate in match.candidates]

            cached=old.get(slots[box])

            if page=='opening' and selected and cached and cached['trusted']:

                for key in ('character_id','name','variant','confidence','rival_confidence','rival_margin','trusted','candidates','unit_icon_id'):

                    item[key]=cached[key]

            cards.append(item)

        members=[]

        if page in ('battle','boss'):

            member_hints=member_slots(frame,refine=False)

            for box in member_slots(frame):

                x,y,w,h=box;icon=(x+6,y+6,w-14,w-14)

                attribute=card_attribute(frame,box)
                mask=IconMask(right=True,top_left=True);match=self.matcher.match(frame,icon,mask=mask,attribute=attribute,roster=roster,_prepared=prepared)

                if not match.trusted:
                    hint=min(member_hints,key=lambda b:abs(b[0]-x),default=None)
                    fallback=None
                    if hint is not None:
                        hx,hy,hw,hh=hint;fallback=(hx+6,hy+6,hw-14,hw-14)
                    match=self.matcher.refine(frame,icon,match,fallback=fallback,
                        mask=mask,attribute=attribute,roster=roster,_prepared=prepared)

                item=dict(box=list(box),icon_box=list(icon),selected=True,**asdict(match))

                item['candidates']=[asdict(candidate) for candidate in match.candidates];members.append(item)

        # Candidate collisions stay unknown; they do not invalidate independent
        # current members or acquire identities through the expected roster.
        duplicates={role for role,count in Counter(c['character_id'] for c in cards if c['trusted']).items() if count>1}
        for card in cards:
            if card['character_id'] in duplicates:
                card.update(character_id=None,name=None,variant=None,unit_icon_id=None,trusted=False)

        selected=[c['character_id'] for c in (cards if page=='opening' else members) if c['selected'] and c['trusted']]

        if len(set(selected))!=len(selected):

            raise BatchStopped('character_identity_ambiguous')

        # Actual semantic/portrait identity determines motion; animated unknown

        # texture does not veto fresh trusted targets. Unknown cards never

        # acquire a character identity; list motion is independent of identity.

        if page=='opening' and observed_count is not None and (type(observed_count) is not int or not 0<=observed_count<=3):

            raise BatchStopped('character_count_unknown')

        count=observed_count if page=='opening' else len(selected)

        unknown_pixels=[hashlib.sha256(frame[max(0,c['box'][1]):min(frame.shape[0],c['box'][1]+c['box'][3]),

            c['box'][0]:c['box'][0]+c['box'][2]].tobytes()).hexdigest() for c in cards if not c['trusted']]

        unknown_visual=_unknown_visual_fingerprint(frame,[c['box'] for c in cards if not c['trusted']])

        signature=_viewport_signature(frame,page=page,cards=cards,members=members,boxes=boxes,

            current_filter=current_filter,count=count,scroll=scroll)

        # Stable selection means the same business identities, not pixel geometry.
        # The viewport signature remains diagnostic motion evidence only.
        tab=boss_team_index(frame) if page=='boss' else None
        selection=(page,current_filter,count,tab,
            tuple(sorted((c['character_id'] or '',c['trusted']) for c in cards if page=='opening' and c['selected'])),
            tuple((c['character_id'],c['trusted']) for c in members))
        pending=self.pending.get(page);stable=bool(pending and pending[0]==selection and pending[1]>=1)

        self.pending[page]=(selection,pending[1]+1 if pending and pending[0]==selection else 1)

        self.revision+=not bool(pending and pending[0]==selection)

        tab=boss_team_index(frame) if page=='boss' else None

        observation=dict(page=page,filter=current_filter,boxes=[list(b) for b in boxes],cards=cards,members=members,

            selected=sorted(selected),scrollbar=scroll,signature=signature,stable=stable,

            revision=self.revision,boss_tab=tab,unknown_pixels=unknown_pixels,unknown_visual_fingerprint=unknown_visual)

        if members_only:observation['members_only']=True
        if stable and not members_only:self.previous[page]=observation

        return observation





def character_pipeline(namespace):

    """Only dormant native actions; each input requires fresh custom mode params."""

    nodes={}

    def custom(suffix,action='DoNothing',**extra):

        return dict(recognition='Custom',custom_recognition=CALLBACK,

            custom_recognition_param=dict(mode='unqualified'),action=action,next=[],timeout=1500,

            pre_delay=0,post_delay=0,**extra)

    nodes[namespace+'Read']=custom('Read')

    nodes[namespace+'ToggleOnce']=custom('ToggleOnce','Click',target=True,max_hit=1)

    nodes[namespace+'FilterOnce']=custom('FilterOnce','Click',target=True,max_hit=1)

    nodes[namespace+'FilterStable']=custom('FilterStable')

    nodes[namespace+'TabOnce']=custom('TabOnce','Click',target=True,max_hit=1)

    nodes[namespace+'ScrollOnce']=custom('ScrollOnce','Swipe',begin=[640,460],end=[640,260],duration=450,max_hit=1)

    nodes[namespace+'StartOnce']=custom('StartOnce','Click',target=True,max_hit=1)

    nodes[namespace+'InviteOnce']=custom('InviteOnce','Click',target=True,max_hit=1)

    nodes[namespace+'OpeningCount']=dict(recognition='OCR',roi=[1170,85,80,40],expected='^[0-3]/[1-3]$',threshold=.8,

        action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)

    for name,text,box in zip(FILTERS,('全部','火','水','风','光','暗','有效效果'),FILTER_RECTS):

        for page in ('opening','battle','boss'):

            # Same-frame OCR locates the actual labelled control. Opening and

            # battle headers differ after scaling; never click reference boxes.

            roi=FILTER_ROIS[page]

            nodes[namespace+page+'Filter'+name]=dict(recognition='OCR',roi=roi,expected=('^有效(?:效果)?$' if name=='EFFECTIVE_EFFECT' else '^'+text+'$'),threshold=.8,

                action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)

    nodes[namespace+'EffectiveEmptyNotice']=dict(recognition='OCR',roi=[525,250,260,230],expected='^[!！]?未搜索到该角色(?:。)?$',threshold=.8,action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)

    nodes[namespace+'BossMemberLabel']=dict(recognition='OCR',roi=[60,495,225,50],expected='^队伍[1-3]$',threshold=.8,replace=[['^·','']],

        action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)

    for i,box in enumerate(BOSS_TABS,1):

        nodes[namespace+'BossTabLabel'+str(i)]=dict(recognition='OCR',roi=list(box),expected='^队伍'+str(i)+'$',threshold=.8,

            action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)

    from .arrivals import NAME_ROIS,_ocr
    nodes.update({namespace+'JoinedName'+str(i):_ocr('.+',roi) for i,roi in enumerate(NAME_ROIS)})

    from copy import deepcopy

    from .interruptions import date_changed_pipeline

    from .session import reconnect_pipeline

    for pipeline,root in ((date_changed_pipeline(),'LabyDateChangedObserved'),(reconnect_pipeline(),'LabyDisconnect6002Observed')):

        nodes.update({key:deepcopy(pipeline[key]) for key in [root]+pipeline[root]['all_of']})

    return nodes





def _native_word(result):

    detail=result.raw_detail if result is not None and result.hit else None

    values=detail.get('filtered') if type(detail) is dict else None

    if (type(values) is not list or not values or type(values[0]) is not dict or detail.get('best')!=values[0]

        or any(v!=values[0] for v in values)):raise BatchStopped('character_page_unknown')

    value=values[0]

    if type(value.get('text')) is not str or type(value.get('box')) is not list:raise BatchStopped('character_page_unknown')

    return value





class CharacterRecognition(CustomRecognition):

    """Maa callback adapter; matching, guard and input target use argv.image."""

    def __init__(self,namespace,recognizer):

        super().__init__()

        self.namespace,self.recognizer=namespace,recognizer

    def analyze(self,context,argv):

        try:

            params=json.loads(argv.custom_recognition_param);mode=params.get('mode');page=params.get('page')

            if mode not in ('read','toggle','filter','freeze','scroll','tab','start','invite') or page not in ('opening','battle','boss','roster','reward','joined'):return None

            members_only=params.get('members_only') is True
            if members_only and (mode!='read' or page not in ('battle','boss')):return None

            if self.recognizer.stopped() or context.tasker.stopping:return None

            for interrupt in ('LabyDateChangedObserved','LabyDisconnect6002Observed'):

                seen=context.run_recognition(interrupt,argv.image)

                if seen is not None and seen.hit:return None

            if page=='joined':
                if mode!='read':return None
                for name in ('LabyRoleRewardJoinedTitle','LabyRoleRewardJoinedClose'):
                    seen=context.run_recognition(name,argv.image)
                    if seen is None or not seen.hit:return None
                # Upstream Joined uses the full icon pack at these three slots.
                # Reuse measured card presence to ignore genuinely empty rows.
                portraits=[];matcher=self.recognizer.matcher;prepared=dict(frame=argv.image)
                for slot,y in enumerate((233,327,421)):
                    box=(353,y,60,60)
                    if card_presence(argv.image,box)<.055:continue
                    if slot!=len(portraits):return None
                    mask=IconMask(right=True,top_left=True)
                    match=matcher.match(argv.image,box,mask=mask,_prepared=prepared)
                    if not match.trusted:match=matcher.refine(argv.image,box,match,mask=mask,_prepared=prepared)
                    portrait=dict(asdict(match),slot=slot,box=list(box),identity_source='portrait',name_native=[])
                    word=named=None
                    try:
                        word=_native_word(context.run_recognition(self.namespace+'JoinedName'+str(slot),argv.image))
                    except BatchStopped:pass
                    if word is not None:
                        portrait['name_native']=[word]
                        try:named=resolve_character_name(word['text'])
                        except BatchStopped:pass
                    if match.trusted:
                        if named is not None and named!=match.character_id:return None
                    elif named is not None:
                        portrait.update(character_id=named,name=word['text'],trusted=True,identity_source='maa_name')
                    else:return None
                    portraits.append(portrait)
                roles=[p['character_id'] for p in portraits]
                if not roles or len(set(roles))!=len(roles):return None
                if params.get('expected_role') is not None and roles!=[params['expected_role']]:return None
                return self.AnalyzeResult([0,0,1,1],dict(page='joined',joined_identity_schema='joined-fullpack-v1',roles_joined=roles,
                    portrait=portraits[0],portraits=portraits))
            if page=='reward':
                if mode!='read':return None
                for name in ('LabyRoleRewardTitle','LabyRoleRewardRemaining'):
                    seen=context.run_recognition(name,argv.image)
                    if seen is None or not seen.hit:return None
                remaining_result=seen
                slot=params.get('expected_slot');names={}
                for index in range(3) if slot is None else (slot,):
                    seen=context.run_recognition('LabyRoleRewardName'+str(index),argv.image)
                    raw=getattr(seen,'raw_detail',None)
                    names[index]=None if seen is None else raw.get('filtered',()) if seen.hit and type(raw) is dict else ()
                observation=self.recognizer.reward_offers(argv.image,slot=slot,name_words=names)
                if slot is None:
                    remaining=_native_word(remaining_result)
                    match=re.fullmatch(r'还剩([1-9][0-9]*)次',remaining['text']) if remaining else None
                    if match is None:return None
                    observation['remaining']=int(match.group(1))
                if params.get('expected_role') is not None:
                    from .rewards import reward_identity_safe
                    selected=observation['cards'][0]
                    if not reward_identity_safe(selected) or selected['character_id']!=params['expected_role']:return None
                expected=params.get('expected_roles')
                if expected is not None and expected!=[c['character_id'] for c in observation['cards']]:return None
                return self.AnalyzeResult([0,0,1,1],observation)
            anchors={'opening':('LabyOpeningRosterTitle','LabyOpeningRosterPrompt'),

                'battle':('LabyTeamTitle','LabyTeamMembersLabel'),'boss':('LabyTeamTitle',*[self.namespace+'BossTabLabel'+str(i) for i in range(1,4)]),

                'roster':('LabyRosterTitle','LabyRosterRoleTab')}[page]

            for name in anchors:

                result=context.run_recognition(name,argv.image)

                if result is None or not result.hit:return None

            if mode=='freeze':
                import time
                from maa.pipeline import JWaitFreezes
                budget=params.get('freeze_timeout_ms')
                if page not in FILTER_ROIS or type(budget) is not int or not 0<budget<=2500:return None
                if page=='boss' and boss_team_index(argv.image) is None:return None
                deadline=time.monotonic()+budget/1000
                if self.recognizer.stopped() or context.tasker.stopping:return None
                # One native wait retains its first frame even when capture exceeds 500 ms.
                frozen=context.wait_freezes(wait_freezes_param=JWaitFreezes(time=200,
                    target=FILTER_ROIS[page],threshold=.98,method=5,rate_limit=100,timeout=budget))
                if self.recognizer.stopped() or context.tasker.stopping:return None
                if not frozen or time.monotonic()>=deadline:return None
                return self.AnalyzeResult([0,0,1,1],dict(page=page,filter_frozen=True))

            if mode=='filter':
                target=params.get('filter')
                if page not in FILTER_ROIS or target not in FILTERS:return None
                if page=='boss' and boss_team_index(argv.image) is None:return None
                word=_native_word(context.run_recognition(self.namespace+page+'Filter'+target,argv.image))
                if self.recognizer.stopped() or context.tasker.stopping:return None
                return self.AnalyzeResult(word['box'],dict(page=page,filter=_selected_filter(argv.image,page)))

            filter_scores=[]

            if page=='opening':

                for name in FILTERS:

                    if page=='opening' and name=='EFFECTIVE_EFFECT':continue

                    result=context.run_recognition(self.namespace+page+'Filter'+name,argv.image)

                    if result is not None and result.hit:

                        word=_native_word(result);x,y,w,h=word['box']

                        expanded=(max(0,x-30),max(0,y-15),min(argv.image.shape[1]-max(0,x-30),w+60),

                            min(argv.image.shape[0]-max(0,y-15),h+30))

                        filter_scores.append((name,_blue(argv.image,expanded)))

            selected_filter=max(filter_scores,key=lambda value:value[1],default=(None,0))

            current_filter=(selected_filter[0] if selected_filter[1]>=.35 else None) if page=='opening' else _selected_filter(argv.image,page)

            # Count is actual same-frame OCR, never supplied expected state.

            count_word=_native_word(context.run_recognition(self.namespace+'OpeningCount',argv.image)) if page=='opening' else None

            count=int(count_word['text'][0]) if count_word is not None else None

            observation=self.recognizer.recognize(argv.image,page,roster=params.get('roster'),

                current_filter=current_filter,observed_count=count,**({'members_only':True} if members_only else {}))

            if page=='opening':

                observation['count']=count;observation['count_native']=count_word

                observation['required_count']=int(count_word['text'][-1]) if count_word is not None else None

            else:observation['count']=len(observation['selected'])
            if not members_only and page in ('battle','boss') and current_filter=='EFFECTIVE_EFFECT' and not observation['cards']:
                notice=context.run_recognition(self.namespace+'EffectiveEmptyNotice',argv.image)
                if notice is None or not notice.hit:return None
                observation.update(empty_effective_verified=True,empty_effective_native=_native_word(notice),stable=True)

            if page=='boss':

                if observation['boss_tab'] is None:return None


            box=[0,0,1,1]

            if mode=='toggle':

                if not observation['stable']:return None

                role=params.get('role_id');expected=params.get('expected_selected')

                if type(expected) is not list or expected!=sorted(set(expected)) or role not in self.recognizer.matcher.names:return None

                if page=='opening':

                    if any(c['selected'] and not c['trusted'] for c in observation['cards']):return None

                    if observation['count']!=len(expected) or not set(observation['selected'])<=set(expected) or role in expected:return None

                    choices=[c for c in observation['cards'] if c['trusted'] and c['character_id']==role and not c['selected']]

                else:

                    if any(not c['trusted'] for c in observation['members']) or observation['selected']!=expected:return None

                    choices=[c for c in (observation['members'] if role in expected else observation['cards'])

                        if c['trusted'] and c['character_id']==role]

                if len(choices)!=1:return None

                box=choices[0]['box']

                # Upstream opening taps 50%/45%; team select/deselect taps center.

                # Return an exact Maa target while retaining the full observed card.

                if page in ('opening','battle','boss'):

                    x,y,w,h=box

                    # Free event picks the upstream card's lower-left plus control.
                    free=page=='opening' and params.get('event_free') is True
                    box=[x+min(w-1,math.floor(w*(.16 if free else .50)+.5)),

                        y+min(h-1,math.floor(h*(.84 if free else .45 if page=='opening' else .50)+.5)),1,1]

            elif mode=='scroll':

                if params.get('direction') not in ('up','down'):return None

                # Fresh page recognition supplies the swipe viewport; no prior pixel equality.

                box=list(VIEWPORTS[page])

            elif mode=='tab':

                tab=params.get('tab')

                if page!='boss' or type(tab) is not int or not 1<=tab<=3:return None

                if params.get('expected_tab')!=observation['boss_tab']:return None

                box=list(BOSS_TABS[tab-1])

            elif mode=='invite':

                expected=params.get('expected_selected')
                required=params.get('required_count',3)

                if (page!='opening' or type(required) is not int or not 1<=required<=3
                    or not observation['stable'] or observation['count']!=required
                    or observation['required_count']!=required

                    or type(expected) is not list or expected!=sorted(set(expected)) or len(expected)!=required

                    or not set(observation['selected'])<=set(expected) or any(c['selected'] and not c['trusted'] for c in observation['cards'])):return None

                button=context.run_recognition('LabyOpeningInviteLabel',argv.image)

                bright=context.run_recognition('LabyOpeningInviteBright',argv.image)

                if button is None or not button.hit or button.box is None or bright is None or not bright.hit:return None

                box=list(button.box)

            elif mode=='start':

                expected=params.get('expected_selected')

                if (not observation['stable'] or any(not c['trusted'] for c in observation['members'])

                    or type(expected) is not list or expected!=observation['selected']

                    or params.get('expected_size')!=len(expected)):return None

                if page=='boss':

                    teams=params.get('expected_teams')

                    if (observation['boss_tab']!=3 or type(teams) is not list or len(teams)!=3

                        or any(type(team) is not list or team!=sorted(set(team)) or len(team)>5 for team in teams)

                        or not any(teams) or teams[2]!=expected):return None

                    all_ids=[role for team in teams for role in team]

                    if len(all_ids)!=len(set(all_ids)) or any(role not in self.recognizer.matcher.names for role in all_ids):return None

                elif not 1<=len(expected)<=5:return None

                button=context.run_recognition('LabyTeamBattleStartLabel',argv.image)

                bright=context.run_recognition('LabyTeamBattleStartBright',argv.image)

                if button is None or not button.hit or button.box is None or bright is None or not bright.hit:return None

                box=list(button.box)

            if self.recognizer.stopped() or context.tasker.stopping:return None

            return self.AnalyzeResult(box,observation)

        except Exception as error:
            import logging
            logging.getLogger(__name__).error("Labyrinth character recognition failed: %s",type(error).__name__)
            return None





class MaaCharacters:

    """Owner-scoped native registration, observation and transactional inputs."""

    def __init__(self,tasker,*,recognizer=None,timings=None):

        from uuid import uuid4

        from .timing import Timings

        self.tasker=tasker;self.timings=timings or Timings();self.recognizer=recognizer or CharacterRecognizer(stopped=lambda:bool(getattr(self.tasker,'stopping',False)))

        self.namespace='LabyCharacters'+uuid4().hex

        self.callback=CharacterRecognition(self.namespace,self.recognizer)

        if not tasker.resource.register_custom_recognition(CALLBACK,self.callback):raise BatchStopped('character_callback_unavailable')

        if not tasker.resource.override_pipeline(character_pipeline(self.namespace)):raise BatchStopped('character_pipeline_unavailable')



    def run(self,suffix,params,*,input=False):

        import time
        from .. import native

        from .game_session import _root

        node=self.namespace+suffix;resource=self.tasker.resource;original=resource.get_node_data(node)

        if suffix=='FilterStable':
            params=dict(params,freeze_timeout_ms=max(0,int(min(2500,(native.RUN_DEADLINE.get()-time.monotonic())*1000))))
        changes=dict(custom_recognition_param=params)

        if suffix=='ScrollOnce':

            page=params['page'];x,y,w,h=VIEWPORTS[page]

            start=[x+w//2,y+int(h*.80)];end=[x+w//2,y+int(h*.22)]

            if params['direction']=='up':start,end=end,start

            changes.update(begin=start,end=end)

        if not resource.override_pipeline({node:changes}):
            if input:raise native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True,reason='character_guard_failed'))
            raise BatchStopped('character_guard_failed')

        try:

            with self.timings.measure('page_wait'):receipt=native.run(self.tasker,node,reset_hits=(node,) if input else ())

            root=_root(receipt,node);action=root.get('action')

            if type(action) is not dict or action.get('name')!=node or action.get('success') is not True:

                raise BatchStopped('character_input_unknown' if input else 'character_readback_unknown')

            # Input callers consume only the native ACK. This owned namespace
            # is configured again on every call; restoring temporary parameters
            # or parsing business detail here must not hide a completed click.
            if input:return None,receipt
            raw=root.get('recognition');best=raw.get('best') if type(raw) is dict else None

            detail=best.get('detail') if type(best) is dict else None

            if type(detail) is not dict or detail.get('page')!=params['page']:raise BatchStopped('character_readback_unknown')
            if params['page']=='joined':
                from .portrait_cache import joined_portrait_readback
                joined_portrait_readback(receipt)
            if suffix=='FilterStable' and detail.get('filter_frozen') is not True:raise BatchStopped('character_readback_unknown')

        except BaseException:

            try:resource.override_pipeline({node:original})

            except Exception:pass

            raise

        else:

            if not resource.override_pipeline({node:original}):raise BatchStopped('character_guard_restore_failed')

            return detail,receipt



    def probe(self,batch,page):
        """One pure native read for optional same-page successor comparison."""
        batch.checkpoint()
        try:observation,_=self.run('Read',dict(mode='read',page=page,roster=None))
        except BatchStopped as error:
            batch.checkpoint()
            if error.args not in (('game_session_readback_unknown',),('character_readback_unknown',)):raise
            return None
        batch.checkpoint()
        return observation

    def wait_for(self,batch,page,predicate,*,roster=None,timeout=12,members_only=False):

        import time

        from .. import native

        deadline=min(native.RUN_DEADLINE.get(),time.monotonic()+timeout)

        while True:

            if time.monotonic()>=deadline:raise BatchStopped('character_feedback_unknown')

            token=native.RUN_DEADLINE.set(deadline)

            try:observation,receipt=self.read(batch,page,roster=roster,stable=False,**({'members_only':True} if members_only else {}))

            finally:native.RUN_DEADLINE.reset(token)

            if time.monotonic()>=deadline:raise BatchStopped('character_feedback_unknown')

            if observation['stable'] and predicate(observation):return observation,receipt



    def read(self,batch,page,*,roster=None,stable=True,timeout=12,expected_role=None,members_only=False):

        import time

        from .. import native

        deadline=min(native.RUN_DEADLINE.get(),time.monotonic()+timeout)
        timeout_reason='character_stability_unknown' if stable else 'character_readback_unknown'
        last_error=None

        while True:

            batch.checkpoint()

            if time.monotonic()>=deadline:raise BatchStopped(timeout_reason) from last_error

            token=native.RUN_DEADLINE.set(deadline)

            try:

                try:observation,receipt=self.run('Read',dict(mode='read',page=page,roster=list(roster) if roster else None,**({'expected_role':expected_role} if expected_role is not None else {}),**({'members_only':True} if members_only else {})))

                except BatchStopped as error:
                    batch.checkpoint()
                    if str(error) not in ('game_session_readback_unknown','character_readback_unknown'):raise
                    last_error=error
                    continue
                except RuntimeError as error:

                    if error.args not in (('Native task exceeded 90 seconds or inspection deadline',),

                            ('Inspection deadline exceeded before task input',),
                    ('Context deadline exceeded after synchronous return; outcome unknown',)):raise

                    batch.checkpoint()  # A real Stop takes priority over the read budget.

                    raise BatchStopped(timeout_reason) from error

            finally:native.RUN_DEADLINE.reset(token)

            batch.checkpoint()

            if time.monotonic()>=deadline:raise BatchStopped(timeout_reason) from last_error

            if not stable or observation['stable']:return observation,receipt





class CharacterNotFound(BatchStopped):
    """A completed visible-list search, not an unknown input or lost identity."""
    def __init__(self,targets,observation,exhausted_filters):
        super().__init__('character_not_visible')
        self.targets=tuple(sorted(set(targets)))
        self.observation=observation
        self.exhausted_filters=tuple(exhausted_filters)


class CharacterNavigation:

    """Bounded page-scoped probe inputs, each settled by a new native read."""

    def __init__(self,characters,stamp,*,roster=None):self.characters,self.stamp,self.roster=characters,stamp,roster

    def move(self,batch,enter_id,page,observation,*,direction=None,filter=None,tab=None,block_id=None):

        params=dict(page=page)
        # Keep the existing owned roster through navigation and feedback.
        if self.roster is not None:params['roster']=list(self.roster)

        if direction is not None:

            suffix='ScrollOnce';kind='character_scroll';params.update(mode='scroll',direction=direction,signature=observation['signature'])

            fields=dict(enter_id=enter_id,page=page,direction=direction)

        elif filter is not None:

            suffix='FilterOnce';kind='character_filter';params.update(mode='filter',filter=filter)

            fields=dict(enter_id=enter_id,page=page,filter=filter)

        elif tab is not None:

            suffix='TabOnce';kind='boss_team_tab';params.update(mode='tab',tab=tab,expected_tab=observation['boss_tab'])

            fields=dict(enter_id=enter_id,page=page,tab=tab)

        else:raise ValueError('character_move_required')

        if block_id is not None:fields['block_id']=block_id

        def send(oid):
            batch.checkpoint();sent=self.characters.run(suffix,params,input=True)[1];batch.checkpoint()
            return self.stamp(source='maa',operation_id=oid,**fields,native=sent,input_native=sent,
                input_action_verified=True,client_page_verified=False)
        import time
        from .. import native
        token=native.RUN_DEADLINE.set(min(native.RUN_DEADLINE.get(),time.monotonic()+12)) if filter is not None else None
        try:
            clicked=batch.transaction(kind,fields,send)
            if filter is not None:
                batch.checkpoint()
                try:self.characters.run('FilterStable',dict(params,mode='freeze'),input=False)
                except (BatchStopped,RuntimeError) as error:
                    batch.checkpoint()
                    if str(error) not in ('game_session_readback_unknown','character_readback_unknown',
                        'Native task exceeded 90 seconds or inspection deadline',
                        'Inspection deadline exceeded before task input',
                        'Context deadline exceeded after synchronous return; outcome unknown'):raise
                    raise BatchStopped('character_feedback_unknown') from error
                batch.checkpoint()
            previous=None
            def settled(value):
                nonlocal previous
                if (filter is not None and value['filter']!=filter) or (tab is not None and value['boss_tab']!=tab):return False
                if direction is None:return True
                # An ACK proves the swipe, not that the list has finished rebounding.
                # Sample only post-ACK reads, without making portrait confidence a motion gate.
                before=previous;previous=value
                if before is None:return False
                def position(view):
                    scroll=view.get('scrollbar',{});pos=scroll.get('position')
                    return pos if (scroll.get('visible') is True and scroll.get('can_scroll') is True
                        and type(pos) in (int,float) and math.isfinite(pos) and 0<=pos<=1) else None
                a,b=position(before),position(value)
                if a is not None or b is not None:return a is not None and b is not None and abs(a-b)<=.02
                return self.unchanged(before,value)
            after,receipt=self.characters.wait_for(batch,page,settled,**({'roster':self.roster} if self.roster is not None else {}))
            batch.observation(self.stamp(source='maa',operation_id=clicked['operation_id'],**fields,native=receipt,
                input_native=clicked['input_native'],input_action_verified=True,client_page_verified=True),**fields)
            return after
        finally:
            if token is not None:native.RUN_DEADLINE.reset(token)

    @staticmethod

    def trusted_content(observation):

        cards=observation.get('cards',())

        return observation.get('stable') is True and (any(c.get('trusted') is True for c in cards)
            or not cards and observation.get('filter')=='EFFECTIVE_EFFECT' and observation.get('empty_effective_verified') is True)



    @staticmethod
    def unchanged(before,after):
        """Keep exact evidence, or whole visible card identities plus stationary scrollbar.
        Elastic rebound can move every box without changing the list contents.
        """
        if before.get('signature') is not None and before['signature']==after.get('signature'):return True
        def content(value,*,whole=False):
            def rows(key):
                result=[];viewport=VIEWPORTS.get(value.get('page'))
                for c in value.get(key,()):
                    slot=c.get('slot_id');box=c.get('box')
                    if whole and key=='cards' and viewport and box:
                        # Rebound clips a row and renumbers slots; its presence is not list motion.
                        if box[1]<viewport[1] or box[1]+box[3]>viewport[1]+viewport[3]:continue
                        slot=None
                    result.append((slot,c.get('character_id'),c.get('trusted'),
                        c.get('selected') if value.get('page')=='opening' or key=='members' else None))
                return tuple(result)
            return (value.get('page'),value.get('filter'),value.get('count'),value.get('boss_tab'),rows('cards'),rows('members'))
        if content(before)!=content(after):
            if content(before,whole=True)!=content(after,whole=True):return False
            # Fresh trusted identities must reach the next scan/seek iteration.
            if not {c.get('character_id') for c in after.get('cards',()) if c.get('trusted')} <= {c.get('character_id') for c in before.get('cards',()) if c.get('trusted')}:return False
        a,b=before.get('scrollbar',{}),after.get('scrollbar',{})
        if not a.get('visible') or not b.get('visible'):return False
        if a.get('can_scroll') is False and b.get('can_scroll') is False:return True
        x,y=a.get('position'),b.get('position')
        # Same native scrollbar tolerance already used by the viewport cache.
        return type(x) in (int,float) and type(y) in (int,float) and math.isfinite(x) and math.isfinite(y) and abs(x-y)<=.02

    def edge(self,batch,enter_id,page,observation,direction,*,block_id=None,limit=24):

        """Two unchanged list observations end scrolling, even with unknown portraits."""

        same=0;anchor=observation

        for _ in range(limit):

            before=observation

            observation=self.move(batch,enter_id,page,before,direction=direction,block_id=block_id)

            unchanged=self.unchanged(anchor,observation)

            same=same+1 if unchanged else 0
            if not unchanged:anchor=observation

            if same>=2:return observation

        raise BatchStopped('character_list_edge_unknown')



    def seek(self,batch,enter_id,page,observation,targets,*,block_id=None,filters=()):

        """Upstream missing-role filter order, then ALL and bounded top-to-bottom."""

        wanted=set(targets);filter_order=tuple(dict.fromkeys((*filters,'ALL'))) if page!='roster' else (None,)
        exhausted_filters=[]

        def found():

            return [c for c in observation['cards'] if c['trusted'] and c['character_id'] in wanted]

        if found():return observation,found()

        for filter in filter_order:

            if filter is not None and observation['filter']!=filter:

                observation=self.move(batch,enter_id,page,observation,filter=filter,block_id=block_id)

            if found():return observation,found()
            observation=self.edge(batch,enter_id,page,observation,'up',block_id=block_id)

            same=0;anchor=observation

            for _ in range(24):

                matches=found()

                if matches:return observation,matches

                before=observation

                observation=self.move(batch,enter_id,page,before,direction='down',block_id=block_id)

                unchanged=self.unchanged(anchor,observation)

                same=same+1 if unchanged else 0
                if not unchanged:anchor=observation

                if same>=2:break

            else:raise BatchStopped('character_list_edge_unknown')
            exhausted_filters.append(observation.get('filter'))

        raise CharacterNotFound(wanted,observation,exhausted_filters)

