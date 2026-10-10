"""Same-frame visible roster cards and a separately qualified one-shot close.

Only the sampled 1280x720 portrait-card border geometry is supported. These are
visible rectangles, not a complete roster or character IDs.
All page/OCR/colour operations use Maa; no game image templates are packaged.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import re
from maa.custom_recognition import CustomRecognition
from maa.define import AlgorithmEnum
from .batch import BatchStopped

THRESHOLD = .85
GRID_ROI = (55,205,1150,378)
_PAGE = {
    'Title': ((470,25,350,55),'现在的状况'),
    'RoleTab': ((190,90,120,50),'角色'),
    'RelicTab': ((510,90,180,50),'迷宫遗物'),
    'All': ((80,150,110,50),'全部'),
    'Sort': ((900,150,100,50),'等级'),
    'Close': ((500,605,280,70),'关闭'),
}
_CLOSE = 'LabyRosterCloseOnce'
_CUSTOM = 'LabyRosterScopedClose'
_DISABLED = {'qualified':False}


@dataclass(frozen=True)
class RosterPage:
    card_boxes: tuple[tuple[int,int,int,int],...]
    close_box: tuple[int,int,int,int]


def _unknown():
    raise BatchStopped('roster_page_unknown')


def _inside(box,roi):
    return (type(box) in (list,tuple) and len(box)==4 and all(type(v) is int for v in box)
        and box[2]>0 and box[3]>0 and box[0]>=roi[0] and box[1]>=roi[1]
        and box[0]+box[2]<=roi[0]+roi[2] and box[1]+box[3]<=roi[1]+roi[3])


def _ocr(expected,roi):
    return dict(recognition='OCR',expected=expected,roi=list(roi),threshold=THRESHOLD,
        action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)


def roster_pipeline():
    nodes={'LabyRoster'+name:_ocr('^'+text+'$',roi) for name,(roi,text) in _PAGE.items()}
    # Qualified sampled card-border RGB colours. Top: 99x1; bottom: 66x2.
    # Independent bottom readback prevents accepting a vertically clipped card.
    nodes['LabyRosterBorders']=dict(recognition='ColorMatch',roi=list(GRID_ROI),method=4,
        lower=[174,115,100],upper=[205,146,135],connected=True,count=40,
        action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)
    nodes['LabyRosterObserved']=dict(recognition='And',
        all_of=['LabyRoster'+name for name in _PAGE]+['LabyRosterBorders'],
        action='DoNothing',next=[],timeout=2500,pre_delay=0,post_delay=0)
    nodes[_CLOSE]=dict(recognition='Custom',custom_recognition=_CUSTOM,
        custom_recognition_param=dict(_DISABLED),action='Click',target=True,max_hit=1,
        next=[],timeout=2000,rate_limit=250,pre_delay=0,post_delay=0)
    return nodes


def _word(word,roi,minimum):
    if type(word) is not dict:_unknown()
    text,box,score=word.get('text'),word.get('box'),word.get('score')
    if (type(text) is not str or not text.strip() or len(text)>48 or any(ord(c)<32 for c in text)
        or type(box) is not list or not _inside(box,roi)
        or type(score) not in (int,float) or not minimum<=score<=1 or not math.isfinite(score)):_unknown()
    return text,tuple(box),float(score)


def _words(row,roi,pattern,maximum):
    if type(row) is not dict or row.get('algorithm')!='OCR':_unknown()
    detail=row.get('detail')
    if type(detail) is not dict:_unknown()
    all_words,filtered,best=detail.get('all'),detail.get('filtered'),detail.get('best')
    if (type(all_words) is not list or not 1<=len(all_words)<=128
        or type(filtered) is not list or not 1<=len(filtered)<=maximum):_unknown()
    for word in all_words:_word(word,roi,0)
    if filtered!=[w for w in all_words if w['score']>=THRESHOLD and re.fullmatch(pattern,w['text'])]:_unknown()
    if best!=filtered[0] or row.get('box')!=best.get('box'):_unknown()
    values=tuple(_word(word,roi,THRESHOLD) for word in filtered)
    if len({v[1] for v in values})!=len(values):_unknown()
    return values


def _border_boxes(row):
    if type(row) is not dict or row.get('algorithm')!='ColorMatch':_unknown()
    detail=row.get('detail')
    if type(detail) is not dict:_unknown()
    all_parts,filtered,best=detail.get('all'),detail.get('filtered'),detail.get('best')
    if (type(all_parts) is not list or not 1<=len(all_parts)<=6000
        or type(filtered) is not list or not filtered):_unknown()
    for part in all_parts:
        if (type(part) is not dict or set(part)!={'box','count'} or type(part['box']) is not list
            or not _inside(part['box'],GRID_ROI) or type(part['count']) is not int
            or not 1<=part['count']<=part['box'][2]*part['box'][3]):_unknown()
    if filtered!=[p for p in all_parts if p['count']>=40] or best!=filtered[0] or row.get('box')!=best['box']:_unknown()
    if len({tuple(p['box']) for p in filtered})!=len(filtered):_unknown()
    tops=[p['box'] for p in filtered if p['box'][2:]==[99,1] and p['count']==99]
    bottoms={tuple(p['box']) for p in filtered if p['box'][2:]==[66,2] and 120<=p['count']<=132}
    if not tops:_unknown()
    boxes=[]
    for x,y,_,_ in tops:
        box=(x-11,y,120,120)
        if not _inside(box,GRID_ROI) or (x-2,y+118,66,2) not in bottoms:_unknown()
        boxes.append(box)
    if len(set(boxes))!=len(boxes):_unknown()
    if any(a[0]<b[0]+b[2] and b[0]<a[0]+a[2] and a[1]<b[1]+b[3] and b[1]<a[1]+a[3]
           for i,a in enumerate(boxes) for b in boxes[i+1:]):_unknown()
    return tuple(sorted(boxes,key=lambda b:(b[1],b[0])))


def _rows_readback(raw):
    names=roster_pipeline()['LabyRosterObserved']['all_of']
    if (type(raw) is not list or len(raw)!=len(names)
        or any(type(row) is not dict or type(row.get('name')) is not str for row in raw)
        or {row['name'] for row in raw}!=set(names)):_unknown()
    rows={row['name']:row for row in raw};close=None
    for name,(roi,text) in _PAGE.items():
        words=_words(rows['LabyRoster'+name],roi,'^'+text+'$',1)
        if words[0][0]!=text:_unknown()
        if name=='Close':close=words[0][1]
    return RosterPage(_border_boxes(rows['LabyRosterBorders']),close)


def roster_readback(receipt):
    """Read only visible cards; neither total availability nor IDs are inferred."""
    if (type(receipt) is not dict or receipt.get('native_ok') is not True
        or receipt.get('tasker_inited') is not True or type(receipt.get('task_id')) is not int
        or receipt['task_id']<=0):_unknown()
    nodes=receipt.get('nodes')
    if (type(nodes) is not list or len(nodes)!=1 or type(nodes[0]) is not dict
        or nodes[0].get('name')!='LabyRosterObserved' or nodes[0].get('completed') is not True):_unknown()
    return _rows_readback(nodes[0].get('recognition'))


def _validated(page):
    if (type(page) is not RosterPage or type(page.card_boxes) is not tuple
        or not 1<=len(page.card_boxes)<=32 or type(page.close_box) is not tuple
        or not _inside(page.close_box,_PAGE['Close'][0])):_unknown()
    boxes=page.card_boxes
    if any(type(box) is not tuple or box[2:]!=(120,120) or not _inside(box,GRID_ROI) for box in boxes):_unknown()
    if len(set(boxes))!=len(boxes) or list(boxes)!=sorted(boxes,key=lambda b:(b[1],b[0])):_unknown()
    if any(a[0]<b[0]+b[2] and b[0]<a[0]+a[2] and a[1]<b[1]+b[3] and b[1]<a[1]+a[3]
           for i,a in enumerate(boxes) for b in boxes[i+1:]):_unknown()
    return page


def register_roster_recognition(resource):
    """Register once on the owning Resource, before borrowing a Context."""
    if not resource.register_custom_recognition(_CUSTOM,RosterCloseRecognition()):
        raise BatchStopped('roster_guard_registration_failed')


class RosterCloseRecognition(CustomRecognition):
    def analyze(self,context,argv):
        try:
            params=json.loads(argv.custom_recognition_param)
            if (type(params) is not dict or set(params)!={'qualified','card_boxes'}
                or params['qualified'] is not True or context.tasker.stopping):return None
            result=context.run_recognition('LabyRosterObserved',argv.image)
            if result is None or result.algorithm!=AlgorithmEnum.And or result.hit is not True:return None
            page=_rows_readback(result.raw_detail)
            if (params['card_boxes']!=[list(box) for box in page.card_boxes]
                or context.tasker.stopping):return None
            return self.AnalyzeResult(list(page.close_box),dict(status='scoped_roster_close',
                visible_card_boxes=[list(box) for box in page.card_boxes]))
        except Exception:return None


@contextmanager
def roster_guard(tasker,page):
    """Recheck the observed visible card layout; never renew a consumed budget."""
    page=_validated(page);resource=tasker.resource;original={_CLOSE:resource.get_node_data(_CLOSE)}
    params=dict(qualified=True,card_boxes=[list(box) for box in page.card_boxes])
    if not original[_CLOSE] or not resource.override_pipeline({_CLOSE:dict(custom_recognition_param=params)}):
        raise BatchStopped('roster_guard_failed')
    try:yield
    except BaseException:
        try:resource.override_pipeline(original)
        except Exception:pass
        raise
    else:
        if not resource.override_pipeline(original):raise BatchStopped('roster_guard_restore_failed')



class MaaRoster:
    """Complete roster only after actual trusted top and bottom probe feedback."""
    def __init__(self,tasker,stamp,*,characters=None):
        from .character_recognition import MaaCharacters
        self.tasker,self.stamp=tasker,stamp
        self.characters=characters or MaaCharacters(tasker)

    def scan(self,batch,enter_id,*,page='roster',expected_ids=()):
        from .character_recognition import CharacterNavigation
        if type(enter_id) is not int or enter_id<=0 or page not in ('roster','battle','boss'):raise BatchStopped('roster_identity_required')
        nav=CharacterNavigation(self.characters,self.stamp)
        observation,receipt=self.characters.read(batch,page)
        if page!='roster' and observation['filter']!='ALL':
            observation=nav.move(batch,enter_id,page,observation,filter='ALL')
        observation=nav.edge(batch,enter_id,page,observation,'up');seen=set();same=0;anchor=observation
        for _ in range(48):
            if not nav.trusted_content(observation) or any(not card['trusted'] for card in observation['cards']):raise BatchStopped('roster_identity_unknown')
            seen.update(c['character_id'] for c in observation['cards'])
            before=observation;observation=nav.move(batch,enter_id,page,before,direction='down')
            unchanged=nav.trusted_content(observation) and nav.unchanged(anchor,observation)
            same=same+1 if unchanged else 0
            if not unchanged:anchor=observation
            if same>=2:break
        else:raise BatchStopped('roster_complete_unknown')
        if not set(expected_ids)<=seen:raise BatchStopped('roster_owned_mismatch')
        observation,receipt=self.characters.read(batch,page)
        result=self.stamp(source='maa',enter_id=enter_id,roles=sorted(seen),complete_roster_verified=True,native=receipt)
        batch.observation(result,enter_id=enter_id,complete_roster_verified=True)
        return result
