"""Per-run portraits learned only from the sampled one/two/three-row joined layouts.

The caller pairs the original BGR Maa frame with strict Arrival facts or a complete
native joined-page readback. Incremental readbacks preserve actual name scores.
Arrival facts do not contain a frame hash; this cache cannot itself prove that
pairing, capture freshness, account identity, ownership or selection. It preserves
pixel hashes and source references, and never silently replaces conflicting art.
Only the observed 50x32 inner crop at 2x and current-status roster page are
qualified. Templates live in memory via Maa override_image, never bundled assets.
"""
from dataclasses import dataclass
import hashlib,math,re,json
from uuid import uuid4
import cv2,numpy as np
from . import arrivals
from .batch import BatchStopped
from .relics import _words,_normalized,_ocr,_inside

FRAME_SHAPE=(720,1280,3)
PORTRAIT_ROIS=tuple((359,245+94*i,50,32) for i in range(3))
GRID_ROI=(70,210,1110,270)
SCALE=2
MATCH_THRESHOLD=.9
TITLE_ROI=(490,35,300,50)
ROLE_ROI=(140,90,240,43)


def _hash(image):return hashlib.sha256(image.tobytes()).hexdigest()


@dataclass(frozen=True)
class Portrait:
    character_id:str
    canonical_name:str
    raw_name:str
    source_reference:str
    source_frame_sha256:str
    portrait_sha256:str
    source_box:tuple[int,int,int,int]
    source_layout:str='arrival3'
    name_box:tuple[int,int,int,int]|None=None
    name_score:float|None=None
    source_receipt_sha256:str|None=None


@dataclass(frozen=True)
class Candidate:
    box:tuple[int,int,int,int]
    score:float


@dataclass(frozen=True)
class PortraitObservation:
    character_id:str
    source_frame_sha256:str
    portrait_sha256:str
    candidates:tuple[Candidate,...]
    matches:tuple[Candidate,...]
    native:dict

    @property
    def unique(self):return len(self.matches)==1

    def single(self):
        if not self.unique:raise BatchStopped('portrait_ambiguous')
        return self.matches[0]


