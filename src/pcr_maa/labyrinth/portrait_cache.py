"""Same-frame joined-page identity from the shared full icon pack.

character_recognition's joined-fullpack-v1 callback supplies one portrait
identity and the native name word(s) for every row. This module validates that
receipt (trusted portrait, conflict rejection, distinct ids, exact
roles_joined) and returns Arrival facts. It also owns the measured one/two/three
row page pipeline used only to classify the joined page, never to decide
identity from names alone.
"""
import math,re
from . import arrivals
from .batch import BatchStopped
from .relics import _inside

PORTRAIT_ROIS=tuple((359,245+94*i,50,32) for i in range(3))


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


def joined_portrait_readback(receipt):
    """Validate one shared fullpack joined readback; legacy receipts stop."""
    def unknown():raise BatchStopped('portrait_joined_unknown')
    if (type(receipt) is not dict or receipt.get('native_ok') is not True
            or receipt.get('tasker_inited') is not True
            or type(receipt.get('task_id')) is not int or receipt['task_id']<=0):unknown()
    nodes=receipt.get('nodes')
    if type(nodes) is not list or len(nodes)!=1 or type(nodes[0]) is not dict:unknown()
    root=nodes[0];name=root.get('name')
    if root.get('completed') is not True:unknown()
    if not (type(name) is str and re.fullmatch(r'LabyCharacters[0-9a-f]{32}Read',name)):unknown()
    return _fullpack_joined_readback(root)


def _fullpack_joined_readback(root):
    """Validate the shared same-frame reader, without expected/configured IDs."""
    from .character_recognition import CONFIDENCE,RIVAL_MARGIN,canonical_id,load_character_pack,resolve_character_name
    from .roles import load_optimizer
    def unknown():raise BatchStopped('portrait_joined_unknown')
    raw=root.get('recognition');action=root.get('action')
    if (type(raw) is not dict or type(action) is not dict
            or action.get('name')!=root['name'] or action.get('success') is not True):unknown()
    best=raw.get('best');detail=best.get('detail') if type(best) is dict else None
    if (raw.get('all')!=[best] or raw.get('filtered')!=[best] or type(detail) is not dict
            or detail.get('page')!='joined' or detail.get('joined_identity_schema')!='joined-fullpack-v1'):unknown()
    portraits=detail.get('portraits')
    if type(portraits) is not list or not 1<=len(portraits)<=3:unknown()
    entries,_,_=load_character_pack();icons={e['unit_icon_id']:e['character_id'] for e in entries}
    optimizer=load_optimizer();items=[]
    for slot,p in enumerate(portraits):
        if (type(p) is not dict or type(p.get('slot')) is not int or p['slot']!=slot
                or p.get('box')!=[353,233+94*slot,60,60] or p.get('trusted') is not True):unknown()
        key=p.get('character_id')
        if type(key) is not str or canonical_id(key)!=key or key=='1000' or key not in optimizer.profiles:unknown()
        words=p.get('name_native');word=None;named=None
        if type(words) is not list or len(words)>1:unknown()
        if words:
            word=words[0]
            if (type(word) is not dict or type(word.get('text')) is not str
                    or type(word.get('score')) not in (int,float) or not arrivals.THRESHOLD<=word['score']<=1
                    or type(word.get('box')) is not list or not _inside(word['box'],arrivals.NAME_ROIS[slot])):unknown()
            try:named=resolve_character_name(word['text'])
            except BatchStopped:pass
        if p.get('identity_source')=='portrait':
            confidence=p.get('confidence');rival=p.get('rival_confidence');margin=p.get('rival_margin')
            if (any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in (confidence,rival,margin))
                    or confidence<CONFIDENCE or margin<RIVAL_MARGIN or abs(confidence-rival-margin)>1e-6
                    or type(p.get('unit_icon_id')) is not str or icons.get(p['unit_icon_id'])!=key
                    or named is not None and named!=key):unknown()
            source='full-pack-portrait';score=confidence
        elif p.get('identity_source')=='maa_name':
            if word is None or named!=key:unknown()
            source='upstream-complete-catalog-name';score=word['score']
        else:unknown()
        items.append(arrivals.Arrival(slot,key,word['text'] if word else '',optimizer.profiles[key].data['displayName'],
            source,tuple(word['box'] if word else p['box']),score))
    ids=[item.character_id for item in items]
    if len(set(ids))!=len(ids) or detail.get('roles_joined')!=ids or detail.get('portrait')!=portraits[0]:unknown()
    return f'joined{len(items)}',tuple(items)
