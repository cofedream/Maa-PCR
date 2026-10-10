"""Same-frame character-detail identity and an exact scoped close.

This readback is a screen observation only, not account, ownership, selection,
arrival or fresh-capture proof. Closing requires a separate guarded input.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import math
import re
from .batch import BatchStopped
from .rewards import ALIASES, normalized_name
from .roles import load_optimizer

NAME_ROI = (670, 84, 330, 43)
THRESHOLD = .85
_PAGE = {
    'Title': ((530, 25, 250, 55), '角色详情'),
    'Level': ((650, 137, 51, 36), '等级'),
    'Attribute': ((670, 180, 145, 44), '属性值'),
    'Skill': ((850, 180, 180, 44), '技能'),
    'Intro': ((1040, 180, 170, 44), '简介'),
    'Close': ((500, 605, 280, 70), '关闭'),
}
_SENTINEL = '^__qualified_character_details_required__$'
# Official name fact: pinned cn-bilibili characters.landosolroster.json gives
# 花音/1349, while the existing candidate catalog calls that character 华音.
# A separate qualification for THIS page: Root visually confirmed 咲恋 in fresh
# roster-detail-observe-live-01/after.png. The sole full-name ROI returned 联恋,
# score .946084, box [672,94,47,27] (character-details-01/probe-receipt.json).
# This alias is neither imported from arrivals nor applied to other page readers.
_LOCAL_ALIASES = {
    '花音': ('1349', '华音', 'cn-bilibili-official-name'),
    '联恋': ('1028', '咲恋', 'qualified-character-details-ocr'),
}


@dataclass(frozen=True)
class CharacterDetails:
    raw_name: str
    canonical_name: str
    character_id: str
    alias_source: str
    name_box: tuple[int, int, int, int]
    name_score: float


def _ocr(expected, roi):
    return dict(recognition='OCR', expected=expected, roi=list(roi), threshold=THRESHOLD,
        action='DoNothing', next=[], timeout=1500, pre_delay=0, post_delay=0)


def character_details_pipeline():
    nodes = {'LabyCharacterDetails'+name:_ocr('^'+text+'$', roi)
             for name, (roi, text) in _PAGE.items()}
    nodes['LabyCharacterDetailsName'] = _ocr('.+', NAME_ROI)
    nodes['LabyCharacterDetailsNameGuard'] = _ocr(_SENTINEL, NAME_ROI)
    nodes['LabyCharacterDetailsObserved'] = dict(recognition='And',
        all_of=['LabyCharacterDetails'+name for name in _PAGE]+['LabyCharacterDetailsName'],
        action='DoNothing', next=[], timeout=3000, pre_delay=0, post_delay=0)
    nodes['LabyCharacterDetailsCloseOnce'] = dict(recognition='And',
        all_of=['LabyCharacterDetailsClose']+['LabyCharacterDetails'+name for name in _PAGE if name!='Close']
            +['LabyCharacterDetailsNameGuard'],
        action='Click', target=True, max_hit=1, next=[], timeout=2500,
        rate_limit=250, pre_delay=0, post_delay=0)
    return nodes


def _unknown():
    raise BatchStopped('character_details_unknown')


def _inside(box, roi, *, margin=0):
    return (type(box) in (list, tuple) and len(box)==4 and all(type(v) is int for v in box)
        and box[2]>0 and box[3]>0 and box[0]>=roi[0]+margin and box[1]>=roi[1]+margin
        and box[0]+box[2]<=roi[0]+roi[2]-margin and box[1]+box[3]<=roi[1]+roi[3]-margin)


def _word(row, roi, *, margin=0):
    detail=row.get('detail');values=detail.get('filtered') if type(detail) is dict else None
    if row.get('algorithm')!='OCR' or type(values) is not list or len(values)!=1 or type(values[0]) is not dict:
        _unknown()
    word=values[0];text,box,score=word.get('text'),word.get('box'),word.get('score')
    if (type(text) is not str or not text.strip() or len(text)>48 or any(ord(c)<32 for c in text)
        or type(box) is not list or not _inside(box,roi,margin=margin)
        or type(row.get('box')) is not list or not _inside(row['box'],roi,margin=margin) or row['box']!=box
        or type(score) not in (int,float) or not THRESHOLD<=score<=1 or not math.isfinite(score)
        or detail.get('all')!=values or detail.get('best')!=word):
        _unknown()
    return text,tuple(box),float(score)


def _identity(text):
    name=normalized_name(text);optimizer=load_optimizer()
    from .character_recognition import resolve_character_name
    try:keys={resolve_character_name(text,optimizer=optimizer)}
    except BatchStopped:keys=set()
    source='catalog'
    if name in ALIASES:keys.add(ALIASES[name]);source='existing-b-server-name'
    if name in _LOCAL_ALIASES:
        key,canonical,source=_LOCAL_ALIASES[name]
        profile=optimizer.profiles.get(key)
        if profile is None or normalized_name(profile.data.get('displayName',''))!=canonical:_unknown()
        keys.add(key)
    if len(keys)!=1:_unknown()
    key=next(iter(keys))
    if type(key) is not str or re.fullmatch(r'[1-9][0-9]{3,5}',key) is None or key not in optimizer.profiles:_unknown()
    canonical=optimizer.profiles[key].data.get('displayName')
    if type(canonical) is not str or not canonical or len(canonical)>48:_unknown()
    return normalized_name(canonical),key,source


def _validated(details):
    if (type(details) is not CharacterDetails or type(details.raw_name) is not str
        or type(details.name_box) is not tuple or type(details.name_score) is not float):_unknown()
    word=dict(text=details.raw_name,box=list(details.name_box),score=details.name_score)
    _word(dict(algorithm='OCR',box=list(details.name_box),
        detail=dict(all=[word],best=word,filtered=[word])),NAME_ROI,margin=2)
    if (details.canonical_name,details.character_id,details.alias_source)!=_identity(details.raw_name):_unknown()
    return details


def character_details_readback(receipt, expected=None):
    """Read one complete details modal; expected is an optional exact catalog ID."""
    if (type(receipt) is not dict or receipt.get('native_ok') is not True or receipt.get('tasker_inited') is not True
        or type(receipt.get('task_id')) is not int or receipt['task_id']<=0):_unknown()
    nodes=receipt.get('nodes')
    if (type(nodes) is not list or len(nodes)!=1 or type(nodes[0]) is not dict
        or nodes[0].get('name')!='LabyCharacterDetailsObserved' or nodes[0].get('completed') is not True):_unknown()
    raw=nodes[0].get('recognition');names=character_details_pipeline()['LabyCharacterDetailsObserved']['all_of']
    if (type(raw) is not list or len(raw)!=len(names)
        or any(type(r) is not dict or type(r.get('name')) is not str for r in raw)
        or {r['name'] for r in raw}!=set(names)):_unknown()
    rows={r['name']:r for r in raw}
    for name,(roi,text) in _PAGE.items():
        if _word(rows['LabyCharacterDetails'+name],roi)[0]!=text:_unknown()
    text,box,score=_word(rows['LabyCharacterDetailsName'],NAME_ROI,margin=2)
    canonical,key,source=_identity(text)
    details=_validated(CharacterDetails(text,canonical,key,source,box,score))
    if expected is not None:
        if type(expected) is not str or re.fullmatch(r'[1-9][0-9]{3}',expected) is None:_unknown()
        if key!=expected:raise BatchStopped('character_details_mismatch')
    return details


def _pattern(text):
    parts=['[（(]' if c=='(' else '[）)]' if c==')' else re.escape(c) for c in normalized_name(text)]
    return r'^\s*'+r'\s*'.join(parts)+r'\s*$'


@contextmanager
def character_details_guard(tasker, details):
    """Qualify only the observed raw name; do not renew a consumed close budget."""
    details=_validated(details);resource=tasker.resource
    name='LabyCharacterDetailsNameGuard';original={name:resource.get_node_data(name)}
    if not original[name] or not resource.override_pipeline({name:dict(expected=_pattern(details.raw_name))}):
        raise BatchStopped('character_details_guard_failed')
    try:yield
    except BaseException:
        try:resource.override_pipeline(original)
        except Exception:pass
        raise
    else:
        if not resource.override_pipeline(original):raise BatchStopped('character_details_guard_restore_failed')