class RunPortraitCache:
    def __init__(self,run_reference):
        if (type(run_reference) is not str or not run_reference or len(run_reference)>128
                or any(ord(c)<32 for c in run_reference)):
            raise BatchStopped('portrait_run_reference_required')
        self.run_reference=run_reference
        self.namespace='LabyRunPortrait'+uuid4().hex
        self._portraits={};self._images={}

    def add(self,arrived,frame,*,source_reference):
        """Atomically learn three paired portraits; no fallback or overwrite."""
        arrived=arrivals._validated(arrived)
        if (type(frame) is not np.ndarray or frame.shape!=FRAME_SHAPE or frame.dtype!=np.uint8
                or type(source_reference) is not str or not source_reference
                or len(source_reference)>512 or any(ord(c)<32 for c in source_reference)):
            raise BatchStopped('portrait_source_unknown')
        original=frame.copy();frame_hash=_hash(original);prepared={}
        owners={p.portrait_sha256:key for key,p in self._portraits.items()}
        for item,box in zip(arrived,PORTRAIT_ROIS):
            x,y,w,h=box;image=original[y:y+h,x:x+w].copy()
            if image.shape!=(h,w,3) or not np.any(image!=image[0,0]):
                raise BatchStopped('portrait_source_unknown')
            portrait_hash=_hash(image)
            if portrait_hash in owners and owners[portrait_hash]!=item.character_id:
                raise BatchStopped('portrait_conflicting')
            owners[portrait_hash]=item.character_id
            if item.character_id in self._portraits and self._portraits[item.character_id].portrait_sha256!=portrait_hash:
                raise BatchStopped('portrait_conflicting')
            prepared[item.character_id]=(Portrait(item.character_id,item.canonical_name,item.raw_text,
                source_reference,frame_hash,portrait_hash,box),image)
        for key,(portrait,image) in prepared.items():
            if key not in self._portraits:
                self._portraits[key]=portrait;self._images[key]=image
        return tuple(self._portraits[item.character_id] for item in arrived)

    def add_joined(self,receipt,frame,*,source_reference):
        """Learn only names contained in a complete native joined-page readback.

        No expected IDs or verified-wrapper booleans are accepted. The owner still
        pairs this receipt with its original frame; neither has a capture token.
        Freshness, account and run membership remain outside this cache contract.
        """
        layout,items=joined_portrait_readback(receipt)
        if (type(frame) is not np.ndarray or frame.shape!=FRAME_SHAPE or frame.dtype!=np.uint8
                or type(source_reference) is not str or not source_reference
                or len(source_reference)>512 or any(ord(c)<32 for c in source_reference)):
            raise BatchStopped('portrait_source_unknown')
        original=frame.copy();frame_hash=_hash(original);prepared={}
        try:
            receipt_hash=hashlib.sha256(json.dumps(receipt,ensure_ascii=False,sort_keys=True,
                separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()
        except (TypeError,ValueError):raise BatchStopped('portrait_source_unknown') from None
        owners={p.portrait_sha256:key for key,p in self._portraits.items()}
        for item,box in zip(items,PORTRAIT_ROIS):
            x,y,w,h=box;image=original[y:y+h,x:x+w].copy()
            if image.shape!=(h,w,3) or not np.any(image!=image[0,0]):
                raise BatchStopped('portrait_source_unknown')
            portrait_hash=_hash(image)
            if portrait_hash in owners and owners[portrait_hash]!=item.character_id:
                raise BatchStopped('portrait_conflicting')
            owners[portrait_hash]=item.character_id
            if item.character_id in self._portraits and self._portraits[item.character_id].portrait_sha256!=portrait_hash:
                raise BatchStopped('portrait_conflicting')
            prepared[item.character_id]=(Portrait(item.character_id,item.canonical_name,item.raw_text,
                source_reference,frame_hash,portrait_hash,box,layout,item.box,item.score,receipt_hash),image)
        for key,(portrait,image) in prepared.items():
            if key not in self._portraits:
                self._portraits[key]=portrait;self._images[key]=image
        return tuple(self._portraits[item.character_id] for item in items)

    def portrait(self,character_id):
        if type(character_id) is not str or character_id not in self._portraits:
            raise BatchStopped('portrait_unknown')
        return self._portraits[character_id]

    def image(self,character_id):
        self.portrait(character_id)
        return self._images[character_id].copy()

    def template_name(self,character_id):
        self.portrait(character_id)
        return self.namespace+'/'+character_id+'-2x.png'

    def install_images(self,override_image):
        """Use native Resource.override_image or Context.override_image explicitly.

        ContextTasker's resource facade does not expose override_image. Callers
        must pass an authorized native image sink; this code does not reach into
        its private facade or write images into assets.
        """
        if not callable(override_image):raise BatchStopped('portrait_image_sink_unknown')
        for key in self._portraits:
            image=self._images[key]
            if _hash(image)!=self._portraits[key].portrait_sha256:
                raise BatchStopped('portrait_conflicting')
            resized=cv2.resize(image,(100,64),interpolation=cv2.INTER_LINEAR)
            if override_image(self.template_name(key),resized) is not True:
                raise BatchStopped('portrait_image_sink_unknown')

    def node_name(self,character_id):
        self.portrait(character_id)
        return self.namespace+character_id+'Observed'

    def pipeline(self,character_id,*,roi=GRID_ROI):
        self.portrait(character_id)
        if (type(roi) not in (tuple,list) or not _inside(roi,GRID_ROI)
                or roi[2]<100 or roi[3]<64):
            raise BatchStopped('portrait_roi_unknown')
        match=self.namespace+character_id+'Match'
        title=self.namespace+'RosterTitle';role=self.namespace+'RosterRole'
        return {
            title:_ocr('^现在的状况$',TITLE_ROI),
            role:_ocr('^角色$',ROLE_ROI),
            match:dict(recognition='TemplateMatch',template=self.template_name(character_id),
                roi=list(roi),threshold=MATCH_THRESHOLD,method=5,action='DoNothing',next=[],
                timeout=1500,pre_delay=0,post_delay=0),
            self.node_name(character_id):dict(recognition='And',all_of=[title,role,match],
                action='DoNothing',next=[],timeout=3000,pre_delay=0,post_delay=0),
        }

    def readback(self,receipt,character_id,*,roi=GRID_ROI):
        portrait=self.portrait(character_id)
        expected=self.pipeline(character_id,roi=roi)[self.node_name(character_id)]['all_of']
        if type(receipt) is not dict or receipt.get('native_ok') is not True:
            raise BatchStopped('portrait_unmatched_or_unknown')
        nodes=receipt.get('nodes')
        if (type(nodes) is not list or len(nodes)!=1 or type(nodes[0]) is not dict
                or nodes[0].get('name')!=self.node_name(character_id) or nodes[0].get('completed') is not True):
            raise BatchStopped('portrait_match_unknown')
        raw=nodes[0].get('recognition')
        if (type(raw) is not list or len(raw)!=3 or any(type(r) is not dict for r in raw)
                or any(type(r.get('name')) is not str for r in raw) or {r['name'] for r in raw}!=set(expected)):
            raise BatchStopped('portrait_match_unknown')
        rows={r['name']:r for r in raw}
        try:
            if (_normalized(_words(rows[expected[0]].get('detail'),TITLE_ROI)[0].text)!='现在的状况'
                    or _normalized(_words(rows[expected[1]].get('detail'),ROLE_ROI)[0].text)!='角色'):
                raise BatchStopped('portrait_match_unknown')
        except BatchStopped:raise BatchStopped('portrait_match_unknown') from None
        row=rows[expected[2]];detail=row.get('detail')
        if row.get('algorithm')!='TemplateMatch' or type(detail) is not dict:
            raise BatchStopped('portrait_match_unknown')
        def candidates(values):
            if type(values) is not list:raise BatchStopped('portrait_match_unknown')
            output=[]
            for value in values:
                if (type(value) is not dict or not _inside(value.get('box'),roi)
                        or value['box'][2:]!=[100,64] or type(value.get('score')) not in (int,float)
                        or not math.isfinite(value['score']) or not -1<=value['score']<=1):
                    raise BatchStopped('portrait_match_unknown')
                output.append(Candidate(tuple(value['box']),float(value['score'])))
            if len(set(output))!=len(output):raise BatchStopped('portrait_match_unknown')
            return tuple(output)
        all_values=candidates(detail.get('all'));matched=candidates(detail.get('filtered'))
        if (not matched or any(c not in all_values or c.score<MATCH_THRESHOLD for c in matched)
                or detail.get('best') not in detail.get('filtered')
                or row.get('box')!=detail['best'].get('box')):
            raise BatchStopped('portrait_match_unknown')
        return PortraitObservation(character_id,portrait.source_frame_sha256,portrait.portrait_sha256,
                                   all_values,matched,receipt)


# These one/two/three-row layouts share the measured portrait geometry. No other
# joined page or card layout is inferred from count, configuration or protocol.
def joined_portrait_pipeline(count):
    """Read only the sampled one/two/three-row joined layouts, without inputs."""
    if type(count) is not int or count not in (1,2,3):
        raise BatchStopped('portrait_joined_layout_unknown')
    prefix=f'LabyPortraitJoined{count}'
    nodes={prefix+'Title':arrivals._ocr('^角色加入$',arrivals.TITLE_ROI),
        prefix+'Message':arrivals._ocr('^.+成为了同伴[。.]?$' if count==1
            else '^以下角色已加入到伙伴中[。.]?$',(455,154,385,43) if count==1 else arrivals.MESSAGE_ROI),
        prefix+'Close':arrivals._ocr('^关闭$',arrivals.CLOSE_ROI)}
    if count==1:nodes[prefix+'Message']['only_rec']=True
    for slot in range(count):
        nodes[prefix+f'Name{slot}']=arrivals._ocr('.+',arrivals.NAME_ROIS[slot])
    for slot in range(count,3):
        # Same-frame native absence guard: the unused portrait rows are blank.
        nodes[prefix+f'Empty{slot}']=dict(recognition='ColorMatch',method=40,
            roi=list(PORTRAIT_ROIS[slot]),lower=[0,0,250],upper=[179,10,255],
            count=1580,connected=False,action='DoNothing',next=[],timeout=1500,
            pre_delay=0,post_delay=0)
    nodes[prefix+'Observed']=dict(recognition='And',all_of=list(nodes),
        action='DoNothing',next=[],timeout=3000,pre_delay=0,post_delay=0)
    return nodes


def joined_portrait_node(count):
    joined_portrait_pipeline(count)
    return f'LabyPortraitJoined{count}Observed'


def joined_portrait_readback(receipt):
    """Infer the observed layout and actual names, never caller-provided IDs.

    Accept exact local native observation receipts and the frozen opening's
    nested joined observation. Do not accept outer verified/role_id wrappers.
    """
    def unknown():raise BatchStopped('portrait_joined_unknown')
    if (type(receipt) is not dict or receipt.get('native_ok') is not True
            or receipt.get('tasker_inited') is not True
            or type(receipt.get('task_id')) is not int or receipt['task_id']<=0):unknown()
    nodes=receipt.get('nodes')
    if type(nodes) is not list or len(nodes)!=1 or type(nodes[0]) is not dict:unknown()
    root=nodes[0];name=root.get('name');raw=root.get('recognition')
    if root.get('completed') is not True:unknown()
    if name=='LabyCharactersArrivedObserved':
        return 'arrival3',arrivals.characters_arrived(receipt)
    if name in ('LabyOpeningJoinedCloseOnce','LabyOpeningAnyJoinedCloseOnce'):
        observed='LabyOpeningAnyJoinedObserved' if name=='LabyOpeningAnyJoinedCloseOnce' else 'LabyOpeningJoinedObserved'
        if (type(raw) is not list or len(raw)!=2
                or {r.get('name') for r in raw if type(r) is dict}!={'LabyJoinedCloseLabel',observed}):unknown()
        outer={r['name']:r for r in raw}
        if arrivals._word(outer['LabyJoinedCloseLabel'],(485,540,305,75))[0]!='关闭':unknown()
        joined=outer[observed]
        if joined.get('algorithm')!='And':unknown()
        name=observed;raw=joined.get('detail')
    if type(raw) is not list or any(type(r) is not dict or type(r.get('name')) is not str for r in raw):unknown()
    rows={r['name']:r for r in raw}
    if len(rows)!=len(raw):unknown()
    if name in ('LabyOpeningJoinedObserved','LabyOpeningAnyJoinedObserved'):
        layout='opening3';count=3;title='LabyJoinedTitle';close=None;message=None
        names=tuple('LabyOpeningJoinedName'+str(i) for i in range(3)) if name=='LabyOpeningAnyJoinedObserved' else (
            'LabyJoined1075','LabyJoined1351','LabyJoined1059')
        empty=()
    elif name=='LabyRecruitJoinedObserved':
        layout='recruit2';count=2;title='LabyRoleRewardJoinedTitle';close='LabyRoleRewardJoinedClose'
        message='LabyRecruitJoinedMessage';names=('LabyRecruitJoinedFirstName','LabyRecruitJoinedSecondName');empty=()
    elif name=='LabyRoleRewardJoinedObserved':
        layout='role_reward1';count=1;title='LabyRoleRewardJoinedTitle';close='LabyRoleRewardJoinedClose'
        message='LabyRoleRewardJoinedMessage';names=('LabyRoleRewardJoinedItem',);empty=()
    elif name in ('LabyPortraitJoined1Observed','LabyPortraitJoined2Observed','LabyPortraitJoined3Observed'):
        count=int(name[len('LabyPortraitJoined')]);layout=f'joined{count}';prefix=f'LabyPortraitJoined{count}'
        title=prefix+'Title';close=prefix+'Close';message=prefix+'Message'
        names=tuple(prefix+f'Name{i}' for i in range(count))
        empty=tuple(prefix+f'Empty{i}' for i in range(count,3))
    else:unknown()
    if set(rows)!={n for n in (title,close,message,*names,*empty) if n is not None}:unknown()
    # Opening's existing native ROI differs from the reward joined layout.
    title_roi=(480,90,320,65) if title=='LabyJoinedTitle' else arrivals.TITLE_ROI
    if arrivals._word(rows[title],title_roi)[0]!='角色加入':unknown()
    if close and arrivals._word(rows[close],arrivals.CLOSE_ROI)[0]!='关闭':unknown()
    from .rewards import resolve_role_name
    from .roles import load_optimizer
    optimizer=load_optimizer();items=[]
    for slot,node in enumerate(names):
        roi=(412,238,475,51) if node=='LabyRoleRewardJoinedItem' else arrivals.NAME_ROIS[slot]
        text,box,score=arrivals._word(rows[node],roi)
        normalized=arrivals.normalized_name(text)
        if count==3 and normalized=='佩可莉姆(夏日)':
            # Existing Opening's actual named readback qualifies this local
            # B-server display name against the scoring catalogue name.
            key='1075';canonical='贪吃佩可(夏日)';alias='qualified-three-character-opening-name'
            if optimizer.profiles.get(key) is None or optimizer.profiles[key].data['displayName']!=canonical:unknown()
        elif name=='LabyPortraitJoined3Observed' and normalized in arrivals._LOCAL_ALIASES:
            # Preserve aliases qualified for this complete three-row joined
            # modal only; no global, single-row or two-row OCR alias fallback.
            key,canonical,alias=arrivals._resolve(text)
            from .character_recognition import resolve_character_name
            try:catalog_key=resolve_character_name(text,optimizer=optimizer)
            except BatchStopped:pass
            else:
                if catalog_key!=key:unknown()
        elif name=='LabyOpeningAnyJoinedObserved' or name.startswith('LabyPortraitJoined'):
            from .character_recognition import resolve_character_name
            key=resolve_character_name(text)
            canonical=optimizer.profiles[key].data['displayName'];alias='upstream-complete-catalog-name'
        elif count==3:
            key,canonical,alias=arrivals._resolve(text)
        else:
            key=resolve_role_name(text,optimizer)
            canonical=optimizer.profiles[key].data['displayName'];alias='actual-joined-name'
        items.append(arrivals.Arrival(slot,key,text,canonical,alias,box,score))
    if len({i.character_id for i in items})!=count:unknown()
    if message:
        roi=(455,154,385,43) if count==1 else arrivals.MESSAGE_ROI
        value=arrivals._word(rows[message],roi)[0]
        if count==1:
            text=arrivals.normalized_name(value)
            expected=arrivals.normalized_name(items[0].raw_text)+'成为了同伴'
            if text not in (expected+'.',expected+'。'):unknown()
        elif value not in ('以下角色已加入到伙伴中。','以下角色已加入到伙伴中.'):unknown()
    for slot,node in zip(range(count,3),empty):
        row=rows[node];detail=row.get('detail');values=detail.get('filtered') if type(detail) is dict else None
        if (row.get('algorithm')!='ColorMatch' or type(values) is not list or len(values)!=1
                or type(values[0]) is not dict or values[0].get('box')!=list(PORTRAIT_ROIS[slot])
                or row.get('box')!=values[0]['box'] or type(values[0].get('count')) is not int
                or not 1580<=values[0]['count']<=1600
                or detail.get('all')!=values or detail.get('best')!=values[0]):unknown()
    return layout,tuple(items)
